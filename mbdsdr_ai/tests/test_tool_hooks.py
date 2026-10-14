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


# ---------------------------------------------------------------------------
# PostToolUse（AGENT_TOOL_RESULT）观测事件——只读、不改写
# ---------------------------------------------------------------------------

def test_tool_result_event_fires_on_success():
    ts = _set()
    seen = []
    ts.hooks.register("agent.tool_result",
                      lambda e: seen.append(e.data))
    out = ts.call_tool("set_frequency",
                       {"hz": 98_500_000, "arm_manual": True})
    assert out == {"ok": True}
    assert len(seen) == 1
    s = seen[0]
    assert s["tool"] == "set_frequency" and s["ok"] is True
    assert s["outcome"] == "executed"      # 执行成功分类
    assert s["spilled"] is False


def test_tool_result_event_fires_on_gate_and_error():
    ts = _set()
    seen = []
    ts.hooks.register("agent.tool_result", lambda e: seen.append(e.data))
    ts.call_tool("set_frequency", {"hz": 98_500_000})          # gated
    # read-side 工具照常执行并返回（connect 失败路径走 SDRUserError）
    out = ts.call_tool("connect", {"device_id": 1})            # 读工具无门
    # connect 无后端时报 not_connected/no_backend 一类错误信封
    assert out.get("ok") is False or out.get("connected") is False
    assert out is not None
    gated = [s for s in seen if s.get("outcome") == "gated"]
    assert gated and gated[0]["tool"] == "set_frequency"
    # set_demod 走 SDRUserError（非法 mode）-> outcome=error
    bad = ts.call_tool("set_demod",
                       {"mode": "NOT_A_MODE", "arm_manual": True})
    assert bad.get("outcome") == "error"
    assert bad.get("ok", False) is False
    assert any(s["outcome"] == "error" for s in seen)


def test_tool_result_event_hook_cannot_mutate_result():
    ts = _set()
    def evil(e):
        return {"decision": "deny", "reason": "too late"}      # 试图事后否决
    ts.hooks.register("agent.tool_result", evil)
    out = ts.call_tool("set_frequency",
                       {"hz": 98_500_000, "arm_manual": True})
    assert out == {"ok": True}      # 结果不因事后钩子改变


def test_tool_result_event_exception_isolated(capsys):
    ts = _set()
    ts.hooks.register("agent.tool_result", lambda e: 1 / 0)
    out = ts.call_tool("set_frequency",
                       {"hz": 98_500_000, "arm_manual": True})
    assert out == {"ok": True}
    logged = (capsys.readouterr().out + capsys.readouterr().err)
    assert "Hook Error" in logged or "division" in logged
