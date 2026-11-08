# SPDX-License-Identifier: MIT
"""模型通道写入门契约（2026-11-08，对照 deepseek-harness 的审计补洞）。

钉住：模型在对话里**自发**调用的发射 / 改源码 / 外部连接类工具，必须过
``tx_guard`` 的 allowed-once 门。未授权时 handler 一次都不能执行，回执
明确说「需要人工授权」，不假装成功、不静默降级；模型自报 ``arm_manual``
不构成授权；门本身坏了按 fail-closed 处理。

本轮只收不可逆 / 有外泄风险的动作（见 ``MODEL_HARD_GATE_PATTERNS``）；
可恢复的调谐仍在 P1 待分级——这里也有一条用例如实钉住"还没修"，避免
把半修当成全修。
"""
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.tool_registry import ToolRegistry, ToolResult  # noqa: E402


def _make_registry():
    reg = ToolRegistry()
    hits = []

    def fire(args):
        hits.append(dict(args))
        return ToolResult(success=True, content="fired")

    params = {"type": "object", "properties": {}, "required": []}
    reg.register("radio_ptt", "PTT 控制", params, fire)
    reg.register("sdr_transmit_test", "测试发射", params, fire)
    reg.register("get_status", "只读状态", params, fire)
    reg.register("sdr_set_frequency", "可恢复调谐", params, fire)
    return reg, hits


def _call(reg, name, args=None):
    return reg.call_from_model({
        "id": "call_1",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args or {})},
    })


def test_emitter_is_gated_without_token():
    from mbdsdr_ai.tx_guard import reset_tx_gate
    reset_tx_gate()
    reg, hits = _make_registry()

    r = _call(reg, "radio_ptt", {"state": "on"})

    assert r.success is False, "未授权发射绝不能成功"
    assert r.error == "manual_gate_required"
    assert r.data.get("gated") is True
    assert r.next_step, "必须给模型恢复指引，不能只丢错误码"
    assert hits == [], "未授权时 handler 一次都不能执行"


def test_emitter_runs_with_valid_one_shot_token():
    from mbdsdr_ai.tx_guard import (action_digest, get_tx_gate,
                                    reset_tx_gate)
    reset_tx_gate()
    reg, hits = _make_registry()
    args = {"state": "on"}
    token = get_tx_gate().arm(
        action=action_digest("radio_ptt", args),
        tool="radio_ptt", source="test")

    r = _call(reg, "radio_ptt", {**args, "confirm_token": token})

    assert r.success is True, r.content
    assert len(hits) == 1, "有有效一次性授权时应真的执行一次"


def test_token_is_consumed_once():
    from mbdsdr_ai.tx_guard import (action_digest, get_tx_gate,
                                    reset_tx_gate)
    reset_tx_gate()
    reg, hits = _make_registry()
    args = {"state": "on"}
    token = get_tx_gate().arm(
        action=action_digest("radio_ptt", args),
        tool="radio_ptt", source="test")

    first = _call(reg, "radio_ptt", {**args, "confirm_token": token})
    second = _call(reg, "radio_ptt", {**args, "confirm_token": token})

    assert first.success is True
    assert second.success is False, "一次性 token 不得复用"
    assert len(hits) == 1, "第二次不得再执行 handler"


def test_model_self_reported_arm_manual_is_not_authorization():
    from mbdsdr_ai.tx_guard import reset_tx_gate
    reset_tx_gate()
    reg, hits = _make_registry()

    r = _call(reg, "radio_ptt", {"state": "on", "arm_manual": True})

    assert r.success is False
    assert r.data.get("gated") is True
    assert hits == []


def test_readonly_tool_is_not_gated():
    from mbdsdr_ai.tx_guard import reset_tx_gate
    reset_tx_gate()
    reg, hits = _make_registry()

    r = _call(reg, "get_status")

    assert r.success is True
    assert len(hits) == 1


def test_gate_failure_fails_closed(monkeypatch):
    """授权检查本身坏了也不能放行（fail-closed）。"""
    import mbdsdr_ai.tx_guard as tx_guard

    def boom(*_a, **_kw):
        raise RuntimeError("audit disk full")

    monkeypatch.setattr(tx_guard, "resolve_write_authorization", boom)
    reg, hits = _make_registry()

    r = _call(reg, "radio_ptt", {"state": "on"})

    assert r.success is False
    assert r.data.get("reason") == "gate_error"
    assert hits == [], "门坏了更不能执行"


def test_recoverable_write_is_not_hard_gated_yet():
    """诚实钉住本轮范围：可恢复调谐还没进硬门，P1 待分级确认。

    这条用例是"未做清单"的机器版本：哪天把 sdr_set_* 也收进硬门，
    这里会红，提醒同步改文档与 UI 的确认流，而不是悄悄换行为。
    """
    from mbdsdr_ai.tx_guard import reset_tx_gate
    reset_tx_gate()
    reg, hits = _make_registry()

    r = _call(reg, "sdr_set_frequency", {"frequency_hz": 145_800_000})

    assert r.success is True
    assert len(hits) == 1
