#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
QPSK 解调链路测试（合成 IQ roundtrip）
=====================================

mbdsdr_ai/demod.py 是完整 QPSK 解调链（匹配滤波 → Costas → Gardner → 判决），
此前零测试。数字链一旦误码/失锁，上层协议全错。用合成 IQ 做端到端 roundtrip：
调制 → RRC 成型 →（可选噪声/频偏）→ 解调 → 比特比对。

运行: python3 -m pytest mbdsdr_ai/tests/test_demod_qpsk.py -v
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.demod import (  # noqa: E402
    CostasLoop, GardnerTimingRecovery, QPSKDemodulator,
    matched_filter, rrc_filter,
)


SPS = 4


def _rng(seed: int) -> np.random.Generator:
    """每条用例独立种子，结果可复现、不受执行顺序影响。"""
    return np.random.default_rng(seed)


def _modulate_qpsk(bits: np.ndarray) -> np.ndarray:
    """按 demod.QPSKDemodulator.symbols_to_bits 的逆映射调制。"""
    assert len(bits) % 2 == 0
    symbols = []
    for i in range(0, len(bits), 2):
        I = 1.0 if bits[i] else -1.0
        Q = 1.0 if bits[i + 1] else -1.0
        symbols.append(complex(I, Q) / np.sqrt(2))
    return np.asarray(symbols, dtype=np.complex128)


def _tx_iq(bits: np.ndarray, *, noise_sigma: float = 0.0,
           freq_offset: float = 0.0,
           rng: np.random.Generator | None = None) -> np.ndarray:
    """比特 → 成型后 IQ 采样（可选噪声/载波频偏）。"""
    syms = _modulate_qpsk(bits)
    # 上采样：符号间插 SPS-1 个零
    up = np.zeros(len(syms) * SPS, dtype=np.complex128)
    up[::SPS] = syms
    # RRC 脉冲成型
    taps = rrc_filter(num_taps=101, sps=SPS, beta=0.35)
    shaped = np.convolve(up, taps, mode="same")
    t = np.arange(len(shaped))
    if freq_offset:
        shaped = shaped * np.exp(2j * np.pi * freq_offset * t / SPS)
    if noise_sigma:
        rng = rng if rng is not None else _rng(1)
        shaped = shaped + (noise_sigma / np.sqrt(2)) * (
            rng.standard_normal(len(shaped))
            + 1j * rng.standard_normal(len(shaped)))
    return shaped.astype(np.complex64)


def _ber(a: np.ndarray, b: np.ndarray) -> float:
    assert len(a) == len(b)
    return float(np.mean(a != b))


def test_rrc_filter_shape_and_symmetry():
    taps = rrc_filter(num_taps=101, sps=4, beta=0.35)
    assert taps.shape == (101,)
    # 对称性（线性相位）
    assert np.allclose(taps, taps[::-1], atol=1e-9), "RRC 抽头必须对称"
    # 能量非零、有限
    assert np.all(np.isfinite(taps))
    assert abs(taps).sum() > 0


def _aligned_ber(tx_bits: np.ndarray, rx_bits: np.ndarray,
                 start: int, length: int, search: int = 12) -> float:
    """在 ±search 比特窗口内做帧对齐后算 BER（模拟真机帧同步）。

    滤波器群延迟与环路收敛会造成固定偏移，不对齐就误报全错。
    """
    ref = tx_bits[start:start + length]
    best = 1.0
    for off in range(-search, search + 1):
        got = rx_bits[start + off:start + off + length]
        if len(got) != len(ref):
            continue
        best = min(best, float(np.mean(ref != got)))
    return best


def test_qpsk_roundtrip_clean_channel():
    """无噪声无频偏：解调必须近乎零误码（含帧对齐后全程）。"""
    n_symbols = 1200
    bits = _rng(11).integers(0, 2, size=n_symbols * 2).astype(np.uint8)
    iq = _tx_iq(bits, noise_sigma=0.0)

    d = QPSKDemodulator(sps=SPS, beta=0.35, num_taps=101)
    symbols = d.demodulate(iq)
    rx_bits = d.symbols_to_bits(symbols)

    # 前 200 比特让环路收敛，其后全程测
    ber = _aligned_ber(bits, rx_bits, start=200,
                       length=len(bits) - 200 - 200)
    assert ber < 0.005, f"干净信道 BER 必须极低，实际 {ber:.4f}"


def _find_offset(tx_bits: np.ndarray, rx_bits: np.ndarray,
                 start: int, length: int, search: int = 12) -> int:
    """在 ±search 比特窗口内找帧对齐偏移（用前段做帧同步）。"""
    ref = tx_bits[start:start + length]
    best_off, best = 0, 1.0
    for off in range(-search, search + 1):
        got = rx_bits[start + off:start + off + length]
        if len(got) != len(ref):
            continue
        b = float(np.mean(ref != got))
        if b < best:
            best, best_off = b, off
    return best_off


def test_qpsk_long_stream_stays_locked():
    """长流不许中途失锁（Costas 周期滑移回归钉扎）。

    帧偏移只在开头对齐一次（真机帧同步语义），其后各段不允许重新对齐，
    否则会掩盖缓慢漂移。
    """
    n_symbols = 2500
    bits = _rng(22).integers(0, 2, size=n_symbols * 2).astype(np.uint8)
    iq = _tx_iq(bits, noise_sigma=0.0)

    d = QPSKDemodulator(sps=SPS, beta=0.35, num_taps=101)
    symbols = d.demodulate(iq)
    rx_bits = d.symbols_to_bits(symbols)

    # 帧同步：用第 0 段定偏移，之后全程固定
    seg_bits = 800
    off = _find_offset(bits, rx_bits, start=200, length=seg_bits)
    for i in range(5):
        start = 200 + i * seg_bits
        ref = bits[start:start + seg_bits]
        got = rx_bits[start + off:start + off + seg_bits]
        ber = float(np.mean(ref != got))
        assert ber < 0.02, \
            f"第 {i} 段失锁/漂移？BER={ber:.4f}（固定偏移 {off}）"


def test_qpsk_roundtrip_with_small_noise():
    """小噪声下链路仍要收敛（解调器不能一有噪声就崩）。"""
    n_symbols = 1200
    rng = _rng(33)
    bits = rng.integers(0, 2, size=n_symbols * 2).astype(np.uint8)
    iq = _tx_iq(bits, noise_sigma=0.05, rng=rng)

    d = QPSKDemodulator(sps=SPS, beta=0.35, num_taps=101)
    symbols = d.demodulate(iq)
    rx_bits = d.symbols_to_bits(symbols)

    ber = _aligned_ber(bits, rx_bits, start=200,
                       length=len(bits) - 200 - 200)
    assert ber < 0.05, f"小噪声下 BER 应受控，实际 {ber:.4f}"


def test_symbols_to_bits_mapping_matches_doc():
    """映射契约：(+1,+1)->11, (-1,+1)->01, (-1,-1)->00, (+1,-1)->10。"""
    d = QPSKDemodulator()
    s = np.array([1 + 1j, -1 + 1j, -1 - 1j, 1 - 1j], dtype=complex) / np.sqrt(2)
    bits = d.symbols_to_bits(s)
    assert bits.tolist() == [1, 1, 0, 1, 0, 0, 1, 0]


def test_costas_loop_pulls_small_phase_error():
    """Costas 环：给定相位旋转的载波，输出应向零相位收敛。"""
    loop = CostasLoop(noise_bw=0.01)
    # 先喂若干纯实符号建立跟踪
    for _ in range(200):
        loop.work(complex(1.0, 0.0))
    # 施加小相位误差
    phase = 0.2
    rotated = np.exp(1j * phase) * np.ones(400, dtype=np.complex128)
    out, _ = loop.work_batch(rotated)
    # 后段输出相位应显著小于初始误差
    final_phase = float(np.angle(out[-1]))
    assert abs(final_phase) < abs(phase), \
        f"Costas 应拉小相位误差，实际末端相位 {final_phase:.3f}"


def test_gardner_produces_one_symbol_per_input_symbol():
    """Gardner 位同步：N 个符号的输入应产出 N 量级的符号（不多不少到 2 倍内）。"""
    g = GardnerTimingRecovery(sps=SPS, mu=0.0, gain_mu=0.05)
    n = 200
    rng = _rng(44)
    syms = np.zeros(n * SPS, dtype=np.complex128)
    syms[::SPS] = (rng.integers(0, 2, n) * 2 - 1) \
        + 1j * (rng.integers(0, 2, n) * 2 - 1)
    # 简易成型（矩形）避免全零输出
    out = g.work_batch(syms)
    assert out is not None
    assert 0.3 * n <= len(out) <= 2.0 * n, \
        f"符号数应与输入同量级，实际 {len(out)}（输入 {n} 符号）"


def test_matched_filter_preserves_length_roughly():
    rng = _rng(55)
    iq = (rng.standard_normal(2000) + 1j * rng.standard_normal(2000)).astype(np.complex64)
    out = matched_filter(iq, num_taps=101, sps=4, beta=0.35)
    assert abs(len(out) - len(iq)) <= 2, "匹配滤波不应大幅改变长度"
    assert np.all(np.isfinite(out))
