# SPDX-License-Identifier: MIT
"""桌面「确认执行」签发真实 token（spec D6 / Task 36）。

旧行为：按钮把布尔 ``confirm=True`` 传给执行方，等于无凭据放行。现在桌面
确认面必须调用 ``tx_guard.issue_confirmation()`` 签发绑定动作摘要的一次性
token，执行方只认 token。本测试钉住：

  * 签发 → 执行成功一次；
  * 布尔 ``confirm=True`` 单独不构成授权；
  * 换参数重放失败（动作摘要失配）；
  * 过期 token 失败；
  * 审计来源区分 desktop（用户点过）与 model（模型自报），且只落前 8 位。
"""
from __future__ import annotations

import json
import os
import sys
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from local_agent_bridge import (  # noqa: E402
    LocalAgentBridge, STATUS_GATED, STATUS_OK,
)
from mbdsdr_ai.tool_results import ToolResult  # noqa: E402

_PROBE = "sdr_set_probe_token"


@pytest.fixture(scope="module")
def agent():
    from mbdsdr_ai import AgentConfig, MBDSDRAgent
    return MBDSDRAgent(AgentConfig(api_key=""))


@pytest.fixture()
def probe(agent):
    hits = []

    def handler(args):
        hits.append(dict(args))
        return ToolResult(success=True, content="probe ok")

    agent.tool_registry.register(
        _PROBE, "桌面确认 token 探针",
        {"type": "object",
         "properties": {"frequency_hz": {"type": "number"}},
         "required": ["frequency_hz"]},
        handler, write=True)
    bridge = LocalAgentBridge(agent)
    return bridge, hits


@pytest.fixture(autouse=True)
def _clean_gate(tmp_path, monkeypatch):
    from mbdsdr_ai.tx_guard import reset_tx_audit, reset_tx_gate
    monkeypatch.setenv("MBDSDR_TX_AUDIT_PATH",
                       str(tmp_path / "tx_audit.jsonl"))
    reset_tx_audit()
    reset_tx_gate()
    yield
    reset_tx_gate()


def _records():
    from mbdsdr_ai.tx_guard import get_tx_audit
    path = get_tx_audit().path
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _confirm(bridge, args, token=""):
    payload = dict(args)
    if token:
        payload["confirm_token"] = token
    return bridge.handle("确认执行 " + _PROBE, confirm=True,
                         only_tool=_PROBE, only_args=payload)


def test_desktop_confirm_issues_token_then_executes(probe):
    from ai_panel import issue_desktop_confirmation

    bridge, hits = probe
    args = {"frequency_hz": 100_000_000}
    token = issue_desktop_confirmation(_PROBE, args)
    out = _confirm(bridge, args, token)

    assert out["status"] == STATUS_OK, out
    assert hits == [args], "token 有效时应真正执行一次，且 handler 不收到 token"
    assert "confirm_token" not in out["cards"][0]["args"]

    records = _records()
    armed = [r for r in records if r["result"] == "armed"][-1]
    assert armed["source"] == "desktop", "签发来源必须是桌面点击"
    assert armed["token_prefix"] == token[:8], "审计只落 token 前 8 位"
    allowed = [r for r in records if r["result"] == "allowed"][-1]
    assert allowed["source"] == "desktop"
    assert allowed["token_prefix"] == token[:8]


def test_boolean_confirm_alone_is_not_authorization(probe):
    bridge, hits = probe
    out = _confirm(bridge, {"frequency_hz": 100_000_000})

    assert out["status"] == STATUS_GATED
    assert out["cards"][0].get("confirm_tool") == _PROBE
    assert hits == [], "布尔 confirm=True 不得再放行"


def test_replay_with_changed_args_is_refused(probe):
    from ai_panel import issue_desktop_confirmation

    bridge, hits = probe
    token = issue_desktop_confirmation(_PROBE, {"frequency_hz": 100_000_000})
    out = _confirm(bridge, {"frequency_hz": 101_000_000}, token)

    assert out["status"] == STATUS_GATED
    assert "gate_action_mismatch" in out["cards"][0]["content"]
    assert hits == [], "换参数重放不得执行"


def test_expired_token_is_refused(probe):
    from mbdsdr_ai.tx_guard import issue_confirmation

    bridge, hits = probe
    args = {"frequency_hz": 99_000_000}
    token = issue_confirmation(_PROBE, args, ttl_s=0.01, source="desktop")
    time.sleep(0.05)

    out = _confirm(bridge, args, token)
    assert out["status"] == STATUS_GATED
    assert "gate_expired" in out["cards"][0]["content"]
    assert hits == [], "过期 token 不得执行"


def test_panel_confirm_button_issues_token_before_dispatch():
    """AIPanel 的确认按钮路径：先签发 token，再把 token 交给执行方。"""
    from ai_panel import AIPanel

    class _PanelProbe:
        def __init__(self):
            self.dispatched = None
            self.messages = []

        def _add_system_message(self, text):
            self.messages.append(text)

        def _local_dispatch(self, *args, **kwargs):
            self.dispatched = (args, kwargs)
            return True

    panel = _PanelProbe()
    args = {"frequency_hz": 100_000_000}
    assert AIPanel._confirm_execute(panel, _PROBE, args) is True
    _args, kwargs = panel.dispatched
    token = kwargs["only_args"]["confirm_token"]
    assert token and kwargs["only_tool"] == _PROBE
    assert kwargs["only_args"]["frequency_hz"] == 100_000_000

    armed = [r for r in _records()
             if r["result"] == "armed" and r["token_prefix"] == token[:8]]
    assert armed and armed[-1]["source"] == "desktop"


def test_audit_distinguishes_desktop_from_model(agent):
    """桌面签发落 source=desktop；模型自报被拒落 source=model。"""
    from ai_panel import issue_desktop_confirmation

    args = {"frequency_hz": 100_000_000}
    issue_desktop_confirmation(_PROBE, args)       # 用户在本机点过

    # 模型通道无 token 调硬门工具：只写拒绝审计，不使用任何硬件。
    agent.tool_registry.call_from_model({
        "id": "call_model", "type": "function",
        "function": {"name": "radio_ptt",
                     "arguments": json.dumps({"state": "on"})},
    })

    records = _records()
    armed = [r for r in records if r["result"] == "armed"]
    refused = [r for r in records if r["result"] == "refused"]
    assert any(r["source"] == "desktop" for r in armed)
    assert any(r["source"] == "model" for r in refused), refused
    assert all(r["token_prefix"] for r in armed), "签发记录必须落 token 前 8 位"
    assert len({len(r["token_prefix"]) for r in armed}) == 1
    assert next(len(r["token_prefix"]) for r in armed) == 8
