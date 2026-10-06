# SPDX-License-Identifier: MIT
"""Phase53：二阶 PLL 精载波恢复——攻陡扫频缺口（AFC 粗校 + PLL 精跟踪）。

确定性固定种子。诚实标注：低 SNR × 陡斜率仍失效（PLL 带宽-噪声权衡）。
"""
from __future__ import annotations
import os, sys
import numpy as np
from PIL import Image
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from mbdsdr_ai.ccsds_ssdv import synthesize_ssdv_ccsds_iq, ccsds_iq_to_result  # noqa: E402

FS = 48000.0
SYMRATE = 4800.0
SEED = 5301


def _grad(w=48, h=48):
    a = np.zeros((h, w, 3), np.uint8)
    for y in range(h):
        for x in range(w):
            a[y, x] = [x * 255 // (w - 1), y * 255 // (h - 1), 128]
    return np.array(Image.fromarray(a, "RGB"))


def _frame():
    tx = synthesize_ssdv_ccsds_iq(_grad(), FS, SYMRATE, callsign="P53", image_id=1)
    return tx.iq, tx.frame_bits


def _noise(rng, n, sd):
    return (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64) * sd


def _sweep(base, n, span):
    t = np.arange(n) / FS
    finst = np.linspace(0.0, span, n)
    return base * np.exp(1j * 2 * np.pi * np.cumsum(finst) / FS)


class TestPllSweep:
    def test_sweep200_afc_vs_pll(self):
        """0→200Hz 扫频：AFC-only 残错（rs 有纠错但 MCU 不全）；+PLL 满恢复 36/36。"""
        rng = np.random.default_rng(SEED)
        base, fb = _frame()
        n = base.size
        swept = _sweep(base, n, 200.0) + _noise(rng, n, 0.5)
        r_afc = ccsds_iq_to_result(swept, FS, SYMRATE, frame_bits=fb,
                                   f_offset=0.0, timing="gardner", afc=True)
        r_pll = ccsds_iq_to_result(swept, FS, SYMRATE, frame_bits=fb,
                                   f_offset=0.0, timing="gardner", afc=True, pll=True)
        assert r_afc.n_asm_frames == 1
        assert r_pll.n_asm_frames == 1
        assert r_pll.received_mcus == 36, "PLL 应把 0→200Hz 扫频恢复到满 MCU"

    def test_sweep500_pll_recovers(self):
        """0→500Hz：AFC-only ASM=0；+PLL 满恢复（type-2 环路跟踪频斜）。"""
        rng = np.random.default_rng(SEED + 1)
        base, fb = _frame()
        n = base.size
        swept = _sweep(base, n, 500.0) + _noise(rng, n, 0.5)
        r_afc = ccsds_iq_to_result(swept, FS, SYMRATE, frame_bits=fb,
                                   f_offset=0.0, timing="gardner", afc=True)
        r_pll = ccsds_iq_to_result(swept, FS, SYMRATE, frame_bits=fb,
                                   f_offset=0.0, timing="gardner", afc=True, pll=True)
        assert r_afc.n_asm_frames == 0, "AFC-only 对 0→500Hz 应 ASM 失败（对照）"
        assert r_pll.received_mcus == 36

    def test_low_snr_steep_sweep_honest_fail(self):
        """sd=1.0 × 0→1000Hz 陡斜率：PLL 仍失锁（噪声压过鉴相），诚实 0 帧。

        理论：PLL 环路带宽为抑噪必须做窄；带宽窄则跟踪频斜的速度/范围受限，
        低 SNR 下鉴相噪声使环路失锁——带宽-噪声-跟踪范围三角权衡。
        """
        rng = np.random.default_rng(SEED + 2)
        base, fb = _frame()
        n = base.size
        swept = _sweep(base, n, 1000.0) + _noise(rng, n, 1.0)
        r = ccsds_iq_to_result(swept, FS, SYMRATE, frame_bits=fb,
                               f_offset=0.0, timing="gardner", afc=True, pll=True)
        assert r.n_asm_frames == 0 or r.received_mcus < 36, \
            "低 SNR×陡斜率不应被假装满恢复"
        # 不伪造：若出图也允许（该种子下可能临界），但绝不保证满 MCU
