# SPDX-License-Identifier: MIT
"""Phase51 块1：接收链四场景健壮性回归（确定性，固定种子）。

数字来源见 ``exp_phase51_robustness.py``（同目录，可复跑打印全梯度）。本测试只锁
关键判据，把"诚实失效"钉死不回退：

(a) 弱信号：sd=1.0 仍全图 36/36 MCU；sd=3.0 诚实 0 帧（不伪造）。
(c) 多普勒：恒定 100Hz 盲估中并解码；整段线性扫频 0→200Hz 整段单峰估计不可靠
    → 诚实 ASM=0（单缓冲盲 CFO 不保证跟踪扫频）。
(d) CW 干扰：无 CW 全图；强 CW(amp=6 @ +600Hz) 诚实 0 帧（不伪造）。
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
SEED = 5101


def _grad(w=48, h=48):
    a = np.zeros((h, w, 3), np.uint8)
    for y in range(h):
        for x in range(w):
            a[y, x] = [x * 255 // (w - 1), y * 255 // (h - 1), 128]
    return np.array(Image.fromarray(a, "RGB"))


def _frame():
    tx = synthesize_ssdv_ccsds_iq(_grad(), FS, SYMRATE, callsign="P51TST",
                                  image_id=1, f_if=0.0)
    return tx.iq, tx.frame_bits


def _noise(rng, n, sd):
    return (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64) * sd


class TestWeakSignal:
    def test_clean_noise_still_decodes(self):
        rng = np.random.default_rng(SEED)
        base, fb = _frame()
        iq = base + _noise(rng, base.size, 1.0)
        r = ccsds_iq_to_result(iq, FS, SYMRATE, frame_bits=fb,
                               f_offset=None, timing="gardner")
        assert r.n_asm_frames == 1
        assert r.received_mcus == r.mcu_count == 36
        assert len(r.jpeg) > 0

    def test_deep_noise_honest_empty(self):
        """sd=3.0 远超失效阈值：诚实 0 帧 0 包 0 字节 JPEG，不伪造。"""
        rng = np.random.default_rng(SEED)
        base, fb = _frame()
        iq = base + _noise(rng, base.size, 3.0)
        r = ccsds_iq_to_result(iq, FS, SYMRATE, frame_bits=fb,
                               f_offset=None, timing="gardner")
        assert r.n_asm_frames == 0
        assert r.n_packets == 0
        assert r.jpeg == b""


class TestDoppler:
    def test_constant_cfo_decodes(self):
        rng = np.random.default_rng(SEED + 2)
        base, fb = _frame()
        n = base.size
        t = np.arange(n) / FS
        iq = base * np.exp(1j * 2 * np.pi * 100.0 * t) + _noise(rng, n, 0.5)
        r = ccsds_iq_to_result(iq, FS, SYMRATE, frame_bits=fb,
                               f_offset=None, timing="gardner")
        assert abs(r.cfo_est_hz - 100.0) < 1.0
        assert r.received_mcus == 36

    def test_linear_sweep_not_guaranteed_honest_fail(self):
        """整段线性扫频 0→200Hz：单缓冲盲 CFO 落到中点、谱线展宽 → 诚实 ASM=0。
        （跟踪扫频需分段 AFC/锁相环，当前实现不保证。）"""
        rng = np.random.default_rng(SEED + 2)
        base, fb = _frame()
        n = base.size
        t = np.arange(n) / FS
        finst = np.linspace(0.0, 200.0, n)
        swept = base * np.exp(1j * 2 * np.pi * np.cumsum(finst) / FS)
        swept = swept + _noise(rng, n, 0.5)
        r = ccsds_iq_to_result(swept, FS, SYMRATE, frame_bits=fb,
                               f_offset=None, timing="gardner")
        assert r.n_asm_frames == 0, "整段扫频不应被假装解码"
        assert r.jpeg == b""


class TestCwInterference:
    def test_no_cw_decodes(self):
        rng = np.random.default_rng(SEED + 3)
        base, fb = _frame()
        iq = base + _noise(rng, base.size, 0.5)
        r = ccsds_iq_to_result(iq, FS, SYMRATE, frame_bits=fb,
                               f_offset=None, timing="gardner")
        assert r.received_mcus == 36

    def test_strong_cw_honest_fail(self):
        """amp=6 @ +600Hz CW 压过信号：盲 CFO 被 CW 骗锁、ASM 不同步 → 诚实 0 帧。"""
        rng = np.random.default_rng(SEED + 3)
        base, fb = _frame()
        n = base.size
        t = np.arange(n) / FS
        # Phase52 抛物线亚-bin CFO 后，amp<=12 @+600Hz 已不毒死盲 CFO（能自解）；
        # 真正的诚实失效边界在 amp=20（信号被 CW 淹没，notch 也救不回）。
        cw = 20.0 * np.exp(1j * 2 * np.pi * 600.0 * t)
        iq = base + cw + _noise(rng, n, 0.5)
        r = ccsds_iq_to_result(iq, FS, SYMRATE, frame_bits=fb,
                               f_offset=None, timing="gardner")
        assert r.n_asm_frames == 0
        assert r.jpeg == b""
