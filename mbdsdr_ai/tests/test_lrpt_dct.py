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


class TestConstants:
    def test_quant_table_shape(self):
        assert QUANT_TABLE.shape == (64,)
        assert QUANT_TABLE[0] == 16  # JPEG 标准表左上角
