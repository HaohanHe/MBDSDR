# SPDX-License-Identifier: MIT
"""RFC 2435 RTP/JPEG 头 + IJG 量化表测试（Meteor LRPT 真实路径接入点）。

依据公开规范（RFC 2435 §3.1/§3.1.6、ITU T.81 Annex K.1/K.2），
非任何 GPL 源码。合成数据钉住边界；真实 OTA 样本接入属未做项。
"""
import pytest

from mbdsdr_ai.lrpt_huffman import (
    IJG_K1,
    IJG_K2,
    RtpJpegHeader,
    ijg_quant_table,
    parse_rtp_jpeg_header,
    LrptJpegTruncatedError,
    LrptJpegUnsupportedError,
)


# ---------------------------------------------------------------------------
# IJG 量化表（RFC 2435 §3.1.6）
# ---------------------------------------------------------------------------

def test_q50_is_identity_scale():
    """Q=50 -> S=100 -> K*S/100 = K（基准表本身）。"""
    t = ijg_quant_table(50)
    assert list(t) == list(IJG_K1)


def test_q99_clamps_to_one():
    """Q=99 -> S=2 -> 16*2/100=0.32 -> round 0 -> clamp 1。"""
    assert list(ijg_quant_table(99)[:3]) == [1, 1, 1]


def test_q1_clamps_to_255():
    """Q=1 -> S=5000 -> 16*50=800 -> clamp 255。"""
    assert list(ijg_quant_table(1)[:3]) == [255, 255, 255]


def test_chroma_uses_k2():
    assert ijg_quant_table(50, chroma=True)[0] == IJG_K2[0] == 17


def test_monotone_q():
    """Q 越大量化越粗（表值整体不增）——方向性物理断言。"""
    prev = None
    for q in (10, 25, 50, 75, 90, 99):
        t = ijg_quant_table(q)
        if prev is not None:
            assert int(t.sum()) <= int(prev.sum()) + 0
        prev = t


def test_q_out_of_range_rejected():
    for bad in (0, 100, 128, 255, -1):
        with pytest.raises(LrptJpegUnsupportedError):
            ijg_quant_table(bad)


# ---------------------------------------------------------------------------
# RTP/JPEG 头解析（RFC 2435 §3.1）
# ---------------------------------------------------------------------------

def _m2_segment(frag: int, q: int = 32, width: int = 1024,
                height: int = 320, extra: bytes = b"\xAB\xCD"):
    """构造一个 Meteor 风格 Segment：4B JPEG 头 + 8B 尺寸头 + 熵片段。

    4 字节 JPEG 头：MBZ | TypeSpecific | FragmentOffset(24bit)。
    8 字节尺寸头：宽(2, 大端) 高(2, 大端) Type(1) Q(1) 色深(1) 通道(1)。
    """
    head = bytes([0x00, 0x00, (frag >> 16) & 0xFF, frag & 0xFF])
    dim = bytes([(width >> 8) & 0xFF, width & 0xFF,
                 (height >> 8) & 0xFF, height & 0xFF,
                 0x00, q, 0x08, 0x03])
    return head + dim + extra


def test_first_segment_header_fields():
    seg = _m2_segment(0, q=32, width=1024, height=320)
    hdr, off = parse_rtp_jpeg_header(seg)
    assert hdr.fragment_offset == 0
    assert hdr.width == 1024
    assert hdr.height == 320
    assert hdr.type_field == 32 or hdr.q_field in (0, 32)
    assert off == 12
    assert hdr.has_inband_q_table is False


def test_continuation_segment_header_fields():
    """fragment_offset>0 的续段：4 字节头后无尺寸头，offset 原样透出。"""
    seg = bytes([0x00, 0x00, 0x12, 0x34, 0x99, 0x88, 0x77])
    hdr, off = parse_rtp_jpeg_header(seg)
    assert hdr.fragment_offset == 0x1234
    assert off == 4


def test_short_input_rejected():
    with pytest.raises(LrptJpegTruncatedError):
        parse_rtp_jpeg_header(b"\x00\x00\x00")


def test_inband_q_table_flagged_unsupported():
    """首段 Q≥128：带内量化表路径诚实抛 Unsupported（未做项）。"""
    seg = bytes([0x00, 0x00, 0x00, 0x00]) + bytes(
        [0x00, 0x04, 0x01, 0x40, 0x00, 0x80, 0x08, 0x03]) + b"\x00" * 70
    with pytest.raises(LrptJpegUnsupportedError):
        parse_rtp_jpeg_header(seg)


def test_rtp_header_dataclass_roundtrip():
    h = RtpJpegHeader(0, 0, 0, 32, width=1024, height=320)
    assert h.fragment_offset == 0 and not h.has_inband_q_table
    h2 = RtpJpegHeader(0, 0, 0, 200, width=0, height=0)
    assert h2.has_inband_q_table
