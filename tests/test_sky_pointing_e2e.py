# SPDX-License-Identifier: MIT
"""AR 指向链路的端到端仿真：观测融合 → 健康分级 → 拍星解算 → 残差 → 识别 → HUD。

全部用例只串既有纯函数，不 import Qt、不读盘、不联网：
* 固定 ``NOW`` / 固定 ``jd`` / 固定假解算器，输出可复现；
* 完整链路用随仓星表真实算出一颗可见亮星方向，假解算器把它当作解算结果，
  因此残差、识别与 HUD 都落在真实星表坐标上；
* 降级分支（残差超阈值、单源、解算不可用、星点不足、相机无帧）逐条钉住
  「保留上一级位姿、不冒充对准」的诚实语义。
"""

import datetime
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.sky_health import health_snapshot  # noqa: E402
from mbdsdr_ai.sky_hud import HUD_HINTS, build_hud  # noqa: E402
from mbdsdr_ai.sky_pointing import (  # noqa: E402
    PoseObservation,
    fuse_poses,
    identify_direction,
    view_params,
)
from mbdsdr_ai.sky_pointing_pipeline import (  # noqa: E402
    CameraParams,
    RESIDUAL_TOLERANCE_DEG,
    StarCentroid,
    apply_solve,
    solve_and_verify,
)

NOW = 1_700_000_000.0
_LAT = 45.0
_LON = 120.0
_JD = 2461359.5  # 固定 UT1 儒略日，保证星表坐标确定
_AT_UTC = datetime.datetime(2026, 11, 15, 0, 0, tzinfo=datetime.timezone.utc)
_CAMERA = CameraParams(width_px=1920, height_px=1080, fov_deg=4.0)
_SIZE = (1920.0, 1080.0)

_NO_SOLVER = {"available": False, "reason": "未安装 tetra3"}
_AVAILABLE_NOT_SOLVED = {"available": True, "name": "tetra3", "solved": False}
_SOLVED = {"available": True, "name": "tetra3", "solved": True}


def _obs(source, az, alt, accuracy=2.0, t=None):
    return PoseObservation(source=source,
                           t_utc=NOW - 0.5 if t is None else t,
                           az_deg=az, alt_deg=alt, accuracy_deg=accuracy)


def _centroids(n=8):
    return [StarCentroid(100.0 + 10.0 * i, 200.0 + 7.0 * i) for i in range(n)]


def _fake_solved(az_deg, alt_deg, *, roll_deg=0.0, matched=12):
    def _solver(points, image_size, fov_deg, *, prior, observer):
        return {"ok": True,
                "solved": {"az_deg": az_deg, "alt_deg": alt_deg,
                           "roll_deg": roll_deg},
                "matched_stars": matched}

    return _solver


def _visible_named_star():
    """按星等从亮到暗找一颗此刻地平高度 > 30° 且有专名的星。"""
    from mbdsdr_ai.celestial_geometry import (
        GroundStation, azalt_from_vec, j2000_to_altaz, vec_from_radec)
    from mbdsdr_ai.skyengine.star_catalog import get_catalog

    cat = get_catalog()
    gs = GroundStation(_LON, _LAT, 0.0, "e2e")
    for i in np.argsort(cat.vmag)[:400]:
        name = str(cat.name[i]).strip()
        if not name:
            continue
        v = vec_from_radec(float(cat.ra_deg[i]), float(cat.dec_deg[i]))
        az, alt = azalt_from_vec(j2000_to_altaz(v, gs, _JD))
        if float(alt) > 30.0:
            return name, float(az), float(alt)
    pytest.skip("固定时刻无可见亮星，环境星表异常")


def _hud_pose(update, fov_deg=4.0):
    """把 ``PoseUpdate`` 包成 ``build_hud`` 可直接消费的位姿 dict。"""
    return {
        "valid": update.az_deg is not None and update.alt_deg is not None,
        "az_deg": update.az_deg,
        "alt_deg": update.alt_deg,
        "roll_deg": update.roll_deg,
        "fov_deg": fov_deg,
    }


# ---------------------------------------------------------------------------
# 1. 完整链路
# ---------------------------------------------------------------------------
def test_full_chain_dual_source_solve_identify_and_hud():
    name, star_az, star_alt = _visible_named_star()

    observations = [
        _obs("imu", star_az - 0.4, star_alt - 0.2, accuracy=2.0),
        _obs("gnss", star_az + 0.6, star_alt + 0.3, accuracy=10.0),
    ]
    pose = fuse_poses(observations, NOW)
    assert pose["valid"] is True
    assert set(pose["sources"]) == {"imu", "gnss"}

    health = health_snapshot(observations, NOW, _AVAILABLE_NOT_SOLVED)
    assert health["level"] == "L2"
    assert health["pose_valid"] is True

    outcome = solve_and_verify(
        _centroids(), _CAMERA, star_az, star_alt, _LAT, _LON, _AT_UTC,
        solver=_fake_solved(star_az, star_alt, roll_deg=1.5))
    assert outcome.ok is True
    assert outcome.residual_deg <= RESIDUAL_TOLERANCE_DEG
    assert outcome.within_tolerance is True

    update = apply_solve(pose, outcome, level_before="L2", source_count=2)
    assert update.applied is True
    assert update.level == "L3"
    assert update.residual_deg == pytest.approx(outcome.residual_deg)

    hud_pose = _hud_pose(update)
    targets = identify_direction(update.az_deg, update.alt_deg, 3.0,
                                 _JD, _LAT, _LON, limit=5)
    assert targets["matched"] is True
    assert targets["stars"]
    assert name in [star["name"] for star in targets["stars"]]

    params = view_params(hud_pose, fov_deg=4.0, min_fov=0.5)
    hud = build_hud(hud_pose, targets["stars"], "L3", params, _SIZE)
    assert hud["level"] == "L3"
    assert hud["hint"] == HUD_HINTS["L3"]
    assert hud["labels"]
    assert name in [label["name"] for label in hud["labels"]]


# ---------------------------------------------------------------------------
# 2. 残差超阈值：保留上一级位姿，退 L2
# ---------------------------------------------------------------------------
def test_residual_over_tolerance_keeps_previous_pose_and_degrades():
    observations = [
        _obs("imu", 180.0, 45.0, accuracy=2.0),
        _obs("gnss", 180.5, 45.3, accuracy=10.0),
    ]
    pose = fuse_poses(observations, NOW)
    assert pose["valid"] is True

    outcome = solve_and_verify(
        _centroids(), _CAMERA, 180.0, 45.0, _LAT, _LON, _AT_UTC,
        solver=_fake_solved(180.0, 50.0))
    assert outcome.ok is True
    assert outcome.residual_deg > RESIDUAL_TOLERANCE_DEG
    assert outcome.within_tolerance is False

    update = apply_solve(pose, outcome, level_before="L2", source_count=2)
    assert update.applied is False
    assert update.level == "L2"
    assert update.az_deg == pytest.approx(pose["az_deg"])
    assert update.alt_deg == pytest.approx(pose["alt_deg"])
    assert update.roll_deg == pytest.approx(pose["roll_deg"])
    assert "残差" in update.reason
    assert f"{RESIDUAL_TOLERANCE_DEG:g}" in update.reason


# ---------------------------------------------------------------------------
# 3/4. 单源：L1，且 HUD 不给精确标签
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("source", ["imu", "gnss"])
def test_single_source_is_l1_and_hud_has_no_precise_labels(source):
    observations = [_obs(source, 114.0, 47.0, accuracy=5.0)]
    pose = fuse_poses(observations, NOW)
    assert pose["valid"] is True
    assert pose["level"] == "single"

    health = health_snapshot(observations, NOW, _NO_SOLVER)
    assert health["level"] == "L1"
    assert [item["source"] for item in health["sources"]] == [source]

    hud = build_hud(pose, [{"name": "假目标", "az_deg": 114.0,
                            "alt_deg": 47.0, "vmag": 1.0}],
                    "L1", {"center_az": 114.0, "center_alt": 47.0,
                           "fov_deg": 6.0, "min_fov": 0.5}, _SIZE)
    assert hud["level"] == "L1"
    assert hud["hint"] == HUD_HINTS["L1"]
    assert hud["labels"] == []
    assert hud["satellite_markers"] == []


# ---------------------------------------------------------------------------
# 5. 解算不可用：结构化失败，位姿不变
# ---------------------------------------------------------------------------
def test_solver_unavailable_does_not_change_pose(monkeypatch):
    import mbdsdr_ai.sky_solve as sky_solve

    monkeypatch.setattr(
        sky_solve, "solver_status",
        lambda *args, **kwargs: {"available": False, "reason": "假解算库缺失"})

    outcome = solve_and_verify(
        _centroids(), _CAMERA, 180.0, 45.0, _LAT, _LON, _AT_UTC, solver=None)
    assert outcome.ok is False
    # 外部库不可用时自动回退内置解算；这组共线星点解不出 -> solver_failed，
    # 原因里同时写明外部缺失与内置失败。
    assert outcome.error == "solver_failed"
    assert "假解算库缺失" in outcome.reason
    assert "内置解算" in outcome.reason
    assert outcome.az_deg is None and outcome.alt_deg is None

    previous = fuse_poses([_obs("imu", 179.0, 44.0),
                           _obs("gnss", 181.0, 46.0, accuracy=10.0)], NOW)
    update = apply_solve(previous, outcome, level_before="L2", source_count=2)
    assert update.applied is False
    assert update.level == "L2"
    assert update.az_deg == pytest.approx(previous["az_deg"])
    assert update.alt_deg == pytest.approx(previous["alt_deg"])
    assert update.error == "solver_failed"


# ---------------------------------------------------------------------------
# 6. 星点不足：不进入解算，不产生新位姿
# ---------------------------------------------------------------------------
def test_too_few_points_produce_no_new_pose():
    outcome = solve_and_verify(
        _centroids(3), _CAMERA, 180.0, 45.0, _LAT, _LON, _AT_UTC,
        solver=_fake_solved(180.0, 45.0))
    assert outcome.ok is False
    assert outcome.error == "too_few_points"
    assert outcome.residual_deg is None

    previous = {"az_deg": 170.0, "alt_deg": 40.0, "roll_deg": 2.0}
    update = apply_solve(previous, outcome, level_before="L1", source_count=1)
    assert update.applied is False
    assert update.az_deg == pytest.approx(170.0)
    assert update.alt_deg == pytest.approx(40.0)
    assert update.roll_deg == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# 7. 相机无帧：即使解算报成功也不得 L3
# ---------------------------------------------------------------------------
def test_camera_no_frame_never_reports_l3():
    observations = [_obs("imu", 10.0, 20.0),
                    _obs("gnss", 11.0, 21.0, accuracy=10.0)]
    health = health_snapshot(observations, NOW, _SOLVED,
                             camera_last_frame_utc=NOW - 6.0)
    assert health["camera_no_frame"] is True
    assert health["solver"]["solved"] is True
    assert health["level"] != "L3"
    assert health["level"] == "L2"
    assert "无帧" in health["reason"]


# ---------------------------------------------------------------------------
# 8. 不冒充对准：全链路文本不含结论性措辞
# ---------------------------------------------------------------------------
def test_chain_text_never_claims_alignment():
    name, star_az, star_alt = _visible_named_star()

    observations = [_obs("imu", star_az - 0.3, star_alt, accuracy=2.0),
                    _obs("gnss", star_az + 0.3, star_alt, accuracy=10.0)]
    pose = fuse_poses(observations, NOW)
    health = health_snapshot(observations, NOW, _SOLVED,
                             camera_last_frame_utc=NOW - 6.0)

    good = solve_and_verify(
        _centroids(), _CAMERA, star_az, star_alt, _LAT, _LON, _AT_UTC,
        solver=_fake_solved(star_az, star_alt))
    good_update = apply_solve(pose, good, level_before="L2", source_count=2)

    bad = solve_and_verify(
        _centroids(), _CAMERA, star_az, star_alt, _LAT, _LON, _AT_UTC,
        solver=_fake_solved((star_az + 3.0) % 360.0, star_alt))
    bad_update = apply_solve(pose, bad, level_before="L2", source_count=2)

    targets = identify_direction(good_update.az_deg, good_update.alt_deg, 3.0,
                                 _JD, _LAT, _LON, limit=5)
    params = view_params(_hud_pose(good_update), fov_deg=4.0, min_fov=0.5)
    hud = build_hud(_hud_pose(good_update), targets["stars"], "L3", params,
                    _SIZE)

    texts = [
        pose.get("reason", ""),
        health.get("reason", ""),
        good.reason, bad.reason,
        good_update.reason, bad_update.reason,
        hud.get("hint", ""),
        hud.get("reason", ""),
    ]
    texts += [label.get("text", "") for label in hud["labels"]]
    blob = "；".join(str(text) for text in texts)

    for forbidden in ("已对准", "对准成功", "指向准确", "精确对准"):
        assert forbidden not in blob, f"{forbidden!r} 出现在链路文本：{blob}"
    assert "残差" in blob
