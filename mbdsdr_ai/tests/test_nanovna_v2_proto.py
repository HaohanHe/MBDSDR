# SPDX-License-Identifier: MIT
"""NanoVNA V2 协议编解码测试。"""
import pytest

from mbdsdr_ai.nanovna_v2_proto import (
    REG_V2,
    V2_USB_POINTS_MAX,
    V2PLUS4_USB_POINTS_MAX,
    build_read,
    build_write,
    encode_fifo_frame,
    parse_fifo_frame,
    sparams_from_frames,
)


def test_build_read_widths():
    assert build_read(0xF0, 1) == bytes((0x10, 0xF0))
    assert build_read(0x00, 8) == bytes((0x13, 0x00))


def test_build_write_endianness():
    # uint64 起点频率小端
    fr = build_write(REG_V2["sweep_start_hz"], 500000000, 8)
    assert fr[0] == 0x23 and fr[1] == 0x00
    assert fr[2:] == (500000000).to_bytes(8, "little")


def test_build_write_width_and_range():
    assert build_write(0x40, 255, 1) == bytes((0x20, 0x40, 0xFF))
    with pytest.raises(ValueError):
        build_write(0x40, 256, 1)
    with pytest.raises(ValueError):
        build_write(0x40, 1, 3)


def test_fifo_frame_roundtrip():
    fr = encode_fifo_frame(complex(1000, -500), complex(-100, 200),
                           complex(50, 25), 42)
    assert len(fr) == 32
    pf = parse_fifo_frame(fr)
    assert pf.ch0_fwd == complex(1000, -500)
    assert pf.ch0_rev == complex(-100, 200)
    assert pf.ch1_rev == complex(50, 25)
    assert pf.freq_index == 42


def test_fifo_frame_bad_checksum_rejected():
    fr = bytearray(encode_fifo_frame(1, 2, 3, 7))
    fr[10] ^= 0xFF
    with pytest.raises(ValueError):
        parse_fifo_frame(bytes(fr))


def test_fifo_frame_bad_length_rejected():
    with pytest.raises(ValueError):
        parse_fifo_frame(b"\x00" * 31)


def test_sparams_ratio_and_grid():
    frames = []
    n = 16
    for k in range(n):
        fwd = complex(1000, 0)
        frames.append(encode_fifo_frame(
            fwd, complex(-100, 50), complex(800, -100), k))
    out = sparams_from_frames(frames, start_hz=100e6,
                              step_hz=1e6, points=n)
    assert out["freq_hz"][0] == 100e6
    assert out["freq_hz"][-1] == 100e6 + 1e6 * (n - 1)
    assert out["s11"][0] == pytest.approx(complex(-0.1, 0.05))
    assert out["s21"][0] == pytest.approx(complex(0.8, -0.1))


def test_sparams_missing_frames_honest():
    frames = [encode_fifo_frame(1, 1, 1, k) for k in (0, 2)]
    with pytest.raises(ValueError):
        sparams_from_frames(frames, 0, 1, 3)


def test_sparams_duplicate_frames_honest():
    frames = [encode_fifo_frame(1, 1, 1, 0),
              encode_fifo_frame(1, 1, 1, 0)]
    with pytest.raises(ValueError):
        sparams_from_frames(frames, 0, 1, 2)


def test_sparams_zero_forward_rejected():
    frames = [encode_fifo_frame(0, 1, 1, 0)]
    with pytest.raises(ValueError):
        sparams_from_frames(frames, 0, 1, 1)


def test_points_limit():
    assert V2_USB_POINTS_MAX == 1024
    assert V2PLUS4_USB_POINTS_MAX == 65536
    frames = [encode_fifo_frame(1, 1, 1, 0)]
    with pytest.raises(ValueError):
        sparams_from_frames(frames, 0, 1, V2PLUS4_USB_POINTS_MAX + 1)
