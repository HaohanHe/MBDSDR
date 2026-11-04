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
  5. DSO 标签进同一套避让队列（优先级 1.5，位于座名与星名之间）；
  6. 窄视场限幅：真实视半径 > 视口 40% 才截断，限幅有可见标注，
     拾取半径与符号同一事实源（M31 不再吃掉整个视口）。

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
        if e.magnitude is None or alt < min_alt or e.magnitude > max_mag:
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
            if e.magnitude is None or e.magnitude < 7.5:
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
# 4. 银河图层诚实空态（本视图确实没有该图层，状态卡必须说明）
# --------------------------------------------------------------------------
def _card_row_crop(v, dy: int, h: int) -> QImage:
    img = QImage(v.width(), v.height(), QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0))
    p = QPainter(img)
    try:
        v._draw_info_overlay(p)
    finally:
        p.end()
    card_y = v.height() - RFSkyView.INFO_CARD_H - 12
    return img.copy(12 + 52, card_y + dy, 196, h)


def _diff_pixels(a, b) -> int:
    n = 0
    for y in range(a.height()):
        for x in range(a.width()):
            if a.pixelColor(x, y) != b.pixelColor(x, y):
                n += 1
    return n


def test_status_card_states_no_galaxy_layer(monkeypatch):
    """状态卡必须明示「无数据·不提供」；不画装饰性银河带冒充图层。"""
    v = _new_view()
    try:
        # 银河行位于观测站 + 视轴 + 视角 + 调谐 + 受晒之后。
        # 批 1 在银河行之前新增「恒星时 / 视轴」两行 → 下移 36px（188→224）。
        shown = _card_row_crop(v, 224, 20)
        # 反证：把该行文案清空后同一区域必须变（证明绘制路径真在写这句）
        monkeypatch.setattr(rf_sky_view, "GALAXY_LAYER_NOTE", "")
        blank = _card_row_crop(v, 224, 20)
    finally:
        v.close()
    assert _diff_pixels(shown, blank) > 20, "状态卡没有写「本视图无银河图层」的说明"


# --------------------------------------------------------------------------
# 5. 标签进同一套避让队列
# --------------------------------------------------------------------------
def test_dso_labels_queue_at_priority_between_constellation_and_star():
    v = _new_view()
    try:
        _render_dso(v)
        catalog_labels = {rf_sky_view.dso_label_text(e)
                          for e in _dso.BUILTIN_CATALOG}
        hits = [(p, lines) for p, _pos, lines, _c in v._label_items
                if len(lines) == 1 and lines[0][0] in catalog_labels]
    finally:
        v.close()
    assert hits, "宽 FOV 下应有深空天体名进避让队列"
    assert all(p == RFSkyView.DSO_LABEL_PRIORITY for p, _l in hits), hits
    assert 1.0 < RFSkyView.DSO_LABEL_PRIORITY < 2.0


# --------------------------------------------------------------------------
# 6. 窄视场限幅：只超视口 40% 才截断、限幅可见标注、拾取与符号同步
# --------------------------------------------------------------------------
def _m31_at(v):
    gs = GroundStation(LON, LAT, 0.0)
    jd = v._current_jd()
    e = _entry("M31")
    az, alt = _altaz(e, gs, jd)
    assert alt > 20.0, alt
    v.view_state.look_at(az, alt)
    return gs, jd, e, az, alt


def test_clamp_only_when_symbol_exceeds_viewport():
    """两个模型只有一条判据：真实视半径 > 视口 40% 才限幅，否则照实画。"""
    v = _new_view()
    try:
        _gs, _jd, e, az, alt = _m31_at(v)
        cap = RFSkyView.DSO_CLAMP_VIEW_FRAC * WIN

        v.view_state.fov_deg = 5.0
        real_wide = float(v._angular_radius_px(
            az, alt, e.size_arcmin / 120.0)[1])
        r_wide, clamped_wide = v._dso_symbol_geometry(az, alt, e.size_arcmin)
        assert clamped_wide is False and real_wide < cap
        assert r_wide == pytest.approx(real_wide)

        v.view_state.fov_deg = 0.5
        real_narrow = float(v._angular_radius_px(
            az, alt, e.size_arcmin / 120.0)[1])
        r_narrow, clamped_narrow = v._dso_symbol_geometry(az, alt,
                                                          e.size_arcmin)
        assert real_narrow > cap, real_narrow    # 前提：真实角径确实超出视口
        assert clamped_narrow is True
        assert r_narrow == pytest.approx(cap)
    finally:
        v.close()


def _danger_pixels(img: QImage) -> int:
    n = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.red() > c.green() + 25 and c.red() > c.blue() + 25:
                n += 1
    return n


def test_clamped_symbol_has_visible_marker():
    """限幅必须看得见（虚线外框 +「已限幅」红角标）；未超限时不得出现。"""
    v = _new_view()
    try:
        _gs, _jd, _e, _az, _alt = _m31_at(v)
        v.view_state.fov_deg = 0.5
        clamped_img = _render_dso(v)
        v.view_state.fov_deg = 5.0
        normal_img = _render_dso(v)
    finally:
        v.close()
    assert _danger_pixels(clamped_img) > 20, "限幅符号没有可见标注"
    assert _danger_pixels(normal_img) == 0, "未超限的天体不该被标注为已限幅"


def test_clamped_dso_pick_radius_follows_symbol(monkeypatch):
    """限幅后命中区同步收窄：M31 不再吃掉视口，视口内其它天体仍可点中。"""
    from mbdsdr_ai.celestial_geometry import (
        altaz_to_j2000, vec_from_azalt, radec_from_vec)

    v = _new_view()
    try:
        gs, jd, _e, az, alt = _m31_at(v)
        v.view_state.fov_deg = 0.5
        center = v._sky_to_screen(az, alt)
        cap = RFSkyView.DSO_CLAMP_VIEW_FRAC * WIN

        # 1) 符号内仍能点中 M31
        body_in = v._find_body_at(center.x() + cap * 0.8, center.y())
        assert body_in and body_in.get("kind") == "dso" \
            and body_in.get("key") == "M31", body_in

        # 2) 限幅外的空点不再被 M31 吃掉（旧实现命中半径 ~2077px，必红）
        blank = v._find_body_at(center.x() + cap + 40, center.y())
        assert blank is None or blank.get("kind") != "dso", blank

        # 3) 视口内另一颗天体（合成 3 等星，落在限幅符号之外）仍能点中。
        #    新接缝是当帧星表选择结果（_star_sel），不再是 merged_star_index；
        #    用一份只含该星的最小 selection 模拟「当帧画了这颗星」。
        import numpy as np
        st_az, st_alt = az + 0.30, alt
        st_vec = altaz_to_j2000(vec_from_azalt(st_az, st_alt), gs, jd)
        ra_s, dec_s = radec_from_vec(st_vec)
        class _OneStar:
            az_deg = np.array([st_az])
            alt_deg = np.array([st_alt])
            eff_mag = np.array([3.0])
            vmag = np.array([3.0])
            name = np.array([""])
            hyg_id = np.array([-1])
            ra_deg = np.array([ra_s])
            dec_deg = np.array([dec_s])
            bv = np.array([np.nan])
            dist_pc = np.array([np.nan])
            spect = np.array([""])
            con = np.array([""])
            limit_mag = 9.0
            available = returned = 1
            truncated = beyond_catalog_limit = False

            def __len__(self):
                return 1

        fake = _OneStar()
        monkeypatch.setattr(v, "_star_sel", fake, raising=False)
        st_scr = v._sky_to_screen_finite(st_az, v._display_alt_deg(st_alt))
        d_star = math.hypot(st_scr.x() - center.x(), st_scr.y() - center.y())
        assert d_star > cap, (d_star, cap)       # 前提：星在限幅符号之外
        body_star = v._find_body_at(st_scr.x(), st_scr.y())
        assert body_star and body_star.get("kind") == "star", body_star
    finally:
        v.close()


# --------------------------------------------------------------------------
# 7. 批 2：cluster_nebula / double_star 两个 glyph（原 else 分支一律画星云）
# --------------------------------------------------------------------------
def _glyph_img(v, kind: str, r: float = 10.0) -> QImage:
    img = QImage(80, 80, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    try:
        v._draw_dso_glyph(p, rf_sky_view.QPointF(40, 40), r, kind, 220)
    finally:
        p.end()
    return img


def _ink_px(img, x0, x1, y0, y1) -> int:
    return sum(1 for y in range(y0, y1) for x in range(x0, x1)
               if sum(img.pixelColor(x, y).getRgb()[:3]) > 90)


@pytest.mark.parametrize("kind", ["cluster_nebula", "double_star"])
def test_new_dso_glyphs_have_ink(kind):
    v = _new_view(80)
    try:
        assert _ink_px(_glyph_img(v, kind), 0, 80, 0, 80) > 0, kind
    finally:
        v.close()


def test_new_dso_glyphs_differ_from_old_types():
    """两个新类型不能落回旧 else 的虚线菱形，也不能与星团/星云同形。"""
    v = _new_view(80)
    imgs = {}
    try:
        for kind in ("cluster_nebula", "double_star", "cluster", "nebula"):
            imgs[kind] = _glyph_img(v, kind)
    finally:
        v.close()
    for a, b in (("cluster_nebula", "cluster"), ("cluster_nebula", "nebula"),
                 ("double_star", "cluster"), ("double_star", "nebula"),
                 ("cluster_nebula", "double_star")):
        assert imgs[a] != imgs[b], f"{a} 与 {b} 画出来一样（类型不可辨）"


def test_double_star_glyph_is_two_dots_with_empty_center():
    """双星：左右两个实心点、中心留空（与星团点环区分）。"""
    v = _new_view(80)
    try:
        img = _glyph_img(v, "double_star", 10.0)
    finally:
        v.close()
    assert _ink_px(img, 39, 42, 39, 42) == 0, "双星中心必须留空"
    assert _ink_px(img, 26, 39, 34, 47) > 0, "左星缺失"
    assert _ink_px(img, 42, 55, 34, 47) > 0, "右星缺失"


def test_cluster_nebula_glyph_has_outer_shell_and_inner_cluster():
    """复合体：外侧虚线(星云) + 内侧点环(星团)，两层都要落笔。"""
    v = _new_view(80)
    try:
        img = _glyph_img(v, "cluster_nebula", 10.0)
    finally:
        v.close()
    assert _ink_px(img, 39, 42, 39, 42) == 0, "中心应留空"
    inner = _ink_px(img, 32, 48, 32, 48)
    outer = (_ink_px(img, 24, 32, 24, 56) + _ink_px(img, 48, 56, 24, 56)
             + _ink_px(img, 32, 48, 24, 32) + _ink_px(img, 32, 48, 48, 56))
    assert inner > 0 and outer > 0, (inner, outer)


# --------------------------------------------------------------------------
# 8. 批 2：标签来源 display_name/zh_name，缺失退回编号，永不空串
# --------------------------------------------------------------------------
def test_dso_label_prefers_display_name_and_falls_back_to_designation():
    from types import SimpleNamespace
    named = SimpleNamespace(display_name="仙女座大星系", zh_name="", name="M31")
    assert rf_sky_view.dso_label_text(named) == "仙女座大星系"
    numbered = SimpleNamespace(display_name="", zh_name="", name="M40")
    assert rf_sky_view.dso_label_text(numbered) == "M40"
    blank = SimpleNamespace(display_name="", zh_name="", name="")
    assert rf_sky_view.dso_label_text(blank) == "深空天体"
    assert rf_sky_view.dso_label_text(SimpleNamespace()) == "深空天体"
    assert rf_sky_view.dso_label_text(blank).strip()


def test_catalog_label_matches_entry_display_name():
    e = _entry("M31")
    assert rf_sky_view.dso_label_text(e) == e.display_name
    assert rf_sky_view.dso_label_text(e).strip()


# --------------------------------------------------------------------------
# 9. 批 2：星系椭圆用真实长短轴比；缺小轴标「轴比未知」
# --------------------------------------------------------------------------
def test_axis_ratio_uses_catalog_minor_axis():
    from types import SimpleNamespace
    assert rf_sky_view.dso_axis_ratio(
        SimpleNamespace(size_arcmin=10.0, size_minor_arcmin=4.0)
    ) == pytest.approx(0.4)
    # 缺小轴 / 0 / 大于长轴 —— 全是「未知」，不拿 0 或 0.55 冒充真值
    assert rf_sky_view.dso_axis_ratio(
        SimpleNamespace(size_arcmin=10.0)) is None
    assert rf_sky_view.dso_axis_ratio(
        SimpleNamespace(size_arcmin=10.0, size_minor_arcmin=0.0)) is None
    assert rf_sky_view.dso_axis_ratio(
        SimpleNamespace(size_arcmin=10.0, size_minor_arcmin=12.0)) is None


def test_real_catalog_axis_ratio_matches_fields():
    with_minor = [e for e in _dso.BUILTIN_CATALOG
                  if (e.size_minor_arcmin or 0.0) > 0.0]
    if not with_minor:
        pytest.skip("当前目录没有可用的短轴数据")
    e = with_minor[0]
    assert rf_sky_view.dso_axis_ratio(e) == pytest.approx(
        e.size_minor_arcmin / e.size_arcmin)


# --------------------------------------------------------------------------
# 10. 批 2：空字段显示「未知」，不拿 0 顶替（OpenNGC 换源口径）
# --------------------------------------------------------------------------
def test_empty_magnitude_and_size_are_none_not_zero():
    from types import SimpleNamespace
    e = SimpleNamespace(magnitude=None, size_arcmin=0.0)
    assert rf_sky_view.dso_magnitude(e) is None
    assert rf_sky_view.dso_size_arcmin(e) is None
    v = _new_view(200)
    try:
        r, clamped = v._dso_symbol_geometry(180.0, 45.0, None)
    finally:
        v.close()
    assert r >= RFSkyView.DSO_MIN_SYMBOL_R_PX     # 最小可辨符号，不是 0
    assert clamped is False


def test_empty_dso_fields_render_unknown_in_body_card(monkeypatch):
    from types import SimpleNamespace
    base = _entry("M40")
    fake = SimpleNamespace(
        name="M40", display_name="", zh_name="", name_en="",
        magnitude=None, size_arcmin=0.0, size_minor_arcmin=None,
        dso_type="double_star", constellation="",
        ra_deg=base.ra_deg, dec_deg=base.dec_deg)
    v = _new_view()
    try:
        monkeypatch.setattr(RFSkyView, "_dso_entry",
                            staticmethod(lambda name: fake))
        v._build_body_info({"kind": "dso", "key": "M40"})
        info = v._body_info
    finally:
        v.close()
    assert info["mag"] is None
    desc = str(info["desc"])
    assert "星等未知" in desc and "角大小 未知" in desc
    assert "0.0" not in desc and " 0′" not in desc


def test_gap_galaxy_marks_axis_ratio_unknown(monkeypatch):
    from types import SimpleNamespace
    base = _entry("M31")
    fake = SimpleNamespace(
        name="M31", display_name="", zh_name="", name_en="",
        magnitude=3.4, size_arcmin=180.0, size_minor_arcmin=None,
        dso_type="galaxy", constellation="And",
        ra_deg=base.ra_deg, dec_deg=base.dec_deg)
    v = _new_view()
    try:
        monkeypatch.setattr(RFSkyView, "_dso_entry",
                            staticmethod(lambda name: fake))
        v._build_body_info({"kind": "dso", "key": "M31"})
        info = v._body_info
    finally:
        v.close()
    assert "轴比未知" in str(info["desc"])
    assert "轴比 0.55" not in str(info["desc"])


# --------------------------------------------------------------------------
# 11. 轴比语义收紧：三态 source；unknown 禁用 0.55 实线椭圆
# --------------------------------------------------------------------------
def test_axis_ratio_source_three_states():
    from types import SimpleNamespace as NS
    src = rf_sky_view.dso_axis_ratio_source
    assert src(NS(size_arcmin=10.0, size_minor_arcmin=4.0)) == "catalog"
    assert src(NS(size_arcmin=10.0)) == "unknown"
    # 数据线直接给 axis_ratio 字段（仅长短轴都在时才非 null）
    assert src(NS(axis_ratio=0.39)) == "catalog"
    assert src(NS(axis_ratio=0.39, axis_ratio_source="estimated")) == "estimated"
    assert src(NS(axis_ratio=0.39, axis_ratio_source="unknown")) == "unknown"
    assert src(NS(size_arcmin=10.0, size_minor_arcmin=4.0,
                  axis_ratio_source="estimated")) == "estimated"
    # 声明 estimated 但没有任何可用值 → 仍未知，不许空值挂估计
    assert src(NS(size_arcmin=10.0, axis_ratio_source="estimated")) == "unknown"


def test_axis_ratio_prefers_data_line_field():
    from types import SimpleNamespace as NS
    assert rf_sky_view.dso_axis_ratio(
        NS(axis_ratio=0.39, size_arcmin=10.0, size_minor_arcmin=9.0)
    ) == pytest.approx(0.39)
    assert rf_sky_view.dso_axis_ratio(NS(axis_ratio=None, size_arcmin=10.0,
                                         size_minor_arcmin=4.0)) \
        == pytest.approx(0.4)


def test_real_m2_missing_minor_axis_is_unknown():
    """M2（OpenNGC GCl，无 MinAx）：轴比必须是 None / unknown，不拿 0.55。"""
    e = _entry("M2")
    assert e.size_minor_arcmin is None
    assert rf_sky_view.dso_axis_ratio(e) is None
    assert rf_sky_view.dso_axis_ratio_source(e) == "unknown"


def test_catalog_axis_ratio_card_is_labelled_catalog(monkeypatch):
    e = _entry("M31")
    expected = e.size_minor_arcmin / e.size_arcmin
    v = _new_view()
    try:
        monkeypatch.setattr(RFSkyView, "_dso_entry",
                            staticmethod(lambda name: e))
        v._build_body_info({"kind": "dso", "key": "M31"})
        desc = str(v._body_info["desc"])
    finally:
        v.close()
    assert f"轴比 {expected:.2f}（目录）" in desc
    assert "实测" not in desc


def test_estimated_axis_ratio_card_says_estimated(monkeypatch):
    from types import SimpleNamespace
    base = _entry("M31")
    fake = SimpleNamespace(
        name="NGC9999", display_name="", zh_name="", name_en="",
        magnitude=8.0, size_arcmin=10.0, size_minor_arcmin=5.5,
        axis_ratio=0.55, axis_ratio_source="estimated",
        dso_type="galaxy", constellation="And",
        ra_deg=base.ra_deg, dec_deg=base.dec_deg)
    v = _new_view()
    try:
        monkeypatch.setattr(RFSkyView, "_dso_entry",
                            staticmethod(lambda name: fake))
        v._build_body_info({"kind": "dso", "key": "NGC9999"})
        desc = str(v._body_info["desc"])
    finally:
        v.close()
    assert "轴比 0.55（估计）" in desc


def _galaxy_img(v, ratio=None, source="unknown", r: float = 10.0) -> QImage:
    img = QImage(80, 80, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    try:
        v._draw_dso_glyph(p, rf_sky_view.QPointF(40, 40), r, "galaxy", 220,
                          ratio, source)
    finally:
        p.end()
    return img


def _ink_bbox(img):
    xs, ys = [], []
    for y in range(img.height()):
        for x in range(img.width()):
            if sum(img.pixelColor(x, y).getRgb()[:3]) > 90:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return min(xs), max(xs), min(ys), max(ys)


def test_unknown_axis_ratio_glyph_is_circle_not_flat_ellipse():
    """探针：unknown 分支画圆（bbox 高≈宽），读不到 0.55 的扁椭圆。"""
    v = _new_view(80)
    try:
        img = _galaxy_img(v)                   # 缺省 source=unknown
        cat = _galaxy_img(v, 0.39, "catalog")
    finally:
        v.close()
    bb = _ink_bbox(img)
    assert bb is not None
    w = bb[1] - bb[0] + 1
    h = bb[3] - bb[2] + 1
    assert h >= 0.8 * w, (w, h)               # 圆：高不塌成 0.55
    bb_cat = _ink_bbox(cat)
    w2 = bb_cat[1] - bb_cat[0] + 1
    h2 = bb_cat[3] - bb_cat[2] + 1
    assert h2 <= 0.6 * w2, (w2, h2)           # 目录轴比 0.39 仍是扁椭圆


def test_axis_ratio_does_not_affect_pick_or_clamp():
    """轴比只影响符号外观/文案，不参与拾取、限幅、排序。"""
    import inspect
    for fn in (RFSkyView._find_body_at, RFSkyView._dso_symbol_geometry):
        assert "axis_ratio" not in inspect.getsource(fn), fn.__name__
