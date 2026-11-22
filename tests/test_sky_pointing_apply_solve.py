# SPDX-License-Identifier: MIT
"""解算闭环：融合位姿 + 解算结果 → 新位姿，失败不冒充新结果。

覆盖口径
--------
* 成功且残差在阈值内：位姿换成解算值，方位跨 0° 正确环绕，级别 L3；
* 残差超阈值 / 残差缺失：逐字段保留上一级位姿，级别按来源数退 L2/L1；
* 解算失败：上一级位姿原样保留，reason/error 透传，级别保持；
* 没有上一级位姿：不能凭空造位姿，判 L0；
* 健康快照把 ``solve_within_tolerance`` 透传给唯一的级别判定。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.sky_health import classify_level, health_snapshot  # noqa: E402
from mbdsdr_ai.sky_pointing import PoseObservation  # noqa: E402
from mbdsdr_ai.sky_pointing_pipeline import (  # noqa: E402
    PoseUpdate,
    RESIDUAL_TOLERANCE_DEG,
    SolveOutcome,
    apply_solve,
)

_PREV = {"az_deg": 359.0, "alt_deg": 45.0, "roll_deg": 10.0}


def _outcome(**kwargs) -> SolveOutcome:
    base = {
        "ok": True,
        "az_deg": 1.0,
        "alt_deg": 46.0,
        "roll_deg": 20.0,
        "residual_deg": 0.2,
        "within_tolerance": True,
    }
    base.update(kwargs)
    return SolveOutcome(**base)


def _assert_same_pose(update: PoseUpdate, az: float, alt: float, roll: float):
    assert update.az_deg == pytest.approx(az)
    assert update.alt_deg == pytest.approx(alt)
    assert update.roll_deg == pytest.approx(roll)


# ---------------------------------------------------------------------------
# 成功：采用解算值
# ---------------------------------------------------------------------------
def test_success_applies_solved_pose_and_wraps_azimuth():
    update = apply_solve(_PREV, _outcome())
    assert update.applied is True
    assert update.level == "L3"
    assert update.az_deg == pytest.approx(1.0)
    assert update.alt_deg == pytest.approx(46.0)
    assert update.roll_deg == pytest.approx(20.0)
    assert update.residual_deg == pytest.approx(0.2)
    assert update.reason == ""
    assert update.error == ""


def test_success_keeps_previous_roll_when_solver_has_none():
    update = apply_solve(_PREV, _outcome(roll_deg=None))
    assert update.applied is True
    assert update.roll_deg == pytest.approx(10.0)


def test_success_clamps_altitude_into_valid_range():
    high = apply_solve(_PREV, _outcome(alt_deg=120.0))
    low = apply_solve(_PREV, _outcome(alt_deg=-120.0))
    assert high.alt_deg == pytest.approx(90.0)
    assert low.alt_deg == pytest.approx(-90.0)


def test_pose_update_is_accepted_as_previous_pose():
    previous = PoseUpdate(az_deg=100.0, alt_deg=20.0, roll_deg=5.0,
                          level="L2", applied=False)
    update = apply_solve(previous, _outcome(az_deg=101.0, alt_deg=21.0,
                                            roll_deg=6.0))
    assert update.applied is True
    assert update.level == "L3"
    _assert_same_pose(update, 101.0, 21.0, 6.0)


# ---------------------------------------------------------------------------
# 残差超阈值：保留上一级
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("source_count, expected_level", [(2, "L2"), (1, "L1")])
def test_over_tolerance_keeps_previous_pose(source_count, expected_level):
    update = apply_solve(
        _PREV,
        _outcome(az_deg=10.0, alt_deg=50.0, residual_deg=1.25,
                 within_tolerance=False),
        source_count=source_count,
    )
    assert update.applied is False
    _assert_same_pose(update, 359.0, 45.0, 10.0)
    assert update.level == expected_level
    assert update.residual_deg == pytest.approx(1.25)
    assert f"{RESIDUAL_TOLERANCE_DEG:g}" in update.reason
    assert "1.25" in update.reason


def test_missing_residual_is_not_applied():
    update = apply_solve(_PREV, _outcome(residual_deg=None),
                         source_count=2)
    assert update.applied is False
    _assert_same_pose(update, 359.0, 45.0, 10.0)
    assert update.level == "L2"
    assert "残差" in update.reason
    assert update.error == "residual_missing"


def test_success_with_zero_sources_is_still_l3():
    """解算本身就是位姿证据：没有融合源也不影响「已解算」这一级。"""
    update = apply_solve(_PREV, _outcome(), source_count=0)
    assert update.applied is True
    assert update.level == "L3"


def test_missing_horizontal_coordinates_are_not_applied():
    update = apply_solve(_PREV, _outcome(az_deg=None, alt_deg=None))
    assert update.applied is False
    _assert_same_pose(update, 359.0, 45.0, 10.0)
    assert update.error == "bad_solution"


# ---------------------------------------------------------------------------
# 解算失败：原样保留上一级
# ---------------------------------------------------------------------------
def test_failed_solve_keeps_previous_pose_and_level():
    outcome = SolveOutcome(ok=False, error="solver_failed",
                           reason="解算器抛出异常：RuntimeError: 假故障")
    update = apply_solve(_PREV, outcome, level_before="L2", source_count=2)
    assert update.applied is False
    assert update.level == "L2"
    _assert_same_pose(update, 359.0, 45.0, 10.0)
    assert update.reason == outcome.reason
    assert update.error == "solver_failed"


# ---------------------------------------------------------------------------
# 没有上一级位姿：不允许凭空造
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("previous", [
    None,
    {},
    {"alt_deg": 20.0},
    {"az_deg": float("nan"), "alt_deg": 20.0},
])
def test_no_previous_pose_cannot_apply(previous):
    update = apply_solve(previous, _outcome())
    assert update.applied is False
    assert update.level == "L0"
    assert update.az_deg is None
    assert update.alt_deg is None
    assert "凭空" in update.reason


# ---------------------------------------------------------------------------
# 健康快照：残差口径透传
# ---------------------------------------------------------------------------
NOW = 1_700_000_000.0
_SOLVED = {"available": True, "name": "tetra3", "solved": True}


def _obs(source: str, accuracy: float = 2.0) -> PoseObservation:
    return PoseObservation(source=source, t_utc=NOW - 0.5,
                           az_deg=10.0, alt_deg=20.0,
                           accuracy_deg=accuracy)


def _two_sources():
    return [_obs("imu"), _obs("gnss", accuracy=10.0)]


def test_health_out_of_tolerance_dual_source_is_l2():
    snap = health_snapshot(_two_sources(), NOW, _SOLVED,
                           solve_within_tolerance=False)
    assert snap["level"] == "L2"
    assert "残差超阈值" in snap["reason"]


def test_health_out_of_tolerance_single_source_is_l1():
    snap = health_snapshot([_obs("imu")], NOW, _SOLVED,
                           solve_within_tolerance=False)
    assert snap["level"] == "L1"
    assert "残差超阈值" in snap["reason"]


def test_health_within_tolerance_is_l3():
    snap = health_snapshot(_two_sources(), NOW, _SOLVED,
                           solve_within_tolerance=True)
    assert snap["level"] == "L3"


def test_health_without_tolerance_info_keeps_old_l3():
    snap = health_snapshot(_two_sources(), NOW, _SOLVED)
    assert snap["level"] == "L3"
    assert "残差超阈值" not in snap["reason"]


def test_health_manual_override_wins_over_out_of_tolerance():
    snap = health_snapshot([_obs("imu")], NOW, _SOLVED,
                           manual_override=True,
                           solve_within_tolerance=False)
    assert snap["level"] == "L4"
    assert "手动接管" in snap["reason"]


def test_classify_level_tolerance_semantics():
    # None 保持旧行为；False 时即使解算成功也按来源数降级。
    assert classify_level(True, 2, True, False) == "L3"
    assert classify_level(True, 2, True, False, True) == "L3"
    assert classify_level(True, 2, True, False, False) == "L2"
    assert classify_level(True, 1, True, False, False) == "L1"
    assert classify_level(False, 0, False, False, False) == "L0"
    assert classify_level(True, 2, True, True, False) == "L4"
