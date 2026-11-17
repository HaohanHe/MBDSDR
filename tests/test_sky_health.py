# SPDX-License-Identifier: MIT
"""AR 指向链路健康快照的纯逻辑测试（离线、无 Qt、无设备）。

覆盖口径
--------
* 级别 L0–L4 的唯一判定：无观测 / 单源 / 多源融合 / 解算成功 / 手动接管；
* 每个 source 的最近观测年龄与可用性（陈旧与时间非法都不冒充可用）；
* 相机超过 5 s 无帧标记并降级；未提供帧时间按「未知」处理；
* `now_utc` / 观测序列非法时返回 `ok=False` 的结构化快照，不猜当下时刻。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.sky_health import (  # noqa: E402
    CAMERA_NO_FRAME_S,
    health_snapshot,
)
from mbdsdr_ai.sky_pointing import PoseObservation  # noqa: E402

NOW = 1_700_000_000.0
_NO_SOLVER = {"available": False, "reason": "未安装 tetra3"}
_SOLVED = {"available": True, "name": "tetra3", "solved": True}


def _obs(source="imu", t=None, az=10.0, alt=20.0, accuracy=2.0):
    return PoseObservation(source=source,
                           t_utc=NOW - 0.5 if t is None else t,
                           az_deg=az, alt_deg=alt, accuracy_deg=accuracy)


def _health(obs, **kwargs):
    kwargs.setdefault("solver_status", _NO_SOLVER)
    return health_snapshot(obs, NOW, **kwargs)


# ---------------------------------------------------------------------------
# 级别判定
# ---------------------------------------------------------------------------
def test_no_observations_is_l0():
    snap = _health([])
    assert snap["ok"] is True
    assert snap["level"] == "L0"
    assert snap["sources"] == []
    assert snap["pose_valid"] is False
    assert "没有可用观测" in snap["reason"]


def test_single_source_is_l1():
    snap = _health([_obs(source="imu")])
    assert snap["level"] == "L1"
    assert snap["pose_valid"] is True
    assert [s["source"] for s in snap["sources"]] == ["imu"]
    assert snap["sources"][0]["available"] is True
    assert snap["sources"][0]["age_s"] == pytest.approx(0.5)


def test_multiple_sources_fused_is_l2():
    snap = _health([_obs(source="imu"), _obs(source="gnss", accuracy=10.0)])
    assert snap["level"] == "L2"
    assert snap["pose_valid"] is True
    assert {s["source"] for s in snap["sources"]} == {"imu", "gnss"}


def test_solved_pose_is_l3():
    snap = _health([_obs(source="imu"), _obs(source="gnss", accuracy=10.0)],
                   solver_status=_SOLVED)
    assert snap["level"] == "L3"
    assert snap["solver"]["solved"] is True
    assert "解算成功" in snap["reason"]


def test_manual_override_is_l4():
    snap = _health([_obs(source="imu")], manual_override=True)
    assert snap["level"] == "L4"
    assert "手动接管" in snap["reason"]


def test_manual_override_wins_over_solver():
    snap = _health([_obs(source="imu")], solver_status=_SOLVED,
                   manual_override=True)
    assert snap["level"] == "L4"


# ---------------------------------------------------------------------------
# 观测可用性
# ---------------------------------------------------------------------------
def test_stale_source_is_unavailable():
    snap = _health([_obs(source="imu", t=NOW - 10.0)])
    assert snap["level"] == "L0"
    assert snap["sources"][0]["available"] is False
    assert "陈旧" in snap["sources"][0]["reason"]


def test_invalid_observation_time_is_unavailable():
    snap = _health([_obs(source="imu", t=float("nan"))])
    assert snap["sources"][0]["available"] is False
    assert snap["sources"][0]["t_utc"] is None
    assert "时间非法" in snap["sources"][0]["reason"]


def test_latest_observation_per_source_wins():
    snap = _health([
        _obs(source="imu", t=NOW - 30.0),
        _obs(source="imu", t=NOW - 0.25),
    ])
    assert snap["sources"][0]["available"] is True
    assert snap["sources"][0]["age_s"] == pytest.approx(0.25)
    assert snap["level"] == "L1"


# ---------------------------------------------------------------------------
# 相机
# ---------------------------------------------------------------------------
def test_camera_no_frame_over_5s_marks_and_degrades():
    assert CAMERA_NO_FRAME_S == pytest.approx(5.0)
    snap = _health(
        [_obs(source="imu"), _obs(source="gnss", accuracy=10.0)],
        solver_status=_SOLVED,
        camera_last_frame_utc=NOW - 6.0,
    )
    assert snap["camera_no_frame"] is True
    assert snap["camera_age_s"] == pytest.approx(6.0)
    # 相机断帧时上一次解算不再代表当前画面：L3 降回融合级别 L2。
    assert snap["level"] == "L2"
    assert "无帧" in snap["reason"]


def test_camera_fresh_frame_keeps_l3():
    snap = _health(
        [_obs(source="imu"), _obs(source="gnss", accuracy=10.0)],
        solver_status=_SOLVED,
        camera_last_frame_utc=NOW - 1.0,
    )
    assert snap["camera_no_frame"] is False
    assert snap["level"] == "L3"


def test_camera_frame_time_unknown_is_not_no_frame():
    snap = _health([_obs(source="imu")], camera_last_frame_utc=None)
    assert snap["camera_no_frame"] is False
    assert snap["camera_age_s"] is None
    assert "未提供" in snap["reason"]


def test_invalid_camera_time_does_not_claim_no_frame():
    snap = _health([_obs(source="imu")],
                   camera_last_frame_utc=float("inf"))
    assert snap["camera_no_frame"] is False
    assert snap["camera_age_s"] is None
    assert "非法" in snap["reason"]


# ---------------------------------------------------------------------------
# 非法输入与透传
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad_now", [float("nan"), float("inf"), "now", None])
def test_invalid_now_returns_structured_snapshot(bad_now):
    snap = health_snapshot([_obs()], bad_now, _NO_SOLVER)
    assert snap["ok"] is False
    assert snap["level"] == "L0"
    assert snap["sources"] == []
    assert "now_utc" in snap["reason"]


def test_invalid_observations_returns_structured_snapshot():
    snap = health_snapshot({"source": "imu"}, NOW, _NO_SOLVER)
    assert snap["ok"] is False
    assert snap["level"] == "L0"
    assert "observations" in snap["reason"]


def test_last_error_is_passed_through():
    snap = _health([_obs()], last_error="相机读取超时")
    assert snap["last_error"] == "相机读取超时"
