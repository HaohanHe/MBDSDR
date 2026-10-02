# SPDX-License-Identifier: MIT
"""FEC 单测。

依据公开编码标准（Reed-Solomon / 随机化 / 差分编码）独立实现；
SatDump 仅作技术参考，未引用其代码。
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


# ---------------------------------------------------------------------------
# fsphil/ssdv 兼容 KAT（根生成元 gamma = alpha^11，本原多项式 0x187，无 0xFF 反转）
# 参考向量由 Phil Karn rs8.c（FCR=112, PRIM=11, GENPOLY 0x187 域）编译生成，
# 仅用于验证事实；GPL/LGPL 参考源码不入库。
# ---------------------------------------------------------------------------

# 消息 msg[i]=i (0..222) 经 fsphil rs8.c encode_rs_8 编码得到的 32 字节校验
SSDV_RS_REF_PARITY = bytes.fromhex(
    "2fbd4fb4748494b9acd554627212eeb3ebed41191de1d36320ea49290b25abcf"
)


def _ssdv_rs() -> ReedSolomon:
    return ReedSolomon(nsym=32, fcr=112, prim=11,
                       prim_poly=0x187, ccsds_invert=False)


def test_rs_ssdv_encode_matches_fsphil_kat():
    """prim=11 编码校验字节与 fsphil rs8.c 参考向量逐字节一致。"""
    rs = _ssdv_rs()
    cw = rs.encode(bytes(range(223)))
    assert cw[223:] == SSDV_RS_REF_PARITY


@pytest.mark.parametrize("nerr", [3, 12, 16])
def test_rs_ssdv_corrects_fsphil_codeword(nerr):
    """prim=11 解码可纠正 fsphil 码字上的符号错误（t=16）。"""
    rs = _ssdv_rs()
    data = bytes(range(223))
    cw = bytearray(rs.encode(data))  # 与 fsphil 编码一致
    for i in range(nerr):
        cw[(i * 13) % 255] ^= (0x11 + i)  # 步长 13 保证 16 个位置互不重叠
    r = rs.decode(bytes(cw))
    assert r.nerrors == nerr
    assert r.corrected is True
    assert r.data == data


def test_rs_ssdv_prim1_backward_compatible():
    """prim=1 保持标准 CCSDS 行为（默认参数不受 prim 修复影响）。"""
    rs_default = ReedSolomon()
    rs_prim1 = ReedSolomon(prim=1, ccsds_invert=True)
    data = bytes(range(223))
    assert rs_default.encode(data) == rs_prim1.encode(data)
    cw = bytearray(rs_default.encode(data))
    for i in range(8):
        cw[i * 31] ^= (0x10 + i)
    r = rs_prim1.decode(bytes(cw))
    assert r.nerrors == 8
    assert r.data == data


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
