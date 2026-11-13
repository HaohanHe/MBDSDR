# SPDX-License-Identifier: MIT
"""模型链路（配 API Key）的工具调用也必须进真实编排器与 Hooks。

用户 2026-11-08：「人工智能编排有没有？包括 Hooks、常驻化」。
本地协商已登记，模型对话这条入口不能另画一套假状态。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_model_round_records_real_tasks_and_events(isolated_profile, qapp):
    from ai_panel import AIPanel
    from mbdsdr_ai import AgentConfig, MBDSDRAgent

    panel = AIPanel()
    panel.agent = MBDSDRAgent(AgentConfig(api_key="test-key-not-used"))
    bridge = panel._bridge_obj()
    assert bridge is not None

    before = bridge.live_state()
    panel._on_ai_finished({
        "content": "已查询设备状态",
        "tool_calls": [{"name": "sdr_status", "arguments": {}}],
        # 真实链路里 tool_results 是 ToolResult.to_dict()，一定带 success；
        # S3 起成败只认这个字段（没有它就不猜，见 tool_success）。
        "tool_results": [{"success": True, "content": "未连接", "error": ""}],
        "compacted": False,
    })
    after = bridge.live_state()

    assert after["tasks"] > before["tasks"], \
        "模型轮次的工具调用必须成为真实编排任务"
    assert after["events"] > before["events"], \
        "模型轮次必须触发真实 Hooks 事件"
    # 2026-11-11：状态条收窄成「模型配置状态 + 工具数」，任务/事件明细
    # 移到可展开区。契约不变：模型轮次仍必须落成真实编排任务与 Hooks 事件。
    panel._refresh_live_bar()
    assert "模型已配置" in panel.live_btn.text()
    assert "工具" in panel.live_btn.text()
    assert "任务" in panel.live_details.text()
    panel.close()


def test_model_round_with_string_arguments_is_not_guessed(
        isolated_profile, qapp):
    from ai_panel import AIPanel
    from mbdsdr_ai import AgentConfig, MBDSDRAgent

    panel = AIPanel()
    panel.agent = MBDSDRAgent(AgentConfig(api_key="test-key-not-used"))
    bridge = panel._bridge_obj()
    before = bridge.live_state()
    panel._on_ai_finished({
        "content": "ok",
        "tool_calls": [{
            "name": "sdr_status",
            "arguments": '{"freq_hz": 98000000}',
        }],
        "tool_results": [{"success": True, "content": "ok", "error": ""}],
        "compacted": False,
    })
    assert bridge.live_state()["tasks"] > before["tasks"]
    panel.close()
