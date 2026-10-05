# SPDX-License-Identifier: MIT
"""Phase48 块1+2：BPSK 盲 CFO 估计 + 定时恢复（coarse vs gardner）实测。

全部云内合成、固定种子、确定性。

块1 盲 CFO（平方环）：
  - 注入已知 CFO（±30/±100 Hz），断言估计误差真实很小；
  - 纯噪声无谱线 → prominence 低 → 诚实返 0 频偏（不伪造估计）。

块2 定时：
  - Gardner TED（盲眼心 seed）vs coarse 眼图扫描，在 sd=0.5 + CFO30Hz 下多 trial
    统计滑移率/BER；**gardner 确实优于 coarse 才断言**（本实现已实测 0 滑移）。
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from mbdsdr_ai.ssdv_phy import (  # noqa: E402
    bpsk_modulate_bits,
    demod_bpsk,
    estimate_cfo_bpsk,
)

FS = 48000.0
SYMRATE = 4800.0
SEED = 20261008


def _make(rng, po, cfo, noise_sd, n_bits=4000):
    tx = rng.integers(0, 2, size=n_bits).astype(np.int8)
    iq = bpsk_modulate_bits(tx, FS, SYMRATE, f_if=cfo)
    d = int(round(po))
    idx = np.arange(len(iq)) - d
    iq = (np.interp(idx, np.arange(len(iq)), iq.real)
          + 1j * np.interp(idx, np.arange(len(iq)), iq.imag)).astype(np.complex64)
    if noise_sd > 0:
        iq = iq + (rng.standard_normal(iq.size)
                   + 1j * rng.standard_normal(iq.size)).astype(np.complex64) * noise_sd
    return tx, iq


def _align_ber(rxb, txb, search=12):
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


class TestBlindCFO:
    def test_cfo_estimate_accurate(self):
        """注入已知 CFO：平方环估计误差真实很小。"""
        rng = np.random.default_rng(SEED + 1)
        for true_cfo in (-100, -30, 30, 100):
            _tx, iq = _make(rng, 5.0, float(true_cfo), 0.5)
            est, prom = estimate_cfo_bpsk(iq, FS)
            assert abs(est - true_cfo) < 1.0, \
                f"CFO 估计 {est:.2f}Hz 偏离真值 {true_cfo} 过大"
            assert prom > 8.0, "有信号应显著高于纯噪声 prominence"

    def test_pure_noise_honest_zero(self):
        """纯噪声无相干谱线：prominence 低 → 诚实返 0 频偏（不伪造）。"""
        rng = np.random.default_rng(SEED + 2)
        noise = (rng.standard_normal(80000)
                 + 1j * rng.standard_normal(80000)).astype(np.complex64)
        est, prom = estimate_cfo_bpsk(noise, FS)
        assert prom < 8.0, f"纯噪声 prominence 应低，实得 {prom:.2f}"
        assert est == 0.0, "无可靠信号应诚实返 0 频偏"


class TestTimingSlipImproved:
    N = 30

    def _rates(self, noise_sd, cfo):
        rng = np.random.default_rng(SEED + int(noise_sd * 100) + int(cfo))
        out = {}
        for name in ("coarse", "gardner"):
            slips, bers = 0, []
            for _ in range(self.N):
                tx, iq = _make(rng, rng.uniform(0, FS / SYMRATE), cfo, noise_sd)
                b = demod_bpsk(iq, FS, SYMRATE, f_offset=None, timing=name)
                ber = _align_ber(b, tx) if b.size else 1.0
                bers.append(ber)
                if ber > 0.1:
                    slips += 1
            out[name] = (slips, float(np.mean(bers)))
        return out

    def test_gardner_beats_coarse_at_noise(self):
        """sd=0.5+CFO30Hz：gardner(盲眼心seed+盲CFO) 滑移率应 <= coarse。

        Phase48 实测：coarse 多次滑移、gardner 0 滑移。本断言锁定该提升不回退。
        """
        r = self._rates(0.5, 30.0)
        c_slips, c_ber = r["coarse"]
        g_slips, g_ber = r["gardner"]
        print(f"\n[slip N={self.N} sd=0.5 cfo=30Hz blind] "
              f"coarse {c_slips}/{self.N} ber={c_ber:.3f} | "
              f"gardner {g_slips}/{self.N} ber={g_ber:.3f}")
        assert g_slips <= c_slips, \
            f"gardner 滑移 {g_slips} 不应多于 coarse {c_slips}"
        assert g_slips == 0, "gardner 在该 SNR 下应 0 滑移"

    def test_noiseless_both_perfect(self):
        r = self._rates(0.0, 0.0)
        assert r["coarse"][0] == 0
        assert r["gardner"][0] == 0
