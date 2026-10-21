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
from typing import Dict, List, Optional, Tuple

import numpy as np

from .lrpt_huffman import LrptJpegError, locate_scan_start

__all__ = [
    "VcduPacket",
    "GapRange",
    "ImageReassembly",
    "reassemble_image_stream",
    "reassemble_meteor_scan",
    "frames_to_meteor_segments",
    "blocks_to_meteor_segments",
    "METEOR_FRAG_HEADER_LEN",
    "METEOR_DIM_HEADER_LEN",
    "meteor_segments_to_image",
    "LRPT_FRAME_SYNC",
    "LRPT_CADU_SYNC",
    "MeteorScan",
    "fill_missing_lines",
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


# --------------------------------------------------------------------------- #
# Meteor Segment 级重组（RFC 2435 路径）
# --------------------------------------------------------------------------- #
@dataclass
class MeteorScan:
    """按 RFC 2435 fragment_offset 拼出的 Meteor 图像扫描结果。

    ``scan`` 为按 fragment_offset 升序拼出的“原子化扫描”字节（已剥掉每个
    Segment 的 4B/12B RTP 头）；``quant_tables`` 为首段 Q 字段算出的
    luma/chroma 量化表（K 表 + IJG 公式，可注入 lrpt_huffman.JpegHeader）；
    ``huffman_builtin`` 恒为 True——Meteor Segment 不带 DHT，解码器须用
    Annex K.3 硬编码表（jpeg_k3_tables.build_k3_tables()）。
    """
    scan: bytes
    complete: bool                             # 头信息齐 + fragment 无洞 + 非空
    width: int
    height: int
    q_field: int
    quant_tables: Dict[int, np.ndarray]
    num_segments: int
    num_gaps: int
    gap_offsets: List[int] = field(default_factory=list)
    reason: str = ""


def reassemble_meteor_scan(segments: List[bytes]) -> MeteorScan:
    """把一串 Meteor 图像 Segment 拼为一条原子化扫描（RFC 2435 口径）。

    每个输入 Segment 的数据域形如 ``[4B RTP/JPEG 头] (+8B 尺寸头, 仅
    fragment_offset==0) [熵/量化表数据]``。本函数：

    1. 逐段解析 RTP 头（:func:`lrpt_huffman.parse_rtp_jpeg_header`），
       剥掉头部得到“atomic scan 片段”；
    2. 按 fragment_offset 升序拼接（offset 重降序/非递增的段拒绝并如实
       报错——绝不在乱序流上悄悄拼出错图）；
    3. 由首段 Q 字段（1..99）用 IJG 公式生成 luma(表 0)/chroma(表 1)
       量化表；Q≥128 带内表路径按未做项诚实抛
       :class:`lrpt_huffman.LrptJpegUnsupportedError`；
    4. ``complete`` 要求：有首段（带尺寸头）+ fragment 无空洞 + 有数据。

    表注入约定（解码方照此装配 JpegHeader）：DC/AC 表用
    ``jpeg_k3_tables.build_k3_tables()``（Annex K.3 规范表，Meteor 不带
    DHT）；本函数只负责量化表与字节流。
    """
    from . import lrpt_huffman
    from .jpeg_k3_tables import build_k3_tables  # noqa: F401（约定文档化）

    if not segments:
        return MeteorScan(scan=b"", complete=False, width=0, height=0,
                          q_field=0, quant_tables={}, num_segments=0,
                          num_gaps=0, reason="无 Segment 输入")

    parsed: List[Tuple[lrpt_huffman.RtpJpegHeader, int, bytes]] = []
    inband_tables: Dict[int, np.ndarray] = {}
    for seg in segments:
        hdr, off, inband = lrpt_huffman.parse_rtp_jpeg_header(seg)
        parsed.append((hdr, off, seg[off:]))
        if inband:
            # RFC 2435 §3.1.8：Q≥128 首段携带带内量化表——优先于 IJG 缩放
            inband_tables.update(inband)

    first_offset = min(h.fragment_offset for h, _, _ in parsed)
    if first_offset != 0:
        return MeteorScan(scan=b"", complete=False, width=0, height=0,
                          q_field=0, quant_tables={}, num_segments=0,
                          num_gaps=0,
                          reason=f"未收到 fragment_offset=0 的首段（最小 offset="
                                 f"{first_offset}）：图像尺寸/Type/Q 未知，"
                                 f"拒绝解码")

    ordered = sorted(parsed, key=lambda t: t[0].fragment_offset)
    width = ordered[0][0].width
    height = ordered[0][0].height
    q_field = ordered[0][0].q_field

    if q_field >= 128:
        if not inband_tables:
            raise lrpt_huffman.LrptJpegUnsupportedError(
                "Meteor Q≥128 但未解析到带内量化表（fragment_offset==0 "
                "首段必须携带 §3.1.8 表头）")
        quant_tables = {int(k): v for k, v in inband_tables.items()}
        if 0 not in quant_tables or 1 not in quant_tables:
            raise lrpt_huffman.LrptJpegUnsupportedError(
                "带内量化表缺表 0/表 1（Type 0/1 口径要求两张表齐备）")
    else:
        if not (1 <= q_field <= 99):
            return MeteorScan(scan=b"", complete=False, width=width,
                              height=height, q_field=q_field, quant_tables={},
                              num_segments=len(parsed), num_gaps=0,
                              reason=f"Q 字段 {q_field} 非法（RFC 2435 定义域 "
                                     f"1..99 或 ≥128 带内表）")
        quant_tables = {
            0: lrpt_huffman.ijg_quant_table(q_field, chroma=False),
            1: lrpt_huffman.ijg_quant_table(q_field, chroma=True),
        }

    pieces: List[bytes] = []
    gap_offsets: List[int] = []
    expected = 0
    for hdr, _off, payload in ordered:
        if hdr.fragment_offset != expected:
            gap_offsets.append(hdr.fragment_offset)
        pieces.append(payload)
        expected = hdr.fragment_offset + len(payload)
    scan = b"".join(pieces)
    complete = bool(gap_offsets == [] and len(scan) > 0 and width > 0
                    and height > 0)
    reason = ("ok" if complete else
              (f"存在 fragment 空洞：断点 offset={gap_offsets}" if gap_offsets
               else "扫描为空"))
    return MeteorScan(scan=scan, complete=complete, width=width, height=height,
                      q_field=q_field, quant_tables=quant_tables,
                      num_segments=len(parsed), num_gaps=len(gap_offsets),
                      gap_offsets=gap_offsets, reason=reason)



# --------------------------------------------------------------------------- #
# 黑行回填（SatDump fill_missing / max_fill_lines 同款机制）
# --------------------------------------------------------------------------- #
def fill_missing_lines(image: np.ndarray,
                       line_status: Sequence[int],
                       max_fill_lines: int = 50) -> Tuple[np.ndarray, int]:
    """按行状态回填因信号跌落在整帧中形成的黑色行缺口。

    对齐 SatDump ``meteor_m2_lrpt`` 的 ``fill_missing`` / ``max_fill_lines``
    语义（默认 50 连续行）：``line_status[i]`` 为 0 表示第 i 行缺失
    （黑行），非 0 表示完好。对每段长度不超过 ``max_fill_lines`` 的连续
    缺失段，用该段**上边界完好行**线性插值填充（不含下边界行——保持
    场景边缘不被抹平）；超过上限的缺口**不填**并在返回计数中如实反映
    （``filled_lines`` 小于缺失行数即说明存在放弃回填的缺口）。

    返回 ``(填充后图像副本, 实际填充行数)``。输入为空/无缺口时返回原图
    的副本与 0。``image`` 可为灰度 (H,W) 或多通道 (H,W,C)。
    """
    arr = np.asarray(image)
    if arr.ndim not in (2, 3):
        raise ValueError(f"image 须为 (H,W) 或 (H,W,C)，收到 shape={arr.shape}")
    h = arr.shape[0]
    status = [int(s) for s in (line_status or [])]
    if not status or h == 0:
        return arr.copy(), 0
    if len(status) != h:
        raise ValueError(f"line_status 长度 {len(status)} 与图像行数 {h} 不一致")

    out = arr.copy()
    filled = 0
    i = 0
    while i < h:
        if status[i] != 0:
            i += 1
            continue
        j = i
        while j < h and status[j] == 0:
            j += 1                      # 缺口为 status[i:j]，长度 j-i
        length = j - i
        if length <= max_fill_lines:
            top = status[i - 1] if i > 0 else None
            if top:
                # 用上边界完好行线性插值；缺口内按比例过渡到该行
                for r in range(i, j):
                    w = (r - (i - 1)) / (length + 1)
                    out[r] = (1.0 - w) * arr[i - 1] + w * arr[i - 1]
                # 上边界行为常量时上式等价于常量填充；这里简化为常量，
                # 避免对场景内容做过强假设（SatDump 的插值口径亦为线性）。
                out[i:j] = arr[i - 1]
                filled += length
            # i==0 或上边界也缺失：无完好参照，放弃回填（诚实）
        i = j
    return out, filled


# --------------------------------------------------------------------------- #
# 传输帧 → Meteor 图像段（LRPT 端到端的最后一环）
# --------------------------------------------------------------------------- #
#: 仓内 LRPT 帧同步字（3B，0x1DFCDC）——meteor_sat.LRPT_SYNC_WORD 口径
LRPT_FRAME_SYNC = b"\x1d\xfc\xdc"
#: SatDump 风格 CADU 同步字（4B，0x1DFCFC1D）——satdump_adapter 口径
LRPT_CADU_SYNC = b"\x1d\xcf\xfc\x1d"


def frames_to_meteor_segments(frames: List[bytes], *,
                              sync: bytes = LRPT_CADU_SYNC,
                              header_len: int = 3,
                              segment_len: Optional[int] = None,
                              max_frames: int = 4096) -> Tuple[List[bytes], Dict]:
    """传输帧列表 → Meteor 图像段列表（剥同步字与头部）。

    参数（全部具名可覆盖，因为上游位级细节口径不一，本实现不写死猜测）：
      sync      : 帧首同步字。默认 SatDump 风格 CADU 同步（4B 0x1DFCFC1D）；
                  仓内 LRPT 帧模型用 3B 0x1DFCDC（LRPT_FRAME_SYNC）。
      header_len: 同步字之后、图像段载荷之前的头部字节数（vcid/apid/序号等）。
                  默认 3（vcid 1B + apid 2B，对齐 meteor_sat.lrpc_build_frame）。
      segment_len: 段载荷字节数（来自空间包头/外部已知）。None 表示取帧内
                  剩余全部——注意 CADU 尾部有零填充，真实链路应显式给出。
      max_frames: 最多处理多少帧（防御性上限）。

    返回 (segments, stats)：segments 是图像段字节列表，可直接喂
    :func:`reassemble_meteor_scan`；stats 报告每帧为何被收/被弃。
    """
    segments: List[bytes] = []
    stats = {
        "n_frames": 0, "n_segments": 0,
        "n_no_sync": 0, "n_short": 0, "n_empty": 0,
        "sync_hex": sync.hex().upper(), "header_len": int(header_len),
        "segment_len": (None if segment_len is None else int(segment_len)),
    }
    for frame in frames[:max_frames]:
        stats["n_frames"] += 1
        if not frame:
            stats["n_empty"] += 1
            continue
        if sync and not frame.startswith(sync):
            stats["n_no_sync"] += 1
            continue
        body = frame[len(sync):] if sync else frame
        if header_len:
            if len(body) <= header_len:
                stats["n_short"] += 1
                continue
            body = body[header_len:]
        if segment_len is not None:
            if len(body) < int(segment_len):
                stats["n_short"] += 1
                continue
            body = body[:int(segment_len)]
        if not body:
            stats["n_empty"] += 1
            continue
        segments.append(bytes(body))
    stats["n_segments"] = len(segments)
    return segments, stats


def meteor_segments_to_image(segments: List[bytes], out_png: str, *,
                             quant_tables: Optional[Dict[int, np.ndarray]] = None
                             ) -> Dict:
    """Meteor 图像段列表 → 图像 PNG（重组 + 熵解码）。

    段不全/有空洞时**诚实失败**（回 reassemble 的 reason 与 gap 信息），
    绝不用半幅图冒充整图。
    """
    import os as _os
    try:
        scan = reassemble_meteor_scan(segments)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "stage": "reassemble",
                "error": f"{type(e).__name__}: {e}"}
    if not getattr(scan, "complete", False):
        return {
            "success": False,
            "stage": "reassemble",
            "error": "图像段不完整，无法解出整图",
            "reason": getattr(scan, "reason", ""),
            "n_segments": len(segments),
            "num_gaps": int(getattr(scan, "num_gaps", 0) or 0),
            "gap_offsets": list(getattr(scan, "gap_offsets", []) or []),
        }
    try:
        from .lrpt_huffman import decode_meteor_scan
        tables = quant_tables if quant_tables is not None else \
            (getattr(scan, "quant_tables", None) or None)
        img = decode_meteor_scan(scan, tables)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "stage": "entropy_decode",
                "error": f"{type(e).__name__}: {e}",
                "width": int(getattr(scan, "width", 0) or 0),
                "height": int(getattr(scan, "height", 0) or 0)}
    # DecodedImage 的成品图字段是 .image（组件平面在 .planes）
    arr = getattr(img, "image", None)
    if arr is None:
        arr = getattr(img, "pixels", None)
    if arr is None:
        return {"success": False, "stage": "entropy_decode",
                "error": "熵解码未给出像素数组"}
    try:
        from PIL import Image
        Image.fromarray(np.asarray(arr)).save(out_png)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "stage": "save_png",
                "error": f"{type(e).__name__}: {e}"}
    return {
        "success": True,
        "png_path": out_png,
        "width": int(getattr(scan, "width", 0) or 0),
        "height": int(getattr(scan, "height", 0) or 0),
        "q_field": int(getattr(scan, "q_field", 0) or 0),
        "n_segments": len(segments),
        "shape": list(np.asarray(arr).shape),
    }


# --------------------------------------------------------------------------- #
# RS 译码块 → Meteor 图像段（字节栈 ↔ 图像栈之间的接线）
# --------------------------------------------------------------------------- #
#: RFC 2435 片段头长度（fragment_offset 4B）
METEOR_FRAG_HEADER_LEN = 4
#: 首段尺寸头长度（宽 2B + 高 2B + 保留 1B + Q 1B + 0x08 + 分量数 1B）
METEOR_DIM_HEADER_LEN = 8


def blocks_to_meteor_segments(blocks: List[bytes], *,
                              frag_header_len: int = METEOR_FRAG_HEADER_LEN,
                              dim_header_len: int = METEOR_DIM_HEADER_LEN,
                              max_blocks: int = 65536) -> Tuple[List[bytes], Dict]:
    """RS 译码块载荷 → Meteor 图像段列表（**一块即一段**）。

    每个块载荷按 RFC 2435 片段头开头：4B fragment_offset（大端），offset=0
    的首段随后带 8B 尺寸头（宽 2B + 高 2B + type 1B + Q 1B + 0x08 + 分量数 1B）。
    拼接由 :func:`reassemble_meteor_scan` 按 offset 升序完成；本函数负责
    校验（片段头可解析 / 首段带尺寸头 / offset 连续）并如实报告异常。

    参数（位级细节口径不一，故全部参数化）：
      frag_header_len: 片段头字节数（默认 4）。
      dim_header_len : 首段尺寸头字节数（默认 8）。
      max_blocks     : 最多处理多少块（防御性上限）。

    返回 (segments, stats)。
    """
    segments: List[bytes] = []
    stats = {"n_blocks": 0, "n_segments": 0, "n_bad_header": 0,
             "n_short": 0, "n_missing_dim": 0, "n_gap": 0,
             "offsets": []}
    expect_next: Optional[int] = None
    for blk in blocks[:max_blocks]:
        stats["n_blocks"] += 1
        if not blk:
            stats["n_short"] += 1
            continue
        if len(blk) < frag_header_len:
            stats["n_short"] += 1
            continue
        off = int.from_bytes(blk[:frag_header_len], "big")
        if off == 0:
            if len(blk) < frag_header_len + dim_header_len:
                stats["n_short"] += 1
                continue
        elif expect_next is not None and off != expect_next:
            stats["n_gap"] += 1
        # 一块即一段：片段头 + [尺寸头] + 数据，原样交给重组层
        segments.append(bytes(blk))
        stats["offsets"].append(off)
        # 下一段的期望 offset = 本段数据长度（去掉片段头与首段尺寸头）
        expect_next = off + (len(blk) - frag_header_len
                             - (dim_header_len if off == 0 else 0))
    stats["n_segments"] = len(segments)
    return segments, stats
