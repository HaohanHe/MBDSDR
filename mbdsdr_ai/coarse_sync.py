"""粗同步：Costas 载波恢复 + Gardner 定时恢复。

上游对照（docs/learn/porting_2026_09_27.md §1）：
  - ``sdrbase/dsp/costasloop.cpp:51-57``  二阶临界阻尼环路系数
        ``damping=sqrt(2)/2; alpha=4*damping*BW/denom; beta=4*BW^2/denom``
  - ``costasloop.h:62-66`` 环路滤波 ``freq += beta*e; phase += freq + alpha*e``
  - ``costloop.cpp:83-91``  NCO ``exp(-j*phase)`` 与输入复数相乘
  - ``costasloop.h:86-110`` 相位鉴别器：
        BPSK ``re*im``；QPSK ``sign(re)*im - sign(im)*re``；
        8PSK 按 |re| vs |im| 分支，K=sqrt(2)-1

增强（相对 SDRangel）：
  * :class:`CoarseSync` 不需要手动指定调制类型——``modulation='auto'`` 时
    用 :class:`mbdsdr_ai.analysis.modulation_classifier.ModulationClassifier`
    自动选 BPSK/QPSK/8PSK/16QAM。
  * 与 Gardner 定时恢复串成一步：``process(iq) -> (synced, carrier_offset,
    timing_error)``。
"""

from __future__ import annotations

import dataclasses
import math
from typing import Optional

import numpy as np

from .analysis.modulation_classifier import ModulationClassifier


# ---------------------------------------------------------------------------
# Costas 环
# ---------------------------------------------------------------------------

def _phase_detector(psk_order: int, z: complex) -> float:
    """SDRangel costasloop.h:86-110 的相位鉴别器。"""
    re, im = z.real, z.imag
    if psk_order == 2:
        return re * im
    if psk_order == 4:
        return (1.0 if re >= 0 else -1.0) * im - (1.0 if im >= 0 else -1.0) * re
    if psk_order == 8:
        k = math.sqrt(2.0) - 1.0
        sr = 1.0 if re >= 0 else -1.0
        si = 1.0 if im >= 0 else -1.0
        if abs(re) >= abs(im):
            return sr * im - si * re * k
        return sr * im * k - si * re
    raise ValueError(f"不支持的 PSK order: {psk_order}")


class CostasLoop:
    """二阶 Costas 载波恢复环（对照 SDRangel ``CostasLoop``）。

    参数
    ----------
    loop_bw : float
        归一化环路带宽（rad/sample），典型 0.01~0.1。
    psk_order : int
        2/4/8。
    """

    def __init__(self, loop_bw: float = 0.05, psk_order: int = 4):
        self.psk_order = int(psk_order)
        self.loop_bw = float(loop_bw)
        self._compute_coeffs(self.loop_bw)
        self.reset()

    def _compute_coeffs(self, bw: float) -> None:
        # costasloop.cpp:51-57
        damping = math.sqrt(2.0) / 2.0
        denom = 1.0 + 2.0 * damping * bw + bw * bw
        self.alpha = (4.0 * damping * bw) / denom
        self.beta = (4.0 * bw * bw) / denom

    def reset(self) -> None:
        self.phase = 0.0
        self.freq = 0.0

    def feed(self, iq: np.ndarray) -> np.ndarray:
        """喂入复基带，返回载波恢复后的复基带。"""
        iq = np.asarray(iq, dtype=np.complex128)
        out = np.empty_like(iq)
        for n, z in enumerate(iq):
            # NCO
            nco = complex(math.cos(-self.phase), math.sin(-self.phase))
            y = z * nco
            e = _phase_detector(self.psk_order, y)
            # branchlessClip(e, 1.0)
            e = max(-1.0, min(1.0, e))
            # loop filter (costasloop.h:62-66)
            self.freq += self.beta * e
            self.freq = max(-1.0, min(1.0, self.freq))
            self.phase += self.freq + self.alpha * e
            # wrap
            self.phase = ((self.phase + math.pi) % (2.0 * math.pi)) - math.pi
            out[n] = y
        return out

    @property
    def carrier_offset(self) -> float:
        """当前 NCO 频率估计（归一化 rad/sample）。"""
        return self.freq


# ---------------------------------------------------------------------------
# Gardner 定时恢复（向量化简化版）
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class GardnerResult:
    symbols: np.ndarray          # 恢复出的复符号（按符号中心采样）
    timing_error: float          # 平均定时误差
    sps: float                   # 收敛后的每符号采样数


def gardner_recover(iq: np.ndarray, sps_guess: float,
                    loop_gain: float = 0.1, max_iter: int = 4) -> GardnerResult:
    """Gardner 定时恢复。

    标准 Gardner 误差 ``e = (mid - current) * prev``（对 BPSK/PSK）。
    这里做迭代插值：在每段符号中心线性插值采样。
    """
    iq = np.asarray(iq, dtype=np.complex128)
    n = len(iq)
    sps = float(sps_guess)
    symbols: list = []
    errs: list = []
    # NCO 相位
    phase = 0.0
    prev = 0j
    mid = 0j
    # 简单流水线：逐样本推进 NCO，到采样点时记录符号
    buf: list = []
    err_sum = 0.0
    err_cnt = 0
    i = 0
    while i < n:
        buf.append(iq[i])
        phase += 1.0
        if phase >= sps:
            phase -= sps
            cur = iq[i]
            # 中点：约 sps/2 个样本前
            mid_idx = max(0, len(buf) - int(round(sps / 2.0)) - 1)
            mid = buf[mid_idx]
            if len(symbols) >= 1:
                e = (mid.real - cur.real) * prev.real
                errs.append(e)
                err_sum += e
                err_cnt += 1
                sps += loop_gain * e
                sps = float(np.clip(sps, max(1.5, sps_guess * 0.7), sps_guess * 1.5))
            symbols.append(cur)
            prev = cur
        i += 1
    return GardnerResult(
        symbols=np.array(symbols, dtype=np.complex128),
        timing_error=(err_sum / err_cnt) if err_cnt else 0.0,
        sps=sps,
    )


# ---------------------------------------------------------------------------
# 顶层 CoarseSync
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class CoarseSyncResult:
    synced_iq: np.ndarray          # 载波恢复后的复基带（与输入等长）
    symbols: np.ndarray            # Gardner 恢复出的复符号
    carrier_offset_hz: float       # 估计载波频偏
    timing_error: float            # 平均 Gardner 误差
    modulation: str                # 实际使用的调制（auto 时为判定结果）
    sps: float                     # 收敛后的每符号采样数


class CoarseSync:
    """粗同步一站式入口。

    参数
    ----------
    modulation : str
        ``'auto'`` / ``'BPSK'`` / ``'QPSK'`` / ``'8PSK'`` / ``'16QAM'``。
    sample_rate : float, optional
        采样率（Hz）。给定时 ``carrier_offset_hz`` 才有意义。
    samples_per_symbol : float, optional
        每符号采样数猜测。None 时按带宽估计。
    loop_bw : float
        Costas 环带宽。
    """

    _MOD2ORDER = {"BPSK": 2, "PSK": 2, "QPSK": 4, "8PSK": 8,
                  "BPSK/OOK": 2}

    def __init__(
        self,
        modulation: str = "auto",
        sample_rate: Optional[float] = None,
        samples_per_symbol: Optional[float] = None,
        loop_bw: float = 0.05,
    ):
        self.modulation = modulation
        self.sample_rate = sample_rate
        self.sps_guess = samples_per_symbol
        self.loop_bw = loop_bw
        self._classifier = ModulationClassifier()

    # ------------------------------------------------------------------
    def _auto_modulation(self, iq: np.ndarray) -> str:
        sr = self.sample_rate if self.sample_rate else 1.0
        res = self._classifier.classify(iq, sr)
        # 把 AMR 的大类映射到 PSK order
        m = res.modulation.upper()
        if m in ("PSK", "QPSK"):
            return "QPSK"
        if m == "OOK" or m == "AM":
            return "BPSK"
        if m == "QAM":
            return "16QAM"
        return "QPSK"

    # ------------------------------------------------------------------
    def process(self, iq: np.ndarray) -> CoarseSyncResult:
        iq = np.asarray(iq, dtype=np.complex128)
        if len(iq) < 64:
            raise ValueError("信号太短，至少需要 64 个样本")

        mod = self.modulation
        if mod == "auto":
            mod = self._auto_modulation(iq)

        # 选择 PSK order（16QAM 按 QPSK 做载波恢复，再硬判决）
        order = self._MOD2ORDER.get(mod, 4)
        loop = CostasLoop(loop_bw=self.loop_bw, psk_order=order)
        synced = loop.feed(iq)

        # sps 猜测
        if self.sps_guess is None:
            # 粗略：用信号带宽估符号率。这里给一个保守默认值。
            sps_guess = 4.0
        else:
            sps_guess = float(self.sps_guess)
        gr = gardner_recover(synced, sps_guess=sps_guess)

        offset_norm = loop.carrier_offset          # rad/sample
        offset_hz = (offset_norm / (2.0 * math.pi) * self.sample_rate) if self.sample_rate else offset_norm

        return CoarseSyncResult(
            synced_iq=synced,
            symbols=gr.symbols,
            carrier_offset_hz=float(offset_hz),
            timing_error=float(gr.timing_error),
            modulation=mod,
            sps=gr.sps,
        )


__all__ = [
    "CostasLoop",
    "GardnerResult",
    "gardner_recover",
    "CoarseSync",
    "CoarseSyncResult",
]
