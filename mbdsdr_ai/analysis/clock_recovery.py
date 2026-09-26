"""
符号定时恢复（Clock Recovery）
==============================

URH 走的是开环路线：先估 samples_per_symbol（平台长度直方图，
``AutoInterpretation.py:344-370``），再在符号中心采样。本模块补上经典
闭环算法，作为 MBDSDR 的增强：

- :class:`GardnerClockRecovery` — Gardner 误差检测器，对 BPSK/OOK/PSK 友好，
  不依赖载波相位恢复（载波偏移下仍收敛）。
- :class:`EarlyLateGate` — 早-晚门（Early-Late Gate），对 OOK/FSK 的包
  络能量敏感，实现简单。

两种恢复器都支持流式 ``feed()`` 输入，输出符号序列。
"""

from __future__ import annotations

import dataclasses
from typing import List, Optional

import numpy as np


@dataclasses.dataclass
class Symbol:
    """恢复出的一个符号。"""
    index: int           # 在输出符号流中的序号
    sample_index: int    # 在输入 IQ 流中的采样点
    value: complex       # 采样时刻的复基带值
    bit: Optional[int] = None   # 判决后的比特（可选）


class GardnerClockRecovery:
    """Gardner 符号定时恢复。

    标准 Gardner 误差（对 BPSK/OOK）::

        e(k) = (y[k - T/2] - y[k]) * y[k - T]

    需要每符号至少 2 个样本。本实现用 NCO 生成采样脉冲，
    并维护「当前符号 / 上一符号 / 中点」三个样本。

    参数
    ----------
    samples_per_symbol : float
        每符号采样数。可与真实值有 ±20% 偏差，NCO 会收敛。
    loop_gain : float
        环路增益（比例 + 积分）。典型 0.01~0.2。
    """

    def __init__(
        self,
        samples_per_symbol: float,
        loop_gain: float = 0.05,
    ):
        self.sps = float(samples_per_symbol)
        self.gain = loop_gain
        self.reset()

    def reset(self) -> None:
        self._nco_phase = 0.0       # NCO 相位 (0..sps)
        self._s1 = None              # y[k-T] 上一符号采样
        self._s0 = None              # y[k] 当前符号采样
        self._mid = None             # y[k-T/2] 中点采样
        self._integrator = 0.0
        self._symbols: List[Symbol] = []
        self._sample_count = 0
        self._past: List[complex] = []   # 环形缓冲，找中点

    def feed(self, iq: np.ndarray) -> List[Symbol]:
        """喂入一段复基带样本，返回本次新恢复出的符号。"""
        iq = np.asarray(iq, dtype=np.complex128)
        out: List[Symbol] = []
        for s in iq:
            new_sym = self._process_one(s)
            if new_sym is not None:
                out.append(new_sym)
        self._symbols.extend(out)
        return out

    def _process_one(self, sample: complex) -> Optional[Symbol]:
        self._sample_count += 1
        self._past.append(sample)
        # 保留最近 sps+2 个样本
        keep = int(self.sps) + 4
        if len(self._past) > keep:
            self._past = self._past[-keep:]

        result = None
        self._nco_phase += 1.0

        if self._nco_phase >= self.sps:
            # 采样时刻到：当前 sample 就是符号采样
            self._nco_phase -= self.sps
            self._s0 = sample

            # 找中点：大约 sps/2 个样本之前
            mid_idx = max(0, len(self._past) - int(round(self.sps / 2.0)) - 1)
            self._mid = self._past[mid_idx] if self._past else sample

            # Gardner 误差
            if self._s1 is not None and self._mid is not None:
                err = (self._mid.real - self._s0.real) * self._s1.real
                self._integrator += err
                self._integrator = float(np.clip(self._integrator, -5.0, 5.0))
                self.sps += self.gain * err + 0.01 * self.gain * self._integrator
                self.sps = float(np.clip(self.sps, 1.5, 64.0))

            result = Symbol(
                index=len(self._symbols),
                sample_index=self._sample_count,
                value=complex(sample),
            )
            self._s1 = self._s0
        return result

    @property
    def symbols(self) -> List[Symbol]:
        return self._symbols

    def bits(self, threshold: float = 0.0) -> np.ndarray:
        """把恢复的符号按实部阈值判决为比特。"""
        return np.array([1 if s.value.real > threshold else 0 for s in self._symbols],
                        dtype=np.int8)


class EarlyLateGate:
    """早-晚门定时恢复。

    在估计符号中心两侧各取一个样本，比较能量。若 early 能量 > late 能量，
    说明采样点偏晚，应提前；反之推后。对包络（OOK/FSK）信号鲁棒。

    参数
    ----------
    samples_per_symbol : float
    offset : float
        早/晚相对符号中心的偏移（samples），典型 sps/4。
    loop_gain : float
        环路增益。
    """

    def __init__(
        self,
        samples_per_symbol: float,
        offset: Optional[float] = None,
        loop_gain: float = 0.1,
    ):
        self.sps = float(samples_per_symbol)
        self.offset = offset if offset is not None else self.sps / 4.0
        self.gain = loop_gain
        self.reset()

    def reset(self) -> None:
        self._nco_phase = 0.0
        self._symbols: List[Symbol] = []
        self._sample_count = 0
        self._early_buf: List[complex] = []
        self._late_buf: List[complex] = []

    def feed(self, iq: np.ndarray) -> List[Symbol]:
        iq = np.asarray(iq, dtype=np.complex128)
        out: List[Symbol] = []
        for s in iq:
            new_sym = self._process_one(s)
            if new_sym is not None:
                out.append(new_sym)
        self._symbols.extend(out)
        return out

    def _process_one(self, sample: complex) -> Optional[Symbol]:
        self._sample_count += 1
        self._nco_phase += 1.0

        # 记录 early / late 样本
        self._early_buf.append(sample)
        self._late_buf.append(sample)
        # 保留最近 sps + offset*2 个样本
        keep = int(self.sps + self.offset * 4) + 4
        if len(self._early_buf) > keep:
            self._early_buf = self._early_buf[-keep:]
            self._late_buf = self._late_buf[-keep:]

        result = None
        if self._nco_phase >= self.sps:
            self._nco_phase -= self.sps
            # 在符号中心采样
            center = complex(sample)
            # early / late 能量
            e_idx = max(0, len(self._early_buf) - int(self.offset) - 1)
            l_idx = max(0, len(self._late_buf) - 1)
            e_energy = abs(self._early_buf[e_idx]) ** 2
            l_energy = abs(self._late_buf[l_idx]) ** 2
            err = e_energy - l_energy
            # 调整 sps：early 能量大 → 提前采样 → sps 减小
            self.sps -= self.gain * err
            self.sps = float(np.clip(self.sps, 1.5, 64.0))

            result = Symbol(
                index=len(self._symbols),
                sample_index=self._sample_count,
                value=center,
            )
        return result

    @property
    def symbols(self) -> List[Symbol]:
        return self._symbols

    def bits(self, threshold: float = 0.5) -> np.ndarray:
        """按包络幅度判决为比特（OOK）。"""
        mags = np.array([abs(s.value) for s in self._symbols])
        thr = threshold * (mags.max() + 1e-12)
        return (mags > thr).astype(np.int8)
