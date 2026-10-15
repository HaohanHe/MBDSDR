# SPDX-License-Identifier: MIT
"""janvna2_proto 测试。"""
import struct

import pytest

from mbdsdr_ai.janvna2_proto import (
    Datapoint,
    PacketStreamSplitter,
    PacketType,
    SweepSettings,
    decode_datapoint,
    decode_packet,
    decode_sweep_settings,
    encode_datapoint,
    encode_packet,
    encode_sweep_settings,
)


def test_datapoint_roundtrip_crc_exempt():
    dp = Datapoint(s11=complex(0.1, -0.2), s21=complex(0.9, 0.05),
                   s12=complex(0.01, 0.02), s22=complex(0.11, -0.21),
                   freq_hz=14_200_000, point_num=33)
    pkt = decode_packet(encode_datapoint(dp))
    assert pkt.packet_type is PacketType.DATAPOINT
    assert pkt.crc_ok                       # 豁免类型恒 True（如实口径）
    back = decode_datapoint(pkt)
    assert back.s11 == pytest.approx(dp.s11)
    assert back.s21 == pytest.approx(dp.s21)
    assert back.freq_hz == dp.freq_hz
    assert back.point_num == dp.point_num


def test_datapoint_crc_field_is_zero():
    raw = encode_datapoint(Datapoint(0, 0, 0, 0, 1, 0))
    assert raw[-4:] == b"\x00" * 4          # 上游吞吐优化：CRC 字段置 0


def test_sweep_settings_roundtrip_with_crc():
    ss = SweepSettings(f_start_hz=500e6, f_stop_hz=1500e6, points=501,
                       if_bandwidth_hz=1000, excitation_dbm_centi=-1000,
                       excite_port1=True, excite_port2=False,
                       suppress_peaks=True)
    pkt = decode_packet(encode_sweep_settings(ss))
    back = decode_sweep_settings(pkt)
    assert back == ss


def test_sweep_settings_corruption_rejected():
    raw = bytearray(encode_sweep_settings(
        SweepSettings(1, 2, 3, 4, 5, True, True, False)))
    raw[10] ^= 0xFF                          # 腐蚀载荷
    with pytest.raises(ValueError):
        decode_packet(bytes(raw))


def test_bad_magic_and_length_rejected():
    good = encode_packet(PacketType.ACK)
    bad = bytes((0x00,)) + good[1:]
    with pytest.raises(ValueError):
        decode_packet(bad)
    short = good[:-1]
    with pytest.raises(ValueError):
        decode_packet(short)


def test_unknown_type_honest():
    raw = bytearray(encode_packet(PacketType.ACK))
    raw[3] = 99
    # 重算 CRC 使未知类型不被 CRC 检查抢先（保持语义聚焦）
    import zlib
    fixed = bytes(raw[:-4]) + struct.pack("<I", zlib.crc32(bytes(raw[:-4])))
    with pytest.raises(ValueError):
        decode_packet(fixed)


def test_wrong_payload_size_honest():
    pkt = decode_packet(encode_packet(PacketType.DATAPOINT, b"\x00" * 10))
    with pytest.raises(ValueError):
        decode_datapoint(pkt)


def test_splitter_reassembles_across_chunks():
    dp1 = encode_datapoint(Datapoint(1, 2, 3, 4, 100, 1))
    dp2 = encode_packet(PacketType.ACK)
    blob = dp1 + dp2
    sp = PacketStreamSplitter()
    assert sp.feed(blob[:7]) == []           # 头都没收齐
    out = sp.feed(blob[7:])
    assert len(out) == 2
    assert decode_packet(out[0]).packet_type is PacketType.DATAPOINT
    assert decode_packet(out[1]).packet_type is PacketType.ACK
    assert sp.incomplete() == 0


def test_splitter_bad_length_honest():
    sp = PacketStreamSplitter()
    with pytest.raises(ValueError):
        sp.feed(bytes((0x5A, 0x02, 0x00, 0x07)) + b"\x00" * 6)
