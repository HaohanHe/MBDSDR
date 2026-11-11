# SPDX-License-Identifier: MIT
"""
P2-9/10 钉扎测试：频率轴滚轮锚点缩放 + FFT 仪器化读数
=========================================================

P2-9（gqrx 密度分层）：悬停底部频率刻度轴（谱面下缘 _SPEC_BOTTOM_PAD 条带）
滚轮 = 以光标为锚缩放 span（缩放前后光标处绝对频率不变，与 Ctrl+滚轮
同数学）；VFO 正下方不抢手势；Shift/Alt 修饰在轴上仍按调谐齿轮走。

P2-10（gqrx 把瀑布当测量仪器）：工具栏 RBW = span/FFT 点数，瀑布时间
跨度 = 环形缓冲深度 ÷ **实测行率**（行是每 IQ 帧一行，与绘图定时器无关；
按定时器算会把 256 行说成 12.8 s、实际 1.75 s，差 7 倍）；未测到行率显
「--」不造假，无数据显 "--" 不造假。

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

    # 滚轮向上 = 拉近（span 变窄）；反向是拉远，但已到全带宽时无处可去。
    _wheel_at(plot, x, y_axis, -120, Qt.NoModifier)

    assert gen.span_hz == pytest.approx(before_span / 1.1)
    # 硬件采样率不是缩放目标：它每帧被 push_iq 覆写，写进去等于白改。
    assert gen.sample_rate_hz == pytest.approx(before_span)
    # 锚点不变：缩放前后光标处绝对频率一致
    r = x / plot.width()
    anchor_before = before_center + (r - 0.5) * before_span
    anchor_after = gen.center_freq_hz + (r - 0.5) * gen.span_hz
    assert anchor_after == pytest.approx(anchor_before)
    # 右侧「缩放」滑杆跟着走到位
    assert panel._span_slider.value() == panel._slider_from_span(gen.span_hz)


def test_wheel_above_axis_still_tunes(qapp):
    from spectrum_widget import _SPEC_BOTTOM_PAD
    panel = _panel(qapp)
    plot = panel._plot
    gen = panel.generator
    before_span = gen.span_hz
    y_above = int(plot.height()) - _SPEC_BOTTOM_PAD - 4  # 条带上方

    _wheel_at(plot, 400, y_above, 120, Qt.NoModifier)

    assert gen.span_hz == pytest.approx(before_span)          # span 未动
    assert gen.center_freq_hz != pytest.approx(100e6)         # 中心被步进


def test_shift_wheel_on_axis_keeps_tuning_gear(qapp):
    from spectrum_widget import _SPEC_BOTTOM_PAD
    panel = _panel(qapp)
    plot = panel._plot
    gen = panel.generator
    before_span = gen.span_hz
    snap = panel._state.snap_interval
    y_axis = int(plot.height()) - _SPEC_BOTTOM_PAD + 4

    _wheel_at(plot, 400, y_axis, 120, Qt.ShiftModifier)

    assert gen.span_hz == pytest.approx(before_span)   # 未缩放
    # Shift×10 步进调谐
    assert abs(gen.center_freq_hz - 100e6) == pytest.approx(10 * snap, rel=1e-3)


# ---------------------------------------------------------------------------
# P2-10（2026-11-10 改判）：顶部不挂任何实时读数
# ---------------------------------------------------------------------------
def test_no_permanent_numeric_readouts(qapp):
    """顶部不再挂 SNR / 跨度 / RBW / 瀑布秒数（用户裁决：数字没人读得过来）。

    SDR++ 顶上只有一个大频点和音量滑杆；读数只在鼠标悬停时临时出现。
    generator 的测量 API（RBW、实测行率、瀑布可回看时长）仍然保留真值，
    需要时能取到——只是不再常驻成一张每秒跳几十次的"读数表"。
    """
    panel = _panel(qapp)
    for gone in ("_snr_label", "_span_label", "_fft_info_label", "_freq_label"):
        assert not hasattr(panel, gone), f"{gone} 又回到顶栏了"
    gen = panel.generator
    gen._has_real_data = True
    panel._refresh_status_labels()          # 数据通路挂钩仍在，且不产生文字
    assert gen.sample_rate_hz / gen.fft_size > 0     # RBW 真值仍可算


def test_span_slider_controls_display_span_only(qapp):
    """右侧「缩放」滑杆改显示跨度，绝不动硬件采样率。

    反证：把实现改回"写 sample_rate_hz"必红——真机上该值每帧被
    push_iq 覆写，缩放下一帧就被抹掉（这就是以前缩放失灵的根因）。
    """
    panel = _panel(qapp)
    gen = panel.generator
    full = gen.span_hz
    panel._span_slider.setValue(panel._span_slider.maximum())
    assert gen.span_hz < full
    assert gen.span_hz >= 48_000.0
    assert gen.sample_rate_hz == pytest.approx(full)   # 硬件采样率不动
    panel._span_slider.setValue(0)
    assert gen.span_hz == pytest.approx(full)          # 拉回全带宽
    # 三根竖杆齐全，且没有「上限 -20 / 下限 -100」这类数值标签
    assert hasattr(panel, "_db_max_slider") and hasattr(panel, "_db_min_slider")
    assert not hasattr(panel, "_db_max_cap") and not hasattr(panel, "_db_min_cap")
