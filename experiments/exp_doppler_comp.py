#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
实验：多普勒补偿收益（确定性仿真口径）
=====================================

工程问题：LEO 过境期间信号携带多普勒频移 fd(t)。若相干解调器不补偿残余频偏，
一个数据包内载波相位持续旋转 -> 积分清零输出逐比特相位漂移 -> 整包误码。
本实验量化"已知轨道多普勒 -> 数字下变频补偿 -> 解码成功率恢复"的收益。

被测链（与 exp_ebno_decode / exp_rate_sweep 同一条相干 BPSK 链，真实 DSP）：
  随机比特 -> ±1 矩形脉冲成型(fs=100k, sps=10, Rb=10k) -> 单位功率归一化 ->
  乘复指数 exp(j·2π·fd·t) 叠加恒定多普勒 fd（一个 20ms 包内多普勒近似恒定）->
  AWGN（固定 Eb/N0=8 dB）->
    * 不补偿臂：直接积分清零（假设 fd=0）；
    * 补偿臂：先乘 exp(-j·2π·fd·t) 数字频偏校正，再积分清零；
  整包 0 误码判成功。

物理（实算）：包长 T=nbits/Rb=20ms。残余 fd 在包内累积相位 2π·fd·T。
fd=25 Hz -> 整包 180° 相位漂移 -> 过半比特 real 分量过零点翻转 -> 失败；
fd 越大失败越彻底。补偿臂用"已知 fd"做相干校正，把相位旋转消掉，成功率回到
无频偏基线。这是"轨道预测多普勒补偿"在理想已知频移下的上界收益。

口径：synthetic（仿真）。fd 由轨道/实验给定（确定性），非估计误差；信号/噪声
全固定种子。每格 trials 次独立试验，成功率 + Wilson 95% CI。
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

# 固定 BPSK 10k，fs=100k，sps=10（与 ebno_decode 自包含链一致）
FS = 100_000.0
SPs = 10
RB = 10_000.0
NBITS = 200
# 取在接近干净但未饱和的拐点上方（ebno_decode 实测 8 dB -> 0.92），
# 这样频偏造成的下跌最容易看清，基线又不至于顶在 1.0 没分辨。
FIXED_EBN0_DB = 8.0


def trial(rng: np.random.Generator, fd_hz: float, compensate: bool) -> bool:
    """一次试验：合成 BPSK -> 叠加多普勒 fd -> 固定 Eb/N0 加噪 -> [补偿?] -> 积分清零 -> 整包无误码?"""
    bits = rng.integers(0, 2, NBITS)
    sym = np.where(bits == 1, 1.0, -1.0).astype(np.complex128)
    x = np.repeat(sym, SPs)
    x = x / (np.sqrt(np.mean(np.abs(x) ** 2)) + 1e-12)
    # 叠加恒定多普勒频移 fd（一个包内近似恒定）
    n = x.size
    t = np.arange(n) / FS
    if fd_hz != 0.0:
        x = x * np.exp(1j * 2.0 * np.pi * fd_hz * t)
    # 固定 Eb/N0 加噪（B=fs 口径）
    snr_in_db = ebno.ebn0_db_to_snr_db(FIXED_EBN0_DB, RB, FS)
    sig_p = float(np.mean(np.abs(x) ** 2))
    noise_p = sig_p * 10.0 ** (-snr_in_db / 10.0)
    w = (np.sqrt(noise_p / 2.0)
         * (rng.standard_normal(n) + 1j * rng.standard_normal(n)))
    y = x + w.astype(np.complex128)
    # 数字多普勒补偿：用已知 fd 做下变频校正
    if compensate and fd_hz != 0.0:
        y = y * np.exp(-1j * 2.0 * np.pi * fd_hz * t)
    # 相干积分清零
    yy = y.reshape(NBITS, SPs).mean(axis=1)
    dec = (np.real(yy) > 0).astype(int)
    return bool(np.array_equal(dec, bits))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=200)
    ap.add_argument("--seed", type=int, default=20261001)
    ap.add_argument("--fd-grid", type=str,
                    default="0,5,10,15,25,50,100,200,400",
                    help="残余多普勒频偏网格(Hz)")
    ap.add_argument("--ebn0-db", type=float, default=FIXED_EBN0_DB)
    ap.add_argument("--out", default=OUT_DIR)
    args = ap.parse_args()

    fd_grid = [float(f) for f in args.fd_grid.split(",")]
    runner_obj = runner.ExperimentRunner(args.seed)

    rows = []
    comp_curve = {"x": [], "rate": [], "lo": [], "hi": []}
    uncomp_curve = {"x": [], "rate": [], "lo": [], "hi": []}
    for fd in fd_grid:
        for compensate, arm in ((False, "uncompensated"), (True, "compensated")):
            rng = runner_obj.make_rng(f"dopplercomp_fd={int(fd)}_{arm}")
            succ = 0
            for _ in range(args.trials):
                if trial(rng, fd, compensate):
                    succ += 1
            cell = runner.BinomialCell(succ, args.trials)
            lo, hi = cell.wilson
            rows.append({
                "mode": "bpsk_10k",
                "rb_bps": RB, "fs_hz": FS, "sps": SPs, "nbits_pkt": NBITS,
                "ebn0_db_fixed": args.ebn0_db,
                "pkt_duration_ms": round(NBITS / RB * 1000.0, 2),
                "fd_hz": fd,
                "phase_drift_over_pkt_cycles": round(fd * NBITS / RB, 4),
                "arm": arm,
                "compensated": compensate,
                "successes": succ, "n_trials": args.trials,
                "success_rate": round(cell.rate, 4),
                "wilson_lo": round(lo, 4), "wilson_hi": round(hi, 4),
                "data_origin": ORIGIN,
            })
            tgt = comp_curve if compensate else uncomp_curve
            tgt["x"].append(fd); tgt["rate"].append(cell.rate)
            tgt["lo"].append(lo); tgt["hi"].append(hi)
            print(f"[fd={fd:6.1f} Hz | {arm:>11}]  "
                  f"success={succ}/{args.trials} ({cell.rate*100:5.1f}%)", flush=True)

    # --- CSV ---
    os.makedirs(args.out, exist_ok=True)
    csv_path = os.path.join(args.out, "doppler_comp_success.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\n[写] {csv_path}")

    # --- 图：两条曲线（补偿 vs 不补偿）vs 残余多普勒 fd ---
    n_samples = len(fd_grid) * 2 * args.trials
    fig_path = eplot.plot_success_vs_ebn0(
        {"compensated (known-D correction)": comp_curve,
         "uncompensated (no correction)": uncomp_curve},
        origin=ORIGIN, n_samples=n_samples, out_dir=FIG_DIR,
        fname_prefix="doppler_comp_success",
        xlabel="Residual Doppler shift (Hz)",
        ylabel="Packet decode success rate",
        title=(f"Doppler compensation benefit "
               f"(fixed Eb/N0={args.ebn0_db:.1f} dB, BPSK 10k, pkt=20ms)"))
    print(f"[写] {fig_path}")

    # --- manifest ---
    mpath = manifest.write_manifest(
        out_dir=args.out, script=__file__, seed=args.seed, data_origin=ORIGIN,
        params={"mode": "bpsk_10k", "rb": RB, "fs": FS, "sps": SPs,
                "nbits_pkt": NBITS, "fixed_ebn0_db": args.ebn0_db,
                "fd_grid_hz": fd_grid,
                "compensation": "数字下变频乘 exp(-j2πfd t)，fd 为轨道预测已知值（理想上界）",
                "csv": os.path.basename(csv_path),
                "figure": os.path.basename(fig_path)},
        n_samples=n_samples,
        filename="manifest_doppler_comp.json")
    print(f"[写] {mpath}")
    print(f"\n口径: {ORIGIN}（仿真）；总样本 N={n_samples}；固定 Eb/N0={args.ebn0_db} dB")


if __name__ == "__main__":
    main()
