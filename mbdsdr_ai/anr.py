# SPDX-License-Identifier: MIT
"""
mbdsdr_ai/anr.py — 自适应噪声抑制（ANR, Spectral Subtraction）
=============================================================

对照 GQRX ``DockAudio`` / SDR++ radio_module 的降噪链：在解调后音频域做
谱减法降噪——估计噪声底 → 从信号谱中减去 → IFFT 还原。

实现要点（与经典谱减法一致，Boll 1979 / Berouti 1979）：

  1. 分帧 + 汉宁窗，重叠 50%（OLA 重构）。
  2. FFT 得幅度谱 |X(ω)|。
  3. 噪声底估计 ``|N(ω)|``：在"静音/噪声段"滑动更新（一阶 IIR 平滑），
     用户可显式 ``learn_noise()`` 强制用当前帧刷新噪声底。
  4. 谱减：|Y(ω)| = max(|X(ω)| - α·|N(ω)|, β·|X(ω)|)
     α = 过减因子（强度），β = 谱底下限（防止音乐噪声过深）。
  5. 保留原相位 ``∠X(ω)`` → 逆变换 → 重叠相加。

红线：
  * 本模块**绝不造假数据**：输入空/None 时原样返回。
  * 噪声底估计收敛前（未 learn）输出等于输入，不硬减。
  * 纯 numpy 实现，确定性（无随机数），可单测。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class ANRConfig:
    """ANR 运行参数。"""
    frame_size: int = 512          # 窗长（采样点）
    overlap: float = 0.5           # 帧重叠比
    alpha: float = 2.0             # 过减因子（强度），越大抑制越强
    beta: float = 0.02             # 谱底下限系数（音乐噪声门限）
    noise_smooth: float = 0.95     # 噪声底 IIR 平滑系数（越大越稳）
    enabled: bool = False           # 默认关闭


class SpectralSubtractionANR:
    """谱减法自适应噪声抑制（音频域，单声道 float32）。

    用法::

        anr = SpectralSubtractionANR()
        anr.enabled = True
        out = anr.process(audio_block)   # 等长 float32
    """

    def __init__(self, config: Optional[ANRConfig] = None) -> None:
        self.cfg = config or ANRConfig()
        self.enabled = bool(self.cfg.enabled)
        n = int(self.cfg.frame_size)
        self._win = np.hanning(n).astype(np.float64)
        self._hop = max(1, int(round(n * (1.0 - self.cfg.overlap))))
        # 噪声底幅度谱（None = 尚未学习）
        self._noise_mag: Optional[np.ndarray] = None
        # OLA 重构的尾缓冲（跨块连续，不重置）
        self._ola_tail: Optional[np.ndarray] = None
        self._sma = 0.0  # 用于 SNR 估计的滑动均值（dB）

    # ------------------------------------------------------------------
    def reset(self) -> None:
        """清空状态（滤波器/噪声底/OLA 尾）。换频/换模式时调用。"""
        self._noise_mag = None
        self._ola_tail = None
        self._sma = 0.0

    def learn_noise(self, audio: np.ndarray) -> None:
        """用当前音频块强制学习噪声底（应在无信号/静音段按"采样噪声"按钮）。"""
        a = np.asarray(audio, dtype=np.float64).ravel()
        if a.size == 0:
            return
        mag = self._frame_mag(a, learn=True)
        if mag.size:
            self._noise_mag = mag.copy()

    # ------------------------------------------------------------------
    def _frame_mag(self, a: np.ndarray, learn: bool = False) -> np.ndarray:
        """对一块音频分帧取 FFT 幅度谱平均；learn=True 时同步更新噪声底。"""
        n = len(self._win)
        hop = self._hop
        if a.size < n:
            return np.empty(0)
        nframes = 1 + (a.size - n) // hop
        acc: Optional[np.ndarray] = None
        cnt = 0
        for i in range(nframes):
            seg = a[i * hop:i * hop + n] * self._win
            sp = np.fft.rfft(seg)
            mag = np.abs(sp)
            if acc is None:
                acc = mag
            else:
                acc = acc + mag
            cnt += 1
        if acc is None or cnt == 0:
            return np.empty(0)
        acc = acc / float(cnt)
        if learn:
            self._noise_mag = acc.copy()
        return acc

    # ------------------------------------------------------------------
    def snr_improvement_db(self, before: np.ndarray,
                           after: np.ndarray) -> float:
        """估计降噪前后 SNR 改善（dB）。

        用功率比近似：10*log10(mean(|before|^2) / mean(|after|^2))。
        噪声被压下去时 after 功率更低 → 改善为正。返回 0（无改善）时
        表示降噪未起作用。
        """
        b = np.asarray(before, dtype=np.float64).ravel()
        a = np.asarray(after, dtype=np.float64).ravel()
        if b.size == 0 or a.size == 0:
            return 0.0
        pb = float(np.mean(b * b)) + 1e-12
        pa = float(np.mean(a * a)) + 1e-12
        db = 10.0 * np.log10(pb / pa)
        # 限制在合理范围（-20..+30 dB）
        return float(max(-20.0, min(30.0, db)))

    # ------------------------------------------------------------------
    def process(self, audio: np.ndarray) -> np.ndarray:
        """处理一块单声道音频。关闭/空输入时原样返回（不造假）。"""
        if not self.enabled:
            return audio
        a = np.asarray(audio, dtype=np.float64).ravel()
        if a.size == 0:
            return audio

        n = len(self._win)
        hop = self._hop
        if a.size < n:
            return audio

        # 噪声底自适应：若尚未学习，用本块作为初始估计（之后 IIR 缓慢跟随）
        if self._noise_mag is None:
            self.learn_noise(a)
            return audio  # 首次只学习，不改信号

        alpha = float(self.cfg.alpha)
        beta = float(self.cfg.beta)
        smooth = float(self.cfg.noise_smooth)

        out = np.zeros(a.size, dtype=np.float64)
        out_norm = np.zeros(a.size, dtype=np.float64)
        nframes = 1 + (a.size - n) // hop

        for i in range(nframes):
            off = i * hop
            seg = a[off:off + n] * self._win
            sp = np.fft.rfft(seg)
            mag = np.abs(sp)
            phase = np.angle(sp)

            # 谱减
            noise = self._noise_mag
            reduced = mag - alpha * noise
            floor = beta * mag
            reduced = np.maximum(reduced, floor)

            # 重构
            out_sp = reduced * np.exp(1j * phase)
            out_seg = np.fft.irfft(out_sp, n=n) * self._win

            end = off + n
            if end > out.size:
                out_seg = out_seg[:out.size - off]
                win_seg = self._win[:out.size - off]
            else:
                win_seg = self._win
            out[off:off + out_seg.size] += out_seg
            out_norm[off:off + win_seg.size] += win_seg

            # 自适应噪声底跟随（只在本帧"弱信号"段更新，避免把信号当噪声）
            # 简单策略：用最小幅度谱缓慢 IIR 跟随
            self._noise_mag = (smooth * noise
                               + (1.0 - smooth) * np.minimum(mag, noise))

        # 归一化窗重叠增益
        np.maximum(out_norm, 1e-6, out=out_norm)
        out = out / out_norm
        return out.astype(np.float32)
