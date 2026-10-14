# SPDX-License-Identifier: MIT
"""Meteor Segment 重组测试（RFC 2435 fragment_offset 口径）。"""
import pytest

from mbdsdr_ai.lrpt_huffman import LrptJpegUnsupportedError
from mbdsdr_ai.lrpt_vcdu import reassemble_meteor_scan


def _seg(frag: int, q: int = 32, width: int = 1024,
         height: int = 320, payload: bytes = b"\xAA\xBB\xCC") -> bytes:
    head = bytes([0x00, 0x00, (frag >> 16) & 0xFF, frag & 0xFF])
    if frag == 0:
        dim = bytes([(width >> 8) & 0xFF, width & 0xFF,
                     (height >> 8) & 0xFF, height & 0xFF,
                     0x00, q, 0x08, 0x03])
        return head + dim + payload
    return head + payload


def test_ordered_segments_reassemble():
    """fragment_offset 为扫描流内位置：首段 payload 2 字节 -> 续段 offset=2。"""
    a = bytes([0, 0, 0, 0, 4, 0, 1, 0x40, 0x00, 0x20, 0x08, 0x03]) + b"\x01\x02"
    b = bytes([0, 0, 0, 2]) + b"\x03\x04"
    r = reassemble_meteor_scan([a, b])
    assert r.complete is True
    assert r.width == 1024 and r.height == 320
    assert r.q_field == 32
    assert r.scan == b"\x01\x02\x03\x04"
    assert r.num_segments == 2
    assert 0 in r.quant_tables and 1 in r.quant_tables


def test_gap_detected():
    """续段 offset=4 而首段只覆盖 0..1 -> offset 2 处空洞。"""
    a = bytes([0, 0, 0, 0, 4, 0, 1, 0x40, 0x00, 0x20, 0x08, 0x03]) + b"\x01\x02"
    c = bytes([0, 0, 0, 4]) + b"\x05\x06"   # 跳过了 offset 2..3
    r = reassemble_meteor_scan([a, c])
    assert r.complete is False
    assert r.num_gaps == 1
    assert 4 in r.gap_offsets
    assert "空洞" in r.reason


def test_missing_first_segment_rejected():
    b = bytes([0, 0, 0, 4]) + b"\x03\x04"
    r = reassemble_meteor_scan([b])
    assert r.complete is False and "首段" in r.reason


def test_q_over_128_rejected():
    a = bytes([0, 0, 0, 0, 4, 0, 1, 0x40, 0x00, 0x80, 0x08, 0x03]) + b"\x00" * 80
    with pytest.raises(LrptJpegUnsupportedError):
        reassemble_meteor_scan([a])


def test_q_out_of_range_reported():
    a = bytes([0, 0, 0, 0, 4, 0, 1, 0x40, 0x00, 0x00, 0x08, 0x03]) + b"\x01"
    r = reassemble_meteor_scan([a])
    assert r.complete is False and "非法" in r.reason


def test_empty_input():
    r = reassemble_meteor_scan([])
    assert r.complete is False and r.reason == "无 Segment 输入"


def test_quant_tables_match_ijg():
    a = bytes([0, 0, 0, 0, 4, 0, 1, 0x40, 0x00, 0x50, 0x08, 0x03]) + b"\x01"
    r = reassemble_meteor_scan([a])
    from mbdsdr_ai.lrpt_huffman import ijg_quant_table
    assert (r.quant_tables[0] == ijg_quant_table(80)).all()
    assert (r.quant_tables[1] == ijg_quant_table(80, chroma=True)).all()
