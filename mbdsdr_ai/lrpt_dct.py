# SPDX-License-Identifier: MIT
"""LRPT 图像 DCT/IDCT 压缩域（最小集，Python 侧）。

依据 SatDump meteor_support 机制（file:line 证据见 lrpt-image-decode-eval.md）
自述重写。本轮范围：
  - 8×8 前向/反向 DCT（scipy.fftpack，准确实现）
  - JPEG 标准量化表 + Zigzag 扫描
  - 量化/反量化 round-trip 验证

诚实边界：Huffman 熵编码（DC 12 类 + AC 162 类）留待后续轮；
本轮直接操作 DCT 系数域，验证 DCT/IDCT/量化路径。
"""
from __future__ import annotations

import numpy as np
from scipy.fftpack import dct as scipy_dct, idct as scipy_idct

__all__ = [
    "QUANT_TABLE",
    "ZIGZAG",
    "dct_8x8",
    "idct_8x8",
    "quantize",
    "dequantize",
    "zigzag_order",
    "unzigzag_order",
]

# --------------------------------------------------------------------------- #
# JPEG 标准亮度量化表（tables.h:16-25）
# --------------------------------------------------------------------------- #
QUANT_TABLE: np.ndarray = np.array([
    16, 11, 10, 16, 24, 40, 51, 61,
    12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56,
    14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68, 109, 103, 77,
    24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101,
    72, 92, 95, 98, 112, 100, 103, 99,
], dtype=np.float64)

# --------------------------------------------------------------------------- #
# Zigzag 扫描顺序（tables.h:28-37）
# --------------------------------------------------------------------------- #
ZIGZAG: np.ndarray = np.array([
    0, 1, 5, 6, 14, 15, 27, 28,
    2, 4, 7, 13, 16, 26, 29, 42,
    3, 8, 12, 17, 25, 30, 41, 43,
    9, 11, 18, 24, 31, 40, 44, 53,
    10, 19, 23, 32, 39, 45, 52, 54,
    20, 22, 33, 38, 46, 51, 55, 60,
    21, 34, 37, 47, 50, 56, 59, 61,
    35, 36, 48, 49, 57, 58, 62, 63,
], dtype=np.int64)


# --------------------------------------------------------------------------- #
# 8×8 前向 / 反向 DCT（scipy DCT-II/III，ortho 归一化）
# --------------------------------------------------------------------------- #
def dct_8x8(block: np.ndarray) -> np.ndarray:
    """8×8 前向 DCT（2D DCT-II，ortho 归一化）。

    输入: (8,8) float，像素域（0..255，内部减 128）。
    输出: (8,8) float，DCT 系数域。
    """
    x = block.astype(np.float64) - 128.0
    # 2D DCT: 行 + 列
    return scipy_dct(scipy_dct(x, axis=0, norm="ortho"), axis=1, norm="ortho")


def idct_8x8(coeffs: np.ndarray) -> np.ndarray:
    """8×8 反向 DCT（2D DCT-III，ortho 归一化）。

    输入: (8,8) float，DCT 系数域。
    输出: (8,8) float，像素域（已 +128，clamp 0..255）。
    """
    y = scipy_idct(scipy_idct(coeffs.astype(np.float64), axis=0, norm="ortho"),
                   axis=1, norm="ortho")
    return np.clip(y + 128.0, 0, 255)


# --------------------------------------------------------------------------- #
# 量化 / 反量化（huffman.cpp:13-29 GetQuantizationTable 机制）
# --------------------------------------------------------------------------- #
def quantize(coeffs: np.ndarray, qf: float = 50.0) -> np.ndarray:
    """DCT 系数量化。"""
    if qf >= 20 and qf < 50:
        q = 5000.0 / qf
    else:
        q = 200.0 - 2.0 * qf
    table = np.clip(q / 100.0 * QUANT_TABLE, 1, None).reshape(8, 8)
    return np.round(coeffs / table).astype(np.int64)


def dequantize(coeffs_q: np.ndarray, qf: float = 50.0) -> np.ndarray:
    """反量化。"""
    if qf >= 20 and qf < 50:
        q = 5000.0 / qf
    else:
        q = 200.0 - 2.0 * qf
    table = np.clip(q / 100.0 * QUANT_TABLE, 1, None).reshape(8, 8)
    return coeffs_q.astype(np.float64) * table


# --------------------------------------------------------------------------- #
# Zigzag 扫描
# --------------------------------------------------------------------------- #
def zigzag_order(block: np.ndarray) -> np.ndarray:
    """(8,8) 2D 系数 → (64,) 1D Zigzag 顺序。"""
    flat = block.flatten()
    return flat[ZIGZAG]


def unzigzag_order(zig: np.ndarray) -> np.ndarray:
    """(64,) 1D Zigzag → (8,8) 2D 系数。"""
    out = np.zeros(64, dtype=zig.dtype)
    for i, idx in enumerate(ZIGZAG):
        out[idx] = zig[i]
    return out.reshape(8, 8)
