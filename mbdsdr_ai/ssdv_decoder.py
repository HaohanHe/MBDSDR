# SPDX-License-Identifier: MIT
"""fsphil SSDV（Slow Scan Digital Video）256 字节包解码器 / 编码器 —— 干净室实现。

本模块依据权威规格 ``docs/learn/phase14/SSDV_SSTV_SPEC.md``（§3 包格式 / §5 任务 /
§6 验收）独立编写；参考 ``repos/ssdv_ref``（fsphil/ssdv，GPL）仅学习机制，**未逐字
复制任何 GPL 代码**。JPEG 表为 ITU-T T.81 Annex K 公开标准采样表。

包布局（固定 256 字节，Normal/FEC 模式）::

    0        sync      0x55
    1        type      0x66 = Normal(FEC)，0x67 = No-FEC
    2..5     callsign  base-40（大端，≤6 字符）
    6        image_id
    7..8     packet_id（大端）
    9        width/16   MCU 列数
    10       height/16  MCU 行数
    11       flags     bits0..1 = mcu_mode；bit2 = EOI；bits3..5 = quality^4
    12       packet_mcu_offset（本包首个字节对齐 MCU 的载荷内偏移）
    13..14   packet_mcu_id（大端；0xFFFF = 本包无新 MCU）
    15..219  payload   205 字节（No-FEC 模式为 237，15..251）
    220..223 CRC32     覆盖 byte[1]..payload 末，标准反射 CRC-32（大端存放）
    224..255 RS(255,223) 校验（仅 Normal 模式）

RS 约定（已对照编译后的 rs8.c 逐字节核实）：
  - 码字 = ``byte[1..255]`` 连续 255 字节（**从 type 字节起，不含 sync**），
    前 223 消息（头[1..14] + 载荷[15..219] + CRC[220..223]）+ 后 32 校验。
  - ``pad = 0``：消息恰好填满 223 字节，**无前置零填充**。
  - 参数：CCSDS RS(255,223)，nsym=32，本原多项式 0x187，根生成元 gamma=alpha^11，
    首根指数 112（根 gamma^(112+i)），**不做 0xFF 字节反转**。纠错能力 t=16。

红线：
  - 不内置任何呼号；callsign 一律由参数传入。
  - 缺失 MCU 诚实报告（不造假数据）；无数据为空态。
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .fec import _GF256

# ---------------------------------------------------------------------------
# 协议常量
# ---------------------------------------------------------------------------
SYNC: int = 0x55
TYPE_NORMAL: int = 0x66     # 带 FEC
TYPE_NOFEC: int = 0x67      # 无 FEC
PACKET_LEN: int = 256
HEADER_LEN: int = 15
CRC_LEN: int = 4
RS_LEN: int = 32
PAYLOAD_FEC: int = PACKET_LEN - HEADER_LEN - CRC_LEN - RS_LEN     # 205
PAYLOAD_NOFEC: int = PACKET_LEN - HEADER_LEN - CRC_LEN           # 237
RS_MSG_LEN: int = 223
OFFSET_CRC_FEC: int = HEADER_LEN + PAYLOAD_FEC                   # 220
OFFSET_CRC_NOFEC: int = HEADER_LEN + PAYLOAD_NOFEC               # 252

# mcu_mode -> 每 MCU 的 8x8 亮度块数（色度固定各 1 块）
MCU_MODE_BLOCKS: Dict[int, int] = {0: 4, 1: 2, 2: 2, 3: 1}

# ---------------------------------------------------------------------------
# JPEG Annex K 公开标准表（ITUT T.81 Annex K.3 采样表）
# ---------------------------------------------------------------------------
_APP0: bytes = bytes([
    0x4A, 0x46, 0x49, 0x46, 0x00, 0x01, 0x01, 0x01,
    0x00, 0x48, 0x00, 0x48, 0x00, 0x00,
])
_SOS_DATA: bytes = bytes([0x03, 0x01, 0x00, 0x02, 0x11, 0x03, 0x11, 0x00, 0x3F, 0x00])
_DQT_SCALES: Tuple[int, ...] = (5000, 357, 172, 116, 100, 58, 28, 0)

_STD_DQT0: bytes = bytes([
    0x00,
    0x10, 0x0C, 0x0C, 0x0E, 0x0C, 0x0A, 0x10, 0x0E, 0x0E, 0x0E, 0x12, 0x12, 0x10, 0x14, 0x18,
    0x28, 0x1A, 0x18, 0x16, 0x16, 0x18, 0x32, 0x24, 0x26, 0x1E, 0x28, 0x3A, 0x34, 0x3E, 0x3C,
    0x3A, 0x34, 0x38, 0x38, 0x40, 0x48, 0x5C, 0x4E, 0x40, 0x44, 0x58, 0x46, 0x38, 0x38, 0x50,
    0x6E, 0x52, 0x58, 0x60, 0x62, 0x68, 0x68, 0x68, 0x3E, 0x4E, 0x72, 0x7A, 0x70, 0x64, 0x78,
    0x5C, 0x66, 0x68, 0x64,
])
_STD_DQT1: bytes = bytes([
    0x01,
    0x12, 0x12, 0x12, 0x16, 0x16, 0x16, 0x30, 0x1A, 0x1A, 0x30, 0x64, 0x42, 0x38, 0x42, 0x64,
    0x64, 0x64, 0x64, 0x64, 0x64, 0x64, 0x64, 0x64, 0x64, 0x64, 0x64, 0x64, 0x64, 0x64, 0x64,
    0x64, 0x64, 0x64, 0x64, 0x64, 0x64, 0x64, 0x64, 0x64, 0x64, 0x64, 0x64, 0x64, 0x64, 0x64,
    0x64, 0x64, 0x64, 0x64, 0x64, 0x64, 0x64, 0x64, 0x64, 0x64, 0x64, 0x64, 0x64, 0x64, 0x64,
    0x64, 0x64, 0x64, 0x64,
])
_STD_DHT_DC_LUMA: bytes = bytes([
    0x00, 0x01, 0x05, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B,
])
_STD_DHT_DC_CHROMA: bytes = bytes([
    0x00, 0x03, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B,
])
_STD_DHT_AC_LUMA: bytes = bytes([
    0x00, 0x02, 0x01, 0x03, 0x03, 0x02, 0x04, 0x03, 0x05, 0x05, 0x04, 0x04, 0x00, 0x00, 0x01, 0x7D,
    0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41, 0x06, 0x13, 0x51, 0x61,
    0x07, 0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xA1, 0x08, 0x23, 0x42, 0xB1, 0xC1, 0x15, 0x52, 0xD1,
    0xF0, 0x24, 0x33, 0x62, 0x72, 0x82, 0x09, 0x0A, 0x16, 0x17, 0x18, 0x19, 0x1A, 0x25, 0x26, 0x27,
    0x28, 0x29, 0x2A, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3A, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48,
    0x49, 0x4A, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5A, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68,
    0x69, 0x6A, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79, 0x7A, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88,
    0x89, 0x8A, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6,
    0xA7, 0xA8, 0xA9, 0xAA, 0xB2, 0xB3, 0xB4, 0xB5, 0xB6, 0xB7, 0xB8, 0xB9, 0xBA, 0xC2, 0xC3, 0xC4,
    0xC5, 0xC6, 0xC7, 0xC8, 0xC9, 0xCA, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0xD9, 0xDA, 0xE1,
    0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8, 0xE9, 0xEA, 0xF1, 0xF2, 0xF3, 0xF4, 0xF5, 0xF6, 0xF7,
    0xF8, 0xF9, 0xFA,
])
_STD_DHT_AC_CHROMA: bytes = bytes([
    0x00, 0x02, 0x01, 0x02, 0x04, 0x04, 0x03, 0x04, 0x07, 0x05, 0x04, 0x04, 0x00, 0x01, 0x02, 0x77,
    0x00, 0x01, 0x02, 0x03, 0x11, 0x04, 0x05, 0x21, 0x31, 0x06, 0x12, 0x41, 0x51, 0x07, 0x61,
    0x71, 0x13, 0x22, 0x32, 0x81, 0x08, 0x14, 0x42, 0x91, 0xA1, 0xB1, 0xC1, 0x09, 0x23, 0x33, 0x52,
    0xF0, 0x15, 0x62, 0x72, 0xD1, 0x0A, 0x16, 0x24, 0x34, 0xE1, 0x25, 0xF1, 0x17, 0x18, 0x19, 0x1A,
    0x26, 0x27, 0x28, 0x29, 0x2A, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3A, 0x43, 0x44, 0x45, 0x46, 0x47,
    0x48, 0x49, 0x4A, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5A, 0x63, 0x64, 0x65, 0x66, 0x67,
    0x68, 0x69, 0x6A, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79, 0x7A, 0x82, 0x83, 0x84, 0x85, 0x86,
    0x87, 0x88, 0x89, 0x8A, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0xA2, 0xA3, 0xA4,
    0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xB2, 0xB3, 0xB4, 0xB5, 0xB6, 0xB7, 0xB8, 0xB9, 0xBA, 0xC2,
    0xC3, 0xC4, 0xC5, 0xC6, 0xC7, 0xC8, 0xC9, 0xCA, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0xD9,
    0xDA, 0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8, 0xE9, 0xEA, 0xF2, 0xF3, 0xF4, 0xF5, 0xF6, 0xF7,
    0xF8, 0xF9, 0xFA,
])

_ZIGZAG: Tuple[int, ...] = (
    0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5,
    12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63,
)


# ---------------------------------------------------------------------------
# Reed-Solomon（fsphil 约定；复用 fec._GF256 字段表，根生成元 gamma=alpha^11）
# ---------------------------------------------------------------------------
class _SsdvRS:
    """fsphil/rs8.c 兼容 RS(255,223) 系统编/解码（干净室，BM+Chien+Forney）。

    字段表复用 :class:`mbdsdr_ai.fec._GF256`（本原多项式 0x187，已与 rs8.c
    ALPHA_TO 表逐字节一致）。根集 beta_i = gamma^(112+i)，gamma = alpha^11。
    """

    _FCR = 112
    _PRIM = 11
    _NSYM = 32

    def __init__(self) -> None:
        self.gf = _GF256(0x187)
        g = self.gf
        self._roots = [g.exp[(self._PRIM * (self._FCR + i)) % 255] for i in range(self._NSYM)]
        poly = [1]
        for rt in self._roots:
            nxt = [0] * (len(poly) + 1)
            for j, c in enumerate(poly):
                nxt[j] ^= g.mul(c, rt)
                nxt[j + 1] ^= c
            poly = nxt
        self._gen = poly

    def encode(self, msg: bytes) -> bytes:
        if len(msg) != 223:
            raise ValueError(f"RS 编码需要 223 字节消息，收到 {len(msg)}")
        g = self.gf
        reg = [0] * self._NSYM
        for d in msg:
            fb = d ^ reg[0]
            reg = reg[1:] + [0]
            if fb:
                li = g.log[fb]
                for j in range(self._NSYM):
                    reg[j] ^= g.mul(self._gen[self._NSYM - 1 - j], g.exp[li])
        return bytes(reg)

    def decode(self, cw: bytes) -> Tuple[bytes, int]:
        if len(cw) != 255:
            raise ValueError(f"RS 解码需要 255 字节码字，收到 {len(cw)}")
        g = self.gf
        nroot = self._NSYM
        r = list(cw)
        S = [0] * nroot
        for i in range(nroot):
            acc = 0
            for byte in r:
                acc = g.mul(acc, self._roots[i]) ^ byte
            S[i] = acc
        if not any(S):
            return bytes(r[:223]), 0
        C = [1]
        B = [1]
        L = 0
        m = 1
        b = 1
        for n in range(nroot):
            d = S[n]
            for i in range(1, L + 1):
                d ^= g.mul(C[i], S[n - i])
            if d == 0:
                m += 1
            elif 2 * L <= n:
                T = C[:]
                coef = g.div(d, b)
                need = len(B) + m
                if len(C) < need:
                    C += [0] * (need - len(C))
                for i in range(len(B)):
                    C[i + m] ^= g.mul(coef, B[i])
                L = n + 1 - L
                B = T
                b = d
                m = 1
            else:
                coef = g.div(d, b)
                need = len(B) + m
                if len(C) < need:
                    C += [0] * (need - len(C))
                for i in range(len(B)):
                    C[i + m] ^= g.mul(coef, B[i])
                m += 1

        def gam(q: int) -> int:
            return g.exp[(self._PRIM * q) % 255]

        errpos: List[int] = []
        for p in range(255):
            base = gam(p)
            val = 0
            xn = 1
            for i in range(len(C)):
                val ^= g.mul(C[i], xn)
                xn = g.mul(xn, base)
            if val == 0:
                errpos.append((p - 1) % 255)
        nerr = len(errpos)
        if nerr == 0 or nerr > nroot // 2:
            return bytes(r[:223]), -1
        omega = [0] * nroot
        for i in range(nroot):
            acc = 0
            for j in range(min(i + 1, len(C))):
                acc ^= g.mul(S[i - j], C[j])
            omega[i] = acc
        out = list(r)
        for pos in errpos:
            Xk = gam((254 - pos) % 255)
            Xk_inv = g.inverse(Xk)
            num = 0
            xn = 1
            for i in range(len(omega)):
                num ^= g.mul(omega[i], xn)
                xn = g.mul(xn, Xk_inv)
            den = 0
            xn = 1
            for i in range(1, len(C)):
                if i % 2 == 1:
                    den ^= g.mul(C[i], xn)
                xn = g.mul(xn, Xk_inv)
            if den == 0:
                return bytes(r[:223]), -1
            mag = g.div(num, den)
            mag = g.mul(mag, g.pow(Xk, 1 - self._FCR))
            out[pos] ^= mag
        return bytes(out[:223]), nerr


# ---------------------------------------------------------------------------
# base-40 呼号
# ---------------------------------------------------------------------------
def encode_callsign(callsign: str) -> int:
    x = 0
    for ch in reversed(callsign[:6]):
        x *= 40
        if 'A' <= ch <= 'Z':
            x += ord(ch) - ord('A') + 14
        elif 'a' <= ch <= 'z':
            x += ord(ch) - ord('a') + 14
        elif '0' <= ch <= '9':
            x += ord(ch) - ord('0') + 1
    return x & 0xFFFFFFFF


def decode_callsign(code: int) -> str:
    if code > 0xF423FFFF:
        return ""
    out = []
    while code:
        s = code % 40
        if s == 0:
            out.append('-')
        elif s < 11:
            out.append(chr(ord('0') + s - 1))
        elif s < 14:
            out.append('-')
        else:
            out.append(chr(ord('A') + s - 14))
        code //= 40
    return "".join(out)


def _crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


# ---------------------------------------------------------------------------
# 解析后的包
# ---------------------------------------------------------------------------
@dataclass
class SsdvPacket:
    pkt_type: int
    callsign: str
    callsign_code: int
    image_id: int
    packet_id: int
    width_px: int
    height_px: int
    flags: int
    eoi: bool
    quality: int
    mcu_mode: int
    mcu_offset: int
    mcu_id: int
    payload: bytes
    nerrors: int = -1

    @property
    def mcu_count(self) -> int:
        cols = self.width_px >> 4
        rows = self.height_px >> 4
        if self.mcu_mode == 0:
            return cols * rows
        if self.mcu_mode in (1, 2):
            return cols * rows * 2
        return cols * rows * 4


class SsdvDecoder:
    """字节流同步 + RS 纠错 + CRC 把关。"""

    def __init__(self) -> None:
        self._rs = _SsdvRS()
        self._buf = bytearray()

    def correct_packet(self, chunk: bytes) -> Optional[SsdvPacket]:
        if len(chunk) != PACKET_LEN or chunk[0] != SYNC:
            return None
        ptype = chunk[1]
        if ptype not in (TYPE_NORMAL, TYPE_NOFEC):
            return None
        raw = bytearray(chunk)
        nerrors = -1
        if ptype == TYPE_NORMAL:
            data, nerrors = self._rs.decode(bytes(raw[1:PACKET_LEN]))
            if nerrors < 0:
                return None
            raw[1:1 + RS_MSG_LEN] = data
            crc_off = OFFSET_CRC_FEC
            payload_len = PAYLOAD_FEC
        else:
            crc_off = OFFSET_CRC_NOFEC
            payload_len = PAYLOAD_NOFEC
        stored = int.from_bytes(raw[crc_off:crc_off + 4], "big")
        if _crc32(bytes(raw[1:crc_off])) != stored:
            return None
        callsign_code = (raw[2] << 24) | (raw[3] << 16) | (raw[4] << 8) | raw[5]
        return SsdvPacket(
            pkt_type=ptype,
            callsign=decode_callsign(callsign_code),
            callsign_code=callsign_code,
            image_id=raw[6],
            packet_id=(raw[7] << 8) | raw[8],
            width_px=raw[9] << 4,
            height_px=raw[10] << 4,
            flags=raw[11],
            eoi=bool((raw[11] >> 2) & 1),
            quality=((raw[11] >> 3) & 7) ^ 4,
            mcu_mode=raw[11] & 3,
            mcu_offset=raw[12],
            mcu_id=(raw[13] << 8) | raw[14],
            payload=bytes(raw[HEADER_LEN:HEADER_LEN + payload_len]),
            nerrors=nerrors,
        )

    def feed(self, data: bytes) -> List[Optional[SsdvPacket]]:
        self._buf.extend(data)
        out: List[Optional[SsdvPacket]] = []
        while True:
            idx = -1
            for i in range(len(self._buf) - 1):
                if self._buf[i] == SYNC and self._buf[i + 1] in (TYPE_NORMAL, TYPE_NOFEC):
                    idx = i
                    break
            if idx < 0:
                if len(self._buf) > 1:
                    del self._buf[:-1]
                break
            if idx > 0:
                del self._buf[:idx]
            if len(self._buf) < PACKET_LEN:
                break
            chunk = bytes(self._buf[:PACKET_LEN])
            del self._buf[:PACKET_LEN]
            out.append(self.correct_packet(chunk))
        return out


# ---------------------------------------------------------------------------
# Huffman 表（Annex K 固定表）
# ---------------------------------------------------------------------------
class _HuffTable:
    def __init__(self, table: bytes) -> None:
        counts = table[0:16]
        syms = list(table[16:16 + sum(counts)])
        self.sym_to_code: Dict[int, Tuple[int, int]] = {}
        self.code_to_sym: Dict[Tuple[int, int], int] = {}
        code = 0
        si = 0
        for length in range(1, 17):
            for _ in range(counts[length - 1]):
                self.sym_to_code[syms[si]] = (code, length)
                self.code_to_sym[(code, length)] = syms[si]
                code += 1
                si += 1
            code <<= 1
        self._counts = counts

    def decode(self, peek_msb: int, avail: int) -> Optional[Tuple[int, int]]:
        code = 0
        for length in range(1, 17):
            if length > avail:
                return None
            code = (code << 1) | ((peek_msb >> (avail - length)) & 1)
            key = (code, length)
            if key in self.code_to_sym:
                return self.code_to_sym[key], length
        return None

    def encode(self, symbol: int) -> Tuple[int, int]:
        return self.sym_to_code[symbol]


_TBL_DC_LUMA = _HuffTable(_STD_DHT_DC_LUMA)
_TBL_DC_CHROMA = _HuffTable(_STD_DHT_DC_CHROMA)
_TBL_AC_LUMA = _HuffTable(_STD_DHT_AC_LUMA)
_TBL_AC_CHROMA = _HuffTable(_STD_DHT_AC_CHROMA)


def _build_dqt(table: bytes, quality: int) -> bytes:
    quality = max(0, min(7, quality))
    scale = _DQT_SCALES[quality]
    out = bytearray(65)
    out[0] = table[0]
    for i in range(64):
        v = (table[1 + i] * scale + 50) // 100
        out[1 + i] = 1 if v == 0 else min(255, v)
    return bytes(out)


def _build_jpeg_header(width: int, height: int, quality: int, mcu_mode: int) -> bytes:
    mcu_mode &= 3
    sof_factor = {0: 0x22, 1: 0x12, 2: 0x21, 3: 0x11}[mcu_mode]
    hdr = bytearray()
    hdr += b"\xFF\xD8"
    hdr += b"\xFF\xE0" + (len(_APP0) + 2).to_bytes(2, "big") + _APP0
    hdr += b"\xFF\xDB" + (65 + 2).to_bytes(2, "big") + _build_dqt(_STD_DQT0, quality)
    hdr += b"\xFF\xDB" + (65 + 2).to_bytes(2, "big") + _build_dqt(_STD_DQT1, quality)
    # DRI：每 1 个 MCU 重启一次（MCU 间插入 RSTn，DC 预测复位）
    hdr += b"\xFF\xDD" + b"\x00\x04" + b"\x00\x01"
    sof = bytearray([8, (height >> 8) & 0xFF, height & 0xFF,
                     (width >> 8) & 0xFF, width & 0xFF, 3,
                     1, sof_factor, 0x00,
                     2, 0x11, 0x01, 3, 0x11, 0x01])
    hdr += b"\xFF\xC0" + (len(sof) + 2).to_bytes(2, "big") + bytes(sof)
    # DHT：每段前补 1 字节 Tc/Td（高4位 class: 0=DC/1=AC，低4位 目标 id）
    hdr += b"\xFF\xC4" + (len(_STD_DHT_DC_LUMA) + 3).to_bytes(2, "big") + b"\x00" + _STD_DHT_DC_LUMA
    hdr += b"\xFF\xC4" + (len(_STD_DHT_AC_LUMA) + 3).to_bytes(2, "big") + b"\x10" + _STD_DHT_AC_LUMA
    hdr += b"\xFF\xC4" + (len(_STD_DHT_DC_CHROMA) + 3).to_bytes(2, "big") + b"\x01" + _STD_DHT_DC_CHROMA
    hdr += b"\xFF\xC4" + (len(_STD_DHT_AC_CHROMA) + 3).to_bytes(2, "big") + b"\x11" + _STD_DHT_AC_CHROMA
    hdr += b"\xFF\xDA" + (len(_SOS_DATA) + 2).to_bytes(2, "big") + _SOS_DATA
    return bytes(hdr)


# ---------------------------------------------------------------------------
# 位读写（MSB 在前）
# ---------------------------------------------------------------------------
class _BitWriter:
    def __init__(self, stuff: bool = True) -> None:
        self.acc = 0
        self.n = 0
        self.out = bytearray()
        self.stuff = stuff

    def write(self, code: int, length: int) -> None:
        for k in range(length - 1, -1, -1):
            self.acc = (self.acc << 1) | ((code >> k) & 1)
            self.n += 1
            if self.n == 8:
                self.out.append(self.acc)
                if self.stuff and self.acc == 0xFF:
                    self.out.append(0x00)
                self.acc = 0
                self.n = 0

    def align(self) -> None:
        if self.n:
            self.write(0, 8 - self.n)

    def bytes(self) -> bytes:
        return bytes(self.out)


class _BitReader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0
        self.bits = 0
        self.n = 0

    def available(self) -> int:
        return self.n + (len(self.data) - self.pos) * 8

    def _need(self, nbits: int) -> bool:
        while self.n < nbits and self.pos < len(self.data):
            self.bits = (self.bits << 8) | self.data[self.pos]
            self.pos += 1
            self.n += 8
        return self.n >= nbits

    def peek(self, nbits: int) -> Optional[int]:
        if not self._need(nbits):
            return None
        return (self.bits >> (self.n - nbits)) & ((1 << nbits) - 1)

    def read(self, nbits: int) -> Optional[int]:
        v = self.peek(nbits)
        if v is None:
            return None
        self.bits &= (1 << (self.n - nbits)) - 1
        self.n -= nbits
        return v

    def byte_align(self) -> None:
        """丢弃到下一字节边界的填充位（每个 MCU blob 从字节边界开始）。"""
        pad = self.n % 8
        if pad:
            self.bits &= (1 << (self.n - pad)) - 1
            self.n -= pad


def _extend(v: int, size: int) -> int:
    if v < (1 << (size - 1)):
        return v - (1 << size) + 1
    return v


# ---------------------------------------------------------------------------
# 块级 Huffman 编/解码（绝对 DC：每个 MCU 前由 RST 复位预测）
# ---------------------------------------------------------------------------
def _read_block(reader: _BitReader, dc_tbl: _HuffTable, ac_tbl: _HuffTable
                ) -> Optional[Tuple[int, List[int]]]:
    """读取一个块，返回 (dc, ac63)。Huffman 码最长 16 位。"""
    MAXCODE = 16

    def peek_code(tbl: _HuffTable):
        a = reader.available()
        if a <= 0:
            return None
        p = reader.peek(min(a, MAXCODE))
        return tbl.decode(p, min(a, MAXCODE))

    r = peek_code(dc_tbl)
    if r is None:
        return None
    dcsym, w = r
    reader.read(w)
    if dcsym == 0:
        dc = 0
    else:
        aux = reader.read(dcsym)
        if aux is None:
            return None
        dc = _extend(aux, dcsym)
    ac = [0] * 63
    pos = 0
    while pos < 63:
        r = peek_code(ac_tbl)
        if r is None:
            return None
        sym, w = r
        reader.read(w)
        if sym == 0x00:      # EOB
            break
        if sym == 0xF0:      # ZRL
            pos += 16
            continue
        run = sym >> 4
        size = sym & 0x0F
        pos += run
        if pos >= 63:
            break
        aux = reader.read(size)
        if aux is None:
            return None
        ac[pos] = _extend(aux, size)
        pos += 1
    return dc, ac


def _write_block(bw: _BitWriter, dc_tbl: _HuffTable, ac_tbl: _HuffTable,
                 dc: int, ac: List[int]) -> None:
    dc = int(dc)
    ac = [int(x) for x in ac]
    # DC
    if dc == 0:
        code, length = dc_tbl.encode(0x00)
        bw.write(code, length)
    else:
        size = dc.bit_length() if dc > 0 else (-dc).bit_length()
        code, length = dc_tbl.encode(size)
        bw.write(code, length)
        bits = dc if dc > 0 else dc + (1 << size) - 1
        bw.write(bits, size)
    # AC
    run = 0
    for idx in range(63):
        v = ac[idx]
        if v == 0:
            run += 1
            continue
        while run >= 16:
            code, length = ac_tbl.encode(0xF0)
            bw.write(code, length)
            run -= 16
        size = v.bit_length() if v > 0 else (-v).bit_length()
        sym = (run << 4) | size
        code, length = ac_tbl.encode(sym)
        bw.write(code, length)
        bits = v if v > 0 else v + (1 << size) - 1
        bw.write(bits, size)
        run = 0
    if run:
        code, length = ac_tbl.encode(0x00)
        bw.write(code, length)


def _blank_block_bytes(dc_tbl: _HuffTable, ac_tbl: _HuffTable) -> bytes:
    bw = _BitWriter(stuff=False)
    _write_block(bw, dc_tbl, ac_tbl, 0, [0] * 63)
    bw.align()
    return bw.bytes()


# ---------------------------------------------------------------------------
# 图像收集 + MCU 级 JPEG 重组
# ---------------------------------------------------------------------------
@dataclass
class ImageResult:
    jpeg: bytes
    width: int
    height: int
    mcu_count: int
    received_mcus: List[int] = field(default_factory=list)
    missing_mcus: List[int] = field(default_factory=list)
    eoi_seen: bool = False
    empty: bool = True


class SsdvImage:
    """按 image_id 收集包，按 packet_id 去重，MCU 级重组标准 JPEG。"""

    def __init__(self, image_id: int) -> None:
        self.image_id = image_id
        self.packets: Dict[int, SsdvPacket] = {}
        self.first: Optional[SsdvPacket] = None

    def add(self, pkt: SsdvPacket) -> bool:
        if pkt.packet_id in self.packets:
            return False
        self.packets[pkt.packet_id] = pkt
        if self.first is None:
            self.first = pkt
        return True

    def build(self) -> ImageResult:
        if not self.packets or self.first is None:
            return ImageResult(b"", 0, 0, 0, empty=True)
        f = self.first
        width, height = f.width_px, f.height_px
        quality, mcu_mode = f.quality, f.mcu_mode
        ycparts = MCU_MODE_BLOCKS[mcu_mode]
        mcu_count = f.mcu_count
        eoi_seen = any(p.eoi for p in self.packets.values())

        out = _build_jpeg_header(width, height, quality, mcu_mode)
        body = bytearray()

        received: List[int] = []
        missing: List[int] = []
        mcu_id = 0

        def blank_mcu(upto: int) -> None:
            nonlocal mcu_id
            while mcu_id < upto:
                # RSTn
                if mcu_id > 0:
                    body.append(0xFF)
                    body.append(0xD0 + ((mcu_id - 1) % 8))
                for part in range(ycparts + 2):
                    comp = 0 if part < ycparts else (part - ycparts + 1)
                    dc_tbl = _TBL_DC_LUMA if comp == 0 else _TBL_DC_CHROMA
                    ac_tbl = _TBL_AC_LUMA if comp == 0 else _TBL_AC_CHROMA
                    bb = _blank_block_bytes(dc_tbl, ac_tbl)
                    body.extend(bb)
                missing.append(mcu_id)
                mcu_id += 1

        order = sorted(self.packets)
        # 每包负责的 MCU 区间：[first_mcu, next_first_mcu)（末包到 mcu_count）
        for idx, pid in enumerate(order):
            pkt = self.packets[pid]
            first_mcu = pkt.mcu_id if pkt.mcu_id != 0xFFFF else mcu_count
            if idx + 1 < len(order):
                nxt = self.packets[order[idx + 1]]
                end_mcu = nxt.mcu_id if nxt.mcu_id != 0xFFFF else mcu_count
            else:
                end_mcu = mcu_count
            # 缺包：填补空白 MCU 到本包首 MCU
            if first_mcu > mcu_id:
                blank_mcu(first_mcu)
            # 喂入本包载荷，逐 MCU 解析（只解析本包负责的 MCU 区间，
            # 避免把包尾零填充误判为空白 MCU）
            reader = _BitReader(pkt.payload[pkt.mcu_offset:]
                                if pkt.mcu_id != 0xFFFF else pkt.payload)
            while mcu_id < end_mcu and mcu_id < mcu_count:
                # RSTn（MCU 之间；MCU0 前无重启）
                if mcu_id > 0:
                    body.append(0xFF)
                    body.append(0xD0 + ((mcu_id - 1) % 8))
                ok = True
                for part in range(ycparts + 2):
                    comp = 0 if part < ycparts else (part - ycparts + 1)
                    dc_tbl = _TBL_DC_LUMA if comp == 0 else _TBL_DC_CHROMA
                    ac_tbl = _TBL_AC_LUMA if comp == 0 else _TBL_AC_CHROMA
                    blk = _read_block(reader, dc_tbl, ac_tbl)
                    if blk is None:
                        ok = False
                        break
                    dc, ac = blk
                    bw = _BitWriter(stuff=True)
                    _write_block(bw, dc_tbl, ac_tbl, dc, ac)
                    bw.align()
                    body.extend(bw.bytes())
                if not ok:
                    # 本包数据损坏/不完整：本包剩余 MCU 记为缺失并补空白
                    blank_mcu(end_mcu)
                    break
                reader.byte_align()
                received.append(mcu_id)
                mcu_id += 1
        if mcu_id < mcu_count:
            blank_mcu(mcu_count)
        body.append(0xFF)
        body.append(0xD9)
        return ImageResult(
            jpeg=out + bytes(body),
            width=width, height=height, mcu_count=mcu_count,
            received_mcus=sorted(set(received)),
            missing_mcus=sorted(set(missing)),
            eoi_seen=eoi_seen, empty=False,
        )


# ---------------------------------------------------------------------------
# 编码方向（仅测试需要）：numpy 图像 -> SSDV 包
# ---------------------------------------------------------------------------
import numpy as np  # noqa: E402


def _dct_8x8(block: np.ndarray) -> np.ndarray:
    """8x8 DCT-II（正交，乘以 1/8 归一化）。"""
    n = 8
    c = np.cos(np.pi / (2 * n) * np.outer(np.arange(n), 2 * np.arange(n) + 1))
    c[0] /= np.sqrt(2)
    return 0.5 * (c @ block @ c.T)


def _idct_8x8(block: np.ndarray) -> np.ndarray:
    n = 8
    c = np.cos(np.pi / (2 * n) * np.outer(np.arange(n), 2 * np.arange(n) + 1))
    c[0] /= np.sqrt(2)
    return 0.5 * (c.T @ block @ c)


def _quant_tables(quality: int) -> Tuple[np.ndarray, np.ndarray]:
    qy = np.frombuffer(_build_dqt(_STD_DQT0, quality)[1:], dtype=np.uint8).reshape(8, 8).astype(np.float64)
    qc = np.frombuffer(_build_dqt(_STD_DQT1, quality)[1:], dtype=np.uint8).reshape(8, 8).astype(np.float64)
    return qy, qc


class SsdvEncoder:
    """把 numpy RGB 图像编码为 fsphil SSDV 字节包（测试/往返用）。

    callsign 必须由调用方传入；本类不内置任何呼号。
    """

    def __init__(self, callsign: str, image_id: int = 0, quality: int = 4,
                 mcu_mode: int = 3, pkt_type: int = TYPE_NORMAL) -> None:
        self.callsign = callsign
        self.image_id = image_id
        self.quality = quality
        self.mcu_mode = mcu_mode
        self.pkt_type = pkt_type
        self._rs = _SsdvRS()

    def encode_image(self, rgb: np.ndarray) -> List[bytes]:
        h, w = rgb.shape[:2]
        ycparts = MCU_MODE_BLOCKS[self.mcu_mode]
        # 对齐到 MCU 网格
        if self.mcu_mode == 0:
            W = (w + 15) // 16 * 16
            H = (h + 15) // 16 * 16
        else:
            W = (w + 15) // 16 * 16
            H = (h + 15) // 16 * 16
        canvas = np.zeros((H, W, 3), dtype=np.uint8)
        canvas[:h, :w] = rgb
        # RGB -> YCbCr (BT.601)
        arr = canvas.astype(np.float64)
        Y = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
        Cb = -0.168736 * arr[..., 0] - 0.331264 * arr[..., 1] + 0.5 * arr[..., 2] + 128
        Cr = 0.5 * arr[..., 0] - 0.418688 * arr[..., 1] - 0.081312 * arr[..., 2] + 128
        qy, qc = _quant_tables(self.quality)

        # 生成每个 MCU 的字节串（绝对 DC，块间无预测；RST 由重组器插入）
        mcu_blobs: List[bytes] = []
        if self.mcu_mode == 0:
            cols, rows = W // 16, H // 16
        elif self.mcu_mode in (1, 2):
            cols, rows = W // 16, H // 8
        else:
            cols, rows = W // 8, H // 8

        for r in range(rows):
            for c in range(cols):
                bw = _BitWriter(stuff=False)
                # 亮度块
                if self.mcu_mode == 0:
                    ybs = [(0, 0), (8, 0), (0, 8), (8, 8)]
                    ox, oy = c * 16, r * 16
                elif self.mcu_mode == 1:
                    ybs = [(0, 0), (8, 0)]
                    ox, oy = c * 16, r * 8
                elif self.mcu_mode == 2:
                    ybs = [(0, 0), (0, 8)]
                    ox, oy = c * 8, r * 16
                else:
                    ybs = [(0, 0)]
                    ox, oy = c * 8, r * 8
                for (dx, dy) in ybs:
                    blk = Y[oy + dy:oy + dy + 8, ox + dx:ox + dx + 8] - 128.0
                    d = _dct_8x8(blk)
                    q = np.round(d / qy).astype(int)
                    zig = q.flatten()[list(_ZIGZAG)]
                    _write_block(bw, _TBL_DC_LUMA, _TBL_AC_LUMA, int(zig[0]), list(zig[1:]))
                # 色度块（8x8，取该 MCU 区域平均/中心）
                cx0, cy0 = ox, oy
                cw_ = 16 if self.mcu_mode == 0 else (8 if self.mcu_mode == 3 else 8)
                cb_blk = Cb[cy0:cy0 + (16 if self.mcu_mode == 0 else 8),
                            cx0:cx0 + (16 if self.mcu_mode == 0 else 8)] - 128.0
                cr_blk = Cr[cy0:cy0 + (16 if self.mcu_mode == 0 else 8),
                            cx0:cx0 + (16 if self.mcu_mode == 0 else 8)] - 128.0
                if self.mcu_mode == 0:
                    cb_small = cb_blk.reshape(2, 8, 2, 8).mean(axis=(0, 2))
                    cr_small = cr_blk.reshape(2, 8, 2, 8).mean(axis=(0, 2))
                else:
                    cb_small = cb_blk
                    cr_small = cr_blk
                for blk, qt in ((cb_small, qc), (cr_small, qc)):
                    d = _dct_8x8(blk)
                    q = np.round(d / qt).astype(int)
                    zig = q.flatten()[list(_ZIGZAG)]
                    _write_block(bw, _TBL_DC_CHROMA, _TBL_AC_CHROMA, int(zig[0]), list(zig[1:]))
                bw.align()
                mcu_blobs.append(bw.bytes())

        # 分包：整 MCU 装入一个包（不在 MCU 中间切开），保证 mcu_offset=0、
        # 每个包从字节对齐的 MCU 边界开始；放不下的余零填充。
        pkt_payload_len = PAYLOAD_FEC if self.pkt_type == TYPE_NORMAL else PAYLOAD_NOFEC
        packets: List[bytes] = []
        pid = 0
        callsign_code = encode_callsign(self.callsign)
        n_mcus = len(mcu_blobs)

        def build_packet(pid: int, first_mcu: int, payload: bytes, eoi: int) -> bytes:
            payload = payload + bytes(pkt_payload_len - len(payload))
            pkt = bytearray(PACKET_LEN)
            pkt[0] = SYNC
            pkt[1] = self.pkt_type
            pkt[2] = (callsign_code >> 24) & 0xFF
            pkt[3] = (callsign_code >> 16) & 0xFF
            pkt[4] = (callsign_code >> 8) & 0xFF
            pkt[5] = callsign_code & 0xFF
            pkt[6] = self.image_id
            pkt[7] = (pid >> 8) & 0xFF
            pkt[8] = pid & 0xFF
            pkt[9] = W >> 4
            pkt[10] = H >> 4
            flags = ((self.quality ^ 4) & 7) << 3
            flags |= (eoi << 2)
            flags |= (self.mcu_mode & 3)
            pkt[11] = flags
            pkt[12] = 0x00            # mcu_offset=0（整 MCU 分包）
            pkt[13] = (first_mcu >> 8) & 0xFF
            pkt[14] = first_mcu & 0xFF
            pkt[HEADER_LEN:HEADER_LEN + pkt_payload_len] = payload
            crc_off = OFFSET_CRC_FEC if self.pkt_type == TYPE_NORMAL else OFFSET_CRC_NOFEC
            pkt[crc_off:crc_off + 4] = _crc32(bytes(pkt[1:crc_off])).to_bytes(4, "big")
            if self.pkt_type == TYPE_NORMAL:
                pkt[1 + RS_MSG_LEN:1 + RS_MSG_LEN + RS_LEN] = self._rs.encode(bytes(pkt[1:1 + RS_MSG_LEN]))
            return bytes(pkt)

        i = 0
        while i < n_mcus:
            first_mcu = i
            buf = bytearray()
            while i < n_mcus and len(buf) + len(mcu_blobs[i]) <= pkt_payload_len:
                buf += mcu_blobs[i]
                i += 1
            if not buf:
                # 单个 MCU 超过包载荷（极端）：强行截断一个 blob
                buf += mcu_blobs[i][:pkt_payload_len]
                i += 1
            eoi = 1 if i >= n_mcus else 0
            packets.append(build_packet(pid, first_mcu, bytes(buf), eoi))
            pid += 1
        if not packets:
            # 空图：至少发一个 EOI 空包
            packets.append(build_packet(0, 0xFFFF, b"", 1))
        return packets


__all__ = [
    "SYNC", "TYPE_NORMAL", "TYPE_NOFEC", "PACKET_LEN",
    "PAYLOAD_FEC", "PAYLOAD_NOFEC",
    "SsdvPacket", "SsdvDecoder", "SsdvImage", "ImageResult", "SsdvEncoder",
    "encode_callsign", "decode_callsign",
]
