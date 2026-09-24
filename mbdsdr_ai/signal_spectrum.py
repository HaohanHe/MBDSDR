"""
MBDSDR 信号频谱分析（纯 numpy，不依赖硬件）
============================================
对一段复数 IQ 同时产出：
  - Welch 平均 PSD（稳定底噪）
  - Peak Hold 峰值保持（捕捉短促信号）
  - 结构化峰列表（中心频率、峰值功率、估计带宽、突出度）

这是"找台 / 找干扰源 / 频谱扫描"技能的核心工具：AI 拿到这段描述后
可直接说"88.7MHz 有一个带宽 200kHz 的信号，比底噪高 23dB"。

FFT 管线参考：
  - GQRX src/dsp/rx_fft.cpp:126-156 — fftshift + |FFT|^2 功率谱
  - GQRX src/applications/gqrx/receiver.cpp:119 — 默认 Hann 窗
  - SDR++ core/src/signal_path/iq_frontend.cpp:248-267 — FFT 执行
  - SDR++ core/src/gui/widgets/waterfall.cpp:934-939 — Peak Hold（带衰减）
"""
from __future__ import annotations

import numpy as np
from typing import List, Dict, Any


# 来源: SDR++ core/src/dsp/window/nuttall.h:5-8 — 4-term Nuttall window 系数
def _nuttall(n: int) -> np.ndarray:
    k = np.arange(n, dtype=np.float64)
    coefs = (0.355768, 0.487396, 0.144232, 0.012604)
    return (coefs[0]
            - coefs[1] * np.cos(2.0 * np.pi * k / max(n - 1, 1))
            + coefs[2] * np.cos(4.0 * np.pi * k / max(n - 1, 1))
            - coefs[3] * np.cos(6.0 * np.pi * k / max(n - 1, 1))).astype(np.float64)


def analyze_iq_spectrum(
    iq: np.ndarray,
    sample_rate: float,
    center_hz: float = 0.0,
    fft_size: int = 2048,
    n_peaks: int = 12,
    margin_db: float = 8.0,
    window: str = "hann",
) -> Dict[str, Any]:
    """分析一段复数 IQ。

    iq: 复数采样。center_hz: 这段频谱中心频率（换算绝对频率用）。
    window: "hann"（GQRX 默认, rx_fft.cpp:222-238）或 "nuttall"
            （SDR++ 默认, nuttall.h:5-8，旁瓣 -93dB）。
    返回 {freqs_hz, avg_psd_db, peak_hold_db, noise_floor_db, peaks:[...]}。
    """
    # 来源: GQRX rx_fft.cpp:95-120 — 去除 DC 分量后做分帧 FFT
    x = np.asarray(iq, dtype=np.complex128)
    x = x - np.mean(x)
    sr = float(sample_rate)
    n = len(x)
    if n < fft_size:
        fft_size = max(64, 1 << int(np.log2(max(64, n))))
    hop = fft_size // 2
    # 来源: GQRX receiver.cpp:119 — 默认 Hann 窗；可选 Nuttall（SDR++ 默认）
    if window == "nuttall":
        win = _nuttall(fft_size)
    else:
        win = np.hanning(fft_size).astype(np.float64)
    wpow = np.sum(win ** 2)

    acc = np.zeros(fft_size)
    hold = np.full(fft_size, -np.inf)
    frames = 0
    # 来源: GQRX rx_fft.cpp:126-156 — 复 FFT + fftshift + 功率谱
    for s in range(0, n - fft_size + 1, hop):
        sp = np.fft.fftshift(np.fft.fft(x[s:s + fft_size] * win))
        p = np.abs(sp) ** 2 / (wpow * sr)
        acc += p
        # 来源: SDR++ waterfall.cpp:934-939 — Peak Hold（离线分析用无衰减版）
        hold = np.maximum(hold, p)
        frames += 1
    if frames == 0:
        pad = np.concatenate([x, np.zeros(fft_size - n)]) * win
        sp = np.fft.fftshift(np.fft.fft(pad))
        acc = np.abs(sp) ** 2 / (wpow * sr)
        hold = acc.copy()
        frames = 1

    avg = 10.0 * np.log10(acc / frames + 1e-20)
    hold_db = 10.0 * np.log10(hold + 1e-20)
    freqs = center_hz + (np.arange(fft_size) - fft_size / 2) * (sr / fft_size)

    noise = float(np.median(avg))
    thresh = noise + margin_db
    bin_hz = sr / fft_size

    # 在峰值保持上找连续超门限段 = 一个信号
    above = hold_db > thresh
    peaks: List[Dict[str, Any]] = []
    i = 0
    while i < fft_size:
        if above[i]:
            j = i
            while j < fft_size and above[j]:
                j += 1
            seg = hold_db[i:j]
            krel = int(np.argmax(seg))
            peak_idx = i + krel
            peaks.append({
                "freq_hz": round(float(freqs[peak_idx]), 1),
                "power_db": round(float(hold_db[peak_idx]), 1),
                "prominence_db": round(float(hold_db[peak_idx] - noise), 1),
                "bw_hz": round(float((j - i) * bin_hz), 0),
            })
            i = j
        else:
            i += 1

    peaks.sort(key=lambda p: -p["power_db"])
    return {
        "n_samples": n,
        "sample_rate": sr,
        "center_hz": center_hz,
        "fft_size": fft_size,
        "frames_averaged": frames,
        "noise_floor_db": round(noise, 1),
        "freqs_hz": [round(float(f), 1) for f in freqs],
        "avg_psd_db": [round(float(v), 1) for v in avg],
        "peak_hold_db": [round(float(v), 1) for v in hold_db],
        "peaks": peaks[:n_peaks],
    }


if __name__ == "__main__":
    # 自测：注入一个 100kHz 处的正弦脉冲 + 噪声
    sr = 1.0e6
    t = np.arange(65536) / sr
    sig = 0.0  # 噪声底
    noise = (np.random.randn(65536) + 1j * np.random.randn(65536)) * 0.1
    burst = np.zeros(65536, dtype=complex)
    burst[20000:22000] = 0.8 * np.exp(2j * np.pi * 100000.0 * t[20000:22000])
    iq = noise + burst
    r = analyze_iq_spectrum(iq, sr, center_hz=100e6)
    print("底噪:", r["noise_floor_db"], "| 检测到峰:")
    for p in r["peaks"][:3]:
        print("  ", p)
