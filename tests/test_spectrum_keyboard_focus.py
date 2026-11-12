# SPDX-License-Identifier: MIT
"""
tests/test_spectrum_keyboard_focus.py
=====================================
键盘调谐的焦点前提（2026-11-11，防呆复现）。

背景：方向键在谱面里是「改中心频率」，在主窗别处是「移动选择」——这是有意
的分歧。但它成立的前提是焦点真的落在谱面上：
  * 谱面 `SpectrumCurveWidget` 是 `StrongFocus`，点击即获焦（Qt 默认）；
  * 瀑布 `WaterfallWidget` 原本 `NoFocus` 且没有 mousePressEvent，点完焦点
    仍留在工具栏/表格，方向键去移动那边的选择 → 看起来像「点完瀑布调谐失灵」；
  * 焦点在输入框/表格时，方向键被它们吃掉，谱面没有任何提示。

修复：谱面 mousePressEvent 显式 `setFocus`；瀑布 press 把焦点转交谱面；
谱面 tooltip 写明「键盘调谐需焦点在谱面」。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_spectrum_keyboard_focus.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_NO_HW", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import spectrum_widget as sw  # noqa: E402

_CENTER = 120e6
_SPAN = 2.4e6
_SNAP = 10_000.0


@pytest.fixture()
def panel():
    p = sw.SpectrumPanel()
    p.resize(900, 650)
    p.show()
    p.activateWindow()
    app.processEvents()
    gen = p.generator
    gen.center_freq_hz = _CENTER
    gen.sample_rate_hz = _SPAN
    p._state.snap_interval = _SNAP
    iq = (np.random.default_rng(0).standard_normal(16384)
          + 1j * np.random.default_rng(1).standard_normal(16384)
          ).astype(np.complex64)
    p.update_iq(iq, _CENTER, _SPAN)
    app.processEvents()
    yield p
    p.deleteLater()
    app.processEvents()


def _send_key(widget, key=Qt.Key_Right, mods=Qt.KeyboardModifier.NoModifier):
    QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, key, mods))


def _arrow_to_focus(key=Qt.Key_Right):
    """按真实键盘路径：键事件发给当前焦点控件（没有焦点则发给窗口）。"""
    fw = app.focusWidget()
    _send_key(fw if fw is not None else app.activeWindow(), key)


class TestClickGivesKeyboardFocus:
    """点谱面 / 点瀑布之后，方向键必须真的能调谐。"""

    def test_click_spectrum_then_arrow_tunes(self, panel):
        plot = panel._plot
        plot.clearFocus()
        app.processEvents()
        QTest.mouseClick(plot, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, QPoint(500, 100))
        app.processEvents()
        assert plot.hasFocus(), "点击谱面后焦点不在谱面（方向键会被别处吃掉）"
        before = panel.generator.center_freq_hz
        _arrow_to_focus()
        app.processEvents()
        assert panel.generator.center_freq_hz == before + _SNAP, (
            f"点谱面后按 → 应 +{_SNAP} Hz，实际 "
            f"{panel.generator.center_freq_hz - before:+.0f}")

    def test_click_waterfall_hands_focus_to_plot_then_arrow_tunes(self, panel):
        plot = panel._plot
        wf = panel._wf_plot
        plot.clearFocus()
        app.processEvents()
        QTest.mouseClick(wf, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, QPoint(300, 30))
        app.processEvents()
        assert app.focusWidget() is plot, (
            "点瀑布后焦点没有转交给谱面：方向键会去移动工具栏/表格的选择")
        before = panel.generator.center_freq_hz
        _arrow_to_focus()
        app.processEvents()
        assert panel.generator.center_freq_hz == before + _SNAP

    def test_show_without_click_arrows_do_not_tune(self, panel):
        """复现用户场景：show() 后不点谱面，方向键不改频率。"""
        if app.focusWidget() is panel._plot:
            pytest.skip("该平台 show() 默认焦点就在谱面，构不出未点场景")
        before = panel.generator.center_freq_hz
        _arrow_to_focus()
        app.processEvents()
        assert panel.generator.center_freq_hz == before


class TestFocusElsewhereConsumesArrows:
    """焦点在别处时方向键归那边，谱面不调谐也没有误导性反馈。"""

    def test_line_edit_consumes_right_arrow(self, panel):
        line = QLineEdit("abcdef")
        line.show()
        line.setFocus()
        line.setCursorPosition(3)
        app.processEvents()
        before = panel.generator.center_freq_hz
        _arrow_to_focus()
        app.processEvents()
        assert line.cursorPosition() == 4, "输入框没吃掉 →，焦点前提不成立"
        assert panel.generator.center_freq_hz == before, (
            "焦点在输入框时谱面不该调谐")

    def test_plot_tooltip_states_focus_requirement(self, panel):
        tip = panel._plot.toolTip()
        assert "焦点" in tip and "谱面" in tip, (
            f"谱面缺少「键盘调谐需焦点在谱面」的文案，实际 tooltip={tip!r}")


class TestSpanZoomKeys:
    """Ctrl+= / Ctrl+-：跨度缩放本来就只有滚轮，纯键盘够不到（2026-11-12）。

    契约：键位来自 shortcuts.py 登记表（scope=spectrum），走与滚轮**同一条**
    缩放数学（×1.1、48 kHz–采样率钳位），键盘没有光标故锚点固定取谱面中心。
    反证：把登记表里这两条的 key 摘成 None，本类第一条断言必红。
    """

    def _focus_plot(self, panel):
        plot = panel._plot
        QTest.mouseClick(plot, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, QPoint(450, 100))
        app.processEvents()
        assert plot.hasFocus(), "焦点没落到谱面，缩放键不会触发"
        return plot

    def test_ctrl_equal_zooms_in_and_ctrl_minus_zooms_out(self, panel):
        plot = self._focus_plot(panel)
        gen = panel.generator
        assert gen.span_hz == _SPAN

        QTest.keyClick(plot, Qt.Key_Equal, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        assert abs(gen.span_hz - _SPAN / 1.1) < 1.0, (
            f"Ctrl+= 应把跨度缩到 {_SPAN / 1.1:.0f} Hz，实际 {gen.span_hz:.0f}")
        assert gen.center_freq_hz == _CENTER, (
            "键盘缩放锚点应取谱面中心：中心频率不该被移动")

        QTest.keyClick(plot, Qt.Key_Minus, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        assert abs(gen.span_hz - _SPAN) < 1.0, (
            f"Ctrl+- 应把跨度放回 {_SPAN:.0f} Hz，实际 {gen.span_hz:.0f}")

    def test_repeat_zoom_out_stops_at_upper_bound(self, panel):
        plot = self._focus_plot(panel)
        for _ in range(80):
            QTest.keyClick(plot, Qt.Key_Minus, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        span = panel.generator.span_hz
        assert span <= _SPAN + 1.0, f"跨度越过采样率：{span:.0f} > {_SPAN:.0f}"
        assert abs(span - _SPAN) < 1.0, f"放到底应回到全带宽，实际 {span:.0f}"

    def test_repeat_zoom_in_stops_at_lower_bound(self, panel):
        plot = self._focus_plot(panel)
        for _ in range(60):
            QTest.keyClick(plot, Qt.Key_Equal, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        assert panel.generator.span_hz == 48_000.0, (
            f"连续放大应停在 48 kHz，实际 {panel.generator.span_hz:.0f}")

    def test_keyboard_matches_wheel_at_center_anchor(self, panel):
        """同一锚点（谱面中心）下，键盘与滚轮必须得到相同的跨度与中心。"""
        plot = self._focus_plot(panel)
        gen = panel.generator
        w = plot.width()
        assert plot._hit_vfo_at(w / 2)[0] is None, (
            "中心有 VFO，滚轮会改带宽而不是跨度，用例前提不成立")

        QTest.keyClick(plot, Qt.Key_Equal, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        key_span, key_center = gen.span_hz, gen.center_freq_hz

        gen.set_span(None)
        gen.center_freq_hz = _CENTER
        app.processEvents()
        pos = QPointF(w / 2, 100)
        # 下滚 = 跨度变小（与 Ctrl+= 同向）；上滚在满带宽时会被上限钳住，
        # 两者没法比较。
        ev = QWheelEvent(pos, QPointF(plot.mapToGlobal(QPoint(w // 2, 100))),
                         QPoint(0, 0), QPoint(0, -120),
                         Qt.MouseButton.NoButton,
                         Qt.KeyboardModifier.ControlModifier,
                         Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(plot, ev)
        app.processEvents()
        assert abs(gen.span_hz - key_span) < 1.0, (
            f"滚轮 {gen.span_hz:.0f} Hz ≠ 键盘 {key_span:.0f} Hz")
        assert abs(gen.center_freq_hz - key_center) < 1.0, (
            f"滚轮中心 {gen.center_freq_hz:.0f} ≠ 键盘中心 {key_center:.0f}")
