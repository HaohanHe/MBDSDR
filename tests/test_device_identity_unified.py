# SPDX-License-Identifier: MIT
"""设备身份收口：同一台设备换 USB 口，三套系统必须给出「正确但不同」的答案。

这条反证锁定的是**同一物理设备、不同问题**：

* 跨进程占用问「是不是同一台」→ 序列号没变 → 同一台，拒绝第二个进程双开；
* 热插拔问「还在不在这条链路上」→ 端口变了 → 报换口（不断言是另一台设备）；
* 面板问「用户该看到什么」→ 显示「换口了，仍是同一台」，参数跟着序列号走。

三条同时成立才算对。任何一条只靠 `index` 推断，都会退化成「拔 A 停 B」或
「把设备让给另一个进程时让错那一台」。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

_ROOT = __file__.rsplit("tests", 1)[0]
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# --------------------------------------------------------------- 合成枚举条目
def _pluto(port: str, serial: str = "SN-0001") -> dict:
    """Pluto 式：序列号 + libiio USB 端口路径（同一条目里两者都有）。"""
    uri = f"usb:1.{port}"
    return {
        "driver": "plutosdr",
        "label": "Pluto #1",
        "serial": serial,
        "uri": uri,
        "device_args": {"uri": uri},
    }


def _rtl(serial: str = "", index: int = 0, bus: str = "", port: str = "") -> dict:
    dev = {
        "driver": "rtlsdr",
        "source": "rtl_native",
        "label": f"RTL #{index}",
        "serial": serial,
        "device_args": {"index": index},
    }
    if bus and port:
        dev["bus"], dev["port"] = bus, port
    return dev


class _OkProbe:
    """假物理探针：一切以枚举为准，不碰真机。"""

    def probe(self):
        from mbdsdr_ai import usb_hotplug as uh
        return uh.Presence(uh.PROBE_OK, authoritative=True)


@pytest.fixture(autouse=True)
def _registry(tmp_path, monkeypatch):
    """占用登记指到临时文件，并清掉进程级 owner。"""
    import mbdsdr_ai.device_owner as own
    import mbdsdr_ai.sdr_backend as sb
    monkeypatch.setenv(own.OWNER_REGISTRY_ENV, str(tmp_path / "device_owner.json"))
    own._OWNED_HERE.clear()
    own.set_process_owner("desktop")
    yield
    own._OWNED_HERE.clear()
    own.set_process_owner("desktop")


def _seed(path, device_id: str, pid: int, proc_start, owner: str = "desktop",
          aliases=None) -> None:
    """模拟「另一个活进程」留下的占用记录。"""
    rec = {
        "owner": owner, "pid": pid, "proc_start": proc_start,
        "device_id": device_id, "claimed_at": time.time(),
    }
    if aliases:
        rec["aliases"] = list(aliases)
    path.write_text(json.dumps({"version": 1,
                                "owners": {device_id: rec}}), encoding="utf-8")


def _spawn_child() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


def _kill(child: subprocess.Popen) -> None:
    if child.poll() is None:
        child.kill()
    child.wait(timeout=10)


# ------------------------------------------------------------------ 测绘/对照
def test_identity_keys_are_the_same_string_in_every_system():
    """四套系统算出的**稳定键**必须是同一个字符串（否则互斥永远对不上）。"""
    from mbdsdr_ai import device_identity as di
    from mbdsdr_ai import device_owner as own
    from mbdsdr_ai import sdr_backend as sb

    devices = [
        _rtl("A-0001", 0),
        _pluto("2.5"),
        {"driver": "network_iq", "label": "IQ 文件", "uri": "file:/tmp/x.iq",
         "device_args": {}},
        _rtl("", 3),
        {"driver": "rtlsdr", "label": "无序列号无序号", "device_args": {}},
    ]
    got = di.describe_all(devices)
    want = sb.resolve_device_identities(devices)
    assert [(i.key, i.quality) for i in got] == [(k, q) for k, q, _ in want]
    for device, ident in zip(devices, got):
        assert ident.key == sb.device_identity_key(device)[0]


def test_manager_key_is_one_definition_not_a_second_fstring():
    """DeviceManager 的拼写与 canonical 不同，但只能有一处定义。"""
    from mbdsdr_ai import device_identity as di
    from mbdsdr_ai.device_manager import DeviceInfo

    info = DeviceInfo(name="RTL", driver="rtlsdr", serial="A-0001")
    assert info.device_key == di.manager_key("rtlsdr", "A-0001") == "rtlsdr:A-0001"
    fallback = DeviceInfo(name="RTL", driver="rtlsdr", serial="", enum_index=3)
    assert fallback.device_key == di.manager_key("rtlsdr", "", 3) == "rtlsdr|index:3"

    # 拼写不同、指的是同一台：serial 是同一份事实，不是两套身份。
    ident = di.describe(_rtl("A-0001", 0))
    assert ident.key == "rtlsdr|serial:A-0001"
    assert ident.manager_key == "rtlsdr:A-0001"
    assert ident.serial == "A-0001"


def test_usb_uri_is_stable_key_but_port_level_grade():
    """``uri:usb:a.b``：占用当稳定键，热插拔当端口身份——必须同时给出。"""
    from mbdsdr_ai import device_identity as di

    dev = {"driver": "plutosdr", "label": "Pluto", "serial": "",
           "uri": "usb:1.2.5", "device_args": {"uri": "usb:1.2.5"}}
    ident = di.describe(dev)
    assert ident.key == "uri:usb:1.2.5"
    assert ident.quality == di.IDENTITY_STABLE       # 占用：可用作互斥键
    assert ident.grade == di.GRADE_USB_PATH          # 热插拔：端口级
    assert ident.port_key == "1-2.5"
    assert "另一台" in ident.ambiguity                # 换口的后果要写出来


def test_serial_grade_carries_port_when_enumeration_reports_it():
    from mbdsdr_ai import device_identity as di

    with_port = di.describe(_rtl("A-0001", 0, bus="1", port="2.5"))
    assert with_port.grade == di.GRADE_SERIAL
    assert with_port.port_key == "1-2.5"
    # 原生 RTL 枚举不给端口：不许编一个出来。
    assert di.describe(_rtl("A-0001", 0)).port_key == ""


# ------------------------------------------------------- 反证：同一台换 USB 口
def test_same_device_moved_port_three_answers(tmp_path):
    from mbdsdr_ai import device_identity as di
    from mbdsdr_ai import usb_hotplug as uh

    before, after = _pluto("2.5"), _pluto("2.6")
    id_before, id_after = di.describe(before), di.describe(after)
    assert id_before.key == id_after.key                 # 同一台
    assert id_before.port_key != id_after.port_key       # 换了口

    # ① 占用：另一个活进程持有旧口上的同一台，换口后仍拒绝（不许双开）
    import mbdsdr_ai.device_owner as own
    import mbdsdr_ai.sdr_backend as sb
    child = _spawn_child()
    try:
        _seed(tmp_path / "device_owner.json", id_before.key, child.pid,
              own._proc_start_time(child.pid), aliases=list(id_before.aliases))
        backend = sb.build_backend_for_device(dict(after))
        assert backend is not None
        assert backend.hardware_owner_identity()[0] == id_before.key
        assert backend._claim_hardware() is False, "换口被当成另一台 → 双开"
        assert backend.last_claim_conflict
        # 反证：若按端口算身份（旧 watchdog 的做法），同一个登记表会放行
        # 第二个进程——那才是真正的双开。端口键与序列号键必须不同。
        assert id_before.port_key != id_after.port_key
        assert own.claim_active_owner("ai", f"uri:{id_after.port_key or '?'}")[0] is True
    finally:
        _kill(child)

    # ② 热插拔：报「换口」，不报插入/拔出成另一台
    monitor = uh.HotplugMonitor(probe=_OkProbe())
    assert monitor.observe([before]) == []               # 基准拍
    events = monitor.observe([after])
    kinds = [e.kind for e in events]
    assert kinds == [uh.EVENT_UNPLUGGED], kinds
    event = events[0]
    assert event.key == id_before.key
    assert event.port_changed is True and event.verified is True
    assert "1-2.5" in event.user_text and "1-2.6" in event.user_text
    assert "同一台" in event.user_text
    assert id_before.key not in event.user_text          # 不嵌内部键

    # ③ 面板：显示「换口了、仍是同一台」，参数仍挂在同一个键上
    from PySide6.QtWidgets import QApplication
    import desktop.device_panel as dp
    import desktop.connect_request_store as store

    QApplication.instance() or QApplication(sys.argv)

    original = dp.DevicePanel.refresh_devices_async
    dp.DevicePanel.refresh_devices_async = lambda self: None
    panel = dp.DevicePanel()
    try:
        panel._hotplug_probe = _OkProbe()
        panel._apply_devices([before])
        unplugged = []
        panel.device_unplugged.connect(lambda: unplugged.append(1))
        panel.set_connected(True, device=before)
        key = panel._current_dev_key
        assert key == id_before.key
        store.update(key, sample_rate_hz=1_024_000)
        panel._on_hotplug_devices([before], "")          # 基准拍
        assert unplugged == []

        panel._on_hotplug_devices([after], "")           # 同一台，换了口
        assert unplugged == [1], "旧口链路已断，必须断开"
        text = panel.status_label.text()
        assert "另一个 USB 口" in text and "同一台" in text
        # 参数跟着序列号走：键没变，记录还在原来的键下（不是新建一台）。
        assert store.request_for(key).sample_rate_hz == 1_024_000
        assert panel._current_dev_key is None            # 连接已断，但身份没变
    finally:
        panel.close()
        dp.DevicePanel.refresh_devices_async = original


def test_port_change_never_uses_unknown_port_to_guess():
    """枚举不给端口时，序号/名字变化不许被当成换口或拔出。"""
    from mbdsdr_ai import usb_hotplug as uh

    monitor = uh.HotplugMonitor(probe=_OkProbe())
    first = _rtl("A-0001", 0)
    moved = _rtl("A-0001", 5)          # 序号/位置变了，序列号与端口事实未变
    moved["label"] = "RTL 换了个位置"
    assert monitor.observe([first]) == []
    assert monitor.observe([moved]) == []


def test_port_change_of_one_device_never_touches_the_other():
    from mbdsdr_ai import usb_hotplug as uh

    a_before, a_after = _pluto("2.5"), _pluto("2.6")
    b = _rtl("B-0002", 1, bus="1", port="3.1")
    monitor = uh.HotplugMonitor(probe=_OkProbe())
    assert monitor.observe([a_before, b]) == []
    events = monitor.observe([a_after, b])
    assert [e.key for e in events] == ["plutosdr|serial:SN-0001"]
    assert events[0].port_changed is True
    # B 没有任何事件（状态与数据流都不该被邻居换口打断）
    assert all(e.key != "rtlsdr|serial:B-0002" for e in events)
