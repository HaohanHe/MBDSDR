# SPDX-License-Identifier: MIT
"""
频谱累加器（FFT 平均 / 峰值保持）
=====================================

对照 SDR++ ``core/src/gui/widgets/waterfall.cpp``：

- 指数平滑（EMA）：waterfall.cpp:914-919
    smoothingBuf = alpha * new + (1-alpha) * smoothingBuf
  其中 alpha 由 setFFTSmoothingSpeed(speed) 直接给（waterfall.cpp:1192-1193
  fftSmoothingAlpha = speed; fftSmoothingBeta = 1.0 - speed）。
- 峰值保持（peak hold）：waterfall.cpp:935-937
    latestFFTHold[i] = max(latestFFT[i], latestFFTHold[i] - fftHoldSpeed)
  即峰值随时间缓慢衰减（holdSpeed 是每帧下降 dB 数），新的更高峰值会取代。

本模块是纯数据层：输入一帧 FFT 幅度谱（dB），输出平滑后的谱与峰值保持谱。
不接 GUI、不接声卡。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class AccumulatorConfig:
    """累加器配置。"""

    size: int                       # 每帧 FFT bin 数
    avg_enabled: bool = True        # 是否启用指数平均
    avg_alpha: float = 0.3          # EMA 系数（新帧权重），越大跟随越快
    peak_enabled: bool = True       # 是否启用峰值保持
    peak_decay_db: float = 0.5      # 每帧峰值衰减 dB（对照 fftHoldSpeed）
    avg_frames: int = 0             # 若 >0，用固定帧数块平均（覆盖 EMA）


class SpectrumAccumulator:
    """对每帧 dB 谱做指数平均 + 峰值保持。

    Parameters
    ----------
    size : int
        FFT bin 数（必须与每帧输入长度一致）。
    avg_alpha : float
        EMA 新帧权重（0~1）。对照 SDR++ setFFTSmoothingSpeed。
    peak_decay_db : float
        峰值保持每帧衰减 dB。对照 SDR++ fftHoldSpeed。
    """

    def __init__(self, size: int,
                 avg_alpha: float = 0.3,
                 peak_decay_db: float = 0.5) -> None:
        if size <= 0:
            raise ValueError("size must be positive")
        self.cfg = AccumulatorConfig(
            size=size, avg_alpha=float(avg_alpha),
            peak_decay_db=float(peak_decay_db),
        )
        self._avg: Optional[np.ndarray] = None       # 平滑谱（dB）
        self._peak: Optional[np.ndarray] = None       # 峰值保持谱（dB）
        self._block_buf: list = []                   # 固定帧数块平均缓冲
        self._frames_since_reset: int = 0

    # ------------------------------------------------------------------
    def reset(self) -> None:
        """清空平均/峰值状态（切频率/切带宽后调用）。"""
        self._avg = None
        self._peak = None
        self._block_buf = []
        self._frames_since_reset = 0

    # ------------------------------------------------------------------
    def push(self, frame_db: np.ndarray) -> np.ndarray:
        """喂入一帧 dB 谱，返回平滑后的谱。

        同时更新内部峰值保持缓冲（用 :meth:`peak` 取回）。
        """
        frame_db = np.asarray(frame_db, dtype=np.float64).ravel()
        if frame_db.size != self.cfg.size:
            raise ValueError(
                f"frame size {frame_db.size} != configured {self.cfg.size}")

        out = frame_db.copy()

        # ---- 平均模式 ----
        if self.cfg.avg_frames and self.cfg.avg_frames > 0:
            # 固定帧数块平均（对照 power-of-N 平均）
            self._block_buf.append(frame_db)
            if len(self._block_buf) >= self.cfg.avg_frames:
                out = np.mean(np.stack(self._block_buf, axis=0), axis=0)
                self._block_buf = []
        elif self.cfg.avg_enabled:
            alpha = self.cfg.avg_alpha
            if self._avg is None:
                self._avg = frame_db.copy()
            else:
                # EMA：对照 waterfall.cpp:916-919
                self._avg = alpha * frame_db + (1.0 - alpha) * self._avg
            out = self._avg.copy()

        # ---- 峰值保持 ----
        if self.cfg.peak_enabled:
            if self._peak is None:
                self._peak = frame_db.copy()
            else:
                # 对照 waterfall.cpp:937
                self._peak = np.maximum(frame_db,
                                        self._peak - self.cfg.peak_decay_db)

        self._frames_since_reset += 1
        return out

    # ------------------------------------------------------------------
    @property
    def average(self) -> Optional[np.ndarray]:
        """当前平滑谱（未 push 过返回 None）。"""
        return None if self._avg is None else self._avg.copy()

    @property
    def peak(self) -> Optional[np.ndarray]:
        """当前峰值保持谱（未 push 过返回 None）。"""
        return None if self._peak is None else self._peak.copy()

    @property
    def frames(self) -> int:
        """自上次 reset 以来累计 push 帧数。"""
        return self._frames_since_reset

    # ------------------------------------------------------------------
    # AI 增强：自动选平均系数（按信号变化速率）
    # ------------------------------------------------------------------
    def auto_tune(self, spectral_flatness: float) -> None:
        """根据频谱平坦度自动调整 EMA alpha。

        spectral_flatness ∈ [0,1]，越接近 1 越像噪声（平坦）。
        - 平坦（噪声底）→ 增大 alpha，让平均更快跟踪噪声底
        - 尖锐（强单音）→ 减小 alpha，保留细峰不糊掉
        """
        f = float(min(1.0, max(0.0, spectral_flatness)))
        self.cfg.avg_alpha = float(0.1 + 0.5 * f)
