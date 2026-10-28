# SPDX-License-Identifier: MIT
"""
constellation.py（星座/EVM 统计）行为测试
=========================================

合成 QPSK 符号钉行为，oracle 来自被测模块的真实语义：
  - EVM 归一化后到理想点 (±1±j)/√2 的 RMS 误差（完美星座 = 0%）；
  - 聚类中心为各象限符号均值（四舍五入 3 位）；
  - 幅度缩放不变（功率归一化）；噪声增大 EVM 单调增大（固定 seed）；
  - 空输入抛 ValueError（旧实现静默返回 NaN）；
  - scatter_points：去 DC + 单位方差归一 + 重采样。

运行：python -m pytest mbdsdr_ai/tests/test_constellation_behavior.py -q
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai.constellation import evm_qpsk, scatter_points  # noqa: E402

IDEAL = np.array([1 + 1j, -1 + 1j, -1 - 1j, 1 - 1j]) / np.sqrt(2)


def _qpsk(n_reps: int = 25) -> np.ndarray:
    return np.tile(IDEAL, n_reps)


# ─────────────────────────── evm_qpsk ───────────────────────────

def test_evm_perfect_qpsk_zero_and_four_clusters():
    r = evm_qpsk(_qpsk())
    assert r["evm_rms_pct"] == 0.0
    assert r["symbol_count"] == 100
    centers = r["cluster_centers"]
    assert len(centers) == 4
    assert {"re": 0.707, "im": 0.707} in centers
    assert {"re": -0.707, "im": -0.707} in centers
    assert {"re": -0.707, "im": 0.707} in centers
    assert {"re": 0.707, "im": -0.707} in centers


def test_evm_cluster_centers_follow_used_quadrants():
    sym = np.array([1 + 1j, -1 - 1j] * 20) / np.sqrt(2)
    r = evm_qpsk(sym)
    assert r["evm_rms_pct"] == 0.0
    assert r["cluster_centers"] == [
        {"re": 0.707, "im": 0.707}, {"re": -0.707, "im": -0.707}]


def test_evm_scale_invariant():
    # 功率归一化：整体幅度缩放不改变 EVM
    base = _qpsk() + 0.05 * (np.random.default_rng(0).standard_normal(100)
                             + 1j * np.random.default_rng(1).standard_normal(100))
    r1 = evm_qpsk(base)
    r2 = evm_qpsk(137.0 * base)
    assert r1["evm_rms_pct"] == r2["evm_rms_pct"]


def test_evm_increases_with_noise():
    clean = _qpsk()
    rng_i = np.random.default_rng(2)
    rng_q = np.random.default_rng(3)
    small = clean + 0.05 * (rng_i.standard_normal(100)
                            + 1j * rng_q.standard_normal(100))
    rng_i = np.random.default_rng(2)
    rng_q = np.random.default_rng(3)
    big = clean + 0.4 * (rng_i.standard_normal(100)
                         + 1j * rng_q.standard_normal(100))
    r_small = evm_qpsk(small)["evm_rms_pct"]
    r_big = evm_qpsk(big)["evm_rms_pct"]
    assert 0.0 < r_small < r_big


def test_evm_all_zero_degenerate_single_cluster():
    # 零功率输入：归一化后落在原点，如实报 100% EVM + 单簇 (0,0)
    r = evm_qpsk(np.zeros(4, dtype=complex))
    assert r["evm_rms_pct"] == 100.0
    assert r["symbol_count"] == 4
    assert r["cluster_centers"] == [{"re": 0.0, "im": 0.0}]


def test_evm_empty_raises_not_nan():
    # 旧实现对空输入静默返回 NaN（静默吞错）
    with pytest.raises(ValueError):
        evm_qpsk(np.zeros(0, dtype=complex))


# ─────────────────────────── scatter_points ───────────────────────────

def test_scatter_points_empty_state():
    r = scatter_points(np.zeros(0, dtype=complex))
    assert r == {"points": [], "n_points": 0}


def test_scatter_points_dc_removal_and_normalization():
    # 常量输入去 DC 后全零 -> 全 (0,0)；[2,-2] 归一到单位方差
    r = scatter_points(np.array([3 + 3j] * 5))
    assert r["points"] == [[0.0, 0.0]] * 5
    r2 = scatter_points(np.array([2 + 0j, -2 + 0j]))
    assert r2["points"] == [[1.0, 0.0], [-1.0, 0.0]]


def test_scatter_points_resample_count():
    x = np.arange(100, dtype=float) + 1j * np.arange(100, dtype=float)
    r = scatter_points(x, n_points=10)
    assert r["n_points"] == 10
    assert len(r["points"]) == 10
    # 点数不足时不补齐
    r2 = scatter_points(x[:3], n_points=10)
    assert r2["n_points"] == 3


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
