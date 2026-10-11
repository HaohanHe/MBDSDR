# SPDX-License-Identifier: MIT
"""MCP tools/list 二级域分组视图（P1 #64）钉扎。

与 C++ 侧 cpp/tests/test_mcp_server.cpp 形成跨语言一致性：
  * 冻结 8 域名集合必须与 C++ mcp_server.cpp 完全一致；
  * 每工具恰好归属一个域，分组后工具总数 == _build_tool_table() 工具数（不丢不重）；
  * 默认 / params.domain / params.detailed 三分支形状一致；未知 domain 报错。

本面（SDRController）当前无 VNA 工具，三个 VNA 域为空卡片——这是诚实空态，
不在此伪造 VNA 工具。
"""
from __future__ import annotations

from mbdsdr_ai.core.mcp_server import (
    FROZEN_DOMAIN_NAMES,
    FROZEN_DOMAIN_SUMMARIES,
    MCPToolSet,
    _build_tool_table,
    _TOOL_DOMAIN,
)

# 与 C++ mcp_server.cpp domainGroups() 冻结的同一组 8 个域名。
EXPECTED_8_DOMAINS = [
    "SDR 调谐/VFO",
    "亚音数字解码",
    "VNA 测量",
    "VNA 分析",
    "VNA 校准",
    "导出演制",
    "书签扫描卫星",
    "系统设备",
]


def _set() -> MCPToolSet:
    return MCPToolSet()  # 无后端，honest empty，不触硬件


def test_frozen_domain_set_matches_cpp():
    """8 域名集合与顺序 == C++ 冻结集合（跨语言钉扎）。"""
    assert FROZEN_DOMAIN_NAMES == EXPECTED_8_DOMAINS
    assert list(FROZEN_DOMAIN_SUMMARIES.keys()) == EXPECTED_8_DOMAINS
    # 每个域都有一句话 summary。
    for name in EXPECTED_8_DOMAINS:
        assert FROZEN_DOMAIN_SUMMARIES[name].strip()


def test_every_tool_in_exactly_one_domain():
    table = _build_tool_table()
    names = [t["name"] for t in table]
    # 每工具恰好一个域：无遗漏、无重复归属。
    assigned = [n for n in names if _TOOL_DOMAIN.get(n) in FROZEN_DOMAIN_NAMES]
    assert len(assigned) == len(set(names)) == len(names)
    # 映射表里没有引用不存在的工具。
    assert set(_TOOL_DOMAIN.keys()) == set(names)


def test_default_grouped_view_loses_nothing():
    ts = _set()
    result, err = ts.tools_list_result({})
    assert err is None
    cards = result["tools"]
    assert len(cards) == 8
    assert [c["domain"] for c in cards] == EXPECTED_8_DOMAINS
    # 展开全部域 tools 名集合 == 表名集合（不丢不重）。
    flat = [n for c in cards for n in c["tools"]]
    table_names = sorted(t["name"] for t in _build_tool_table())
    assert sorted(flat) == table_names
    assert len(flat) == len(set(flat)) == len(table_names)


def test_domain_param_returns_full_schema():
    ts = _set()
    result, err = ts.tools_list_result({"domain": "系统设备"})
    assert err is None
    tools = result["tools"]
    # 该域工具数 == 映射分配数。
    expected = [n for n, d in _TOOL_DOMAIN.items() if d == "系统设备"]
    assert len(tools) == len(expected)
    for t in tools:
        assert {"name", "description", "inputSchema"} <= set(t)


def test_domain_param_fuzzy_and_unknown():
    ts = _set()
    # 容错（小写）。
    result, err = ts.tools_list_result({"domain": "vna 测量"})
    assert err is None
    assert result["tools"] == []  # 本面 VNA 测量为空（诚实空态）
    # 未知 domain -> 错误（列出合法域名）。
    result, err = ts.tools_list_result({"domain": "完全不存在的域"})
    assert result is None
    assert "VNA 测量" in err


def test_detailed_all_domains_full_schema():
    ts = _set()
    result, err = ts.tools_list_result({"detailed": True})
    assert err is None
    doms = result["domains"]
    assert len(doms) == 8
    total = sum(len(d["tools"]) for d in doms)
    assert total == len(_build_tool_table())
    # detailed 卡片里 tools 是完整 schema 条目。
    for d in doms:
        for t in d["tools"]:
            assert {"name", "description", "inputSchema"} <= set(t)


def test_handle_request_tools_list_branches():
    ts = _set()
    # JSON-RPC 层：默认分组。
    r = ts.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert len(r["result"]["tools"]) == 8
    # JSON-RPC 层：未知 domain -> error。
    r = ts.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                          "params": {"domain": "nope"}})
    assert r["error"]["code"] == -32602
