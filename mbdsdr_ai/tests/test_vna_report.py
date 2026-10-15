# SPDX-License-Identifier: MIT
"""vna_report 测试：双面板 Smith/log-mag 渲染。"""
import numpy as np
import pytest

from mbdsdr_ai.tests.test_spectrum_snapshot import _parse_png
from mbdsdr_ai.vna_report import render_s11_report, render_s11_report_png

F = np.linspace(1e6, 1001e6, 401)


def test_report_structure():
    s11 = 0.5 * np.exp(1j * np.linspace(0, 6, len(F)))
    data = render_s11_report_png(F, s11, 800, 420)
    w, h, img = _parse_png(data)
    assert (w, h) == (800, 420)
    blue = (img == (20, 60, 200)).all(axis=2)
    assert blue.any()
    # 双面板都有迹线：左半与右半均见蓝色
    assert blue[:, :w // 2].any() and blue[:, w // 2:].any()


def test_smith_open_short_match_positions():
    """开路/短路/匹配三迹线在 Smith 面板（右半）的预期方位。"""
    n = 64
    mid = 420 // 2

    def smith_cols(s):
        _, _, img = _parse_png(
            render_s11_report_png(np.linspace(1, n, n), s, 420, 300))
        blue = (img == (20, 60, 200)).all(axis=2)
        blue[:, :mid + 8] = False             # 只看 Smith 面板
        return np.where(blue.any(axis=0))[0]

    cols = smith_cols(np.full(n, 0.99 + 0j))
    assert len(cols) and cols.mean() > mid + 140     # 开路：盘右缘（实测 409）
    cols = smith_cols(np.full(n, -0.99 + 0j))
    assert len(cols) and cols.mean() < mid + 60      # 短路：盘左缘（实测 221）

    _, _, img = _parse_png(render_s11_report_png(
        np.linspace(1, n, n), np.full(n, 0.0 + 0j), 420, 300))
    blue = (img == (20, 60, 200)).all(axis=2)
    blue[:, :mid + 8] = False
    rows, cols = np.where(blue)
    cx = (mid + 8 + 420 - 8) // 2
    cy = (8 + 300 - 8) // 2
    assert abs(cols.mean() - cx) < 6 and abs(rows.mean() - cy) < 6


def test_smith_grid_present():
    s11 = 0.3 * np.ones(32)
    _, _, img = _parse_png(render_s11_report_png(np.linspace(1, 32, 32),
                                                 s11, 420, 300))
    gray = (img == (128, 128, 128)).all(axis=2)
    assert gray.sum() > 200                # 外圆 + r 圆网格墨迹


def test_clipped_points_reported():
    s11 = np.array([0.5 + 0j, 1.2 + 0j, 0.1 + 0j])   # 含 |Γ|>1 越界
    data, meta = render_s11_report([1.0, 2.0, 3.0], s11, 420, 300)
    assert meta["clipped_points"] == 1
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_honest_inputs():
    with pytest.raises(ValueError):
        render_s11_report_png(F, np.ones(10))
    with pytest.raises(ValueError):
        render_s11_report_png(F, np.full(len(F), np.nan))
    with pytest.raises(ValueError):
        render_s11_report_png(F, np.ones(len(F)), 100, 100)
