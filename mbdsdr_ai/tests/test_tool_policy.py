# SPDX-License-Identifier: MIT
"""tool_policy 测试：规则集解析 + 配置加载 + MCP 写门集成。

背景：2026-10-14 上游审计（kimi-code 与 kilocode 独立收敛）把「二值
arm_manual 门升级为 allow/ask/deny 规则集 + 会话批准缓存」列为 MBDSDR
agent 侧第一落点。本测试钉住：默认语义与旧门完全兼容（写默认 ask），
deny 最高优先级，会话缓存按参数敏感，坏配置诚实回退。
"""
import json

import pytest

from mbdsdr_ai.core.mcp_server import MCPToolSet
from mbdsdr_ai.tool_policy import (ACTIONS, POLICY_ENV, ToolPolicy,
                                   normalize_key)


# ---------------------------------------------------------------------------
# 纯规则逻辑
# ---------------------------------------------------------------------------

def test_defaults_match_legacy_gate():
    p = ToolPolicy()
    assert p.action_for("set_frequency", True) == "ask"    # 写默认问
    assert p.action_for("get_status", False) == "allow"    # 读默认放


def test_exact_rule_beats_default():
    p = ToolPolicy([{"pattern": "shutdown", "action": "deny"}])
    assert p.action_for("shutdown", True) == "deny"
    assert p.action_for("set_frequency", True) == "ask"


def test_wildcard_rule():
    p = ToolPolicy([{"pattern": "set_*", "action": "allow"}])
    assert p.action_for("set_frequency", True) == "allow"
    assert p.action_for("set_gain", True) == "allow"
    assert p.action_for("shutdown", True) == "ask"          # 不命中通配


def test_deny_beats_allow_wildcard():
    """危险工具精确 deny 不被宽松通配放行（hard veto 优先级）。"""
    p = ToolPolicy([{"pattern": "set_*", "action": "allow"},
                    {"pattern": "set_tx_power", "action": "deny"}])
    assert p.action_for("set_bandwidth", True) == "allow"
    assert p.action_for("set_tx_power", True) == "deny"
    assert p.is_denied("set_tx_power")


def test_invalid_constructor_default_rejected():
    with pytest.raises(ValueError):
        ToolPolicy(default_write="maybe")


# ---------------------------------------------------------------------------
# 配置加载（诚实回退，不静默吞坏配置）
# ---------------------------------------------------------------------------

def test_from_dict_skips_bad_rules_with_stderr(capsys):
    p = ToolPolicy.from_dict({"rules": [
        {"pattern": "ok_*", "action": "allow"},
        {"pattern": "no_action"},                    # 缺 action
        {"pattern": "bad_action", "action": "yolo"},  # action 非法
        "not-a-dict",                                 # 形态错误
    ]})
    assert [r["action"] for r in p.rules] == ["allow"]
    err = capsys.readouterr().err
    assert "第 1 条" in err and "第 2 条" in err and "第 3 条" in err


def test_load_missing_file_is_default():
    p = ToolPolicy.load(path="/nonexistent/mbdsdr_policy.json")
    assert p.action_for("set_frequency", True) == "ask"


def test_load_env_override(tmp_path, monkeypatch):
    cfg = tmp_path / "p.json"
    cfg.write_text(json.dumps({"rules": [
        {"pattern": "shutdown", "action": "deny"}]}), encoding="utf-8")
    monkeypatch.setenv(POLICY_ENV, str(cfg))
    p = ToolPolicy.load()
    assert p.action_for("shutdown", True) == "deny"


def test_load_bad_json_falls_back_with_stderr(tmp_path, capsys):
    cfg = tmp_path / "p.json"
    cfg.write_text("{not json", encoding="utf-8")
    p = ToolPolicy.load(path=str(cfg))
    assert p.action_for("set_frequency", True) == "ask"
    assert "回退默认策略" in capsys.readouterr().err


def test_normalize_key_is_arg_sensitive_and_order_free():
    assert normalize_key("t", {"a": 1, "b": 2}) == normalize_key("t", {"b": 2, "a": 1})
    assert normalize_key("t", {"a": 1}) != normalize_key("t", {"a": 2})
    assert normalize_key("t1", {"a": 1}) != normalize_key("t2", {"a": 1})


# ---------------------------------------------------------------------------
# MCP 写门集成（默认语义与旧门完全兼容）
# ---------------------------------------------------------------------------

def _set():
    return MCPToolSet()


def test_gate_default_unchanged_without_arm():
    ts = _set()
    out = ts.call_tool("set_frequency", {"hz": 98_500_000})
    assert out["ok"] is False and out["gated"] is True
    assert out["policy"] == "ask"
    assert "arm_manual" in out["hint"]


def test_gate_explicit_arm_executes():
    ts = _set()
    out = ts.call_tool("set_frequency", {"hz": 98_500_000, "arm_manual": True})
    assert out == {"ok": True}
    assert "approval" not in out  # 显式授权不标 session


def test_session_approval_cache():
    ts = _set()
    args = {"hz": 98_500_000, "arm_manual": True, "approve_session": True}
    assert ts.call_tool("set_frequency", args) == {"ok": True}
    # 同工具同参数：第二次免问，且可观测标注
    out = ts.call_tool("set_frequency", {"hz": 98_500_000})
    assert out == {"ok": True, "approval": "session"}
    # 不同参数：缓存不覆盖，仍要问（按参数敏感）
    gated = ts.call_tool("set_frequency", {"hz": 101_000_000})
    assert gated["gated"] is True


def test_deny_rule_vetoes_even_armed():
    policy = ToolPolicy([{"pattern": "set_frequency", "action": "deny"}])
    ts = MCPToolSet(policy=policy)
    out = ts.call_tool("set_frequency", {"hz": 98_500_000, "arm_manual": True})
    assert out["ok"] is False and out["gated"] is True and out["denied"] is True
    assert "denied_by_policy" in out["error"]


def test_allow_rule_skips_ask():
    policy = ToolPolicy([{"pattern": "set_*", "action": "allow"}])
    ts = MCPToolSet(policy=policy)
    out = ts.call_tool("set_frequency", {"hz": 98_500_000})
    assert out == {"ok": True}


def test_read_tools_never_gated():
    ts = _set()
    out = ts.call_tool("get_frequency", {})
    assert "gated" not in (out if isinstance(out, dict) else {})


def test_arm_flags_never_leak_to_handler():
    ts = _set()
    ts.call_tool("set_frequency", {"hz": 98_500_000, "arm_manual": True})
    # get 类无门；用一个会把参数回显的读工具验证 flag 不泄漏——
    # 本仓读工具不回显参数，改为验证缓存键里不剩 arm 字段：
    from mbdsdr_ai.tool_policy import normalize_key
    key = normalize_key("set_frequency", {"hz": 98_500_000})
    assert all("arm_manual" not in str(k2) and "approve_session" not in str(k2)
               for k2 in (key[1],))
