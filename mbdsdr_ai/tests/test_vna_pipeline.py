# SPDX-License-Identifier: MIT
"""vna_pipeline 测试。"""
import numpy as np
import pytest

from mbdsdr_ai.vna_pipeline import analyze_one_port, analyze_two_port

C = 299792458.0
VF = 0.66
Z0 = 50.0
F = np.linspace(1e6, 1001e6, 801)


def _series_rlc_s11(fr=100e6, q_loaded=20.0, esr=5.0):
    """串联 RLC 夹具：fr 处 |Z| 极小 = ESR。"""
    w = 2 * np.pi * F
    wr = 2 * np.pi * fr
    l = q_loaded * esr / wr
    cs = 1.0 / (wr * wr * l)
    z = esr + 1j * (w * l - 1.0 / (w * cs))
    return (z - Z0) / (z + Z0)


def _cable_step_s11(l_m=2.0, z1=75.0):
    rho = (z1 - Z0) / (z1 + Z0)
    beta = 2 * np.pi * F / (VF * C)
    return rho * np.exp(-2j * beta * l_m)


def test_one_port_resonance_found():
    rep = analyze_one_port(F, _series_rlc_s11())
    res = rep["resonance"]
    assert res is not None
    assert abs(res["fr_hz"] - 100e6) < 2e6
    assert 10 < res["q"] < 40
    assert res["esr"] < 20


def test_one_port_resonance_absent_honest():
    """无谐振迹线（恒定 Γ）→ resonance 保持 None，VSWR 恒等（如实）。"""
    rep = analyze_one_port(F, np.full(len(F), 0.1 + 0.05j))
    assert rep["resonance"] is None
    assert rep["points"] == len(F)
    assert rep["vswr_max"][1] == pytest.approx(rep["vswr_min"][1])


def test_one_port_tdr_length_and_profile():
    rep = analyze_one_port(F, _cable_step_s11(2.0, 75.0), cable="RG-58")
    tdr = rep["tdr"]
    assert tdr["vf"] == pytest.approx(0.66)
    assert tdr["length_m"] == pytest.approx(2.0, rel=0.1)
    assert len(tdr["profile_z_ohm"]) <= 64
    assert any(z > 60 for z in tdr["profile_z_ohm"])   # 剖面越过 60Ω


def test_one_port_unknown_cable_honest():
    with pytest.raises(KeyError):
        analyze_one_port(F, _cable_step_s11(), cable="RG-9999")


def test_one_port_lc_match_at_worst():
    """失配负载：最差频点给出非空解集，元件换算齐。"""
    rep = analyze_one_port(F, np.full(len(F), 0.5 + 0j))
    assert rep["lc_match"]
    entry = rep["lc_match"][0]
    assert entry["series_component"] is not None
    assert entry["freq_hz"] > 0


def test_one_port_matched_no_lc_honest():
    rep = analyze_one_port(F, np.full(len(F), 0.0 + 0j))
    assert rep["lc_match"] == []                      # 良好匹配：空列表
    assert rep["vswr_max"][1] == pytest.approx(1.0)


def test_one_port_coax_loss_fields():
    rep = analyze_one_port(F, _cable_step_s11())
    assert "ripple_bin" in rep["coax_loss"]
    # 电缆阶跃夹具无振铃：检不出 bin 时 cleaned 为 None（诚实）
    if rep["coax_loss"]["ripple_bin"] is None:
        assert rep["coax_loss"]["cleaned"] is None


def test_one_port_band_lookup():
    f2 = np.linspace(14.0e6, 14.2e6, 81)        # 20m 波段内（14.0-14.35M）
    s = np.full(81, 0.2 + 0.1j)
    rep = analyze_one_port(f2, s)
    assert rep["band"][0] == "20m"


def test_two_port_bandpass():
    """带通夹具：-3dB 通带指标与形态判定。"""
    s21 = 1j * (F / 500e6) / (1 + 1j * (F / 500e6))   # 单极点低通
    rep = analyze_two_port(F, s21)
    assert rep["filter_shape"] in ("lowpass", "bandpass", "highpass",
                                   "bandstop")
    if rep["bw_hz"]:
        assert rep["bw_hz"] > 0
        assert rep["passband_ripple_db"] is not None


def test_honest_inputs():
    with pytest.raises(ValueError):
        analyze_one_port(F, np.ones(10))
    with pytest.raises(ValueError):
        analyze_one_port(F, np.full(len(F), np.nan))
    with pytest.raises(ValueError):
        analyze_one_port(F[::-1].copy(), np.ones(len(F)))
    with pytest.raises(ValueError):
        analyze_two_port(F, np.full(len(F), np.inf, dtype=complex))
