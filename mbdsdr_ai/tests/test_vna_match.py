# SPDX-License-Identifier: MIT
"""vna_match 测试：L 网络解端到端验证 + 元件换算 + 电缆 VF 库。"""
import math

import pytest

from mbdsdr_ai.vna_match import (
    CABLE_VF,
    LcSolution,
    cable_velocity_factor,
    lc_match,
    reactance_to_component,
)


def _input_impedance(sol: LcSolution, zl: complex) -> complex:
    """把解接回负载，看源端输入阻抗（端到端验证的核心）。"""
    z = zl
    if sol.xpl is not None:                    # 负载侧并联臂
        zp = 1j * sol.xpl
        z = z * zp / (z + zp)
    z = z + 1j * sol.xs                        # 串联臂
    if sol.xps is not None:                    # 源侧并联臂
        zs = 1j * sol.xps
        z = z * zs / (z + zs)
    return z


def _assert_all_solutions_match(zl: complex, r0: float):
    sols = lc_match(zl, r0)
    assert len(sols) >= 1
    for s in sols:
        zin = _input_impedance(s, zl)
        assert abs(zin.real - r0) < 1e-6 * r0, f"Re {zin} != {r0}"
        assert abs(zin.imag) < 1e-6 * r0, f"Im {zin} != 0"


# ---- 高阻（R > R0） -----------------------------------------------------------

def test_hi_purely_resistive():
    _assert_all_solutions_match(100 + 0j, 50.0)


def test_hi_with_reactive_load():
    _assert_all_solutions_match(75 + 30j, 50.0)
    _assert_all_solutions_match(200 - 80j, 50.0)


def test_hi_returns_two_solutions():
    sols = lc_match(100 + 0j, 50.0)
    assert len(sols) == 2
    assert all(s.xpl is not None and s.xps is None for s in sols)
    # 两支解一感一容（并联臂符号相反）
    assert sols[0].xpl * sols[1].xpl < 0


# ---- 低阻（R < R0） -----------------------------------------------------------

def test_lo_purely_resistive():
    _assert_all_solutions_match(25 + 0j, 50.0)


def test_lo_with_reactive_load():
    _assert_all_solutions_match(20 + 15j, 50.0)
    _assert_all_solutions_match(10 - 5j, 50.0)


def test_lo_shunt_on_source_side():
    sols = lc_match(25 + 0j, 50.0)
    assert all(s.xps is not None and s.xpl is None for s in sols)


# ---- 退化/拒绝 ----------------------------------------------------------------

def test_already_matched_returns_empty():
    assert lc_match(50 + 0j, 50.0) == []


def test_slight_reactance_single_series_solution():
    """50+j5：VSWR 1.105 > 1.05，按上游口径给单串解（xs=−XL 抵消）。"""
    sols = lc_match(50 + 5j, 50.0)
    assert len(sols) == 1 and sols[0].xs == pytest.approx(-5.0)
    _assert_all_solutions_match(50 + 5j, 50.0)


def test_near_r0_single_series_solution():
    """|R−R0|≤5% 退化支路：单串电抗只抵消虚部，不精确等于 R0——上游口径
    是"SWR<1.1 即可"（52Ω → SWR 1.04），不保证 Z_in == R0。"""
    sols = lc_match(52 + 20j, 50.0)
    assert len(sols) == 1
    assert sols[0].xps is None and sols[0].xpl is None
    assert abs(sols[0].xs - (-20.0)) < 1e-9
    zin = _input_impedance(sols[0], 52 + 20j)
    assert abs(zin.imag) < 1e-9                             # 虚部抵消
    assert 0.91 * 50.0 <= zin.real <= 1.1 * 50.0            # SWR<1.1 口径


def test_zero_resistance_rejected():
    with pytest.raises(ValueError):
        lc_match(0 + 10j, 50.0)


def test_extreme_q_rejected():
    with pytest.raises(ValueError):
        lc_match(1 + 200j, 50.0)


# ---- 元件换算 -----------------------------------------------------------------

def test_reactance_to_inductor():
    kind, val, unit = reactance_to_component(100e6, 50.0)
    assert kind == "L" and unit == "H"
    assert abs(val - 50.0 / (2 * math.pi * 100e6)) < 1e-18


def test_reactance_to_capacitor():
    kind, val, unit = reactance_to_component(100e6, -50.0)
    assert kind == "C" and unit == "F"
    assert abs(val - 1.0 / (2 * math.pi * 100e6 * 50.0)) < 1e-21


def test_zero_reactance_honest_none():
    assert reactance_to_component(100e6, 0.0) is None


def test_negative_frequency_rejected():
    with pytest.raises(ValueError):
        reactance_to_component(-1.0, 50.0)


# ---- 电缆 VF 库 ---------------------------------------------------------------

def test_vf_known_entries():
    assert cable_velocity_factor("RG-58") == pytest.approx(0.66)
    assert cable_velocity_factor("lmr-400") == pytest.approx(0.85)
    assert cable_velocity_factor("  RG-6 ") == pytest.approx(0.82)


def test_vf_unknown_rejected():
    with pytest.raises(KeyError):
        cable_velocity_factor("RG-9999")


def test_vf_table_sane():
    assert len(CABLE_VF) >= 30
    assert all(0.5 < vf < 1.0 for vf in CABLE_VF.values())


def test_vf_roundtrip_with_tdr():
    """VF 与 TDR 长度换算口径联动：vf=0.66 的 1m 线往返时延固定值。"""
    vf = cable_velocity_factor("RG-58")
    c = 299792458.0
    tau = 2.0 * 1.0 / (vf * c)
    assert tau == pytest.approx(1.0103e-8, rel=1e-3)
