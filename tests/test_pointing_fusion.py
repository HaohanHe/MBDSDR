# SPDX-License-Identifier: MIT
"""指向融合契约的行为测试。

全部用合成数据：给定一个已知的「传感器帧 → 天球帧」旋转，按它生成星对准
观测与 IMU 姿态，再验证融合器解回来的方向、质量分级与不确定度。

这里刻意不 mock 相机：视觉解算在本模块里只是一个「绝对参考」输入，
有没有相机不影响契约成立与否。
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.pointing_fusion import (  # noqa: E402
    AlignPair, PointingFusion, QUALITY_ALIGNED, QUALITY_COARSE,
    QUALITY_SOLVED, QUALITY_UNAVAILABLE, attitude_frame, azalt_to_unit,
    frame_to_attitude, solve_alignment, unit_to_azalt,
)


# ---------------------------------------------------------------------------
# 合成工具
# ---------------------------------------------------------------------------

def _rot(axis: str, deg: float) -> np.ndarray:
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    if axis == "z":
        return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
    if axis == "y":
        return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


def _sample_pair(rotation: np.ndarray, true_az: float,
                 true_alt: float) -> AlignPair:
    """按已知旋转生成一对观测：传感器指向 = Rᵀ·真实指向。"""
    sensor = rotation.T @ azalt_to_unit(true_az, true_alt)
    sensor_az, sensor_alt = unit_to_azalt(sensor)
    return AlignPair(sensor_az_deg=sensor_az, sensor_alt_deg=sensor_alt,
                     true_az_deg=true_az, true_alt_deg=true_alt)


def _angdiff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


# ---------------------------------------------------------------------------
# 星对准
# ---------------------------------------------------------------------------

def test_two_star_alignment_recovers_rotation():
    rotation = _rot("z", 25.0) @ _rot("y", 8.0) @ _rot("x", 12.0)
    pairs = [_sample_pair(rotation, 30.0, 40.0),
             _sample_pair(rotation, 210.0, 25.0)]

    alignment, reason = solve_alignment(pairs)
    assert alignment is not None, reason
    assert alignment.points == 2
    assert alignment.rms_deg < 1e-6

    # 第三方向：解回来的旋转必须和真旋转一致
    sensor_az, sensor_alt = 10.0, 45.0
    az, alt, _ = alignment.apply(sensor_az, sensor_alt, 0.0)
    want_az, want_alt = unit_to_azalt(
        rotation @ azalt_to_unit(sensor_az, sensor_alt))
    assert _angdiff(az, want_az) < 1e-6
    assert abs(alt - want_alt) < 1e-6


def test_alignment_keeps_roll_when_rotating_about_zenith():
    rotation = _rot("z", 30.0)
    pairs = [_sample_pair(rotation, 100.0, 35.0),
             _sample_pair(rotation, 250.0, 20.0)]

    alignment, reason = solve_alignment(pairs)
    assert alignment is not None, reason

    az, alt, roll = alignment.apply(10.0, 45.0, 0.0)
    # _rot("z", +30) 是数学正方向（自上看逆时针），方位角自北向东为正，
    # 所以真实方位是 10 - 30 = 340，横滚不受绕天顶旋转影响
    assert _angdiff(az, 340.0) < 1e-6
    assert abs(alt - 45.0) < 1e-6
    assert abs(roll) < 1e-6


def test_collinear_pairs_are_rejected():
    pair = AlignPair(10.0, 30.0, 50.0, 30.0)
    alignment, reason = solve_alignment([pair, pair])
    assert alignment is None
    assert "太接近" in reason


def test_misidentified_star_is_rejected():
    # 传感器夹角 60°，天球夹角 90°——不是同一个刚体，多半认错了星
    pair_a = AlignPair(0.0, 30.0, 0.0, 30.0)
    pair_b = AlignPair(60.0, 0.0, 90.0, 0.0)
    alignment, reason = solve_alignment([pair_a, pair_b])
    assert alignment is None
    assert "夹角" in reason


def test_alignment_needs_two_stars():
    alignment, reason = solve_alignment([AlignPair(0.0, 30.0, 0.0, 30.0)])
    assert alignment is None
    assert "2 颗星" in reason


# ---------------------------------------------------------------------------
# 缺失与过期：宁可不给，也不给假方向
# ---------------------------------------------------------------------------

def test_missing_attitude_is_unavailable_not_zero():
    fusion = PointingFusion()
    solution = fusion.solution(now=100.0)
    assert solution.quality == QUALITY_UNAVAILABLE
    assert solution.az_deg is None and solution.alt_deg is None
    assert solution.uncertainty_deg is None
    assert "没有姿态" in solution.warnings[0]


def test_stale_attitude_is_unavailable():
    fusion = PointingFusion()
    fusion.update_attitude(10.0, 20.0, 0.0, timestamp=0.0)
    solution = fusion.solution(now=1000.0)
    assert solution.quality == QUALITY_UNAVAILABLE
    assert solution.az_deg is None
    assert "过期" in solution.warnings[0]


def test_visual_fix_without_attitude_is_refused():
    fusion = PointingFusion()
    with pytest.raises(ValueError):
        fusion.update_visual_fix(100.0, 30.0, residual_deg=0.1, timestamp=0.0)


# ---------------------------------------------------------------------------
# 质量分级与不确定度
# ---------------------------------------------------------------------------

def test_uncalibrated_imu_is_coarse_and_cannot_name_star():
    fusion = PointingFusion()
    fusion.update_attitude(10.0, 20.0, 0.0, timestamp=0.0)
    solution = fusion.solution(now=0.0)
    assert solution.quality == QUALITY_COARSE
    assert solution.can_name_star is False
    assert solution.can_name_region is False
    assert "未做星对准" in solution.warnings[-1]


def test_aligned_starts_precise_then_drifts_out_of_star_accuracy():
    rotation = _rot("z", 20.0)
    pairs = [_sample_pair(rotation, 30.0, 40.0),
             _sample_pair(rotation, 200.0, 25.0)]
    alignment, reason = solve_alignment(pairs, timestamp=0.0)
    assert alignment is not None, reason

    fusion = PointingFusion()
    fusion.set_alignment(alignment)
    fusion.update_attitude(5.0, 30.0, 0.0, timestamp=0.0)

    fresh = fusion.solution(now=0.0)
    assert fresh.quality == QUALITY_ALIGNED
    assert fresh.can_name_star is True
    assert fresh.uncertainty_deg < 0.5

    # 60 s 后漂移 3°，已认不了单颗星；50 s 时 2.5°，还够认一片天区
    drifted = fusion.solution(now=60.0)
    assert drifted.quality == QUALITY_ALIGNED
    assert drifted.uncertainty_deg == pytest.approx(3.0, abs=1e-3)
    assert drifted.can_name_star is False
    assert fusion.solution(now=50.0).can_name_region is True

    # 超过对准有效期：降级为裸 IMU，并说明原因（姿态本身仍在流）
    fusion.update_attitude(5.0, 30.0, 0.0, timestamp=700.0)
    expired = fusion.solution(now=700.0)
    assert expired.quality == QUALITY_COARSE
    assert any("星对准已过去" in w for w in expired.warnings)


def test_visual_fix_is_solved_while_fresh():
    fusion = PointingFusion()
    fusion.update_attitude(10.0, 20.0, 0.0, timestamp=0.0)
    fusion.update_visual_fix(100.0, 30.0, residual_deg=0.1, timestamp=0.0)

    fresh = fusion.solution(now=0.5)
    assert fresh.quality == QUALITY_SOLVED
    assert fresh.source == "camera+solve"
    assert fresh.uncertainty_deg == pytest.approx(0.125, abs=1e-9)
    assert _angdiff(fresh.az_deg, 100.0) < 1e-9
    assert fresh.can_name_star is True


def test_visual_fix_expires_and_falls_back_to_alignment():
    rotation = _rot("z", 20.0)
    alignment, reason = solve_alignment(
        [_sample_pair(rotation, 30.0, 40.0),
         _sample_pair(rotation, 200.0, 25.0)], timestamp=0.0)
    assert alignment is not None, reason

    fusion = PointingFusion()
    fusion.set_alignment(alignment)
    fusion.update_attitude(10.0, 20.0, 0.0, timestamp=0.0)
    fusion.update_visual_fix(*alignment.apply(10.0, 20.0, 0.0)[:2],
                             residual_deg=0.1, timestamp=0.0)

    assert fusion.solution(now=1.0).quality == QUALITY_SOLVED
    later = fusion.solution(now=10.0)
    assert later.quality == QUALITY_ALIGNED
    assert later.source == "imu+align"


def test_visual_fix_extrapolates_with_imu_motion():
    """解算之后靠 IMU 相对运动外推：指向应跟着传感器一起转。

    解算器给出天球横滚时，重置出来的旋转与真实旋转逐位一致；这也是
    ``update_visual_fix`` 把 ``true_roll_deg`` 单独列出来的原因。
    """
    rotation = _rot("z", 15.0) @ _rot("x", 6.0)
    sensor_az, sensor_alt, sensor_roll = 10.0, 20.0, 0.0
    true_az, true_alt, true_roll = frame_to_attitude(
        rotation @ attitude_frame(sensor_az, sensor_alt, sensor_roll))

    fusion = PointingFusion(solve_valid_s=5.0)
    fusion.update_attitude(sensor_az, sensor_alt, sensor_roll, timestamp=0.0)
    fusion.update_visual_fix(true_az, true_alt, residual_deg=0.0,
                             timestamp=0.0, true_roll_deg=true_roll)

    # 传感器转了 3°，解算还没过期
    fusion.update_attitude(sensor_az + 3.0, sensor_alt, sensor_roll, timestamp=1.0)
    solution = fusion.solution(now=1.0)
    assert solution.quality == QUALITY_SOLVED

    want_az, want_alt = unit_to_azalt(
        rotation @ azalt_to_unit(sensor_az + 3.0, sensor_alt))
    assert _angdiff(solution.az_deg, want_az) < 1e-6
    assert abs(solution.alt_deg - want_alt) < 1e-6


def test_readonly_contract_exposes_reasons():
    fusion = PointingFusion()
    fusion.update_attitude(10.0, 20.0, 0.0, timestamp=0.0)
    data = fusion.solution(now=0.0).to_dict()
    assert data["quality"] == QUALITY_COARSE
    assert data["available"] is True
    assert data["warnings"] and isinstance(data["warnings"], list)
    assert data["can_name_star"] is False


def test_attitude_frame_roundtrip():
    for az, alt, roll in ((0.0, 0.0, 0.0), (123.0, 45.0, 30.0),
                          (300.0, 80.0, -20.0), (10.0, 89.99, 15.0)):
        got = frame_to_attitude(attitude_frame(az, alt, roll))
        assert _angdiff(got[0], az) < 1e-6
        assert abs(got[1] - alt) < 1e-6
        assert _angdiff(got[2], roll) < 1e-6
