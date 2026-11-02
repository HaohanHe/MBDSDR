# SPDX-License-Identifier: MIT
"""
tests/test_sky_selection_cleanup.py
====================================
验证选中态清理与站址/EXT 星绘制修复（2026-11-02 审计发现）:

  1. 空白点击: 卫星选中后点空白 -> 卫星卡/环与 body 全清;
  2. 搜索选卫星: 统一入口 on_object_picked, 清 body 且发 object_clicked;
  3. EXT 星（北斗 Merak 等）也参与绘制（此前只画 BRIGHT_STARS）;
  4. 主窗口站址接线: _apply_observer_location 必须真调 set_observer。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_sky_selection_cleanup.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from datetime import datetime, timezone  # noqa: E402

from PySide6.QtCore import Qt, QPointF  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from rf_sky_view import RFSkyView, SkyObject  # noqa: E402
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402
from mbdsdr_ai.skyengine import constellations as _constel  # noqa: E402
from mbdsdr_ai.celestial_geometry import (  # noqa: E402
    GroundStation, vec_from_radec, j2000_to_altaz, azalt_from_vec,
)

LAT, LON = 40.0, 116.0


def _new_view():
    eng = get_time_engine()
    eng.set_unix(datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc).timestamp())
    eng.pause()
    v = RFSkyView()
    v.resize(700, 700)
    v.set_observer(LAT, LON, 0.0)
    return v


def _click(v, x, y):
    for typ in (QMouseEvent.Type.MouseButtonPress,
                QMouseEvent.Type.MouseButtonRelease):
        ev = QMouseEvent(typ, QPointF(x, y), Qt.LeftButton,
                         Qt.LeftButton, Qt.NoModifier)
        (v.mousePressEvent(ev) if typ == QMouseEvent.Type.MouseButtonPress
         else v.mouseReleaseEvent(ev))


def _empty_point(v):
    for gx in range(20, v.width() - 20, 37):
        for gy in range(20, v.height() - 20, 41):
            if v._find_body_at(float(gx), float(gy)) is None \
                    and v._find_object_at(float(gx), float(gy)) is None:
                return float(gx), float(gy)
    raise AssertionError("无空白点")


class TestBlankClearsSatellite:
    def test_blank_click_clears_satellite_and_body(self):
        v = _new_view()
        v.add_object(SkyObject("SAT X", 90.0, 45.0, obj_type="satellite"))
        pos = v._sky_to_screen(90.0, 45.0)
        _click(v, pos.x(), pos.y())
        assert v._hovered_object is not None
        ex, ey = _empty_point(v)
        _click(v, ex, ey)
        assert v._hovered_object is None, "空白点击后卫星卡应清除"
        assert v._picked_body is None
        assert v._selected_info is None


class TestSearchSatelliteUnified:
    def test_search_sat_clears_body_and_emits(self):
        v = _new_view()
        v.add_object(SkyObject("ISS (ZARYA)", 90.0, 45.0,
                               obj_type="satellite"))
        v.set_satellite_catalog([{"name": "ISS (ZARYA)", "line1": "",
                                  "line2": ""}])
        v.on_body_picked({"kind": "planet", "key": "mars", "zh": "火星"})
        assert v._picked_body is not None
        v._on_search_text("ISS")
        hits = [r for r in v._search_results if r["kind"] != "planet"]
        assert hits, "应能搜到 ISS"
        got = []
        v.object_clicked.connect(lambda obj: got.append(obj))
        # 走真实下拉选中分支（与鼠标点击 QListWidget 等价）
        v._search_popup.setCurrentRow(0)
        v._on_search_picked(v._search_popup.currentItem())
        assert v._picked_body is None, "搜索选卫星应清掉行星选中态"
        assert got, "应发 object_clicked"


class TestExtStarsDrawn:
    def test_ext_star_visible_after_draw(self):
        v = _new_view()
        # 选一颗 EXT 星, 找仰角>15°的
        gs = GroundStation(LON, LAT, 0.0)
        jd = v._current_jd()
        idx = _constel.merged_star_index()
        chosen = None
        for name in ("Seginus", "Izar", "Rasalhague", "Merak", "Nunki"):
            s = idx[name]
            az, alt = azalt_from_vec(j2000_to_altaz(
                vec_from_radec(s.ra_deg, s.dec_deg), gs, jd))
            if alt > 15.0:
                chosen = (name, az, alt)
                break
        assert chosen, "EXT 星该历元应有可见者"
        name, az, alt = chosen
        v.view_state.look_at(az, alt)
        v.show()
        app.processEvents()
        v.repaint()
        img = v.grab().toImage()
        cx, cy = img.width() // 2, img.height() // 2
        lit = 0
        for dy in range(-8, 9):
            for dx in range(-8, 9):
                c = img.pixelColor(cx + dx, cy + dy)
                if c.red() + c.green() + c.blue() > 60:
                    lit += 1
        assert lit > 3, f"EXT 星 {name} 应被绘制在视中心"


class TestObserverWiring:
    def test_main_window_sets_observer(self):
        src = open(os.path.join(_ROOT, "desktop", "main_window.py"),
                   encoding="utf-8").read()
        i = src.index("def _apply_observer_location")
        j = src.index("def ", i + 10)
        body = src[i:j]
        assert "self.sky_view.set_observer(lat, lon)" in body, \
            "手动站址必须真调 set_observer（此前只标 real 未注入）"
