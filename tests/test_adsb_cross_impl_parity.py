"""ADS-B / Mode-S 奇偶语义跨实现对拍（Python 侧孪生）。

共享真值（与 cpp/tests/test_adsb_parity_cross.cpp、tests/test_adsb_dump1090_golden.py
同源）：dump1090 作者官方测试样本 testfiles/modes1.bin README 示例帧——
  DF11  5D4D20237A55A6  (ICAO 4D2023, IID=0, 纯校验 AP==CRC(data))
  DF4   20000F1F684A6C  (ICAO 4D2023, 地址奇偶 AP==CRC(data)^ICAO)

钉死的数学语义：教科书余数 F(x) mod G = crc24(数据段) ^ AP。
mode_s_crc24 / adsb_lite.crc24 是 CRC 惯例（F·x^24 mod G）：DF4 全帧输入
给 0x1EB2B4 (= 0x4D2023·x^24 mod G)，不是地址 0x4D2023。decode_frame 曾
把全帧 LFSR 当零判据（DF20/21 合法 Comm-B 全标 crc_ok=False）、把明文 AA
域当 ICAO（DF4/5 那是数据段，瞎取）——本测试为回归地板。
"""
from __future__ import annotations

import pytest

from mbdsdr_ai.adsb import decode_frame, mode_s_crc24
from mbdsdr_ai.adsb_lite import bytes_to_bits

ICAO = 0x4D2023
T_DF11 = bytes.fromhex("5D4D20237A55A6")
T_DF4 = bytes.fromhex("20000F1F684A6C")


def _addr_parity_long(df_ca: int, addr: int, mb7: bytes) -> bytes:
    """地址奇偶长帧：AP = crc24(head) ^ addr。"""
    head = bytes([df_ca]) + addr.to_bytes(3, "big") + mb7
    ap = mode_s_crc24(bytes_to_bits(head)) ^ addr
    return head + ap.to_bytes(3, "big")


def _pure_crc_long(df_ca: int, addr: int, me7: bytes) -> bytes:
    """纯校验长帧（DF17 式）：AP = crc24(head)。"""
    head = bytes([df_ca]) + addr.to_bytes(3, "big") + me7
    ap = mode_s_crc24(bytes_to_bits(head))
    return head + ap.to_bytes(3, "big")


def test_x24_trap_documented():
    """全帧 CRC 惯例值钉点：为什么不能拿它当地址（C++ 侧同值）。"""
    assert mode_s_crc24(bytes_to_bits(T_DF4)) == 0x1EB2B4
    # 教科书余数才是地址
    bits = bytes_to_bits(T_DF4)
    ap = int("".join(map(str, bits[-24:])), 2)
    assert mode_s_crc24(bits[:-24]) ^ ap == ICAO


def test_df11_truth():
    f = decode_frame(T_DF11)
    assert f is not None and f.crc_ok
    assert f.icao_hex == "4D2023"


def test_df4_truth_recovered_icao():
    """DF4 真值：ICAO 由地址奇偶还原，不是明文 AA 域（那 3 字节是数据段）。"""
    f = decode_frame(T_DF4)
    assert f is not None and f.crc_ok
    assert f.icao_hex == "4D2023"
    assert f.icao_hex != "000F1F", "明文 AA 域是数据段，不得当 ICAO"


def test_df20_addr_parity_accepted():
    """DF20 地址奇偶合法帧必须过（回归地板：旧全帧零判据误拒）。"""
    raw = _addr_parity_long(0xA0, ICAO, b"\x01\x02\x03\x04\x05\x06\x07")
    f = decode_frame(raw)
    assert f is not None and f.crc_ok
    assert f.icao_hex == "4D2023"
    # Comm-B 不得按 ADS-B ME 误读（无 tc/callsign 污染）
    assert f.tc is None and f.callsign is None


def test_df20_corrupted_ap_rejected():
    raw = bytearray(_addr_parity_long(0xA0, ICAO, b"\x01\x02\x03\x04\x05\x06\x07"))
    raw[13] ^= 0x01
    f = decode_frame(bytes(raw))
    assert f is not None and not f.crc_ok


def test_df20_pure_crc_ap_rejected():
    raw = _pure_crc_long(0xA0, ICAO, b"\x01\x02\x03\x04\x05\x06\x07")
    f = decode_frame(raw)
    assert f is not None and not f.crc_ok


def test_df4_bitflip_formula():
    """地址奇偶是识别不是检错：单比特误码 → 还原成另一个地址，公式钉死。"""
    bad = bytearray(T_DF4)
    bad[2] ^= 0x08
    f = decode_frame(bytes(bad))
    assert f is not None and f.crc_ok
    ap = int(f.raw_hex[8:14], 16)
    expect = mode_s_crc24(bytes_to_bits(bytes(bad))[:32]) ^ ap
    assert int(f.icao_hex, 16) == expect
    assert f.icao_hex != "4D2023"


def test_df17_bitflip_detected():
    raw = bytearray(_pure_crc_long(0x8D, ICAO, b"\x11\x22\x33\x44\x55\x66\x77"))
    f = decode_frame(bytes(raw))
    assert f is not None and f.crc_ok
    assert f.icao_hex == "4D2023"
    raw[5] ^= 0x01
    f2 = decode_frame(bytes(raw))
    assert f2 is not None and not f2.crc_ok


def test_df11_nonzero_iid_rejected():
    bad = bytearray(T_DF11)
    crc = mode_s_crc24(bytes_to_bits(bytes(bad))[:32])
    ap = crc ^ 0x5
    bad[4] = (ap >> 16) & 0xFF
    bad[5] = (ap >> 8) & 0xFF
    bad[6] = ap & 0xFF
    f = decode_frame(bytes(bad))
    assert f is not None and not f.crc_ok
