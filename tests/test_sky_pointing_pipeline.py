# SPDX-License-Identifier: MIT
"""AR 指向残差管线：前置失败必须结构化，残差用独立 oracle 对账。

全部用例不接相机、不联网、不调用真实解算库：
* 成功路径注入 fake solver，返回手算已知的 RA/Dec；
* 残差期望值来自球面几何手算（赤道 RA 差 × cos(dec)、球面余弦定理），
  不用被测模块反推；
* 一条用例走真实 ``sky_solve.solver_status()`` 分支，tetra3 缺失时
  验证 ``solver_unavailable``，安装了就验证不会误报不可用（不 skip）。
"""

import datetime
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.celestial_geometry import gmst  # noqa: E402
from mbdsdr_ai.sky_pointing_pipeline import (  # noqa: E402
    CameraParams,
    MIN_SOLVE_STARS,
    RESIDUAL_TOLERANCE_DEG,
    SolveOutcome,
    StarCentroid,
    apply_solve,
    solve_and_verify,
)
from mbdsdr_ai.sky_solve import solver_status  # noqa: E402

_JD_J2000 = 2451545.0
_AT_UTC = datetime.datetime(2000, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)
_CAMERA = CameraParams(width_px=1920, height_px=1080, fov_deg=8.0)


def _centroids(n: int = MIN_SOLVE_STARS):
    return [StarCentroid(100.0 + 10.0 * i, 200.0 + 7.0 * i) for i in range(n)]


def _site_for_lst_zero(lat_deg: float) -> tuple:
    """J2000 时刻把本地恒星时对到 0°：lon = -GMST(J2000)。

    只用既有 ``gmst`` 对齐坐标系；残差期望值仍是手算的球面角距。
    """
    lon = (-gmst(_JD_J2000)) % 360.0
    if lon > 180.0:
        lon -= 360.0
    return lat_deg, lon


def _fake_solver(ra_deg: float, dec_deg: float, *, roll_deg: float = 0.0,
                 matched: int = 12, calls: dict = None):
    def _solver(points, image_size, fov_deg, *, prior, observer):
        if calls is not None:
            calls.update({
                "points": list(points),
                "image_size": image_size,
                "fov_deg": fov_deg,
                "prior": dict(prior),
                "observer": dict(observer),
            })
        return {"ok": True, "ra_deg": ra_deg, "dec_deg": dec_deg,
                "roll_deg": roll_deg, "matched_stars": matched}

    return _solver


def _run(lat_deg, lon_deg, az_deg, el_deg, solver, **kwargs):
    return solve_and_verify(
        kwargs.pop("centroids", _centroids()),
        kwargs.pop("camera", _CAMERA),
        az_deg,
        el_deg,
        lat_deg,
        lon_deg,
        kwargs.pop("at_utc", _AT_UTC),
        solver=solver,
    )


def _assert_failed_without_coordinates(outcome: SolveOutcome):
    assert outcome.ok is False
    assert outcome.ra_deg is None
    assert outcome.dec_deg is None
    assert outcome.residual_deg is None
    assert outcome.within_tolerance is False
    assert outcome.az_deg is None
    assert outcome.alt_deg is None
    assert "已对准" not in outcome.reason


# ---------------------------------------------------------------------------
# 内置 quad 解算（Task 10.1 接线）
# ---------------------------------------------------------------------------
def _builtin_scene():
    """真实 HYG 星表 + 合成的小角度指向误差。"""
    import datetime as _dt

    from mbdsdr_ai.celestial_geometry import GroundStation, PerspectiveProjection
    from mbdsdr_ai.new_spacetime import utc_to_jd
    from mbdsdr_ai.sky_interaction import ViewState, sky_to_screen
    from mbdsdr_ai.skyengine.star_catalog import select_for_view

    at = _dt.datetime(2026, 9, 27, 12, 0, tzinfo=_dt.timezone.utc)
    jd = utc_to_jd(at)
    lat, lon, az, el, fov = 40.0, 116.0, 210.0, 35.0, 20.0
    gs = GroundStation(lon, lat)
    sel = select_for_view(gs, jd, az, el, fov, limit_mag=6.5,
                          max_count=400, margin_deg=0.5)
    assert len(sel) >= 12, f"该天区亮星只有 {len(sel)} 颗"
    proj = PerspectiveProjection()
    true = ViewState(center_az=az + 0.3, center_alt=el - 0.2, fov_deg=fov,
                     rotation=0.5, min_fov=0.5, max_fov=120.0)
    pts = []
    # 星表返回暗→亮；检测列表按契约亮星在前，所以反向遍历。
    for a, e in zip(sel.az_deg[::-1], sel.alt_deg[::-1]):
        x, y = sky_to_screen(float(a), float(e), true, proj, (1000, 1000))
        if (math.isfinite(x) and math.isfinite(y)
                and 0.0 <= x <= 1000.0 and 0.0 <= y <= 1000.0):
            pts.append((x, y))
    assert len(pts) >= MIN_SOLVE_STARS
    camera = CameraParams(width_px=1000, height_px=1000, fov_deg=fov)
    return pts, camera, az, el, lat, lon, at


def _assert_recovered(out, az: float, el: float):
    """内置解算应恢复到注入的真值位姿；residual 是与指令指向的真实偏差。"""
    from mbdsdr_ai.sky_interaction import angular_distance_deg

    assert out.ok, (out.error, out.reason)
    assert out.matched_stars >= MIN_SOLVE_STARS
    assert out.az_deg == pytest.approx((az + 0.3) % 360.0, abs=0.03)
    assert out.alt_deg == pytest.approx(el - 0.2, abs=0.03)
    assert out.roll_deg == pytest.approx(0.5, abs=0.05)
    expected = angular_distance_deg(az + 0.3, el - 0.2, az, el)
    assert out.residual_deg == pytest.approx(expected, abs=0.02)


def test_builtin_solver_corrects_small_pointing_error():
    pts, camera, az, el, lat, lon, at = _builtin_scene()
    out = solve_and_verify(pts, camera, az, el, lat, lon, at,
                           solver="builtin")
    _assert_recovered(out, az, el)


def test_auto_falls_back_to_builtin_when_external_unavailable(monkeypatch):
    import mbdsdr_ai.sky_solve as sky_solve

    monkeypatch.setattr(
        sky_solve, "solver_status",
        lambda *a, **k: {"available": False, "name": "",
                         "reason": "stub: tetra3/ASTAP 都不在"})
    pts, camera, az, el, lat, lon, at = _builtin_scene()
    out = solve_and_verify(pts, camera, az, el, lat, lon, at)
    _assert_recovered(out, az, el)


# ---------------------------------------------------------------------------
# 前置校验
# ---------------------------------------------------------------------------
def test_too_few_points_returns_structured_failure():
    out = solve_and_verify(
        _centroids(MIN_SOLVE_STARS - 1), _CAMERA, 0.0, 0.0, 0.0, 0.0, _AT_UTC,
        solver=_fake_solver(0.0, 0.0))
    _assert_failed_without_coordinates(out)
    assert out.error == "too_few_points"
    assert str(MIN_SOLVE_STARS) in out.reason


def test_non_finite_centroid_is_rejected_without_raising():
    stars = _centroids()
    stars[0] = StarCentroid(float("nan"), 100.0)
    out = solve_and_verify(stars, _CAMERA, 0.0, 0.0, 0.0, 0.0, _AT_UTC,
                           solver=_fake_solver(0.0, 0.0))
    _assert_failed_without_coordinates(out)
    assert out.error == "bad_points"
    assert "非有限" in out.reason


def test_missing_site_is_structured_as_no_site():
    out = solve_and_verify(_centroids(), _CAMERA, 0.0, 0.0, None, 0.0, _AT_UTC,
                           solver=_fake_solver(0.0, 0.0))
    _assert_failed_without_coordinates(out)
    assert out.error == "no_site"
    assert "站址" in out.reason


def test_missing_time_is_structured_as_no_site():
    out = solve_and_verify(_centroids(), _CAMERA, 0.0, 0.0, 0.0, 0.0, None,
                           solver=_fake_solver(0.0, 0.0))
    _assert_failed_without_coordinates(out)
    assert out.error == "no_site"
    assert "时刻" in out.reason


def test_bad_camera_fov_and_size_are_rejected():
    bad_fov = CameraParams(width_px=1920, height_px=1080, fov_deg=0.0)
    out = solve_and_verify(_centroids(), bad_fov, 0.0, 0.0, 0.0, 0.0, _AT_UTC,
                           solver=_fake_solver(0.0, 0.0))
    _assert_failed_without_coordinates(out)
    assert out.error == "bad_camera"
    assert "fov_deg" in out.reason

    bad_size = CameraParams(width_px=0, height_px=1080, fov_deg=8.0)
    out2 = solve_and_verify(_centroids(), bad_size, 0.0, 0.0, 0.0, 0.0,
                            _AT_UTC, solver=_fake_solver(0.0, 0.0))
    _assert_failed_without_coordinates(out2)
    assert out2.error == "bad_camera"


def test_bad_commanded_elevation_is_rejected():
    out = solve_and_verify(_centroids(), _CAMERA, 0.0, 91.0, 0.0, 0.0, _AT_UTC,
                           solver=_fake_solver(0.0, 0.0))
    _assert_failed_without_coordinates(out)
    assert out.error == "bad_command"


# ---------------------------------------------------------------------------
# 解算器失败映射
# ---------------------------------------------------------------------------
def test_solver_unavailable_uses_real_solver_status():
    lat, lon = _site_for_lst_zero(45.0)
    out = solve_and_verify(_centroids(), _CAMERA, 180.0, 45.0, lat, lon,
                           _AT_UTC, solver=None)
    _assert_failed_without_coordinates(out)
    status = solver_status()
    if not status["available"]:
        # 外部库不在时会自动回退到内置 quad 解算；这组随机星点匹配不上，
        # 因此结论是 solver_failed（不是「没有解算器」），原因里两边都要有。
        assert out.error == "solver_failed"
        assert status["reason"] in out.reason
        assert "内置解算" in out.reason
    else:
        # 装了 tetra3 / 配了 ASTAP 时不能把「解算失败」谎报成「库不可用」。
        assert out.error != "solver_unavailable"


def test_solver_exception_becomes_solver_failed():
    def _boom(points, image_size, fov_deg, *, prior, observer):
        raise RuntimeError("假解算器故障")

    lat, lon = _site_for_lst_zero(45.0)
    out = _run(lat, lon, 180.0, 45.0, _boom)
    _assert_failed_without_coordinates(out)
    assert out.error == "solver_failed"
    assert "RuntimeError" in out.reason


def test_solver_ok_false_unavailable_maps_to_solver_unavailable():
    def _unavailable(points, image_size, fov_deg, *, prior, observer):
        return {"ok": False, "error": "unavailable", "reason": "没有解算库"}

    lat, lon = _site_for_lst_zero(45.0)
    out = _run(lat, lon, 180.0, 45.0, _unavailable)
    _assert_failed_without_coordinates(out)
    assert out.error == "solver_unavailable"
    assert "没有解算库" in out.reason


# ---------------------------------------------------------------------------
# 残差：独立 oracle
# ---------------------------------------------------------------------------
def test_one_degree_azimuth_offset_residual_matches_independent_oracle():
    # 纬度 0、LST=0：RA=90°, dec=0 正好在东地平 (az 90°, el 0°)；
    # RA 差 1° 在赤道上就是 1° 的球面角距 —— 手算，不读产品代码。
    lat, lon = _site_for_lst_zero(0.0)
    out = _run(lat, lon, 90.0, 0.0, _fake_solver(89.0, 0.0))
    assert out.ok is True, out.reason
    assert out.ra_deg == pytest.approx(89.0, abs=1e-9)
    assert out.dec_deg == pytest.approx(0.0, abs=1e-9)
    assert out.residual_deg == pytest.approx(1.0, abs=0.01)
    assert out.error == ""
    assert out.reason == ""
    assert out.within_tolerance is False
    assert "已对准" not in out.reason


def test_one_hour_ra_offset_residual_matches_independent_oracle():
    # 纬度 45、LST=0：指令点 (RA=0°, dec=0°) 位于 az 180°, el 45°；
    # 解算点 RA=15°, dec=0°。赤道上 RA 差 1h = 15° 角距。
    lat, lon = _site_for_lst_zero(45.0)
    out = _run(lat, lon, 180.0, 45.0, _fake_solver(15.0, 0.0))
    assert out.ok is True, out.reason
    assert out.residual_deg == pytest.approx(15.0, abs=0.02)
    assert out.within_tolerance is False


def test_dec30_ra_offset_residual_matches_hand_computed_oracle():
    # 纬度 30、LST=0：指令点 (RA=0°, dec=30°) 在天顶；
    # 解算点 (RA=15°, dec=30°)。球面余弦定理手算：
    # cos(d) = sin²30 + cos²30·cos15。
    lat, lon = _site_for_lst_zero(30.0)
    expected = math.degrees(math.acos(
        math.sin(math.radians(30.0)) ** 2
        + math.cos(math.radians(30.0)) ** 2
        * math.cos(math.radians(15.0))))
    out = _run(lat, lon, 0.0, 90.0, _fake_solver(15.0, 30.0))
    assert out.ok is True, out.reason
    assert out.residual_deg == pytest.approx(expected, abs=0.02)
    assert out.residual_deg > RESIDUAL_TOLERANCE_DEG


def test_success_within_tolerance_and_roll_passthrough():
    # 指令点与解算点是同一个点（RA=0°, dec=0° ↔ az 180°, el 45°）；
    # 残差只应剩数值噪声，横滚必须原样透传。
    lat, lon = _site_for_lst_zero(45.0)
    out = _run(lat, lon, 180.0, 45.0,
               _fake_solver(0.0, 0.0, roll_deg=123.25, matched=17))
    assert out.ok is True, out.reason
    # UTC→UT1 的秒级差会让同一颗星差几个角秒；期望仍是「同一个点」。
    assert out.residual_deg == pytest.approx(0.0, abs=0.01)
    assert out.residual_deg <= RESIDUAL_TOLERANCE_DEG
    assert out.within_tolerance is True
    assert out.roll_deg == pytest.approx(123.25)
    assert out.matched_stars == 17
    assert out.reason == ""


def test_success_with_large_residual_stays_ok_but_outside_tolerance():
    # 纬度 0、LST=0：指令 az 0°, el 45°；解算点 RA=0°, dec=0° 在天顶，
    # 手算角距 45°。ok 仍为 True —— 模块只给数字，不替上层下结论。
    lat, lon = _site_for_lst_zero(0.0)
    out = _run(lat, lon, 0.0, 45.0, _fake_solver(0.0, 0.0))
    assert out.ok is True, out.reason
    assert out.residual_deg == pytest.approx(45.0, abs=0.01)
    assert out.within_tolerance is False
    assert out.reason == ""


def test_sky_solve_shaped_result_contract_is_accepted():
    # 真实 solve_frame 成功契约给的是 solved{az_deg,alt_deg,roll_deg}；
    # 管线应经既有坐标链反算 RA/Dec，再正向验证，残差接近 0。
    def _solved(points, image_size, fov_deg, *, prior, observer):
        return {
            "ok": True,
            "solved": {"az_deg": 180.0, "alt_deg": 45.0, "roll_deg": 7.0},
            "matched_stars": 12,
            "residual_arcsec": 7.2,
        }

    lat, lon = _site_for_lst_zero(45.0)
    out = _run(lat, lon, 180.0, 45.0, _solved)
    assert out.ok is True, out.reason
    assert out.residual_deg == pytest.approx(0.0, abs=0.01)
    assert out.roll_deg == pytest.approx(7.0)
    assert out.matched_stars == 12


def test_solved_horizontal_coordinates_are_reported_and_applicable():
    # 解算结果必须带出地平 az/alt，apply_solve 才能把它变成新位姿。
    lat, lon = _site_for_lst_zero(45.0)
    out = _run(lat, lon, 180.0, 45.0, _fake_solver(0.0, 0.0, roll_deg=7.0))
    assert out.ok is True, out.reason
    assert out.az_deg == pytest.approx(180.0, abs=0.02)
    assert out.alt_deg == pytest.approx(45.0, abs=0.02)

    update = apply_solve(
        {"az_deg": 170.0, "alt_deg": 40.0, "roll_deg": 0.0}, out)
    assert update.applied is True
    assert update.level == "L3"
    assert update.az_deg == pytest.approx(out.az_deg)
    assert update.alt_deg == pytest.approx(out.alt_deg)
    assert update.roll_deg == pytest.approx(7.0)


def test_solver_receives_camera_prior_and_observed_site():
    calls = {}
    lat, lon = _site_for_lst_zero(45.0)
    out = _run(lat, lon, 180.0, 45.0,
               _fake_solver(0.0, 0.0, calls=calls))
    assert out.ok is True, out.reason
    assert calls["image_size"] == (1920.0, 1080.0)
    assert calls["fov_deg"] == pytest.approx(8.0)
    assert calls["prior"]["az_deg"] == pytest.approx(180.0)
    assert calls["prior"]["alt_deg"] == pytest.approx(45.0)
    assert calls["prior"]["roll_deg"] == pytest.approx(0.0)
    assert calls["observer"]["lat_deg"] == pytest.approx(lat)
    assert calls["observer"]["lon_deg"] == pytest.approx(lon)
    assert calls["observer"]["jd_ut1"] == pytest.approx(_JD_J2000, abs=0.01)
    assert len(calls["points"]) == MIN_SOLVE_STARS


@pytest.mark.parametrize("outcome", [
    SolveOutcome(ok=False, reason="星点不足：至少需要 8 颗", error="too_few_points"),
    SolveOutcome(ok=False, reason="缺少站址纬度", error="no_site"),
    SolveOutcome(ok=False, reason="fov_deg 非法", error="bad_camera"),
    SolveOutcome(ok=False, reason="星点解算不可用", error="solver_unavailable"),
])
def test_failure_reason_never_claims_alignment(outcome):
    assert "已对准" not in outcome.reason
