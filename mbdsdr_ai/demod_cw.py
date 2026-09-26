"""
demod_cw.py — CW 等报 BFO 差拍解调（纯 numpy 状态化）。

移植自 SDR++：
  * core/src/dsp/demod/cw.h:57-68            —— CW 解调管线
  * decoder_modules/radio/src/demodulators/cw.h:82-107 —— IF=3kHz, BW=200Hz, tone=800Hz
  * core/src/dsp/channel/frequency_xlator.h:43-48 —— NCO 相位累加混频

算法（cw.h:57-68 process）：
    1) FrequencyXlator：乘 BFO 复指数 —— y[n] = z[n]·e^{j φ[n]}
       φ 逐样本累加 Δφ = 2π·tone/if_sr（频率_xlator.h:43-48）。
    2) ComplexToReal：取实部 —— audio = Re{y}（cw.h:60,64）。
    3) AGC（接收链后级）。

这是真 BFO 差拍：把零中频载波（被抑制的 CW 等频）搬到音频音调 tone。
若直接 FFT 取峰值则丢失音调/莫尔斯包络，这里用乘积检波保持音频波形。
tone 范围 250~1250Hz（cw.h:65），默认 800Hz（cw.h:107）。
"""
from __future__ import annotations
import numpy as np


class DemodCW:
    """CW BFO 差拍解调器。

    Parameters
    ----------
    if_sr : IF 复采样率 Hz（cw.h:82 = 3000）。
    tone : BFO 拍频 Hz（cw.h:107 = 800；范围 250~1250）。
    bandwidth : 信道带宽 Hz（cw.h:84 = 200；音频低通用）。
    """

    def __init__(self, if_sr: float = 3000.0, tone: float = 800.0,
                 bandwidth: float = 200.0):
        self.if_sr = float(if_sr)
        self.tone = float(tone)
        self.bandwidth = float(bandwidth)
        # cw.h:21 xlator.init(NULL, tone, samplerate)
        self._dphi = 2.0 * np.pi * self.tone / self.if_sr
        self._phase = 0.0  # frequency_xlator.h:64

    def set_tone(self, tone: float) -> None:
        self.tone = float(tone)
        self._dphi = 2.0 * np.pi * self.tone / self.if_sr

    def reset(self) -> None:
        self._phase = 0.0

    def process(self, iq: np.ndarray) -> np.ndarray:
        """复 IF → 实音频（if_sr 采样率）。"""
        z = np.asarray(iq, dtype=np.complex128)
        n = len(z)
        if n == 0:
            return np.zeros(0, dtype=np.float32)

        # 1) BFO 乘积检波（cw.h:58 xlator.process）
        phases = self._phase + self._dphi * np.arange(n)
        mixed = z * np.exp(1j * phases)
        self._phase = (self._phase + self._dphi * n) % (2.0 * np.pi)

        # 2) ComplexToReal 取实部（cw.h:60）
        audio = mixed.real
        return audio.astype(np.float32)
