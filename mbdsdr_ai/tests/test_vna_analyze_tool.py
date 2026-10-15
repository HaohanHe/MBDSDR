# SPDX-License-Identifier: MIT
"""vna_analyze_sweep MCP 工具面测试。"""
import numpy as np
import pytest

from mbdsdr_ai.core.mcp_server import MCPToolSet, _TOOL_DOMAIN

C = 299792458.0
VF = 0.66
Z0 = 50.0
F = np.linspace(1e6, 1001e6, 201)


def _cable_s11(l_m=2.0, z1=75.0):
    rho = (z1 - Z0) / (z1 + Z0)
    beta = 2 * np.pi * F / (VF * C)
    return rho * np.exp(-2j * beta * l_m)


def _pairs(s):
    return [[v.real, v.imag] for v in s]


def _set():
    return MCPToolSet()


def test_tool_registered_read_only():
    assert _TOOL_DOMAIN.get("vna_analyze_sweep") == "VNA 分析"
    ts = _set()
    full = ts.tools_list_result({"detail": "schema"})[0]
    tools = {t["name"]: t for dom in full["domains"] for t in dom["tools"]}
    assert tools["vna_analyze_sweep"]["write"] is False   # 只读分析


def test_analyze_cable_via_tool():
    ts = _set()
    out = ts.call_tool("vna_analyze_sweep", {
        "freqs_hz": F.tolist(), "s11": _pairs(_cable_s11()),
        "cable": "RG-58"})
    assert out["tdr"]["length_m"] == pytest.approx(2.0, rel=0.1)
    assert out["tdr"]["vf"] == pytest.approx(0.66)
    assert out["points"] == len(F)
    # 只读工具：无 arm_manual 也不 gated
    assert "gated" not in out


def test_analyze_honest_bad_inputs():
    ts = _set()
    out = ts.call_tool("vna_analyze_sweep", {
        "freqs_hz": F.tolist()[:50], "s11": _pairs(_cable_s11())})
    assert "error" in out                          # 长度不齐诚实报错
    out2 = ts.call_tool("vna_analyze_sweep", {
        "freqs_hz": F.tolist(), "s11": _pairs(_cable_s11()),
        "cable": "RG-9999"})
    assert "error" in out2                         # 未知电缆诚实报错
