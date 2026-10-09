#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""FT8 第①步实验：8-FSK 合成器 + Costas 粗同步的同步成功率 / 估计误差扫描。

不依赖 wsjtx / 硬件，全部用 mbdsdr_ai.ft8_modem 在合成 15 s 捕获窗上闭环：

  (1) 扫 SNR（-20..+20 dB）× trials：随机频偏（±80 Hz）+ 随机时偏放置 +
      AWGN（固定种子派生）-> Costas 同步成功率、逐符号检出率、频偏/时偏
      估计误差 mean/std；成功率配 Wilson 95% CI（common/runner.py）。
  (2) 纯噪声对照组：统计 15 s 纯噪声窗被误判为同步的虚警率（诚实空态验证）。

固定随机种子，trials 可调；CSV 写入 paper/experiments/。所有数字来自合成
信号，data-origin: synthetic。LDPC 编码/解码不在本轮（第②步）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mbdsdr_ai.ft8_modem import (  # noqa: E402
    FS_HZ, WINDOW_SEC, N_SYMBOLS, NSPS,
    Ft8Modulator, Ft8CostasSync,
)
from experiments.common.runner import ExperimentRunner, wilson_ci  # noqa: E402

OUT_DIR = os.path.join(ROOT, "paper", "experiments")
CAPTURE = int(WINDOW_SEC * FS_HZ)
FRAME_SAMPLES = N_SYMBOLS * NSPS


def _one_trial(mod: Ft8Modulator, sync: Ft8CostasSync, rng: np.random.Generator,
               snr_db: float, itone: np.ndarray) -> dict:
    """单次试验：随机频偏/放置 + AWGN -> 同步结果指标。"""
    fo = float(rng.uniform(-80.0, 80.0))
    at = int(rng.integers(0, CAPTURE - FRAME_SAMPLES - 500))
    noise_seed = int(rng.integers(0, 2 ** 31 - 1))
    iq = mod.modulate(itone, freq_offset_hz=fo, awgn_snr_db=snr_db,
                      noise_seed=noise_seed)
    buf = np.zeros(CAPTURE, dtype=np.complex64)
    buf[at:at + FRAME_SAMPLES] = iq
    r = sync.process(buf)
    return {
        "synced": r.synced,
        "exact": bool(r.synced and np.array_equal(r.symbols, itone)),
        "fo_est": r.freq_offset_hz if r.synced else np.nan,
        "to_est": r.time_offset_samples if r.synced else np.nan,
        "fo_true": fo,
        "at_true": at,
    }


def run_snr_sweep(trials: int, seed: int,
                  snr_grid=(-20, -15, -10, -5, 0, 5, 10, 15, 20)):
    mod = Ft8Modulator()
    sync = Ft8CostasSync()
    runner = ExperimentRunner(seed)
    itone = mod.build_frame_symbols()
    rows = []
    for snr in snr_grid:
        rng = runner.make_rng(f"ft8_step1_snr_{snr}")
        res = [_one_trial(mod, sync, rng, float(snr), itone) for _ in range(trials)]
        n_sync = sum(x["synced"] for x in res)
        n_exact = sum(x["exact"] for x in res)
        fo_err = [abs(x["fo_est"] - x["fo_true"]) for x in res if x["synced"]]
        to_err = [abs(x["to_est"] - x["at_true"]) for x in res if x["synced"]]
        lo, hi = wilson_ci(n_exact, trials)
        rows.append({
            "snr_db": snr,
            "n_trials": trials,
            "sync_rate": round(n_sync / trials, 4),
            "exact_symbol_rate": round(n_exact / trials, 4),
            "wilson_lo": round(lo, 4),
            "wilson_hi": round(hi, 4),
            "fo_err_mean_hz": round(float(np.mean(fo_err)), 3) if fo_err else -1.0,
            "fo_err_std_hz": round(float(np.std(fo_err)), 3) if fo_err else -1.0,
            "to_err_mean_samples": round(float(np.mean(to_err)), 1) if to_err else -1.0,
            "to_err_std_samples": round(float(np.std(to_err)), 1) if to_err else -1.0,
        })
        print(f"[FT8-1] SNR={snr:>4} dB  sync={rows[-1]['sync_rate']:.2f}  "
              f"exact={rows[-1]['exact_symbol_rate']:.2f} "
              f"[{lo:.2f},{hi:.2f}]  fo_err={rows[-1]['fo_err_mean_hz']} Hz",
              flush=True)
    return rows


def run_false_alarm(trials: int, seed: int):
    sync = Ft8CostasSync()
    rng = np.random.default_rng(seed + 99)
    fa = 0
    for _ in range(trials):
        noise = (rng.standard_normal(CAPTURE) + 1j * rng.standard_normal(CAPTURE)) \
            / np.sqrt(2.0)
        r = sync.process(noise.astype(np.complex64))
        if r.synced:
            fa += 1
    lo, hi = wilson_ci(fa, trials)
    row = {
        "n_trials": trials,
        "false_alarm_rate": round(fa / trials, 6),
        "wilson_lo": round(lo, 6),
        "wilson_hi": round(hi, 6),
    }
    print(f"[FT8-1 虚警] 纯噪声窗同步误判 {fa}/{trials} = {row['false_alarm_rate']}",
          flush=True)
    return row


def _write_csv(name, rows):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    if not rows:
        return path
    keys = list(rows[0].keys())
    with open(path, "w", encoding="utf-8") as f:
        f.write(",".join(keys) + "\n")
        for r in rows:
            f.write(",".join(str(r[k]) for k in keys) + "\n")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20261010)
    args = ap.parse_args()

    snr_rows = run_snr_sweep(args.trials, args.seed)
    fa_row = run_false_alarm(max(args.trials, 50), args.seed)

    p1 = _write_csv("ft8_step1_sync_vs_snr.csv", snr_rows)
    p2 = _write_csv("ft8_step1_false_alarm.csv", [fa_row])
    print("\nCSV:")
    print(" ", p1)
    print(" ", p2)

    hi_row = max(snr_rows, key=lambda r: r["exact_symbol_rate"])
    result = {
        "experiment": "ft8_step1_modem_costas",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data_origin": "synthetic",
        "config": {"trials": args.trials, "seed": args.seed,
                   "freq_search_hz": "±80", "window_sec": WINDOW_SEC},
        "metrics": {
            "best_snr_db": hi_row["snr_db"],
            "best_exact_symbol_rate": hi_row["exact_symbol_rate"],
            "snr0_exact_rate": next(r["exact_symbol_rate"] for r in snr_rows if r["snr_db"] == 0),
            "snr10_exact_rate": next(r["exact_symbol_rate"] for r in snr_rows if r["snr_db"] == 10),
            "false_alarm_rate": fa_row["false_alarm_rate"],
        },
        "output_files": [p1, p2],
    }
    print("\n=== JSON RESULT ===")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
