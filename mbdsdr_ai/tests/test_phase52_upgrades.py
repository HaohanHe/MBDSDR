# SPDX-License-Identifier: MIT
"""Phase52：接收链真机级健壮性升级——分段 AFC / CW 对消 / 多缓冲重同步。

确定性固定种子。诚实标注：陡扫频 / 超强 CW 仍不保证（AFC 带宽-分辨率矛盾、
信号被淹没）。
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
SEED = 5201


def _grad(w=48, h=48):
    a = np.zeros((h, w, 3), np.uint8)
    for y in range(h):
        for x in range(w):
            a[y, x] = [x * 255 // (w - 1), y * 255 // (h - 1), 128]
    return np.array(Image.fromarray(a, "RGB"))


def _frame():
    tx = synthesize_ssdv_ccsds_iq(_grad(), FS, SYMRATE, callsign="P52", image_id=1)
    return tx.iq, tx.frame_bits


def _noise(rng, n, sd):
    return (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64) * sd


class TestSegmentAfc:
    def test_constant_cfo_still_fine(self):
        """恒定 CFO 是单估平方环的舒适区（不是 AFC 的用例），回归基线：仍 36/36。"""
        rng = np.random.default_rng(SEED)
        base, fb = _frame()
        n = base.size
        t = np.arange(n) / FS
        iq = base * np.exp(1j * 2 * np.pi * 100.0 * t) + _noise(rng, n, 0.5)
        r = ccsds_iq_to_result(iq, FS, SYMRATE, frame_bits=fb,
                               f_offset=None, timing="gardner")
        assert r.received_mcus == 36

    def test_sweep_improves_asm_acquisition(self):
        """线性扫 0→200Hz：无 AFC 时 ASM=0；分段 AFC 至少恢复 ASM 捕获。

        诚实：满 36/36 对陡斜率仍不保证（AFC 窗分辨率-跟踪带宽固有矛盾），
        这里只断言 AFC 把 ASM 从无到有（捕获改善），不伪造全恢复。
        """
        rng = np.random.default_rng(SEED + 1)
        base, fb = _frame()
        n = base.size
        t = np.arange(n) / FS
        finst = np.linspace(0.0, 200.0, n)
        swept = base * np.exp(1j * 2 * np.pi * np.cumsum(finst) / FS)
        swept = swept + _noise(rng, n, 0.5)
        r_no = ccsds_iq_to_result(swept, FS, SYMRATE, frame_bits=fb,
                                  f_offset=None, timing="gardner")
        r_afc = ccsds_iq_to_result(swept, FS, SYMRATE, frame_bits=fb,
                                   f_offset=0.0, timing="gardner", afc=True)
        assert r_no.n_asm_frames == 0, "无 AFC 扫频应 ASM 失败（对照基线）"
        assert r_afc.n_asm_frames >= 1, "AFC 至少应恢复 ASM 捕获"


class TestCwNotch:
    def test_no_cw_not_false_triggered(self):
        rng = np.random.default_rng(SEED + 2)
        base, fb = _frame()
        iq = base + _noise(rng, base.size, 0.5)
        r = ccsds_iq_to_result(iq, FS, SYMRATE, frame_bits=fb,
                               f_offset=None, timing="gardner", notch_cw=True)
        assert r.received_mcus == 36, "无 CW 不应被误陷而掉 MCU"

    def test_moderate_cw_notched(self):
        """CW amp=3 @ +600Hz：notch 后仍全图（参数对消干净）。"""
        rng = np.random.default_rng(SEED + 2)
        base, fb = _frame()
        n = base.size
        t = np.arange(n) / FS
        cw = 3.0 * np.exp(1j * 2 * np.pi * 600.0 * t)
        iq = base + cw + _noise(rng, n, 0.5)
        r = ccsds_iq_to_result(iq, FS, SYMRATE, frame_bits=fb,
                               f_offset=None, timing="gardner", notch_cw=True)
        assert r.received_mcus == 36

    def test_extreme_cw_honest_fail(self):
        """amp=20 CW 淹没信号：notch 也救不回，诚实 0 帧。"""
        rng = np.random.default_rng(SEED + 2)
        base, fb = _frame()
        n = base.size
        t = np.arange(n) / FS
        cw = 20.0 * np.exp(1j * 2 * np.pi * 600.0 * t)
        iq = base + cw + _noise(rng, n, 0.5)
        r = ccsds_iq_to_result(iq, FS, SYMRATE, frame_bits=fb,
                               f_offset=None, timing="gardner", notch_cw=True)
        assert r.n_asm_frames == 0
        assert r.jpeg == b""


class TestMultiBufferResync:
    def test_recovers_after_gap(self):
        """clean|噪声遮挡|clean：无 resync 0 帧；resync 恢复尾部帧 36/36。"""
        rng = np.random.default_rng(SEED + 3)
        base, fb = _frame()
        gap = _noise(rng, base.size, 1.5)
        iq = np.concatenate([base, gap, base])
        r_no = ccsds_iq_to_result(iq, FS, SYMRATE, frame_bits=fb,
                                  f_offset=None, timing="gardner")
        r_rs = ccsds_iq_to_result(iq, FS, SYMRATE, frame_bits=fb,
                                  f_offset=None, timing="gardner", resync=True)
        assert r_no.n_asm_frames == 0, "无 resync 被遮挡段带偏（对照）"
        assert r_rs.n_asm_frames >= 1
        assert r_rs.received_mcus == 36
