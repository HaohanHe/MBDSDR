# SPDX-License-Identifier: MIT
"""
P2-9/10 钉扎测试：频率轴滚轮锚点缩放 + FFT 仪器化读数
=========================================================

P2-9（gqrx 密度分层）：悬停底部频率刻度轴（谱面下缘 _SPEC_BOTTOM_PAD 条带）
滚轮 = 以光标为锚缩放 span（缩放前后光标处绝对频率不变，与 Ctrl+滚轮
同数学）；VFO 正下方不抢手势；Shift/Alt 修饰在轴上仍按调谐齿轮走。

P2-10（gqrx 把瀑布当测量仪器）：工具栏 RBW = span/FFT 点数，瀑布时间
跨度 = 环形缓冲行数 × 绘图定时器间隔；无数据显 "--" 不造假。

不依赖硬件 / 声卡（spectrum/IQ 数组直接钉值）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _panel(qapp):
    from spectrum_widget import SpectrumPanel
    panel = SpectrumPanel()
    iq = np.zeros(4096, dtype=np.complex64)
    panel.update_iq(iq, 100e6, 2.4e6)
    panel._plot.resize(800, 300)
    return panel


def _wheel_at(plot, x, y, dy, mods):
    pos = QPointF(float(x), float(y))
    ev = QWheelEvent(pos, pos, QPoint(0, 0), QPoint(0, dy),
                     Qt.NoButton, mods, Qt.ScrollUpdate, False)
    plot.wheelEvent(ev)


# ---------------------------------------------------------------------------
# P2-9 频率轴滚轮锚点缩放
# ---------------------------------------------------------------------------
def test_wheel_on_freq_axis_zooms_span_anchored(qapp):
    from spectrum_widget import _SPEC_BOTTOM_PAD
    panel = _panel(qapp)
    plot = panel._plot
    gen = panel.generator
    y_axis = int(plot.height()) - _SPEC_BOTTOM_PAD + 4   # 刻度条带内
    x = 200.0                                            # r = 0.25
    before_center = gen.center_freq_hz
    before_span = gen.sample_rate_hz

    _wheel_at(plot, x, y_axis, 120, Qt.NoModifier)

    assert gen.sample_rate_hz == pytest.approx(before_span * 1.1)
    # 锚点不变：缩放前后光标处绝对频率一致
    r = x / plot.width()
    anchor_before = before_center + (r - 0.5) * before_span
    anchor_after = gen.center_freq_hz + (r - 0.5) * gen.sample_rate_hz
    assert anchor_after == pytest.approx(anchor_before)


def test_wheel_above_axis_still_tunes(qapp):
    from spectrum_widget import _SPEC_BOTTOM_PAD
    panel = _panel(qapp)
    plot = panel._plot
    gen = panel.generator
    before_span = gen.sample_rate_hz
    y_above = int(plot.height()) - _SPEC_BOTTOM_PAD - 4  # 条带上方

    _wheel_at(plot, 400, y_above, 120, Qt.NoModifier)

    assert gen.sample_rate_hz == pytest.approx(before_span)   # span 未动
    assert gen.center_freq_hz != pytest.approx(100e6)         # 中心被步进


def test_shift_wheel_on_axis_keeps_tuning_gear(qapp):
    from spectrum_widget import _SPEC_BOTTOM_PAD
    panel = _panel(qapp)
    plot = panel._plot
    gen = panel.generator
    before_span = gen.sample_rate_hz
    snap = panel._state.snap_interval
    y_axis = int(plot.height()) - _SPEC_BOTTOM_PAD + 4

    _wheel_at(plot, 400, y_axis, 120, Qt.ShiftModifier)

    assert gen.sample_rate_hz == pytest.approx(before_span)   # 未缩放
    # Shift×10 步进调谐
    assert abs(gen.center_freq_hz - 100e6) == pytest.approx(10 * snap, rel=1e-3)


# ---------------------------------------------------------------------------
# P2-10 FFT 仪器化读数
# ---------------------------------------------------------------------------
def test_fft_info_shows_rbw_and_waterfall_span(qapp):
    panel = _panel(qapp)
    gen = panel.generator
    gen._has_real_data = True
    panel._refresh_status_labels()
    text = panel._fft_info_label.text()
    rbw = gen.sample_rate_hz / gen.fft_size
    ms = panel._plot._timer.interval()
    wf_s = gen.max_waterfall_lines * ms / 1000.0
    assert text == f"RBW {rbw:g} Hz · 瀑布 {wf_s:g} s"


def test_fft_info_updates_with_performance_mode(qapp):
    panel = _panel(qapp)
    gen = panel.generator
    gen._has_real_data = True
    panel.set_performance_mode(2)          # refresh 100ms / fft 512
    panel._refresh_status_labels()
    text = panel._fft_info_label.text()
    rbw = gen.sample_rate_hz / gen.fft_size
    wf_s = gen.max_waterfall_lines * 100 / 1000.0
    assert gen.fft_size == 512
    assert text == f"RBW {rbw:g} Hz · 瀑布 {wf_s:g} s"


def test_fft_info_empty_state(qapp):
    from spectrum_widget import SpectrumPanel
    panel = SpectrumPanel()
    panel._refresh_status_labels()
    assert panel._fft_info_label.text() == "RBW --"


# ---------------------------------------------------------------------------
# 顶部 span 读数：带含义与单位，不是裸数字
# ---------------------------------------------------------------------------
def test_span_label_has_meaning_and_unit_not_bare_number(qapp):
    """反证：顶部 span 读数必须带含义+单位，不能是裸数字。

    上游：该格取 ``generator.sample_rate_hz``；同一个变量在本文件里就是
    显示跨度——频率轴 ``f0 = center − sr/2``、``RBW = sr/fft_size``，
    Ctrl+滚轮/捏合还会把 ``new_span`` 直接写回 ``sample_rate_hz``。
    所以单位是带宽的 MHz，而不是采样率 MS/s；裸 ``2.400 MHz`` 会被
    读成第二个频率。
    """
    from spectrum_widget import SpectrumPanel
    panel = _panel(qapp)                       # update_iq(..., 100e6, 2.4e6)
    text = panel._span_label.text()
    assert text == "跨度 2.400 MHz"
    assert text != "2.400 MHz"                 # 裸数字会被误读成第二个频率
    assert "跨度" in text and "MHz" in text

    # 缩放会直接改写 sample_rate_hz（此处它就是显示跨度），读数仍是带含义的串。
    panel.generator.sample_rate_hz = 1.2e6
    panel._refresh_status_labels()
    assert panel._span_label.text() == "跨度 1.200 MHz"

    # 无数据态同样不许出现裸占位符。
    empty = SpectrumPanel()
    empty._refresh_status_labels()
    assert empty._span_label.text() == "跨度 --"
