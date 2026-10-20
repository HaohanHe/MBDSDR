# SPDX-License-Identifier: MIT
"""MBDSDR AI 内核 - 同步/调制解调原子积木（纯函数、可组合）
=========================================================

设计红线（AGENTS.md「原子能力、模型编排」）：每块积木只干一件事，绝不
打包成「解调大工具」。载波恢复/符号同步/GMSK/频率轨迹提取等由模型现场
调用这些积木自由编排。

积木清单（复基带约定：复数数组，单位归一化；sps=每符号采样数）：
- 载波：FrequencyLockedLoop（FLL，频率鉴别器 + PI 环路 + NCO）
- 定时：SymbolSync（统一 TED：gardner / mueller_muller / early_late）
- GMSK：gaussian_taps / gmsk_modulate / gmsk_demodulate（BT 参数化）
- 频率轨迹：iq_freq_track（STFT 峰值轨迹，复用 cfo.estimate_cfo_fft）

依据：FLL/Gardner/M&M/早迟门为经典符号同步算法（Rice、Mengali&D'Andrea）；
GMSK 为高斯预滤波 MSK（BT=0.3 GSM 口径 / 0.5 AIS 口径）。
"""
from __future__ import annotations

import math
from typing import List, Tuple

import numpy as np


# ─────────────────────────────────────────────────────────────
# 载波同步：FLL 锁频环
# ─────────────────────────────────────────────────────────────
class FrequencyLockedLoop:
    """二阶 FLL（锁频环）：频率鉴别器 + PI 环路滤波 + NCO。

    鉴别器 e = Im(y[n]·conj(y[n-1])) / |y[n]·conj(y[n-1])|（归一化瞬时频率），
    对任意调制均可用（载波跟踪前置），与 CostasLoop 互补：FLL 只锁频不锁相，
    捕获带宽大、对相位模糊不敏感。

    参数
    ----------
    loop_bw : float
        归一化环路带宽（rad/sample），典型 0.01~0.1。
    """

    def __init__(self, loop_bw: float = 0.05):
        self.loop_bw = float(loop_bw)
        damping = math.sqrt(2.0) / 2.0
        denom = 1.0 + 2.0 * damping * self.loop_bw + self.loop_bw * self.loop_bw
        self.alpha = (4.0 * damping * self.loop_bw) / denom   # 比例
        self.beta = (4.0 * self.loop_bw * self.loop_bw) / denom  # 积分
        self.reset()

    def reset(self) -> None:
        self.phase = 0.0
        self.freq = 0.0
        self._prev_y: complex | None = None

    def feed(self, iq: np.ndarray) -> np.ndarray:
        """喂入复基带，返回去频偏后的复基带。"""
        iq = np.asarray(iq, dtype=np.complex128)
        out = np.empty_like(iq)
        for n, z in enumerate(iq):
            nco = complex(math.cos(-self.phase), math.sin(-self.phase))
            y = z * nco
            out[n] = y
            if self._prev_y is not None:
                p = y * np.conj(self._prev_y)
                mag = abs(p)
                e = (p.imag / mag) if mag > 1e-12 else 0.0   # 归一化瞬时频率
                e = max(-1.0, min(1.0, e))
                self.freq += self.beta * e
                self.freq = max(-1.0, min(1.0, self.freq))
                self.phase += self.freq + self.alpha * e
                self.phase = ((self.phase + math.pi) % (2.0 * math.pi)) - math.pi
            self._prev_y = y
        return out

    @property
    def frequency_estimate(self) -> float:
        """当前 NCO 频率估计（归一化 rad/sample）。"""
        return self.freq


# ─────────────────────────────────────────────────────────────
# 符号定时同步：统一 TED 接口
# ─────────────────────────────────────────────────────────────
def gardner_ted(y_prev: complex, y_mid: complex, y_cur: complex) -> float:
    """Gardner 误差（对 PSK/OOK，不依赖载波相位）。

    环路极性约定：err = mid·(prev − cur)，配合 sps += gain·err 收敛
    （实测锁定 0 误码；教科书形 mid·(cur − prev) 对应反极性环路）。
    """
    return float((y_prev.real - y_cur.real) * y_mid.real)


def mueller_muller_ted(y_prev: complex, y_cur: complex,
                       a_prev: float, a_cur: float) -> float:
    """Mueller-Muller 误差（判决引导，1 sample/symbol 可用）。"""
    return float(a_prev * y_cur.real - a_cur * y_prev.real)


def early_late_ted(y_early: complex, y_prompt: complex, y_late: complex) -> float:
    """早迟门误差（包络能量型，对 OOK/FSK 鲁棒）。"""
    return float(abs(y_late) - abs(y_early))


class SymbolSync:
    """统一符号定时恢复（可插拔 TED）。

    与 analysis/clock_recovery.py 的 GardnerClockRecovery/EarlyLateGate 互补：
    本类提供单一入口 + 可插拔 TED，供模型按调制选择（gardner 对 PSK、
    early_late 对 OOK/FSK、mueller_muller 判决引导）。

    参数
    ----------
    samples_per_symbol : float
        每符号采样数初值（±20% 内 NCO 收敛）。
    ted : str
        "gardner" | "mueller_muller" | "early_late"。
    loop_gain : float
        环路增益，典型 0.01~0.2。
    """

    def __init__(self, samples_per_symbol: float, ted: str = "gardner",
                 loop_gain: float = 0.05):
        if ted not in ("gardner", "mueller_muller", "early_late"):
            raise ValueError(f"未知 TED: {ted}")
        self.ted_name = ted
        self.sps = float(samples_per_symbol)
        self.gain = float(loop_gain)
        self.reset()

    def reset(self) -> None:
        self._nco = 0.0
        self._buf: List[complex] = []
        self._prev_bit: float = 0.0
        self.symbols: List[complex] = []

    def feed(self, iq: np.ndarray) -> np.ndarray:
        """喂入复基带，返回恢复出的复符号序列（本次新增）。"""
        iq = np.asarray(iq, dtype=np.complex128)
        new_syms: List[complex] = []
        for z in iq:
            self._buf.append(complex(z))
            keep = int(round(self.sps)) + 8
            if len(self._buf) > keep:
                self._buf = self._buf[-keep:]
            self._nco += 1.0
            if self._nco >= self.sps:
                self._nco -= self.sps
                cur = self._buf[-1]
                # 按符号精确取 prev/mid/early/late（环路 sps 可能非整）
                n_prev = int(round(self.sps))
                n_mid = int(round(self.sps / 2))
                n_early = int(round(self.sps * 0.75))
                n_late = max(1, int(round(self.sps * 0.25)))
                prev = self._buf[max(0, len(self._buf) - n_prev - 1)]
                mid = self._buf[max(0, len(self._buf) - n_mid - 1)]
                early = self._buf[max(0, len(self._buf) - n_early - 1)]
                late = self._buf[max(0, len(self._buf) - n_late - 1)]
                err = self._ted_error(prev, early, mid, cur, late)
                self.sps += self.gain * err
                self.sps = float(np.clip(self.sps, 1.5, 64.0))
                self._prev_bit = 1.0 if cur.real >= 0 else -1.0
                new_syms.append(cur)
        self.symbols.extend(new_syms)
        return np.asarray(new_syms, dtype=np.complex128)

    def _ted_error(self, prev: complex, early: complex, mid: complex,
                   cur: complex, late: complex) -> float:
        if self.ted_name == "gardner":
            return gardner_ted(prev, mid, cur)
        if self.ted_name == "mueller_muller":
            return mueller_muller_ted(prev, cur,
                                      self._prev_bit, 1.0 if cur.real >= 0 else -1.0)
        return early_late_ted(early, cur, late)


# ─────────────────────────────────────────────────────────────
# GMSK 调制/解调
# ─────────────────────────────────────────────────────────────
def gaussian_taps(bt: float, sps: int, span_symbols: int = 4) -> np.ndarray:
    """高斯脉冲成形滤波器抽头（BT=带宽×符号周期，sps=每符号采样数）。

    归一化使 sum(taps)=1（每符号相位增量 π/2）。
    """
    if bt <= 0:
        raise ValueError("BT 必须 > 0")
    n = np.arange(-span_symbols * sps // 2, span_symbols * sps // 2 + 1, dtype=float)
    t = n / float(sps)
    s = math.sqrt(math.log(2.0)) / (2.0 * math.pi * bt)
    h = np.exp(-t**2 / (2.0 * s * s)) / (s * math.sqrt(2.0 * math.pi))
    return h / np.sum(h)


def gmsk_modulate(bits, sps: int = 8, bt: float = 0.3) -> np.ndarray:
    """GMSK 调制：NRZ -> 高斯成形 -> 频率积分 -> 复基带。

    每符号相位增量 ±π/2（BT→∞ 即 MSK）。
    """
    bits = np.asarray(bits, dtype=int)
    if bits.size == 0:
        raise ValueError("比特序列为空")
    if not np.all((bits == 0) | (bits == 1)):
        raise ValueError("比特必须为 0/1")
    nrz = 2.0 * bits.astype(float) - 1.0
    up = np.zeros(len(nrz) * sps, dtype=float)
    up[::sps] = nrz
    h = gaussian_taps(bt, sps)
    freq_shape = np.convolve(up, h, mode="same")
    phase = (np.pi / 2.0) * np.cumsum(freq_shape)
    return np.exp(1j * phase)


def gmsk_demodulate(iq, sps: int = 8) -> np.ndarray:
    """GMSK 非相干解调：相位差分鉴频 + 峰值对称窗判决。

    GMSK 高斯成形后，比特 k 的频率脉冲峰恰在样本 n=k*sps（已实测），故以
    峰为中心对称取窗做相位差分平均判决，抗相邻符号 ISI；窗取 sps//2 宽。
    返回比特数组（0/1）。适用于 BT 0.3~0.5 高 SNR 场景。
    """
    iq = np.asarray(iq, dtype=np.complex128)
    if iq.size < sps * 2:
        raise ValueError("样本不足以解调一个符号")
    dphase = np.angle(iq[1:] * np.conj(iq[:-1]))  # d[n] 对应 freq_shape[n]
    half = max(2, sps // 2)
    n_sym = len(iq) // sps
    bits = np.empty(n_sym, dtype=int)
    for k in range(n_sym):
        c = k * sps
        seg = dphase[max(0, c - half):c + half + 1]
        bits[k] = 1 if float(np.sum(seg)) > 0.0 else 0
    return bits


# ─────────────────────────────────────────────────────────────
# 频率轨迹提取（实测多普勒反演前置）
# ─────────────────────────────────────────────────────────────
def iq_freq_track(iq, sample_rate: float, window: int = 1024,
                  hop: int = 512) -> Tuple[np.ndarray, np.ndarray]:
    """STFT 峰值频率轨迹：IQ -> (时刻 s, 频率 Hz) 序列。

    逐窗功率谱取峰值 bin + 抛物线亚 bin 插值（复用 cfo.estimate_cfo_fft
    同口径）。输出可直接喂 doppler_from_frequency 换算伪距率，再喂
    rls_update 定轨——深空追迹定轨「频率提取」环节的原子积木。
    """
    from .cfo import estimate_cfo_fft  # 复用既有峰值频率原子

    iq = np.asarray(iq, dtype=np.complex128)
    if window < 8:
        raise ValueError("window 太小")
    if hop < 1 or hop > window:
        raise ValueError("hop 须在 1..window")
    if len(iq) < window:
        raise ValueError("IQ 长度不足一个窗口")

    times: List[float] = []
    freqs: List[float] = []
    for start in range(0, len(iq) - window + 1, hop):
        seg = iq[start:start + window]
        _offset, peak_hz = estimate_cfo_fft(seg, sample_rate)
        times.append((start + window / 2.0) / float(sample_rate))
        freqs.append(float(peak_hz))
    return np.asarray(times), np.asarray(freqs)
