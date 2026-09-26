"""
channelizer.py — Xlating FIR 数字下变频信道化（纯 numpy，状态化流式）。

移植自 SDR++：
  * core/src/dsp/channel/frequency_xlator.h:43-48  —— NCO 相位累加复指数混频
  * core/src/dsp/channel/rx_vfo.h:89-100,117-121   —— 混频→重采样抽取→FIR 低通
  * core/src/dsp/taps/low_pass.h:7-11               —— windowed-sinc + Nuttall 窗

管线（与 rx_vfo.h:89-100 完全一致的顺序）：
    1) NCO 混频：  y[n] = x[n] * e^{j φ[n]},  φ[n] = φ[n-1] + Δφ
                    Δφ = 2π·(-offset)/in_sr          (rx_vfo.h:27 用 -_offset)
    2) 重采样/抽取：in_sr → out_sr                    (rx_vfo.h:94 resamp.process)
    3) FIR 低通：  截止 = BW/2, 过渡带 = BW/2*0.1     (rx_vfo.h:119-120)
                    窗函数 Nuttall                    (low_pass.h:10)

注意：这是真正的「NCO 混频 → 低通 → 抽取」，不是简单截取频域窗口。
NCO 相位跨块连续（phase accumulator），与 frequency_xlator.h:64-65 的
`phase`/`phaseDelta` 状态对应。
"""
from __future__ import annotations
import numpy as np
from scipy.signal import resample_poly
from math import gcd


def _nuttall(M: int) -> np.ndarray:
    """Nuttall 窗 —— 对应 core/src/dsp/window/nuttall.h。

    w(n) = a0 - a1·cos(2πn/(M-1)) + a2·cos(4πn/(M-1)) - a3·cos(6πn/(M-1))
    """
    n = np.arange(M)
    a0, a1, a2, a3 = 0.355768, 0.487396, 0.144232, 0.012604
    return (a0
            - a1 * np.cos(2.0 * np.pi * n / (M - 1))
            + a2 * np.cos(4.0 * np.pi * n / (M - 1))
            - a3 * np.cos(6.0 * np.pi * n / (M - 1)))


def lowpass_taps(cutoff: float, trans_width: float, sample_rate: float,
                 odd_taps: bool = False) -> np.ndarray:
    """窗函数 sinc 低通抽头 —— 对应 core/src/dsp/taps/low_pass.h:7-11。

    count = estimateTapCount(trans_width, sample_rate)
    h = windowedSinc(count, cutoff, sample_rate, nuttall)
    """
    # estimateTapCount: 主瓣/过渡带经验关系，这里取 4·fs/trans_width 量级
    n_taps = int(np.ceil(4.0 * sample_rate / trans_width)) | 1  # 奇数
    if odd_taps and not (n_taps % 2):
        n_taps += 1
    n_taps = max(n_taps, 11)
    n = np.arange(n_taps) - (n_taps - 1) / 2.0
    h = 2.0 * cutoff / sample_rate * np.sinc(2.0 * cutoff / sample_rate * n)
    h *= _nuttall(n_taps)
    h /= h.sum()
    return h.astype(np.float64)


class XlatingFIR:
    """Xlating FIR 信道化（NCO 混频 → 抽取 → FIR 低通）。

    对应 core/src/dsp/channel/RxVFO（rx_vfo.h:19-33 init）。

    Parameters
    ----------
    in_sr : 输入复采样率 Hz。
    out_sr : 输出（IF）复采样率 Hz（= in_sr / 抽取倍数）。
    bandwidth : 信道双边带宽 Hz（低通截止 = bandwidth/2）。
    offset : VFO 偏移 Hz——把 +offset 处的信号搬到基带 0Hz
             （rx_vfo.h:27 `xlator.init(NULL, -_offset, _inSamplerate)`）。
    """

    def __init__(self, in_sr: float, out_sr: float, bandwidth: float,
                 offset: float = 0.0):
        self.in_sr = float(in_sr)
        self.out_sr = float(out_sr)
        self.bandwidth = float(bandwidth)
        self.offset = float(offset)

        # NCO 相位增量（频率_xlator.h:17 phaseDelta = cos/sin(offset_rad)）
        # rx_vfo.h:27 混频偏移取 -offset，把信号从 +offset 搬到 0。
        self._dphi = -2.0 * np.pi * self.offset / self.in_sr
        self._phase = 0.0  # frequency_xlator.h:64 phase

        # 抽取有理比
        g = gcd(int(round(self.out_sr)), int(round(self.in_sr)))
        self._up = int(round(self.out_sr)) // g
        self._down = int(round(self.in_sr)) // g

        # FIR 低通（rx_vfo.h:119-120）：截止 = BW/2，过渡 = BW/2*0.1
        cutoff = self.bandwidth / 2.0
        trans = cutoff * 0.1
        self._lpf = lowpass_taps(cutoff, trans, self.out_sr)

        # FIR 卷积状态（跨块连续）
        self._z = np.zeros(len(self._lpf) - 1)

    def reset(self) -> None:
        self._phase = 0.0
        self._z = np.zeros(len(self._lpf) - 1)

    def process(self, iq: np.ndarray) -> np.ndarray:
        """处理一段复 IQ，返回信道化后的复基带（out_sr 采样率）。"""
        x = np.asarray(iq, dtype=np.complex128)
        n = len(x)
        if n == 0:
            return np.zeros(0, dtype=np.complex128)

        # 1) NCO 混频（frequency_xlator.h:43-48 相位累加旋转器）
        phases = self._phase + self._dphi * np.arange(n)
        mixed = x * np.exp(1j * phases)
        self._phase = (self._phase + self._dphi * n) % (2.0 * np.pi)

        # 2) 有理重采样/抽取（rx_vfo.h:94 resamp.process）
        if self._up == 1 and self._down == 1:
            dec = mixed
        else:
            dec = resample_poly(mixed, self._up, self._down)

        # 3) FIR 低通（rx_vfo.h:97 filter.process），状态跨块连续
        y = np.convolve(dec, self._lpf, mode="full")
        out = y[: len(dec)]
        # 保存尾部状态（下一块接着卷）
        tail = y[len(dec):]
        if len(tail) < len(self._z):
            # 不足时用已有状态补齐
            self._z = np.concatenate([self._z[len(tail):], tail]) if len(tail) else self._z
        else:
            self._z = tail[: len(self._z)]

        return out.astype(np.complex128)
