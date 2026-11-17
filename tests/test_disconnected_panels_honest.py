# SPDX-License-Identifier: MIT
"""断开态不许说谎：设备面板的枚举文案、增益面板的未连接读数。

离屏走查发现两处「断开态不诚实」：

  1. ``device.png``：下拉框还写「正在枚举设备…」，同一面板的状态行却已经
     写「未检测到设备 · 插上后点『刷新』」——枚举早已结束，两种结论并存。
  2. ``gain.png``：未连接时 LNA/Mixer/VGA 右侧仍显示一个数字（-48 之类的
     滑块下限），总增益却写「未连接」。那个数字不是设备读数。

本文件只钉两件事：
  * 设备面板：枚举中 → 两处同说「正在枚举」；枚举完成无设备 → 「未检测到
    设备」且不含「正在枚举」；枚举失败 → 「枚举失败：<原因>」。
  * 增益面板：未连接 → 三滑块禁用、数值标签「—」、总增益「未连接」；
    连接后 → 滑块可用、标签回到数值。断开 → 再回到「—」。

运行（离屏）：
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_disconnected_panels_honest.py -v
"""
from __future__ import annotations

import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_NUMBER = re.compile(r"-?\d+(?:\.\d+)? dB")


@pytest.fixture(autouse=True)
def _isolated(isolated_profile):
    yield


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


# ---------------------------------------------------------------- 设备面板
@pytest.fixture
def device_panel(monkeypatch):
    """构造设备面板，但不让它真的枚举 USB（结果由用例手喂）。"""
    _app()
    import desktop.device_panel as dp

    monkeypatch.setattr(dp.DevicePanel, "refresh_devices_async",
                        lambda self: None)
    panel = dp.DevicePanel()
    panel._hotplug_timer.stop()
    panel._first_enum_timer.stop()
    yield panel
    panel.close()


def test_initial_state_says_enumerating_everywhere(device_panel):
    """枚举尚未结束时，下拉框与状态行必须说同一句话。"""
    import desktop.device_panel as dp

    assert device_panel.dev_combo.currentText() == dp._ENUMERATING
    assert device_panel.status_label.text() == dp._ENUMERATING


def test_empty_enum_finishes_as_no_device(device_panel, monkeypatch):
    """枚举完成且没有设备：界面写「未检测到设备」，不许残留「正在枚举」。"""
    import desktop.device_panel as dp

    monkeypatch.setattr(dp, "_ENUM_OK", True)
    monkeypatch.setattr(dp, "enumerate_all_sdr_devices", lambda: [])
    device_panel.refresh_devices()

    combo = device_panel.dev_combo.currentText()
    status = device_panel.status_label.text()
    assert combo == "未检测到设备", f"下拉框应写空态事实，实际：{combo!r}"
    assert "未检测到设备" in status, f"状态行应与之一致，实际：{status!r}"
    assert "正在枚举" not in combo
    assert "正在枚举" not in status


def test_enum_failure_keeps_reason_not_enumerating(device_panel, monkeypatch):
    """枚举失败：说「枚举失败：<原因>」，既不是空态也不再是「正在枚举」。"""
    import desktop.device_panel as dp

    def _boom():
        raise OSError("USB open timeout")

    monkeypatch.setattr(dp, "_ENUM_OK", True)
    monkeypatch.setattr(dp, "enumerate_all_sdr_devices", _boom)
    device_panel.refresh_devices()

    status = device_panel.status_label.text()
    combo = device_panel.dev_combo.currentText()
    assert status.startswith("枚举失败："), f"实际：{status!r}"
    assert "USB open timeout" in status
    assert "正在枚举" not in status
    assert "正在枚举" not in combo
    assert combo != "未检测到设备", "枚举失败不等于没插设备"


# ---------------------------------------------------------------- 增益面板
class _FakeDevice:
    """Soapy 风格假设备：三档都在，范围/写入都记账。"""

    def __init__(self):
        self.gains = {"LNA": 18.0, "MIXER": 24.0, "VGA": 30.0}

    def listGains(self, direction, channel):
        return ["LNA", "MIXER", "VGA"]

    def getGainRange(self, direction, channel, name):
        return (0.0, 40.0)

    def setGain(self, direction, channel, value, name):
        self.gains[name] = float(value)
        return True


class _FakeBackend:
    def __init__(self):
        self._sdr = _FakeDevice()


def test_gain_panel_unconnected_shows_dash_not_number():
    """未连接：滑块禁用、标签「—」、总增益「未连接」，不亮任何数字。"""
    _app()
    from gain_panel import GainPanel

    panel = GainPanel()
    try:
        for stage in (panel.lna, panel.mixer, panel.vga):
            assert stage.slider.isEnabled() is False, "未连接时滑块必须禁用"
            assert stage.value_label.text() == "—", (
                f"{stage.logical} 未连接时不许显示数值，"
                f"实际：{stage.value_label.text()!r}")
        assert panel.total_label.text() == "总增益: 未连接"
        assert panel.agc_chk.isEnabled() is False
    finally:
        panel.deleteLater()


def test_gain_panel_connected_restores_values():
    """模拟连接后：三档滑块可用，标签回到数值，总增益为数字。"""
    _app()
    from gain_panel import GainPanel

    panel = GainPanel()
    try:
        panel.set_backend(_FakeBackend())
        for stage in (panel.lna, panel.mixer, panel.vga):
            assert stage.slider.isEnabled() is True, (
                f"{stage.logical} 连接后应可编辑")
            assert _NUMBER.fullmatch(stage.value_label.text()), (
                f"{stage.logical} 连接后应显示数值，"
                f"实际：{stage.value_label.text()!r}")
        assert _NUMBER.search(panel.total_label.text()), (
            f"总增益应为数值，实际：{panel.total_label.text()!r}")
    finally:
        panel.deleteLater()


def test_gain_panel_disconnect_clears_values_again():
    """断开后不许把连接时的数值留在界面上冒充当前读数。"""
    _app()
    from gain_panel import GainPanel

    panel = GainPanel()
    try:
        panel.set_backend(_FakeBackend())
        panel.set_backend(None)
        for stage in (panel.lna, panel.mixer, panel.vga):
            assert stage.slider.isEnabled() is False
            assert stage.value_label.text() == "—"
        assert panel.total_label.text() == "总增益: 未连接"
    finally:
        panel.deleteLater()
