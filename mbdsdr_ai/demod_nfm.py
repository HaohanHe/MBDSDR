"""
demod_nfm.py — 窄带 FM 相位差分鉴频 + RC 去加重（纯 numpy 状态化）。

移植自 SDR++：
  * core/src/dsp/demod/fm.h:79-96          —— FM 解调管线
  * core/src/dsp/demod/quadrature.h:39-46  —— 相位差分鉴频器
  * decoder_modules/radio/src/demodulators/nfm.h:56-58 —— IF=50kHz, BW=12.5kHz
  * core/src/dsp/filter/deephasis.h:58-94  —— 一阶 RC 去加重 IIR

鉴频（quadrature.h:40-44）：
    cphase = angle(z[n])
    out[n]  = normalize(cphase - phase_prev) * (1/deviation)
    phase_prev = cphase
  等价于 GNU Radio quadrature_demod_cf_impl.cc:50-52：
    tmp = z[n]·conj(z[n-1]);  out = gain·angle(tmp)
  其中 deviation = BW/2（fm.h:30,60），gain = 1/(2π·deviation/if_sr)。

去加重（deephasis.h:91-93,60-62）：
    dt = 1/if_sr;  alpha = dt/(tau+dt)
    out[n] = alpha·in[n] + (1-alpha)·out[n-1]
  tau = 50μs(欧/中)/75μs(美)/22μs。

注：SDR++ NFM 链无显式 limiter——AGC（在解调前）已把幅度压平，
相位差分对幅度不敏感，故无需单独限幅（fm.h 无 limiter 块）。
"""
from __future__ import annotations
import numpy as np


class DeemphasisIIR:
    """一阶 RC 去加重 IIR —— 对应 core/src/dsp/filter/deephasis.h。"""

    def __init__(self, tau: float, samplerate: float):
        self.tau = float(tau)
        self.sr = float(samplerate)
        self._last = 0.0
        self._update_alpha()

    def _update_alpha(self) -> None:
        dt = 1.0 / self.sr                       # deephasis.h:92
        self.alpha = dt / (self.tau + dt)       # deephasis.h:93

    def set_tau(self, tau: float) -> None:
        self.tau = float(tau)
        self._update_alpha()

    def reset(self) -> None:
        self._last = 0.0

    def process(self, x: np.ndarray) -> np.ndarray:
        if self.tau <= 0 or len(x) == 0:
            return np.asarray(x, dtype=np.float64)
        x = np.asarray(x, dtype=np.float64)
        out = np.empty_like(x)
        prev = self._last
        a = self.alpha
        for i in range(len(x)):                 # deephasis.h:60-62
            prev = a * x[i] + (1.0 - a) * prev
            out[i] = prev
        self._last = out[-1]
        return out


class DemodNFM:
    """窄带 FM 鉴频器。

    Parameters
    ----------
    if_sr : IF 复采样率 Hz（nfm.h:56 = 50000）。
    bandwidth : 双边带宽 Hz（nfm.h:58 = 12500；deviation = bandwidth/2）。
    deemph_tau : 去加重时间常数秒（nfm.h:66 默认 0=不去加重；50e-6/75e-6）。
    """

    def __init__(self, if_sr: float = 50000.0, bandwidth: float = 12500.0,
                 deemph_tau: float = 0.0):
        self.if_sr = float(if_sr)
        self.bandwidth = float(bandwidth)
        # fm.h:30,60 deviation = bandwidth/2
        self.deviation = self.bandwidth / 2.0
        # 鉴频增益 = 1/(2π·deviation/if_sr)（quadrature.h:19,24）
        self._gain = 1.0 / (2.0 * np.pi * self.deviation / self.if_sr)
        self._phase = 0.0  # quadrature.h:67
        self.deemph = DeemphasisIIR(deemph_tau, self.if_sr)

    def reset(self) -> None:
        self._phase = 0.0
        self.deemph.reset()

    def process(self, iq: np.ndarray) -> np.ndarray:
        """复 IF → 实音频（if_sr 采样率）。"""
        z = np.asarray(iq, dtype=np.complex128)
        n = len(z)
        if n == 0:
            return np.zeros(0, dtype=np.float32)

        # 相位差分鉴频（quadrature.h:40-44）
        # dphase[n] = angle(z[n]) - angle(z[n-1])，相邻样本差
        # 等价 GNU Radio quadrature_demod_cf_impl.cc:50-52: angle(z[n]·conj(z[n-1]))
        phase = np.angle(z)
        dphase = np.empty(n)
        dphase[0] = phase[0] - self._phase
        dphase[1:] = phase[1:] - phase[:-1]
        # 归一化到 [-π, π]（quadrature.h:42 normalizePhase）
        dphase = (dphase + np.pi) % (2.0 * np.pi) - np.pi
        self._phase = phase[-1] if n else self._phase
        audio = dphase * self._gain

        # 去加重（deephasis.h，tau=0 时直通）
        audio = self.deemph.process(audio)
        return audio.astype(np.float32)
