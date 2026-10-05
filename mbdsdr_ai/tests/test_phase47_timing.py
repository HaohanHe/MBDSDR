# SPDX-License-Identifier: MIT
"""Phase47 块2：BPSK 符号定时滑移率实测（coarse 眼图粗定时 vs Gardner TED 闭环）。

方法（公平、确定性、固定种子）
------------------------------
每个 trial：随机比特 → BPSK 复 IQ → 注入 (a) 随机定时相位偏移 δ∈[0,sps)，
(b) 小频偏 CFO=30 Hz，(c) AWGN sd。RX 用**真值 CFO 下变频**（给定时恢复上界条件：
频偏已对齐，只比定时），分别跑 coarse 眼图扫描 / Gardner TED 闭环，解出比特后对
已知 TX 比特做整数偏移对齐，取最小 BER。BER>0.1 记一次"滑移"（整比特错位、
后级 ASM/RS 不可恢复）。

诚实声明（红线）
----------------
- coarse 眼图扫描滑移率如实报告。
- **当前干净室 Gardner TED 闭环尚未正确锁到眼心**：在随机（非 0x55 前导）数据上
  收敛到约半符号稳态偏差，BER≈0.5。本测试不伪造"gardner 优于 coarse"；gardner
  滑移率如实计入并标注未收敛 FAIL。低 SNR / 未收敛区间不给假图。
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from mbdsdr_ai.ssdv_phy import bpsk_modulate_bits, _gardner_symbols  # noqa: E402

FS = 48000.0
SYMRATE = 4800.0
SPS = FS / SYMRATE
N_TRIALS = 30
SEED = 20261007


def _align_ber(rxb: np.ndarray, txb: np.ndarray, search: int = 4) -> float:
    L = min(len(rxb), len(txb))
    best = 1.0
    for k in range(-search, search + 1):
        a = rxb[max(0, k):L]
        b = txb[max(0, -k):L - abs(k)]
        m = min(len(a), len(b))
        if m < 20:
            continue
        best = min(best, float(np.mean(a[:m] != b[:m])))
    return best


def _make(rng: np.random.Generator, phase_off: float, cfo: float,
          noise_sd: float, n_bits: int = 4000):
    tx = rng.integers(0, 2, size=n_bits).astype(np.int8)
    iq = bpsk_modulate_bits(tx, FS, SYMRATE, f_if=cfo)
    # 分数样本定时偏移
    d = int(round(phase_off))
    idx = np.arange(len(iq)) - d
    iq = (np.interp(idx, np.arange(len(iq)), iq.real)
          + 1j * np.interp(idx, np.arange(len(iq)), iq.imag)).astype(np.complex64)
    if noise_sd > 0:
        iq = iq + (rng.standard_normal(iq.size)
                   + 1j * rng.standard_normal(iq.size)).astype(np.complex64) * noise_sd
    return tx, iq


def _downconvert(iq: np.ndarray, cfo: float) -> np.ndarray:
    t = np.arange(iq.size) / FS
    base = iq * np.exp(-1j * 2 * np.pi * cfo * t)
    return np.convolve(base.real, np.ones(int(round(SPS))) / int(round(SPS)),
                       mode="same")


def _coarse_bits(filt: np.ndarray) -> np.ndarray:
    n_sym = int(filt.size / SPS)
    bo, bs = 0.0, -1.0
    for off in np.linspace(0.0, SPS, int(SPS), endpoint=False):
        ix = np.round(off + np.arange(n_sym) * SPS).astype(int)
        ix = ix[ix < filt.size]
        s = abs(np.mean(filt[ix]))
        if s > bs:
            bs, bo = s, off
    ix = np.round(bo + np.arange(n_sym) * SPS).astype(int)
    ix = ix[ix < filt.size]
    return (filt[ix] > 0.0).astype(np.int8)


def _gardner_bits(filt: np.ndarray) -> np.ndarray:
    b, diag = _gardner_symbols(filt, SPS)
    return b if diag.locked else np.zeros(0, dtype=np.int8)


def _slip_rates(noise_sd: float, cfo: float, n_trials: int = N_TRIALS):
    rng = np.random.default_rng(SEED + int(noise_sd * 100) + int(cfo))
    out = {}
    for name, fn in (("coarse", _coarse_bits), ("gardner", _gardner_bits)):
        slips = 0
        bers = []
        for _ in range(n_trials):
            tx, iq = _make(rng, rng.uniform(0, SPS), cfo, noise_sd)
            filt = _downconvert(iq, cfo)
            b = fn(filt)
            ber = _align_ber(b, tx) if b.size else 1.0
            bers.append(ber)
            if ber > 0.1:
                slips += 1
        out[name] = (slips, np.mean(bers))
    return out


class TestTimingSlipRates:
    def test_noiseless_coarse_perfect(self):
        """无噪：coarse 眼图扫描必须 0 滑移（确定性基线）。"""
        r = _slip_rates(0.0, 0.0, n_trials=20)
        slips, mean_ber = r["coarse"]
        assert slips == 0, f"无噪 coarse 不应滑移，实得 {slips}/20"
        assert mean_ber < 0.01

    def test_report_slip_rates_at_moderate_noise(self):
        """中等噪声(sd=0.5)+CFO30Hz：如实打印 coarse/gardner 滑移率。

        断言只锁 coarse 不崩溃（滑移率有上界）；**不**断言 gardner 优于 coarse
        ——当前干净室 gardner 未正确锁眼心（见模块 docstring），如实记录。
        """
        r = _slip_rates(0.5, 30.0)
        c_slips, c_ber = r["coarse"]
        g_slips, g_ber = r["gardner"]
        print(f"\n[slip-rate N={N_TRIALS} sd=0.5 cfo=30Hz] "
              f"coarse: {c_slips}/{N_TRIALS} slips mean_ber={c_ber:.3f} | "
              f"gardner: {g_slips}/{N_TRIALS} slips mean_ber={g_ber:.3f}")
        # coarse 在该 SNR 下应大部分 trial 锁住（如实上界）。
        assert c_slips <= int(0.5 * N_TRIALS), \
            f"coarse 滑移率异常高：{c_slips}/{N_TRIALS}"
