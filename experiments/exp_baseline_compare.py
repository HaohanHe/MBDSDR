#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
实验：基线对比 —— 经典特征规则 AMR vs AI/KNN AMR（仿真口径）
=============================================================

同一确定性数据集（同一 seed 生成的已知调制样本）、同一指标（调制识别准确率），
两条路径同场对比：
  - classic : 手写规则的经典 DSP 调制识别（瞬时频率标准差 / 幅度标准差阈值判决）。
  - knn_amr : mbdsdr_ai.amr.AMRClassifier（25 维特征 + KNN，纯 Python）。

LLM/Agent 路径（exp_weak_model_toolcall）需在线 API、云内不可确定性复现 ——
本实验只在 CSV/表里留一行占位 "待在线运行"，**不伪造其数值**。

口径：synthetic（仿真）。样本由 amr.synthesize_modulation_iq 固定种子生成。
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

from mbdsdr_ai import amr  # noqa: E402
from experiments.common import ebno, runner, manifest  # noqa: E402
from experiments.common import plot as eplot  # noqa: E402

ORIGIN = "synthetic"
OUT_DIR = os.path.join(ROOT, "paper", "experiments")
FIG_DIR = os.path.join(OUT_DIR, "figures")

# 同一份数据集里的调制类别（与 AMRClassifier 内置模板对齐）
# 选 {FSK, PSK, NOISE}：三者在"瞬时频率标准差"上可分（实测 FSK~2.5k、PSK~7.8k、
# NOISE~29k Hz），经典规则可给出非平凡基线；QAM 与 PSK 特征重叠，留给 KNN 优势区。
CLASSES = ("FSK", "PSK", "NOISE")
FS = 100_000.0


# ---------------------------------------------------------------------------
# 经典规则分类器（手写、真实 DSP 特征，非 mock）
# ---------------------------------------------------------------------------
def classic_classify(iq: np.ndarray) -> str:
    """经典特征规则 AMR：按瞬时频率标准差 std_f 判决三类。

    实测（SNR=20dB）：FSK std_f~2.5kHz、PSK~7.8kHz、NOISE~29kHz，阈值取中点。
    这是一个刻意简单的经典基线 —— 它在低 SNR 与更细分类（QAM/AM）上会明显输给 KNN。
    """
    x = np.asarray(iq, dtype=np.complex128)
    n = len(x)
    if n < 64:
        return "NOISE"
    ph = np.angle(x)
    dph = np.diff(np.unwrap(ph))
    std_f = float(np.std(dph * FS / (2 * np.pi)))
    if std_f < 4000.0:
        return "FSK"
    if std_f < 15000.0:
        return "PSK"
    return "NOISE"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials-per-class", type=int, default=40)
    ap.add_argument("--seed", type=int, default=20261001)
    ap.add_argument("--snr-grid", type=str, default="0,5,10,15,20")
    ap.add_argument("--out", default=OUT_DIR)
    args = ap.parse_args()

    runner_obj = runner.ExperimentRunner(args.seed)
    knn = amr.AMRClassifier(k=5)
    snrs = [float(s) for s in args.snr_grid.split(",")]

    rows = []
    bar_groups, bar_classic, bar_knn = [], [], []
    for snr in snrs:
        classic_ok = knn_ok = 0
        total = 0
        for cls in CLASSES:
            for k in range(args.trials_per_class):
                rng = runner_obj.make_rng(f"base|{cls}|{snr}|{k}")
                iq = amr.synthesize_modulation_iq(cls, rng, snr_db=snr, fs=FS)
                # classic
                if classic_classify(iq) == cls:
                    classic_ok += 1
                # knn
                res = knn.classify_iq(list(iq), FS)
                if res.predicted_modulation.value == cls:
                    knn_ok += 1
                total += 1
        c_cell = runner.BinomialCell(classic_ok, total)
        k_cell = runner.BinomialCell(knn_ok, total)
        rows.append({
            "snr_inband_db": snr,
            "ebn0_ref_note": "FSK/PSK/QAM 共用 fs=100k，Δ=10log10(100k/Rb)",
            "classic_correct": classic_ok,
            "knn_correct": knn_ok,
            "n_trials": total,
            "classic_acc": round(c_cell.rate, 4),
            "classic_wilson_lo": round(c_cell.wilson[0], 4),
            "classic_wilson_hi": round(c_cell.wilson[1], 4),
            "knn_acc": round(k_cell.rate, 4),
            "knn_wilson_lo": round(k_cell.wilson[0], 4),
            "knn_wilson_hi": round(k_cell.wilson[1], 4),
            "llm_agent": "PENDING_ONLINE_RUN",
            "data_origin": ORIGIN,
        })
        bar_groups.append(f"{snr:g} dB")
        bar_classic.append(c_cell.rate)
        bar_knn.append(k_cell.rate)
        print(f"[SNR={snr:5.1f} dB] classic={c_cell.rate*100:5.1f}%  "
              f"knn={k_cell.rate*100:5.1f}%  (LLM/Agent: 待在线运行)", flush=True)

    # LLM 占位行（明确不伪造）
    rows.append({
        "snr_inband_db": "ALL", "ebn0_ref_note": "n/a",
        "classic_correct": "", "knn_correct": "", "n_trials": "",
        "classic_acc": "", "classic_wilson_lo": "", "classic_wilson_hi": "",
        "knn_acc": "", "knn_wilson_lo": "", "knn_wilson_hi": "",
        "llm_agent": ("PENDING_ONLINE_RUN: 需在线 LLM API，云 VM 无 key、不可确定性复现；"
                      "真机/有 key 环境运行 exp_weak_model_toolcall.py 后回填"),
        "data_origin": "pending_online",
    })

    os.makedirs(args.out, exist_ok=True)
    csv_path = os.path.join(args.out, "baseline_compare.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print(f"\n[写] {csv_path}")

    n_samples = len(snrs) * args.trials_per_class * len(CLASSES)
    fig_path = eplot.plot_grouped_bars(
        groups=bar_groups, methods=["classic_rules", "knn_amr"],
        values={"classic_rules": bar_classic, "knn_amr": bar_knn},
        errors={"classic_rules": [runner.BinomialCell(r["classic_correct"], r["n_trials"]).half_width_wilson for r in rows[:-1]],
                "knn_amr": [runner.BinomialCell(r["knn_correct"], r["n_trials"]).half_width_wilson for r in rows[:-1]]},
        origin=ORIGIN, n_samples=n_samples, out_dir=FIG_DIR,
        fname_prefix="baseline_compare_amr", ylabel="Recognition accuracy",
        title="Classic rules vs AI/KNN modulation recognition")
    print(f"[写] {fig_path}")

    mpath = manifest.write_manifest(
        out_dir=args.out, script=__file__, seed=args.seed, data_origin=ORIGIN,
        params={"trials_per_class": args.trials_per_class,
                "classes": list(CLASSES), "snr_grid": snrs,
                "llm_agent": "PENDING_ONLINE_RUN",
                "csv": os.path.basename(csv_path),
                "figure": os.path.basename(fig_path)},
        n_samples=n_samples,
        filename="manifest_baseline.json")
    print(f"[写] {mpath}")
    print(f"\n口径: {ORIGIN}（仿真）；总样本 N={n_samples}；LLM/Agent 列=待在线运行（未伪造）")


if __name__ == "__main__":
    main()
