# SPDX-License-Identifier: MIT
"""指向融合：IMU 姿态 + GNSS 站址/时刻 + 视觉解算 → 一个可判读的指向。

这个模块解决的是「我指的那颗星是谁」之前的一步：**我现在到底指着哪**。
它不产生观测，只做合成与裁定，所以三条输入都没有时它必须说「不知道」，
而不是给出一个看起来像答案的 0°/0°。

三路输入的角色（与 PiFinder / StarSense 的工程实践同构）：

- **IMU**：相对运动。两次绝对参考之间靠它外推，但没有绝对基准会漂；
- **GNSS**：站址与时刻。它不告诉你指向，但决定了「这个 az/alt 对应哪颗星」；
- **视觉解算**：绝对参考。一次解算把漂移清零，之后继续由 IMU 外推。

质量分级（``PointingSolution.quality``）是本模块的核心产出：

===========  ==========================================  ==================
quality      含义                                        典型不确定度
===========  ==========================================  ==================
unavailable  缺姿态 / 姿态过期 / 光轴不可得              ——（返回 None）
coarse       只有裸 IMU，没做过星对准                    十几度，不能认星
aligned      已用恒星对准，IMU 外推                      对准残差 + 漂移×时长
solved       视觉解算在有效期内                          解算残差 + 漂移×时长
===========  ==========================================  ==================

**漂移是必须显式算进来的**：把陀螺仪当成「对准一次就能一直用」是这类系统
最常见的谎。默认按 0.05°/s 计，也就是对准 20 秒后已经不确定到 1°，不足以
认单颗星。想认星就得持续解算，或者接受它只说「这一片大概是什么」。

坐标系约定：az 自北向东（度），alt 自地平（度），均为 ENU 右手系；
roll 为绕光轴的横滚角，正方向把「上」转向「右」。天球与传感器共用这套
约定，两者之间只差一个刚体旋转——这正是星对准要解的那个量。

与 ``pose.py`` 的分工：``pose.py`` 负责把 IMU/GNSS 原始数据变成位姿
（滤波、四元数），本模块负责把位姿对准到天球并裁定「这个指向能拿来做什么」。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

#: 质量分级
QUALITY_UNAVAILABLE = "unavailable"
QUALITY_COARSE = "coarse"
QUALITY_ALIGNED = "aligned"
QUALITY_SOLVED = "solved"

#: 认单颗星 / 认一片天区所需的不确定度上限（度）
NAME_STAR_MAX_DEG = 0.5
NAME_REGION_MAX_DEG = 3.0

#: 两颗星方向夹角过小则无法定姿（度）
_MIN_SEPARATION_DEG = 5.0

#: 两星夹角与天球夹角的一致性容差（度）；超出说明认错了星
_MAX_ANGLE_MISMATCH_DEG = 2.0


# ═══════════════════════════════════════════════════════
# 坐标换算
# ═══════════════════════════════════════════════════════

def azalt_to_unit(az_deg: float, alt_deg: float) -> np.ndarray:
    """az/alt（度）→ ENU 单位向量。"""
    az = math.radians(float(az_deg))
    alt = math.radians(float(alt_deg))
    return np.array([
        math.cos(alt) * math.sin(az),   # 东
        math.cos(alt) * math.cos(az),   # 北
        math.sin(alt),                  # 天
    ], dtype=float)


def unit_to_azalt(vec: Sequence[float]) -> Tuple[float, float]:
    """ENU 单位向量 → (az, alt)（度）。零向量抛 ValueError，不返回 0/0。"""
    v = np.asarray(vec, dtype=float)
    norm = float(np.linalg.norm(v))
    if norm < 1e-12:
        raise ValueError("零向量没有方向")
    v = v / norm
    alt = math.degrees(math.asin(max(-1.0, min(1.0, float(v[2])))))
    az = math.degrees(math.atan2(float(v[0]), float(v[1]))) % 360.0
    return az, alt


def _up_reference(boresight: np.ndarray) -> np.ndarray:
    """给定光轴，返回 roll=0 时的「上」向量。

    常规情况取天顶在光轴垂面上的投影；接近天顶时该投影退化，改用北向。
    这个分支必须显式，否则指向天顶时 roll 会变成 atan2(0,0)。
    """
    zenith = np.array([0.0, 0.0, 1.0])
    if abs(float(boresight[2])) > 0.999:
        ref = np.array([0.0, 1.0, 0.0])
    else:
        ref = zenith
    up = ref - float(np.dot(ref, boresight)) * boresight
    return up / np.linalg.norm(up)


def attitude_frame(az_deg: float, alt_deg: float, roll_deg: float) -> np.ndarray:
    """(az, alt, roll) → 3×3 姿态帧，列向量为 [光轴, 上, 右]。"""
    b = azalt_to_unit(az_deg, alt_deg)
    u0 = _up_reference(b)
    r0 = np.cross(b, u0)
    roll = math.radians(float(roll_deg))
    u = u0 * math.cos(roll) + r0 * math.sin(roll)
    return np.column_stack([b, u, r0 * math.cos(roll) - u0 * math.sin(roll)])


def frame_to_attitude(frame: np.ndarray) -> Tuple[float, float, float]:
    """姿态帧 → (az, alt, roll)。"""
    b = frame[:, 0] / np.linalg.norm(frame[:, 0])
    u = frame[:, 1]
    u = u - float(np.dot(u, b)) * b
    u = u / np.linalg.norm(u)
    u0 = _up_reference(b)
    r0 = np.cross(b, u0)
    roll = math.degrees(math.atan2(float(np.dot(u, r0)), float(np.dot(u, u0))))
    az, alt = unit_to_azalt(b)
    return az, alt, roll


def _angle_between(a: np.ndarray, b: np.ndarray) -> float:
    """两个单位向量的夹角（度）。"""
    c = max(-1.0, min(1.0, float(np.dot(a, b))))
    return math.degrees(math.acos(c))


# ═══════════════════════════════════════════════════════
# 星对准
# ═══════════════════════════════════════════════════════

@dataclass(frozen=True)
class AlignPair:
    """一对「传感器说指着这 / 实际是那颗星」的观测。

    传感器指向来自 IMU（含安装偏置），真实指向来自用户目镜里认出的星
    （或视觉解算结果）。
    """
    sensor_az_deg: float
    sensor_alt_deg: float
    true_az_deg: float
    true_alt_deg: float
    label: str = ""


@dataclass(frozen=True)
class Alignment:
    """传感器帧 → 天球帧的刚体旋转，附残差与取样时刻。"""
    rotation: np.ndarray
    rms_deg: float
    points: int
    timestamp: float

    def apply(self, sensor_az_deg: float, sensor_alt_deg: float,
              sensor_roll_deg: float = 0.0) -> Tuple[float, float, float]:
        """把传感器姿态映射到天球姿态。"""
        frame = attitude_frame(sensor_az_deg, sensor_alt_deg, sensor_roll_deg)
        return frame_to_attitude(self.rotation @ frame)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rms_deg": self.rms_deg,
            "points": self.points,
            "timestamp": self.timestamp,
        }


def solve_alignment(pairs: Sequence[AlignPair],
                    timestamp: float = 0.0) -> Tuple[Optional[Alignment], str]:
    """用 ≥2 对不共线观测解传感器帧 → 天球帧的旋转。

    返回 ``(Alignment, "")`` 或 ``(None, 原因)``。失败原因必须能归因：
    星太少、两颗星方向太近、两星夹角与天球夹角对不上（多半认错了星）。
    """
    usable = list(pairs)
    if len(usable) < 2:
        return None, f"星对准至少需要 2 颗星，当前只有 {len(usable)} 颗"

    sensor_vecs = [azalt_to_unit(p.sensor_az_deg, p.sensor_alt_deg)
                   for p in usable]
    true_vecs = [azalt_to_unit(p.true_az_deg, p.true_alt_deg) for p in usable]

    # 共线性：两颗星方向太近，绕它们连线的旋转不受约束，解出来的姿态是假的
    for i in range(len(sensor_vecs)):
        for j in range(i + 1, len(sensor_vecs)):
            sep = _angle_between(sensor_vecs[i], sensor_vecs[j])
            if sep < _MIN_SEPARATION_DEG:
                return None, (f"两颗星方向太接近（{sep:.1f}°，需 ≥"
                              f"{_MIN_SEPARATION_DEG:.0f}°），无法定姿")

    # 刚性一致性：传感器夹角与天球夹角必须一致，否则是把甲星认成了乙星
    for i in range(len(sensor_vecs)):
        for j in range(i + 1, len(sensor_vecs)):
            sep_s = _angle_between(sensor_vecs[i], sensor_vecs[j])
            sep_t = _angle_between(true_vecs[i], true_vecs[j])
            if abs(sep_s - sep_t) > _MAX_ANGLE_MISMATCH_DEG:
                return None, (f"两星夹角对不上（传感器 {sep_s:.1f}° / 天球 "
                              f"{sep_t:.1f}°），请核对星名是否认错")

    # Wahba：R = argmin Σ|R·s_i − t_i|²，SVD 闭式解
    h = np.zeros((3, 3))
    for s, t in zip(sensor_vecs, true_vecs):
        h += np.outer(s, t)
    u_mat, _, vt = np.linalg.svd(h)
    d = np.diag([1.0, 1.0, float(np.sign(np.linalg.det(vt.T @ u_mat.T)))])
    rotation = vt.T @ d @ u_mat.T

    residuals = [_angle_between(rotation @ s, t)
                 for s, t in zip(sensor_vecs, true_vecs)]
    rms = math.sqrt(sum(r * r for r in residuals) / len(residuals))
    return Alignment(rotation=rotation, rms_deg=rms, points=len(usable),
                     timestamp=timestamp), ""


# ═══════════════════════════════════════════════════════
# 融合与裁定
# ═══════════════════════════════════════════════════════

@dataclass(frozen=True)
class PointingSolution:
    """一次指向的结论：方向、质量、不确定度、缺什么。"""
    az_deg: Optional[float]
    alt_deg: Optional[float]
    roll_deg: Optional[float]
    quality: str
    uncertainty_deg: Optional[float]
    age_s: Optional[float]
    source: str
    warnings: Tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return self.quality != QUALITY_UNAVAILABLE

    @property
    def can_name_star(self) -> bool:
        return (self.uncertainty_deg is not None
                and self.uncertainty_deg <= NAME_STAR_MAX_DEG)

    @property
    def can_name_region(self) -> bool:
        return (self.uncertainty_deg is not None
                and self.uncertainty_deg <= NAME_REGION_MAX_DEG)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "az_deg": self.az_deg,
            "alt_deg": self.alt_deg,
            "roll_deg": self.roll_deg,
            "quality": self.quality,
            "uncertainty_deg": self.uncertainty_deg,
            "age_s": self.age_s,
            "source": self.source,
            "warnings": list(self.warnings),
            "available": self.available,
            "can_name_star": self.can_name_star,
            "can_name_region": self.can_name_region,
        }


@dataclass
class PointingFusion:
    """三源融合器。

    调用顺序不设强制状态机，但语义是固定的：
    ``update_attitude`` 每帧调；``set_alignment`` 在星对准后调；
    ``update_visual_fix`` 每次解算成功调；``solution`` 取结论。
    """
    drift_deg_per_s: float = 0.05
    solve_valid_s: float = 3.0
    alignment_valid_s: float = 600.0
    attitude_stale_s: float = 300.0
    coarse_uncertainty_deg: float = 15.0
    _attitude: Optional[Tuple[float, float, float]] = field(default=None, repr=False)
    _attitude_ts: Optional[float] = field(default=None, repr=False)
    _alignment: Optional[Alignment] = field(default=None, repr=False)
    _fix_ts: Optional[float] = field(default=None, repr=False)

    def update_attitude(self, sensor_az_deg: float, sensor_alt_deg: float,
                        sensor_roll_deg: float, timestamp: float) -> None:
        """喂入一帧 IMU 姿态（光轴在传感器帧里的 az/alt/roll）。"""
        self._attitude = (float(sensor_az_deg), float(sensor_alt_deg),
                          float(sensor_roll_deg))
        self._attitude_ts = float(timestamp)

    def set_alignment(self, alignment: Alignment) -> None:
        """设置星对准结果；会清掉视觉解算的「新鲜」标记。"""
        self._alignment = alignment
        self._fix_ts = None

    def update_visual_fix(self, true_az_deg: float, true_alt_deg: float,
                          residual_deg: float, timestamp: float,
                          true_roll_deg: float = 0.0) -> None:
        """喂入一次视觉解算（绝对参考），用它重置传感器帧 → 天球帧的旋转。

        没有当前姿态就拒收：一条没有对应传感器姿态的绝对参考无法建立对应
        关系，收下只会制造「看起来有解」的假象。

        ``true_roll_deg`` 只有解算器真的给出天球横滚时才传；省略时按 0 处理，
        此时 az/alt 仍然准确（光轴映射与横滚无关），只有画面旋转角不可信。
        """
        if self._attitude is None:
            raise ValueError("收到视觉解算但没有当前姿态，无法建立参考")
        sensor_frame = attitude_frame(*self._attitude)
        true_frame = attitude_frame(true_az_deg, true_alt_deg, true_roll_deg)
        self._alignment = Alignment(
            rotation=true_frame @ sensor_frame.T,
            rms_deg=max(0.0, float(residual_deg)),
            points=1,
            timestamp=float(timestamp),
        )
        self._fix_ts = float(timestamp)

    def solution(self, now: float) -> PointingSolution:
        """在 ``now`` 时刻给出结论。缺失一律显式。"""
        if self._attitude is None or self._attitude_ts is None:
            return PointingSolution(
                az_deg=None, alt_deg=None, roll_deg=None,
                quality=QUALITY_UNAVAILABLE, uncertainty_deg=None,
                age_s=None, source="none",
                warnings=("没有姿态数据：IMU 未连接或尚未输出有效姿态",))

        attitude_age = max(0.0, float(now) - self._attitude_ts)
        if attitude_age > self.attitude_stale_s:
            return PointingSolution(
                az_deg=None, alt_deg=None, roll_deg=None,
                quality=QUALITY_UNAVAILABLE, uncertainty_deg=None,
                age_s=attitude_age, source="imu",
                warnings=(f"姿态数据已过期 {attitude_age:.0f}s"
                          f"（上限 {self.attitude_stale_s:.0f}s）",))

        warnings: List[str] = []

        # 1) 视觉解算有效期：解算已把旋转重置到绝对参考，之后由 IMU 外推
        if (self._alignment is not None and self._fix_ts is not None
                and max(0.0, float(now) - self._fix_ts) <= self.solve_valid_s):
            fix_age = max(0.0, float(now) - self._fix_ts)
            az, alt, roll = self._alignment.apply(*self._attitude)
            return PointingSolution(
                az_deg=az, alt_deg=alt, roll_deg=roll,
                quality=QUALITY_SOLVED,
                uncertainty_deg=self._alignment.rms_deg
                + self.drift_deg_per_s * fix_age,
                age_s=fix_age, source="camera+solve", warnings=tuple(warnings))

        # 2) 星对准后的 IMU 外推
        if self._alignment is not None:
            align_age = max(0.0, float(now) - self._alignment.timestamp)
            az, alt, roll = self._alignment.apply(*self._attitude)
            if align_age <= self.alignment_valid_s:
                return PointingSolution(
                    az_deg=az, alt_deg=alt, roll_deg=roll,
                    quality=QUALITY_ALIGNED,
                    uncertainty_deg=self._alignment.rms_deg
                    + self.drift_deg_per_s * align_age,
                    age_s=align_age, source="imu+align",
                    warnings=tuple(warnings))
            warnings.append(
                f"上次星对准已过去 {align_age / 60.0:.0f} 分钟，漂移已不可忽略")

        # 3) 裸 IMU：只给大致方位
        az, alt, roll = frame_to_attitude(attitude_frame(*self._attitude))
        warnings.append("未做星对准：方向只可当大致方位，不能据此认星")
        return PointingSolution(
            az_deg=az, alt_deg=alt, roll_deg=roll,
            quality=QUALITY_COARSE,
            uncertainty_deg=self.coarse_uncertainty_deg,
            age_s=attitude_age, source="imu", warnings=tuple(warnings))


__all__ = [
    "AlignPair",
    "Alignment",
    "PointingFusion",
    "PointingSolution",
    "QUALITY_ALIGNED",
    "QUALITY_COARSE",
    "QUALITY_SOLVED",
    "QUALITY_UNAVAILABLE",
    "NAME_REGION_MAX_DEG",
    "NAME_STAR_MAX_DEG",
    "attitude_frame",
    "azalt_to_unit",
    "frame_to_attitude",
    "solve_alignment",
    "unit_to_azalt",
]
