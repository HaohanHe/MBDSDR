# SPDX-License-Identifier: MIT
"""fill_missing_lines 测试（SatDump fill_missing 同款口径）。"""
import numpy as np
import pytest

from mbdsdr_ai.lrpt_vcdu import fill_missing_lines


def _img(h=8, w=4):
    return (np.arange(h * w, dtype=np.uint8) % 251).reshape(h, w)


def test_short_gap_filled_from_top_good_line():
    """3 行黑缺口（长度 <= max）-> 填为上边界完好行的常量。"""
    img = _img()
    status = [1, 0, 0, 0, 1, 1, 1, 1]
    out, n = fill_missing_lines(img, status)
    assert n == 3
    for r in (1, 2, 3):
        assert np.array_equal(out[r], img[0])


def test_long_gap_not_filled():
    """缺口长度超过 max_fill_lines -> 放弃回填，如实报告。"""
    img = _img(h=60)
    status = [1] + [0] * 58 + [1]
    out, n = fill_missing_lines(img, status, max_fill_lines=50)
    assert n == 0
    assert np.array_equal(out[1], img[1])       # 原样保留黑行，不伪造


def test_boundary_exactly_at_limit_fills():
    """缺口长度 == max_fill_lines -> 回填（含边界）。"""
    img = _img(h=52)
    status = [1] + [0] * 50 + [1]
    out, n = fill_missing_lines(img, status, max_fill_lines=50)
    assert n == 50


def test_no_gap_returns_copy():
    img = _img()
    status = [1] * 8
    out, n = fill_missing_lines(img, status)
    assert n == 0
    assert np.array_equal(out, img) and out is not img


def test_leading_gap_without_top_is_skipped():
    """缺口顶到图像开头（无上边界完好行）-> 不填，不拿第 0 行糊弄。"""
    img = _img()
    status = [0, 0, 1, 1, 1, 1, 1, 1]
    out, n = fill_missing_lines(img, status)
    assert n == 0


def test_multichannel_gap_filled():
    """(H,W,C) 多通道同样回填。"""
    img = np.dstack([_img(), _img() + 1])
    status = [1, 0, 0, 1, 1, 1, 1, 1]
    out, n = fill_missing_lines(img, status)
    assert n == 2
    assert np.array_equal(out[1], img[0])


def test_bad_shapes_rejected():
    with pytest.raises(ValueError):
        fill_missing_lines(np.zeros((3,)), [1, 1, 1])
    with pytest.raises(ValueError):
        fill_missing_lines(_img(), [1, 0])          # 长度不一致


def test_empty_status():
    img = _img()
    out, n = fill_missing_lines(img, [])
    assert n == 0 and np.array_equal(out, img)
