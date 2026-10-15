# SPDX-License-Identifier: MIT
"""export_spectrum_snapshot MCP 工具面测试（SDR 侧登记缺口销账）。"""
import os

import numpy as np

from mbdsdr_ai.core.mcp_server import MCPToolSet, _TOOL_DOMAIN

F = np.linspace(88e6, 108e6, 256)
DB = -60 + 25 * np.exp(-((F - 98e6) / 2e6) ** 2)


def _set() -> MCPToolSet:
    return MCPToolSet()


def test_tool_registered_and_grouped():
    assert _TOOL_DOMAIN.get("export_spectrum_snapshot") == "导出演制"
    ts = _set()
    names = [t["name"] for t in ts._tool_table.values()] if hasattr(
        ts, "_tool_table") else None
    full = ts.tools_list_result({"detail": "schema"})[0]
    all_tools = {t["name"]: t for dom in full["domains"] for t in dom["tools"]}
    spec = all_tools["export_spectrum_snapshot"]
    assert spec["write"] is True


def test_gated_without_arm(tmp_path):
    ts = _set()
    out = ts.call_tool("export_spectrum_snapshot", {
        "freqs_hz": F.tolist(), "db": DB.tolist(),
        "path": str(tmp_path / "s.png")})
    assert out.get("gated") is True
    assert not (tmp_path / "s.png").exists()


def test_export_writes_valid_png(tmp_path):
    ts = _set()
    p = tmp_path / "snap.png"
    out = ts.call_tool("export_spectrum_snapshot", {
        "freqs_hz": F.tolist(), "db": DB.tolist(), "path": str(p),
        "arm_manual": True})
    assert out.get("ok") is True
    data = p.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert out["bytes"] == len(data) > 500


def test_bad_data_honest_error(tmp_path):
    ts = _set()
    out = ts.call_tool("export_spectrum_snapshot", {
        "freqs_hz": F.tolist(), "db": [float("nan")] * len(F),
        "path": str(tmp_path / "x.png"), "arm_manual": True})
    assert out.get("ok") is not True
    assert "error" in out
    assert not (tmp_path / "x.png").exists() or \
        (tmp_path / "x.png").stat().st_size == 0
