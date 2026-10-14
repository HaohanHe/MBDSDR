# SPDX-License-Identifier: MIT
"""VCDU → 图像段重组：按 (VCID, APID, sequence) 有序拼接 JPEG 头+扫描段。

诚实边界 / 参数化声明：
  - 本模块不识CADU/RS/Viterbi，只吃“已解出的 VCDU 包”序列。
    上游 CCSDS 传输帧/VCDU/空间包头的位级细节（VCID 位宽、APID 语义、
    packet_sequence_count 14-bit 无符号计数、翻卷处理）在不同实现/文档口径
    下不完全一致——**此处全部参数化**，不在实现里写死猜测：
      ``vcid``/``apid``：SOF/SOS 之外的顶层过滤器（本仓 METEOR 口径
          LRPC/series 常用 APID 64..69 → 通道 0..5，见 lrpt-render.md）。
      ``sequence``：单调无符号计数，按 ``sequence_modulus`` 取模判定翻卷/空洞。
  - “重组”语义：按 sequence 排序去重后，把各包 payload 顺序拼接为一条字节流；
    header（SOI…SOF…SOS 头）与 scan（熵数据）连续一体，可整套交给
    ``lrpt_huffman.decode_image_blocks``。用 ``lrpt_huffman.locate_scan_start``
    判定 header 是否完整/定位熵数据起点。
  - 丢包/乱序**诚实跳过**：不伪造缺失字节；报告 gap 计数与缺失 sequence 号，
    以及“被排除的非目标 APID/VCID 包数”“重复 sequence 数”。

不抄 GPL 代码：机制参考 SatDump msumr reader 的 per-channel sequence/heartbeat
（lrpt_msumr_reader.cpp:58-93 的 packet_sequence_count / 43 包传输环），
此处只学“按 APID 分流 + 按 sequence 排序 + 诚实记 gap”的机制，代码自写。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .lrpt_huffman import LrptJpegError, locate_scan_start

__all__ = [
    "VcduPacket",
    "GapRange",
    "ImageReassembly",
    "reassemble_image_stream",
    "LrptJpegError",
]


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass
class VcduPacket:
    """一个已解出的 VCDU/图像包（不带头长度前缀，只有 payload 字节）。"""
    vcid: int
    apid: int
    sequence: int
    payload: bytes


@dataclass
class GapRange:
    """一段缺失的 sequence 区间 [start, end]（闭区间，取模计数）。"""
    start: int
    end: int

    @property
    def size(self) -> int:
        return (self.end - self.start) + 1


@dataclass
class ImageReassembly:
    """重组结果（诚实空态：失败时 stream=b""，fields 保持 0/空）。"""
    stream: bytes                              # header+scan 拼接（含空洞则不可信）
    complete: bool                             # 头完整 + 无洞 + 见到 EOI
    header_complete: bool                      # 能定位到 SOS 熵起点
    scan_start: Optional[int]                  # 熵数据在 stream 内的起点
    num_packets: int                           # 参与拼接的目标包数
    num_duplicates: int                        # 同 sequence 重复包数
    num_gaps: int                              # 空洞段数
    gap_ranges: List[GapRange] = field(default_factory=list)
    missing_sequences: List[int] = field(default_factory=list)
    num_filtered: int = 0                      # 被 APID/VCID 过滤掉的非目标包
    first_sequence: Optional[int] = None
    last_sequence: Optional[int] = None
    reason: str = ""


# --------------------------------------------------------------------------- #
# 入口：按 (VCID, APID, sequence) 重组
# --------------------------------------------------------------------------- #
def reassemble_image_stream(
    packets: List[VcduPacket],
    *,
    target_vcid: Optional[int] = None,
    target_apid: Optional[int] = None,
    sequence_modulus: int = 16384,
) -> ImageReassembly:
    """把若干 VCDU 包重组为一条 JPEG 字节流，诚实报告丢包/乱序/重复。

    参数均参数化（见模块 docstring）：
      target_vcid / target_apid：None 表示不按该维度过滤。
      sequence_modulus：sequence 计数取模（CCSDS 13/14-bit 口径不定，默认 16384）。

    返回 :class:`ImageReassembly`。``complete=False`` 时 ``stream`` 仍可能是
    局部拼接（不可信），调用方应据 gap 信息决定是否交给解码器。
    """
    res = ImageReassembly(stream=b"", complete=False, header_complete=False,
                          scan_start=None, num_packets=0, num_duplicates=0,
                          num_gaps=0)
    if sequence_modulus <= 1:
        raise ValueError(f"sequence_modulus 须 >1，收到 {sequence_modulus}")

    # 1) 顶层过滤：只保留目标 (vcid, apid) 的图像包
    wanted: List[VcduPacket] = []
    for p in packets:
        if target_vcid is not None and p.vcid != target_vcid:
            res.num_filtered += 1
            continue
        if target_apid is not None and p.apid != target_apid:
            res.num_filtered += 1
            continue
        wanted.append(p)

    # 2) 去重：同 (vcid, apid) 下同一 sequence 只保留首个到达者
    by_seq: Dict[int, VcduPacket] = {}
    for p in wanted:
        if not (0 <= p.sequence < sequence_modulus):
            raise ValueError(
                f"sequence={p.sequence} 越界（应在 [0,{sequence_modulus})）——"
                "疑似跨取模翻卷的片段，本轮单调窗口口径拒绝接受")
        if p.sequence in by_seq:
            res.num_duplicates += 1
            continue
        by_seq[p.sequence] = p

    if not by_seq:
        res.reason = "无目标图像包"
        return res

    seqs = sorted(by_seq)
    res.num_packets = len(seqs)
    res.first_sequence = seqs[0]
    res.last_sequence = seqs[-1]

    # 3) gap 检测（单调窗口口径）：sequence 视为捕获窗口内单调递增计数，
    #    相邻升序差 >1 即空洞。（窗口内跨 sequence_modulus 翻卷不在本轮支持
    #    范围——单幅图像重组窗口极短，通常远小于一个取模周期；若需翻卷感知
    #    需引入 SatDump reader 的 rollover 阈值外推，见模块 docstring。）
    missing: List[int] = []
    gaps: List[GapRange] = []
    for a, b in zip(seqs, seqs[1:]):
        step = b - a
        if step > 1:
            miss = list(range(a + 1, b))
            missing.extend(miss)
            gaps.append(GapRange(miss[0], miss[-1]))
    res.gap_ranges = gaps
    res.num_gaps = len(gaps)
    res.missing_sequences = missing

    # 4) 拼接（存在空洞时 stream 物理连续但语义不完整——如实标注）
    res.stream = b"".join(by_seq[s].payload for s in seqs)

    # 5) header 完整性 + EOI 侦测
    pref = locate_scan_start(res.stream)
    res.header_complete = pref.scan_start is not None
    res.scan_start = pref.scan_start
    saw_eoi = b"\xff\xd9" in res.stream
    res.complete = bool(res.header_complete and res.num_gaps == 0 and saw_eoi)
    if res.num_gaps > 0:
        res.reason = (f"存在 {len(gaps)} 个空洞，缺失 {len(missing)} 个 sequence"
                      f"（首缺 {missing[0]}）")
    elif not res.header_complete:
        res.reason = f"header 不完整: {pref.reason}"
    elif not saw_eoi:
        res.reason = "未在流尾检出 EOI(0xFFD9)（扫描可能被截断）"
    else:
        res.reason = "ok"
    return res

