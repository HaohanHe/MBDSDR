#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Phase9 P3 — 新时空融合 · 一条命令确定性演示
================================================================

把"时间基准(GNSS 授时) + 多普勒补偿"串成一条**云内无硬件也能确定性跑通**的
演示路径，并与桌面"时空视图"tab 的诚实空态一一对应：

  1. 注入固定 GNSS UTC 时间（不读串口/不联网）->
     ``mbdsdr_ai.gnss_timestamps.resolve_capture_datetime`` ->
     得 ``CaptureTime(time_source="gnss")``，把 ``core:datetime`` 与
     ``mbdsdr:time_source`` 写进 SigMF sidecar；
  2. 合成一段**带多普勒**的基带信号（固定种子），写成 cf32_le SigMF；
  3. 用 ``mbdsdr_ai.doppler_compensation.remove_doppler_shift``（NCO 数字下混频）
     对已知多普勒做逆运算补偿；
  4. "解码"= 估计载波峰值残余频偏：补偿前偏在 f0+fd，补偿后拉回 f0；
  5. 出一张带**时空标注**的图（时间源 / UTC / 多普勒补偿状态），全英文
     （云 VM 无中文字体）。

诚实空态红线（与 tab 一致）：
  * 云内无 GNSS 硬件：本演示**注入**一个固定 GNSS 时间并如实标 ``gnss``；
    同时演示无 GNSS 时 ``resolve_capture_datetime(gnss_dt=None, clock=...)``
    退化为 ``system``（本机时钟），绝不把 system 伪装成 gnss。
  * 全程固定种子、固定时间、固定多普勒 -> 两次运行数值逐位一致。

一条命令跑通：``python3 experiments/demo_spacetime.py``
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from mbdsdr_ai import gnss_timestamps as gt  # noqa: E402
from mbdsdr_ai import doppler_compensation as dc  # noqa: E402
from mbdsdr_ai import playback  # noqa: E402

# ---------------------------------------------------------------------------
# 固定场景参数（确定性：任何机器跑都得到同一组数字）
# ---------------------------------------------------------------------------
FS_HZ = 200_000.0          # 采样率
N_SAMPLES = 40_000         # 0.2 s
TONE_HZ = 2_000.0          # 基带期望载波位置
DOPPLER_HZ = 150.0         # 注入的恒定多普勒（卫星径向运动）
CENTER_FREQ_HZ = 137.1e6   # 标称下行中心频率（NOAA 类）
SEED = 20261002

# 注入的"GNSS 授时"与"本机时钟"——都写死，不读墙钟。
INJECTED_GNSS_UTC = datetime(2026, 10, 2, 7, 25, 45, tzinfo=timezone.utc)
INJECTED_SYSTEM_UTC = datetime(2026, 10, 2, 7, 25, 46, tzinfo=timezone.utc)

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "axes.unicode_minus": False,
    "figure.dpi": 110,
})


def _fixed_clock() -> datetime:
    """注入的本机时钟（替代真实墙钟），保证确定性。"""
    return INJECTED_SYSTEM_UTC


def peak_freq_hz(iq: np.ndarray, fs: float) -> float:
    """估计频谱峰值位置（Hz）。复基带用复 FFT；Hann 窗减少泄漏。确定性。"""
    w = iq * np.hanning(iq.size).astype(iq.dtype)
    spec = np.abs(np.fft.fft(w))
    freqs = np.fft.fftfreq(iq.size, d=1.0 / fs)
    return float(freqs[int(np.argmax(spec))])


def build_signal() -> np.ndarray:
    """固定种子：干净基带载波（+TONE_HZ）+ 固定幅度微扰。复 complex64。"""
    rng = np.random.default_rng(SEED)
    t = np.arange(N_SAMPLES, dtype=np.float64) / FS_HZ
    tone = np.exp(1j * 2.0 * np.pi * TONE_HZ * t)
    # 固定种子的微小带内扰动（不是噪声随机路径，保证可复现）
    perturbation = 0.01 * (rng.standard_normal(N_SAMPLES)
                           + 1j * rng.standard_normal(N_SAMPLES))
    x = tone + perturbation.astype(np.complex128)
    return x.astype(np.complex64)


def write_sigmf(out_dir: str, iq: np.ndarray, cap: gt.CaptureTime) -> str:
    """写 cf32_le SigMF（data + meta），meta 带 time_source 标注。返回 data 路径。"""
    base = os.path.join(out_dir, "demo_spacetime")
    data_path = base + ".sigmf-data"
    meta_path = base + ".sigmf-meta"
    iq.tofile(data_path)
    meta = {
        "global": {
            "core:datatype": "cf32_le",
            "core:sample_rate": FS_HZ,
            "core:version": "1.0.0",
            "core:num_channels": 1,
            "core:frequency": CENTER_FREQ_HZ,
            "core:num_samples": int(iq.size),
            "core:hw": "synthetic-injected (NOT HARDWARE)",
            "core:description": "Phase9 P3 spacetime demo: injected GNSS time + "
                                 "known Doppler, fixed seed.",
        },
        "captures": [{
            "core:sample_start": 0,
            "core:frequency": CENTER_FREQ_HZ,
            "core:datetime": cap.datetime_iso,
            **cap.as_capture_fields(),
        }],
        "annotations": [],
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, sort_keys=True)
    return data_path


def make_figure(out_dir: str, iq_rx: np.ndarray, iq_comp: np.ndarray,
                residual_before: float, residual_after: float,
                cap: gt.CaptureTime, honest_fallback: gt.CaptureTime) -> str:
    """出带时空标注的图：补偿前/后 PSD 对比 + 时间源/多普勒状态文本框。"""
    fig, ax = plt.subplots(figsize=(8.6, 4.6))
    win = np.hanning(N_SAMPLES).astype(np.complex128)
    freqs = np.fft.fftfreq(N_SAMPLES, d=1.0 / FS_HZ)
    band = (freqs >= TONE_HZ - 400) & (freqs <= TONE_HZ + 400)
    for iq, label, color in (
        (iq_rx, "received (uncompensated)", "#e0b35a"),
        (iq_comp, "NCO Doppler-compensated", "#5fd08a"),
    ):
        spec = 20.0 * np.log10(np.abs(np.fft.fft(iq * win)) + 1e-12)
        ax.plot(freqs[band], spec[band], lw=1.2, color=color, label=label)
    ax.axvline(TONE_HZ, color="#7CC4FF", ls="--", lw=1.0,
               label=f"expected tone @ {TONE_HZ:.0f} Hz")
    ax.set_xlim(TONE_HZ - 400, TONE_HZ + 400)
    ax.set_xlabel("Baseband frequency (Hz)")
    ax.set_ylabel("PSD (dB)")
    ax.set_title("Spacetime demo: injected GNSS time + NCO Doppler compensation "
                 "(synthetic, fixed seed)")
    ax.legend(loc="upper right", fontsize=8)
    note = (f"capture time: {cap.datetime_iso}\n"
            f"time source: {cap.time_source} (GNSS-injected)\n"
            f"Doppler injected: {DOPPLER_HZ:.0f} Hz -> removed via NCO\n"
            f"residual before/after: {residual_before:+.1f} / {residual_after:+.1f} Hz\n"
            f"honest fallback (no GNSS): time_source={honest_fallback.time_source}")
    ax.text(0.02, 0.97, note, transform=ax.transAxes, va="top", ha="left",
            fontsize=8, family="monospace",
            bbox=dict(boxstyle="round", fc="#f4f1ea", ec="#999", alpha=0.9))
    fig.tight_layout()
    fig_dir = os.path.join(out_dir, "figures")
    os.makedirs(fig_dir, exist_ok=True)
    path = os.path.join(fig_dir, "spacetime_demo.png")
    fig.savefig(path)
    plt.close(fig)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "experiments", "artifacts",
                                                  "spacetime_demo"))
    args = ap.parse_args(argv)
    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)

    # 1) 时间基准：注入固定 GNSS 时间 -> gnss；再演示无 GNSS -> system 诚实退化。
    cap = gt.resolve_capture_datetime(gnss_dt=INJECTED_GNSS_UTC)
    honest_fallback = gt.resolve_capture_datetime(gnss_dt=None, clock=_fixed_clock)
    assert cap.time_source == gt.TIME_SOURCE_GNSS
    assert honest_fallback.time_source == gt.TIME_SOURCE_SYSTEM

    # 2) 合成带多普勒的信号：干净基带载波 -> 叠加已知多普勒。
    base = build_signal()
    iq_rx = dc.apply_doppler_shift(base, FS_HZ, DOPPLER_HZ)   # 模拟接收

    # 写 SigMF（time_source=gnss）。
    data_path = write_sigmf(out_dir, iq_rx, cap)

    # 回放端读回 meta（与桌面 C++ 同一字段约定）。
    meta_back = playback.parse_sigmf_meta(data_path.replace(".sigmf-data",
                                                           ".sigmf-meta"))
    assert meta_back["time_source"] == "gnss", meta_back
    assert meta_back["datetime"] == cap.datetime_iso

    # 3) NCO 补偿：用已知多普勒逆运算回基带。
    iq_comp = dc.remove_doppler_shift(iq_rx, FS_HZ, DOPPLER_HZ)

    # 4) "解码"：载波峰值残余频偏。补偿前应偏在 +fd，补偿后回到 ~0。
    peak_before = peak_freq_hz(iq_rx, FS_HZ)
    peak_after = peak_freq_hz(iq_comp, FS_HZ)
    residual_before = peak_before - TONE_HZ
    residual_after = peak_after - TONE_HZ
    bin_hz = FS_HZ / N_SAMPLES
    decode_ok = bool(abs(residual_after) <= 2.0 * bin_hz)

    # 5) 图。
    fig_path = make_figure(out_dir, iq_rx, iq_comp,
                           residual_before, residual_after, cap, honest_fallback)

    # 6) 确定性摘要 JSON（只含固定字段，无墙钟/git sha）。
    summary = {
        "seed": SEED,
        "fs_hz": FS_HZ,
        "n_samples": N_SAMPLES,
        "center_freq_hz": CENTER_FREQ_HZ,
        "tone_hz": TONE_HZ,
        "capture_datetime": cap.datetime_iso,
        "time_source": cap.time_source,
        "time_note": cap.note,
        "fallback_when_no_gnss": {
            "time_source": honest_fallback.time_source,
            "datetime_iso": honest_fallback.datetime_iso,
            "note": honest_fallback.note,
        },
        "doppler_injected_hz": DOPPLER_HZ,
        "doppler_compensated": True,
        "residual_offset_before_hz": round(residual_before, 3),
        "residual_offset_after_hz": round(residual_after, 3),
        "decode_ok": decode_ok,
        "sigmf_data": os.path.basename(data_path),
        "figure": os.path.relpath(fig_path, out_dir),
        "data_origin": "synthetic-injected-fixed-seed",
        "license": "MIT",
    }
    sum_path = os.path.join(out_dir, "spacetime_demo.json")
    with open(sum_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, sort_keys=True)

    print(f"[time]   capture={cap.datetime_iso} source={cap.time_source}")
    print(f"[sig]    Doppler +{DOPPLER_HZ:.0f} Hz injected -> NCO removed")
    print(f"[decode] residual before={residual_before:+.1f} Hz "
          f"after={residual_after:+.1f} Hz  ok={decode_ok}")
    print(f"[write] {data_path}")
    print(f"[write] {fig_path}")
    print(f"[write] {sum_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
