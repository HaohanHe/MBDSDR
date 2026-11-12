# SPDX-License-Identifier: MIT
"""运行期热插拔监听（``mbdsdr_ai/usb_hotplug.py``）的行为级用例。

本文件回答四件事：
  1. 身份为什么不能用枚举序号（带一条把旧口径跑红/跑错的反证）；
  2. 两台同类设备拔掉一台，另一台零事件；
  3. 拔出后插回不会被自动接成另一台（本模块不产出连接动作）；
  4. unplug 与「在位但打不开」、以及三种监听失败各自可归因。

全部用假探针 + 临时目录，**不依赖真机**。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT_DIR = __file__.rsplit("tests", 1)[0]
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from mbdsdr_ai import usb_hotplug as uh  # noqa: E402


# ------------------------------------------------------------------ 工具
def _dev(serial: str = "", index=None, driver: str = "rtlsdr",
         label: str = "", uri: str = "") -> dict:
    device = {"driver": driver, "label": label or f"{driver} #{index}",
              "serial": serial, "device_args": {}}
    if index is not None:
        device["device_args"]["index"] = index
    if uri:
        device["uri"] = uri
    return device


def _presence(serials=(), ports=(), ports_known=False, authoritative=True):
    return uh.Presence(
        uh.PROBE_OK,
        serials=frozenset(s.lower() for s in serials),
        ports=frozenset(ports), ports_known=ports_known,
        authoritative=authoritative)


class _Probe:
    def __init__(self, result):
        self.result = result

    def probe(self):
        return self.result


class _ExplodingProbe:
    def probe(self):
        raise RuntimeError("探针线程被卸载")


def _kinds(events):
    return [e.kind for e in events]


def _legacy_index_identities(devices):
    """反证用：旧口径「按枚举序号识别」，把枚举结果当成权威身份。"""
    out = []
    for device in devices:
        idx = (device.get("device_args") or {}).get("index", -1)
        out.append(uh.HotplugIdentity(
            f"{device.get('driver')}|index:{idx}", uh.GRADE_ENDPOINT, ""))
    return out


# ------------------------------------------------------------------ 身份
def test_identity_prefers_serial():
    ident = uh.resolve_hotplug_identities([_dev("ABC123", index=0)])[0]
    assert ident.key == "rtlsdr|serial:ABC123"
    assert ident.grade == uh.GRADE_SERIAL


def test_uri_usb_is_port_level_not_serial():
    ident = uh.resolve_hotplug_identities(
        [_dev(uri="usb:1.2.5", index=0, driver="plutosdr")])[0]
    assert ident.key == "uri:usb:1.2.5"
    assert ident.grade == uh.GRADE_USB_PATH
    assert "另一台" in ident.note, "换口会变成另一台，必须写进给用户的说明"
    assert uh.usb_port_path(ident.key) == "1-2.5"


def test_index_fallback_is_provisional_and_says_why():
    ident = uh.resolve_hotplug_identities([_dev(index=1)])[0]
    assert ident.grade == uh.GRADE_PROVISIONAL
    assert "序号" in ident.note and "另一台" in ident.note


# ------------------------------------------------------------------ 单台拔出只影响单台
def test_two_identical_devices_unplug_one_leaves_other_silent():
    a = _dev("A-0001", index=0, label="RTL-A")
    b = _dev("B-0002", index=1, label="RTL-B")
    monitor = uh.HotplugMonitor(probe=_Probe(_presence(serials=["B-0002"])))
    assert monitor.observe([a, b]) == [], "首拍只建基准，不许把既有设备报成新插入"

    events = monitor.observe([b])          # A 已拔，B 仍在（且序号前移到 0）
    assert _kinds(events) == [uh.EVENT_UNPLUGGED]
    assert events[0].key == "rtlsdr|serial:A-0001"
    assert events[0].verified is True
    assert all("B-0002" not in e.key for e in events), "B 不许出现在任何事件里"

    # 后续每一拍 B 都必须零事件（状态与数据流不中断的监听侧证据）
    for _ in range(3):
        assert monitor.observe([b]) == []


def test_reverse_proof_index_identity_blames_the_wrong_device():
    """反证：同一场景换成「枚举序号」身份，结论会落到另一台设备上。

    这正是被删 watchdog 的病根：A 在序号 0，B 在序号 1；A 拔掉后 B 前移到
    序号 0，「序号 1 消失」被记成拔出——真正的 A 反倒没被识别。本用例把
    旧口径跑成错误结论，锁死「身份不能是序号」。
    """
    a = _dev("A-0001", index=0, label="RTL-A")
    b = _dev("B-0002", index=1, label="RTL-B")
    monitor = uh.HotplugMonitor(
        probe=_Probe(_presence(serials=["B-0002"])),
        identity_fn=_legacy_index_identities)
    monitor.observe([a, b])

    # A 拔掉后 B 的枚举序号从 1 前移到 0（真实枚举就是这个行为）
    shifted_b = _dev("B-0002", index=0, label="RTL-B")
    events = monitor.observe([shifted_b]) + monitor.observe([shifted_b])
    unplug = [e for e in events if e.kind == uh.EVENT_UNPLUGGED]
    assert len(unplug) == 1
    assert unplug[0].key == "rtlsdr|index:1", "旧口径把「序号 1 消失」当成了拔出"
    assert unplug[0].label == "RTL-B", "结论落在了另一台设备（B）上"
    assert all("A-0001" not in e.key for e in events), "真正的 A 反而没被识别"


def test_replug_same_identity_is_a_plug_event_not_another_device():
    a = _dev("A-0001", index=0, label="RTL-A")
    monitor = uh.HotplugMonitor(probe=_Probe(_presence(serials=[])))
    monitor.observe([a])
    monitor.observe([])
    events = monitor.observe([a])
    assert _kinds(events) == [uh.EVENT_PLUGGED]
    assert events[0].key == "rtlsdr|serial:A-0001"
    assert events[0].verified is True


# ------------------------------------------------------------------ unplug vs 在位打不开
def test_present_device_is_not_reported_as_unplugged():
    a = _dev("A-0001", index=0, label="RTL-A")
    monitor = uh.HotplugMonitor(probe=_Probe(_presence(serials=["A-0001"])))
    monitor.observe([a])

    first = monitor.observe([])
    assert uh.EVENT_UNPLUGGED not in _kinds(first)
    assert uh.EVENT_PRESENT_UNOPENABLE in _kinds(first)

    for _ in range(5):
        assert uh.EVENT_UNPLUGGED not in _kinds(monitor.observe([]))


def test_permission_error_never_claims_unplug():
    a = _dev("A-0001", index=0, label="RTL-A")
    monitor = uh.HotplugMonitor(probe=_Probe(uh.Presence(
        uh.PROBE_PERMISSION, authoritative=False, detail="Access denied")))
    baseline = monitor.observe([a])
    assert uh.EVENT_PROBE_PROBLEM in _kinds(baseline), "权限不足要在启动时就明说"
    problem = [e for e in baseline if e.kind == uh.EVENT_PROBE_PROBLEM][0]
    assert "权限" in problem.user_text

    first = monitor.observe([])
    assert uh.EVENT_DISAPPEARED_UNVERIFIED in _kinds(first)
    assert uh.EVENT_UNPLUGGED not in _kinds(first)

    for _ in range(5):
        assert uh.EVENT_UNPLUGGED not in _kinds(monitor.observe([])), \
            "权限问题不许被误判成设备拔出"


def test_exploding_probe_is_attributed_not_silent():
    a = _dev("A-0001", index=0, label="RTL-A")
    monitor = uh.HotplugMonitor(probe=_ExplodingProbe())
    baseline = monitor.observe([a])
    assert uh.EVENT_PROBE_PROBLEM in _kinds(baseline)
    events = monitor.observe([])
    assert uh.EVENT_UNPLUGGED not in _kinds(events)


# ------------------------------------------------------------------ 监听失败可归因
def test_platform_without_probe_is_explicit_and_falls_back_to_enumeration():
    a = _dev("A-0001", index=0, label="RTL-A")
    monitor = uh.HotplugMonitor(probe=_Probe(uh.Presence(
        uh.PROBE_UNSUPPORTED, authoritative=False, detail="sys.platform=darwin")))
    baseline = monitor.observe([a])
    problem = [e for e in baseline if e.kind == uh.EVENT_PROBE_PROBLEM]
    assert problem, "平台不支持必须在启动时明确写出，不能静默降级"
    assert "没有" in problem[0].user_text and "枚举" in problem[0].user_text

    first = monitor.observe([])
    assert uh.EVENT_UNPLUGGED not in _kinds(first), "无探针时至少连续 2 拍才判定"

    second = monitor.observe([])
    unplug = [e for e in second if e.kind == uh.EVENT_UNPLUGGED]
    assert len(unplug) == 1
    assert unplug[0].verified is False
    assert "枚举" in unplug[0].user_text


@pytest.mark.parametrize("error,expected,word", [
    ("OSError: USB open timeout", uh.REASON_POLL_FAILED, "失败"),
    ("PermissionError: Access denied", uh.REASON_PERMISSION, "权限"),
    ("ImportError: No module named 'SoapySDR'", uh.REASON_UNSUPPORTED, "依赖"),
])
def test_poll_failure_is_classified_and_does_not_lose_snapshot(error, expected, word):
    a = _dev("A-0001", index=0, label="RTL-A")
    monitor = uh.HotplugMonitor(probe=_Probe(_presence(serials=["A-0001"])))
    monitor.observe([a])

    assert uh.classify_poll_error(error) == expected
    events = monitor.observe(None, error)
    assert _kinds(events) == [uh.EVENT_POLL_PROBLEM]
    assert word in events[0].user_text
    assert "已拔出" not in events[0].user_text

    # 失败这一拍不许改快照：下一拍成功，A 仍在，不产生任何设备事件
    rest = [e for e in monitor.observe([a]) if e.kind != uh.EVENT_RECOVERED]
    assert rest == []


# ------------------------------------------------------------------ 歧义与文案
def test_provisional_disappearance_is_ambiguous_not_unplug():
    a = _dev(index=0, label="RTL-A")
    b = _dev(index=1, label="RTL-B")
    monitor = uh.HotplugMonitor(probe=_Probe(_presence()))
    monitor.observe([a, b])
    events = monitor.observe([b])
    assert uh.EVENT_UNPLUGGED not in _kinds(events)
    assert uh.EVENT_AMBIGUOUS in _kinds(events)
    assert "哪一台" in [e for e in events if e.kind == uh.EVENT_AMBIGUOUS][0].user_text


def test_user_text_never_embeds_internal_key():
    a = _dev(uri="usb:1.2.5", index=0, driver="plutosdr", label="Pluto")
    monitor = uh.HotplugMonitor(probe=_Probe(_presence()))
    monitor.observe([a])
    events = monitor.observe([])
    events += monitor.observe([a])
    for event in events:
        assert "uri:" not in event.user_text
        assert "plutosdr|" not in event.user_text


# ------------------------------------------------------------------ 探针离线可验
def test_sysfs_scan_offline(tmp_path: Path):
    dev_dir = tmp_path / "1-2.5"
    dev_dir.mkdir()
    (dev_dir / "serial").write_text("ABC123\n", encoding="utf-8")
    (tmp_path / "1-3").mkdir()              # 无 serial 文件（没有 iSerialNumber）
    (tmp_path / "usb1").mkdir()             # 根 hub，必须跳过

    presence = uh.scan_sysfs_usb(str(tmp_path))
    assert presence.status == uh.PROBE_OK
    assert presence.ports_known is True
    assert presence.ports == frozenset({"1-2.5", "1-3"})
    assert presence.has_serial("abc123")
    assert not presence.has_serial("other")


def test_sysfs_scan_missing_root_is_failure(tmp_path: Path):
    presence = uh.scan_sysfs_usb(str(tmp_path / "nope"))
    assert presence.status == uh.PROBE_FAILED
    assert presence.authoritative is False


@pytest.mark.skipif(not sys.platform.startswith("win"),
                    reason="Windows SetupAPI 探针只在 Windows 上有意义")
def test_windows_setupapi_probe_smoke():
    """不验 SDR，只验 ctypes 签名与调用路径不炸、且能给出可归因结果。"""
    presence = uh.WindowsSetupApiProbe().probe()
    assert presence.status in (uh.PROBE_OK, uh.PROBE_PERMISSION,
                               uh.PROBE_FAILED, uh.PROBE_UNSUPPORTED)
    if presence.status == uh.PROBE_OK:
        assert isinstance(presence.serials, frozenset)
        assert isinstance(presence.ports, frozenset)


# ------------------------------------------------------------------ 与重连解耦
def test_module_has_no_reconnection_dependency():
    source = Path(uh.__file__).read_text(encoding="utf-8").lower()
    assert "reconnection" not in source
    assert "reconnect" not in source
