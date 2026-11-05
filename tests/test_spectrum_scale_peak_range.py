# SPDX-License-Identifier: MIT
"""
频谱侧三项的守卫 + 反证
========================

  1. 频轴刻度 1/2/5×10ⁿ 阶梯 + 按量程自动单位（kHz/MHz/GHz）；
     反证：旧的“等距 5 刻度、固定 MHz”在窄谱面压字 / 在窄 span 上
     给出非阶梯值且没有单位。
  2. 峰值检测双通道（窄峰 + 平滑宽峰，合并去重）；
     反证：旧的单通道局部极大把一条带内起伏的宽带信号拆成 ≥5 个点。
  3. dB 轴按住拖动改量程，读数跟手，且与两根滑杆共享唯一状态源；
     反证：旧路径只写 state 不写滑杆（两套量程值会不一致）。

全部 offscreen，无硬件 / 无声卡；手感用几何断言 + 离屏截图，不靠肉眼。
"""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor, QFont, QFontMetricsF, QImage, QMouseEvent, QWheelEvent,
)
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# ---------------------------------------------------------------------------
# 文本几何采集（与 test_ui_layout_locks.py 同一套口径）
# ---------------------------------------------------------------------------
class _RecordingPainter:
    def __init__(self):
        self._font = QFont()
        self._fm = QFontMetricsF(self._font)
        self.texts = []

    def setPen(self, *_a, **_k):
        pass

    def setBrush(self, *_a, **_k):
        pass

    def setFont(self, font):
        self._font = font
        self._fm = QFontMetricsF(font)

    def fontMetrics(self):
        return self._fm

    def drawText(self, *args):
        if len(args) == 2:
            pt, text = args
            self.texts.append((float(pt.x()), float(pt.y()), str(text), self._fm))

    # 峰值圆点 / dB 拖动气泡会用到；仅记录 drawText，图元本身不需要断言。
    def drawEllipse(self, *_a, **_k):
        pass

    def drawRoundedRect(self, *_a, **_k):
        pass


def _text_rects(rec):
    out = []
    for x, y, text, fm in rec.texts:
        w = fm.horizontalAdvance(text)
        out.append((x, x + w, y - fm.ascent(), y + fm.descent(), text))
    return out


def _overlaps(a, b):
    return a[0] < b[1] and a[1] > b[0] and a[2] < b[3] and a[3] > b[2]


def _font8():
    f = QFont()
    f.setPointSize(8)
    return QFontMetricsF(f)


# ---------------------------------------------------------------------------
# 1. 频轴刻度
# ---------------------------------------------------------------------------
def _legacy_freq_labels(center_hz, span_hz, n=5):
    """旧实现：固定等距 n 刻度、固定 MHz、2 位小数。"""
    return [f"{center_hz / 1e6 - span_hz / 2e6 + span_hz / 1e6 * i / (n - 1):.2f}"
            for i in range(n)]


def test_counterproof_legacy_freq_scale_presses_text_on_narrow_plot():
    """窄谱面（120px）旧 5 等距刻度必然互相压字，几何判定当场红。"""
    fm = _font8()
    labels = _legacy_freq_labels(100e6, 2.048e6, 5)
    rects = []
    for i, s in enumerate(labels):
        x = 120.0 * i / 4.0
        w = fm.horizontalAdvance(s)
        rects.append((x - w / 2, x + w / 2, -fm.ascent(), fm.descent(), s))
    assert any(_overlaps(a, b) for i, a in enumerate(rects)
               for b in rects[i + 1:]), "旧刻度在 120px 窄面上居然没压字？"


def test_new_freq_scale_never_overlaps_or_clips(qapp):
    import spectrum_widget as sw
    fm = _font8()
    for width, span, center in ((120.0, 2.048e6, 100e6),
                                (220.0, 500e3, 100e6),
                                (345.0, 48e3, 100e6)):
        rect = QRectF(0, 0, width, 160)
        rec = _RecordingPainter()
        sw._draw_freq_scale(rec, rect, center, span, QColor("white"))
        rects = _text_rects(rec)
        # 刻度标签 + 右端一个自动单位
        assert 3 <= len(rects) <= 6, f"width={width} 文本数 {len(rects)}"
        assert any(r[4] in ("kHz", "MHz", "GHz") for r in rects), \
            f"width={width} 没有自动单位"
        for i, a in enumerate(rects):
            for b in rects[i + 1:]:
                assert not _overlaps(a, b), f"width={width} 压字 {a[4]}/{b[4]}"
            assert a[0] >= rect.left() - 0.5 and a[1] <= rect.right() + 0.5, \
                f"width={width} 刻度越界 {a[4]}"


def test_freq_ticks_use_nice_ladder_and_auto_unit():
    import spectrum_widget as sw
    fm = _font8()
    cases = (
        (100e6, 48e3, "kHz"),
        (100e6, 500e3, "kHz"),
        (100e6, 2.048e6, "MHz"),
        (100e6, 20e6, "MHz"),
        (1e9, 1e9, "GHz"),
    )
    for center, span, unit in cases:
        _step, vals, labels, uname = sw._freq_scale_ticks(
            center, span, 345.0, fm.horizontalAdvance, reserve_px=40.0)
        assert uname == unit, f"span={span} 单位应为 {unit}，实际 {uname}"
        mang = _step / 10 ** math.floor(math.log10(_step))
        assert mang in (1.0, 2.0, 5.0), f"步长不是 1/2/5：{_step}"
        for v in vals:                       # 刻度必须落在阶梯网格上
            assert abs(v / _step - round(v / _step)) < 1e-6


def test_counterproof_legacy_freq_has_no_auto_unit():
    labels = _legacy_freq_labels(100e6, 48e3, 5)
    assert not any(s.endswith(("kHz", "MHz", "GHz")) for s in labels)
    import spectrum_widget as sw
    assert sw._freq_unit(48e3)[1] == "kHz"      # 新实现按量程给 kHz
    values = [float(s) for s in labels]
    diffs = [b - a for a, b in zip(values, values[1:])]
    # 旧步长 12 kHz（= span/4）不在 1/2/5 阶梯上；新实现要求 m ∈ {1,2,5}。
    assert any(d / 10 ** math.floor(math.log10(d)) not in (1.0, 2.0, 5.0)
               for d in diffs), "旧刻度步长居然全落在 1/2/5 阶梯上？"


# ---------------------------------------------------------------------------
# 2. 双通道峰检测
# ---------------------------------------------------------------------------
def _legacy_peaks(s, rel_threshold_db=6.0, max_peaks=8):
    """旧实现：仅原始谱严格局部极大。"""
    finite = np.isfinite(s)
    ss = s[finite]
    if ss.size < 3:
        return []
    floor = float(np.median(ss))
    thresh = floor + rel_threshold_db
    left = np.empty_like(s)
    right = np.empty_like(s)
    left[0] = s[0]
    left[1:] = s[:-1]
    right[-1] = s[-1]
    right[:-1] = s[1:]
    idxs = np.nonzero(finite & (s >= left) & (s > right) & (s >= thresh))[0]
    peaks = sorted([(int(i), float(s[i])) for i in idxs],
                   key=lambda q: q[1], reverse=True)
    return peaks[:max_peaks]


def _wideband_with_ripple(n=512):
    _x = np.arange(n)
    bump = 20.0 * np.exp(-0.5 * ((_x - 256) / 18.0) ** 2)
    ripple = 2.0 * np.sin(2 * np.pi * (_x - 256) / 4.0) * (bump > 4.0)
    return np.full(n, -100.0) + bump + ripple


def test_counterproof_legacy_splits_wideband_signal():
    s = _wideband_with_ripple()
    legacy = _legacy_peaks(s)
    assert len(legacy) >= 5, f"旧实现没拆点：{len(legacy)}"
    assert max(i for i, _ in legacy) - min(i for i, _ in legacy) > 10, \
        "旧实现拆出的点没有铺开（反证场景不成立）"


def test_new_dual_channel_merges_wideband_to_one_peak(qapp):
    from spectrum_widget import SpectrumDataGenerator, _detect_peak_bins
    s = _wideband_with_ripple()
    bins = _detect_peak_bins(s, 6.0)
    assert len(bins) == 1, f"宽带信号被拆成 {len(bins)} 个峰（应合并为 1）"
    assert abs(bins[0] - 256) <= 18, f"合并后的峰位偏了：{bins[0]}"

    gen = SpectrumDataGenerator(num_bins=512)
    gen.spectrum = s.astype(np.float32)
    gen._has_real_data = True
    gen.wf_rows_written = 10          # 让节流缓存失效
    peaks = gen.find_peaks(6.0)
    assert len(peaks) == 1
    assert abs(peaks[0][1] - (float(s.max())) ) < 3.0


def test_new_dual_channel_keeps_wide_peak_without_ripple(qapp):
    from spectrum_widget import _detect_peak_bins
    n = 512
    x = np.arange(n)
    s = np.full(n, -100.0) + 18.0 * np.exp(-0.5 * ((x - 200) / 22.0) ** 2)
    bins = _detect_peak_bins(s, 6.0)
    assert len(bins) == 1
    assert abs(bins[0] - 200) <= 22


def test_new_dual_channel_keeps_narrow_spike(qapp):
    from spectrum_widget import _detect_peak_bins
    n = 512
    s = np.full(n, -100.0)
    s[300] = -65.0                  # 35 dB 单 bin 窄峰
    bins = _detect_peak_bins(s, 6.0)
    assert len(bins) == 1, f"窄单音被平滑吞掉：{bins}"
    assert abs(bins[0] - 300) <= 2


def test_new_dual_channel_keeps_two_far_apart_carriers(qapp):
    from spectrum_widget import _detect_peak_bins
    n = 512
    s = np.full(n, -100.0)
    s[120] = s[400] = -80.0
    bins = _detect_peak_bins(s, 6.0)
    assert len(bins) == 2, f"两个远离的载波被误合并：{bins}"


# ---------------------------------------------------------------------------
# 3. dB 量程拖动 + 滑杆共享状态源
# ---------------------------------------------------------------------------
def _panel(qapp, width=800, height=360):
    from spectrum_widget import SpectrumPanel
    panel = SpectrumPanel()
    panel.resize(width, height)
    panel._plot.resize(width, height // 2)
    panel.show()
    qapp.processEvents()
    return panel


def _mouse(typ, x, y, button=Qt.LeftButton, buttons=Qt.LeftButton):
    pos = QPointF(float(x), float(y))
    glob = QPointF(float(x), float(y))
    return QMouseEvent(typ, pos, pos, glob, button, buttons, Qt.NoModifier)


def _assert_single_source(panel):
    lo, hi = panel.db_range()
    assert panel._db_min_slider.value() == int(lo), \
        f"状态 {lo} 与 Min 滑杆 {panel._db_min_slider.value()} 不一致"
    assert panel._db_max_slider.value() == int(hi), \
        f"状态 {hi} 与 Max 滑杆 {panel._db_max_slider.value()} 不一致"


def _img_arr(widget):
    img = widget.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
    ptr = img.constBits()
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape(
        img.height(), img.bytesPerLine())
    return arr.copy()


def test_counterproof_legacy_state_only_write_diverges(qapp):
    """旧路径只写 state（拖动不碰滑杆）→ 两套值当场不一致。"""
    panel = _panel(qapp)
    before_slider = panel._db_min_slider.value()
    panel._state.db_min = float(before_slider - 20)   # 模拟旧实现：只写 state
    assert panel._db_min_slider.value() != int(panel._state.db_min), \
        "旧的两套值竟然同步了？反证不成立"
    panel.apply_db_range(-100.0, -20.0)                # 唯一状态源恢复正常
    _assert_single_source(panel)


def test_db_axis_drag_updates_readout_and_shared_sliders(qapp):
    import spectrum_widget as sw
    panel = _panel(qapp)
    plot = panel._plot
    _assert_single_source(panel)                       # 初始同源

    plot.mousePressEvent(_mouse(QEvent.MouseButtonPress, 10, 120))
    assert panel._state.db_drag_active is True
    assert plot._db_drag_active is True

    plot.mouseMoveEvent(_mouse(QEvent.MouseMove, 10, 80))
    _assert_single_source(panel)                       # 拖动后仍同源
    lo1, hi1 = panel.db_range()
    assert hi1 - lo1 == pytest.approx(80.0, abs=1.5)   # 跨度保持

    # 读数跟手：同一拖拽位置画出的文本与当前状态一致
    rect = QRectF(0, 0, plot.width(), plot.height() - sw._SPEC_BOTTOM_PAD)
    rec = _RecordingPainter()
    sw._draw_db_scale(rec, rect, panel._state, QColor("white"))
    readout = [t[2] for t in rec.texts if t[2].endswith("dB")]
    assert readout, "拖动时没有量程读数"
    assert readout[0] == (
        f"{lo1:.0f} … {hi1:.0f} dB · 跨度 {hi1 - lo1:.0f} dB"), \
        f"读数缺跨度：{readout[0]!r}"
    for r in _text_rects(rec):
        assert r[0] >= rect.left() - 0.5 and r[1] <= rect.right() + 0.5

    img1 = _img_arr(plot)                              # 离屏截图：拖动中
    plot.mouseMoveEvent(_mouse(QEvent.MouseMove, 10, 240))
    _assert_single_source(panel)
    lo2, hi2 = panel.db_range()
    assert (lo2, hi2) != (lo1, hi1), "拖动没有改变量程"
    img2 = _img_arr(plot)
    assert img1.shape == img2.shape and np.any(img1 != img2), \
        "两次拖动位置的离屏截图完全相同（读数没跟手）"

    plot.mouseReleaseEvent(_mouse(QEvent.MouseButtonRelease, 10, 240,
                                  buttons=Qt.NoButton))
    assert panel._state.db_drag_active is False
    _assert_single_source(panel)


def test_slider_and_apply_share_same_range(qapp):
    panel = _panel(qapp)
    panel._db_min_slider.setValue(-70)                 # 滑杆 → 唯一状态源
    assert panel.db_range()[0] == -70.0
    panel.apply_db_range(-90.0, -25.0)                 # 外部/拖动 → 滑杆
    assert panel._db_min_slider.value() == -90
    assert panel._db_max_slider.value() == -25
    _assert_single_source(panel)


def test_db_drag_clamps_to_axis_bounds(qapp):
    panel = _panel(qapp)
    plot = panel._plot
    plot.mousePressEvent(_mouse(QEvent.MouseButtonPress, 10, 180))
    plot.mouseMoveEvent(_mouse(QEvent.MouseMove, 10, 10_000))   # 向上狂拖
    lo, hi = panel.db_range()
    assert hi <= 0.0 and lo >= -160.0
    assert hi - lo == pytest.approx(80.0, abs=1.5)
    plot.mouseMoveEvent(_mouse(QEvent.MouseMove, 10, -10_000))  # 向下狂拖
    lo, hi = panel.db_range()
    assert hi <= 0.0 and lo >= -160.0
    _assert_single_source(panel)
    plot.mouseReleaseEvent(_mouse(QEvent.MouseButtonRelease, 10, -10_000,
                                  buttons=Qt.NoButton))


# ---------------------------------------------------------------------------
# 4. 峰值命中：命中区可宽，落点必须准（标签文字也算自己的峰）
# ---------------------------------------------------------------------------
def _panel_with_one_peak(qapp, center_hz=100e6, span_hz=2.4e6, bin_idx=200):
    panel = _panel(qapp)
    gen = panel.generator
    gen.center_freq_hz = center_hz
    gen.sample_rate_hz = span_hz
    s = np.full(gen.num_bins, -100.0, dtype=np.float32)
    s[bin_idx] = -40.0
    gen.spectrum = s
    gen._has_real_data = True
    gen.wf_rows_written = 10          # 让 find_peaks 缓存失效
    return panel, float(gen.bin_to_freq(bin_idx))


def _peak_xy(panel, freq_hz, db=-40.0):
    import spectrum_widget as sw
    plot = panel._plot
    rect = QRectF(0, 0, plot.width(), plot.height() - sw._SPEC_BOTTOM_PAD)
    x = sw._freq_to_x(rect, sw._view_center_hz(panel._state),
                      panel.generator.sample_rate_hz, freq_hz)
    y = sw._db_to_y(rect, db, panel._state.db_min, panel._state.db_max)
    return x, y


def _click_at(plot, x, y):
    plot.mousePressEvent(_mouse(QEvent.MouseButtonPress, x, y))
    plot.mouseReleaseEvent(
        _mouse(QEvent.MouseButtonRelease, x, y, buttons=Qt.NoButton))


def test_peak_click_lands_on_measured_peak_not_pixel_and_not_snapped(qapp):
    panel, peak_freq = _panel_with_one_peak(qapp)
    plot = panel._plot
    gen = panel.generator
    x, y = _peak_xy(panel, peak_freq)
    assert peak_freq % panel._state.snap_interval != 0, \
        "该峰正好落在 snap 网格上，反证不成立"
    _click_at(plot, x, y)
    assert gen.center_freq_hz == pytest.approx(peak_freq, abs=1e-6), (
        f"点峰应精确落到 {peak_freq:.1f} Hz，实际 {gen.center_freq_hz:.1f}"
        f"（偏差 {gen.center_freq_hz - peak_freq:.1f} Hz）")


def test_peak_label_click_hits_its_own_peak_and_lands_exact(qapp):
    panel, peak_freq = _panel_with_one_peak(qapp)
    plot = panel._plot
    gen = panel.generator
    x, y = _peak_xy(panel, peak_freq)
    assert plot._peak_at(x, y) is not None
    # 标签挂在峰右侧 6px 起：按像素旧路径会落到“峰右边”，命中盒必须认回原峰
    lx, ly = x + 40.0, y - 8.0
    hit = plot._peak_at(lx, ly)
    assert hit is not None and abs(hit[0] - peak_freq) < 1.0
    _click_at(plot, lx, ly)
    assert gen.center_freq_hz == pytest.approx(peak_freq, abs=1e-6)


def test_peak_click_after_view_offset_still_lands_on_peak(qapp):
    panel, peak_freq = _panel_with_one_peak(qapp)
    panel._state.view_offset_hz = 300e3
    plot = panel._plot
    gen = panel.generator
    x, y = _peak_xy(panel, peak_freq)
    assert plot._peak_at(x, y) is not None
    _click_at(plot, x, y)
    assert gen.center_freq_hz == pytest.approx(peak_freq, abs=1e-6)


def test_hover_peak_shows_pointing_hand_cursor(qapp):
    panel, peak_freq = _panel_with_one_peak(qapp)
    plot = panel._plot
    x, y = _peak_xy(panel, peak_freq)
    plot.mouseMoveEvent(_mouse(QEvent.MouseMove, x, y,
                               button=Qt.NoButton, buttons=Qt.NoButton))
    assert plot.cursor().shape() == Qt.PointingHandCursor, \
        "峰命中区内没有给出“可点”的光标反馈"
    # 离开峰命中区 → 光标恢复默认
    plot.mouseMoveEvent(_mouse(QEvent.MouseMove, x + 200.0, y,
                               button=Qt.NoButton, buttons=Qt.NoButton))
    assert plot.cursor().shape() != Qt.PointingHandCursor


def test_peak_click_moves_current_vfo_exact_in_normal_mode(qapp):
    panel, peak_freq = _panel_with_one_peak(qapp)
    plot = panel._plot
    mgr = panel.vfo_manager
    vfo = mgr.add(100e6, 8000.0, "FM", inherit_last=False)
    mgr.set_active_context(vfo, temporary=False)
    panel._state.tune_mode = "normal"
    x, y = _peak_xy(panel, peak_freq)
    _click_at(plot, x, y)
    assert vfo.center_hz == pytest.approx(peak_freq, abs=1e-6), \
        f"normal 模式点峰应精确移 VFO，实际 {vfo.center_hz:.1f} Hz"


# ---------------------------------------------------------------------------
# 4b. 峰值 SNR：基准必须是「本帧全谱有限 bin 的中位数」，标签与命中同值
# ---------------------------------------------------------------------------
def _panel_with_known_floor_peak(qapp, floor_db=-100.0, peak_db=-40.0,
                                 bin_idx=200):
    panel = _panel(qapp)
    gen = panel.generator
    gen.center_freq_hz = 100e6
    gen.sample_rate_hz = 2.4e6
    s = np.full(gen.num_bins, floor_db, dtype=np.float32)
    s[bin_idx] = peak_db
    gen.spectrum = s
    gen._has_real_data = True
    gen.wf_rows_written = 10          # 让峰缓存失效
    return panel, float(gen.bin_to_freq(bin_idx))


def test_counterproof_legacy_peak_has_no_snr(qapp):
    """旧口径：find_peaks 只回 (freq, db)，没有 SNR；标签也只有频率+dB。"""
    panel, _peak_freq = _panel_with_known_floor_peak(qapp)
    old = panel.generator.find_peaks(6.0)
    assert len(old) == 1 and len(old[0]) == 2, f"旧峰结构变了：{old}"
    assert not any(field == "SNR" for field in old[0])


def test_peak_snr_baseline_is_median_and_shown_on_label(qapp):
    """SNR = 峰功率 − 本帧全谱中位数；图上的标签与命中返回必须同值。"""
    import spectrum_widget as sw
    panel, peak_freq = _panel_with_known_floor_peak(qapp)
    gen = panel.generator
    assert gen.noise_floor_db() == pytest.approx(-100.0, abs=0.1), \
        "SNR 基准不是本帧全谱中位数"
    detail = gen.find_peaks_with_snr(6.0)
    assert detail and detail[0][0] == pytest.approx(peak_freq, abs=1.0)
    assert detail[0][2] == pytest.approx(60.0, abs=0.2), \
        f"SNR 应为 60 dB：{detail[0]}"

    rect = QRectF(0, 0, panel._plot.width(),
                  panel._plot.height() - sw._SPEC_BOTTOM_PAD)
    rec = _RecordingPainter()
    sw._draw_peaks(rec, panel._state, rect, QColor("white"))
    labels = [t[2] for t in rec.texts]
    assert any("SNR" in s for s in labels), f"峰值标签缺 SNR：{labels}"
    assert any("60" in s for s in labels), f"SNR 数值没上标签：{labels}"

    x, y = _peak_xy(panel, peak_freq)
    hit = panel._plot._peak_at(x, y)
    assert hit is not None and len(hit) == 3, f"命中没带 SNR：{hit}"
    assert hit[2] == pytest.approx(60.0, abs=0.2)
    # 标签文案必须由同一份数据生成：命中频率与图上标签同源
    assert any(s.startswith(f"{hit[0] / 1e6:.3f}MHz") for s in labels), \
        f"图上标签与命中峰不是同一份数据：{labels}"


def test_peak_snr_uses_median_not_mean(qapp):
    """把 20% 的 bin 压到 -120：均值 -104、中位数 -100；SNR 必须按中位数算。"""
    panel = _panel(qapp)
    gen = panel.generator
    gen.center_freq_hz = 100e6
    gen.sample_rate_hz = 2.4e6
    s = np.full(gen.num_bins, -100.0, dtype=np.float32)
    s[:int(gen.num_bins * 0.2)] = -120.0
    s[200] = -40.0
    gen.spectrum = s
    gen._has_real_data = True
    gen.wf_rows_written = 10
    mean = float(np.mean(s[np.isfinite(s)]))
    median = float(np.median(s[np.isfinite(s)]))
    assert mean < median - 2.0, "反证场景不成立：均值与中位数没拉开"
    assert gen.noise_floor_db() == pytest.approx(median, abs=0.1)
    detail = gen.find_peaks_with_snr(6.0)
    assert detail[0][2] == pytest.approx(-40.0 - median, abs=0.2)
    assert detail[0][2] != pytest.approx(-40.0 - mean, abs=0.2), \
        "SNR 按均值算了（基准写错）"


# ---------------------------------------------------------------------------
# 5. dB 轴手势：Ctrl+竖拖改跨度；轴上滚轮缩跨度，不再落到调频
# ---------------------------------------------------------------------------
def _mouse_mod(typ, x, y, modifiers, buttons=None):
    pos = QPointF(float(x), float(y))
    if buttons is None:
        buttons = Qt.LeftButton if typ != QEvent.MouseButtonRelease else Qt.NoButton
    return QMouseEvent(typ, pos, pos, pos, Qt.LeftButton, buttons, modifiers)


def _wheel(widget, x, y, dy, modifiers=Qt.NoModifier):
    ev = QWheelEvent(QPointF(float(x), float(y)),
                     widget.mapToGlobal(QPointF(float(x), float(y))),
                     QPoint(0, 0), QPoint(0, dy),
                     Qt.NoButton, modifiers,
                     Qt.NoScrollPhase, False)
    widget.wheelEvent(ev)


def test_ctrl_db_drag_narrows_span_anchored_at_press(qapp):
    panel = _panel(qapp)
    plot = panel._plot
    h = float(plot._spectrum_height() - 18)
    press_y = h * 0.55
    plot.mousePressEvent(
        _mouse_mod(QEvent.MouseButtonPress, 10, press_y, Qt.ControlModifier))
    lo0, hi0 = panel.db_range()
    anchor0 = hi0 - (press_y / h) * (hi0 - lo0)

    # 向下拖 = 收窄跨度（锚点仍是按下处 dB）
    plot.mouseMoveEvent(
        _mouse_mod(QEvent.MouseMove, 10, h * 0.80, Qt.ControlModifier))
    lo1, hi1 = panel.db_range()
    assert hi1 - lo1 < 80.0, f"Ctrl 竖拖没有收窄跨度：{lo1} … {hi1}"
    assert hi1 - lo1 >= 10.0, f"跨度越过 10 dB 下限：{lo1} … {hi1}"
    anchor1 = hi1 - (press_y / h) * (hi1 - lo1)
    assert abs(anchor1 - anchor0) <= 1.5, \
        f"按下处 dB 没被锚住：{anchor0:.1f} → {anchor1:.1f}"
    _assert_single_source(panel)


def test_wheel_on_db_axis_zooms_span_not_tune(qapp):
    panel, _peak_freq = _panel_with_one_peak(qapp)
    plot = panel._plot
    gen = panel.generator
    center0 = gen.center_freq_hz
    lo0, hi0 = panel.db_range()
    # 下滚（dy<0）= 收窄跨度（与频率轴 Ctrl+滚轮“上滚=放大”同一方向约定）
    _wheel(plot, 10, 100, -120)
    assert gen.center_freq_hz == pytest.approx(center0, abs=1e-6), \
        "dB 轴上的滚轮又落到了调频"
    lo1, hi1 = panel.db_range()
    assert hi1 - lo1 < hi0 - lo0, f"dB 轴滚轮没有收窄跨度：{lo1} … {hi1}"
    _assert_single_source(panel)


def test_ctrl_db_zoom_shows_value_bubble_at_cursor_and_hides_on_release(qapp):
    """Ctrl+竖拖缩放时，光标旁要有实时数值气泡；松手即消失。

    气泡内容必须复用现有读数格式（"-120 … -40 dB · 跨度 80 dB"），
    不许另造一套。普通拖（非 Ctrl）不弹气泡，读数仍在轴区右上。
    """
    import spectrum_widget as sw
    panel = _panel(qapp)
    plot = panel._plot
    h = float(plot._spectrum_height() - sw._SPEC_BOTTOM_PAD)
    press_y = h * 0.55
    cursor_y = h * 0.80
    plot.mousePressEvent(
        _mouse_mod(QEvent.MouseButtonPress, 10, press_y, Qt.ControlModifier))
    plot.mouseMoveEvent(
        _mouse_mod(QEvent.MouseMove, 10, cursor_y, Qt.ControlModifier))
    lo, hi = panel.db_range()
    txt = f"{lo:.0f} … {hi:.0f} dB · 跨度 {hi - lo:.0f} dB"
    rect = QRectF(0, 0, plot.width(), plot.height() - sw._SPEC_BOTTOM_PAD)
    rec = _RecordingPainter()
    sw._draw_db_scale(rec, rect, panel._state, QColor("white"))
    near = [t for t in rec.texts
            if t[2] == txt and abs(t[1] - cursor_y) <= 20.0
            and t[0] < rect.width() * 0.5]
    assert near, f"Ctrl 缩放时没有光标旁数值气泡：{rec.texts}"

    plot.mouseReleaseEvent(
        _mouse(QEvent.MouseButtonRelease, 10, cursor_y, buttons=Qt.NoButton))
    rec2 = _RecordingPainter()
    sw._draw_db_scale(rec2, rect, panel._state, QColor("white"))
    assert not [t for t in rec2.texts if t[2] == txt], "松手后气泡没有消失"


def test_db_zoom_clamps_span_and_axis_bounds(qapp):
    panel = _panel(qapp)
    plot = panel._plot
    h = float(plot._spectrum_height() - 18)
    plot.mousePressEvent(
        _mouse_mod(QEvent.MouseButtonPress, 10, h / 2.0, Qt.ControlModifier))
    plot.mouseMoveEvent(
        _mouse_mod(QEvent.MouseMove, 10, -5000, Qt.ControlModifier))
    lo, hi = panel.db_range()
    assert hi - lo <= 160.0
    assert lo >= -160.0 and hi <= 0.0
    plot.mouseMoveEvent(
        _mouse_mod(QEvent.MouseMove, 10, 5000, Qt.ControlModifier))
    lo, hi = panel.db_range()
    assert hi - lo >= 10.0
    assert lo >= -160.0 and hi <= 0.0
    _assert_single_source(panel)


# ---------------------------------------------------------------------------
# 6. 瀑布与频谱共用同一 x→频率映射（含 Ctrl 平移偏移）
# ---------------------------------------------------------------------------
def _wf_panel_with_bin(qapp, bin_idx=256, offset_hz=0.0):
    panel = _panel(qapp)
    gen = panel.generator
    gen.center_freq_hz = 100e6
    gen.sample_rate_hz = 2.4e6
    s = np.full(gen.num_bins, -100.0, dtype=np.float32)
    s[bin_idx] = -20.0
    gen.spectrum = s
    gen._has_real_data = True
    gen.wf_rows_written = 10
    panel._state.view_offset_hz = float(offset_hz)
    return panel


def test_counterproof_legacy_waterfall_mapping_ignores_view_offset(qapp):
    """旧画布 x→bin 是 linspace，不含 view_offset；Ctrl 平移 300 kHz 后，
    同一 x 在频谱与瀑布上相差整整 300 kHz。"""
    panel = _wf_panel_with_bin(qapp, offset_hz=300e3)
    gen = panel.generator
    x = panel._plot.width() * 0.35
    legacy_hz = float(gen.bin_to_freq(
        int(round(x / panel._plot.width() * (gen.num_bins - 1)))))
    spec_hz = panel._plot._freq_at_x(x)
    assert abs(spec_hz - legacy_hz) == pytest.approx(300e3, abs=5e3), \
        f"反证场景不成立：频谱 {spec_hz/1e6}MHz / 旧瀑布 {legacy_hz/1e6}MHz"


def test_waterfall_and_spectrum_map_same_x_to_same_freq(qapp):
    """同一 x 像素在两区必须映射到同一频率；Ctrl 平移后依然一致。"""
    panel = _wf_panel_with_bin(qapp)
    plot = panel._plot
    wf = panel._wf_plot
    wf.resize(plot.width(), 200)
    for offset in (0.0, 300e3, -500e3):
        panel._state.view_offset_hz = offset
        for x in (0.0, plot.width() * 0.25, plot.width() * 0.5,
                  plot.width() - 1):
            assert wf.freq_at_x(x) == pytest.approx(
                plot._freq_at_x(x), abs=1.0), \
                f"offset={offset} x={x}: 瀑布 {wf.freq_at_x(x)} ≠ 频谱 {plot._freq_at_x(x)}"


def test_waterfall_canvas_pixel_follows_view_offset(qapp):
    """画布里的 bin 必须落在频谱同一频率的 x 上，而不是固定画在中间。"""
    import spectrum_widget as sw
    panel = _wf_panel_with_bin(qapp, offset_hz=300e3)
    gen = panel.generator
    sw._wf_build_lut(panel._state)
    w = 800
    dst = np.zeros((w, 4), dtype=np.uint8)
    row = np.full(gen.num_bins, -100.0, dtype=np.float32)
    row[256] = -20.0
    sw._wf_paint_row_into(panel._state, row, dst)
    top = panel._state._wf_lut[255]
    cols = np.nonzero(np.all(dst[:, :3] == top, axis=1))[0]
    want = int(round(sw._freq_to_x(
        QRectF(0, 0, w, 1), gen.center_freq_hz + 300e3,
        gen.sample_rate_hz, gen.bin_to_freq(256))))
    assert cols.size and abs(int(cols[0]) - want) <= 3, \
        f"瀑布 bin 落在 {cols[:3]}px，频谱对应 {want}px"
