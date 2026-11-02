# SPDX-License-Identifier: MIT
"""经典 WSPR Type 1 位级编解码测试。

真值不是本实现自编自解：下列打包字节和符号均由 WSJT 官方仓库
b4f9a431bcf6449df8f37b56de79d48b665b044c 的未修改 wsprsim 编码源生成，
原始控制台输出归档为 standards/wsjt/wspr_official_vectors_raw.txt。
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from mbdsdr_ai.wspr_codec import (  # noqa: E402
    GENERATOR_POLYNOMIALS,
    SYNC_VECTOR,
    WsprMessage,
    convolutional_encode,
    decode_convolutional,
    decode_message,
    decode_symbols,
    deinterleave,
    deinterleave_soft,
    encode_message,
    interleave,
    merge_sync,
    pack_callsign,
    pack_message,
    pack_message_bytes,
    symbols_to_llrs,
    sync_mismatch_count,
    unpack_callsign,
    unpack_message,
)


# (呼号, 网格, dBm, 官方 11 字节, 官方 162 信道符号)
OFFICIAL_VECTORS = [
    (
        "BI1AAA", "FN20", 37, "4D BB 3E 7B 39 D9 40 00 00 00 00",
        """1 3 0 2 2 2 2 0 1 0 0 2 1 3 1 2 0 2 3 2 0 3 2 3 1 1 3 0 2 0 0 0 2 0 3 2 2 3 2 1 0 0 0 0 2 2 1 2 3 3 0 2 3 3 2 1 0 2 0 1 1 0 3 2 2 0 0 1 1 2 3 2 1 0 1 0 3 2 0 1 2 2 3 2 1 3 0 0 0 1 3 0 1 2 3 2 2 2 3 0 0 0 0 0 3 2 2 3 0 2 3 1 1 0 3 3 0 2 3 1 2 3 0 2 2 3 1 3 0 2 2 0 2 1 0 3 0 2 1 1 2 2 0 2 2 0 2 1 1 2 3 2 1 3 0 0 2 3 3 2 0 0""",
    ),
    (
        "BI1AAA", "AA00", 0, "4D BB 3E 7F BB 90 00 00 00 00 00",
        """1 3 0 0 2 2 0 0 1 0 0 0 1 3 3 0 0 2 3 0 0 1 0 3 1 3 3 0 2 2 2 0 2 2 3 0 2 1 0 3 0 0 0 0 2 0 3 0 3 1 0 2 3 3 0 1 0 0 2 1 1 2 3 0 2 0 0 3 1 2 1 2 1 2 1 2 3 0 0 1 2 0 3 0 1 3 0 2 0 3 1 0 1 2 3 0 2 0 3 0 0 0 2 2 3 0 2 1 0 2 3 1 1 2 3 3 0 0 1 1 2 3 0 0 2 1 3 1 0 0 2 0 2 1 0 3 0 0 1 3 2 0 2 0 2 2 2 1 1 2 1 2 1 1 0 2 2 3 3 0 0 2""",
    ),
    (
        "BI1AAA", "RR99", 60, "4D BB 3E 70 16 7F 00 00 00 00 00",
        """1 3 0 2 2 2 2 0 1 0 0 2 1 1 3 2 0 2 3 0 0 3 2 1 1 3 1 2 2 0 2 2 2 0 3 0 2 1 0 1 0 0 0 2 2 0 3 0 3 1 0 2 3 1 2 1 0 2 2 1 1 0 3 0 2 2 0 1 1 2 3 2 1 0 1 0 3 0 2 1 2 0 3 0 1 1 2 2 0 1 3 2 1 2 1 0 2 2 3 0 0 0 0 0 3 2 2 3 0 2 1 3 1 2 3 3 0 0 1 3 2 1 2 0 2 3 3 1 0 0 2 0 2 1 2 3 0 2 1 1 2 0 2 2 2 0 2 1 1 2 1 0 1 3 2 2 2 3 3 0 0 2""",
    ),
    (
        "BI1AAA", "IO90", 23, "4D BB 3E 77 FA 95 C0 00 00 00 00",
        """1 3 0 0 2 2 2 0 1 0 0 0 1 3 1 2 0 0 3 0 0 3 2 1 1 3 1 0 2 2 2 2 2 2 3 0 2 3 2 3 0 0 0 2 2 2 3 0 3 3 0 0 3 1 0 1 0 2 0 1 1 0 1 2 2 0 0 1 1 0 1 2 1 0 1 0 3 0 0 3 2 0 3 0 1 1 2 2 0 3 1 2 1 0 3 0 2 2 3 0 0 0 2 2 3 2 2 3 0 0 3 3 1 0 3 3 0 0 1 1 2 3 0 2 2 1 1 3 0 2 2 0 2 3 0 3 0 0 1 1 2 2 0 0 2 2 2 1 1 2 3 2 1 1 2 0 2 3 1 0 0 2""",
    ),
]


def _symbols(vector):
    values = [int(value) for value in vector[4].split()]
    assert len(values) == 162
    return values


@pytest.mark.parametrize("vector", OFFICIAL_VECTORS, ids=lambda v: f"{v[1]}-{v[2]}")
def test_official_wsjtx_encoding_vector(vector):
    call, grid, power, packed_hex, _ = vector
    expected_symbols = _symbols(vector)
    bits = pack_message(call, grid, power)
    assert len(bits) == 50
    assert pack_message_bytes(call, grid, power) == bytes.fromhex(packed_hex)
    assert encode_message(call, grid, power) == expected_symbols
    assert unpack_message(bits) == WsprMessage(call, grid, power)


@pytest.mark.parametrize("vector", OFFICIAL_VECTORS, ids=lambda v: f"decode-{v[1]}-{v[2]}")
def test_official_wsjtx_symbols_decode(vector):
    call, grid, power, _, _ = vector
    symbols = _symbols(vector)
    decoded_bits = decode_symbols(symbols)
    assert decoded_bits == pack_message(call, grid, power)
    llr = symbols_to_llrs(symbols)
    assert decode_message(llr) == WsprMessage(call, grid, power)


def test_protocol_constants_and_sync_merge():
    assert GENERATOR_POLYNOMIALS == (0xF2D05351, 0xE4613C47)
    assert len(SYNC_VECTOR) == 162 and set(SYNC_VECTOR) == {0, 1}
    data = [(index * 37 + 1) & 1 for index in range(162)]
    symbols = merge_sync(data)
    assert symbols == [2 * bit + sync for bit, sync in zip(data, SYNC_VECTOR)]
    assert sync_mismatch_count(symbols) == 0
    symbols[9] ^= 1  # 仅翻转同步 LSB，不改变数据 MSB。
    assert sync_mismatch_count(symbols) == 1


def test_interleave_and_soft_deinterleave_are_inverse():
    encoded = convolutional_encode(pack_message("BI1AAA", "FN20", 37))
    assert len(encoded) == 162
    interleaved = interleave(encoded)
    assert deinterleave(interleaved) == encoded
    llr = [(-1.0 if bit else 1.0) * (index + 1) / 162
           for index, bit in enumerate(interleaved)]
    # 对每个位置直接按交织结果重建软值，避免用解交织函数生成期望。
    expected = [0.0] * 162
    p = 0
    for address in range(256):
        reversed_address = int(f"{address:08b}"[::-1], 2)
        if reversed_address < 162:
            expected[p] = llr[reversed_address]
            p += 1
    assert deinterleave_soft(llr) == pytest.approx(expected)


def test_data_msb_burst_errors_are_corrected():
    vector = OFFICIAL_VECTORS[3]
    call, grid, power = vector[:3]
    damaged = _symbols(vector)
    for index in (40, 41, 42):
        damaged[index] ^= 2  # 交织域连续错误；同步 LSB 保持原值。
    assert sync_mismatch_count(damaged) == 0
    assert decode_symbols(damaged) == pack_message(call, grid, power)


def test_clean_decode_returns_exact_50_bits():
    bits = pack_message("BI1AAA", "FN20", 37)
    symbols = encode_message("BI1AAA", "FN20", 37)
    decoded = decode_symbols(symbols)
    assert len(decoded) == 50
    assert decoded == bits


@pytest.mark.parametrize("call", [
    "", "B", "BIAAAA", "BI1A2A", "BI1AAAX", "BI1AAA/X", "B I1AA", "BÏ1AA",
])
def test_invalid_callsigns_raise(call):
    with pytest.raises(ValueError):
        pack_callsign(call)


@pytest.mark.parametrize("grid", ["", "FN2", "FN200", "S000", "AS00", "FN2A", "F 20"])
def test_invalid_grids_raise(grid):
    with pytest.raises(ValueError):
        pack_message("BI1AAA", grid, 37)


@pytest.mark.parametrize("power", [-3, 1, 61, 62, 37.0, True])
def test_invalid_powers_raise(power):
    with pytest.raises(ValueError):
        pack_message("BI1AAA", "FN20", power)


def test_unpack_rejects_reserved_or_non_type1_content():
    with pytest.raises(ValueError):
        unpack_callsign(262_177_560)
    with pytest.raises(ValueError):
        unpack_message([0] * 50)
    bits = pack_message("BI1AAA", "FN20", 37)
    # 低 7 bit 改成 ntype=1（power+64=65），不是合法 Type 1 功率。
    bits[-7:] = [(65 >> shift) & 1 for shift in range(6, -1, -1)]
    with pytest.raises(ValueError):
        unpack_message(bits)


@pytest.mark.parametrize("function,args", [
    (convolutional_encode, ([0] * 49,)),
    (convolutional_encode, ([0] * 49 + [2],)),
    (interleave, ([0] * 161,)),
    (deinterleave, ([0] * 161 + [-1],)),
    (merge_sync, ([0] * 162 + [0],)),
    (symbols_to_llrs, ([0] * 161 + [4],)),
    (decode_convolutional, ([0.0] * 161,)),
])
def test_invalid_bit_and_symbol_shapes_raise(function, args):
    with pytest.raises(ValueError):
        function(*args)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, "0"])
def test_llr_rejects_nonfinite_and_nonnumeric(bad):
    llr = [0.0] * 162
    llr[12] = bad
    with pytest.raises(ValueError):
        decode_convolutional(llr)


def test_no_information_and_work_limit_fail_honestly():
    assert decode_convolutional([0.0] * 162) is None
    assert decode_message([0.0] * 162) is None
    llr = symbols_to_llrs(_symbols(OFFICIAL_VECTORS[0]))
    assert decode_convolutional(llr, max_expansions=1) is None
    with pytest.raises(ValueError):
        decode_convolutional(llr, max_expansions=0)
    with pytest.raises(ValueError):
        symbols_to_llrs([0] * 162, confidence=0)
