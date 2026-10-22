# SPDX-License-Identifier: MIT
"""CRC 原子积木正确性（已知答案向量钉扎，收敛零漂移）。

对照全仓既有实现的回归向量（Williams/Ardplatts 标准 "123456789" check 值 +
各协议模块实测值），验证统一内核精确复刻所有变体。
"""
from __future__ import annotations
import os
import sys
import zlib

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mbdsdr_ai import crc  # noqa: E402


class TestKnownAnswers:
    """标准 check 值 "123456789"（Williams CRC catalogue）。"""

    def test_crc16_x25(self):
        # 既有 ax25_aprs_roundtrip 钉扎值
        assert crc.crc_generic(b"123456789", crc.CRC16_X25) == 0x906E

    def test_crc16_ccitt_false(self):
        # 既有 test_protocol_parser / goes_lrit 钉扎值
        assert crc.crc_generic(b"123456789", crc.CRC16_CCITT_FALSE) == 0x29B1

    def test_crc16_ccitt_dab(self):
        # 既有 dab_plus_test 钉扎值（xorout 0xFFFF 变体）
        assert crc.crc_generic(b"123456789", crc.CRC16_CCITT_DAB) == 0xD64E

    def test_crc16_m17(self):
        # 既有 digital_voice_roundtrip 钉扎值（专用非标 LFSR 结构）
        assert crc.crc16_m17(b"123456789") == 0x772B

    def test_crc32_ieee_matches_zlib(self):
        assert crc.crc_generic(b"123456789", crc.CRC32_IEEE) == (zlib.crc32(b"123456789") & 0xFFFFFFFF)

    def test_crc32_dslwp_matches_zlib_trick(self):
        # 与 ssdv_decoder._crc32_dslwp 的 zlib 技巧一致
        magic = 0x4EE4FDE1
        expected = zlib.crc32(b"123456789", magic ^ 0xFFFFFFFF) & 0xFFFFFFFF
        assert crc.crc_generic(b"123456789", crc.CRC32_DSLWP) == expected


class TestPresetLookup:
    def test_named_presets_resolve(self):
        for name in ("crc16_x25", "crc16_ccitt_false", "crc16_ccitt_dab",
                     "crc16_acars", "crc16_m17", "crc24_modes", "crc24q",
                     "crc32_ieee", "crc32_dslwp"):
            p = crc._preset(name)
            assert isinstance(p, crc.CrcParams)

    def test_unknown_preset_rejected(self):
        with pytest.raises(ValueError):
            crc._preset("nope_crc")

    def test_convenience_alias(self):
        assert crc.crc_generic(b"abc", crc._preset("crc16_x25")) == crc.crc_generic(b"abc", crc.CRC16_X25)


class TestBitDomain:
    def test_crc14_ft8_roundtrip(self):
        msg = ([1, 0, 1, 1] * 20)[:77]  # 77 位
        c = crc.crc14_ft8(msg)
        assert len(c) == 14
        bits91 = list(msg) + list(c)
        assert crc.check_crc14_ft8(bits91) is True
        # 翻转任一位 -> 校验失败
        bad = list(bits91)
        bad[3] ^= 1
        assert crc.check_crc14_ft8(bad) is False

    def test_crc14_ft8_matches_ft8_codec(self):
        # 与既有 ft8_codec.crc14_bits 逐位一致（跨实现对拍，零漂移）
        from mbdsdr_ai import ft8_codec
        msg = ([1, 0, 1, 1] * 20)[:77]
        assert crc.crc14_ft8(msg) == ft8_codec.crc14_bits(msg)

    def test_crc14_ft8_len_guard(self):
        with pytest.raises(ValueError):
            crc.crc14_ft8([1, 0, 1])  # 非 77 位

    def test_crc_bits_msb_len_guard(self):
        with pytest.raises(ValueError):
            crc.crc_bits_msb([1, 0], [1, 1, 0, 1])  # 太短
        with pytest.raises(ValueError):
            crc.crc_bits_msb([1, 0, 1], [0, 1, 1])  # poly 须以 1 开头


class TestResidueAndStream:
    def test_residue_ok_acars_frame(self):
        # ACARS（xorout=0 反射型）：整帧含 CRC 余数==0 约定（FCS 小端追加）
        data = b"HELLO"
        fcs = crc.crc_generic(data, crc.CRC16_ACARS)
        frame = data + bytes([fcs & 0xFF, (fcs >> 8) & 0xFF])  # 反射型小端 FCS
        assert crc.crc_residue_ok(frame, crc.CRC16_ACARS) is True

    def test_x25_fcs_check(self):
        # X.25：FCS 小端追加，重算 data 应等于尾部 FCS（比对式校验）
        data = b"HELLO"
        fcs = crc.crc_generic(data, crc.CRC16_X25)
        frame = data + bytes([fcs & 0xFF, (fcs >> 8) & 0xFF])
        recomputed = crc.crc_generic(frame[:-2], crc.CRC16_X25)
        tail = frame[-2] | (frame[-1] << 8)
        assert recomputed == tail

    def test_crc_update_stream_equals_oneshot(self):
        p = crc.CRC16_CCITT_FALSE
        whole = crc.crc_generic(b"abcdefg", p)
        state = crc.crc_update(p.init, b"abcd", p)
        state = crc.crc_update(state, b"efg", p)
        assert (state ^ p.xorout) == whole

    def test_crc_update_reflected_stream_equals_oneshot(self):
        p = crc.CRC16_X25
        whole = crc.crc_generic(b"abcdefg", p)
        state = crc.crc_update(p.init, b"abcd", p)
        state = crc.crc_update(state, b"efg", p)
        assert (state ^ p.xorout) == whole
