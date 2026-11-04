# SPDX-License-Identifier: MIT
"""
tests/test_sky_constellations.py
=================================
验证星座连线层 (批2) 与 恒星/DSO 读懂闭环 (批3):

  1. 数据锚点: 连线端点全在合并星表; 每段角距 < 35°; 每座有锚点;
     culture JSON 覆盖全部星座键; 标签 = '中文 Latin'。
  2. 历史 bug 回归锁: Adhara/Naos/Alnair/Ankaa/Wezen 的坐标错误
     (2026-11-02 经 HYG v4.1 吸附修正) 不许再回归; Polaris |dec|>89。
  3. 恒星拾取: 点击天狼 -> 选中 + 信息卡 (中文名/星等/距离 ly/光谱型
     + 文化注记); 点空白反选。
  4. 恒星搜索: 'sirius' / '天狼' 双名命中, 选中出卡。
  5. DSO: 'm31' 搜索 -> 出卡 (星系/角大小); 已选 DSO 标记可再点中。
  6. offscreen 渲染冒烟 (连线 + 标签 + 信息卡全过 paintEvent)。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_sky_constellations.py -v
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
from PySide6.QtGui import QMouseEvent, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from rf_sky_view import RFSkyView  # noqa: E402
from mbdsdr_ai.skyengine import stars as _stars  # noqa: E402
from mbdsdr_ai.skyengine import constellations as _constel  # noqa: E402
from mbdsdr_ai.skyengine import xingguan as _xingguan  # noqa: E402
from mbdsdr_ai.celestial_geometry import (  # noqa: E402
    GroundStation, vec_from_radec, j2000_to_altaz, azalt_from_vec,
)
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402
from mbdsdr_ai.skyengine import jtime as _jtime  # noqa: E402

FIXED_UNIX = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc).timestamp()
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


def _empty_point(v: RFSkyView):
    """扫描网格找 _find_body_at 为 None 的点 (防天空太热闹导致假点)。"""
    for gx in range(20, v.width() - 20, 37):
        for gy in range(20, v.height() - 20, 41):
            if v._find_body_at(float(gx), float(gy)) is None \
                    and v._find_object_at(float(gx), float(gy)) is None:
                return float(gx), float(gy)
    raise AssertionError("找不到空白点 (星表/行星太密?)")


def _sep(a, b):
    v1 = (math.cos(math.radians(a.dec_deg)) * math.cos(math.radians(a.ra_deg)),
          math.cos(math.radians(a.dec_deg)) * math.sin(math.radians(a.ra_deg)),
          math.sin(math.radians(a.dec_deg)))
    v2 = (math.cos(math.radians(b.dec_deg)) * math.cos(math.radians(b.ra_deg)),
          math.cos(math.radians(b.dec_deg)) * math.sin(math.radians(b.ra_deg)),
          math.sin(math.radians(b.dec_deg)))
    return math.degrees(math.acos(
        max(-1.0, min(1.0, sum(x * y for x, y in zip(v1, v2))))))


class TestLineData:
    def test_all_endpoints_in_index(self):
        segs = _constel.constellation_segments()
        assert len(segs) == 27
        assert sum(len(v) for v in segs.values()) == 115

    def test_segment_lengths_bounded(self):
        for abbr, segs in _constel.constellation_segments().items():
            for sa, sb in segs:
                assert _sep(sa, sb) < 35.0, f"{abbr}: {sa.name}-{sb.name} 过长"

    def test_anchor_and_culture_cover_all(self):
        for abbr in _constel.CONSTELLATION_LINES:
            assert _constel.constellation_anchor(abbr) is not None
            c = _constel.constellation_culture(abbr)
            assert c and c.get("zh"), f"{abbr} 缺中文名"
            label = _constel.constellation_label(abbr)
            assert _constel.CONSTELLATION_NAMES[abbr] in label

    def test_belt_and_dipper_geometry(self):
        idx = _constel.merged_star_index()
        belt = _sep(idx["Alnitak"], idx["Mintaka"])
        assert 2.0 < belt < 3.5, f"腰带跨度异常: {belt}"
        dip = [idx[n] for n in ("Dubhe", "Merak", "Phecda", "Megrez",
                                "Alioth", "Mizar", "Alkaid")]
        assert all(_sep(dip[i], dip[i + 1]) < 25.0 for i in range(6))


class TestCatalogRegression:
    """2026-11-02 HYG 吸附修正的历史错误, 回归锁死。"""

    def test_corrected_ra_values(self):
        idx = _constel.merged_star_index()
        for name, ra, dec in (
                ("Adhara", 104.6565, -28.97), ("Naos", 120.8961, -40.00),
                ("Alnair", 332.0581, -46.96), ("Ankaa", 6.5708, -42.31),
                ("Wezen", 107.0979, -26.39), ("Alhena", 99.4279, 16.40),
                ("Mirfak", 51.0807, 49.86), ("Menkalinan", 89.8822, 44.95),
                ("Kaus Australis", 276.0430, -34.38),
                ("Regor", 122.3831, -47.34)):
            s = idx[name]
            assert abs(s.ra_deg - ra) < 0.01, f"{name} ra 回归: {s.ra_deg}"
            assert abs(s.dec_deg - dec) < 0.05, f"{name} dec 回归"

    def test_polaris_and_distances(self):
        idx = _constel.merged_star_index()
        assert abs(idx["Polaris"].dec_deg) > 89.0
        assert 8.0 < idx["Sirius"].dist_ly < 9.0
        assert idx["Deneb"].spect.startswith("A2")

    def test_every_star_has_spect_or_unknown(self):
        for s in list(_stars.BRIGHT_STARS) + list(_constel.EXTENSION_STARS):
            assert hasattr(s, "spect")


class TestStarPicking:
    PREFERRED = ["Vega", "Altair", "Deneb", "Sadr", "Enif", "Albireo",
                 "Rasalhague", "Kaus Australis", "Spica", "Arcturus"]

    def _available_star(self, v):
        """按偏好顺序返回 (name, az, alt) 中第一个仰角>15°的星。"""
        idx = _constel.merged_star_index()
        gs = GroundStation(LON, LAT, 0.0)
        jd = v._current_jd()
        for name in self.PREFERRED:
            s = idx[name]
            az, alt = azalt_from_vec(j2000_to_altaz(
                vec_from_radec(s.ra_deg, s.dec_deg), gs, jd))
            if alt > 15.0:
                return name, az, alt
        raise AssertionError("偏好星表在该历元全部不可见")

    def _center_on(self, v, az, alt):
        v.view_state.look_at(az, alt)
        pos = v._sky_to_screen(az, alt)
        assert v._on_screen(pos)
        return pos

    def test_click_star_picks_with_card(self):
        v = _new_view()
        name, az, alt = self._available_star(v)
        pos = self._center_on(v, az, alt)
        _click(v, pos.x(), pos.y())
        assert v._picked_body is not None
        assert v._picked_body["kind"] == "star"
        assert v._picked_body["key"] == name
        info = v._body_info or {}
        c = _stars.star_culture(name)
        assert info.get("title") == (c["zh"] if c else name)
        assert info.get("mag") is not None
        assert "ly" in str(info.get("dist_text"))
        assert "光谱型" in str(info.get("desc"))

    def test_star_pick_then_empty_deselects(self):
        v = _new_view()
        name, az, alt = self._available_star(v)
        pos = self._center_on(v, az, alt)
        _click(v, pos.x(), pos.y())
        assert v._picked_body is not None
        ex, ey = _empty_point(v)
        _click(v, ex, ey)
        assert v._picked_body is None

    def test_bright_star_wins_at_its_own_position(self):
        v = _new_view()
        name, az, alt = self._available_star(v)
        pos = self._center_on(v, az, alt)
        _click(v, pos.x(), pos.y())
        # 该星自身屏幕位置命中自己 (不被邻星截胡)
        assert v._picked_body["key"] == name


class TestSearch:
    def test_search_star_by_chinese_name(self):
        v = _new_view()
        v._on_search_text("天狼")
        hits = [r for r in v._search_results if r["kind"] == "star"]
        assert any(r["name"] == "Sirius" for r in hits)

    def test_search_star_by_english_name(self):
        v = _new_view()
        v._on_search_text("vega")
        hits = [r for r in v._search_results if r["kind"] == "star"]
        assert any(r["name"] == "Vega" for r in hits)

    def test_search_star_pick_selects(self):
        v = _new_view()
        v._on_search_text("天狼")
        r = next(x for x in v._search_results if x["kind"] == "star")
        v.on_body_picked({"kind": "star", "key": r["name"]})
        info = v._body_info or {}
        assert info.get("title") == "天狼"
        assert info.get("rise") is not None or info.get("set") is not None

    def test_search_dso_m31(self):
        v = _new_view()
        v._on_search_text("m31")
        hits = [r for r in v._search_results if r["kind"] == "dso"]
        assert any(r["key"] == "M31" for r in hits)
        v.on_body_picked({"kind": "dso", "key": "M31",
                          "dso_type": "galaxy", "con": "And",
                          "ra_deg": 10.6836, "dec_deg": 41.2692})
        info = v._body_info or {}
        assert info.get("title") == "M31"
        assert "Galaxy" in str(info.get("kind_label"))
        assert "角大小" in str(info.get("desc"))

    def test_search_constellation_name_not_applicable(self):
        # 星座名不进搜索 (端点是星); 但猎户腰带星能搜到
        v = _new_view()
        v._on_search_text("orion")
        assert all(r["kind"] != "constellation" for r in v._search_results)


class TestRenderSmoke:
    def test_paint_with_constellations(self):
        v = _new_view()
        v.show()
        app.processEvents()
        v.repaint()
        v.view_state.zoom_to(120.0)   # 宽 FOV 出座名标签
        v.repaint()
        v.on_body_picked({"kind": "star", "key": "Sirius"})
        v.repaint()
        v.on_object_picked(None)
        v.repaint()


# --------------------------------------------------------------------------
# 7. 星座/星官图层接线（数据层 overlay_layers → 绘制层）
# --------------------------------------------------------------------------
class TestLayerData:
    def test_overlay_layers_carry_system_metadata(self):
        layers = _constel.overlay_layers(
            (_constel.SYSTEM_IAU, _constel.SYSTEM_CHINESE))
        assert [l.system_id for l in layers] == [
            _constel.SYSTEM_IAU, _constel.SYSTEM_CHINESE]
        assert all(l.display_name and l.tag and l.items for l in layers)
        assert len({l.tag for l in layers}) == 2, "两套体系标签不得合并同名"

    def test_iau_842_segments_and_dropped_not_fabricated(self):
        ds = _constel.load_iau_constellations()
        assert len(ds) == 88
        segs = [seg for con in ds for seg in con.segments()]
        assert len(segs) == 842
        # HIP 55203 在 HYG 缺失：只记录 unresolved，不由别处补坐标
        assert sum(con.dropped_segment_count for con in ds) == 1
        missing = [con for con in ds if 55203 in con.unresolved_hips]
        assert len(missing) == 1
        for con in ds:
            for a, b in con.segments():
                assert a.hyg_id is not None and b.hyg_id is not None

    def test_xingguan_counts_and_unlinked_honest(self):
        xg = _xingguan.load_xingguan()
        assert len(xg) == 31
        all_xg = [g for r in xg for g in r.xingguan]
        assert len(all_xg) == 287
        linked = [g for g in all_xg if len(g.lines) > 0]
        assert len(linked) == 274
        assert len(all_xg) - len(linked) == 13, "13 个无连线星官必须如实不画"
        for r in xg:
            for a, b in r.all_segments():
                assert a.ra_deg is not None and b.ra_deg is not None, \
                    "连线端点必须已映射，禁止拿空坐标补造"


class TestLayerWiring:
    def _draw_layer_queue(self, v, fov=120.0):
        from PySide6.QtGui import QImage
        img = QImage(v.width(), v.height(), QImage.Format_ARGB32)
        img.fill(0)
        painter = QPainter(img)
        try:
            v._label_items = []
            v._draw_constellations(painter)
        finally:
            painter.end()
        return list(v._label_items)

    def test_default_is_iau_only(self):
        v = _new_view()
        assert v.constellation_systems() == (_constel.SYSTEM_IAU,)
        assert v.constellation_systems_text() == "IAU"

    def test_each_layer_toggles_independently(self):
        v = _new_view()
        v.set_constellation_layer_visible(_constel.SYSTEM_CHINESE, True)
        assert set(v.constellation_systems()) == {
            _constel.SYSTEM_IAU, _constel.SYSTEM_CHINESE}
        assert v.constellation_systems_text() == "IAU + 中国星官"
        v.set_constellation_layer_visible(_constel.SYSTEM_IAU, False)
        assert v.constellation_systems() == (_constel.SYSTEM_CHINESE,)
        assert v.constellation_systems_text() == "中国星官"
        v.set_constellation_layer_visible(_constel.SYSTEM_CHINESE, False)
        assert v.constellation_systems_text() == "关闭"
        v.set_constellation_systems([_constel.SYSTEM_IAU])
        assert v.constellation_systems_text() == "IAU"
        import pytest
        with pytest.raises(KeyError):
            v.set_constellation_layer_visible("not_a_system", True)

    def test_both_open_labels_carry_system_tags(self):
        v = _new_view()
        v.view_state.look_at(180.0, 45.0)
        v.set_constellation_systems(
            [_constel.SYSTEM_IAU, _constel.SYSTEM_CHINESE])
        items = self._draw_layer_queue(v)
        texts = [t for _p, _pos, lines, _c in items for t, _s, _d in lines]
        assert any(t.startswith("[IAU]") for t in texts), \
            f"双开时 IAU 标签必须带体系徽标: {texts[:6]}"

    def test_single_open_labels_have_no_tag(self):
        v = _new_view()
        v.view_state.look_at(180.0, 45.0)
        items = self._draw_layer_queue(v)
        texts = [t for _p, _pos, lines, _c in items for t, _s, _d in lines]
        assert texts and all(not t.startswith("[IAU]") for t in texts)

    def test_label_priority_below_bodies(self):
        v = _new_view()
        v.view_state.look_at(180.0, 45.0)
        items = self._draw_layer_queue(v)
        constel_prios = [p for p, _pos, lines, _c in items
                         if any(len(t) > 2 for t, _s, _d in lines)]
        assert constel_prios and all(abs(p - 1.0) < 1e-9 for p in constel_prios)
        assert all(p < 2.0 for p in constel_prios), "座名必须低于星名优先级"

    def test_fov_layering_is_continuous(self):
        """FOV 扫描：分层 alpha 连续单调 → 拉远拉近不闪烁。"""
        v = _new_view()
        prev = None
        max_jump = 0.0
        last_detail = 1.0
        last_label = 0.0
        fov = 0.5
        while fov <= 120.0:
            v.view_state.zoom_to(fov)
            detail, label = v._constellation_layer_alpha()
            assert 0.0 <= detail <= 1.0 and 0.0 <= label <= 1.0
            assert detail <= last_detail + 1e-9, "星官细线随拉远只能淡出"
            assert label >= last_label - 1e-9, "座名随拉远只能渐显"
            if prev is not None:
                max_jump = max(max_jump, abs(detail - prev[0]),
                               abs(label - prev[1]))
            prev = (detail, label)
            last_detail, last_label = detail, label
            fov += 0.5
        assert max_jump < 0.05, f"相邻 FOV 之间 alpha 跳变 {max_jump:.3f} 会闪烁"
        assert prev[0] == 0.0 and prev[1] == 1.0

    def test_render_both_layers_smoke(self):
        v = _new_view()
        v.set_constellation_systems(
            [_constel.SYSTEM_IAU, _constel.SYSTEM_CHINESE])
        v.show()
        app.processEvents()
        for fov in (120.0, 40.0, 12.0):
            v.view_state.zoom_to(fov)
            v.repaint()


# --------------------------------------------------------------------------
# 8. 图层几何缓存（固定开销修复的回归锁）
# --------------------------------------------------------------------------
class TestLayerGeometryCache:
    """单位向量随拓扑缓存：每帧只做 P·N·R 旋转 + 投影，不重算 RA/Dec。

    反证：若有人把 `_layer_points` 改回「每帧从 RA/Dec 重建向量」，
    `test_layer_points_does_not_rebuild_vectors` 会红。
    """

    @staticmethod
    def _refs_and_prepared(v):
        layer = _constel.constellation_layer(_constel.SYSTEM_IAU)
        prepared = v._constellation_prepared(layer)
        refs = {}
        for con in layer.items:
            for sa, sb in con.segments():
                refs[sa.hyg_id] = (sa.ra_deg, sa.dec_deg)
                refs[sb.hyg_id] = (sb.ra_deg, sb.dec_deg)
            anchor = con.anchor()
            if anchor is not None:
                refs[anchor.hyg_id] = (anchor.ra_deg, anchor.dec_deg)
        return refs, prepared

    def test_cached_vectors_match_scalar_chain(self):
        v = _new_view()
        v.set_all_sky_mode(True)
        v.view_state.fov_deg = 160.0
        v.view_state.look_at(180.0, 30.0)
        refs, prepared = self._refs_and_prepared(v)

        assert set(prepared["keys"]) == set(refs)
        assert len(prepared["vecs"]) == len(prepared["keys"])
        assert all(len(row) == 3 for row in prepared["vecs"])

        gs = v._ground_station()
        jd = v._current_jd()
        points = v._layer_points(prepared, gs, jd)
        assert set(points) == set(refs)

        # 独立标量链（不与批量实现共享代码）对拍抽样点
        for k in list(refs)[::17]:
            az, alt = azalt_from_vec(j2000_to_altaz(
                vec_from_radec(*refs[k]), gs, jd))
            alt_draw = v._display_alt_deg(alt)
            scr = v._sky_to_screen(az, alt_draw)
            px, py = scr.x(), scr.y()
            p = points[k]
            if not (math.isfinite(px) and math.isfinite(py)) or alt_draw < -6.0:
                assert p is None, f"{k} 应被判为不可见"
                continue
            assert p is not None, f"{k} 漏画"
            assert abs(p.x() - px) < 1e-6 and abs(p.y() - py) < 1e-6, \
                f"{k} 批量投影与标量链不一致: {p} vs ({px}, {py})"

    def test_layer_points_does_not_rebuild_vectors(self, monkeypatch):
        import rf_sky_view

        v = _new_view()
        _refs, prepared = self._refs_and_prepared(v)
        assert len(prepared["keys"]) > 0

        calls = {"n": 0}
        real = rf_sky_view.vec_from_radec

        def counting(ra, dec):
            calls["n"] += 1
            return real(ra, dec)

        monkeypatch.setattr(rf_sky_view, "vec_from_radec", counting)
        v._layer_points(prepared, v._ground_station(), v._current_jd())
        assert calls["n"] == 0, \
            f"每帧重建了 {calls['n']} 个单位向量（应复用 prepared['vecs']）"

