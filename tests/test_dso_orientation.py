# SPDX-License-Identifier: MIT
"""
tests/test_dso_orientation.py
=============================
星系长轴位置角（目录 PA）→ 屏幕角度的纯数学层。

角度约定（与 QPainter.rotate 同口径）：屏幕 y 向下、PA 从北向东量，
北向量 (0,-1)、东向量 (1,0) 时：

    PA=0°   -> 长轴朝上（北）-> atan2(-1, 0) = -90°
    PA=90°  -> 长轴朝右（东）-> 0°
    PA=45°  -> 右上             -> -45°
    PA=180° -> 朝下（南）        -> +90°

退化/非法输入一律 None，调用方退回不旋转——绝不猜朝向。
"""
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from mbdsdr_ai.dso_orientation import (  # noqa: E402
    basis_offsets, major_axis_screen_deg,
)


# --------------------------------------------------------------------------
# 1. 屏幕角度：y 向下、Qt rotate 顺时针为正
# --------------------------------------------------------------------------
@pytest.mark.parametrize("pa,expected", [(0.0, -90.0), (90.0, 0.0),
                                         (45.0, -45.0), (180.0, 90.0)])
def test_major_axis_angle_screen_convention(pa, expected):
    got = major_axis_screen_deg(pa, 0.0, -1.0, 1.0, 0.0)
    assert got is not None
    assert got == pytest.approx(expected, abs=1e-9)


def test_east_vector_pointing_down_gives_downward_axis():
    """东在屏幕上朝下（y 增大）时 PA=90° 必须返回 +90°（顺时针）。"""
    assert major_axis_screen_deg(90.0, 0.0, -1.0, 0.0, 1.0) \
        == pytest.approx(90.0, abs=1e-9)


def test_combined_axis_uses_both_vectors():
    """长轴 = north*cos(pa) + east*sin(pa)，不是只看 north。"""
    north = (0.0, -2.0)
    east = (2.0, 0.0)
    assert major_axis_screen_deg(45.0, *north, *east) \
        == pytest.approx(-45.0, abs=1e-9)


# --------------------------------------------------------------------------
# 2. 退化/非法输入 -> None（不猜朝向）
# --------------------------------------------------------------------------
@pytest.mark.parametrize("args", [
    (35.0, 0.0, 0.0, 1.0, 0.0),        # 北向量模长 0
    (35.0, 0.0, -1.0, 0.0, 0.0),       # 东向量模长 0
    (35.0, float("nan"), -1.0, 1.0, 0.0),
    (35.0, 0.0, -1.0, float("inf"), 0.0),
    (float("nan"), 0.0, -1.0, 1.0, 0.0),
    (float("inf"), 0.0, -1.0, 1.0, 0.0),
    (-1.0, 0.0, -1.0, 1.0, 0.0),       # PA 越界
    (360.0, 0.0, -1.0, 1.0, 0.0),
    (None, 0.0, -1.0, 1.0, 0.0),
    ("nope", 0.0, -1.0, 1.0, 0.0),
])
def test_degenerate_or_invalid_returns_none(args):
    assert major_axis_screen_deg(*args) is None


# --------------------------------------------------------------------------
# 3. basis_offsets：东点 Δra = step/cos(dec)，RA 归一，极点不炸
# --------------------------------------------------------------------------
def test_basis_offsets_at_equator_east_step_is_ra_step():
    n_ra, n_dec, e_ra, e_dec = basis_offsets(10.0, 0.0, 0.2)
    assert n_ra == pytest.approx(10.0)
    assert n_dec == pytest.approx(0.2)          # 北点 dec+step
    assert e_dec == pytest.approx(0.0)
    assert e_ra - 10.0 == pytest.approx(0.2, abs=1e-12)


def test_basis_offsets_east_step_scales_with_cos_dec():
    n_ra, n_dec, e_ra, e_dec = basis_offsets(10.0, 80.0, 0.2)
    assert n_dec == pytest.approx(80.2)
    assert e_ra - 10.0 == pytest.approx(
        0.2 / math.cos(math.radians(80.0)), rel=1e-12)


def test_basis_offsets_ra_wraps_below_360():
    n_ra, n_dec, e_ra, e_dec = basis_offsets(359.9, 0.0, 0.2)
    assert 0.0 <= e_ra < 360.0
    assert e_ra == pytest.approx(0.1, abs=1e-12)


def test_basis_offsets_near_pole_stays_finite():
    for dec in (89.9999, -89.9999, 90.0, -90.0):
        n_ra, n_dec, e_ra, e_dec = basis_offsets(123.4, dec, 0.2)
        for v in (n_ra, n_dec, e_ra, e_dec):
            assert math.isfinite(v), (dec, v)
        assert 0.0 <= n_ra < 360.0 and 0.0 <= e_ra < 360.0
        assert abs(n_dec) <= 89.999


def test_basis_offsets_rejects_degenerate_step_or_nonfinite():
    with pytest.raises(ValueError):
        basis_offsets(10.0, 0.0, 0.0)
    with pytest.raises(ValueError):
        basis_offsets(float("nan"), 0.0)
    with pytest.raises(ValueError):
        basis_offsets(10.0, float("inf"))
