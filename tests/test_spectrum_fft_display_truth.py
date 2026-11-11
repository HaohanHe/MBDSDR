# SPDX-License-Identifier: MIT
"""
tests/test_spectrum_fft_display_truth.py
========================================
「FFT 点数决定计算分辨率，不决定显示分辨率」的界面口径钉子（2026-11-11）。

背景（端到端实测）：选 4096 点后 `generator.fft_size=4096`，但
`_resample_max` 把谱线合并到 `generator.num_bins`（512），绘制层直接
`len(gen.spectrum)`。旧 schema help 写「点数越大，频率分得越细」，
用户会以为屏幕上是 4096 根谱线——这是假话。

守卫：
1. schema help 不得再出现「越大…越细」这类承诺，必须写明计算/显示两种分辨率。
2. 工具栏 FFT 行必须标出「计算 N / 显示 M」，且两个数字都能从文本解析。
3. 标签里的 M 必须等于该帧实际送绘数组长度（不是读控件属性糊弄）。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_spectrum_fft_display_truth.py -v
"""
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import config_schema  # noqa: E402
from spectrum_widget import SpectrumPanel  # noqa: E402

#: 旧文案里的假承诺（出现任意一条即说明又回到「4096 点 = 4096 列」的暗示）。
_FAKE_PROMISES = ("分得越细", "越细越", "细节的平衡点")

_LABEL_RE = re.compile(r"计算\s*(\d+)\s*/\s*显示\s*(\d+)")


def _parse_label(text: str):
    m = _LABEL_RE.search(text)
    assert m, f"FFT 行缺少可解析的「计算 N / 显示 M」：{text!r}"
    return int(m.group(1)), int(m.group(2))


class TestSchemaHelpTellsTruth:
    def test_help_has_two_resolutions(self):
        help_text = config_schema.SCHEMA["fft_size"].help
        assert "计算分辨率" in help_text, help_text
        assert "显示分辨率" in help_text, help_text

    def test_help_drops_fake_promise(self):
        help_text = config_schema.SCHEMA["fft_size"].help
        for phrase in _FAKE_PROMISES:
            assert phrase not in help_text, f"help 仍在承诺「{phrase}」：{help_text}"

    def test_label_no_longer_claims_spectrum_resolution(self):
        """设置页标题不能再把 FFT 点数写成「频谱分辨率」。"""
        label = config_schema.SCHEMA["fft_size"].label
        assert "频谱分辨率" not in label, label
        assert "计算分辨率" in label, label


class TestToolbarShowsComputeVsDisplay:
    def test_label_matches_generator_state(self):
        panel = SpectrumPanel()
        try:
            n, m = _parse_label(panel._fft_bin_label.text())
            assert n == panel.generator.fft_size
            assert m == panel.generator.num_bins
        finally:
            panel.deleteLater()

    def test_label_updates_after_fft_change(self):
        panel = SpectrumPanel()
        try:
            panel._fft_combo.setCurrentText("4096")
            n, m = _parse_label(panel._fft_bin_label.text())
            assert n == 4096
            assert panel.generator.fft_size == 4096
            # 4096 点不代表 4096 列：M 仍是实际送绘根数
            assert m == panel.generator.num_bins
        finally:
            panel.deleteLater()

    def test_display_number_equals_drawn_array_length(self):
        """端到端：真实重算后，标签里的 M == len(generator.spectrum)（送绘数组）。"""
        panel = SpectrumPanel()
        try:
            panel._fft_combo.setCurrentText("4096")
            frame = np.ones(8192, dtype=np.complex64)
            panel.generator.push_iq(frame, 98.5e6, 2.4e6)
            n, m = _parse_label(panel._fft_bin_label.text())
            assert n == 4096
            assert m == len(panel.generator.spectrum), (
                "标签「显示 M」必须等于实际送绘数组长度，"
                f"而不是控件属性：label={m} actual={len(panel.generator.spectrum)}")
        finally:
            panel.deleteLater()


class TestBinsFollowCanvasWidth:
    """num_bins = min(FFT 点数, 画布像素宽, 档位上限)，不再写死 512。"""

    def test_narrow_canvas_gets_fewer_bins_than_wide(self):
        B = SpectrumPanel._bins_for_width
        # 窄：331 px 面板去掉滑杆列 ≈ 299 px 画布（最小窗口实测）
        narrow = B(4096, 299, 2048)
        # 宽：1400 px 面板 ≈ 1368 px 画布
        wide = B(4096, 1400, 2048)
        assert narrow == 256, narrow
        assert wide == 1024, wide
        assert narrow != wide

    def test_perf_cap_is_upper_bound(self):
        B = SpectrumPanel._bins_for_width
        # 省电档上限 256：画布再宽也不超过 256
        assert B(4096, 1400, 256) == 256
        # 均衡档上限 512
        assert B(4096, 1400, 512) == 512
        # FFT 点数本身也是上限
        assert B(1024, 1400, 2048) == 1024

    def test_bins_divide_fft_for_fast_resample(self):
        B = SpectrumPanel._bins_for_width
        for width in (120, 299, 640, 1368, 1920, 3000):
            for fft in (1024, 2048, 4096):
                bins = B(fft, width, 2048)
                assert fft % bins == 0, (fft, width, bins)

    def test_panel_wide_vs_narrow_end_to_end(self):
        """同一个 4096 点设置，宽窗和窄窗送绘长度必须不同；写死 512 会红。"""
        panel = SpectrumPanel()
        try:
            panel.set_performance_mode(0)
            panel._fft_combo.setCurrentText("4096")
            panel.resize(1400, 700)
            panel.show()
            app.processEvents()
            panel._apply_render_bins()
            wide = panel.generator.num_bins
            assert wide == 1024, f"宽画布应送 1024 根，实际 {wide}"

            panel.resize(331, 500)
            app.processEvents()
            panel._apply_render_bins()
            narrow = panel.generator.num_bins
            assert narrow == 256, f"窄画布应送 256 根，实际 {narrow}"
            assert wide != narrow
            assert wide != 512 and narrow != 512, "num_bins 不能被写死回 512"

            panel.generator.push_iq(
                np.ones(4096, dtype=np.complex64), 98.5e6, 2.4e6)
            assert len(panel.generator.spectrum) == narrow
            n, m = _parse_label(panel._fft_bin_label.text())
            assert (n, m) == (4096, narrow)
        finally:
            panel.deleteLater()


class TestLabelStaysTrueAfterMutations:
    """「计算 N / 显示 M」在性能档切换、窗口 resize 后仍 == 实际送绘数组长度。"""

    @staticmethod
    def _push_and_read(panel):
        panel.generator.push_iq(
            np.ones(4096, dtype=np.complex64), 98.5e6, 2.4e6)
        n, m = _parse_label(panel._fft_bin_label.text())
        return n, m, len(panel.generator.spectrum)

    def test_label_after_performance_switch(self):
        panel = SpectrumPanel()
        try:
            panel.resize(1400, 700)
            panel.show()
            app.processEvents()
            panel._fft_combo.setCurrentText("4096")

            panel.set_performance_mode(2)   # 低档：上限 256
            n, m, actual = self._push_and_read(panel)
            assert (n, m, actual) == (4096, 256, 256)

            panel.set_performance_mode(0)   # 高档：上限 2048，宽画布 → 1024
            n, m, actual = self._push_and_read(panel)
            assert (n, m, actual) == (4096, 1024, 1024)
        finally:
            panel.deleteLater()

    def test_label_after_resize(self):
        panel = SpectrumPanel()
        try:
            panel.resize(1400, 700)
            panel.show()
            app.processEvents()
            panel._fft_combo.setCurrentText("4096")
            n, m, actual = self._push_and_read(panel)
            assert (n, m, actual) == (4096, 1024, 1024)

            panel.resize(331, 500)
            app.processEvents()
            panel._apply_render_bins()
            n, m, actual = self._push_and_read(panel)
            assert (n, m, actual) == (4096, 256, 256)

            panel.resize(1400, 700)
            app.processEvents()
            panel._apply_render_bins()
            n, m, actual = self._push_and_read(panel)
            assert (n, m, actual) == (4096, 1024, 1024)
        finally:
            panel.deleteLater()
