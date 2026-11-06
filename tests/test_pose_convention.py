# SPDX-License-Identifier: MIT
"""6DOF 姿态约定与几何的钉子（2026-11-06 重写姿态链后新增）。

为什么需要单独一份
------------------
``mbdsdr_ai/pose.py`` 原先三处各自为政：加速度计公式暗示 z 轴朝上、磁罗盘的
倾斜补偿按 z 轴朝下写、航向又用 ``atan2(-mx, my)``（参量顺序错），结果是
罗盘航向恒偏 90°、倾斜时更乱；``MadgwickFilter`` 的加速度误差项符号也会带出
4–5° 的稳态偏置；AR 指引的仰角符号与注释互相矛盾（"已对准"会报成"向上抬
2×仰角"）。这些都不是"精度差一点"，是**画错方向**。

本文件把约定钉死并把三条几何恒等式逐点锁定：
  机体 x=前、y=左、z=上；水平静止 accel=(0,0,+g)
  roll>0=右倾、pitch>0=抬头、yaw=航向（顺时针自北）
  roll  = atan2(a_y, a_z)
  pitch = atan2(a_x, hypot(a_y, a_z))
  ψ     = atan2(h_y, h_x),  h = Ry(−pitch)·Rx(roll)·m
"真值"由独立构造的旋转矩阵给出（不调用被测实现），故不是自洽回环。
"""

from __future__ import annotations

import itertools
import math

import numpy as np
import pytest

from mbdsdr_ai.pose import (
    IMUData, GPSData, MadgwickFilter, PoseFusion, Pose, ARProjector,
    TiltCompensatedCompass,
)

BN, BD, G = 20.0, 25.0, 9.81          # 地磁水平/垂直分量 (μT)、重力加速度


# ---------------------------------------------------------------------------
# 独立真值：机体系 -> 地球系(north, west, up) 的旋转矩阵
# ---------------------------------------------------------------------------
def _rx(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


def _ry(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def _rz(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def truth_R(yaw_deg: float, pitch_deg: float, roll_deg: float) -> np.ndarray:
    """真值姿态矩阵（抬头为正 → 绕 y 取 −pitch）。"""
    return (_rz(-math.radians(yaw_deg)) @ _ry(-math.radians(pitch_deg))
            @ _rx(math.radians(roll_deg)))


def truth_imu(yaw_deg: float, pitch_deg: float, roll_deg: float):
    R = truth_R(yaw_deg, pitch_deg, roll_deg)
    accel = R.T @ np.array([0.0, 0.0, G])
    mag = R.T @ np.array([BN, 0.0, -BD])
    return accel, mag


def _ang_diff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


# ---------------------------------------------------------------------------
# 1. 加速度计 → roll/pitch（含组合倾斜）
# ---------------------------------------------------------------------------
class TestAccelConvention:
    @pytest.mark.parametrize("roll,pitch", [
        (0, 0), (20, 0), (0, 20), (15, 25), (-10, -20), (35, 45), (-30, 30),
    ])
    def test_level_angles_recovered_exactly(self, roll, pitch):
        accel, _ = truth_imu(0.0, pitch, roll)
        f = MadgwickFilter(beta=0.0)
        # 直接喂两帧（首帧只做时间初始化）
        f.update(IMUData(accel_x=accel[0], accel_y=accel[1], accel_z=accel[2],
                         timestamp=0.0))
        f.update(IMUData(accel_x=accel[0], accel_y=accel[1], accel_z=accel[2],
                         timestamp=0.01))
        # 用互补滤波那条路径核对"加速度计公式本身"（不经融合）
        from mbdsdr_ai.pose import ComplementaryFilter
        cf = ComplementaryFilter(alpha=0.0)     # alpha=0 → 完全信任加速度计
        cf.update(IMUData(accel_x=accel[0], accel_y=accel[1],
                          accel_z=accel[2], timestamp=1.0))   # 首帧只做初始化
        r, p = cf.update(IMUData(accel_x=accel[0], accel_y=accel[1],
                                 accel_z=accel[2], timestamp=1.01))
        assert r == pytest.approx(roll, abs=0.5), (roll, pitch, r, p)
        assert p == pytest.approx(pitch, abs=0.5), (roll, pitch, r, p)


# ---------------------------------------------------------------------------
# 2. 磁罗盘 → 航向（整网格 0.01°）
# ---------------------------------------------------------------------------
class TestCompassConvention:
    @pytest.mark.parametrize("psi,pitch,roll", [
        (0, 0, 0), (45, 0, 0), (90, 0, 0), (180, 0, 0), (270, 0, 0),
        (45, 20, 0), (45, 0, 20), (300, 25, 35), (120, -20, -15),
    ])
    def test_heading_recovered_from_tilted_mag(self, psi, pitch, roll):
        _, mag = truth_imu(psi, pitch, roll)
        comp = TiltCompensatedCompass(declination=0.0)
        got = comp.update(IMUData(mag_x=mag[0], mag_y=mag[1], mag_z=mag[2]),
                          roll=roll, pitch=pitch)
        assert _ang_diff(got, psi) < 0.05, (psi, pitch, roll, got)

    def test_declination_shifts_heading(self):
        _, mag = truth_imu(0.0, 0.0, 0.0)
        comp = TiltCompensatedCompass(declination=-9.0)
        got = comp.update(IMUData(mag_x=mag[0], mag_y=mag[1], mag_z=mag[2]),
                          roll=0.0, pitch=0.0)
        assert _ang_diff(got, -9.0) < 0.05


# ---------------------------------------------------------------------------
# 3. 9DOF 融合收敛到真值姿态
# ---------------------------------------------------------------------------
class TestFusionConvergence:
    @pytest.mark.parametrize("psi,pitch,roll", [
        (0, 0, 0), (90, 0, 0), (180, 0, 20), (270, 25, 0), (45, 20, 20),
    ])
    def test_converges_to_truth(self, psi, pitch, roll):
        accel, mag = truth_imu(psi, pitch, roll)
        f = MadgwickFilter(beta=2.0)
        f.beta_mag = 1.0
        for i in range(1500):
            f.update(IMUData(accel_x=accel[0], accel_y=accel[1],
                             accel_z=accel[2],
                             mag_x=mag[0], mag_y=mag[1], mag_z=mag[2],
                             timestamp=i * 0.01))
        r, p, y = f.get_euler()
        assert _ang_diff(y, psi) < 1.0, (psi, pitch, roll, "yaw", y)
        assert abs(r - roll) < 1.0, (psi, pitch, roll, "roll", r)
        assert abs(p - pitch) < 1.0, (psi, pitch, roll, "pitch", p)

    def test_gyro_positive_z_decreases_heading(self):
        """绕 +z（指向天顶）正转是逆时针（北→西），航向必须**减小**。"""
        f = MadgwickFilter(beta=0.0)
        for i in range(200):
            f.update(IMUData(gyro_z=0.5, timestamp=i * 0.01))
        _, _, y = f.get_euler()
        # 0.5 rad/s × 2 s = 1 rad = 57.3°，逆时针 → 航向 = 360 − 57.3
        assert _ang_diff(y, 360.0 - math.degrees(1.0)) < 2.0, y


# ---------------------------------------------------------------------------
# 4. AR 指引：pitch 就是设备仰角（"已对准"不能报成"向上抬两倍仰角"）
# ---------------------------------------------------------------------------
class TestPointingGuidance:
    def test_aligned_pose_reports_no_error(self):
        ar = ARProjector()
        pose = Pose(roll=0.0, pitch=20.0, yaw=120.0)
        g = ar.get_pointing_guidance(120.0, 20.0, pose)
        assert abs(g["elevation_error_deg"]) < 1e-6, g
        assert abs(g["azimuth_error_deg"]) < 1e-6, g
        assert g["status"] == "已对准", g
        assert g["vertical_direction"] == "仰角正确"

    def test_marker_centered_when_aligned(self):
        ar = ARProjector()
        pose = Pose(roll=0.0, pitch=20.0, yaw=120.0)
        m = ar.project_satellite("ISS", 20.0, 120.0, 600.0, pose)
        assert abs(m.screen_x - 0.5) < 1e-9
        assert abs(m.screen_y - 0.5) < 1e-9
        assert m.in_view

    def test_lower_target_asks_to_lower(self):
        """目标比设备低 → 应提示"向下压"，不是"向上抬"。"""
        ar = ARProjector()
        pose = Pose(roll=0.0, pitch=40.0, yaw=0.0)
        g = ar.get_pointing_guidance(0.0, 10.0, pose)
        assert g["elevation_error_deg"] == pytest.approx(-30.0)
        assert "向下压" in g["vertical_direction"], g


# ---------------------------------------------------------------------------
# 5. GNSS：四元数与欧拉角同姿态、无定位不给置信度
# ---------------------------------------------------------------------------
class TestGpsConsistency:
    def test_quaternion_matches_euler_after_yaw_blend(self):
        pf = PoseFusion()
        pf.pose.yaw = 10.0
        pf.pose.pitch = 5.0
        pf.pose.roll = 2.0
        pf.update_gps(GPSData(latitude=43.9, longitude=125.3, speed=5.0,
                              course=90.0, fix_quality=1))
        # 由 q 反算的航向/姿态必须与 pose 上的欧拉角一致
        from mbdsdr_ai.pose import _rot_from_quat, _heading_from_matrix, \
            _roll_pitch_from_matrix
        R = _rot_from_quat(np.array([pf.pose.q_w, pf.pose.q_x,
                                     pf.pose.q_y, pf.pose.q_z]))
        r, p = _roll_pitch_from_matrix(R)
        assert _ang_diff(_heading_from_matrix(R), pf.pose.yaw) < 1e-6
        assert abs(r - pf.pose.roll) < 1e-6
        assert abs(p - pf.pose.pitch) < 1e-6

    def test_no_fix_gives_zero_confidence(self):
        pf = PoseFusion()
        pf.update_gps(GPSData(latitude=43.9, longitude=125.3, fix_quality=0))
        assert pf.pose.confidence == 0.0

    def test_rtk_fix_gives_high_confidence(self):
        pf = PoseFusion()
        pf.update_gps(GPSData(latitude=43.9, longitude=125.3, fix_quality=4))
        assert pf.pose.confidence > 0.9


# ---------------------------------------------------------------------------
# 6. 反证：约定一旦被改回旧式，本文件的断言必须立刻红
# ---------------------------------------------------------------------------
def test_old_heading_formula_is_detectably_wrong():
    """旧式 atan2(−mx, my) 在正北朝向上给出 270°，本文件的公式给出 0°。"""
    _, mag = truth_imu(0.0, 0.0, 0.0)
    old_style = math.degrees(math.atan2(-mag[0], mag[1])) % 360.0
    new_style = TiltCompensatedCompass(0.0).update(
        IMUData(mag_x=mag[0], mag_y=mag[1], mag_z=mag[2]), 0.0, 0.0)
    assert _ang_diff(old_style, 0.0) > 45.0      # 旧式确实偏 90°
    assert _ang_diff(new_style, 0.0) < 0.05
