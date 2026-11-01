# SPDX-License-Identifier: MIT
"""sat_link_* 工具面测试（MCP 表 + Agent tool_registry 双注册）。"""
import pytest

from mbdsdr_ai.core.mcp_server import MCPToolSet, _TOOL_DOMAIN
from mbdsdr_ai.sat_links import (find_tuning_for, list_link_sats,
                                 load_sat_link)

TOOLS = ("sat_link_list", "sat_link_get", "sat_link_identify", "sat_link_tuning")


def _first_downlink():
    """取目录里第一条下行频点（测试不硬编码频点字面量）。"""
    for name in list_link_sats():
        entry = load_sat_link(name)
        for link in entry.get("links", []):
            if link.get("direction") == "downlink":
                return name, float(link["freq_hz"])
    pytest.skip("链路目录无下行频点条目")


# ── MCP 表注册 ──────────────────────────────────────────────
def test_domain_registration():
    for t in TOOLS:
        assert _TOOL_DOMAIN.get(t) == "卫星链路"


def test_listed_read_only():
    ts = MCPToolSet()
    full = ts.tools_list_result({"detail": "schema"})[0]
    tools = {t["name"]: t for dom in full["domains"] for t in dom["tools"]}
    for name in TOOLS:
        assert name in tools, f"{name} 不在 MCP 工具表"
        assert tools[name]["write"] is False


def test_list_matches_library():
    ts = MCPToolSet()
    out = ts.call_tool("sat_link_list", {})
    assert out["sats"] == list_link_sats()
    assert len(out["sats"]) > 0


def test_get_known_and_unknown():
    ts = MCPToolSet()
    name = list_link_sats()[0]
    out = ts.call_tool("sat_link_get", {"name": name})
    assert out["_link_name"] == name
    bad = ts.call_tool("sat_link_get", {"name": "NO_SUCH_SAT_XYZ"})
    assert bad.get("outcome") == "error"
    assert "可用" in bad.get("message", "")


def test_identify_hit_and_miss():
    ts = MCPToolSet()
    _name, freq = _first_downlink()
    hit = ts.call_tool("sat_link_identify", {"freq_hz": freq})
    assert len(hit["hits"]) >= 1
    miss = ts.call_tool("sat_link_identify", {"freq_hz": 1.0})
    assert miss["hits"] == []
    assert "note" in miss
    bad = ts.call_tool("sat_link_identify",
                       {"freq_hz": freq, "tolerance_hz": -1.0})
    assert bad.get("outcome") == "error"


def test_tuning_found_and_honest_miss():
    ts = MCPToolSet()
    name, _freq = _first_downlink()
    entry = load_sat_link(name)
    tle_name = entry.get("norad_name") or name
    out = ts.call_tool("sat_link_tuning", {"norad_name": tle_name})
    assert out["found"] is True
    assert out["downlink"]["direction"] == "downlink"
    miss = ts.call_tool("sat_link_tuning", {"norad_name": "NO_SUCH_SAT_XYZ"})
    assert miss["found"] is False
    assert "note" in miss


# ── Agent tool_registry 面 ──────────────────────────────────
def test_agent_surface_registration():
    from mbdsdr_ai.sdr_tools import register_sdr_tools
    from mbdsdr_ai.tool_registry import ToolRegistry

    class _FakeAgent:
        tool_registry = ToolRegistry()

    register_sdr_tools(_FakeAgent())
    names = set(_FakeAgent.tool_registry.get_tool_names())
    assert set(TOOLS) <= names


def test_agent_surface_call_list():
    from mbdsdr_ai.sdr_tools import register_sdr_tools
    from mbdsdr_ai.tool_registry import ToolRegistry

    class _FakeAgent:
        tool_registry = ToolRegistry()

    register_sdr_tools(_FakeAgent())
    res = _FakeAgent.tool_registry.call("sat_link_list", {})
    assert res.success
    assert res.data["sats"] == list_link_sats()
