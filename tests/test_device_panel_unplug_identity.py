# SPDX-License-Identifier: MIT
"""设备面板拔出检测：身份必须来自「实际连上的那台设备」。

历史缺陷：``set_connected(True)`` 用**下拉框当前选中项**推断已连接设备，
于是「下拉选 B、实际连 A」会把身份记成 B，「枚举还没回来就连上」会记成
None —— 两种情况 A 拔掉都不会触发 ``device_unplugged``。

本文件锁死三条：
  1. 实际连 A、下拉选 B → A 从枚举消失必须触发；
  2. 枚举为空时连上（带设备身份）→ 消失必须触发；拿不到身份时必须
     **显式拒绝检测**（日志 + tooltip），不许静默当没这回事；
  3. 面板发起的连接以「请求里那台设备」为准，不以下拉框为准。
"""
from __future__ import annotations

import sys

import pytest

_ROOT_DIR = __file__.rsplit("tests", 1)[0]
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)


@pytest.fixture(autouse=True)
def _isolated(isolated_profile):
    yield


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


class _AbsentProbe:
    """假物理探针：枚举里没有的序列号就当作物理不在（不碰真机）。"""

    def probe(self):
        import mbdsdr_ai.usb_hotplug as uh
        return uh.Presence(uh.PROBE_OK, authoritative=True)


class _UnsupportedProbe:
    def probe(self):
        import mbdsdr_ai.usb_hotplug as uh
        return uh.Presence(uh.PROBE_UNSUPPORTED, authoritative=False,
                           detail="测试平台无探针")


def _dev(index: int, serial: str = "", driver: str = "rtlsdr", label: str = "") -> dict:
    return {
        "driver": driver,
        "label": label or f"{driver} #{index}",
        "serial": serial,
        "device_args": {"index": index},
    }


@pytest.fixture
def panel(monkeypatch):
    _app()
    import desktop.device_panel as dp

    # 构造面板会异步枚举外部设备：本用例全部用手喂列表，禁掉真枚举。
    monkeypatch.setattr(dp.DevicePanel, "refresh_devices_async",
                        lambda self: None)
    widget = dp.DevicePanel()
    # 热插拔判定的物理探针换成假的：本文件不依赖真机，也不让测试受
    # 运行机器的 USB 拓扑影响。
    widget._hotplug_probe = _AbsentProbe()
    yield widget
    widget.close()


def _unplug_counter(panel):
    seen = []
    panel.device_unplugged.connect(lambda: seen.append(1))
    return seen


def test_unplug_uses_connected_device_not_combo_selection(panel):
    """下拉选 B、实际连 A：A 从枚举消失必须触发拔出。"""
    a = _dev(0, serial="A-0001", label="RTL-A")
    b = _dev(1, serial="B-0002", label="RTL-B")
    panel._apply_devices([a, b])
    panel.dev_combo.setCurrentIndex(1)          # 下拉选 B
    seen = _unplug_counter(panel)

    panel.set_connected(True, device=a)         # 实际连的是 A

    panel._on_hotplug_devices([b], "")          # 枚举里只剩 B（A 已拔）
    assert seen, "实际连 A 时 A 被拔，必须触发 device_unplugged"


def test_unplug_detected_when_connected_before_enumeration(panel):
    """枚举还没回来就连上：身份键必须有值，设备消失必须触发。"""
    a = _dev(0, serial="A-0001", label="RTL-A")
    assert panel._devices == []
    panel.set_connected(True, device=a)
    assert panel._current_dev_key, "连上时就该有身份键（不依赖枚举）"
    seen = _unplug_counter(panel)

    panel._on_hotplug_devices([], "")           # 之后枚举为空 = 设备已消失
    assert seen, "连上后才枚举/发现设备消失，必须触发 device_unplugged"


def test_unplugging_other_device_does_not_touch_connected_one(panel):
    """两台同类设备：拔掉没在用的那台，正在用的状态与数据流不许中断。"""
    a = _dev(0, serial="A-0001", label="RTL-A")
    b = _dev(1, serial="B-0002", label="RTL-B")
    panel._apply_devices([a, b])
    panel.set_connected(True, device=b)
    panel._on_hotplug_devices([a, b], "")       # 基准
    seen = _unplug_counter(panel)
    key_before = panel._current_dev_key

    panel._on_hotplug_devices([b], "")          # A 拔掉（B 的序号前移到 0）
    assert not seen, "拔掉 A 不许断开正在用的 B"
    assert panel._connected, "B 的连接状态不许被别的设备拔出改动"
    assert panel._current_dev_key == key_before
    assert "已连接" in panel.status_label.text()


def test_enum_failure_is_visible_and_never_means_unplug(panel):
    """监听失败本身要可见；枚举失败不能当成设备已拔出。"""
    a = _dev(0, serial="A-0001", label="RTL-A")
    panel.set_connected(True, device=a)
    seen = _unplug_counter(panel)

    panel._on_hotplug_devices(None, "OSError: USB open timeout")
    assert not seen, "枚举失败不等于拔出"
    assert "轮询失败" in panel.status_label.text()
    assert "USB open timeout" in panel.status_label.toolTip()


def test_probe_unsupported_is_written_out_not_silent(panel):
    """平台没有物理探针时必须明确写出来，不许静默降级成什么都没发生。"""
    panel._hotplug_probe = _UnsupportedProbe()
    panel._on_hotplug_devices([_dev(0, serial="A-0001", label="RTL-A")], "")
    text = panel.status_label.text()
    assert "探针" in text and "枚举" in text, f"平台不支持要显式说明，实际：{text!r}"


def test_unknown_identity_refuses_detection_loudly(panel, caplog):
    """拿不到身份：显式拒绝检测（warning + tooltip），不许静默。"""
    panel._apply_devices([_dev(0, serial="A-0001", label="RTL-A")])
    with caplog.at_level("WARNING"):
        panel.set_connected(True)               # 主窗口没给设备，也没有面板请求
    assert panel._current_dev_key is None
    assert any("无法确认已连接设备的身份" in r.message for r in caplog.records), \
        "拒绝检测必须在日志里说清，不能静默"
    assert "无法确认设备身份" in panel.status_label.toolTip()
    # 拒绝 ≠ 误报：空枚举不能被当成「设备已拔出」去断开用户正在用的连接
    seen = _unplug_counter(panel)
    panel._on_hotplug_devices([], "")
    assert not seen


def test_panel_connect_identity_comes_from_request_not_combo(panel):
    """面板发起的连接：身份取请求里的设备，之后改下拉选中项也不影响。"""
    a = _dev(0, serial="A-0001", driver="fake", label="Fake-A")
    b = _dev(1, serial="B-0002", driver="fake", label="Fake-B")
    panel._apply_devices([a, b])
    panel.dev_combo.setCurrentIndex(0)
    panel.sr_edit.setText("2048000")
    panel.gain_edit.setText("20")
    panel.ppm_edit.setText("0")
    requested = []

    def _fake_connect(dev):
        # 复刻主窗口 _connect_backend 成功后的同步回调：emit 期间连接完成。
        # 同时把下拉改到 B——旧实现会在这一刻读选中项，把身份记成 B。
        requested.append(dev)
        panel.dev_combo.setCurrentIndex(1)
        panel.set_connected(True)

    panel.connect_requested.connect(_fake_connect)

    panel._on_connect_clicked()                 # 请求连 A
    assert requested, "应发出连接请求"
    assert panel._pending_connect_device is None, \
        "emit 返回后 pending 必须已消费（失败则作废）"
    key = panel._current_dev_key
    assert key
    assert "A-0001" in key or "0" in key, "身份应绑定实际连接的 A"

    seen = _unplug_counter(panel)
    panel._on_hotplug_devices([b], "")          # A 消失
    assert seen, "身份应绑定实际连接的 A，而不是后来选中的 B"


# ------------------------------------ 跨进程占用：面板不再自己 claim（唯一判据在后端）
# 2026-11-12 缺陷：claim 放在 UI 入口（本面板）时，另外三条连接路径（连接
# 对话框 / 自动枚举 / 模块面板）不经过它，同一台设备会被打开两次而且不报错。
# 判据已下移到 ``SDRBackend.connect()``；这里锁死「面板不再写登记」——
# 若有人把 claim 加回面板，两层会在改一层时悄悄不一致。
def test_panel_connect_does_not_claim_owner(panel, monkeypatch, tmp_path):
    import mbdsdr_ai.device_owner as own
    monkeypatch.setenv(own.OWNER_REGISTRY_ENV, str(tmp_path / "owner.json"))
    own._OWNED_HERE.clear()

    panel._apply_devices([_dev(0, serial="A-0001", label="RTL-A")])
    key = panel._identities[0].key
    panel.sr_edit.setText("2048000")
    panel.gain_edit.setText("20")
    panel.ppm_edit.setText("0")
    panel.connect_requested.connect(
        lambda dev: panel.set_connected(True, dev.get("label", ""), dev))

    panel._on_connect_clicked()
    assert panel._current_dev_key == key
    assert own.get_active_owner(key) is None, \
        "UI 入口不许再写占用登记（判据只在后端 connect）"

    panel.set_connected(False)
    assert own.get_active_owner(key) is None
    own._OWNED_HERE.clear()


def test_set_connected_true_does_not_claim_owner(panel, monkeypatch, tmp_path):
    """模块面板发起的连接同样由后端登记，面板不补登记。"""
    import mbdsdr_ai.device_owner as own
    monkeypatch.setenv(own.OWNER_REGISTRY_ENV, str(tmp_path / "owner.json"))
    own._OWNED_HERE.clear()

    a = _dev(0, serial="A-0001", label="RTL-A")
    panel.set_connected(True, device=a)
    assert panel._current_dev_key
    assert own.get_active_owner(panel._current_dev_key) is None
    own._OWNED_HERE.clear()


# ------------------------------------ 跨进程占用三态：未知不许显示成空闲
# 旧行为：get_active_owner 读坏登记时返回 None，面板据此当「设备空闲」，
# 用户点连接才被拒——界面在说一件与实际不符的话。现在第三态单独展示，
# 并给出「重试 / 查看诊断」入口。
def test_owner_unreadable_registry_is_shown_as_unknown_not_free(
        panel, monkeypatch, tmp_path):
    import mbdsdr_ai.device_owner as own

    reg = tmp_path / "device_owner.json"
    monkeypatch.setenv(own.OWNER_REGISTRY_ENV, str(reg))
    # 备份失败：坏文件留在原地，claim 必然失败——这正是「说空闲却连不上」的场景。
    monkeypatch.setattr(own, "_backup_corrupt_registry", lambda reason: None)
    reg.write_bytes(b'{"owners": ')

    panel._apply_devices([_dev(0, serial="A-0001", label="RTL-A")])
    assert panel._identities and panel._identities[0].key, \
        "面板应已解析出设备身份"

    text = panel.owner_state_label.text()
    assert "无法读取设备占用登记" in text
    assert "无法判断是否被占用" in text
    assert "空闲" not in text, f"第三态不许说成空闲：{text!r}"
    assert panel.owner_state_label.isHidden() is False
    assert panel.owner_retry_btn.isHidden() is False
    assert panel.owner_diag_btn.isHidden() is False

    # 「重试」真的会重读：登记被修好/移除后，状态回到空闲并收起整行。
    reg.unlink()
    panel.owner_retry_btn.click()
    assert panel.owner_state_label.isHidden() is True


def test_owner_free_state_hides_row(panel, monkeypatch, tmp_path):
    import mbdsdr_ai.device_owner as own

    monkeypatch.setenv(own.OWNER_REGISTRY_ENV, str(tmp_path / "owner.json"))
    panel._apply_devices([_dev(0, serial="A-0001", label="RTL-A")])

    assert panel.owner_state_label.text() == ""
    assert panel.owner_state_label.isHidden() is True
    assert panel.owner_retry_btn.isHidden() is True
    assert panel.owner_diag_btn.isHidden() is True


def test_owner_occupied_state_shows_pid(panel, monkeypatch, tmp_path):
    import mbdsdr_ai.device_owner as own

    monkeypatch.setenv(own.OWNER_REGISTRY_ENV, str(tmp_path / "owner.json"))
    own._OWNED_HERE.clear()
    panel._apply_devices([_dev(0, serial="A-0001", label="RTL-A")])
    key = panel._identities[0].key
    assert own.claim_active_owner("desktop", key)[0] is True

    panel._refresh_owner_state()
    text = panel.owner_state_label.text()
    assert "占用" in text and "pid" in text, text
    assert "空闲" not in text
    # 被占用时应给「重试」（对方可能已释放），但不把人引到诊断。
    assert panel.owner_retry_btn.isHidden() is False
    assert panel.owner_diag_btn.isHidden() is True
    own.release_active_owner("desktop", key)


# ------------------------------------------------ 枚举失败 != 没插设备（W92 patch）
# 同一次枚举失败，module_panel 说「这不等于没插设备」，device_panel 却说
# 「未检测到 SDR 设备」——用户会去拔插 USB，而真实原因可能是依赖缺失或驱动报错。
def test_enum_failure_is_not_reported_as_no_device(panel, monkeypatch):
    import desktop.device_panel as dp

    def boom():
        raise OSError("USB open timeout")

    monkeypatch.setattr(dp, "_ENUM_OK", True)
    monkeypatch.setattr(dp, "enumerate_all_sdr_devices", boom)
    panel.refresh_devices()

    text = panel.status_label.text()
    assert "枚举失败" in text, f"枚举失败必须如实说，实际：{text!r}"
    assert text != dp._NO_DEVICE_STATUS, "枚举失败不许说成「没插设备」"
    assert "USB open timeout" in panel.status_label.toolTip()


def test_empty_enum_still_says_no_device(panel, monkeypatch):
    import desktop.device_panel as dp
    monkeypatch.setattr(dp, "_ENUM_OK", True)
    monkeypatch.setattr(dp, "enumerate_all_sdr_devices", lambda: [])
    panel.refresh_devices()
    assert panel.status_label.text() == dp._NO_DEVICE_STATUS


def test_async_enum_error_is_propagated(panel):
    """后台任务带回的 err 不许丢：丢了就退化成「没插设备」。"""
    import desktop.device_panel as dp
    panel._refresh_token = 7
    panel._on_devices_refreshed(None, "OSError: USB open timeout", 7)
    text = panel.status_label.text()
    assert "枚举失败" in text
    assert text != dp._NO_DEVICE_STATUS


def test_missing_enum_dependency_is_explained(panel, monkeypatch):
    import desktop.device_panel as dp
    monkeypatch.setattr(dp, "_ENUM_OK", False)
    panel.refresh_devices()
    assert "枚举失败" in panel.status_label.text()
    assert "依赖缺失" in panel.status_label.toolTip()
