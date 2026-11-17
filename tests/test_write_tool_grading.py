# SPDX-License-Identifier: MIT
"""写工具全量分级（spec D6 / Task 35）。

钉住三件事：
  1. 96 个写工具每一个都已登记 ``hard_gate`` / ``confirm`` / ``low_risk`` +
     理由，不允许未分级项；
  2. 未登记分级的写工具默认按 ``hard_gate`` 处理（fail-closed），
     模型通道拿不到 token 就一次 handler 都不执行；
  3. 三类各有一条行为断言：hard/confirm 无 token 拒绝、有 token 放行；
     low_risk 无 token 直接执行。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.tool_registry import (  # noqa: E402
    ToolRegistry,
    ToolResult,
    WRITE_TOOL_GRADES,
    WRITE_TOOL_GRADE_CONFIRM,
    WRITE_TOOL_GRADE_HARD_GATE,
    WRITE_TOOL_GRADE_LOW_RISK,
    write_tool_grade,
    write_tool_grade_reason,
)


@pytest.fixture(scope="module")
def agent():
    from mbdsdr_ai import AgentConfig, MBDSDRAgent
    return MBDSDRAgent(AgentConfig(api_key=""))


@pytest.fixture(autouse=True)
def _clean_gate(tmp_path, monkeypatch):
    from mbdsdr_ai.tx_guard import reset_tx_audit, reset_tx_gate
    monkeypatch.setenv("MBDSDR_TX_AUDIT_PATH",
                       str(tmp_path / "tx_audit.jsonl"))
    reset_tx_audit()
    reset_tx_gate()
    yield
    reset_tx_gate()


def _registry_with(name: str):
    reg = ToolRegistry()
    hits = []

    def handler(args):
        hits.append(dict(args))
        return ToolResult(success=True, content="ok")

    reg.register(name, "分级探针", {"type": "object", "properties": {},
                                    "required": []}, handler, write=True)
    return reg, hits


def _call(reg: ToolRegistry, name: str, args=None):
    return reg.call_from_model({
        "id": "call_1", "type": "function",
        "function": {"name": name, "arguments": json.dumps(args or {})},
    })


def test_all_96_write_tools_are_graded(agent):
    writes = {name for name, tool in agent.tool_registry.tools.items()
              if tool.get("write")}
    assert len(writes) == 96, "写工具总数变化时需同步补分级并更新此断言"

    missing = sorted(writes - set(WRITE_TOOL_GRADES))
    assert missing == [], f"未分级写工具必须列名：{missing}"

    extra = sorted(set(WRITE_TOOL_GRADES) - writes)
    assert extra == [], f"分级表里存在已不存在的工具：{extra}"

    grades = {WRITE_TOOL_GRADE_HARD_GATE, WRITE_TOOL_GRADE_CONFIRM,
              WRITE_TOOL_GRADE_LOW_RISK}
    for name in writes:
        assert write_tool_grade(name) in grades
        assert write_tool_grade_reason(name).strip(), f"{name} 缺少分级理由"

    # 三个级别都真实存在（不是只登记了一类来凑数）。
    assert {write_tool_grade(n) for n in writes} == grades


def test_unregistered_write_defaults_to_hard_gate():
    name = "probe_unregistered_write"
    assert name not in WRITE_TOOL_GRADES
    assert write_tool_grade(name) == WRITE_TOOL_GRADE_HARD_GATE

    reg, hits = _registry_with(name)
    refused = _call(reg, name, {"x": 1})
    assert refused.success is False
    assert refused.data.get("gated") is True
    assert refused.data.get("grade") == WRITE_TOOL_GRADE_HARD_GATE
    assert hits == [], "未分级写工具在模型通道必须先拒"

    # 拿到有效 token 后才按 hard_gate 放行，证明默认语义就是硬门。
    from mbdsdr_ai.tx_guard import issue_confirmation
    args = {"x": 1}
    token = issue_confirmation(name, args, ttl_s=30, source="test")
    allowed = _call(reg, name, dict(args, confirm_token=token))
    assert allowed.success is True
    assert len(hits) == 1


def test_hard_gate_requires_token():
    reg, hits = _registry_with("radio_ptt")
    refused = _call(reg, "radio_ptt", {"state": "on"})
    assert refused.success is False
    assert refused.data.get("grade") == WRITE_TOOL_GRADE_HARD_GATE
    assert hits == []

    from mbdsdr_ai.tx_guard import issue_confirmation
    args = {"state": "on"}
    token = issue_confirmation("radio_ptt", args, ttl_s=30, source="test")
    ok = _call(reg, "radio_ptt", dict(args, confirm_token=token))
    assert ok.success is True
    assert hits == [{"state": "on"}], "门参数不得下发给 handler"


def test_confirm_grade_requires_token():
    reg, hits = _registry_with("switch_model")
    assert write_tool_grade("switch_model") == WRITE_TOOL_GRADE_CONFIRM

    refused = _call(reg, "switch_model", {"model": "x"})
    assert refused.success is False
    assert refused.data.get("gated") is True
    assert refused.data.get("grade") == WRITE_TOOL_GRADE_CONFIRM
    assert hits == []

    from mbdsdr_ai.tx_guard import issue_confirmation
    args = {"model": "x"}
    token = issue_confirmation("switch_model", args, ttl_s=30, source="test")
    ok = _call(reg, "switch_model", dict(args, confirm_token=token))
    assert ok.success is True
    assert hits == [{"model": "x"}]


def test_low_risk_runs_without_token():
    reg, hits = _registry_with("sdr_set_gain")
    assert write_tool_grade("sdr_set_gain") == WRITE_TOOL_GRADE_LOW_RISK

    ok = _call(reg, "sdr_set_gain", {"gain_db": 20})
    assert ok.success is True
    assert hits == [{"gain_db": 20}]
