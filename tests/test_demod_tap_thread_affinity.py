# SPDX-License-Identifier: MIT
"""解调 tap 线程归属：worker 线程不许直接写 Qt 控件。

`pipe._demod1.audio_tap = MainWindow._tap_demod_audio` 是 DemodWorker 线程
里的**同步回调**（不是 signal），里面却调了 APRS 表格刷新、ANR 标签、
云图进度条——全是 QWidget 写操作，随机崩溃/渲染错乱。现在三处都走
Qt Signal 的 AutoConnection：同线程保持同步（测试/外部后端不被打破），
跨线程自动排队回 GUI 线程。
"""
import os
import sys
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _in_worker(fn):
    """在真正的非 GUI 线程里跑 fn，并把它抛出的异常带回主线程。"""
    box = {}

    def run():
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001
            box["exc"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "worker 卡住"
    if "exc" in box:
        raise box["exc"]


class TestAprsPacketAffinity:
    def test_worker_packet_is_queued_not_applied_inline(self, qapp):
        from aprs_panel import AprsPanel
        p = AprsPanel()
        pkt = {"type": "message", "raw": "hello"}
        _in_worker(lambda: p.submit_aprs_dict(pkt))
        assert p._packets == [], "worker 线程里不许直接改表格/包列表"
        qapp.processEvents()
        assert len(p._packets) == 1 and p._packets[0]["raw"] == "hello"
        p.deleteLater()

    def test_same_thread_call_stays_synchronous(self, qapp):
        from aprs_panel import AprsPanel
        p = AprsPanel()
        p.submit_aprs_dict({"type": "message", "raw": "sync"})
        assert len(p._packets) == 1, "同线程 AutoConnection 必须同步，测试/后端不受影响"
        p.deleteLater()


class TestAnrSnrAffinity:
    def test_worker_snr_does_not_touch_label(self, qapp):
        from anr_panel import AnrPanel
        p = AnrPanel()
        p._connected = True
        before = p.snr_label.text()
        _in_worker(lambda: p.set_snr_improvement(3.5))
        assert p.snr_label.text() == before, "worker 线程不许 setText"
        qapp.processEvents()
        assert "3.5" in p.snr_label.text()
        p.deleteLater()

    def test_same_thread_snr_is_immediate(self, qapp):
        from anr_panel import AnrPanel
        p = AnrPanel()
        p._connected = True
        p.set_snr_improvement(2.0)
        assert "2.0" in p.snr_label.text()
        p.deleteLater()


class TestSatImageProgressAffinity:
    def test_worker_progress_is_queued(self, qapp):
        from satellite_image_panel import SatelliteImagePanel
        p = SatelliteImagePanel()
        p._running = True
        before = p.progress.value()
        audio = np.zeros(240_000, dtype=np.float64)   # 240s @1000 Hz → 100%
        _in_worker(lambda: p.feed_audio(audio, 1000.0))
        assert p.progress.value() == before, "worker 线程不许 setValue"
        qapp.processEvents()
        assert p.progress.value() == 100
        p.deleteLater()
