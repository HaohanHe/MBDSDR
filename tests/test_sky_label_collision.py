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
import math
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
    LABEL_PRIORITY_CONSTELLATION,
    LABEL_PRIORITY_NORMAL_STAR,
    LABEL_PRIORITY_PROMINENT_STAR,
    LABEL_TIER_CAPS,
    RFSkyView,
    is_prominent_star,
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
    # P1 与亮星名共用：4→20，避免 5 条以上亮星名被当成卫星限额砍掉。
    assert LABEL_TIER_CAPS == (5, 20, 24, 12, 20)
    assert [label_tier(p) for p in (0.0, 0.5, 1.0, 1.5, 2.0, 3.5)] \
        == [0, 1, 2, 3, 4, 4]


# --------------------------------------------------------------------------
# 4b. 亮星判定与优先级：亮星名（0.5）必须压过星座名（1.0）
# --------------------------------------------------------------------------
def test_is_prominent_star_by_magnitude_or_proper_name():
    assert is_prominent_star(2.0, "") is True          # 门槛含等号
    assert is_prominent_star(2.05, "") is False        # 暗且无名
    assert is_prominent_star(5.1, "Vega") is True      # 暗但有专名
    assert is_prominent_star(5.1, "  ") is False       # 空白名不算专名
    assert is_prominent_star(float("nan"), "") is False
    assert is_prominent_star(float("nan"), "Vega") is True


def test_prominent_star_label_beats_constellation():
    # 框格式 (优先级, x0, y0, x1, y1)：心宿二（0.5）与暗星座名（1.0）全重叠。
    prominent = (LABEL_PRIORITY_PROMINENT_STAR, 0.0, 0.0, 60.0, 14.0)
    constellation = (LABEL_PRIORITY_CONSTELLATION, 0.0, 0.0, 120.0, 14.0)
    assert label_boxes_keep([constellation, prominent]) == [1]
    # 普通星名（2.0+vmag）仍然让位给星座名（保持旧行为）。
    normal = (LABEL_PRIORITY_NORMAL_STAR + 3.2, 0.0, 0.0, 40.0, 14.0)
    assert label_boxes_keep([normal, constellation]) == [1]


def test_prominent_stars_survive_tier_cap():
    # 13 条亮星名（南半球 120° B3 实测条数）必须全部留下。
    from PySide6.QtCore import QPointF
    items = [(LABEL_PRIORITY_PROMINENT_STAR, QPointF(float(i * 5), 0.0),
              [("星名", False, 0.0)], QColor(255, 255, 255))
             for i in range(13)]
    kept = label_tier_keep(items, 0.0, 0.0)
    assert len(kept) == 13


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
    for p, n in ((0.0, 10), (0.5, 25), (1.0, 30), (1.5, 13), (2.0, 21)):
        items.extend((p, QPointF(float(j), 0.0), [("x", False, 0.0)],
                      QColor(255, 255, 255)) for j in range(n))
    kept = label_tier_keep(items, 0.0, 0.0)
    assert len(kept) == 5 + 20 + 24 + 12 + 20


class _RecordingPainter:
    """假画家：不落像素，只记录 `_flush_labels` 真正画出的文本。"""

    def __init__(self):
        self.texts = []

    def save(self):
        pass

    def restore(self):
        pass

    def setRenderHint(self, *args, **kwargs):  # noqa: N802 (Qt 命名)
        pass

    def setPen(self, *args, **kwargs):  # noqa: N802
        pass

    def setFont(self, *args, **kwargs):  # noqa: N802
        pass

    def drawText(self, pos, text):  # noqa: N802
        self.texts.append(text)


def _ring_item(center, radius: float, prefix: str, index: int, deg: float):
    return (1.5, rf_sky_view.QPointF(
        center[0] + radius * math.cos(math.radians(deg)),
        center[1] - radius * math.sin(math.radians(deg))),
        [(f"{prefix}{index}", False, 0.0)], QColor(255, 255, 255))


def test_flush_labels_uses_tier_cap_before_collision():
    """限流必须先于碰撞剔除：远处标签不能先占像素位把近处名字挤没。

    行为断言（不读源码）：12 条近处标签（半径 100，档位 1.5 上限 12）+
    8 条远处标签（半径 112，各自与自己那条近处框压字），远处先入队。
    先限流 → 远处整批被档位上限丢弃，落笔恰好是 12 条近处文本；
    若顺序颠倒（先避让）→ 远处先占位，近处被挤掉，落笔文本集不同。
    用例同时自检两种顺序结果确实不同，避免输入退化成顺序无关。
    """
    v = _new_view(600)
    v.resize(600, 300)                     # 高度受控件最小尺寸约束，按实际取心
    cx, cy = v.width() / 2.0, v.height() / 2.0
    fonts = {False: v._font, True: v._gnss_label_font}

    angles_near = [i * 30.0 for i in range(12)]
    near = [_ring_item((cx, cy), 100.0, "N", i, deg)
            for i, deg in enumerate(angles_near)]
    far = [_ring_item((cx, cy), 112.0, "F", i, deg)
           for i, deg in enumerate(angles_near[:8])]
    items = far + near                     # 远处先入队（仅作干扰顺序）

    try:
        # 期望结果：先按档限流，再做二维避让。
        capped = label_tier_keep(items, cx, cy)
        capped_items = [items[i] for i in capped]
        kept = label_boxes_keep(_boxes(capped_items, fonts))
        expected = [capped_items[i][2][0][0] for i in kept]

        # 自检：顺序颠倒（先避让后限流）必须给出不同文本集。
        pre = label_boxes_keep(_boxes(items, fonts))
        pre_items = [items[i] for i in pre]
        capped2 = label_tier_keep(pre_items, cx, cy)
        alt_items = [pre_items[i] for i in capped2]
        alt = [alt_items[i][2][0][0]
               for i in label_boxes_keep(_boxes(alt_items, fonts))]
        assert set(alt) != set(expected), (
            "本用例对「限流/避让」顺序不敏感，无法抓住顺序颠倒")

        rec = _RecordingPainter()
        v._label_items = list(items)
        v._flush_labels(rec)
        assert rec.texts == expected, (
            "落笔文本与「先限流后避让」的结果不一致："
            f"实际 {rec.texts}，期望 {expected}")
    finally:
        v.close()


# --------------------------------------------------------------------------
# 4c. 120° 银心场（南半球）回归锁：P1=4 / 20 / 不限 的最终落笔不得压字
# --------------------------------------------------------------------------
SOUTH_UNIX = datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc).timestamp()
SOUTH_CENTER = (88.05, 65.80)   # Sgr A*


def _southern_galactic_view() -> RFSkyView:
    eng = get_time_engine()
    eng.set_unix(SOUTH_UNIX)
    eng.pause()
    v = RFSkyView()
    v.resize(700, 700)
    v.set_observer(-33.0, 151.0, 0.0)
    v.view_state.fov_deg = 120.0
    v.view_state.center_az = SOUTH_CENTER[0]
    v.view_state.center_alt = SOUTH_CENTER[1]
    v._sky_brightness = 0.0
    return v


def _strict_box_hits(boxes):
    """严格包围盒相交（不加 pad）的对数与涉及条数。"""
    pairs = 0
    involved = set()
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            if a[1] < b[3] and a[3] > b[1] and a[2] < b[4] and a[4] > b[2]:
                pairs += 1
                involved.update((i, j))
    return pairs, len(involved)


def _min_edge_gap(boxes):
    """最终包围盒边到边最小欧氏距离（与 `_flush_labels` 同口径）。"""
    best = float("inf")
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            dx = max(b[1] - a[3], a[1] - b[3])
            dy = max(b[2] - a[4], a[2] - b[4])
            if dx < 0 and dy < 0:
                continue
            best = min(best, math.hypot(max(dx, 0.0), max(dy, 0.0)))
    return best


def _landed(v, items, caps):
    kept = label_tier_keep(items, 350.0, 350.0, caps)
    boxes = _boxes([items[i] for i in kept], {False: v._font, True: v._gnss_label_font})
    return boxes, label_boxes_keep(boxes)


def test_southern_galactic_field_labels_never_overlap():
    """120° 银心场：P1=4 / 20 / 不限，最终落笔零相交且边距 ≥ pad。

    实测（2026-11-05，窗口 700×700、Bortle 3 档 `_sky_brightness=0.0`）：
    P1=4 → 26 条、边距 3.836 px；P1=20 → 30 条、边距 3.003 px；两版最终
    相交对数均 0。断言只锁**不变量**（零相交、边距 ≥ `pad=2.0`、放宽 P1
    只会多落不会少落），不锁条数——条数随星表/字体/平台会浮动；
    3.003 px 是观测值，2.0 px 才是 `label_boxes_keep(pad=2.0)` 给出的下界。
    """
    v = _southern_galactic_view()
    try:
        img = QImage(700, 700, QImage.Format_ARGB32)
        img.fill(QColor(0, 0, 0))
        painter = QPainter(img)
        try:
            v._label_items = []
            v._draw_planets(painter)
            v._draw_stars(painter)
            v._draw_constellations(painter)
            v._draw_dso(painter)
        finally:
            painter.end()
        items = list(v._label_items)
    finally:
        v.close()

    assert len(items) >= 40, f"银心宽场候选应 ≥40 条，实测 {len(items)}"

    caps4 = (5, 4, 24, 12, 20)
    caps20 = LABEL_TIER_CAPS
    caps_inf = (5, 10 ** 9, 24, 12, 20)
    landed = {}
    for name, caps in (("P1=4", caps4), ("P1=20", caps20), ("P1=inf", caps_inf)):
        boxes, final_idx = _landed(v, items, caps)
        final_boxes = [boxes[i] for i in final_idx]
        pairs, involved = _strict_box_hits(boxes)
        final_pairs, _ = _strict_box_hits(final_boxes)
        gap = _min_edge_gap(final_boxes)
        landed[name] = (len(boxes), len(final_boxes), final_pairs, gap)
        print(f"[{name}] 限流后 {len(boxes)} 条（剔除前相交 {pairs} 对 / {involved} 条）；"
              f"落笔 {len(final_boxes)} 条，最终相交 {final_pairs} 对，边距 {gap:.3f} px")
        assert final_pairs == 0, \
            f"{name} 最终仍有 {final_pairs} 对包围盒相交（压字）"
        assert gap >= 2.0, \
            f"{name} 最终包围盒边距 {gap:.3f} px < pad 2.0（放置函数漏了 pad？）"

    assert landed["P1=20"][1] > landed["P1=4"][1], \
        "P1 上限 4→20 必须多落标签，否则上限改动没有意义"
    assert landed["P1=inf"][1] == landed["P1=20"][1], \
        "本场 P1 候选 <20，'20' 与'不限'应等价（若不等说明候选数变了，重测文档）"


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
