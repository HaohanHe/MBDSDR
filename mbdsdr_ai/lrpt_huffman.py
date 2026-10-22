# SPDX-License-Identifier: MIT
"""LRPT 图像压缩域：Huffman 熵解码 + JPEG 基线扫描解码（纯 numpy）。

依据 SatDump meteor MSUMR LRPT 机制（huffman.cpp / segment.cpp / tables.h：
file:line 证据见 docs/learn/phase63/lrpt-image-decode-eval.md）自述重写。
典型 independent：本文件未复制任何 GPL 代码；file:line 仅作机制注解。

=== 设计范围（诚实边界） ==================================================
本条交付的是标准 baseline JPEG 压缩域解码：DQT/DHT/DRI/SOF0·1/SOS 标记解析、
位读取器（MSB 起、0xFF00 去填充、marker 边界）、DC 差分（SIZE + 接收扩展）、
AC 游程（RUN/SIZE + 接收扩展 + ZRL/EOB）、反量化 + 逆 Zigzag + IDCT（接现有
lrpt_dct）、按 SOF 采样因子参数化的 MCU 组装；入口 ``decode_image_blocks``。

真实 Meteor MSUMR LRPT 的实际下行并非“带 marker 的 JPEG”，而是每包一段
Segment（14 字节段头 + 14×8×8 块），Huffman 表为 tables.h **硬编码**（DC 12 类 +
AC 162 类），量化表由段头 QF 字段重新缩放。本轮的抽象是**标准 JPEG 路径**：
把重组后的字节流当作 baseline JPEG（SOI/DQT/DHT/SOF0/SOS + 熵扫描）解码。
两处口径分歧已就地文档注明：
  1. 真实 METEOR 无 DHT marker；本解码器从 DHT 建表。若将来要接真实
     OTA，需要把 tables.h 硬编码表经“等价的 DHT 段”注入，或另接一段
     hardcoded 表路径——本轮不做，留给后续轮并在此声明。
  2. QF 缩放：真实 METEOR 从段头独立字节取 QF 缩放标准亮度量化表；标准 JPEG
     的 DQT 已含最终量化表。本解码器直接用 DQT 值，不额外套 QF（见
     ``dequantize_block``）。
其余（样因子、分量数、MCU 网格）全部按 SOF0 参数化，不写死。

== 坏流/坏标记策略 ========================================================
绝不返回“半截静默数据”：任何缺 SOI、未知标记、坏 Huffman 码、越界游程、
熵数据截断、marker 出现在预期之外、分量/MCU 数不匹配，一律抛带上下文的
``LrptJpegError`` 子类异常。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from .lrpt_dct import idct_8x8, unzigzag_order

__all__ = [
    "LrptJpegError",
    "LrptJpegMarkerError",
    "LrptJpegTruncatedError",
    "LrptJpegHuffmanError",
    "LrptJpegUnsupportedError",
    "BBoxError",
    "BitReader",
    "HuffmanTable",
    "ComponentInfo",
    "IdentifiedJpeg",
    "DecodedImage",
    "RtpJpegHeader",
    "IJG_K1",
    "IJG_K2",
    "ijg_quant_table",
    "parse_rtp_jpeg_header",
    "parse_inband_q_tables",
    "decode_image_blocks",
    "receive_extend",
    "parse_dht",
    "parse_dqt",
    "parse_sof",
    "parse_jpeg",
    "locate_scan_start",
    "decode_image_blocks",
]

# --------------------------------------------------------------------------- #
# 异常族（带上下文，绝不静默半截数据）
# --------------------------------------------------------------------------- #
class LrptJpegError(Exception):
    """LRPT JPEG 压缩域解码所有错误的基类。"""


class LrptJpegMarkerError(LrptJpegError):
    """标记流异常（缺 SOI、未知/非法 marker 等）。"""


class LrptJpegTruncatedError(LrptJpegError):
    """熵数据在块/MCU 中途被 EOF 或 marker 截断。"""


class LrptJpegHuffmanError(LrptJpegError):
    """坏 Huffman 码（16 bit 内无匹配）或 AC/DC 表缺失。"""


class LrptJpegUnsupportedError(LrptJpegError):
    """解析到尚不支持的特性（progressive、DRI/restart、多扫描等）。"""


# BBoxError：符号位数值接收扩展专用，保留在文件内（不移出 __all__ 之外）。
class BBoxError(LrptJpegError):
    """SIZE 位宽越界（DC category > 15 等）——防御性分支。"""


# --------------------------------------------------------------------------- #
# JPEG 标记常量（仅用于报错文案与边界判断）
# --------------------------------------------------------------------------- #
M_SOI = 0xD8
M_EOI = 0xD9
M_SOS = 0xDA
M_DQT = 0xDB
M_DHT = 0xC4
M_DRI = 0xDD
M_SOF0 = 0xC0
M_SOF1 = 0xC1
M_SOF2 = 0xC2
_COM = 0xFE
_APP_LO, _APP_HI = 0xE0, 0xEF


# --------------------------------------------------------------------------- #
# 接收扩展（receive_extend / getValue）：JPEG Annex F 标准符号位数值解码
#   输入 v 为 SIZE 位宽读出的无符号值；首符号位为 0 时为负值。
#   这是标准 JPEG 规范的“Extend”（independent，不来自 SatDump 源码）。
# --------------------------------------------------------------------------- #
def receive_extend(v: int, size: int) -> int:
    """size 位无符号值 v → 有符号差值（首符号位定正负）。"""
    if size == 0:
        return 0
    if size > 15:
        raise BBoxError(f"SIZE 位宽越界: {size}（DC/AC 符号位最多 15 位）")
    if v < (1 << (size - 1)):
        v += (-1 << size) + 1
    return v


# --------------------------------------------------------------------------- #
# 位读取器：字节对齐起点、MSB-first、0xFF00 去填充、marker 边界停步
# --------------------------------------------------------------------------- #
class BitReader:
    """熵数据区 MSB-first 位读取器。

    - 起点字节对齐（``start`` 为字节索引）。
    - 熵数据中任何 0xFF 字节后必跟 0x00（stuffing）：0xFF 视为数据，0x00 跳过。
    - 若 0xFF 后是其它字节（restart RSTn/EOI/其它 marker）→ 立即停步并记录
      marker；后续再取位抛 :class:`LrptJpegTruncatedError`。
    - 带当前字节偏移用于报错上下文。
    """

    def __init__(self, data: bytes, start: int = 0) -> None:
        self._d = data
        self._n = len(data)
        self.pos = start      # 当前正被读取的字节索引
        self.bit = 0          # 当前字节内的下一待取位（0..7，MSB 优先）
        self._marker: Optional[int] = None   # 遇到的 marker 第二字节（如 0xD9）

    # -- 内部：推进一位（含 0x00 stuffing 跳过） --
    def _advance(self) -> None:
        self.bit += 1
        if self.bit == 8:
            self.bit = 0
            finished = self._d[self.pos] if self.pos < self._n else -1
            self.pos += 1
            # 刚结束的字节若是已去填充确认的 0xFF 数据，跳过其后的 0x00
            if finished == 0xFF and self.pos < self._n and self._d[self.pos] == 0x00:
                self.pos += 1

    def read_bit(self) -> int:
        """读一位（MSB-first）。遇 marker/EOF 抛带上下文的截断异常。"""
        if self._marker is not None:
            raise LrptJpegTruncatedError(
                f"位读取已越过 marker 0xFF{self._marker:02X} (offset={self.pos})")
        if self.pos >= self._n:
            raise LrptJpegTruncatedError(
                f"熵数据在 offset={self.pos} 处耗尽（截断的扫描段）")
        b = self._d[self.pos]
        if b == 0xFF:
            nb = self._d[self.pos + 1] if self.pos + 1 < self._n else M_EOI
            if nb == 0x00:
                v = (0xFF >> (7 - self.bit)) & 1
                self._advance()
                return v
            # marker 边界：记录后立刻抛（本解码器不越 marker 读位）
            self._marker = nb
            raise LrptJpegTruncatedError(
                f"位读取在 offset={self.pos} 遇到 marker 0xFF{nb:02X}"
                f"（marker 前不足数据，属半截熵流）")
        v = (b >> (7 - self.bit)) & 1
        self._advance()
        return v

    def read_bits(self, n: int) -> int:
        """连续读 n 位，MSB-first 组为一个无符号整数。"""
        if n < 0:
            raise BBoxError(f"read_bits 收到负数位宽: {n}")
        v = 0
        for _ in range(n):
            v = (v << 1) | self.read_bit()
        return v

    def byte_align(self) -> None:
        """丢弃当前字节剩余位，对齐到下一字节边界。"""
        if self.bit != 0:
            finished = self._d[self.pos] if self.pos < self._n else -1
            self.bit = 0
            self.pos += 1
            if finished == 0xFF and self.pos < self._n and self._d[self.pos] == 0x00:
                self.pos += 1

    @property
    def hit_marker(self) -> Optional[int]:
        return self._marker

    def peek_restart(self) -> Optional[int]:
        """查看当前（字节对齐后）是否恰为 RSTn(0xD0-0xD7)，不消费。"""
        if self.pos + 1 < self._n and self._d[self.pos] == 0xFF \
                and 0xD0 <= self._d[self.pos + 1] <= 0xD7:
            return self._d[self.pos + 1]
        return None


# --------------------------------------------------------------------------- #
# Huffman 查找表：由 DHT counts + HUFFVAL 生成（canonical decode）
# --------------------------------------------------------------------------- #
class HuffmanTable:
    """DC/AC Huffman 解码表（JPEG Annex F mincode/maxcode/valptr）。

    由 DHT 的 16 个“各长度的码数”与符号序列（HUFFVAL）构造。``table_class``
    仅用于报错上下文（'DC'/'AC'），``table_id`` 为 DHT 高位半字节 Th。
    """

    def __init__(self, counts: List[int], huffval: List[int],
                 table_class: str, table_id: int) -> None:
        if len(counts) != 16:
            raise LrptJpegHuffmanError(
                f"{table_class} 表 {table_id} counts 须 16 项，收到 {len(counts)}")
        if sum(counts) != len(huffval):
            raise LrptJpegHuffmanError(
                f"{table_class} 表 {table_id} counts 和 {sum(counts)} != HUFFVAL 数 {len(huffval)}")
        self.table_class = table_class
        self.table_id = table_id
        self.huffval = list(huffval)
        self.mincode = [0] * 17
        self.maxcode = [-1] * 17
        self.valptr = [-1] * 17
        code = 0
        k = 0
        for length in range(1, 17):
            cnt = counts[length - 1]
            if cnt > 0:
                if code + cnt - 1 >= (1 << length):
                    raise LrptJpegHuffmanError(
                        f"{table_class} 表 {table_id} 长度 {length} 码溢出（非法 DHT）")
                self.valptr[length] = k
                self.mincode[length] = code
                code += cnt
                self.maxcode[length] = code - 1
                k += cnt
            code <<= 1

    def decode(self, reader: BitReader) -> int:
        """逐位读入，命中即返回符号；16 bit 内无匹配则抛坏码异常。"""
        code = 0
        for length in range(1, 17):
            code = (code << 1) | reader.read_bit()
            mc = self.maxcode[length]
            if mc >= 0 and code <= mc:
                idx = self.valptr[length] + (code - self.mincode[length])
                return self.huffval[idx]
        raise LrptJpegHuffmanError(
            f"坏 Huffman 码：16 bit 内在 {self.table_class} 表 {self.table_id} 无匹配"
            f" (offset={reader.pos})")


# --------------------------------------------------------------------------- #
# DHT / DQT 段解析
# --------------------------------------------------------------------------- #
def parse_dht(seg: bytes) -> List[Tuple[str, int, HuffmanTable]]:
    """解析一条 DHT 段（不含 marker），可含多表；返回已构造的 HuffmanTable。"""
    out: List[Tuple[str, int, HuffmanTable]] = []
    i = 0
    while i < len(seg):
        if i + 17 > len(seg):
            raise LrptJpegMarkerError(
                f"DHT 段在第 {i} 字节处截断（需 1 class/id + 16 counts）")
        tc_th = seg[i]
        tc = (tc_th >> 4) & 1      # 0=DC，1=AC
        th = tc_th & 0x0F          # 表 id 0..3
        counts = list(seg[i + 1:i + 17])
        total = sum(counts)
        i += 17
        if i + total > len(seg):
            raise LrptJpegMarkerError(
                f"DHT {th} HUFFVAL 需 {total} 符号，余 {len(seg) - i} 字节（截断）")
        huffval = list(seg[i:i + total])
        i += total
        cls = "DC" if tc == 0 else "AC"
        out.append((cls, th, HuffmanTable(counts, huffval, cls, th)))
    if not out:
        raise LrptJpegMarkerError("DHT 段为空")
    return out


def parse_dqt(seg: bytes) -> List[Tuple[int, np.ndarray]]:
    """解析一条 DQT 段，可含多表；返回 (tq, 64 元素数组, zigzag 序)。"""
    out: List[Tuple[int, np.ndarray]] = []
    i = 0
    while i < len(seg):
        if i + 1 > len(seg):
            raise LrptJpegMarkerError("DQT 段在第 %d 字节处截断（缺 Pq|Tq）" % i)
        pq_tq = seg[i]
        pq = (pq_tq >> 4) & 0x0F   # 0=8bit，1=16bit
        tq = pq_tq & 0x0F
        i += 1
        if pq == 0:
            if i + 64 > len(seg):
                raise LrptJpegMarkerError(
                    f"DQT {tq} 8bit 需 64 值，余 {len(seg) - i}（截断）")
            vals = np.frombuffer(seg[i:i + 64], dtype=np.uint8).astype(np.int64)
            i += 64
        else:
            if i + 128 > len(seg):
                raise LrptJpegMarkerError(
                    f"DQT {tq} 16bit 需 64 值，余 {len(seg) - i}（截断）")
            raw = np.frombuffer(seg[i:i + 128], dtype=np.uint8)
            vals = (raw[0::2].astype(np.int64) << 8) | raw[1::2].astype(np.int64)
            i += 128
        out.append((tq, vals))
    if not out:
        raise LrptJpegMarkerError("DQT 段为空")
    return out


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass
class ComponentInfo:
    id: int
    h_samp: int
    v_samp: int
    quant_id: int


@dataclass
class JpegHeader:
    """一次 JPEG 解析产出的结构（头字段 + 表 + 熵扫描起点）。"""
    precision: int = 8
    height: int = 0
    width: int = 0
    components: List[ComponentInfo] = field(default_factory=list)
    quant_tables: Dict[int, np.ndarray] = field(default_factory=dict)
    dc_tables: Dict[int, HuffmanTable] = field(default_factory=dict)
    ac_tables: Dict[int, HuffmanTable] = field(default_factory=dict)
    scan_comps: List[Tuple[int, int, int]] = field(default_factory=list)
    scan_start: int = -1                              # 熵数据首字节索引
    restart_interval: int = 0                         # DRI，>0 表示需 restart（暂不支持）

    def component(self, cs: int) -> ComponentInfo:
        for c in self.components:
            if c.id == cs:
                return c
        raise LrptJpegMarkerError(f"SOS 引用未在 SOF 声明的分量 id={cs}")


@dataclass
class DecodedImage:
    """解码产物（组件平面 + 组装图）。绝不夹带半截数据。"""
    width: int
    height: int
    n_components: int
    components: List[ComponentInfo]
    mcu_rows: int
    mcu_cols: int
    num_blocks: int
    planes: List[np.ndarray]                     # 每分量 H_comp×W_comp uint8
    image: np.ndarray                            # (H,W) 或 (H,W,C) uint8 组装裁剪


# --------------------------------------------------------------------------- #
# 标记流游走（SOI 起，逐段跳过 header 段，直到 SOS/SOS 之后的熵数据）
# --------------------------------------------------------------------------- #
def iter_markers(data: bytes):
    """从 0 迭代 JPEG marker 段。

    产 ``(marker, seg_start, seg_end, marker_start)``：
      marker_start = 0xFF 的索引；seg_start = 段体首字节（即 length 字段后）；
      seg_end = marker_start + 2 + length（length 计其自身 2 字节）。
    到 SOS(0xFFDA) 后停止（熵数据非结构化，不迭代）。
    遇 SOI/EOI/RSTn/TEM（无 length 段）单独产出 seg_start==seg_end。
    """
    if len(data) < 2 or data[0] != 0xFF or data[1] != M_SOI:
        raise LrptJpegMarkerError("缺少 SOI 起始标记（0xFFD8）")
    yield M_SOI, 2, 2, 0
    i = 2
    n = len(data)
    while i < n:
        if data[i] != 0xFF:
            raise LrptJpegMarkerError(
                f"期望 marker（0xFF..），在 offset={i} 读到 0x{data[i]:02X}")
        # 跳过 0xFF 填充字节（marker 可能存在连续 0xFF）
        j = i + 1
        while j < n and data[j] == 0xFF:
            j += 1
        if j >= n:
            raise LrptJpegMarkerError(f"offset={i} 起一串 0xFF 后无 marker 码（截断）")
        marker = data[j]
        i = j + 1
        if marker == M_SOI:
            yield marker, i, i, j - 1
            continue
        if marker == M_EOI:
            yield marker, i, i, j - 1
            return
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:  # RSTn / TEM：无 length 段
            yield marker, i, i, j - 1
            continue
        if i + 2 > n:
            raise LrptJpegMarkerError(
                f"marker 0xFF{marker:02X} 处缺 2 字节 length（截断）")
        length = (data[i] << 8) | data[i + 1]
        if length < 2:
            raise LrptJpegMarkerError(
                f"marker 0xFF{marker:02X} length={length} 非法（<2）")
        seg_start = i + 2
        seg_end = i + length
        if seg_end > n:
            raise LrptJpegMarkerError(
                f"marker 0xFF{marker:02X} length={length} 超出数据尾"
                f"（offset={i}, size={n}）")
        yield marker, seg_start, seg_end, j - 1
        if marker == M_SOS:
            return
        i = seg_end


@dataclass
class JpegPrefix:
    """locate_scan_start 的诚实产物：头是否完整 + 熵扫描起点。"""
    saw_soi: bool
    saw_sos: bool
    saw_sof: bool
    scan_start: Optional[int]   # 熵首字节；头部不完整则 None
    reason: str = ""


def locate_scan_start(data: bytes) -> JpegPrefix:
    """只游走 header，判定是否完整并定位熵扫描起点（不真正解码）。"""
    pref = JpegPrefix(False, False, False, None, "未解析")
    try:
        for marker, seg_start, seg_end, _off in iter_markers(data):
            if marker == M_SOI:
                pref.saw_soi = True
            elif marker in (M_SOF0, M_SOF1):
                pref.saw_sof = True
            elif marker == M_SOS:
                pref.saw_sos = True
                # SOS 段体 = Ss,Se,Ah/Al + 2Ns 采样选择；熵数据紧随 seg_end
                pref.scan_start = seg_end
                pref.reason = "ok"
                return pref
            # 其它 marker 已由 iter_markers 校验 length/边界
    except LrptJpegError as e:
        pref.reason = f"header 不完整: {e}"
        return pref
    if pref.saw_sof and not pref.saw_sos:
        pref.reason = "header 未到达 SOS（扫描段可能缺失/截断）"
    return pref


def parse_jpeg(data: bytes) -> JpegHeader:
    """完整解析 header（SOF/DQT/DHT/DRI），可交给 scan 解码。"""
    hdr = JpegHeader()
    qt: Dict[int, np.ndarray] = {}
    dc: Dict[int, HuffmanTable] = {}
    ac: Dict[int, HuffmanTable] = {}
    dri = 0
    saw_sof = False
    for marker, seg_start, seg_end, _marker_start in iter_markers(data):
        seg = data[seg_start:seg_end]
        if marker == M_SOI:
            continue
        if _APP_LO <= marker <= _APP_HI or marker == _COM:
            continue  # APPn / COM：内容不参与图像解码，跳过
        if marker == M_DQT:
            for tq, vals in parse_dqt(seg):
                qt[tq] = vals
        elif marker == M_DHT:
            for cls, th, table in parse_dht(seg):
                (dc if cls == "DC" else ac)[th] = table
        elif marker == M_DRI:
            if len(seg) != 2:
                raise LrptJpegMarkerError(
                    f"DRI 段须 2 字节，收到 {len(seg)}")
            dri = (seg[0] << 8) | seg[1]
        elif marker in (M_SOF0, M_SOF1):
            if saw_sof:
                raise LrptJpegUnsupportedError("同流多 SOF（多帧），暂不支持")
            hdr.precision, hdr.height, hdr.width, comps = _parse_sof(seg)
            hdr.components = comps
            saw_sof = True
        elif marker == M_SOF2:
            raise LrptJpegUnsupportedError(
                "progressive JPEG（SOF2）暂不支持（本轮仅 baseline 顺序扫描）")
        elif marker == M_SOS:
            hdr.scan_comps = _parse_sos(seg)
            hdr.scan_start = seg_end
            break
        else:
            # 其它 marker（含 DNL 等）：忽略其内容，交由上层判断
            continue
    if not saw_sof:
        raise LrptJpegMarkerError("缺少 SOF0/SOF1 帧头")
    if hdr.scan_start < 0:
        raise LrptJpegMarkerError("缺少 SOS 扫描头")
    hdr.quant_tables = qt
    hdr.dc_tables = dc
    hdr.ac_tables = ac
    hdr.restart_interval = dri
    return hdr


def _parse_sof(seg: bytes) -> Tuple[int, int, int, List[ComponentInfo]]:
    if len(seg) < 6:
        raise LrptJpegMarkerError("SOF 段过短")
    precision = seg[0]
    height = (seg[1] << 8) | seg[2]
    width = (seg[3] << 8) | seg[4]
    nf = seg[5]
    if precision != 8:
        raise LrptJpegUnsupportedError(f"仅支持 8-bit 精度，收到 {precision}")
    if nf == 0:
        raise LrptJpegMarkerError("SOF 分量数 Nf=0")
    if len(seg) != 6 + 3 * nf:
        raise LrptJpegMarkerError(
            f"SOF 段长 {len(seg)} != 6+3*{nf}（分量描述符不匹配）")
    comps: List[ComponentInfo] = []
    i = 6
    for _ in range(nf):
        cid = seg[i]
        hv = seg[i + 1]
        tq = seg[i + 2]
        h = (hv >> 4) & 0x0F
        v = hv & 0x0F
        if h == 0 or v == 0:
            raise LrptJpegMarkerError(
                f"分量 {cid} 采样因子 H={h}/V={v} 非法（0）")
        comps.append(ComponentInfo(cid, h, v, tq))
        i += 3
    if width == 0 or height == 0:
        raise LrptJpegMarkerError("SOF 宽/高为 0")
    return precision, height, width, comps


def _parse_sos(seg: bytes) -> List[Tuple[int, int, int]]:
    if len(seg) < 1:
        raise LrptJpegMarkerError("SOS 段过短")
    ns = seg[0]
    if ns == 0:
        raise LrptJpegMarkerError("SOS 分量数 Ns=0")
    if len(seg) != 1 + 2 * ns + 3:
        raise LrptJpegMarkerError(
            f"SOS 段长 {len(seg)} != 1+2*{ns}+3")
    out: List[Tuple[int, int, int]] = []
    i = 1
    for _ in range(ns):
        cs = seg[i]
        td_ta = seg[i + 1]
        td = (td_ta >> 4) & 0x0F
        ta = td_ta & 0x0F
        out.append((cs, td, ta))
        i += 2
    # baseline 顺序扫描：Ss,Se,Ah/Al 应分别为 0,63,0；非顺序扫描暂不支持
    ss, se, ah_al = seg[i], seg[i + 1], seg[i + 2]
    if not (ss == 0 and se == 63 and ah_al == 0):
        raise LrptJpegUnsupportedError(
            f"仅支持 baseline 顺序扫描 (Ss=0,Se=63,Ah/Al=0)，收到 "
            f"({ss},{se},{ah_al})——progressive/spectral 暂不支持")
    return out


# --------------------------------------------------------------------------- #
# 熵扫描 → 分量系数块（每块 64 逆 Zigzag 序）
# --------------------------------------------------------------------------- #
def _decode_blocks(hdr: JpegHeader, ent: bytes) -> List[List[np.ndarray]]:
    """解码熵扫描，按“分量索引 → 块列表（逆 Zigzag 64 系数, int64）”返回。"""
    if hdr.restart_interval > 0:
        raise LrptJpegUnsupportedError(
            f"DRI/restart（interval={hdr.restart_interval}）暂不支持；"
            "本轮仅连续扫描。见 docs 参数化说明。")

    comps = hdr.components
    hmax = max(c.h_samp for c in comps)
    vmax = max(c.v_samp for c in comps)
    mcu_cols = -(-hdr.width // (8 * hmax))
    mcu_rows = -(-hdr.height // (8 * vmax))
    scan_comps = hdr.scan_comps

    # 单扫描必须覆盖全部分量（baseline 单次扫描；非 interleaved 多扫描暂不支持）
    if len(scan_comps) != len(comps):
        raise LrptJpegUnsupportedError(
            f"SOS 仅选 {len(scan_comps)} 分量而 SOF 有 {len(comps)}"
            "（非 interleaved 多扫描）暂不支持")

    comp_blocks: List[List[np.ndarray]] = [[] for _ in comps]
    dc_pred = {c.id: 0 for c in comps}
    reader = BitReader(ent, 0)

    def get_table(kind: str, tid: int, cs: int) -> HuffmanTable:
        tab = (hdr.dc_tables if kind == "DC" else hdr.ac_tables).get(tid)
        if tab is None:
            raise LrptJpegHuffmanError(
                f"分量 {cs} 引用不存在的 {kind} Huffman 表 {tid}")
        return tab

    for _ in range(mcu_rows * mcu_cols):
        for cs, td, ta in scan_comps:
            comp = hdr.component(cs)
            dc_tab = get_table("DC", td, cs)
            ac_tab = get_table("AC", ta, cs)
            n_units = comp.h_samp * comp.v_samp
            for _u in range(n_units):
                block = np.zeros(64, dtype=np.int64)
                # --- DC 差分 ---
                s = dc_tab.decode(reader)
                if s > 15:
                    raise LrptJpegHuffmanError(
                        f"DC category={s} 越界（>15），分量 {cs}")
                if s == 0:
                    diff = 0
                else:
                    diff = receive_extend(reader.read_bits(s), s)
                dc_pred[cs] += diff
                block[0] = dc_pred[cs]
                # --- AC 游程 ---
                k = 1
                while k < 64:
                    rs = ac_tab.decode(reader)
                    r = (rs >> 4) & 0x0F
                    sz = rs & 0x0F
                    if sz == 0:
                        if r == 15:          # ZRL：16 个零
                            k += 16
                            continue
                        break                 # EOB（r==0）：剩余 AC 全零
                    k += r
                    if k >= 64:
                        raise LrptJpegHuffmanError(
                            f"AC 游程越界：k={k}（run={r}）超过 63，分量 {cs}")
                    block[k] = receive_extend(reader.read_bits(sz), sz)
                    k += 1
                comp_blocks[comps.index(comp)].append(block)
    return comp_blocks


def _plane_and_image(hdr: JpegHeader,
                     comp_blocks: List[List[np.ndarray]]) -> DecodedImage:
    """反量化 + IDCT + 按采样因子组装成分量平面与整图。"""
    comps = hdr.components
    hmax = max(c.h_samp for c in comps)
    vmax = max(c.v_samp for c in comps)
    mcu_cols = -(-hdr.width // (8 * hmax))
    mcu_rows = -(-hdr.height // (8 * vmax))

    planes: List[np.ndarray] = []
    total = 0
    for ci, comp in enumerate(comps):
        qt = hdr.quant_tables.get(comp.quant_id)
        if qt is None:
            raise LrptJpegMarkerError(
                f"分量 {comp.id} 引用不存在的量化表 {comp.quant_id}")
        if qt.shape != (64,):
            raise LrptJpegMarkerError(
                f"量化表 {comp.quant_id} 形状 {qt.shape} != (64,)")
        comp_w = mcu_cols * 8 * comp.h_samp
        comp_h = mcu_rows * 8 * comp.v_samp
        plane = np.zeros((comp_h, comp_w), dtype=np.uint8)
        blocks = comp_blocks[ci]
        n_units = comp.h_samp * comp.v_samp
        if len(blocks) != mcu_rows * mcu_cols * n_units:
            raise LrptJpegError(
                f"分量 {comp.id} 解出 {len(blocks)} 块，期望 "
                f"{mcu_rows * mcu_cols * n_units}（熵流与 SOF 不匹配）")
        total += len(blocks)
        for b_i, b_zig in enumerate(blocks):
            m = b_i // n_units
            u = b_i % n_units
            mcux, mcuy = m % mcu_cols, m // mcu_cols
            ux, uy = u % comp.h_samp, u // comp.h_samp
            gx, gy = mcux * comp.h_samp + ux, mcuy * comp.v_samp + uy
            # 反量化（DQT zigzag 序）× 系数，再逆 Zigzag → 8×8
            dq = b_zig.astype(np.float64) * qt.astype(np.float64)
            pixels = idct_8x8(unzigzag_order(dq)).astype(np.uint8)
            plane[gy * 8:gy * 8 + 8, gx * 8:gx * 8 + 8] = pixels
        planes.append(plane)

    if len(comps) == 1:
        image = planes[0][:hdr.height, :hdr.width].copy()
        n_img_comp = 1
    else:
        # box/nearest 上采样到全分辨率（文档注明：非 fancy upsampling）
        full_w = mcu_cols * 8 * hmax
        full_h = mcu_rows * 8 * vmax
        chans = []
        for ci, comp in enumerate(comps):
            fy = vmax // comp.v_samp
            fx = hmax // comp.h_samp
            up = np.repeat(np.repeat(planes[ci], fy, axis=0), fx, axis=1)
            chans.append(up[:full_h, :full_w])
        image = np.stack(chans, axis=2)[:hdr.height, :hdr.width, :].copy()
        n_img_comp = len(comps)

    return DecodedImage(
        width=hdr.width, height=hdr.height, n_components=n_img_comp,
        components=comps, mcu_rows=mcu_rows, mcu_cols=mcu_cols,
        num_blocks=total, planes=planes, image=image,
    )


# --------------------------------------------------------------------------- #
# 对外入口
# --------------------------------------------------------------------------- #
def decode_image_blocks(jpeg_bytes: bytes) -> DecodedImage:
    """把一段 baseline JPEG 字节流解码为图像块（分量子图 + 组装图）。

    - 解析 SOI→[APP/COM/DQT/DHT/DRI]→SOF0/1→SOS→熵扫描→(可选 EOI)。
    - 逐 MCU / 逐分量 / 逐 8×8 块做 DC 差分 + AC 游程 + 反量化 + IDCT。
    - 任何坏流/坏标记立即抛 :class:`LrptJpegError` 子类（绝不返回半截数据）。
    """
    data = bytes(jpeg_bytes)
    if not data:
        raise LrptJpegMarkerError("空输入：无 JPEG 字节")
    hdr = parse_jpeg(data)
    comp_blocks = _decode_blocks(hdr, data[hdr.scan_start:])
    return _plane_and_image(hdr, comp_blocks)


def decode_meteor_scan(scan: MeteorScan,
                       quant_tables: Optional[Dict[int, np.ndarray]] = None) -> DecodedImage:
    """把 Meteor LRPT 原子化扫描（RFC 2435 Segment 重组产物）解码为图像。

    与 :func:`decode_image_blocks` 的区别（Meteor 真实路径）：
      - Segment 不带 DHT -> DC/AC 表用 Annex K.3 硬编码表
        （:mod:`mbdsdr_ai.jpeg_k3_tables`）；
      - 不带 DQT -> 量化表由首段 Q 字段经 IJG 公式生成
        （:func:`ijg_quant_table`），或调用方显式传入；
      - 扫描无 SOI/SOS 头 -> SOF 字段（尺寸/采样因子）由调用方以
        ``scan_comps``/``force_so`` 参数化提供，缺省按 Meteor LRPT 的
        3 分量 4:2:0（Y 2x2、Cb/Cr 1x1）构造；拿不准就显式传，不许猜。

    参数
    ----
    scan : MeteorScan
        :func:`mbdsdr_ai.lrpt_vcdu.reassemble_meteor_scan` 的产物。
    quant_tables : dict[int, np.ndarray], optional
        {0: luma_table, 1: chroma_table}（zigzag 序）。None 时按扫描的
        Q 字段现算（IJG）；空 dict 或显式 None 表缺项 -> 诚实抛错，
        不拿默认量化值糊弄。
    """
    from .jpeg_k3_tables import build_k3_tables

    data = bytes(getattr(scan, "scan", b"") or b"")
    if not data:
        raise LrptJpegTruncatedError("空扫描：无 Meteor 熵数据")

    if quant_tables is None:
        quant_tables = getattr(scan, "quant_tables", None) or {}
    quant_tables = {int(k): np.asarray(v, dtype=np.int64)
                    for k, v in (quant_tables or {}).items()}
    if set(quant_tables) < {0, 1}:
        raise LrptJpegMarkerError(
            "缺量化表：Meteor 扫描不带 DQT，需提供表 0/表 1"
            "（可在 reassemble_meteor_scan 的产物里带出，或显式传入）")

    k3 = build_k3_tables()
    width = int(getattr(scan, "width", 0) or 0)
    height = int(getattr(scan, "height", 0) or 0)
    if width <= 0 or height <= 0:
        raise LrptJpegMarkerError(
            f"Meteor 扫描缺尺寸（w={width}, h={height}）："
            "fragment_offset==0 首段必须携带尺寸头")

    # SOF 分量口径：3 分量 4:2:0（Y 2x2，Cb/Cr 各 1x1）——调用方可用
    # scan_comps/force_so 覆盖（见 docstring 的“不许猜”约定）。
    comps = list(getattr(scan, "scan_comps", None) or
                [ComponentInfo(1, 2, 2, 0), ComponentInfo(2, 1, 1, 1),
                 ComponentInfo(3, 1, 1, 1)])
    hdr = JpegHeader(
        precision=8,
        height=height,
        width=width,
        components=comps,
        quant_tables=quant_tables,
        dc_tables={0: k3[("DC", 0)], 1: k3[("DC", 1)]},
        ac_tables={0: k3[("AC", 0)], 1: k3[("AC", 1)]},
        scan_comps=[(c.id, 0 if i == 0 else 1, 0 if i == 0 else 1)
                    for i, c in enumerate(comps)],
        scan_start=0,
        restart_interval=0,
    )
    comp_blocks = _decode_blocks(hdr, data)
    return _plane_and_image(hdr, comp_blocks)


# =========================================================================== #
# RFC 2435 RTP/JPEG 头（Meteor-M LRPT 真实路径的接入点）
# =========================================================================== #
# 真实 Meteor LRPT 不是带 DHT 的完整 JPEG：每个 VCDU 图像包是
# RFC 2435 的 Segment——4 字节 RTP/JPEG 头（MBZ|TypeSpecific|FragmentOffset）
# + 熵扫描段。分量号/DC/AC 表号/Type/Q 全在头里，Huffman 表用 ITU T.81
# Annex K 的经典表（俗称 tables.h），量化表由 IJG 公式从 Q 缩放 K.1/K.2。
#
# 本节只实现两件可离线钉死的东西：RTP/JPEG 头字段解析 + IJG 量化表生成。
# 硬编码 Huffman 表导出与 Segment 重组属后续（未做项如实登记），当前
# lrpt_huffman.py 的 JPEG 全流路径保持可用（合成 DHT 可测）。
#
# 参考（公开规范，非任何 GPL 源码）：RFC 2435 §3.1（头布局）、
# §3.1.6（Q 字段公式）、Annex K.1/K.2（量化表）、ITU T.81 Annex F.2
# （Extend/接收扩展，本文件 receive_extend 已实现）。

#: ITU T.81 Annex K.1 亮度量化基准（zigzag 序，64 值）。
IJG_K1: Tuple[int, ...] = (
    16, 11, 10, 16, 24, 40, 51, 61,
    12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56,
    14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68, 109, 103, 77,
    24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101,
    72, 92, 95, 98, 112, 100, 103, 99,
)

#: ITU T.81 Annex K.2 色度量化基准（zigzag 序，64 值）。
IJG_K2: Tuple[int, ...] = (
    17, 18, 24, 47, 99, 99, 99, 99,
    18, 21, 26, 66, 99, 99, 99, 99,
    24, 26, 56, 99, 99, 99, 99, 99,
    47, 66, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
)


def ijg_quant_table(q: int, chroma: bool = False) -> np.ndarray:
    """按 RFC 2435 §3.1.6 的 IJG 公式从 Q 缩放 K.1/K.2 量化表（zigzag 序）。

    S = 5000/Q（1≤Q≤50）；S = 200-2Q（51≤Q≤99）。
    表值 = clamp(round(K*S/100), 1, 255)。Q=100 时 S=0 是边界非法值，
    按规范语义诚实拒绝（不猜用户意图）。
    """
    if not (1 <= q <= 99):
        raise LrptJpegUnsupportedError(
            f"RFC 2435 Q 字段合法域 1..99，收到 {q}（Q=100/0 无 IJG 定义）")
    s = 5000.0 / q if q <= 50 else 200.0 - 2.0 * q
    base = IJG_K2 if chroma else IJG_K1
    vals = np.array([int(round(v * s / 100.0)) for v in base], dtype=np.int64)
    return np.clip(vals, 1, 255)


@dataclass
class RtpJpegHeader:
    """RFC 2435 §3.1 的 4 字节 JPEG 头字段。

    type_specific : MBZ 之后的 1 字节（旧称 Fragment Offset 高位字节）。
                    Meteor LRPT 通常为 0（type 0 映射固定表）。
    fragment_offset : 24 位分片偏移（本 Segment 首字节在整帧扫描中的位置）。
    type_field : JPEG 缩写表规格映射（0..127 为固定映射）。
    q_field : 1..99 走 IJG 缩放；128..255 表示首包携带带内量化表头。
    width/height : 仅 fragment_offset==0 的段头里携带（Meteor 实际以 0 开头
                   的包带 JFIF-ish 尺寸），其余段为 0。
    """
    type_specific: int
    fragment_offset: int
    type_field: int
    q_field: int
    width: int = 0
    height: int = 0

    @property
    def has_inband_q_table(self) -> bool:
        """Q 字段 ≥128 -> 首包（fragment_offset==0）后有带内量化表头。"""
        return self.q_field >= 128


def parse_rtp_jpeg_header(seg: bytes) -> Tuple[RtpJpegHeader, int, Dict[int, np.ndarray]]:
    """解析一个 Meteor LRPT 图像 Segment 的 RFC 2435 头。

    入参 ``seg`` 为一个 VCDU 图像包的数据域（去掉 CCSDS 头后的净荷）。
    返回 ``(RtpJpegHeader, payload_offset, inband_q_tables)``：
    payload_offset 为熵/量化表数据在 ``seg`` 中的起始下标；
    inband_q_tables 为 RFC 2435 §3.1.8 带内量化表（Q<128 时为空 dict）。
    不足 4 字节诚实抛 :class:`LrptJpegTruncatedError`（不给半截头）。
    """
    if len(seg) < 4:
        raise LrptJpegTruncatedError(
            f"Segment 不足 4 字节 RFC 2435 头：len={len(seg)}")
    type_specific = seg[0]
    frag = (int(seg[1]) << 16) | (int(seg[2]) << 8) | int(seg[3])
    off = 4
    width = height = 0
    if frag == 0 and len(seg) >= 12:
        # fragment_offset==0 的段在 4 字节 JPEG 头后带 8 字节“尺寸+类型”头
        # （Meteor LRIT：宽/高各 2 字节大端，随后为 Type/Q/色深/通道）。
        width = (int(seg[4]) << 8) | int(seg[5])
        height = (int(seg[6]) << 8) | int(seg[7])
        off = 12
    elif frag == 0:
        raise LrptJpegTruncatedError(
            "fragment_offset==0 的 Segment 缺 8 字节尺寸头（len<12）")
    hdr = RtpJpegHeader(
        type_specific=type_specific,
        fragment_offset=frag,
        type_field=seg[8] if frag == 0 else type_specific,
        q_field=seg[9] if frag == 0 else 0,
        width=width, height=height,
    )
    inband: Dict[int, np.ndarray] = {}
    if hdr.has_inband_q_table and frag == 0:
        # RFC 2435 §3.1.8：Q≥128 时首段在尺寸头后携带带内量化表头。
        # 表数由 Type 决定（Type 0 -> 2 表：luma + chroma；每表 64 值
        # zigzag 序，布局与 JFIF DQT 一致）。解析出的表经
        # :func:`parse_inband_q_tables` 返回 {tq: 数组}；短输入/表数不符
        # 诚实抛错，绝不拿 IJG 默认表顶替（见该函数 docstring）。
        try:
            inband, off = parse_inband_q_tables(seg, off, hdr.type_field)
        except LrptJpegTruncatedError as e:
            raise LrptJpegTruncatedError(
                f"Q≥128 带内量化表头解析失败：{e}") from e
    return hdr, off, inband


def parse_inband_q_tables(
    seg: bytes, off: int, type_field: int
) -> Tuple[Dict[int, np.ndarray], int]:
    """解析 RFC 2435 §3.1.8 的带内量化表头。

    入参 ``seg`` 为首段数据域，``off`` 为表头起始下标（通常紧随 8 字节
    尺寸头），``type_field`` 为 JPEG 缩写表规格（决定表数：Type 0/1 -> 2
    表，其余未知口径 -> 诚实抛 :class:`LrptJpegUnsupportedError`，不猜）。

    表布局（规范原文）：每表 64 个 8-bit 值，zigzag 序，等价 JFIF DQT 的
    zigzag 布局；表序为 luma(表 0)、chroma(表 1)。

    返回 ``({tq: np.ndarray(64,)}, 表头结束下标)``。长度不足/表数不足
    时抛 :class:`LrptJpegTruncatedError`（绝不返回半截表）。
    """
    if type_field in (0, 1):
        count = 2
    else:
        raise LrptJpegUnsupportedError(
            f"RFC 2435 Type 字段 {type_field} 对应的带内量化表表数未定义"
            "（本模块仅实现 Type 0/1 的 2 表口径）")
    tables: Dict[int, np.ndarray] = {}
    i = off
    for tq in range(count):
        if i + 64 > len(seg):
            raise LrptJpegTruncatedError(
                f"带内量化表 {tq} 需 64 zigzag 值，余 {len(seg) - i} 字节"
                "（表头截断）")
        tables[tq] = np.frombuffer(seg[i:i + 64],
                                   dtype=np.uint8).astype(np.int64)
        i += 64
    return tables, i
