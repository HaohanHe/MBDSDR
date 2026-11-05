# SPDX-License-Identifier: MIT
"""扫频活动段 → 调谐：峰值频点缺值不得冒充 0 Hz。

旧写法 `float(seg.get("peak_freq", 0.0))` 把「没读到」变成 0 Hz，唯一守卫是
`peak <= 0: return`；一旦守卫被调整，设备就会被真的调到 0 Hz。这里钉住行为：
缺值/0/NaN 一律跳过调谐，带宽缺值也不写 0。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import main_window as mw  # noqa: E402


def test_scanner_segment_without_peak_never_tunes_to_zero():
    """缺峰值频点 → 不调谐、界面显「—」；缺带宽 → 只调频率。"""
    calls = []
    messages = []

    class _StatusBar:
        def showMessage(self, msg, *a, **k):  # noqa: N802
            messages.append(msg)

    class _QMB:
        Yes = 1
        No = 2

        @staticmethod
        def question(*a, **k):
            return _QMB.Yes

    orig_mb = mw.QMessageBox
    win = mw.MainWindow.__new__(mw.MainWindow)
    win._on_tune_sdr = lambda f, m: calls.append(("tune", f, m))
    win._on_vfo_bandwidth_changed_ui = lambda *a, **k: calls.append(("bw", a))
    win.statusBar = lambda: _StatusBar()
    mw.QMessageBox = _QMB
    try:
        mw.MainWindow._on_scanner_segment_chosen(win, {"kind": "FM"})
        assert calls == [], "缺峰值频点不得调谐"
        assert messages and "—" in messages[-1], "缺峰值频点必须显示空态"
        mw.MainWindow._on_scanner_segment_chosen(
            win, {"peak_freq": 0.0, "kind": "FM"})
        assert calls == [], "0 Hz 不是合法调谐目标，不得下发"
        mw.MainWindow._on_scanner_segment_chosen(
            win, {"peak_freq": float("nan"), "kind": "FM"})
        assert calls == [], "NaN 不是可调谐频点"
        mw.MainWindow._on_scanner_segment_chosen(
            win, {"peak_freq": 98_500_000.0, "kind": "FM"})
        assert calls == [("tune", 98_500_000.0, "NFM")], \
            "缺带宽时只调频率，带宽不得写 0"
    finally:
        mw.QMessageBox = orig_mb
