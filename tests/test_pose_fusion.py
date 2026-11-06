# SPDX-License-Identifier: MIT
"""
pose.py 姿态融合回归测试。

全部输入均为纯数值注入：无 BMI260 / TMAG5273 驱动，数值不代表真实硬件读数。
测试不写文件、不联网、不启动外部进程，仅做内存中的数值断言。
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.pose import IMUData, MadgwickFilter, PoseFusion

DT = 0.01


def _imu(i, accel=(0.0, 0.0, 1.0), gyro=(0.0, 0.0, 0.0), mag=(0.0, 0.0, 0.0)):
    """构造一帧纯数值 IMU（timestamp 单调递增，保证 dt=DT）。"""
    return IMUData(
        accel_x=accel[0], accel_y=accel[1], accel_z=accel[2],
        gyro_x=gyro[0], gyro_y=gyro[1], gyro_z=gyro[2],
        mag_x=mag[0], mag_y=mag[1], mag_z=mag[2],
        timestamp=i * DT,
    )


# 首次 update 会命中 _last_time 初始化语义并直接返回 q.copy()，
# 故各用例均以多步循环推进，不依赖单步结果。


def test_beta_zero_vs_positive_changes_quaternion():
    """同样输入下 beta=0 与 beta>0 的四元数不同，证明观测修正真正进入积分。"""
    a = 0.2
    tilted = (math.sin(a), 0.0, math.cos(a))  # 加速度计与估计重力不一致
    f0 = MadgwickFilter(beta=0.0)
    f1 = MadgwickFilter(beta=1.0)
    for i in range(30):
        sample = _imu(i, accel=tilted)
        q0 = f0.update(sample)
        q1 = f1.update(sample)
    assert not np.allclose(q0, q1)
    # beta=0：误差修正被关闭且 gyro 为零，应始终保持单位四元数
    assert np.allclose(q0, [1.0, 0.0, 0.0, 0.0])


def _run_bias(beta, bias_x, bias_y, steps):
    mf = MadgwickFilter(beta=beta)
    rolls, pitches = [], []
    for i in range(steps + 1):
        mf.update(_imu(i, accel=(0.0, 0.0, 1.0), gyro=(bias_x, bias_y, 0.0)))
        r, p, _ = mf.get_euler()
        rolls.append(r)
        pitches.append(p)
    return rolls, pitches


def test_accel_correction_prevents_roll_pitch_drift():
    """静止纯重力 + 恒定陀螺零偏：beta>0 收敛接近 0，beta=0 持续漂移。"""
    steps = 600
    bias_x, bias_y = 0.02, 0.014
    r0, p0 = _run_bias(0.0, bias_x, bias_y, steps)
    r1, p1 = _run_bias(2.0, bias_x, bias_y, steps)

    # beta>0：roll/pitch 收敛到接近 0，且末尾不再持续漂移
    assert abs(r1[-1]) < 2.0
    assert abs(p1[-1]) < 2.0
    assert abs(r1[-1] - r1[-100]) < 0.1
    assert abs(p1[-1] - p1[-100]) < 0.1

    # beta=0：roll/pitch 明显漂移，且末尾仍在线性增长。
    # 符号按 2026-11-06 钉死的机体约定：x=前、y=左、z=上，pitch>0=抬头。
    # 绕 +x 正转 = 右翼下沉 → roll 增大；绕 **+y**（指左）正转 = 抬头方向为负，
    # 即 x̂ 转向 −z → pitch 减小。这两条一起把"约定"钉住，改约定会立刻红。
    assert abs(r0[-1]) > 5.0
    assert abs(p0[-1]) > 3.0
    assert (r0[-1] - r0[-100]) > 0.5
    assert (p0[-1] - p0[-100]) < -0.3

    # 修正显著抑制漂移：beta=0 的最终误差远大于 beta>0
    assert abs(r0[-1]) > 5.0 * abs(r1[-1])
    assert abs(p0[-1]) > 5.0 * abs(p1[-1])


def test_mag_all_zero_degrades_to_6dof_without_fabricated_heading():
    """mag 全零：不崩、不产生 NaN、诚实降级为 6DOF、不编造航向。"""
    mf = MadgwickFilter(beta=1.0)
    for i in range(50):
        q = mf.update(_imu(i, accel=(0.0, 0.0, 1.0)))  # gyro/mag 均全零
        assert np.all(np.isfinite(q))
    r, p, y = mf.get_euler()
    assert abs(y) < 1.0, "mag 全零时不应凭空产生航向"
    assert abs(r) < 1.0 and abs(p) < 1.0

    # 6DOF 仍工作：倾斜加速度下加速度修正仍把姿态拉离单位四元数
    a = 0.2
    mf2 = MadgwickFilter(beta=1.0)
    for i in range(50):
        q = mf2.update(_imu(i, accel=(math.sin(a), 0.0, math.cos(a))))
    assert not np.allclose(q, [1.0, 0.0, 0.0, 0.0])


def test_mag_nonzero_enters_integration():
    """mag 非零时倾斜补偿航向修正进入积分，且收敛到磁矢量隐含的航向。

    2026-11-06 重写：旧用例注入 mag=(0.2,0,0.4)，在**正确**的公式下这恰好
    就是"航向 0°"的磁场（水平分量全在机体 x 轴），修正量本就是 0——旧用例
    之所以能通过，是因为旧式 atan2(−mx, my) 把它算成了 −90°（错的）。
    现在改用隐含航向 56.3° 的磁矢量（atan2(0.3, 0.2)），并直接断言收敛值。
    """
    def run(mag):
        mf = MadgwickFilter(beta=1.0)
        for i in range(2000):
            mf.update(_imu(i, accel=(0.0, 0.0, 1.0), mag=mag))
        return mf.q.copy(), mf.get_euler()

    q_zero, e_zero = run((0.0, 0.0, 0.0))
    q_mag, e_mag = run((0.2, 0.3, 0.4))
    assert np.all(np.isfinite(q_mag))
    assert not np.allclose(q_zero, q_mag)
    # 航向 = atan2(m_y, m_x)（水平、机体 y 指左 → 顺时针自北）
    want = math.degrees(math.atan2(0.3, 0.2))
    d = abs((e_mag[2] - want + 180) % 360 - 180)
    assert d < 2.0, f"磁航向应收敛到 {want:.1f}°，实际 {e_mag[2]:.1f}°"
    # mag 全零那一组不编造航向
    assert abs(e_zero[2]) < 1.0


def test_confidence_increases_with_consistent_observations():
    """观测有效且与估计一致（静止纯重力）时 confidence 上升。"""
    pf = PoseFusion()
    c0 = pf.pose.confidence
    for i in range(50):
        pf.update_imu(_imu(i, accel=(0.0, 0.0, 1.0)))
    assert pf.pose.confidence > c0
    assert 0.0 <= pf.pose.confidence <= 1.0


def test_confidence_decreases_after_zero_imu():
    """连续全零 IMU 后 confidence 下降，并始终钳在 [0,1]。"""
    pf = PoseFusion()
    for i in range(200):
        pf.update_imu(_imu(i, accel=(0.0, 0.0, 1.0)))
    c_high = pf.pose.confidence
    assert c_high > 0.0
    for i in range(200, 250):
        pf.update_imu(_imu(i, accel=(0.0, 0.0, 0.0),
                           gyro=(0.0, 0.0, 0.0), mag=(0.0, 0.0, 0.0)))
    c_low = pf.pose.confidence
    assert c_low < c_high
    assert 0.0 <= c_low <= 1.0
