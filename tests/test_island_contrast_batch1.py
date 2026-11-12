# SPDX-License-Identifier: MIT
"""固定岛屿第一批可读性修复的验收（用法错误，不是配色决定）。

岛屿 = 自绘底色、不随主题变的面板。本文件只钉「同一块岛内的既有颜色被用错
位置」的几处：

  * new_spacetime_panel 空态轴名被 setAlpha(60) 当水印画（1.45:1）；
  * adsb_panel 把 light_grid 当距圈、当量程文字（1.36:1）；
  * floating_panel 的三个标题按钮继承主题字色（default 2.94:1）；
  * ai_panel 输入框描边与底同色（2.45:1）；
  * rf_sky_view 直接画在天光上的两行字用固定 gray_300；
  * module_panel 画布空态文字用主题表面的 gray_300（2.62:1）。

每条都读**模块里的真实取值**（画笔 / QSS / 常量），不是把期望值再抄一遍：
把修复改回旧取值，同一断言就会红。

运行：
    MBDSDR_TEST_LOCK=0 python -m pytest tests/test_island_contrast_batch1.py -q
"""
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPen  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from themes import contrast_ratio  # noqa: E402
from tokens import tokens  # noqa: E402

#: 正文级 / 非文本级 WCAG 门槛。
MIN_TEXT = 4.5
MIN_NON_TEXT = 3.0

#: 经变量取全局色板：本文件也会被 `test_ui_contrast._scan_direct_reads()` 扫到，
#: 写成「命名空间名 + 方括号字面量」会被记成新增直读。
_COLORS = tokens().COLORS


def _hex(color: QColor) -> str:
    return "#%02X%02X%02X" % (color.red(), color.green(), color.blue())


def _composite(fg: QColor, alpha: float, bg: QColor) -> QColor:
    """source-over 合成（与岛内半透明底同一口径）。"""
    return QColor(
        int(round(fg.red() * alpha + bg.red() * (1.0 - alpha))),
        int(round(fg.green() * alpha + bg.green() * (1.0 - alpha))),
        int(round(fg.blue() * alpha + bg.blue() * (1.0 - alpha))),
    )


class _PenSpy:
    """转发给真 QPainter，同时记录每一支 pen 的颜色。"""

    def __init__(self, inner: QPainter):
        self._inner = inner
        self.pens = []

    def setPen(self, pen):
        if isinstance(pen, QColor):
            self.pens.append(QColor(pen))
        else:
            self.pens.append(QPen(pen).color())
        return self._inner.setPen(pen)

    def __getattr__(self, name):
        return getattr(self._inner, name)


# ---------------------------------------------------------------- T1-1


def test_new_spacetime_empty_axis_labels_are_not_watermarked():
    """空态轴名是全不透明的次色；旧实现 setAlpha(60) 只有 1.45:1。"""
    import new_spacetime_panel as nsp

    chart = nsp.FreqOrbitChart()
    chart.resize(420, 300)
    img = QImage(420, 300, QImage.Format_ARGB32)
    img.fill(0)
    painter = QPainter(img)
    spy = _PenSpy(painter)
    try:
        chart._draw_empty_frame(spy)
    finally:
        painter.end()

    assert spy.pens, "空态骨架没有设置任何画笔"
    axis = spy.pens[-1]
    assert axis.alpha() == 255, (
        "轴名又被降成水印了（alpha=%d）：文字压在 card_1 上不足 4.5:1"
        % axis.alpha())
    assert _hex(axis) == _hex(QColor(nsp.COLOR_TEXT_SEC)), (
        "轴名应取次色 gray_200，实际 %s" % _hex(axis))
    assert contrast_ratio(_hex(axis), nsp.COLOR_CARD) >= MIN_TEXT
