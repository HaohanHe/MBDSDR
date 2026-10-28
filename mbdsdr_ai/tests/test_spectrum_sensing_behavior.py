# SPDX-License-Identifier: MIT
"""spectrum_sensing 行为测试（能量检测器，纯数学 + 合成 IQ）。

oracle 来源（读码语义/教科书公式，非拍脑袋）：
- qfunc(x)=½erfc(x/√2)；qinv(p)=Φ⁻¹(1-p)（Acklam 近似，相对误差 ~1e-9）；
- threshold_for_pfa: γ = σ²ρ·(1 + Q⁻¹(Pfa)/√N)，ρ=10^(U/10)；
- theory_pd: Pd = Q( √N(ρ-1-λ) + ρ·Q⁻¹(Pfa) )，λ 为线性 SNR；
- snr_wall_db: 10log10(ρ-1)，U=0 → -inf；
- EnergyDetector.detect: 判决 stat > γ（严格大于）；SNR 估计 = T/σ²-1，
  非正时钳到 -90dB；必须显式给 noise_power 或 noise_ref。
"""
import numpy as np
import pytest

from math import log10, sqrt

from mbdsdr_ai.spectrum_sensing import (
    EnergyDetector,
    energy_statistic,
    estimate_noise_power,
    qfunc,
    qinv,
    snr_wall_db,
    theory_pd,
    threshold_for_pfa,
)


def _cnoise(n, sigma2=1.0, seed=42):
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(n) + 1j * rng.standard_normal(n)) * sqrt(sigma2 / 2.0)


# ── Q 函数数值语义 ──────────────────────────────────────────

def test_qfunc_known_values():
    assert qfunc(0.0) == pytest.approx(0.5, abs=1e-12)
    assert qfunc(1.959963984540054) == pytest.approx(0.025, rel=1e-6)
    assert qfunc(-1.959963984540054) == pytest.approx(0.975, rel=1e-6)


def test_qinv_known_values_and_symmetry():
    assert qinv(0.5) == 0.0
    assert qinv(0.025) == pytest.approx(1.959963984540054, rel=1e-6)
    assert qinv(0.975) == pytest.approx(-1.959963984540054, rel=1e-6)
    # 对称性 Q⁻¹(p) = -Q⁻¹(1-p)
    for p in (0.001, 0.01, 0.1, 0.3):
        assert qinv(p) == pytest.approx(-qinv(1.0 - p), rel=1e-9)


def test_qinv_qfunc_roundtrip():
    for p in (0.025, 0.1, 0.5, 0.9, 0.975):
        assert qfunc(qinv(p)) == pytest.approx(p, rel=1e-5)


def test_qinv_out_of_range_limits():
    """p 越界按扩展实数语义：p<=0 → +inf，p>=1 → -inf。"""
    assert qinv(0.0) == np.inf
    assert qinv(1.0) == -np.inf
    assert qinv(-0.5) == np.inf
    assert qinv(1.5) == -np.inf


# ── 能量统计量 / 门限 / 理论 Pd ─────────────────────────────

def test_energy_statistic_and_estimator():
    x = np.array([1 + 0j, 0 + 1j, -1 + 0j, 0 - 1j])   # |x|²=1
    assert energy_statistic(x) == pytest.approx(1.0)
    assert estimate_noise_power(x) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        energy_statistic([])


def test_threshold_for_pfa_formula_and_uncertainty():
    """γ = σ²ρ(1 + Q⁻¹(Pfa)/√N)：ρ=1 时经典式，U 抬高门限 ρ 倍。"""
    n, sigma2, pfa = 4096, 1.0, 0.01
    gamma0 = threshold_for_pfa(n, sigma2, pfa)
    assert gamma0 == pytest.approx(sigma2 * (1.0 + qinv(pfa) / sqrt(n)))
    gamma_u = threshold_for_pfa(n, sigma2, pfa, noise_uncertainty_db=3.0)
    rho = 10 ** 0.3
    assert gamma_u == pytest.approx(gamma0 * rho)
    with pytest.raises(ValueError):
        threshold_for_pfa(n, 0.0, pfa)


def test_theory_pd_matches_closed_form_and_monotonic():
    """Pd = Q(√N(ρ-1-λ) + ρ·Q⁻¹(Pfa))；ρ=1 退化为 Q(Q⁻¹(Pfa)-√N·λ)。SNR 越高 Pd 越大。"""
    n, pfa = 4096, 0.01
    for snr_db in (-20.0, -10.0, 0.0, 10.0):
        lam = 10 ** (snr_db / 10.0)
        expect = qfunc(qinv(pfa) - sqrt(n) * lam)   # ρ=1 经典式
        assert theory_pd(snr_db, n, pfa) == pytest.approx(expect, rel=1e-9)
    assert theory_pd(-20.0, n, pfa) < theory_pd(-10.0, n, pfa) < theory_pd(10.0, n, pfa)
    assert theory_pd(10.0, n, pfa) > 0.99
    # 噪声不确定度下高 SNR 仍可逼近 1
    assert theory_pd(30.0, n, pfa, noise_uncertainty_db=3.0) > 0.99


def test_snr_wall_semantics():
    """U=0 无墙（-inf）；U>0 墙 = 10log10(ρ-1)。"""
    assert snr_wall_db(0.0) == -np.inf
    for u in (0.5, 1.0, 3.0):
        assert snr_wall_db(u) == pytest.approx(10.0 * log10(10 ** (u / 10.0) - 1.0))
    # 墙的存在性：U=3 时 λ < ρ-1（SNR < ~-0.02dB），N→∞ Pd→0
    wall = snr_wall_db(3.0)
    assert theory_pd(wall - 1.0, 1 << 20, 0.01, noise_uncertainty_db=3.0) < 0.05


# ── EnergyDetector 判决行为（合成 IQ） ──────────────────────

def test_detector_h0_not_occupied():
    """纯噪声（σ²=1，N=4096）在 Pfa=0.01 门限下判空闲；SNR 估计按码内公式或钳位 -90。"""
    x = _cnoise(4096, sigma2=1.0, seed=42)
    r = EnergyDetector(noise_power=1.0).detect(x, pfa=0.01)
    assert r.decision is False
    assert r.statistic < r.threshold
    # 语义：T/σ²-1 > 1e-9 时取 10log10(T/σ²-1)，否则钳 -90
    if r.statistic / r.noise_power - 1.0 > 1e-9:
        assert r.estimated_snr_db == pytest.approx(
            10.0 * log10(r.statistic / r.noise_power - 1.0), rel=1e-9)
    else:
        assert r.estimated_snr_db == -90.0
    assert r.n == 4096 and r.pfa_target == 0.01


def test_detector_h1_occupied_with_snr_estimate():
    """噪声+已知功率信号：判占用，SNR 估计接近真值 10log10(Ps/σ²)。"""
    n, sigma2, ps = 4096, 1.0, 0.5
    x = _cnoise(n, sigma2=sigma2, seed=1) + _cnoise(n, sigma2=ps, seed=2)
    r = EnergyDetector(noise_power=sigma2).detect(x, pfa=0.01)
    assert r.decision is True
    assert r.statistic > r.threshold
    assert r.estimated_snr_db == pytest.approx(10.0 * log10(ps / sigma2), abs=1.5)


def test_detector_noise_ref_estimates_threshold():
    """noise_ref 纯噪声段估计 σ²：判决路径可用，估计值等于参考段能量。"""
    ref = _cnoise(2048, sigma2=2.0, seed=3)
    x = _cnoise(2048, sigma2=2.0, seed=4)
    r = EnergyDetector().detect(x, pfa=0.01, noise_ref=ref)
    assert r.noise_power == pytest.approx(estimate_noise_power(ref))
    assert r.decision is False


def test_detector_noise_uncertainty_raises_threshold():
    """噪声不确定度 U 抬高门限 ρ 倍，同一信号判决可能翻转。"""
    n, sigma2 = 1024, 1.0
    x = _cnoise(n, sigma2=sigma2, seed=5) + _cnoise(n, sigma2=0.2, seed=6)
    r0 = EnergyDetector(noise_power=sigma2).detect(x, pfa=0.1)
    r3 = EnergyDetector(noise_power=sigma2, noise_uncertainty_db=10.0).detect(x, pfa=0.1)
    assert r3.threshold == pytest.approx(r0.threshold * 10.0)
    assert r3.statistic == pytest.approx(r0.statistic)
    assert r0.decision is True and r3.decision is False


def test_detector_strict_greater_than_threshold():
    """判决边界为严格大于：stat == γ 判空闲。"""
    det = EnergyDetector(noise_power=1.0)
    # 构造能量统计量恰好等于门限的常数序列
    n, pfa = 1024, 0.1
    gamma = threshold_for_pfa(n, 1.0, pfa)
    x = np.full(n, sqrt(gamma), dtype=np.complex128)
    r = det.detect(x, pfa=pfa)
    assert r.statistic == pytest.approx(gamma, rel=1e-12)
    assert r.decision is False


def test_detector_error_paths_honest():
    """空样本 / 无噪声来源：显式 ValueError，不猜测不伪造。"""
    det = EnergyDetector()
    with pytest.raises(ValueError):
        det.detect([], pfa=0.01)                       # 空样本
    with pytest.raises(ValueError):
        EnergyDetector(noise_power=1.0).detect(np.zeros(0, dtype=np.complex128))
    with pytest.raises(ValueError):
        det.detect(np.ones(16, dtype=np.complex128), pfa=0.01)  # 无 noise_power 也无 noise_ref
