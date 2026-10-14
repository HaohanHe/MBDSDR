# SPDX-License-Identifier: MIT
"""PreToolUse 可否决钩子测试（2026-10-14，kimi-code 审计 Top-3 落地）。

形态对齐 kimi-code externalHooks 的可否决语义（stdin JSON、deny 即拦），
MBDSDR 版取进程内 HookManager 子集：AGENT_PRE_TOOL_USE 事件在规则门与
分发之前触发，任一同步钩子返回 {"decision": "deny"} 即否决；veto 压过
arm_manual 与 policy allow（与 deny 规则同级）；钩子异常绝不阻断主流程。
"""
import sys

from mbdsdr_ai.core.mcp_server import MCPToolSet
from mbdsdr_ai.hooks import EventType


def _set():
    return MCPToolSet()


def test_veto_blocks_even_armed_write():
    ts = _set()

    def deny(event):
        assert event.data["tool"] == "set_frequency"
        assert event.data["write"] is True
        assert event.data["arguments"]["hz"] == 98_500_000
        return {"decision": "deny", "reason": "排障：禁止调谐"}

    ts.hooks.register(EventType.AGENT_PRE_TOOL_USE, deny)
    out = ts.call_tool("set_frequency",
                       {"hz": 98_500_000, "arm_manual": True})
    assert out["ok"] is False and out["gated"] is True
    assert out["outcome"] == "vetoed"
    assert out["veto_reason"] == "排障：禁止调谐"


def test_veto_applies_to_read_tools_too():
    ts = _set()
    ts.hooks.register(EventType.AGENT_PRE_TOOL_USE,
                      lambda e: {"decision": "deny", "reason": "全场静默"})
    out = ts.call_tool("get_frequency", {})
    assert out["outcome"] == "vetoed" and out["gated"] is True


def test_non_deny_hook_result_passes_through():
    ts = _set()
    seen = []

    def observer(event):
        seen.append(event.data["tool"])
        return {"note": "just watching"}     # 非 deny 不拦截

    ts.hooks.register(EventType.AGENT_PRE_TOOL_USE, observer)
    out = ts.call_tool("set_frequency",
                       {"hz": 98_500_000, "arm_manual": True})
    assert out == {"ok": True}
    assert seen == ["set_frequency"]          # 钩子确实被触发过


def test_hook_exception_does_not_break_call(capsys):
    ts = _set()
    ts.hooks.register(EventType.AGENT_PRE_TOOL_USE,
                      lambda e: 1 / 0)          # 钩子自己炸
    out = ts.call_tool("set_frequency",
                       {"hz": 98_500_000, "arm_manual": True})
    assert out == {"ok": True}                # 主流程放行
    cap = capsys.readouterr()
    logged = cap.out + cap.err                # Hook.trigger 的留痕走 stdout
    assert "Hook Error" in logged or "division" in logged  # 有迹可查


def test_disabled_hook_does_not_veto():
    ts = _set()
    hid = ts.hooks.register(EventType.AGENT_PRE_TOOL_USE,
                            lambda e: {"decision": "deny", "reason": "x"})
    ts.hooks.disable(hid)
    out = ts.call_tool("set_frequency",
                       {"hz": 98_500_000, "arm_manual": True})
    assert out == {"ok": True}


def test_hooks_instance_is_per_toolset():
    a, b = _set(), _set()
    a.hooks.register(EventType.AGENT_PRE_TOOL_USE,
                     lambda e: {"decision": "deny", "reason": "only-a"})
    out = b.call_tool("set_frequency",
                      {"hz": 98_500_000, "arm_manual": True})
    assert out == {"ok": True}                # b 不受 a 的钩子影响
