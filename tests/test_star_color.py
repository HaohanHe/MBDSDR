# SPDX-License-Identifier: MIT
"""星点颜色：暗星去饱和（B-V>=1.0 的 1/3 恒星不能全画成橙红点）。

2026-11-10 截图实证：90° 视场下 555 颗星大面积偏橙红，不符合暗视觉。
修法不是改星表或 B-V→RGB 表，而是按可视亮度把颜色向白插值。
"""
from __future__ import annotations

import numpy as np
import pytest

from mbdsdr_ai.skyengine import stars


def test_dark_stars_are_nearly_white():
    """6.5 等星（bright=0）：红色恒星也必须接近白，不能是橙点。"""
    red = stars.bv_to_rgb(1.5)                 # (255, 150, 80)
    r, g, b = stars.desaturate_rgb_many(
        np.array([red[0]]), np.array([red[1]]), np.array([red[2]]),
        stars.saturate_for_brightness_many(np.array([0.0])))
    assert (int(r[0]), int(g[0]), int(b[0])) != red
    assert int(b[0]) >= 225                    # 蓝通道被拉回白
    assert int(r[0]) - int(b[0]) < 20


def test_bright_stars_keep_their_color():
    """亮星（bright=1）保留原色，否则五车二/毕宿五就分不出来了。"""
    red = stars.bv_to_rgb(1.5)
    r, g, b = stars.desaturate_rgb_many(
        np.array([red[0]]), np.array([red[1]]), np.array([red[2]]),
        stars.saturate_for_brightness_many(np.array([1.0])))
    assert (int(r[0]), int(g[0]), int(b[0])) == pytest.approx(red, abs=1)


def test_saturation_is_monotonic_and_bounded():
    v = stars.saturate_for_brightness_many(np.linspace(0.0, 1.0, 21))
    assert float(v.min()) >= 0.0
    assert float(v.max()) <= 1.0
    assert np.all(np.diff(v) >= 0)             # 越亮越有颜色，不来回跳


def test_saturation_handles_bad_input():
    """NaN / 越界值不能把整帧颜色算成黑块或抛异常。"""
    v = stars.saturate_for_brightness_many(
        np.array([-3.0, 0.0, 0.5, 1.0, 9.0, np.nan]))
    assert v.shape == (6,)
    assert np.all(np.isfinite(v))
    assert np.all((v >= 0.0) & (v <= 1.0))


def test_vector_shape_matches_batch():
    bv = np.array([-0.2, 0.3, 0.8, 1.4, np.nan])
    cr, cg, cb = stars.bv_to_rgb_many(bv)
    sat = stars.saturate_for_brightness_many(np.linspace(0, 1, len(bv)))
    r, g, b = stars.desaturate_rgb_many(cr, cg, cb, sat)
    assert r.shape == g.shape == b.shape == (len(bv),)
    assert np.all((r >= 0) & (r <= 255))
    assert np.all((g >= 0) & (g <= 255))
    assert np.all((b >= 0) & (b <= 255))


def test_bv_table_matches_scalar_still():
    """去饱和是绘制端的新层，不能动 B-V→RGB 这条事实源。"""
    vals = [-0.3, 0.0, 0.3, 0.6, 1.0, 1.5, np.nan]
    r, g, b = stars.bv_to_rgb_many(np.array(vals, dtype=float))
    for i, x in enumerate(vals):
        want = stars.bv_to_rgb(float(x))
        assert (int(r[i]), int(g[i]), int(b[i])) == want
