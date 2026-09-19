# -*- coding: utf-8 -*-
"""
频谱感知（Spectrum Sensing）：认知无线电的能量检测器（Energy Detector）。

二元假设：
  H0（频段空闲）: x = w
  H1（频段占用）: x = s + w
其中 w 为零均值复高斯白噪声，s 为信号。能量检验统计量 T = (1/N) Σ|x[n]|²。

复高斯噪声下（N 个复样本，E|w|² = σ²）：
  H0: T ~ 近似 N(σ², σ⁴/N)
  H1: T ~ 近似 N(σ²(1+λ), σ⁴/N)，λ = Ps/σ² 为线性 SNR
对目标虚警率 Pfa，门限
  γ = σ² [1 + Q⁻¹(Pfa)/√N]
检测概率
  Pd = Q( Q⁻¹(Pfa) - √N · λ )
噪声功率存在 dB 不确定度 U（真实值落在 σ²/ρ ~ σ²ρ，ρ=10^(U/10)）时，
保守按 σ²ρ 设门限，存在经典的 SNR wall（Tandra & Sahai）。

该模块同时被产品工具（energy_sense）与可复现实验（experiments/exp_spectrum_sensing.py）
复用，保证论文曲线与产品实现一致。Q⁻¹ 用 Acklam 有理近似，无需 scipy。
"""
from dataclasses import dataclass
from math import erfc, sqrt, log10
from typing import List, Optional, Sequence

import numpy as np


def qfunc(x: float) -> float:
    """标准正态右尾概率 Q(x)。"""
    return 0.5 * erfc(x / sqrt(2.0))


def qinv(p: float) -> float:
    """
    标准正态分位数 Q⁻¹(p)（Acklam 有理近似，相对误差约 1e-9）。
    即返回 z 使 Q(z)=p，等价于 Φ⁻¹(1-p)。
    """
    if not 0.0 < p < 1.0:
        if p >= 1.0:
            return -np.inf
        if p <= 0.0:
            return np.inf
        raise ValueError("p 必须在 (0,1)")
    # 以下 Acklam 近似计算的是 CDF 逆 Φ⁻¹(p)；Q⁻¹(p)=Φ⁻¹(1-p)
    p = 1.0 - p
    # Acklam 系数
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow = 0.02425
    phigh = 1.0 - plow
    if p < plow:
        q = sqrt(-2.0 * np.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if p <= phigh:
        q = p - 0.5
        r = q * q
        return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
               (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    q = sqrt(-2.0 * np.log(1.0 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
        ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)


def energy_statistic(x: Sequence[complex]) -> float:
    """能量检验统计量 T = (1/N) Σ|x[n]|²。"""
    a = np.asarray(x, dtype=np.complex128)
    if a.size == 0:
        raise ValueError("空样本无法计算能量统计量")
    return float(np.mean(np.abs(a) ** 2))


def estimate_noise_power(x_ref: Sequence[complex]) -> float:
    """从纯噪声参考段估计噪声功率 σ² = mean|x|²。"""
    return energy_statistic(x_ref)


def threshold_for_pfa(n: int, noise_power: float, pfa: float,
                      noise_uncertainty_db: float = 0.0) -> float:
    """
    给定目标虚警率 Pfa 的检测门限。
    noise_uncertainty_db>0 时按噪声功率保守上限 σ²·ρ 设门限。
    """
    if noise_power <= 0:
        raise ValueError("噪声功率必须为正")
    rho = 10.0 ** (noise_uncertainty_db / 10.0)
    worst_noise = noise_power * rho
    return worst_noise * (1.0 + qinv(pfa) / sqrt(n))


def theory_pd(snr_db: float, n: int, pfa: float,
              noise_uncertainty_db: float = 0.0) -> float:
    """
    能量检测器理论检测概率（复高斯、高斯近似）。

    门限按最坏噪声 σ²ρ 设定（ρ=10^(U/10)）：γ=σ²ρ[1+Q⁻¹(Pfa)/√N]；
    H1 下真实噪声取标称 σ²，T1~N(σ²(1+λ), σ⁴/N)，λ 为线性 SNR。于是
      Pd = Q( √N(ρ-1-λ) + ρ·Q⁻¹(Pfa) )。
    U=0（ρ=1）退化为经典式 Pd=Q(Q⁻¹(Pfa)-√N·λ)；U>0 时 λ<ρ-1（SNR 低于
    SNR wall）则 N→∞ 时 Pd→0，刻画了噪声不确定度带来的不可检测墙。
    """
    lam = 10.0 ** (snr_db / 10.0)
    rho = 10.0 ** (noise_uncertainty_db / 10.0)
    z = sqrt(n) * (rho - 1.0 - lam) + rho * qinv(pfa)
    return qfunc(z)


def snr_wall_db(noise_uncertainty_db: float) -> float:
    """
    噪声不确定度 U(dB) 下能量检测器的 SNR wall（线性 ρ-1 的 dB 值）。
    低于该 SNR，无论感知多久都无法同时满足 Pfa/Pd 目标。
    """
    rho = 10.0 ** (noise_uncertainty_db / 10.0)
    wall_linear = rho - 1.0
    if wall_linear <= 0:
        return -np.inf
    return 10.0 * log10(wall_linear)


@dataclass
class SensingResult:
    decision: bool               # True=判为占用(H1)，False=判为空闲(H0)
    statistic: float             # 实测能量统计量 T
    threshold: float             # 使用的门限 γ
    noise_power: float           # 噪声功率 σ²（估计或给定）
    pfa_target: float            # 目标虚警率
    n: int                       # 样本数
    estimated_snr_db: float      # 由 T 与 σ² 粗估的 SNR
    noise_uncertainty_db: float


class EnergyDetector:
    """标准能量检测器，支持显式噪声功率或参考段估计、噪声不确定度。"""

    def __init__(self, noise_power: Optional[float] = None,
                 noise_uncertainty_db: float = 0.0):
        self.noise_power = noise_power
        self.noise_uncertainty_db = noise_uncertainty_db

    def detect(self, x: Sequence[complex], pfa: float = 0.01,
               noise_ref: Optional[Sequence[complex]] = None) -> SensingResult:
        a = np.asarray(x, dtype=np.complex128)
        n = int(a.size)
        if n == 0:
            raise ValueError("待检测样本为空")

        if noise_ref is not None:
            noise_power = estimate_noise_power(noise_ref)
        elif self.noise_power is not None:
            noise_power = float(self.noise_power)
        else:
            raise ValueError("需要显式提供 noise_power 或纯噪声参考段 noise_ref，"
                             "不能从可能含信号的待检测样本自身估计门限")

        gamma = threshold_for_pfa(n, noise_power, pfa, self.noise_uncertainty_db)
        stat = energy_statistic(a)
        decision = bool(stat > gamma)
        # E[T|H1]=σ²(1+Ps/σ²)，故 Ps/σ²=T/σ²-1；H0 下该值≤0，钳到极小表示无信号
        snr_linear = stat / noise_power - 1.0
        snr_est = 10.0 * log10(snr_linear) if snr_linear > 1e-9 else -90.0
        return SensingResult(
            decision=decision, statistic=stat, threshold=gamma,
            noise_power=noise_power, pfa_target=pfa, n=n,
            estimated_snr_db=snr_est,
            noise_uncertainty_db=self.noise_uncertainty_db,
        )
