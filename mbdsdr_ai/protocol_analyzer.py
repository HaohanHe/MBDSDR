"""三层一体协议分析（URH 风格，但自动化）。

上游对照（docs/learn/porting_2026_09_27.md §3）：
  - ``src/urh/ainterpretation/AutoInterpretation.py:151-207``  调制检测
  - ``:344-370`` plateau 长度直方图估计符号率
  - ``:373-440`` ``estimate()`` 全流程：噪声门限→分段→解调→采样→比特
  - ``src/urh/awre/engines/LengthEngine.py``  长度字段聚类
  - ``src/urh/awre/engines/ChecksumEngine.py:36-80``  CRC 猜测

URH 需要 GUI 手动逐步；本模块把三层串成一键：
  第一层 :mod:`~mbdsdr_ai.analysis.modulation_classifier` 自动检测调制 + 符号率
  第二层 Gardner/包络采样 → 硬判决比特 → 同步字检测
  第三层 :mod:`~mbdsdr_ai.analysis.protocol_parser` 切帧、CRC、字段推断
"""

from __future__ import annotations

import dataclasses
from typing import List, Optional

import numpy as np

from .analysis.modulation_classifier import ModulationClassifier
from .analysis.clock_recovery import GardnerClockRecovery, EarlyLateGate
from .analysis.protocol_parser import (
    ProtocolParser,
    crc16_ccitt,
    bits_to_bytes,
    bytes_to_bits,
)


# ---------------------------------------------------------------------------
# 结果
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class ProtocolFrame:
    modulation: str
    symbol_rate_baud: float
    sync_word: bytes
    fields: dict            # name -> value
    crc_ok: bool
    raw_bits: np.ndarray     # 0/1 比特数组
    payload: bytes = b""


# ---------------------------------------------------------------------------
# 第一层：硬判决比特
# ---------------------------------------------------------------------------

def _demodulate_bits(iq: np.ndarray, sample_rate: float,
                     modulation: str, baud_hint: Optional[float] = None
                     ) -> tuple:
    """第一层：按调制类型解调并采样为硬判决比特。

    返回 (bits, baud)。
    """
    iq = np.asarray(iq, dtype=np.complex128)
    mag = np.abs(iq)

    # 符号率猜测：包络过零/plateau 长度
    if baud_hint is None:
        # 粗略：用信号带宽估计（1 个 symbol ≈ 2 Hz 占用）
        # 这里用 plateau 长度法：找包络电平跳变的最小宽度
        high = mag > (mag.max() * 0.5)
        # 找连续 high/low 段长度
        runs = []
        prev = high[0]
        count = 0
        for h in high:
            if h == prev:
                count += 1
            else:
                runs.append(count)
                prev = h
                count = 1
        runs.append(count)
        min_run = max(2, int(np.median(runs))) if runs else 8
        sps = min_run
    else:
        sps = max(2, int(round(sample_rate / baud_hint)))

    baud = sample_rate / sps

    if modulation in ("OOK", "ASK", "AM"):
        # 包络检波 + 早-晚门
        gate = EarlyLateGate(samples_per_symbol=sps, loop_gain=0.1)
        syms = gate.feed(iq)
        bits = []
        for s in syms:
            bits.append(1 if abs(s.value) > (mag.max() * 0.4) else 0)
        return np.array(bits, dtype=np.int8), baud

    if modulation == "FSK":
        # 正交鉴频：相位差分 → 过零判决
        phase = np.unwrap(np.angle(iq))
        df = np.diff(phase)
        df = np.concatenate([[0], df])
        # 采样
        bits = []
        n = len(df)
        idx = 0
        while idx + sps < n:
            center = idx + sps // 2
            bits.append(1 if df[center] > np.median(df) else 0)
            idx += sps
        return np.array(bits, dtype=np.int8), baud

    # PSK：直接用实部阈值
    gate = GardnerClockRecovery(samples_per_symbol=sps, loop_gain=0.05)
    syms = gate.feed(iq)
    bits = [1 if s.value.real > 0 else 0 for s in syms]
    return np.array(bits, dtype=np.int8), baud


# ---------------------------------------------------------------------------
# 顶层
# ---------------------------------------------------------------------------

class ProtocolAnalyzer:
    """三层一体协议分析。

    参数
    ----------
    sync_word : bytes, optional
        已知同步字。None 时自动在比特流中找重复模式。
    length_offset : int
        长度字段相对同步字后的字节偏移。
    length_bytes : int
        长度字段字节数。
    """

    def __init__(
        self,
        sync_word: Optional[bytes] = None,
        length_offset: int = 2,
        length_bytes: int = 1,
    ):
        self.sync_word = sync_word
        self.length_offset = length_offset
        self.length_bytes = length_bytes
        self._classifier = ModulationClassifier()

    # ------------------------------------------------------------------
    def analyze(self, iq: np.ndarray, sample_rate: float,
                baud_hint: Optional[float] = None) -> List[ProtocolFrame]:
        iq = np.asarray(iq, dtype=np.complex128)
        if len(iq) < 64:
            return []

        # 第一层：调制识别
        mr = self._classifier.classify(iq, sample_rate)
        modulation = mr.modulation

        # 第一层：硬判决比特
        bits, baud = _demodulate_bits(iq, sample_rate, modulation, baud_hint)
        if len(bits) < 16:
            return []

        # 第二层/第三层：复用 ProtocolParser
        parser = ProtocolParser(
            sync_word=self.sync_word,
            length_offset=self.length_offset,
            length_field_bytes=self.length_bytes,
        )
        messages = parser.parse(bits)

        frames: List[ProtocolFrame] = []
        for msg in messages:
            sync_val = None
            fields = {}
            for f in msg.fields:
                fields[f.name] = f.value
                if f.name == "SYNC":
                    sync_val = f.value
            # sync_word bytes
            if sync_val is not None:
                nbytes = (len(msg.fields[0].raw_bits) // 8) if msg.fields else 1
                sw = sync_val.to_bytes(max(1, nbytes), byteorder="big")
            else:
                sw = b""
            payload = b""
            for f in msg.fields:
                if f.name == "DATA":
                    payload = f.to_bytes()
            frames.append(ProtocolFrame(
                modulation=modulation,
                symbol_rate_baud=float(baud),
                sync_word=sw,
                fields=fields,
                crc_ok=msg.valid_checksum,
                raw_bits=msg.raw_bits,
                payload=payload,
            ))
        return frames


__all__ = ["ProtocolAnalyzer", "ProtocolFrame"]
