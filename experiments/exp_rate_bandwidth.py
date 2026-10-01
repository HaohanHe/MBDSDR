#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
实验：解码成功率 vs 接收带宽 B_rx（固定 Eb/N0，仿真口径）
=========================================================

工程问题：固定比特率 Rb 与固定噪声 PSD 下，接收端低通带宽 B_rx 取多宽？
  - B_rx < null-to-null(≈2·Rb)：把矩形 BPSK 主瓣切掉 -> ISI + 信号能量损失 -> 成功率降。
  - B_rx ≥ null-to-null：主瓣完整通过 -> 成功率饱和（积分清零再抑制带外噪声）。

被测链（自包含、真实 DSP，非 mock）：
  随机比特 -> ±1 矩形脉冲成型(fs=200k, sps=20, Rb=10k) -> 加固定方差复高斯白噪
  -> firwin 低通(截止 B_rx，扫描) -> 群延迟补偿 -> 积分清零(每 sps 样)相干判决
  -> 整包 0 误码判成功。噪声随 B_rx 被同一低通带限（B_rx 越宽带入噪声越多，
     但此处主导项是"信号主瓣是否被切"）。

口径：synthetic（仿真）。噪声方差与信号功率全程固定，只扫 B_rx。
每格 trials 次独立试验，成功率 + Wilson 95% CI。
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

import numpy as np
from scipy.signal import firwin, lfilter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from experiments.common import runner, manifest  # noqa: E402
from experiments.common import plot as eplot  # noqa: E402

ORIGIN = "synthetic"
OUT_DIR = os.path.join(ROOT, "paper", "experiments")
FIG_DIR = os.path.join(OUT_DIR, "figures")

# 固定参数（BPSK 10k，fs=200k 使 Nyquist 远大于信号主瓣，可扫完整带宽区间）
FS = 200_000.0
SPs = 20
RB = 10_000.0
NBITS = 200
# 固定噪声方差（=固定噪声 PSD / 固定 Eb/N0 标定档）
SIG_VAR = 2.0
TAPS = 101
DELAY = (TAPS - 1) // 2          # firwin 群延迟（样），积分前补偿


def trial(rng: np.random.Generator, brx_hz: float) -> bool:
    """一次试验：合成 BPSK -> 固定方差白噪 -> 可变低通 -> 延迟补偿 -> 积分清零 -> 整包无误码?"""
    bits = rng.integers(0, 2, NBITS)
    sym = np.where(bits == 1, 1.0, -1.0).astype(np.complex128)
    x = np.repeat(sym, SPs)
    x = x / (np.sqrt(np.mean(np.abs(x) ** 2)) + 1e-12)
    w = (rng.standard_normal(x.size) + 1j * rng.standard_normal(x.size)) / np.sqrt(2.0)
    y = x + SIG_VAR * w
    taps = firwin(TAPS, brx_hz, fs=FS)     # cutoff 必须 < fs/2
    y = lfilter(taps, 1.0, y)
    y = y[DELAY:]                          # 群延迟补偿，对齐码元边界
    m = (len(y) // SPs) * SPs
    y = y[:m]
    dec = (np.real(y).reshape(-1, SPs).mean(axis=1) > 0).astype(int)
    n = min(len(bits), len(dec))
    return bool(np.array_equal(dec[:n], bits[:n]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=100)
    ap.add_argument("--seed", type=int, default=20261001)
    ap.add_argument("--bw-grid", type=str,
                    default="8000,12000,16000,20000,25000,30000,40000,60000,99000",
                    help="接收低通截止带宽(Hz)；99000≈Nyquist 全通参考")
    ap.add_argument("--out", default=OUT_DIR)
    args = ap.parse_args()

    bw_grid = [float(b) for b in args.bw_grid.split(",")]
    runner_obj = runner.ExperimentRunner(args.seed)

    rows = []
    curve_x, curve_rate, curve_lo, curve_hi = [], [], [], []
    for brx in bw_grid:
        rng = runner_obj.make_rng(f"bw={brx:.0f}")
        succ = 0
        for _ in range(args.trials):
            if trial(rng, brx):
                succ += 1
        cell = runner.BinomialCell(succ, args.trials)
        lo, hi = cell.wilson
        row = {
            "mode": "bpsk_10k",
            "rb_bps": RB, "fs_hz": FS, "sig_var_fixed": SIG_VAR,
            "brx_hz": round(brx, 1),
            "brx_over_rb": round(brx / RB, 3),
            "brx_label": f"{brx/1000:.0f}k",
            "successes": succ, "n_trials": args.trials,
            "success_rate": round(cell.rate, 4),
            "wilson_lo": round(lo, 4), "wilson_hi": round(hi, 4),
            "data_origin": ORIGIN,
        }
        rows.append(row)
        curve_x.append(brx / 1000.0)
        curve_rate.append(cell.rate); curve_lo.append(lo); curve_hi.append(hi)
        print(f"[BPSK 10k] B_rx={brx/1000:6.1f} kHz (B/Rb={brx/RB:4.2f})  "
              f"success={succ}/{args.trials} ({cell.rate*100:5.1f}%)", flush=True)

    os.makedirs(args.out, exist_ok=True)
    csv_path = os.path.join(args.out, "rate_bandwidth_success.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print(f"\n[写] {csv_path}")

    n_samples = len(bw_grid) * args.trials
    fig_path = eplot.plot_success_vs_ebn0(
        {"BPSK 10k": {"x": curve_x, "rate": curve_rate,
                      "lo": curve_lo, "hi": curve_hi}},
        origin=ORIGIN, n_samples=n_samples, out_dir=FIG_DIR,
        fname_prefix="rate_bandwidth_success",
        xlabel="Receiver low-pass cutoff (kHz)",
        ylabel="Packet decode success rate",
        title="Decode success vs receiver bandwidth (fixed Eb/N0, BPSK 10k)")
    print(f"[写] {fig_path}")

    mpath = manifest.write_manifest(
        out_dir=args.out, script=__file__, seed=args.seed, data_origin=ORIGIN,
        params={"mode": "bpsk_10k", "rb": RB, "fs": FS, "sps": SPs,
                "nbits": NBITS, "sig_var_fixed": SIG_VAR,
                "bw_grid_hz": bw_grid,
                "bw_note": "噪声 PSD 全程固定；低于 null-to-null(≈2Rb=20k) 信号主瓣被切 -> ISI；"
                           "低通已做群延迟补偿",
                "csv": os.path.basename(csv_path),
                "figure": os.path.basename(fig_path)},
        n_samples=n_samples,
        filename="manifest_rate_bandwidth.json")
    print(f"[写] {mpath}")
    print(f"\n口径: {ORIGIN}（仿真）；总样本 N={n_samples}；固定噪声 PSD，只扫 B_rx")


if __name__ == "__main__":
    main()
