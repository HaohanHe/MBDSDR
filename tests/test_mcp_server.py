# SPDX-License-Identifier: MIT
"""
MCP 接口确定性测试。

验证：
  * 工具列表完整（与 controller 能力一一对应）；
  * 每个工具 inputSchema 是合法 JSON Schema object；
  * 无后端时工具安全降级为 {"error": "not_connected"}；
  * JSON-RPC initialize / tools/list / tools/call 分发正确。
"""

from __future__ import annotations

import json

import pytest

from mbdsdr_ai.core import SDRController
from mbdsdr_ai.core.mcp_server import MCPToolSet, _build_tool_table


# 任务书点名必须暴露的核心工具
REQUIRED_TOOLS = {
    "list_devices", "connect", "set_frequency", "set_demod",
    "start_audio", "read_spectrum", "start_scan", "start_decoder",
    "tune_satellite", "add_bookmark", "start_recording",
    "start_remote_control", "set_anr_enabled", "get_status",
}


@pytest.fixture
def toolset(tmp_path):
    c = SDRController(config_dir=str(tmp_path))
    ts = MCPToolSet(c)
    yield ts, c
    try:
        c.shutdown()
    except Exception:
        pass


def test_tool_list_complete(toolset):
    ts, _ = toolset
    names = {t["name"] for t in ts.list_tools()}
    assert REQUIRED_TOOLS.issubset(names), f"缺少工具: {REQUIRED_TOOLS - names}"


def test_every_tool_has_object_schema(toolset):
    ts, _ = toolset
    for t in ts.list_tools():
        assert t["inputSchema"]["type"] == "object", f"{t['name']} schema 不是 object"
        assert "properties" in t["inputSchema"]
        assert isinstance(t["description"], str) and t["description"]


def test_no_backend_safe_degradation(toolset):
    """未连接时 read_spectrum / read_iq 返回 not_connected，不崩。"""
    ts, _ = toolset
    spec = ts.call_tool("read_spectrum", {"nfft": 128})
    assert spec["error"] == "not_connected"
    assert "suggestion" in spec
    iq = ts.call_tool("read_iq", {"n": 64})
    assert iq["error"] == "not_connected"


def test_unknown_tool_returns_error(toolset):
    ts, _ = toolset
    out = ts.call_tool("does_not_exist", {})
    assert out["error"] == "unknown_tool"


def test_call_with_debug_backend(toolset):
    ts, c = toolset
    c.connect("debug")
    # 写工具有外部确认 token 门；直调 call_tool 同样过门。
    from mbdsdr_ai.tx_guard import issue_confirmation
    tok = issue_confirmation("set_frequency", {"hz": 98_500_000})
    out = ts.call_tool("set_frequency",
                       {"hz": 98_500_000, "confirm_token": tok})
    # 已连接时回执带 requested_hz 等元数据（2026-11-05 诚实性批次），
    # 但必须仍是成功且真的下发。
    assert out["ok"] is True
    assert out.get("requested_hz") == 98_500_000
    assert c.get_frequency() == 98_500_000
    spec = ts.call_tool("read_spectrum", {"nfft": 128})
    assert spec["length"] == 128
    status = ts.call_tool("get_status", {})
    assert status["connected"] is True


def test_jsonrpc_initialize(toolset):
    ts, _ = toolset
    resp = ts.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert resp["result"]["serverInfo"]["name"] == "mbdsdr-core"
    assert resp["id"] == 1


def test_jsonrpc_tools_list(toolset):
    ts, _ = toolset
    resp = ts.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    # 默认视图是域卡片（domain cards）：摊平两种形状（裸工具 / 分组卡片）
    # 再取名字，兼容未来默认视图调整。
    names = set()
    for entry in resp["result"]["tools"]:
        if "name" in entry:
            names.add(entry["name"])
        for t in entry.get("tools", []) or []:
            if isinstance(t, dict) and "name" in t:
                names.add(t["name"])
            elif isinstance(t, str):
                names.add(t)
    assert REQUIRED_TOOLS.issubset(names)


def test_jsonrpc_tools_call(toolset):
    ts, c = toolset
    c.connect("debug")
    # 写工具有外部确认 token 门：不带 token 必须被拦（gated），不下发硬件。
    gated = ts.handle_request({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "set_demod", "arguments": {"mode": "FM"}},
    })
    gtext = gated["result"]["content"][0]["text"]
    assert json.loads(gtext).get("gated") is True
    # 带外部确认 token 后正常执行。
    from mbdsdr_ai.tx_guard import issue_confirmation
    tok = issue_confirmation("set_demod", {"mode": "FM"})
    resp = ts.handle_request({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "set_demod",
                   "arguments": {"mode": "FM", "confirm_token": tok}},
    })
    text = resp["result"]["content"][0]["text"]
    # 已连接时回执带 requested_mode 元数据（2026-11-05 诚实性批次）。
    assert json.loads(text)["ok"] is True
    assert c.get_demod() == "FM"


def test_notification_has_no_response(toolset):
    ts, _ = toolset
    resp = ts.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert resp is None
