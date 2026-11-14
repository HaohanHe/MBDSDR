# SPDX-License-Identifier: MIT
"""12.1 状态面板 FEC 纠错量一行 —— 判据（offscreen）。

钉三件事：
  1. 初始空态是「—」，不是 0；
  2. 有统计时按「纠错 n/N 帧 / 最大 m 符号 / 不可纠 k 帧」落一行；
  3. 主窗口只把带 ``rs_*`` 键的真录链 meta 送进来，别的链路不动这一行。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_NO_HW", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtWidgets import QApplication  # noqa: E402

from desktop.status_panel import StatusPanel, format_fec_quality  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

_STATS = {
    "rs_frames": 5,
    "rs_corrected_frames": 3,
    "rs_max_errors": 7,
    "rs_uncorrectable_frames": 2,
    "rs_mean_errors": 3.5,
}


class TestFormatFecQuality:
    def test_none_and_garbage_are_empty(self):
        assert format_fec_quality(None) == "—"
        assert format_fec_quality({}) == "—"
        assert format_fec_quality("x") == "—"

    def test_zero_frames_is_empty_not_zero_correction(self):
        """没有帧时不能显示「纠错 0/0 帧」——那是把空态伪装成好信号。"""
        assert format_fec_quality({"rs_frames": 0}) == "—"

    def test_aggregate_line(self):
        assert format_fec_quality(_STATS) == (
            "纠错 3/5 帧 / 最大 7 符号 / 不可纠 2 帧")

    def test_null_max_renders_dash(self):
        txt = format_fec_quality({
            "rs_frames": 2, "rs_corrected_frames": 0,
            "rs_max_errors": None, "rs_uncorrectable_frames": 2,
        })
        assert "最大 — 符号" in txt and "不可纠 2 帧" in txt


class TestPanelLanding:
    def test_initial_state_is_empty(self):
        panel = StatusPanel()
        assert panel.fec_value.text() == "—"
        assert panel.fec_last is None

    def test_setter_lands_on_label(self):
        panel = StatusPanel()
        panel.set_fec_quality(_STATS)
        assert panel.fec_value.text() == (
            "纠错 3/5 帧 / 最大 7 符号 / 不可纠 2 帧")
        assert panel.fec_last == _STATS

    def test_setter_empty_clears(self):
        panel = StatusPanel()
        panel.set_fec_quality(_STATS)
        panel.set_fec_quality(None)
        assert panel.fec_value.text() == "—"
        assert panel.fec_last is None


class TestMainWindowRouting:
    def test_rs_meta_reaches_panel(self):
        import main_window as mw

        class _Stub:
            status_panel = StatusPanel()

        mw.MainWindow._on_weather_decode_result(
            _Stub(), {"ok": True, "meta": dict(_STATS)})
        assert _Stub.status_panel.fec_value.text() == (
            "纠错 3/5 帧 / 最大 7 符号 / 不可纠 2 帧")

    def test_non_lrpt_meta_leaves_row_untouched(self):
        import main_window as mw

        class _Stub:
            status_panel = StatusPanel()

        before = _Stub.status_panel.fec_value.text()
        mw.MainWindow._on_weather_decode_result(
            _Stub(), {"ok": True, "meta": {"n_files": 1}})
        assert _Stub.status_panel.fec_value.text() == before
