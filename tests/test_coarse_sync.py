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


def _rrc_taps(beta, span, sps):
    """根升余弦 taps（闭式）。Gardner TED 的适用域是带限（RRC/Nyquist）脉冲。"""
    n = np.arange(-span * sps, span * sps + 1)
    t = n / sps
    taps = np.zeros_like(t, dtype=np.float64)
    for i, ti in enumerate(t):
        if abs(ti) < 1e-12:
            taps[i] = 1.0 - beta + 4.0 * beta / np.pi
        elif beta > 0 and abs(abs(ti) - 1.0 / (4.0 * beta)) < 1e-9:
            taps[i] = (beta / np.sqrt(2.0)) * (
                (1 + 2 / np.pi) * np.sin(np.pi / (4 * beta))
                + (1 - 2 / np.pi) * np.cos(np.pi / (4 * beta)))
        else:
            num = (np.sin(np.pi * ti * (1 - beta))
                   + 4 * beta * ti * np.cos(np.pi * ti * (1 + beta)))
            den = np.pi * ti * (1 - (4 * beta * ti) ** 2)
            taps[i] = num / den
    taps /= np.sqrt(np.sum(taps ** 2))
    return taps


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
    """Gardner 在带限（RRC）BPSK 上恢复 sps 与符号数。

    真值源：Gardner TED ``e=Re{(cur-prev)*conj(mid)}`` 适用于带限脉冲
    （RRC/Nyquist），矩形脉冲不在其域内。旧 fixture 用矩形 BPSK，旧错误
    TED ``(mid-cur)*prev`` 恰在矩形上零偏（sps=8.0000）而侥幸通过；纠正后
    的 TED 在带限信号上收敛到真值（独立 oracle 钉扎见
    mbdsdr_ai/tests/test_gardner_recover.py）。此处把 fixture 改到检测器
    适用域，并把契约从弱符号数代理强化为 sps 精度：旧实现 runaway 到
    11.85≈clamp 天花板（48% 偏），必红。
    """
    sps = 8
    n_sym = 200
    rng = np.random.default_rng(2)
    bits = rng.integers(0, 2, n_sym) * 2 - 1
    up = np.zeros(n_sym * sps, dtype=np.complex128)
    up[np.arange(n_sym) * sps] = bits
    iq = np.convolve(up, _rrc_taps(0.35, 8, sps), mode="same")
    iq /= (np.sqrt(np.mean(np.abs(iq) ** 2)) + 1e-12)
    gr = gardner_recover(iq, sps_guess=sps, loop_gain=0.05)
    # 主契约（强化）：sps 收敛到真值 2% 内（旧实现 runaway 48% 必红）
    assert abs(gr.sps - sps) / sps < 0.02, f"sps={gr.sps:.4f} 偏离真值 {sps}"
    # 符号数：NCO 在带限信号上有小残差，5% 内（旧实现 158 偏 21% 必红）
    assert abs(len(gr.symbols) - n_sym) <= n_sym * 0.05


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
