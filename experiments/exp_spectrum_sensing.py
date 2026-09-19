# -*- coding: utf-8 -*-
"""
实验：认知无线电频谱感知——能量检测器性能（纯软件、可复现，无需硬件）。

对应论文 Section VI.B（Spectrum Sensing / Energy Detection）。三组蒙特卡洛实验，
检测逻辑全部复用产品模块 mbdsdr_ai.spectrum_sensing（EnergyDetector / threshold_for_pfa
/ theory_pd / snr_wall_db），保证论文曲线与产品实现一致。

  实验1 ROC：固定 N 与 SNR，扫目标虚警率，实测 (Pfa, Pd)，与高斯近似理论曲线对比。
  实验2 Pd vs SNR：固定 Pfa，比较不同感知样本数 N（感知时长/时间带宽积）的检测增益。
  实验3 噪声不确定度与 SNR wall：噪声功率存在 dB 不确定度时，存在无论感知多久都无法
        可靠检测的 SNR 下限（Tandra & Sahai 的 energy-detection SNR wall）。

用法：
  python experiments/exp_spectrum_sensing.py --trials 4000
输出：
  paper/experiments/sensing_roc.csv
  paper/experiments/sensing_pd_vs_snr.csv
  paper/experiments/sensing_noise_uncertainty.csv
"""
import argparse
import csv
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mbdsdr_ai.spectrum_sensing import (  # noqa: E402
    EnergyDetector, threshold_for_pfa, theory_pd, snr_wall_db,
)

SIG2 = 1.0  # 标称噪声功率 σ²，结果对其归一化、与具体值无关


def gen_statistics(rng, n, snr_db, trials, f0_norm=0.05):
    """生成 H0（纯噪声）与 H1（单位功率复正弦+噪声）的能量统计量，各 trials 个。"""
    w = np.sqrt(SIG2 / 2.0) * (rng.standard_normal((trials, n))
                               + 1j * rng.standard_normal((trials, n)))
    t0 = np.mean(np.abs(w) ** 2, axis=1)
    ps = SIG2 * 10.0 ** (snr_db / 10.0)
    tt = np.arange(n)
    s = np.sqrt(ps) * np.exp(1j * 2.0 * np.pi * f0_norm * tt)[None, :]
    t1 = np.mean(np.abs(s + w) ** 2, axis=1)
    return t0, t1


def experiment_roc(rng, trials, out):
    n = 512
    snr_list = [-12.0, -8.0, -4.0]
    pfa_targets = np.concatenate([
        np.linspace(0.002, 0.02, 6),
        np.linspace(0.03, 0.30, 10),
    ])
    rows = []
    for snr in snr_list:
        t0, t1 = gen_statistics(rng, n, snr, trials)
        for pfa in pfa_targets:
            gamma = threshold_for_pfa(n, SIG2, float(pfa))
            pfa_meas = float(np.mean(t0 > gamma))
            pd_meas = float(np.mean(t1 > gamma))
            rows.append({
                "n": n, "snr_db": snr, "pfa_target": round(float(pfa), 5),
                "threshold": f"{gamma:.6f}",
                "pfa_meas": round(pfa_meas, 5),
                "pd_theory": round(theory_pd(snr, n, float(pfa)), 5),
                "pd_meas": round(pd_meas, 5),
            })
    _write(out, "sensing_roc.csv", rows)
    print(f"[1] ROC：{len(rows)} 行（N={n}, SNR={snr_list}）")


def experiment_pd_snr(rng, trials, out):
    pfa = 0.01
    n_list = [64, 256, 1024]
    snr_list = np.arange(-20.0, 2.1, 2.0)
    rows = []
    for n in n_list:
        for snr in snr_list:
            t0, t1 = gen_statistics(rng, int(n), float(snr), trials)
            gamma = threshold_for_pfa(int(n), SIG2, pfa)
            pfa_meas = float(np.mean(t0 > gamma))
            pd_meas = float(np.mean(t1 > gamma))
            rows.append({
                "n": n, "pfa_target": pfa, "snr_db": f"{snr:.0f}",
                "pd_theory": round(theory_pd(float(snr), int(n), pfa), 5),
                "pd_meas": round(pd_meas, 5),
                "pfa_meas": round(pfa_meas, 5),
            })
    _write(out, "sensing_pd_vs_snr.csv", rows)
    print(f"[2] Pd vs SNR：{len(rows)} 行（Pfa={pfa}, N={n_list}）")


def experiment_noise_uncertainty(rng, trials, out):
    """大 N 下扫噪声不确定度，展示 SNR wall：低于 wall 时 Pd 贴近 Pfa、无法靠加长感知突破。"""
    pfa = 0.01
    n = 4096
    unc_list = [0.0, 0.5, 1.0, 2.0]
    snr_list = np.arange(-14.0, 4.1, 1.0)
    rows = []
    for unc in unc_list:
        wall = snr_wall_db(unc)
        ed = EnergyDetector(noise_power=SIG2, noise_uncertainty_db=unc)
        for snr in snr_list:
            t0, t1 = gen_statistics(rng, n, float(snr), trials)
            gamma = threshold_for_pfa(n, SIG2, pfa, unc)
            pfa_meas = float(np.mean(t0 > gamma))
            pd_meas = float(np.mean(t1 > gamma))
            rows.append({
                "n": n, "pfa_target": pfa, "noise_uncertainty_db": f"{unc:g}",
                "snr_wall_db": ("-inf" if np.isinf(wall) else f"{wall:.2f}"),
                "snr_db": f"{snr:.0f}",
                "pd_theory": round(theory_pd(float(snr), n, pfa, unc), 5),
                "pd_meas": round(pd_meas, 5),
                "pfa_meas": round(pfa_meas, 5),
            })
    _write(out, "sensing_noise_uncertainty.csv", rows)
    print(f"[3] 噪声不确定度：{len(rows)} 行（N={n}）")
    for unc in unc_list:
        w = snr_wall_db(unc)
        print(f"    不确定度 {unc:g} dB -> 理论 SNR wall "
              f"{'不存在(0)' if np.isinf(w) else f'{w:.2f} dB'}")
    # 用 EnergyDetector API 走一次完整判决，确认产品路径与实验同源
    r = ed.detect((np.sqrt(SIG2 / 2) * (rng.standard_normal(2048)
               + 1j * rng.standard_normal(2048))).tolist(), pfa=pfa)
    print(f"    产品 API 冒烟：H0 判决={r.decision}（应为 False），T={r.statistic:.3f}，γ={r.threshold:.3f}")


def _write(out, name, rows):
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, name)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=20260919)
    ap.add_argument("--out", default=os.path.join("paper", "experiments"))
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    print(f"=== 频谱感知能量检测实验（trials={a.trials}, seed={a.seed}）===")
    experiment_roc(rng, a.trials, a.out)
    experiment_pd_snr(rng, a.trials, a.out)
    experiment_noise_uncertainty(rng, a.trials, a.out)
    print("完成，输出目录：", a.out)


if __name__ == "__main__":
    main()
