# SPDX-License-Identifier: MIT
"""宽带 FM 广播鉴频 + 真立体声解码（纯 numpy 状态化）。

MPX 复合基带结构：
    mpx = (L+R) + pilot·cos(2π·19k·t) + (L-R)·cos(2π·38k·t)
    其中 (L+R)∈[0,15k]，pilot=19k，(L-R) 副载波在 38k±15k。

立体声恢复：
    1) 鉴频得 mpx
    2) pilot 带通 18.75~19.25k → 提取 19k pilot
    3) Hilbert 解析信号取 pilot 相位 θ
    4) 再生 38k 副载波 cos(2θ)
    5) mpx·cos(2θ) 取出差信号 (L-R)，×2
    6) L = (L+R) + (L-R)，R = (L+R) - (L-R)
    7) 音频低通 15k → 去加重 50μs

这是真立体声：pilot→38k 再生→L/R 矩阵。若 pilot 能量不足则退化为 mono。
"""
from __future__ import annotations
import numpy as np
from scipy.signal import butter, sosfilt, hilbert
from mbdsdr_ai.demod_nfm import DeemphasisIIR


class DemodWFM:
    """宽带 FM 立体声解调器。

    Parameters
    ----------
    if_sr : IF 复采样率 Hz，默认 250000。
    bandwidth : 双边带宽 Hz，默认 150000；deviation = bandwidth/2 = 75k。
    stereo : 是否解码立体声。
    deemph_tau : 去加重秒，默认 50μs。
    """

    PILOT_LO = 18750.0
    PILOT_HI = 19250.0
    PILOT_F0 = 19000.0
    AUDIO_LP = 15000.0

    def __init__(self, if_sr: float = 250000.0, bandwidth: float = 150000.0,
                 stereo: bool = True, deemph_tau: float = 50e-6):
        self.if_sr = float(if_sr)
        self.bandwidth = float(bandwidth)
        self.stereo = bool(stereo)
        self.deviation = self.bandwidth / 2.0
        self._gain = 1.0 / (2.0 * np.pi * self.deviation / self.if_sr)
        self._phase = 0.0
        # L/R 各自独立的去加重状态：立体声两路应分别保持连续 IIR 状态，
        # 不能共用一个对象再在中间 reset（那样会让右声道每块从零起振、左右响应不一致）。
        self.deemph_l = DeemphasisIIR(deemph_tau, self.if_sr)
        self.deemph_r = DeemphasisIIR(deemph_tau, self.if_sr)

        nyq = self.if_sr / 2.0

        self._bp = butter(5, [self.PILOT_LO / nyq, self.PILOT_HI / nyq],
                          btype="bandpass", output="sos")

        self._lp = butter(7, self.AUDIO_LP / nyq, btype="low", output="sos")

        # pilot 带通群延迟补偿
        # pilot 路径经过带通（有群延迟），而 MPX 直采路径没有——必须把 MPX
        # 延迟 pilot 群延迟，否则再生的 38k 副载波与 L-R 副载波相位错开，
        # 差信号提取幅度暴跌、立体声声道串音。用冲激响应峰值测群延迟。
        imp = np.zeros(4096)
        imp[0] = 1.0
        self._pilot_gd = int(np.argmax(np.abs(sosfilt(self._bp, imp))))
        self._mpx_delay = np.zeros(self._pilot_gd) if self._pilot_gd > 0 else None

    def reset(self) -> None:
        self._phase = 0.0
        self.deemph_l.reset()
        self.deemph_r.reset()
        if self._mpx_delay is not None:
            self._mpx_delay[:] = 0.0

    def process(self, iq: np.ndarray):
        """复 IF → (left, right) 实音频（if_sr 采样率）。"""
        z = np.asarray(iq, dtype=np.complex128)
        n = len(z)
        if n == 0:
            return (np.zeros(0, dtype=np.float32), np.zeros(0, dtype=np.float32))

        # 1) 宽带鉴频得 MPX
        # dphase[n] = angle(z[n]) - angle(z[n-1])，相邻样本差
        phase = np.angle(z)
        dphase = np.empty(n)
        dphase[0] = phase[0] - self._phase
        dphase[1:] = phase[1:] - phase[:-1]
        dphase = (dphase + np.pi) % (2.0 * np.pi) - np.pi
        self._phase = phase[-1] if n else self._phase
        mpx = dphase * self._gain

        # 2) 先把 MPX 延迟 pilot 带通群延迟，
        #    使直采 MPX 与经带通的 pilot 路径同相，再生 38k 副载波才不错位。
        if self._pilot_gd > 0:
            framed = np.concatenate([self._mpx_delay, mpx])
            mpx_delayed = framed[:n]
            self._mpx_delay = framed[n:n + self._pilot_gd]
            if len(self._mpx_delay) < self._pilot_gd:  # 块不足补零
                self._mpx_delay = np.concatenate(
                    [self._mpx_delay, np.zeros(self._pilot_gd - len(self._mpx_delay))])
        else:
            mpx_delayed = mpx
        # mono 主信道 (L+R)：低通 15k
        M = sosfilt(self._lp, mpx_delayed)

        if not self.stereo or n < 64:
            L = R = M
        else:
            # 3) pilot 带通提取
            pilot = sosfilt(self._bp, mpx)
            pilot_rms = float(np.sqrt(np.mean(pilot ** 2)))
            mpx_rms = float(np.sqrt(np.mean(mpx ** 2))) + 1e-12

            if pilot_rms < 0.02 * mpx_rms:
                # pilot 不足 → mono
                L = R = M
            else:
                # 4) Hilbert 解析信号取 pilot 相位 θ
                z_p = hilbert(pilot)
                theta = np.angle(z_p)
                # 5) 再生 38k 副载波 cos(2θ)
                car38 = np.cos(2.0 * theta)
                # 6) mpx_delayed·cos(2θ) → 差信号 (L-R)，低通 15k
                D = sosfilt(self._lp, mpx_delayed * car38)
                D = 2.0 * D
                # 7) L = M + D, R = M - D
                L = M + D
                R = M - D

        # 8) 去加重 50μs；L/R 独立状态
        L = self.deemph_l.process(L)
        R = self.deemph_r.process(R)
        return (L.astype(np.float32), R.astype(np.float32))
