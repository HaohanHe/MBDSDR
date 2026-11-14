# SPDX-License-Identifier: MIT
"""S1/S2：扫频的三种终止必须分开说——完成 / 已取消 / 读取失败。

事故（2026-11-13）：
  * S1：``psd_provider`` 把 ``set_frequency`` / ``read_samples`` 的异常吞成
    空数组，全失败时界面报「完成：0 个活动段」。用户以为频段干净，实际
    设备根本没读回来——两种结论对应相反的下一步（换门限 vs 查设备）。
  * S2：用户点取消，代码 ``done.emit([])``，界面同样报「完成：0 个活动段」。

这里用**真实会抛异常的 fake backend**造「设备读失败」，不 mock 返回值。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)  # noqa: F401

import scanner_panel as sp  # noqa: E402
from scanner_panel import ScannerPanel, _ScanWorker, empty_hint_text  # noqa: E402


class _BrokenBackend:
    """真实会抛异常的读失败（模拟 USB 掉线），不是返回 None。"""

    def __init__(self):
        self.reads = 0

    def get_sample_rate(self):
        return 2.048e6

    def set_frequency(self, hz):
        return True

    def read_samples(self, n):
        self.reads += 1
        raise RuntimeError("USB 设备已断开（libusb: -5）")


class _NoisyBackend:
    """能读回噪声的后端：用于验证「取消」不落进「完成」语义。"""

    def get_sample_rate(self):
        return 2.048e6

    def set_frequency(self, hz):
        return True

    def read_samples(self, n):
        rng = np.random.default_rng(0)
        return rng.standard_normal(int(n)) + 1j * rng.standard_normal(int(n))


SEG = {"start_freq": 100e6, "end_freq": 100.2e6, "peak_freq": 100.1e6,
       "peak_db": -30.0, "bandwidth": 200e3, "kind": "WFM",
       "suggested_mode": "WFM", "confidence": 0.7}


@pytest.fixture()
def panel(monkeypatch):
    # _on_failed 会弹 QMessageBox；测试里不许阻塞在模态框上
    monkeypatch.setattr(sp.QMessageBox, "warning", lambda *a, **k: None)
    p = ScannerPanel()
    yield p
    p.close()
    p.deleteLater()


def test_repeat_start_while_scanning_says_so(panel):
    """扫频中再触发开始：不许静默 return，要写出「忽略这一次」。

    实测缺陷（2026-11-14）：`_on_start_clicked` 在 worker 未结束时直接
    return。按钮虽然置灰，程序化调用（工具/快捷键）仍能走到这里，用户
    得不到任何「这次没开始」的回执；同形态的 modulation_panel 会写
    「识别中…，请稍候」。
    """

    class _Running:
        def isRunning(self):
            return True

    panel._worker = _Running()
    try:
        panel._on_start_clicked()
        assert "扫频进行中" in panel.status_label.text()
    finally:
        panel._worker = None


def _wire(panel, worker):
    worker.done.connect(panel._on_done)
    worker.cancelled.connect(panel._on_cancelled)
    worker.failed.connect(panel._on_failed)


# ── S1：全读失败必须报「设备未返回数据（N 次读取失败）」 ─────────────
def test_all_reads_failing_reports_device_error_not_empty_band(panel):
    be = _BrokenBackend()
    panel.set_backend(be)
    panel.set_sdr_connected(True)  # 只有已连接时才会真的开扫
    worker = _ScanWorker(be, 100e6, 101e6, 0.25e6, 6.0,
                         parent=None)
    done, cancelled, failed = [], [], []
    worker.done.connect(lambda segs, n: done.append((segs, n)))
    worker.cancelled.connect(lambda segs, n: cancelled.append((segs, n)))
    worker.failed.connect(lambda msg, n: failed.append((msg, n)))
    _wire(panel, worker)

    worker.run()

    assert not done and not cancelled
    assert failed, "全读失败必须走失败分支，不能发 done([]) 当成「0 个活动段」"
    msg, n = failed[0]
    assert n == worker.read_attempts > 0
    assert "设备未返回数据（%d 次读取失败）" % n in msg
    assert "USB 设备已断开" in msg, "首个错误要能对上真实原因"
    # 面板：状态条与空态都必须是「读失败」，不能出现门限建议
    assert panel.status_label.text() == "设备未返回数据（%d 次读取失败）" % n
    hint = panel.empty_hint.text()
    assert "读取失败" in hint
    assert "没有发现超过门限的活动段" not in hint, "读失败不能说成「频段干净」"
    # 终态不得被 finished 后的 _refresh_state 冲成「就绪」
    panel._refresh_state()
    assert "设备未返回数据" in panel.status_label.text()


def test_empty_hint_distinguishes_read_failure_from_clean_band():
    clean = empty_hint_text(True, True, True)
    broken = empty_hint_text(False, True, True, outcome="failed",
                             read_failures=7)
    assert "没有发现超过门限的活动段" in clean
    assert "读取失败" in broken
    assert "没有发现超过门限的活动段" not in broken
    assert "7" in broken


# ── S2：取消不是完成，已扫到的段要保留 ────────────────────────────
def test_cancel_does_not_report_completed(panel):
    worker = _ScanWorker(_NoisyBackend(), 100e6, 101e6, 0.25e6, 6.0)
    worker.cancel()  # 用户在第一拍之前就点了取消
    done, cancelled = [], []
    worker.done.connect(lambda segs, n: done.append((segs, n)))
    worker.cancelled.connect(lambda segs, n: cancelled.append((segs, n)))
    _wire(panel, worker)

    worker.run()

    assert not done, "取消不得发 done（会让状态条说「扫频完成」）"
    assert cancelled, "取消必须走 cancelled 分支"
    assert "已取消" in panel.status_label.text()
    assert "完成" not in panel.status_label.text()
    assert "已取消" in panel.empty_hint.text(), "空态也不许说「没有发现超过门限」"


def test_cancel_keeps_segments_already_scanned(panel):
    emitted = []
    panel.done.connect(emitted.append)

    panel._on_cancelled([dict(SEG)], 0)

    assert panel.result_list.count() == 1, "取消前已扫到的段要保留在列表里"
    assert "已取消" in panel.status_label.text()
    assert "保留已扫到的 1 个活动段" in panel.status_label.text()
    assert emitted == [], "取消不得向上层发 done（否则状态栏会写「扫频完成」）"
