# SPDX-License-Identifier: MIT
"""LRPT DCT/IDCT 压缩域确定性互测。"""
from __future__ import annotations

import numpy as np
import pytest

from mbdsdr_ai.lrpt_dct import (
    QUANT_TABLE,
    ZIGZAG,
    dct_8x8,
    idct_8x8,
    quantize,
    dequantize,
    zigzag_order,
    unzigzag_order,
)


class TestDCTRoundTrip:
    def test_dct_idct_perfect_recovery(self):
        """无量化时 DCT→IDCT 逐像素恢复。"""
        rng = np.random.default_rng(42)
        block = rng.integers(0, 256, size=(8, 8)).astype(np.float64)
        coeffs = dct_8x8(block)
        recovered = idct_8x8(coeffs)
        assert np.max(np.abs(block - recovered)) < 1e-6

    def test_flat_block(self):
        """全平块 → DC-only → 恢复。"""
        block = np.full((8, 8), 128.0)
        coeffs = dct_8x8(block)
        assert abs(coeffs[0, 0]) < 1e-6  # 128-128=0
        assert np.max(np.abs(coeffs[1:, :])) < 1e-6  # AC 全零
        recovered = idct_8x8(coeffs)
        assert np.max(np.abs(block - recovered)) < 1e-6


class TestQuantization:
    def test_quantize_dequantize(self):
        """量化→反量化是有损的（qf=50，误差有界）。"""
        rng = np.random.default_rng(0)
        block = rng.integers(0, 256, size=(8, 8)).astype(np.float64)
        coeffs = dct_8x8(block)
        coeffs_q = quantize(coeffs, qf=50)
        coeffs_dq = dequantize(coeffs_q, qf=50)
        recovered = idct_8x8(coeffs_dq)
        # 有损：误差应在合理范围（< 50/像素）
        assert np.max(np.abs(block - recovered)) < 60

    def test_qf_100_near_lossless(self):
        """高 QF 接近无损。"""
        rng = np.random.default_rng(0)
        block = rng.integers(0, 256, size=(8, 8)).astype(np.float64)
        coeffs = dct_8x8(block)
        coeffs_q = quantize(coeffs, qf=95)
        coeffs_dq = dequantize(coeffs_q, qf=95)
        recovered = idct_8x8(coeffs_dq)
        assert np.max(np.abs(block - recovered)) < 10


class TestZigzag:
    def test_zigzag_unzigzag(self):
        """Zigzag→unzigzag 可逆。"""
        rng = np.random.default_rng(1)
        block = rng.standard_normal((8, 8))
        z = zigzag_order(block)
        assert z.shape == (64,)
        back = unzigzag_order(z)
        np.testing.assert_allclose(back, block)

    def test_zigzag_length(self):
        assert len(ZIGZAG) == 64
        assert set(ZIGZAG.tolist()) == set(range(64))

    def test_zigzag_jpeg_standard_forward_order(self):
        """钉死正向 zigzag（序号→自然索引），防逆置换回潮。

        真值独立取自 JPEG Annex F.2.1 / meteor_decode jpeg.c::_zigzag_lut。
        历史 bug：表曾填成逆置换，round-trip 仍自洽（gather/scatter 互逆）
        但真录 JPEG 系数错位——只有对拍标准序才能揪出。
        """
        standard = [
            0, 1, 8, 16, 9, 2, 3, 10,
            17, 24, 32, 25, 18, 11, 4, 5,
            12, 19, 26, 33, 40, 48, 41, 34,
            27, 20, 13, 6, 7, 14, 21, 28,
            35, 42, 49, 56, 57, 50, 43, 36,
            29, 22, 15, 23, 30, 37, 44, 51,
            58, 59, 52, 45, 38, 31, 39, 46,
            53, 60, 61, 54, 47, 55, 62, 63,
        ]
        assert ZIGZAG.tolist() == standard
        # 语义锚：第 2 个 zigzag 系数落在自然位置 8（自然序第 1 行第 0 列）
        assert ZIGZAG[2] == 8


class TestConstants:
    def test_quant_table_shape(self):
        assert QUANT_TABLE.shape == (64,)
        assert QUANT_TABLE[0] == 16  # JPEG 标准表左上角
