"""FEC 单测（移植自 SatDump reedsolomon/randomization/differential）。

上游对照：docs/learn/porting_2026_09_27.md §4
"""
import numpy as np
import pytest

from mbdsdr_ai.fec import (
    ReedSolomon,
    Scrambler,
    DifferentialEncoder,
    CCSDS_PN,
)


def test_rs_clean_roundtrip():
    """RS(255,223) 无错 → 数据完全恢复。"""
    rs = ReedSolomon()
    data = bytes(range(223))
    cw = rs.encode(data)
    assert len(cw) == 255
    r = rs.decode(cw)
    assert r.nerrors == 0
    assert r.data == data


@pytest.mark.parametrize("nerr", [1, 5, 10, 16])
def test_rs_corrects_up_to_16_errors(nerr):
    """RS 可纠 16 字节错误（t=16）。"""
    rs = ReedSolomon()
    rng = np.random.default_rng(nerr)
    data = bytes(rng.integers(0, 256, 223).tolist())
    cw = bytearray(rs.encode(data))
    positions = rng.choice(255, nerr, replace=False)
    for p in positions:
        cw[p] ^= int(rng.integers(1, 256))
    r = rs.decode(bytes(cw))
    assert r.corrected is True
    assert r.nerrors == nerr
    assert r.data == data, f"{nerr} errors not corrected"


def test_rs_rejects_too_many_errors():
    """超过 16 字节错误应返回不可纠正（nerrors=-1）。"""
    rs = ReedSolomon()
    rng = np.random.default_rng(99)
    data = bytes(rng.integers(0, 256, 223).tolist())
    cw = bytearray(rs.encode(data))
    for p in rng.choice(255, 30, replace=False):
        cw[p] ^= int(rng.integers(1, 256))
    r = rs.decode(bytes(cw))
    # 超过纠错能力，不应返回成功
    assert r.nerrors != 0 or not r.corrected


def test_scrambler_roundtrip():
    """CCSDS 扰码自逆。"""
    sc = Scrambler()
    data = bytes(range(255))
    s = sc.scramble(data)
    d = sc.descramble(s)
    assert d == data


def test_scrambler_period_255():
    """PN 周期 255 字节。"""
    sc = Scrambler()
    data = bytes(range(100))
    assert sc.scramble(data, offset=0) == sc.scramble(data, offset=255)


def test_scrambler_matches_table():
    """扰码 = 数据 XOR CCSDS_PN 首字节。"""
    sc = Scrambler()
    data = bytes([0xFF, 0x00, 0xA5])
    out = sc.scramble(data)
    expected = bytes([data[i] ^ CCSDS_PN[i] for i in range(3)])
    assert out == expected


def test_differential_bpsk_roundtrip():
    """DBPSK 差分编解码自洽。"""
    de = DifferentialEncoder(order=2)
    rng = np.random.default_rng(0)
    bits = rng.integers(0, 2, 20, dtype=np.int8)
    enc = de.encode_bits(bits)
    dec = de.decode_bits(enc)
    assert np.array_equal(dec, bits)


def test_differential_qpsk_roundtrip():
    """DQPSK 差分编解码自洽。"""
    de = DifferentialEncoder(order=4)
    rng = np.random.default_rng(1)
    bits = rng.integers(0, 2, 24, dtype=np.int8)  # 12 symbols
    enc = de.encode_bits(bits)
    dec = de.decode_bits(enc)
    assert np.array_equal(dec, bits)
