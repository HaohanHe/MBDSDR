# SPDX-License-Identifier: MIT
"""门层信封稳定错误 code + deny 单调性钉扎（dsh 借鉴落地）。

- code 词汇表与 core/errors.py::SDRUserError.code 同风格（机器可读蛇形），
  本批补齐门层三处裸字符串信封：denied_by_policy / manual_gate_required /
  internal_error。客户端可按 code 编程分支，不靠匹配中文消息。
- deny 单调性（dsh ToolGuard 机制思想）：policy deny 之后 armed /
  approve_session / 会话缓存一律不得翻回 allow——armed+approve_session
  同参调用被拒即钉死（hard veto 语义回归锁）。
"""
from mbdsdr_ai.core.mcp_server import MCPToolSet, normalize_key


def _set() -> MCPToolSet:
    return MCPToolSet()      # 无后端，honest empty，不触硬件


def _deny_rule():
    return [{"pattern": "set_frequency", "action": "deny"}]


def test_deny_envelope_has_stable_code():
    srv = _set()
    srv.policy.rules = _deny_rule()
    out = srv.call_tool("set_frequency", {"hz": 145500000,
                                          "arm_manual": True,
                                          "approve_session": True})
    assert out.get("code") == "denied_by_policy"
    assert out.get("outcome") == "denied"
    assert out.get("denied") is True


def test_deny_is_monotonic_against_arm_and_session_approval():
    """deny 单调性：显式武装 + 会话批准缓存都不得把 deny 翻成 allow。"""
    srv = _set()
    srv.policy.rules = _deny_rule()
    # 先伪造一个已批准的同参缓存——仍必须拒（单调性核心断言）
    key = normalize_key("set_frequency", {"hz": 145500000})
    srv._session_approvals.add(key)
    out = srv.call_tool("set_frequency", {"hz": 145500000,
                                          "arm_manual": True,
                                          "approve_session": True})
    assert out.get("outcome") == "denied"
    assert out.get("code") == "denied_by_policy"


def test_gate_envelope_has_stable_code():
    srv = _set()
    srv.policy.rules = [{"pattern": "set_frequency", "action": "ask"}]
    out = srv.call_tool("set_frequency", {"hz": 145500000})
    assert out.get("code") == "manual_gate_required"
    assert out.get("outcome") == "gated"
    assert "arm_manual=true" in out.get("hint", "")
