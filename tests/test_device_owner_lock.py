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


def _seed(path, device_id: str, pid: int, proc_start, owner: str = "desktop",
          aliases=None) -> None:
    """直接写登记文件，模拟「另一个进程」留下的占用记录。"""
    rec = {
        "owner": owner, "pid": pid, "proc_start": proc_start,
        "device_id": device_id, "claimed_at": time.time(),
    }
    if aliases:
        rec["aliases"] = list(aliases)
    path.write_text(json.dumps({
        "version": 1,
        "owners": {device_id: rec},
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
        text = sb.owner_conflict_text(holder)
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


# ------------------------------------------ 判活：没权限查 ≠ 进程不存在
def test_open_process_error_classification():
    """只有 ERROR_INVALID_PARAMETER(87) 说明「进程不存在」。

    ACCESS_DENIED(5) 是「没权限查」——低完整性进程查高完整性持有者就是这一种;
    其余失败码同样「查不到」。把这些判死会清掉活着的持有者记录。
    """
    assert sb._open_process_failure_means_dead(87) is True
    for err in (5, 0, None, 6, 87_000):
        assert sb._open_process_failure_means_dead(err) is False, err


@pytest.mark.skipif(os.name != "nt", reason="错误码分流走的是 Windows 探活路径")
def test_access_denied_keeps_live_holder_record(_registry, monkeypatch):
    """反证：没权限查（ACCESS_DENIED）时记录不被清，claim 必须被拒。"""
    _seed(_registry, "rtl_0", 424242, None)
    monkeypatch.setattr(sb, "_open_process_for_query", lambda pid: (0, 5))

    assert sb._proc_probe(424242) == (sb.PROC_UNKNOWN, None)
    assert sb.release_stale_owners() == [], "没权限查不能当成持有者已死"
    assert sb.get_active_owner("rtl_0") is not None
    ok, holder = sb.claim_active_owner(sb.AI_OWNER, "rtl_0")
    assert ok is False, "查不到 ≠ 不存在：不能让第二个进程抢进同一根棒"
    assert holder["pid"] == 424242


@pytest.mark.skipif(os.name != "nt", reason="错误码分流走的是 Windows 探活路径")
def test_invalid_parameter_still_releases_dead_holder(_registry, monkeypatch):
    """反证的另一半（别为了安全把功能锁死）：确证进程不存在时仍必须让位。"""
    _seed(_registry, "rtl_0", 424242, None)
    monkeypatch.setattr(sb, "_open_process_for_query", lambda pid: (0, 87))

    assert sb._proc_probe(424242) == (sb.PROC_DEAD, None)
    ok, _ = sb.claim_active_owner(sb.AI_OWNER, "rtl_0")
    assert ok is True, "ERROR_INVALID_PARAMETER 已证明进程不存在，记录必须清掉"


# ------------------------------------------ 登记表损坏：留证据，不静默丢
def test_corrupt_registry_is_backed_up_with_reason(_registry):
    """损坏 → ``.corrupt-<时间戳>`` + 成对的 ``.reason.txt``（复用统一模板）。"""
    _registry.write_text("{not json", encoding="utf-8")

    assert sb.get_active_owner() is None          # 按空处理，不抛、不阻塞连接
    backups = [p for p in _registry.parent.glob("device_owner.json.corrupt-*")
               if not p.name.endswith(".reason.txt")]
    assert len(backups) == 1, backups
    reason = backups[0].with_name(backups[0].name + ".reason.txt")
    assert reason.exists(), "损坏备份必须与理由文件成对"
    text = reason.read_text(encoding="utf-8").strip()
    assert text, "理由不能为空"
    # 理由文本必须自带登记文件路径：其余四类（配置/书签/布局/AI 记忆）
    # 都有，单独看理由或以后加导出时才能定位到是哪一份文件坏了。
    assert str(_registry) in text, f"理由缺少登记文件路径：{text!r}"
    assert "JSON 读取/解析失败" in text, text


def test_non_object_registry_is_also_treated_as_corrupt(_registry):
    """JSON 合法但顶层不是对象，同样算损坏（不能静默当成「无占用」）。"""
    _registry.write_text("[]", encoding="utf-8")

    assert sb.get_active_owner() is None
    assert list(_registry.parent.glob("device_owner.json.corrupt-*"))


def test_missing_registry_is_not_corruption(_registry):
    """首次运行/用户手动删除 ≠ 损坏：不留备份，否则每次首启都多一个文件。"""
    assert sb.get_active_owner() is None
    assert not list(_registry.parent.glob("*.corrupt-*"))


def test_corrupt_registry_shows_up_in_evidence_scan(_registry):
    """统一模板的验收：备份能被 ``corrupt_evidence`` 清点到（诊断页可见）。"""
    from mbdsdr_ai.corrupt_evidence import scan_corruption_evidence

    _registry.write_text("{not json", encoding="utf-8")
    sb.get_active_owner()

    report = scan_corruption_evidence([str(_registry.parent)])
    assert report["total"] == 1, report
    assert report["recent"], report


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


# ------------------------------------------------ 别名键：设备被占用时读不到序列号
# 实测依据（2026-11-12 真机端到端）：桌面端占着 RTL-SDR 时，AI 进程枚举拿不到
# serial（libusb 打不开设备），只能算出 ``rtlsdr|index:0``；持有者却登记在
# ``rtlsdr|serial:...``。只比对主键 → 漏掉活着的持有者 → AI 直接去 open，
# 拿到 LIBUSB_ERROR_ACCESS。登记必须带上别名，任一命中即同一台设备。
def test_alias_key_finds_live_holder(_registry):
    child = _spawn_child()
    try:
        start = sb._proc_start_time(child.pid)
        _seed(_registry, "rtlsdr|serial:777", child.pid, start,
              aliases=["rtlsdr|index:0"])

        holder = sb.get_active_owner("rtlsdr|index:0")
        assert holder and holder["owner"] == "desktop", "别名键必须能查到持有者"

        ok, holder = sb.claim_active_owner(sb.AI_OWNER, "rtlsdr|index:0")
        assert ok is False, "只算出序号键的竞争者仍必须被拒"
        assert "桌面端" in sb.owner_conflict_text(holder)
    finally:
        _kill(child)


def test_release_by_alias_key(_registry):
    """持有者按序列号登记、释放时只拿到序号键（换进程后重算不出 serial）。"""
    start = sb._self_start()
    _seed(_registry, "rtlsdr|serial:777", os.getpid(), start,
          aliases=["rtlsdr|index:0"])
    assert sb.release_active_owner("desktop", "rtlsdr|index:0") is True
    assert sb.get_active_owner("rtlsdr|serial:777") is None


def test_aliases_include_serial_index_and_label():
    dev = {"driver": "rtlsdr", "serial": "777", "label": "RTL-SDR #0",
           "device_args": {"index": 0}}
    keys = sb.device_identity_aliases(dev)
    assert keys[0] == "rtlsdr|serial:777"
    assert "rtlsdr|index:0" in keys
    assert "rtlsdr|label:RTL-SDR #0" in keys


def test_aliases_include_usb_bus_port_when_available():
    """bus/port 被占用时也读得到（不需要 open），是 serial 读不到时的替代依据。"""
    dev = {"driver": "rtlsdr", "serial": "777", "label": "RTL-SDR #0",
           "bus": 1, "port": 3, "device_args": {"index": 0}}
    keys = sb.device_identity_aliases(dev)
    assert "rtlsdr|bus:1|port:3" in keys


def _seed_many(path, records) -> None:
    """一次写多条占用记录（模拟两台设备同时被别的进程占着）。"""
    path.write_text(json.dumps({
        "version": 1,
        "owners": {dev: rec for dev, rec in records},
    }), encoding="utf-8")


def _live_record(child, device_id, aliases):
    return {"owner": "desktop", "pid": child.pid,
            "proc_start": sb._proc_start_time(child.pid),
            "device_id": device_id, "aliases": list(aliases),
            "claimed_at": time.time()}


def test_ambiguous_alias_refuses_instead_of_guessing(_registry):
    """两条登记共用同一组备用标识 → 必须拒绝，不许取第一条。

    猜错比拒绝坏得多：拒绝只要用户换一台设备，猜错会让两个进程分同一条流。
    """
    child = _spawn_child()
    try:
        aliases = ["rtlsdr|index:0", "rtlsdr|bus:1|port:2"]
        _seed_many(_registry, [
            ("rtlsdr|serial:AAA", _live_record(child, "rtlsdr|serial:AAA", aliases)),
            ("rtlsdr|serial:BBB", _live_record(child, "rtlsdr|serial:BBB", aliases)),
        ])
        ok, holder = sb.claim_active_owner(sb.AI_OWNER, "rtlsdr|index:0")
        assert ok is False
        assert isinstance(holder, sb.AmbiguousOwners)
        assert len(holder.candidates) == 2
        # 它不是一条占用记录：当 holder 用必须当场炸，而不是显示假的
        # 「owner 空、pid 0」。类型上就堵住，不靠文档提醒。
        assert not hasattr(holder, "get")
        with pytest.raises(AttributeError):
            holder.get("owner")
        with pytest.raises((AttributeError, TypeError)):
            holder["owner"]

        text = sb.owner_conflict_text(holder)
        assert "无法唯一确定设备" in text
        assert "AAA" in text and "BBB" in text
        # 拒绝之外必须给出路，不能只说「无法唯一确定」让用户卡死。
        assert "断开其中一台" in text and "拔掉另一根" in text

        # 读侧同样不猜：返回同一类标记（真值），调用方一律按被占用处理。
        seen = sb.get_active_owner("rtlsdr|index:0")
        assert isinstance(seen, sb.AmbiguousOwners)
        assert bool(seen) is True

        # 别名有歧义时也不许释放任何一条（避免放错人）。
        assert sb.release_active_owner("desktop", "rtlsdr|index:0") is False
    finally:
        _kill(child)


def test_serial_identity_survives_usb_port_change(_registry):
    """同一根棒子换 USB 口、serial 仍可读 → 仍是同一台设备（serial 优先）。"""
    child = _spawn_child()
    try:
        old = {"driver": "rtlsdr", "serial": "777", "label": "RTL-SDR #0",
               "bus": 1, "port": 2, "device_args": {"index": 0}}
        moved = dict(old, port=6)
        # 换口不改变身份键本身（bus/port 不进主键）。
        assert sb.device_identity_key(old)[0] == sb.device_identity_key(moved)[0]
        assert "rtlsdr|bus:1|port:6" in sb.device_identity_aliases(moved)

        _seed(_registry, "rtlsdr|serial:777", child.pid,
              sb._proc_start_time(child.pid),
              aliases=sb.device_identity_aliases(old)[1:])

        # serial 可得：只按主键认，换口后仍命中原记录。
        holder = sb.get_active_owner("rtlsdr|serial:777",
                                     allow_alias_match=False)
        assert holder and holder["owner"] == "desktop"

        ok, holder = sb.claim_active_owner(
            sb.AI_OWNER, "rtlsdr|serial:777",
            aliases=sb.device_identity_aliases(moved),
            allow_alias_match=False)
        assert ok is False, "换 USB 口不能把同一台设备变成第二台（否则会双开）"
        assert holder.get("device_id") == "rtlsdr|serial:777"
    finally:
        _kill(child)


def test_stable_identity_does_not_fall_back_to_aliases(_registry):
    """serial 可得时序列号就是身份，别名（序号/标签/端口）一律不参与匹配。

    与上一条互补：上一条证明「换口不换身份」，这一条证明「身份稳定时不去
    翻别名」。别名是给读不到 serial 的竞争者用的替代依据，不是让 stable
    身份绕开序列号去撞别人记录的后门——真被绕过去，两个进程就会分同一条流。
    """
    child = _spawn_child()
    try:
        # 故意让旧记录的别名里出现另一个 stable 键（登记内容异常/旧版本遗留
        # 都可能是这种形状）：stable 查询若回落去翻别名，就会误拦这台设备。
        _seed(_registry, "rtlsdr|serial:777", child.pid,
              sb._proc_start_time(child.pid),
              aliases=["rtlsdr|serial:999", "rtlsdr|index:0"])

        # 对照：默认（允许别名回落）确实会撞上——说明下面那条不是空过。
        assert sb.get_active_owner("rtlsdr|serial:999")["owner"] == "desktop"

        ok, holder = sb.claim_active_owner(
            sb.AI_OWNER, "rtlsdr|serial:999", allow_alias_match=False)
        assert ok is True, "serial 可得时不该靠别名去撞别人的记录"
        assert holder.get("device_id") == "rtlsdr|serial:999"
    finally:
        _kill(child)
