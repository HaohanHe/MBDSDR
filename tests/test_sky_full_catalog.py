# SPDX-License-Identifier: MIT
"""
tests/test_sky_full_catalog.py
==============================
天空视图接入全天星表（HYG v4.1, V<=9）后的行为契约。

覆盖：
  1. 宽视场用 `star_catalog.select_for_view` 的全天星表，不再退回 141 颗
     亮星子集；拉远 120° 能看到上千颗真实暗星；
  2. 视场驱动：同一 az/alt 天区，FOV 缩小后集合变大，且新增的每一颗都
     比宽视场极限更暗（不是随机丢/随机补）；
  3. 绘制与拾取同源：点击当帧画出的星命中同一 HYG id；把当帧集合换成
     只含亮星的子集后，同一位置不再命中那颗暗星（看不见就不能点中）；
  4. 截断如实：`truncated` 时状态卡写「已按上限显示 N / 可用 M」，
     正常时写「显示 N 颗 · 极限 Vx.x」，白天/失败各自明说；
  5. 标签密度随视场收放，且只给具名星（无名星不编造名字）。

反证：
  - 旧实现只画 141 颗亮星：用例 1 的 `len(sel) > 141`、用例 2 的增量必红；
  - 若拾取从 `merged_star_index` 另取一套坐标：用例 3 的 id/位置对不上；
  - 若忽略 `truncated`：用例 4 的文案与像素差异必红。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import math  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import rf_sky_view  # noqa: E402
from rf_sky_view import (  # noqa: E402
    RFSkyView, _star_label_limit_mag, star_catalog_status_text,
)
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402

LAT, LON = 40.0, 116.0
UNIX = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc).timestamp()   # 夜侧
WIN = 700
CENTER_AZ, CENTER_ALT = 180.0, 45.0


def _new_view(size: int = WIN) -> RFSkyView:
    eng = get_time_engine()
    eng.set_unix(UNIX)
    eng.pause()
    v = RFSkyView()
    v.resize(size, size)
    v.set_observer(LAT, LON, 0.0)
    v.view_state.fov_deg = 120.0
    v._bortle = 3
    v._sky_brightness = 0.0
    v.view_state.look_at(CENTER_AZ, CENTER_ALT)
    return v


def _selection(v: RFSkyView, fov: float,
               az: float = CENTER_AZ, alt: float = CENTER_ALT):
    v.view_state.fov_deg = fov
    v.view_state.look_at(az, alt)
    gs = v._ground_station()
    assert gs is not None
    return v._star_selection(gs, v._current_jd())


def _render_stars(v: RFSkyView) -> QImage:
    img = QImage(WIN, WIN, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    try:
        v._label_items = []
        v._draw_stars(p)
        return img
    finally:
        p.end()


def _ang_sep_deg(az1, alt1, az2, alt2) -> float:
    a1, h1 = math.radians(az1), math.radians(alt1)
    a2, h2 = math.radians(az2), math.radians(alt2)
    c = (math.sin(h1) * math.sin(h2)
         + math.cos(h1) * math.cos(h2) * math.cos(a1 - a2))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def _patch_ids(sel, radius_deg: float) -> dict:
    out = {}
    for i in range(len(sel)):
        if _ang_sep_deg(float(sel.az_deg[i]), float(sel.alt_deg[i]),
                        CENTER_AZ, CENTER_ALT) <= radius_deg:
            out[int(sel.hyg_id[i])] = float(sel.eff_mag[i])
    return out


def _screen_of(v, az, alt):
    return v._sky_to_screen(float(az), v._display_alt_deg(float(alt)))


def _pick(v, pos):
    return v._find_body_at(float(pos.x()), float(pos.y()))


# --------------------------------------------------------------------------
# 1. 宽视场：全天星表，不是 141 颗子集
# --------------------------------------------------------------------------
def test_wide_fov_draws_thousands_of_real_catalog_stars():
    v = _new_view()
    try:
        sel = _selection(v, 120.0)
        assert sel is not None
        assert len(sel) > 141, "仍在用 141 颗亮星子集"
        assert len(sel) >= 1000, f"120° 视场只取到 {len(sel)} 颗"
        assert not sel.truncated, "默认 20000 上限下不应截断"
        assert sel.limit_mag == pytest.approx(6.5)     # Bortle 3 裸眼基线
    finally:
        v.close()


def test_batch_projection_matches_scalar_projection():
    """批量投影与逐点 _sky_to_screen 必须逐星一致（不是第二套几何）。"""
    v = _new_view()
    try:
        sel = _selection(v, 120.0)
        alt_draw = [v._display_alt_deg(float(a)) for a in sel.alt_deg]
        px, py = v._sky_to_screen_many(sel.az_deg, alt_draw)
        step = max(1, len(sel) // 40)
        for i in range(0, len(sel), step):
            p = v._sky_to_screen(float(sel.az_deg[i]), alt_draw[i])
            assert abs(float(px[i]) - p.x()) < 1e-6
            assert abs(float(py[i]) - p.y()) < 1e-6
    finally:
        v.close()


# --------------------------------------------------------------------------
# 2. 视场驱动：放大后同一天区必须多出更暗的星
# --------------------------------------------------------------------------
def test_zoom_in_same_patch_adds_only_fainter_stars():
    v = _new_view()
    try:
        wide = _selection(v, 120.0)
        narrow = _selection(v, 5.0)
        assert wide is not None and narrow is not None
        assert narrow.limit_mag > wide.limit_mag     # LOD 深度增益生效
        patch = 3.0                                  # 两次取数都完整覆盖的中心天区
        wide_ids = _patch_ids(wide, patch)
        narrow_ids = _patch_ids(narrow, patch)
        assert len(narrow_ids) > len(wide_ids), (
            f"放大后同一天区星数未增加: {len(wide_ids)} -> {len(narrow_ids)}")
        new_ids = set(narrow_ids) - set(wide_ids)
        assert new_ids, "放大后没有任何新增星"
        # 新增的每一颗都必须暗于宽视场极限（按消光后的有效星等比较）
        for hid in new_ids:
            assert narrow_ids[hid] > wide.limit_mag + 1e-9, (
                f"HYG {hid} 有效星等 {narrow_ids[hid]:.2f} 未暗于宽视场极限 "
                f"{wide.limit_mag:.2f}")
        # 宽视场集合里的星在放大后必须仍然在（不许随机丢）
        assert set(wide_ids) <= set(narrow_ids)
        # 新增星必须真的被画出来（不是只在取数里多、绘制被裸眼基线砍掉）
        for hid in list(new_ids)[:20]:
            assert v._star_visual(narrow_ids[hid], narrow.limit_mag,
                                  v._bortle)[0] > 0.0, (
                f"HYG {hid} 已被 LOD 选出但视觉模型仍返回不可见")
            assert v._star_visual(narrow_ids[hid], wide.limit_mag,
                                  v._bortle)[0] == 0.0, (
                f"HYG {hid} 在宽视场极限下本不应可见（阈值被串了）")
    finally:
        v.close()


# --------------------------------------------------------------------------
# 3. 绘制与拾取同源：当帧集合决定可点集合
# --------------------------------------------------------------------------
def test_pick_hits_only_stars_drawn_this_frame():
    v = _new_view()
    try:
        sel = _selection(v, 120.0)
        _render_stars(v)                 # 绘制后缓存当帧集合并登记标签
        assert v._star_sel is not None
        # 从当帧集合里挑一颗“看得见、附近没有更亮星”的暗星
        target = None
        for i in range(len(sel)):
            az, alt = float(sel.az_deg[i]), float(sel.alt_deg[i])
            if float(sel.eff_mag[i]) <= 3.5:
                continue
            pos = _screen_of(v, az, alt)
            if not v._on_screen(pos):
                continue
            body = _pick(v, pos)
            if not body or body.get("kind") != "star":
                continue
            if int(body.get("hyg_id", -1)) != int(sel.hyg_id[i]):
                continue
            target = (int(sel.hyg_id[i]), pos)
            break
        assert target is not None, "当帧画出的暗星应能点中且 id 一致"
        target_id, pos = target

        # 反证 1：同一位置、当帧集换成只含亮星的子集 -> 这颗暗星不可点
        full_again = _selection(v, 120.0)
        # 用同一取数函数显式收紧极限，模拟“另一帧只画了亮星”
        from mbdsdr_ai.skyengine.star_catalog import select_for_view
        gs = v._ground_station()
        bright_sel = select_for_view(
            gs, v._current_jd(), CENTER_AZ, CENTER_ALT, 120.0,
            bortle=int(v._bortle), limit_mag=3.5, max_count=v.STAR_MAX_DRAW)
        assert target_id not in {int(x) for x in bright_sel.hyg_id}
        v._star_sel = bright_sel
        body_absent = _pick(v, pos)
        assert (not body_absent
                or body_absent.get("kind") != "star"
                or int(body_absent.get("hyg_id", -1)) != target_id), \
            "当前帧未画的星仍可点中（看不见却能点中）"

        # 反证 2：恢复当帧集合后同一位置又能点中 -> 拾取确实读的是当帧集合
        v._star_sel = full_again
        # full_again 是完整集合（limit 6.5），target 在其内
        assert target_id in {int(x) for x in full_again.hyg_id}
        body_back = _pick(v, pos)
        assert body_back is not None and body_back.get("kind") == "star"
        assert int(body_back.get("hyg_id", -1)) == target_id
    finally:
        v.close()


def test_pick_uses_apparent_altitude_not_geometric():
    """低空星：点视高度位置能中，点几何高度位置（折射差 > 容差）点不中。"""
    v = _new_view()
    try:
        # 窄视场放大折射差；中心放到低空，取一颗 geom alt 较低的星
        sel = _selection(v, 5.0, az=270.0, alt=5.0)
        assert sel is not None
        v._star_sel = sel
        cand = None
        for i in range(len(sel)):
            alt = float(sel.alt_deg[i])
            if alt < 2.0 or alt > 7.0:
                continue
            az = float(sel.az_deg[i])
            apparent = _screen_of(v, az, alt)
            geometric = v._sky_to_screen(az, alt)
            d_px = math.hypot(apparent.x() - geometric.x(),
                              apparent.y() - geometric.y())
            if d_px < 15.0:
                continue                 # 折射差太小，容差内分辨不出
            if not v._on_screen(apparent):
                continue
            body_app = _pick(v, apparent)
            if not body_app or body_app.get("kind") != "star":
                continue
            if int(body_app.get("hyg_id", -1)) != int(sel.hyg_id[i]):
                continue
            cand = (apparent, geometric, d_px,
                    int(body_app.get("hyg_id", -1)))
            break
        if cand is None:
            pytest.skip("该时刻/视场没有合适的低空星（不编造样本）")
        apparent, geometric, d_px, tid = cand
        # 视高度位置命中同一颗星；几何高度位置（低 d_px 之外）不得命中它
        body_geom = _pick(v, geometric)
        assert (not body_geom or body_geom.get("kind") != "star"
                or int(body_geom.get("hyg_id", -1)) != tid), (
            f"几何高度位置也能点中同一颗星（{d_px:.1f}px 偏差），"
            f"说明拾取没有走视高度")
    finally:
        v.close()


# --------------------------------------------------------------------------
# 4. 截断与状态卡如实说明
# --------------------------------------------------------------------------
def test_status_text_is_honest_for_truncation_and_empty_states():
    fake_trunc = SimpleNamespace(returned=20000, available=83479,
                                 limit_mag=9.0, truncated=True)
    fake_norm = SimpleNamespace(returned=1232, available=1232,
                                limit_mag=6.5, truncated=False)
    assert star_catalog_status_text(fake_trunc) == "已按上限显示 20000 / 可用 83479"
    assert star_catalog_status_text(fake_norm) == "显示 1232 颗 · 极限 V6.5"
    assert star_catalog_status_text(None) == "—"
    assert star_catalog_status_text(None, daylight_hidden=True) == "白天·不显示"
    assert star_catalog_status_text(None, error="星表不可用") == "星表不可用"


def test_status_text_says_catalog_limit_not_silent_cut():
    """请求深度超过 V9.0 时如实写「受目录上限」，不静默截断。"""
    fake_cap = SimpleNamespace(returned=1519, available=1519,
                               limit_mag=9.0, truncated=False,
                               beyond_catalog_limit=True)
    txt = star_catalog_status_text(fake_cap)
    assert txt == "显示 1519 颗 · 受目录上限 V9.0"
    assert "受目录上限" in txt


def test_max_count_truncates_but_keeps_brightest():
    v = _new_view()
    try:
        full = _selection(v, 120.0)
        assert len(full) > 100
        v.STAR_MAX_DRAW = 100        # 实例级收紧预算（不动类常量）
        cut = _selection(v, 120.0)
        assert cut is not None
        assert cut.truncated and cut.available > 100
        assert len(cut) == 100
        # 截断保留最亮：返回集合的最大有效星等 <= 全集第 100 亮者
        full_sorted = sorted(float(x) for x in full.eff_mag)
        assert max(float(x) for x in cut.eff_mag) <= full_sorted[99] + 1e-9
        txt = star_catalog_status_text(cut)
        assert txt.startswith("已按上限显示 100 / 可用 ")
    finally:
        v.close()


def _card_crop(v: RFSkyView, dy: int, h: int = 20) -> QImage:
    img = QImage(v.width(), v.height(), QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0))
    p = QPainter(img)
    try:
        v._draw_info_overlay(p)
    finally:
        p.end()
    card_y = v.height() - RFSkyView.INFO_CARD_H - 12
    return img.copy(12 + 52, card_y + dy, 176, h)


def _diff_pixels(a, b) -> int:
    n = 0
    for y in range(a.height()):
        for x in range(a.width()):
            if a.pixelColor(x, y) != b.pixelColor(x, y):
                n += 1
    return n


def test_status_card_draws_star_row_and_truncation_warning():
    v = _new_view()
    try:
        # 星表行位于银河之后、提示行之前 → 基线 220；批 1 在它之前新增
        # 「恒星时 / 视轴」两行 → 下移 36px（dy 210→246）。
        v._star_sel = SimpleNamespace(returned=1232, available=1232,
                                      limit_mag=6.5, truncated=False)
        normal = _card_crop(v, 246)
        v._star_sel = SimpleNamespace(returned=20000, available=83479,
                                      limit_mag=9.0, truncated=True)
        truncated = _card_crop(v, 246)
    finally:
        v.close()
    assert _diff_pixels(normal, truncated) > 20, \
        "截断态与正常态的状态卡星表行必须不同（不许静默少画）"


# --------------------------------------------------------------------------
# 5. 标签密度：只给具名星，随视场收放
# --------------------------------------------------------------------------
def test_label_threshold_shrinks_when_zooming_out():
    assert _star_label_limit_mag(120.0) == 2.0
    assert _star_label_limit_mag(30.0) == 3.0
    assert _star_label_limit_mag(5.0) == 4.0
    assert _star_label_limit_mag(1.0) == 5.5


def test_wide_fov_labels_only_named_bright_stars():
    v = _new_view()
    try:
        sel = _selection(v, 120.0)
        _render_stars(v)
        labels = list(v._label_items)
        assert labels, "宽视场至少要给最亮的具名星标签"
        assert len(labels) < len(sel), "标签数不能等于星数（会糊满屏）"
        named_bright = {
            int(sel.hyg_id[i]) for i in range(len(sel))
            if str(sel.name[i] or "") and float(sel.vmag[i]) <= 2.0
        }
        # 标签来自当帧集里 V<=2 的具名星（无名星不编名字）
        assert len(labels) <= max(1, len(named_bright))
    finally:
        v.close()
