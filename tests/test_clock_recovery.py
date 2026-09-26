"""时钟恢复单测。

合成已知符号率的 FSK / BPSK / OOK 信号，验证 Gardner 与 EarlyLateGate
能恢复出正确符号。
"""
import numpy as np

from mbdsdr_ai.analysis.clock_recovery import (
    GardnerClockRecovery,
    EarlyLateGate,
)


SR = 1_000_000.0
FC = 50_000.0
SPB = 10  # samples per symbol


def _make_fsk_bits(bits, df=30_000.0):
    n = len(bits) * SPB
    t = np.arange(n) / SR
    out = np.zeros(n, dtype=complex)
    for i, b in enumerate(bits):
        f = FC + (df if b else -df)
        sl = slice(i * SPB, (i + 1) * SPB)
        out[sl] = np.exp(1j * 2 * np.pi * f * t[sl])
    return out


def _discriminate(iq):
    """FSK 正交鉴频 → 双极性基带。"""
    dphase = np.diff(np.unwrap(np.angle(iq)))
    return np.sign(dphase)


def test_early_late_recovers_fsk_symbols():
    """合成 FSK（已知 sps=10），鉴频后用 EarlyLateGate 恢复符号。"""
    bits = np.array([1, 0, 1, 1, 0, 0, 1, 0, 1, 1, 0, 1, 0, 0, 1, 1], dtype=int)
    fsk = _make_fsk_bits(bits)
    disc = _discriminate(fsk).astype(complex)

    elg = EarlyLateGate(samples_per_symbol=SPB, loop_gain=0.0)  # 已知 sps，开环
    elg.feed(disc)
    got = elg.bits().tolist()[:len(bits)]
    assert got == bits.tolist(), f"Expected {bits.tolist()}, got {got}"


def test_gardner_recovers_baseband_bpsk():
    """Gardner 对基带 BPSK 恢复符号。"""
    bits = np.array([1, 0, 1, 1, 0, 0, 1, 0, 1, 1], dtype=int)
    n = len(bits) * SPB
    t = np.arange(n) / SR
    # 基带 BPSK（无载波，±1）
    bpsk = np.exp(1j * np.pi * np.repeat(bits, SPB))
    # 搬到中频便于处理，但 Gardner 用实部
    bpsk_bb = np.repeat(bits, SPB).astype(float) * 2 - 1  # ±1

    gc = GardnerClockRecovery(samples_per_symbol=SPB, loop_gain=0.02)
    gc.feed(bpsk_bb.astype(complex))
    got = gc.bits().tolist()[:len(bits)]
    # 允许前导 1-2 个符号的收敛误差
    match = sum(a == b for a, b in zip(got[1:], bits[1:]))
    assert match >= len(bits) - 2, f"Expected ~{bits.tolist()}, got {got}"


def test_gardner_recovers_baseband_ook():
    """Gardner 对基带 OOK（0/1）恢复符号。"""
    bits = np.array([1, 0, 1, 1, 0, 0, 1, 0, 1, 1], dtype=int)
    ook_bb = np.repeat(bits, SPB).astype(complex)

    gc = GardnerClockRecovery(samples_per_symbol=SPB, loop_gain=0.01)
    gc.feed(ook_bb)
    got = gc.bits(threshold=0.5).tolist()[:len(bits)]
    assert got == bits.tolist(), f"Expected {bits.tolist()}, got {got}"


def test_symbol_count_matches():
    """恢复出的符号数量应接近预期符号数。"""
    bits = np.array([1, 0, 1, 1, 0, 0, 1, 0, 1, 1, 0, 1, 0, 0, 1, 1], dtype=int)
    fsk = _make_fsk_bits(bits)
    disc = _discriminate(fsk).astype(complex)
    elg = EarlyLateGate(samples_per_symbol=SPB, loop_gain=0.0)
    elg.feed(disc)
    # 鉴频少一个样本，所以符号数应接近 len(bits)
    assert abs(len(elg.symbols) - len(bits)) <= 2


def test_reset_clears_state():
    bits = np.array([1, 0, 1, 1, 0, 0], dtype=int)
    ook_bb = np.repeat(bits, SPB).astype(complex)
    gc = GardnerClockRecovery(samples_per_symbol=SPB)
    gc.feed(ook_bb)
    assert len(gc.symbols) > 0
    gc.reset()
    assert len(gc.symbols) == 0
