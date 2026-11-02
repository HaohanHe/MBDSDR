# SPDX-License-Identifier: MIT
"""
tests/test_replay_resume_wiring.py
===================================
验证"回放后实时接收停摆"修复（2026-11-02 SDR 审计 P0）:

  1. 恢复条件不再取旧 UI 线程 _iq_poll_timer 的活动态;
  2. _stop_replay 在后端连着且曾经实时接收时, 真调 _start_iq_streams 恢复;
  3. 后端未连接时不恢复, 频谱标未连接。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_replay_resume_wiring.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from main_window import MainWindow  # noqa: E402


class _SpectrumStub:
    def __init__(self):
        self.connected = None

    def set_connected(self, on):
        self.connected = on


def _mw_with_resume(backend_present):
    w = MainWindow.__new__(MainWindow)
    w._replay_timer = None
    w._replay_iq = None
    w._replay_pos = 0
    w._replay_resume_poll = True
    w._active_sdr_backend = object() if backend_present else None
    w.spectrum = _SpectrumStub()
    w._start_calls = []
    w._start_iq_streams = lambda: w._start_calls.append(1)
    return w


class TestReplayResume:
    def test_resume_condition_is_backend_not_poll_timer(self):
        src = open(os.path.join(_ROOT, "desktop", "main_window.py"),
                   encoding="utf-8").read()
        i = src.index("self._replay_resume_poll = ")
        line = src[i:src.index("\n", i)]
        assert "_active_sdr_backend is not None" in line
        assert "_iq_poll_timer" not in line

    def test_stop_replay_restarts_streams_when_backend_connected(self):
        w = _mw_with_resume(backend_present=True)
        w._stop_replay()
        assert w._start_calls == [1], "回放结束应恢复实时流水线"
        assert w._replay_resume_poll is False

    def test_stop_replay_no_resume_without_backend(self):
        w = _mw_with_resume(backend_present=False)
        w._stop_replay()
        assert w._start_calls == []
        assert w.spectrum.connected is False
