# SPDX-License-Identifier: MIT
"""P0-17 反证：设备 connect 期间 GUI 事件循环必须还在转（无硬件，offscreen）。

事故形态：原生 ``connect()`` 本机实测 2.0 s（USB 打开 + 调谐器探测），原先在
GUI 线程直接调用、只在前面 ``processEvents()`` 冲一次事件循环 —— 这 2 s 里
窗口不重绘、「连接中」标签停在旧值，用户看到的就是「点了没反应」。

反证方式：假装后端 connect 要 400 ms，期间每 50 ms 的定时器必须照常触发。
改前（阻塞式 connect）在 400 ms 内一次都打不进来；改后应能打到 4 次以上。
"""
import os
import sys
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402


class _SlowDevice:
    name = "SlowStick"
    max_gain = 49.0


class _SlowBackend:
    """connect 故意睡 400 ms；其余接口给主窗口最小可用的桩。"""

    def __init__(self, delay_s: float = 0.4):
        self.device = _SlowDevice()
        self.status = SimpleNamespace(error="", gain_db=20.0, demod_mode="FM")
        self.connected = False
        self.delay_s = delay_s
        self.sr = 2.048e6
        self.freq = 98.0e6

    def connect(self):
        time.sleep(self.delay_s)
        self.connected = True
        return True

    def disconnect(self):
        self.connected = False

    def get_status(self):
        return SimpleNamespace(connected=self.connected, error=self.status.error,
                               gain_db=self.status.gain_db,
                               demod_mode=self.status.demod_mode)

    def get_sample_rate(self):
        return self.sr

    def set_sample_rate(self, rate):
        self.sr = float(rate)

    def get_frequency(self):
        return self.freq

    def set_frequency(self, freq):
        self.freq = float(freq)

    def set_gain(self, _g):
        pass

    def set_ppm(self, _p):
        pass

    def set_agc(self, _b):
        pass

    def set_offset_tuning(self, _b):
        pass

    def set_bandwidth(self, _b):
        pass

    def read_samples(self, _n):
        return None


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def window(isolated_profile, qtapp):
    import main_window as mw
    w = mw.MainWindow()
    yield w
    try:
        w._teardown_connection()
    except Exception:  # noqa: BLE001 - 收尾失败不影响断言结论
        pass


def test_event_loop_keeps_running_during_connect(window):
    from PySide6.QtCore import QTimer
    from PySide6.QtTest import QTest

    window._teardown_connection()
    QTest.qWait(20)

    ticks = []
    timer = QTimer()
    timer.setInterval(50)
    timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
    timer.start()
    started = time.perf_counter()
    try:
        ok = window._connect_backend(_SlowBackend(0.4))
    finally:
        timer.stop()
    elapsed = time.perf_counter() - started

    assert ok is True, "桩后端 connect 返回 True，主窗口不应判失败"
    assert elapsed >= 0.39, f"等待时长异常（桩只睡 0.4 s）：{elapsed:.3f}s"
    assert len(ticks) >= 4, (
        f"connect 期间事件循环被堵住：0.4 s 内只跑了 {len(ticks)} 次 50 ms 定时器"
        f"（改前为 0–1 次，改后应 ≥4 次）")
