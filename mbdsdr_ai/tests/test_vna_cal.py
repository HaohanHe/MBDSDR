# SPDX-License-Identifier: MIT
"""vna_cal 测试：非理想 OSL + 误差盒闭环仿真钉死。"""
import numpy as np
import pytest

from mbdsdr_ai.vna_cal import (
    ErrorBox,
    LoadStandard,
    OpenStandard,
    ShortStandard,
    apply_calibration,
    s21_enhanced_response,
    solve_error_box,
)

F = np.linspace(1e6, 3e9, 401)
Z0 = 50.0


# ---- 仿真夹具：已知误差项的整台 VNA -------------------------------------------

def _simulate(box: ErrorBox, gamma_true):
    """被测件真实 Γ 过误差盒 -> 实测 Γm。"""
    g = np.asarray(gamma_true, dtype=np.complex128)
    return box.e00 + box.t * g / (1.0 - box.e11 * g)


def _roundtrip(box, dut_gamma, open_std, short_std, load_std):
    """标准件+DUT 全走误差盒 -> 求解 -> 校准 -> 恢复 DUT。"""
    mo = _simulate(box, open_std.gamma(F, Z0))
    ms = _simulate(box, short_std.gamma(F, Z0))
    ml = _simulate(box, load_std.gamma(F, Z0))
    solved = solve_error_box(mo, ms, ml,
                             open_std.gamma(F, Z0),
                             short_std.gamma(F, Z0),
                             load_std.gamma(F, Z0))
    mdut = _simulate(box, dut_gamma)
    return apply_calibration(mdut, solved), solved


# ---- 定义件行为 ---------------------------------------------------------------

def test_open_standard_edge_capacitance():
    o = OpenStandard(c0_pF=2.0)
    g = o.gamma(1e9, Z0)
    assert np.all(np.abs(g) > 0.99)                       # 近全反射
    assert np.all(g.imag < 0)                             # 容性 -> 下半平面


def test_short_standard_lead_inductance():
    s = ShortStandard(l0_nH=1.0)
    g = s.gamma(1e9, Z0)
    assert np.all(np.abs(g) > 0.99)
    assert np.all(g.imag > 0)                             # 感性 -> 上半平面


def test_short_ideal_is_minus_one():
    s = ShortStandard(use_ideal=True)
    assert np.allclose(s.gamma(F, Z0), -1.0)


def test_load_ideal_matched():
    assert np.allclose(LoadStandard().gamma(F, Z0), 0.0, atol=1e-15)


def test_load_series_l():
    ld = LoadStandard(r_ohm=50.0, l_nH=2.0)
    g = ld.gamma(1e9, Z0)
    assert np.all(g.imag > 0)                             # 正电抗 -> 上半平面
    assert np.all(np.abs(g) < 0.15)                       # 2nH@1GHz ≈ 0.125


def test_offset_phase_rotates():
    base = OpenStandard(use_ideal=True)
    shifted = OpenStandard(use_ideal=True, offset_len_m=0.01)
    g0, g1 = base.gamma(1e9, Z0), shifted.gamma(1e9, Z0)
    ratio = g1 / g0
    expect = np.exp(-1j * 4 * np.pi * 1e9 * 0.01 / 299792458.0)
    assert abs(ratio - expect) < 1e-12


def test_offset_bad_vf_rejected():
    with pytest.raises(ValueError):
        OpenStandard(offset_len_m=0.01, offset_vf=0.0).gamma(F, Z0)


# ---- 误差盒闭环（仿真钉死推导正确性） -----------------------------------------

def test_roundtrip_ideal_standards_recovers_dut():
    box = ErrorBox(e00=0.05 + 0.02j, t=0.98 - 0.01j, e11=0.1 - 0.05j)
    o, s, l = (OpenStandard(use_ideal=True),
               ShortStandard(use_ideal=True),
               LoadStandard())
    dut = 0.3 * np.exp(1j * np.linspace(0, 4 * np.pi, len(F)))
    recovered, solved = _roundtrip(box, dut, o, s, l)
    assert np.max(np.abs(recovered - dut)) < 1e-9
    # 误差项也还原（解是逐频点数组，取全局最大偏差）
    assert np.max(np.abs(solved.e00 - box.e00)) < 1e-9
    assert np.max(np.abs(solved.t - box.t)) < 1e-9
    assert np.max(np.abs(solved.e11 - box.e11)) < 1e-9


def test_roundtrip_nonideal_standards_recovers_dut():
    """非理想定义件 + 偏移长度 + 大误差项：校准后仍恢复 DUT。"""
    box = ErrorBox(e00=0.1 + 0.05j, t=0.9 + 0.1j, e11=0.2 + 0.1j)
    o = OpenStandard(c0_pF=2.2, c1=0.3, c2=-0.01, offset_len_m=0.008)
    s = ShortStandard(l0_nH=1.2, l1=0.1, offset_len_m=0.008)
    l = LoadStandard(r_ohm=49.9, l_nH=0.05)
    dut = (0.2 + 0.4j) * np.ones_like(F)
    recovered, _ = _roundtrip(box, dut, o, s, l)
    assert np.max(np.abs(recovered - dut)) < 1e-6


def test_apply_calibration_zero_tracking_rejected():
    box = ErrorBox(e00=0.0, t=0.0, e11=0.0)
    with pytest.raises(ValueError):
        s21_enhanced_response(np.ones(4), np.ones(4), 0.0, 0.0, 0.0)


def test_enhanced_response_recovers_s21():
    """S21 增强响应：仿真 through 测量 -> 校准恢复。"""
    e11 = 0.1 - 0.03j
    e30 = 0.001 + 0.0005j
    e10e32 = 0.95 + 0.02j
    s11c = np.full(F.shape, 0.1 + 0.2j, dtype=np.complex128)
    s21_true = 0.8 * np.exp(1j * np.linspace(0, 3 * np.pi, len(F)))
    # 反推实测：S21m = S21c·e10e32·(1−e11·S11c) + e30
    s21m = s21_true * e10e32 * (1 - e11 * s11c) + e30
    out = s21_enhanced_response(s21m, s11c, e30, e10e32, e11)
    assert np.max(np.abs(out - s21_true)) < 1e-12


def test_singular_system_honest_error():
    """三件标准定义 Γ 相同 -> 方程组奇异，显式报错不返回垃圾。"""
    with pytest.raises(ValueError):
        solve_error_box(0.1, 0.1, 0.1, 1.0, 1.0, 1.0)
