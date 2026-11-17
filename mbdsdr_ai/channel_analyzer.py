# SPDX-License-Identifier: MIT
"""
mbdsdr_ai/channel_analyzer.py — 信道分析器
=============================================

对照 SDRangel ``plugins/channelrx/chanalyzer/``：
  - ``chanalyzersink.cpp:87-93``  SSB/DSB/RRC 滤波器 + PLL/Costas
  - ``chanalyzersink.cpp:189``    ``m_channelPowerAvg(m_magsq)`` 滑动功率平均
  - 同时输出：FFT 功率谱 + 解调音频 + 信号统计（带宽/占用率/峰值/均值）

本模块不依赖硬件：输入一段复数 IQ，即可同时产出：
  * 功率谱密度 (dB/Hz)
  * 峰值/均值/底噪功率
  * 每个检测到的信号：中心频率、-20dB 带宽、占用率
  * 简单 FM 解调音频（供耳机/录波）
  * AI 自动信号分类（基于带宽/形状的规则分类器，可插拔替换）

我们的增强：
  * :func:`classify_signal` —— 实时标注信号类型（CW/AM/FM/数字/噪声未定）。
    峰值抬升 < :data:`NOISE_CFAR_P2M_DB` 的段只给「噪声/未定」，不给类型。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

__all__ = ["ChannelAnalysis", "ChannelAnalyzer", "analyze_iq", "classify_signal"]


#: 噪声凸起的 CFAR 门：段峰值相对**中位噪声底**的抬升（dB）。
#:
#: 依据（推导上界 + 实测）：单 bin 越限概率在无窗（2 自由度指数分布）时
#: P = exp(-ln2·10^(X/10))——X=14 给 P≈2.8e-8（2048 bin 一帧约 6e-5）；
#: Hann 窗把每 bin 自由度抬到 ≈2.67，尾部更轻（实测 6 dB 阈值时越限 bin
#: 占比 5.8%，指数上界为 25%），但一帧 2048 bin 仍有约 120 个越限 bin，
#: 成串的「假信号」必然出现——所以门不能只靠分段阈值。
#:
#: 实测（2026-11-17，本机，见 tests/test_channel_analyzer_noise_gate.py）：
#:   * 复高斯白噪声 1200 个单帧（fft 512/1024/2048）共 22,340 个越限凸起，
#:     峰值抬升 p50 7.94 / p99 11.03 / **最大 13.21 dB**；
#:   * 真实 OTA FM 样本 tests/fixtures/real_fm_88m1（511 帧平均）：残余凸起
#:     6.2/7.3/6.5 dB（旧实现三次全被标「FM/模拟」——假阳性），
#:     真正的 FM 广播 18.7 dB。
#: 14 dB 落在实测噪声上界（13.21）与真实信号下界（18.7）之间。
NOISE_CFAR_P2M_DB = 14.0


@dataclass
class ChannelAnalysis:
    """一次分析的全部产物。

    ``signals`` 里可能出现 ``type == "噪声/未定"`` 的条目：它们确实越过了
    分段阈值（6 dB），但峰值抬升不到 :data:`NOISE_CFAR_P2M_DB`，按统计
    无法与噪声凸起区分——保留是为了让调用方能看见「有多少越限凸起」，
    不是信号声明。调用方按类型过滤即可。
    """
    sample_rate: float
    center_hz: float
    fft_size: int
    freqs_hz: np.ndarray
    psd_db: np.ndarray
    noise_floor_db: float
    peak_power_db: float
    mean_power_db: float
    occupancy: float
    signals: List[Dict] = field(default_factory=list)
    demod_audio: Optional[np.ndarray] = None


def _hann(n: int) -> np.ndarray:
    k = np.arange(n, dtype=np.float64)
    return 0.5 - 0.5 * np.cos(2.0 * np.pi * k / max(n - 1, 1))


def classify_signal(bandwidth_hz: float, peak_to_mean_db: float,
                     spectral_flatness: float) -> str:
    """基于带宽/形状的规则分类器（可替换为 ML 模型）。

    第一道门是**可信度**而不是类型：峰值相对中位噪声底的抬升小于
    :data:`NOISE_CFAR_P2M_DB`（14 dB）时，这段「信号」在统计上与单帧
    噪声凸起不可分（实测噪声最大 13.21 dB），任何类型断言都是编造，
    一律回 ``"噪声/未定"``。过了这道门才谈带宽/形状。

    带宽是 -20 dB 带宽（解析度由 sample_rate / fft_size 决定，见
    :meth:`ChannelAnalyzer._add_signal`）：
      < 200 Hz              → CW/窄带
      < 6 kHz 且高信噪比     → AM/话音
      < 20 kHz 且谱顶平坦    → FM/模拟
      < 50 kHz              → 数字/宽带
      其余                   → 未知
    """
    if peak_to_mean_db < NOISE_CFAR_P2M_DB:
        return "噪声/未定"
    if bandwidth_hz < 200:
        return "CW/窄带"
    if bandwidth_hz < 6000 and peak_to_mean_db > 12:
        return "AM/话音"
    if bandwidth_hz < 20000 and spectral_flatness > 0.5:
        return "FM/模拟"
    if bandwidth_hz < 50000:
        return "数字/宽带"
    return "未知"


class ChannelAnalyzer:
    """信道分析器：一段 IQ 进，频谱+音频+统计出。"""

    def __init__(self, fft_size: int = 2048, overlap: float = 0.5,
                 threshold_db: float = 6.0):
        self.fft_size = fft_size
        self.overlap = overlap
        self.threshold_db = threshold_db

    def analyze(self, iq: np.ndarray, sample_rate: float,
                center_hz: float = 0.0,
                demod: str = "none") -> ChannelAnalysis:
        iq = np.asarray(iq, dtype=np.complex128)
        n = len(iq)
        fs = sample_rate

        # --- Welch 平均功率谱 --- #
        win = _hann(self.fft_size)
        step = max(1, int(self.fft_size * (1.0 - self.overlap)))
        n_frames = max(1, (n - self.fft_size) // step + 1)
        psd = np.zeros(self.fft_size, dtype=np.float64)
        for i in range(n_frames):
            seg = iq[i * step: i * step + self.fft_size] * win
            spec = np.fft.fftshift(np.fft.fft(seg))
            psd += np.abs(spec) ** 2
        psd /= max(n_frames, 1)
        psd /= (fs * np.sum(win ** 2))  # 归一化为 PSD
        psd_db = 10.0 * np.log10(psd + 1e-20)

        freqs = (np.fft.fftshift(np.fft.fftfreq(self.fft_size, d=1.0 / fs))) + center_hz

        # --- 统计 --- #
        noise_floor = float(np.median(psd_db))
        peak = float(np.max(psd_db))
        mean_p = float(np.mean(psd_db))
        occupancy = float(np.mean(psd_db > noise_floor + self.threshold_db))

        # --- 信号检测（峰搜索） --- #
        signals: List[Dict] = []
        above = psd_db > noise_floor + self.threshold_db
        # 找连续段
        in_sig = False
        start = 0
        for i in range(self.fft_size):
            if above[i] and not in_sig:
                in_sig = True
                start = i
            elif not above[i] and in_sig:
                in_sig = False
                self._add_signal(signals, freqs, psd_db, start, i, noise_floor)
        if in_sig:
            self._add_signal(signals, freqs, psd_db, start, self.fft_size, noise_floor)

        # --- 解调 --- #
        audio = None
        if demod == "fm":
            audio = self._demod_fm(iq, fs, max_deviation=75000.0)
        elif demod == "am":
            audio = self._demod_am(iq)

        return ChannelAnalysis(
            sample_rate=fs, center_hz=center_hz, fft_size=self.fft_size,
            freqs_hz=freqs, psd_db=psd_db,
            noise_floor_db=noise_floor, peak_power_db=peak, mean_power_db=mean_p,
            occupancy=occupancy, signals=signals, demod_audio=audio,
        )

    def _add_signal(self, out: List[Dict], freqs: np.ndarray, psd_db: np.ndarray,
                    start: int, end: int, noise_floor: float) -> None:
        seg = psd_db[start:end]
        if len(seg) < 2:
            return
        peak_idx = start + int(np.argmax(seg))
        peak_val = psd_db[peak_idx]
        # -20dB 带宽：从峰向两侧找降到 peak-20 的位置
        bw_low = peak_idx
        while bw_low > start and psd_db[bw_low] > peak_val - 20.0:
            bw_low -= 1
        bw_high = peak_idx
        while bw_high < end - 1 and psd_db[bw_high] > peak_val - 20.0:
            bw_high += 1
        bw_hz = abs(freqs[bw_high] - freqs[bw_low])
        # 谱平坦度（几何均值/算术均值）必须在线性功率域算：在 dB 域上，
        # 任何「抬升段」的 log-mean/mean 比恒 ≈1（实测噪声凸起 0.92–1.00、
        # 真实 OTA FM 0.77、单音 0.04），旧实现让形状判据退化成
        # 「带宽 < 20 kHz 即 FM/模拟」，噪声与信号同标 FM。
        seg_power = np.power(10.0, seg / 10.0)
        flatness = float(np.exp(np.mean(np.log(np.maximum(seg_power, 1e-30)))) /
                         max(np.mean(seg_power), 1e-30))
        out.append({
            "center_hz": float(freqs[peak_idx]),
            "peak_power_db": float(peak_val),
            "bandwidth_hz": float(bw_hz),
            "occupied_bins": int(end - start),
            "snr_db": float(peak_val - noise_floor),
            "spectral_flatness": flatness,
            "type": classify_signal(bw_hz, peak_val - noise_floor, flatness),
        })

    @staticmethod
    def _demod_fm(iq: np.ndarray, fs: float, max_deviation: float = 75000.0) -> np.ndarray:
        phase = np.angle(iq)
        dphase = np.diff(np.unwrap(phase))
        audio = dphase / (2.0 * np.pi) * fs
        audio = np.clip(audio / max_deviation, -1.0, 1.0)
        return audio

    @staticmethod
    def _demod_am(iq: np.ndarray) -> np.ndarray:
        return np.abs(iq)


def analyze_iq(iq: np.ndarray, sample_rate: float, center_hz: float = 0.0,
               fft_size: int = 2048, threshold_db: float = 6.0) -> ChannelAnalysis:
    """便捷函数：一段 IQ 直接分析。"""
    return ChannelAnalyzer(fft_size=fft_size, threshold_db=threshold_db).analyze(
        iq, sample_rate, center_hz)
