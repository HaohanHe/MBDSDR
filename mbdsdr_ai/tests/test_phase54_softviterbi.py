# SPDX-License-Identifier: MIT
"""Phase54 块2：软判决 Viterbi LLR vs 硬判决 BER 增益（确定性固定种子）。"""
from __future__ import annotations
import os, sys
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from mbdsdr_ai.ccsds_rx import ConvolutionalEncoder, ViterbiDecoder  # noqa: E402


def _ber_pair(sd: float, seed: int, n_bits: int = 4000, reps: int = 3):
    enc = ConvolutionalEncoder()
    dec = ViterbiDecoder()
    rng = np.random.default_rng(seed)
    info = rng.integers(0, 2, size=n_bits).astype(np.int8)
    pairs = enc.encode_bits(list(info)) + enc.encode_bits([0] * 6)
    tx = np.array([2.0 * b - 1.0 for p in pairs for b in p])  # bit1->+1
    hb, sb = [], []
    for _ in range(reps):
        rx = tx + (rng.standard_normal(len(tx))) * sd
        hbits = (rx > 0).astype(int)
        hpairs = [(hbits[2 * i], hbits[2 * i + 1]) for i in range(len(hbits) // 2)]
        dh = dec.decode(hpairs, final_state=0)[:n_bits]
        hb.append(np.mean(np.array(dh) != info))
        spairs = [(rx[2 * i], rx[2 * i + 1]) for i in range(len(rx) // 2)]
        ds = dec.decode_soft(spairs, final_state=0)[:n_bits]
        sb.append(np.mean(np.array(ds) != info))
    return float(np.mean(hb)), float(np.mean(sb))


class TestSoftVsHard:
    def test_soft_beats_hard_at_sd08(self):
        h, s = _ber_pair(0.8, seed=5401)
        assert s < h, f"软判决 {s:.4f} 应优于硬判决 {h:.4f}"
        assert s < 0.02, "软判决 sd=0.8 应近干净"

    def test_soft_beats_hard_at_sd10(self):
        h, s = _ber_pair(1.0, seed=5402)
        assert s < h, f"软判决 {s:.4f} 应优于硬判决 {h:.4f}"

    def test_beyond_waterfall_both_fail(self):
        """sd=2.0 远超 waterfall：硬/软都 ~0.5，软判决不再改善（诚实不夸大）。"""
        h, s = _ber_pair(2.0, seed=5403)
        assert h > 0.45 and s > 0.4, "低质量点两者都应接近不可解"
