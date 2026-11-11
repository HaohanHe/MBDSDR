# SPDX-License-Identifier: MIT
"""
tests/test_spectrum_slider_overflow.py
======================================
右侧竖滑杆列「fixed 最小高把面板顶出可视区」的回归钉子（2026-11-11）。

实测（Windows / dark_car）：
  - 旧：三根 `setMinimumHeight(90/80/80)` → 列最小高 314、面板最小高 347；
        请求 303 时 Qt 拒绝缩小，父容器裁掉末根 `_db_min_slider` 下半段 44 px。
  - 新：统一 `_SLIDER_MIN_H=50` → 列表最小高 214、面板最小高 247；
        请求 303 精确命中，三根滑杆完整可见。

守卫：
1. 303 档下面板实际高必须 == 303（不再被最小高顶回 347）。
2. 三根滑杆底边必须落在滑杆列/面板内（不越界）。
3. 把最小高改回 90/80/80 必须复现 30–60 px 的溢出（防止有人悄悄改回去）。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_spectrum_slider_overflow.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import spectrum_widget  # noqa: E402
from spectrum_widget import SpectrumPanel  # noqa: E402

_OLD_MINIMA = (90, 80, 80)
_SLIDERS = ("_span_slider", "_db_max_slider", "_db_min_slider")


def _force_layout(panel: SpectrumPanel) -> None:
    """让 Qt 重新计算最小尺寸缓存（只 resize 不会刷新 minimumSizeHint）。"""
    lay = panel.layout()
    if lay is not None:
        lay.invalidate()
        lay.activate()
    app.processEvents()


def _bottom_in(widget, anc) -> int:
    """widget 底边在 anc 坐标系里的 y（含）。"""
    return widget.mapTo(anc, QPoint(0, widget.height())).y()


def _make_panel(width=331, height=303) -> SpectrumPanel:
    panel = SpectrumPanel()
    panel.resize(width, height)
    panel.show()
    _force_layout(panel)
    app.processEvents()
    return panel


class TestSliderColumnFits303:
    def test_all_sliders_use_50px_floor(self):
        panel = _make_panel()
        try:
            assert spectrum_widget._SLIDER_MIN_H == 50
            for name in _SLIDERS:
                assert getattr(panel, name).minimumHeight() == 50, name
        finally:
            panel.deleteLater()

    def test_panel_accepts_303_and_nothing_is_clipped(self):
        panel = _make_panel()
        try:
            assert panel.height() == 303, (
                f"面板最小高仍把 303 顶回 {panel.height()}，说明又被 fixed 尺寸撑住")
            col = panel._db_slider_col
            assert _bottom_in(col, panel) <= panel.height()
            for name in _SLIDERS:
                slider = getattr(panel, name)
                assert _bottom_in(slider, col) <= col.height(), name
                assert slider.height() >= 50, f"{name} 被压到 {slider.height()}"
        finally:
            panel.deleteLater()

    def test_min_hint_dropped_below_303(self):
        panel = _make_panel()
        try:
            hint = panel.minimumSizeHint().height()
            assert hint <= 303, f"面板最小高 {hint} 仍高于 303 档"
        finally:
            panel.deleteLater()


class TestOldFixedValuesRegress:
    def test_restoring_90_80_80_reproduces_overflow(self):
        panel = _make_panel()
        try:
            for name, old in zip(_SLIDERS, _OLD_MINIMA):
                getattr(panel, name).setMinimumHeight(old)
            _force_layout(panel)
            panel.resize(331, 303)
            app.processEvents()
            _force_layout(panel)

            assert panel.height() > 303, "旧 fixed 值应把面板顶高"
            overflow = panel.height() - 303
            col = panel._db_slider_col
            # 越界量 == 列最小高 − 303 档可用 body 高。
            # Windows/dark_car 实测 44 px（列 314 vs body 270）；offscreen 字体
            # 度量为 0，标签更矮，同一回归只剩 14 px。关键是「>0 且可复现」。
            col_overflow = col.minimumSizeHint().height() - (303 - col.y())
            assert col_overflow == overflow, (col_overflow, overflow)
            assert overflow >= 10, (
                f"旧值应复现越界，实际仅 {overflow} px（面板 {panel.height()}）")
            assert _bottom_in(col, panel) > 303, "旧值下列底边应越过 303 请求高"
        finally:
            panel.deleteLater()
