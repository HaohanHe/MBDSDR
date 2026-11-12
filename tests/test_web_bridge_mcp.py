# SPDX-License-Identifier: MIT
"""``mbdsdr_ai/web_bridge_mcp.py`` 的回归：适配器形状 + 真实确认门语义。

这些用例保护的是**安全不变量**，不是功能：
* 前端带来的 token 一律无效（宿主只认自己签的）；
* 没有「本动作的用户确认」→ 恒 gated，且不会签发 token；
* 一次确认只对一个动作摘要有效、只消费一次、有 TTL；
* ``MBDSDR_BRIDGE_CONFIRM=deny`` 时整体只读；
* 真实 ``MCPToolSet`` 的工具面被如实反映（82 工具 / 43 写工具）。
"""
from __future__ import annotations

import json

import pytest

from mbdsdr_ai.tx_guard import action_digest, get_tx_gate, reset_tx_gate
from mbdsdr_ai.web_bridge import WebBridge
from mbdsdr_ai.web_bridge_mcp import (DEFAULT_CONFIRM_TTL_S, PendingConfirms,
                                      build_default_backend, confirm_enabled,
                                      make_confirm_channel,
                                      validate_target)

CONFIRM_ENV = "MBDSDR_BRIDGE_CONFIRM"

#: 本仓代码：MCPToolSet 构造失败或工具面为空 = 回归，不是「环境不具备」（A09）。
#: 这里必须红——改成 skip 会把「内核工具面被改坏」长期伪装成环境不凑巧。
_REGISTRY_ABSENT = (
    "build_default_backend 没有构造出 MCPToolSet registry：MCPToolSet 是本仓代码，"
    "导入/构造失败或工具面为空属于回归，必须红；不得按环境缺失跳过")


# ---------------------------------------------------------------------------
# 假 toolset：复刻 ToolCallGate 的**判定形状**（真门仍走 tx_guard）
# ---------------------------------------------------------------------------
class FakeToolSet:
    def __init__(self) -> None:
        self._by_name = {
            "read_x": {"name": "read_x", "description": "只读", "write": False,
                       "inputSchema": {"type": "object", "properties": {}}},
            "write_x": {"name": "write_x", "description": "写入", "write": True,
                        "inputSchema": {"type": "object",
                                        "properties": {"v": {"type": "integer"}}}},
        }
        self.executed: list = []

    def list_tools(self):
        return [{"name": n, "description": e["description"],
                 "inputSchema": e["inputSchema"]} for n, e in self._by_name.items()]

    def call_tool(self, name, args=None):
        a = dict(args or {})
        entry = self._by_name.get(name)
        if entry is None:
            return {"error": "unknown_tool", "outcome": "error"}
        if entry["write"]:
            token = a.pop("confirm_token", None)
            decision = get_tx_gate().authorize(
                token=token, action=action_digest(name, a))
            if not decision.allowed:
                return {"ok": False, "error": decision.reason,
                        "code": "manual_gate_required", "gated": True,
                        "outcome": "gated"}
        self.executed.append((name, a))
        return {"executed": True, "name": name, "args": a}


@pytest.fixture(autouse=True)
def _clean_gate(monkeypatch):
    reset_tx_gate()
    monkeypatch.delenv(CONFIRM_ENV, raising=False)
    yield
    reset_tx_gate()


def make_bridge(pending=None, ttl_s=30.0):
    from mbdsdr_ai.web_bridge_mcp import McpToolSetRegistry
    ts = FakeToolSet()
    reg = McpToolSetRegistry(ts)
    pending = pending or PendingConfirms()
    bridge = WebBridge(registry=reg,
                       confirm_channel=make_confirm_channel(reg, pending, ttl_s=ttl_s))
    return bridge, reg, pending, ts


# ---------------------------------------------------------------------------
# 适配器形状
# ---------------------------------------------------------------------------
def test_registry_adapter_reflects_tool_table():
    _, reg, _, _ = make_bridge()
    assert reg.get_tool_names() == ["read_x", "write_x"]
    tools = reg.list_tools()
    assert [t["write"] for t in tools] == [False, True]
    assert reg.is_write_tool("write_x") and not reg.is_write_tool("read_x")
    assert reg.schema_of("write_x")["properties"]["v"]["type"] == "integer"
    assert validate_target(reg, "write_x") and not validate_target(reg, "rm_rf")


def test_read_tool_flows_without_confirmation():
    bridge, _, _, ts = make_bridge()
    out = bridge.invoke_tool("read_x", {})
    assert out["ok"] is True
    assert ts.executed == [("read_x", {})]


def test_bare_result_dict_is_not_a_failure():
    """裸结果 dict（无 ok 字段）是工具执行成功，不是 tool_failed。"""
    bridge, _, _, _ = make_bridge()
    out = bridge.invoke_tool("read_x", {})
    assert out["ok"] is True
    assert out["data"] == {"executed": True, "name": "read_x", "args": {}}


# ---------------------------------------------------------------------------
# 门：无确认 / 伪造 token / 一次性 / 换参重放
# ---------------------------------------------------------------------------
def test_write_without_confirmation_is_gated_and_never_executes():
    bridge, _, _, ts = make_bridge()
    out = bridge.invoke_tool("write_x", {"v": 1})
    assert out["ok"] is False
    assert out["gated"] is True and out["notFailure"] is True
    assert out["error"] == "manual_gate_required"
    assert ts.executed == []


def test_frontend_supplied_token_is_ignored():
    """伪造的 confirm_token 既不授权、也不导致额外失败：直接丢弃。"""
    bridge, _, _, ts = make_bridge()
    out = bridge.invoke_tool("write_x", {"v": 1, "confirm_token": "forged-deadbeef"})
    assert out["gated"] is True
    assert ts.executed == []
    # 宿主自己的确认到了，伪造 token 一起送也不影响（先剥外层再签自己的）
    bridge, _, pending, ts = make_bridge()
    pending.request("write_x", {"v": 1})
    out = bridge.invoke_tool("write_x", {"v": 1, "confirm_token": "forged-deadbeef"})
    assert out["ok"] is True
    assert ts.executed == [("write_x", {"v": 1})]


def test_self_reported_arm_manual_is_stripped():
    bridge, _, _, ts = make_bridge()
    out = bridge.invoke_tool("write_x", {"v": 1, "arm_manual": True})
    assert out["gated"] is True
    assert out["selfReportRejected"] == ["arm_manual"]
    assert ts.executed == []


def test_confirmation_is_single_use():
    bridge, _, pending, ts = make_bridge()
    assert pending.request("write_x", {"v": 7})[0] is True
    assert bridge.invoke_tool("write_x", {"v": 7})["ok"] is True
    again = bridge.invoke_tool("write_x", {"v": 7})
    assert again["gated"] is True
    assert len(ts.executed) == 1


def test_confirmation_is_bound_to_exact_arguments():
    bridge, _, pending, _ = make_bridge()
    pending.request("write_x", {"v": 7})
    out = bridge.invoke_tool("write_x", {"v": 8})
    assert out["gated"] is True


def test_confirmation_expires():
    now = {"t": 1000.0}
    pending = PendingConfirms(ttl_s=5.0, clock=lambda: now["t"])
    bridge, _, _, ts = make_bridge(pending)
    pending.request("write_x", {"v": 1})
    now["t"] += 6.0
    out = bridge.invoke_tool("write_x", {"v": 1})
    assert out["gated"] is True
    assert ts.executed == []


def test_revoked_confirmations_do_not_authorize():
    bridge, _, pending, ts = make_bridge()
    pending.request("write_x", {"v": 1})
    pending.revoke_all()
    assert bridge.invoke_tool("write_x", {"v": 1})["gated"] is True
    assert ts.executed == []


def test_unknown_tool_never_gets_a_token():
    bridge, _, pending, _ = make_bridge()
    out = bridge.invoke_tool("os.system", {"cmd": "whoami"})
    # 未注册工具在 WebBridge 层按写工具处理（不存在于写模式表 → 走非写分支），
    # 但通道仍不该签票；直接调通道验证
    channel = bridge.confirm_channel
    res = channel("os.system", {"cmd": "whoami"})
    assert res["ok"] is False and res["error"] == "unknown_tool"
    assert get_tx_gate().authorize().allowed is False


def test_deny_env_makes_it_read_only(monkeypatch):
    monkeypatch.setenv(CONFIRM_ENV, "deny")
    assert confirm_enabled() is False
    pending = PendingConfirms()
    ok, _ = pending.request("write_x", {"v": 1})
    assert ok is False
    bridge, _, _, ts = make_bridge(pending)
    assert bridge.invoke_tool("write_x", {"v": 1})["gated"] is True
    assert ts.executed == []


# ---------------------------------------------------------------------------
# 真实 MCPToolSet 工具面（无硬件，只断言形状与门，不碰设备）
# ---------------------------------------------------------------------------
def test_real_toolset_face_is_wired():
    bridge, reg, pending = build_default_backend()
    assert reg is not None, _REGISTRY_ABSENT
    names = reg.get_tool_names()
    assert len(names) >= 80, f"工具面异常小: {len(names)}"
    writes = [n for n in names if reg.is_write_tool(n)]
    assert 30 <= len(writes) <= 60, f"写工具数异常: {len(writes)}"
    caps = bridge.get_capabilities()
    assert len(caps) == len(names)
    assert any(c["write"] for c in caps)
    assert bridge.confirm_wired is True
    assert bridge.manifest()["gate"]["mcp"]["confirmChannelWired"] is True


def test_real_write_tool_is_gated_without_confirmation():
    bridge, reg, pending = build_default_backend()
    assert reg is not None, _REGISTRY_ABSENT
    out = bridge.invoke_tool("set_frequency", {"hz": 100e6})
    assert out["gated"] is True
    assert out["error"] == "manual_gate_required"
    assert pending.pending() == []


def test_real_write_tool_passes_gate_after_confirmation():
    """确认后门必须放行：无设备时应是「已调用但未下发硬件」，**不能**还是 gated。"""
    bridge, reg, pending = build_default_backend()
    assert reg is not None, _REGISTRY_ABSENT
    assert pending.request("set_frequency", {"hz": 100e6})[0] is True
    out = bridge.invoke_tool("set_frequency", {"hz": 100e6})
    assert not out.get("gated"), f"门没放行: {json.dumps(out, ensure_ascii=False)[:300]}"
    assert out["ok"] is True
    # 无设备时必须如实暴露「没下发」，不能显示成调谐成功
    assert out.get("applied") is False
    assert out.get("degraded") is True
    assert "未连接" in str(out.get("note", ""))


def test_manifest_not_verified_is_conditional():
    bridge, reg, _ = build_default_backend()
    assert reg is not None, _REGISTRY_ABSENT
    nv = bridge.manifest()["notVerified"]
    assert any("WebChannel" in s for s in nv)
    assert any("physically isolated" in s or "same-origin" in s for s in nv)
    assert not any("not wired" in s for s in nv)


def test_pending_listing_shows_expiry():
    pending = PendingConfirms(ttl_s=DEFAULT_CONFIRM_TTL_S)
    pending.request("write_x", {"v": 1})
    items = pending.pending()
    assert len(items) == 1 and items[0]["tool"] == "write_x"
    assert 0 < items[0]["expiresInS"] <= DEFAULT_CONFIRM_TTL_S


# ---------------------------------------------------------------------------
# 宿主端到端：/bridge/confirm + /bridge/invokeTool（真实 MCPToolSet）
# ---------------------------------------------------------------------------
def _host_calls(host, method, payload):
    raw = host.dispatch("POST", "/bridge/" + method,
                        json.dumps(payload).encode("utf-8"),
                        {"X-MBDSDR-Bridge": host.token, "Host": "127.0.0.1"})
    return raw[0], json.loads(raw[1])


def test_host_confirm_flow_end_to_end():
    from mbdsdr_ai.web_bridge_host import BridgeHost
    host = BridgeHost(port=0, mcp=True)
    assert host.registry is not None, _REGISTRY_ABSENT

    shim = host.dispatch("GET", "/bridge.js", b"", {"Host": "127.0.0.1"})[1].decode("utf-8")
    assert "confirm: function" in shim and "canConfirm: true" in shim
    assert host.bootstrap()["canConfirm"] is True
    assert host.bootstrap()["toolCount"] >= 80

    code, out = _host_calls(host, "invokeTool",
                            {"name": "set_frequency", "args": {"hz": 145e6}})
    assert code == 200 and out["gated"] is True

    code, out = _host_calls(host, "confirm",
                            {"tool": "set_frequency", "args": {"hz": 145e6}})
    assert code == 200 and out["ok"] is True and out["pending"] is True

    code, out = _host_calls(host, "invokeTool",
                            {"name": "set_frequency", "args": {"hz": 145e6}})
    assert not out.get("gated"), out
    assert out["ok"] is True and out.get("applied") is False

    # 一次性：再来一次必须回到 gated
    code, out = _host_calls(host, "invokeTool",
                            {"name": "set_frequency", "args": {"hz": 145e6}})
    assert out["gated"] is True


def test_host_confirm_rejects_unknown_tool_and_deny_mode(monkeypatch):
    from mbdsdr_ai.web_bridge_host import BridgeHost
    host = BridgeHost(port=0, mcp=True)
    assert host.registry is not None, _REGISTRY_ABSENT
    code, out = _host_calls(host, "confirm", {"tool": "os.system", "args": {}})
    assert out["ok"] is False and out["error"] == "unknown_tool"

    monkeypatch.setenv(CONFIRM_ENV, "deny")
    code, out = _host_calls(host, "confirm",
                            {"tool": "set_frequency", "args": {"hz": 145e6}})
    assert out["ok"] is False and out["error"] == "confirm_disabled"
    # deny 模式下写工具仍然 gated
    code, out = _host_calls(host, "invokeTool",
                            {"name": "set_frequency", "args": {"hz": 145e6}})
    assert out["gated"] is True


def test_host_no_mcp_stays_offline_but_has_confirm_table():
    from mbdsdr_ai.web_bridge_host import BridgeHost
    host = BridgeHost(port=0, mcp=False)
    assert host.registry is None
    assert host.bridge.is_live() is False
    assert host.bootstrap()["live"] is False
    # 没有真实工具面 → 不给确认按钮，写工具在 WebBridge 层就 gated
    code, out = _host_calls(host, "invokeTool",
                            {"name": "set_frequency", "args": {"hz": 1e6}})
    assert out.get("gated") is True or out.get("offline") is True
