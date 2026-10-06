# SPDX-License-Identifier: MIT
"""Phase54 块1：接收能力包络网格（SNR × CFO × 扫频斜率 × CW × 遮挡）。

固定种子确定性，分批跑防 OOM。每格跑全链（AFC+PLL+notch+resync 视场景），
记录 ASM/RS nerrors/MCU 恢复数，输出 CSV。失效面诚实标注。
运行: python3 mbdsdr_ai/tests/exp_phase54_envelope.py
"""
from __future__ import annotations
import os, sys, csv
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from mbdsdr_ai.ccsds_ssdv import synthesize_ssdv_ccsds_iq, ccsds_iq_to_result

FS = 48000.0
SYMRATE = 4800.0
SEED = 5400
OUT = os.path.join(os.path.dirname(__file__), "..", "..",
                   "docs", "learn", "phase54", "envelope.csv")


def grad(w=48, h=48):
    a = np.zeros((h, w, 3), np.uint8)
    for y in range(h):
        for x in range(w):
            a[y, x] = [x * 255 // (w - 1), y * 255 // (h - 1), 128]
    return np.array(Image.fromarray(a, "RGB"))


def main():
    tx = synthesize_ssdv_ccsds_iq(grad(), FS, SYMRATE, callsign="ENV", image_id=1)
    base, fb = tx.iq, tx.frame_bits
    n = base.size
    t = np.arange(n) / FS
    rows = []

    def run(tag, iq, **kw):
        r = ccsds_iq_to_result(iq, FS, SYMRATE, frame_bits=fb, f_offset=0.0,
                               timing="gardner", **kw)
        rows.append({"case": tag, "asm": r.n_asm_frames,
                     "rs": str(r.rs_nerrors), "mcu": r.received_mcus,
                     "mcu_tot": r.mcu_count})
        print(f"  {tag}: asm={r.n_asm_frames} mcu={r.received_mcus}/{r.mcu_count}")

    print("=== (1) SNR 梯度 sd × (AFC+PLL, 恒定 100Hz CFO) ===")
    rng = np.random.default_rng(SEED)
    for sd in (0.3, 0.5, 0.8, 1.0, 1.3, 1.6, 2.0):
        iq = base * np.exp(1j * 2 * np.pi * 100 * t) + \
            (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64) * sd
        run(f"snr_sd{sd}", iq, afc=True, pll=True)

    print("=== (2) 扫频斜率 0→span Hz (sd=0.5, AFC+PLL) ===")
    for span in (0, 100, 200, 500, 1000):
        finst = np.linspace(0, span, n)
        iq = base * np.exp(1j * 2 * np.pi * np.cumsum(finst) / FS) + \
            (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64) * 0.5
        run(f"sweep{span}", iq, afc=True, pll=True)

    print("=== (3) CW 强度 @+600Hz (sd=0.5, notch) ===")
    for amp in (0, 3, 6, 12, 20):
        cw = amp * np.exp(1j * 2 * np.pi * 600 * t)
        iq = base + cw + (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64) * 0.5
        run(f"cw{amp}", iq, notch_cw=True)

    print("=== (4) 遮挡长度（帧）(resync) ===")
    for gap_frames in (0, 1, 2, 4):
        gap = (rng.standard_normal(base.size * gap_frames)
               + 1j * rng.standard_normal(base.size * gap_frames)).astype(np.complex64) * 1.5
        iq = np.concatenate([base, gap, base]) if gap_frames else base
        run(f"gap{gap_frames}", iq, resync=True)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["case", "asm", "rs", "mcu", "mcu_tot"])
        w.writeheader()
        w.writerows(rows)
    print(f"\nCSV -> {os.path.abspath(OUT)} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
