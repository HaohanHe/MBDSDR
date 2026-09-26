"""纯软件 DMR 解码器：复数 IQ -> 4FSK 基带 -> 符号同步 -> DMR 成帧 -> 时隙/源ID/目的ID/色码。

本模块不依赖 scipy，仅用 numpy + 标准库；复用 dsdcc_lite.rrc_impulse_response 与
同步字极性思路。所有 DMR 协议常量与 FEC 算法均对照 MMDVMHost 源码实现，关键位置标注
``repos/MMDVMHost/<file>:<line>``。

参考源码清单（本任务实读）：
  - DMRDefines.h:24-28        帧长 264bit/33byte，同步 48bit
  - DMRDefines.h:42-54        BS/MS sourced audio/data 同步字 7 字节 + SYNC_MASK
  - DMRDefines.h:71-76        Full LC CRC 掩码（Voice LC Header 0x96..., Terminator 0x99...）
  - DMRDefines.h:83-94        Data Type 枚举（VOICE_LC_HEADER=0x01 等）
  - DMRDefines.h:116-124      FLCO 枚举（GROUP=0, USER_USER=3）
  - Golay2087.cpp:26-264      Golay(20,8,7) 编码表/伴随式译码，GENPOL=0xC75
  - QR1676.cpp:27-117         QR(16,7,6) 缩短汉明，GENPOL=0x139
  - Hamming.cpp:24-180        Hamming(15,11,3)_2 与 Hamming(13,9,3) 校验矩阵
  - RS129.cpp:33-131          RS(12,9) GF(256) 编码/校验，本原多项式 0x11D
  - BPTC19696.cpp:83-349      BPTC(196,96) 解交织 (a*181)%196 + 行列 Hamming 纠错
  - DMRFullLC.cpp:40-100      Full LC = RS(12,9) + BPTC(196,96) + CRC 掩码异或
  - DMRLC.cpp:49-138          LC 9 字节布局（PF/R/FLCO/FID/options/DstId/SrcId）
  - DMRSlotType.cpp:37-73     Slot Type 20bit 散布 + Golay 译码 -> ColorCode/DataType
  - DMREMB.cpp:38-71          EMB 16bit 散布 + QR 译码 -> ColorCode/PI/LCSS
  - op25 fsk4_demod_ff        正交鉴频 + MM 符号同步 + 判决引导 AGC + 4 电平切片

解调管线（参考 op25 p25_demodulator.py / fsk4_demod_ff_impl.cc）：
  1. 正交鉴频：y = unwrap(angle(z[1:]*conj(z[:-1]))) * fs/(2*pi*deviation)
  2. RRC 匹配滤波（alpha=0.2，复用 dsdcc_lite.rrc_impulse_response）
  3. Mueller-Muller 判决引导符号定时恢复（含最佳相位兜底）
  4. 4 电平判决（门限 0/±2，判决引导 spread AGC）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from mbdsdr_ai.dsdcc_lite import (
    RRC_ALPHA_DMR,
    rrc_impulse_response,
)

# --------------------------------------------------------------------------- #
# 协议常量（来源: repos/MMDVMHost/DMRDefines.h）
# --------------------------------------------------------------------------- #
DMR_FRAME_LENGTH_BITS = 264   # DMRDefines.h:24
DMR_FRAME_LENGTH_BYTES = 33   # DMRDefines.h:25
DMR_SYNC_LENGTH_BITS = 48     # DMRDefines.h:27

#: 同步字掩码（DMRDefines.h:54）：byte13 低4bit + byte14-18 全8bit + byte19 高4bit
SYNC_MASK = (0x0F, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xF0)

#: 四组 48bit 同步字，7 字节数组（有效位由 SYNC_MASK 裁剪）—— DMRDefines.h:42-46
BS_SOURCED_AUDIO_SYNC = (0x07, 0x55, 0xFD, 0x7D, 0xF7, 0x5F, 0x70)  # h:42
BS_SOURCED_DATA_SYNC = (0x0D, 0xFF, 0x57, 0xD7, 0x5D, 0xF5, 0xD0)   # h:43
MS_SOURCED_AUDIO_SYNC = (0x07, 0xF7, 0xD5, 0xDD, 0x57, 0xDF, 0xD0)  # h:45
MS_SOURCED_DATA_SYNC = (0x0D, 0x5D, 0x7F, 0x77, 0xFD, 0x75, 0x70)   # h:46

#: 同步字 CRC 掩码（DMRDefines.h:71-72）
VOICE_LC_HEADER_CRC_MASK = (0x96, 0x96, 0x96)   # h:71
TERMINATOR_WITH_LC_CRC_MASK = (0x99, 0x99, 0x99)  # h:72

#: Data Type 枚举（DMRDefines.h:83-94）
DT_VOICE_PI_HEADER = 0x00
DT_VOICE_LC_HEADER = 0x01
DT_TERMINATOR_WITH_LC = 0x02
DT_CSBK = 0x03
DT_DATA_HEADER = 0x06
DT_IDLE = 0x09

#: FLCO 枚举（DMRDefines.h:116-124）
FLCO_GROUP = 0       # 群呼/通话组
FLCO_USER_USER = 3   # 个呼

#: DMR 符号率与典型采样率
DMR_SYMBOL_RATE = 4800.0
DMR_DEVIATION = 600.0  # Hz，参考 op25 p25_demodulator.py:427-428


# --------------------------------------------------------------------------- #
# 位/字节工具
# --------------------------------------------------------------------------- #
def _byte_to_bits_be(b: int) -> List[int]:
    """一个字节 -> 8 个 bit，MSB 在前（对应 CUtils::byteToBitsBE）。"""
    return [(b >> (7 - i)) & 1 for i in range(8)]


def _bits_to_byte_be(bits: Sequence[int], off: int = 0) -> int:
    """8 个 bit（从 bits[off] 起）-> 一个字节，MSB 在前（CUtils::bitsToByteBE）。"""
    v = 0
    for i in range(8):
        v = (v << 1) | (int(bits[off + i]) & 1)
    return v & 0xFF


def _bytes_to_bits_be(data: Sequence[int]) -> List[int]:
    out: List[int] = []
    for b in data:
        out.extend(_byte_to_bits_be(b))
    return out


def _bits_to_bytes_be(bits: Sequence[int]) -> List[int]:
    n = len(bits) // 8
    return [_bits_to_byte_be(bits, i * 8) for i in range(n)]


# --------------------------------------------------------------------------- #
# Golay(20,8,7) —— 来源: Golay2087.cpp
# GENPOL=0xC75, X18=0x40000, X11=0x800, MASK8=0xFFFFF800 (Golay2087.cpp:210-213)
# --------------------------------------------------------------------------- #
_GOLAY_X18 = 0x40000
_GOLAY_X11 = 0x800
_GOLAY_MASK = 0xFFFFF800
_GOLAY_GENPOL = 0xC75


def _golay2087_syndrome(pattern: int) -> int:
    """伴随式：多项式除法余数（Golay2087.cpp:215-238 getSyndrome1987）。"""
    aux = _GOLAY_X18
    if pattern >= _GOLAY_X11:
        while pattern & _GOLAY_MASK:
            while not (aux & pattern):
                aux >>= 1
            pattern ^= (aux // _GOLAY_X11) * _GOLAY_GENPOL
    return pattern


def _build_golay2087_decode_table() -> Dict[int, int]:
    """在 19bit 码空间里枚举重量<=3 的错误图样，syndrome->error。"""
    table: Dict[int, int] = {}
    n = 19
    table[0] = 0
    for w in range(1, 4):
        # 枚举组合
        idxs = list(range(w))
        while True:
            err = 0
            for i in idxs:
                err |= 1 << i
            syn = _golay2087_syndrome(err)
            if syn not in table:
                table[syn] = err
            # 下一个组合
            k = w - 1
            while k >= 0 and idxs[k] == n - w + k:
                k -= 1
            if k < 0:
                break
            idxs[k] += 1
            for j in range(k + 1, w):
                idxs[j] = idxs[j - 1] + 1
    return table


_GOLAY_DEC_TABLE = _build_golay2087_decode_table()


def golay2087_encode(info8: int) -> int:
    """8bit 信息 -> 19bit 码字（Golay2087.cpp:254-264 encode 等价）。

    返回 19bit 整数；info 在高 8 位。
    """
    info8 &= 0xFF
    cksum = _golay2087_syndrome(info8 << 11)
    return (info8 << 11) | cksum


def golay2087_decode(code19: int) -> Tuple[int, bool]:
    """19bit 接收码 -> (8bit 信息, 是否纠错)。

    对应 Golay2087.cpp:240-252 decode。
    """
    code19 &= 0x7FFFF
    syn = _golay2087_syndrome(code19)
    err = _GOLAY_DEC_TABLE.get(syn, 0)
    corrected = err != 0
    fixed = code19 ^ err
    return (fixed >> 11) & 0xFF, corrected


# --------------------------------------------------------------------------- #
# QR(16,7,6) 缩短汉明 —— 来源: QR1676.cpp
# GENPOL=0x139, X14=0x4000, X8=0x100, MASK7=0xFFFFFF00 (QR1676.cpp:64-67)
# --------------------------------------------------------------------------- #
_QR_X14 = 0x4000
_QR_X8 = 0x100
_QR_MASK = 0xFFFFFF00
_QR_GENPOL = 0x139


def _qr1676_syndrome(pattern: int) -> int:
    """QR 伴随式（QR1676.cpp:69-92 getSyndrome1576）。"""
    aux = _QR_X14
    if pattern >= _QR_X8:
        while pattern & _QR_MASK:
            while not (aux & pattern):
                aux >>= 1
            pattern ^= (aux // _QR_X8) * _QR_GENPOL
    return pattern


def _build_qr1676_decode_table() -> Dict[int, int]:
    """15bit 码空间，重量<=3 错误图样。"""
    table: Dict[int, int] = {0: 0}
    n = 15
    for w in range(1, 4):
        idxs = list(range(w))
        while True:
            err = 0
            for i in idxs:
                err |= 1 << i
            syn = _qr1676_syndrome(err)
            if syn not in table:
                table[syn] = err
            k = w - 1
            while k >= 0 and idxs[k] == n - w + k:
                k -= 1
            if k < 0:
                break
            idxs[k] += 1
            for j in range(k + 1, w):
                idxs[j] = idxs[j - 1] + 1
    return table


_QR_DEC_TABLE = _build_qr1676_decode_table()


def qr1676_encode(info7: int) -> int:
    """7bit 信息 -> 15bit 码字（QR1676.cpp:95-104 encode 等价）。"""
    info7 &= 0x7F
    cksum = _qr1676_syndrome(info7 << 8)
    return (info7 << 8) | cksum


def qr1676_decode(code15: int) -> Tuple[int, bool]:
    """15bit 接收码 -> (7bit 信息, 是否纠错)（QR1676.cpp:106-117 decode）。"""
    code15 &= 0x7FFF
    syn = _qr1676_syndrome(code15)
    err = _QR_DEC_TABLE.get(syn, 0)
    corrected = err != 0
    fixed = code15 ^ err
    return (fixed >> 8) & 0x7F, corrected


# --------------------------------------------------------------------------- #
# Hamming(15,11,3)_2 与 Hamming(13,9,3) —— 来源: Hamming.cpp
# --------------------------------------------------------------------------- #
def hamming15113_encode(d: List[int]) -> None:
    """就地计算 15bit 行校验位（Hamming.cpp:120-129 encode15113_2）。"""
    d[11] = d[0] ^ d[1] ^ d[2] ^ d[3] ^ d[5] ^ d[7] ^ d[8]
    d[12] = d[1] ^ d[2] ^ d[3] ^ d[4] ^ d[6] ^ d[8] ^ d[9]
    d[13] = d[2] ^ d[3] ^ d[4] ^ d[5] ^ d[7] ^ d[9] ^ d[10]
    d[14] = d[0] ^ d[1] ^ d[2] ^ d[4] ^ d[6] ^ d[7] ^ d[10]


def hamming15113_decode(d: List[int]) -> bool:
    """就地纠错，返回是否纠正了一位错误（Hamming.cpp:79-118 decode15113_2）。"""
    c0 = d[0] ^ d[1] ^ d[2] ^ d[3] ^ d[5] ^ d[7] ^ d[8]
    c1 = d[1] ^ d[2] ^ d[3] ^ d[4] ^ d[6] ^ d[8] ^ d[9]
    c2 = d[2] ^ d[3] ^ d[4] ^ d[5] ^ d[7] ^ d[9] ^ d[10]
    c3 = d[0] ^ d[1] ^ d[2] ^ d[4] ^ d[6] ^ d[7] ^ d[10]
    n = 0
    n |= 0x01 if c0 != d[11] else 0
    n |= 0x02 if c1 != d[12] else 0
    n |= 0x04 if c2 != d[13] else 0
    n |= 0x08 if c3 != d[14] else 0
    if n == 0:
        return False
    flip = {
        0x01: 11, 0x02: 12, 0x04: 13, 0x08: 14,
        0x09: 0, 0x0B: 1, 0x0F: 2, 0x07: 3, 0x0E: 4,
        0x05: 5, 0x0A: 6, 0x0D: 7, 0x03: 8, 0x06: 9, 0x0C: 10,
    }.get(n)
    if flip is None:
        return False
    d[flip] ^= 1
    return True


def hamming1393_encode(d: List[int]) -> None:
    """就地计算 13bit 列校验位（Hamming.cpp:171-180 encode1393）。"""
    d[9] = d[0] ^ d[1] ^ d[3] ^ d[5] ^ d[6]
    d[10] = d[0] ^ d[1] ^ d[2] ^ d[4] ^ d[6] ^ d[7]
    d[11] = d[0] ^ d[1] ^ d[2] ^ d[3] ^ d[5] ^ d[7] ^ d[8]
    d[12] = d[0] ^ d[2] ^ d[4] ^ d[5] ^ d[8]


def hamming1393_decode(d: List[int]) -> bool:
    """就地纠错，返回是否纠正（Hamming.cpp:132-169 decode1393）。"""
    c0 = d[0] ^ d[1] ^ d[3] ^ d[5] ^ d[6]
    c1 = d[0] ^ d[1] ^ d[2] ^ d[4] ^ d[6] ^ d[7]
    c2 = d[0] ^ d[1] ^ d[2] ^ d[3] ^ d[5] ^ d[7] ^ d[8]
    c3 = d[0] ^ d[2] ^ d[4] ^ d[5] ^ d[8]
    n = 0
    n |= 0x01 if c0 != d[9] else 0
    n |= 0x02 if c1 != d[10] else 0
    n |= 0x04 if c2 != d[11] else 0
    n |= 0x08 if c3 != d[12] else 0
    if n == 0:
        return False
    flip = {
        0x01: 9, 0x02: 10, 0x04: 11, 0x08: 12,
        0x0F: 0, 0x07: 1, 0x0E: 2, 0x05: 3, 0x0A: 4,
        0x0D: 5, 0x03: 6, 0x06: 7, 0x0C: 8,
    }.get(n)
    if flip is None:
        return False
    d[flip] ^= 1
    return True


# --------------------------------------------------------------------------- #
# RS(12,9) over GF(256) —— 来源: RS129.cpp
# 本原多项式 0x11D，生成多项式 POLY={64,56,14,1,0}（RS129.cpp:33）
# --------------------------------------------------------------------------- #
_RS_EXP = [
    0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1D, 0x3A, 0x74, 0xE8, 0xCD, 0x87, 0x13, 0x26,
    0x4C, 0x98, 0x2D, 0x5A, 0xB4, 0x75, 0xEA, 0xC9, 0x8F, 0x03, 0x06, 0x0C, 0x18, 0x30, 0x60, 0xC0,
    0x9D, 0x27, 0x4E, 0x9C, 0x25, 0x4A, 0x94, 0x35, 0x6A, 0xD4, 0xB5, 0x77, 0xEE, 0xC1, 0x9F, 0x23,
    0x46, 0x8C, 0x05, 0x0A, 0x14, 0x28, 0x50, 0xA0, 0x5D, 0xBA, 0x69, 0xD2, 0xB9, 0x6F, 0xDE, 0xA1,
    0x5F, 0xBE, 0x61, 0xC2, 0x99, 0x2F, 0x5E, 0xBC, 0x65, 0xCA, 0x89, 0x0F, 0x1E, 0x3C, 0x78, 0xF0,
    0xFD, 0xE7, 0xD3, 0xBB, 0x6B, 0xD6, 0xB1, 0x7F, 0xFE, 0xE1, 0xDF, 0xA3, 0x5B, 0xB6, 0x71, 0xE2,
    0xD9, 0xAF, 0x43, 0x86, 0x11, 0x22, 0x44, 0x88, 0x0D, 0x1A, 0x34, 0x68, 0xD0, 0xBD, 0x67, 0xCE,
    0x81, 0x1F, 0x3E, 0x7C, 0xF8, 0xED, 0xC7, 0x93, 0x3B, 0x76, 0xEC, 0xC5, 0x97, 0x33, 0x66, 0xCC,
    0x85, 0x17, 0x2E, 0x5C, 0xB8, 0x6D, 0xDA, 0xA9, 0x4F, 0x9E, 0x21, 0x42, 0x84, 0x15, 0x2A, 0x54,
    0xA8, 0x4D, 0x9A, 0x29, 0x52, 0xA4, 0x55, 0xAA, 0x49, 0x92, 0x39, 0x72, 0xE4, 0xD5, 0xB7, 0x73,
    0xE6, 0xD1, 0xBF, 0x63, 0xC6, 0x91, 0x3F, 0x7E, 0xFC, 0xE5, 0xD7, 0xB3, 0x7B, 0xF6, 0xF1, 0xFF,
    0xE3, 0xDB, 0xAB, 0x4B, 0x96, 0x31, 0x62, 0xC4, 0x95, 0x37, 0x6E, 0xDC, 0xA5, 0x57, 0xAE, 0x41,
    0x82, 0x19, 0x32, 0x64, 0xC8, 0x8D, 0x07, 0x0E, 0x1C, 0x38, 0x70, 0xE0, 0xDD, 0xA7, 0x53, 0xA6,
    0x51, 0xA2, 0x59, 0xB2, 0x79, 0xF2, 0xF9, 0xEF, 0xC3, 0x9B, 0x2B, 0x56, 0xAC, 0x45, 0x8A, 0x09,
    0x12, 0x24, 0x48, 0x90, 0x3D, 0x7A, 0xF4, 0xF5, 0xF7, 0xF3, 0xFB, 0xEB, 0xCB, 0x8B, 0x0B, 0x16,
    0x2C, 0x58, 0xB0, 0x7D, 0xFA, 0xE9, 0xCF, 0x83, 0x1B, 0x36, 0x6C, 0xD8, 0xAD, 0x47, 0x8E, 0x01,
]
_RS_LOG = [
    0x00, 0x00, 0x01, 0x19, 0x02, 0x32, 0x1A, 0xC6, 0x03, 0xDF, 0x33, 0xEE, 0x1B, 0x68, 0xC7, 0x4B,
    0x04, 0x64, 0xE0, 0x0E, 0x34, 0x8D, 0xEF, 0x81, 0x1C, 0xC1, 0x69, 0xF8, 0xC8, 0x08, 0x4C, 0x71,
    0x05, 0x8A, 0x65, 0x2F, 0xE1, 0x24, 0x0F, 0x21, 0x35, 0x93, 0x8E, 0xDA, 0xF0, 0x12, 0x82, 0x45,
    0x1D, 0xB5, 0xC2, 0x7D, 0x6A, 0x27, 0xF9, 0xB9, 0xC9, 0x9A, 0x09, 0x78, 0x4D, 0xE4, 0x72, 0xA6,
    0x06, 0xBF, 0x8B, 0x62, 0x66, 0xDD, 0x30, 0xFD, 0xE2, 0x98, 0x25, 0xB3, 0x10, 0x91, 0x22, 0x88,
    0x36, 0xD0, 0x94, 0xCE, 0x8F, 0x96, 0xDB, 0xBD, 0xF1, 0xD2, 0x13, 0x5C, 0x83, 0x38, 0x46, 0x40,
    0x1E, 0x42, 0xB6, 0xA3, 0xC3, 0x48, 0x7E, 0x6E, 0x6B, 0x3A, 0x28, 0x54, 0xFA, 0x85, 0xBA, 0x3D,
    0xCA, 0x5E, 0x9B, 0x9F, 0x0A, 0x15, 0x79, 0x2B, 0x4E, 0xD4, 0xE5, 0xAC, 0x73, 0xF3, 0xA7, 0x57,
    0x07, 0x70, 0xC0, 0xF7, 0x8C, 0x80, 0x63, 0x0D, 0x67, 0x4A, 0xDE, 0xED, 0x31, 0xC5, 0xFE, 0x18,
    0xE3, 0xA5, 0x99, 0x77, 0x26, 0xB8, 0xB4, 0x7C, 0x11, 0x44, 0x92, 0xD9, 0x23, 0x20, 0x89, 0x2E,
    0x37, 0x3F, 0xD1, 0x5B, 0x95, 0xBC, 0xCF, 0xCD, 0x90, 0x87, 0x97, 0xB2, 0xDC, 0xFC, 0xBE, 0x61,
    0xF2, 0x56, 0xD3, 0xAB, 0x14, 0x2A, 0x5D, 0x9E, 0x84, 0x3C, 0x39, 0x53, 0x47, 0x6D, 0x41, 0xA2,
    0x1F, 0x2D, 0x43, 0xD8, 0xB7, 0x7B, 0xA4, 0x76, 0xC4, 0x17, 0x49, 0xEC, 0x7F, 0x0C, 0x6F, 0xF6,
    0x6C, 0xA1, 0x3B, 0x52, 0x29, 0x9D, 0x55, 0xAA, 0xFB, 0x60, 0x86, 0xB1, 0xBB, 0xCC, 0x3E, 0x5A,
    0xCB, 0x59, 0x5F, 0xB0, 0x9C, 0xA9, 0xA0, 0x51, 0x0B, 0xF5, 0x16, 0xEB, 0x7A, 0x75, 0x2C, 0xD7,
    0x4F, 0xAE, 0xD5, 0xE9, 0xE6, 0xE7, 0xAD, 0xE8, 0x74, 0xD6, 0xF4, 0xEA, 0xA8, 0x50, 0x58, 0xAF,
]
_RS_POLY = (64, 56, 14, 1, 0)


def _rs_gmult(a: int, b: int) -> int:
    """GF(256) 乘法（RS129.cpp:88-97 gmult）。"""
    if a == 0 or b == 0:
        return 0
    return _RS_EXP[(_RS_LOG[a] + _RS_LOG[b]) % 255]


def rs129_encode(data9: Sequence[int]) -> Tuple[int, int, int]:
    """9 字节信息 -> 3 字节奇偶校验（RS129.cpp:104-120 encode）。

    返回 (parity[2], parity[1], parity[0])，与 check() 的比较顺序一致。
    """
    npar = 3
    parity = [0, 0, 0, 0]
    for i in range(9):
        dbyte = data9[i] ^ parity[npar - 1]
        for j in range(npar - 1, 0, -1):
            parity[j] = parity[j - 1] ^ _rs_gmult(_RS_POLY[j], dbyte)
        parity[0] = _rs_gmult(_RS_POLY[0], dbyte)
    return parity[2], parity[1], parity[0]


def rs129_check(data12: Sequence[int]) -> bool:
    """校验 12 字节（9 信息 + 3 校验），通过返回 True（RS129.cpp:123-131 check）。"""
    p2, p1, p0 = rs129_encode(data12[:9])
    return data12[9] == p2 and data12[10] == p1 and data12[11] == p0


# --------------------------------------------------------------------------- #
# BPTC(196,96) —— 来源: BPTC19696.cpp
# 解交织 deInter[a] = raw[(a*181)%196]（BPTC19696.cpp:130）
# 96bit 载荷位置（BPTC19696.cpp:180-205）
# --------------------------------------------------------------------------- #
#: 96bit 载荷在 deInterData 中的下标区间（闭区间）
_BPTC_PAYLOAD_RANGES = (
    range(4, 12),    # 8 bit (row0)
    range(16, 27),   # 11 bit
    range(31, 42),
    range(46, 57),
    range(61, 72),
    range(76, 87),
    range(91, 102),
    range(106, 117),
    range(121, 132),
)


def _bptc_payload_indices() -> List[int]:
    idx: List[int] = []
    for r in _BPTC_PAYLOAD_RANGES:
        idx.extend(r)
    return idx


_BPTC_PAYLOAD_POS = _bptc_payload_indices()  # 96 个下标


def bptc19696_decode(raw196: Sequence[int]) -> Tuple[List[int], bool]:
    """196bit 接收 -> (96bit 载荷, 是否行列纠错有效)。

    对应 BPTC19696::decode（BPTC19696.cpp:46-62）。
    """
    raw = [int(b) & 1 for b in raw196]
    if len(raw) != 196:
        raise ValueError("BPTC 输入必须是 196 bit")

    # 解交织：deInter[a] = raw[(a*181)%196]（BPTC19696.cpp:128-133）
    deint = [0] * 196
    for a in range(196):
        deint[a] = raw[(a * 181) % 196]

    # 行列 Hamming 交替纠错最多 5 次（BPTC19696.cpp:141-172）
    for _ in range(5):
        fixing = False
        # 列校验 Hamming(13,9,3)，15 列（BPTC19696.cpp:146-162）
        for c in range(15):
            col = [deint[c + 1 + a * 15] for a in range(13)]
            if hamming1393_decode(col):
                for a in range(13):
                    deint[c + 1 + a * 15] = col[a]
                fixing = True
        # 行校验 Hamming(15,11,3)_2，9 行有数据（BPTC19696.cpp:165-169）
        for r in range(9):
            pos = r * 15 + 1
            row = deint[pos:pos + 15]
            if hamming15113_decode(row):
                deint[pos:pos + 15] = row
                fixing = True
        if not fixing:
            break

    payload = [deint[p] for p in _BPTC_PAYLOAD_POS]
    return payload, True


def bptc19696_encode(payload96: Sequence[int]) -> List[int]:
    """96bit 载荷 -> 196bit（BPTC19696::encode，BPTC19696.cpp:65-81）。"""
    if len(payload96) != 96:
        raise ValueError("BPTC 载荷必须是 96 bit")
    deint = [0] * 196
    for p, bit in zip(_BPTC_PAYLOAD_POS, payload96):
        deint[p] = int(bit) & 1

    # 行校验（BPTC19696.cpp:274-278）
    for r in range(9):
        pos = r * 15 + 1
        row = deint[pos:pos + 15]
        hamming15113_encode(row)
        deint[pos:pos + 15] = row
    # 列校验（BPTC19696.cpp:281-296）
    for c in range(15):
        col = [deint[c + 1 + a * 15] for a in range(13)]
        hamming1393_encode(col)
        for a in range(13):
            deint[c + 1 + a * 15] = col[a]

    # 交织：raw[(a*181)%196] = deint[a]（BPTC19696.cpp:300-311）
    raw = [0] * 196
    for a in range(196):
        raw[(a * 181) % 196] = deint[a]
    return raw


# --------------------------------------------------------------------------- #
# 33 字节帧 <-> 196bit BPTC 数据的映射（BPTC19696.cpp:83-119, 314-349）
# --------------------------------------------------------------------------- #
def _frame_to_bptc_raw(frame: Sequence[int]) -> List[int]:
    """33 字节帧 -> 196bit raw（decodeExtractBinary，BPTC19696.cpp:83-119）。"""
    raw = [0] * 196
    for i in range(13):  # byte0..12 -> raw[0:104]
        bits = _byte_to_bits_be(frame[i])
        for j in range(8):
            raw[i * 8 + j] = bits[j]
    # byte20 的 bit1,bit0 -> raw[98],raw[99]（覆盖 byte12 写入的对应位）
    b20 = _byte_to_bits_be(frame[20])
    raw[98] = b20[6]
    raw[99] = b20[7]
    for i in range(12):  # byte21..32 -> raw[100:196]
        bits = _byte_to_bits_be(frame[21 + i])
        for j in range(8):
            raw[100 + i * 8 + j] = bits[j]
    return raw


def _bptc_raw_to_frame(raw196: Sequence[int], frame: bytearray) -> None:
    """196bit raw -> 写回 33 字节帧的 BPTC 区域（encodeExtractBinary，BPTC19696.cpp:314-349）。"""
    for i in range(12):  # raw[0:96] -> byte0..11
        frame[i] = _bits_to_byte_be(raw196, i * 8)
    # raw[96:104] 打包成临时字节
    tmp = _bits_to_byte_be(raw196, 96)
    frame[12] = (frame[12] & 0x3F) | ((tmp >> 0) & 0xC0)   # raw[96],97 -> byte12 bit7,6
    frame[20] = (frame[20] & 0xFC) | ((tmp >> 4) & 0x03)   # raw[99],98 -> byte20 bit1,0
    for i in range(12):  # raw[100:196] -> byte21..32
        frame[21 + i] = _bits_to_byte_be(raw196, 100 + i * 8)


# --------------------------------------------------------------------------- #
# Slot Type 散布（DMRSlotType.cpp:37-73）
# --------------------------------------------------------------------------- #
def _slot_type_golay_to_frame(cc: int, dt: int, frame: bytearray) -> None:
    """把 (color_code, data_type) Golay 编码后散布进 frame（DMRSlotType.cpp:57-73）。"""
    info8 = ((cc & 0x0F) << 4) | (dt & 0x0F)
    code19 = golay2087_encode(info8)  # 19 bit
    # 拆成 3 字节（与 putData 的逆过程一致）
    st0 = (code19 >> 11) & 0xFF       # bits 18:11
    st1 = (code19 >> 3) & 0xFF        # bits 10:3
    st2 = (code19 & 0x07) << 5        # bits 2:0 -> 高3位

    frame[12] = (frame[12] & 0xC0) | ((st0 >> 2) & 0x3F)
    frame[13] = (frame[13] & 0x0F) | ((st0 << 6) & 0xC0) | ((st1 >> 2) & 0x30)
    frame[19] = (frame[19] & 0xF0) | ((st1 >> 2) & 0x0F)
    frame[20] = (frame[20] & 0x03) | ((st1 << 6) & 0xC0) | ((st2 >> 2) & 0x3C)


def _frame_to_slot_type(frame: Sequence[int]) -> Tuple[int, int]:
    """从 frame 解出 (color_code, data_type)（DMRSlotType.cpp:37-55 putData）。"""
    st0 = ((frame[12] << 2) & 0xFC) | ((frame[13] >> 6) & 0x03)
    st1 = ((frame[13] << 2) & 0xC0) | ((frame[19] << 2) & 0x3C) | ((frame[20] >> 6) & 0x03)
    st2 = (frame[20] << 2) & 0xF0
    code19 = (st0 << 11) | (st1 << 3) | (st2 >> 5)
    info8, _ = golay2087_decode(code19)
    cc = (info8 >> 4) & 0x0F
    dt = info8 & 0x0F
    return cc, dt


# --------------------------------------------------------------------------- #
# 同步字嵌入/提取（DMRDefines.h:42-54）
# --------------------------------------------------------------------------- #
def _embed_sync(frame: bytearray, sync7: Sequence[int]) -> None:
    """把 7 字节同步字按 SYNC_MASK 嵌入 frame[13:20]。"""
    frame[13] = (frame[13] & 0xF0) | (sync7[0] & 0x0F)
    frame[14] = sync7[1]
    frame[15] = sync7[2]
    frame[16] = sync7[3]
    frame[17] = sync7[4]
    frame[18] = sync7[5]
    frame[19] = (frame[19] & 0x0F) | (sync7[6] & 0xF0)


def _extract_sync48(frame: Sequence[int]) -> List[int]:
    """从 frame[13:20] 按 SYNC_MASK 提取 48bit 同步位。"""
    bits: List[int] = []
    for i in range(7):
        mask = SYNC_MASK[i]
        b = frame[13 + i] & mask
        # 把有效位展开成 bit 序列（左对齐到字节，再按 mask 取有效位）
        full = _byte_to_bits_be(b)
        # mask 中为 1 的位才有效
        for j in range(8):
            if (mask >> (7 - j)) & 1:
                bits.append(full[j])
    return bits


#: 同步字 48bit 展开（预计算，用于匹配）
def _sync7_to_bits(sync7: Sequence[int]) -> List[int]:
    bits: List[int] = []
    for i in range(7):
        mask = SYNC_MASK[i]
        b = sync7[i] & mask
        full = _byte_to_bits_be(b)
        for j in range(8):
            if (mask >> (7 - j)) & 1:
                bits.append(full[j])
    return bits


_SYNC_TABLE = {
    "BS_VOICE": _sync7_to_bits(BS_SOURCED_AUDIO_SYNC),
    "BS_DATA": _sync7_to_bits(BS_SOURCED_DATA_SYNC),
    "MS_VOICE": _sync7_to_bits(MS_SOURCED_AUDIO_SYNC),
    "MS_DATA": _sync7_to_bits(MS_SOURCED_DATA_SYNC),
}


# --------------------------------------------------------------------------- #
# LC 9 字节构造/解析（DMRLC.cpp:49-138）
# --------------------------------------------------------------------------- #
def _build_lc9(src_id: int, dst_id: int, flco: int) -> List[int]:
    """构造 9 字节 LC（DMRLC.cpp:115-138 getData）。"""
    return [
        flco & 0x3F,          # byte0: PF=0,R=0,FLCO
        0x00,                 # byte1: FID = ETSI (0)
        0x00,                 # byte2: options
        (dst_id >> 16) & 0xFF,
        (dst_id >> 8) & 0xFF,
        dst_id & 0xFF,
        (src_id >> 16) & 0xFF,
        (src_id >> 8) & 0xFF,
        src_id & 0xFF,
    ]


def _parse_lc9(lc9: Sequence[int]) -> Tuple[int, int, int, int]:
    """解析 9 字节 LC -> (flco, dst_id, src_id, fid)（DMRLC.cpp:49-60）。"""
    flco = lc9[0] & 0x3F
    fid = lc9[1]
    dst_id = (lc9[3] << 16) | (lc9[4] << 8) | lc9[5]
    src_id = (lc9[6] << 16) | (lc9[7] << 8) | lc9[8]
    return flco, dst_id, src_id, fid


# --------------------------------------------------------------------------- #
# 编码函数：构造 Voice LC Header 33 字节帧
# --------------------------------------------------------------------------- #
def dmr_encode_voice_lc_header(src_id: int, dst_id: int, slot: int = 0,
                               color_code: int = 1, flco: int = FLCO_GROUP) -> bytes:
    """构造一个 Voice LC Header 数据帧（33 字节）。

    包含：BS sourced data 同步字 + Slot Type(Golay) + BPTC(RS(12,9)) 编码的 LC。
    对应 MMDVMHost CDMRFullLC::encode + CDMRSlotType::getData。
    """
    frame = bytearray(33)

    # 1) LC 9 字节 + RS(12,9) -> 12 字节
    lc9 = _build_lc9(src_id, dst_id, flco)
    p2, p1, p0 = rs129_encode(lc9)
    lc12 = list(lc9) + [p2, p1, p0]
    # CRC 掩码异或（DMRDefines.h:71，Voice LC Header）
    lc12[9] ^= VOICE_LC_HEADER_CRC_MASK[0]
    lc12[10] ^= VOICE_LC_HEADER_CRC_MASK[1]
    lc12[11] ^= VOICE_LC_HEADER_CRC_MASK[2]

    # 2) BPTC(196,96) 编码 12 字节 -> 196bit -> 写入帧
    payload96 = _bytes_to_bits_be(lc12)
    raw196 = bptc19696_encode(payload96)
    _bptc_raw_to_frame(raw196, frame)

    # 3) Slot Type：color_code + data_type=VOICE_LC_HEADER
    _slot_type_golay_to_frame(color_code, DT_VOICE_LC_HEADER, frame)

    # 4) 同步字：BS sourced data（Voice LC Header 是数据突发）
    _embed_sync(frame, BS_SOURCED_DATA_SYNC)

    return bytes(frame)


# --------------------------------------------------------------------------- #
# 4FSK 调制：33 字节帧 -> 复数 IQ / 实数基带
# --------------------------------------------------------------------------- #
#: dibit -> 发送电平（DMR 标准：0=+1, 1=+3, 2=-1, 3=-3）
DIBIT_LEVELS = (1.0, 3.0, -1.0, -3.0)


def _frame_to_dibits(frame: Sequence[int]) -> np.ndarray:
    """33 字节（264bit）-> 132 dibit（uint8）。"""
    bits = _bytes_to_bits_be(frame)  # 264 bit
    dibits = np.zeros(132, dtype=np.uint8)
    for i in range(132):
        dibits[i] = (bits[2 * i] << 1) | bits[2 * i + 1]
    return dibits


def dibits_to_levels(dibits: Sequence[int]) -> np.ndarray:
    """dibit 流 -> 4 电平符号幅度（float）。"""
    arr = np.asarray(dibits, dtype=np.uint8)
    out = np.empty(len(arr), dtype=float)
    for d in (0, 1, 2, 3):
        out[arr == d] = DIBIT_LEVELS[d]
    return out


def _rrc_shape(levels: np.ndarray, sps: int) -> np.ndarray:
    """符号电平 -> RRC 成形的过采样基带波形。"""
    taps = rrc_impulse_response(sps, RRC_ALPHA_DMR)
    # 零阶保持上采样后卷积
    ups = np.zeros(len(levels) * sps, dtype=float)
    ups[::sps] = levels
    shaped = np.convolve(ups, taps, mode="same")
    return shaped


def dmr_encode_baseband(frame_bytes: Sequence[int], sample_rate: float = 48000.0,
                        sps: Optional[int] = None) -> np.ndarray:
    """33 字节帧 -> 实数基带波形（判别器输出等价，单位为 4FSK 电平）。"""
    if sps is None:
        sps = int(round(sample_rate / DMR_SYMBOL_RATE))
    dibits = _frame_to_dibits(frame_bytes)
    levels = dibits_to_levels(dibits)
    return _rrc_shape(levels, sps)


def dmr_encode_iq(frame_bytes: Sequence[int], sample_rate: float = 48000.0,
                  sps: Optional[int] = None) -> np.ndarray:
    """33 字节帧 -> 复数 IQ 信号（FM 调制）。

    FM 调制：phase = 2*pi * deviation * cumsum(baseband)/sample_rate；
    iq = exp(1j*phase)。baseband 幅值对应频偏（op25 p25_demodulator.py:427-428 逆过程）。
    """
    if sps is None:
        sps = int(round(sample_rate / DMR_SYMBOL_RATE))
    baseband = dmr_encode_baseband(frame_bytes, sample_rate, sps)
    # baseband 幅值（±1/±3）对应频偏 ±deviation
    freq_hz = baseband * DMR_DEVIATION
    phase = 2.0 * np.pi * np.cumsum(freq_hz) / sample_rate
    return np.exp(1j * phase)


# --------------------------------------------------------------------------- #
# 4FSK 解调：复数 IQ / 实数基带 -> dibit 流
# --------------------------------------------------------------------------- #
class DMRDemodulator:
    """4FSK(C4FM) 解调：鉴频 -> RRC 匹配滤波 -> 符号同步 -> 4 电平判决。

    参考 op25 fsk4_demod_ff_impl.cc（NCO+MMSE 插值+判决引导 TED）与
    dsdcc_lite.FourFSKDemod。
    """

    def __init__(self, sample_rate: float = 48000.0, symbol_rate: float = DMR_SYMBOL_RATE,
                 deviation: float = DMR_DEVIATION):
        self.sample_rate = float(sample_rate)
        self.symbol_rate = float(symbol_rate)
        self.deviation = float(deviation)
        self.sps = int(round(sample_rate / symbol_rate))
        self.taps = rrc_impulse_response(self.sps, RRC_ALPHA_DMR)
        # 判决引导 spread AGC（op25 fsk4_demod_ff_impl.cc:348-396）
        self._spread = 2.0

    # -- IQ -> 基带（正交鉴频） -------------------------------------------- #
    def _discriminate(self, iq: np.ndarray) -> np.ndarray:
        """复数 IQ -> 实数基带（正交鉴频）。

        y = angle(z[1:]*conj(z[:-1])) * fs/(2*pi*deviation)
        参考 op25 p25_demodulator.py:427-428。
        """
        z = np.asarray(iq, dtype=complex)
        if len(z) < 2:
            return np.zeros(0)
        phase_inc = np.angle(z[1:] * np.conj(z[:-1]))
        gain = self.sample_rate / (2.0 * np.pi * self.deviation)
        return phase_inc * gain

    # -- 符号定时恢复（Mueller-Muller 判决引导） ---------------------------- #
    def _mm_symbol_sync(self, mf: np.ndarray) -> np.ndarray:
        """Mueller-Muller 判决引导 TED，返回符号间隔采样值。

        参考 op25 fsk4_demod_ff_impl.cc:288-410。误差 e 做钳位防止大尖峰导致
        环发散（mu 抵消 omega 的死锁）。若收敛不佳，调用方应回退到最佳相位搜索。
        """
        sps = self.sps
        n = len(mf)
        out: List[float] = []
        mu = 0.0
        omega = float(sps)
        gain_mu = 0.01
        gain_omega = 0.0001
        omega_min = sps * 0.92
        omega_max = sps * 1.08
        prev_y = 0.0
        prev_dec = 0.0
        have_prev = False
        idx = 0.0
        max_iter = n * 2  # 安全上限，防止发散死循环
        for _ in range(max_iter):
            if idx >= n - 2:
                break
            i0 = int(idx)
            frac = idx - i0
            if i0 + 1 >= n:
                break
            y = mf[i0] * (1.0 - frac) + mf[i0 + 1] * frac  # 线性插值
            # MM 误差：y[n]*(decision(y[n-1]) - y[n-1])，钳位防发散
            if have_prev:
                e = y * (prev_dec - prev_y)
                if e > 2.0:
                    e = 2.0
                elif e < -2.0:
                    e = -2.0
                omega += gain_omega * e
                omega = max(omega_min, min(omega_max, omega))
                mu = gain_mu * e
            out.append(y)
            prev_y = y
            prev_dec = self._slice_level(y)
            have_prev = True
            idx += omega + mu
        return np.array(out)

    def _best_phase_symbols(self, mf: np.ndarray) -> np.ndarray:
        """兜底：遍历 sps 个相位，取符号值方差最大（最佳眼图张开）的相位采样。"""
        sps = self.sps
        best = None
        best_score = -1e18
        n_sym = (len(mf) - sps) // sps
        for p in range(sps):
            seg = mf[p:p + n_sym * sps].reshape(n_sym, sps)
            sym = seg[:, 0]
            # 分数：符号值绝对值的均值（眼图张开度）
            score = float(np.mean(np.abs(sym)))
            if score > best_score:
                best_score = score
                best = sym
        return best if best is not None else np.zeros(0)

    @staticmethod
    def _slice_level(v: float) -> float:
        """把浮点符号值硬判决到最近的理想电平 {-3,-1,+1,+3}。"""
        if v > 2.0:
            return 3.0
        if v > 0.0:
            return 1.0
        if v > -2.0:
            return -1.0
        return -3.0

    def _update_spread(self, sym_values: np.ndarray) -> None:
        """判决引导 spread AGC（op25 fsk4_demod_ff_impl.cc:348-396）。"""
        if sym_values.size == 0:
            return
        # 估计外层电平幅度
        mask = np.abs(sym_values) > 1.5
        if not np.any(mask):
            return
        outer = float(np.mean(np.abs(sym_values[mask])))
        if outer > 1e-6:
            target = 3.0
            self._spread += 0.01 * (target - outer)
            self._spread = max(1.6, min(2.4, self._spread))

    def _slice_dibits(self, sym_values: np.ndarray) -> np.ndarray:
        """符号浮点值 -> dibit {0,1,2,3}（op25 fsk4_slicer_fb_impl.cc:80-93）。

        归一化后门限 0/±2。dibit 编码：0=+1,1=+3,2=-1,3=-3。
        """
        s = self._spread
        dibits = np.empty(len(sym_values), dtype=np.uint8)
        for i, v in enumerate(sym_values):
            if v > s:
                dibits[i] = 1      # +3
            elif v > 0.0:
                dibits[i] = 0      # +1
            elif v > -s:
                dibits[i] = 2      # -1
            else:
                dibits[i] = 3      # -3
        return dibits

    def demod_baseband(self, baseband_samples: np.ndarray) -> np.ndarray:
        """已鉴频实数基带 -> dibit 流（uint8, 0-3）。"""
        bb = np.asarray(baseband_samples, dtype=float)
        if bb.size < self.sps * 8:
            return np.zeros(0, dtype=np.uint8)
        mf = np.convolve(bb, self.taps, mode="same")
        # 先尝试 MM 定时恢复
        sym = self._mm_symbol_sync(mf)
        # 若 MM 输出太短或能量弱，回退最佳相位
        if sym.size < 20 or np.mean(np.abs(sym)) < 0.3:
            sym = self._best_phase_symbols(mf)
        if sym.size == 0:
            return np.zeros(0, dtype=np.uint8)
        self._update_spread(sym)
        # 按当前 spread 归一化
        sym_norm = sym * (2.0 / self._spread)
        return self._slice_dibits(sym_norm)

    def demod_iq(self, iq_samples: np.ndarray) -> np.ndarray:
        """复数 IQ -> dibit 流。"""
        bb = self._discriminate(np.asarray(iq_samples, dtype=complex))
        return self.demod_baseband(bb)


# --------------------------------------------------------------------------- #
# DMR 帧结果 dataclass
# --------------------------------------------------------------------------- #
@dataclass
class DMRFrame:
    ok: bool = False
    slot: int = 0
    color_code: int = 0
    data_type: int = 0
    src_id: int = 0
    dst_id: int = 0
    flco: int = 0
    is_voice: bool = False
    sync_type: str = "unknown"
    raw_frame: bytes = b""


# --------------------------------------------------------------------------- #
# DMR 成帧器：同步检测 -> 帧解析 -> LC 解码
# --------------------------------------------------------------------------- #
class DMRFramer:
    """在 dibit 流中找 DMR 同步字并解出完整帧。

    帧 = 132 dibits = 33 字节。同步字 24 dibits（48bit）位于字节 13-19。
    """

    SYNC_LEN_DIBITS = 24  # 48bit / 2

    def _dibits_to_bits(self, dibits: Sequence[int]) -> List[int]:
        bits: List[int] = []
        for d in dibits:
            bits.append((int(d) >> 1) & 1)
            bits.append(int(d) & 1)
        return bits

    def find_sync(self, dibits: Sequence[int]) -> List[Tuple[int, str]]:
        """在 dibit 流中找同步字位置。返回 [(dibit_pos, sync_type), ...]。

        同步字 24 dibits = 48bit。逐位汉明距离 <= 2 即命中。
        """
        dib = np.asarray(dibits, dtype=np.uint8)
        n = len(dib)
        out: List[Tuple[int, str]] = []
        for pos in range(0, n - self.SYNC_LEN_DIBITS + 1):
            win_bits = self._dibits_to_bits(dib[pos:pos + self.SYNC_LEN_DIBITS])
            for name, ref in _SYNC_TABLE.items():
                dist = sum(1 for a, b in zip(win_bits, ref) if a != b)
                if dist <= 2:
                    out.append((int(pos), name))
        return out

    def decode_stream(self, dibits: Sequence[int]) -> List[DMRFrame]:
        """完整流解码：找同步 -> 解帧。"""
        hits = self.find_sync(dibits)
        frames: List[DMRFrame] = []
        used: set = set()
        for pos, name in hits:
            # 同步字 24 dibits 起点 = byte13 低 nibble = dibit 54
            # （byte13 = dibit52..55，SYNC_MASK=0x0F 取低 nibble = dibit54,55）
            # 整个 132 dibit 帧起点 = pos - 54
            start = pos - 54
            if start < 0 or start + 132 > len(dibits):
                continue
            # 避免重叠命中
            if any(abs(start - u) < 60 for u in used):
                continue
            used.add(start)
            fr = self.decode_frame(dibits, start)
            if fr.ok:
                frames.append(fr)
        return frames

    def decode_frame(self, dibits: Sequence[int], frame_start: int) -> DMRFrame:
        """从 dibit 流的 frame_start 解一个完整 132-dibit 帧。"""
        dib = list(dibits)
        if frame_start < 0 or frame_start + 132 > len(dib):
            return DMRFrame(ok=False)
        frame_dibits = dib[frame_start:frame_start + 132]
        bits = self._dibits_to_bits(frame_dibits)  # 264 bit
        frame_bytes = _bits_to_bytes_be(bits)  # 33 字节

        # 1) 同步字识别
        sync_bits = _extract_sync48(frame_bytes)
        sync_type = "unknown"
        best = 99
        for name, ref in _SYNC_TABLE.items():
            dist = sum(1 for a, b in zip(sync_bits, ref) if a != b)
            if dist < best:
                best = dist
                sync_type = name
        if best > 4:
            return DMRFrame(ok=False, raw_frame=bytes(frame_bytes), sync_type=sync_type)

        # 2) Slot Type -> color_code, data_type
        cc, dt = _frame_to_slot_type(frame_bytes)

        # 3) 对数据类突发做 BPTC + LC 解码
        src_id = 0
        dst_id = 0
        flco = 0
        ok = True
        is_voice = sync_type.endswith("VOICE")

        if dt in (DT_VOICE_LC_HEADER, DT_TERMINATOR_WITH_LC):
            raw = _frame_to_bptc_raw(frame_bytes)
            payload96, _ = bptc19696_decode(raw)
            lc12 = _bits_to_bytes_be(payload96)  # 12 字节
            # CRC 掩码逆异或
            if dt == DT_VOICE_LC_HEADER:
                mask = VOICE_LC_HEADER_CRC_MASK
            else:
                mask = TERMINATOR_WITH_LC_CRC_MASK
            lc12[9] ^= mask[0]
            lc12[10] ^= mask[1]
            lc12[11] ^= mask[2]
            if rs129_check(lc12):
                flco, dst_id, src_id, _fid = _parse_lc9(lc12[:9])
            else:
                ok = False

        # 时隙：BS sourced 默认 slot0（CACH 未深入解析）
        slot = 0

        return DMRFrame(
            ok=ok, slot=slot, color_code=cc, data_type=dt,
            src_id=src_id, dst_id=dst_id, flco=flco,
            is_voice=is_voice, sync_type=sync_type,
            raw_frame=bytes(frame_bytes),
        )


# --------------------------------------------------------------------------- #
# 便捷一键入口
# --------------------------------------------------------------------------- #
def dmr_decode_iq(iq_samples: np.ndarray, sample_rate: float = 48000.0) -> List[Dict]:
    """一键 IQ 解码，返回字典列表（无信号时返回空列表，不假装有呼号/ID）。"""
    demod = DMRDemodulator(sample_rate=sample_rate)
    dibits = demod.demod_iq(np.asarray(iq_samples, dtype=complex))
    if dibits.size == 0:
        return []
    framer = DMRFramer()
    frames = framer.decode_stream(dibits)
    return [
        {
            "ok": f.ok, "slot": f.slot, "color_code": f.color_code,
            "data_type": f.data_type, "src_id": f.src_id, "dst_id": f.dst_id,
            "flco": f.flco, "is_voice": f.is_voice, "sync_type": f.sync_type,
        }
        for f in frames if f.ok
    ]
