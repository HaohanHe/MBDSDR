#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实验：跳频扩频（FHSS）信号检测与跳频图案估计（纯软件、可复现）。

复用产品内核 mbdsdr_ai/decoders.py 的 detect_fhss（与 sdr_detect_fhss 工具同源）。
detect_fhss 做短时傅里叶逐帧取峰、绝对功率门限判决、按采样率比例容差判跳变与
频率聚类，输出检出标志、唯一频率数、跳频速率、驻留时间与跳频序列。

三组实验：
  1) fhss_pd_vs_snr.csv            跳频信号检出率 / 信道数正确率 vs SNR；
  2) fhss_pfa_vs_threshold.csv     两类负样本（纯噪声、定频连续波 CW）的虚警率 vs 门限；
  3) fhss_param_vs_snr.csv         跳频速率、驻留时间、信道数估计精度 vs SNR。

功率约定（与门限标定一致）：复高斯噪声单位功率（每实虚维 0.5），单音信号幅度
A=10^(SNR/20)，信号功率 A^2、噪声功率 1，故 SNR=10log10(A^2)。fs=240 kHz、
frame=10 ms、FFT=1024 时，纯噪声逐帧 argmax 峰功率 99 分位约 35 dB，主门限取 38 dB。

注：本实验为跳变沿与分析帧对齐的基线情形（best-case）；跳变定时偏移、多网台、
碰撞与跟踪丢失等复杂工况留待后续。
"""
import argparse
import csv
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mbdsdr_ai.decoders import detect_fhss  # noqa: E402


def _noise(rng, n):
    """单位功率复高斯噪声。"""
    return (rng.standard_normal(n) + 1j * rng.standard_normal(n)) / np.sqrt(2.0)


def _hop_pattern(rng, nch, nhops):
    """分块随机置换跳频图案：每个 nch 跳覆盖全部信道，相邻块首尾不同。"""
    pattern = []
    while len(pattern) < nhops:
        block = rng.permutation(nch).tolist()
        if pattern and block[0] == pattern[-1]:
            block[0], block[1] = block[1], block[0]
        pattern.extend(block)
    return pattern[:nhops]


def gen_fhss(rng, fs, n, channels, dwell_samples, snr_db, hop_offset=0):
    """合成跳频信号：每个驻留段一个信道单音（分块置换图案）+ 高斯噪声。

    hop_offset：首个跳变沿相对样本零点的偏移（样本），用于模拟接收机未知跳变
    沿时的定时错位；0 表示跳变沿与分析帧边界对齐（best-case）。
    """
    nch = len(channels)
    amp = 10 ** (snr_db / 20.0)
    nhops = int(np.ceil((n + hop_offset) / dwell_samples)) + 1
    pattern = _hop_pattern(rng, nch, nhops)
    x = np.zeros(n, dtype=np.complex128)
    for j in range(nhops):
        s = max(0, j * dwell_samples - hop_offset)
        e = min(n, (j + 1) * dwell_samples - hop_offset)
        if s >= n or e <= s:
            continue
        ch = pattern[j]
        seg_t = np.arange(e - s) / fs
        x[s:e] += amp * np.exp(1j * 2 * np.pi * channels[ch] * seg_t
                               + 1j * rng.uniform(0, 2 * np.pi))
    return x + _noise(rng, n)


def gen_cw(rng, fs, n, freq, snr_db):
    """定频连续波（非跳频负样本）。"""
    amp = 10 ** (snr_db / 20.0)
    tt = np.arange(n) / fs
    return amp * np.exp(1j * 2 * np.pi * freq * tt) + _noise(rng, n)


def run_detect(x, fs, cf, num_frames, frame_ms, threshold):
    try:
        return detect_fhss(x, fs, cf, num_frames=num_frames,
                           frame_interval_ms=frame_ms, fft_size=1024,
                           threshold_db=threshold)
    except Exception:
        return {"detected": False}


def exp_pd_vs_snr(rng, fs, n, channels, dwell_samples, num_frames, frame_ms,
                  threshold, nch, trials, out):
    """组1：检出率与信道数正确率 vs SNR。"""
    rows = []
    for snr in range(-20, 17, 2):
        n_det, n_unique_ok = 0, 0
        for _ in range(trials):
            x = gen_fhss(rng, fs, n, channels, dwell_samples, snr)
            r = run_detect(x, fs, 100e6, num_frames, frame_ms, threshold)
            det = bool(r.get("detected", False))
            n_det += int(det)
            if det and abs(r.get("num_unique_frequencies", -1) - nch) <= 1:
                n_unique_ok += 1
        rows.append({
            "snr_db": snr, "threshold_db": threshold, "n_trials": trials,
            "detected_rate": round(n_det / trials, 4),
            "detection_rate": round(n_unique_ok / trials, 4),
            "true_n_channels": nch,
        })
    path = os.path.join(out, "fhss_pd_vs_snr.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wr.writeheader(); wr.writerows(rows)
    print(f"[1] 检出率-SNR：{len(rows)} 行 -> {path}")
    return rows


def exp_pfa(rng, fs, n, channels, num_frames, frame_ms, trials, out):
    """组2：纯噪声与定频 CW 的虚警率 vs 绝对门限。"""
    cw_freq = float(channels[len(channels) // 2])
    thresholds = [30, 32, 34, 35, 36, 37, 38, 40, 42]
    rows = []
    for th in thresholds:
        fa_noise = fa_cw = 0
        for _ in range(trials):
            xn = _noise(rng, n)
            rn = run_detect(xn, fs, 100e6, num_frames, frame_ms, th)
            fa_noise += int(bool(rn.get("detected", False)))
            xc = gen_cw(rng, fs, n, cw_freq, 10)
            rc = run_detect(xc, fs, 100e6, num_frames, frame_ms, th)
            fa_cw += int(bool(rc.get("detected", False)))
        rows.append({
            "threshold_db": th, "n_trials": trials,
            "noise_false_alarm_rate": round(fa_noise / trials, 4),
            "cw_false_alarm_rate": round(fa_cw / trials, 4),
        })
    path = os.path.join(out, "fhss_pfa_vs_threshold.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wr.writeheader(); wr.writerows(rows)
    print(f"[2] 虚警率-门限：{len(rows)} 行 -> {path}")
    return rows


def exp_param(rng, fs, n, channels, dwell_samples, dwell_ms, num_frames,
              frame_ms, threshold, nch, trials, out):
    """组3：跳频速率/驻留/信道数估计精度 vs SNR（对齐与定时偏移两场景）。"""
    true_rate = 1000.0 / dwell_ms
    rows = []
    scenarios = [("aligned", lambda: 0),
                 ("misaligned", lambda: int(rng.integers(0, dwell_samples)))]
    for snr in range(-10, 21, 5):
        for timing, off_fn in scenarios:
            rate_err, dwell_err, unique_vals, unique_exact = [], [], [], []
            n_det = 0
            for _ in range(trials):
                x = gen_fhss(rng, fs, n, channels, dwell_samples, snr,
                             hop_offset=off_fn())
                r = run_detect(x, fs, 100e6, num_frames, frame_ms, threshold)
                if not r.get("detected", False):
                    continue
                n_det += 1
                rate_err.append(abs(float(r.get("hop_rate_hz", 0.0)) - true_rate))
                dwell_err.append(abs(float(r.get("avg_dwell_time_ms", 0.0)) - dwell_ms))
                nu = int(r.get("num_unique_frequencies", 0))
                unique_vals.append(nu)
                unique_exact.append(int(nu == nch))

            def mae(v):
                return round(float(np.mean(v)), 4) if v else ""
            rows.append({
                "snr_db": snr, "timing_alignment": timing,
                "n_detected": n_det, "n_trials": trials,
                "true_hop_rate_hz": true_rate,
                "hop_rate_mae_hz": mae(rate_err),
                "hop_rate_rel_err": round(float(np.mean(rate_err)) / true_rate, 4) if rate_err else "",
                "true_dwell_ms": dwell_ms,
                "dwell_mae_ms": mae(dwell_err),
                "true_n_channels": nch,
                "unique_mean": round(float(np.mean(unique_vals)), 3) if unique_vals else "",
                "unique_exact_rate": round(float(np.mean(unique_exact)), 4) if unique_exact else "",
            })
    path = os.path.join(out, "fhss_param_vs_snr.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wr.writeheader(); wr.writerows(rows)
    print(f"[3] 参数估计-SNR：{len(rows)} 行 -> {path}")
    return rows


def main():
    ap = argparse.ArgumentParser(description="FHSS 跳频检测可复现实验")
    ap.add_argument("--trials", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20260919)
    ap.add_argument("--fs", type=float, default=240e3)
    ap.add_argument("--frame-ms", type=float, default=10.0)
    ap.add_argument("--dwell-ms", type=float, default=20.0)
    ap.add_argument("--n-channels", type=int, default=8)
    ap.add_argument("--num-frames", type=int, default=60)
    ap.add_argument("--threshold", type=float, default=38.0)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))), "paper", "experiments"))
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    rng = np.random.default_rng(args.seed)

    frame_samples = int(args.fs * args.frame_ms / 1000.0)
    n = frame_samples * args.num_frames
    dwell_samples = int(args.fs * args.dwell_ms / 1000.0)
    # 8 个信道等间隔分布在 ±80 kHz，信道间隔 22.9 kHz，远大于 2%fs=4.8 kHz 聚类容差
    channels = np.linspace(-80e3, 80e3, args.n_channels)

    print(f"fs={args.fs/1e3:.0f}kHz frame={args.frame_ms}ms({frame_samples}smp) "
          f"frames={args.num_frames} n={n} dwell={args.dwell_ms}ms({dwell_samples}smp) "
          f"channels={args.n_channels} thr={args.threshold}dB "
          f"trials={args.trials}")

    exp_pd_vs_snr(rng, args.fs, n, channels, dwell_samples, args.num_frames,
                  args.frame_ms, args.threshold, args.n_channels, args.trials, args.out)
    exp_pfa(rng, args.fs, n, channels, args.num_frames, args.frame_ms,
            args.trials, args.out)
    exp_param(rng, args.fs, n, channels, dwell_samples, args.dwell_ms,
              args.num_frames, args.frame_ms, args.threshold, args.n_channels,
              args.trials, args.out)
    print("完成。")


if __name__ == "__main__":
    main()
