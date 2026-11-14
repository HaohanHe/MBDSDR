# SPDX-License-Identifier: MIT
"""12.2 状态面板「文件重组」一行 —— 判据（offscreen）。

钉三件事：
  1. 初始空态是「—」，不是「成文 0」；
  2. 有一行把丢帧/断链/丢文件/长度异常/CRC 失败分开列（丢了几次、为什么）；
  3. 主窗口与 GOES 工具结果两条路都能把统计落到这一行。
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

from desktop.status_panel import (  # noqa: E402
    StatusPanel,
    format_reassembly_quality,
)

app = QApplication.instance() or QApplication(sys.argv)

_STATS = {
    "files_ok": 8,
    "vcdus_missing": 3,
    "continuity_breaks": 1,
    "tpdu_crc_fail": 2,
    "session_crc_fail": 1,
    "session_len_mismatch": 0,
    "files_dropped": 2,
}
_LINE = "成文 8 / 丢帧 3（断链 1 次）/ 丢文件 2 / 长度异常 0 / CRC 失败 3"


class TestFormat:
    def test_none_and_garbage_are_empty(self):
        assert format_reassembly_quality(None) == "—"
        assert format_reassembly_quality("x") == "—"
        assert format_reassembly_quality({"files_ok": 1}) == "—"

    def test_line_lists_each_reason(self):
        assert format_reassembly_quality(_STATS) == _LINE

    def test_crc_fail_sums_packet_and_session(self):
        txt = format_reassembly_quality({
            "files_ok": 1, "vcdus_missing": 0, "continuity_breaks": 0,
            "tpdu_crc_fail": 2, "session_crc_fail": 1,
            "session_len_mismatch": 0, "files_dropped": 0,
        })
        assert txt.endswith("CRC 失败 3")


class TestPanelLanding:
    def test_initial_state_is_empty(self):
        panel = StatusPanel()
        assert panel.reassembly_value.text() == "—"
        assert panel.reassembly_last is None

    def test_setter_lands_on_label(self):
        panel = StatusPanel()
        panel.set_reassembly_quality(_STATS)
        assert panel.reassembly_value.text() == _LINE
        assert panel.reassembly_last == _STATS

    def test_setter_empty_clears(self):
        panel = StatusPanel()
        panel.set_reassembly_quality(_STATS)
        panel.set_reassembly_quality(None)
        assert panel.reassembly_value.text() == "—"
        assert panel.reassembly_last is None

    def test_goes_tool_result_routes(self):
        panel = StatusPanel()
        panel.on_tool_result("goes_lrit_parse",
                             {"success": True, "data": {"reassembly": _STATS}})
        assert panel.reassembly_value.text() == _LINE

    def test_non_goes_tool_leaves_row(self):
        panel = StatusPanel()
        before = panel.reassembly_value.text()
        panel.on_tool_result("get_version", {"firmware": "1.0"})
        assert panel.reassembly_value.text() == before


class TestMainWindowRouting:
    def test_reassembly_meta_reaches_panel(self):
        import main_window as mw

        class _Stub:
            status_panel = StatusPanel()

        mw.MainWindow._on_weather_decode_result(
            _Stub(), {"ok": False, "meta": {"reassembly": dict(_STATS)}})
        assert _Stub.status_panel.reassembly_value.text() == _LINE

    def test_goes_failure_meta_without_fec_keeps_fec_row(self):
        import main_window as mw

        class _Stub:
            status_panel = StatusPanel()

        mw.MainWindow._on_weather_decode_result(
            _Stub(), {"ok": False, "meta": {"reassembly": dict(_STATS)}})
        assert _Stub.status_panel.fec_value.text() == "—"
