# SPDX-License-Identifier: MIT
"""确定性单测：USB 拔出检测 / 优雅停止 / 不崩溃 / 重新插入。"""
import threading

import numpy as np
import pytest

from mbdsdr_ai.usb_watchdog import USBWatchdog


class FakeUSBDevice:
    """模拟一个可被"物理拔出"的 SDR 后端。"""

    def __init__(self):
        self.present = True
        self.read_calls = 0

    def read_rx(self, n):
        if not self.present:
            raise OSError("LIBUSB_ERROR_NO_DEVICE: device removed")
        self.read_calls += 1
        return np.zeros(n, dtype=np.complex64)


def test_detects_removal_and_graceful_stop():
    dev = FakeUSBDevice()
    removed = threading.Event()
    stops = {"reading": 0, "audio": 0}

    wd = USBWatchdog(
        dev, interval=0.02,
        on_device_removed=removed.set,
        stop_reading=lambda: stops.__setitem__("reading", 1),
        stop_audio=lambda: stops.__setitem__("audio", 1),
    )
    wd.start()
    try:
        assert wd.is_alive is True
        # 拔出
        dev.present = False
        assert removed.wait(2.0), "未检测到设备拔出"
        # 优雅停止被调用
        assert stops["reading"] == 1
        assert stops["audio"] == 1
        assert wd.is_alive is False
    finally:
        wd.stop()


def test_detects_replug():
    dev = FakeUSBDevice()
    removed = threading.Event()
    added = threading.Event()

    wd = USBWatchdog(
        dev, interval=0.02,
        on_device_removed=removed.set,
        on_device_added=added.set,
    )
    wd.start()
    try:
        dev.present = False
        assert removed.wait(2.0)
        dev.present = True
        assert added.wait(2.0), "未检测到设备插回"
        assert wd.is_alive is True
    finally:
        wd.stop()


def test_never_crashes_when_backend_none():
    wd = USBWatchdog(None, interval=0.02)
    # 直接调用 check_alive：None 后端返回 False，不抛
    assert wd.check_alive() is False
    wd.start()
    wd.stop()  # 不崩即可


def test_check_alive_wraps_exceptions():
    class Exploding:
        def read_rx(self, n):
            raise RuntimeError("boom")
    wd = USBWatchdog(Exploding())
    assert wd.check_alive() is False  # 异常被吞，视为拔出


def test_custom_alive_fn():
    state = {"alive": True}
    wd = USBWatchdog(
        object(), interval=0.02,
        alive_fn=lambda b: state["alive"],
    )
    assert wd.check_alive() is True
    state["alive"] = False
    assert wd.check_alive() is False


def test_stop_idempotent():
    wd = USBWatchdog(FakeUSBDevice())
    wd.stop()
    wd.start()
    wd.stop()
    wd.stop()
    assert wd.running is False
