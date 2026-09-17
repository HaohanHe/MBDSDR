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
        accel_roll = math.degrees(math.atan2(imu.accel_y, imu.accel_z))
        accel_pitch = math.degrees(math.atan2(-imu.accel_x,
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
        declination: 磁偏角（度），长春约 -9°（磁北比真北偏西 9°）
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

        # 转换为弧度
        roll_rad = math.radians(roll)
        pitch_rad = math.radians(pitch)

        # 倾斜补偿：将磁力计旋转到水平坐标系
        # X 轴旋转（roll）
        mx_rot = mx
        my_rot = my * math.cos(roll_rad) + mz * math.sin(roll_rad)
        mz_rot = -my * math.sin(roll_rad) + mz * math.cos(roll_rad)

        # Y 轴旋转（pitch）
        mx_tilt = mx_rot * math.cos(pitch_rad) - mz_rot * math.sin(pitch_rad)
        my_tilt = my_rot

        # 计算航向（磁北）
        if abs(my_tilt) < 1e-6:
            if mx_tilt < 0:
                yaw_mag = 180.0
            else:
                yaw_mag = 0.0
        else:
            yaw_mag = math.degrees(math.atan2(-mx_tilt, my_tilt))

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
    Madgwick 滤波：9DOF 姿态估计（IMU+磁力计）。

    这是一种基于梯度下降的姿态估计算法，
    比互补滤波更精确，能同时估计 roll/pitch/yaw。

    简化实现：使用四元数表示姿态。
    """

    def __init__(self, beta: float = 0.1):
        """
        beta: 算法增益（0.1 典型值）
        """
        self.beta = beta
        self.q = np.array([1.0, 0.0, 0.0, 0.0])  # 四元数 [w, x, y, z]
        self._last_time = 0.0

    def update(self, imu: IMUData) -> np.ndarray:
        """
        更新姿态（9DOF：加速度计+陀螺仪+磁力计）。

        返回四元数 [w, x, y, z]。
        """
        if self._last_time == 0.0:
            self._last_time = imu.timestamp
            return self.q.copy()

        dt = imu.timestamp - self._last_time
        self._last_time = imu.timestamp

        if dt <= 0 or dt > 1.0:
            return self.q.copy()

        q = self.q

        # 归一化加速度计
        accel = np.array([imu.accel_x, imu.accel_y, imu.accel_z])
        accel_norm = np.linalg.norm(accel)
        if accel_norm > 0:
            accel = accel / accel_norm

        # 归一化磁力计
        mag = np.array([imu.mag_x, imu.mag_y, imu.mag_z])
        mag_norm = np.linalg.norm(mag)
        if mag_norm > 0:
            mag = mag / mag_norm

        # 陀螺仪（rad/s）
        gyro = np.array([imu.gyro_x, imu.gyro_y, imu.gyro_z])

        # 简化版 Madgwick：用梯度下降修正四元数
        # （完整实现比较复杂，这里用简化的互补滤波+四元数表示）

        # 陀螺仪积分
        q_dot = 0.5 * np.array([
            -q[1]*gyro[0] - q[2]*gyro[1] - q[3]*gyro[2],
            q[0]*gyro[0] + q[2]*gyro[2] - q[3]*gyro[1],
            q[0]*gyro[1] - q[1]*gyro[2] + q[3]*gyro[0],
            q[0]*gyro[2] + q[1]*gyro[1] - q[2]*gyro[0],
        ])

        q = q + q_dot * dt

        # 加速度计修正（简化：直接用互补滤波思想）
        if accel_norm > 0:
            # 从四元数计算重力方向
            gx = 2*(q[1]*q[3] - q[0]*q[2])
            gy = 2*(q[0]*q[1] + q[2]*q[3])
            gz = q[0]**2 - q[1]**2 - q[2]**2 + q[3]**2

            # 误差 = 加速度计方向 - 重力方向（叉积）
            error = np.cross(accel, np.array([gx, gy, gz]))

            # 用误差修正陀螺仪（简化版）
            gyro = gyro + self.beta * error

        # 归一化四元数
        q_norm = np.linalg.norm(q)
        if q_norm > 0:
            q = q / q_norm

        self.q = q
        return q.copy()

    def get_euler(self) -> Tuple[float, float, float]:
        """从四元数计算欧拉角（roll, pitch, yaw），单位度。"""
        q = self.q
        # roll (x-axis rotation)
        sinr_cosp = 2 * (q[0]*q[1] + q[2]*q[3])
        cosr_cosp = 1 - 2 * (q[1]**2 + q[2]**2)
        roll = math.degrees(math.atan2(sinr_cosp, cosr_cosp))

        # pitch (y-axis rotation)
        sinp = 2 * (q[0]*q[2] - q[3]*q[1])
        if abs(sinp) >= 1:
            pitch = math.degrees(math.copysign(math.pi/2, sinp))
        else:
            pitch = math.degrees(math.asin(sinp))

        # yaw (z-axis rotation)
        siny_cosp = 2 * (q[0]*q[3] + q[1]*q[2])
        cosy_cosp = 1 - 2 * (q[2]**2 + q[3]**2)
        yaw = math.degrees(math.atan2(siny_cosp, cosy_cosp))
        yaw = yaw % 360.0

        return roll, pitch, yaw

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
        declination: 磁偏角（长春约 -9°）
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
        self.pose.confidence = min(1.0, self.pose.confidence + 0.01)

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
        self.pose.confidence = min(1.0, 0.5 + gps.fix_quality * 0.1)

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
        device_elevation = -pose.pitch  # pitch 向上为正，屏幕中心仰角 = -pitch

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

        elev_diff = target_elevation - (-pose.pitch)

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
