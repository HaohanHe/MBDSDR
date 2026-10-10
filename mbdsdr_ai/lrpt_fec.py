# SPDX-License-Identifier: MIT
"""LRPT 卷积内码 + CCSDS 解扰 + RS 外码（干净室自写，第②步）。

本模块依据 ``docs/learn/phase63/weather-sat-digital-study.md`` 自述重写：
  - Viterbi K=7 r=1/2 软判决译码（多项式 {79,109}，CCSDS 域）
  - CCSDS 同步解扰（复用本仓 ``fec.Scrambler``）
  - RS(255,223)×4 交织解码（复用本仓 ``fec.ReedSolomon``）

参考（仅机制证据，未读入 GPL 源码）：
  - SatDump ``viterbi27.h:8``（CCSDS_R2_K7_POLYS = {79,109}）
  - SatDump ``reedsolomon.cpp:34``（RS223: fcr=112, index=11, roots=32）
  - goestools ``derandomizer.cc:13-30``（PN x^8+x^7+x^5+x^3+1 init 0xff）

红线：纯 NumPy / 纯 Python，无 C 扩展；MIT；固定 seed 可复现。
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np

from .fec import ReedSolomon, RSResult, Scrambler

__all__ = [
    "CCSDS_POLY1",
    "CCSDS_POLY2",
    "CONV_K",
    "viterbi_decode",
    "convolve_encode",
    "LrptCaduDecoder",
    "CADU_SIZE",
    "RS_INTERLEAVE",
]

# --------------------------------------------------------------------------- #
# CCSDS 卷积码参数（公开标准；证据 viterbi27.h:8 / viterbi.h:27）
# --------------------------------------------------------------------------- #
CONV_K: int = 7                  # 约束长度
CCSDS_POLY1: int = 79            # g1 = 117 octal = 1001111
CCSDS_POLY2: int = 109            # g2 = 155 octal = 1101101
NUM_STATES: int = 1 << (CONV_K - 1)   # 64 状态

# CADU 帧大小（SatDump module_meteor_lrpt_decoder.cpp:14）
CADU_SIZE: int = 1024             # 字节（4 ASM + 1020 数据）
RS_INTERLEAVE: int = 4            # 4 路交织
RS_BLOCK: int = 255               # RS(255,223)


# --------------------------------------------------------------------------- #
# 卷积编码（测试向量用；真实发射端在卫星上）
# --------------------------------------------------------------------------- #
def convolve_encode(info_bits: np.ndarray,
                    poly1: int = CCSDS_POLY1,
                    poly2: int = CCSDS_POLY2) -> np.ndarray:
    """CCSDS K=7 r=1/2 卷积编码（用于合成测试向量）。

    参数:
        info_bits: 输入信息位 (0/1)，形状 (N,)。

    返回:
        编码后位串 (0/1)，形状 (2N,)，顺序 c0,c1,c0,c1,...
        尾部追加 K-1=6 个 0 比特做尾比特归零（CCSDS 惯例）。
    """
    info_bits = np.asarray(info_bits, dtype=np.uint8).reshape(-1)
    # 尾比特：6 个 0（把 6 级移位寄存器清零）
    tail = np.zeros(CONV_K - 1, dtype=np.uint8)
    bits = np.concatenate([info_bits, tail])

    # 移位寄存器：[u0, u1, u2, u3, u4, u5]（u0=当前输入）
    # state = [u1, u2, u3, u4, u5, u6]（6 bit，对应 64 状态）
    shift = [0] * (CONV_K - 1)
    out = []
    for b in bits:
        u = int(b)
        # c0 = u·poly1[0] ⊕ u1·poly1[1] ⊕ ... ⊕ u6·poly1[6]
        # poly 含 u0（当前位）：shift[0]=u1, shift[1]=u2, ..., shift[5]=u6
        c0 = u
        c1 = u
        for i in range(CONV_K - 1):
            if (poly1 >> (i + 1)) & 1:
                c0 ^= shift[i]
            if (poly2 >> (i + 1)) & 1:
                c1 ^= shift[i]
        out.append(c0)
        out.append(c1)
        # 移位
        shift.pop()
        shift.insert(0, u)
    return np.array(out, dtype=np.uint8)


# --------------------------------------------------------------------------- #
# Viterbi K=7 r=1/2 软判决译码
# --------------------------------------------------------------------------- #
def _build_transitions(poly1: int = CCSDS_POLY1,
                       poly2: int = CCSDS_POLY2) -> Tuple[np.ndarray, np.ndarray]:
    """预计算 trellis 转移表。

    返回:
        next_state: (64, 2) — state s 输入 bit b 后的下一状态
        output:     (64, 2, 2) — state s 输入 bit b 的 (c0, c1)
    """
    nstates = NUM_STATES
    next_state = np.zeros((nstates, 2), dtype=np.int32)
    output = np.zeros((nstates, 2, 2), dtype=np.uint8)
    for s in range(nstates):
        # state s = [u1, u2, u3, u4, u5, u6]（bit0=u1, bit5=u6）
        for b in (0, 1):
            # c0 = b·poly1[0] ⊕ u1·poly1[1] ⊕ ... ⊕ u6·poly1[6]
            c0 = b
            c1 = b
            for i in range(CONV_K - 1):
                ui = (s >> i) & 1
                if (poly1 >> (i + 1)) & 1:
                    c0 ^= ui
                if (poly2 >> (i + 1)) & 1:
                    c1 ^= ui
            output[s, b, 0] = c0
            output[s, b, 1] = c1
            # next state: shift left, insert b at top
            ns = ((s << 1) | b) & (nstates - 1)
            next_state[s, b] = ns
    return next_state, output


_NEXT_STATE, _OUTPUT = _build_transitions()


def viterbi_decode(soft_bits: np.ndarray,
                   poly1: int = CCSDS_POLY1,
                   poly2: int = CCSDS_POLY2,
                   traceback_depth: int = 96) -> np.ndarray:
    """Viterbi K=7 r=1/2 软判决译码（ACS + 回溯）。

    参数:
        soft_bits: 软判决位串，形状 (2N,)，顺序 c0,c1,c0,c1,...。
                   正值 = 大概率 bit 1，负值 = 大概率 bit 0；
                   绝对值越大越确定。硬判决可传 ±1.0。
        traceback_depth: 回溯深度（默认 96 ≈ 15×K）。

    返回:
        译码后信息位 (0/1)，形状 (N,)（不含尾比特）。
    """
    soft = np.asarray(soft_bits, dtype=np.float64).reshape(-1)
    if soft.size % 2 != 0:
        raise ValueError("soft_bits 长度必须为偶数（r=1/2）")
    n_pairs = soft.size // 2
    nstates = NUM_STATES

    # 转移表（用默认多项式预计算）
    next_st = _NEXT_STATE
    out_tab = _OUTPUT

    # 初始化路径度量：全 ∞，state 0 = 0
    path_metric = np.full(nstates, -1e18, dtype=np.float64)
    path_metric[0] = 0.0

    # 幸存路径历史：history[t, s] = 从哪来的状态（用于回溯）
    history = np.zeros((n_pairs, nstates), dtype=np.int32)

    for t in range(n_pairs):
        s0 = soft[2 * t]       # c0 的软值
        s1 = soft[2 * t + 1]   # c1 的软值
        new_metric = np.full(nstates, -1e18, dtype=np.float64)

        for s in range(nstates):
            if path_metric[s] < -1e17:
                continue
            for b in (0, 1):
                ns = next_st[s, b]
                # 分支度量：期望 c0=out_tab[s,b,0], c1=out_tab[s,b,1]
                # reward = (2*c0-1)*s0 + (2*c1-1)*s1
                # （期望 c=1 时 reward=+s0；期望 c=0 时 reward=-s0）
                # 注意：out_tab 是 uint8，必须先转 int 避免 0-1 下溢成 255
                c0 = int(out_tab[s, b, 0])
                c1 = int(out_tab[s, b, 1])
                reward = (2 * c0 - 1) * s0 + (2 * c1 - 1) * s1
                total = path_metric[s] + reward
                if total > new_metric[ns]:
                    new_metric[ns] = total
                    history[t, ns] = s  # 记录前驱状态

        path_metric = new_metric

    # 回溯：从最终度量最大的状态开始
    # CCSDS 尾比特归零 → 结束时状态应回到 0
    # 但为鲁棒，取全局最大度量状态
    best_end = int(np.argmax(path_metric))

    # 回溯
    decoded = np.zeros(n_pairs, dtype=np.uint8)
    cur = best_end
    for t in range(n_pairs - 1, -1, -1):
        prev = int(history[t, cur])
        # 当前状态是 prev 输入某个 bit 得到的 → 反推输入 bit
        # next_state[prev, b] = cur → 找 b
        b = cur & 1  # next state LSB = 输入 bit（ns = (s<<1)|b）
        decoded[t] = b
        cur = prev

    # 去掉尾比特（K-1=6 个 0）
    return decoded[: -(CONV_K - 1)] if n_pairs > CONV_K - 1 else decoded


# --------------------------------------------------------------------------- #
# CCSDS CADU 解码链（Viterbi → 解扰 → RS×4 交织）
# --------------------------------------------------------------------------- #
class LrptCaduDecoder:
    """LRPT CADU 解码：Viterbi → CCSDS 解扰 → RS(255,223)×4 交织。

    机制（笔记 §2.2）：
      1. Viterbi K7 r1/2 软判决译码
      2. 跳过 4 字节 ASM，对 1020 字节做 CCSDS 解扰
      3. 4 路解交织 → 每路 RS(255,223) 解码
      4. 4 块全可纠才算有效帧（诚实锁帧判据）
    """

    def __init__(self) -> None:
        self.scrambler = Scrambler()
        self.rs = ReedSolomon(nsym=32, fcr=112, prim=1, ccsds_invert=True)

    def decode_viterbi_to_bytes(self, soft_coded_bits: np.ndarray) -> bytes:
        """Viterbi 译码 + 组帧字节（含 4 字节 ASM 在前）。

        参数:
            soft_coded_bits: 编码后软位串 (c0,c1,c0,c1,...)，形状 (2N,)。

        返回:
            Viterbi 译码后的字节串（MSB-first），长度 = N*8//2... 按位对齐。
        """
        info_bits = viterbi_decode(soft_coded_bits)
        # MSB-first 组字节
        nbytes = (info_bits.size + 7) // 8
        padded = np.zeros(nbytes * 8, dtype=np.uint8)
        padded[: info_bits.size] = info_bits
        return np.packbits(padded).tobytes()

    @staticmethod
    def _deinterleave_4(blob: bytes) -> List[bytes]:
        """4 路解交织：输入 1020 字节 → 4×255 字节。

        SatDump reedsolomon.cpp:145-155 机制：out[ii] = data[ii*4 + pos]。
        """
        if len(blob) != RS_INTERLEAVE * RS_BLOCK:
            raise ValueError(f"解交织需要 {RS_INTERLEAVE*RS_BLOCK} 字节，收到 {len(blob)}")
        blocks = [bytearray() for _ in range(RS_INTERLEAVE)]
        for i, b in enumerate(blob):
            blocks[i % RS_INTERLEAVE].append(b)
        return [bytes(b) for b in blocks]

    @staticmethod
    def _interleave_4(blocks: List[bytes]) -> bytes:
        """4 路交织：4×255 字节 → 1020 字节。"""
        out = bytearray()
        for pos in range(RS_BLOCK):
            for ii in range(RS_INTERLEAVE):
                out.append(blocks[ii][pos])
        return bytes(out)

    def decode_cadu(self, viterbi_bytes: bytes) -> Tuple[bytes, List[int]]:
        """对 Viterbi 译码后的完整 CADU 字节流做解扰 + RS 解码。

        输入格式：viterbi_bytes 应包含 4 字节 ASM + 1020 字节数据。
        本函数跳过前 4 字节 ASM，对 1020 字节解扰 + RS。

        返回:
            (decoded_data, rs_errors)
            decoded_data: 223×4 = 892 字节纠正后数据（全 4 块可纠时）
            rs_errors:    每块纠错字节数（-1 = 不可纠）
        """
        if len(viterbi_bytes) < CADU_SIZE:
            raise ValueError(f"CADU 需要 {CADU_SIZE} 字节，收到 {len(viterbi_bytes)}")

        # 跳过 4 字节 ASM
        data = viterbi_bytes[4:CADU_SIZE]   # 1020 字节
        if len(data) != 1020:
            raise ValueError(f"数据段应为 1020 字节，收到 {len(data)}")

        # CCSDS 解扰
        derandomized = self.scrambler.descramble(data)

        # 4 路解交织
        blocks = self._deinterleave_4(derandomized)

        # RS 解码每块
        results: List[RSResult] = []
        errors: List[int] = []
        for blk in blocks:
            r = self.rs.decode(blk)
            results.append(r)
            errors.append(r.nerrors)

        # 全 4 块可纠才算有效
        if all(e >= 0 for e in errors):
            decoded = b"".join(r.data for r in results)
            return decoded, errors
        else:
            return b"", errors
