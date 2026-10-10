# SPDX-License-Identifier: MIT
"""LRPT 第②步：卷积内码 + RS 外码确定性互测。

镜像 test_ft8_modem.py / test_lrpt_modem.py 风格。
"""
from __future__ import annotations

import numpy as np
import pytest

from mbdsdr_ai.lrpt_fec import (
    CADU_SIZE,
    CCSDS_POLY1,
    CCSDS_POLY2,
    CONV_K,
    LrptCaduDecoder,
    convolve_encode,
    viterbi_decode,
)

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def decoder() -> LrptCaduDecoder:
    return LrptCaduDecoder()


@pytest.fixture
def sample_payload() -> bytes:
    rng = np.random.default_rng(42)
    return rng.integers(0, 256, size=892, dtype=np.uint8).tobytes()


@pytest.fixture
def sample_cadu(decoder: LrptCaduDecoder, sample_payload: bytes) -> bytes:
    """组装完整 CADU：ASM + 交织 + 加扰。"""
    blocks = [sample_payload[i*223:(i+1)*223] for i in range(4)]
    encoded = [decoder.rs.encode(b) for b in blocks]
    data = decoder._interleave_4(encoded)
    scrambled = decoder.scrambler.scramble(data)
    asm = bytes([0x1A, 0xCF, 0xFC, 0x1D])
    return asm + scrambled


# --------------------------------------------------------------------------- #
# Viterbi 卷积译码
# --------------------------------------------------------------------------- #

class TestViterbi:
    def test_poly_constants(self):
        """CCSDS K=7 r=1/2 多项式（viterbi27.h:8 交叉证实）。"""
        assert CCSDS_POLY1 == 79    # 117 octal
        assert CCSDS_POLY2 == 109   # 155 octal
        assert CONV_K == 7

    def test_noiseless_roundtrip(self):
        """无噪 Viterbi 译码完美恢复。"""
        rng = np.random.default_rng(0)
        info = rng.integers(0, 2, size=200, dtype=np.uint8)
        coded = convolve_encode(info)
        soft = coded.astype(np.float64) * 2 - 1
        decoded = viterbi_decode(soft)
        assert np.array_equal(info, decoded)

    def test_bit_flips_recovered(self):
        """少量位翻转被 Viterbi 纠正。"""
        rng = np.random.default_rng(1)
        info = rng.integers(0, 2, size=500, dtype=np.uint8)
        coded = convolve_encode(info)
        # 翻转 20 个随机位
        flip_idx = rng.choice(coded.size, size=20, replace=False)
        coded_err = coded.copy()
        coded_err[flip_idx] ^= 1
        soft = coded_err.astype(np.float64) * 2 - 1
        decoded = viterbi_decode(soft)
        assert np.sum(info != decoded) == 0, \
            f"20 bit flips should be corrected, got {np.sum(info != decoded)} errors"

    def test_deterministic(self):
        """同一输入两次译码结果一致。"""
        rng = np.random.default_rng(2)
        info = rng.integers(0, 2, size=100, dtype=np.uint8)
        coded = convolve_encode(info)
        soft = coded.astype(np.float64) * 2 - 1
        d1 = viterbi_decode(soft)
        d2 = viterbi_decode(soft)
        assert np.array_equal(d1, d2)


# --------------------------------------------------------------------------- #
# CCSDS CADU 解码链（Viterbi → 解扰 → RS×4）
# --------------------------------------------------------------------------- #

class TestCaduDecode:
    def test_cadu_size(self):
        assert CADU_SIZE == 1024

    def test_full_chain_noiseless(self, decoder: LrptCaduDecoder,
                                  sample_cadu: bytes, sample_payload: bytes):
        """全链无噪 round-trip：CADU → 卷积编码 → Viterbi → 解扰 → RS。"""
        cadu_bits = np.unpackbits(np.frombuffer(sample_cadu, dtype=np.uint8))
        coded = convolve_encode(cadu_bits)
        soft = coded.astype(np.float64) * 2 - 1
        decoded_bits = viterbi_decode(soft)
        # 组回字节
        padded = np.zeros((decoded_bits.size + 7) // 8 * 8, dtype=np.uint8)
        padded[:decoded_bits.size] = decoded_bits
        decoded_cadu = np.packbits(padded).tobytes()
        # 解码
        data, errors = decoder.decode_cadu(decoded_cadu)
        assert all(e >= 0 for e in errors), f"RS 错误：{errors}"
        assert data == sample_payload

    def test_asm_recovered(self, decoder: LrptCaduDecoder, sample_cadu: bytes):
        """Viterbi 译码后前 4 字节 = ASM 0x1ACFFC1D。"""
        cadu_bits = np.unpackbits(np.frombuffer(sample_cadu, dtype=np.uint8))
        coded = convolve_encode(cadu_bits)
        soft = coded.astype(np.float64) * 2 - 1
        decoded_bits = viterbi_decode(soft)
        padded = np.zeros((decoded_bits.size + 7) // 8 * 8, dtype=np.uint8)
        padded[:decoded_bits.size] = decoded_bits
        decoded_cadu = np.packbits(padded).tobytes()
        assert decoded_cadu[:4] == bytes([0x1A, 0xCF, 0xFC, 0x1D])

    def test_error_recovery_100_flips(self, decoder: LrptCaduDecoder,
                                       sample_cadu: bytes, sample_payload: bytes):
        """100 个编码位翻转 → Viterbi + RS 仍能恢复。"""
        rng = np.random.default_rng(99)
        cadu_bits = np.unpackbits(np.frombuffer(sample_cadu, dtype=np.uint8))
        coded = convolve_encode(cadu_bits)
        flip_idx = rng.choice(coded.size, size=100, replace=False)
        coded_err = coded.copy()
        coded_err[flip_idx] ^= 1
        soft = coded_err.astype(np.float64) * 2 - 1
        decoded_bits = viterbi_decode(soft)
        padded = np.zeros((decoded_bits.size + 7) // 8 * 8, dtype=np.uint8)
        padded[:decoded_bits.size] = decoded_bits
        decoded_cadu = np.packbits(padded).tobytes()
        data, errors = decoder.decode_cadu(decoded_cadu)
        assert data == sample_payload, f"100 flips should recover, errors={errors}"

    def test_rs_correction_16_bytes_per_block(self, decoder: LrptCaduDecoder):
        """RS(255,223) 每块可纠 ≤16 字节错误（上限实测）。"""
        rng = np.random.default_rng(7)
        payload = rng.integers(0, 256, size=223, dtype=np.uint8).tobytes()
        codeword = decoder.rs.encode(payload)
        # 注入 16 字节错误（上限）
        err_pos = rng.choice(255, size=16, replace=False)
        corrupted = bytearray(codeword)
        for p in err_pos:
            corrupted[p] ^= 0xFF
        result = decoder.rs.decode(bytes(corrupted))
        assert result.nerrors == 16
        assert result.data == payload

    def test_rs_uncorrectable_17_bytes(self, decoder: LrptCaduDecoder):
        """RS(255,223) 每块 >16 字节错误 → 不可纠（诚实返回 -1）。"""
        rng = np.random.default_rng(8)
        payload = rng.integers(0, 256, size=223, dtype=np.uint8).tobytes()
        codeword = decoder.rs.encode(payload)
        err_pos = rng.choice(255, size=17, replace=False)
        corrupted = bytearray(codeword)
        for p in err_pos:
            corrupted[p] ^= 0xFF
        result = decoder.rs.decode(bytes(corrupted))
        assert result.nerrors == -1  # 不可纠


# --------------------------------------------------------------------------- #
# 诚实空态
# --------------------------------------------------------------------------- #

class TestHonestEmpty:
    def test_too_short_cadu(self, decoder: LrptCaduDecoder):
        """输入不足 1024 字节 → 抛 ValueError（诚实报错）。"""
        with pytest.raises(ValueError):
            decoder.decode_cadu(b"\x00" * 100)

    def test_uncorrectable_returns_empty(self, decoder: LrptCaduDecoder):
        """RS 不可纠时返回空数据 + 负错误计数（诚实空态）。"""
        # 构造一个全噪声 1024 字节
        rng = np.random.default_rng(123)
        noise = rng.integers(0, 256, size=1024, dtype=np.uint8).tobytes()
        data, errors = decoder.decode_cadu(noise)
        # 不一定全失败，但至少不假装成功
        # 如果有失败块，data 应为空
        if any(e < 0 for e in errors):
            assert data == b""
