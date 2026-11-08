# SPDX-License-Identifier: MIT
"""本地能力协商层的契约测试。

全部用**真实内核对象**（无 API Key 的 MBDSDRAgent），不 mock 工具结果：
能力来自真实注册表，回执来自真实 ``ToolResult``，任务来自真实编排器，
事件来自真实 Hooks 历史。这里钉的是「不编造」和「人工门不旁路」。
"""
from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(__file__))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from local_agent_bridge import (  # noqa: E402
    LocalAgentBridge, STATUS_GATED, STATUS_NO_MATCH, STATUS_OK,
)


def _bridge():
    from mbdsdr_ai import AgentConfig, MBDSDRAgent
    return LocalAgentBridge(MBDSDRAgent(AgentConfig(api_key="")))


@pytest.fixture()
def bridge():
    return _bridge()


def test_capabilities_come_from_real_registry(bridge):
    caps = bridge.capabilities()
    assert len(caps) > 100, "能力位必须来自真实注册表，不许是示例值"
    assert all({"name", "description", "category", "available"} <= set(c)
               for c in caps)
    assert any(c["name"] == "sdr_status" for c in caps)


def test_status_question_routes_to_status_tool(bridge):
    out = bridge.handle("现在设备什么状态")
    assert out["status"] == STATUS_OK
    assert [c["tool"] for c in out["cards"]] == ["sdr_status"]
    assert out["cards"][0]["content"], "回执必须是工具真实输出"


def test_nonsense_does_not_invent_an_answer(bridge):
    out = bridge.handle("asdfqwer 无意义的话")
    assert out["status"] == STATUS_NO_MATCH
    assert out["cards"] == []
    assert "不会编" in out["reply"]
    assert out["suggestions"], "没有命中时给真实能力域，不给噪声工具名"


def test_star_culture_is_repo_data_and_does_not_run_random_tools(bridge):
    out = bridge.handle("天狼星有什么故事")
    assert out["status"] == "culture"
    assert any(h["zh"] == "天狼" for h in out["culture"])
    assert out["cards"] == [], "问星名不该顺手执行一个沾星字的工具"
    assert "不会编" in out["reply"]
    assert bridge.star_culture_count() > 100
    assert bridge.constellation_culture_count() > 10


def test_list_tools_is_executable_locally(bridge):
    out = bridge.handle("列出所有工具")
    assert out["status"] == STATUS_OK
    assert out["cards"][0]["tool"] == "list_tools"


def test_write_tool_is_gated_and_never_executed(bridge):
    from mbdsdr_ai import AgentConfig, MBDSDRAgent
    agent = MBDSDRAgent(AgentConfig(api_key=""))
    b = LocalAgentBridge(agent)
    out = b.handle("帮我用「sdr_set_frequency」能力：98 MHz")
    assert out["status"] == STATUS_GATED
    card = out["cards"][0]
    assert card["confirm_tool"] == "sdr_set_frequency"
    # 参数名来自工具 schema 的真实声明（sdr_set_frequency 用 frequency_hz）
    assert 98_000_000.0 in card["args"].values()
    assert out["tasks"] == [], "人工门没确认前不许产生已执行任务"
    # 确认后才真的调用；这里直接指定只读工具验证重放路径不串台
    replay = b.handle("确认执行", confirm=True, only_tool="sdr_status",
                      only_args={})
    assert replay["status"] == STATUS_OK
    assert replay["cards"][0]["tool"] == "sdr_status"


def test_tasks_and_hook_events_are_real(bridge):
    before = bridge.live_state()
    out = bridge.handle("现在设备什么状态")
    after = bridge.live_state()
    assert out["tasks"] and out["hooks"]
    assert after["tasks"] > before["tasks"], "编排器里应真的多一条任务"
    assert after["events"] > before["events"], "Hooks 历史里应真的多事件"
    assert after["tools_available"] <= after["tools"]
    assert after["model"] is False, "无 API Key 必须如实报未配置"


def test_explicit_capability_name_is_honoured(bridge):
    out = bridge.handle("帮我用「sdr_status」能力：")
    assert out["status"] == STATUS_OK
    assert out["cards"][0]["tool"] == "sdr_status"
