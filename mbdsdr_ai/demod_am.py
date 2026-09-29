# SPDX-License-Identifier: MIT
"""AM 包络检波解调（纯 numpy，状态化流式）。

管线：
    1) 包络检波：audio = |IQ|
    2) 直流阻断：dcBlock(audio)
    3) 可选音频 AGC（这里默认关闭）
    4) 低通 FIR：截止 = bandwidth/2

注意：这是「|IQ| 包络检波」，不是乘积检波；无显式载波恢复（包络检波本身
适用于带载波 AM）。AGC 在信道化之后、解调之前的链路上完成。
"""
from __future__ import annotations
import numpy as np
from mbdsdr_ai.channelizer import lowpass_taps


class DCBlocker:
    """单极点直流阻断器。

    标准一阶高通：y[n] = x[n] - x[n-1] + R·y[n-1]，R≈0.995。
    
    """

    def __init__(self, rate: float = 0.0066):
        # R = 1 - rate（rate 为每样本阻断系数）
        self.R = max(0.0, min(0.99999, 1.0 - float(rate)))
        self._x1 = 0.0
        self._y1 = 0.0

    def reset(self) -> None:
        self._x1 = 0.0
        self._y1 = 0.0

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        out = np.empty_like(x)
        x1 = self._x1
        y1 = self._y1
        for i in range(len(x)):
            out[i] = x[i] - x1 + self.R * y1
            x1 = x[i]
            y1 = out[i]
        self._x1 = x1
        self._y1 = y1
        return out


class DemodAM:
    """AM 包络检波器。

    Parameters
    ----------
    if_sr : IF 复采样率 Hz，默认 15000。
    bandwidth : 双边带宽 Hz，默认 10000；LPF 截止 = bandwidth/2。
    """

    def __init__(self, if_sr: float = 15000.0, bandwidth: float = 10000.0):
        self.if_sr = float(if_sr)
        self.bandwidth = float(bandwidth)
        self.dcblock = DCBlocker(rate=100.0 / self.if_sr)

        cutoff = self.bandwidth / 2.0
        self._lpf = lowpass_taps(cutoff, cutoff * 0.1, self.if_sr)
        self._z = np.zeros(len(self._lpf) - 1)

    def reset(self) -> None:
        self.dcblock.reset()
        self._z = np.zeros(len(self._lpf) - 1)

    def process(self, iq: np.ndarray) -> np.ndarray:
        """复 IF → 实音频（if_sr 采样率）。"""
        z = np.asarray(iq, dtype=np.complex128)
        # 1) 包络检波 |IQ|
        audio = np.abs(z)
        # 2) 直流阻断
        audio = self.dcblock.process(audio)
        # 4) 低通
        y = np.convolve(audio, self._lpf, mode="full")
        out = y[: len(audio)]
        tail = y[len(audio):]
        self._z = np.concatenate([self._z[len(tail):], tail]) if len(tail) else self._z
        return out.astype(np.float32)
