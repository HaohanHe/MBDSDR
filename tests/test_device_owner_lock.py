# SPDX-License-Identifier: MIT
"""设备占用登记：pid + 进程启动时间判活，失效持有者立即让位。

要锁死的是「方向反过来的那类失效」：桌面端/AI 进程崩溃或被杀后，占用记录
留在登记表里，后来者被永久拒绝，还会收到一句「设备当前由桌面端占用」，
用户怎么点都没用。

判活口径：**pid + 进程启动时间**，两者都对上才算持有者活着（启动时间同时
防 pid 复用）。判占用之前先清失效记录——不靠 TTL，不做「第一次失败、过期
后才成功」。

反证核心：写一条持有者进程已不存在的记录 → 下一次 claim / 开流必须**立即**
成功（< 1s），而不是等 TTL。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

import pytest

import mbdsdr_ai.sdr_backend as sb


@pytest.fixture(autouse=True)
def _registry(tmp_path, monkeypatch):
    path = tmp_path / "device_owner.json"
    monkeypatch.setenv(sb.OWNER_REGISTRY_ENV, str(path))
    sb._OWNED_HERE.clear()
    yield path
    sb._OWNED_HERE.clear()


def _seed(path, device_id: str, pid: int, proc_start, owner: str = "desktop") -> None:
    """直接写登记文件，模拟「另一个进程」留下的占用记录。"""
    path.write_text(json.dumps({
        "version": 1,
        "owners": {device_id: {
            "owner": owner, "pid": pid, "proc_start": proc_start,
            "device_id": device_id, "claimed_at": time.time(),
        }},
    }), encoding="utf-8")


def _spawn_child() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


def _kill(child: subprocess.Popen) -> None:
    if child.poll() is None:
        child.kill()
    child.wait(timeout=10)


# ------------------------------------------------------------ 登记表基本行为
def test_claim_get_release_roundtrip():
    ok, rec = sb.claim_active_owner("desktop", "rtl_0")
    assert ok is True
    assert rec["pid"] == os.getpid()
    assert sb.get_active_owner("rtl_0")["owner"] == "desktop"

    assert sb.release_active_owner("desktop", "rtl_0") is True
    assert sb.get_active_owner("rtl_0") is None


def test_release_does_not_drop_other_owners_record():
    sb.claim_active_owner("desktop", "rtl_0")
    assert sb.release_active_owner(sb.AI_OWNER, "rtl_0") is False
    assert sb.get_active_owner("rtl_0")["owner"] == "desktop"


def test_same_process_can_hand_over_ownership():
    sb.claim_active_owner("desktop", "rtl_0")
    ok, _ = sb.claim_active_owner(sb.AI_OWNER, "rtl_0")
    assert ok is True
    assert sb.get_active_owner("rtl_0")["owner"] == sb.AI_OWNER


def test_exit_hook_releases_this_process_claims():
    sb.claim_active_owner(sb.AI_OWNER, "rtl_0")
    assert sb.get_active_owner("rtl_0") is not None
    sb._release_owned_here_at_exit()
    assert sb.get_active_owner("rtl_0") is None


# ------------------------------------------------------- 判活：死进程必须让位
def test_dead_holder_is_released_immediately_without_ttl(_registry):
    child = _spawn_child()
    try:
        start = sb._proc_start_time(child.pid)
        assert start is not None, "拿不到子进程启动时间，判活口径不成立"
        _seed(_registry, "rtl_0", child.pid, start)
    finally:
        _kill(child)

    t0 = time.monotonic()
    ok, rec = sb.claim_active_owner(sb.AI_OWNER, "rtl_0")
    elapsed = time.monotonic() - t0
    assert ok is True, f"持有者已死仍被拒: {rec}"
    assert elapsed < 1.0, f"不应依赖 TTL 过期（实际等待 {elapsed:.2f}s）"
    assert sb.get_active_owner("rtl_0")["owner"] == sb.AI_OWNER


def test_live_holder_is_refused_then_released_after_death(_registry):
    child = _spawn_child()
    try:
        start = sb._proc_start_time(child.pid)
        assert start is not None
        _seed(_registry, "rtl_0", child.pid, start)

        ok, holder = sb.claim_active_owner(sb.AI_OWNER, "rtl_0")
        assert ok is False
        text = sb._owner_conflict_text(holder)
        assert "桌面端" in text and "断开" in text, text
        assert "pid" in text
    finally:
        _kill(child)

    ok, _ = sb.claim_active_owner(sb.AI_OWNER, "rtl_0")
    assert ok is True, "持有者进程消失后必须立即能占用"


def test_pid_reuse_is_not_mistaken_for_same_holder(_registry):
    # 同一个 pid（本进程）但启动时间对不上：典型 pid 复用，必须判失效
    _seed(_registry, "rtl_0", os.getpid(), (sb._self_start() or 0.0) - 10_000.0)
    ok, rec = sb.claim_active_owner(sb.AI_OWNER, "rtl_0")
    assert ok is True, f"pid 复用被误判为活持有者: {rec}"


# ------------------------------------------------------------ manager 接线
class _StubDevice:
    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.name = device_id
        self.type = "stub"


class _StubStatus:
    def __init__(self) -> None:
        self.connected = False
        self.error = ""


class _StubBackend:
    def __init__(self, device_id: str, connect_ok: bool = True) -> None:
        self.device = _StubDevice(device_id)
        self.status = _StubStatus()
        self._connect_ok = connect_ok
        self.connect_calls = 0

    def connect(self) -> bool:
        self.connect_calls += 1
        if not self._connect_ok:
            self.status.error = "模拟连接失败"
            return False
        self.status.connected = True
        return True

    def disconnect(self) -> None:
        self.status.connected = False

    def readback_hw_state(self) -> None:
        return None


def _manager_with(*backends: _StubBackend) -> sb.SDRBackendManager:
    """不经 _discover（避免真开 USB），只装好切换所需的最小状态。"""
    mgr = sb.SDRBackendManager.__new__(sb.SDRBackendManager)
    mgr.backends = {b.device.device_id: b for b in backends}
    mgr.active_backend = None
    mgr.vfos = {}
    mgr._vfo_counter = 0
    mgr.active_vfo_id = None
    mgr._switch_lock = threading.RLock()
    mgr._switch_in_progress = False
    mgr.switch_generation = 0
    mgr.last_conflict = ""
    mgr.last_connect_notice = ""
    mgr._cfg_store = None
    mgr._discover_lock = threading.Lock()
    mgr._last_discover_ts = time.monotonic()   # TTL 内不重探
    return mgr


def test_open_hardware_claims_and_disconnect_releases():
    mgr = _manager_with(_StubBackend("rtl_0"))
    assert mgr.connect("rtl_0") is True
    owner = sb.get_active_owner("rtl_0")
    assert owner and owner["owner"] == sb.AI_OWNER

    mgr.disconnect()
    assert sb.get_active_owner("rtl_0") is None


def test_connect_refused_when_live_holder_exists(_registry):
    child = _spawn_child()
    try:
        start = sb._proc_start_time(child.pid)
        assert start is not None
        _seed(_registry, "rtl_0", child.pid, start)

        be = _StubBackend("rtl_0")
        mgr = _manager_with(be)
        assert mgr.connect("rtl_0") is False
        assert be.connect_calls == 0, "被占用时不许碰硬件"
        assert "占用" in mgr.last_connect_notice
        assert "桌面端" in mgr.last_connect_notice
    finally:
        _kill(child)

    be2 = _StubBackend("rtl_0")
    mgr2 = _manager_with(be2)
    assert mgr2.connect("rtl_0") is True, "持有者已死，开流必须立即成功"
    assert be2.connect_calls == 1


def test_failed_connect_leaves_no_claim():
    mgr = _manager_with(_StubBackend("rtl_0", connect_ok=False))
    assert mgr.connect("rtl_0") is False
    assert sb.get_active_owner("rtl_0") is None, "失败路径不能留占用记录"


def test_switch_moves_claim_to_new_device():
    a, b = _StubBackend("usb"), _StubBackend("rtl_tcp")
    mgr = _manager_with(a, b)
    assert mgr.connect("usb") is True
    assert mgr.switch_device("rtl_tcp") is True
    assert sb.get_active_owner("usb") is None
    owner = sb.get_active_owner("rtl_tcp")
    assert owner and owner["owner"] == sb.AI_OWNER
    mgr.disconnect()
    assert sb.get_active_owner("rtl_tcp") is None
