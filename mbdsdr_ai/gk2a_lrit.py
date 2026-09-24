"""
MBDSDR AI 内核 - GK-2A LRIT 全管道接收链（IQ 采样 → 云图 PNG）
================================================================

对标 SatDump (GPL-3.0) GK-2A LRIT 处理链，物理层 → 应用层完整实现：

    IQ complex64 采样
      → RRC 匹配滤波 + Costas 载波恢复 + 符号定时
      → BPSK 软符号
      → Viterbi 软判决译码 (K=7, R=1/2, poly 0x4F/0x6D)
      → 帧同步字 0x1ACFFC1D 相关检测 (1024B/帧)
      → CCSDS 解扰 (PN 255B 周期, 偏移 4)
      → Reed-Solomon (255,223) 译码 + I=4 交错 → 892B VCDU
      → VCDU 解复用 → M_PDU 第一头指针 → TP_PDU (APID/CRC16)
      → SessionPDU 按 APID 重组 → 完整 LRIT 文件
      → GK-2A LRIT 文件头 (Primary/ImageStructure/Segmentation/Annotation)
      → 图像段组装 → PNG 输出

关键常量来源（SatDump 源码 GPL-3.0）：
  - 调制 BPSK / 符号率 128kbaud / RRC α=0.5
      来源: SatDump resources/pipelines/GK2A.json  "gk2a_lrit" 段
  - 下行频率 1692.14 MHz (L 波段)
      来源: SatDump resources/pipelines/GK2A.json  frequencies
  - CADU 8192 bit = 1024B; 同步字 0x1ACFFC1D
      来源: SatDump src-core/pipeline/modules/ccsds/module_ccsds_conv_concat_decoder.cpp:90
  - Viterbi K=7 R=1/2 多项式 {79,109} = {0x4F,0x6D}
      来源: SatDump src-core/common/codings/viterbi/viterbi27.h:16
  - 卷积编码移位寄存器约定 my_state=(my_state<<1)|bit; out=parity(state&poly)
      来源: SatDump src-core/common/codings/viterbi/cc_encoder.cpp:108-113
  - CCSDS 解扰 PN 表 (255B 周期), 偏移 4, 解扰在 RS 之前
      来源: SatDump src-core/common/codings/randomization.cpp:3-31,
            module_ccsds_conv_concat_decoder.cpp:31,177-178
  - RS(255,223) 本原多项式 0x187, fcr=112, 根间隔 11, 32 校验字节, dual-basis
      来源: SatDump src-core/common/codings/reedsolomon/reedsolomon.cpp:34
            src-core/libs/correct/correct.h:180-181
  - RS 交错深度 I=4, fill_bytes=-1 (无缩短)
      来源: SatDump resources/pipelines/GK2A.json  "rs_i":4
            module_ccsds_conv_concat_decoder.cpp:35,181
  - GK-2A 图像分段头 (type=128): image_seq_nb/total_segments_nb/line_nb
      来源: SatDump plugins/xrit_support/xrit/gk2a/gk2a_headers.h:43-61
  - GK-2A 压缩标志: 0=无, 1=小波/J2K, 2=渐进JPEG
      来源: SatDump plugins/xrit_support/xrit/gk2a/decomp.cpp:27-43

传输层 (VCDU/M_PDU/TP_PDU/SessionPDU/CRC16) 复用 mbdsdr_ai/goes_lrit.py
（同样源自 CCSDS 标准，与 GOES-R 一致）。
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np


# ============================================================================
# 链路层常量 —— 来源: SatDump resources/pipelines/GK2A.json
# ============================================================================

#: 下行中心频率 (Hz)。来源: GK2A.json frequencies [[LRIT, 1692.14e6]]
GK2A_LRIT_FREQ_HZ = 1692.14e6

#: 推荐采样率 (Hz)。来源: GK2A.json "samplerate": 1e6
GK2A_LRIT_SAMPLERATE = 1e6

#: 符号率 (Bd)。来源: GK2A.json psk_demod "symbolrate": 128e3
GK2A_SYMBOL_RATE = 128e3

#: RRC 滚降系数 α。来源: GK2A.json psk_demod "rrc_alpha": 0.5
GK2A_RRC_ALPHA = 0.5

#: 载波环带宽。来源: GK2A.json psk_demod "pll_bw": 0.02
GK2A_PLL_BW = 0.02

#: CADU 长度 8192 bit = 1024 字节。来源: GK2A.json "cadu_size": 8192
CADU_BITS = 8192
CADU_BYTES = CADU_BITS // 8  # = 1024

#: 32 位同步字。来源: module_ccsds_conv_concat_decoder.cpp:90  asm_sync = 0x1acffc1d
SYNC_WORD = 0x1ACFFC1D
SYNC_WORD_BYTES = SYNC_WORD.to_bytes(4, "big")
SYNC_BYTES_LEN = 4

#: 解扰起始偏移（跳过同步字）。来源: module_ccsds_conv_concat_decoder.cpp:31
#:   d_derand_from = parameters["derand_start"] (default 4)
DERAND_OFFSET = 4

#: RS 交错深度。来源: GK2A.json "rs_i": 4
RS_INTERLEAVE = 4

#: RS 码参数。来源: reedsolomon.cpp:34
#:   correct_reed_solomon_create(ccsds, 112, 11, 32)
RS_N = 255          # 码长
RS_K = 223          # 数据字节数
RS_NROOTS = 32      # 校验字节数 (= 2*t, t=16 纠错)
RS_FCR = 112        # 首根
RS_PRIM = 11        # 根间隔
RS_POLY = 0x187     # 本原多项式 x^8+x^7+x^2+x+1
#: RS 译码后 VCDU 长度 = RS_INTERLEAVE * RS_K = 4*223 = 892
VCDU_LEN = RS_INTERLEAVE * RS_K  # = 892

#: Viterbi 约束长度 K 与码率。来源: viterbi27.h:16 CCSDS_R2_K7_POLYS={79,109}
VIT_K = 7
VIT_POLYS = (0x4F, 0x6D)  # = (79, 109)
VIT_RATE = 2              # 1/2 码率 → 每个输入比特输出 2 比特


# ============================================================================
# CCSDS 解扰 PN 表 (255 字节周期)
# 来源: SatDump src-core/common/codings/randomization.cpp:3-31  ccsds_pn[255]
# ============================================================================

CCSDS_PN: bytes = bytes([
    0xff, 0x48, 0x0e, 0xc0, 0x9a, 0x0d, 0x70, 0xbc,
    0x8e, 0x2c, 0x93, 0xad, 0xa7, 0xb7, 0x46, 0xce,
    0x5a, 0x97, 0x7d, 0xcc, 0x32, 0xa2, 0xbf, 0x3e,
    0x0a, 0x10, 0xf1, 0x88, 0x94, 0xcd, 0xea, 0xb1,
    0xfe, 0x90, 0x1d, 0x81, 0x34, 0x1a, 0xe1, 0x79,
    0x1c, 0x59, 0x27, 0x5b, 0x4f, 0x6e, 0x8d, 0x9c,
    0xb5, 0x2e, 0xfb, 0x98, 0x65, 0x45, 0x7e, 0x7c,
    0x14, 0x21, 0xe3, 0x11, 0x29, 0x9b, 0xd5, 0x63,
    0xfd, 0x20, 0x3b, 0x02, 0x68, 0x35, 0xc2, 0xf2,
    0x38, 0xb2, 0x4e, 0xb6, 0x9e, 0xdd, 0x1b, 0x39,
    0x6a, 0x5d, 0xf7, 0x30, 0xca, 0x8a, 0xfc, 0xf8,
    0x28, 0x43, 0xc6, 0x22, 0x53, 0x37, 0xaa, 0xc7,
    0xfa, 0x40, 0x76, 0x04, 0xd0, 0x6b, 0x85, 0xe4,
    0x71, 0x64, 0x9d, 0x6d, 0x3d, 0xba, 0x36, 0x72,
    0xd4, 0xbb, 0xee, 0x61, 0x95, 0x15, 0xf9, 0xf0,
    0x50, 0x87, 0x8c, 0x44, 0xa6, 0x6f, 0x55, 0x8f,
    0xf4, 0x80, 0xec, 0x09, 0xa0, 0xd7, 0x0b, 0xc8,
    0xe2, 0xc9, 0x3a, 0xda, 0x7b, 0x74, 0x6c, 0xe5,
    0xa9, 0x77, 0xdc, 0xc3, 0x2a, 0x2b, 0xf3, 0xe0,
    0xa1, 0x0f, 0x18, 0x89, 0x4c, 0xde, 0xab, 0x1f,
    0xe9, 0x01, 0xd8, 0x13, 0x41, 0xae, 0x17, 0x91,
    0xc5, 0x92, 0x75, 0xb4, 0xf6, 0xe8, 0xd9, 0xcb,
    0x52, 0xef, 0xb9, 0x86, 0x54, 0x57, 0xe7, 0xc1,
    0x42, 0x1e, 0x31, 0x12, 0x99, 0xbd, 0x56, 0x3f,
    0xd2, 0x03, 0xb0, 0x26, 0x83, 0x5c, 0x2f, 0x23,
    0x8b, 0x24, 0xeb, 0x69, 0xed, 0xd1, 0xb3, 0x96,
    0xa5, 0xdf, 0x73, 0x0c, 0xa8, 0xaf, 0xcf, 0x82,
    0x84, 0x3c, 0x62, 0x25, 0x33, 0x7a, 0xac, 0x7f,
    0xa4, 0x07, 0x60, 0x4d, 0x06, 0xb8, 0x5e, 0x47,
    0x16, 0x49, 0xd6, 0xd3, 0xdb, 0xa3, 0x67, 0x2d,
    0x4b, 0xbe, 0xe6, 0x19, 0x51, 0x5f, 0x9f, 0x05,
    0x08, 0x78, 0xc4, 0x4a, 0x66, 0xf5, 0x58,
])


def derandomize_ccsds(data: bytearray, length: int, offset: int = 0) -> None:
    """CCSDS 解扰：data[offset..offset+length) 异或周期 255 的 PN。

    来源: randomization.cpp:33-38  derand_ccsds()
    调用处: module_ccsds_conv_concat_decoder.cpp:178
      derand_ccsds(&cadu[4], d_cadu_bytes - d_derand_from)
    （解扰在 RS 之前，作用于同步字之后的 1020 字节）
    """
    for i in range(length):
        data[offset + i] ^= CCSDS_PN[i % 255]


# ============================================================================
# Reed-Solomon (255,223) CCSDS  ——  本原多项式 0x187, fcr=112, prim=11
# 来源: reedsolomon.cpp:30-46 (构造), reedsolomon.cpp:53-116 (译码)
#        correct.h:180-181 (本原多项式定义)
# ============================================================================

#: dual-basis 查表。来源: reedsolomon.cpp:6-16  ToDualBasis[256]
_TO_DUAL_BASIS = bytes([
    0x00, 0x7b, 0xaf, 0xd4, 0x99, 0xe2, 0x36, 0x4d, 0xfa, 0x81, 0x55, 0x2e, 0x63, 0x18, 0xcc, 0xb7,
    0x86, 0xfd, 0x29, 0x52, 0x1f, 0x64, 0xb0, 0xcb, 0x7c, 0x07, 0xd3, 0xa8, 0xe5, 0x9e, 0x4a, 0x31,
    0xec, 0x97, 0x43, 0x38, 0x75, 0x0e, 0xda, 0xa1, 0x16, 0x6d, 0xb9, 0xc2, 0x8f, 0xf4, 0x20, 0x5b,
    0x6a, 0x11, 0xc5, 0xbe, 0xf3, 0x88, 0x5c, 0x27, 0x90, 0xeb, 0x3f, 0x44, 0x09, 0x72, 0xa6, 0xdd,
    0xef, 0x94, 0x40, 0x3b, 0x76, 0x0d, 0xd9, 0xa2, 0x15, 0x6e, 0xba, 0xc1, 0x8c, 0xf7, 0x23, 0x58,
    0x69, 0x12, 0xc6, 0xbd, 0xf0, 0x8b, 0x5f, 0x24, 0x93, 0xe8, 0x3c, 0x47, 0x0a, 0x71, 0xa5, 0xde,
    0x03, 0x78, 0xac, 0xd7, 0x9a, 0xe1, 0x35, 0x4e, 0xf9, 0x82, 0x56, 0x2d, 0x60, 0x1b, 0xcf, 0xb4,
    0x85, 0xfe, 0x2a, 0x51, 0x1c, 0x67, 0xb3, 0xc8, 0x7f, 0x04, 0xd0, 0xab, 0xe6, 0x9d, 0x49, 0x32,
    0x8d, 0xf6, 0x22, 0x59, 0x14, 0x6f, 0xbb, 0xc0, 0x77, 0x0c, 0xd8, 0xa3, 0xee, 0x95, 0x41, 0x3a,
    0x0b, 0x70, 0xa4, 0xdf, 0x92, 0xe9, 0x3d, 0x46, 0xf1, 0x8a, 0x5e, 0x25, 0x68, 0x13, 0xc7, 0xbc,
    0x61, 0x1a, 0xce, 0xb5, 0xf8, 0x83, 0x57, 0x2c, 0x9b, 0xe0, 0x34, 0x4f, 0x02, 0x79, 0xad, 0xd6,
    0xe7, 0x9c, 0x48, 0x33, 0x7e, 0x05, 0xd1, 0xaa, 0x1d, 0x66, 0xb2, 0xc9, 0x84, 0xff, 0x2b, 0x50,
    0x62, 0x19, 0xcd, 0xb6, 0xfb, 0x80, 0x54, 0x2f, 0x98, 0xe3, 0x37, 0x4c, 0x01, 0x7a, 0xae, 0xd5,
    0xe4, 0x9f, 0x4b, 0x30, 0x7d, 0x06, 0xd2, 0xa9, 0x1e, 0x65, 0xb1, 0xca, 0x87, 0xfc, 0x28, 0x53,
    0x8e, 0xf5, 0x21, 0x5a, 0x17, 0x6c, 0xb8, 0xc3, 0x74, 0x0f, 0xdb, 0xa0, 0xed, 0x96, 0x42, 0x39,
    0x08, 0x73, 0xa7, 0xdc, 0x91, 0xea, 0x3e, 0x45, 0xf2, 0x89, 0x5d, 0x26, 0x6b, 0x10, 0xc4, 0xbf,
])

#: dual-basis 逆查表。来源: reedsolomon.cpp:18-28  FromDualBasis[256]
_FROM_DUAL_BASIS = bytes([
    0x00, 0xcc, 0xac, 0x60, 0x79, 0xb5, 0xd5, 0x19, 0xf0, 0x3c, 0x5c, 0x90, 0x89, 0x45, 0x25, 0xe9,
    0xfd, 0x31, 0x51, 0x9d, 0x84, 0x48, 0x28, 0xe4, 0x0d, 0xc1, 0xa1, 0x6d, 0x74, 0xb8, 0xd8, 0x14,
    0x2e, 0xe2, 0x82, 0x4e, 0x57, 0x9b, 0xfb, 0x37, 0xde, 0x12, 0x72, 0xbe, 0xa7, 0x6b, 0x0b, 0xc7,
    0xd3, 0x1f, 0x7f, 0xb3, 0xaa, 0x66, 0x06, 0xca, 0x23, 0xef, 0x8f, 0x43, 0x5a, 0x96, 0xf6, 0x3a,
    0x42, 0x8e, 0xee, 0x22, 0x3b, 0xf7, 0x97, 0x5b, 0xb2, 0x7e, 0x1e, 0xd2, 0xcb, 0x07, 0x67, 0xab,
    0xbf, 0x73, 0x13, 0xdf, 0xc6, 0x0a, 0x6a, 0xa6, 0x4f, 0x83, 0xe3, 0x2f, 0x36, 0xfa, 0x9a, 0x56,
    0x6c, 0xa0, 0xc0, 0x0c, 0x15, 0xd9, 0xb9, 0x75, 0x9c, 0x50, 0x30, 0xfc, 0xe5, 0x29, 0x49, 0x85,
    0x91, 0x5d, 0x3d, 0xf1, 0xe8, 0x24, 0x44, 0x88, 0x61, 0xad, 0xcd, 0x01, 0x18, 0xd4, 0xb4, 0x78,
    0xc5, 0x09, 0x69, 0xa5, 0xbc, 0x70, 0x10, 0xdc, 0x35, 0xf9, 0x99, 0x55, 0x4c, 0x80, 0xe0, 0x2c,
    0x38, 0xf4, 0x94, 0x58, 0x41, 0x8d, 0xed, 0x21, 0xc8, 0x04, 0x64, 0xa8, 0xb1, 0x7d, 0x1d, 0xd1,
    0xeb, 0x27, 0x47, 0x8b, 0x92, 0x5e, 0x3e, 0xf2, 0x1b, 0xd7, 0xb7, 0x7b, 0x62, 0xae, 0xce, 0x02,
    0x16, 0xda, 0xba, 0x76, 0x6f, 0xa3, 0xc3, 0x0f, 0xe6, 0x2a, 0x4a, 0x86, 0x9f, 0x53, 0x33, 0xff,
    0x87, 0x4b, 0x2b, 0xe7, 0xfe, 0x32, 0x52, 0x9e, 0x77, 0xbb, 0xdb, 0x17, 0x0e, 0xc2, 0xa2, 0x6e,
    0x7a, 0xb6, 0xd6, 0x1a, 0x03, 0xcf, 0xaf, 0x63, 0x8a, 0x46, 0x26, 0xea, 0xf3, 0x3f, 0x5f, 0x93,
    0xa9, 0x65, 0x05, 0xc9, 0xd0, 0x1c, 0x7c, 0xb0, 0x59, 0x95, 0xf5, 0x39, 0x20, 0xec, 0x8c, 0x40,
    0x54, 0x98, 0xf8, 0x34, 0x2d, 0xe1, 0x81, 0x4d, 0xa4, 0x68, 0x08, 0xc4, 0xdd, 0x11, 0x71, 0xbd,
])


class _GF256:
    """GF(2^8) 算术表，本原多项式 0x187。

    来源: correct.h:180-181  correct_rs_primitive_polynomial_ccsds = 0x187
    """

    def __init__(self, poly: int = RS_POLY):
        self.exp = [0] * 512
        self.log = [0] * 256
        x = 1
        for i in range(255):
            self.exp[i] = x
            self.log[x] = i
            x <<= 1
            if x & 0x100:
                x ^= poly
        for i in range(255, 512):
            self.exp[i] = self.exp[i - 255]

    def mul(self, a: int, b: int) -> int:
        if a == 0 or b == 0:
            return 0
        return self.exp[self.log[a] + self.log[b]]

    def div(self, a: int, b: int) -> int:
        if a == 0:
            return 0
        return self.exp[(self.log[a] - self.log[b]) % 255]

    def pow(self, a: int, n: int) -> int:
        if a == 0:
            return 0
        return self.exp[(self.log[a] * n) % 255]


_GF = _GF256()


def _rs_generator_poly(nroots: int = RS_NROOTS, fcr: int = RS_FCR,
                       prim: int = RS_PRIM) -> List[int]:
    """生成 RS 生成多项式系数（GF 域）。

    g(x) = (x + α^fcr)(x + α^(fcr+prim))...(x + α^(fcr+(nroots-1)*prim))
    来源: reedsolomon.cpp:34  fcr=112, prim=11, nroots=32
    """
    g = [1]
    for i in range(nroots):
        # 乘 (x + α^(fcr + i*prim))
        root = _GF.exp[(fcr + i * prim) % 255]
        new_g = [0] * (len(g) + 1)
        for j in range(len(g)):
            new_g[j] ^= _GF.mul(g[j], root)   # 常数项
            new_g[j + 1] ^= g[j]               # x 项
        g = new_g
    return g


_RS_GEN = _rs_generator_poly()


def rs_encode(codeword: bytearray) -> None:
    """对 255 字节 codeword（前 223 数据，后 32 校验）做 RS 编码（conventional basis）。

    codeword[i] 对应多项式系数 x^(n-1-i)（最高次在前）。
    g(x) = Σ g[j] x^j, g[RS_NROOTS]=1（首一）。
    直接多项式长除：d(x)*x^nroots ÷ g(x)，余数填低次端。
    来源: reedsolomon.cpp:128-143  ReedSolomon::encode()
    """
    gen = _RS_GEN  # gen[0]=常数, gen[nroots]=1
    # dividend[0..k-1] = 数据, dividend[k..n-1] = 0（余数区）
    div = list(codeword[:RS_K]) + [0] * RS_NROOTS
    for i in range(RS_K):
        coef = div[i]
        if coef != 0:
            # g[j] x^j 对齐到 x^(n-1-i)：index = i + nroots - j
            for j in range(RS_NROOTS + 1):
                div[i + RS_NROOTS - j] ^= _GF.mul(gen[j], coef)
    # div[k..n-1] 即校验字节
    for i in range(RS_NROOTS):
        codeword[RS_K + i] = div[RS_K + i]


def rs_decode(codeword: bytearray) -> int:
    """RS(255,223) 译码（conventional basis）。返回纠错字节数；-1 = 不可纠。

    约定: codeword[j] 对应多项式系数 x^(n-1-j)（j=0 最高次）。
    来源: reedsolomon.cpp:63-116  ReedSolomon::decode()
    使用 Berlekamp-Massey + Chien search + Forney。
    """
    n = RS_N
    # ── 伴随子 S_i = c(α^(fcr+i*prim))，Horner 从 j=0（最高次）──
    syn = [0] * RS_NROOTS
    for i in range(RS_NROOTS):
        root = _GF.exp[(RS_FCR + i * RS_PRIM) % 255]
        s = 0
        for j in range(n):
            s = _GF.mul(s, root) ^ codeword[j]
        syn[i] = s
    if max(syn) == 0:
        return 0  # 无错误

    # ── Berlekamp-Massey 求错误位置多项式 σ(x) ──
    sigma = [1]
    omega = [1]  # b(x)
    L = 0
    m = 1
    bb = 1
    for n_i in range(RS_NROOTS):
        d = syn[n_i]
        for i in range(1, L + 1):
            d ^= _GF.mul(sigma[i], syn[n_i - i])
        if d == 0:
            m += 1
        elif 2 * L <= n_i:
            t = sigma[:]
            coef = _GF.div(d, bb)
            need = m + len(omega)
            if len(sigma) < need:
                sigma = sigma + [0] * (need - len(sigma))
            for i in range(len(omega)):
                sigma[i + m] ^= _GF.mul(coef, omega[i])
            L = n_i + 1 - L
            omega = t
            bb = d
            m = 1
        else:
            coef = _GF.div(d, bb)
            need = m + len(omega)
            if len(sigma) < need:
                sigma = sigma + [0] * (need - len(sigma))
            for i in range(len(omega)):
                sigma[i + m] ^= _GF.mul(coef, omega[i])
            m += 1

    # ── Chien 搜索错误位置（在 β=α^prim 域内）──
    # 根间隔 prim≠1 时，定位数为 Y_j = β^(n-1-j)，β=α^prim
    beta = _GF.exp[RS_PRIM]
    errs_pos = []
    for j in range(n):
        y_inv = _GF.exp[(-RS_PRIM * (n - 1 - j)) % 255]
        eval_sigma = 0
        for k in range(len(sigma)):
            eval_sigma ^= _GF.mul(sigma[k], _GF.pow(y_inv, k))
        if eval_sigma == 0:
            errs_pos.append(j)
    if len(errs_pos) > RS_NROOTS // 2:
        return -1  # 错误数超过纠错能力

    # ── Forney 求错误值 ──
    # Ω(x) = S(x)·σ(x) mod x^2t
    omega_poly = [0] * RS_NROOTS
    for i in range(RS_NROOTS):
        acc = syn[i]
        for j in range(1, min(i + 1, len(sigma))):
            acc ^= _GF.mul(sigma[j], syn[i - j])
        omega_poly[i] = acc

    for j in errs_pos:
        y_inv = _GF.exp[(-RS_PRIM * (n - 1 - j)) % 255]
        # Ω(Y^-1)
        omega_eval = 0
        for k in range(len(omega_poly)):
            omega_eval ^= _GF.mul(omega_poly[k], _GF.pow(y_inv, k))
        # σ'(Y^-1) = Σ_{odd m} σ[m]·(Y^-1)^(m-1)
        sp_eval = 0
        for m in range(1, len(sigma)):
            if m % 2 == 1:
                sp_eval ^= _GF.mul(sigma[m], _GF.pow(y_inv, m - 1))
        if sp_eval == 0:
            return -1
        # e' = Ω(Y^-1)/σ'(Y^-1)；单错推导: e = e'·Y_j / A
        #   Y_j = α^(prim*(n-1-j)), A = α^(fcr*(n-1-j))
        e_tag = _GF.div(omega_eval, sp_eval)
        y_j = _GF.exp[(RS_PRIM * (n - 1 - j)) % 255]
        a_j = _GF.exp[(RS_FCR * (n - 1 - j)) % 255]
        e_val = _GF.div(_GF.mul(e_tag, y_j), a_j)
        codeword[j] ^= e_val
    return len(errs_pos)


def rs_encode_interleaved(block: bytearray) -> None:
    """对 1020 字节块做 I=4 交错 RS 编码（含 dual-basis 变换）。

    来源: reedsolomon.cpp:118-126 encode_interlaved, :128-143 encode
    输入 block 长度必须为 RS_INTERLEAVE*RS_N = 1020；前 892B 数据。
    """
    i = RS_INTERLEAVE
    for b in range(i):
        # deinterleave: 抽出第 b 个 codeblock
        cb = bytearray(RS_N)
        for ii in range(RS_N):
            cb[ii] = block[ii * i + b]
        # dual-basis: conventional → CCSDS
        for j in range(RS_N):
            cb[j] = _FROM_DUAL_BASIS[cb[j]]
        rs_encode(cb)
        # CCSDS → conventional
        for j in range(RS_N):
            cb[j] = _TO_DUAL_BASIS[cb[j]]
        # interleave 写回
        for ii in range(RS_N):
            block[ii * i + b] = cb[ii]


def rs_decode_interleaved(block: bytearray) -> List[int]:
    """对 1020 字节块做 I=4 交错 RS 译码（含 dual-basis 变换）。

    返回每个 codeblock 的纠错字节数（-1 = 不可纠）。
    来源: reedsolomon.cpp:53-61 decode_interlaved, :63-116 decode
    """
    i = RS_INTERLEAVE
    errors = [0] * i
    for b in range(i):
        cb = bytearray(RS_N)
        for ii in range(RS_N):
            cb[ii] = block[ii * i + b]
        for j in range(RS_N):
            cb[j] = _FROM_DUAL_BASIS[cb[j]]
        err = rs_decode(cb)
        if err < 0:
            errors[b] = -1
        else:
            errors[b] = err
            # 译码成功 → 取前 223 数据写回 cb（conventional）
            for j in range(RS_N):
                cb[j] = _TO_DUAL_BASIS[cb[j]]
            for ii in range(RS_N):
                block[ii * i + b] = cb[ii]
    return errors


# ============================================================================
# 卷积码 (K=7, R=1/2) 编码 + Viterbi 软判决译码
# 来源: viterbi27.h:16 CCSDS_R2_K7_POLYS={79,109};
#        cc_encoder.cpp:108-113 移位寄存器约定
# ============================================================================

def conv_encode_bits(in_bits: np.ndarray) -> np.ndarray:
    """CCSDS K=7 R=1/2 卷积编码。

    来源: cc_encoder.cpp:108-113
      my_state = (my_state << 1) | (in[i] & 1)
      out[2i]   = parity(my_state & 0x4F)
      out[2i+1] = parity(my_state & 0x6D)
    """
    g1, g2 = VIT_POLYS
    state = 0
    out = np.empty(len(in_bits) * 2, dtype=np.uint8)
    for i, bit in enumerate(in_bits):
        state = ((state << 1) | int(bit)) & 0x7F  # K=7 → 7 位移位寄存器
        out[2 * i] = (state & g1).bit_count() & 1
        out[2 * i + 1] = (state & g2).bit_count() & 1
    return out


class ViterbiDecoder:
    """软判决 Viterbi 译码（K=7, R=1/2）。

    64 状态，ACS 加-比-选，回溯长度 ~5*K=35。
    输入为软符号（float，越大越倾向 bit=0；BPSK 中 +1→bit0, -1→bit1）。
    来源: viterbi27.h / cc_decoder.*
    """

    def __init__(self, traceback: int = 40):
        self.nstates = 1 << (VIT_K - 1)  # 64
        self.traceback = traceback
        g1, g2 = VIT_POLYS
        n = self.nstates
        # 预计算反向转移：对每个当前状态 cur
        self.b_arr = (np.arange(n) & 1).astype(np.int32)           # 输入比特
        self.p0_arr = (np.arange(n) >> 1).astype(np.int32)          # 前驱 0
        self.p1_arr = (self.p0_arr | 32).astype(np.int32)           # 前驱 1
        # 预计算每条转移的期望软值 (1-2*o)
        self.exp0_p0 = np.empty(n)
        self.exp1_p0 = np.empty(n)
        self.exp0_p1 = np.empty(n)
        self.exp1_p1 = np.empty(n)
        for cur in range(n):
            b = cur & 1
            for p, (e0, e1) in ((cur >> 1, (self.exp0_p0, self.exp1_p0)),
                                ((cur >> 1) | 32, (self.exp0_p1, self.exp1_p1))):
                full = ((p << 1) | b) & 0x7F
                o1 = (full & g1).bit_count() & 1
                o2 = (full & g2).bit_count() & 1
                e0[cur] = 1.0 - 2.0 * o1
                e1[cur] = 1.0 - 2.0 * o2

    def decode(self, soft: np.ndarray) -> np.ndarray:
        """输入软符号数组（长度为偶数），返回硬比特。"""
        nsym = len(soft) // 2
        n = self.nstates
        pm = np.full(n, 1e18)
        pm[0] = 0.0
        tb = np.zeros((nsym, n), dtype=np.int32)
        soft = soft.astype(np.float64)
        p0 = self.p0_arr
        p1 = self.p1_arr
        b = self.b_arr

        for i in range(nsym):
            s0 = soft[2 * i]
            s1 = soft[2 * i + 1]
            # 向量化 ACS
            d0 = (s0 - self.exp0_p0) ** 2 + (s1 - self.exp1_p0) ** 2
            d1 = (s0 - self.exp0_p1) ** 2 + (s1 - self.exp1_p1) ** 2
            cand0 = pm[p0] + d0
            cand1 = pm[p1] + d1
            choose0 = cand0 < cand1
            new_pm = np.where(choose0, cand0, cand1)
            tb[i] = np.where(choose0, p0, p1)
            pm = new_pm

        # 回溯
        out = np.empty(nsym, dtype=np.uint8)
        state = int(np.argmin(pm))
        for i in range(nsym - 1, -1, -1):
            out[i] = state & 1
            state = int(tb[i, state])
        return out


# ============================================================================
# BPSK 调制 / 解调 + RRC 成形滤波
# 来源: GK2A.json psk_demod: bpsk, symbolrate=128e3, rrc_alpha=0.5
# ============================================================================

def rrc_filter(sps: int, alpha: float = GK2A_RRC_ALPHA,
               num_taps: int = 33) -> np.ndarray:
    """根升余弦 (RRC) 成形/匹配滤波器。

    来源: GK2A.json "rrc_alpha": 0.5
    """
    half = num_taps // 2
    t = np.arange(num_taps) - half
    h = np.zeros(num_taps)
    for i, ti in enumerate(t):
        if ti == 0:
            h[i] = 1.0 - alpha + 4 * alpha / math.pi
        elif abs(ti) == 1.0 / (4 * alpha):
            h[i] = (alpha / math.sqrt(2)) * (
                (1 + 2 / math.pi) * math.sin(math.pi / (4 * alpha))
                + (1 - 2 / math.pi) * math.cos(math.pi / (4 * alpha))
            )
        else:
            num = math.sin(math.pi * ti * (1 - alpha) / sps) + 4 * alpha * ti / sps * math.cos(
                math.pi * ti * (1 + alpha) / sps)
            den = math.pi * ti / sps * (1 - (4 * alpha * ti / sps) ** 2)
            h[i] = num / den if abs(den) > 1e-12 else 0.0
    h /= np.sqrt(np.sum(h ** 2))
    return h


def bpsk_modulate(bits: np.ndarray, sps: int, alpha: float = GK2A_RRC_ALPHA) -> np.ndarray:
    """比特流 → RRC 成形 BPSK IQ 采样（complex64）。

    bit 0 → +1, bit 1 → -1。上采样 sps 倍后 RRC 脉冲成形。
    """
    symbols = 1.0 - 2.0 * bits.astype(np.float64)  # bit0→+1, bit1→-1
    up = np.zeros(len(bits) * sps, dtype=np.float64)
    up[::sps] = symbols
    h = rrc_filter(sps, alpha)
    sig = np.convolve(up, h, mode="same")
    return (sig + 0j).astype(np.complex64)


def bpsk_demod(iq: np.ndarray, sps: int, alpha: float = GK2A_RRC_ALPHA,
               f0_offset: float = 0.0) -> np.ndarray:
    """IQ 采样 → BPSK 软符号（float，长度 = 编码位数）。

    步骤：RRC 匹配滤波 → 频偏校正 → 最佳采样相位搜索 → 四次方相位校正。
    返回每个编码位的软值（>0 → bit0, <0 → bit1）。
    """
    iq = iq.astype(np.complex128)
    # 1. RRC 匹配滤波
    h = rrc_filter(sps, alpha)
    filt = np.convolve(iq, h, mode="same")

    # 2. 频偏校正
    if f0_offset != 0.0:
        t = np.arange(len(filt)) / GK2A_LRIT_SAMPLERATE
        filt = filt * np.exp(-1j * 2 * np.pi * f0_offset * t)

    # 3. 搜索最佳采样相位：在 sps 个候选相位中，选眼图最张开的
    #    （mode="same" 卷积已居中；从 0 开始搜索，跳过前几 tap 瞬态）
    best_phase = 0
    best_score = -1.0
    start = 0
    for ph in range(sps):
        idx = start + ph
        if idx + sps >= len(filt):
            break
        seg = filt[idx::sps]
        if len(seg) < 16:
            continue
        # BPSK 最佳相位：实部能量集中，虚部能量最小
        score = np.abs(seg.real).mean() / (np.abs(seg.imag).mean() + 1e-9)
        if score > best_score:
            best_score = score
            best_phase = ph

    idx0 = start + best_phase
    n_sym = (len(filt) - idx0) // sps
    soft_iq = filt[idx0: idx0 + n_sym * sps: sps]

    # 4. 残留相位校正：四次方
    ph = np.angle(np.mean(soft_iq ** 4)) / 4.0
    soft_iq = soft_iq * np.exp(-1j * ph)
    soft = soft_iq.real.astype(np.float64)
    return soft


# ============================================================================
# 帧同步：0x1ACFFC1D 相关检测
# 来源: module_ccsds_conv_concat_decoder.cpp:90,120
# ============================================================================

def bits_to_bytes(bits: np.ndarray) -> bytes:
    """硬比特数组（MSB first）打包为字节。"""
    n = (len(bits) + 7) // 8
    b = np.zeros(n, dtype=np.uint8)
    for i, bit in enumerate(bits):
        b[i // 8] |= (int(bit) & 1) << (7 - (i % 8))
    return bytes(b)


def bytes_to_bits(data: bytes) -> np.ndarray:
    """字节（MSB first）解包为硬比特数组。"""
    bits = np.zeros(len(data) * 8, dtype=np.uint8)
    for i, byte in enumerate(data):
        for j in range(8):
            bits[i * 8 + j] = (byte >> (7 - j)) & 1
    return bits


def frame_sync_search(bitstream: np.ndarray) -> int:
    """在硬比特流中搜索同步字 0x1ACFFC1D，返回比特偏移。未找到 -1。

    来源: module_ccsds_conv_concat_decoder.cpp:120 BPSK_CCSDS_Deframer
    """
    target = bytes_to_bits(SYNC_WORD_BYTES)
    L = len(target)
    for i in range(len(bitstream) - L):
        if np.array_equal(bitstream[i:i + L], target):
            return i
    return -1


# ============================================================================
# VCDU → M_PDU → TP_PDU → SessionPDU（复用 goes_lrit 的传输层逻辑）
# ============================================================================

# 复用 goes_lrit 中的 CCSDS 传输层实现（同源 CCSDS 标准）
from .goes_lrit import (  # noqa: E402
    VCDU,
    TransportPDU,
    crc16_ccitt,
    FILL_VCID,
    FILL_APID,
    MPDU_NO_PACKET,
    H_PRIMARY,
    H_IMAGE_STRUCTURE,
    H_ANNOTATION,
    H_SEGMENT_ID,
    H_RICE_COMPRESSION,
    PRIMARY_HEADER_LEN,
    _VirtualChannel,
)


# ============================================================================
# GK-2A LRIT 文件头解析（GK-2A 专用分段头）
# 来源: gk2a_headers.h:43-61
# ============================================================================

@dataclass
class GK2AHeader:
    """解析后的 GK-2A LRIT 文件头。"""
    file_type: int = 0
    total_header_length: int = 0
    data_length_bits: int = 0
    bits_per_pixel: int = 0
    columns: int = 0
    lines: int = 0
    compression: int = 0          # 0=无压缩, 1=小波, 2=JPEG (decomp.cpp:27-43)
    annotation: str = ""
    # GK-2A 分段头 (type=128)。来源: gk2a_headers.h:43-61
    image_seq_nb: int = 0
    total_segments_nb: int = 0
    line_nb: int = 0              # 本段起始行号 (1-based)


def parse_gk2a_headers(buf: bytes) -> GK2AHeader:
    """解析 GK-2A LRIT 文件头。

    通用头与 GOES 一致；分段头 (type=128) 布局来自 gk2a_headers.h:43-61:
      data[3] = image_seq_nb
      data[4] = total_segments_nb
      data[5..6] = line_nb (BE)
    """
    out = GK2AHeader()
    if len(buf) < PRIMARY_HEADER_LEN or buf[0] != H_PRIMARY:
        return out
    out.file_type = buf[3]
    out.total_header_length = struct.unpack_from(">I", buf, 4)[0]
    out.data_length_bits = struct.unpack_from(">Q", buf, 8)[0]

    pos = 0
    total = out.total_header_length
    while pos + 3 <= total and pos < len(buf):
        htype = buf[pos]
        hlen = (buf[pos + 1] << 8) | buf[pos + 2]
        if hlen == 0:
            break
        p = pos + 3
        if htype == H_IMAGE_STRUCTURE and hlen >= 8:
            out.bits_per_pixel = buf[p]
            out.columns = (buf[p + 1] << 8) | buf[p + 2]
            out.lines = (buf[p + 3] << 8) | buf[p + 4]
            out.compression = buf[p + 5]
        elif htype == H_ANNOTATION:
            out.annotation = bytes(buf[pos + 3: pos + hlen]).rstrip(b"\x00").decode("ascii", "replace")
        elif htype == H_SEGMENT_ID and hlen >= 7:
            # GK-2A 专用分段头。来源: gk2a_headers.h:53-60
            out.image_seq_nb = buf[p]
            out.total_segments_nb = buf[p + 1]
            out.line_nb = (buf[p + 2] << 8) | buf[p + 3]
        pos += hlen
    return out


# ============================================================================
# GK-2A VCDU 流 → LRIT 文件重组引擎
# ============================================================================

class GK2ALRITReassembler:
    """把 892B VCDU 流重组为完整 LRIT 文件。

    复用 goes_lrit._VirtualChannel 的 M_PDU/TP_PDU/SessionPDU 状态机。
    """

    def __init__(self) -> None:
        self._vcs: Dict[int, _VirtualChannel] = {}

    def feed_vcdu(self, raw_vcdu: bytes) -> List[bytes]:
        vcdu = VCDU.parse(raw_vcdu)
        if vcdu.vcid == FILL_VCID:
            return []
        vc = self._vcs.get(vcdu.vcid)
        if vc is None:
            vc = _VirtualChannel(vcdu.vcid)
            self._vcs[vcdu.vcid] = vc
        return vc.process(vcdu)


# ============================================================================
# GK-2A 图像段组装 → 灰度画布
# ============================================================================

@dataclass
class GK2AImage:
    width: int
    height: int
    pixels: np.ndarray          # uint8, shape (height, width)
    annotation: str = ""
    image_seq_nb: int = 0


class GK2AImageAssembler:
    """按 GK-2A ImageSegmentationIdentification 把多段拼成整图。

    来源: segment_decoder.h:54-65 pushSegment()
      image_seq_nb 区分整图；line_nb 为该段起始行；段宽=columns。
    """

    def __init__(self) -> None:
        self._segs: Dict[int, Dict[int, Tuple[GK2AHeader, bytes]]] = {}

    def add_file(self, file_buf: bytes) -> Optional[GK2AImage]:
        h = parse_gk2a_headers(file_buf)
        if h.file_type != 0:
            return None
        data = bytes(file_buf[h.total_header_length:])
        ident = h.image_seq_nb
        line = h.line_nb
        pool = self._segs.setdefault(ident, {})
        pool[line] = (h, data)

        total_seg = max((sh.total_segments_nb for sh, _ in pool.values()), default=0)
        if total_seg == 0 or len(pool) < total_seg:
            return None

        width = h.columns
        height = h.lines * total_seg if h.lines else 0
        # 每行像素 = columns；段内行数 = len(data)/columns
        canvas = np.zeros((height, width), dtype=np.uint8)
        for y0, (sh, sdata) in sorted(pool.items()):
            rows = len(sdata) // max(1, width)
            arr = np.frombuffer(sdata[: rows * width], dtype=np.uint8)
            arr = arr.reshape(rows, width)
            r0 = y0 - 1  # line_nb 1-based
            canvas[r0: r0 + rows, :] = arr
        self._segs.pop(ident, None)
        return GK2AImage(width=width, height=height, pixels=canvas,
                        annotation=h.annotation, image_seq_nb=ident)


# ============================================================================
# 顶层解码管道：IQ → PNG
# ============================================================================

@dataclass
class GK2ADecodeResult:
    success: bool
    png_path: str = ""
    width: int = 0
    height: int = 0
    annotation: str = ""
    n_files: int = 0
    error: str = ""
    metadata: Dict = field(default_factory=dict)


def decode_iq_to_image(iq: np.ndarray, out_png: str,
                       sps: int = 8, f_offset: float = 0.0) -> GK2ADecodeResult:
    """完整管道：IQ complex64 → 保存云图 PNG。

    参数:
        iq: complex64 采样数组
        out_png: 输出 PNG 路径
        sps: 每个符号采样数
        f_offset: 发射端加的频偏（Hz），用于载波校正
    """
    try:
        # 1. BPSK 解调 → 软符号
        soft = bpsk_demod(iq, sps, f0_offset=f_offset)
        if len(soft) < CADU_BITS * 2:
            return GK2ADecodeResult(success=False, error="软符号长度不足")

        # 2. Viterbi 译码 → 硬比特
        vit = ViterbiDecoder()
        bits = vit.decode(soft)

        # 3. 帧同步
        sync_off = frame_sync_search(bits)
        if sync_off < 0:
            return GK2ADecodeResult(success=False, error="未找到同步字 0x1ACFFC1D")

        # 4. 按 CADU 切帧
        bits = bits[sync_off:]
        n_frames = len(bits) // CADU_BITS
        vcdus: List[bytes] = []
        reassembler = GK2ALRITReassembler()
        assembler = GK2AImageAssembler()
        img: Optional[GK2AImage] = None

        for fi in range(n_frames):
            frame_bits = bits[fi * CADU_BITS: (fi + 1) * CADU_BITS]
            frame = bytearray(bits_to_bytes(frame_bits))
            # 4a. 同步字校验
            if bytes(frame[:4]) != SYNC_WORD_BYTES:
                continue
            # 4b. 解扰（同步字之后 1020 字节；PN 索引从 0 起对应 cadu[4]）
            # 来源: module_ccsds_conv_concat_decoder.cpp:178
            #   derand_ccsds(&cadu[4], d_cadu_bytes - d_derand_from)
            derandomize_ccsds(frame, CADU_BYTES - DERAND_OFFSET, offset=DERAND_OFFSET)
            # 4c. RS 译码（交错 I=4）。来源: reedsolomon.cpp:53-61
            block = frame[DERAND_OFFSET: CADU_BYTES]
            errs = rs_decode_interleaved(block)
            if any(e < 0 for e in errs):
                continue  # 不可纠错帧丢弃
            # 4d. 取 892B VCDU（交错块前 892B 即 VCDU 自然序）
            vcdu = bytes(block[:VCDU_LEN])
            files = reassembler.feed_vcdu(vcdu)
            for fbuf in files:
                got = assembler.add_file(fbuf)
                if got is not None:
                    img = got

        if img is None:
            return GK2ADecodeResult(success=False, error="未组装出完整图像",
                                    n_files=len(reassembler._vcs))

        # 5. 保存 PNG
        from PIL import Image
        Image.fromarray(img.pixels, mode="L").save(out_png)
        return GK2ADecodeResult(
            success=True, png_path=out_png,
            width=img.width, height=img.height,
            annotation=img.annotation,
            n_files=len(reassembler._vcs),
            metadata={"image_seq_nb": img.image_seq_nb},
        )
    except Exception as e:  # noqa: BLE001
        return GK2ADecodeResult(success=False, error=f"{type(e).__name__}: {e}")


# ============================================================================
# 编码端（用于往返测试）：图像 → LRIT 文件 → VCDU → RS → 卷积 → BPSK IQ
# ============================================================================

def build_gk2a_lrit_file(image: np.ndarray, line_nb: int, total_segments: int,
                         image_seq: int = 1, annotation: str = "GK2A TEST") -> bytes:
    """把一段图像行打包为一个完整 GK-2A LRIT 文件字节流。

    布局: PrimaryHeader(16) + ImageStructure(8+3) + Segmentation(7+3) + Annotation
          + 原始像素数据（compression=0 无压缩）。
    """
    hdr = bytearray()
    # ── PrimaryHeader type=0, len=16
    hdr += bytes([H_PRIMARY])
    hdr += (16).to_bytes(2, "big")
    hdr += bytes([0])            # file_type_code=0 (image)
    data_bits = image.size * 8
    hdr += (16).to_bytes(4, "big")       # total_header_length（占位，后面回填）
    hdr += data_bits.to_bytes(8, "big")  # data_length
    # ── ImageStructureRecord type=1（record_length=9: 3头+6负载）
    # 来源: xrit_file.h:33-49  ImageStructureRecord
    hdr += bytes([H_IMAGE_STRUCTURE])
    hdr += (9).to_bytes(2, "big")
    hdr += bytes([8])                       # bit_per_pixel=8
    hdr += image.shape[1].to_bytes(2, "big") # columns
    hdr += image.shape[0].to_bytes(2, "big")# lines
    hdr += bytes([0])                       # compression_flag=0 (无压缩)
    # ── GK-2A SegmentationIdentification type=128
    # 来源: gk2a_headers.h:43-61
    hdr += bytes([H_SEGMENT_ID])
    hdr += (7).to_bytes(2, "big")
    hdr += bytes([image_seq & 0xFF])        # image_seq_nb
    hdr += bytes([total_segments & 0xFF])   # total_segments_nb
    hdr += line_nb.to_bytes(2, "big")       # line_nb (BE)
    # ── Annotation type=4
    ann = annotation.encode("ascii")[:64]
    hdr += bytes([H_ANNOTATION])
    hdr += (len(ann) + 3).to_bytes(2, "big")
    hdr += ann
    # 回填 total_header_length
    struct.pack_into(">I", hdr, 4, len(hdr))
    # ── 像素数据
    hdr += image.astype(np.uint8).tobytes()
    return bytes(hdr)


def file_to_vcdus(file_buf: bytes, vcid: int = 10) -> List[bytes]:
    """把一个完整 LRIT 文件切成 VCDU 流（含 TP_PDU 分割、M_PDU 封装）。

    编码端模拟 CCSDS 源包封装：TP_PDU = 6B头 + payload + 2B CRC。
    """
    # 构造一个大 TP_PDU（整文件作为 payload，带 CRC）
    # CCSDS 源包头: version=0, type=0, secHdr=0, APID=...
    # seqFlag=3 (整包), seqCount=0, length = len(payload)-1
    # 注意: SessionPDU 重组时会跳过首 TP_PDU payload 的前 10 字节
    # （来源: goes_lrit session_pdu.cc:78-82），故这里补 10 字节哑元。
    payload = bytearray(10) + bytearray(file_buf)
    # 加 CRC16
    crc = crc16_ccitt(bytes(payload))
    payload += crc.to_bytes(2, "big")
    pkt_len = len(payload) - 1
    apid = 200
    hdr = bytearray()
    hdr += bytes([0x00 | (apid >> 8) & 0x07])  # version(3)=0,type(1)=0,secHdr(1)=0,APID_h(3)
    hdr += bytes([apid & 0xFF])
    hdr += bytes([0xC0 | 0])                    # seqFlag=3(整包)
    hdr += bytes([0])                           # seqCount
    hdr += pkt_len.to_bytes(2, "big")
    tpdu = bytes(hdr) + bytes(payload)

    # 切 VCDU：每 VCDU 892B，头 6B，M_PDU 头 2B，净荷 884B
    vcdu_hdr = bytearray(6)
    vcdu_hdr[0] = 0x00              # version=0
    vcdu_hdr[1] = vcid & 0x3F       # SCID_low=0, VCID
    # counter 在封装时填
    vcdus = []
    pos = 0
    counter = 0
    # 第一个 VCDU 的 M_PDU 第一头指针
    # M_PDU = 2B (FHP) + 884B 数据
    MPDU_DATA = 884
    # 第一个包起始偏移：在 M_PDU 数据区内从 0 开始（FHP=0）
    first = True
    while pos < len(tpdu) or first:
        body = bytearray(886)  # VCDU 数据区 886B = M_PDU头2 + MPDU_DATA 884
        if first:
            body[0] = 0x00
            body[1] = 0x00   # FHP = 0
            chunk = tpdu[pos: pos + MPDU_DATA]
            body[2: 2 + len(chunk)] = chunk
            pos += len(chunk)
            first = False
        else:
            body[0] = 0x07
            body[1] = 0xFF   # FHP = 2047 (无新包)
            chunk = tpdu[pos: pos + MPDU_DATA]
            body[2: 2 + len(chunk)] = chunk
            pos += len(chunk)
        # VCDU 计数器
        vc = bytearray(vcdu_hdr)
        vc[2] = (counter >> 16) & 0xFF
        vc[3] = (counter >> 8) & 0xFF
        vc[4] = counter & 0xFF
        vc[5] = 0
        vcdus.append(bytes(vc) + bytes(body))
        counter += 1
    return vcdus


def vcdus_to_cadu(vcdus: List[bytes]) -> bytes:
    """VCDU 列表 → CADU 字节流（RS 编码 + 解扰 + 同步字）。

    编码端顺序（与 SatDump 接收端相反）：
      VCDU(892) → RS 交错编码(1020) → 解扰异或PN → 加同步字(4) = CADU(1024)
    来源: module_ccsds_conv_concat_decoder.cpp:177-181
    """
    out = bytearray()
    for vcdu in vcdus:
        if len(vcdu) != VCDU_LEN:
            raise ValueError(f"VCDU 长度 {len(vcdu)} != {VCDU_LEN}")
        # 构造 1020B 交错块：先把 892B 数据按 I=4 放入前 892 位置，后 128 留 0
        block = bytearray(RS_INTERLEAVE * RS_N)  # 1020
        for i, b in enumerate(vcdu):
            block[i] = b
        # RS 交错编码（填校验）
        rs_encode_interleaved(block)
        # 解扰（PN 从 block[0] 起；对应 CADU 中 sync 之后）
        derandomize_ccsds(block, len(block))
        # 同步字 + block
        out += SYNC_WORD_BYTES + block
    return bytes(out)


def cadu_to_iq(cadu: bytes, sps: int = 8, alpha: float = GK2A_RRC_ALPHA,
               f_offset: float = 0.0) -> np.ndarray:
    """CADU 字节流 → 卷积编码 → BPSK 调制 → IQ complex64。

    前导 ~200 bit 空闲 0 让 Viterbi 译码器收敛（避免起始段不可靠）。
    """
    bits = bytes_to_bits(cadu)
    # 前导空闲比特（全 0，卷积码保持状态 0）
    idle = np.zeros(200, dtype=np.uint8)
    tx_bits = np.concatenate([idle, bits])
    enc = conv_encode_bits(tx_bits)
    iq = bpsk_modulate(enc, sps, alpha)
    if f_offset != 0.0:
        t = np.arange(len(iq)) / GK2A_LRIT_SAMPLERATE
        iq = iq * np.exp(1j * 2 * np.pi * f_offset * t)
    return iq


# ============================================================================
# 工具注册表接入
# ============================================================================

def register_tool_registry(registry) -> None:
    """注册 gk2a_lrit_decode 工具。

    输入 IQ 文件路径（complex64 raw），输出 PNG 路径 + 元数据。
    风格参考 goes_lrit.register_tool_registry。
    """
    registry.register(
        name="gk2a_lrit_decode",
        description=(
            "GK-2A LRIT 全管道解码（对标 SatDump GK2A.json）：从 IQ complex64 raw 文件 "
            "完成 BPSK 解调→Viterbi(K=7,R=1/2,0x4F/0x6D)→帧同步0x1ACFFC1D→CCSDS解扰→"
            "RS(255,223,I=4)→VCDU→M_PDU→TP_PDU→SessionPDU→GK-2A文件头→图像段组装，"
            "输出灰度云图 PNG。返回 PNG 路径/宽高/annotation/元数据。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "iq_path": {
                    "type": "string",
                    "description": "complex64 IQ raw 文件路径",
                },
                "out_png": {
                    "type": "string",
                    "description": "输出 PNG 文件路径",
                },
                "sps": {
                    "type": "integer",
                    "description": "每符号采样数（默认 8）",
                    "default": 8,
                },
            },
            "required": ["iq_path", "out_png"],
        },
        handler=_tool_gk2a_decode,
        category="satellite",
    )


def _tool_gk2a_decode(args):
    """handler：读 IQ raw → 解码 → 返回 ToolResult。"""
    import json as _json
    from .tool_registry import ToolResult

    iq_path = args["iq_path"]
    out_png = args["out_png"]
    sps = int(args.get("sps", 8))
    iq = np.fromfile(iq_path, dtype=np.complex64)
    res = decode_iq_to_image(iq, out_png, sps=sps)
    return ToolResult(
        success=res.success,
        content=_json.dumps({
            "png": res.png_path, "width": res.width, "height": res.height,
            "annotation": res.annotation, "error": res.error,
        }, ensure_ascii=False),
        data={"success": res.success, "png": res.png_path,
              "width": res.width, "height": res.height,
              "error": res.error, "metadata": res.metadata},
    )
