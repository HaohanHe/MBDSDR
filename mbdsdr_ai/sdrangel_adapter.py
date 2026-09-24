"""
MBDSDR AI - SDRangel 真实源码移植适配器
==========================================

本模块把 SDRangel (GPLv3, Edouard Griffiths F4EXB 等) 的真实 DSP 架构移植为
NumPy 实现，所有关键常量/算法均标注来源 file:line。只移植架构与参数，不复制
C++ 工程框架（Qt 事件循环/MOC 等）。

数据流（来源: sdrbase/dsp/dspdevicesourceengine.cpp:288-337 work()）::

    DeviceSampleSource ──► SampleSinkFifo ──► DC/IQ校正 ──► BasebandSampleSinks
                                                              │
                                          每个通道: DownChannelizer(DDC)
                                                              │
                                                        DemodSink ──► audio/data

移植清单:
  - FFTFilter        来源: sdrbase/dsp/fftfilt.cpp / fftfilt.h  (overlap-add FFT 卷积)
  - DownChannelizer  来源: sdrbase/dsp/downchannelizer.cpp/.h   (半带链整数 2^N 抽取)
  - MagAGC           来源: sdrbase/dsp/agc.cpp/.h               (滑动均值幅度 AGC)
  - DSPDeviceEngine  来源: sdrbase/dsp/dspdevicesourceengine.cpp (采样流管道/状态机)
  - NFMDemodSink     来源: plugins/channelrx/demodnfm/nfmdemodsink.cpp + sdrbase/dsp/phasediscri.h
  - SSBDemodSink     来源: plugins/channelrx/demodssb/ssbdemodsink.cpp
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np


# ========================================================================
# 窗口函数  来源: sdrbase/dsp/fftfilt.h:96-101 _blackman()
# ========================================================================
def _blackman(n: int, L: int) -> np.ndarray:
    """Blackman 窗。来源: fftfilt.h:96-101
    0.42 - 0.5*cos(2*pi*i/L) + 0.08*cos(4*pi*i/L)
    """
    i = np.arange(L)
    return 0.42 - 0.50 * np.cos(2.0 * np.pi * i / n) + 0.08 * np.cos(4.0 * np.pi * i / n)


# ========================================================================
# FFTFilter —— overlap-add 快速卷积 FFT 滤波器
# 来源: sdrbase/dsp/fftfilt.cpp:144-186 create_filter()
#       sdrbase/dsp/fftfilt.cpp:436-457 runFilt()
#       sdrbase/dsp/fftfilt.h:89-94 fsinc()
# ========================================================================
class FFTFilter:
    """Overlap-add FFT 卷积滤波器（窗 sinc 冲激响应）。

    参数
    ----
    f1, f2 : 归一化频率（采样率单位，0.5 == Nyquist）。
        - f1==0          : 低通 (lowpass @ f2)
        - f2==0          : 高通 (highpass @ f1)
        - 0 < f1 < f2    : 带通 (bandpass)
        - f2 < f1        : 带阻 (band reject)
    flen   : FFT 长度（2 的幂）。块长 = flen/2。
        来源: fftfilt.cpp:76 flen2 = flen>>1。

    来源: fftfilt.cpp:144-186
        for i in [0, flen2):
            h[i]  = fsinc(f2, i, flen2)   # lowpass @ f2
            h[i] -= fsinc(f1, i, flen2)   # highpass @ f1
        若 f2 < f1（带阻）: h[flen2/2] += 1   (delta - h)
        加窗 -> FFT -> 按最大模归一化到单位增益。
    """

    def __init__(self, f1: float, f2: float, flen: int = 1024,
                 window: str = "blackman"):
        assert flen % 2 == 0, "flen 必须为 2 的幂 (fftfilt.cpp:76 flen2=flen>>1)"
        self.flen = flen
        self.flen2 = flen >> 1  # 来源: fftfilt.cpp:76
        self.f1 = float(f1)
        self.f2 = float(f2)
        self._window_name = window
        self.H = self._design()
        # overlap-add 状态: 来源 fftfilt.cpp:83 ovlbuf[flen2]
        self._ovl = np.zeros(self.flen2, dtype=np.complex128)
        self._in_buf: List[np.ndarray] = []
        self._in_len = 0

    # -- 冲激响应 fsinc: 来源 fftfilt.h:89-94 --
    @staticmethod
    def _fsinc(fc: float, i: int, length: int) -> float:
        """来源: fftfilt.h:89-94
            (i == len2) ? 2.0*fc : sin(2*pi*fc*(i-len2))/(pi*(i-len2))
        """
        len2 = length // 2
        if i == len2:
            return 2.0 * fc
        return math.sin(2.0 * math.pi * fc * (i - len2)) / (math.pi * (i - len2))

    def _design(self) -> np.ndarray:
        """构造频域响应 H[k]。来源: fftfilt.cpp:144-186"""
        L = self.flen2
        h = np.zeros(L, dtype=np.complex128)
        f1, f2 = self.f1, self.f2
        b_lowpass = (f2 != 0)   # 来源 fftfilt.cpp:151
        b_highpass = (f1 != 0)  # 来源 fftfilt.cpp:152

        for i in range(L):
            if b_lowpass:
                h[i] += self._fsinc(f2, i, L)   # 来源 fftfilt.cpp:158
            if b_highpass:
                h[i] -= self._fsinc(f1, i, L)   # 来源 fftfilt.cpp:161

        # highpass = delta[flen2/2] - h(t)  来源 fftfilt.cpp:164-165
        if b_highpass and f2 < f1:
            h[L // 2] += 1.0

        # 加窗（默认 Blackman） 来源 fftfilt.cpp:167-169
        if self._window_name == "blackman":
            h *= _blackman(L, L)

        # 时域冲激响应 -> 频域响应 H  来源 fftfilt.cpp:174 ComplexFFT(filter)
        H = np.fft.fft(h, self.flen)

        # 单位增益归一化  来源 fftfilt.cpp:177-185
        scale = float(np.max(np.abs(H)))
        if scale != 0.0:
            H /= scale
        return H

    # -- overlap-add 流式处理: 来源 fftfilt.cpp:436-457 runFilt() --
    def filter(self, x: np.ndarray) -> np.ndarray:
        """处理一段复数输入，返回滤波后复数输出（块长对齐，可能为 0 长度）。

        来源: fftfilt.cpp:436-457
            data[inptr++] = in;  攒满 flen2 个输入 -> FFT -> *=filter -> IFFT
            output[i] = ovlbuf[i] + data[i];  ovlbuf[i] = data[flen2+i];
        """
        x = np.asarray(x, dtype=np.complex128)
        out_blocks: List[np.ndarray] = []
        # 把新样本接到内部缓冲
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
            # 来源 fftfilt.cpp:443-447
            D = np.fft.fft(data)
            D *= self.H
            y = np.fft.ifft(D)
            # 来源 fftfilt.cpp:449-452
            out = self._ovl + y[:self.flen2]
            self._ovl = y[self.flen2:]
            out_blocks.append(out)

        # 保留不足一块的样本
        if pos < len(pending):
            self._in_buf = [pending[pos:]]
            self._in_len = len(pending) - pos

        if not out_blocks:
            return np.zeros(0, dtype=np.complex128)
        return np.concatenate(out_blocks)

    def filter_all(self, x: np.ndarray) -> np.ndarray:
        """一次性处理整段（自动 flush 剩余块，零填充）。便于离线往返测试。"""
        out = self.filter(x)
        # flush: 补零凑够一个块
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
# SSB 单边带滤波  来源: sdrbase/dsp/fftfilt.cpp:460-531 runSSB()
#                  plugins/channelrx/demodssb/ssbdemodsink.cpp:31-32,73
# ========================================================================
class SSBFilter(FFTFilter):
    """SSB 边带滤波器。

    来源: ssbdemodsink.cpp:31   m_ssbFftLen = 2048
    来源: ssbdemodsink.cpp:52-53 m_Bandwidth=5000, m_LowCutoff=300
    来源: ssbdemodsink.cpp:73  fftfilt(LowCutoff/audioSR, Bandwidth/audioSR, 2048)
    来源: fftfilt.cpp:475-502  USB 保留正频率 bin、丢弃负频率；LSB 反之。
    """

    SSB_FFT_LEN = 2048          # ssbdemodsink.cpp:31
    DEFAULT_BANDWIDTH = 5000.0  # ssbdemodsink.cpp:52
    DEFAULT_LOWCUT = 300.0      # ssbdemodsink.cpp:53

    def __init__(self, audio_sr: int = 48000, bandwidth: float = DEFAULT_BANDWIDTH,
                 low_cutoff: float = DEFAULT_LOWCUT, flen: int = SSB_FFT_LEN):
        self.audio_sr = audio_sr
        self.bandwidth = bandwidth
        self.low_cutoff = low_cutoff
        # 归一化到采样率单位（0.5=Nyquist） 来源 ssbdemodsink.cpp:301
        super().__init__(low_cutoff / audio_sr, bandwidth / audio_sr, flen=flen)

    def filter_ssb(self, x: np.ndarray, usb: bool = True) -> np.ndarray:
        """SSB 边带选择。来源: fftfilt.cpp:460-531 runSSB()

        USB: 保留 bin[1..flen2-1]（正频率），置零 bin[flen2+1..]（负频率）
        LSB: 保留负频率，置零正频率。DC (bin0) 按 getDC 处理。
        """
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
            # DC 保留  来源 fftfilt.cpp:470
            D[0] *= self.H[0]
            if usb:
                # 来源 fftfilt.cpp:477-480
                for i in range(1, L):
                    D[i] *= self.H[i]
                    D[L + i] = 0.0
            else:
                # 来源 fftfilt.cpp:491-494
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
# Halfband 抽取级 —— 来源: sdrbase/dsp/downchannelizer.cpp:196-215
#                         sdrbase/dsp/downchannelizer.h:31
# ========================================================================
DOWNCHANNELIZER_HB_FILTER_ORDER = 48  # 来源: downchannelizer.h:31


def _design_halfband(order: int = DOWNCHANNELIZER_HB_FILTER_ORDER) -> np.ndarray:
    """设计半带 FIR 系数。

    半带滤波器：阻带/通带关于 1/4 采样率对称，除中心抽头外所有偶下标为 0，
    每级 2:1 抽取无镜像。来源: downchannelizer.cpp:176 IntHalfbandFilterEO<..., order>。
    order=48 -> 对称抽头，中心抽头=0.5。
    """
    # 半带：归一化截止 0.25（当前采样率单位），Kaiser 窗
    numtaps = order + 1
    t = np.arange(numtaps) - order / 2.0
    h = np.sinc(0.5 * t)  # 截止 0.25 -> sinc(0.5 t) 归一化
    # 半带条件：偶下标（除中心）置零
    h[np.arange(numtaps) % 2 == 0] = 0.0
    h[order // 2] = 0.5
    w = np.kaiser(numtaps, beta=8.0)
    h *= w
    h /= np.sum(h)
    return h


class DownChannelizer:
    """整数 2^N 下变频通道化器。

    来源: sdrbase/dsp/downchannelizer.cpp
      - applyChannelization() :116-144  选半带链级数 n，channelSR = basebandSR/2^n (:136)
      - createFilterChain()   :230-272  Lower/Center/Upper 半带级递归选择
      - feed()                :47-90    每级半带滤波->2:1抽取，最后 NCO 残余频偏

    本移植用「NCO 复数混频到通道中心 + n 级半带 2:1 抽取」等价实现：
    任意通道中心频率先由 NCO 搬到基带（对应 C 版最后的 m_channelFrequencyOffset
    残余混频），再用 log2(D) 级半带滤波器整数抽取 D=2^n。
    """

    def __init__(self, baseband_sr: int, channel_sr: int, channel_offset: float = 0.0):
        self.baseband_sr = int(baseband_sr)
        self.channel_offset = float(channel_offset)
        # D = basebandSR / channelSR，必须为 2 的幂  来源 downchannelizer.cpp:136
        ratio = self.baseband_sr / float(channel_sr)
        n = int(round(math.log2(ratio)))
        assert abs(2 ** n - ratio) < 1e-6, \
            f"DownChannelizer 仅支持 2^N 抽取 (来源 downchannelizer.cpp:136), got ratio={ratio}"
        self.n_stages = n
        self.channel_sr = self.baseband_sr // (2 ** n)  # 来源 :136
        self._h = _design_halfband(DOWNCHANNELIZER_HB_FILTER_ORDER)
        self._phase = 0.0  # NCO 相位累加

    def process(self, x: np.ndarray) -> np.ndarray:
        """baseband IQ -> channel IQ。

        步骤:
          1. NCO 混频 -channel_offset  (对应 downchannelizer.cpp:162 m_channelFrequencyOffset)
          2. n 级半带滤波 + 2:1 抽取   (对应 downchannelizer.cpp:61-85)
        """
        x = np.asarray(x, dtype=np.complex128)
        # 1. NCO 复数混频
        n = len(x)
        t = np.arange(n) / self.baseband_sr
        rot = np.exp(-1j * 2.0 * np.pi * self.channel_offset * t)
        rot *= np.exp(1j * self._phase)
        x = x * rot
        self._phase = (self._phase - 2.0 * np.pi * self.channel_offset * n / self.baseband_sr) % (2 * np.pi)

        # 2. n 级半带 2:1 抽取
        y = x
        for _ in range(self.n_stages):
            y = np.convolve(y, self._h, mode='same')
            y = y[::2]
        return y


# ========================================================================
# MagAGC —— 来源: sdrbase/dsp/agc.cpp:53-179
# ========================================================================
def _smootherstep(t: float) -> float:
    """SDRangel StepFunctions::smootherstep 平滑包络 (agc.cpp:153 调用)。
    smootherstep(t) = t*t*t*(t*(6t-15)+10)，t in [0,1]。"""
    t = max(0.0, min(1.0, t))
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


class MagAGC:
    """幅度 AGC（滑动均值）。来源: sdrbase/dsp/agc.cpp:53-179

    关键参数（来源 agc.cpp:60-61）:
        m_stepLength = min(2400, historySize/2)   # @48kHz 最长 50ms 攻击/释放
        m_stepDelta  = 1/m_stepLength
        m_u0 = m_R / sqrt(mean(|x|^2))            # agc.cpp:117
    硬限幅（agc.cpp:104-111）: 输出幅度不超过 1.0。
    """

    def __init__(self, history_size: int = 12000, target: float = 3276.0,
                 threshold: float = 1e-2, sample_rate: int = 48000):
        self.history_size = int(history_size)
        self.target = float(target)            # m_R  来源 ssbdemodsink.cpp:32 m_agcTarget=3276
        self.threshold = float(threshold)     # m_threshold 来源 agc.cpp:57
        self.sample_rate = sample_rate
        # 来源 agc.cpp:60  m_stepLength = min(2400, historySize/2)
        self.step_length = min(2400, self.history_size // 2)
        self.step_delta = 1.0 / self.step_length  # 来源 agc.cpp:61
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
        down = 0
        for i, s in enumerate(x):
            magsq = float(s.real ** 2 + s.imag ** 2)
            # 滑动均值  来源 agc.cpp:115-116
            old = self._ring[self._idx]
            self._sum += magsq - old
            self._ring[self._idx] = magsq
            self._idx = (self._idx + 1) % self.history_size
            self._filled = min(self._filled + 1, self.history_size)

            avg = self._avg_magsq()
            u0 = self.target / math.sqrt(avg + 1e-12)  # 来源 agc.cpp:117

            # 阈值门控 + smootherstep 包络（简化为无门限连续版）
            step = up / self.step_length if up > 0 else 0.0
            g = u0 * _smootherstep(step) if up > 0 else 0.0
            if magsq > self.threshold:
                up = min(up + 1, self.step_length)
                down = 0
            else:
                up = max(up - 1, 0)
                down += 1
            # 硬限幅  来源 agc.cpp:104-111
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
# DSPDeviceEngine —— 采样流管道  来源: sdrbase/dsp/dspdevicesourceengine.cpp
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
    """采样流管道引擎。

    来源: sdrbase/dsp/dspdevicesourceengine.cpp
      - 状态机 notStarted->idle->init(ready)->running (:339-341)
      - work() :288-337  从 SampleFifo 读 -> DC/IQ 校正 -> 广播给所有 BasebandSampleSink
      - DC 偏移校正 dcOffset() :227-237  m_iBeta/m_qBeta 滑动均值后减去
    多通道并行：每个通道独立 DownChannelizer。
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
        self._alpha = 0.0002  # DC 环路时间常数（滑动平均）

    def add_channel(self, sink: ChannelSink):
        """对应 DSPAddBasebandSampleSink  dspdevicesourceengine.cpp:614-627"""
        self.sinks.append(sink)

    def start(self):
        self.state = "running"

    def work(self, iq: np.ndarray) -> Dict[str, int]:
        """喂入一段基带 IQ，分发到各通道。对应 work() :288-337。"""
        x = np.asarray(iq, dtype=np.complex128).copy()
        # DC 偏移校正  来源 dspdevicesourceengine.cpp:227-237
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
# NFMDemodSink —— 来源: plugins/channelrx/demodnfm/nfmdemodsink.cpp
#                   + sdrbase/dsp/phasediscri.h:75-92
# ========================================================================
class NFMDemodSink:
    """窄带 FM 解调。

    来源 nfmdemodsink.cpp:35   FFT_FILTER_LENGTH = 1024
    来源 nfmdemodsettings.cpp:57-59 默认 rfBW=12500, afBW=3000, fmDeviation=5000
    来源 nfmdemodsink.cpp:297-299 RF 滤波带: [-dev, +dev]/channelSR
    来源 nfmdemodsink.cpp:321,390  FM 缩放 = audioSR/fmDeviation
    来源 phasediscri.h:75-92 相位差分鉴频: dphi=angle(cur)-angle(prev), wrap 到[-1,1]
    来源 nfmdemodsink.cpp:326 音频带通 300Hz ~ afBandwidth
    """

    FFT_FILTER_LENGTH = 1024  # nfmdemodsink.cpp:35

    def __init__(self, channel_sr: int = 48000, audio_sr: int = 48000,
                 fm_deviation: float = 5000.0, af_bandwidth: float = 3000.0):
        self.channel_sr = channel_sr
        self.audio_sr = audio_sr
        self.fm_deviation = fm_deviation
        self.af_bandwidth = af_bandwidth
        # RF 带通: [-dev, +dev] 归一化  来源 nfmdemodsink.cpp:297-299
        norm = fm_deviation / channel_sr
        self._rf = FFTFilter(-norm, norm, flen=self.FFT_FILTER_LENGTH)
        self._prev_phase = 0.0
        self._audio: List[np.ndarray] = []

    def _discrim(self, x: np.ndarray) -> np.ndarray:
        """相位差分鉴频。来源 phasediscri.h:75-92。"""
        ang = np.angle(x)
        dphi = np.diff(np.concatenate([[self._prev_phase], ang]))
        self._prev_phase = ang[-1] if len(ang) else self._prev_phase
        dphi = (dphi + np.pi) % (2 * np.pi) - np.pi  # wrap  来源 phasediscri.h:85-89
        dphi /= math.pi
        # FM 缩放 = audioSR/fmDeviation  来源 nfmdemodsink.cpp:321
        return dphi * (self.audio_sr / self.fm_deviation)

    def process(self, iq: np.ndarray) -> np.ndarray:
        """channel IQ -> 音频浮点数组。"""
        if len(iq) < 4:
            return np.zeros(0)
        rf = self._rf.filter_all(iq)
        audio = self._discrim(rf)
        return audio.astype(np.float32)


# ========================================================================
# SSBDemodSink —— 来源: plugins/channelrx/demodssb/ssbdemodsink.cpp
# ========================================================================
class SSBDemodSink:
    """单边带解调。

    来源 ssbdemodsink.cpp:208  audio = (I+Q)*0.7
    来源 ssbdemodsink.cpp:40   AGC(history=12000, target=3276, threshold=1e-2)
    """

    def __init__(self, audio_sr: int = 48000, usb: bool = True,
                 bandwidth: float = SSBFilter.DEFAULT_BANDWIDTH,
                 low_cutoff: float = SSBFilter.DEFAULT_LOWCUT):
        self.audio_sr = audio_sr
        self.usb = usb
        self._filt = SSBFilter(audio_sr, bandwidth, low_cutoff)

    def process(self, iq: np.ndarray) -> np.ndarray:
        if len(iq) < self._filt.flen2:
            return np.zeros(0)
        # 对齐到块长
        n = (len(iq) // self._filt.flen2) * self._filt.flen2
        side = self._filt.filter_ssb(iq[:n], usb=self.usb)
        # SSB 检波: audio = (I+Q)*0.7  来源 ssbdemodsink.cpp:208
        audio = (side.real + side.imag) * 0.7
        return audio.astype(np.float32)


# ========================================================================
# 设备参数表（来源: plugins/samplesource/）
# ========================================================================
DEVICE_PRESETS: Dict[str, Dict[str, float]] = {
    # 来源 rtlsdrinput.cpp:51-54, rtlsdrsettings.cpp:29
    "rtlsdr": {
        "default_sample_rate": 1024e3,
        "low_sr_min": 225001, "low_sr_max": 300000,
        "high_sr_min": 900001, "high_sr_max": 3.2e6,
        "default_gain_db": 0,  # 0 = 自动
    },
    # 来源 hackrfinputsettings.cpp:45
    "hackrf": {
        "default_sample_rate": 2.4e6,
    },
    # 来源 bladerf1inputsettings.cpp:33-37
    "bladerf1": {
        "default_sample_rate": 3.072e6,
        "lna_gain_db": 0, "vga1_db": 20, "vga2_db": 9, "bandwidth_hz": 1.5e6,
    },
}
