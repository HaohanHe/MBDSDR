# SPDX-License-Identifier: MIT
"""export_vna_report 工具面测试。"""
import numpy as np
import pytest

from mbdsdr_ai.core.mcp_server import MCPToolSet, _TOOL_DOMAIN

F = np.linspace(1e6, 1001e6, 201)


def _pairs(z):
    return [[v.real, v.imag] for v in z]


def _set():
    return MCPToolSet()


def test_registered_write_tool():
    assert _TOOL_DOMAIN.get("export_vna_report") == "导出演制"
    ts = _set()
    full = ts.tools_list_result({"detail": "schema"})[0]
    tools = {t["name"]: t for dom in full["domains"] for t in dom["tools"]}
    assert tools["export_vna_report"]["write"] is True


def test_gated_without_arm(tmp_path):
    ts = _set()
    out = ts.call_tool("export_vna_report", {
        "freqs_hz": F.tolist(),
        "s11": _pairs(0.3 * np.ones(len(F))),
        "path": str(tmp_path / "r.png")})
    assert out.get("gated") is True
    assert not (tmp_path / "r.png").exists()


def test_report_written_with_metadata(tmp_path):
    ts = _set()
    s11 = 0.4 * np.exp(1j * np.linspace(0, 5, len(F)))
    s11[10] = 1.5 + 0j                              # 越界点
    p = tmp_path / "rep.png"
    out = ts.call_tool("export_vna_report", {
        "freqs_hz": F.tolist(), "s11": _pairs(s11),
        "path": str(p), "arm_manual": True})
    assert out.get("ok") is True
    assert out["clipped_points"] == 1
    assert out["points"] == len(F)
    assert p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_bad_input_honest(tmp_path):
    ts = _set()
    out = ts.call_tool("export_vna_report", {
        "freqs_hz": F.tolist(),
        "s11": _pairs(np.full(len(F), np.nan)),
        "path": str(tmp_path / "x.png"), "arm_manual": True})
    assert "error" in out
