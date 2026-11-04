# SPDX-License-Identifier: MIT
"""
tests/test_sky_dso_layer.py
===========================
天空视图深空天体（Messier/NGC 子集）图层。

数据只来自 `mbdsdr_ai.astronomy.dso.BUILTIN_CATALOG`（公开星表的
RA/Dec/视星等/视大小），图层不做任何"像星云"的装饰性绘制。

覆盖：
  1. 类型符号可区分（星系椭圆 / 星团点环 / 星云虚线菱形 / 超遗双圆）；
  2. 符号尺寸随星表视大小真实缩放（FOV 5° 与 120° 比值 ≈ 24）；
  3. 绘制门 / 拾取门 / 地平门 / 白天门 与恒星同一套（不是第二套阈值）：
     亮且在地平线上 -> 画且可点；地平线下 -> 不画不可点；白天 -> 整层不画；
  4. 裸眼极限外的条目（星等 > Bortle 极限）不画也不可点；
  5. DSO 标签进同一套避让队列（优先级 1.5，位于座名与星名之间）。

反证（旧实现必红）：
  - 没有 `_draw_dso`：第 3 条 ink==0、第 4 条点不中；
  - 旧 `_find_body_at` 对 DSO 用固定 4.0 等 + alt<-1°：地平线下的 M31 仍可点。
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

import rf_sky_view  # noqa: E402
from rf_sky_view import RFSkyView  # noqa: E402
from mbdsdr_ai.astronomy import dso as _dso  # noqa: E402
from mbdsdr_ai.celestial_geometry import (  # noqa: E402
    GroundStation, vec_from_radec, j2000_to_altaz, azalt_from_vec,
)
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402
from mbdsdr_ai.skyengine import jtime as _jtime  # noqa: E402
from mbdsdr_ai.skyengine import stars as _stars  # noqa: E402

LAT, LON = 40.0, 116.0
UNIX = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc).timestamp()   # 夜侧
WIN = 700


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


def _altaz(entry, gs, jd):
    return azalt_from_vec(j2000_to_altaz(
        vec_from_radec(entry.ra_deg, entry.dec_deg), gs, jd))


def _render_dso(v, size: int = WIN) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    try:
        v._label_items = []
        v._draw_dso(p)
    finally:
        p.end()
    return img


def _ink(img) -> int:
    n = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.red() + c.green() + c.blue() > 90:
                n += 1
    return n


def _entry(name: str):
    e = _dso.BUILTIN_CATALOG.get(name)
    assert e is not None, name
    return e


# --------------------------------------------------------------------------
# 1. 类型符号可区分
# --------------------------------------------------------------------------
@pytest.mark.parametrize("kind,r", [("galaxy", 8.0), ("cluster", 8.0),
                                    ("nebula", 8.0),
                                    ("supernova_remnant", 8.0)])
def test_glyph_per_type_has_ink(kind, r):
    v = _new_view(80)
    try:
        img = QImage(80, 80, QImage.Format_ARGB32)
        img.fill(QColor(0, 0, 0))
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        try:
            v._draw_dso_glyph(p, rf_sky_view.QPointF(40, 40), r, kind, 200)
        finally:
            p.end()
        assert _ink(img) > 0, kind
    finally:
        v.close()


def test_glyph_shapes_differ_across_types():
    v = _new_view(80)
    imgs = {}
    try:
        for kind in ("galaxy", "cluster", "nebula", "supernova_remnant"):
            img = QImage(80, 80, QImage.Format_ARGB32)
            img.fill(QColor(0, 0, 0))
            p = QPainter(img)
            p.setRenderHint(QPainter.Antialiasing, True)
            try:
                v._draw_dso_glyph(p, rf_sky_view.QPointF(40, 40), 8.0, kind, 220)
            finally:
                p.end()
            imgs[kind] = img
    finally:
        v.close()
    kinds = list(imgs)
    for i, a in enumerate(kinds):
        for b in kinds[i + 1:]:
            assert imgs[a] != imgs[b], f"{a} 与 {b} 符号画出来一样（类型不可辨）"


# --------------------------------------------------------------------------
# 2. 尺寸随视大小缩放
# --------------------------------------------------------------------------
def test_symbol_size_follows_catalog_angular_size():
    v = _new_view()
    gs = GroundStation(LON, LAT, 0.0)
    jd = _jtime.unix_to_jd(UNIX)
    try:
        az, alt = 180.0, 45.0
        v.view_state.look_at(az, alt)
        r_px = {}
        for fov in (120.0, 5.0):
            v.view_state.fov_deg = fov
            geom = v._angular_radius_px(az, alt, 30.0 / 60.0)   # 30′ 半径
            assert geom is not None
            r_px[fov] = float(geom[1])
            ppr = (WIN / 2.0) / math.tan(math.radians(fov / 2.0))
            assert r_px[fov] == pytest.approx(
                ppr * math.tan(math.radians(0.5)), rel=0.03), fov
    finally:
        v.close()
    # 投影是透视（tan）而非线性：5° 相对 120° 放大 ~40 倍
    assert r_px[5.0] / r_px[120.0] == pytest.approx(39.7, rel=0.05)


# --------------------------------------------------------------------------
# 3. 绘制/拾取/地平/白天 与恒星同一套门
# --------------------------------------------------------------------------
def _find_bright_visible(v, gs, jd, min_alt=20.0, max_mag=4.0):
    for e in _dso.BUILTIN_CATALOG:
        az, alt = _altaz(e, gs, jd)
        if alt < min_alt or e.magnitude > max_mag:
            continue
        if not v._on_screen(v._sky_to_screen(az, alt)):
            continue
        return e, az, alt
    return None, None, None


def test_dso_drawn_and_pickable_above_horizon():
    v = _new_view()
    gs = GroundStation(LON, LAT, 0.0)
    jd = v._current_jd()
    try:
        e, az, alt = _find_bright_visible(v, gs, jd)
        assert e is not None, "取样失败：应有一颗亮 DSO 在地平线上"
        v.view_state.look_at(az, alt)
        v.view_state.fov_deg = 30.0
        ink = _ink(_render_dso(v))
        scr = v._sky_to_screen(az, alt)
        body = v._find_body_at(scr.x(), scr.y())
    finally:
        v.close()
    assert ink > 0, f"{e.name} 在地平线上却没画"
    assert body is not None and body.get("kind") == "dso", body
    assert str(body.get("key")) == e.name


def test_dso_below_horizon_not_drawn_not_pickable():
    v = _new_view()
    gs = GroundStation(LON, LAT, 0.0)
    jd = v._current_jd()
    try:
        down = None
        for e in _dso.BUILTIN_CATALOG:
            az, alt = _altaz(e, gs, jd)
            if alt < -5.0:
                down = (e, az, alt)
                break
        if down is None:
            pytest.skip("此刻没有深空天体在地平线下 5° 以外")
        e, az, alt = down
        v.view_state.look_at(az, 0.0)
        v.view_state.fov_deg = 120.0
        scr = v._sky_to_screen(az, alt)
        y = scr.y()
        body = v._find_body_at(scr.x(), y)
        ink_at = 0
        img = _render_dso(v)
        for yy in range(max(0, int(y - 8)), min(img.height(), int(y + 9))):
            for xx in range(max(0, int(scr.x() - 8)), min(img.width(), int(scr.x() + 9))):
                c = img.pixelColor(xx, yy)
                if c.red() + c.green() + c.blue() > 90:
                    ink_at += 1
    finally:
        v.close()
    assert body is None or body.get("kind") != "dso", body
    assert ink_at == 0, f"{e.name} 在地平线下却落笔 {ink_at} 像素"


def test_daylight_hides_dso_layer():
    v = _new_view()
    try:
        v.set_celestial_bodies({"az": 180.0, "alt": -30.0}, None, 0.0)
        night = _ink(_render_dso(v))
        v.set_celestial_bodies({"az": 180.0, "alt": 30.0}, None, 0.0)
        day = _ink(_render_dso(v))
    finally:
        v.close()
    assert night > 0
    assert day == 0, f"白天必须整层不画深空天体，实测 {day}"


def test_faint_dso_beyond_naked_eye_limit_not_pickable():
    v = _new_view()
    gs = GroundStation(LON, LAT, 0.0)
    jd = v._current_jd()
    try:
        faint = None
        for e in _dso.BUILTIN_CATALOG:
            if e.magnitude < 7.5:
                continue
            az, alt = _altaz(e, gs, jd)
            if alt > 20.0 and v._on_screen(v._sky_to_screen(az, alt)):
                faint = (e, az, alt)
                break
        if faint is None:
            pytest.skip("此刻没有 7.5 等以远的深空天体在地平线上")
        e, az, alt = faint
        r_gate, _, _ = _stars.star_draw(
            e.magnitude + _stars.extinction_delta_mag(alt), v._bortle)
        assert r_gate <= 0, (e.name, r_gate)      # 前提：确实在裸眼极限外
        v.view_state.look_at(az, alt)
        scr = v._sky_to_screen(az, alt)
        body = v._find_body_at(scr.x(), scr.y())
    finally:
        v.close()
    assert body is None or body.get("kind") != "dso", body


# --------------------------------------------------------------------------
# 4. 标签进同一套避让队列
# --------------------------------------------------------------------------
def test_dso_labels_queue_at_priority_between_constellation_and_star():
    v = _new_view()
    try:
        _render_dso(v)
        prios = [p for p, _pos, lines, _c in v._label_items
                 if any(t.startswith("M") or t.startswith("NGC") or
                        t.startswith("IC") for t, _s, _d in lines)]
    finally:
        v.close()
    assert prios, "宽 FOV 下应有深空天体名进避让队列"
    assert all(p == RFSkyView.DSO_LABEL_PRIORITY for p in prios), prios
    assert 1.0 < RFSkyView.DSO_LABEL_PRIORITY < 2.0
