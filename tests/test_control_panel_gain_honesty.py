# SPDX-License-Identifier: MIT
"""P2-C 行为验收：控制面板增益「滑杆=配置意图，标签=实际读数」。

对应 docs/pending-patches/2026-11-06-handoff-p2c-control-panel-gain.md：
未连接一律「—」；启动恢复只进滑杆/tooltip；后端夹值或写入失败时
标签显示真实值并给 ⚠，滑杆仍停在配置意图，配置文件不回写。
端到端（假后端 + settings 不落盘）见
tests/test_main_window_hw_truthfulness.py::test_gain_failure_rolls_back_real_value。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _panel():
    from control_panel import ControlPanel
    return ControlPanel()


def test_unconnected_reading_is_dash(qapp):
    """未连接时没有真实施加，标签必须是「—」，也不接受读数回填。"""
    panel = _panel()
    try:
        assert panel.gain_val.text() == "—"
        assert panel.set_gain_applied(20.0) is False
        assert panel.gain_val.text() == "—", "未连接时不许显示读数"
    finally:
        panel.deleteLater()


def test_restore_goes_to_slider_and_tooltip_only(qapp):
    """启动恢复的 20 dB：滑杆=20，标签=「—」，只在 tooltip 里说明。"""
    panel = _panel()
    try:
        panel.set_gain(20.0)
        assert panel.gain_slider.value() == 20, "配置意图应进滑杆"
        assert panel.gain_val.text() == "—", "恢复值不许写成读数"
        assert "上次配置 20 dB" in panel.gain_val.toolTip()
    finally:
        panel.deleteLater()


def test_clamped_backend_reading_wins_label_intent_stays(qapp):
    """设备夹到 20 dB：标签=20（真实值），滑杆=25（用户意图），并给 ⚠。"""
    panel = _panel()
    try:
        panel.set_sdr_connected(True)
        panel.set_gain(25.0)                       # 配置意图 25
        assert panel.set_gain_applied(20.0) is True  # 设备实际 20
        assert panel.gain_val.text() == "20.0 dB", "标签必须是设备真实值"
        assert panel.gain_slider.value() == 25, "滑杆仍是用户意图，不被回写"
        assert "⚠" in panel.gain_val.toolTip()
        assert "未生效" in panel.gain_val.toolTip()
    finally:
        panel.deleteLater()


def test_disconnect_clears_stale_reading(qapp):
    """断开后旧读数必须清掉，不许让上一个数字冒充当前增益。"""
    panel = _panel()
    try:
        panel.set_sdr_connected(True)
        panel.set_gain_applied(20.0)
        assert panel.gain_val.text() == "20.0 dB"
        panel.set_sdr_connected(False)
        assert panel.gain_val.text() == "—"
        assert panel.set_gain_applied(20.0) is False
    finally:
        panel.deleteLater()


def test_equal_reading_does_not_claim_device_receipt(qapp):
    """读数与请求相等 ≠ 设备回执：tooltip 不许替没回读过的值背书。

    RTL 等后端的 ``get_gain()`` 返回的就是请求值（离散调谐器吸附结果
    没有回读通道）。旧 tooltip「读数来自设备回执」把假值包装成真值，
    是本缺口里最恶劣的一句——这里钉住它不得复活。
    """
    panel = _panel()
    try:
        panel.set_sdr_connected(True)
        panel.set_gain(30.0)
        assert panel.set_gain_applied(30.0) is True
        tip = panel.gain_val.toolTip()
        assert "回执" not in tip, "不得再声称是设备回执"
        assert "未回读" in tip, "必须明确说明读数可能只是请求值"
    finally:
        panel.deleteLater()


def test_mismatch_wording_does_not_call_request_device_actual(qapp):
    """值不等时也只能说「后端报告」，不能断言是设备实测值。"""
    panel = _panel()
    try:
        panel.set_sdr_connected(True)
        panel.set_gain(30.0)
        panel.set_gain_applied(19.2)
        tip = panel.gain_val.toolTip()
        assert "后端报告" in tip
        assert "设备实际" not in tip, "没有回读通道时不得写成设备实际增益"
    finally:
        panel.deleteLater()
