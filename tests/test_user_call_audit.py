# SPDX-License-Identifier: MIT
"""用户直点工具调用必须真落盘（不是只发进程内事件）。

背景：天气面板那处 ``reg.call("sat_image_enhance", ...)`` 走的是用户触发
（``trigger=user``），只发 ``agent.pre_tool_use`` / ``agent.tool_result``。
若没人挂观测者，磁盘上什么都没有——那等于宣称留痕而实际没留。

选型（写进改动里的理由）：给它接一个真落盘的审计观测者，而不是把那处
改称「本地即时调用」。因为 UI 直点同样是对硬件/文件有副作用的真实调用，
「谁/何时/对哪个工具/结果如何」必须可回溯；观测者挂在 ``ToolRegistry``
构造处，落点与 MCP 面完全相同（``MBDSDR_AUDIT_LOG``，默认
``~/.mbdsdr/audit.jsonl``），不另建第二处账本。

判据：
* 设好 ``MBDSDR_AUDIT_LOG`` 后构造 ``ToolRegistry``（与天气面板同样的
  ``ToolRegistry()``），调一次工具，能在该账本里查到这次调用，含
  ``tool`` / ``trigger`` / ``ok``；
* 该落点与 MCP 面是同一处：同一次测试里两个面写同一个文件。

反证：把 ``ToolRegistry.__init__`` 里的 ``attach_audit_log`` 摘掉，
``test_user_call_lands_in_shared_ledger`` 与 ``test_registry_attaches_observer``
都必须红（账本文件不存在 / ``audit_log is None``）。
"""
from __future__ import annotations

import json

from mbdsdr_ai.core.mcp_server import MCPToolSet
from mbdsdr_ai.hooks import EventType
from mbdsdr_ai.tool_guard import AUDIT_LOG_ENV, TRIGGER_USER
from mbdsdr_ai.tool_registry import ToolRegistry
from mbdsdr_ai.tool_results import ToolResult


def _registry() -> ToolRegistry:
    """与 desktop/weather_panel.py 同款构造：默认参数、自建 hooks。"""
    reg = ToolRegistry()

    def _handler(args):
        return ToolResult(success=True, content="ok", data={"done": True})

    reg.register(
        name="counting_read",
        description="只读工具（测试用）",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=_handler,
        write=False,
    )
    return reg


def _read_ledger(path):
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_registry_attaches_observer(tmp_path, monkeypatch):
    ledger = tmp_path / "audit.jsonl"
    monkeypatch.setenv(AUDIT_LOG_ENV, str(ledger))

    reg = ToolRegistry()

    assert reg.audit_log is not None, "设了 MBDSDR_AUDIT_LOG 就必须挂观测者"
    assert reg.audit_log.path == str(ledger)


def test_user_call_lands_in_shared_ledger(tmp_path, monkeypatch):
    ledger = tmp_path / "audit.jsonl"
    monkeypatch.setenv(AUDIT_LOG_ENV, str(ledger))

    reg = _registry()
    reg.call("counting_read", {})

    rows = _read_ledger(ledger)
    pre = [r for r in rows if r["event_type"] == EventType.AGENT_PRE_TOOL_USE]
    res = [r for r in rows if r["event_type"] == EventType.AGENT_TOOL_RESULT]
    assert len(pre) == 1 and len(res) == 1
    assert pre[0]["data"]["tool"] == "counting_read"
    assert pre[0]["data"]["trigger"] == TRIGGER_USER
    assert res[0]["data"]["tool"] == "counting_read"
    assert res[0]["data"]["trigger"] == TRIGGER_USER
    assert res[0]["data"]["ok"] is True


def test_mcp_and_inproc_write_the_same_ledger(tmp_path, monkeypatch):
    ledger = tmp_path / "audit.jsonl"
    monkeypatch.setenv(AUDIT_LOG_ENV, str(ledger))

    MCPToolSet().call_tool("get_frequency", {})
    _registry().call("counting_read", {})

    rows = _read_ledger(ledger)
    tools = {(r["data"].get("tool"), r["data"].get("trigger")) for r in rows}
    assert ("get_frequency", "model") in tools
    assert ("counting_read", TRIGGER_USER) in tools


def test_no_env_means_no_ledger(tmp_path, monkeypatch):
    """未设环境变量时不挂观测者：库内构造与测试/CI 无副作用。"""
    monkeypatch.delenv(AUDIT_LOG_ENV, raising=False)

    assert ToolRegistry().audit_log is None
