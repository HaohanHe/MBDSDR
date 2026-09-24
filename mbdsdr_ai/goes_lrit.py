"""
MBDSDR AI 内核 - GOES LRIT/HRIT 帧解析与虚拟信道重组
================================================================

本模块逐行移植自 goestools (https://github.com/pietern/goestools) 的真实 C++ 源码，
覆盖 GOES-R 系列卫星 (GOES-16/17/18/19) 的 HRIT/LRIT 接收后端：

数据流（物理层 → 应用层）：

    软符号流
      → Viterbi 译码 (R=1/2, K=7)                    [goestools src/decoder/viterbi.h]
      → 帧同步字 0x1ACFFC1D 相关峰检测                 [goestools src/decoder/compute_sync_words.cc:37-40]
      → 1024 字节/帧 = 4B 同步字 + 1020B 数据          [goestools src/decoder/packetizer.h:14-26]
      → 解扰 (PN: x^8+x^7+x^5+x^3+1, init=0xFF)       [goestools src/decoder/derandomizer.cc:9-30]
      → Reed-Solomon (255,223) 译码 → 892B VCDU       [goestools src/assembler/vcdu.h:9]
      → 按 VCID 解复用 + M_PDU 第一头指针定位 TP_PDU    [goestools src/assembler/virtual_channel.cc]
      → CCSDS TP_PDU (APID/seqFlag/CRC16)             [goestools src/assembler/transport_pdu.h]
      → 按 APID 重组 SessionPDU = 一个 LRIT 文件        [goestools src/assembler/session_pdu.cc]
      → LRIT 文件头 (Primary/ImageStructure/SegmentId/Annotation/Rice ...)
                                                       [goestools src/lrit/lrit.h:16-149]

本移植不实现 Viterbi / Reed-Solomon（它们在 goestools 里是硬件/优化密集型），
而是从「RS 译码后的 892 字节 VCDU」开始向上重组；同时提供同步字搜索与解扰表，
便于对已抓包的 1020 字节传输帧做离线解析。

代码中所有关键常量与算法均标注「来源: goestools <file>:<line>」。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ============================================================================
# 帧层常量 —— 来源: goestools/src/decoder/packetizer.{h,cc}
# ============================================================================

#: 一帧 8192 位 = 1024 字节（Viterbi 译码后）
#: 来源: goestools src/decoder/packetizer.h:14  frameBits = 8192
FRAME_BITS = 8192
FRAME_BYTES = FRAME_BITS // 8  # = 1024

#: 32 位同步字。来源: goestools src/decoder/compute_sync_words.cc:37-40
#:   syncWord[0]=0x1A; syncWord[1]=0xCF; syncWord[2]=0xFC; syncWord[3]=0x1D;
SYNC_WORD = 0x1ACFFC1D
SYNC_WORD_BYTES = SYNC_WORD.to_bytes(4, "big")

#: 同步字之后的净荷长度 = 1024 - 4 = 1020 字节
#: 来源: goestools src/decoder/packetizer.cc:178  len = frameBytes - syncWordBytes
FRAME_DATA_BYTES = FRAME_BYTES - len(SYNC_WORD_BYTES)  # = 1020

#: RS 译码后的 VCDU 长度。来源: goestools src/assembler/vcdu.h:9
#:   using raw = std::array<uint8_t, 892>;
VCDU_LEN = 892

#: VCDU 头长度（版本/SCID/VCID/计数器）。来源: goestools src/assembler/vcdu.h:34-38
VCDU_HEADER_LEN = 6
VCDU_DATA_LEN = VCDU_LEN - VCDU_HEADER_LEN  # = 886

#: 填充 VCID。来源: goestools src/assembler/assembler.cc:13  if (vcid == 63) return;
FILL_VCID = 63

#: LRIT / HRIT 符号率。来源: goestools src/decoder/packetizer.cc:121-123
LRIT_SYMBOL_RATE = 293883
HRIT_SYMBOL_RATE = 927000


# ============================================================================
# 解扰 PN 表 —— 来源: goestools/src/decoder/derandomizer.cc:7-31
# ============================================================================

def _build_derandomizer_table() -> bytes:
    """构造 CCSDS 解扰伪随机序列（1020 字节）。

    多项式 h(x) = x^8 + x^7 + x^5 + x^3 + 1，LFSR 初值 0xFF。
    来源: goestools src/decoder/derandomizer.cc:9-30
    """
    table = bytearray(FRAME_DATA_BYTES)
    lfsr = 0xFF
    for i in range(FRAME_DATA_BYTES):
        b = 0
        for _ in range(8):
            b <<= 1
            b |= lfsr & 0x1
            bit = ((lfsr >> 7) ^ (lfsr >> 5) ^ (lfsr >> 3) ^ (lfsr >> 0)) & 0x1
            lfsr = (lfsr >> 1) | (bit << 7)
        table[i] = b
    return bytes(table)


#: 预生成的解扰表（1020 字节）。来源: derandomizer.cc:22  table_.size()
DERANDOM_TABLE = _build_derandomizer_table()


def derandomize(frame_data: bytes) -> bytes:
    """对同步字之后的 1020 字节做异或解扰。

    来源: goestools src/decoder/derandomizer.cc:33-38
      void Derandomizer::run(uint8_t* data, size_t len) {
        ASSERT(len == table_.size());
        for (i...) data[i] ^= table_[i];
      }
    """
    if len(frame_data) != FRAME_DATA_BYTES:
        raise ValueError(
            f"解扰长度必须为 {FRAME_DATA_BYTES} 字节（一帧净荷），实际 {len(frame_data)}"
        )
    return bytes(a ^ b for a, b in zip(frame_data, DERANDOM_TABLE))


# ============================================================================
# CRC-16/CCITT (LRIT TP_PDU 尾部校验) —— 来源: goestools/src/assembler/crc.cc
# ============================================================================

_CRC_TABLE = [
    0x0000, 0x1021, 0x2042, 0x3063, 0x4084, 0x50A5, 0x60C6, 0x70E7,
    0x8108, 0x9129, 0xA14A, 0xB16B, 0xC18C, 0xD1AD, 0xE1CE, 0xF1EF,
    0x1231, 0x0210, 0x3273, 0x2252, 0x52B5, 0x4294, 0x72F7, 0x62D6,
    0x9339, 0x8318, 0xB37B, 0xA35A, 0xD3BD, 0xC39C, 0xF3FF, 0xE3DE,
    0x2462, 0x3443, 0x0420, 0x1401, 0x64E6, 0x74C7, 0x44A4, 0x5485,
    0xA56A, 0xB54B, 0x8528, 0x9509, 0xE5EE, 0xF5CF, 0xC5AC, 0xD58D,
    0x3653, 0x2672, 0x1611, 0x0630, 0x76D7, 0x66F6, 0x5695, 0x46B4,
    0xB75B, 0xA77A, 0x9719, 0x8738, 0xF7DF, 0xE7FE, 0xD79D, 0xC7BC,
    0x48C4, 0x58E5, 0x6886, 0x78A7, 0x0840, 0x1861, 0x2802, 0x3823,
    0xC9CC, 0xD9ED, 0xE98E, 0xF9AF, 0x8948, 0x9969, 0xA90A, 0xB92B,
    0x5AF5, 0x4AD4, 0x7AB7, 0x6A96, 0x1A71, 0x0A50, 0x3A33, 0x2A12,
    0xDBFD, 0xCBDC, 0xFBBF, 0xEB9E, 0x9B79, 0x8B58, 0xBB3B, 0xAB1A,
    0x6CA6, 0x7C87, 0x4CE4, 0x5CC5, 0x2C22, 0x3C03, 0x0C60, 0x1C41,
    0xEDAE, 0xFD8F, 0xCDEC, 0xDDCD, 0xAD2A, 0xBD0B, 0x8D68, 0x9D49,
    0x7E97, 0x6EB6, 0x5ED5, 0x4EF4, 0x3E13, 0x2E32, 0x1E51, 0x0E70,
    0xFF9F, 0xEFBE, 0xDFDD, 0xCFFC, 0xBF1B, 0xAF3A, 0x9F59, 0x8F78,
    0x9188, 0x81A9, 0xB1CA, 0xA1EB, 0xD10C, 0xC12D, 0xF14E, 0xE16F,
    0x1080, 0x00A1, 0x30C2, 0x20E3, 0x5004, 0x4025, 0x7046, 0x6067,
    0x83B9, 0x9398, 0xA3FB, 0xB3DA, 0xC33D, 0xD31C, 0xE37F, 0xF35E,
    0x02B1, 0x1290, 0x22F3, 0x32D2, 0x4235, 0x5214, 0x6277, 0x7256,
    0xB5EA, 0xA5CB, 0x95A8, 0x8589, 0xF56E, 0xE54F, 0xD52C, 0xC50D,
    0x34E2, 0x24C3, 0x14A0, 0x0481, 0x7466, 0x6447, 0x5424, 0x4405,
    0xA7DB, 0xB7FA, 0x8799, 0x97B8, 0xE75F, 0xF77E, 0xC71D, 0xD73C,
    0x26D3, 0x36F2, 0x0691, 0x16B0, 0x6657, 0x7676, 0x4615, 0x5634,
    0xD94C, 0xC96D, 0xF90E, 0xE92F, 0x99C8, 0x89E9, 0xB98A, 0xA9AB,
    0x5844, 0x4865, 0x7806, 0x6827, 0x18C0, 0x08E1, 0x3882, 0x28A3,
    0xCB7D, 0xDB5C, 0xEB3F, 0xFB1E, 0x8BF9, 0x9BD8, 0xABBB, 0xBB9A,
    0x4A75, 0x5A54, 0x6A37, 0x7A16, 0x0AF1, 0x1AD0, 0x2AB3, 0x3A92,
    0xFD2E, 0xED0F, 0xDD6C, 0xCD4D, 0xBDAA, 0xAD8B, 0x9DE8, 0x8DC9,
    0x7C26, 0x6C07, 0x5C64, 0x4C45, 0x3CA2, 0x2C83, 0x1CE0, 0x0CC1,
    0xEF1F, 0xFF3E, 0xCF5D, 0xDF7C, 0xAF9B, 0xBFBA, 0x8FD9, 0x9FF8,
    0x6E17, 0x7E36, 0x4E55, 0x5E74, 0x2E93, 0x3EB2, 0x0ED1, 0x1EF0,
]


def crc16_ccitt(data: bytes, crc_init: int = 0xFFFF) -> int:
    """LRIT TP_PDU 尾部 CRC-16/CCITT。

    来源: goestools src/assembler/crc.cc:46-52
      uint16_t crc(const uint8_t* buf, size_t len) {
        uint16_t crc = 0xffff;
        for (i...) crc = (crc<<8) ^ table[(crc>>8) ^ buf[i]];
        return crc;
      }
    查表见 crc.cc:9-42。
    """
    crc = crc_init
    for b in data:
        crc = ((crc << 8) & 0xFFFF) ^ _CRC_TABLE[((crc >> 8) ^ b) & 0xFF]
    return crc & 0xFFFF


# ============================================================================
# VCDU —— Virtual Channel Data Unit（RS 译码后的 892 字节）
# 来源: goestools/src/assembler/vcdu.h
# ============================================================================

@dataclass
class VCDU:
    """892 字节 VCDU 解包结果。

    位域布局（大端）:
        byte0: [version(2) | SCID_high(6)]
        byte1: [SCID_low(2) | VCID(6)]
        byte2..4: VCDU counter (24-bit, 大端)
        byte5:   保留
        byte6..: M_PDU 数据
    来源: goestools src/assembler/vcdu.h:17-39
    """

    version: int
    scid: int
    vcid: int
    counter: int
    #: VCDU 头之后的 886 字节 M_PDU
    data: bytes

    @classmethod
    def parse(cls, raw: bytes) -> "VCDU":
        if len(raw) != VCDU_LEN:
            raise ValueError(f"VCDU 长度必须为 {VCDU_LEN} 字节，实际 {len(raw)}")
        b = raw
        # getVersion: (data_[0] & 0xc0) >> 6          vcdu.h:17-19
        version = (b[0] & 0xC0) >> 6
        # getSCID: (data_[0] & 0x3f)<<2 | (data_[1] & 0xc0)>>6   vcdu.h:21-23
        scid = ((b[0] & 0x3F) << 2) | ((b[1] & 0xC0) >> 6)
        # getVCID: (data_[1] & 0x3f)                          vcdu.h:25-27
        vcid = b[1] & 0x3F
        # getCounter: (b[2]<<16)|(b[3]<<8)|b[4]               vcdu.h:29-31
        counter = (b[2] << 16) | (b[3] << 8) | b[4]
        # data(): &data_[6], len = size-6 = 886               vcdu.h:33-39
        return cls(version=version, scid=scid, vcid=vcid,
                   counter=counter, data=bytes(b[VCDU_HEADER_LEN:]))


# ============================================================================
# CCSDS Transport PDU (TP_PDU / Source Packet)
# 来源: goestools/src/assembler/transport_pdu.{h,cc}
# ============================================================================

#: TP_PDU 主头 6 字节。来源: transport_pdu.h:14  headerBytes = 6
TP_PDU_HEADER_BYTES = 6
#: APID 全 1 = 填充包。来源: virtual_channel.cc:118  if (apid == 2047) return;
FILL_APID = 2047
#: M_PDU 中「无新包起始」的第一头指针值。来源: virtual_channel.cc:95  if (firstHeader == 2047) return;
MPDU_NO_PACKET = 2047


@dataclass
class TransportPDU:
    """CCSDS 源包（TP_PDU）。

    主头 6 字节（大端）:
        byte0: [version(3) | type(1) | secHdr(1) | APID_high(3)]
        byte1: [APID_low(8)]
        byte2: [seqFlag(2) | seqCount_high(6)]
        byte3: [seqCount_low(8)]
        byte4..5: packet length (大端) = 后续字节数 - 1
    来源: transport_pdu.h:33-68
    """

    apid: int
    seq_flag: int       # 3=整包, 1=首段, 0=续段, 2=末段
    seq_count: int      # 14-bit, mod 16384
    length: int         # 用户数据字节数（= packet length 字段 + 1）
    payload: bytes = b""

    @classmethod
    def parse_header(cls, hdr6: bytes) -> "TransportPDU":
        if len(hdr6) < TP_PDU_HEADER_BYTES:
            raise ValueError("TP_PDU 头至少 6 字节")
        b = hdr6
        # apid: ((header[0]&0x7)<<8) | header[1]     transport_pdu.h:45-47
        apid = ((b[0] & 0x07) << 8) | b[1]
        # sequenceFlag: (header[2]>>6)&0x3          transport_pdu.h:50-52
        seq_flag = (b[2] >> 6) & 0x03
        # sequenceCount: (header[2]&0x3f)<<8 | header[3]   transport_pdu.h:54-56
        seq_count = ((b[2] & 0x3F) << 8) | b[3]
        # length: ((header[4]<<8)|header[5]) + 1      transport_pdu.h:59-68
        length = ((b[4] << 8) | b[5]) + 1
        return cls(apid=apid, seq_flag=seq_flag, seq_count=seq_count, length=length)

    def crc_value(self) -> int:
        """包尾 2 字节 CRC。来源: transport_pdu.h:70-73"""
        if len(self.payload) < 2:
            return 0
        return (self.payload[-2] << 8) | self.payload[-1]

    def verify_crc(self) -> bool:
        """校验包尾 CRC。来源: transport_pdu.cc:37-43
          return crc(&data[0], length-2) == this->crc();
        """
        if self.length < 2 or len(self.payload) < self.length:
            return False
        body = self.payload[: self.length - 2]
        return crc16_ccitt(body) == self.crc_value()


# ============================================================================
# LRIT 文件头（SessionPDU 解出来的就是一个完整 LRIT 文件）
# 来源: goestools/src/lrit/lrit.{h,cc}
# ============================================================================

#: 头类型码。来源: goestools src/lrit/lrit.h:16-149
H_PRIMARY = 0            # lrit.h:17
H_IMAGE_STRUCTURE = 1    # lrit.h:27
H_IMAGE_NAVIGATION = 2   # lrit.h:38
H_IMAGE_DATA_FUNC = 3    # lrit.h:53
H_ANNOTATION = 4         # lrit.h:61
H_TIMESTAMP = 5           # lrit.h:69
H_ANCILLARY_TEXT = 6     # lrit.h:83
H_KEY = 7                # lrit.h:91
H_SEGMENT_ID = 128       # lrit.h:100
H_NOAA_LRIT = 129        # lrit.h:114
H_HEADER_STRUCTURE = 130 # lrit.h:126
H_RICE_COMPRESSION = 131  # lrit.h:134
H_DCS_FILENAME = 132      # lrit.h:144

#: PrimaryHeader 固定 16 字节。来源: lrit.cc:85  ASSERT(headerLength == 16)
PRIMARY_HEADER_LEN = 16


@dataclass
class LRIHeader:
    """解析后的 LRIT 文件头集合。"""
    file_type: int = 0          # 0=图像, 1=文本消息, 2=产品, 130=DCS (goeslrit.cc:26-41)
    total_header_length: int = 0
    data_length_bits: int = 0   # 单位 bit
    bits_per_pixel: int = 0
    columns: int = 0
    lines: int = 0
    compression: int = 0         # 1 = Rice 压缩 (session_pdu.cc:135)
    annotation: str = ""
    image_identifier: int = 0
    segment_number: int = 0
    segment_start_column: int = 0
    segment_start_line: int = 0
    max_segment: int = 0
    max_column: int = 0
    max_line: int = 0
    rice_flags: int = 0
    pixels_per_block: int = 0
    scan_lines_per_packet: int = 0
    raw_headers: Dict[int, int] = field(default_factory=dict)


def parse_lrit_headers(buf: bytes) -> LRIHeader:
    """从一个完整 LRIT 文件缓冲区解析全部头。

    移植自:
      - getHeaderMap          lrit.cc:76-105  遍历 type/length 记录
      - getHeader<Primary>    lrit.cc:164-172
      - getHeader<ImageStructure>  lrit.cc:174-183
      - getHeader<Annotation> lrit.cc:214-220
      - getHeader<SegmentIdentification> lrit.cc:238-250
      - getHeader<RiceCompression> lrit.cc:272-280
    """
    out = LRIHeader()
    if len(buf) < PRIMARY_HEADER_LEN:
        return out

    # ── PrimaryHeader: type=0, length=16, fileType(1), totalHdrLen(4 BE), dataLength(8 BE)
    # 来源: lrit.cc:82-87, 165-172
    if buf[0] != H_PRIMARY:
        return out
    out.file_type = buf[3]
    out.total_header_length = struct.unpack_from(">I", buf, 4)[0]
    out.data_length_bits = struct.unpack_from(">Q", buf, 8)[0]

    # ── 遍历二级头：每条 = type(1) + length(2 BE) + payload
    # 来源: lrit.cc:91-102
    pos = 0
    total = out.total_header_length
    while pos + 3 <= total and pos < len(buf):
        htype = buf[pos]
        hlen = (buf[pos + 1] << 8) | buf[pos + 2]
        if hlen == 0:
            break
        out.raw_headers[htype] = pos
        p = pos + 3  # 跳过 type+length 三字节
        if htype == H_IMAGE_STRUCTURE and hlen >= 8:
            # 来源: lrit.cc:175-183
            out.bits_per_pixel = buf[p]
            out.columns = (buf[p + 1] << 8) | buf[p + 2]
            out.lines = (buf[p + 3] << 8) | buf[p + 4]
            out.compression = buf[p + 5]
        elif htype == H_ANNOTATION and hlen >= 3:
            # 来源: lrit.cc:215-220  text 长度 = headerLength-3
            out.annotation = bytes(buf[pos + 3: pos + hlen]).rstrip(b"\x00").decode("ascii", "replace")
        elif htype == H_SEGMENT_ID and hlen >= 17:
            # 来源: lrit.cc:239-250
            out.image_identifier = (buf[p] << 8) | buf[p + 1]
            out.segment_number = (buf[p + 2] << 8) | buf[p + 3]
            out.segment_start_column = (buf[p + 4] << 8) | buf[p + 5]
            out.segment_start_line = (buf[p + 6] << 8) | buf[p + 7]
            out.max_segment = (buf[p + 8] << 8) | buf[p + 9]
            out.max_column = (buf[p + 10] << 8) | buf[p + 11]
            out.max_line = (buf[p + 12] << 8) | buf[p + 13]
        elif htype == H_RICE_COMPRESSION and hlen >= 8:
            # 来源: lrit.cc:273-280
            out.rice_flags = (buf[p] << 8) | buf[p + 1]
            out.pixels_per_block = buf[p + 2]
            out.scan_lines_per_packet = buf[p + 3]
        pos += hlen
    return out


# ============================================================================
# Virtual Channel 重组器
# 来源: goestools/src/assembler/virtual_channel.{h,cc} + session_pdu.{h,cc}
# ============================================================================

class _SessionPDU:
    """按 APID 重组一个完整 LRIT 文件（Session PDU）。

    移植自 session_pdu.cc:73-220。
    关键规则:
      - 第一个 TP_PDU 的用户数据前 10 字节是垃圾，跳过 (session_pdu.cc:78-82)
      - 每个 TP_PDU 最后 2 字节是 CRC，不并入文件 (session_pdu.cc:80,118)
      - seqFlag=1/3 开始新文件；seqFlag=2 结束文件
    """

    def __init__(self, vcid: int, apid: int):
        self.vcid = vcid
        self.apid = apid
        self.buf = bytearray()
        self._ph_parsed = False
        self._total_hdr = 0
        self._first = True

    def append(self, tpdu: TransportPDU) -> bool:
        """把一个 TP_PDU 的用户数据并入 S_PDU。返回是否接受。"""
        data = tpdu.payload
        # 截掉尾部 CRC 2 字节。来源: session_pdu.cc:80,118
        body = bytes(data[: tpdu.length - 2]) if tpdu.length >= 2 else b""

        if self._first:
            self._first = False
            # 第一个包前 10 字节是垃圾。来源: session_pdu.cc:78-82
            body = body[10:]

        # 先攒够 primary header (16B) 以得知 totalHeaderLength
        if not self._ph_parsed:
            need = PRIMARY_HEADER_LEN - len(self.buf)
            if need > 0:
                take = min(need, len(body))
                self.buf += body[:take]
                body = body[take:]
                if len(self.buf) < PRIMARY_HEADER_LEN:
                    return True
            # 解析 primary header
            self._total_hdr = struct.unpack_from(">I", self.buf, 4)[0]
            self._ph_parsed = True

        # 继续攒二级头
        if len(self.buf) < self._total_hdr:
            need = self._total_hdr - len(self.buf)
            take = min(need, len(body))
            self.buf += body[:take]
            body = body[take:]

        # 头齐了之后，剩余是图像/文本数据，直接追加
        if body:
            self.buf += body
        return True

    @property
    def complete(self) -> bool:
        return self._ph_parsed and len(self.buf) >= self._total_hdr

    def expected_size(self) -> int:
        """文件应有总字节数 = totalHeaderLength + ceil(dataLength/8)。
        来源: virtual_channel.cc:284  size = totalHeaderLength + (dataLength+7)/8
        """
        if not self._ph_parsed:
            return 0
        data_bits = struct.unpack_from(">Q", self.buf, 8)[0]
        return self._total_hdr + (data_bits + 7) // 8


class _VirtualChannel:
    """单个 VCID 的状态机：VCDU → TP_PDU → SessionPDU。

    移植自 virtual_channel.cc:14-110（M_PDU 切包）与 :112-274（按 APID 重组）。
    """

    def __init__(self, vcid: int):
        self.vcid = vcid
        self._last_counter = -1
        # 跨 VCDU 未收完的 TP_PDU（头已解析、payload 可能不足 length）。
        # 来源: virtual_channel.cc:34  std::unique_ptr<TransportPDU> tpdu_;
        self._in_progress: Optional[TransportPDU] = None
        # 跨 VCDU 连头都没凑够 6 字节时缓存的前导字节。
        self._hdr_tail = bytearray()
        self._apid_spdu: Dict[int, _SessionPDU] = {}

    def process(self, vcdu: VCDU) -> List[bytes]:
        out: List[bytes] = []

        # VCDU 计数器丢包检测（mod 2^24）。来源: virtual_channel.cc:20-34
        if self._last_counter >= 0:
            nxt = vcdu.counter
            diff = (nxt - self._last_counter) & ((1 << 24) - 1)
            if diff > 1:
                # 丢帧，丢弃未完成的 TP_PDU
                self._in_progress = None
                self._hdr_tail.clear()
        self._last_counter = vcdu.counter

        data = vcdu.data
        # M_PDU 第一头指针。来源: virtual_channel.cc:44
        #   firstHeader = ((data[0] & 0x7) << 8) | data[1];
        fhp = ((data[0] & 0x07) << 8) | data[1]
        # 跳过 M_PDU 头 2 字节。来源: virtual_channel.cc:47-48
        mpdu = data[2:]

        # 若上一个 TP_PDU 没收完，先续上。来源: virtual_channel.cc:52-92
        pos = 0
        if self._in_progress is not None or self._hdr_tail:
            pos = self._continue_in_progress(mpdu, out)
            if pos == len(mpdu):
                return out

        # 2047 = 本 VCDU 无新包起始。来源: virtual_channel.cc:95-97
        if fhp == MPDU_NO_PACKET:
            return out

        # 从 fhp 位置开始切出新的 TP_PDU。来源: virtual_channel.cc:100-107
        pos = fhp
        while pos < len(mpdu):
            pos = self._consume_one_tpdu(mpdu, pos, out)
            if pos < 0:
                break  # 包未收完，已存为 in_progress
        return out

    def _continue_in_progress(self, mpdu: bytes, out: List[bytes]) -> int:
        """继续填充跨 VCDU 没收完的 TP_PDU，返回已消费字节数。"""
        pos = 0
        # 先补头
        if self._hdr_tail:
            need = TP_PDU_HEADER_BYTES - len(self._hdr_tail)
            take = min(need, len(mpdu))
            self._hdr_tail += mpdu[:take]
            pos += take
            if len(self._hdr_tail) < TP_PDU_HEADER_BYTES:
                return pos
            hdr = bytes(self._hdr_tail)
            self._hdr_tail = bytearray()
            self._in_progress = TransportPDU.parse_header(hdr)

        if self._in_progress is not None:
            need = self._in_progress.length - len(self._in_progress.payload)
            take = min(need, len(mpdu) - pos)
            self._in_progress.payload += mpdu[pos: pos + take]
            pos += take
            if len(self._in_progress.payload) >= self._in_progress.length:
                done = self._in_progress
                self._in_progress = None
                self._finish_tpdu(done, out)
        return pos

    def _consume_one_tpdu(self, mpdu: bytes, pos: int, out: List[bytes]) -> int:
        """从 mpdu[pos] 读一个 TP_PDU。返回下一个起点；<0 表示包未读完已挂起。"""
        # 头不足 6 字节
        if pos + TP_PDU_HEADER_BYTES > len(mpdu):
            self._hdr_tail = bytearray(mpdu[pos:])
            return -1
        tpdu = TransportPDU.parse_header(mpdu[pos: pos + TP_PDU_HEADER_BYTES])
        pos += TP_PDU_HEADER_BYTES

        # 读 payload 直到 length
        need = tpdu.length
        take = min(need, len(mpdu) - pos)
        tpdu.payload = bytearray(mpdu[pos: pos + take])
        pos += take
        if len(tpdu.payload) < tpdu.length:
            self._in_progress = tpdu
            return -1
        self._finish_tpdu(tpdu, out)
        return pos

    def _finish_tpdu(self, tpdu: TransportPDU, out: List[bytes]) -> None:
        """TP_PDU 收齐后，按 APID/seqFlag 并入对应 SessionPDU。

        移植自 virtual_channel.cc:112-274。
        """
        if tpdu.apid == FILL_APID:
            return  # virtual_channel.cc:118-120

        # CRC 校验失败则丢弃该 APID 状态。来源: virtual_channel.cc:123-134
        if not tpdu.verify_crc():
            self._apid_spdu.pop(tpdu.apid, None)
            return

        flag = tpdu.seq_flag
        if flag in (1, 3):
            # 新文件开始。来源: virtual_channel.cc:170-223
            spdu = _SessionPDU(self.vcid, tpdu.apid)
            spdu.append(tpdu)
            if flag == 3:
                # 整包在一个 TP_PDU 内
                if spdu.complete and spdu.expected_size() == len(spdu.buf):
                    out.append(bytes(spdu.buf))
            else:
                self._apid_spdu[tpdu.apid] = spdu
        else:
            # 续段/末段。来源: virtual_channel.cc:224-273
            spdu = self._apid_spdu.get(tpdu.apid)
            if spdu is None:
                return
            spdu.append(tpdu)
            if flag == 2:
                if spdu.complete and spdu.expected_size() == len(spdu.buf):
                    out.append(bytes(spdu.buf))
                self._apid_spdu.pop(tpdu.apid, None)


# ============================================================================
# LRITParser / HRITParser
# ============================================================================

class _BaseParser:
    """LRIT/HRIT 共用的 VCDU→文件重组引擎。

    HRIT 与 LRIT 在 VCDU/TP_PDU/S_PDU 层完全一致，差别只在物理层
    （HRIT 符号率 927kbaud、NRZ-M 译码；LRIT 293kbaud、NRZ-L）。
    来源: packetizer.cc:120-123, 157-168
    """

    #: 子类覆盖：符号率（仅元信息）
    symbol_rate: int = 0

    def __init__(self) -> None:
        self._vcs: Dict[int, _VirtualChannel] = {}

    # ── 同步字搜索 ──────────────────────────────────────────────
    @staticmethod
    def find_sync(buf: bytes) -> int:
        """在字节流中搜索同步字 0x1ACFFC1D，返回偏移；未找到返回 -1。

        来源: goestools src/decoder/compute_sync_words.cc:37-40 同步字定义；
              correlator.cc:47-62 为软符号相关峰，这里对已硬判决字节流做精确匹配。
        """
        idx = buf.find(SYNC_WORD_BYTES)
        return idx

    @staticmethod
    def parse_transport_frame(frame: bytes) -> bytes:
        """处理一个 1024 字节传输帧：校验同步字 → 取 1020B 净荷 → 解扰。

        返回解扰后的 1020 字节（注意：尚未做 Reed-Solomon，真实链路此处
        还会 RS 解出 892 字节 VCDU；离线抓包场景若已有 892B VCDU 可直接 feed_vcdu）。
        来源: packetizer.cc:170-179
        """
        if len(frame) != FRAME_BYTES:
            raise ValueError(f"传输帧长度必须为 {FRAME_BYTES} 字节，实际 {len(frame)}")
        if frame[:4] != SYNC_WORD_BYTES:
            raise ValueError("同步字不匹配 0x1ACFFC1D")
        return derandomize(frame[4:4 + FRAME_DATA_BYTES])

    # ── VCDU 喂入 ──────────────────────────────────────────────
    def feed_vcdu(self, raw_vcdu: bytes) -> List[bytes]:
        """喂入一个 RS 译码后的 892 字节 VCDU，返回本次收齐的完整 LRIT 文件列表。"""
        vcdu = VCDU.parse(raw_vcdu)
        if vcdu.vcid == FILL_VCID:
            return []  # assembler.cc:13
        vc = self._vcs.get(vcdu.vcid)
        if vc is None:
            vc = _VirtualChannel(vcdu.vcid)
            self._vcs[vcdu.vcid] = vc
        return vc.process(vcdu)

    def parse_stream(self, vcdus: List[bytes]) -> List[bytes]:
        """批量喂入 VCDU 流，返回所有收齐的文件。"""
        out: List[bytes] = []
        for v in vcdus:
            out.extend(self.feed_vcdu(v))
        return out


class LRITParser(_BaseParser):
    """GOES LRIT 低分辨率信息传输解析器。

    来源: goestools src/goeslrit/goeslrit.cc + src/assembler/*
    符号率 293883 baud（packetizer.cc:123）。
    """
    symbol_rate = LRIT_SYMBOL_RATE


class HRITParser(_BaseParser):
    """GOES HRIT 高分辨率信息传输解析器。

    来源: goestools src/goesproc/* + src/assembler/*
    符号率 927000 baud（packetizer.cc:121），物理层 NRZ-M（packetizer.cc:157-168）。
    """
    symbol_rate = HRIT_SYMBOL_RATE


# ============================================================================
# Rice/Golomb 压缩骨架
# 来源: goestools/src/assembler/session_pdu.cc:139-160
# ============================================================================

class RiceDecoder:
    """ Rice 压缩解码骨架。

    goestools 真实实现依赖 NASA szlib (SZ_BufftoBuffDecompress)，见
      session_pdu.h:7-9  #include <szlib.h>
      session_pdu.cc:152-160  SZ_com_t 参数填充
      session_pdu.cc:211      SZ_BufftoBuffDecompress(...)

    参数来自 LRIT RiceCompressionHeader (type=131):
      - bits_per_pixel        ← ImageStructureHeader.bitsPerPixel
      - pixels_per_block      ← RiceCompressionHeader.pixelsPerBlock
      - pixels_per_scanline   ← ImageStructureHeader.columns
      - options_mask          ← RiceCompressionHeader.flags | SZ_RAW_OPTION_MASK

    此处仅保留参数解析与未压缩直通路径；完整 Rice 解码需链接 szlib。
    """

    def __init__(self, bits_per_pixel: int, pixels_per_block: int,
                 pixels_per_scanline: int, flags: int = 0):
        self.bits_per_pixel = bits_per_pixel
        self.pixels_per_block = pixels_per_block
        self.pixels_per_scanline = pixels_per_scanline
        self.flags = flags

    @classmethod
    def from_lrit_header(cls, h: LRIHeader) -> "RiceDecoder":
        """从解析出的 LRIT 头构造解码器。来源: session_pdu.cc:152-159"""
        return cls(
            bits_per_pixel=h.bits_per_pixel,
            pixels_per_block=h.pixels_per_block,
            pixels_per_scanline=h.columns,
            flags=h.rice_flags,
        )

    def decompress_line(self, compressed: bytes) -> bytes:
        """解一行扫描线。骨架：未压缩 (compression!=1) 时直通。

        真实 Rice 解码需 szlib；此处返回原始字节并标注。
        """
        return bytes(compressed)


# ============================================================================
# GOES 图像段重组 + 灰度映射
# ============================================================================

@dataclass
class GOESImage:
    """重组完成的整幅图像。"""
    width: int
    height: int
    pixels: List[int]           # 长度 width*height，uint8 灰度
    annotation: str = ""
    image_identifier: int = 0


class GOESImageDecoder:
    """把多个 LRIT 图像段（fileType==0）重组为完整灰度图。

    goestools 中 GOES-R 系列图像按段下发，每段一个独立 LRIT 文件，
    通过 SegmentIdentificationHeader (type=128) 定位：
      - imageIdentifier      同一张图的所有段相同
      - segmentNumber        段序号 (1..maxSegment)
      - segmentStartColumn/Line  该段在整图中的起始位置
      - maxColumn/maxLine    整图像素数
    来源: goestools src/lrit/lrit.h:99-111, src/goeslrit/goeslrit.cc:55-64
    """

    def __init__(self) -> None:
        #: image_identifier -> {segment_number: (header, data_bytes)}
        self._segments: Dict[int, Dict[int, Tuple[LRIHeader, bytes]]] = {}

    def add_lrit_file(self, file_buf: bytes) -> Optional[GOESImage]:
        """喂入一个完整 LRIT 文件；若凑齐整图则返回 GOESImage，否则 None。

        返回整图的条件：收到 imageIdentifier 对应的全部 maxSegment 段。
        """
        h = parse_lrit_headers(file_buf)
        if h.file_type != 0:
            return None  # 仅处理图像文件 (goeslrit.cc:26)

        data = bytes(file_buf[h.total_header_length:])
        ident = h.image_identifier
        seg_no = h.segment_number

        pool = self._segments.setdefault(ident, {})
        pool[seg_no] = (h, data)

        # 判断是否凑齐
        max_seg = max(hdr.max_segment for hdr, _ in pool.values()) or 1
        if len(pool) < max_seg:
            return None

        # 凑齐 → 拼接
        width = h.max_column or h.columns
        height = h.max_line or h.lines
        canvas = bytearray(width * height)

        for _, (sh, sdata) in pool.items():
            x0 = sh.segment_start_column
            y0 = sh.segment_start_line
            # 每段有 columns 列、(段内行数) 行；按行写入画布
            seg_cols = sh.columns or width
            # 段数据按行连续；行高 = len(sdata)/seg_cols（1bpp 时按 bit 展开）
            bpp = max(1, sh.bits_per_pixel)
            row_bytes = (seg_cols * bpp + 7) // 8
            seg_rows = len(sdata) // row_bytes if row_bytes else 0
            for r in range(seg_rows):
                dst_row = y0 + r
                if dst_row >= height:
                    break
                src = r * row_bytes
                # 1bpp 展开为灰度（可见光反射率常用 10bit→8bit 映射，这里简化为 8bpp 直通）
                for c in range(seg_cols):
                    if x0 + c >= width:
                        break
                    if bpp == 8:
                        px = sdata[src + c]
                    elif bpp == 16:
                        px = ((sdata[src + 2 * c] << 8) | sdata[src + 2 * c + 1]) >> 8
                    else:
                        # 1bpp：取 bit
                        byte_idx = c // 8
                        bit_idx = 7 - (c % 8)
                        px = 255 if (sdata[src + byte_idx] >> bit_idx) & 1 else 0
                    canvas[dst_row * width + x0 + c] = px

        self._segments.pop(ident, None)
        return GOESImage(
            width=width,
            height=height,
            pixels=list(canvas),
            annotation=h.annotation,
            image_identifier=ident,
        )

    @staticmethod
    def ir_to_grayscale(raw: List[int], vmin: int = 180, vmax: int = 255) -> List[int]:
        """IR 通道原始计数 → 8bit 灰度（亮=冷云顶，暗=暖地表）。

        GOES ABI IR 通道原始计数通常 10bit；这里做线性窗口拉伸到 0..255。
        参考: goestools src/goesproc/gradient.cc 梯度映射。
        """
        out = []
        span = max(1, vmax - vmin)
        for v in raw:
            g = (v - vmin) * 255 // span
            if g < 0:
                g = 0
            elif g > 255:
                g = 255
            out.append(g)
        return out


# ============================================================================
# 工具注册表接入
# ============================================================================

def register_tool_registry(registry) -> None:
    """把 GOES LRIT/HRIT 解析工具注册到 MBDSDR ToolRegistry。

    在 tool_registry.py 的 register_builtin_tools() 中调用。
    """
    import json as _json

    registry.register(
        name="goes_lrit_parse",
        description=(
            "GOES LRIT 帧解析（移植 goestools src/assembler/*）：喂入 892B VCDU 列表，"
            "按 VCID/M_PDU/CCSDS TP_PDU 重组出完整 LRIT 文件并解析文件头。"
            "返回每个文件的 fileType/文件名(annotation)/头长度/数据长度/段信息。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "vcdus": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "892 字节 VCDU 的 hex 字符串列表（RS 译码后）",
                },
            },
            "required": ["vcdus"],
        },
        handler=lambda args: ToolResult_goes(
            args, is_hrit=False),
        category="satellite",
    )

    registry.register(
        name="goes_hrit_parse",
        description=(
            "GOES HRIT 帧解析（移植 goestools src/assembler/*）：与 LRIT 同协议栈，"
            "符号率 927kbaud。喂入 892B VCDU 列表，重组 LRIT 文件。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "vcdus": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "892 字节 VCDU 的 hex 字符串列表（RS 译码后）",
                },
            },
            "required": ["vcdus"],
        },
        handler=lambda args: ToolResult_goes(args, is_hrit=True),
        category="satellite",
    )

    registry.register(
        name="goes_extract_image",
        description=(
            "GOES 图像段重组（移植 goestools src/goesproc/lrit_processor.cc）："
            "把多个 LRIT 图像段按 SegmentIdentificationHeader 拼接为整幅灰度图，"
            "返回 width/height/像素数与 annotation。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "完整 LRIT 文件字节的 hex 字符串列表",
                },
            },
            "required": ["files"],
        },
        handler=lambda args: ToolResult_extract_image(args),
        category="satellite",
    )


def ToolResult_goes(args, is_hrit: bool):
    """handler 辅助：解析 VCDU 流并返回文件头摘要。"""
    import json as _json
    from .tool_registry import ToolResult  # 延迟导入避免循环

    parser = HRITParser() if is_hrit else LRITParser()
    files = []
    for hx in args.get("vcdus", []):
        raw = bytes.fromhex(hx)
        for f in parser.feed_vcdu(raw):
            h = parse_lrit_headers(f)
            files.append({
                "file_type": h.file_type,
                "annotation": h.annotation,
                "total_header_length": h.total_header_length,
                "data_length_bits": h.data_length_bits,
                "columns": h.columns,
                "lines": h.lines,
                "compression": h.compression,
                "image_identifier": h.image_identifier,
                "segment_number": h.segment_number,
                "max_segment": h.max_segment,
                "size_bytes": len(f),
            })
    return ToolResult(
        success=True,
        content=_json.dumps({"files": files, "symbol_rate": parser.symbol_rate},
                            ensure_ascii=False),
        data={"files": files, "symbol_rate": parser.symbol_rate},
    )


def ToolResult_extract_image(args):
    """handler 辅助：重组图像段。"""
    import json as _json
    from .tool_registry import ToolResult

    dec = GOESImageDecoder()
    images = []
    for hx in args.get("files", []):
        img = dec.add_lrit_file(bytes.fromhex(hx))
        if img is not None:
            images.append({
                "width": img.width,
                "height": img.height,
                "pixels": len(img.pixels),
                "annotation": img.annotation,
                "image_identifier": img.image_identifier,
            })
    return ToolResult(
        success=True,
        content=_json.dumps({"images": images}, ensure_ascii=False),
        data={"images": images},
    )
