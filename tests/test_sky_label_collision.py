# SPDX-License-Identifier: MIT
"""
tests/test_sky_label_collision.py
=================================
天空视图密集天区标签避让（星名 / 座名 / 行星名）。

覆盖：
  1. `label_boxes_keep` 纯函数：优先级高者先占位、边框 pad 生效、输入顺序无关；
  2. 真实帧队列：宽 FOV 下亮星名与座名同时在场，剔除确实发生但不过度剔除；
  3. 绘制路径：两条标签框重叠时，实际落笔只画优先级高的那条（像素级对拍）。

反证：把 `label_boxes_keep` 换回"全留"（旧实现无剔除）后，第 3 条的像素对比
必红——旧实现两条标签都画，叠加后与单标签渲染不等。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from datetime import datetime, timezone  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import rf_sky_view  # noqa: E402
from rf_sky_view import (  # noqa: E402
    LABEL_TIER_CAPS,
    RFSkyView,
    label_boxes_keep,
    label_tier,
    label_tier_keep,
)
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402

LAT, LON = 40.0, 116.0
UNIX = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc).timestamp()   # 夜侧
WIN = 500
BASE = QFont()
BASE.setPointSize(9)


def _new_view(size: int = WIN, fov: float = 120.0) -> RFSkyView:
    eng = get_time_engine()
    eng.set_unix(UNIX)
    eng.pause()
    v = RFSkyView()
    v.resize(size, size)
    v.set_observer(LAT, LON, 0.0)
    v.view_state.fov_deg = fov
    v._sky_brightness = 0.0
    return v


def _one(prio, x, y, text, color, small=False):
    """单行标签块：(优先级, 锚点, [(文本, 小字号, dy)], 颜色)。"""
    return (prio, rf_sky_view.QPointF(x, y), [(text, small, 0.0)],
            QColor(color))


def _boxes(items, fonts):
    """与 _flush_labels 同口径：块的外接框 = 各行并集。"""
    out = []
    for prio, pos, lines, _c in items:
        x0 = y0 = float("inf")
        x1 = y1 = float("-inf")
        for text, small, dy in lines:
            fm = QFontMetrics(fonts[small])
            x0 = min(x0, pos.x())
            x1 = max(x1, pos.x() + fm.horizontalAdvance(text))
            y0 = min(y0, pos.y() + dy - fm.ascent())
            y1 = max(y1, pos.y() + dy + fm.descent())
        out.append((prio, x0, y0, x1, y1))
    return out


# --------------------------------------------------------------------------
# 1. 纯函数
# --------------------------------------------------------------------------
def test_disjoint_boxes_all_kept():
    items = [(0.0, 0, 0, 20, 10), (1.0, 30, 0, 50, 10), (2.0, 0, 20, 20, 30)]
    assert label_boxes_keep(items) == [0, 1, 2]


def test_overlap_drops_lower_priority():
    items = [(2.0, 0, 0, 20, 10),       # 星名（低优先）
             (0.0, 10, 0, 30, 10)]      # 行星名（高优先，压过来）
    assert label_boxes_keep(items) == [1]


def test_overlap_order_independent():
    # 输入顺序反过来，仍是优先级高的那条留下
    items = [(0.0, 10, 0, 30, 10), (2.0, 0, 0, 20, 10)]
    assert label_boxes_keep(items) == [0]


def test_padding_counts_as_collision():
    # 左右各留 2px：相距 1px 也视为压字
    items = [(0.0, 0, 0, 20, 10), (1.0, 21, 0, 41, 10)]
    assert label_boxes_keep(items, pad=2.0) == [0]
    assert label_boxes_keep(items, pad=0.0) == [0, 1]


def test_vertical_overlap_also_collides():
    items = [(0.0, 0, 0, 20, 10), (1.0, 0, 9, 20, 19)]
    assert label_boxes_keep(items) == [0]


# --------------------------------------------------------------------------
# 2. 真实帧队列：宽 FOV 下确实有星名与座名，且剔除生效但不过度
# --------------------------------------------------------------------------
def test_real_sky_labels_are_culled_but_not_wiped():
    v = _new_view()
    try:
        img = QImage(WIN, WIN, QImage.Format_ARGB32)
        img.fill(QColor(0, 0, 0))
        painter = QPainter(img)
        try:
            v._label_items = []
            v._draw_stars(painter)
            v._draw_constellations(painter)
        finally:
            painter.end()
        items = list(v._label_items)
    finally:
        v.close()

    assert len(items) >= 10, f"宽 FOV 下应有星名+座名标签，实测 {len(items)} 条"
    kinds = {small for _p, _pos, lines, _c in items
             for _t, small, _dy in lines}
    assert kinds == {False, True}, "星名与座名两类都应登记（否则取样天区无效）"

    fonts = {False: v._font, True: v._gnss_label_font}
    keep_all = len(items)
    kept = label_boxes_keep(_boxes(items, fonts))
    assert len(kept) < keep_all, \
        f"密集星空旧实现全画（{keep_all} 条）应出现压字被剔除"
    assert len(kept) >= 1, "不能把标签全灭（剔除过头也是坏）"


# --------------------------------------------------------------------------
# 3. 绘制路径像素级对拍：重叠只留高优先级
# --------------------------------------------------------------------------
def _render_flush(v, items, size=(120, 60)) -> QImage:
    img = QImage(size[0], size[1], QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0))
    painter = QPainter(img)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    try:
        v._label_items = list(items)
        v._flush_labels(painter)
    finally:
        painter.end()
    return img


def test_flush_paints_only_higher_priority_of_overlapping_pair():
    v = _new_view()
    try:
        planet = _one(0.0, 6, 20, "金星", "#FFFFFF")
        star = _one(2.3, 10, 20, "织女一", "#C8D2E6")
        both = _render_flush(v, [planet, star])
        alone = _render_flush(v, [planet])
        disjoint = _render_flush(v, [planet,
                                     _one(2.3, 40, 40, "织女一", "#C8D2E6")])
    finally:
        v.close()

    assert both == alone, "两条标签框重叠时只能画优先级高的那条（否则压字）"
    assert disjoint != alone, "不重叠的标签必须都画（剔除不能误伤）"


# --------------------------------------------------------------------------
# 4. 每档上限 + 丢弃规则：远处先丢，不是按队列/目录顺序随机截断
# --------------------------------------------------------------------------
def test_tier_caps_are_the_agreed_numbers():
    assert LABEL_TIER_CAPS == (5, 4, 24, 12, 20)
    assert [label_tier(p) for p in (0.0, 0.5, 1.0, 1.5, 2.0, 3.5)] \
        == [0, 1, 2, 3, 4, 4]


def test_constellation_cap_drops_farthest_not_queue_order():
    from PySide6.QtCore import QPointF
    # 30 条座名，x 距离 = (i*7)%30（0..29 各一次），中心在 (0,0)。
    items = [(1.0, QPointF(float((i * 7) % 30), 0.0),
              [("座名", False, 0.0)], QColor(255, 255, 255))
             for i in range(30)]
    kept = label_tier_keep(items, 0.0, 0.0)
    assert len(kept) == 24
    kept_dist = sorted(abs(float(items[i][1].x())) for i in kept)
    assert kept_dist == [float(d) for d in range(24)], \
        "必须保留离视场中心最近的 24 条"
    dropped = sorted(i for i in range(30) if i not in kept)
    assert all(abs(float(items[i][1].x())) >= 24.0 for i in dropped)


def test_tier_caps_apply_per_tier_independently():
    from PySide6.QtCore import QPointF
    items = []
    for p, n in ((0.0, 10), (0.5, 6), (1.0, 30), (1.5, 13), (2.0, 21)):
        items.extend((p, QPointF(float(j), 0.0), [("x", False, 0.0)],
                      QColor(255, 255, 255)) for j in range(n))
    kept = label_tier_keep(items, 0.0, 0.0)
    assert len(kept) == 5 + 4 + 24 + 12 + 20


def test_flush_labels_uses_tier_cap_before_collision():
    import inspect
    src = inspect.getsource(RFSkyView._flush_labels)
    assert "label_tier_keep(" in src
    # 限流必须先于 label_boxes_keep，否则远处标签先占像素位
    assert src.index("label_tier_keep(") < src.index("label_boxes_keep(")


# --------------------------------------------------------------------------
# 4. 卫星三行标签块：整块同进同退（不会被各自拆掉）
# --------------------------------------------------------------------------
def _sat_block(prio, x, y):
    """模拟 _draw_objects 的卫星块：名称 + 仰角方位 + 频率。"""
    return (prio, rf_sky_view.QPointF(x, y),
            [("ISS (ZARYA)", False, -5.0),
             ("EL 31.7 AZ 204.0", True, 22.0),
             ("145.8 MHz", True, 36.0)],
            QColor("#F5F3EF"))


def test_satellite_block_survives_overlap_with_star_label():
    v = _new_view()
    try:
        block = _sat_block(0.5, 6, 8)
        star = _one(2.3, 8, 30, "织女一", "#C8D2E6")   # 压在卫星块第 2 行上
        both = _render_flush(v, [block, star])
        block_only = _render_flush(v, [block])
    finally:
        v.close()

    assert both == block_only, "卫星块 0.5 优先级应先占位，星名让位"
    # 块内三行都必须落笔：只画名称/只画频率都会让像素数明显变少
    ink_all = _ink(block_only)
    ink_line1 = _ink(_render_flush(v, [(0.5, rf_sky_view.QPointF(6, 8),
                                       [block[2][0]], block[3])]))
    assert ink_all > ink_line1 * 1.6, \
        f"三行块应整块画出（ink {ink_all} vs 仅首行 {ink_line1}）"


def test_star_label_next_to_block_is_kept():
    """块外（右侧/下方）的星名不被块误伤。"""
    v = _new_view()
    try:
        block = _sat_block(0.5, 6, 8)
        star = _one(2.3, 6, 95, "织女一", "#C8D2E6")   # 块外下方
        both = _render_flush(v, [block, star], size=(120, 140))
        block_only = _render_flush(v, [block], size=(120, 140))
    finally:
        v.close()
    assert both != block_only, "不与块相交的星名必须照样画"


def _ink(img) -> int:
    n = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.red() + c.green() + c.blue() > 90:
                n += 1
    return n


@pytest.mark.parametrize("prio", [0.0, 0.5, 1.0, 2.0])
def test_flush_keeps_single_label(prio):
    v = _new_view()
    try:
        img = _render_flush(v, [_one(prio, 6, 20, "测试", "#FFFFFF")])
    finally:
        v.close()
    assert _ink(img) > 0, "单标签必须真的落笔（基础路径不能空）"
