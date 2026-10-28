#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""旧壳 MCP 安全面收口测试（mbdsdr_ai_mcp_server.py × ToolCallGate）。

背景（缺口分析遗留项，2026-10-28 收口）：旧壳 call_tool 此前直调
tool_registry.call，无 PreToolUse 钩子、无 allow/ask/deny 规则门、无
arm_manual 门、无审计日志、无参数校验。现在走与正规面 MCPToolSet 共用的
ToolCallGate。本测试钉住：

  1. write 类工具未武装被门拦（不触达 tool_registry.call）；
  2. deny 规则命中即拒且单调（armed 翻不动 deny）；
  3. PreToolUse veto 压过 policy allow + arm_manual；
  4. 审计 opt-in：开启时 JSONL 有记录、默认无账本；
  5. 未知工具保持原 tool_not_found 错误信封形状；
  6. 错误信封既有键 {content, isError} 不变，只增 outcome/code 新键；
  7. 读工具 n 参数越界报 invalid_argument（缺口 P2 收口，不钳位）。

stub 注册表只登记少量命名工具（不实例化真 MBDSDRAgent、不触硬件、无身份
字面量）；安全门一律显式注入 ToolPolicy()，不读真实 ~/.mbdsdr 配置。
"""
from __future__ import annotations

import json
import os
import sys
import types

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.tool_registry import ToolRegistry, ToolResult  # noqa: E402
from mbdsdr_ai.tool_policy import ToolPolicy  # noqa: E402
from mbdsdr_ai.hooks import EventType  # noqa: E402
from mbdsdr_ai.core.mcp_server import AUDIT_LOG_ENV, AuditLog, ToolCallGate  # noqa: E402
from mbdsdr_ai_mcp_server import MBSDRAIMCPServer  # noqa: E402


# ---------------------------------------------------------------------------
# stub 工具面（名字沿用真实工具名，分类判定走真实代码路径）
# ---------------------------------------------------------------------------
def _ok_handler(args):
    return ToolResult(success=True, content="ok")


def _build_registry():
    reg = ToolRegistry()
    reg.register(
        name="sdr_set_frequency",
        description="调谐中心频率（写工具，名字命中写模式）。",
        parameters={"type": "object",
                    "properties": {"frequency_hz": {"type": "number"}},
                    "required": ["frequency_hz"]},
        handler=_ok_handler, category="sdr")
    reg.register(
        name="code_modify_file",
        description="修改源代码（写工具，arm_manual schema 契约）。",
        parameters={"type": "object",
                    "properties": {"file_path": {"type": "string"},
                                   "arm_manual": {"type": "boolean"}},
                    "required": ["file_path", "arm_manual"]},
        handler=_ok_handler, category="code_editor")
    reg.register(
        name="sdr_spectrum_analyze",
        description="谱分析（读工具）。",
        parameters={"type": "object",
                    "properties": {"fft_size": {"type": "integer"}},
                    "required": []},
        handler=_ok_handler, category="sdr")
    reg.register(
        name="fft_filter",
        description="FFT 滤波（读工具，n 为测试信号长度）。",
        parameters={"type": "object",
                    "properties": {"n": {"type": "integer", "default": 1024}},
                    "required": []},
        handler=_ok_handler, category="dsp")
    return reg


def _server(monkeypatch, policy=None, audit_log=None, registry=None):
    """轻量旧壳实例：stub agent 注入，不建真 MBDSDRAgent。"""
    monkeypatch.delenv(AUDIT_LOG_ENV, raising=False)
    reg = registry if registry is not None else _build_registry()
    gate = ToolCallGate(policy=policy if policy is not None else ToolPolicy(),
                        audit_log=audit_log,
                        source="legacy_mcp_shell")
    return MBSDRAIMCPServer(agent=types.SimpleNamespace(tool_registry=reg),
                            gate=gate)


def _forbid_dispatch(monkeypatch, srv):
    """把 registry.call 换成地雷：门必须在分发之前拦下。"""
    calls = []

    def _boom(*_a, **_k):
        calls.append(1)
        raise AssertionError("门拦失败：触达了 tool_registry.call")

    monkeypatch.setattr(srv.tool_registry, "call", _boom)
    return calls


# ---------------------------------------------------------------------------
# 1. write 类工具未武装：门拦在 tool_registry.call 之前
# ---------------------------------------------------------------------------
def test_write_tool_unarmed_gated_before_dispatch(monkeypatch):
    """名字判定的写工具（sdr_set_frequency）未武装必须被门拦。"""
    srv = _server(monkeypatch)
    calls = _forbid_dispatch(monkeypatch, srv)
    out = srv.call_tool("sdr_set_frequency", {"frequency_hz": 98_500_000})
    assert calls == [], "未武装的写工具不得触达 tool_registry.call"
    assert out["isError"] is True
    assert out["outcome"] == "gated"
    verdict = json.loads(out["content"][0]["text"])
    assert verdict["code"] == "manual_gate_required"
    assert verdict["ok"] is False and verdict["gated"] is True
    assert "arm_manual=true" in verdict["hint"]


def test_arm_contract_tool_unarmed_gated_before_dispatch(monkeypatch):
    """arm_manual schema 契约的写工具（code_modify_file）同样被门拦。"""
    srv = _server(monkeypatch)
    calls = _forbid_dispatch(monkeypatch, srv)
    out = srv.call_tool("code_modify_file", {"file_path": "x.py"})
    assert calls == []
    assert out["isError"] is True and out["outcome"] == "gated"


def test_write_tool_armed_dispatches_with_arm_flag(monkeypatch):
    """正向对照：武装后放行分发，且 arm_manual 透传给 handler（旧壳契约）。"""
    seen = {}

    def _spy(args):
        seen.update(args)
        return ToolResult(success=True, content="ok")

    reg = _build_registry()
    reg.tools["sdr_set_frequency"]["handler"] = _spy
    srv = _server(monkeypatch, registry=reg)
    out = srv.call_tool("sdr_set_frequency",
                        {"frequency_hz": 98_500_000, "arm_manual": True})
    assert out == {"content": [{"type": "text", "text": "ok"}], "isError": False}
    assert seen.get("arm_manual") is True, "旧壳 handler 自检依赖 arm_manual 透传"


def test_read_tool_not_gated(monkeypatch):
    """只读工具不受写门影响（不带 arm 照常分发）。"""
    srv = _server(monkeypatch)
    out = srv.call_tool("sdr_spectrum_analyze", {"fft_size": 512})
    assert out["isError"] is False
    assert out["content"][0]["text"] == "ok"


# ---------------------------------------------------------------------------
# 2. deny 规则：命中即拒，且单调（armed 翻不动 deny）
# ---------------------------------------------------------------------------
def test_deny_rule_rejects_and_is_monotone(monkeypatch):
    policy = ToolPolicy([{"pattern": "sdr_set_frequency", "action": "deny"}])
    srv = _server(monkeypatch, policy=policy)
    calls = _forbid_dispatch(monkeypatch, srv)
    out = srv.call_tool("sdr_set_frequency",
                        {"frequency_hz": 98_500_000, "arm_manual": True})
    assert calls == [], "deny 命中不得触达 tool_registry.call"
    assert out["isError"] is True
    assert out["outcome"] == "denied"
    assert out["code"] == "denied_by_policy"
    verdict = json.loads(out["content"][0]["text"])
    assert verdict["denied"] is True and verdict["gated"] is True
    assert "denied_by_policy" in verdict["error"]


def test_deny_rule_pattern_also_denies(monkeypatch):
    """通配规则同样生效（set_* 全拒）。"""
    policy = ToolPolicy([{"pattern": "sdr_set_*", "action": "deny"}])
    srv = _server(monkeypatch, policy=policy)
    calls = _forbid_dispatch(monkeypatch, srv)
    out = srv.call_tool("sdr_set_frequency",
                        {"frequency_hz": 98_500_000, "arm_manual": True,
                         "approve_session": True})
    assert calls == []
    assert out["outcome"] == "denied", "approve_session 也不得翻 deny"


# ---------------------------------------------------------------------------
# 3. PreToolUse veto 压过 policy allow + arm_manual
# ---------------------------------------------------------------------------
def test_pre_tool_veto_beats_allow_and_arm(monkeypatch):
    policy = ToolPolicy([{"pattern": "sdr_set_frequency", "action": "allow"}])
    srv = _server(monkeypatch, policy=policy)
    calls = _forbid_dispatch(monkeypatch, srv)
    srv.gate.hooks.register(
        EventType.AGENT_PRE_TOOL_USE,
        lambda e: {"decision": "deny", "reason": "排障静默"})
    out = srv.call_tool("sdr_set_frequency",
                        {"frequency_hz": 98_500_000, "arm_manual": True})
    assert calls == [], "veto 命中不得触达 tool_registry.call"
    assert out["isError"] is True
    assert out["outcome"] == "vetoed"
    verdict = json.loads(out["content"][0]["text"])
    assert verdict["veto_reason"] == "排障静默"
    assert verdict["gated"] is True


def test_pre_tool_veto_applies_to_read_tools(monkeypatch):
    srv = _server(monkeypatch)
    calls = _forbid_dispatch(monkeypatch, srv)
    srv.gate.hooks.register(
        EventType.AGENT_PRE_TOOL_USE,
        lambda e: {"decision": "deny", "reason": "全场静默"})
    out = srv.call_tool("sdr_spectrum_analyze", {"fft_size": 512})
    assert calls == []
    assert out["outcome"] == "vetoed"


# ---------------------------------------------------------------------------
# 4. 审计 opt-in：开启有 JSONL、默认无账本
# ---------------------------------------------------------------------------
def test_audit_opt_in_env_writes_jsonl(tmp_path, monkeypatch):
    """MBDSDR_AUDIT_LOG 设路径即开：pre/result 两事件各落一行 JSONL。"""
    ledger = tmp_path / "audit.jsonl"
    monkeypatch.setenv(AUDIT_LOG_ENV, str(ledger))
    gate = ToolCallGate(policy=ToolPolicy(), source="legacy_mcp_shell")
    assert gate.audit_log is not None and gate.audit_log.path == str(ledger)
    srv = MBSDRAIMCPServer(
        agent=types.SimpleNamespace(tool_registry=_build_registry()), gate=gate)
    out = srv.call_tool("sdr_spectrum_analyze", {"fft_size": 512})
    assert out["isError"] is False
    lines = [json.loads(l)
             for l in ledger.read_text(encoding="utf-8").splitlines()]
    etypes = [l["event_type"] for l in lines]
    assert "agent.pre_tool_use" in etypes
    assert "agent.tool_result" in etypes
    pre = next(l for l in lines if l["event_type"] == "agent.pre_tool_use")
    assert pre["data"]["tool"] == "sdr_spectrum_analyze"
    assert pre["source"] == "legacy_mcp_shell"
    res = next(l for l in lines if l["event_type"] == "agent.tool_result")
    assert res["data"]["tool"] == "sdr_spectrum_analyze"


def test_audit_default_off_no_ledger(tmp_path, monkeypatch):
    """未设 env 且未注入：默认关闭，调用不产生任何账本文件。"""
    srv = _server(monkeypatch)
    assert srv.gate.audit_log is None
    srv.call_tool("sdr_spectrum_analyze", {"fft_size": 512})
    assert list(tmp_path.glob("*.jsonl")) == []


def test_audit_explicit_injection_writes_ledger(tmp_path, monkeypatch):
    """显式注入 AuditLog 同样只读观测，不改写工具结果。"""
    ledger = tmp_path / "audit.jsonl"
    srv = _server(monkeypatch, audit_log=AuditLog(str(ledger)))
    out = srv.call_tool("sdr_spectrum_analyze", {"fft_size": 512})
    assert out["content"][0]["text"] == "ok"   # 观测面不得改写结果
    assert ledger.is_file()
    assert ledger.read_text(encoding="utf-8").strip() != ""


# ---------------------------------------------------------------------------
# 5. 未知工具保持原错误信封形状
# ---------------------------------------------------------------------------
def test_unknown_tool_envelope_unchanged(monkeypatch):
    srv = _server(monkeypatch)
    out = srv.call_tool("no_such_tool", {})
    # 原形状零变化：只有 content/isError，没有 outcome 等新键
    assert set(out.keys()) == {"content", "isError"}
    assert out["isError"] is True
    text = out["content"][0]["text"]
    assert text.startswith("工具调用失败: 工具 'no_such_tool' 不存在")


# ---------------------------------------------------------------------------
# 6. 错误信封既有键不变，只增 outcome/code 等新键
# ---------------------------------------------------------------------------
def test_registry_error_envelope_keys_unchanged(monkeypatch):
    """既有失败路径（缺必填参数）信封保持原构：只有 content/isError。"""
    srv = _server(monkeypatch)
    out = srv.call_tool("sdr_set_frequency", {"arm_manual": True})
    assert set(out.keys()) == {"content", "isError"}, \
        f"既有错误信封不许增删键，实际: {out}"
    assert out["isError"] is True
    assert out["content"][0]["text"].startswith("工具调用失败:")


def test_gate_rejection_envelope_only_adds_new_keys(monkeypatch):
    """门拒绝信封：content/isError 既有键不变，只增 outcome/code。"""
    srv = _server(monkeypatch)
    out = srv.call_tool("sdr_set_frequency", {"frequency_hz": 98_500_000})
    assert out["isError"] is True
    assert isinstance(out["content"], list)
    assert out["content"][0]["type"] == "text"
    extra = set(out.keys()) - {"content", "isError"}
    assert extra <= {"outcome", "code"}, f"只允许新增 outcome/code，实际: {extra}"
    assert out["outcome"] == "gated"


def test_success_envelope_shape_unchanged(monkeypatch):
    """成功信封零变化（不多任何键）。"""
    srv = _server(monkeypatch)
    out = srv.call_tool("sdr_spectrum_analyze", {})
    assert out == {"content": [{"type": "text", "text": "ok"}], "isError": False}


# ---------------------------------------------------------------------------
# 7. 读工具 n 参数上界（缺口 P2 收口）：越界报 invalid_argument，不钳位
# ---------------------------------------------------------------------------
def test_read_tool_n_upper_bound_rejected_before_dispatch(monkeypatch):
    srv = _server(monkeypatch)
    calls = _forbid_dispatch(monkeypatch, srv)
    out = srv.call_tool("fft_filter", {"n": 2 ** 30})
    assert calls == [], "越界 n 不得触达 handler（不钳位、不带病执行）"
    assert out["isError"] is True
    verdict = json.loads(out["content"][0]["text"])
    assert verdict["error"] == "invalid_argument"
    assert verdict["outcome"] == "error"


def test_read_tool_n_bounds_pinned(monkeypatch):
    """n 区间常量成对钉死：[0, 1<<24] 端点放行，越界即拒。"""
    import mbdsdr_ai.core.mcp_server as ms
    assert ms._ARG_RANGES["n"] == (0, 1 << 24)

    reg = _build_registry()
    seen = []

    def _spy(args):
        seen.append(args.get("n"))
        return ToolResult(success=True, content="ok")

    reg.tools["fft_filter"]["handler"] = _spy
    srv = _server(monkeypatch, registry=reg)
    for ok_n in (0, 1, 1 << 24):
        out = srv.call_tool("fft_filter", {"n": ok_n})
        assert out["isError"] is False, f"n={ok_n} 是合法端点/值，实际: {out}"
    for bad_n in (-1, (1 << 24) + 1):
        out = srv.call_tool("fft_filter", {"n": bad_n})
        assert out["isError"] is True, f"n={bad_n} 必须越界即拒，实际: {out}"
    assert seen == [0, 1, 1 << 24], "越界值绝不钳位后混进 handler"
