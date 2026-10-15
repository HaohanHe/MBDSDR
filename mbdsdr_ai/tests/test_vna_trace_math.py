# SPDX-License-Identifier: MIT
"""vna_trace_math 测试：群延迟/归一化/GJB/频段表。"""
import numpy as np
import pytest

from mbdsdr_ai.vna_trace_math import (
    HAM_BANDS,
    find_band,
    gjb_conductance,
    group_delay,
    normalize_trace,
)

F = np.linspace(1e6, 1001e6, 501)


def test_group_delay_linear_phase_constant():
    """相位 = −2πfτ -> 群延迟处处 = τ（端点复制口径同值）。"""
    tau = 12.3e-9
    ph = np.degrees(-2 * np.pi * F * tau)
    gd = group_delay(F, ph)
    assert np.allclose(gd, tau, rtol=1e-9)


def test_group_delay_known_delay_midpoints():
    """端点复制语义：gd[0]==gd[1]，gd[-1]==gd[-2]。"""
    ph = np.random.default_rng(1).standard_normal(len(F)) * 10
    gd = group_delay(F, ph)
    assert gd[0] == gd[1] and gd[-1] == gd[-2]


def test_group_delay_unwraps_phase_jumps():
    """±180° 跳变被 unwrap 吸收，不产生伪群延迟尖峰。"""
    tau = 5e-9
    ph = np.degrees(-2 * np.pi * F * tau)
    ph = (ph + 180) % 360 - 180                       # 折回 ±180°
    gd = group_delay(F, ph)
    assert np.allclose(gd, tau, rtol=1e-6)


def test_group_delay_rejects_nonmonotonic():
    with pytest.raises(ValueError):
        group_delay([1e6, 1e6, 2e6], [0, 1, 2])


def test_normalize_complex_divides():
    lvl = np.array([2 + 2j, 4 + 0j])
    ref = np.array([1 + 1j, 2 + 0j])
    assert np.allclose(normalize_trace(lvl, ref, "complex"), [2, 2])


def test_normalize_complex_zero_ref_honest():
    with pytest.raises(ValueError):
        normalize_trace(np.ones(3), np.zeros(3), "complex")


def test_normalize_db_subtracts():
    out = normalize_trace(np.array([10.0, -3.0]), np.array([4.0, 4.0]), "db")
    assert np.allclose(out, [6.0, -7.0])


def test_normalize_bad_domain_honest():
    with pytest.raises(ValueError):
        normalize_trace([1], [1], "mag")


def test_gjb_conductance():
    g, b = gjb_conductance(np.array([100.0, 50.0]), np.array([50.0, -25.0]))
    assert np.allclose(g, [0.01, 0.02])
    assert np.allclose(b, [-0.02, 0.04])


def test_gjb_zero_rp_honest():
    with pytest.raises(ValueError):
        gjb_conductance(np.array([0.0]), np.array([1.0]))


def test_find_band_hits():
    assert find_band(14.2e6)[0] == "20m"
    assert find_band(144.5e6)[0] == "2m"


def test_find_band_out_of_band_none():
    assert find_band(25e6) is None          # 12m/10m 之间是带外
    assert find_band(100e6) is None


def test_band_table_sane():
    assert len(HAM_BANDS) >= 15
    assert all(lo < hi for _, lo, hi in HAM_BANDS)
