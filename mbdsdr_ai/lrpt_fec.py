# SPDX-License-Identifier: MIT
"""LRPT 卷积内码 + CCSDS 解扰 + RS 外码（独立实现，第②步）。

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
# 【系数字约定深挖（2026-10-14，SatDump clone 逐行核对）】
# 本模块 {79, 109} 与 meteor_sat.py:635 的 G1=171/G2=133（八进制）是
# **同一物理码的两种系数书写约定**：79 = 0o117 = 0b1001111，其 7bit 逆序
# = 0b1111001 = 121 = 0o171；109 = 0o155 的逆序 = 0b1011011 = 91 = 0o133。
# 即 LSB-first 系数表（本模块 / SatDump viterbi27.h:8 的 {79,109}）与
# MSB-first NASA 八进制约定（0o171/0o133）互为比特逆序。
#
# 【真录裁决（2026-11-03）】之前悬置的「哪一端 u0 先进寄存器」已由
# Meteor-M2 真实过境录（tests/test_lrpt_meteor_real_golden.py）裁决：
# **真实下行的卷积位→I/Q 映射与本仓合成链相反**——解真录必须对软位流做
# pair-swap（c0/c1 互换 ≡ poly1↔poly2）才能过 Viterbi 出 ASM 0x1ACFFC1D；
# 合成链（convolve_encode → bits_to_symbols）自洽、不需要 swap。
# 含义：合成链与真录各用各的约定，测试里已钉死 pair-swap 路径；若未来
# 统一约定（改合成链跟真录对齐），同步字 LRPT_SYNC_WORD 与全部合成
# fixture 须随代码同一提交重生成（血规：代码与金样同走）。
# 另：真软判决（符号幅度）是 RS 纠错边际关键，硬 ±1 下同样帧 RS 不可纠。
CCSDS_POLY1: int = 79            # g1 = 117 octal = 1001111（0o171/121 的逆序）
CCSDS_POLY2: int = 109            # g2 = 155 octal = 1101101（0o133/91 的逆序）
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

# 向量化 ACS 预计算：分支度量符号表 + 前驱对索引。
# ns = ((s<<1)|b) & 63 → ns=2k+b 的前驱恰是 s=k 与 s=k+32（同 b）。
_BRANCH_SIGN0 = (2.0 * _OUTPUT[:, :, 0].astype(np.float64) - 1.0)  # (64,2)
_BRANCH_SIGN1 = (2.0 * _OUTPUT[:, :, 1].astype(np.float64) - 1.0)  # (64,2)
_K_IDX = np.arange(32, dtype=np.int32)                             # k = 0..31


def viterbi_decode(soft_bits: np.ndarray,
                   poly1: int = CCSDS_POLY1,
                   poly2: int = CCSDS_POLY2,
                   return_reliability: bool = False):
    """Viterbi K=7 r=1/2 软判决译码（向量化 ACS + 全帧回溯）。

    参数:
        soft_bits: 软判决位串，形状 (2N,)，顺序 c0,c1,c0,c1,...。
                    正值 = 大概率 bit 1，负值 = 大概率 bit 0；
                    绝对值越大越确定。硬判决可传 ±1.0。

    语义（2026-10-14 与 SatDump 逐行核对后收敛至此）：
    前向 ACS 跑满全部符号对（含尾部 6 个归零尾比特的影响），从终态
    最大度量状态做**全帧回溯**——与 SatDump/GNU Radio cc_decoder 的
    chainback_viterbi（src-core/common/codings/viterbi/cc_decoder.cpp:228-276，
    final_state=-1 由度量自选终态）同构；差异仅在 SatDump 前向窗口少算
    K-1 个符号（viterbi27.cpp:40 的 cc_decoder_in.work），对带尾比特的
    CCSDS 流可忽略。译码输出剥除末尾 K-1=6 个尾比特。

    实现（2026-11-03 向量化）：ACS 内层 64 状态 × 2 分支的 Python 双层
    循环改为 numpy 前驱对比较——ns=2k+b 的两个前驱 s=k/k+32 一次
    np.where 完成取最大 + tie-break（并列取小 s，与旧实现「严格大于才
    替换、s 升序遍历」的幸存者一致，逐位输出不变）。单帧 1.4s → 0.3s 量级，
    连帧扫描几百帧才有预算。

    返回:
        译码后信息位 (0/1)，形状 (N,)（不含尾比特）。

        ``return_reliability=True`` 时返回 ``(bits, rel)``：``rel[i]`` 是第 i
        个译码比特的 MLM 可靠性（该步幸存支路与竞争支路的路径度量差）。
        差值越小越可疑——这是擦除辅助 RS 的擦除位置来源（LRPT 队列 #9）。
        采集靠回溯时读同一张度量表，**不改变译码结果**（已有测试钉死）。
    """
    soft = np.asarray(soft_bits, dtype=np.float64).reshape(-1)
    if soft.size % 2 != 0:
        raise ValueError("soft_bits 长度必须为偶数（r=1/2）")
    n_pairs = soft.size // 2
    nstates = NUM_STATES

    # 初始化路径度量：全 ∞，state 0 = 0
    path_metric = np.full(nstates, -1e18, dtype=np.float64)
    path_metric[0] = 0.0

    # 幸存路径历史：history[t, s] = 从哪来的状态（用于回溯）
    history = np.zeros((n_pairs, nstates), dtype=np.int32)

    # 每步每状态的「幸存支路 − 竞争支路」度量差（只在要可靠性时分配）。
    # 内存：1020 字节 CADU ≈ 4120 对 × 64 × 4B ≈ 1 MB，可接受。
    mem = (np.zeros((n_pairs, nstates), dtype=np.float32)
           if return_reliability else None)

    # 预取符号表列（b=0 / b=1），内循环不再触表
    sgn0_b0 = _BRANCH_SIGN0[:, 0]
    sgn1_b0 = _BRANCH_SIGN1[:, 0]
    sgn0_b1 = _BRANCH_SIGN0[:, 1]
    sgn1_b1 = _BRANCH_SIGN1[:, 1]

    for t in range(n_pairs):
        s0 = soft[2 * t]       # c0 的软值
        s1 = soft[2 * t + 1]   # c1 的软值
        # 128 分支度量一次算出（b=0/b=1 两个 (64,) 向量）
        t0 = path_metric + sgn0_b0 * s0 + sgn1_b0 * s1
        t1 = path_metric + sgn0_b1 * s0 + sgn1_b1 * s1
        lo0, hi0 = t0[:32], t0[32:]
        lo1, hi1 = t1[:32], t1[32:]
        if mem is not None:
            # 到状态 2k+b 的两支分别来自 s=k 与 s=k+32：|差| 即该步该状态的
            # MLM 度量差（0 = 两支完全等价，译码器在这里没有任何把握）
            mem[t, 0::2] = np.abs(lo0 - hi0)
            mem[t, 1::2] = np.abs(lo1 - hi1)
        take0 = lo0 >= hi0    # 并列取小 s（=旧实现先到先得）
        take1 = lo1 >= hi1
        new_metric = np.empty(nstates, dtype=np.float64)
        new_metric[0::2] = np.where(take0, lo0, hi0)
        new_metric[1::2] = np.where(take1, lo1, hi1)
        hist_row = np.empty(nstates, dtype=np.int32)
        hist_row[0::2] = np.where(take0, _K_IDX, _K_IDX + 32)
        hist_row[1::2] = np.where(take1, _K_IDX, _K_IDX + 32)
        # 旧实现对「两前驱都不可达」的 ns 保持 -1e18 / history=0（从不更新）
        both_bad = (path_metric[:32] < -1e17) & (path_metric[32:] < -1e17)
        if both_bad.any():
            new_metric[0::2] = np.where(both_bad, -1e18, new_metric[0::2])
            new_metric[1::2] = np.where(both_bad, -1e18, new_metric[1::2])
            hist_row[0::2] = np.where(both_bad, 0, hist_row[0::2])
            hist_row[1::2] = np.where(both_bad, 0, hist_row[1::2])
        history[t] = hist_row
        path_metric = new_metric

    # 回溯：从最终度量最大的状态开始
    # CCSDS 尾比特归零 → 结束时状态应回到 0
    # 但为鲁棒，取全局最大度量状态
    best_end = int(np.argmax(path_metric))

    # 回溯
    decoded = np.zeros(n_pairs, dtype=np.uint8)
    rel = np.zeros(n_pairs, dtype=np.float64) if mem is not None else None
    cur = best_end
    for t in range(n_pairs - 1, -1, -1):
        prev = int(history[t, cur])
        # 当前状态是 prev 输入某个 bit 得到的 → 反推输入 bit
        # next_state[prev, b] = cur → 找 b
        b = cur & 1  # next state LSB = 输入 bit（ns = (s<<1)|b）
        decoded[t] = b
        if rel is not None:
            rel[t] = mem[t, cur]      # 路径经过该状态的 MLM 度量差
        cur = prev

    # 去掉尾比特（K-1=6 个 0）
    n_out = (n_pairs - (CONV_K - 1)) if n_pairs > CONV_K - 1 else n_pairs
    if return_reliability:
        return decoded[:n_out], rel[:n_out]
    return decoded[:n_out]


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

    def decode_viterbi_to_bytes(self, soft_coded_bits: np.ndarray,
                                return_reliability: bool = False):
        """Viterbi 译码 + 组帧字节（含 4 字节 ASM 在前）。

        参数:
            soft_coded_bits: 编码后软位串 (c0,c1,c0,c1,...)，形状 (2N,)。
            return_reliability: 同时返回**每字节可靠性**（该字节 8 个比特的
                MLM 度量差最小值）——一位崩了整字节就不可信，所以取 min。
                这是擦除位置的直接来源（见 :meth:`decode_cadu_erasure`）。

        返回:
            默认返回字节串；``return_reliability=True`` 时返回
            ``(bytes, byte_rel)``，``byte_rel`` 为 float64 数组，长度与字节串
            相同，值越小越可疑。
        """
        if return_reliability:
            info_bits, rel = viterbi_decode(soft_coded_bits,
                                            return_reliability=True)
        else:
            info_bits = viterbi_decode(soft_coded_bits)
        # MSB-first 组字节
        nbytes = (info_bits.size + 7) // 8
        padded = np.zeros(nbytes * 8, dtype=np.uint8)
        padded[: info_bits.size] = info_bits
        raw = np.packbits(padded).tobytes()
        if not return_reliability:
            return raw
        rel_padded = np.zeros(nbytes * 8, dtype=np.float64)
        rel_padded[: rel.size] = rel
        byte_rel = rel_padded.reshape(-1, 8).min(axis=1)
        return raw, byte_rel

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

    def decode_cadu(self, viterbi_bytes: bytes,
                    order: str = "concat") -> Tuple[bytes, List[int]]:
        """对 Viterbi 译码后的完整 CADU 字节流做解扰 + RS 解码。

        输入格式：viterbi_bytes 应包含 4 字节 ASM + 1020 字节数据。
        本函数跳过前 4 字节 ASM，对 1020 字节解扰 + RS。

        参数:
            order: 892B 数据区重组口径（**比特约定分叉，2026-11-03 裁决**）：
              - ``"woven"`` —— 真实 Meteor-M2 / CCSDS 131.0 标准口径：
                TX 按**往返**把数据位分入 4 个码字（data[j] → 块 j%4），
                交织区前 892 字节与数据同序；RX 就近回织
                （payload[j] = 块[j%4].data[j//4]）。TU Berlin 同录音解码器
                reedsolomon.m:48-51（corrected(j:I:end)=decoded）实证，
                真录 VCDU→M_PDU→空间包按此序才解析得出（探针 0 包 vs N 包）。
              - ``"concat"`` —— 本仓合成链口径（块=连续 223B 切分，
                RX 顺序拼接），lrpt_pipeline.build_cadu 自洽；非 CCSDS
                标准，仅合成环使用。统一约定时（改合成链跟真录对齐）
                此参数随之退役。

        返回:
            (decoded_data, rs_errors)
            decoded_data: 223×4 = 892 字节纠正后数据（全 4 块可纠时）
            rs_errors:    每块纠错字节数（-1 = 不可纠）
        """
        if order not in ("concat", "woven"):
            raise ValueError(f"order 须 'concat' 或 'woven'，收到 {order!r}")
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
            if order == "woven":
                # 回织（CCSDS 往返切分的逆）：payload[j] = 块[j%4].data[j//4]
                datas = [r.data for r in results]
                decoded = bytes(datas[j % 4][j // 4] for j in range(892))
            else:
                decoded = b"".join(r.data for r in results)
            return decoded, errors
        else:
            return b"", errors

    # ------------------------------------------------------------------ #
    # 擦除辅助解码（LRPT 队列 #9）
    # ------------------------------------------------------------------ #
    #: 擦除尝试阶梯（与 :attr:`fec.ReedSolomon.ERASURE_LADDER` 同一份口径）。
    ERASURE_LADDER: Tuple[int, ...] = (4, 8, 12, 16, 20, 24, 28, 32)

    def _rs_decode_block(self, blk: bytes, byte_rel, blk_idx: int,
                         ladder: Tuple[int, ...]):
        """单块解码：把「Viterbi 字节可靠性」映射成本块的符号可靠性，
        交给 :meth:`fec.ReedSolomon.decode_with_reliability` 决定擦除。

        映射：4 路交织下，块 ``blk_idx`` 的第 j 字节 = 交织区第 ``4j+blk_idx``
        字节 = ``viterbi_bytes`` 的第 ``4 + 4j + blk_idx`` 字节（+4 跳过 ASM）。

        返回 (RSResult, 用掉的擦除数)。
        """
        if byte_rel is None:
            return self.rs.decode(blk), 0
        rel = np.asarray(byte_rel, dtype=np.float64)
        if rel.size == 0:
            return self.rs.decode(blk), 0
        idx = np.arange(RS_BLOCK, dtype=np.int64) * RS_INTERLEAVE + blk_idx + 4
        rel_blk = rel[np.minimum(idx, rel.size - 1)]
        return self.rs.decode_with_reliability(blk, rel_blk, ladder)

    def decode_cadu_erasure(self, viterbi_bytes: bytes, byte_rel,
                            order: str = "concat",
                            ladder: Optional[Tuple[int, ...]] = None
                            ) -> Tuple[bytes, List[int], List[int]]:
        """擦除辅助 CADU 解码（2e+f ≤ nsym）。

        参数:
            viterbi_bytes: 含 4 字节 ASM 的 CADU 字节串。
            byte_rel: 每字节可靠性（:meth:`decode_viterbi_to_bytes` 的输出，
                长度 ≥ 1024，值越小越可疑）。``None`` = 退化为盲解，行为与
                :meth:`decode_cadu` 一致。
            order: 同 :meth:`decode_cadu`（``"concat"`` / ``"woven"``）。
            ladder: 覆盖 :attr:`ERASURE_LADDER`（测试/调参用）。

        返回:
            (decoded_data, rs_errors, erasures_used)
            **诚实边界不变**：仍是「4 块全可纠才算有效帧」，否则 payload
            为空。擦除只是提高单块可纠概率，不放松整帧判据。
        """
        if order not in ("concat", "woven"):
            raise ValueError(f"order 须 'concat' 或 'woven'，收到 {order!r}")
        if len(viterbi_bytes) < CADU_SIZE:
            raise ValueError(f"CADU 需要 {CADU_SIZE} 字节，收到 {len(viterbi_bytes)}")
        lad = tuple(ladder) if ladder is not None else self.ERASURE_LADDER

        derandomized = self.scrambler.descramble(viterbi_bytes[4:CADU_SIZE])
        blocks = self._deinterleave_4(derandomized)

        results: List[RSResult] = []
        errors: List[int] = []
        used: List[int] = []
        for i, blk in enumerate(blocks):
            r, k = self._rs_decode_block(blk, byte_rel, i, lad)
            results.append(r)
            errors.append(r.nerrors)
            used.append(k)

        if not all(e >= 0 for e in errors):
            return b"", errors, used
        if order == "woven":
            datas = [r.data for r in results]
            return (bytes(datas[j % 4][j // 4] for j in range(892)),
                    errors, used)
        return b"".join(r.data for r in results), errors, used
