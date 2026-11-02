# SPDX-License-Identifier: MIT
"""CCSDS BCH CLTU 编码的官方 golden 与输入边界测试。"""
import json
from pathlib import Path

import pytest

from mbdsdr_ai.tc_cltu import tc_cltu_encode

GOLDEN = (Path(__file__).resolve().parents[2] / "tests" / "golden" /
          "ccsds_cltu_golden.json")
START = bytes.fromhex("EB90")
TAIL = bytes.fromhex("C5C5C5C5C5C5C579")


@pytest.fixture(scope="module")
def golden():
    assert GOLDEN.is_file(), f"golden 缺失: {GOLDEN}"
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.mark.parametrize("randomize", [False, True],
                         ids=["unrandomized", "randomized"])
def test_annex_f_example_10_byte_exact(golden, randomize):
    """官方 Annex F Example 10 双向量必须逐字节一致。"""
    vector = next(item for item in golden["vectors"]
                  if item["randomize"] is randomize)
    actual = tc_cltu_encode(bytes.fromhex(golden["input_frame_hex"]),
                            randomize=randomize)
    assert actual == bytes.fromhex(vector["cltu_hex"])


@pytest.mark.parametrize("randomize", [False, True])
def test_start_and_tail_are_never_randomized(randomize):
    encoded = tc_cltu_encode(b"\x00", randomize=randomize)
    assert encoded[:2] == START
    assert encoded[-8:] == TAIL


def test_last_block_uses_55_fill_and_zero_filler_bit():
    encoded = tc_cltu_encode(b"\xAA")
    assert encoded[2:9] == bytes.fromhex("AA555555555555")
    assert encoded[9] & 1 == 0


def test_randomized_last_block_fill_remains_unrandomized():
    encoded = tc_cltu_encode(b"\x00", randomize=True)
    assert encoded[2:9] == bytes.fromhex("FF555555555555")


def test_empty_frame_rejected():
    with pytest.raises(ValueError, match="不能为空"):
        tc_cltu_encode(b"")


def test_frame_longer_than_1024_octets_rejected():
    with pytest.raises(ValueError, match="1024"):
        tc_cltu_encode(bytes(1025))


def test_1024_octet_boundary_accepted():
    encoded = tc_cltu_encode(bytes(1024))
    assert len(encoded) == 2 + 147 * 8 + 8


@pytest.mark.parametrize("invalid", [
    bytearray(b"\x00"),
    memoryview(b"\x00"),
    [0],
    "00",
    0,
    None,
])
def test_frame_input_requires_exact_bytes(invalid):
    with pytest.raises(TypeError, match="bytes"):
        tc_cltu_encode(invalid)


@pytest.mark.parametrize("invalid", [0, 1, None, "false"])
def test_randomize_requires_exact_bool(invalid):
    with pytest.raises(TypeError, match="bool"):
        tc_cltu_encode(b"\x00", randomize=invalid)
