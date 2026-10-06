# SPDX-License-Identifier: MIT
"""Phase56：ASM hamming 容忍 + 软路径联合精恢复。"""
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


class TestAsmTolerance:
    def test_clean_unchanged_tol2(self):
        tx = synthesize_ssdv_ccsds_iq(_img(), FS, SR, callsign="T", image_id=1)
        r = ccsds_iq_to_result(tx.iq, FS, SR, frame_bits=tx.frame_bits,
                               timing="coarse", asm_tol=2)
        assert r.received_mcus == 36

    def test_soft_tol2_beats_hard_weak(self):
        """sd=1.0 硬(tol2)=0，软(tol2) 应恢复更多 MCU。"""
        tx = synthesize_ssdv_ccsds_iq(_img(), FS, SR, callsign="T", image_id=1)
        base, fb, n = tx.iq, tx.frame_bits, tx.iq.size
        rng = np.random.default_rng(5610)
        h = s = 0
        for _ in range(8):
            iq = base + (rng.standard_normal(n) + 1j * rng.standard_normal(n)
                         ).astype(np.complex64) * 1.0
            h += ccsds_iq_to_result(iq, FS, SR, frame_bits=fb,
                                   timing="coarse", asm_tol=2).received_mcus
            s += ccsds_iq_to_result(iq, FS, SR, frame_bits=fb, timing="coarse",
                                    soft=True, asm_tol=2).received_mcus
        assert s > h

    def test_false_sync_pure_noise(self):
        """纯噪声 tol=2 不产生假同步（诚实计数）。"""
        rng = np.random.default_rng(5611)
        fake = 0
        for _ in range(30):
            nz = (rng.standard_normal(int(FS) * 2) +
                  1j * rng.standard_normal(int(FS) * 2)).astype(np.complex64)
            fake += ccsds_iq_to_result(nz, FS, SR, frame_bits=8172,
                                       timing="coarse", asm_tol=2).n_asm_frames
        assert fake == 0, f"纯噪声不应假同步，实得 {fake}"
