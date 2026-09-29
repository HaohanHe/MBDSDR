# SPDX-License-Identifier: MIT
"""
音频有理重采样器（有状态、抗混叠 FIR）
=========================================

把解调后音频（典型 12 kHz / 24 kHz / 48 kHz 正交 IF）重采样到声卡原生率
（通常 48 kHz），供 AudioPlayer 播放。

实现依据（公开多速率 DSP 方法；SDR++/GQRX 仅作技术参考，本仓未包含其源代码）：
- 有理重采样：gcd 约分得到 interp/decim；多相 FIR 低通，
  截止频率 = min(in_sr, out_sr)/2，过渡带 = 截止*0.1；
  抽头整体乘以 interp（补偿插值插零的幅度损失）。
- 多相滤波器组：按相位选择子滤波响应，保证块间无边界断裂。
- 典型用法：解调器输出固定率（96 kHz 正交 IF 量级），再有理数重采样到声卡率。

与 audio_out.py 里临时用的 scipy.signal.resample_poly 的区别：
那个是「无状态、逐块」调用——每块独立，块与块之间滤波器历史丢失，
块边界会产生可闻的滴答/相位跳变。本类保留滤波器延迟线（zi）与多相
相位游标，跨块连续，适合实时流式喂数。

纯 numpy + scipy（scipy 仅用于 firwin 设计 FIR 系数；过滤本身用 numpy
卷积 + 自维护延迟线，无 scipy 运行时依赖也能工作）。
"""
from __future__ import annotations

from math import gcd
from typing import Tuple

import numpy as np


def _design_lowpass_taps(in_sr: float, out_sr: float,
                          up: int, down: int,
                          numtaps: int = 65) -> np.ndarray:
    """设计抗混叠低通 FIR 抽头。

    多相 FIR 低通设计（公开多速率 DSP 方法）：
        tapSamplerate  = intSamplerate * interp     （滤波在高率域做）
        tapBandwidth   = min(in,out)/2              （通带到奈奎斯特较小者）
        tapTransWidth  = tapBandwidth * 0.1
        rtaps = lowPass(tapBandwidth, tapTransWidth, tapSamplerate)
        for i: rtaps[i] *= interp                   （插值插零补偿）
    """
    high_rate = float(in_sr) * up
    cutoff = min(in_sr, out_sr) / 2.0
    # firwin 归一化频率：f / (high_rate/2)
    if high_rate <= 0:
        raise ValueError(f"非法高率 {high_rate}")
    nyq = high_rate / 2.0
    # 保证截止频率严格在 (0, 1) 归一化区间内
    cutoff = min(cutoff, nyq * 0.98)
    try:
        from scipy.signal import firwin
        taps = firwin(numtaps, cutoff / nyq, window="hann")
    except Exception:
        # scipy 不可用：窗 sinc 手工实现（Hann 窗）
        half = numtaps // 2
        n = np.arange(numtaps) - half
        # sinc
        with np.errstate(invalid="ignore", divide="ignore"):
            x = 2.0 * (cutoff / nyq) * n
            taps = np.where(n == 0, 1.0, np.sin(np.pi * x) / (np.pi * n))
        taps *= 0.5 * (1.0 - np.cos(2.0 * np.pi * np.arange(numtaps) / (numtaps - 1)))
    # 插值补偿：插 up 个零后信号能量变成 1/up，滤波增益需乘 up
    taps = taps * float(up)
    return taps.astype(np.float64)


class AudioResampler:
    """有状态有理重采样器（float32 音频）。

    Parameters
    ----------
    in_sr : float
        输入采样率 Hz（解调输出率，如 12000）。
    out_sr : float
        目标采样率 Hz（声卡原生率，如 48000）。

    用法（实时流式）::

        rs = AudioResampler(12000, 48000)
        for block in demod_audio_blocks():   # 每块 float32 (N,)
            out = rs.process(block)          # 重采样到 48k 的 float32
            player.write(out)

    线程安全：process() 非线程安全；应在单条音频消费线程里调用。
    """

    def __init__(self, in_sr: float, out_sr: float, numtaps: int = 65):
        in_sr = float(in_sr)
        out_sr = float(out_sr)
        if in_sr <= 0 or out_sr <= 0:
            raise ValueError(f"采样率必须为正: in={in_sr}, out={out_sr}")
        self.in_sr = in_sr
        self.out_sr = out_sr

        # 有理数约分（gcd 求 interp/decim）
        g = gcd(int(round(in_sr)), int(round(out_sr)))
        self.up = int(round(out_sr)) // g
        self.down = int(round(in_sr)) // g

        if self.up == 1 and self.down == 1:
            self.taps = np.ones(1, dtype=np.float64)
        else:
            self.taps = _design_lowpass_taps(in_sr, out_sr,
                                             self.up, self.down, numtaps)

        # ── 有状态：滤波延迟线 + 多相相位游标 ──
        # 多相相位寄存器与滤波历史延迟线（跨块连续）。
        # 延迟线保存上一块尾部 taps_len-1 个「高率域」样本。
        self._filter_delay = np.zeros(len(self.taps) - 1, dtype=np.float64)
        # 多相相位：当前在 up 相中的位置 [0, up)。
        # 每消费一个输入样本，相位前进 down 步；每累积满 up 就出一个输出样本。
        self._phase = 0

    def reset(self) -> None:
        """清空滤波器历史与相位游标（换频率/换模式后调用，避免旧数据串音）。"""
        self._filter_delay[:] = 0.0
        self._phase = 0

    @property
    def ratio(self) -> float:
        """输出/输入采样率比。"""
        return self.out_sr / self.in_sr

    def process(self, x: np.ndarray) -> np.ndarray:
        """处理一块输入音频，返回重采样后的 float32 块。

        块之间保留滤波器状态；首次调用前延迟线为零（相当于滤波器上电
        瞬态，输出头部会有 numtaps/up 个样本的建立过程，可接受）。
        """
        x = np.asarray(x, dtype=np.float64).ravel()
        if x.size == 0:
            return np.zeros(0, dtype=np.float32)

        if self.up == 1 and self.down == 1:
            return x.astype(np.float32)

        # ── 在高率域构造插值流：每个输入样本后插 up-1 个零 ──
        # 标准上采-滤波-下采路径：先插值（插零）→ 低通滤波 → 抽取。
        high = np.zeros(x.size * self.up, dtype=np.float64)
        high[0::self.up] = x

        # ── FIR 滤波（带历史延迟线）──
        # y[n] = sum_k taps[k] * high[n-k]，延迟线记住上一块尾部。
        n_taps = len(self.taps)
        # 把历史接到当前 high 前面做一次完整卷积，再截掉历史段
        extended = np.concatenate([self._filter_delay, high])
        filtered = np.convolve(extended, self.taps, mode="valid")
        # valid 模式输出长度 = len(extended) - n_taps + 1
        # 前 (n_taps-1) 个样本依赖旧历史，正是我们要的连续滤波输出。
        # 更新延迟线 = extended 尾部 n_taps-1 个样本（跨块保留）
        self._filter_delay = extended[-(n_taps - 1):].copy() if n_taps > 1 else np.zeros(0)

        # ── 抽取：按多相相位游标取输出样本 ──
        # 多相相位游标从 self._phase 继续，每 down 个高率样本出一个输出。
        out_indices = []
        # 当前相位在 filtered 数组里的起始位置
        pos = self._phase
        n_filt = filtered.size
        while pos < n_filt:
            out_indices.append(pos)
            pos += self.down
        if out_indices:
            y = filtered[out_indices]
        else:
            y = np.zeros(0, dtype=np.float64)
        # 更新相位游标：下一块从 pos - n_filt 继续（取模回 [0, down)）
        # 注意：相位是高率域坐标，模 down 对齐抽取栅格。
        self._phase = int(round((pos - n_filt) % self.down))

        return y.astype(np.float32)


def resample_audio(x: np.ndarray, in_sr: float, out_sr: float) -> np.ndarray:
    """一次性（无状态）重采样：便捷函数，适合离线/测试。

    实时流式请用 :class:`AudioResampler` 以保留块间状态。
    """
    rs = AudioResampler(in_sr, out_sr)
    return rs.process(x)
