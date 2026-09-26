"""
demod_ssb.py — SSB 单边带乘积检波解调（USB / LSB 真区分，纯 numpy 状态化）。

移植自 SDR++：
  * core/src/dsp/demod/ssb.h:77-92,106-116 —— SSB 解调与边带偏移
  * decoder_modules/radio/src/demodulators/usb.h:34,70-74  —— USB: Mode::USB
  * decoder_modules/radio/src/demodulators/lsb.h:33,69-76  —— LSB: Mode::LSB
  * core/src/dsp/channel/frequency_xlator.h:43-48          —— NCO 相位累加混频

算法（ssb.h:77-92 process）：
    1) FrequencyXlator：把边带搬回基带 —— y[n] = z[n]·e^{j φ[n]}
       其中 φ 逐样本累加 Δφ = 2π·translation/if_sr（频率_xlator.h:43-48）。
    2) ComplexToReal：取实部 —— audio = Re{y}（ssb.h:82）。
    3) AGC（在接收链后级，这里不内置）。

边带偏移（ssb.h:106-116 getTranslation）—— 这是 USB/LSB 真区分的核心：
    USB:  translation = +bandwidth/2     （usb.h:34 Mode::USB）
    LSB:  translation = -bandwidth/2     （lsb.h:33 Mode::LSB）
    DSB:  translation = 0

即 BFO（拍频本振）频率方向相反：USB 用 +BW/2 复指数，LSB 用 -BW/2。
这不是「共用一套代码只改标签」——两个模式喂同样的复信号，输出落在
音频带内的频率位置关于 0 镜像，错模式时音频被移到带宽外被低通滤除。

IF=24kHz, BW=2.8kHz（usb.h:70,72 / lsb.h:69,71）。
"""
from __future__ import annotations
import numpy as np
from mbdsdr_ai.channelizer import lowpass_taps


class DemodSSB:
    """SSB 乘积检波器（USB/LSB 真区分）。

    Parameters
    ----------
    mode : "usb" | "lsb" | "dsb"
    if_sr : IF 复采样率 Hz（usb.h:70 = 24000）。
    bandwidth : 双边带宽 Hz（usb.h:72 = 2800；BFO 偏移 = ±bandwidth/2）。
    """

    def __init__(self, mode: str = "usb", if_sr: float = 24000.0,
                 bandwidth: float = 2800.0):
        self.mode = (mode or "usb").lower()
        self.if_sr = float(if_sr)
        self.bandwidth = float(bandwidth)

        # ssb.h:106-116 getTranslation：USB=+BW/2, LSB=-BW/2, DSB=0
        if self.mode == "usb":
            self.translation = +self.bandwidth / 2.0
        elif self.mode == "lsb":
            self.translation = -self.bandwidth / 2.0
        else:
            self.translation = 0.0

        # NCO 相位增量（频率_xlator.h:17,28 phaseDelta = e^{j offset}）
        self._dphi = 2.0 * np.pi * self.translation / self.if_sr
        self._phase = 0.0  # frequency_xlator.h:64

        # 音频低通（带宽内）—— 错模式时把搬出音频带的分量滤掉
        self._lpf = lowpass_taps(self.bandwidth / 2.0, self.bandwidth / 2.0 * 0.1,
                                 self.if_sr)
        self._z = np.zeros(len(self._lpf) - 1)

    def reset(self) -> None:
        self._phase = 0.0
        self._z = np.zeros(len(self._lpf) - 1)

    def process(self, iq: np.ndarray) -> np.ndarray:
        """复 IF → 实音频（if_sr 采样率）。"""
        z = np.asarray(iq, dtype=np.complex128)
        n = len(z)
        if n == 0:
            return np.zeros(0, dtype=np.float32)

        # 1) FrequencyXlator 乘积检波（ssb.h:79 xlator.process）
        phases = self._phase + self._dphi * np.arange(n)
        mixed = z * np.exp(1j * phases)
        self._phase = (self._phase + self._dphi * n) % (2.0 * np.pi)

        # 2) ComplexToReal 取实部（ssb.h:82）
        audio = mixed.real

        # 3) 音频低通（保留带宽内，滤除错模式镜像分量）
        y = np.convolve(audio, self._lpf, mode="full")
        out = y[:n]
        tail = y[n:]
        self._z = np.concatenate([self._z[len(tail):], tail]) if len(tail) else self._z
        return out.astype(np.float32)
