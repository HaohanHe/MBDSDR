# SPDX-License-Identifier: MIT
"""瀑布图渲染 + export_peaks_csv 工具测试（第十一批）。"""
import csv

import numpy as np
import pytest

from mbdsdr_ai.core.mcp_server import MCPToolSet, _TOOL_DOMAIN
from mbdsdr_ai.spectrum_snapshot import render_waterfall_png

from .test_spectrum_snapshot import _parse_png

F = np.linspace(88e6, 108e6, 128)


def _rows(n=40):
    rows = []
    for t in range(n):
        sig = -70 + 30 * np.exp(-((np.arange(128) - (64 + t)) / 6) ** 2)
        rows.append(sig)
    return np.array(rows)


def test_waterfall_structure_and_colors():
    data = render_waterfall_png(_rows(), F, 320, 240, 80.0)
    w, h, img = _parse_png(data)
    assert h == 240 + 24                          # 带频率标注底边
    assert w == 320
    # 色带非单色：强信号区（黄/红）与底噪区（蓝）共存
    uniq = np.unique(img.reshape(-1, 3), axis=0)
    assert len(uniq) > 10


def test_waterfall_honest_inputs():
    with pytest.raises(ValueError):
        render_waterfall_png(np.ones(5))                  # 非 2D
    with pytest.raises(ValueError):
        render_waterfall_png(np.full((4, 4), np.nan))     # 非有限
    with pytest.raises(ValueError):
        render_waterfall_png(_rows(), F[:-1])             # 频率轴不齐
    with pytest.raises(ValueError):
        render_waterfall_png(_rows(), F[::-1])            # 频率轴乱序
    with pytest.raises(ValueError):
        render_waterfall_png(_rows(), F, 100, 100)        # 画布过小


def test_waterfall_strongest_at_expected_column():
    rows = np.full((20, 100), -80.0)
    rows[:, 70] = -10.0                               # 固定强列
    data = render_waterfall_png(rows, None, 200, 120)
    _, _, img = _parse_png(data)
    # 最强色（红系）像素应集中在 x≈140 附近（70/100 × 200）
    red = (img[:, :, 0] > 150) & (img[:, :, 2] < 80)
    cols = np.where(red.any(axis=0))[0]
    assert 120 < cols.mean() < 160


def _set():
    return MCPToolSet()


def test_peaks_csv_tool_roundtrip(tmp_path):
    ts = _set()
    assert _TOOL_DOMAIN.get("export_peaks_csv") == "导出演制"
    p = tmp_path / "peaks.csv"
    out = ts.call_tool("export_peaks_csv", {
        "peaks": [{"freq_hz": 98e6, "db": -30.5, "label": "wfm"},
                  [100.1e6, -45.0]],
        "path": str(p), "arm_manual": True})
    assert out.get("ok") is True and out["peaks"] == 2
    with open(p, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["freq_hz", "db", "label"]
    assert rows[1] == ["98000000.0", "-30.5", "wfm"]
    assert rows[2] == ["100100000.0", "-45.0", ""]


def test_peaks_csv_gated_and_honest(tmp_path):
    ts = _set()
    p = tmp_path / "x.csv"
    out = ts.call_tool("export_peaks_csv", {"peaks": [[1, 2]],
                                            "path": str(p)})
    assert out.get("gated") is True
    bad = ts.call_tool("export_peaks_csv", {
        "peaks": [{"db": -1}], "path": str(p), "arm_manual": True})
    assert bad.get("ok") is not True and "error" in bad
    empty = ts.call_tool("export_peaks_csv", {
        "peaks": [], "path": str(p), "arm_manual": True})
    assert empty.get("ok") is not True


def test_snapshot_waterfall_view_via_tool(tmp_path):
    ts = _set()
    p = tmp_path / "wf.png"
    out = ts.call_tool("export_spectrum_snapshot", {
        "freqs_hz": F.tolist(), "db": _rows().tolist(),
        "path": str(p), "view": "waterfall", "arm_manual": True})
    assert out.get("ok") is True
    assert p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
