# SPDX-License-Identifier: MIT
"""Phase51 块1：接收链全场景健壮性实验（确定性，固定种子）。

四真实退化场景注入合成 CCSDS/BPSK IQ，跑盲 CFO + Gardner 全链，给真实数字。
运行: python3 mbdsdr_ai/tests/exp_phase51_robustness.py
"""
from __future__ import annotations
import warnings; warnings.filterwarnings("ignore")
import os, sys
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from mbdsdr_ai.ccsds_ssdv import synthesize_ssdv_ccsds_iq, ccsds_iq_to_result
from mbdsdr_ai.ssdv_phy import estimate_cfo_bpsk

FS = 48000.0
SYMRATE = 4800.0
SEED = 5101


def grad(w=48, h=48):
    a = np.zeros((h, w, 3), np.uint8)
    for y in range(h):
        for x in range(w):
            a[y, x] = [x * 255 // (w - 1), y * 255 // (h - 1), 128]
    return np.array(Image.fromarray(a, "RGB"))


def frame_iq():
    tx = synthesize_ssdv_ccsds_iq(grad(), FS, SYMRATE, callsign="P51TST",
                                  image_id=1, f_if=0.0)
    return tx.iq, tx.frame_bits


def noise_like(rng, n, sd):
    return (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64) * sd


def report(tag, r):
    return (f"{tag}: cfo={r.cfo_est_hz:+6.1f} asm={r.n_asm_frames} "
            f"rs={r.rs_nerrors} pkts={r.n_packets} "
            f"mcu={r.received_mcus}/{r.mcu_count} jpg={len(r.jpeg)}")


def scenario_a_weak():
    print("\n=== (a) 弱信号 sd 梯度 (盲CFO+gardner) ===")
    rng = np.random.default_rng(SEED)
    base, fb = frame_iq()
    for sd in (0.3, 0.5, 0.8, 1.0, 1.3, 1.6, 2.0, 2.5, 3.0):
        iq = base + noise_like(rng, base.size, sd)
        r = ccsds_iq_to_result(iq, FS, SYMRATE, frame_bits=fb, f_offset=None, timing="gardner")
        print(f"  sd={sd:<4} " + report("", r))


def scenario_b_burst():
    print("\n=== (b) 突发遮挡：帧重复3次，中间段置零(遮挡) ===")
    rng = np.random.default_rng(SEED + 1)
    base, fb = frame_iq()
    gap = base.size
    # 三段：clean | 遮挡(零+噪) | clean
    obsc = noise_like(rng, base.size, 1.5)  # 遮挡段只剩噪声
    iq = np.concatenate([base, obsc, base + noise_like(rng, base.size, 0.6)])
    r = ccsds_iq_to_result(iq, FS, SYMRATE, frame_bits=fb, f_offset=None, timing="gardner")
    print("  clean|noise-gap|clean(sd0.6): " + report("", r))
    # 无遮挡对照
    iq0 = np.concatenate([base, base])
    r0 = ccsds_iq_to_result(iq0, FS, SYMRATE, frame_bits=fb, f_offset=None, timing="gardner")
    print("  clean|clean (对照):            " + report("", r0))


def scenario_c_doppler():
    print("\n=== (c) 多普勒线性扫频 0→+200Hz 全程 (盲CFO整段估计) ===")
    rng = np.random.default_rng(SEED + 2)
    base, fb = frame_iq()
    n = base.size
    t = np.arange(n) / FS
    f_inst = np.linspace(0.0, 200.0, n)          # 瞬时频偏线性爬升
    phase = 2 * np.pi * np.cumsum(f_inst) / FS
    swept = base * np.exp(1j * phase)
    swept = swept + noise_like(rng, n, 0.5)
    est, prom = estimate_cfo_bpsk(swept, FS)
    print(f"  整段盲CFO: est={est:+.1f}Hz prom={prom:.1f} (真值0~200扫，无单峰可估)")
    r = ccsds_iq_to_result(swept, FS, SYMRATE, frame_bits=fb, f_offset=None, timing="gardner")
    print("  " + report("swept", r))
    # 对照：恒定 100Hz（盲CFO应估中）
    const = base * np.exp(1j * 2 * np.pi * 100.0 * t)
    const = const + noise_like(rng, n, 0.5)
    r2 = ccsds_iq_to_result(const, FS, SYMRATE, frame_bits=fb, f_offset=None, timing="gardner")
    print("  对照 const100Hz: " + report("", r2))


def scenario_d_cw():
    print("\n=== (d) 窄带CW干扰叠加 (频率落在带内 +600Hz) ===")
    rng = np.random.default_rng(SEED + 3)
    base, fb = frame_iq()
    n = base.size
    t = np.arange(n) / FS
    for amp in (0.0, 0.3, 0.8, 1.5, 3.0, 6.0):
        cw = amp * np.exp(1j * 2 * np.pi * 600.0 * t)
        iq = base + cw + noise_like(rng, n, 0.5)
        r = ccsds_iq_to_result(iq, FS, SYMRATE, frame_bits=fb, f_offset=None, timing="gardner")
        print(f"  cw_amp={amp:<4} " + report("", r))


if __name__ == "__main__":
    scenario_a_weak()
    scenario_b_burst()
    scenario_c_doppler()
    scenario_d_cw()
