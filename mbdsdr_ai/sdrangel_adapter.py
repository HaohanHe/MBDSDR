# SPDX-License-Identifier: MIT
"""
MBDSDR 自有接收通道编排（多通道 DDC → FFT 滤波 → 解调）
============================================================

本模块是 MBDSDR 自有的基带接收通道：把一段宽带复数基带 IQ 按通道中心
频率下变频（NCO 混频 + 整数 2^N 半带抽取），再送入各自的窄带 FFT 滤波器
与 NFM/SSB 解调器。所有 DSP 均依据通用数字信号处理教材方法独立实现：

  - 频域滤波：窗函数 sinc 设计 + overlap-add 快速卷积（块长 = FFT/2）
  - 整数抽取：NCO 复数混频把通道搬到基带，再级联 n 级半带 FIR 做 2:1 抽取
  - 幅度 AGC：滑动窗均值估计包络，按目标 RMS 归一化并硬限幅
  - NFM 鉴频：复基带相位差分（角解调）后按频偏归一化
  - SSB：边带选择（保留单边频域 bin）后做乘积检波

外部 SDR 应用仅作技术参考与致谢，本仓未包含其源代码；上述算法为通用 DSP
原理的独立实现。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np


# ========================================================================
# 窗口函数
# ========================================================================
def _blackman(n: int, L: int) -> np.ndarray:
    """对称 Blackman 窗，长度 L，主瓣宽度参考 n。

    w[i] = 0.42 - 0.5*cos(2*pi*i/n) + 0.08*cos(4*pi*i/n)
    """
    i = np.arange(L)
    return 0.42 - 0.50 * np.cos(2.0 * np.pi * i / n) + 0.08 * np.cos(4.0 * np.pi * i / n)


# ========================================================================
# FFTFilter —— overlap-add 快速卷积 FFT 滤波器
# ========================================================================
class FFTFilter:
    """Overlap-add 频域滤波器（窗 sinc 冲激响应）。

    参数
    ----
    f1, f2 : 归一化截止频率（采样率单位，0.5 == Nyquist）。
        - f1==0          : 低通 (lowpass @ f2)
        - f2==0          : 高通 (highpass @ f1)
        - 0 < f1 < f2    : 带通 (bandpass)
        - f2 < f1        : 带阻 (band reject)
    flen   : FFT 长度（偶数）。块长 = flen/2。
    """

    def __init__(self, f1: float, f2: float, flen: int = 1024,
                 window: str = "blackman"):
        assert flen % 2 == 0, "flen 必须为偶数"
        self.flen = flen
        self.flen2 = flen >> 1
        self.f1 = float(f1)
        self.f2 = float(f2)
        self._window_name = window
        self.H = self._design()
        # overlap-add 状态缓冲
        self._ovl = np.zeros(self.flen2, dtype=np.complex128)
        self._in_buf: List[np.ndarray] = []
        self._in_len = 0

    @staticmethod
    def _fsinc(fc: float, i: int, length: int) -> float:
        """归一化 sinc 冲激响应采样点（理想低通 sinc）。

        中心抽头 i==length/2 取 2*fc，其余取 sin(2*pi*fc*(i-length/2))/(pi*(i-length/2))。
        """
        len2 = length // 2
        if i == len2:
            return 2.0 * fc
        return math.sin(2.0 * math.pi * fc * (i - len2)) / (math.pi * (i - len2))

    def _design(self) -> np.ndarray:
        """构造频域响应 H[k]：窗 sinc 低通/带通原型，再按最大模归一化。"""
        L = self.flen2
        h = np.zeros(L, dtype=np.complex128)
        f1, f2 = self.f1, self.f2
        b_lowpass = (f2 != 0)
        b_highpass = (f1 != 0)

        for i in range(L):
            if b_lowpass:
                h[i] += self._fsinc(f2, i, L)
            if b_highpass:
                h[i] -= self._fsinc(f1, i, L)

        # 高通 = 冲激 - 低通原型：中心抽头补 delta
        if b_highpass and f2 < f1:
            h[L // 2] += 1.0

        if self._window_name == "blackman":
            h *= _blackman(L, L)

        H = np.fft.fft(h, self.flen)
        scale = float(np.max(np.abs(H)))
        if scale != 0.0:
            H /= scale
        return H

    def filter(self, x: np.ndarray) -> np.ndarray:
        """流式处理一段复数输入，按块长对齐输出（不足一块返回空）。"""
        x = np.asarray(x, dtype=np.complex128)
        out_blocks: List[np.ndarray] = []
        if self._in_len == 0:
            pending = x
        else:
            pending = np.concatenate([np.asarray(self._in_buf[0], dtype=np.complex128), x])
            self._in_buf = []
            self._in_len = 0

        pos = 0
        while pos + self.flen2 <= len(pending):
            block = pending[pos:pos + self.flen2]
            pos += self.flen2
            data = np.zeros(self.flen, dtype=np.complex128)
            data[:self.flen2] = block
            D = np.fft.fft(data)
            D *= self.H
            y = np.fft.ifft(D)
            out = self._ovl + y[:self.flen2]
            self._ovl = y[self.flen2:]
            out_blocks.append(out)

        if pos < len(pending):
            self._in_buf = [pending[pos:]]
            self._in_len = len(pending) - pos

        if not out_blocks:
            return np.zeros(0, dtype=np.complex128)
        return np.concatenate(out_blocks)

    def filter_all(self, x: np.ndarray) -> np.ndarray:
        """一次性处理整段（自动补零 flush 尾块）。便于离线往返测试。"""
        out = self.filter(x)
        if self._in_len > 0:
            pad = np.zeros(self.flen2 - self._in_len, dtype=np.complex128)
            tail = self.filter(pad)
            out = np.concatenate([out, tail]) if len(out) else tail
        return out

    def reset(self):
        self._ovl = np.zeros(self.flen2, dtype=np.complex128)
        self._in_buf = []
        self._in_len = 0


# ========================================================================
# SSB 单边带边带选择
# ========================================================================
class SSBFilter(FFTFilter):
    """SSB 边带滤波器：带通成形 + 在频域保留单边 bin。

    USB 保留正频率 bin、置零负频率；LSB 反之。
    """

    SSB_FFT_LEN = 2048
    DEFAULT_BANDWIDTH = 5000.0
    DEFAULT_LOWCUT = 300.0

    def __init__(self, audio_sr: int = 48000, bandwidth: float = DEFAULT_BANDWIDTH,
                 low_cutoff: float = DEFAULT_LOWCUT, flen: int = SSB_FFT_LEN):
        self.audio_sr = audio_sr
        self.bandwidth = bandwidth
        self.low_cutoff = low_cutoff
        super().__init__(low_cutoff / audio_sr, bandwidth / audio_sr, flen=flen)

    def filter_ssb(self, x: np.ndarray, usb: bool = True) -> np.ndarray:
        """按边带选择滤波。输入长度需为 flen2 的整数倍。"""
        x = np.asarray(x, dtype=np.complex128)
        out_blocks: List[np.ndarray] = []
        pending = x
        pos = 0
        L = self.flen2
        while pos + L <= len(pending):
            block = pending[pos:pos + L]
            pos += L
            data = np.zeros(self.flen, dtype=np.complex128)
            data[:L] = block
            D = np.fft.fft(data)
            D[0] *= self.H[0]
            if usb:
                for i in range(1, L):
                    D[i] *= self.H[i]
                    D[L + i] = 0.0
            else:
                for i in range(1, L):
                    D[i] = 0.0
                    D[L + i] *= self.H[L + i]
            y = np.fft.ifft(D)
            out = self._ovl + y[:L]
            self._ovl = y[L:]
            out_blocks.append(out)
        if pos < len(pending):
            raise ValueError("SSBFilter.filter_ssb 需整段长度为 flen2 的整数倍（先用 filter_all 对齐）")
        if not out_blocks:
            return np.zeros(0, dtype=np.complex128)
        return np.concatenate(out_blocks)


# ========================================================================
# 半带抽取级
# ========================================================================
DOWNCHANNELIZER_HB_FILTER_ORDER = 48


def _design_halfband(order: int = DOWNCHANNELIZER_HB_FILTER_ORDER) -> np.ndarray:
    """设计半带 FIR：除中心抽头外所有偶下标为 0，每级 2:1 抽取无镜像。

    半带滤波器通带/阻带关于 1/4 采样率对称；用 sinc 原型加 Kaiser 窗。
    """
    numtaps = order + 1
    t = np.arange(numtaps) - order / 2.0
    h = np.sinc(0.5 * t)
    h[np.arange(numtaps) % 2 == 0] = 0.0
    h[order // 2] = 0.5
    w = np.kaiser(numtaps, beta=8.0)
    h *= w
    h /= np.sum(h)
    return h


class DownChannelizer:
    """整数 2^N 下变频通道化器。

    流程：NCO 复数混频把通道中心搬到基带，再级联 log2(D) 级半带 FIR 做
    2:1 抽取，使 channelSR = basebandSR / 2^n。
    """

    def __init__(self, baseband_sr: int, channel_sr: int, channel_offset: float = 0.0):
        self.baseband_sr = int(baseband_sr)
        self.channel_offset = float(channel_offset)
        ratio = self.baseband_sr / float(channel_sr)
        n = int(round(math.log2(ratio)))
        assert abs(2 ** n - ratio) < 1e-6, \
            f"DownChannelizer 仅支持 2^N 抽取, got ratio={ratio}"
        self.n_stages = n
        self.channel_sr = self.baseband_sr // (2 ** n)
        self._h = _design_halfband(DOWNCHANNELIZER_HB_FILTER_ORDER)
        self._phase = 0.0

    def process(self, x: np.ndarray) -> np.ndarray:
        """baseband IQ -> channel IQ。"""
        x = np.asarray(x, dtype=np.complex128)
        n = len(x)
        t = np.arange(n) / self.baseband_sr
        rot = np.exp(-1j * 2.0 * np.pi * self.channel_offset * t)
        rot *= np.exp(1j * self._phase)
        x = x * rot
        self._phase = (self._phase - 2.0 * np.pi * self.channel_offset * n / self.baseband_sr) % (2 * np.pi)

        y = x
        for _ in range(self.n_stages):
            y = np.convolve(y, self._h, mode='same')
            y = y[::2]
        return y


# ========================================================================
# 幅度 AGC
# ========================================================================
def _smootherstep(t: float) -> float:
    """平滑包络函数 t^3*(t*(6t-15)+10)，t 截断到 [0,1]。"""
    t = max(0.0, min(1.0, t))
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


class MagAGC:
    """幅度 AGC（滑动窗均值 RMS 估计）。

    维护长度 history_size 的平方幅度滑动均值，把输出幅度归一化到 target；
    并用平滑包络缓慢改变增益，输出硬限幅在单位圆内。
    """

    def __init__(self, history_size: int = 12000, target: float = 3276.0,
                 threshold: float = 1e-2, sample_rate: int = 48000):
        self.history_size = int(history_size)
        self.target = float(target)
        self.threshold = float(threshold)
        self.sample_rate = sample_rate
        self.step_length = min(2400, self.history_size // 2)
        self.step_delta = 1.0 / self.step_length
        self._ring = np.zeros(self.history_size)
        self._idx = 0
        self._sum = 0.0
        self._filled = 0
        self.gain = 1.0

    def _avg_magsq(self) -> float:
        if self._filled == 0:
            return 1.0
        return self._sum / self._filled

    def process(self, x: np.ndarray) -> np.ndarray:
        """逐样本 AGC（向量化维护滑动窗）。"""
        x = np.asarray(x, dtype=np.complex128).copy()
        out = np.empty_like(x)
        up = 0
        for i, s in enumerate(x):
            magsq = float(s.real ** 2 + s.imag ** 2)
            old = self._ring[self._idx]
            self._sum += magsq - old
            self._ring[self._idx] = magsq
            self._idx = (self._idx + 1) % self.history_size
            self._filled = min(self._filled + 1, self.history_size)

            avg = self._avg_magsq()
            u0 = self.target / math.sqrt(avg + 1e-12)

            step = up / self.step_length if up > 0 else 0.0
            g = u0 * _smootherstep(step) if up > 0 else 0.0
            if magsq > self.threshold:
                up = min(up + 1, self.step_length)
            else:
                up = max(up - 1, 0)
            if g * g * magsq > 1.0:
                g = 1.0 / math.sqrt(magsq)
            out[i] = s * g
            self.gain = g
        return out

    def reset(self):
        self._ring[:] = 0.0
        self._idx = 0
        self._sum = 0.0
        self._filled = 0
        self.gain = 1.0


# ========================================================================
# 采样流管道
# ========================================================================
@dataclass
class ChannelSink:
    """一个通道 sink（解调器）。feed(iq_array) -> None。"""
    name: str
    channelizer: Optional[DownChannelizer] = None
    handler: Optional[Callable[[np.ndarray], None]] = None

    def feed(self, iq: np.ndarray):
        if self.handler is not None:
            self.handler(iq)


class DSPDeviceEngine:
    """采样流管道引擎：读入宽带基带 IQ，做 DC 偏移校正，再分发到各通道。

    每个通道带独立 DownChannelizer；work() 返回各通道输出样本数统计。
    """

    def __init__(self, sample_rate: int = 1024000, center_frequency: int = 100e6,
                 dc_offset_correction: bool = True):
        self.sample_rate = sample_rate
        self.center_frequency = center_frequency
        self.dc_offset_correction = dc_offset_correction
        self.sinks: List[ChannelSink] = []
        self.state = "idle"
        self._i_beta = 0.0
        self._q_beta = 0.0
        self._alpha = 0.0002

    def add_channel(self, sink: ChannelSink):
        self.sinks.append(sink)

    def start(self):
        self.state = "running"

    def work(self, iq: np.ndarray) -> Dict[str, int]:
        """喂入一段基带 IQ，分发到各通道。"""
        x = np.asarray(iq, dtype=np.complex128).copy()
        if self.dc_offset_correction and len(x):
            self._i_beta += self._alpha * (float(np.mean(x.real)) - self._i_beta)
            self._q_beta += self._alpha * (float(np.mean(x.imag)) - self._q_beta)
            x -= complex(self._i_beta, self._q_beta)
        stats = {}
        for sink in self.sinks:
            ch = sink.channelizer.process(x) if sink.channelizer is not None else x
            sink.feed(ch)
            stats[sink.name] = len(ch)
        return stats


# ========================================================================
# NFM 窄带 FM 解调
# ========================================================================
class NFMDemodSink:
    """窄带 FM 解调：RF 带通 + 相位差分鉴频。

    鉴频输出正比于瞬时频偏，按 audioSR/fmDeviation 归一化到音频幅值。
    """

    FFT_FILTER_LENGTH = 1024

    def __init__(self, channel_sr: int = 48000, audio_sr: int = 48000,
                 fm_deviation: float = 5000.0, af_bandwidth: float = 3000.0):
        self.channel_sr = channel_sr
        self.audio_sr = audio_sr
        self.fm_deviation = fm_deviation
        self.af_bandwidth = af_bandwidth
        norm = fm_deviation / channel_sr
        self._rf = FFTFilter(-norm, norm, flen=self.FFT_FILTER_LENGTH)
        self._prev_phase = 0.0
        self._audio: List[np.ndarray] = []

    def _discrim(self, x: np.ndarray) -> np.ndarray:
        """相位差分鉴频：dphi = angle(cur)-angle(prev)，wrap 到 [-pi,pi]。"""
        ang = np.angle(x)
        dphi = np.diff(np.concatenate([[self._prev_phase], ang]))
        self._prev_phase = ang[-1] if len(ang) else self._prev_phase
        dphi = (dphi + np.pi) % (2 * np.pi) - np.pi
        dphi /= math.pi
        return dphi * (self.audio_sr / self.fm_deviation)

    def process(self, iq: np.ndarray) -> np.ndarray:
        """channel IQ -> 音频浮点数组。"""
        if len(iq) < 4:
            return np.zeros(0)
        rf = self._rf.filter_all(iq)
        audio = self._discrim(rf)
        return audio.astype(np.float32)


# ========================================================================
# SSB 单边带解调
# ========================================================================
class SSBDemodSink:
    """单边带解调：边带选择滤波后做乘积检波 (I+Q)*0.7。"""

    def __init__(self, audio_sr: int = 48000, usb: bool = True,
                 bandwidth: float = SSBFilter.DEFAULT_BANDWIDTH,
                 low_cutoff: float = SSBFilter.DEFAULT_LOWCUT):
        self.audio_sr = audio_sr
        self.usb = usb
        self._filt = SSBFilter(audio_sr, bandwidth, low_cutoff)

    def process(self, iq: np.ndarray) -> np.ndarray:
        if len(iq) < self._filt.flen2:
            return np.zeros(0)
        n = (len(iq) // self._filt.flen2) * self._filt.flen2
        side = self._filt.filter_ssb(iq[:n], usb=self.usb)
        audio = (side.real + side.imag) * 0.7
        return audio.astype(np.float32)


# ========================================================================
# 设备采样率预设（公开硬件数据手册/驱动常用值）
# ========================================================================
DEVICE_PRESETS: Dict[str, Dict[str, float]] = {
    "rtlsdr": {
        "default_sample_rate": 1024e3,
        "low_sr_min": 225001, "low_sr_max": 300000,
        "high_sr_min": 900001, "high_sr_max": 3.2e6,
        "default_gain_db": 0,  # 0 = 自动
    },
    "hackrf": {
        "default_sample_rate": 2.4e6,
    },
    "bladerf1": {
        "default_sample_rate": 3.072e6,
        "lna_gain_db": 0, "vga1_db": 20, "vga2_db": 9, "bandwidth_hz": 1.5e6,
    },
}
