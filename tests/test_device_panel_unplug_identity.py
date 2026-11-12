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
    a = _dev(0, driver="fake", label="Fake-A")
    b = _dev(1, driver="fake", label="Fake-B")
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


# ---------------------------------------------------- 跨进程占用登记（连/断）
def _record_owner_calls(monkeypatch, dp, claim_result=(True, {}), ):
    """把占用登记的 claim/release 换成记录器，不碰真实登记文件。"""
    calls = []
    monkeypatch.setattr(
        dp, "_claim_owner",
        lambda owner, dev, aliases=None: (
            calls.append(("claim", owner, dev)) or claim_result))
    monkeypatch.setattr(
        dp, "_release_owner",
        lambda owner, dev: (calls.append(("release", owner, dev)) or True))
    return calls


def test_connect_claims_owner_and_disconnect_releases(panel, monkeypatch):
    """连上必须登记占用；断开必须释放——否则设备被锁到进程退出。"""
    import desktop.device_panel as dp
    calls = _record_owner_calls(monkeypatch, dp)
    panel._apply_devices([_dev(0, serial="A-0001", label="RTL-A")])
    key = panel._identities[0].key
    panel.sr_edit.setText("2048000")
    panel.gain_edit.setText("20")
    panel.ppm_edit.setText("0")
    panel.connect_requested.connect(
        lambda dev: panel.set_connected(True, dev.get("label", ""), dev))

    panel._on_connect_clicked()
    assert ("claim", dp._DESKTOP_OWNER, key) in calls

    panel.set_connected(False)
    assert ("release", dp._DESKTOP_OWNER, key) in calls


def test_claim_conflict_refuses_connect_and_says_who_holds(panel, monkeypatch, caplog):
    """占用登记被别的进程持有时：不发起连接，并把「谁占着」原样说明。"""
    import desktop.device_panel as dp
    holder = {"owner": "mbdsdr-ai", "device_id": "rtlsdr|serial:A-0001", "pid": 4242}
    _record_owner_calls(monkeypatch, dp, claim_result=(False, holder))
    warned = []
    monkeypatch.setattr(dp.QMessageBox, "warning",
                        lambda *args, **kwargs: warned.append(args))
    panel._apply_devices([_dev(0, serial="A-0001", label="RTL-A")])
    panel.sr_edit.setText("2048000")
    panel.gain_edit.setText("20")
    panel.ppm_edit.setText("0")
    requested = []
    panel.connect_requested.connect(requested.append)

    with caplog.at_level("WARNING"):
        panel._on_connect_clicked()

    assert not requested, "占用冲突时必须拒绝发起连接"
    assert "被占用" in panel.status_label.text()
    assert warned, "占用冲突必须弹窗说明，不能只写日志"
    assert "AI" in str(warned[0]) and "4242" in str(warned[0])


def test_failed_connect_releases_claim(panel, monkeypatch):
    """连不上（没人回调 set_connected(True)）时必须立刻释放占用，不许锁死。"""
    import desktop.device_panel as dp
    calls = _record_owner_calls(monkeypatch, dp)
    panel._apply_devices([_dev(0, serial="A-0001", label="RTL-A")])
    key = panel._identities[0].key
    panel.sr_edit.setText("2048000")
    panel.gain_edit.setText("20")
    panel.ppm_edit.setText("0")
    panel.connect_requested.connect(lambda dev: None)   # 主窗口连接失败

    panel._on_connect_clicked()
    assert ("claim", dp._DESKTOP_OWNER, key) in calls
    assert ("release", dp._DESKTOP_OWNER, key) in calls


def test_set_connected_true_registers_owner_without_panel_click(panel, monkeypatch):
    """模块面板发起的连接不经 _on_connect_clicked，set_connected(True) 要补登记。"""
    import desktop.device_panel as dp
    calls = _record_owner_calls(monkeypatch, dp)
    a = _dev(0, serial="A-0001", label="RTL-A")
    panel.set_connected(True, device=a)
    assert ("claim", dp._DESKTOP_OWNER, panel._current_dev_key) in calls


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
