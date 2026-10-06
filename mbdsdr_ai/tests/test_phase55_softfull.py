# SPDX-License-Identifier: MIT
"""Phase55：软判决全链闭环（demod_bpsk_soft→decode_soft→解扰→RS→JPEG）。"""
from __future__ import annotations
import os, sys
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from mbdsdr_ai.ccsds_ssdv import synthesize_ssdv_ccsds_iq, ccsds_iq_to_result  # noqa: E402

FS, SR = 48000.0, 4800.0


def _img():
    a = np.zeros((48, 48, 3), np.uint8)
    a[:, :, 0] = np.linspace(0, 255, 48, dtype=np.uint8)
    return a


class TestSoftFullChain:
    def test_clean_soft_full_chain(self):
        tx = synthesize_ssdv_ccsds_iq(_img(), FS, SR, callsign="T", image_id=1)
        r = ccsds_iq_to_result(tx.iq, FS, SR, frame_bits=tx.frame_bits,
                               timing="coarse", soft=True)
        assert r.n_asm_frames == 1
        assert r.received_mcus == r.mcu_count == 36

    def test_soft_beats_hard_weak(self):
        """sd=0.7 多 reps 累计：软判决恢复 MCU 应 > 硬判决（14× 隔离增益兑现到出图）。"""
        tx = synthesize_ssdv_ccsds_iq(_img(), FS, SR, callsign="T", image_id=1)
        base, fb, n = tx.iq, tx.frame_bits, tx.iq.size
        rng = np.random.default_rng(5570)
        hw = sw = 0
        for _ in range(8):
            iq = base + (rng.standard_normal(n) + 1j * rng.standard_normal(n)
                         ).astype(np.complex64) * 0.7
            hw += ccsds_iq_to_result(iq, FS, SR, frame_bits=fb,
                                     timing="coarse").received_mcus
            sw += ccsds_iq_to_result(iq, FS, SR, frame_bits=fb,
                                     timing="coarse", soft=True).received_mcus
        assert sw > hw, f"软 {sw} 应多于硬 {hw}"

    def test_pure_noise_honest_empty(self):
        rng = np.random.default_rng(5571)
        noise = (rng.standard_normal(48000 * 2) +
                 1j * rng.standard_normal(48000 * 2)).astype(np.complex64)
        r = ccsds_iq_to_result(noise, FS, SR, frame_bits=8172,
                               timing="coarse", soft=True)
        assert r.n_asm_frames == 0 and r.received_mcus == 0  # 诚实空态不伪造
