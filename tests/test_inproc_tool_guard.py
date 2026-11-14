# SPDX-License-Identifier: MIT
"""进程内工具调用与 MCP 面共用同一套守卫（PRE/RESULT + 触发者）的判据。

判据：
* 模型触发的进程内调用能观察到 ``agent.pre_tool_use`` 与
  ``agent.tool_result``，且 ``data["trigger"] == "model"``；
* PRE 否决后 handler 确实没有执行（用副作用计数器检测），返回结构化
  ``hook_vetoed`` 而不是照常执行；
* 用户直接触发的调用同样留痕（``trigger == "user"``），但不被模型侧守卫
  （进程内钩子 / ``~/.mbdsdr/hooks.json`` 外部命令）拦下；
* 两条面的 PRE/RESULT 事件字段集合完全一致。

反证（把入口改回直连必须红）：
把 ``ToolRegistry.call`` 里的 ``emit_pre_tool_use`` 去掉、或让
``call_from_model`` 不传 ``model``，则 ``test_model_veto_blocks_execution``
的副作用计数会从 0 变成 1，``test_model_call_emits_pre_and_result`` /
``test_event_fields_match_mcp_face`` 会因事件缺失而失败。
"""
from __future__ import annotations

import json

from mbdsdr_ai.core.mcp_server import MCPToolSet
from mbdsdr_ai.hooks import EventType
from mbdsdr_ai.tool_guard import TRIGGER_MODEL, TRIGGER_USER
from mbdsdr_ai.tool_registry import ToolRegistry
from mbdsdr_ai.tool_results import ToolResult


class _Counter:
    """可观测副作用：handler 每执行一次 +1。"""

    def __init__(self) -> None:
        self.value = 0

    def bump(self, args):
        self.value += 1
        return ToolResult(success=True, content="ok",
                          data={"count": self.value})


def _registry(counter: _Counter, **kwargs) -> ToolRegistry:
    reg = ToolRegistry(**kwargs)
    reg.register(
        name="counting_read",
        description="计数只读工具（测试用）",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=counter.bump,
        write=False,
    )
    return reg


def _call_from_model(reg: ToolRegistry) -> ToolResult:
    return reg.call_from_model({
        "id": "call_1",
        "type": "function",
        "function": {"name": "counting_read", "arguments": json.dumps({})},
    })


def _events(events, event_type):
    return [e for e in events if e.event_type == event_type]


def test_model_call_emits_pre_and_result():
    counter = _Counter()
    reg = _registry(counter)
    seen = []
    reg.hooks.register("*", seen.append)

    out = _call_from_model(reg)

    assert out.success is True and counter.value == 1
    pre = _events(seen, EventType.AGENT_PRE_TOOL_USE)
    res = _events(seen, EventType.AGENT_TOOL_RESULT)
    assert len(pre) == 1 and len(res) == 1
    assert pre[0].data["tool"] == "counting_read"
    assert pre[0].data["write"] is False
    assert pre[0].data["trigger"] == TRIGGER_MODEL
    assert res[0].data["tool"] == "counting_read"
    assert res[0].data["ok"] is True
    assert res[0].data["outcome"] == "executed"
    assert res[0].data["trigger"] == TRIGGER_MODEL


def test_model_veto_blocks_execution():
    counter = _Counter()
    reg = _registry(counter)
    reg.hooks.register(EventType.AGENT_PRE_TOOL_USE,
                       lambda e: {"decision": "deny", "reason": "排障静默"})
    seen = []
    reg.hooks.register(EventType.AGENT_TOOL_RESULT, seen.append)

    out = _call_from_model(reg)

    assert counter.value == 0, "否决后 handler 绝不能被执行"
    assert out.success is False
    assert out.error == "hook_vetoed"
    assert out.data["outcome"] == "vetoed"
    assert out.data["veto_reason"] == "排障静默"
    assert out.data["gated"] is True
    # 与 MCP 面一致：vetoed/denied 不冒泡 result 事件（只有 PRE 留痕）。
    assert seen == []


def test_user_call_is_traced_but_not_vetoed():
    counter = _Counter()
    reg = _registry(counter)
    reg.hooks.register(EventType.AGENT_PRE_TOOL_USE,
                       lambda e: {"decision": "deny", "reason": "模型侧静默"})
    seen = []
    reg.hooks.register("*", seen.append)

    out = reg.call("counting_read", {})

    assert out.success is True and counter.value == 1
    pre = _events(seen, EventType.AGENT_PRE_TOOL_USE)
    res = _events(seen, EventType.AGENT_TOOL_RESULT)
    assert len(pre) == 1 and len(res) == 1
    assert pre[0].data["trigger"] == TRIGGER_USER
    assert res[0].data["trigger"] == TRIGGER_USER


def test_external_command_hooks_apply_to_model_only():
    class _FakeCommands:
        def __init__(self) -> None:
            self.calls = []

        def decide(self, event):
            self.calls.append(event)
            return {"reason": "外部命令否决"}

    counter = _Counter()
    fake = _FakeCommands()
    reg = _registry(counter, hook_commands=fake)

    model_out = _call_from_model(reg)
    assert model_out.success is False
    assert model_out.error == "hook_vetoed"
    assert counter.value == 0
    assert len(fake.calls) == 1

    user_out = reg.call("counting_read", {})
    assert user_out.success is True and counter.value == 1
    assert len(fake.calls) == 1, "用户触发不得跑模型侧外部命令闸"


def test_event_fields_match_mcp_face():
    mcp = MCPToolSet()
    mcp_seen = []
    mcp.hooks.register("*", mcp_seen.append)
    mcp.call_tool("get_frequency", {})

    counter = _Counter()
    reg = _registry(counter)
    reg_seen = []
    reg.hooks.register("*", reg_seen.append)
    _call_from_model(reg)

    pre_mcp = _events(mcp_seen, EventType.AGENT_PRE_TOOL_USE)[0]
    pre_reg = _events(reg_seen, EventType.AGENT_PRE_TOOL_USE)[0]
    assert set(pre_mcp.data) == {"tool", "arguments", "write", "trigger"}
    assert set(pre_mcp.data) == set(pre_reg.data)
    assert pre_mcp.data["trigger"] == TRIGGER_MODEL
    assert pre_reg.data["trigger"] == TRIGGER_MODEL

    res_mcp = _events(mcp_seen, EventType.AGENT_TOOL_RESULT)[0]
    res_reg = _events(reg_seen, EventType.AGENT_TOOL_RESULT)[0]
    assert set(res_mcp.data) == {"tool", "kind", "ok", "outcome",
                                 "spilled", "trigger"}
    assert set(res_mcp.data) == set(res_reg.data)
