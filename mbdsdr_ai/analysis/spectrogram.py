# SPDX-License-Identifier: MIT
"""
时频分析（Spectrogram）
=======================

依据经典 STFT 时频分析方法独立实现（inspectrum 仅作技术参考，本仓未包含其源代码）：
- 单帧谱：加窗 → FFT → fftshift → dB。
- Tuner：中心频率 + deviation（半带宽）。
- 选框测量：选框时间段 → 持续时间 = sample_count / rate；导出选框样本。

本模块用 numpy 实现 STFT，提供：
- :class:`Spectrogram` — 计算时频谱。
- :class:`Selection` — 选框测量结果（中心频率 / 带宽 / 持续时间 / IQ 样本）。

AI 增强：在选框上可直接跑 :class:`ModulationClassifier`，输出建议解调参数。
"""

from __future__ import annotations

import dataclasses
from typing import Optional, Tuple

import numpy as np


@dataclasses.dataclass
class Selection:
    """选框测量结果。"""
    t_start: float          # 秒
    t_end: float            # 秒
    f_low: float            # Hz（相对中心）
    f_high: float           # Hz
    center_freq: float      # Hz
    bandwidth: float        # Hz
    duration: float         # 秒
    iq: np.ndarray          # 选框内的复基带样本（已下变频）
    sample_rate: float      # iq 的采样率

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"Selection(t={self.t_start:.3f}-{self.t_end:.3f}s, "
            f"f={self.center_freq/1e3:.1f}±{self.bandwidth/2e3:.1f}kHz, "
            f"dur={self.duration:.3f}s, n={len(self.iq)})"
        )


class Spectrogram:
    """STFT 时频分析。

    参数
    ----------
    iq : complex np.ndarray
        复基带样本。
    sample_rate : float
        采样率 Hz。
    fft_size : int
        FFT 点数（inspectrum 默认 512）。
    window : str
        窗函数（``np.hanning`` 等）。
    overlap : float
        帧重叠比例（0..1）。inspectrum zoom=1 时 stride=fft_size，即 overlap=0。
    """

    def __init__(
        self,
        iq: np.ndarray,
        sample_rate: float,
        fft_size: int = 512,
        window: str = "hann",
        overlap: float = 0.5,
    ):
        self.iq = np.asarray(iq, dtype=np.complex128)
        self.sample_rate = float(sample_rate)
        self.fft_size = int(fft_size)
        self.overlap = float(overlap)

        win_func = {
            "hann": np.hanning,
            "hamming": np.hamming,
            "blackman": np.blackman,
            "rect": np.ones,
        }.get(window, np.hanning)
        self.window = win_func(self.fft_size).astype(np.float64)

        self.hop = max(1, int(self.fft_size * (1.0 - self.overlap)))

        # 预计算时频图
        self._times, self._freqs, self._power_db = self._compute_stft()

    # ------------------------------------------------------------------
    # STFT
    # ------------------------------------------------------------------

    def _compute_stft(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        n = len(self.iq)
        if n < self.fft_size:
            return (np.array([0.0]),
                    np.fft.fftshift(np.fft.fftfreq(self.fft_size, d=1.0 / self.sample_rate)),
                    np.full((self.fft_size, 1), -100.0))

        n_frames = 1 + (n - self.fft_size) // self.hop
        # 预分配：(freq_bins, n_frames)
        power = np.zeros((self.fft_size, n_frames), dtype=np.float64)
        for i in range(n_frames):
            start = i * self.hop
            frame = self.iq[start:start + self.fft_size] * self.window
            spectrum = np.fft.fftshift(np.fft.fft(frame))
            mag2 = np.abs(spectrum) ** 2
            power[:, i] = mag2

        # dB：10 log10（inspectrum:311 logMultiplier = 10/log2(10)）
        power_db = 10.0 * np.log10(power / (np.max(power) + 1e-12) + 1e-12)

        times = (np.arange(n_frames) * self.hop + self.fft_size / 2.0) / self.sample_rate
        freqs = np.fft.fftshift(np.fft.fftfreq(self.fft_size, d=1.0 / self.sample_rate))
        return times, freqs, power_db

    # ------------------------------------------------------------------
    # 访问器
    # ------------------------------------------------------------------

    @property
    def times(self) -> np.ndarray:
        return self._times

    @property
    def freqs(self) -> np.ndarray:
        return self._freqs

    @property
    def power_db(self) -> np.ndarray:
        """时频谱，shape (freq_bins, n_time_frames)。"""
        return self._power_db

    def energy_at(self, t: float, f: float) -> float:
        """查询 (t, f) 处的功率 dB。"""
        ti = int(np.argmin(np.abs(self._times - t)))
        fi = int(np.argmin(np.abs(self._freqs - f)))
        return float(self._power_db[fi, ti])

    # ------------------------------------------------------------------
    # 选框测量
    # ------------------------------------------------------------------

    def select(self, t_start: float, t_end: float,
               f_low: float, f_high: float) -> Selection:
        """选框测量。

        参数为秒和 Hz（相对基带中心）。返回 :class:`Selection`，内含
        下变频后的 IQ 样本（选框样本导出，通用时频分析做法）。
        """
        # 时间 → 样本索引
        s_start = int(max(0, t_start * self.sample_rate))
        s_end = int(min(len(self.iq), t_end * self.sample_rate))
        if s_end <= s_start:
            raise ValueError(f"无效时间范围: {t_start}-{t_end}s")

        band = self.iq[s_start:s_end].copy()

        # 下变频：把 f_low..f_high 搬到基带
        center = (f_low + f_high) / 2.0
        t = np.arange(len(band)) / self.sample_rate
        band = band * np.exp(-2j * np.pi * center * t)

        # 简单低通：取选框带宽一半的截止
        bw = f_high - f_low
        # 频域窗
        spec = np.fft.fft(band)
        freqs = np.fft.fftfreq(len(band), d=1.0 / self.sample_rate)
        mask = np.abs(freqs) <= bw / 2.0
        band = np.fft.ifft(spec * mask)

        return Selection(
            t_start=t_start,
            t_end=t_end,
            f_low=f_low,
            f_high=f_high,
            center_freq=center,
            bandwidth=bw,
            duration=t_end - t_start,
            iq=band,
            sample_rate=self.sample_rate,
        )

    def auto_select_peak(self, time_pad: float = 0.01) -> Selection:
        """AI 增强：自动找能量峰值区域并选框。"""
        # 在时频谱上找最大功率点
        idx = np.unravel_index(np.argmax(self._power_db), self._power_db.shape)
        f_idx, t_idx = idx
        peak_t = float(self._times[t_idx])
        peak_f = float(self._freqs[f_idx])

        # 找 -10dB 轮廓
        threshold = self._power_db[f_idx, t_idx] - 10.0
        row = self._power_db[f_idx, :] > threshold
        cols = np.where(row)[0]
        if len(cols) > 0:
            t_lo = self._times[cols[0]]
            t_hi = self._times[cols[-1]]
        else:
            t_lo = max(0, peak_t - time_pad)
            t_hi = peak_t + time_pad

        col = self._power_db[:, t_idx] > threshold
        rows = np.where(col)[0]
        if len(rows) > 0:
            f_lo = self._freqs[rows[0]]
            f_hi = self._freqs[rows[-1]]
        else:
            f_lo = peak_f - self.sample_rate / self.fft_size
            f_hi = peak_f + self.sample_rate / self.fft_size

        return self.select(t_lo, t_hi, f_lo, f_hi)
