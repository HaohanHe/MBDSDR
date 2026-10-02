#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
实验：解码成功率 vs 采样率 fs（固定 Eb/N0，仿真口径）
=====================================================

工程问题：固定比特率 Rb、固定每比特能量 Eb/N0 下，ADC/复基带采样率 fs
取多少才够？过采样（sps=fs/Rb）对整包解码成功率有多大影响？

被测链（自包含、真实 DSP，与 exp_ebno_decode.trial_bpsk 同一条相干 BPSK 链）：
  随机比特 -> ±1 矩形脉冲成型(sps=fs/Rb) -> 单位功率归一化 ->
  AWGN（噪声铺满整段 fs，按 common/ebno.py 的 B=fs 口径把固定 Eb/N0
  换算成带内 SNR）-> 积分清零(每 sps 样)相干判决 -> 整包 0 误码判成功。

物理（实算、非估算）：固定 Eb/N0 下，带内 SNR_in = Eb/N0 - 10log10(fs/Rb)；
积分清零把 sps 个噪声样平均掉，方差 ÷sps，于是"每比特后相关 SNR"
= sps · 10^(SNR_in/10) = 10^(Eb/N0/10)，与 fs/sps 无关。故本实验预期：
在 Nyquist 以上、理想相干同步下，整包成功率对采样率近似不变——
即 Eb/N0 归一化把"采样率自由度"消掉了，选最低满足 Nyquist 的 fs 即可，
没有 SNR 代价。这是本实验要诚实验证/证伪的命题（数字全部来自试验）。

口径：synthetic（仿真）。信号/噪声全由固定种子派生。每格 trials 次独立试验，
成功率 + Wilson 95% CI。
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from experiments.common import ebno, runner, manifest  # noqa: E402
from experiments.common import plot as eplot  # noqa: E402

ORIGIN = "synthetic"
OUT_DIR = os.path.join(ROOT, "paper", "experiments")
FIG_DIR = os.path.join(OUT_DIR, "figures")

# 固定 BPSK 10 kbps（与 ebno.MODE_TABLE["bpsk_10k"] 同模式；Rb 固定，扫 fs）
RB = 10_000.0
NBITS = 200
# 默认 Eb/N0 取在 BPSK 解码悬崖中段（exp_ebno_decode 实测 6 dB -> 0.60），
# 此点对任何采样率依赖最敏感：若 fs 真有影响，这里最容易看出来。
FIXED_EBN0_DB = 6.0


def trial_bpsk_sweep(rng: np.random.Generator, fs: float, nbits: int,
                     ebn0_db: float) -> bool:
    """一次试验：固定 Rb=10k，按 fs 决定 sps，合成 BPSK -> 固定 Eb/N0 加噪 ->
    积分清零相干判决 -> 整包 0 误码?

    sps = fs/Rb 必须为整数（采样率网格据此取整）。
    """
    sps = int(round(fs / RB))
    assert sps >= 1, f"fs={fs} 低于码率，sps<1"
    bits = rng.integers(0, 2, nbits)
    sym = np.where(bits == 1, 1.0, -1.0).astype(np.complex128)
    x = np.repeat(sym, sps)                       # 矩形脉冲成型
    x = x / (np.sqrt(np.mean(np.abs(x) ** 2)) + 1e-12)
    # 口径 B=fs：把固定 Eb/N0 换算成带内 SNR（与 common/ebno.py 一致）
    snr_in_db = ebno.ebn0_db_to_snr_db(ebn0_db, RB, fs)
    sig_p = float(np.mean(np.abs(x) ** 2))
    noise_p = sig_p * 10.0 ** (-snr_in_db / 10.0)
    w = (np.sqrt(noise_p / 2.0)
         * (rng.standard_normal(x.size) + 1j * rng.standard_normal(x.size)))
    y = x + w.astype(np.complex128)
    # 相干积分清零（理想位同步/载波相位同步，best-case）
    m = (len(y) // sps) * sps
    y = y[:m]
    yy = y.reshape(nbits, sps).mean(axis=1)
    dec = (np.real(yy) > 0).astype(int)
    return bool(np.array_equal(dec, bits))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=200)
    ap.add_argument("--seed", type=int, default=20261001)
    ap.add_argument("--ebn0-db", type=float, default=FIXED_EBN0_DB,
                    help="固定 Eb/N0(dB)；默认 6 dB（BPSK 悬崖中段）")
    ap.add_argument("--fs-grid", type=str,
                    default="10000,20000,50000,100000,200000,500000,1000000",
                    help="采样率 fs 网格(Hz)；须使 sps=fs/Rb 为整数")
    ap.add_argument("--out", default=OUT_DIR)
    args = ap.parse_args()

    fs_grid = [float(f) for f in args.fs_grid.split(",")]
    runner_obj = runner.ExperimentRunner(args.seed)

    rows = []
    curve_x, curve_rate, curve_lo, curve_hi = [], [], [], []
    for fs in fs_grid:
        sps = int(round(fs / RB))
        rng = runner_obj.make_rng(f"rate_sweep_fs={int(fs)}")
        succ = 0
        for _ in range(args.trials):
            if trial_bpsk_sweep(rng, fs, NBITS, args.ebn0_db):
                succ += 1
        cell = runner.BinomialCell(succ, args.trials)
        lo, hi = cell.wilson
        snr_in = ebno.ebn0_db_to_snr_db(args.ebn0_db, RB, fs)
        row = {
            "mode": "bpsk_10k",
            "rb_bps": RB,
            "ebn0_db_fixed": args.ebn0_db,
            "snr_inband_db_follows_fs": round(snr_in, 3),
            "fs_hz": round(fs, 1),
            "sps": sps,
            "fs_over_rb": round(fs / RB, 3),
            "nbits_pkt": NBITS,
            "successes": succ, "n_trials": args.trials,
            "success_rate": round(cell.rate, 4),
            "wilson_lo": round(lo, 4), "wilson_hi": round(hi, 4),
            "data_origin": ORIGIN,
        }
        rows.append(row)
        curve_x.append(fs / 1000.0)
        curve_rate.append(cell.rate)
        curve_lo.append(lo)
        curve_hi.append(hi)
        print(f"[BPSK 10k @ Eb/N0={args.ebn0_db:4.1f} dB] fs={fs/1000:7.1f} kHz "
              f"(sps={sps:3d}, SNR_in={snr_in:6.2f} dB)  "
              f"success={succ}/{args.trials} ({cell.rate*100:5.1f}%)", flush=True)

    # --- CSV ---
    os.makedirs(args.out, exist_ok=True)
    csv_path = os.path.join(args.out, "rate_sweep_success.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\n[写] {csv_path}")

    # --- 图：成功率 vs fs，对数 x 轴更合适，但 plot helper 用线性；这里传 kHz ---
    n_samples = len(fs_grid) * args.trials
    fig_path = eplot.plot_success_vs_ebn0(
        {"BPSK 10k": {"x": curve_x, "rate": curve_rate,
                      "lo": curve_lo, "hi": curve_hi}},
        origin=ORIGIN, n_samples=n_samples, out_dir=FIG_DIR,
        fname_prefix="rate_sweep_success",
        xlabel="Sample rate fs (kHz)",
        ylabel="Packet decode success rate",
        title=(f"Decode success vs sample rate "
               f"(fixed Eb/N0={args.ebn0_db:.1f} dB, BPSK {int(RB//1000)}k)"))
    print(f"[写] {fig_path}")

    # --- manifest ---
    mpath = manifest.write_manifest(
        out_dir=args.out, script=__file__, seed=args.seed, data_origin=ORIGIN,
        params={"mode": "bpsk_10k", "rb": RB, "nbits_pkt": NBITS,
                "fixed_ebn0_db": args.ebn0_db,
                "fs_grid_hz": fs_grid,
                "snr_convention": "SNR_inband = Eb/N0 - 10log10(fs/Rb) (B=fs, common/ebno.py)",
                "hypothesis": "固定 Eb/N0 下整包成功率对 fs/sps 近似不变（能量归一化消掉采样率自由度）",
                "csv": os.path.basename(csv_path),
                "figure": os.path.basename(fig_path)},
        n_samples=n_samples,
        filename="manifest_rate_sweep.json")
    print(f"[写] {mpath}")
    print(f"\n口径: {ORIGIN}（仿真）；总样本 N={n_samples}；固定 Eb/N0={args.ebn0_db} dB，只扫 fs")


if __name__ == "__main__":
    main()
