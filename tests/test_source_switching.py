# SPDX-License-Identifier: MIT
"""多源在册与切换：单活跃、旧源释放、并发/冲突明确报错（无硬件）。"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from mbdsdr_ai.sdr_backend import SDRBackendManager


class _StubDevice:
    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.type = "stub"


class _StubStatus:
    def __init__(self) -> None:
        self.connected = False
        self.error = ""


class _StubBackend:
    """带真实生产者线程的替身，用来验证切换后线程不残留。"""

    def __init__(self, device_id: str, fail_connect: bool = False,
                 raise_on_disconnect: bool = False,
                 leak_handle: bool = False, connect_delay: float = 0.0):
        self.device = _StubDevice(device_id)
        self.status = _StubStatus()
        self._fail_connect = fail_connect
        self._raise_on_disconnect = raise_on_disconnect
        self._leak_handle = leak_handle
        self._connect_delay = connect_delay
        self._ring_reader = None
        self._sdr = None
        self._stop = threading.Event()
        self._thread = None

    def connect(self) -> bool:
        if self._connect_delay:
            time.sleep(self._connect_delay)
        if self._fail_connect:
            self.status.error = "连接失败: 故意失败"
            return False
        self._stop.clear()
        self._ring_reader = SimpleNamespace(
            stop=lambda: self._stop.set())
        self._thread = threading.Thread(
            target=self._stop.wait, name="mbdsdr-ring-producer", daemon=True)
        self._thread.start()
        self.status.connected = True
        self.status.error = ""
        return True

    def disconnect(self) -> None:
        if self._raise_on_disconnect:
            raise RuntimeError("device busy: 句柄无法释放")
        if not self._leak_handle:
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=1.0)
            self._thread = None
            self._ring_reader = None
        self.status.connected = False

    def readback_hw_state(self) -> None:
        return None


def _manager_with(*backends: _StubBackend) -> SDRBackendManager:
    mgr = SDRBackendManager()
    mgr.backends = {b.device.device_id: b for b in backends}
    mgr.active_backend = None
    return mgr


def _producer_alive() -> bool:
    return any(t.name == "mbdsdr-ring-producer" and t.is_alive()
               for t in threading.enumerate())


def test_three_sources_registered_but_only_one_active():
    a, b, c = (_StubBackend("usb"), _StubBackend("rtl_tcp"),
               _StubBackend("spyserver"))
    mgr = _manager_with(a, b, c)
    assert mgr.switch_device("usb") is True
    snap = mgr.get_source_status()
    assert snap["active"] == "usb"
    assert snap["generation"] == 1
    assert [s["device_id"] for s in snap["sources"]] == [
        "usb", "rtl_tcp", "spyserver"]
    assert sum(1 for s in snap["sources"] if s["active"]) == 1
    assert a.status.connected and not b.status.connected
    mgr.disconnect()
    assert mgr.active_backend is None
    assert not _producer_alive()


def test_switch_releases_old_handle_and_thread():
    a, b = _StubBackend("usb"), _StubBackend("rtl_tcp")
    mgr = _manager_with(a, b)
    assert mgr.switch_device("usb") is True
    assert a._ring_reader is not None
    assert mgr.switch_device("rtl_tcp") is True
    assert mgr.active_backend is b
    assert b.status.connected
    assert a.status.connected is False
    assert a._ring_reader is None
    assert a._thread is None
    assert mgr.switch_generation == 2
    # 仅允许一个生产者线程存活
    alive = [t for t in threading.enumerate()
             if t.name == "mbdsdr-ring-producer" and t.is_alive()]
    assert len(alive) == 1
    mgr.disconnect()


def test_reconnecting_same_source_is_idempotent():
    a = _StubBackend("usb")
    mgr = _manager_with(a)
    assert mgr.switch_device("usb") is True
    gen = mgr.switch_generation
    assert mgr.connect("usb") is True
    assert mgr.switch_generation == gen
    assert a.status.connected
    mgr.disconnect()


def test_unknown_source_is_explicit_error():
    mgr = _manager_with(_StubBackend("usb"))
    assert mgr.switch_device("does-not-exist") is False
    assert "未知设备" in mgr.last_conflict
    assert mgr.active_backend is None


def test_failed_activation_reports_reason_and_leaves_no_active():
    a, bad = _StubBackend("usb"), _StubBackend("bad", fail_connect=True)
    mgr = _manager_with(a, bad)
    assert mgr.switch_device("usb") is True
    assert mgr.switch_device("bad") is False
    assert mgr.active_backend is None
    assert "故意失败" in mgr.last_conflict
    assert a.status.connected is False
    assert not _producer_alive()


def test_old_source_release_failure_aborts_switch_explicitly():
    a = _StubBackend("usb", raise_on_disconnect=True)
    b = _StubBackend("rtl_tcp")
    mgr = _manager_with(a, b)
    assert mgr.switch_device("usb") is True
    assert mgr.switch_device("rtl_tcp") is False
    assert mgr.active_backend is None
    assert "未完全释放" in mgr.last_conflict
    # 不允许出现两路同时连接
    assert not b.status.connected


def test_leaked_handle_blocks_switch_instead_of_silent_double_open():
    a = _StubBackend("usb", leak_handle=True)
    b = _StubBackend("rtl_tcp")
    mgr = _manager_with(a, b)
    assert mgr.switch_device("usb") is True
    assert mgr.switch_device("rtl_tcp") is False
    assert mgr.active_backend is None
    assert "未完全释放" in mgr.last_conflict
    assert not b.status.connected


def test_switch_is_serialized_while_in_progress():
    slow = _StubBackend("slow", connect_delay=0.3)
    other = _StubBackend("other")
    mgr = _manager_with(slow, other)

    def run() -> None:
        assert mgr.switch_device("slow") is True

    t = threading.Thread(target=run)
    t.start()
    deadline = time.time() + 0.2
    while time.time() < deadline and not mgr.get_source_status()["switching"]:
        time.sleep(0.01)
    assert mgr.get_source_status()["switching"] is True
    t.join(timeout=2.0)
    assert mgr.get_source_status()["switching"] is False
    assert mgr.get_source_status()["active"] == "slow"
    assert mgr.switch_generation == 1
    mgr.disconnect()
