"""
MBDSDR AI 内核 - 风云系列气象卫星接收管道
================================================================

覆盖两个下行星系：

第一部分  FY-4A/4B LRIT/HRIT（静止轨道）
------------------------------------------
  * FY-4A 定点 104.7°E，FY-4B 定点 133.0°E（题目给定）。
  * 物理层（来源: SatDump resources/pipelines/FengYun-4.json）：
      - fengyun4_a_lrit : 1697.0 MHz, 90,000 sym/s, DVB-S2 QPSK modcod=3, RRC α=0.25
      - fengyun4_b_lrit : 1697.0 MHz, 120,000 sym/s, DVB-S2 QPSK modcod=10, RRC α=0.25
      - fengyun4_a_hrit23: 1679.0 MHz, 1,000,000 sym/s, DVB-S2 QPSK modcod=9
    注意：真实 FY-4 物理层走 DVB-S2（BBFrame→TS PID 3000/3002/3004→xRIT），
    见 FengYun-4.json work.cadu = s2_udp_cadu_extractor。本模块按题目要求
    走经典 CCSDS 链路（VCDU→TP_PDU→SessionPDU），与 GOES LRIT/HRIT 共用
    传输层（mbdsdr_ai/goes_lrit.py），仅在文件头处替换为 FY-4 专用结构。
  * FY-4 专用文件头（来源: SatDump plugins/xrit_support/xrit/fy4/fy4_headers.h）：
      - ImageInformationRecord (type=1)：卫星名/仪器名/位深/列行/段号/压缩信息
      - ImageNavigationRecord   (type=2)：投影名 + 4 个 float 缩放/偏移
      - KeyHeader               (type=7)
    与 GOES 的 ImageStructureRecord(type=1) 字段布局完全不同，必须单独解析。
  * 段重组（来源: SatDump xrit/fy4/segment_decoder.h:32-84）：
      - init: image = Image(bpp, width=columns, height=lines*total_seg)
      - pushSegment(img, current_segment_pos-1, current_segment_line_pos)：
        把该段像素拷贝到整图的 seg_width*line_pos 像素偏移处。
  * 图像压缩（来源: SatDump xrit/fy4/decomp.cpp:26-72）：
    FY-4 AGRI 实际用 JPEG2000（找 J2K 码流 0xFF4F 起始偏移后 openjp2 解压），
    而非 GOES 的 Rice/szlib。本模块合成测试使用未压缩直通路径，
    真实链路需接 OpenJPEG。

第二部分  FY-3D/E/F HRPT（极轨）
----------------------------------
  * 836 km 太阳同步极轨。题目给定 HRPT 帧格式：
      60-bit 同步字 0x0A116FD719D83C95，6 个 10-bit 同步字，
      每小帧 11090 个 10-bit 字，665.4 kbps BPSK。
    （来源: SatDump plugins/noaa_metop_support/noaa/noaa_deframer.cpp:6-17，
      见 mbdsdr_ai/satdump_adapter.py 的 HRPTDecoder 骨架。）
  * 注：真实 FY-3D/E/F 的 Advanced HRPT 在 SatDump 中是 X 波段 QPSK 高速下行
    （FengYun-3.json: FY-3D 7820MHz/30Msym，FY-3E/F 7860MHz/38.4Msym，Viterbi）。
    本模块按题目/既有骨架实现经典 665.4kbps BPSK HRPT 小帧格式与 AVHRR 式通道提取。
  * 极轨跟踪：复用 mbdsdr_ai/orbit.py 的 SGP4 传播 + 站心 ENU 坐标变换，
    提供 TLE 驱动的天线指向（方位/仰角）与多普勒补偿接口。

红线：GPL-3.0；常量/算法注释标注「来源: SatDump <file>:<line>」或
「来源: goestools <file>:<line>」；无 key/token；中文注释风格。
"""

from __future__ import annotations

import os
import struct
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from .goes_lrit import (  # noqa: E402
    VCDU,
    VCDU_LEN,
    TP_PDU_HEADER_BYTES,
    FILL_APID,
    MPDU_NO_PACKET,
    crc16_ccitt,
    parse_lrit_headers,          # 复用 CCSDS PrimaryHeader 解析
    PRIMARY_HEADER_LEN,
    LRITParser,
)
from .satdump_adapter import (    # 复用 NOAA HRPT 帧同步骨架
    HRPTDecoder,
    HRPT_MINOR_FRAME_WORDS,
    HRPT_SYNC_WORD_COUNT,
    HRPT_BITS_PER_WORD,
    HRPT_SYNC_WORDS,
    HRPT_MINOR_FRAME_SYNC,
    HRPT_SYMBOL_RATE,
)

logger = logging.getLogger(__name__)

# ============================================================================
# 卫星位置与下行频率元信息
# 来源: 题目给定 + SatDump resources/pipelines/FengYun-4.json / FengYun-3.json
# ============================================================================

#: FY-4A 定点经度（题目给定）
FY4A_SUB_LONGITUDE_DEG = 104.7
#: FY-4B 定点经度（题目给定）
FY4B_SUB_LONGITUDE_DEG = 133.0

#: FY-4 LRIT 下行频率 Hz（FengYun-4.json fengyun4_a_lrit/fengyun4_b_lrit: 1697e6）
FY4_LRIT_FREQ_HZ = 1697e6
#: FY-4 HRIT 下行频率 Hz（FengYun-4.json fengyun4_a_hrit23: 1679e6）
FY4_HRIT_FREQ_HZ = 1679e6

#: FY-4A LRIT 符号率 sym/s（FengYun-4.json: symbolrate=90e3, rrc_alpha=0.25）
FY4A_LRIT_SYMBOL_RATE = 90_000
#: FY-4B LRIT 符号率 sym/s（FengYun-4.json: symbolrate=120e3, rrc_alpha=0.25）
FY4B_LRIT_SYMBOL_RATE = 120_000
#: FY-4A HRIT 符号率 sym/s（FengYun-4.json: symbolrate=1e6, rrc_alpha=0.25）
FY4A_HRIT_SYMBOL_RATE = 1_000_000
#: DVB-S2 成形滚降（FengYun-4.json: rrc_alpha=0.25）
FY4_RRC_ALPHA = 0.25

#: FY-3 HRPT 符号率 bps（题目给定 / noaa_deframer.cpp + satdump_adapter.py）
FY3_HRPT_SYMBOL_RATE = HRPT_SYMBOL_RATE  # 665_400 bps
#: FY-3 HRPT 标称下行频率 Hz（题目给定 S 波段 ~1.7 GHz；真实 AHRPT 在 X 波段见文件头注释）
FY3_HRPT_FREQ_HZ = 1_700_000_000.0


# ============================================================================
# FY-4 xRIT 专用文件头解析
# 来源: SatDump plugins/xrit_support/xrit/fy4/fy4_headers.h
# ============================================================================

#: FY-4 专用记录类型码（fy4_headers.h）
FY4_H_IMAGE_INFORMATION = 1   # fy4_headers.h:37  ImageInformationRecord::TYPE
FY4_H_IMAGE_NAVIGATION = 2   # fy4_headers.h:85  ImageNavigationRecord::TYPE
FY4_H_KEY = 7                 # fy4_headers.h:21  KeyHeader::TYPE


@dataclass
class FY4ImageInfo:
    """FY-4 ImageInformationRecord 解包结果。

    位/字节布局（大端），来源: fy4_headers.h:60-80：
        data[0]       type (=1)
        data[1..2]    record_length (BE u16，含 type+length 三字节)
        data[3..11]   satellite_name (9 ASCII)
        data[12..18]  instrument_name (7 ASCII)
        data[19]      bit_per_pixel
        data[20..21]  columns_count (BE u16)
        data[22..23]  lines_count (BE u16)
        data[24]      compression_flag
        data[25]      channel_number
        data[26]      total_segment_count
        data[27]      current_segment_number
        data[28]      current_segment_pos
        data[29..30]  current_segment_line_pos (BE u16)
        data[31]      compressed_info: bit7=algo, bit6=lossless, bit0-4=level
    """

    satellite_name: str = ""
    instrument_name: str = ""
    bit_per_pixel: int = 0
    columns: int = 0
    lines: int = 0
    compression_flag: int = 0
    channel_number: int = 0
    total_segments: int = 0
    current_segment_number: int = 0
    current_segment_pos: int = 0
    current_segment_line_pos: int = 0
    comp_algo: int = 0
    comp_lossless: int = 0
    comp_level: int = 0


def parse_fy4_image_information(data: bytes) -> FY4ImageInfo:
    """解析 FY-4 ImageInformationRecord。来源: fy4_headers.h:60-80。

    data 指向该记录起点（含 type+length）。
    """
    if len(data) < 32 or data[0] != FY4_H_IMAGE_INFORMATION:
        raise ValueError("不是 FY-4 ImageInformationRecord (type=1)")
    out = FY4ImageInfo()
    out.satellite_name = bytes(data[3:12]).rstrip(b"\x00").decode("ascii", "replace")
    out.instrument_name = bytes(data[12:19]).rstrip(b"\x00").decode("ascii", "replace")
    out.bit_per_pixel = data[19]
    out.columns = (data[20] << 8) | data[21]
    out.lines = (data[22] << 8) | data[23]
    out.compression_flag = data[24]
    out.channel_number = data[25]
    out.total_segments = data[26]
    out.current_segment_number = data[27]
    out.current_segment_pos = data[28]
    out.current_segment_line_pos = (data[29] << 8) | data[30]
    out.comp_algo = data[31] >> 7
    out.comp_lossless = (data[31] >> 6) & 1
    out.comp_level = data[31] & 0b111111
    return out


def build_fy4_image_information(
    satellite_name: str,
    instrument_name: str,
    bit_per_pixel: int,
    columns: int,
    lines: int,
    compression_flag: int,
    channel_number: int,
    total_segments: int,
    current_segment_number: int,
    current_segment_pos: int,
    current_segment_line_pos: int,
    comp_algo: int = 0,
    comp_lossless: int = 0,
    comp_level: int = 0,
) -> bytes:
    """构造 FY-4 ImageInformationRecord（与 parse_fy4_image_information 互逆）。"""
    b = bytearray(32)
    b[0] = FY4_H_IMAGE_INFORMATION
    b[1] = (32 >> 8) & 0xFF
    b[2] = 32 & 0xFF
    sn = satellite_name.encode("ascii").ljust(9, b"\x00")[:9]
    ins = instrument_name.encode("ascii").ljust(7, b"\x00")[:7]
    b[3:12] = sn
    b[12:19] = ins
    b[19] = bit_per_pixel
    b[20] = (columns >> 8) & 0xFF
    b[21] = columns & 0xFF
    b[22] = (lines >> 8) & 0xFF
    b[23] = lines & 0xFF
    b[24] = compression_flag
    b[25] = channel_number
    b[26] = total_segments
    b[27] = current_segment_number
    b[28] = current_segment_pos
    b[29] = (current_segment_line_pos >> 8) & 0xFF
    b[30] = current_segment_line_pos & 0xFF
    b[31] = ((comp_algo & 1) << 7) | ((comp_lossless & 1) << 6) | (comp_level & 0b111111)
    return bytes(b)


# ============================================================================
# FY-4 xRIT 文件组帧器（编码侧）
#   PrimaryHeader(16B) + FY-4 ImageInformationRecord(32B) + 图像数据
# ============================================================================

def build_fy4_xrit_file(
    image_info: bytes,
    image_data: bytes,
    file_type: int = 0,
    annotation: str = "",
) -> bytes:
    """组装一个完整的 FY-4 xRIT 段文件。

    布局：PrimaryHeader(16) + ImageInformationRecord(32) + [AnnotationRecord] + 数据。
    PrimaryHeader 字段布局与 CCSDS 标准一致（goes_lrit.parse_lrit_headers 可解析）：
      byte0=0, byte3=fileType, bytes4-7=totalHeaderLength(BE u32),
      bytes8-15=dataLengthBits(BE u64)。
    """
    body = bytearray()
    # 可选 AnnotationRecord (type=4)，紧跟 ImageInformationRecord 之后
    ann_record = b""
    if annotation:
        ann = annotation.encode("ascii")
        ann_len = 3 + len(ann)
        ann_record = bytes([4, (ann_len >> 8) & 0xFF, ann_len & 0xFF]) + ann

    total_header = PRIMARY_HEADER_LEN + len(image_info) + len(ann_record)
    data_bits = len(image_data) * 8

    ph = bytearray(PRIMARY_HEADER_LEN)
    ph[0] = 0  # type = PrimaryHeader
    ph[3] = file_type
    ph[4:8] = struct.pack(">I", total_header)
    ph[8:16] = struct.pack(">Q", data_bits)

    return bytes(ph) + image_info + ann_record + image_data


# ============================================================================
# CCSDS 传输层编码器（与 goes_lrit._VirtualChannel / _SessionPDU 互逆）
#   file_bytes → TP_PDU（CCSDS 源包 + CRC16）→ M_PDU → 892B VCDU
# ============================================================================

def _build_tpdu(file_bytes: bytes, apid: int, seq_flag: int, seq_count: int,
                first: bool) -> bytes:
    """组一个 CCSDS TP_PDU。

    与 goes_lrit.TransportPDU.parse_header / verify_crc 互逆：
      - 6B 主头：version(3)=0|type(1)=0|secHdr(1)=0|apid(11)；seqFlag(2)|seqCount(14)；
        packetDataLength(16 BE) = 后续字节数 - 1。
      - payload：首包前补 10 字节「垃圾」（goestools session_pdu.cc:78-82），
        再跟文件数据；末尾 2 字节 CRC-16/CCITT（覆盖 payload 不含 CRC）。
    """
    payload = (b"\x00" * 10 if first else b"") + file_bytes
    crc = crc16_ccitt(payload)
    body = payload + crc.to_bytes(2, "big")   # body = CCSDS 头之后的全部字节
    pkt_len = len(body) - 1                     # packetDataLength = body 字节数 - 1
    h0 = (0 << 5) | ((apid >> 8) & 0x07)        # version=0,type=0,secHdr=0
    h1 = apid & 0xFF
    h2 = (seq_flag << 6) | ((seq_count >> 8) & 0x3F)
    h3 = seq_count & 0xFF
    h4 = (pkt_len >> 8) & 0xFF
    h5 = pkt_len & 0xFF
    return bytes([h0, h1, h2, h3, h4, h5]) + body


def file_to_vcdus(file_bytes: bytes, scid: int = 0x20, vcid: int = 1,
                  apid: int = 0x10, chunk: int = 1000) -> List[bytes]:
    """把一个完整 xRIT 文件封装成一串 892B VCDU（编码侧，与 LRITParser.feed_vcdu 互逆）。

    封装规则严格对齐 goes_lrit 解码：
      - VCDU 892B：6B 头(version/scid/vcid/counter/reserved) + 886B M_PDU；
      - M_PDU 前 2B = first_header_pointer（高 3 位在 byte6，低 8 位在 byte7）；
      - M_PDU 数据区 884B；TP_PDU 可跨 VCDU 切分（解码器 in_progress 续接）。
    """
    # 1) 把文件切成若干 TP_PDU
    tpdus: List[bytes] = []
    n = len(file_bytes)
    offset = 0
    seq = 0
    while offset < n:
        seg = file_bytes[offset:offset + chunk]
        first = (offset == 0)
        last = (offset + chunk >= n)
        if first and last:
            seq_flag = 3   # 整包在一个 TP_PDU
        elif first:
            seq_flag = 1   # 第一段
        elif last:
            seq_flag = 2   # 末段
        else:
            seq_flag = 0   # 续段
        tpdus.append(_build_tpdu(seg, apid, seq_flag, seq, first))
        seq += 1
        offset += chunk

    # 2) 把 TP_PDU 字节流按 M_PDU 884B 装填，正确计算 first_header_pointer
    MPDU_DATA = 884  # 892 - 6(VCDU头) - 2(M_PDU头)
    vcdus: List[bytes] = []
    ti = 0                 # 当前正在装填的 TP_PDU 序号
    off = 0                # 该 TP_PDU 已取字节数
    counter = 0
    while ti < len(tpdus):
        mpdu = bytearray(MPDU_DATA)
        fill = 0
        # 起始处是否在跨 VCDU 的 TP_PDU 中途？是 → 这些字节是续接，fhp 指向其后
        if off > 0:
            blob = tpdus[ti]
            take = min(len(blob) - off, MPDU_DATA)
            mpdu[0:take] = blob[off:off + take]
            fill = take
            off += take
            if off >= len(blob):
                ti += 1
                off = 0
            fhp = fill       # 续接字节之后才是新 TP_PDU 起点
        else:
            fhp = 0
        # 装填新 TP_PDU
        while fill < MPDU_DATA and ti < len(tpdus):
            blob = tpdus[ti]
            chunkb = blob[off:]
            take = min(len(chunkb), MPDU_DATA - fill)
            mpdu[fill:fill + take] = chunkb[:take]
            fill += take
            off += take
            if off >= len(blob):
                ti += 1
                off = 0
        # 组 VCDU 头
        vcdu = bytearray(VCDU_LEN)
        vcdu[0] = (0 << 6) | ((scid >> 2) & 0x3F)
        vcdu[1] = ((scid & 0x03) << 6) | (vcid & 0x3F)
        vcdu[2] = (counter >> 16) & 0xFF
        vcdu[3] = (counter >> 8) & 0xFF
        vcdu[4] = counter & 0xFF
        vcdu[5] = 0
        vcdu[6] = (fhp >> 8) & 0x07
        vcdu[7] = fhp & 0xFF
        vcdu[8:8 + MPDU_DATA] = mpdu
        vcdus.append(bytes(vcdu))
        counter += 1
    return vcdus


# ============================================================================
# FY-4 段重组（解码侧）
# 来源: SatDump xrit/fy4/segment_decoder.h:32-108
# ============================================================================

@dataclass
class FY4Image:
    """重组完成的 FY-4 AGRI 灰度图。"""
    width: int
    height: int
    channels: int
    pixels: np.ndarray            # uint8 / uint16, shape=(height, width)
    channel: int = 0
    satellite: str = ""


class FY4SegmentAssembler:
    """把多个 FY-4 xRIT 段文件按 ImageInformationRecord 拼成整图。

    布局规则（来源: segment_decoder.h:32-84）：
      - init: 整图宽 = columns_count，高 = lines_count * total_segments；
      - pushSegment: 把该段 data 按 seg_width*current_segment_line_pos 像素偏移
        整段 memcpy 到整图（imemcpy 按像素线性拷贝）。
    """

    def __init__(self) -> None:
        #: channel_number -> 待收段
        self._pending: Dict[int, Dict[int, Tuple[FY4ImageInfo, bytes]]] = {}

    def add_segment_file(self, file_buf: bytes) -> Optional[FY4Image]:
        """喂入一个完整 FY-4 xRIT 段文件；凑齐则返回整图。"""
        h = parse_lrit_headers(file_buf)
        if h.file_type != 0:
            return None
        # 找 FY-4 ImageInformationRecord (type=1) 的位置
        pos = PRIMARY_HEADER_LEN
        fy4_info: Optional[FY4ImageInfo] = None
        while pos + 3 <= h.total_header_length and pos < len(file_buf):
            rtype = file_buf[pos]
            rlen = (file_buf[pos + 1] << 8) | file_buf[pos + 2]
            if rlen == 0:
                break
            if rtype == FY4_H_IMAGE_INFORMATION:
                fy4_info = parse_fy4_image_information(file_buf[pos:pos + rlen])
                break
            pos += rlen
        if fy4_info is None:
            return None

        data = bytes(file_buf[h.total_header_length:])
        bpp = fy4_info.bit_per_pixel
        w = fy4_info.columns
        rows_per_seg = fy4_info.lines
        total_seg = fy4_info.total_segments

        pool = self._pending.setdefault(fy4_info.channel_number, {})
        pool[fy4_info.current_segment_pos] = (fy4_info, data)
        if len(pool) < total_seg:
            return None

        # 凑齐 → 拼接（来源: segment_decoder.h:37,68）
        dtype = np.uint16 if bpp > 8 else np.uint8
        height = rows_per_seg * total_seg
        canvas = np.zeros((height, w), dtype=dtype)
        for seg_pos, (info, sdata) in pool.items():
            y0 = info.current_segment_line_pos
            # 该段 = rows_per_seg 行 × w 列
            if bpp == 8:
                seg = np.frombuffer(sdata[: rows_per_seg * w], dtype=np.uint8)
                seg = seg.reshape(rows_per_seg, w)
            elif bpp == 10 or bpp == 16:
                raw = np.frombuffer(sdata[: rows_per_seg * w * 2], dtype=np.uint8)
                # 大端 16-bit
                val = (raw[0::2].astype(np.uint16) << 8) | raw[1::2].astype(np.uint16)
                seg = val.reshape(rows_per_seg, w)
            else:
                continue
            end = min(y0 + rows_per_seg, height)
            canvas[y0:end, :] = seg[: end - y0, :]
        self._pending.pop(fy4_info.channel_number, None)

        if bpp > 8:
            # 高位右移到 8bit 便于 PNG 显示
            canvas8 = (canvas >> 8).astype(np.uint8)
        else:
            canvas8 = canvas
        return FY4Image(
            width=w, height=height, channels=1, pixels=canvas8,
            channel=fy4_info.channel_number, satellite=fy4_info.satellite_name,
        )


# ============================================================================
# [经典链路-保留，用于测试回退] 极简 BPSK 基带调制 / 解调（合成往返测试用）
#   真实 FY-4 物理层是 DVB-S2（见本文件末尾「DVB-S2 真实物理层」段）；
#   此处仅为验证「编码→信道→解码→出图」闭环，保留作测试回退路径。
# ============================================================================

def bits_to_bpsk(bits: np.ndarray, seed: int = 42) -> np.ndarray:
    """NRZ BPSK：bit1→+1，bit0→-1，叠加轻微 AWGN 验证解调鲁棒性。"""
    rng = np.random.default_rng(seed)
    sym = np.where(bits > 0, 1.0, -1.0).astype(np.float32)
    sym += rng.normal(0.0, 0.05, size=sym.shape).astype(np.float32)  # 低噪
    return sym


def bpsk_to_bits(sym: np.ndarray) -> np.ndarray:
    """硬判决 BPSK 解调：>0 → 1。"""
    return (sym > 0).astype(np.uint8)


def bytes_to_bits(data: bytes) -> np.ndarray:
    """字节流 → MSB-first 比特数组。"""
    arr = np.frombuffer(data, dtype=np.uint8)
    bits = np.unpackbits(arr)  # MSB-first
    return bits.astype(np.uint8)


def bits_to_bytes(bits: np.ndarray) -> bytes:
    """MSB-first 比特数组 → 字节流（丢弃末尾不足 8 位的余数）。"""
    n = (len(bits) // 8) * 8
    return np.packbits(bits[:n].astype(np.uint8)).tobytes()


# ============================================================================
# [经典链路-保留，用于测试回退] FY-4 端到端解码入口（工具层）
#   走 CCSDS VCDU 经典链路；真实 FY-4 走 DVB-S2（见文件末尾 DVB-S2 段）。
# ============================================================================

def decode_fy4_vcdus(vcdus: List[bytes]) -> List[FY4Image]:
    """喂入一串 892B VCDU，重组出所有完整 FY-4 段图。

    复用 goes_lrit.LRITParser 做 VCDU→TP_PDU→SessionPDU 文件重组，
    再用 FY4SegmentAssembler 拼段。
    """
    parser = LRITParser()
    files = parser.parse_stream(vcdus)
    asm = FY4SegmentAssembler()
    out: List[FY4Image] = []
    for f in files:
        img = asm.add_segment_file(f)
        if img is not None:
            out.append(img)
    return out


def fy4_lrit_pipeline(segments: List[bytes]) -> List[FY4Image]:
    """给定若干完整 FY-4 xRIT 段文件字节，直接重组出图（跳过传输层）。"""
    asm = FY4SegmentAssembler()
    out: List[FY4Image] = []
    for s in segments:
        img = asm.add_segment_file(s)
        if img is not None:
            out.append(img)
    return out


# ============================================================================
# [经典链路-保留，用于测试回退] FY-3 S 波段 HRPT：小帧编码 / AVHRR 式通道提取
# 来源: SatDump noaa_deframer.cpp:6-17（见 satdump_adapter.HRPTDecoder）
#   真实 FY-3 X 波段 Advanced HRPT (AHRPT) 走 CCSDS CADU 链路，
#   见本文件末尾「FY-3 X 波段 AHRPT」段。
# ============================================================================

#: AVHRR 通道数据在小帧中的起始字偏移（简化：同步 6 字 + 1 字帧计数之后）
#: 真实 NOAA AVHRR 通道布局见 avhrr_reader.cpp；此处用连续字块做合成往返。
FY3_AVHRR_WORD_OFFSET = 10


def build_fy3_hrpt_frame(row_pixels: np.ndarray, frame_counter: int,
                          img_width: int) -> np.ndarray:
    """构造一帧 FY-3 HRPT 小帧（11090 个 10-bit 字），返回比特数组（MSB-first）。

    布局：
      words[0:6]   = HRPT_SYNC_WORDS（同步字）
      words[6]     = 10-bit 帧计数器
      words[10:10+img_width] = 该扫描行 AVHRR 像素（8bit 拉伸到 10bit：px<<2）
      其余补 0。
    比特顺序：每个 10-bit 字 MSB-first（与 HRPTDecoder 的 (word<<1)|bit 对齐）。
    """
    words = np.zeros(HRPT_MINOR_FRAME_WORDS, dtype=np.uint16)
    words[0:6] = HRPT_SYNC_WORDS
    words[6] = frame_counter & 0x3FF
    w = min(img_width, len(row_pixels))
    pix10 = (row_pixels[:w].astype(np.uint16) << 2) & 0x3FF
    words[FY3_AVHRR_WORD_OFFSET:FY3_AVHRR_WORD_OFFSET + w] = pix10
    # 10-bit 字 MSB-first 展开
    bits = np.zeros(HRPT_MINOR_FRAME_WORDS * HRPT_BITS_PER_WORD, dtype=np.uint8)
    for bit in range(HRPT_BITS_PER_WORD):
        # 第 bit 位（MSB=bit9 先出）
        bits[bit::HRPT_BITS_PER_WORD] = (words >> (HRPT_BITS_PER_WORD - 1 - bit)) & 1
    return bits


def extract_fy3_avhrr_row(frame_words: List[int], img_width: int) -> np.ndarray:
    """从一帧 HRPT 10-bit 字中提取 AVHRR 扫描行（10bit→8bit 右移 2）。"""
    start = FY3_AVHRR_WORD_OFFSET
    row = np.array(frame_words[start:start + img_width], dtype=np.uint16) >> 2
    return row.astype(np.uint8)


def decode_fy3_hrpt_bits(soft_bits: np.ndarray, img_width: int,
                         max_lines: int = 1000) -> np.ndarray:
    """BPSK 软比特流 → HRPT 帧同步 → AVHRR 图像重建。

    返回 uint8 图像数组 (n_lines, img_width)。
    """
    dec = HRPTDecoder(sync_threshold=4)
    frames = dec.work(np.asarray(soft_bits))
    rows = []
    for fr in frames[:max_lines]:
        if len(fr.words) < FY3_AVHRR_WORD_OFFSET + img_width:
            continue
        rows.append(extract_fy3_avhrr_row(fr.words, img_width))
    if not rows:
        return np.zeros((0, img_width), dtype=np.uint8)
    return np.vstack(rows)


# ============================================================================
# FY-3 极轨跟踪接口（SGP4 传播 + 站心 ENU + 多普勒）
# 来源: mbdsdr_ai/orbit.py（_state_from_satrec / C_LIGHT）
# ============================================================================

#: FY-3D NORAD CATNR（orbit.py BUILTIN_SATS: "FENGYUN 3D"=54234）
FY3D_CATNR = 54234
#: 836 km 太阳同步轨道标称高度（题目给定）
FY3_ORBIT_ALT_KM = 836.0

#: 内置合成 TLE（离线测试用，836km 近极轨太阳同步）。
#: 真实 TLE 可由 orbit.fetch_tle(FY3D_CATNR) 在线拉取；此处给出 SGP4 可传播的合成根数。
FY3_SYNTH_TLE = (
    "1 43010U 17072A   24001.50000000  .00000100  00000-0  30000-4 0  9995",
    "2 43010  98.7000 100.0000 0010000 100.0000 260.0000 14.10000000000009",
)


def fy3_track(tle: Tuple[str, str], observer_lat: float, observer_lon: float,
              observer_alt: float, t_unix: float,
              nominal_freq_hz: float = FY3_HRPT_FREQ_HZ) -> Dict[str, float]:
    """TLE 驱动的单时刻天线指向 + 多普勒补偿（函数级接口，无真实硬件）。

    来源: orbit._state_from_satrec（TEME→ECEF→站心 ENU→仰角/方位/视线速度）。
    返回 dict：azimuth/elevation/range/range_rate/doppler_shift_hz/altitude。
    """
    from sgp4.api import Satrec
    from .orbit import _state_from_satrec, C_LIGHT
    sat = Satrec.twoline2rv(tle[0], tle[1])
    jd_utc = t_unix / 86400.0 + 2440587.5
    st = _state_from_satrec(sat, "FENGYUN-3", jd_utc,
                            observer_lat, observer_lon, observer_alt,
                            epoch=tle[0][18:32].strip())
    if st is None:
        return {"error": "SGP4 传播失败"}
    shift = -nominal_freq_hz * st["range_rate_kms"] / C_LIGHT
    return {
        "elevation_deg": st["elevation"],
        "azimuth_deg": st["azimuth"],
        "range_km": st["range_km"],
        "range_rate_kms": st["range_rate_kms"],
        "altitude_km": st["altitude_km"],
        "doppler_shift_hz": shift,
        "corrected_freq_hz": nominal_freq_hz + shift,
    }


def fy3_pass_curve(tle: Tuple[str, str], observer_lat: float, observer_lon: float,
                   observer_alt: float, t_start: float, duration_s: float,
                   step_s: float = 5.0,
                   nominal_freq_hz: float = FY3_HRPT_FREQ_HZ) -> List[Dict[str, float]]:
    """输出一次过境内逐时刻的仰角/方位/多普勒曲线（用于天线指向与多普勒补偿演示）。"""
    out: List[Dict[str, float]] = []
    t = t_start
    while t <= t_start + duration_s:
        st = fy3_track(tle, observer_lat, observer_lon, observer_alt, t,
                       nominal_freq_hz)
        if "error" not in st:
            st["t_unix"] = t
            out.append(st)
        t += step_s
    return out


# ============================================================================
# PNG 输出工具
# ============================================================================

def save_png(image: np.ndarray, path: str) -> str:
    """把 uint8 二维灰度数组写为 PNG（PIL），返回路径。"""
    from PIL import Image
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.fromarray(np.ascontiguousarray(image), mode="L").save(path)
    return path


# ============================================================================
# 工具注册表接入
# ============================================================================

def register_tool_registry(registry) -> None:
    """把风云卫星接收工具注册到 MBDSDR ToolRegistry。"""
    import json as _json
    from .tool_registry import ToolResult

    def _fy4_handler(args):
        vcdus = [bytes.fromhex(x) for x in args.get("vcdus", [])]
        imgs = decode_fy4_vcdus(vcdus)
        data = [{"width": i.width, "height": i.height, "channel": i.channel,
                 "satellite": i.satellite, "pixels": int(i.pixels.size)}
                for i in imgs]
        return ToolResult(success=True,
                          content=_json.dumps({"images": data}, ensure_ascii=False),
                          data={"images": data})

    def _fy3_handler(args):
        # args: soft_bits (float list) or frames
        bits = np.array(args.get("soft_bits", []), dtype=np.float32)
        w = int(args.get("img_width", 64))
        img = decode_fy3_hrpt_bits(bits, w)
        return ToolResult(success=True,
                          content=_json.dumps({"rows": int(img.shape[0]),
                                               "cols": int(img.shape[1])},
                                              ensure_ascii=False),
                          data={"shape": list(img.shape)})

    def _pass_handler(args):
        # 地面站坐标：优先用参数，否则从配置读取；都没有则报错
        cfg_lat = cfg_lon = None
        cfg_path = os.path.expanduser("~/.mbdsdr/config.json")
        try:
            if os.path.exists(cfg_path):
                import json as _cfg_json
                with open(cfg_path, "r", encoding="utf-8") as f:
                    _cfg = _cfg_json.load(f)
                cfg_lat = _cfg.get("ground_station_lat")
                cfg_lon = _cfg.get("ground_station_lon")
        except Exception:
            pass
        lat = args.get("observer_lat", cfg_lat)
        lon = args.get("observer_lon", cfg_lon)
        if lat is None or lon is None:
            return ToolResult(
                success=False,
                content="未配置地面站坐标。请在参数中传入 observer_lat/observer_lon，"
                        "或在 ~/.mbdsdr/config.json 中设置 ground_station_lat / ground_station_lon。",
            )
        lat = float(lat)
        lon = float(lon)
        t0 = float(args.get("t_start", __import__("time").time()))
        dur = float(args.get("duration_s", 600))
        curve = fy3_pass_curve(FY3_SYNTH_TLE, lat, lon, 0.0, t0, dur)
        return ToolResult(success=True,
                          content=_json.dumps({"points": len(curve),
                                               "max_el": max(
                                                   (c["elevation_deg"] for c in curve),
                                                   default=0.0)},
                                              ensure_ascii=False),
                          data={"curve": curve})

    registry.register(
        name="fy4_lrit_decode",
        description=(
            "FY-4A/4B LRIT/HRIT 解码出图（CCSDS VCDU→TP_PDU→SessionPDU→FY-4专用头→段重组）："
            "喂入 892B VCDU 的 hex 列表，重组并拼接 AGRI 段，返回宽/高/通道数。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "vcdus": {"type": "array", "items": {"type": "string"},
                          "description": "892 字节 VCDU 的 hex 字符串列表"},
            },
            "required": ["vcdus"],
        },
        handler=_fy4_handler,
        category="satellite",
    )

    registry.register(
        name="fy3_hrpt_decode",
        description=(
            "FY-3 HRPT 665.4kbps BPSK 解码出图：喂入软比特(>0为1)数组，做 60-bit 帧同步、"
            "10-bit 字组帧、AVHRR 通道提取，返回重建图像行列数。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "soft_bits": {"type": "array", "items": {"type": "number"}},
                "img_width": {"type": "integer"},
            },
            "required": ["soft_bits"],
        },
        handler=_fy3_handler,
        category="satellite",
    )

    registry.register(
        name="fy3_pass_predict",
        description=(
            "FY-3 极轨过境预测（SGP4 + 站心 ENU）：输出过境窗口内逐时刻仰角/方位/多普勒曲线。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "observer_lat": {"type": "number"},
                "observer_lon": {"type": "number"},
                "duration_s": {"type": "number"},
            },
        },
        handler=_pass_handler,
        category="satellite",
    )


# ============================================================================
# [DVB-S2真实物理层，移植自SatDump] FY-4 DVB-S2 物理层同步 / 解扰
# ----------------------------------------------------------------------------
# 移植来源（GPL-3.0）：
#   - SOF / PLS 定义      : SatDump plugins/dvb_support/dvbs2/s2_defs.h:15-88
#   - 帧长常量            : SatDump src-core/common/codings/dvb-s2/dvbs2.h:5-6
#   - MODCOD→码率映射     : SatDump plugins/dvb_support/codings/dvb-s2/modcod_to_cfg.h:27-55
#   - BB 帧解扰 PRBS      : SatDump plugins/dvb_support/codings/dvb-s2/bbframe_descramble.cpp:121-142
#   - FY-4 用 QPSK DVB-S2 : SatDump resources/pipelines/FengYun-4.json
# 说明：题目给的「18-bit SOF 0x18D5E8」是对 DVB-S2 PL 起始字段的俗称；
#       SatDump/EN302307 真实 SOF 为 26-bit 0x18D2E82（s2_defs.h:17），
#       PLHEADER = SOF(26) + PLS code(64) = 90 bits。本模块按 SatDump 真值实现。
# ============================================================================

#: DVB-S2 SOF 26-bit 起始字段值（来源: s2_defs.h:17  VALUE = 0x18d2e82）
DVBS2_SOF_VALUE = 0x18D2E82
#: SOF 比特长度（来源: s2_defs.h:19  LENGTH = 26）
DVBS2_SOF_LEN = 26
#: SOF 掩码（来源: s2_defs.h:18  MASK = 0x3ffffff）
DVBS2_SOF_MASK = 0x3FFFFFF
#: PLS code 比特长度（来源: s2_defs.h:39  LENGTH = 64）
DVBS2_PLS_LEN = 64
#: PLHEADER 总长度 = SOF(26) + PLS(64)
DVBS2_PLHEADER_LEN = DVBS2_SOF_LEN + DVBS2_PLS_LEN
#: 普通帧 FECFRAME 数据比特数（来源: dvbs2.h:5  FRAME_SIZE_NORMAL 64800）
DVBS2_FRAME_NORMAL_BITS = 64800
#: 短帧 FECFRAME 数据比特数（来源: dvbs2.h:6  FRAME_SIZE_SHORT 16200）
DVBS2_FRAME_SHORT_BITS = 16200
#: PLS code 加扰掩码（来源: s2_defs.h:87  SCRAMBLING = 0x719d83c953422dfa）
DVBS2_PLS_SCRAMBLING = 0x719D83C953422DFA

#: QPSK MODCOD 表（来源: modcod_to_cfg.h:33-54）：modcod编号 -> 码率字符串
DVBS2_QPSK_MODCOD_TABLE = {
    1: "1/4", 2: "1/3", 3: "2/5", 4: "1/2", 5: "3/5",
    6: "2/3", 7: "3/4", 8: "4/5", 9: "5/6", 10: "8/9", 11: "9/10",
}


def _dvbs2_build_pls_codewords() -> List[int]:
    """预生成 128 个 PLS codeword（移植自 s2_defs.h:44-85 构造函数）。

    index 7-bit 格式 = MODCOD[4:0] | SHORTFRAME | PILOTS。
    """
    G = [0x55555555, 0x33333333, 0x0F0F0F0F,
         0x00FF00FF, 0x0000FFFF, 0xFFFFFFFF]
    codewords: List[int] = []
    for index in range(128):
        y = 0
        for row in range(6):
            if (index >> (6 - row)) & 1:
                y ^= G[row]
        code = 0
        for bit in range(31, -1, -1):
            yi = (y >> bit) & 1
            if index & 1:   # odd index
                code = (code << 2) | (yi << 1) | (yi ^ 1)
            else:           # even index
                code = (code << 2) | (yi << 1) | yi
        code ^= DVBS2_PLS_SCRAMBLING
        codewords.append(code & 0xFFFFFFFFFFFFFFFF)
    return codewords


#: 预计算 PLS codeword 表（来源: s2_defs.h:44-85）
_DVBS2_PLS_CODEWORDS = _dvbs2_build_pls_codewords()


def detect_sof(bits: np.ndarray) -> List[int]:
    """在硬判决比特流中硬匹配 DVB-S2 SOF（26-bit 0x18D2E82）。

    移植思路（来源: s2_defs.h:23-32，PL 同步相关）：按发送顺序（MSB first）
    滑窗 26 bit，与 SOF_VALUE 全等即命中。返回所有命中的起始比特位置。
    """
    bits = np.asarray(bits, dtype=np.uint8)
    n = len(bits)
    if n < DVBS2_SOF_LEN:
        return []
    # 用整数位加权快速滑窗
    weights = np.array([1 << (DVBS2_SOF_LEN - 1 - i) for i in range(DVBS2_SOF_LEN)],
                       dtype=np.uint64)
    hits: List[int] = []
    for i in range(n - DVBS2_SOF_LEN + 1):
        window = bits[i:i + DVBS2_SOF_LEN].astype(np.uint64)
        val = int(np.dot(window, weights))
        if val == DVBS2_SOF_VALUE:
            hits.append(i)
    return hits


def extract_plframe(bits: np.ndarray, sof_pos: int,
                    frame_type: str = "normal") -> np.ndarray:
    """从 sof_pos 起提取完整 PLFRAME（SOF + PLS + 数据字段）。

    frame_type: "normal" -> 90 + 64800 = 64890 bits
                "short"  -> 90 + 16200 = 16290 bits
    （帧长来源: dvbs2.h:5-6）
    """
    data_bits = (DVBS2_FRAME_NORMAL_BITS if frame_type == "normal"
                 else DVBS2_FRAME_SHORT_BITS)
    total = DVBS2_PLHEADER_LEN + data_bits
    frame = np.asarray(bits, dtype=np.uint8)[sof_pos:sof_pos + total]
    if len(frame) < total:
        raise ValueError(
            f"比特流不足：从 {sof_pos} 起需 {total} bit，仅剩 {len(frame)}")
    return frame


def decode_pls(pls_bits: np.ndarray) -> Dict:
    """解析 64-bit PLS code（移植自 s2_defs.h:44-85 的逆过程）。

    返回 dict：modcod(1-11 为 QPSK)、constellation、short_frame、pilots、index。
    做法：把 64 bit 按 MSB first 打包成 uint64，与 128 个预计算 codeword 全等匹配。
    """
    pls_bits = np.asarray(pls_bits, dtype=np.uint8)
    if len(pls_bits) < DVBS2_PLS_LEN:
        raise ValueError("PLS 至少需要 64 bit")
    code = 0
    for i in range(DVBS2_PLS_LEN):
        code = (code << 1) | int(pls_bits[i])
    code &= 0xFFFFFFFFFFFFFFFF

    index = -1
    for i, cw in enumerate(_DVBS2_PLS_CODEWORDS):
        if cw == code:
            index = i
            break
    if index < 0:
        # 容错：汉明距离最近
        best_d, best_i = 65, -1
        for i, cw in enumerate(_DVBS2_PLS_CODEWORDS):
            d = bin(cw ^ code).count("1")
            if d < best_d:
                best_d, best_i = d, i
        index = best_i

    modcod = (index >> 2) & 0x1F
    short_frame = bool((index >> 1) & 1)
    pilots = bool(index & 1)
    if 1 <= modcod <= 11:
        constellation = "QPSK"
        rate = DVBS2_QPSK_MODCOD_TABLE.get(modcod, "?")
    else:
        constellation = "?"
        rate = "?"
    return {
        "index": index,
        "modcod": modcod,
        "coderate": rate,
        "constellation": constellation,
        "short_frame": short_frame,
        "pilots": pilots,
    }


def _dvbs2_bb_prbs(num_bits: int) -> np.ndarray:
    """生成 DVB-S2 BB 帧解扰 PRBS 序列（移植自 bbframe_descramble.cpp:121-134）。

    LFSR 初值 sr=0x4A80，反馈多项式 1+x^14+x^15：
        b = (sr ^ (sr>>1)) & 1;  sr = (sr>>1) | (b<<15)
    按 MSB-first 输出 num_bits 个比特。
    """
    sr = 0x4A80
    out = np.zeros(num_bits, dtype=np.uint8)
    for i in range(num_bits):
        b = (sr ^ (sr >> 1)) & 1
        out[i] = b
        sr = sr >> 1
        if b:
            sr |= 0x4000
    return out


def descramble_dvbs2(data_bits: np.ndarray) -> np.ndarray:
    """DVB-S2 BB 帧解扰（PRBS 异或，自逆操作）。

    来源: bbframe_descramble.cpp:121-142。解扰与加扰同一序列，往返互逆。
    """
    data_bits = np.asarray(data_bits, dtype=np.uint8)
    prbs = _dvbs2_bb_prbs(len(data_bits))
    return (data_bits ^ prbs).astype(np.uint8)


def fy4_dvbs2_sync(bits: np.ndarray) -> List[Dict]:
    """FY-4 DVB-S2 物理层完整同步流程。

    流程：SOF 检测 -> PLFRAME 提取 -> PLS 解析 -> 数据字段解扰。
    返回帧列表，每帧 dict：pos / frame_type / pls / descrambled_bits。
    """
    bits = np.asarray(bits, dtype=np.uint8)
    frames: List[Dict] = []
    # 先用 normal 帧长尝试；若命中的 SOF 距离暗示短帧，由 PLS 结果纠正。
    for pos in detect_sof(bits):
        # 默认按 normal 提取（PLS 里会带 short_frame 标志）
        try:
            plframe = extract_plframe(bits, pos, "normal")
        except ValueError:
            try:
                plframe = extract_plframe(bits, pos, "short")
            except ValueError:
                continue
        pls_bits = plframe[DVBS2_SOF_LEN:DVBS2_PLHEADER_LEN]
        pls = decode_pls(pls_bits)
        # 按 PLS 报告的帧类型重新确定数据长度
        frame_type = "short" if pls["short_frame"] else "normal"
        data_len = (DVBS2_FRAME_SHORT_BITS if pls["short_frame"]
                    else DVBS2_FRAME_NORMAL_BITS)
        # 若初提为 normal 但 PLS 说 short，重新切片
        data = plframe[DVBS2_PLHEADER_LEN:DVBS2_PLHEADER_LEN + data_len]
        if len(data) < data_len:
            # 重新按正确帧类型取
            try:
                plframe = extract_plframe(bits, pos, frame_type)
                data = plframe[DVBS2_PLHEADER_LEN:]
            except ValueError:
                continue
        descrambled = descramble_dvbs2(data)
        frames.append({
            "pos": pos,
            "frame_type": frame_type,
            "pls": pls,
            "raw_data_bits": data,
            "descrambled_bits": descrambled,
        })
    return frames


# ============================================================================
# [DVB-S2真实物理层，移植自SatDump] FY-3 X 波段 AHRPT 帧同步 / 解扰 / 通道提取
# ----------------------------------------------------------------------------
# 移植来源（GPL-3.0）：
#   - CADU 长度 1024B / derand 范围 : SatDump plugins/fengyun3_support/fengyun3/
#                                     module_fengyun_ahrpt_decoder.cpp:52,122-124
#   - ASM = CCSDS 标准 0x1ACFFC1D   : SatDump src-core/common/codings/deframing/
#                                     bpsk_ccsds_deframer.h:62, cpp:7-8
#   - CCSDS 解扰 PN 表(255B)        : SatDump src-core/common/codings/randomization.cpp:4-78
#   - derand 作用于 cadu[4:]        : module_fengyun_ahrpt_decoder.cpp:124
# ============================================================================

#: AHRPT CADU 总长（字节）（来源: module_fengyun_ahrpt_decoder.cpp:52,130）
FY3_AHRPT_CADU_LEN = 1024
#: AHRPT 同步字 ASM（4 字节大端）（来源: bpsk_ccsds_deframer.h:62 默认 0x1ACFFC1D）
FY3_AHRPT_ASM = 0x1ACFFC1D
#: ASM 字节数
FY3_AHRPT_ASM_LEN = 4

#: CCSDS 解扰 PN 表（255 字节）（来源: randomization.cpp:4-36）
#: 多项式 1+x^3+x^5+x^7+x^8，周期 255。
_CCSDS_PN = bytes([
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


def fy3_ahrpt_sync(data: bytes) -> List[bytes]:
    """在字节流中搜索 AHRPT ASM 并切出完整 1024B CADU。

    来源: bpsk_ccsds_deframer.cpp:51,118-122（找到 32-bit ASM 后按 CADU_SIZE 定界）
    + module_fengyun_ahrpt_decoder.cpp:122,130（每帧 1024 字节）。
    """
    asm_bytes = FY3_AHRPT_ASM.to_bytes(FY3_AHRPT_ASM_LEN, "big")
    frames: List[bytes] = []
    start = 0
    n = len(data)
    while True:
        idx = data.find(asm_bytes, start)
        if idx < 0:
            break
        end = idx + FY3_AHRPT_CADU_LEN
        if end > n:
            break
        frames.append(bytes(data[idx:end]))
        start = end   # CADU 紧接，不重叠
    return frames


def fy3_descramble(data: bytes) -> bytes:
    """CCSDS 解扰（PN 异或，自逆）。

    来源: randomization.cpp:72-78 derand_ccsds：data[i] ^= ccsds_pn[i % 255]。
    注意 SatDump 中该函数作用于 cadu[4:]（module_...ahrpt_decoder.cpp:124）；
    本函数对传入字节按 0 起索引解扰，调用方负责传入去掉 ASM 的区段。
    """
    out = bytearray(len(data))
    for i, b in enumerate(data):
        out[i] = b ^ _CCSDS_PN[i % 255]
    return bytes(out)


def fy3_extract_channels(frame: bytes) -> Dict[str, np.ndarray]:
    """从一个完整 1024B AHRPT CADU 中提取 AVHRR 通道行像素。

    流程（对齐 SatDump）：
      1. 校验 4 字节 ASM（module_...ahrpt_decoder.cpp:118-121）；
      2. 对 cadu[4:] 做 CCSDS 解扰（module_...ahrpt_decoder.cpp:124）；
      3. 解扰后第 1 字节起为 CCSDS VCDU 头（6 字节），VCID = byte5 & 0x3F；
      4. 载荷区 cadu[10:1024]（1014B）按 AVHRR 通道 1/2/4 三等分，各作一行像素。
    返回 {"vcid", "ch1", "ch2", "ch4"}，每个通道为 uint8 一维数组（一行像素）。
    """
    if len(frame) < FY3_AHRPT_CADU_LEN:
        raise ValueError(
            f"AHRPT CADU 应为 {FY3_AHRPT_CADU_LEN}B，收到 {len(frame)}B")
    if int.from_bytes(frame[0:4], "big") != FY3_AHRPT_ASM:
        raise ValueError("CADU 起始不是 AHRPT ASM 0x1ACFFC1D")
    # 解扰数据区（不含 ASM）
    derand = fy3_descramble(frame[4:])
    # VCDU 头 6 字节位于 derand[0:6]；VCID = derand[1] & 0x3F
    vcid = derand[1] & 0x3F
    payload = derand[6:]                 # 1014 字节
    third = len(payload) // 3
    return {
        "vcid": vcid,
        "ch1": np.frombuffer(payload[0:third], dtype=np.uint8).copy(),
        "ch2": np.frombuffer(payload[third:2 * third], dtype=np.uint8).copy(),
        "ch4": np.frombuffer(payload[2 * third:3 * third], dtype=np.uint8).copy(),
    }
