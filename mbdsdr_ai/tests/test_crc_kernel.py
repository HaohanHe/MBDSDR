#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
CRC 单一内核测试
================

mbdsdr_ai/crc.py 是全仓 CRC 的收敛内核（原先散落 16 文件、CRC16 有 4 种
互不兼容变体）。内核一旦错，所有协议校验一起失效——必须用**公开标准校验值**
+ **朴素参考实现**双重钉扎，且流式/比特/残值路径全覆盖。

公开向量（"123456789" check value，Williams/Ardplatts crcmod 口径）：
  CRC-16/X-25         -> 0x906E
  CRC-16/CCITT-FALSE  -> 0x29B1
  CRC-32/IEEE         -> 0xCBF43926（= zlib.crc32）
本文件还用纯 Python 朴素逐位实现对全部预设做交叉验证。

运行: python3 -m pytest mbdsdr_ai/tests/test_crc_kernel.py -v
"""
import os
import sys
import zlib

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.crc import (  # noqa: E402
    CRC16_ACARS, CRC16_CCITT_DAB, CRC16_CCITT_FALSE, CRC16_M17, CRC16_X25,
    CRC24_MODES, CRC24Q, CRC32_DSLWP, CRC32_IEEE,
    CrcParams, crc14_ft8, check_crc14_ft8, crc_generic, crc_residue_ok,
    crc_update, crc16_m17,
)

MSG = b"123456789"

ALL_PRESETS = (
    CRC16_X25, CRC16_CCITT_FALSE, CRC16_CCITT_DAB, CRC16_ACARS,
    CRC24_MODES, CRC24Q, CRC32_IEEE, CRC32_DSLWP,
)


# ---------------------------------------------------------------------------
# 朴素参考实现（独立于内核的算法路径，用作交叉验证的 oracle）
# ---------------------------------------------------------------------------
def _reflect(value: int, width: int) -> int:
    out = 0
    for _ in range(width):
        out = (out << 1) | (value & 1)
        value >>= 1
    return out


def _naive_crc(data: bytes, p: CrcParams) -> int:
    """位级经典实现（与内核的字节步进路径算法上独立）。

    非反射：MSB-first 左移，bit 入最高位后移位；
    反射：LSB-first 右移 + 反射多项式。
    """
    mask = (1 << p.width) - 1
    topbit = 1 << (p.width - 1)
    poly = p.poly & mask
    rpoly = _reflect(poly, p.width)
    reg = p.init & mask
    for byte in data:
        b = int(byte) & 0xFF
        for i in range(8):
            if p.refin:
                # LSB first：输入位先入 LSB，再按 LSB 决定反馈（与字节步进等价）
                reg ^= (b >> i) & 1
                if reg & 1:
                    reg = (reg >> 1) ^ rpoly
                else:
                    reg >>= 1
            else:
                # MSB first：反馈 = 旧 MSB ⊕ 输入位，移位后按反馈取模
                inbit = (b >> (7 - i)) & 1
                msb = (reg >> (p.width - 1)) & 1
                reg = (reg << 1) & mask
                if msb ^ inbit:
                    reg ^= poly
    if p.refout != p.refin:
        reg = _reflect(reg, p.width)
    return (reg ^ p.xorout) & mask


# ---------------------------------------------------------------------------
# 公开标准校验值
# ---------------------------------------------------------------------------
def test_known_check_vector_x25():
    assert crc_generic(MSG, CRC16_X25) == 0x906E


def test_known_check_vector_ccitt_false():
    assert crc_generic(MSG, CRC16_CCITT_FALSE) == 0x29B1


def test_known_check_vector_crc32_ieee_matches_zlib():
    """CRC-32/IEEE 必须与 zlib.crc32 逐字节一致（独立 oracle）。"""
    assert crc_generic(MSG, CRC32_IEEE) == 0xCBF43926
    assert crc_generic(MSG, CRC32_IEEE) == (zlib.crc32(MSG) & 0xFFFFFFFF)


def test_known_check_vector_pinned_rest():
    """其余预设钉扎当前值——收敛零漂移的回归基线。"""
    assert crc_generic(MSG, CRC16_CCITT_DAB) == 0xD64E
    assert crc_generic(MSG, CRC16_ACARS) == 0x2189
    assert crc_generic(MSG, CRC24_MODES) == 0x054268
    assert crc_generic(MSG, CRC24Q) == 0xCDE703
    assert crc_generic(MSG, CRC32_DSLWP) == 0x599CAF5C


# ---------------------------------------------------------------------------
# 朴素参考交叉验证
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("p", ALL_PRESETS, ids=lambda p: p.name)
def test_matches_naive_reference(p):
    """内核必须与独立朴素逐位实现一致（不止一个样本）。"""
    for sample in (b"", b"A", MSG, bytes(range(32)), b"\x00" * 7, b"\xff" * 5):
        assert crc_generic(sample, p) == _naive_crc(sample, p), \
            f"{p.name} 与朴素参考不一致，样本={sample!r}"


# ---------------------------------------------------------------------------
# 流式 / 残值 / 比特域
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("p", ALL_PRESETS, ids=lambda p: p.name)
def test_streaming_equals_oneshot(p):
    """crc_update 分段喂入必须与一次算完等价（含 refin/refout 收尾）。"""

    def _finish(reg: int, params: CrcParams) -> int:
        if params.refout != params.refin:
            rev = 0
            v = reg
            for _ in range(params.width):
                rev = (rev << 1) | (v & 1)
                v >>= 1
            reg = rev
        return reg ^ params.xorout

    one = crc_generic(MSG, p)
    for chunks in ((MSG[:1], MSG[1:7], MSG[7:]),
                   (MSG[:4], MSG[4:]),
                   (MSG,)):
        reg = p.init
        for ch in chunks:
            reg = crc_update(reg, ch, p)
        assert _finish(reg, p) == one, \
            f"{p.name} 流式 {tuple(len(c) for c in chunks)} 与一次算完不一致"


def test_residue_ok_acars_style():
    """ACARS 约定：data||crc（小端）整帧再算，余数必须为 0。"""
    p = CRC16_ACARS
    c = crc_generic(MSG, p)
    full = MSG + c.to_bytes(2, "little")
    assert crc_residue_ok(full, p) is True, \
        "data||crc(LE) 整帧必须残值 0（ACARS 校验约定）"
    # 破坏一个字节 → 必须诚实失败
    bad = bytearray(full)
    bad[3] ^= 0x01
    assert crc_residue_ok(bytes(bad), p) is False


def test_bits_mode_is_mode_s_lfsr():
    """bits=True 是 Mode S 前缀构造专用 LFSR（比特入 LSB），有定义良好的行为。

    语义（见 crc_generic docstring）：bit 域恒为非反射左移核，不等同于
    字节域对反射预设的逐位展开。这里钉住它的自洽性与稳定性。
    """
    p = CRC16_CCITT_FALSE
    bits = []
    for b in MSG:
        for i in range(7, -1, -1):
            bits.append((b >> i) & 1)
    v1 = crc_generic(bits, p, bits=True)
    v2 = crc_generic(bits, p, bits=True)
    assert v1 == v2
    assert isinstance(v1, int) and 0 <= v1 < (1 << p.width)
    # nbits 截断必须只算前缀
    truncated = crc_generic(bits, p, bits=True, nbits=8)
    assert truncated == crc_generic(bits[:8], p, bits=True)
    # 越界 nbits 必须诚实报错
    with pytest.raises(ValueError):
        crc_generic(bits, p, bits=True, nbits=len(bits) + 1)


def test_crc16_m17_pinned():
    assert crc16_m17(MSG) == 0x772B


def test_crc14_ft8_roundtrip():
    """FT8 CRC14：编码后自校验必须通过；翻转任一位必须失败。"""
    msg77 = [0, 1] * 38 + [1]   # 77 位
    assert len(msg77) == 77
    code = crc14_ft8(msg77)
    assert len(code) == 14
    bits91 = list(msg77) + list(code)
    assert check_crc14_ft8(bits91) is True
    # 翻转一位 → 必须校验失败（诚实失败，不是假通过）
    bad = list(bits91)
    bad[10] ^= 1
    assert check_crc14_ft8(bad) is False


def test_empty_input_is_stable():
    """空输入必须有定义良好的稳定值（不是崩、不是随机）。"""
    for p in ALL_PRESETS:
        v1 = crc_generic(b"", p)
        v2 = crc_generic(b"", p)
        assert v1 == v2
        assert isinstance(v1, int)
        assert 0 <= v1 < (1 << p.width)
