# SPDX-License-Identifier: MIT
"""DSB-SC 乘积检波解调（纯 numpy，状态化流式）。

管线（对照 demod_am.py 的包络检波）：
    1) 乘积检波：audio = 2 * Re(IQ)   —— 零中频下复基带乘单位载波
    2) 直流阻断：dcBlock(audio)
    3) 低通 FIR：截止 = bandwidth/2

为什么不是包络检波：DSB-SC（抑制载波双边带）没有载波分量，|IQ| 检波会把
两个边带折叠成同一种失真（平方律检波的过调制失真）；乘积检波取实部才能
无失真还原双边带音频。零中频复基带乘 1（DC 载波）即得乘积检波输出，
故无需显式载波恢复——这也是 SDR++ DSB 模式的同机制做法（其 8 模拟模式
中 DSB 一档此前 MBDSDR 缺席，2026-10-14 补上，SDR++ 审计缺口 #1 销账）。

注意：输出幅度约为同信号 AM 包络检波的 2 倍（乘积检波增益 2），属预期，
后续 AGC/音量链路统一归一。
"""
from __future__ import annotations
import numpy as np
from mbdsdr_ai.channelizer import lowpass_taps


class DCBlocker:
    """单极点直流阻断器（与 demod_am.py 同实现的轻量副本）。

    y[n] = x[n] - x[n-1] + R·y[n-1]。
    """

    def __init__(self, rate: float = 0.0066):
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


class DemodDSB:
    """DSB-SC 乘积检波器（2*Re(IQ) -> DC block -> LPF）。

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
        # 1) 乘积检波：2 * Re(IQ)（零中频单位载波）
        audio = 2.0 * z.real
        # 2) 直流阻断
        audio = self.dcblock.process(audio)
        # 3) 低通（与 demod_am 相同的 FIR + 尾状态保留）
        y = np.convolve(audio, self._lpf, mode="full")
        out = y[: len(audio)]
        tail = y[len(audio):]
        self._z = np.concatenate([self._z[len(tail):], tail]) if len(tail) else self._z
        return out.astype(np.float32)
