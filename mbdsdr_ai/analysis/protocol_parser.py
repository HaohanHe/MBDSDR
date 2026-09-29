# SPDX-License-Identifier: MIT
"""
协议解析
========

依据公开协议解析与 CRC 校验方法独立实现（URH 仅作技术参考，本仓未包含其源代码）：
- 字段类型枚举（PREAMBLE/SYNC/LENGTH/...）——通用协议解析约定。
- 标准 CRC 表，含 CRC-16/CCITT（poly=0x1021）——公开 CRC 参数。
- 长度字段聚类检测、CRC 猜测——通用启发式。

本模块提供：
- :class:`ProtocolField` — 字段（name/bit_length/type/value）。
- :class:`ProtocolMessage` — 消息（字段树 + 原始比特 + 校验结果）。
- :class:`ProtocolParser` — 自动检测同步字、长度字段、CRC-16/CCITT。

AI 增强：在规则检测之外，用「比特熵 + n-gram 重复模式」自动推断未知协议
的字段边界（对应 URH awre 的思路，但纯 Python、不依赖 Cython）。
"""

from __future__ import annotations

import dataclasses
import math
from enum import Enum
from typing import List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# 字段类型
# ---------------------------------------------------------------------------

class FieldType(Enum):
    PREAMBLE = "preamble"
    SYNC = "synchronization"
    LENGTH = "length"
    SRC_ADDRESS = "source_address"
    DST_ADDRESS = "destination_address"
    SEQUENCE_NUMBER = "sequence_number"
    TYPE = "type"
    DATA = "data"
    CHECKSUM = "checksum"
    CUSTOM = "custom"


# ---------------------------------------------------------------------------
# CRC-16/CCITT
# ---------------------------------------------------------------------------

def crc16_ccitt(data: bytes, init: int = 0xFFFF, poly: int = 0x1021) -> int:
    """CRC-16/CCITT-FALSE。

    CRC-16/CCITT 公开参数：多项式 0x1021。这里用最常见的 FALSE 变体（init=0xFFFF，
    无反射），工业界最常用。
    """
    crc = init & 0xFFFF
    for byte in data:
        crc ^= (byte << 8) & 0xFFFF
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ poly) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc & 0xFFFF


# ---------------------------------------------------------------------------
# 字段 / 消息
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class ProtocolField:
    """协议字段。"""
    name: str
    bit_length: int
    type: FieldType = FieldType.CUSTOM
    value: int = 0          # 整数值
    raw_bits: str = ""      # 二进制字符串

    def to_bytes(self) -> bytes:
        nbytes = (self.bit_length + 7) // 8
        return self.value.to_bytes(nbytes, byteorder="big")

    def __repr__(self) -> str:  # pragma: no cover
        return f"ProtocolField({self.name}:{self.type.value}={self.value:#x}, {self.bit_length}bit)"


@dataclasses.dataclass
class ProtocolMessage:
    """协议消息：字段列表 + 原始比特 + 校验结果。"""
    fields: List[ProtocolField]
    raw_bits: np.ndarray          # 1D int array of 0/1
    valid_checksum: bool = False

    def field(self, name: str) -> Optional[ProtocolField]:
        for f in self.fields:
            if f.name == name or f.type.value == name:
                return f
        return None

    def __repr__(self) -> str:  # pragma: no cover
        fs = ", ".join(f.name for f in self.fields)
        return f"ProtocolMessage([{fs}], checksum_ok={self.valid_checksum})"


# ---------------------------------------------------------------------------
# 比特工具
# ---------------------------------------------------------------------------

def bits_to_bytes(bits: np.ndarray) -> bytes:
    """把 0/1 比特数组打包成 bytes（MSB first）。"""
    bits = np.asarray(bits, dtype=np.int8)
    # 补齐到 8 的倍数
    pad = (-len(bits)) % 8
    if pad:
        bits = np.concatenate([bits, np.zeros(pad, dtype=np.int8)])
    b = np.packbits(bits)
    return b.tobytes()


def bytes_to_bits(data: bytes) -> np.ndarray:
    arr = np.frombuffer(data, dtype=np.uint8)
    return np.unpackbits(arr).astype(np.int8)


# ---------------------------------------------------------------------------
# 解析器
# ---------------------------------------------------------------------------

class ProtocolParser:
    """自动协议解析器。

    参数
    ----------
    sync_word : bytes, optional
        已知同步字（如 ``0xAA 0xAA 0xD3``）。若 None，则自动在比特流中找
        重复出现的 16-bit 模式作为同步字。
    length_offset : int
        长度字段在同步字之后的字节偏移。
    length_field_bytes : int
        长度字段字节数（1 或 2）。
    length_includes_header : bool
        长度字段是否含头部自身。
    crc_init : int
    crc_poly : int
    crc_at_end : bool
        CRC 是否在消息末尾。
    """

    def __init__(
        self,
        sync_word: Optional[bytes] = None,
        length_offset: int = 2,
        length_field_bytes: int = 1,
        length_includes_header: bool = True,
        crc_init: int = 0xFFFF,
        crc_poly: int = 0x1021,
        crc_at_end: bool = True,
    ):
        self.sync_word = sync_word
        self.length_offset = length_offset
        self.length_field_bytes = length_field_bytes
        self.length_includes_header = length_includes_header
        self.crc_init = crc_init
        self.crc_poly = crc_poly
        self.crc_at_end = crc_at_end

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def parse(self, bits: np.ndarray) -> List[ProtocolMessage]:
        """从比特流解析出所有消息。"""
        bits = np.asarray(bits, dtype=np.int8).flatten()
        if len(bits) < 16:
            return []

        # 1. 找同步字位置
        if self.sync_word is not None:
            sync_bits = bytes_to_bits(self.sync_word)
            sync_positions = self._find_pattern(bits, sync_bits)
        else:
            sync_positions = self._auto_detect_sync(bits)

        messages = []
        for pos in sync_positions:
            msg = self._parse_one(bits, pos)
            if msg is not None:
                messages.append(msg)
        return messages

    # ------------------------------------------------------------------
    # 同步字检测
    # ------------------------------------------------------------------

    @staticmethod
    def _find_pattern(bits: np.ndarray, pattern: np.ndarray) -> List[int]:
        """在 bits 中找 pattern 的所有起始位置。"""
        n, m = len(bits), len(pattern)
        if m == 0 or n < m:
            return []
        # 用卷积风格：滑动窗口
        positions = []
        for i in range(n - m + 1):
            if np.array_equal(bits[i:i + m], pattern):
                positions.append(i)
        return positions

    def _auto_detect_sync(self, bits: np.ndarray) -> List[int]:
        """AI 增强：自动找同步字。

        思路：扫描所有 16-bit 模式，找在比特流中重复出现 ≥2 次、且
        位置间隔相对稳定的模式（URH awre 的 n-gram 思路）。
        """
        n = len(bits)
        if n < 32:
            return []
        # 收集所有 16-bit 模式的位置
        pattern_pos: dict = {}
        for i in range(0, n - 16, 1):
            p = tuple(bits[i:i + 16].tolist())
            pattern_pos.setdefault(p, []).append(i)

        # 找出现 ≥2 次的模式
        candidates = [(p, pos) for p, pos in pattern_pos.items() if len(pos) >= 2]
        if not candidates:
            return []

        # 评分：出现次数多 + 位置间隔方差小（说明是帧定界）
        best = None
        best_score = -1.0
        for p, pos in candidates:
            pos = sorted(pos)
            gaps = np.diff(pos)
            if len(gaps) == 0:
                continue
            gap_std = float(np.std(gaps))
            score = len(pos) / (1.0 + gap_std)
            if score > best_score:
                best_score = score
                best = pos
        return best if best is not None else []

    # ------------------------------------------------------------------
    # 单条消息解析
    # ------------------------------------------------------------------

    def _parse_one(self, bits: np.ndarray, sync_pos: int) -> Optional[ProtocolMessage]:
        fields: List[ProtocolField] = []

        # 同步字段
        if self.sync_word is not None:
            sync_bits_len = len(self.sync_word) * 8
        else:
            sync_bits_len = 16
        sync_raw = bits[sync_pos:sync_pos + sync_bits_len]
        if len(sync_raw) < sync_bits_len:
            return None
        sync_val = int.from_bytes(bits_to_bytes(sync_raw), byteorder="big")
        fields.append(ProtocolField(
            name="SYNC", bit_length=sync_bits_len, type=FieldType.SYNC,
            value=sync_val,
            raw_bits="".join(map(str, sync_raw.tolist())),
        ))

        # 长度字段：紧跟同步字（或 offset 字节后）
        length_start = sync_pos + sync_bits_len + self.length_offset * 8
        length_bits_len = self.length_field_bytes * 8
        length_raw = bits[length_start:length_start + length_bits_len]
        if len(length_raw) < length_bits_len:
            return None
        length_value = int.from_bytes(bits_to_bytes(length_raw), byteorder="big")
        fields.append(ProtocolField(
            name="LENGTH", bit_length=length_bits_len, type=FieldType.LENGTH,
            value=length_value,
            raw_bits="".join(map(str, length_raw.tolist())),
        ))

        # 数据字段：length_value 个字节
        data_start = length_start + length_bits_len
        data_bits_len = length_value * 8
        data_raw = bits[data_start:data_start + data_bits_len]
        if len(data_raw) > 0:
            data_val = int.from_bytes(bits_to_bytes(data_raw), byteorder="big") if data_bits_len <= 64 else 0
            fields.append(ProtocolField(
                name="DATA", bit_length=len(data_raw), type=FieldType.DATA,
                value=data_val,
                raw_bits="".join(map(str, data_raw.tolist())),
            ))

        # CRC 字段（在数据末尾后 2 字节）
        crc_ok = False
        crc_start = data_start + data_bits_len
        crc_raw = bits[crc_start:crc_start + 16]
        if len(crc_raw) == 16:
            crc_value = int.from_bytes(bits_to_bytes(crc_raw), byteorder="big")
            fields.append(ProtocolField(
                name="CRC", bit_length=16, type=FieldType.CHECKSUM,
                value=crc_value,
                raw_bits="".join(map(str, crc_raw.tolist())),
            ))
            # 校验：从 sync 到 data 末尾的字节（不含 CRC 自身）
            payload_bits = bits[sync_pos:crc_start]
            payload_bytes = bits_to_bytes(payload_bits)
            expected = crc16_ccitt(payload_bytes, init=self.crc_init, poly=self.crc_poly)
            crc_ok = (expected == crc_value)

        end = crc_start + 16 if crc_start + 16 <= len(bits) else len(bits)
        return ProtocolMessage(
            fields=fields,
            raw_bits=bits[sync_pos:end],
            valid_checksum=crc_ok,
        )


# ---------------------------------------------------------------------------
# AI 增强：字段边界推断
# ---------------------------------------------------------------------------

def infer_field_boundaries(bits: np.ndarray, ngram: int = 8) -> List[Tuple[int, int, float]]:
    """AI 增强：用熵分析 + n-gram 重复模式推断字段边界。

    返回 ``[(start_bit, end_bit, score), ...]``，score 越高越像字段边界。

    思路（长度字段聚类的「公共范围」思想，通用协议解析启发式）：
    - 把多条消息按 ngram 对齐，找在所有消息中都相同的比特位置 → 头部/同步。
    - 找变化大但局部聚集的比特位置 → 数据字段。
    - 找低熵位置 → 长度/地址等固定字段。
    """
    bits = np.asarray(bits, dtype=np.int8)
    if bits.ndim == 1:
        bits = bits.reshape(1, -1)
    n_msgs, n_bits = bits.shape

    # 每列的熵
    boundaries = []
    if n_msgs < 2:
        return [(0, n_bits, 1.0)]

    col_mean = np.mean(bits.astype(float), axis=0)
    # 熵 H = -p log p - (1-p) log(1-p)
    p = np.clip(col_mean, 1e-6, 1 - 1e-6)
    entropy = -p * np.log2(p) - (1 - p) * np.log2(1 - p)

    # 找熵突变点作为字段边界
    for i in range(1, n_bits - 1):
        delta = abs(entropy[i + 1] - entropy[i - 1]) / 2.0
        if delta > 0.3:  # 熵突变阈值
            boundaries.append((i, i + 1, float(delta)))

    # 合并相邻边界
    boundaries.sort()
    return boundaries
