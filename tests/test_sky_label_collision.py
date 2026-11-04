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
from rf_sky_view import RFSkyView, label_boxes_keep  # noqa: E402
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


def _boxes(items, font: QFont):
    fm = QFontMetrics(font)
    return [(prio, pos.x(), pos.y() - fm.ascent(),
             pos.x() + fm.horizontalAdvance(text), pos.y() + fm.descent())
            for prio, pos, text, _c, _s in items]


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
    kinds = {small for small in (it[4] for it in items)}
    assert kinds == {False, True}, "星名与座名两类都应登记（否则取样天区无效）"

    font = v._font
    fm = QFontMetrics(font)
    keep_all = len(items)
    kept = label_boxes_keep(_boxes(items, font))
    # 座名用小字号，但了解框宽差异足以判断同胞重叠；这里只要求整体剔除生效
    assert len(kept) < keep_all, \
        f"密集星空旧实现全画（{keep_all} 条）应出现压字被剔除"
    assert len(kept) >= 1, "不能把标签全灭（剔除过头也是坏）"


# --------------------------------------------------------------------------
# 3. 绘制路径像素级对拍：重叠只留高优先级
# --------------------------------------------------------------------------
def _render_flush(v, items) -> QImage:
    img = QImage(120, 60, QImage.Format_ARGB32)
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
        planet = (0.0, rf_sky_view.QPointF(6, 20), "金星",
                  QColor(255, 255, 255), False)
        star = (2.3, rf_sky_view.QPointF(10, 20), "织女一",
                QColor(200, 210, 230), False)
        both = _render_flush(v, [planet, star])
        alone = _render_flush(v, [planet])
        disjoint = _render_flush(v, [planet, (2.3, rf_sky_view.QPointF(40, 40),
                                            "织女一",
                                            QColor(200, 210, 230), False)])
    finally:
        v.close()

    assert both == alone, "两条标签框重叠时只能画优先级高的那条（否则压字）"
    assert disjoint != alone, "不重叠的标签必须都画（剔除不能误伤）"


@pytest.mark.parametrize("prio", [0.0, 1.0, 2.0])
def test_flush_keeps_single_label(prio):
    v = _new_view()
    try:
        img = _render_flush(v, [(prio, rf_sky_view.QPointF(6, 20), "测试",
                                 QColor(255, 255, 255), False)])
    finally:
        v.close()
    ink = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.red() + c.green() + c.blue() > 90:
                ink += 1
    assert ink > 0, "单标签必须真的落笔（基础路径不能空）"
