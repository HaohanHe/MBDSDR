# SPDX-License-Identifier: MIT
"""
tests/test_sky_sun.py
=====================
天空视图太阳：视直径缩放 / 日出日落特例 / 拾取与信息卡。

覆盖：
  1. 圆面尺寸按真实视直径换算（0.5° FOV 下 ≈ 373px，120° FOV 下不足 2px
     时给点源下限），不是写死 14/7px；
  2. 日出日落特例：alt=-0.5° 仍画且圆心贴地平（alt→0）；alt≤-1° 不画；
  3. 绘制与拾取共用 `_sun_screen_geom()`：能画的点位能点中，且 kind="sun"；
  4. 信息卡与行星同骨架：标题/类别/视直径/距离/RA-Dec/升落；
  5. 历表缺测时不编造角直径（点源下限 + 卡片如实写"缺测"）。

反证（旧实现必红）：
  - 旧实现写死 drawEllipse(pos, 14, 14)/(7, 7)，第 1 条比例断言必红；
  - 旧实现太阳不可点、无信息卡，第 3、4 条必红。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import math  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from rf_sky_view import RFSkyView  # noqa: E402
from mbdsdr_ai.celestial_geometry import (  # noqa: E402
    GroundStation, vec_from_radec, j2000_to_altaz, azalt_from_vec,
)
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402
from mbdsdr_ai.skyengine import jtime as _jtime  # noqa: E402

LAT, LON = 40.0, 116.0
UNIX = datetime(2026, 9, 28, 2, 0, tzinfo=timezone.utc).timestamp()
WIN = 700
SUN_DIA_DEG = 0.5333          # ≈ 32′（真实值随日地距离 31.5′~32.5′）


def _new_view(size: int = WIN) -> RFSkyView:
    eng = get_time_engine()
    eng.set_unix(UNIX)
    eng.pause()
    v = RFSkyView()
    v.resize(size, size)
    v.set_observer(LAT, LON, 0.0)
    v.view_state.fov_deg = 120.0
    v._sky_brightness = 0.0
    return v


def _sun_dict(alt=30.0, az=180.0, with_dia=True):
    d = {"az": az, "alt": alt}
    if with_dia:
        d["diameter_deg"] = SUN_DIA_DEG
    return d


# --------------------------------------------------------------------------
# 1. 视直径 -> 像素半径
# --------------------------------------------------------------------------
def test_sun_disc_scales_with_fov():
    v = _new_view()
    try:
        v.set_celestial_bodies(_sun_dict(alt=45.0), None, 0.0)
        v.view_state.look_at(180.0, 45.0)
        got = {}
        for fov in (0.5, 5.0, 120.0):
            v.view_state.fov_deg = fov
            geom = v._sun_screen_geom()
            assert geom is not None
            got[fov] = float(geom["r"])
            ppr = (WIN / 2.0) / math.tan(math.radians(fov / 2.0))
            ideal = ppr * math.tan(math.radians(SUN_DIA_DEG / 2.0))
            if ideal >= v.SUN_POINT_MIN_R_PX:
                assert got[fov] == pytest.approx(ideal, rel=0.05), (fov, got[fov], ideal)
    finally:
        v.close()
    assert got[0.5] == pytest.approx(373.0, rel=0.05)
    assert got[120.0] == pytest.approx(v.SUN_POINT_MIN_R_PX, abs=0.01)
    assert got[0.5] / got[5.0] == pytest.approx(10.0, rel=0.03)


def test_sun_draw_uses_angular_size_not_fixed_14px():
    v = _new_view()
    try:
        v.set_celestial_bodies(_sun_dict(alt=45.0), None, 0.0)
        v.view_state.look_at(180.0, 45.0)
        v.view_state.fov_deg = 1.0
        img = QImage(WIN, WIN, QImage.Format_ARGB32)
        img.fill(QColor(0, 0, 0))
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        try:
            v._draw_celestial_bodies(p)
        finally:
            p.end()
        geom = v._sun_screen_geom()
        r = float(geom["r"])
        c = geom["center"]
        # 圆面中心点亮、圆面外 1.3r 处不亮（说明不是 14px 写死光环）
        assert img.pixelColor(int(c.x()), int(c.y())).red() > 150
        for ang in (0.0, 90.0, 180.0, 270.0):
            xx = int(c.x() + 1.35 * r * math.cos(math.radians(ang)))
            yy = int(c.y() + 1.35 * r * math.sin(math.radians(ang)))
            col = img.pixelColor(xx, yy)
            assert col.red() < 200 or r < 4, (ang, col.name())
    finally:
        v.close()


# --------------------------------------------------------------------------
# 2. 日出日落特例
# --------------------------------------------------------------------------
def test_sunrise_special_case_clamps_center_to_horizon():
    v = _new_view()
    try:
        v.view_state.look_at(180.0, 0.0)
        v.view_state.fov_deg = 60.0
        v.set_celestial_bodies(_sun_dict(alt=-0.5), None, 0.0)
        geom = v._sun_screen_geom()
        assert geom is not None, "日出日落（alt=-0.5°）仍应画太阳"
        assert geom["alt_draw"] == 0.0
        horizon = v._sky_to_screen(180.0, 0.0)
        assert geom["center"].y() == pytest.approx(horizon.y(), abs=0.5)

        v.set_celestial_bodies(_sun_dict(alt=-1.1), None, 0.0)
        assert v._sun_screen_geom() is None

        v.set_celestial_bodies(_sun_dict(alt=-1.0), None, 0.0)
        assert v._sun_screen_geom() is None
    finally:
        v.close()


# --------------------------------------------------------------------------
# 3. 绘制与拾取同一事实源
# --------------------------------------------------------------------------
def test_sun_pick_matches_draw_gate():
    v = _new_view()
    try:
        v.set_celestial_bodies(_sun_dict(alt=30.0, az=180.0), None, 0.0)
        v.view_state.look_at(180.0, 30.0)
        v.view_state.fov_deg = 20.0
        geom = v._sun_screen_geom()
        assert geom is not None
        c = geom["center"]
        body = v._find_body_at(c.x(), c.y())
        assert body is not None and body.get("kind") == "sun", body

        v.set_celestial_bodies(_sun_dict(alt=-2.0), None, 0.0)
        v.view_state.look_at(180.0, 0.0)
        below = v._find_body_at(c.x(), c.y())
        assert below is None or below.get("kind") != "sun"
    finally:
        v.close()


# --------------------------------------------------------------------------
# 4. 信息卡与行星同骨架
# --------------------------------------------------------------------------
def test_sun_info_card_fields():
    v = _new_view()
    try:
        ra, dec = 180.0, 5.0
        v.set_celestial_bodies({"az": 200.0, "alt": 20.0,
                                "diameter_deg": SUN_DIA_DEG,
                                "distance_au": 1.003, "ra_deg": ra,
                                "dec_deg": dec}, None, 0.0)
        v._build_body_info({"kind": "sun", "key": "sun"})
        info = v._body_info
        assert info is not None
        assert info["title"] == "太阳"
        assert info["kind_label"] == "Star"
        assert info["mag"] == pytest.approx(-26.74)
        assert info["ra"] == pytest.approx(ra)
        assert info["dec"] == pytest.approx(dec)
        assert "0.533" in info["desc"] or "32.0" in info["desc"]
        assert "AU" in info["dist_text"]
        assert info["rise"] is not None and info["set"] is not None
    finally:
        v.close()


def test_sun_without_ephemeris_does_not_invent_diameter():
    v = _new_view()
    try:
        v.set_celestial_bodies(_sun_dict(alt=30.0, with_dia=False), None, 0.0)
        geom = v._sun_screen_geom()
        assert geom is not None
        assert float(geom["r"]) == pytest.approx(v.SUN_POINT_MIN_R_PX)

        v._build_body_info({"kind": "sun", "key": "sun"})
        info = v._body_info
        assert "缺测" in info["desc"]
        assert info["ra"] is None
        assert info["dist_text"] == "—"
    finally:
        v.close()
