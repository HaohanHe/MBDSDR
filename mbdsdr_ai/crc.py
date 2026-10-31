# SPDX-License-Identifier: MIT
"""MBDSDR AI 内核 - CRC 校验原子积木（参数化内核 + 命名预设）
=========================================================

设计红线（AGENTS.md「杜绝硬编码/一次性工程」）：全仓 CRC 原先散落 16 个
文件、CRC16 有 4 种互不兼容变体。本模块提供**单一参数化内核** + **命名预设**，
覆盖全部变体（CRC8/14/16/24/32、比特域），各协议模块收敛到此单一来源。

内核统一约定：数据可为 bytes 或 MSB-first 0/1 比特序列（bits=True）。
预设忠实复刻既有实现的 (poly, init, refin, refout, xorout) 参数，回归向量
（如 "123456789" -> X.25=0x906E / CCITT-FALSE=0x29B1）由测试钉扎，收敛零漂移。

依据：Williams/Ardplatts CRC 参数模型（crcmod 口径）。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, List, Optional, Sequence, Union

BytesLike = Union[bytes, bytearray, Sequence[int]]


@dataclass(frozen=True)
class CrcParams:
    """CRC 参数五元组（+ M17 等特例的预热/清尾步数）。"""
    width: int
    poly: int
    init: int
    refin: bool
    refout: bool
    xorout: int
    name: str = ""
    warmup_bits: int = 0   # M17：喂数据前的 LSB-first 预热步数
    flush_bits: int = 0    # M17：喂数据后的无输入移位步数


def _reflect(value: int, width: int) -> int:
    """把 width 位整数按位翻转（LSB<->MSB）。"""
    out = 0
    for _ in range(width):
        out = (out << 1) | (value & 1)
        value >>= 1
    return out


def _to_bits(data: BytesLike) -> List[int]:
    """bytes -> MSB-first 比特列表。"""
    bits: List[int] = []
    for b in data:
        b = int(b) & 0xFF
        for i in range(7, -1, -1):
            bits.append((b >> i) & 1)
    return bits


def _byte_step_normal(reg: int, byte: int, poly: int, width: int,
                      topbit: int, mask: int) -> int:
    """非反射核：XOR 字节入高位 + 8 次左移（标准 CRC-16/32 口径）。"""
    reg ^= (byte << (width - 8)) & mask
    for _ in range(8):
        if reg & topbit:
            reg = ((reg << 1) ^ poly) & mask
        else:
            reg = (reg << 1) & mask
    return reg


def _byte_step_reflected(reg: int, byte: int, rpoly: int) -> int:
    """反射核：XOR 字节入低位 + 8 次右移 + 反射多项式。"""
    reg ^= byte
    for _ in range(8):
        if reg & 1:
            reg = (reg >> 1) ^ rpoly
        else:
            reg >>= 1
    return reg


def crc_generic(data: Union[BytesLike, Sequence[int]], p: CrcParams,
                *, bits: bool = False, nbits: Optional[int] = None) -> int:
    """参数化 CRC 内核（标准 CRC：CCITT/X.25/ACARS/MODES/24Q/IEEE/DSLWP）。

    data: bytes（默认）或 0/1 序列（bits=True）。nbits 可截断比特前缀
    （覆盖 Mode S 只算前 88/32 位等构造用法）。
    返回整数余数（已含 refout/xorout 后处理）。

    多项式统一存**正常形**（Williams 口径）：反射型（refin=True）走右移核
    内部自动取反射多项式；非反射走左移核。bit 域恒为非反射左移（Mode S）。
    注意：M17 是非标准 LFSR 结构（预热/清尾 + 数据入 LSB），走专用 crc16_m17。
    """
    if p.width <= 0 or p.width > 64:
        raise ValueError(f"非法 width: {p.width}")
    mask = (1 << p.width) - 1
    topbit = 1 << (p.width - 1)
    poly = p.poly & mask

    if bits:
        bl = [int(x) & 1 for x in data]
        if nbits is not None:
            if not (0 <= nbits <= len(bl)):
                raise ValueError("nbits 超出范围")
            bl = bl[:nbits]
    else:
        bl = [int(x) & 0xFF for x in data]

    reg = p.init & mask
    if bits:
        # bit 域：非反射左移核（Mode S 前缀构造）
        for b in bl:
            msb = reg & topbit
            reg = ((reg << 1) & mask) | b
            if msb:
                reg ^= poly
    else:
        step = _byte_step_reflected if p.refin else _byte_step_normal
        rpoly = _reflect(poly, p.width) if p.refin else 0
        for byte in bl:
            reg = step(reg, byte, rpoly) if p.refin else step(
                reg, byte, poly, p.width, topbit, mask)

    # refin/refout 不一致时才需输出反射（同向时核已就位）
    if p.refout != p.refin:
        reg = _reflect(reg, p.width)
    return (reg ^ p.xorout) & mask


def crc_update(state: int, data: BytesLike, p: CrcParams) -> int:
    """流式增量：给前一段的寄存器状态，追加 data 段（bytes 域，未含 refout/xorout）。"""
    mask = (1 << p.width) - 1
    topbit = 1 << (p.width - 1)
    poly = p.poly & mask
    reg = state & mask
    rpoly = _reflect(poly, p.width) if p.refin else 0
    for byte in data:
        if p.refin:
            reg = _byte_step_reflected(reg, int(byte) & 0xFF, rpoly)
        else:
            reg = _byte_step_normal(reg, int(byte) & 0xFF, poly, p.width, topbit, mask)
    return reg


def crc16_m17(data: BytesLike) -> int:
    """M17 CRC-16（poly 0x5935，预热 16 + 清尾 16，数据逐比特入 LSB）。

    非标准 LFSR 结构（与 m17_adapter.M17CRC16 逐位一致，check("123456789")=0x772B）。
    """
    poly = 0x5935
    mask = 0xFFFF
    msb_top = 0x8000
    reg = 0xFFFF
    for _ in range(16):  # 预热：LSB-first 移位
        bit = reg & 1
        if bit:
            reg ^= poly
        reg >>= 1
        if bit:
            reg |= msb_top
    for byte in data:  # 逐比特 MSB-first 入 LSB
        b = int(byte) & 0xFF
        for i in range(8):
            bit = (b >> (7 - i)) & 1
            msb = reg & msb_top
            reg = ((reg << 1) & mask) | bit
            if msb:
                reg ^= poly
    for _ in range(16):  # 清尾：无输入左移
        msb = reg & msb_top
        reg = (reg << 1) & mask
        if msb:
            reg ^= poly
    return reg & mask


def crc_residue_ok(data_with_crc: BytesLike, p: CrcParams) -> bool:
    """余数==0 约定（ACARS 等整帧含 CRC、xorout=0 的反射型：求余为 0 即通过）。"""
    return crc_generic(data_with_crc, p) == 0


# ─────────────────────────────────────────────────────────────
# 命名预设（覆盖全仓既有变体，参数忠实复刻）
# ─────────────────────────────────────────────────────────────
CRC16_X25 = CrcParams(16, 0x1021, 0xFFFF, True, True, 0xFFFF, "crc16_x25")
CRC16_CCITT_FALSE = CrcParams(16, 0x1021, 0xFFFF, False, False, 0x0000, "crc16_ccitt_false")
#: CCSDS TC 帧校验（CCSDS 232.0-B：G(x)=x^16+x^12+x^5+1，初值全 1，
#: MSB 先行，无反射无终异或——与 CCITT-FALSE 同参数，别名单列以名实相符）
CRC16_CCSDS = CrcParams(16, 0x1021, 0xFFFF, False, False, 0x0000, "crc16_ccsds")
CRC16_CCITT_DAB = CrcParams(16, 0x1021, 0xFFFF, False, False, 0xFFFF, "crc16_ccitt_dab")
CRC16_ACARS = CrcParams(16, 0x1021, 0x0000, True, True, 0x0000, "crc16_acars")
CRC16_M17 = CrcParams(16, 0x5935, 0xFFFF, False, False, 0x0000, "crc16_m17",
                       warmup_bits=16, flush_bits=16)
CRC24_MODES = CrcParams(24, 0xFFF409, 0x000000, False, False, 0, "crc24_modes")
CRC24Q = CrcParams(24, 0x1864CFB, 0x000000, False, False, 0, "crc24q")
CRC32_IEEE = CrcParams(32, 0x04C11DB7, 0xFFFFFFFF, True, True, 0xFFFFFFFF, "crc32_ieee")
CRC32_DSLWP = CrcParams(32, 0x04C11DB7, 0x4EE4FDE1, True, True, 0xFFFFFFFF, "crc32_dslwp")


def _preset(name: str) -> CrcParams:
    table = {
        "crc16_x25": CRC16_X25,
        "crc16_ccitt_false": CRC16_CCITT_FALSE,
        "crc16_ccsds": CRC16_CCSDS,
        "crc16_ccitt_dab": CRC16_CCITT_DAB,
        "crc16_acars": CRC16_ACARS,
        "crc16_m17": CRC16_M17,
        "crc24_modes": CRC24_MODES,
        "crc24q": CRC24Q,
        "crc32_ieee": CRC32_IEEE,
        "crc32_dslwp": CRC32_DSLWP,
    }
    key = name.strip().lower()
    if key not in table:
        raise ValueError(f"未知 CRC 预设: {name}（可用: {', '.join(table)}）")
    return table[key]


# ─────────────────────────────────────────────────────────────
# 比特域 CRC（FT8 CRC14 / FST4 CRC24 / Mode S 前缀构造）
# ─────────────────────────────────────────────────────────────
def crc_bits_msb(bits: Sequence[int], poly_bits: Sequence[int]) -> List[int]:
    """比特域 MSB-first 长除余数（FT8/FST4 口径）。

    poly_bits: 多项式系数序列（首项 1，含常数项），返回与 (len(poly_bits)-1)
    同宽的余数比特列表。对补零后整段求余==0 即通过。
    """
    poly = [int(x) & 1 for x in poly_bits]
    if not poly or poly[0] != 1:
        raise ValueError("poly_bits 须以 1 开头")
    deg = len(poly) - 1
    work = [int(x) & 1 for x in bits]
    if len(work) < deg:
        raise ValueError("比特长度不足")
    for i in range(len(work) - deg):
        if work[i]:
            for j in range(len(poly)):
                work[i + j] ^= poly[j]
    return work[-deg:]


def crc14_ft8(msg77: Sequence[int]) -> List[int]:
    """FT8 CRC14：77 消息位 + 补 14 零 -> 14 位 CRC。"""
    if len(msg77) != 77:
        raise ValueError("FT8 CRC14 需 77 位消息")
    poly = [1, 1, 0, 0, 1, 1, 1, 0, 1, 0, 1, 0, 1, 1, 1]  # 0x6757
    work = [int(x) & 1 for x in msg77] + [0] * 14
    return crc_bits_msb(work, poly)


def check_crc14_ft8(bits91: Sequence[int]) -> bool:
    """FT8 91 位（77+14）校验。"""
    if len(bits91) != 91:
        raise ValueError("需 91 位")
    return [int(x) & 1 for x in bits91[77:91]] == crc14_ft8(bits91[:77])
