# SPDX-License-Identifier: MIT
"""MCP tools/list 二级域分组视图（P1 #64）钉扎。

与 C++ 侧 cpp/tests/test_mcp_server.cpp 形成跨语言一致性：
  * 冻结 9 域名集合必须与 C++ mcp_server.cpp 完全一致；
  * 每工具恰好归属一个域，分组后工具总数 == _build_tool_table() 工具数（不丢不重）；
  * 默认 / params.domain / params.detailed 三分支形状一致；未知 domain 报错。

本面（SDRController）当前无 VNA/记忆工具，VNA 三域与「记忆编排」为空卡片——
这是诚实空态，不在此伪造 VNA/记忆工具。
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

from mbdsdr_ai.core.mcp_server import (
    ALL_DOMAIN_NAMES,
    FROZEN_DOMAIN_NAMES,
    FROZEN_DOMAIN_SUMMARIES,
    MCPToolSet,
    PYTHON_EXTENSION_DOMAIN_NAMES,
    PYTHON_EXTENSION_DOMAIN_SUMMARIES,
    _build_tool_table,
    _prepare_stdio_utf8,
    _reconfigure_to_utf8,
    _TOOL_DOMAIN,
)

# 与 C++ mcp_server.cpp domainGroups() 冻结的同一组 10 个域名。
EXPECTED_10_DOMAINS = [
    "SDR 调谐/VFO",
    "亚音数字解码",
    "VNA 测量",
    "VNA 分析",
    "VNA 校准",
    "导出演制",
    "书签扫描卫星",
    "系统设备",
    "记忆编排",
    "工作台编排",
]

# Python 面扩展域（P0 #65）——不与 C++ 冻结，仅本 server 追加。
EXPECTED_EXTENSION_DOMAINS = ["解码器长尾"]

# 长尾解码器工具（P0 #65）必须挂入工具表且归属「解码器长尾」。
EXPECTED_LONGTAIL_TOOLS = [
    "decoder_dab_fic_decode",
    "decoder_nrsc5_frame_parse",
    "decoder_rtl433_list",
    "decoder_rtl433_decode",
    "decoder_gk2a_parse_headers",
    "decoder_fst4_encode_text",
    "device_bladerf_params_query",
]


def _set() -> MCPToolSet:
    return MCPToolSet()  # 无后端，honest empty，不触硬件


def test_frozen_domain_set_matches_cpp():
    """10 域名集合与顺序 == C++ 冻结集合（跨语言钉扎，不受 Python 扩展影响）。"""
    assert FROZEN_DOMAIN_NAMES == EXPECTED_10_DOMAINS
    assert list(FROZEN_DOMAIN_SUMMARIES.keys()) == EXPECTED_10_DOMAINS
    # 每个域都有一句话 summary。
    for name in EXPECTED_10_DOMAINS:
        assert FROZEN_DOMAIN_SUMMARIES[name].strip()


def test_python_extension_domain_is_separate():
    """Python 扩展域独立追加，不污染冻结 10 域。"""
    assert PYTHON_EXTENSION_DOMAIN_NAMES == EXPECTED_EXTENSION_DOMAINS
    assert ALL_DOMAIN_NAMES == EXPECTED_10_DOMAINS + EXPECTED_EXTENSION_DOMAINS
    for name in EXPECTED_EXTENSION_DOMAINS:
        assert PYTHON_EXTENSION_DOMAIN_SUMMARIES[name].strip()
        # 扩展域不属于 C++ 冻结集合。
        assert name not in EXPECTED_10_DOMAINS


def test_every_tool_in_exactly_one_domain():
    table = _build_tool_table()
    names = [t["name"] for t in table]
    # 每工具恰好一个域：无遗漏、无重复归属。
    assigned = [n for n in names if _TOOL_DOMAIN.get(n) in ALL_DOMAIN_NAMES]
    assert len(assigned) == len(set(names)) == len(names)
    # 映射表里没有引用不存在的工具。
    assert set(_TOOL_DOMAIN.keys()) == set(names)


def test_longtail_tools_registered_and_grouped():
    table = _build_tool_table()
    names = {t["name"] for t in table}
    # 7 个长尾工具全部在表内，且全部归属「解码器长尾」。
    for t in EXPECTED_LONGTAIL_TOOLS:
        assert t in names
        assert _TOOL_DOMAIN[t] == "解码器长尾"
    # 该域工具数 == 长尾工具数（不混入冻结域）。
    lt = [n for n, d in _TOOL_DOMAIN.items() if d == "解码器长尾"]
    assert sorted(lt) == sorted(EXPECTED_LONGTAIL_TOOLS)


def test_default_grouped_view_loses_nothing():
    ts = _set()
    result, err = ts.tools_list_result({})
    assert err is None
    cards = result["tools"]
    # 8 冻结域 + 1 Python 扩展域 = 9 张卡片，顺序冻结在前。
    assert len(cards) == len(ALL_DOMAIN_NAMES)
    assert [c["domain"] for c in cards] == ALL_DOMAIN_NAMES
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


def test_domain_param_resolves_python_extension():
    ts = _set()
    result, err = ts.tools_list_result({"domain": "解码器长尾"})
    assert err is None
    names = sorted(t["name"] for t in result["tools"])
    assert names == sorted(EXPECTED_LONGTAIL_TOOLS)


def test_domain_param_fuzzy_and_unknown():
    ts = _set()
    # 容错（小写）。
    result, err = ts.tools_list_result({"domain": "vna 测量"})
    assert err is None
    assert result["tools"] == []  # 本面 VNA 测量为空（诚实空态）
    # 未知 domain -> 错误（列出合法域名，含扩展域）。
    result, err = ts.tools_list_result({"domain": "完全不存在的域"})
    assert result is None
    assert "VNA 测量" in err
    assert "解码器长尾" in err


def test_detailed_all_domains_full_schema():
    ts = _set()
    result, err = ts.tools_list_result({"detailed": True})
    assert err is None
    doms = result["domains"]
    assert len(doms) == len(ALL_DOMAIN_NAMES)
    total = sum(len(d["tools"]) for d in doms)
    assert total == len(_build_tool_table())
    # detailed 卡片里 tools 是完整 schema 条目。
    for d in doms:
        assert {"domain", "summary", "tools"} <= set(d)
        for t in d["tools"]:
            assert {"name", "description", "inputSchema"} <= set(t)


def test_handle_request_tools_list_branches():
    ts = _set()
    # JSON-RPC 层：默认分组。
    r = ts.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert len(r["result"]["tools"]) == len(ALL_DOMAIN_NAMES)
    # JSON-RPC 层：未知 domain -> error。
    r = ts.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                          "params": {"domain": "nope"}})
    assert r["error"]["code"] == -32602


# ----------------------------------------------------------------------
# tools/list 多级详情分层（detail=name/desc/schema）+ listChanged 缓存语义
# ----------------------------------------------------------------------
def test_detail_name_level_bare_names():
    ts = _set()
    result, err = ts.tools_list_result({"detail": "name"})
    assert err is None
    # 域卡片 tools 项是裸名字串（无 description 键）。
    for card in result["tools"]:
        for t in card["tools"]:
            assert isinstance(t, str)


def test_detail_desc_level_name_plus_description_no_schema():
    ts = _set()
    result, err = ts.tools_list_result({"detail": "desc"})
    assert err is None
    seen_desc = 0
    for card in result["tools"]:
        for t in card["tools"]:
            assert {"name", "description"} <= set(t)
            assert "inputSchema" not in t
            seen_desc += 1
    assert seen_desc == len(_build_tool_table())


def test_detail_schema_level_full():
    ts = _set()
    result, err = ts.tools_list_result({"detail": "schema"})
    assert err is None
    assert "domains" in result
    for card in result["domains"]:
        for t in card["tools"]:
            assert {"name", "description", "inputSchema"} <= set(t)


def test_detailed_true_alias_equals_schema():
    ts = _set()
    a, _ = ts.tools_list_result({"detail": "schema"})
    b, _ = ts.tools_list_result({"detailed": True})
    assert a == b


def test_detail_unknown_value_errors():
    ts = _set()
    result, err = ts.tools_list_result({"detail": "banana"})
    assert result is None
    assert "name" in err and "desc" in err and "schema" in err


def test_domain_param_respects_explicit_detail():
    ts = _set()
    # 显式 detail=desc：单域条目含 name+description 但无 inputSchema。
    result, err = ts.tools_list_result({"domain": "系统设备", "detail": "desc"})
    assert err is None
    assert result["tools"]
    for t in result["tools"]:
        assert {"name", "description"} <= set(t)
        assert "inputSchema" not in t


def test_initialize_declares_list_changed():
    ts = _set()
    r = ts.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r["result"]["capabilities"]["tools"]["listChanged"] is True


def test_notification_list_changed_no_stdout_when_untagged():
    ts = _set()
    # 无 id 的通知 -> None（不回 stdout）。
    assert ts.handle_request({"jsonrpc": "2.0",
                              "method": "notifications/tools/list_changed"}) is None
    # 带 id（误用为请求）-> result:null。
    r = ts.handle_request({"jsonrpc": "2.0", "id": 9,
                          "method": "notifications/tools/list_changed"})
    assert r["result"] is None


# ----------------------------------------------------------------------
# 长尾工具行为钉扎（P0 #65）：真实调用公开纯函数，诚实错误 / PENDING 标注。
# ----------------------------------------------------------------------
def test_longtail_fst4_encoder_real_roundtrip():
    ts = _set()
    out = ts.call_tool("decoder_fst4_encode_text", {"text": "CQ BI4MIB OM74"})
    assert out["n_tones"] == 160
    assert len(out["tones"]) == 160
    # FST4 同步块固定在 0/38/76/114/152，第一块为 SYNC_WORD1。
    assert out["tones"][:8] == [0, 1, 3, 2, 1, 0, 2, 3]
    assert out["real_hardware_verified"] is False


def test_longtail_rtl433_list_honest():
    ts = _set()
    out = ts.call_tool("decoder_rtl433_list", {})
    assert out["count"] == len(out["protocols"]) >= 1
    assert {"device_type", "name"} <= set(out["protocols"][0])


def test_longtail_bladerf_params_query_and_check():
    ts = _set()
    out = ts.call_tool("device_bladerf_params_query",
                       {"model": "bladerf2_micro", "freq_hz": 433.92e6,
                        "sample_rate_hz": 1e6, "bandwidth_hz": 200e3})
    assert out["model"] == "bladerf2_micro"
    assert out["checks"]["freq_in_range"] is True
    assert out["checks"]["sample_rate_in_range"] is True
    # 低于 70MHz 下限应判 False（诚实校验，不造假）。
    bad = ts.call_tool("device_bladerf_params_query", {"freq_hz": 10e6})
    assert bad["checks"]["freq_in_range"] is False


def test_longtail_bad_inputs_honest_errors():
    ts = _set()
    # 空输入 / 坏 hex -> 诚实错误，不崩。
    assert ts.call_tool("decoder_dab_fic_decode", {"fic_hex": ""})["error"] == "empty_input"
    assert ts.call_tool("decoder_nrsc5_frame_parse", {"data_hex": "zz"})["error"] == "bad_hex"
    # 未知 device_type -> 诚实建议。
    out = ts.call_tool("decoder_rtl433_decode",
                       {"device_type": "nope", "message_hex": "aabb"})
    assert out["error"] == "unknown_device_type"


def test_write_tools_manual_gate_aligns_cpp():
    """Python MCP 写工具手动 gate：无 arm_manual 拒、有则执行；只读工具不 gate；
    schema 视图暴露 write 字段（与 C++ 信封对齐）。"""
    ts = _set()
    # 1) 写工具无 arm -> gated 信封（同 C++ gatedToolResult 形状）。
    g = ts.call_tool("set_frequency", {"hz": 100e6})
    assert g == {"ok": False, "error": "手动模式：未执行 set_frequency", "gated": True}
    # 2) arm_manual=true 才执行（无后端仍诚实 not_connected 而非 gate）。
    r = ts.call_tool("set_frequency", {"hz": 100e6, "arm_manual": True})
    assert "gated" not in r
    # 3) arm_manual 不透传（消费后不落在 handler 参数里）：读工具不受 gate 影响。
    ro = ts.call_tool("get_frequency", {})
    assert "gated" not in ro
    # 4) schema 视图含 write 标记。
    full = ts.tools_list_result({"detail": "schema"})[0]
    all_tools = {}
    for dom in full["domains"]:
        for t in dom["tools"]:
            all_tools[t["name"]] = t
    assert all_tools["set_frequency"]["write"] is True
    assert all_tools["tune_satellite"]["write"] is True
    # 只读工具 write=False。
    any_read = next(t for t in all_tools.values() if not t["write"])
    assert any_read is not None


# ----------------------------------------------------------------------
# stdio 编码启动准备（中文 Windows cp936/GBK 回归）
#
# JSON-RPC 响应行按 UTF-8 约定传输（ensure_ascii=False 写中文/µ），但
# 中文 Windows 上进程 stdout 默认落 ANSI 代码页，响应行失去 UTF-8 合法性，
# 严格按约定解析的外部客户端会解帧崩溃。启动准备必须先于 stdin/stdout
# 读写把输出流切成 UTF-8。
# ----------------------------------------------------------------------
def test_prepare_reconfigures_ansi_codepage_stream_to_utf8():
    """cp936 文本流经准备逻辑后：编码必须变 utf-8、errors=replace（确定性断言）。"""
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp936")
    assert stream.encoding != "utf-8"  # 前提：确实是 ANSI 代码页坏编码
    assert _reconfigure_to_utf8(stream) is True
    assert stream.encoding.lower() == "utf-8"
    assert stream.errors == "replace"  # 不可编码字符退化替换，不炸协议流


def test_prepare_silently_skips_stream_without_reconfigure():
    """无 reconfigure 的流（io.StringIO 等替身 / 旧 Python）静默跳过，不炸启动。"""
    stream = io.StringIO()
    assert not hasattr(stream, "reconfigure")
    assert _reconfigure_to_utf8(stream) is False


def test_prepare_stdio_utf8_forces_real_stdout():
    """启动准备执行后，进程真实 sys.stdout 编码必须为 utf-8。"""
    _prepare_stdio_utf8()
    assert sys.stdout.encoding.lower().replace("_", "-") == "utf-8"


def test_child_pythonioencoding_gbk_becomes_utf8_after_prepare():
    """端到端：PYTHONIOENCODING=cp936 起子进程复现中文 Windows 坏环境，
    执行入口启动准备后 stdout 编码必须从 cp936 变成 utf-8（修复前会失败）。"""
    code = (
        "import sys;"
        "from mbdsdr_ai.core.mcp_server import _prepare_stdio_utf8;"
        "before = sys.stdout.encoding;"
        "_prepare_stdio_utf8();"
        "print(before);"
        "print(sys.stdout.encoding);"
        "print(sys.stdout.errors)"
    )
    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root),
             "PYTHONIOENCODING": "cp936"},
    )
    assert proc.returncode == 0, proc.stderr
    before, after, errors = proc.stdout.strip().splitlines()
    # 子进程确实被钉在 GBK 家族（否则本用例失去回归意义）。
    assert before.lower() in ("cp936", "gbk")
    assert after.lower() == "utf-8"
    assert errors == "replace"
