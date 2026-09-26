"""
MBDSDR AI 内核 - IQ 域自适应降噪（ANR）
=========================================

注意：本项目已有 ``mbdsdr_ai/anr.py``（音频域 float32 单声道谱减，
类名 ``SpectralSubtractionANR``）。本文件是**新增的 IQ 域（complex64）版本**，
不改原文件，对应任务要求的 ``ANR`` 类。

对照上游（见 docs/learn/sdrpp_gnuradio_port.md）：
  - SDR++ noise_reduction 模块   <-> repos/sdrpp/core/src/dsp/noise_reduction/
  - 谱减法（Boll 1979）：FFT → 减噪声底 → 保相位 → IFFT → 重叠相加

API 约定（任务要求）：
  - ``ANR(fft_size=...)``
  - ``process(iq: np.ndarray) -> np.ndarray``（complex64 进 complex64 出）
  - ``set_noise_estimate(iq)``：手动喂一段纯静默 IQ 估噪声底
  - ``set_strength(0.0..1.0)``：减噪强度
  - 自动噪声底跟踪（AI 增强）：无需手动标静默，用滑动百分位

红线：
  * 输入空/None 原样返回，不造假；
  * 噪声底未估计时（既没手动 set 也没自动跟踪到），输出 == 输入；
  * 确定性：固定窗、固定算法、无随机数，可复现 SNR 改善。
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class ANR:
    """IQ 域谱减法自适应降噪（complex64 进 complex64 出）。

    Parameters:
        fft_size:   FFT 帧长（样本数，必须 2 的幂）。
        overlap:    相邻帧重叠比例（0.0..0.5），默认 0.5。
        strength:   初始减噪强度 0.0..1.0。
        noise_percentile: 自动噪声跟踪百分位（0..100），默认 10（取下包络）。
    """

    def __init__(
        self,
        fft_size: int = 1024,
        overlap: float = 0.5,
        strength: float = 0.5,
        noise_percentile: float = 10.0,
    ):
        if fft_size <= 0 or (fft_size & (fft_size - 1)) != 0:
            raise ValueError("fft_size 必须是 2 的幂")
        self.fft_size = int(fft_size)
        self.hop = max(1, int(round(fft_size * (1.0 - overlap))))
        self.set_strength(strength)
        self.noise_percentile = float(noise_percentile)

        # 汉宁窗（确定性）
        self._win = np.hanning(fft_size).astype(np.float64)
        # 手动噪声底（幅度谱）
        self._noise_mag: Optional[np.ndarray] = None
        # 自动跟踪噪声底
        self._auto_noise: Optional[np.ndarray] = None
        # OLA 尾缓冲
        self._ola_tail: Optional[np.ndarray] = None

    # -- 配置 ---------------------------------------------------------------
    def set_strength(self, s: float) -> None:
        """减噪强度 0.0..1.0（0=不减，1=全减）。"""
        self.strength = float(min(1.0, max(0.0, s)))

    def set_noise_estimate(self, iq: np.ndarray) -> None:
        """手动喂一段纯静默 IQ，估计噪声幅度谱。"""
        a = np.asarray(iq, dtype=np.complex64).ravel()
        if a.size == 0:
            return
        if a.size < self.fft_size:
            a = np.pad(a, (0, self.fft_size - a.size))
        n = self.fft_size
        hop = self.hop
        nframes = 1 + (a.size - n) // hop
        acc: Optional[np.ndarray] = None
        cnt = 0
        for i in range(nframes):
            seg = a[i * hop:i * hop + n] * self._win
            sp = np.fft.fft(seg)
            mag = np.abs(sp)
            acc = mag if acc is None else acc + mag
            cnt += 1
        if cnt:
            self._noise_mag = (acc / cnt).astype(np.float64)

    def reset(self) -> None:
        """清空状态。"""
        self._noise_mag = None
        self._auto_noise = None
        self._ola_tail = None

    # -- 核心处理 -----------------------------------------------------------
    def process(self, iq: np.ndarray) -> np.ndarray:
        """处理一段 IQ，返回降噪后的 IQ。

        谱减：
            X = FFT(x * win)
            N = noise_floor（手动 > 自动百分位跟踪）
            |Y| = max(|X| - strength * N, floor * N)
            y = IFFT(|Y| * exp(j*angle(X)))
            重叠相加（OLA）
        """
        a = np.asarray(iq, dtype=np.complex64).ravel()
        if a.size == 0:
            return iq
        # strength=0：直通，不造假也不破坏
        if self.strength <= 0.0:
            return a.copy()

        n = self.fft_size
        hop = self.hop

        # 短输入：不足一帧，原样返回（不造假）
        if a.size < n:
            return a

        out = np.zeros(a.size, dtype=np.complex64)
        out_norm = np.zeros(a.size, dtype=np.float64)
        nframes = 1 + (a.size - n) // hop

        for i in range(nframes):
            off = i * hop
            seg = a[off:off + n] * self._win
            sp = np.fft.fft(seg)
            mag = np.abs(sp)
            phase = np.angle(sp)

            # 取噪声底
            noise = self._current_noise_floor(mag)

            # 谱减：过减因子 = 1.0 + strength * 2.0（strength=1 -> 3x 过减）
            # 这是 Boll 1979 / Berouti 1979 的经典做法：过减比 1 大才能压得住音乐噪声
            alpha = 1.0 + 2.0 * self.strength
            reduced = mag - alpha * noise
            floor = 0.05 * noise
            reduced = np.maximum(reduced, floor)

            # 保相位重建
            out_sp = reduced * np.exp(1j * phase)
            out_seg = np.fft.ifft(out_sp) * self._win

            end = off + n
            if end > out.size:
                out_seg = out_seg[:out.size - off]
                win_seg = self._win[:out.size - off]
            else:
                win_seg = self._win
            out[off:off + out_seg.size] += out_seg
            out_norm[off:off + win_seg.size] += win_seg

        # 归一化窗重叠增益
        np.maximum(out_norm, 1e-6, out=out_norm)
        out = out / out_norm
        return out.astype(np.complex64)

    def _current_noise_floor(self, mag: np.ndarray) -> np.ndarray:
        """手动 > 自动百分位跟踪。"""
        if self._noise_mag is not None:
            return self._noise_mag
        # 自动：本帧幅度谱的下百分位
        p = float(np.percentile(mag, self.noise_percentile))
        if self._auto_noise is None:
            self._auto_noise = np.full_like(mag, p, dtype=np.float64)
        else:
            # 慢 IIR 跟随，避免信号帧污染
            self._auto_noise = 0.9 * self._auto_noise + 0.1 * p
        return self._auto_noise
