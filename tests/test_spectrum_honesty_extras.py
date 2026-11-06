# SPDX-License-Identifier: MIT
"""
tests/test_spectrum_honesty_extras.py
=====================================
专业模式频谱链上「显示数字必须来自实测」的四处钉子（2026-11-06）：

1. 瀑布「可回看时长」：行是每 IQ 帧写一行（≈146 行/s），与 FFT 重绘定时器
   （50 ms）无关。旧实现用 `max_lines × 定时器间隔` 算出 12.8 s，实际只有
   max_lines/146 ≈ 1.75 s（差 7 倍）。现在必须用**实测行率**，未测到就写「--」。
2. 悬停读数：Ctrl 平移把半屏推到采集带宽外后，旧实现把频率**夹到边缘 bin**
   继续显示一个 dBFS——那是"另一个频率的功率"，比显示空更糟。现在带外返回 None。
3. 性能档：低档原用 512 点，不在 FFT_SIZES(1024/2048/4096) 白名单内；绕校验
   生效后非可编辑下拉 `setCurrentText("512")` 静默无效 → 下拉显示 2048、实际
   512、RBW 又按 512 算。现在档位必须落白名单，且下拉同步要能验证。
4. 瀑布画布缓存键：拖动 dB 量程后 LUT 重建、但已画进画布的历史行仍是旧配色
   → 永久水平色带。键里必须含 db_min/db_max。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_spectrum_honesty_extras.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import spectrum_widget as sw  # noqa: E402
from spectrum_widget import SpectrumDataGenerator, SpectrumPanel  # noqa: E402


def _gen(center=98.5e6, sr=2.4e6, bins=256):
    g = SpectrumDataGenerator(num_bins=bins)
    g.center_freq_hz = center
    g.sample_rate_hz = sr
    return g


# ---------------------------------------------------------------------------
# 1. 瀑布时长 = 实测行率
# ---------------------------------------------------------------------------
class TestWaterfallSpan:
    def test_unknown_rate_reports_none_not_fake_seconds(self):
        """还没测到行率时不编秒数（调用方显示「--」）。"""
        g = _gen()
        assert g.wf_row_rate_hz() == 0.0
        assert g.wf_span_seconds() is None

    def test_span_uses_measured_row_rate(self, monkeypatch):
        """实测 100 行/s、深度 256 行 → 2.56 s（而不是按 50 ms 定时器算的 12.8 s）。"""
        g = _gen()
        g.max_waterfall_lines = 256
        clock = {"t": 1000.0}
        monkeypatch.setattr(sw.time, "monotonic", lambda: clock["t"])
        frame = np.zeros(1024, dtype=complex)
        # 先建立基准点，再在 1 s 内推 100 帧 → 行率 100 Hz
        g.push_iq(frame, 98.5e6, 2.4e6)
        for _ in range(100):
            clock["t"] += 0.01
            g.push_iq(frame, 98.5e6, 2.4e6)
        rate = g.wf_row_rate_hz()
        assert rate == pytest.approx(100.0, rel=0.05), rate
        assert g.wf_span_seconds() == pytest.approx(2.56, rel=0.05)

    def test_span_tracks_depth_change(self, monkeypatch):
        """改深度后时长按同一行率线性变化（不是写死的常数）。"""
        g = _gen()
        clock = {"t": 0.0}
        monkeypatch.setattr(sw.time, "monotonic", lambda: clock["t"])
        frame = np.zeros(1024, dtype=complex)
        g.push_iq(frame, 98.5e6, 2.4e6)
        for _ in range(50):
            clock["t"] += 0.02
            g.push_iq(frame, 98.5e6, 2.4e6)
        rate = g.wf_row_rate_hz()
        assert rate > 0
        g.max_waterfall_lines = 512
        assert g.wf_span_seconds() == pytest.approx(512 / rate, rel=0.01)


# ---------------------------------------------------------------------------
# 2. 悬停读数不得越界夹取
# ---------------------------------------------------------------------------
class TestHoverValueHonesty:
    def test_inside_band_returns_value(self):
        g = _gen(center=98.5e6, sr=2.4e6, bins=256)
        g.spectrum = np.full(256, -55.0, dtype=np.float32)
        g._has_real_data = True
        assert g.value_at_freq(98.5e6) == pytest.approx(-55.0)

    def test_outside_band_returns_none(self):
        """带外频率 → None（旧实现夹到边缘 bin，显示另一个频率的功率）。"""
        g = _gen(center=98.5e6, sr=2.4e6, bins=256)
        g.spectrum = np.full(256, -55.0, dtype=np.float32)
        g._has_real_data = True
        assert g.value_at_freq(98.5e6 + 1.5e6) is None      # 高出带 0.3 MHz
        assert g.value_at_freq(98.5e6 - 3.0e6) is None

    def test_edge_of_band_still_valid(self):
        g = _gen(center=98.5e6, sr=2.4e6, bins=256)
        g.spectrum = np.full(256, -70.0, dtype=np.float32)
        g._has_real_data = True
        assert g.value_at_freq(98.5e6 + 1.2e6) is not None
        assert g.value_at_freq(98.5e6 - 1.2e6) is not None


# ---------------------------------------------------------------------------
# 3. 性能档必须落在 FFT 白名单内，且下拉能同步
# ---------------------------------------------------------------------------
class TestPerformanceLevels:
    def test_all_levels_use_legal_fft_sizes(self):
        for i, (fft, bins, ms) in enumerate(SpectrumPanel._PERF_LEVELS):
            assert fft in SpectrumDataGenerator.FFT_SIZES, (i, fft)
            assert bins > 0 and ms > 0

    def test_low_level_syncs_combo_and_generator(self):
        panel = SpectrumPanel()
        try:
            level = len(WIDGET_LEVELS(panel)) - 1
            panel.set_performance_mode(level)
            want = panel._PERF_LEVELS[level][0]
            assert panel.generator.fft_size == want, panel.generator.fft_size
            assert panel._fft_combo.currentText() == str(want), \
                "下拉与实际 FFT 点数必须一致（不能静默停在旧档）"
        finally:
            panel.deleteLater()


def WIDGET_LEVELS(panel):
    return panel._PERF_LEVELS


# ---------------------------------------------------------------------------
# 4. 瀑布画布缓存键含 dB 量程
# ---------------------------------------------------------------------------
class TestWaterfallMapKeyIncludesDbRange:
    def _fill(self, panel, n=40):
        frame = (np.random.default_rng(0).standard_normal(4096)
                 + 1j * np.random.default_rng(1).standard_normal(4096))
        for _ in range(n):
            panel.generator.push_iq(frame, 98.5e6, 2.4e6)

    def test_db_range_change_invalidates_canvas(self):
        from PySide6.QtGui import QImage, QPainter

        panel = SpectrumPanel()
        try:
            panel.resize(900, 520)
            panel.show()
            self._fill(panel)
            img = QImage(900, 520, QImage.Format_ARGB32)
            p = QPainter(img)
            try:
                st = panel._state
                st.db_min, st.db_max = -100.0, -20.0
                rect = panel._wf_plot.rect()
                sw._draw_waterfall(p, rect, st)
                key_before = getattr(st, "_wf_map_key", None)
                assert key_before is not None
                # 拖 dB 量程：若键里没有量程，画布不会重涂，历史行仍是旧配色
                st.db_min, st.db_max = -120.0, -10.0
                sw._draw_waterfall(p, rect, st)
                key_after = getattr(st, "_wf_map_key", None)
            finally:
                p.end()
            assert key_after != key_before, "dB 量程变化必须让瀑布画布全量重绘"
            assert -120.0 in key_after and -10.0 in key_after
        finally:
            panel.deleteLater()
