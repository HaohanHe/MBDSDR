#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实验四：自动调制识别（AMR）的信噪比鲁棒性与混淆结构（纯软件、可复现）。

被测对象是 mbdsdr_ai/amr.py 中开箱即用的 AMRClassifier（25 维真实特征 + KNN，
内置模板由合成典型信号提取真实特征得到）。实验不做任何针对测试集的再训练：
  1) 在 -5~30 dB 的 SNR 网格上，对 8 类调制各生成若干独立实现，统计总体与
     逐类识别准确率，得到准确率-SNR 曲线；
  2) 在固定中等 SNR（默认 10 dB）下统计 8x8 混淆矩阵。

输出（CSV，写入 paper/experiments/）：
  amr_accuracy_vs_snr.csv      每个 SNR 的总体与逐类准确率
  amr_confusion_matrix_snrXX.csv  归一化混淆矩阵（行=真实，列=预测）

用法：python experiments/exp_amr.py [--trials 30] [--fs 100000]
"""
import argparse
import csv
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mbdsdr_ai.amr import AMRClassifier, synthesize_modulation_iq  # noqa: E402

MODS = ["AM", "FM", "CW", "FSK", "PSK", "QAM", "OFDM", "NOISE"]
SNRS = [-5, 0, 5, 10, 15, 20, 25, 30]
CONF_SNR = 10
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "paper", "experiments")


def run(trials: int, fs: float, seed_base: int = 40000):
    clf = AMRClassifier(k=5)  # 开箱即用，不再训练
    n = len(MODS)

    # 结果容器
    acc_by_snr = {}
    confusion = np.zeros((n, n), dtype=int)

    for si, snr in enumerate(SNRS):
        correct = np.zeros(n, dtype=int)
        for mi, mod in enumerate(MODS):
            for tr in range(trials):
                rng = np.random.default_rng(seed_base + si * 100000 + mi * 1000 + tr)
                iq = synthesize_modulation_iq(mod, rng, snr_db=float(snr), fs=fs)
                pred = clf.classify_iq(list(iq), fs).predicted_modulation.value
                if pred == mod:
                    correct[mi] += 1
                if snr == CONF_SNR:
                    pj = MODS.index(pred) if pred in MODS else None
                    if pj is not None:
                        confusion[mi, pj] += 1
        acc_by_snr[snr] = correct / trials
        overall = correct.sum() / (n * trials)
        print(f"SNR={snr:>3}dB  总体准确率={overall:.3f}  "
              + "  ".join(f"{m}={acc_by_snr[snr][i]:.2f}" for i, m in enumerate(MODS)))

    os.makedirs(OUT_DIR, exist_ok=True)

    # CSV1：准确率 vs SNR
    p1 = os.path.join(OUT_DIR, "amr_accuracy_vs_snr.csv")
    with open(p1, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["snr_db", "overall_accuracy"] + [f"acc_{m}" for m in MODS])
        for snr in SNRS:
            a = acc_by_snr[snr]
            w.writerow([snr, f"{a.mean():.4f}"] + [f"{v:.4f}" for v in a])

    # CSV2：固定 SNR 归一化混淆矩阵
    p2 = os.path.join(OUT_DIR, f"amr_confusion_matrix_snr{CONF_SNR}.csv")
    with open(p2, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["true\\pred"] + MODS)
        for mi, mod in enumerate(MODS):
            row_sum = confusion[mi].sum()
            norm = confusion[mi] / row_sum if row_sum else confusion[mi]
            w.writerow([mod] + [f"{v:.4f}" for v in norm])

    # 汇总指标
    high = acc_by_snr[30].mean()
    mid = acc_by_snr[CONF_SNR].mean()
    low = acc_by_snr[SNRS[0]].mean()
    print("\n========== AMR 实验汇总 ==========")
    print(f"高 SNR(30dB) 准确率: {high:.3f}")
    print(f"中 SNR({CONF_SNR}dB) 准确率: {mid:.3f}")
    print(f"低 SNR({SNRS[0]}dB) 准确率: {low:.3f}")
    print(f"输出: {p1}")
    print(f"输出: {p2}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=30)
    ap.add_argument("--fs", type=float, default=100_000.0)
    args = ap.parse_args()
    run(args.trials, args.fs)
