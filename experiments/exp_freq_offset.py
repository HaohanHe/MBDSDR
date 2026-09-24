#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实验：载波频偏（CFO）估计与校正性能（纯软件、可复现）。

复用产品内核 mbdsdr_ai/cfo.py（与 sdr_cfo_correct 工具同源），三组实验：
  1) cfo_accuracy_vs_snr.csv   估计/残余频偏 RMS vs SNR，含 Kay 精估启用率与相干性；
  2) cfo_vs_blocklength.csv    精度 vs FFT 块长 N（FFT bin ∝1/N，Kay 精估 ∝N^-3/2）；
  3) cfo_ppm_scenario.csv      典型晶体 ppm 误差（10/30/50 ppm @100MHz）的校正残余。

信号模型：未调制载波（CW/信标/FM 载波的基带等效）
    x[n] = exp(j 2π f_cfo n/fs) + CN(0, σ²)，f_cfo 为接收机频偏折到基带的频率。
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mbdsdr_ai.cfo import estimate_and_correct, estimate_cfo_fft  # noqa: E402


def make_tone(rng, f_cfo, fs, n, snr_db):
    ps = 10 ** (snr_db / 10.0)
    w = np.sqrt(0.5 / ps) * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    tt = np.arange(n)
    return np.exp(1j * 2 * np.pi * f_cfo * tt / fs) + w


def exp_snr(rng, fs, n, trials, out):
    """组1：精度 vs SNR（两级 vs 纯 FFT 粗估）。"""
    f_cfo = 3000.0  # 100 MHz @ 30 ppm
    rows = []
    for snr in range(-10, 32, 2):
        e_two, e_coarse, e_res, used, coh = [], [], [], 0, []
        for _ in range(trials):
            x = make_tone(rng, f_cfo, fs, n, snr)
            r = estimate_and_correct(x, fs)
            coarse, _ = estimate_cfo_fft(x, fs)
            e_two.append(abs(r.offset_hz - f_cfo))
            e_coarse.append(abs(coarse - f_cfo))
            e_res.append(abs(r.residual_hz))
            used += 1 if r.fine_applied else 0
            coh.append(r.coherence)
        rows.append({
            "snr_db": snr, "true_cfo_hz": f_cfo,
            "rms_coarse_only_hz": round(float(np.sqrt(np.mean(np.square(e_coarse)))), 4),
            "rms_twostage_hz": round(float(np.sqrt(np.mean(np.square(e_two)))), 4),
            "rms_residual_hz": round(float(np.sqrt(np.mean(np.square(e_res)))), 4),
            "kay_apply_rate": round(used / trials, 4),
            "mean_coherence": round(float(np.mean(coh)), 4),
            "bin_hz": round(fs / n, 3),
        })
    path = os.path.join(out, "cfo_accuracy_vs_snr.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wr.writeheader(); wr.writerows(rows)
    print(f"[1] 精度-SNR：{len(rows)} 行 -> {path}")
    return rows


def exp_blocklength(rng, fs, trials, out):
    """组2：块长 N 对精度的影响（低/高 SNR 两点）。"""
    f_cfo = 3000.0
    rows = []
    for snr in (0, 15):
        for n in (512, 1024, 2048, 4096, 8192, 16384):
            e_two, e_coarse, used = [], [], 0
            for _ in range(trials):
                x = make_tone(rng, f_cfo, fs, n, snr)
                r = estimate_and_correct(x, fs)
                coarse, _ = estimate_cfo_fft(x, fs)
                e_two.append(abs(r.offset_hz - f_cfo))
                e_coarse.append(abs(coarse - f_cfo))
                used += 1 if r.fine_applied else 0
            rows.append({
                "snr_db": snr, "n": n, "bin_hz": round(fs / n, 3),
                "rms_coarse_only_hz": round(float(np.sqrt(np.mean(np.square(e_coarse)))), 4),
                "rms_twostage_hz": round(float(np.sqrt(np.mean(np.square(e_two)))), 4),
                "kay_apply_rate": round(used / trials, 4),
            })
    path = os.path.join(out, "cfo_vs_blocklength.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wr.writeheader(); wr.writerows(rows)
    print(f"[2] 精度-块长：{len(rows)} 行 -> {path}")


def exp_ppm(rng, fs, n, trials, out):
    """组3：典型 ppm 晶体误差在 15 dB 下的校正残余。"""
    rows = []
    for ppm in (5, 10, 20, 30, 50):
        f_cfo = ppm * 1e-6 * 100e6  # 折合到基带的频偏 Hz
        e_off, e_res = [], []
        for _ in range(trials):
            x = make_tone(rng, f_cfo, fs, n, 15)
            r = estimate_and_correct(x, fs)
            e_off.append(abs(r.offset_hz - f_cfo))
            e_res.append(abs(r.residual_hz))
        rows.append({
            "ppm_at_100mhz": ppm, "true_cfo_hz": round(f_cfo, 2),
            "rms_estimate_error_hz": round(float(np.sqrt(np.mean(np.square(e_off)))), 4),
            "rms_residual_hz": round(float(np.sqrt(np.mean(np.square(e_res)))), 4),
        })
    path = os.path.join(out, "cfo_ppm_scenario.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wr.writeheader(); wr.writerows(rows)
    print(f"[3] ppm 场景：{len(rows)} 行 -> {path}")
    for r in rows:
        print(f"    {r['ppm_at_100mhz']:>2} ppm ({r['true_cfo_hz']:>6.0f} Hz) "
              f"残余 RMS={r['rms_residual_hz']} Hz")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=500)
    ap.add_argument("--seed", type=int, default=20260919)
    ap.add_argument("--fs", type=float, default=240e3)
    ap.add_argument("--n", type=int, default=4096)
    ap.add_argument("--out", default=os.path.join("paper", "experiments"))
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    rng = np.random.default_rng(args.seed)
    snr_rows = exp_snr(rng, args.fs, args.n, args.trials, args.out)
    exp_blocklength(rng, args.fs, args.trials, args.out)
    ppm_rows = exp_ppm(rng, args.fs, args.n, args.trials, args.out)
    print("完成，输出目录：", args.out)

    # --- 论文级 JSON 结论输出 ---
    best_snr_row = min(snr_rows, key=lambda r: r["rms_twostage_hz"])
    result = {
        "experiment": "cfo_estimation_performance",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config": {
            "trials": args.trials,
            "seed": args.seed,
            "fs": args.fs,
            "n": args.n,
        },
        "metrics": {
            "best_rms_two_stage_hz": best_snr_row["rms_twostage_hz"],
            "best_rms_snr_db": best_snr_row["snr_db"],
            "worst_rms_two_stage_hz": max(r["rms_twostage_hz"] for r in snr_rows),
            "ppm_residual_rms_hz_50ppm": ppm_rows[-1]["rms_residual_hz"],
            "ppm_estimate_rms_hz_50ppm": ppm_rows[-1]["rms_estimate_error_hz"],
        },
        "samples": {
            "snr_sweep_total": len(snr_rows) * args.trials,
            "blocklength_total": 2 * 6 * args.trials,
            "ppm_total": len(ppm_rows) * args.trials,
        },
        "output_files": [
            os.path.join(args.out, "cfo_accuracy_vs_snr.csv"),
            os.path.join(args.out, "cfo_vs_blocklength.csv"),
            os.path.join(args.out, "cfo_ppm_scenario.csv"),
        ],
    }
    print("\n=== JSON RESULT ===")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
