"""
MBDSDR - ACARS 解码器测试
==========================

覆盖（对应任务要求）：
  1. MSK 解调：合成 MSK 信号 -> 解调 -> 位序列正确
  2. 消息解析：已知 ACARS 消息 -> 地址/模式/标签/文本正确
  3. CRC16：已知消息通过；破坏 1 bit -> 失败
  4. 同步字检测：含噪声信号 -> 检测到 SOH 并解出帧
  5. 参数验证：频率/波特率/音调常量正确

运行: pytest tests/acars_test.py -v
"""

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mbdsdr_ai.acars_decoder import (  # noqa: E402
    ACARSMessageParser,
    ACARSDecoder,
    ACARSMDemod,
    ACARS_STANDARD_CHANNELS_MHZ,
    ACARS_BAUD_RATE,
    ACARS_CENTER_FREQ_HZ,
    ACARS_MARK_FREQ_HZ,
    ACARS_SPACE_FREQ_HZ,
    SYN, SOH, ETX, ETB, DLE,
    bits_to_bytes,
    build_acars_frame,
    crc16_ccitt,
    frame_bits_from_bytes,
    synthesize_msk_audio,
    acars_decode_iq,
    acars_parse_message,
    acars_msk_demod,
)


# ─────────────────────────────────────────────────────────────────────
# 5. 参数验证：频率/波特率/音调
# ─────────────────────────────────────────────────────────────────────
class TestConstants:
    """验证协议常量与 acarsdec/libacars 源码一致。"""

    def test_baud_rate(self):
        # acarsdec/msk.c: FLEN=INTRATE/1200 -> 1200 bps
        assert ACARS_BAUD_RATE == 1200

    def test_mark_space_center(self):
        # mark/space 中点 = 中心
        assert ACARS_MARK_FREQ_HZ == 2400
        assert ACARS_SPACE_FREQ_HZ == 1200
        assert ACARS_CENTER_FREQ_HZ == 1800
        assert (ACARS_MARK_FREQ_HZ + ACARS_SPACE_FREQ_HZ) / 2 == ACARS_CENTER_FREQ_HZ

    def test_control_chars(self):
        # acars.c:22-27
        assert SYN == 0x16
        assert SOH == 0x01
        assert ETX == 0x83
        assert ETB == 0x97
        assert DLE == 0x7F

    def test_standard_channels_not_region_locked(self):
        # 只列标准 VHF 信道，不硬编码地区台站
        assert 131.550 in ACARS_STANDARD_CHANNELS_MHZ
        assert 131.725 in ACARS_STANDARD_CHANNELS_MHZ
        assert 131.850 in ACARS_STANDARD_CHANNELS_MHZ


# ─────────────────────────────────────────────────────────────────────
# 3. CRC16
# ─────────────────────────────────────────────────────────────────────
class TestCRC:
    def test_crc_table_first_entries_match_libacars(self):
        # libacars/crc.c:77-78
        from mbdsdr_ai.acars_decoder import _CRC_TABLE
        assert _CRC_TABLE[0] == 0x0000
        assert _CRC_TABLE[1] == 0x1189
        assert _CRC_TABLE[2] == 0x2312
        assert _CRC_TABLE[3] == 0x329B

    def test_good_frame_crc_passes(self):
        body = build_acars_frame(mode="2", reg="N12345", label="DF",
                                 block_id="2", text="HELLO")
        # 去掉末尾 DEL 后 CRC 余数应为 0
        assert crc16_ccitt(body[:-1], 0x0000) == 0

    def test_flipped_bit_crc_fails(self):
        body = bytearray(build_acars_frame(mode="2", reg="N12345", label="DF",
                                           block_id="2", text="HELLO"))
        # 翻转文本区 1 bit（第 10 字节）
        body[10] ^= 0x01
        # 去掉 DEL 后余数不应为 0
        assert crc16_ccitt(bytes(body[:-1]), 0x0000) != 0


# ─────────────────────────────────────────────────────────────────────
# 2. 消息解析
# ─────────────────────────────────────────────────────────────────────
class TestMessageParse:
    def test_parse_downlink_fields(self):
        body = build_acars_frame(mode="2", reg="N842UA", label="DF",
                                 block_id="6", text="#DFB9102,0043,188/9")
        m = ACARSMessageParser().parse(body)
        assert m is not None
        assert m.crc_ok is True
        assert m.mode == "2"
        assert m.reg.strip() == "N842UA"
        assert m.label == "DF"
        assert m.block_id == "6"
        assert m.text.startswith("#DFB9102")
        # 下行 block_id '0'-'9' 应解析出航班号
        assert m.flight_id == "ABC123"

    def test_parse_uplink_no_flight(self):
        body = build_acars_frame(mode="B", reg="D-ABCE", label="_d",
                                 block_id="X", text="HOWGOZIT")
        m = ACARSMessageParser().parse(body)
        assert m is not None
        assert m.mode == "B"
        assert m.reg.strip() == "D-ABCE"
        assert m.label == "_d"
        assert m.block_id == "X"
        assert m.text == "HOWGOZIT"

    def test_corrupted_frame_rejected(self):
        body = bytearray(build_acars_frame(mode="2", reg="N12345", label="DF",
                                           block_id="2", text="ABC"))
        body[8] ^= 0x10   # 破坏 ack 字节
        m = ACARSMessageParser().parse(bytes(body))
        # CRC 失败 -> crc_ok 应为 False
        assert m is not None
        assert m.crc_ok is False


# ─────────────────────────────────────────────────────────────────────
# 1. MSK 解调
# ─────────────────────────────────────────────────────────────────────
class TestMSKDemod:
    def test_alternating_bits_recovered(self):
        # 交替 1010... 是最好的位同步训练序列
        bits = [1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0,
                1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0]
        audio = synthesize_msk_audio(bits, 12500, 1200)
        rec = ACARSMDemod(12500, 1200).demodulate_bits(audio)
        # 允许起点偏移，但恢复出的比特序列应包含完整交替模式
        # 在恢复序列里找一段 1010...
        joined = "".join(str(b) for b in rec)
        assert "101010101010" in joined or "010101010101" in joined

    def test_mark_space_classification(self):
        # 纯 mark (全 1) 和纯 space (全 0) 的能量判决方向正确
        t = np.arange(1250) / 12500.0
        mark_audio = np.cos(2 * math.pi * 2400 * t)
        space_audio = np.cos(2 * math.pi * 1200 * t)
        d = ACARSMDemod(12500, 1200)
        e_mark = np.mean(d.discriminate(mark_audio))
        e_space = np.mean(d.discriminate(space_audio))
        assert e_mark > 0, "2400Hz 应判为 mark(1)"
        assert e_space < 0, "1200Hz 应判为 space(0)"

    def test_bits_to_bytes_lsb_first(self):
        # 0x16 = 0b00010110, LSB-first 发送位序
        bits = [(0x16 >> j) & 1 for j in range(8)]
        assert bits_to_bytes(bits)[0] == 0x16


# ─────────────────────────────────────────────────────────────────────
# 4. 同步字检测 + 端到端
# ─────────────────────────────────────────────────────────────────────
class TestEndToEnd:
    def test_frame_with_noise_detected(self):
        """含噪声的合成 ACARS 信号 -> 检测到 SOH 并解出消息。"""
        body = build_acars_frame(mode="2", reg="N842UA", label="DF",
                                 block_id="6", text="WEATHER 1500Z")
        over_air = bytes([SYN, SYN, SOH]) + body
        bits = frame_bits_from_bytes(over_air)
        audio = synthesize_msk_audio(bits, 12500, 1200)
        # 加噪
        rng = np.random.default_rng(2024)
        audio = audio + 0.03 * rng.standard_normal(len(audio))
        msgs = acars_decode_iq(audio, 12500, 1200)
        assert len(msgs) >= 1
        m = msgs[0]
        assert m["crc_ok"] is True
        assert m["reg"].strip() == "N842UA"
        assert m["label"] == "DF"
        assert "WEATHER" in m["text"]

    def test_tool_entry_functions(self):
        body = build_acars_frame(mode="A", reg="D-ABYZ", label="AN",
                                 block_id="1", text="POS 47.0N 011.0E")
        # acars_parse_message
        d = acars_parse_message(list(body))
        assert d["crc_ok"] is True
        assert d["reg"].strip() == "D-ABYZ"
        assert d["label"] == "AN"
        # acars_msk_demod on a known bit pattern
        bits = frame_bits_from_bytes(bytes([0xAA, 0x55]))
        audio = synthesize_msk_audio(bits, 12500, 1200)
        rec = acars_msk_demod(audio, 12500, 1200)
        assert len(rec) >= 8


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
