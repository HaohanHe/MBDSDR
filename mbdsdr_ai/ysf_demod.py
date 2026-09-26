"""纯软件 YSF/C4FM 数字语音解码器：复数 IQ -> 4FSK 基带 -> 符号同步 -> 成帧 -> FICH/DCH/呼号。

本模块严格对照已完成的 DMR 解调器（mbdsdr_ai/dmr_demod.py）的架构与风格实现，
不依赖 scipy，仅用 numpy + 标准库。解调管线（正交鉴频 -> RRC 匹配滤波 -> MM 符号同步
-> 4 电平判决）直接复用 dmr_demod.DMRDemodulator（C4FM 4800 baud 完全一致）。

本模块仅实现 YSF 数据层（成帧/同步/FICH/呼号），不包含 AMBE/IMBE 专利声码器，
语音解码不在范围内。

参考源码清单（本任务实读，file:line）：
  - repos/MMDVMHost/YSFDefines.h:22          帧长 120 字节
  - repos/MMDVMHost/YSFDefines.h:24          同步字 5 字节 {D4,71,C9,63,4D}
  - repos/MMDVMHost/YSFDefines.h:27          FICH 长 25 字节
  - repos/MMDVMHost/YSFDefines.h:31          呼号长 10
  - repos/MMDVMHost/YSFDefines.h:35-43      FI/DT/CM/MR 枚举
  - repos/MMDVMHost/YSFFICH.cpp:37-57       FICH 交织表（100 项）
  - repos/MMDVMHost/YSFFICH.cpp:87-99       FICH Viterbi 译码
  - repos/MMDVMHost/YSFFICH.cpp:101-121     4x Golay(24,12,8) + CRC16
  - repos/MMDVMHost/YSFFICH.cpp:123-176     FICH 编码
  - repos/MMDVMHost/YSFFICH.cpp:178-226     FICH 字段位域 getFI/getCM/getDT/getDev...
  - repos/MMDVMHost/YSFConvolution.cpp:32-33  BRANCH_TABLE1/2
  - repos/MMDVMHost/YSFConvolution.cpp:35-38  NUM_OF_STATES=16, K=5, M=2
  - repos/MMDVMHost/YSFConvolution.cpp:70-99  decode (ACS)
  - repos/MMDVMHost/YSFConvolution.cpp:101-125 chainback
  - repos/MMDVMHost/YSFConvolution.cpp:127-152 encode g1=d+d3+d4, g2=d+d1+d2+d4
  - repos/MMDVMHost/YSFPayload.cpp:29-49    DCH 交织表 9_20（180 项）
  - repos/MMDVMHost/YSFPayload.cpp:51-71    DCH 交织表 5_20（100 项）
  - repos/MMDVMHost/YSFPayload.cpp:80-81    WHITENING_DATA 20 字节
  - repos/MMDVMHost/YSFPayload.cpp:105-252  processHeaderData（DCH1/DCH2 解出呼号）
  - repos/MMDVMHost/YSFPayload.cpp:902-988  writeDataFRModeData1/2（DCH 编码）
  - repos/MMDVMHost/Golay24128.cpp:1043-1046 X22=0x400000/X11=0x800/MASK12/GENPOL=0xC75
  - repos/MMDVMHost/Golay24128.cpp:1078      encode24128
  - repos/MMDVMHost/Golay24128.cpp:1093-1105 decode24128
  - repos/MMDVMHost/CRC.cpp:156-195          addCCITT162/checkCCITT162（init=0, 末取反）
  - repos/DSDcc/ysf.h                        FICH 字段定义、DT 枚举
  - mbdsdr_ai/dmr_demod.py                   完整参考模式（位工具/4FSK 鉴频+RRC+MM/framer）
  - mbdsdr_ai/dsdcc_lite.py                  rrc_impulse_response / RRC_ALPHA_DMR=0.2
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from mbdsdr_ai.dmr_demod import (
    DMRDemodulator,
    DIBIT_LEVELS,
    _bits_to_byte_be,
    _bits_to_bytes_be,
    _byte_to_bits_be,
    _bytes_to_bits_be,
    dibits_to_levels,
    dmr_encode_baseband,
)
from mbdsdr_ai.dsdcc_lite import (
    RRC_ALPHA_DMR,
    rrc_impulse_response,
)

# --------------------------------------------------------------------------- #
# 协议常量（来源: repos/MMDVMHost/YSFDefines.h）
# --------------------------------------------------------------------------- #
YSF_FRAME_LENGTH_BYTES = 120       # YSFDefines.h:22
YSF_SYNC_LENGTH_BYTES = 5          # YSFDefines.h:25
YSF_FICH_LENGTH_BYTES = 25         # YSFDefines.h:27
YSF_CALLSIGN_LENGTH = 10           # YSFDefines.h:31

#: 同步字 5 字节 —— YSFDefines.h:24
YSF_SYNC_BYTES = (0xD4, 0x71, 0xC9, 0x63, 0x4D)

#: FI 枚举（YSFDefines.h:35-38）
YSF_FI_HEADER = 0x00
YSF_FI_COMMUNICATIONS = 0x01
YSF_FI_TERMINATOR = 0x02
YSF_FI_TEST = 0x03

#: DT 枚举（YSFDefines.h:40-43）
YSF_DT_VD_MODE1 = 0x00
YSF_DT_DATA_FR_MODE = 0x01
YSF_DT_VD_MODE2 = 0x02
YSF_DT_VOICE_FR_MODE = 0x03

#: CM 枚举（YSFDefines.h:45-47）
YSF_CM_GROUP1 = 0x00
YSF_CM_GROUP2 = 0x01
YSF_CM_INDIVIDUAL = 0x03

#: MR 枚举（YSFDefines.h:49-51）
YSF_MR_DIRECT = 0x00
YSF_MR_NOT_BUSY = 0x01
YSF_MR_BUSY = 0x02

#: YSF 符号率 4800 baud，宽模式频偏 ±1800 Hz
YSF_SYMBOL_RATE = 4800.0
YSF_DEVIATION = 1800.0

# --------------------------------------------------------------------------- #
# FICH 交织表（YSFFICH.cpp:37-57，100 项）
# --------------------------------------------------------------------------- #
FICH_INTERLEAVE_TABLE = [
    0, 40, 80, 120, 160,
    2, 42, 82, 122, 162,
    4, 44, 84, 124, 164,
    6, 46, 86, 126, 166,
    8, 48, 88, 128, 168,
    10, 50, 90, 130, 170,
    12, 52, 92, 132, 172,
    14, 54, 94, 134, 174,
    16, 56, 96, 136, 176,
    18, 58, 98, 138, 178,
    20, 60, 100, 140, 180,
    22, 62, 102, 142, 182,
    24, 64, 104, 144, 184,
    26, 66, 106, 146, 186,
    28, 68, 108, 148, 188,
    30, 70, 110, 150, 190,
    32, 72, 112, 152, 192,
    34, 74, 114, 154, 194,
    36, 76, 116, 156, 196,
    38, 78, 118, 158, 198,
]

#: DCH 交织表 9_20（YSFPayload.cpp:29-49，180 项）
DCH_INTERLEAVE_TABLE_9_20 = [
    0, 40, 80, 120, 160, 200, 240, 280, 320,
    2, 42, 82, 122, 162, 202, 242, 282, 322,
    4, 44, 84, 124, 164, 204, 244, 284, 324,
    6, 46, 86, 126, 166, 206, 246, 286, 326,
    8, 48, 88, 128, 168, 208, 248, 288, 328,
    10, 50, 90, 130, 170, 210, 250, 290, 330,
    12, 52, 92, 132, 172, 212, 252, 292, 332,
    14, 54, 94, 134, 174, 214, 254, 294, 334,
    16, 56, 96, 136, 176, 216, 256, 296, 336,
    18, 58, 98, 138, 178, 218, 258, 298, 338,
    20, 60, 100, 140, 180, 220, 260, 300, 340,
    22, 62, 102, 142, 182, 222, 262, 302, 342,
    24, 64, 104, 144, 184, 224, 264, 304, 344,
    26, 66, 106, 146, 186, 226, 266, 306, 346,
    28, 68, 108, 148, 188, 228, 268, 308, 348,
    30, 70, 110, 150, 190, 230, 270, 310, 350,
    32, 72, 112, 152, 192, 232, 272, 312, 352,
    34, 74, 114, 154, 194, 234, 274, 314, 354,
    36, 76, 116, 156, 196, 236, 276, 316, 356,
    38, 78, 118, 158, 198, 238, 278, 318, 358,
]

#: DCH 交织表 5_20（YSFPayload.cpp:51-71，100 项）
DCH_INTERLEAVE_TABLE_5_20 = [
    0, 40, 80, 120, 160,
    2, 42, 82, 122, 162,
    4, 44, 84, 124, 164,
    6, 46, 86, 126, 166,
    8, 48, 88, 128, 168,
    10, 50, 90, 130, 170,
    12, 52, 92, 132, 172,
    14, 54, 94, 134, 174,
    16, 56, 96, 136, 176,
    18, 58, 98, 138, 178,
    20, 60, 100, 140, 180,
    22, 62, 102, 142, 182,
    24, 64, 104, 144, 184,
    26, 66, 106, 146, 186,
    28, 68, 108, 148, 188,
    30, 70, 110, 150, 190,
    32, 72, 112, 152, 192,
    34, 74, 114, 154, 194,
    36, 76, 116, 156, 196,
    38, 78, 118, 158, 198,
]

#: 数据扰码序列（YSFPayload.cpp:80-81）
WHITENING_DATA = [
    0x93, 0xD7, 0x51, 0x21, 0x9C, 0x2F, 0x6C, 0xD0, 0xEF, 0x0F,
    0xF8, 0x3D, 0xF1, 0x73, 0x20, 0x94, 0xED, 0x1E, 0x7C, 0xD8,
]


# --------------------------------------------------------------------------- #
# 位/字节工具（与 dmr_demod 相同风格，MSB 在前）
# --------------------------------------------------------------------------- #
def byte_to_bits_be(b: int) -> List[int]:
    """一个字节 -> 8 个 bit，MSB 在前。"""
    return _byte_to_bits_be(b)


def bits_to_byte_be(bits: Sequence[int], off: int = 0) -> int:
    return _bits_to_byte_be(bits, off)


def bytes_to_bits_be(data: Sequence[int]) -> List[int]:
    return _bytes_to_bits_be(data)


def bits_to_bytes_be(bits: Sequence[int]) -> List[int]:
    return _bits_to_bytes_be(bits)


# --------------------------------------------------------------------------- #
# K=5 卷积码 / Viterbi 译码 —— 来源: YSFConvolution.cpp
# BRANCH_TABLE1/2 (行32-33), decode(行70-99), chainback(行101-125), encode(行127-152)
# --------------------------------------------------------------------------- #
_BRANCH_TABLE1 = (0, 0, 0, 0, 1, 1, 1, 1)   # YSFConvolution.cpp:32
_BRANCH_TABLE2 = (0, 1, 1, 0, 0, 1, 1, 0)   # YSFConvolution.cpp:33
_NUM_STATES_D2 = 8                           # YSFConvolution.cpp:35
_NUM_STATES = 16                             # YSFConvolution.cpp:36
_M = 2                                       # YSFConvolution.cpp:37
_K = 5                                       # YSFConvolution.cpp:38


class YSFViterbi:
    """K=5 r=1/2 卷积码 Viterbi 硬判决译码（YSFConvolution.cpp 逐行移植）。

    生成多项式：g1 = d + d3 + d4，g2 = d + d1 + d2 + d4（YSFConvolution.cpp:138-139）。
    """

    def __init__(self) -> None:
        self._old = [0] * _NUM_STATES
        self._new = [0] * _NUM_STATES
        self._decisions: List[int] = []

    def start(self) -> None:
        """初始化度量全 0（YSFConvolution.cpp:60-68）。"""
        self._old = [0] * _NUM_STATES
        self._new = [0] * _NUM_STATES
        self._decisions = []

    def decode(self, s0: int, s1: int) -> None:
        """喂入一对接收码元 (s0, s1)，做 ACS（YSFConvolution.cpp:70-99）。"""
        dp = 0
        for i in range(_NUM_STATES_D2):
            j = i * 2
            metric = (_BRANCH_TABLE1[i] ^ s0) + (_BRANCH_TABLE2[i] ^ s1)
            m0 = self._old[i] + metric
            m1 = self._old[i + _NUM_STATES_D2] + (_M - metric)
            decision0 = 1 if m0 >= m1 else 0
            self._new[j + 0] = m1 if decision0 else m0

            m0 = self._old[i] + (_M - metric)
            m1 = self._old[i + _NUM_STATES_D2] + metric
            decision1 = 1 if m0 >= m1 else 0
            self._new[j + 1] = m1 if decision1 else m0

            dp |= (decision1 << (j + 1)) | (decision0 << (j + 0))
        self._decisions.append(dp)
        # swap
        self._old, self._new = self._new, self._old

    def chainback(self, n_bits: int) -> List[int]:
        """回溯 n_bits 个信息位（YSFConvolution.cpp:101-125）。

        返回长度 n_bits 的 bit 列表，按发送顺序排列（bit0 先发）。
        """
        out = [0] * n_bits
        state = 0
        dp_idx = len(self._decisions) - 1
        for n in range(n_bits - 1, -1, -1):
            dp = self._decisions[dp_idx]
            dp_idx -= 1
            i = state >> (9 - _K)  # = state >> 4
            bit = (dp >> i) & 1
            state = (bit << 7) | (state >> 1)
            out[n] = bit
        return out

    @staticmethod
    def encode(in_bits: Sequence[int], n_bits: int) -> List[int]:
        """n_bits 个信息位 -> 2*n_bits 个卷积码码元（YSFConvolution.cpp:127-152）。

        g1 = d + d3 + d4, g2 = d + d1 + d2 + d4。
        """
        d1 = d2 = d3 = d4 = 0
        out: List[int] = []
        for i in range(n_bits):
            d = int(in_bits[i]) & 1
            g1 = (d + d3 + d4) & 1
            g2 = (d + d1 + d2 + d4) & 1
            d4 = d3
            d3 = d2
            d2 = d1
            d1 = d
            out.append(g1)
            out.append(g2)
        return out


# --------------------------------------------------------------------------- #
# Golay(24,12,8) —— 来源: Golay24128.cpp
# GENPOL=0xC75 (行1046), X22=0x400000 (行1043), X11=0x800 (行1044), MASK12=0xFFFFF800 (行1045)
# --------------------------------------------------------------------------- #
_G24_X22 = 0x00400000
_G24_X11 = 0x00000800
_G24_MASK12 = 0xFFFFF800
_G24_GENPOL = 0x00000C75


def _golay24128_syndrome(pattern: int) -> int:
    """Golay(23,12,7) 伴随式多项式除法余数（Golay24128.cpp:1048-1071）。"""
    aux = _G24_X22
    if pattern >= _G24_X11:
        while pattern & _G24_MASK12:
            while not (aux & pattern):
                aux >>= 1
            pattern ^= (aux // _G24_X11) * _G24_GENPOL
    return pattern


def _build_golay23127_decode_table() -> Dict[int, int]:
    """在 23bit 码空间枚举重量<=3 错误图样，syndrome -> error（仿 dmr_demod）。"""
    table: Dict[int, int] = {0: 0}
    n = 23
    for w in range(1, 4):
        idxs = list(range(w))
        while True:
            err = 0
            for i in idxs:
                err |= 1 << i
            syn = _golay24128_syndrome(err)
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


_G23_DEC_TABLE = _build_golay23127_decode_table()


def golay24128_encode(info12: int) -> int:
    """12bit 信息 -> 24bit 码字（Golay24128.cpp:1078 encode24128 等价）。

    23bit Golay(23,12,7) 码字 = (info<<11) | syndrome(info<<11)；
    第 24 位（LSB）为整体偶校验位，使 24bit 总重量为偶数。
    """
    info12 &= 0xFFF
    code23 = (info12 << 11) | _golay24128_syndrome(info12 << 11)
    # 整体偶校验：code24 = (code23 << 1) | parity
    parity = bin(code23).count("1") & 1
    return (code23 << 1) | parity


def golay24128_decode(code24: int) -> Tuple[int, bool]:
    """24bit 接收码 -> (12bit 信息, 是否有效)。

    对应 Golay24128.cpp:1093-1105 decode24128。
    """
    code24 &= 0xFFFFFF
    syndrome = _golay24128_syndrome(code24 >> 1)
    error_pattern = _G23_DEC_TABLE.get(syndrome, 0) << 1
    out = code24 ^ error_pattern
    # valid: 伴随式重量<3（纠错成功）或整体偶校验通过
    valid = (bin(syndrome).count("1") < 3) or (bin(out).count("1") & 1) == 0
    info12 = (out >> 12) & 0xFFF
    return info12, valid


# --------------------------------------------------------------------------- #
# CRC-CCITT16 —— 来源: CRC.cpp:156-195
# init=0, poly=0x1021（查表 CCITT16_TABLE2），末取反（等价 init=0xFFFF, xorout=0）
# --------------------------------------------------------------------------- #
def _build_ccitt16_table() -> List[int]:
    """由多项式 0x1021 生成 CRC-16 查表（与 CRC.cpp:92-124 CCITT16_TABLE2 一致）。"""
    table = [0] * 256
    for i in range(256):
        crc = i << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
        table[i] = crc
    return table


_CCITT16_TABLE = _build_ccitt16_table()


def _ccitt16_compute(data: Sequence[int], length: int) -> int:
    """计算前 length-2 字节的 CRC-CCITT16（CRC.cpp:166-171 等价）。

    crc=0；逐字节 crc = (crc>>8) ^ table[(crc&0xFF) ^ b]；末取反。
    """
    crc = 0
    for i in range(length - 2):
        crc = ((crc >> 8) & 0xFF) ^ _CCITT16_TABLE[(crc & 0xFF) ^ data[i]]
    crc = (~crc) & 0xFFFF
    return crc


def ccitt16_add(data: bytearray, length: Optional[int] = None) -> None:
    """就地追加 CRC16 到 data 末尾两字节（CRC.cpp:156-175 addCCITT162）。

    data[length-2] = crc 低字节, data[length-1] = crc 高字节。
    length 默认 len(data)；调用方可传实际覆盖长度（如 FICH=6, DCH=22）。
    """
    n = length if length is not None else len(data)
    crc = _ccitt16_compute(data, n)
    data[n - 2] = crc & 0xFF
    data[n - 1] = (crc >> 8) & 0xFF


def ccitt16_check(data: Sequence[int], length: Optional[int] = None) -> bool:
    """校验末两字节 CRC16（CRC.cpp:177-195 checkCCITT162）。"""
    n = length if length is not None else len(data)
    crc = _ccitt16_compute(data, n)
    return (crc & 0xFF) == data[n - 2] and ((crc >> 8) & 0xFF) == data[n - 1]


# --------------------------------------------------------------------------- #
# FICH 编解码 —— 来源: YSFFICH.cpp
# --------------------------------------------------------------------------- #
def _bits_at(bytes25: Sequence[int], n: int) -> int:
    """25 字节数组里 bit n（MSB first）取值（READ_BIT1, YSFFICH.cpp:35）。"""
    return (bytes25[n >> 3] >> (7 - (n & 7))) & 1


def _set_bit(bytes25: bytearray, n: int, val: int) -> None:
    """写 bit n（WRITE_BIT1, YSFFICH.cpp:34）。"""
    if val:
        bytes25[n >> 3] |= (0x80 >> (n & 7))
    else:
        bytes25[n >> 3] &= ~(0x80 >> (n & 7)) & 0xFF


def fich_decode(fich25: Sequence[int]) -> Tuple[bytes, bool]:
    """25 字节 FICH 区域 -> (6 字节 FICH, 是否有效)。

    对应 CYSFFICH::decode（YSFFICH.cpp:80-121）。
    """
    if len(fich25) < 25:
        return b"\x00" * 6, False
    viterbi = YSFViterbi()
    viterbi.start()
    for i in range(100):
        n = FICH_INTERLEAVE_TABLE[i]
        s0 = _bits_at(fich25, n)
        s1 = _bits_at(fich25, n + 1)
        viterbi.decode(s0, s1)
    info_bits = viterbi.chainback(96)  # 96 bit = 12 字节
    conv_bytes = bits_to_bytes_be(info_bits)  # 12 字节

    # 4x Golay(24,12,8)
    b0, ok0 = golay24128_decode((conv_bytes[0] << 16) | (conv_bytes[1] << 8) | conv_bytes[2])
    b1, ok1 = golay24128_decode((conv_bytes[3] << 16) | (conv_bytes[4] << 8) | conv_bytes[5])
    b2, ok2 = golay24128_decode((conv_bytes[6] << 16) | (conv_bytes[7] << 8) | conv_bytes[8])
    b3, ok3 = golay24128_decode((conv_bytes[9] << 16) | (conv_bytes[10] << 8) | conv_bytes[11])
    if not (ok0 and ok1 and ok2 and ok3):
        return b"\x00" * 6, False

    # 组合 6 字节（YSFFICH.cpp:113-118）
    fich6 = bytearray(6)
    fich6[0] = (b0 >> 4) & 0xFF
    fich6[1] = ((b0 << 4) & 0xF0) | ((b1 >> 8) & 0x0F)
    fich6[2] = b1 & 0xFF
    fich6[3] = (b2 >> 4) & 0xFF
    fich6[4] = ((b2 << 4) & 0xF0) | ((b3 >> 8) & 0x0F)
    fich6[5] = b3 & 0xFF

    if not ccitt16_check(fich6):
        return bytes(fich6), False
    return bytes(fich6), True


def fich_encode(fich6: Sequence[int]) -> bytes:
    """6 字节 FICH -> 25 字节编码后数据（CYSFFICH::encode，YSFFICH.cpp:123-176）。"""
    fich = bytearray(fich6)
    ccitt16_add(fich)  # 写 fich[4], fich[5]

    # 拆分 4 个 12bit 字（YSFFICH.cpp:132-135）
    b0 = ((fich[0] << 4) & 0xFF0) | ((fich[1] >> 4) & 0x00F)
    b1 = ((fich[1] << 8) & 0xF00) | (fich[2] & 0x0FF)
    b2 = ((fich[3] << 4) & 0xFF0) | ((fich[4] >> 4) & 0x00F)
    b3 = ((fich[4] << 8) & 0xF00) | (fich[5] & 0x0FF)

    c0 = golay24128_encode(b0)
    c1 = golay24128_encode(b1)
    c2 = golay24128_encode(b2)
    c3 = golay24128_encode(b3)

    # 打包 13 字节（YSFFICH.cpp:143-155）
    conv = bytearray(13)
    conv[0] = (c0 >> 16) & 0xFF
    conv[1] = (c0 >> 8) & 0xFF
    conv[2] = c0 & 0xFF
    conv[3] = (c1 >> 16) & 0xFF
    conv[4] = (c1 >> 8) & 0xFF
    conv[5] = c1 & 0xFF
    conv[6] = (c2 >> 16) & 0xFF
    conv[7] = (c2 >> 8) & 0xFF
    conv[8] = c2 & 0xFF
    conv[9] = (c3 >> 16) & 0xFF
    conv[10] = (c3 >> 8) & 0xFF
    conv[11] = c3 & 0xFF
    conv[12] = 0x00  # 4bit tail

    conv_bits = bytes_to_bits_be(conv)  # 104 bit
    encoded = YSFViterbi.encode(conv_bits, 100)  # 200 bit

    out = bytearray(25)
    j = 0
    for i in range(100):
        n = FICH_INTERLEAVE_TABLE[i]
        _set_bit(out, n, encoded[j])
        j += 1
        _set_bit(out, n + 1, encoded[j])
        j += 1
    return bytes(out)


# --------------------------------------------------------------------------- #
# DCH 载荷编解码（Data FR Mode，DT=1）
# 来源: YSFPayload.cpp:105-252 (processHeaderData), 902-988 (writeDataFRModeData1/2)
# --------------------------------------------------------------------------- #
def _dch_decode_45(dch45: Sequence[int]) -> Tuple[List[int], bool]:
    """45 字节交织后 DCH -> (22 字节译码结果, CRC 是否通过)。

    对应 processHeaderData 里的 Viterbi + CRC 部分（YSFPayload.cpp:120-136）。
    """
    viterbi = YSFViterbi()
    viterbi.start()
    for i in range(180):
        n = DCH_INTERLEAVE_TABLE_9_20[i]
        s0 = _bits_at(dch45, n)
        s1 = _bits_at(dch45, n + 1)
        viterbi.decode(s0, s1)
    info_bits = viterbi.chainback(176)  # 176 bit = 22 字节
    out22 = bits_to_bytes_be(info_bits)
    ok = ccitt16_check(out22)
    return out22, ok


def dch_decode(dch45: Sequence[int]) -> Tuple[bytes, bool]:
    """45 字节 DCH -> (解扰后 20 字节载荷, 是否有效)。"""
    out22, ok = _dch_decode_45(dch45)
    if not ok:
        return b"", False
    payload20 = bytearray(20)
    for i in range(20):
        payload20[i] = out22[i] ^ WHITENING_DATA[i]
    return bytes(payload20), True


def dch_encode(payload20: Sequence[int]) -> bytes:
    """20 字节载荷 -> 45 字节交织后 DCH（writeDataFRModeData1, YSFPayload.cpp:902-944）。"""
    output = bytearray(25)
    for i in range(20):
        output[i] = payload20[i] ^ WHITENING_DATA[i]
    ccitt16_add(output, 22)  # 写 output[20], output[21]（YSFPayload.cpp:913）
    output[22] = 0x00

    out_bits = bytes_to_bits_be(output)  # 200 bit
    encoded = YSFViterbi.encode(out_bits, 180)  # 360 bit

    convolved = bits_to_bytes_be(encoded)  # 45 字节（顺序输出）
    # 解交织到 bytes[45]
    out = bytearray(45)
    j = 0
    for i in range(180):
        n = DCH_INTERLEAVE_TABLE_9_20[i]
        _set_bit(out, n, encoded[j])
        j += 1
        _set_bit(out, n + 1, encoded[j])
        j += 1
    return bytes(out)


# --------------------------------------------------------------------------- #
# 呼号编解码
# --------------------------------------------------------------------------- #
def callsign_to_bytes(callsign: str) -> bytes:
    """呼号字符串 -> 10 字节（空格右填充，YSFPayload.cpp:990-999 setUplink 风格）。"""
    b = callsign.encode("ascii", errors="replace")[:YSF_CALLSIGN_LENGTH]
    return b + b" " * (YSF_CALLSIGN_LENGTH - len(b))


def bytes_to_callsign(data: Sequence[int]) -> str:
    """10 字节 -> 呼号字符串（strip 尾部空格）。"""
    return bytes(data[:YSF_CALLSIGN_LENGTH]).decode("ascii", errors="replace").strip()


# --------------------------------------------------------------------------- #
# FICH 6 字节字段构造/解析
# --------------------------------------------------------------------------- #
def build_fich6(fi: int = YSF_FI_HEADER, dt: int = YSF_DT_DATA_FR_MODE,
                cm: int = YSF_CM_GROUP1, bn: int = 0, bt: int = 0,
                fn: int = 0, ft: int = 0, dev: bool = True,
                mr: int = YSF_MR_DIRECT, voip: bool = False,
                dgid: int = 0) -> bytearray:
    """构造 6 字节 FICH（YSFFICH.cpp:178-284 位域）。

    byte0[7:6]=FI, [3:2]=CM, [1:0]=BN
    byte1[7:6]=BT, [5:3]=FN, [2:0]=FT
    byte2[6]=Dev, [4:3]=MR, [2]=VoIP, [1:0]=DT
    byte3[6:0]=DGId
    """
    f = bytearray(6)
    f[0] = ((fi & 0x3) << 6) | ((cm & 0x3) << 2) | (bn & 0x3)
    f[1] = ((bt & 0x3) << 6) | ((fn & 0x7) << 3) | (ft & 0x7)
    f[2] = ((dev & 0x1) << 6) | ((mr & 0x3) << 3) | ((voip & 0x1) << 2) | (dt & 0x3)
    f[3] = dgid & 0x7F
    return f


def parse_fich6(fich6: Sequence[int]) -> Dict[str, int]:
    """解析 6 字节 FICH 位域（YSFFICH.cpp:178-226）。"""
    return {
        "fi": (fich6[0] >> 6) & 0x3,
        "cm": (fich6[0] >> 2) & 0x3,
        "bn": fich6[0] & 0x3,
        "bt": (fich6[1] >> 6) & 0x3,
        "fn": (fich6[1] >> 3) & 0x7,
        "ft": fich6[1] & 0x7,
        "dev": (fich6[2] >> 6) & 0x1,
        "mr": (fich6[2] >> 3) & 0x3,
        "voip": (fich6[2] >> 2) & 0x1,
        "dt": fich6[2] & 0x3,
        "dgid": fich6[3] & 0x7F,
    }


# --------------------------------------------------------------------------- #
# 编码：构造完整 120 字节 Data FR 帧
# --------------------------------------------------------------------------- #
def ysf_encode_data_frame(src_callsign: str, dst_callsign: str,
                          fi: int = YSF_FI_HEADER, dt: int = YSF_DT_DATA_FR_MODE,
                          cm: int = YSF_CM_GROUP1, dev: bool = True,
                          mr: int = YSF_MR_DIRECT, voip: bool = False,
                          dgid: int = 0) -> bytes:
    """构造一个 YSF Data FR Mode 帧（120 字节）。

    布局：[同步5B][FICH 25B][载荷90B]
    载荷：DCH1（CSD1: dest呼号0-9 + source呼号10-19）在偏移 0,18,36,54,72 各9B；
          DCH2（downlink/uplink，本实现填空格）在偏移 9,27,45,63,81 各9B。
    """
    frame = bytearray(120)
    # 1) 同步字 5 字节
    for i in range(5):
        frame[i] = YSF_SYNC_BYTES[i]

    # 2) FICH
    fich6 = build_fich6(fi=fi, dt=dt, cm=cm, dev=dev, mr=mr, voip=voip, dgid=dgid)
    fich25 = fich_encode(fich6)
    frame[5:30] = fich25

    # 3) 载荷 CSD1：dest(0-9) + source(10-19)
    csd1 = callsign_to_bytes(dst_callsign) + callsign_to_bytes(src_callsign)
    dch1_45 = dch_encode(csd1)
    # 散布到偏移 0,18,36,54,72 各 9 字节（YSFPayload.cpp:115-118）
    payload_off = 30  # 跳过 sync(5) + FICH(25)
    p1 = payload_off
    p2 = 0
    for _ in range(5):
        frame[p1:p1 + 9] = dch1_45[p2:p2 + 9]
        p1 += 18
        p2 += 9

    # 4) DCH2（downlink/uplink）填空格呼号
    blank = b" " * 10
    csd2 = blank + blank
    dch2_45 = dch_encode(csd2)
    p1 = payload_off + 9
    p2 = 0
    for _ in range(5):
        frame[p1:p1 + 9] = dch2_45[p2:p2 + 9]
        p1 += 18
        p2 += 9

    return bytes(frame)


# --------------------------------------------------------------------------- #
# 4FSK/C4FM 调制：120 字节帧 -> 实数基带 / 复数 IQ
# --------------------------------------------------------------------------- #
def _ysf_frame_to_dibits(frame: Sequence[int]) -> np.ndarray:
    """120 字节（480bit）-> 240 dibit（uint8）。"""
    bits = bytes_to_bits_be(frame)  # 960 bit
    dibits = np.zeros(480, dtype=np.uint8)
    for i in range(480):
        dibits[i] = (bits[2 * i] << 1) | bits[2 * i + 1]
    return dibits


def ysf_encode_baseband(frame: Sequence[int], sample_rate: float = 48000.0,
                        sps: Optional[int] = None) -> np.ndarray:
    """120 字节帧 -> 实数基带（RRC 成形后）。"""
    if sps is None:
        sps = int(round(sample_rate / YSF_SYMBOL_RATE))
    dibits = _ysf_frame_to_dibits(frame)
    levels = dibits_to_levels(dibits)
    taps = rrc_impulse_response(sps, RRC_ALPHA_DMR)
    ups = np.zeros(len(levels) * sps, dtype=float)
    ups[::sps] = levels
    return np.convolve(ups, taps, mode="same")


def ysf_encode_iq(frame: Sequence[int], sample_rate: float = 48000.0,
                  sps: Optional[int] = None,
                  deviation: float = YSF_DEVIATION) -> np.ndarray:
    """120 字节帧 -> 复数 IQ（FM 调制，同 DMR 方式）。"""
    if sps is None:
        sps = int(round(sample_rate / YSF_SYMBOL_RATE))
    baseband = ysf_encode_baseband(frame, sample_rate, sps)
    freq_hz = baseband * deviation
    phase = 2.0 * np.pi * np.cumsum(freq_hz) / sample_rate
    return np.exp(1j * phase)


# --------------------------------------------------------------------------- #
# 解调：复用 DMRDemodulator（C4FM 4800 baud 管线完全一致）
# --------------------------------------------------------------------------- #
class YSFDemodulator:
    """YSF/C4FM 解调：包装 DMRDemodulator，偏差设为 YSF 宽模式。"""

    def __init__(self, sample_rate: float = 48000.0,
                 symbol_rate: float = YSF_SYMBOL_RATE,
                 deviation: float = YSF_DEVIATION):
        self._demod = DMRDemodulator(
            sample_rate=sample_rate, symbol_rate=symbol_rate, deviation=deviation)

    def demod_baseband(self, baseband: np.ndarray) -> np.ndarray:
        return self._demod.demod_baseband(baseband)

    def demod_iq(self, iq: np.ndarray) -> np.ndarray:
        return self._demod.demod_iq(iq)


# --------------------------------------------------------------------------- #
# YSF 帧结果 dataclass
# --------------------------------------------------------------------------- #
@dataclass
class YSFFrame:
    ok: bool = False
    fi: int = 0
    dt: int = 0
    cm: int = 0
    src_callsign: str = ""
    dst_callsign: str = ""
    downlink: str = ""
    uplink: str = ""
    raw_frame: bytes = b""


# --------------------------------------------------------------------------- #
# YSF 成帧器：同步检测 -> FICH 解码 -> DCH 呼号提取
# --------------------------------------------------------------------------- #
def _sync_bytes_to_dibits() -> List[int]:
    """5 字节同步字 -> 20 dibit（MSB first）。"""
    bits: List[int] = []
    for b in YSF_SYNC_BYTES:
        bits.extend(byte_to_bits_be(b))
    dibits: List[int] = []
    for i in range(20):
        dibits.append((bits[2 * i] << 1) | bits[2 * i + 1])
    return dibits


_SYNC_DIBITS = _sync_bytes_to_dibits()


class YSFFramer:
    """在 dibit 流中找 YSF 同步字并解出完整 120 字节帧。

    帧 = 480 dibits = 120 字节。同步字 20 dibits（5 字节）位于 dibit 0..19。
    """

    SYNC_LEN_DIBITS = 20

    def find_sync(self, dibits: Sequence[int]) -> List[int]:
        """在 dibit 流中找同步字位置（dibit 起点）。逐位汉明距离 <= 3。"""
        dib = list(dibits)
        n = len(dib)
        out: List[int] = []
        for pos in range(0, n - self.SYNC_LEN_DIBITS + 1):
            dist = sum(1 for a, b in zip(dib[pos:pos + 20], _SYNC_DIBITS) if a != b)
            if dist <= 3:
                out.append(pos)
        return out

    def decode_frame(self, dibits: Sequence[int], start: int) -> YSFFrame:
        """从 dibit 流的 start 解一个完整 480-dibit 帧。"""
        dib = list(dibits)
        if start < 0 or start + 480 > len(dib):
            return YSFFrame(ok=False)
        frame_dibits = dib[start:start + 480]
        bits: List[int] = []
        for d in frame_dibits:
            bits.append((int(d) >> 1) & 1)
            bits.append(int(d) & 1)
        frame_bytes = bits_to_bytes_be(bits)  # 120 字节

        # 1) FICH 解码（frame[5:30]）
        fich6, fich_ok = fich_decode(frame_bytes[5:30])
        if not fich_ok:
            return YSFFrame(ok=False, raw_frame=bytes(frame_bytes))

        fields = parse_fich6(fich6)
        dt = fields["dt"]

        src = dst = ""
        downlink = uplink = ""

        # 2) 载荷 DCH 解码
        payload_off = 30
        if dt == YSF_DT_DATA_FR_MODE:
            # DCH1: 偏移 0,18,36,54,72 各 9 字节
            dch1 = bytearray(45)
            p1 = payload_off
            p2 = 0
            for _ in range(5):
                dch1[p2:p2 + 9] = frame_bytes[p1:p1 + 9]
                p1 += 18
                p2 += 9
            payload1, ok1 = dch_decode(dch1)
            if ok1:
                dst = bytes_to_callsign(payload1[0:10])
                src = bytes_to_callsign(payload1[10:20])

            # DCH2: 偏移 9,27,45,63,81 各 9 字节
            dch2 = bytearray(45)
            p1 = payload_off + 9
            p2 = 0
            for _ in range(5):
                dch2[p2:p2 + 9] = frame_bytes[p1:p1 + 9]
                p1 += 18
                p2 += 9
            payload2, ok2 = dch_decode(dch2)
            if ok2:
                downlink = bytes_to_callsign(payload2[0:10])
                uplink = bytes_to_callsign(payload2[10:20])

        return YSFFrame(
            ok=fich_ok,
            fi=fields["fi"], dt=dt, cm=fields["cm"],
            src_callsign=src, dst_callsign=dst,
            downlink=downlink, uplink=uplink,
            raw_frame=bytes(frame_bytes),
        )

    def decode_stream(self, dibits: Sequence[int]) -> List[YSFFrame]:
        """完整流解码：找同步 -> 解帧。"""
        hits = self.find_sync(dibits)
        frames: List[YSFFrame] = []
        used: set = set()
        for pos in hits:
            if any(abs(pos - u) < 200 for u in used):
                continue
            used.add(pos)
            fr = self.decode_frame(dibits, pos)
            if fr.ok:
                frames.append(fr)
        return frames


# --------------------------------------------------------------------------- #
# 便捷一键入口
# --------------------------------------------------------------------------- #
def ysf_decode_iq(iq_samples: np.ndarray, sample_rate: float = 48000.0) -> List[Dict]:
    """一键 IQ 解码，返回字典列表（无信号返回空列表）。"""
    demod = YSFDemodulator(sample_rate=sample_rate)
    dibits = demod.demod_iq(np.asarray(iq_samples, dtype=complex))
    if dibits.size == 0:
        return []
    framer = YSFFramer()
    frames = framer.decode_stream(list(dibits))
    return [
        {
            "ok": f.ok, "fi": f.fi, "dt": f.dt, "cm": f.cm,
            "src_callsign": f.src_callsign, "dst_callsign": f.dst_callsign,
            "downlink": f.downlink, "uplink": f.uplink,
        }
        for f in frames if f.ok
    ]
