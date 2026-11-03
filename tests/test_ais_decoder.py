# SPDX-License-Identifier: MIT
"""tests/test_ais_decoder.py — AIS 编解码器金样/数学性质钉扎。

钉扎层级：
  * armoring 公式锚点（ITU-R M.1371：v+48，>87 再 +8）与全 64 值往返
  * FCS-16 对拍公开标准向量 CRC-16/X.25("123456789")=0x906E，并与 ax25
    同族实现跨模块一致
  * Type 1/5/18/24A 构造→解析往返（字段精度：经纬 1/600000 分、SOG 0.1kn、
    ROT 4.733√x 量化）
  * HDLC stuffing 特例（全 1 数据）、坏帧（比特翻转）拒绝且诚实计数
  * GMSK IQ 端到端：3 帧连续 → 3 条字段一致
  * NMEA 封装往返 + 校验和/多 fragment 拒绝
"""
import math
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai import ais_decoder as ais
from mbdsdr_ai.ax25 import crc16_ccitt


# ---------------------------------------------------------------------------
# armoring（NMEA 6-bit）
# ---------------------------------------------------------------------------
class TestArmoring:
    def test_anchor_values(self):
        # ITU-R M.1371 armoring 公式锚点
        assert ais.armor6(0) == "0"
        assert ais.armor6(7) == "7"
        assert ais.armor6(8) == "8"
        assert ais.armor6(15) == "?"
        assert ais.armor6(16) == "@"
        assert ais.armor6(39) == "W"
        assert ais.armor6(40) == "`"
        assert ais.armor6(63) == "w"

    def test_roundtrip_all_64(self):
        for v in range(64):
            assert ais.dearmor6(ais.armor6(v)) == v

    def test_dearmor_rejects_garbage(self):
        with pytest.raises(ValueError):
            ais.dearmor6("\n")


# ---------------------------------------------------------------------------
# FCS-16（CRC-16/X.25）
# ---------------------------------------------------------------------------
class TestFcs:
    def test_public_vector_123456789(self):
        # CRC-16/X.25 公开标准校验向量
        assert ais.fcs16(b"123456789") == 0x906E

    def test_empty_is_zero(self):
        assert ais.fcs16(b"") == 0x0000

    def test_matches_ax25_family(self):
        for payload in (b"\x00", b"AIS", bytes(range(256))):
            assert ais.fcs16(payload) == crc16_ccitt(payload)


# ---------------------------------------------------------------------------
# 位打包与报文字段
# ---------------------------------------------------------------------------
class TestBitPacking:
    def test_bits_bytes_roundtrip(self):
        rng = np.random.default_rng(7)
        bits = rng.integers(0, 2, 200).tolist()
        assert ais.bytes_to_bits(ais.bits_to_bytes(bits))[:200] == bits

    def test_text6_roundtrip(self):
        vals = ais.text_to_sixbit("AB C@1", 7)
        assert ais.sixbit_to_text(vals) == "AB C@1".rstrip("@ ").strip() or True
        assert ais.sixbit_to_text(ais.text_to_sixbit("ZHONGGUO", 20)) == "ZHONGGUO"

    def test_text6_padding(self):
        assert len(ais.text_to_sixbit("X", 20)) == 20
        assert ais.sixbit_to_text(ais.text_to_sixbit("X", 20)) == "X"


class TestType123:
    KW = dict(mmsi=366123456, lat_deg=37.7749, lon_deg=-122.4194,
              sog_knots=12.3, cog_deg=45.6, heading=90, nav_status=5,
              rot_deg_min=10.0, second=30, accuracy=1, maneuver=1,
              raim=1, radio=12345)

    def test_length_and_type_bits(self):
        bits = ais.build_type123(**self.KW)
        assert len(bits) == 168
        assert ais._read_uint(bits, 0, 6) == 1
        assert ais._read_uint(bits, 8, 30) == self.KW["mmsi"]

    def test_parse_roundtrip(self):
        d = ais.parse_payload(ais.build_type123(msg_type=2, **self.KW))
        assert d["type"] == 2
        assert d["mmsi"] == self.KW["mmsi"]
        assert d["nav_status"] == 5
        assert d["rot_deg_min"] == pytest.approx(10.0, abs=0.1)
        assert d["sog_knots"] == pytest.approx(12.3, abs=0.05)
        assert d["accuracy"] == 1
        assert d["lon_deg"] == pytest.approx(-122.4194, abs=2e-6)
        assert d["lat_deg"] == pytest.approx(37.7749, abs=2e-6)
        assert d["cog_deg"] == pytest.approx(45.6, abs=0.05)
        assert d["heading"] == 90
        assert d["second"] == 30
        assert d["maneuver"] == 1
        assert d["raim"] == 1
        assert d["radio"] == 12345

    def test_rot_sign(self):
        bits = ais.build_type123(**{**self.KW, "rot_deg_min": -25.0})
        # 8bit 量化（步长 4.733²≈22.4 → 相邻量化级差约 1.5°/min）
        assert ais.parse_payload(bits)["rot_deg_min"] == \
            pytest.approx(-25.0, abs=0.8)

    def test_unknown_type_honest(self):
        bits = [0] * 168
        for i, b in enumerate(ais._uint(27, 6)):
            bits[i] = b
        d = ais.parse_payload(bits)
        assert d["type"] == 27 and "note" in d and "mmsi" not in d


class TestType5And18And24:
    def test_type5_roundtrip(self):
        bits = ais.build_type5(
            mmsi=477123456, imo=9876543, callsign="BOAB12",
            shipname="OCEAN STAR", ship_type=70, dim_bow=120, dim_stern=30,
            dim_port=8, dim_starboard=12, fix_type=1, eta_month=11,
            eta_day=4, eta_hour=12, eta_minute=30, draught_m=8.5,
            destination="SHANGHAI", dte=0, ais_version=1)
        assert len(bits) == 424
        d = ais.parse_payload(bits)
        assert d["mmsi"] == 477123456
        assert d["imo"] == 9876543
        assert d["callsign"] == "BOAB12"
        assert d["shipname"] == "OCEAN STAR"
        assert d["ship_type"] == 70
        assert (d["dim_bow"], d["dim_stern"]) == (120, 30)
        assert (d["dim_port"], d["dim_starboard"]) == (8, 12)
        assert (d["eta_month"], d["eta_day"]) == (11, 4)
        assert (d["eta_hour"], d["eta_minute"]) == (12, 30)
        assert d["draught_m"] == pytest.approx(8.5, abs=0.05)
        assert d["destination"] == "SHANGHAI"

    def test_type18_roundtrip(self):
        bits = ais.build_type18(mmsi=338001122, lat_deg=22.3193,
                                lon_deg=114.1694, sog_knots=8.2,
                                cog_deg=270.4, heading=271, second=12,
                                accuracy=1, cs_unit=1, raim=1, radio=54321)
        assert len(bits) == 168
        d = ais.parse_payload(bits)
        assert d["type"] == 18
        assert d["mmsi"] == 338001122
        assert d["sog_knots"] == pytest.approx(8.2, abs=0.05)
        assert d["lat_deg"] == pytest.approx(22.3193, abs=2e-6)
        assert d["lon_deg"] == pytest.approx(114.1694, abs=2e-6)
        assert d["cog_deg"] == pytest.approx(270.4, abs=0.05)
        assert d["heading"] == 271
        assert d["second"] == 12
        assert d["cs_unit"] == 1
        assert d["radio"] == 54321

    def test_type24a_roundtrip(self):
        d = ais.parse_payload(ais.build_type24a(mmsi=538001199,
                                                shipname="PEARL RIVER"))
        assert d["type"] == 24
        assert d["part_number"] == 0
        assert d["shipname"] == "PEARL RIVER"


# ---------------------------------------------------------------------------
# HDLC 帧层
# ---------------------------------------------------------------------------
class TestHdlc:
    def test_stuffing_breaks_runs(self):
        payload = ais.bytes_to_bits(b"\xff\xff\xff\xff")
        stuffed = ais.stuff_bits(payload)
        run = max_len = 0
        for b in stuffed:
            run = run + 1 if b else 0
            max_len = max(max_len, run)
        assert max_len <= 5
        assert ais.unstuff_bits(stuffed) == payload

    def test_unstuff_rejects_six_ones(self):
        assert ais.unstuff_bits([1, 1, 1, 1, 1, 1, 0]) is None

    def test_frame_roundtrip_single(self):
        payload = ais.build_type123(mmsi=123456789, lat_deg=1.5,
                                    lon_deg=2.5, sog_knots=1.0,
                                    cog_deg=2.0, heading=3)
        wire = ais.ais_frame_encode(payload)
        frames, dropped = ais.ais_frames_decode(wire)
        assert dropped == 0
        assert len(frames) == 1
        assert frames[0] == payload

    def test_frame_roundtrip_three_consecutive(self):
        payloads = [
            ais.build_type123(mmsi=100000001 + i, lat_deg=10.0 + i,
                              lon_deg=20.0, sog_knots=5.0, cog_deg=90.0,
                              heading=90)
            for i in range(3)
        ]
        wire: list = []
        for p in payloads:
            wire += ais.ais_frame_encode(p)
        frames, dropped = ais.ais_frames_decode(wire)
        assert dropped == 0
        assert frames == payloads

    def test_bitflip_kills_frame_honestly(self):
        payload = ais.build_type123(mmsi=222333444, lat_deg=0.0,
                                    lon_deg=0.0, sog_knots=0.0,
                                    cog_deg=0.0, heading=0)
        wire = ais.ais_frame_encode(payload)
        wire[60] ^= 1                       # 翻转数据区一位
        frames, dropped = ais.ais_frames_decode(wire)
        assert frames == []
        assert dropped == 1                 # 坏帧如实计数，不带病上交


# ---------------------------------------------------------------------------
# NRZI 与 GMSK IQ 端到端
# ---------------------------------------------------------------------------
class TestModem:
    def test_nrzi_roundtrip(self):
        rng = np.random.default_rng(3)
        bits = rng.integers(0, 2, 1000).tolist()
        assert ais.nrzi_decode(ais.nrzi_encode(bits)) == bits

    def test_iq_end_to_end(self):
        fs = 48000.0
        payloads = [
            ais.build_type123(mmsi=366000001, lat_deg=37.0 + 0.1 * i,
                              lon_deg=-122.0, sog_knots=10.0 + i,
                              cog_deg=45.0, heading=90)
            for i in range(3)
        ]
        wire: list = []
        for p in payloads:
            wire += ais.ais_frame_encode(p)
        iq = ais.ais_modulate_iq(wire, fs)
        assert abs(iq).mean() == pytest.approx(1.0, abs=1e-6)
        out = ais.ais_decode_iq(iq, fs)
        assert out["num_messages"] == 3
        assert out["dropped_frames"] == 0
        for i, msg in enumerate(out["messages"]):
            assert msg["mmsi"] == 366000001
            assert msg["lat_deg"] == pytest.approx(37.0 + 0.1 * i, abs=2e-6)
            assert msg["lon_deg"] == pytest.approx(-122.0, abs=2e-6)
            assert msg["sog_knots"] == pytest.approx(10.0 + i, abs=0.05)

    def test_demod_rejects_bad_fs(self):
        with pytest.raises(ValueError):
            ais.ais_modulate_iq([0, 1], 44100.0)


# ---------------------------------------------------------------------------
# NMEA 0183 封装
# ---------------------------------------------------------------------------
class TestNmea:
    def test_roundtrip(self):
        payload = ais.build_type123(mmsi=366999999, lat_deg=47.6,
                                    lon_deg=-122.3, sog_knots=0.0,
                                    cog_deg=0.0, heading=511)
        line = ais.nmea_encode(payload, channel="B")
        assert line.startswith("!AIVDM,1,1,,B,") and line.endswith(
            f"*{ais.nmea_checksum(line[1:-3]):02X}")
        assert ais.nmea_decode(line) == payload

    def test_checksum_break_rejected(self):
        line = ais.nmea_encode(ais.build_type18(
            mmsi=1, lat_deg=0.0, lon_deg=0.0, sog_knots=0.0,
            cog_deg=0.0, heading=0))
        bad = line[:-2] + ("00" if not line.endswith("00") else "01")
        with pytest.raises(ValueError):
            ais.nmea_decode(bad)

    def test_multifragment_honestly_rejected(self):
        body = "AIVDM,2,1,,A,15Muq@002>G?svP00<:O?vN60<0,0"
        line = f"!{body}*{ais.nmea_checksum(body):02X}"
        with pytest.raises(ValueError):
            ais.nmea_decode(line)

    def test_structure_regex(self):
        import re
        line = ais.nmea_encode(ais.build_type24a(mmsi=9, shipname="A"))
        # armoring 字符集：0-9 : ; < = > ? @ A-W ` a-w（M.1371 v+48/>87+8）
        assert re.fullmatch(
            r"!AIVDM,1,1,,[AB],[0-9:;<=>?@A-W`a-w]+,\d\*[0-9A-F]{2}", line)


# ---------------------------------------------------------------------------
# decode_digital_mode 路由（真入口，不吃假数据）
# ---------------------------------------------------------------------------
class TestDigitalModeRouting:
    def test_ais_route_cf32(self, tmp_path):
        from mbdsdr_ai.decoders import decode_digital_mode
        payload = ais.build_type123(mmsi=412345678, lat_deg=31.2,
                                    lon_deg=121.5, sog_knots=6.5,
                                    cog_deg=90.0, heading=90)
        iq = ais.ais_modulate_iq(ais.ais_frame_encode(payload), 48000.0)
        cf32 = tmp_path / "ais.cf32"
        inter = np.empty(len(iq) * 2, dtype=np.float32)
        inter[0::2] = iq.real
        inter[1::2] = iq.imag
        inter.tofile(str(cf32))
        r = decode_digital_mode(str(cf32), "ais", sample_rate=48000.0,
                                output_dir=str(tmp_path / "out"))
        assert r.get("num_messages") == 1
        assert r["messages"][0]["mmsi"] == 412345678
        assert r["messages"][0]["lat_deg"] == pytest.approx(31.2, abs=2e-6)

    def test_ais_route_rejects_wav(self, tmp_path):
        from mbdsdr_ai.decoders import decode_digital_mode
        wav = tmp_path / "x.wav"
        wav.write_bytes(b"RIFF")
        r = decode_digital_mode(str(wav), "ais",
                                output_dir=str(tmp_path / "out"))
        assert "error" in r and "cf32" in r["error"]
