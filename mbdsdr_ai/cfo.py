# -*- coding: utf-8 -*-
"""
载波频率偏移（Carrier Frequency Offset, CFO）估计与校正。

面向低成本 SDR（如 RTL2832U 无 TCXO，频偏可达数十 ppm）的自动频偏校正技能。
人是中心：模型通过工具对话式触发，而不是固定菜单按钮；这里提供确定性的信号处理内核。

两级估计（工程标准的 coarse→fine 载波恢复）：
  1. 粗估 estimate_cfo_fft：加 Hann 窗 FFT，峰值 + 抛物线亚 bin 插值，无模糊、
     但精度约为 bin 宽度（fs/N）量级；
  2. 精估 estimate_cfo_kay：Kay(1989) 自回归相位差分加权估计，对单音/导频是近似
     最大似然估计，高 SNR 下方差随 N^-3 下降，远精于 FFT bin；要求残余频偏相邻
     样本相位差小于 π（粗估补偿后必然满足）。
校正 correct_cfo：复混频 x·exp(-j2π f̂ n/fs)。

适用对象：未调制载波、CW、FM 载波、卫星信标、带导频/同步字的信号（已知标称频率
f_expected）。对完全未知且无离散载波的调制信号，应改用数据辅助（同步字相关）或
调制非线性（M-PSK 的 M 次方去调制）方法，不在本模块范围。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


def _parabolic_subbin(p: np.ndarray, k: int) -> float:
    """峰值三点抛物线插值，返回相对峰 bin 的亚 bin 偏移（约 -0.5~0.5）。"""
    if not (0 < k < len(p) - 1):
        return 0.0
    y0, y1, y2 = float(p[k - 1]), float(p[k]), float(p[k + 1])
    denom = y0 - 2.0 * y1 + y2
    if abs(denom) < 1e-15:
        return 0.0
    return 0.5 * (y0 - y2) / denom


def estimate_cfo_fft(x, fs: float, f_expected: float = 0.0,
                     search_hz: Optional[float] = None):
    """频域峰值粗估。

    返回 (offset_hz, peak_hz)：peak_hz 为实测峰值频率，offset_hz=peak_hz-f_expected。
    search_hz 给定时只在 f_expected±search_hz 邻域找峰（抗强邻台干扰）。
    """
    x = np.asarray(x, dtype=np.complex128)
    n = len(x)
    win = np.hanning(n)
    spec = np.fft.fftshift(np.fft.fft(x * win))
    power = np.abs(spec) ** 2
    freqs = np.fft.fftshift(np.fft.fftfreq(n, d=1.0 / fs))
    if search_hz is not None:
        band = np.abs(freqs - f_expected) <= search_hz
        if not np.any(band):
            raise ValueError("search_hz 邻域内无频率 bin")
        k = int(np.argmax(np.where(band, power, -np.inf)))
    else:
        k = int(np.argmax(power))
    sub = _parabolic_subbin(power, k)
    bin_hz = fs / n
    peak_hz = float(freqs[k] + sub * bin_hz)
    return peak_hz - float(f_expected), peak_hz


def estimate_cfo_kay(x, fs: float) -> float:
    """Kay 相位差分频偏精估（单音/导频近似 MLE），单位 Hz。"""
    x = np.asarray(x, dtype=np.complex128)
    m = len(x) - 1
    if m < 2:
        return 0.0
    dphi = np.angle(x[1:] * np.conj(x[:-1]))
    idx = np.arange(m)
    # Kay 抛物窗，归一化为权重和 1
    w = 1.5 * (1.0 - ((idx - (m - 1) / 2.0) / ((m + 1) / 2.0)) ** 2)
    w = w / np.sum(w)
    return float(fs / (2.0 * np.pi) * np.sum(w * dphi))


def correct_cfo(x, fs: float, offset_hz: float):
    """复混频补偿频偏：x·exp(-j2π f̂ n/fs)。"""
    x = np.asarray(x, dtype=np.complex128)
    n = np.arange(len(x))
    return x * np.exp(-1j * 2.0 * np.pi * offset_hz * n / fs)


@dataclass
class CFOResult:
    offset_hz: float          # 估计总频偏（相对 f_expected）
    coarse_hz: float          # FFT 粗估
    fine_hz: float            # Kay 精估（粗补后的残余，未启用时为 0）
    residual_hz: float        # 完全校正后再估计的残余
    f_expected: float
    fs: float
    fine_applied: bool = False  # Kay 精估是否通过相干性门限被采用
    coherence: float = 0.0      # 粗补后相邻样本相位差相干性 0~1

    @property
    def offset_ppm_100mhz(self) -> float:
        """以 100 MHz 载波为参考的 ppm（便于对照 RTL-SDR 晶体误差）。"""
        return self.offset_hz / 100e6 * 1e6


def _phase_coherence(x) -> float:
    """相邻样本相位差的圆相干性 |mean e^{jΔφ}|：高 SNR 单音趋近 1，噪声趋近 0。"""
    x = np.asarray(x, dtype=np.complex128)
    dphi = np.angle(x[1:] * np.conj(x[:-1]))
    return float(np.abs(np.mean(np.exp(1j * dphi))))


def estimate_and_correct(x, fs: float, f_expected: float = 0.0,
                         search_hz: Optional[float] = None,
                         fine: bool = True, coherence_threshold: float = 0.9):
    """粗估→补偿→（相干性达标才）Kay 精估→再补偿。

    Kay 精估存在低 SNR 门限：噪声主导相位差时会把粗估带偏，因此用粗补后信号的
    相位差相干性与 |fine|≤0.5 bin 双重判据门控，不达标就稳健回退到 FFT 粗估。
    """
    x = np.asarray(x, dtype=np.complex128)
    n = len(x)
    bin_hz = fs / n
    coarse, _ = estimate_cfo_fft(x, fs, f_expected=f_expected, search_hz=search_hz)
    x_coarse = correct_cfo(x, fs, coarse)
    coh = _phase_coherence(x_coarse)
    fine_raw = estimate_cfo_kay(x_coarse, fs) if fine else 0.0
    if fine and coh >= coherence_threshold and abs(fine_raw) <= 0.5 * bin_hz:
        fine_off, applied = fine_raw, True
    else:
        fine_off, applied = 0.0, False
    total = coarse + fine_off
    corrected = correct_cfo(x, fs, total)
    # 残余用稳健的 FFT 邻域估计（低 SNR 下 Kay 不可信）
    residual, _ = estimate_cfo_fft(corrected, fs, f_expected=0.0,
                                   search_hz=max(bin_hz, abs(fine_off) + bin_hz))
    res = CFOResult(offset_hz=float(total), coarse_hz=float(coarse),
                    fine_hz=float(fine_off), residual_hz=float(residual),
                    f_expected=float(f_expected), fs=float(fs),
                    fine_applied=applied, coherence=coh)
    res.corrected = corrected
    return res
