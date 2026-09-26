"""
MBDSDR AI 内核 - FIR 抽头生成器（窗函数法）
============================================

逐行对照 SDR++ 的 taps 包（repos/sdrpp/core/src/dsp/taps/）与 window 包：

  - estimate_tap_count.h:4-6   estimateTapCount(transWidth, sr) = 3.8*sr/transWidth
  - windowed_sinc.h:17-26      windowedSinc(count, omega, window)
  - window/cosine.h:7-16       余弦窗通式（交替符号求和）
  - window/nuttall.h:6         Nuttall 窗四系数
  - window/hann.h / hamming.h  Hann / Hamming 窗
  - taps/low_pass.h:7-11       lowPass(cutoff, transWidth, sr)
  - taps/high_pass.h / band_pass.h
  - taps/root_raised_cosine.h:8-29  RRC 脉冲成形

本模块是纯 NumPy 实现，不依赖 SciPy（SciPy 仅在可选加速时使用）。
所有抽头都是「原型低通/带通/脉冲成形」的实数 FIR 系数，供
fir.h / decimating_fir.h / polyphase_resampler.h 直接卷积使用。

设计约定与 SDR++ 完全一致：
  - 抽头数 N = estimateTapCount(transWidth, sampleRate)，默认偶数；
    需要 Type I 线性相位时取奇数（low_pass.h:9 oddTapCount 开关）。
  - 窗函数自变量 n = i - N/2（关于 0 对称），窗在 ±N/2 处取值。
  - windowedSinc 的增益归一 corr = omega/pi（windowed_sinc.h:15），
    即直流增益 = 1（截止内通带平坦）。

License: GPL-3.0-or-later
"""

from __future__ import annotations

import numpy as np

__all__ = [
    "estimate_tap_count",
    "nuttall",
    "hann",
    "hamming",
    "windowed_sinc",
    "lowpass_taps",
    "highpass_taps",
    "bandpass_taps",
    "root_raised_cosine_taps",
]


# ─────────────────────────────────────────────────────────────────
# 窗函数（对照 sdrpp/core/src/dsp/window/）
# ─────────────────────────────────────────────────────────────────
def _cosine_window(n: np.ndarray, N: float, coefs) -> np.ndarray:
    """余弦窗通式：sum_i sign_i * coef_i * cos(i*2*pi*n/N)，符号交替。

    对照 window/cosine.h:7-16。
    """
    win = np.zeros_like(n, dtype=np.float64)
    sign = 1.0
    for i, c in enumerate(coefs):
        win += sign * c * np.cos(i * 2.0 * np.pi * n / N)
        sign = -sign
    return win


def nuttall(n: np.ndarray, N: float) -> np.ndarray:
    """三阶 Nuttall 窗（连续导数为零的最优旁瓣窗）。

    对照 window/nuttall.h:5-8，系数 0.355768/0.487396/0.144232/0.012604。
    阻带衰减约 -93 dB，SDR++ lowPass 的默认窗。
    """
    return _cosine_window(n, N, (0.355768, 0.487396, 0.144232, 0.012604))


def hann(n: np.ndarray, N: float) -> np.ndarray:
    """Hann 窗。对照 window/hann.h（0.5 - 0.5*cos(2pi n/N)）。"""
    return _cosine_window(n, N, (0.5, 0.5))


def hamming(n: np.ndarray, N: float) -> np.ndarray:
    """Hamming 窗。对照 window/hamming.h（0.54 - 0.46*cos(2pi n/N)）。"""
    return _cosine_window(n, N, (0.54, 0.46))


# ─────────────────────────────────────────────────────────────────
# 抽头数估计 + 窗函数 sinc
# ─────────────────────────────────────────────────────────────────
def estimate_tap_count(trans_width_hz: float, sample_rate_hz: float) -> int:
    """按过渡带宽度估计所需 FIR 抽头数。

    对照 taps/estimate_tap_count.h:4-6：return 3.8 * sampleRate / transWidth。
    3.8 是 Nuttall 窗（~93dB 阻带）对应的经验常数。
    """
    if trans_width_hz <= 0:
        raise ValueError(f"trans_width must be > 0, got {trans_width_hz}")
    return int(round(3.8 * sample_rate_hz / trans_width_hz))


def windowed_sinc(count: int, cutoff_hz: float, sample_rate_hz: float,
                  window=nuttall) -> np.ndarray:
    """窗函数法 sinc FIR（实数低通原型）。

    对照 taps/windowed_sinc.h:17-26：
        half = count/2
        corr = omega/pi          （omega = 2*pi*cutoff/sr，hz_to_rads）
        t[i] = i - half + 0.5
        taps[i] = sinc(t[i]*omega) * window(t[i]-half, count) * corr
    """
    count = int(count)
    if count < 1:
        raise ValueError("count must be >= 1")
    half = count / 2.0
    omega = 2.0 * np.pi * cutoff_hz / sample_rate_hz  # hz_to_rads, math/hz_to_rads.h
    corr = omega / np.pi                              # windowed_sinc.h:15

    i = np.arange(count, dtype=np.float64)
    t = i - half + 0.5                                # windowed_sinc.h:18
    # math/sinc.h: sinc(x) = sin(x)/x；x=0 处取 1
    with np.errstate(invalid="ignore", divide="ignore"):
        s = np.sinc(t * omega / np.pi)  # np.sinc 已归一化（sin(pi x)/(pi x)）
    # windowed_sinc.h:20: math::sinc(t*omega) —— math::sinc 定义为 sin(x)/x（未归一化）
    # np.sinc(z) = sin(pi z)/(pi z)，所以 math::sinc(t*omega) = np.sinc(t*omega/pi)
    w = window(t - half, count)                       # window(t-half,count)
    taps = s * w * corr
    return taps.astype(np.float64)


def lowpass_taps(cutoff_hz: float, trans_width_hz: float, sample_rate_hz: float,
                 odd: bool = False, window=nuttall) -> np.ndarray:
    """低通 FIR 抽头。对照 taps/low_pass.h:7-11。

    Args:
        cutoff_hz: -6dB 截止频率（Hz），必须 < sample_rate/2。
        trans_width_hz: 过渡带宽度（Hz），决定抽头数。
        sample_rate_hz: 采样率（Hz）。
        odd: True 强制奇数抽头（Type I 线性相位），对照 low_pass.h:9。
    """
    count = estimate_tap_count(trans_width_hz, sample_rate_hz)
    if odd and count % 2 == 0:
        count += 1
    if count < 3:
        count = 3 if not odd or 3 % 2 else 3
    return windowed_sinc(count, cutoff_hz, sample_rate_hz, window)


def highpass_taps(cutoff_hz: float, trans_width_hz: float, sample_rate_hz: float,
                  window=nuttall) -> np.ndarray:
    """高通 FIR 抽头 = 频移低通（i 倍调制到 ±Nyquist）。

    对照 taps/high_pass.h：低通原型调制 exp(j*pi*n) 等效频率搬移 fs/2。
    高通必须用奇数抽头（Type I，反对称到直流为零）。
    """
    count = estimate_tap_count(trans_width_hz, sample_rate_hz)
    if count % 2 == 0:
        count += 1
    lp = windowed_sinc(count, cutoff_hz, sample_rate_hz, window)
    n = np.arange(count)
    return (lp * ((-1.0) ** n)).astype(np.float64)


def bandpass_taps(low_cut_hz: float, high_cut_hz: float, trans_width_hz: float,
                  sample_rate_hz: float, window=nuttall) -> np.ndarray:
    """带通 FIR 抽头 = 两个低通之差。对照 taps/band_pass.h。"""
    count = estimate_tap_count(trans_width_hz, sample_rate_hz)
    if count % 2 == 0:
        count += 1
    lp_hi = windowed_sinc(count, high_cut_hz, sample_rate_hz, window)
    lp_lo = windowed_sinc(count, low_cut_hz, sample_rate_hz, window)
    return (lp_hi - lp_lo).astype(np.float64)


# ─────────────────────────────────────────────────────────────────
# Root Raised Cosine（RRC）脉冲成形
# ─────────────────────────────────────────────────────────────────
def root_raised_cosine_taps(count: int, beta: float,
                            symbol_rate_hz: float, sample_rate_hz: float) -> np.ndarray:
    """Root Raised Cosine 脉冲成形 FIR。

    对照 taps/root_raised_cosine.h:8-34：
        Ts = samplerate/symbolrate（每个符号的采样数）
        t[i] = i - count/2 + 0.5
        特殊点 t=0 与 t=±Ts/(4*beta) 给闭式；否则标准 RRC 公式。
    """
    if not 0.0 < beta <= 1.0:
        raise ValueError(f"beta must be in (0,1], got {beta}")
    count = int(count)
    Ts = sample_rate_hz / symbol_rate_hz
    half = count / 2.0
    limit = Ts / (4.0 * beta)

    i = np.arange(count, dtype=np.float64)
    t = i - half + 0.5
    taps = np.zeros(count, dtype=np.float64)

    for k in range(count):
        tk = t[k]
        if tk == 0.0:
            # root_raised_cosine.h:18
            taps[k] = (1.0 + beta * (4.0 / np.pi - 1.0)) / Ts
        elif abs(tk - limit) < 1e-12 or abs(tk + limit) < 1e-12:
            # root_raised_cosine.h:21
            taps[k] = (
                ((1.0 + 2.0 / np.pi) * np.sin(np.pi / (4.0 * beta))
                 + (1.0 - 2.0 / np.pi) * np.cos(np.pi / (4.0 * beta)))
                * beta / (Ts * np.sqrt(2.0))
            )
        else:
            # root_raised_cosine.h:24
            x = 4.0 * beta * tk / Ts
            taps[k] = (
                (np.sin((1.0 - beta) * np.pi * tk / Ts)
                 + np.cos((1.0 + beta) * np.pi * tk / Ts) * x)
                / ((1.0 - x * x) * np.pi * tk / Ts)
            ) / Ts
    return taps.astype(np.float64)
