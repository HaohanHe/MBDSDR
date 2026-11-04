# SPDX-License-Identifier: MIT
"""set_deemphasis MCP 工具面测试（sdrpp 缺口收尾）。"""
import pytest

from mbdsdr_ai.core.mcp_server import MCPToolSet


def _tok():
    """外部确认通道（测试）：签发一次性 token（S13 后 arm_manual 不再放行）。"""
    from mbdsdr_ai.tx_guard import get_tx_gate
    return get_tx_gate().arm(ttl_s=60)


def _set():
    return MCPToolSet()


def test_gated_without_arm():
    """写工具：无 arm_manual -> gated 信封（不下发）。"""
    ts = _set()
    out = ts.call_tool("set_deemphasis", {"tau_us": "50us"})
    assert out["ok"] is False and out["gated"] is True
    assert out["policy"] == "ask" and out["outcome"] == "gated"


def test_armed_no_backend_is_honest_error():
    """武装后仍无后端 -> 诚实 sdr_error，不伪造 ok=True。"""
    ts = _set()
    out = ts.call_tool("set_deemphasis", {"tau_us": "50us",
                                          "confirm_token": _tok()})
    assert out["error"] == "sdr_error"
    assert "后端" in out["message"]
    assert "RTL-SDR" in out["suggestion"]
    assert out["outcome"] == "error"


def test_bad_grade_rejected():
    """非法档位 -> 诚实错误（不落到默认档装成功）。"""
    ts = _set()
    out = ts.call_tool("set_deemphasis", {"tau_us": "35us",
                                          "confirm_token": _tok()})
    assert out["outcome"] == "error"


def test_tool_registered_and_domain_mapped():
    """工具已注册且归属 SDR 调谐/VFO 域（分组视图不丢）。"""
    from mbdsdr_ai.core.mcp_server import _TOOL_DOMAIN, _build_tool_table
    assert "set_deemphasis" in _TOOL_DOMAIN
    assert _TOOL_DOMAIN["set_deemphasis"] == "SDR 调谐/VFO"
    names = {t["name"] for t in _build_tool_table()}
    assert "set_deemphasis" in names
    specs = {t["name"]: t for t in _build_tool_table()}
    assert specs["set_deemphasis"].get("write") is True


def test_session_approval_path():
    """approve_session 后同参免问（审批缓存在门层生效）。"""
    ts = _set()
    ts.call_tool("set_deemphasis",
                 {"tau_us": "75us", "confirm_token": _tok(),
                  "approve_session": True})
    out = ts.call_tool("set_deemphasis", {"tau_us": "75us"})
    # 免问命中 -> 走 handler -> 无后端 -> 诚实错误（而非 gated）
    assert out.get("outcome") == "error"
