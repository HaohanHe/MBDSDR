# SPDX-License-Identifier: MIT
"""
tests/test_sky_planets.py
==========================
验证天空视图行星层 (Stellarium 首屏玩法: 行星标记 + 搜索 + 拾取 + 信息卡):

  1. 行星位置: compute_all 固定历元 sanity (库内 de440s 对拍在
     tests/test_planets.py, 这里只测接线口径);
  2. 搜索: 西名 "mars" / 中文名 "火星" 双匹配出分类候选;
  3. 拾取: 点击行星屏幕坐标 -> _picked_body + 信息卡字段 (标题/星等/
     距离/升降); 点空白 -> 反选;
  4. 选中态互斥: 选卫星后行星态清空, 反之亦然;
  5. 升降扫描: Sirius (lat 40) 24h 内有升有落且 set > rise;
     Polaris (lat 40) 拱极不落实为 None;
  6. offscreen 渲染冒烟: 行星层开过 paintEvent 不崩。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_sky_planets.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import math  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

from PySide6.QtCore import Qt, QPointF  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from rf_sky_view import RFSkyView, SkyObject, _PLANET_STYLE  # noqa: E402
from mbdsdr_ai.astronomy import planets as P  # noqa: E402
from mbdsdr_ai.celestial_geometry import (  # noqa: E402
    GroundStation, vec_from_radec, j2000_to_altaz, azalt_from_vec,
)
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402
from mbdsdr_ai.skyengine import jtime as _jtime  # noqa: E402

# 固定历元: 2026-09-27 12:00 UTC (与 tests/test_planets.py 同源)
FIXED_UNIX = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc).timestamp()
FIXED_JD = _jtime.unix_to_jd(FIXED_UNIX)
LAT, LON = 40.0, 116.0


def _fix_time():
    eng = get_time_engine()
    eng.set_unix(FIXED_UNIX)
    eng.pause()


def _new_view(size: int = 700) -> RFSkyView:
    _fix_time()
    v = RFSkyView()
    v.resize(size, size)
    v.set_observer(LAT, LON, 0.0)
    return v


def _click(v: RFSkyView, x: float, y: float) -> None:
    for typ in (QMouseEvent.Type.MouseButtonPress,
                QMouseEvent.Type.MouseButtonRelease):
        ev = QMouseEvent(typ, QPointF(x, y),
                         Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        (v.mousePressEvent(ev) if typ == QMouseEvent.Type.MouseButtonPress
         else v.mouseReleaseEvent(ev))


def _above_horizon_planet(v: RFSkyView, min_alt: float = 5.0):
    """按亮度顺序找一颗仰角足够的行星, 返回 (key, az, alt); 无则 None。"""
    gs = GroundStation(LON, LAT, 0.0)
    jd = v._current_jd()
    for key in ("jupiter", "mars", "venus", "saturn", "mercury"):
        eph = P.compute_planet(key, jd)
        az, alt = azalt_from_vec(
            j2000_to_altaz(vec_from_radec(eph.ra_deg, eph.dec_deg), gs, jd))
        if alt >= min_alt:
            return key, az, alt
    return None


def _center_and_click(v: RFSkyView, az: float, alt: float) -> None:
    """把行星放到视中心后点击 (等价用户先 goto 再点的真实交互)。"""
    v.view_state.look_at(az, alt)
    pos = v._sky_to_screen(az, alt)
    assert v._on_screen(pos), "居中后目标应在屏内"
    _click(v, pos.x(), pos.y())


class TestPlanetPositions:
    def test_compute_all_fixed_epoch(self):
        out = P.compute_all(FIXED_JD)
        # 口径: 7 行星 + 月球, 数值有限
        assert len(out) == 8
        for name, p in out.items():
            assert math.isfinite(p.ra_deg) and math.isfinite(p.dec_deg)
            assert -90.0 <= p.dec_deg <= 90.0
        # 火星距离物理范围 0.37..2.68 AU
        assert 0.3 < out["mars"].distance_au < 2.8
        # 木星 3.9..6.5 AU
        assert 3.8 < out["jupiter"].distance_au < 6.6
        # 月球 ~0.0024..0.0027 AU
        assert 0.002 < out["moon"].distance_au < 0.003


class TestSearch:
    def test_search_planet_by_english_name(self):
        v = _new_view()
        v._on_search_text("mars")
        hits = [r for r in v._search_results if r["kind"] == "planet"]
        assert any(r["key"] == "mars" for r in hits)

    def test_search_planet_by_chinese_name(self):
        v = _new_view()
        v._on_search_text("火星")
        hits = [r for r in v._search_results if r["kind"] == "planet"]
        assert any(r["key"] == "mars" for r in hits)

    def test_search_planet_pick_selects_body(self):
        v = _new_view()
        v._on_search_text("jupiter")
        hits = [r for r in v._search_results if r["kind"] == "planet"]
        assert hits, "固定历元下应能搜到木星"
        # 直接走选中通路 (下拉点击等价)
        v.on_body_picked({"kind": "planet", "key": "jupiter", "zh": "木星"})
        assert v._picked_body is not None
        info = v._body_info or {}
        assert info.get("title") == "木星"
        assert info.get("mag") is not None
        assert "AU" in str(info.get("dist_text"))
        assert info.get("ra") is not None and info.get("dec") is not None
        assert "rise" in info and "set" in info

    def test_search_empty_hides_popup(self):
        v = _new_view()
        v._on_search_text("")
        assert v._search_results == []


class TestPicking:
    def test_click_planet_picks_body(self):
        v = _new_view()
        got = _above_horizon_planet(v)
        assert got is not None, "测试历元下应至少一颗亮行星在地平上"
        key, az, alt = got
        _center_and_click(v, az, alt)
        assert v._picked_body is not None
        assert v._picked_body["kind"] == "planet"
        assert v._picked_body["key"] == key
        assert v._hovered_object is None       # 互斥

    def test_click_empty_sky_deselects(self):
        v = _new_view()
        v.on_body_picked({"kind": "planet", "key": "mars", "zh": "火星"})
        assert v._picked_body is not None
        # 点天顶附近空白 (天体拾取容差 14px 内无任何行星/恒星/卫星)
        _click(v, v.width() - 6.0, v.height() / 2.0)
        assert v._picked_body is None
        assert v._body_info is None

    def test_satellite_pick_clears_body(self):
        v = _new_view()
        v.on_body_picked({"kind": "planet", "key": "mars", "zh": "火星"})
        assert v._picked_body is not None
        # 放一颗卫星并点它
        v.add_object(SkyObject("TEST SAT 1", 180.0, 60.0,
                               obj_type="satellite"))
        pos = v._sky_to_screen(180.0, 60.0)
        _click(v, pos.x(), pos.y())
        assert v._hovered_object is not None
        assert v._picked_body is None        # 互斥清理

    def test_body_pick_clears_satellite(self):
        v = _new_view()
        v.add_object(SkyObject("TEST SAT 2", 90.0, 45.0,
                               obj_type="satellite"))
        pos = v._sky_to_screen(90.0, 45.0)
        _click(v, pos.x(), pos.y())
        assert v._hovered_object is not None
        got = _above_horizon_planet(v)
        assert got is not None
        _key, az, alt = got
        _center_and_click(v, az, alt)
        assert v._picked_body is not None
        assert v._hovered_object is None


class TestRiseSet:
    def test_sirius_rises_and_sets(self):
        v = _new_view()
        rs = v._body_rise_set(101.287, -16.716, v._current_jd())
        assert rs["rise_jd"] is not None, "Sirius (dec -16.7, lat 40) 应升起"
        assert rs["set_jd"] is not None, "Sirius 应落下"
        assert rs["set_jd"] > rs["rise_jd"]

    def test_polaris_circumpolar_at_lat40(self):
        v = _new_view()
        rs = v._body_rise_set(37.954, 89.264, v._current_jd())
        # 拱极: 24h 扫描无穿越 -> None 如实占位 (不编造时刻)
        assert rs["rise_jd"] is None or rs["set_jd"] is None

    def test_no_observer_returns_none(self):
        _fix_time()
        v = RFSkyView()
        # 未设观测站: 诚实空态
        assert v._find_body_at(100.0, 100.0) is None
        assert v._body_rise_set(101.287, -16.716, 2461240.0) == \
            {"rise_jd": None, "set_jd": None}


class TestRenderSmoke:
    def test_paint_with_planets_on(self):
        v = _new_view()
        v.show()
        app.processEvents()
        v.repaint()
        # 行星层与信息卡都在 paintEvent 路径上, 不崩即过
        v.on_body_picked({"kind": "planet", "key": "mars", "zh": "火星"})
        v.repaint()
        v.on_object_picked(None)
        v.repaint()
