#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
滚轮调谐分档测试（phase83-A6）
=============================

操作逻辑：普通滚轮 = snap 步进；Shift ×10；Alt ×0.1；Ctrl = 锚点缩放 span。
步进必须作用在中心频率上并 round 到网格；瞬时 tooltip 给出可见反馈。

运行: QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_wheel_step_gear.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _panel(qapp):
    from spectrum_widget import SpectrumPanel
    import numpy as np
    panel = SpectrumPanel()
    # 喂一段 IQ，让 generator 有真实采样率/中心频率状态
    iq = (np.zeros(4096, dtype=np.complex64))
    panel.update_iq(iq, 100e6, 2.4e6)
    return panel


def _wheel(plot, dy, mods):
    """合成滚轮事件并派发给 plot。"""
    from PySide6.QtCore import QPointF, QPoint, Qt
    from PySide6.QtGui import QWheelEvent
    pos = QPointF(50.0, 50.0)
    ev = QWheelEvent(
        pos, pos, QPoint(0, 0), QPoint(0, dy),
        Qt.NoButton, mods, Qt.ScrollUpdate, False,
    )
    plot.wheelEvent(ev)


def _key(plot, key, mods=None):
    """合成一次按键并派发给 plot（与真实快捷键走同一 keyPressEvent）。"""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    ev = QKeyEvent(QEvent.KeyPress, key, mods if mods is not None else Qt.NoModifier)
    plot.keyPressEvent(ev)


def test_tuning_step_100k_arrow_is_snap_interval_end_to_end(qapp):
    """tuning_step_hz 端到端：切到 100 kHz，按一次 → Δfreq == snap 且落点在网格。

    反证：若吸附没跟上档位（仍是默认 10 kHz），Δ 会是 10 kHz（应跳的 1/10），
    且落点按 10 kHz 网格吸附——本测试立即红。
    """
    from PySide6.QtCore import Qt
    panel = _panel(qapp)
    panel.set_snap_interval(100_000.0)
    plot = panel._plot
    gen = panel._state.panel.generator
    snap = panel._state.snap_interval
    assert snap == 100_000.0
    gen.center_freq_hz = round(gen.center_freq_hz / snap) * snap
    before = gen.center_freq_hz

    _key(plot, Qt.Key_Right)

    after = gen.center_freq_hz
    assert after - before == snap, \
        f"100 kHz 档按一次应跳 {snap} Hz，实际 {after - before}"
    assert after % snap == 0, f"落点 {after} 未吸附到 {snap} 网格"


def test_plain_wheel_steps_by_snap_interval(qapp):
    from PySide6.QtCore import Qt
    panel = _panel(qapp)
    plot = panel._plot
    gen = panel._state.panel.generator
    snap = panel._state.snap_interval
    before = gen.center_freq_hz

    _wheel(plot, 120, Qt.NoModifier)

    after = gen.center_freq_hz
    expected = round((before + snap) / snap) * snap
    assert after == expected, \
        f"普通滚轮应步进一个 snap({snap})，{before} -> {after}，期望 {expected}"


def test_shift_wheel_multiplies_by_10(qapp):
    from PySide6.QtCore import Qt
    panel = _panel(qapp)
    plot = panel._plot
    gen = panel._state.panel.generator
    snap = panel._state.snap_interval
    before = gen.center_freq_hz

    _wheel(plot, 120, Qt.ShiftModifier)
    after = gen.center_freq_hz
    step = snap * 10.0
    expected = round((before + step) / step) * step
    assert after == expected, \
        f"Shift 必须 ×10 步进，{before} -> {after}，期望 {expected}"


def test_alt_wheel_multiplies_by_0_1(qapp):
    from PySide6.QtCore import Qt
    panel = _panel(qapp)
    plot = panel._plot
    gen = panel._state.panel.generator
    snap = panel._state.snap_interval
    before = gen.center_freq_hz

    _wheel(plot, 120, Qt.AltModifier)
    after = gen.center_freq_hz
    step = snap * 0.1
    expected = round((before + step) / step) * step
    assert after == expected, \
        f"Alt 必须 ×0.1 步进，{before} -> {after}，期望 {expected}"


def test_wheel_down_decreases_freq(qapp):
    from PySide6.QtCore import Qt
    panel = _panel(qapp)
    gen = panel._state.panel.generator
    before = gen.center_freq_hz
    _wheel(panel._plot, -120, Qt.NoModifier)
    assert gen.center_freq_hz < before, "下滚必须减频"


def test_tooltip_feedback_hooked(qapp, monkeypatch):
    """滚轮调谐必须触发瞬时 tooltip（可见反馈）。"""
    from PySide6.QtCore import Qt
    panel = _panel(qapp)
    calls = []
    monkeypatch.setattr(
        panel._plot, "_show_tune_tip",
        lambda text: calls.append(text))
    _wheel(panel._plot, 120, Qt.ShiftModifier)
    assert calls, "Shift 滚轮必须给出瞬时 tooltip"
    assert "MHz" in calls[0], f"tooltip 应含频率单位，实际: {calls[0]!r}"
