# SPDX-License-Identifier: MIT
"""Costas 环 + Gardner 定时恢复单测。

依据公开数字通信教科书模型（Costas 环 / Gardner 定时误差检测）独立实现；
SDRangel 仅作技术参考，未引用其代码。
"""
import numpy as np
import pytest

from mbdsdr_ai.coarse_sync import (
    CostasLoop,
    CoarseSync,
    gardner_recover,
)


def _make_bpsk(sps=8, n_sym=300, f_offset=0.1, seed=0):
    rng = np.random.default_rng(seed)
    bits = rng.integers(0, 2, n_sym) * 2 - 1
    syms = np.repeat(bits, sps).astype(float)
    t = np.arange(len(syms))
    iq = syms * np.exp(1j * f_offset * t)
    iq += 0.01 * (rng.standard_normal(len(iq)) + 1j * rng.standard_normal(len(iq)))
    return iq, bits


def test_costas_bpsk_converges():
    """已知 +0.1 rad/sample 频偏的 BPSK，恢复后星座应收敛到实轴。"""
    iq, _ = _make_bpsk()
    loop = CostasLoop(loop_bw=0.1, psk_order=2)
    out = loop.feed(iq)
    # 后半段（收敛后）虚部应接近 0，实部应接近 ±1
    tail = out[len(out) // 2:]
    assert abs(np.mean(tail.imag)) < 0.1
    assert np.std(tail.imag) < 0.15
    # 实部应在 ±1 附近（BPSK）
    assert abs(np.std(tail.real) - 1.0) < 0.3


def test_costas_qpsk_converges():
    """QPSK 星座收敛到 4 个象限。"""
    rng = np.random.default_rng(1)
    sps = 8
    n_sym = 300
    sym_idx = rng.integers(0, 4, n_sym)
    const = np.array([1 + 1j, 1 - 1j, -1 + 1j, -1 - 1j]) / np.sqrt(2)
    syms = np.repeat(const[sym_idx], sps)
    t = np.arange(len(syms))
    iq = syms * np.exp(1j * 0.08 * t)
    iq += 0.01 * (rng.standard_normal(len(iq)) + 1j * rng.standard_normal(len(iq)))
    loop = CostasLoop(loop_bw=0.1, psk_order=4)
    out = loop.feed(iq)
    tail = out[len(out) // 2:]
    # 收敛后 I/Q 应都在 ±0.7 附近
    assert abs(np.std(tail.imag) - 0.707) < 0.2
    assert abs(np.std(tail.real) - 0.707) < 0.2


def test_gardner_timing():
    """Gardner 恢复出的符号数与预期一致。"""
    sps = 8
    n_sym = 200
    rng = np.random.default_rng(2)
    bits = rng.integers(0, 2, n_sym) * 2 - 1
    syms = np.repeat(bits, sps).astype(float)
    gr = gardner_recover(syms.astype(complex), sps_guess=sps, loop_gain=0.05)
    # 恢复出的符号数应接近 n_sym
    assert abs(len(gr.symbols) - n_sym) <= 5


def test_coarse_sync_auto_modulation():
    """CoarseSync(modulation='auto') 能自动选调制并收敛。"""
    iq, _ = _make_bpsk(seed=3)
    cs = CoarseSync(modulation="auto", sample_rate=1.0,
                    samples_per_symbol=8, loop_bw=0.1)
    res = cs.process(iq)
    assert res.modulation in ("BPSK", "PSK", "OOK", "QPSK")
    # 收敛后信号幅度应接近常数（载波环已锁定频偏）
    tail = res.synced_iq[len(res.synced_iq) // 2:]
    mag = np.abs(tail)
    # 原始 BPSK 经 QPSK 环可能转到对角，但幅度波动应小
    assert np.std(mag) / (np.mean(mag) + 1e-9) < 0.3


def test_coarse_sync_known_offset_recovery():
    """已知偏移的 BPSK 信号，Costas 环应把载波频偏收敛到接近 0。"""
    sps = 8
    n_sym = 400
    rng = np.random.default_rng(4)
    bits = rng.integers(0, 2, n_sym)
    bpsk = np.where(bits == 1, 1.0, -1.0)
    tx = np.repeat(bpsk, sps)
    t = np.arange(len(tx))
    # 频偏 + 相位偏
    iq = tx * np.exp(1j * (0.12 * t + 0.5))
    cs = CoarseSync(modulation="BPSK", sample_rate=1.0,
                    samples_per_symbol=sps, loop_bw=0.12)
    res = cs.process(iq)
    # 收敛后：信号应聚到实轴附近（BPSK 2 星座点）
    tail = res.synced_iq[len(res.synced_iq) // 2:]
    # 虚部应远小于实部（载波恢复后 BPSK 在实轴）
    assert np.std(tail.imag) < 0.25, f"imag std={np.std(tail.imag)}"
    # 实部应在 ±1 附近
    assert abs(np.std(tail.real) - 1.0) < 0.3
