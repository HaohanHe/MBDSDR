# SPDX-License-Identifier: MIT
"""
tests/test_sky_pointing_binding.py — 指向绑定（PRD v2 F7）+ 叠加接线

验证的是这件事：**把解算出的光轴绑到视图上，既有的星官连线图层不用改
一行代码就落到正确位置**。

覆盖：
  1. bind_pointing 成功时 view_state 的 az/alt/fov 与输入一致
  2. 非法输入（NaN / alt 越界 / fov<=0）一律拒绝，视图保持原状
  3. pointing_binding 给出实时接管判定（用户拖一下就不再算绑定）
  4. clear_pointing_binding 恢复绑定前视角
  5. overlay_for_current_view 走视图自己的投影，北斗落在画面里
  6. **线落在星上**：未被裁剪的段端点 == 该星在屏幕上的位置
  7. 窄视场下依然有线（F6 与 F7 联动的验收点）

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_sky_pointing_binding.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import math  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from rf_sky_view import RFSkyView  # noqa: E402
from mbdsdr_ai.celestial_geometry import (  # noqa: E402
    GroundStation, azalt_from_vec_many, j2000_to_altaz_many, vec_from_radec,
)
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402
from mbdsdr_ai.overlay_asterisms import CLIP_BOTH_IN  # noqa: E402
from mbdsdr_ai.skyengine.xingguan import get_xingguan  # noqa: E402

FIXED_UNIX = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc).timestamp()
GS = GroundStation(116.0, 40.0, 50.0, "bench")
JD = 2460000.5 + 0.5


def _fix_time():
    eng = get_time_engine()
    eng.set_unix(FIXED_UNIX)
    eng.pause()


def _new_view(size: int = 700) -> RFSkyView:
    _fix_time()
    v = RFSkyView()
    v.resize(size, size)
    return v


def _beidou():
    for r in get_xingguan().regions:
        for g in r.xingguan:
            if g.name_zh == "北斗":
                return g
    pytest.fail("星官数据里找不到北斗")


def _star_azalt(star, jd: float = JD):
    mat = j2000_to_altaz_many(
        vec_from_radec(float(star.ra_deg), float(star.dec_deg))[None, :], GS, jd)
    az, alt = azalt_from_vec_many(mat)
    return float(az[0]), float(alt[0])


# ---------------------------------------------------------------------------
# 绑定本身
# ---------------------------------------------------------------------------
def test_bind_sets_view_state():
    v = _new_view()
    ok = v.bind_pointing(123.4, 45.6, 8.0, source="solve:field",
                         uncertainty_deg=0.2, now_unix=1000.0)
    assert ok is True
    assert v.view_state.center_az == pytest.approx(123.4, abs=1e-9)
    assert v.view_state.center_alt == pytest.approx(45.6, abs=1e-9)
    assert v.view_state.fov_deg == pytest.approx(8.0, abs=1e-9)
    info = v.pointing_binding
    assert info is not None
    assert info["source"] == "solve:field"
    assert info["uncertainty_deg"] == pytest.approx(0.2)
    assert info["still_bound"] is True


def test_bind_rejects_bad_input_and_keeps_view():
    v = _new_view()
    v.bind_pointing(10.0, 20.0, 30.0, now_unix=1.0)
    before = (v.view_state.center_az, v.view_state.center_alt,
              v.view_state.fov_deg)
    for args in ((float("nan"), 20.0, 30.0),
                 (10.0, 120.0, 30.0),
                 (10.0, 20.0, 0.0),
                 (10.0, 20.0, -3.0),
                 (10.0, 20.0, 999.0)):
        assert v.bind_pointing(*args, now_unix=2.0) is False
        assert (v.view_state.center_az, v.view_state.center_alt,
                v.view_state.fov_deg) == pytest.approx(before)


def test_user_pan_marks_not_still_bound():
    v = _new_view()
    v.bind_pointing(100.0, 40.0, 6.0, now_unix=1.0)
    assert v.pointing_binding["still_bound"] is True
    v.view_state.pan(5.0, 0.0)          # 用户拖了一下
    info = v.pointing_binding
    assert info["still_bound"] is False
    assert info["drift_deg"] > v.POINTING_DRIFT_TOLERANCE_DEG


def test_clear_restores_previous_view():
    v = _new_view()
    v.view_state.look_at(200.0, 55.0)
    v.view_state.zoom_to(50.0)
    saved = (v.view_state.center_az, v.view_state.center_alt, v.view_state.fov_deg)
    v.bind_pointing(10.0, 10.0, 2.5, now_unix=1.0)
    assert v.view_state.fov_deg == pytest.approx(2.5, abs=1e-9)
    v.clear_pointing_binding(restore=True)
    assert (v.view_state.center_az, v.view_state.center_alt,
            v.view_state.fov_deg) == pytest.approx(saved)
    assert v.pointing_binding is None


def test_clear_without_restore_leaves_view():
    v = _new_view()
    v.bind_pointing(10.0, 10.0, 2.5, now_unix=1.0)
    v.clear_pointing_binding(restore=False)
    assert v.view_state.fov_deg == pytest.approx(2.5, abs=1e-9)
    assert v.pointing_binding is None


# ---------------------------------------------------------------------------
# 叠加接线：视图指向驱动既有星官图层
# ---------------------------------------------------------------------------
def test_overlay_follows_bound_pointing():
    v = _new_view()
    g = _beidou()
    az, alt = _star_azalt(g.lines[0][0])
    assert v.bind_pointing(az, alt, 12.0, source="test", now_unix=1.0)
    res = v.overlay_for_current_view(site=GS, jd_ut1=JD)
    assert res.drawable is True
    names = {a.name for a in res.asterisms}
    assert "北斗" in names


def test_overlay_narrow_fov_still_has_lines():
    """2.3° 目镜视场：线必须延伸到画面边界，不能一条都没有。"""
    v = _new_view()
    g = _beidou()
    az, alt = _star_azalt(g.lines[0][0])
    assert v.bind_pointing(az, alt, 2.3, source="test", now_unix=1.0)
    res = v.overlay_for_current_view(site=GS, jd_ut1=JD)
    assert res.drawable is True
    bd = [a for a in res.asterisms if a.name == "北斗"]
    assert bd and bd[0].segments
    assert res.clipped_segments >= 1


def test_overlay_refuses_when_axis_error_exceeds_limit():
    v = _new_view()
    g = _beidou()
    az, alt = _star_azalt(g.lines[0][0])
    v.bind_pointing(az, alt, 12.0, source="test",
                    uncertainty_deg=4.0, now_unix=1.0)
    res = v.overlay_for_current_view(site=GS, jd_ut1=JD,
                                     min_confident_axis_deg=1.0)
    assert res.drawable is False
    assert res.asterisms == []


def test_overlay_endpoints_sit_on_stars():
    """线的端点必须就是那颗星在屏幕上的位置——这是"自证诚实"的硬条件。"""
    v = _new_view()
    g = _beidou()
    star = g.lines[0][0]
    az, alt = _star_azalt(star)
    v.bind_pointing(az, alt, 12.0, source="test", now_unix=1.0)
    res = v.overlay_for_current_view(site=GS, jd_ut1=JD)
    bd = [a for a in res.asterisms if a.name == "北斗"][0]

    # 该星在屏幕上的真实位置（走视图自己的投影）
    xs, ys = v._sky_to_screen_many(np.array([az]), np.array([alt]))
    sx, sy = float(xs[0]), float(ys[0])

    # 找到以该星为端点的段（未被裁剪的那种），端点应与星点重合
    hits = 0
    for s in bd.segments:
        if s.clip != CLIP_BOTH_IN:
            continue
        for (px, py) in s.screen:
            if math.hypot(px - sx, py - sy) <= 1.0:
                hits += 1
    assert hits >= 1, "没有任何段端点落在该星位置上：线没对准星"
