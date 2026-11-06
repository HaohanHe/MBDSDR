# SPDX-License-Identifier: MIT
"""
MBDSDR AI 内核 - 6DOF 位姿融合与 AR 投影
==========================================
Pose：6 自由度位姿估计 + 增强现实卫星投影。

对照白皮书第八章 8.1 6DOF 位姿融合、8.2 AR 投影。

硬件支持：
- BMI260：6 轴 IMU（3 轴加速度计 + 3 轴陀螺仪）
- TMAG5273：3 轴磁力计
- ATGM336H：GNSS（北斗/GPS/GLONASS/Galileo）

核心算法：
- 互补滤波（Complementary Filter）：加速度计+陀螺仪融合
- 磁力计航向校正：倾斜补偿的电子罗盘
- Madgwick 滤波：6DOF/9DOF 姿态估计
- 卡尔曼滤波：位置速度融合（GNSS+IMU 航位推算）

AR 投影：
- 卫星位置在相机视图中的投影
- 指向辅助：告诉用户把天线指向哪个方向
- 卫星过境 AR 可视化
"""

import math
import time
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Tuple, List


# ═══════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════

@dataclass
class IMUData:
    """IMU 原始数据。"""
    accel_x: float = 0.0  # m/s²
    accel_y: float = 0.0
    accel_z: float = 0.0
    gyro_x: float = 0.0  # rad/s
    gyro_y: float = 0.0
    gyro_z: float = 0.0
    mag_x: float = 0.0  # μT
    mag_y: float = 0.0
    mag_z: float = 0.0
    timestamp: float = field(default_factory=time.time)
    temperature: float = 0.0  # °C


@dataclass
class GPSData:
    """GNSS 数据。"""
    latitude: float = 0.0  # 度
    longitude: float = 0.0
    altitude: float = 0.0  # 米
    speed: float = 0.0  # m/s
    course: float = 0.0  # 度
    satellites: int = 0
    hdop: float = 0.0
    fix_quality: int = 0  # 0=无定位, 1=GPS, 2=DGPS, 4=RTK固定
    timestamp: float = field(default_factory=time.time)


@dataclass
class Pose:
    """6DOF 位姿。"""
    # 位置
    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0

    # 姿态（欧拉角，度）
    roll: float = 0.0   # 横滚（X 轴）
    pitch: float = 0.0  # 俯仰（Y 轴）
    yaw: float = 0.0    # 偏航/航向（Z 轴）

    # 四元数
    q_w: float = 1.0
    q_x: float = 0.0
    q_y: float = 0.0
    q_z: float = 0.0

    # 速度
    velocity_north: float = 0.0
    velocity_east: float = 0.0
    velocity_up: float = 0.0

    # 状态
    confidence: float = 0.0  # 0-1，位姿估计置信度
    timestamp: float = field(default_factory=time.time)
    source: str = "unknown"  # imu / gps / fused

    def to_dict(self) -> Dict[str, Any]:
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "altitude": self.altitude,
            "roll": self.roll,
            "pitch": self.pitch,
            "yaw": self.yaw,
            "quaternion": [self.q_w, self.q_x, self.q_y, self.q_z],
            "velocity": {
                "north": self.velocity_north,
                "east": self.velocity_east,
                "up": self.velocity_up,
            },
            "confidence": self.confidence,
            "timestamp": self.timestamp,
            "source": self.source,
        }


@dataclass
class ARMarker:
    """AR 标记（卫星在视图中的投影位置）。"""
    name: str
    # 屏幕坐标（0-1，归一化）
    screen_x: float
    screen_y: float
    # 真实位置
    elevation: float  # 仰角（度）
    azimuth: float    # 方位角（度）
    distance_km: float
    # 可见性
    visible: bool
    in_view: bool  # 是否在相机视野内
    # 元数据
    frequency_mhz: float = 0.0
    doppler_hz: float = 0.0
    size: float = 1.0  # 标记大小（根据距离缩放）


# ═══════════════════════════════════════════════════════
# 0. 机体坐标系约定与共用几何（2026-11-06 钉死）
# ═══════════════════════════════════════════════════════
# 全模块统一口径（此前各处各自为政，导致罗盘偏 90°、pitch 正负与注释相反）：
#
#   机体：x = 前、y = 左、z = 上（右手系）。
#         水平静止时加速度计读 a = (0, 0, +g)（读的是支撑反力），与
#         tests/test_pose_fusion.py 注入的 accel=(0,0,1) 一致。
#   地球：x = 北、y = 西、z = 天顶。故航向 ψ=0 时机体系与地球系重合
#         （四元数为单位元），绕 +z 的正向旋转是**逆时针**（北→西），
#         即航向（顺时针自北）ψ 对应绕 z 转 −ψ。
#   姿态：roll  > 0 = 右翼下沉（右倾）
#         pitch > 0 = **抬头**
#         yaw        = 航向 ψ，顺时针自北，0=N / 90=E（与 GPS course 同口径）
#
# 三条数值恒等式（有单测逐点锁定，改约定必须同时改这三条）：
#   roll  = atan2(a_y, a_z)
#   pitch = atan2(a_x, hypot(a_y, a_z))
#   ψ     = atan2(h_y, h_x),  h = Ry(−pitch)·Rx(roll)·m
# 全部在 (航向 × 俯仰 × 横滚) 网格上与真值 0.00° 重合。


def _rot_x(deg: float) -> np.ndarray:
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


def _rot_y(deg: float) -> np.ndarray:
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def _tilt_to_level(mx: float, my: float, mz: float,
                   roll_deg: float, pitch_deg: float) -> Tuple[float, float]:
    """磁矢量去倾斜：返回水平系里的 (h_x, h_y)。仅用于求航向。

    h = Ry(−pitch)·Rx(roll)·m —— 与本模块机体约定严格配对；
    换成别的符号组合会整体偏 90°（旧实现就是这样）。
    """
    m = np.array([mx, my, mz], dtype=float)
    h = _rot_y(-float(pitch_deg)) @ _rot_x(float(roll_deg)) @ m
    return float(h[0]), float(h[1])


def _rot_from_quat(q: np.ndarray) -> np.ndarray:
    """四元数 [w,x,y,z]（机体系→地球系）转 3×3 旋转矩阵。"""
    w, x, y, z = (float(q[0]), float(q[1]), float(q[2]), float(q[3]))
    return np.array([
        [1 - 2*(y*y + z*z), 2*(x*y - w*z), 2*(x*z + w*y)],
        [2*(x*y + w*z), 1 - 2*(x*x + z*z), 2*(y*z - w*x)],
        [2*(x*z - w*y), 2*(y*z + w*x), 1 - 2*(x*x + y*y)]])


def _quat_mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return np.array([w1*w2 - x1*x2 - y1*y2 - z1*z2,
                     w1*x2 + x1*w2 + y1*z2 - z1*y2,
                     w1*y2 - x1*z2 + y1*w2 + z1*x2,
                     w1*z2 + x1*y2 - y1*x2 + z1*w2])


def _roll_pitch_from_matrix(R: np.ndarray) -> Tuple[float, float]:
    """从旋转矩阵取 roll/pitch —— **第三行**即重力反力方向，与加速度计同式。"""
    ax, ay, az = float(R[2, 0]), float(R[2, 1]), float(R[2, 2])
    roll = math.degrees(math.atan2(ay, az))
    pitch = math.degrees(math.atan2(ax, math.hypot(ay, az)))
    return roll, pitch


def _heading_from_matrix(R: np.ndarray) -> float:
    """从旋转矩阵取航向（顺时针自北）。机体 x̂ 在地球系 (北,西,天顶) 的方位。"""
    hd = math.degrees(math.atan2(-float(R[1, 0]), float(R[0, 0]))) % 360.0
    return hd


def _matrix_from_rpy(roll_deg: float, pitch_deg: float, yaw_deg: float) -> np.ndarray:
    """(roll, pitch, 航向) -> 机体系→地球系旋转矩阵（_roll_pitch/_heading 的逆）。"""
    return (_rot_z(-float(yaw_deg)) @ _rot_y(-float(pitch_deg))
            @ _rot_x(float(roll_deg)))


def _rot_z(deg: float) -> np.ndarray:
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def _quat_from_matrix(R: np.ndarray) -> np.ndarray:
    """旋转矩阵 -> 四元数 [w,x,y,z]（数值稳定分支，取最大对角元）。"""
    tr = float(np.trace(R))
    if tr > 0.0:
        s = math.sqrt(tr + 1.0) * 2.0
        q = [0.25 * s,
             (R[2, 1] - R[1, 2]) / s,
             (R[0, 2] - R[2, 0]) / s,
             (R[1, 0] - R[0, 1]) / s]
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2.0
        q = [(R[2, 1] - R[1, 2]) / s, 0.25 * s,
             (R[0, 1] + R[1, 0]) / s, (R[0, 2] + R[2, 0]) / s]
    elif R[1, 1] > R[2, 2]:
        s = math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2.0
        q = [(R[0, 2] - R[2, 0]) / s, (R[0, 1] + R[1, 0]) / s,
             0.25 * s, (R[1, 2] + R[2, 1]) / s]
    else:
        s = math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2.0
        q = [(R[1, 0] - R[0, 1]) / s, (R[0, 2] + R[2, 0]) / s,
             (R[1, 2] + R[2, 1]) / s, 0.25 * s]
    v = np.array(q, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n > 0 else np.array([1.0, 0.0, 0.0, 0.0])


# ═══════════════════════════════════════════════════════
# 1. 互补滤波（6DOF 姿态估计）
# ═══════════════════════════════════════════════════════

class ComplementaryFilter:
    """
    互补滤波器：加速度计 + 陀螺仪融合。

    加速度计提供长期稳定的俯仰/横滚（受重力），
    陀螺仪提供短期精确的角速度，
    互补滤波结合两者优点。

    angle = alpha * (angle + gyro * dt) + (1 - alpha) * accel_angle
    """

    def __init__(self, alpha: float = 0.98):
        """
        alpha: 陀螺仪权重（0.98 典型值，越高越信任陀螺仪）
        """
        self.alpha = alpha
        self.roll = 0.0
        self.pitch = 0.0
        self.yaw = 0.0  # 互补滤波不能估计 yaw（需要磁力计）
        self._last_time = 0.0

    def update(self, imu: IMUData) -> Tuple[float, float]:
        """
        更新姿态。

        返回 (roll, pitch)，单位度。
        """
        if self._last_time == 0.0:
            self._last_time = imu.timestamp
            return self.roll, self.pitch

        dt = imu.timestamp - self._last_time
        self._last_time = imu.timestamp

        if dt <= 0 or dt > 1.0:
            return self.roll, self.pitch

        # 陀螺仪积分（短期）
        gyro_roll = self.roll + math.degrees(imu.gyro_x) * dt
        gyro_pitch = self.pitch + math.degrees(imu.gyro_y) * dt

        # 加速度计计算（长期，受重力）
        # 2026-11-06 口径统一：机体取 x=前、y=左、z=上（水平静止时传感器读
        # a=(0,0,+g)，与 tests/test_pose_fusion.py 注入的 accel=(0,0,1) 一致）。
        # 该约定下 pitch>0 = **抬头**，故取 +a_x；旧式 atan2(-a_x, …) 实际是
        # "低头为正"，与 AR 投影 device_elevation 的注释口径相反。
        accel_roll = math.degrees(math.atan2(imu.accel_y, imu.accel_z))
        accel_pitch = math.degrees(math.atan2(imu.accel_x,
                                                 math.sqrt(imu.accel_y**2 + imu.accel_z**2)))

        # 互补滤波
        self.roll = self.alpha * gyro_roll + (1 - self.alpha) * accel_roll
        self.pitch = self.alpha * gyro_pitch + (1 - self.alpha) * accel_pitch

        return self.roll, self.pitch

    def reset(self):
        self.roll = 0.0
        self.pitch = 0.0
        self.yaw = 0.0
        self._last_time = 0.0


# ═══════════════════════════════════════════════════════
# 2. 倾斜补偿电子罗盘（磁力计航向）
# ═══════════════════════════════════════════════════════

class TiltCompensatedCompass:
    """
    倾斜补偿电子罗盘。

    普通电子罗盘只在水平时准确，倾斜时需要用加速度计
    测量的俯仰/横滚来补偿磁力计的倾斜。

    步骤：
    1. 用加速度计计算 roll/pitch
    2. 将磁力计从机体坐标系旋转到水平坐标系
    3. 在水平坐标系计算航向角
    """

    def __init__(self, declination: float = 0.0):
        """
        declination: 磁偏角（度），示例城市约 -9°（磁北比真北偏西 9°）
        """
        self.declination = declination
        self.yaw = 0.0
        # 磁力计校准参数（硬铁/软铁）
        self._mag_offset = np.zeros(3)
        self._mag_scale = np.ones(3)

    def calibrate(self, mag_samples: np.ndarray):
        """
        磁力计校准（简化版：计算硬铁偏移）。

        mag_samples: Nx3 的磁力计样本数组
        """
        if len(mag_samples) < 10:
            return
        self._mag_offset = np.mean(mag_samples, axis=0)
        # 简化：不做软铁校准

    def update(self, imu: IMUData, roll: float, pitch: float) -> float:
        """
        更新航向。

        roll, pitch: 度（来自互补滤波或加速度计）
        返回 yaw（度，0-360，真北）
        """
        # 应用校准
        mx = (imu.mag_x - self._mag_offset[0]) * self._mag_scale[0]
        my = (imu.mag_y - self._mag_offset[1]) * self._mag_scale[1]
        mz = (imu.mag_z - self._mag_offset[2]) * self._mag_scale[2]

        # 倾斜补偿：把机体磁矢量转回水平系（只去 roll/pitch，不碰航向）
        #
        # 2026-11-06 修正（原式整体偏 90° 且倾斜项符号错）：
        #   旧式 my_rot = my cosφ + mz sinφ、mx_tilt = mx cosθ − mz_rot sinθ，
        #   末尾 atan2(-mx_tilt, my_tilt) —— 在"z 上机体"约定下，atan2 的
        #   参量顺序与符号都不对，航向恒偏 90°，叠加倾斜时更乱。
        #
        # 现式（数值验证：整 (航向×俯仰×横滚) 网格误差 **0.00°**，
        # 见 tests/test_pose_fusion.py::test_compass_heading_matches_geometry）：
        #   h = Ry(−pitch) · Rx(roll) · m
        #   ψ = atan2(h_y, h_x)        # 顺时针自北
        # 依据：机体 x=前、y=左、z=上（与加速度计口径一致），地球系
        # (north, west, up)，水平静止时 h 退化为 (Bn cosψ, Bn sinψ)。
        h_x, h_y = _tilt_to_level(mx, my, mz, roll, pitch)
        yaw_mag = math.degrees(math.atan2(h_y, h_x))

        # 归一化到 0-360
        yaw_mag = yaw_mag % 360.0

        # 加上磁偏角得到真北
        self.yaw = (yaw_mag + self.declination) % 360.0

        return self.yaw


# ═══════════════════════════════════════════════════════
# 3. Madgwick 滤波（9DOF 姿态估计）
# ═══════════════════════════════════════════════════════

class MadgwickFilter:
    """
    简化互补 / Madgwick 风格姿态融合（9DOF：加速度计 + 陀螺仪 + 磁力计）。

    这是简化实现，不是完整的 Madgwick 梯度下降：
    - 加速度计与磁力计的观测修正已作为等效角速度的修正项进入积分
      （在 q_dot 计算之前），不再是写完即弃的无效局部变量。
    - 加速度计用 accel × estimated_gravity 的叉积误差修正等效角速度；
      磁力计用倾斜补偿的水平航向误差只修正 z 轴角速度。
    - mag 全零时诚实降级为 6DOF（只做加速度计 + 陀螺仪），不编造航向。
    - 不实现完整 Madgwick 的 Jacobian / 梯度步长，也不代表真实硬件精度。

    注意：本模块无 BMI260 / TMAG5273 驱动，数值全部由调用方注入，
    不代表真实硬件读数。
    """

    def __init__(self, beta: float = 0.1):
        """
        beta: 加速度计观测修正增益（0.1 典型值）。
        beta_mag: 磁力计航向修正增益，默认与 beta 相同，仅作用于 z 轴角速度。
        """
        self.beta = beta
        self.beta_mag = beta
        self.q = np.array([1.0, 0.0, 0.0, 0.0])  # 四元数 [w, x, y, z]
        self._last_time = 0.0
        # 最近一次加速度观测与估计重力的一致性（误差向量模）；无观测时为 None
        self.last_accel_error: Optional[float] = None

    def update(self, imu: IMUData) -> np.ndarray:
        """
        更新姿态（9DOF：加速度计+陀螺仪+磁力计）。

        返回四元数 [w, x, y, z]。

        观测修正在积分之前作用于等效角速度，随后才计算 q_dot 并积分。
        无 BMI260 / TMAG5273 驱动：数值全部由调用方注入，不代表真实硬件读数。
        """
        # 每次先清空一致性诊断：本次无有效观测融合（早退/首次）即保持 None
        self.last_accel_error = None

        if self._last_time == 0.0:
            self._last_time = imu.timestamp
            return self.q.copy()

        dt = imu.timestamp - self._last_time
        self._last_time = imu.timestamp

        if dt <= 0 or dt > 1.0:
            return self.q.copy()

        # 用积分前的当前姿态做观测修正（副本，避免原地改动 self.q）
        q = self.q.copy()

        # 归一化加速度计
        accel = np.array([imu.accel_x, imu.accel_y, imu.accel_z], dtype=float)
        accel_norm = np.linalg.norm(accel)
        if accel_norm > 0:
            accel = accel / accel_norm

        # 归一化磁力计
        mag = np.array([imu.mag_x, imu.mag_y, imu.mag_z], dtype=float)
        mag_norm = np.linalg.norm(mag)
        if mag_norm > 0:
            mag = mag / mag_norm

        # 陀螺仪（rad/s）
        gyro = np.array([imu.gyro_x, imu.gyro_y, imu.gyro_z], dtype=float)

        # ---- 加速度计观测修正：在积分之前修正等效角速度 ----
        #
        # 2026-11-06 重写（原式符号错，稳态 roll/pitch 差 4–5°）：
        #   观测 = 归一化加速度（机体里"世界天顶"的方向，水平静止时即 +z_b）；
        #   估计 = 由四元数算出同一方向；误差 = 观测 × 估计（机体里）。
        #   符号经网格验证：ω_a = +β·(a_hat × up_hat)。反向会让滤波器在
        #   真值附近出现偏置平衡点（实测 roll 20° 收敛到 15.8°）。
        R = _rot_from_quat(q)
        if accel_norm > 0:
            up_hat = R.T @ np.array([0.0, 0.0, 1.0])   # 世界天顶在机体里的方向
            error = np.cross(accel, up_hat)
            self.last_accel_error = float(np.linalg.norm(error))
            gyro = gyro + self.beta * error

        # ---- 磁力计观测修正：倾斜补偿的水平航向误差，只修正 z 轴 ----
        # mag 全零时诚实降级为 6DOF，完全跳过本分支，不编造航向
        if mag_norm > 0:
            roll_deg, pitch_deg = _roll_pitch_from_matrix(R)
            h_x, h_y = _tilt_to_level(float(mag[0]), float(mag[1]), float(mag[2]),
                                      roll_deg, pitch_deg)
            if math.hypot(h_x, h_y) > 1e-9:
                psi_mag = math.atan2(h_y, h_x)               # 观测航向（顺时针自北）
                psi_est = math.radians(_heading_from_matrix(R))
                err = (psi_mag - psi_est + math.pi) % (2*math.pi) - math.pi
                # 符号验证：绕 +z 的正转是逆时针（北→西），航向要**增大**就得
                # 取 −err；写成 +err 会让磁修正与陀螺互相对冲（网格搜索出 180° 错）。
                gyro[2] += -self.beta_mag * err

        # ---- 用修正后的等效角速度积分（机体角速度，右乘）----
        q_dot = 0.5 * _quat_mul(q, np.array([0.0, *gyro]))

        q = q + q_dot * dt

        # 归一化四元数
        q_norm = np.linalg.norm(q)
        if q_norm > 0:
            q = q / q_norm

        self.q = q
        return q.copy()

    def get_euler(self) -> Tuple[float, float, float]:
        """从四元数计算欧拉角（roll, pitch, yaw），单位度。

        2026-11-06：改为与加速度计/磁力计**同一套**反解（此前用 ZYX 公式，
        pitch 符号与本模块机体约定相反，roll/pitch 在组合倾斜下也不自洽）：
          roll/pitch ← 旋转矩阵第三行（重力反力方向，与 accel 公式逐字同式）
          yaw       ← 机体 x̂ 在水平面的方位（顺时针自北，可直接对 GPS course）
        """
        R = _rot_from_quat(self.q)
        roll, pitch = _roll_pitch_from_matrix(R)
        return roll, pitch, _heading_from_matrix(R)

    def reset(self):
        self.q = np.array([1.0, 0.0, 0.0, 0.0])
        self._last_time = 0.0


# ═══════════════════════════════════════════════════════
# 4. 位姿融合器（主接口）
# ═══════════════════════════════════════════════════════

class PoseFusion:
    """
    位姿融合器：IMU + 磁力计 + GNSS 融合。

    支持三种模式：
    - imu_only：只用 IMU（互补滤波+磁力计）
    - gps_only：只用 GNSS
    - fused：IMU + GNSS 融合（卡尔曼/互补）

    输出统一的 Pose 对象。
    """

    def __init__(self, mode: str = "fused", declination: float = -9.0):
        """
        mode: imu_only / gps_only / fused
        declination: 磁偏角（示例城市约 -9°）
        """
        self.mode = mode
        self.complementary = ComplementaryFilter(alpha=0.98)
        self.compass = TiltCompensatedCompass(declination=declination)
        self.madgwick = MadgwickFilter(beta=0.1)

        self.pose = Pose()
        self._last_gps: Optional[GPSData] = None
        self._gps_history: List[GPSData] = []
        self._use_madgwick = True  # 优先用 Madgwick

    def update_imu(self, imu: IMUData) -> Pose:
        """更新 IMU 数据。"""
        if self._use_madgwick:
            q = self.madgwick.update(imu)
            roll, pitch, yaw = self.madgwick.get_euler()
            self.pose.q_w, self.pose.q_x, self.pose.q_y, self.pose.q_z = q
        else:
            roll, pitch = self.complementary.update(imu)
            yaw = self.compass.update(imu, roll, pitch)

        self.pose.roll = roll
        self.pose.pitch = pitch
        self.pose.yaw = yaw
        self.pose.timestamp = imu.timestamp
        self.pose.source = "imu"
        # confidence：依据观测质量可升可降，钳在 [0,1]，不再无条件 +0.01
        accel = np.array([imu.accel_x, imu.accel_y, imu.accel_z])
        gyro = np.array([imu.gyro_x, imu.gyro_y, imu.gyro_z])
        mag = np.array([imu.mag_x, imu.mag_y, imu.mag_z])
        imu_all_zero = (np.linalg.norm(accel) == 0.0
                        and np.linalg.norm(gyro) == 0.0
                        and np.linalg.norm(mag) == 0.0)
        if imu_all_zero:
            delta = -0.05                       # 缺观测 / 全零 IMU → 下降
        elif (self._use_madgwick
              and self.madgwick.last_accel_error is not None
              and self.madgwick.last_accel_error <= 0.3):
            delta = +0.01                       # 有效观测且与估计一致 → 上升
        elif (not self._use_madgwick
              and np.linalg.norm(accel) > 0.0):
            delta = +0.01                       # 互补滤波路径：有有效加速度观测
        else:
            delta = -0.02                       # 无有效观测或误差大 → 下降
        self.pose.confidence = max(0.0, min(1.0, self.pose.confidence + delta))

        return self.pose

    def update_gps(self, gps: GPSData) -> Pose:
        """更新 GNSS 数据。"""
        self._last_gps = gps
        self._gps_history.append(gps)
        if len(self._gps_history) > 100:
            self._gps_history = self._gps_history[-100:]

        # 位置
        self.pose.latitude = gps.latitude
        self.pose.longitude = gps.longitude
        self.pose.altitude = gps.altitude

        # 速度（从 GNSS course/speed 转换为 NED）
        if gps.speed > 0:
            course_rad = math.radians(gps.course)
            self.pose.velocity_north = gps.speed * math.cos(course_rad)
            self.pose.velocity_east = gps.speed * math.sin(course_rad)

        # 航向（如果有速度，用 GNSS course 修正）
        if gps.speed > 1.0:  # 速度 > 1 m/s 时信任 GNSS 航向
            # 互补融合 GNSS 航向和 IMU 航向
            alpha = 0.9  # 信任 IMU
            # 处理角度环绕
            diff = gps.course - self.pose.yaw
            if diff > 180:
                diff -= 360
            elif diff < -180:
                diff += 360
            self.pose.yaw = (self.pose.yaw + (1 - alpha) * diff) % 360

        self.pose.timestamp = gps.timestamp
        self.pose.source = "fused" if self.mode == "fused" else "gps"

        # 四元数与欧拉角必须描述同一姿态：GNSS 融合改了 yaw（或 GPS-only 模式
        # 从未有 IMU），这里按当前 (roll, pitch, yaw) 重建 q——否则 to_dict()
        # 同时吐 euler 和 quaternion，两者指向不同姿态，消费方读 q 就是错的。
        self.pose.q_w, self.pose.q_x, self.pose.q_y, self.pose.q_z = (
            _quat_from_matrix(_matrix_from_rpy(
                self.pose.roll, self.pose.pitch, self.pose.yaw)))

        # confidence 与定位质量真挂钩：fix_quality=0（无定位）不给半分。
        # 旧式 min(1, 0.5 + quality*0.1) 在无 fix 时也给 0.5，是可见的假指标。
        if gps.fix_quality <= 0:
            self.pose.confidence = 0.0
        else:
            self.pose.confidence = min(1.0, 0.4 + 0.15 * gps.fix_quality)

        return self.pose

    def get_pose(self) -> Pose:
        """获取当前位姿。"""
        return self.pose

    def reset(self):
        """重置位姿估计器。"""
        self.complementary.reset()
        self.madgwick.reset()
        self.pose = Pose()
        self._last_gps = None
        self._gps_history.clear()


# ═══════════════════════════════════════════════════════
# 5. AR 投影：卫星在相机视图中的位置
# ═══════════════════════════════════════════════════════

class ARProjector:
    """
    AR 投影器：将卫星位置投影到相机视图。

    输入：
    - 观测点位姿（Pose：经纬度+姿态）
    - 卫星位置（仰角/方位角/距离）
    - 相机参数（FOV、宽高比）

    输出：
    - ARMarker：卫星在屏幕上的归一化坐标 (0-1)
    - 指向辅助：告诉用户把天线指向哪个方向
    """

    def __init__(
        self,
        camera_fov_deg: float = 60.0,
        screen_aspect: float = 16.0 / 9.0,
        pointer_offset_deg: float = 0.0,
    ):
        """
        camera_fov_deg: 相机水平视野（度）
        screen_aspect: 屏幕宽高比
        pointer_offset_deg: 天线指向与相机的夹角偏移
        """
        self.camera_fov_deg = camera_fov_deg
        self.screen_aspect = screen_aspect
        self.pointer_offset_deg = pointer_offset_deg

    def project_satellite(
        self,
        satellite_name: str,
        elevation_deg: float,
        azimuth_deg: float,
        distance_km: float,
        pose: Pose,
        frequency_mhz: float = 0.0,
        doppler_hz: float = 0.0,
    ) -> ARMarker:
        """
        将卫星投影到屏幕坐标。

        原理：
        1. 计算卫星相对于设备指向的方位角差和仰角差
        2. 将角度差转换为屏幕归一化坐标
        3. 判断是否在视野内
        """
        # 设备指向（用 yaw 作为水平指向，pitch 作为垂直指向）
        device_azimuth = pose.yaw
        # 2026-11-06：pitch 现为**抬头为正**（与 accel / get_euler 同口径），
        # 设备指向的仰角就是 pitch 本身。旧式 −pitch 在旧的"低头为正"约定下
        # 恰好等价；改成抬头为太后若还取负号，会把"已对准"报成"向上抬 2×仰角"。
        device_elevation = pose.pitch

        # 卫星相对于设备指向的角度差
        az_diff = azimuth_deg - device_azimuth
        # 归一化到 -180 ~ 180
        while az_diff > 180:
            az_diff -= 360
        while az_diff < -180:
            az_diff += 360

        elev_diff = elevation_deg - device_elevation

        # 转换为屏幕坐标（归一化 0-1）
        # 水平 FOV
        h_fov = self.camera_fov_deg
        v_fov = h_fov / self.screen_aspect

        screen_x = 0.5 + az_diff / h_fov
        screen_y = 0.5 - elev_diff / v_fov  # Y 轴向下

        # 判断是否在视野内（留一些边距）
        in_view = (0.0 <= screen_x <= 1.0) and (0.0 <= screen_y <= 1.0)
        visible = elevation_deg > 0  # 仰角 > 0 才可见

        # 标记大小（根据距离缩放，越近越大）
        size = max(0.5, min(2.0, 1000.0 / max(distance_km, 100)))

        return ARMarker(
            name=satellite_name,
            screen_x=screen_x,
            screen_y=screen_y,
            elevation=elevation_deg,
            azimuth=azimuth_deg,
            distance_km=distance_km,
            visible=visible,
            in_view=in_view,
            frequency_mhz=frequency_mhz,
            doppler_hz=doppler_hz,
            size=size,
        )

    def get_pointing_guidance(
        self,
        target_azimuth: float,
        target_elevation: float,
        pose: Pose,
    ) -> Dict[str, Any]:
        """
        获取指向辅助：告诉用户把天线指向哪个方向。

        返回方向指示和角度差。
        """
        az_diff = target_azimuth - pose.yaw
        while az_diff > 180:
            az_diff -= 360
        while az_diff < -180:
            az_diff += 360

        elev_diff = target_elevation - pose.pitch

        # 方向文字
        if abs(az_diff) < 5:
            horizontal_dir = "正对目标"
        elif az_diff > 0:
            horizontal_dir = f"向右转 {az_diff:.1f}°"
        else:
            horizontal_dir = f"向左转 {-az_diff:.1f}°"

        if abs(elev_diff) < 5:
            vertical_dir = "仰角正确"
        elif elev_diff > 0:
            vertical_dir = f"向上抬 {elev_diff:.1f}°"
        else:
            vertical_dir = f"向下压 {-elev_diff:.1f}°"

        # 总偏差
        total_error = math.sqrt(az_diff**2 + elev_diff**2)

        # 对准状态
        if total_error < 5:
            status = "已对准"
        elif total_error < 15:
            status = "接近对准"
        elif total_error < 45:
            status = "需要调整"
        else:
            status = "偏差较大"

        return {
            "horizontal_direction": horizontal_dir,
            "vertical_direction": vertical_dir,
            "azimuth_error_deg": az_diff,
            "elevation_error_deg": elev_diff,
            "total_error_deg": total_error,
            "status": status,
            "target_azimuth": target_azimuth,
            "target_elevation": target_elevation,
            "current_yaw": pose.yaw,
            "current_pitch": pose.pitch,
        }

    def project_multiple(
        self,
        satellites: List[Dict[str, Any]],
        pose: Pose,
    ) -> List[ARMarker]:
        """
        批量投影多颗卫星。

        satellites: [{"name":..., "elevation":..., "azimuth":..., "distance_km":..., "frequency_mhz":..., "doppler_hz":...}]
        """
        markers = []
        for sat in satellites:
            marker = self.project_satellite(
                satellite_name=sat["name"],
                elevation_deg=sat["elevation"],
                azimuth_deg=sat["azimuth"],
                distance_km=sat.get("distance_km", 1000),
                pose=pose,
                frequency_mhz=sat.get("frequency_mhz", 0.0),
                doppler_hz=sat.get("doppler_hz", 0.0),
            )
            markers.append(marker)
        return markers
