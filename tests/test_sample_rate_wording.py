# SPDX-License-Identifier: MIT
"""采样率「默认」文案统一回归（无硬件，offscreen）。

缺口 G06：同一块窗口里采样率有三种写法——控制面板直接显示 ``2.400 MS/s``
（像设备正在跑的读数）、模块面板写「未知（默认 2.400 MS/s）」、设备面板预填
``1.024 MS/s``。用户无法判断哪个是"设备真值"、哪个只是"没确认过的默认"。

统一口径：未连接时一律标「默认 · 未确认」（``constants.DEFAULT_SR_UNCONFIRMED``）；
设备面板的 1.024 是**性能档预填**，标「性能档默认 · 未确认」并说明不是设备读数。

反证方式：删掉 ``_apply_sample_rate_unconfirmed_marker`` 的调用或恢复旧的
``未知（默认 …）`` 文案，本文件即红。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

TOKEN = "默认 · 未确认"


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.mark.usefixtures("isolated_profile", "qtapp")
class TestSampleRateWording:
    def test_shared_constant_is_single_wording(self):
        from constants import DEFAULT_SR_UNCONFIRMED, DEFAULT_SAMPLE_RATE_HZ
        from constants import format_sample_rate
        assert DEFAULT_SR_UNCONFIRMED == (
            f"{format_sample_rate(DEFAULT_SAMPLE_RATE_HZ)}（{TOKEN}）")

    def test_control_panel_marks_default_unconfirmed_until_connected(self, qtapp):
        from control_panel import ControlPanel
        panel = ControlPanel()
        try:
            text = panel.sample_rate_combo.currentText()
            assert TOKEN in text, "未连接时采样率不得裸显具体值"
            assert text.startswith("2.400 MS/s")
            panel.set_sdr_connected(True)
            assert panel.sample_rate_combo.currentText() == "2.400 MS/s", \
                "连上后必须去掉「未确认」标记"
            panel.set_sdr_connected(False)
            assert TOKEN in panel.sample_rate_combo.currentText()
        finally:
            panel.deleteLater()

    def test_module_panel_unknown_item_uses_same_wording(self, qtapp):
        from module_panel import ModulePanel
        panel = ModulePanel()
        try:
            panel._fill_sample_rate_options(None)
            assert panel.sr_combo.currentText() == "档位未知，连接设备后探测"
            assert "MS/s" not in panel.sr_combo.currentText(), \
                "未知档位不得夹带 MS/s 数字（会被读成设备能跑的速率）"
            assert panel.sr_combo.currentData() is None, "未知档不得塞假数据"
            assert panel.sr_combo.isEnabled() is False
        finally:
            panel.deleteLater()

    def test_device_panel_labels_perf_default_as_intent(self, qtapp):
        from device_panel import DevicePanel, PERF_DEFAULT_SR
        from constants import format_sample_rate
        panel = DevicePanel()
        try:
            assert panel.sr_equiv.text() == (
                f"= {format_sample_rate(PERF_DEFAULT_SR)}（性能档默认 · 未确认）")
            assert "不是设备读数" in panel.sr_edit.toolTip()
            panel.set_connected(True, "测试设备")
            assert panel.sr_equiv.text() == (
                f"= {format_sample_rate(PERF_DEFAULT_SR)}"), \
                "连上后不得再标默认/未确认"
        finally:
            panel.deleteLater()
