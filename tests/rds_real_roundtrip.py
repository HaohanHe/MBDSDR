"""
redsea 真实移植 - RDS 块同步/CRC/PS/RT/PTY/AF/时钟 往返验证
============================================================

对照 redsea 真实源码（repos/redsea/src/）逐字段验证 mbdsdr_ai/rds_lite.py：
  1. 块同步：合成 4 块+偏移字的组 → 正确提取 A/B/C/D 四个 16bit 字
  2. CRC：已知合法组伴随式命中；破坏 1bit 后该组伴随式失配、被丢弃
  3. PS：4 组 Type0A → 拼出 8 字符节目名
  4. RT：16 组 Type2A → 拼出 64 字符无线电文本
  5. PI/PTY/TP/TA 字段提取
  6. 4A 时钟时间解码（MJD → 日期，时/分/本地偏移）

所有常量/位域来源见 rds_lite.py 内联注释（redsea src/...:行号）。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai import rds_lite as R  # noqa: E402


class TestBlockSync(unittest.TestCase):
    """块同步：合成 RDS 数据组(4块+偏移字) → 正确提取。来源: block_sync.cc"""

    def test_offset_words_match_redsea(self):
        """偏移字必须与 redsea block_sync.cc:138-144 完全一致。"""
        self.assertEqual(R.OFFSET_WORDS["A"], 0x0FC)
        self.assertEqual(R.OFFSET_WORDS["B"], 0x198)
        self.assertEqual(R.OFFSET_WORDS["C"], 0x168)
        self.assertEqual(R.OFFSET_WORDS["C'"], 0x350)
        self.assertEqual(R.OFFSET_WORDS["D"], 0x1B4)

    def test_syndromes_match_redsea(self):
        """特征伴随式必须与 redsea block_sync.cc:72-78 完全一致。"""
        self.assertEqual(R.SYNDROME_TO_OFFSET[0b1111011000], "A")
        self.assertEqual(R.SYNDROME_TO_OFFSET[0b1111010100], "B")
        self.assertEqual(R.SYNDROME_TO_OFFSET[0b1001011100], "C")
        self.assertEqual(R.SYNDROME_TO_OFFSET[0b1111001100], "C'")
        self.assertEqual(R.SYNDROME_TO_OFFSET[0b1001011000], "D")

    def test_encode_decode_block_roundtrip(self):
        """encode_block → decode_block 往返：信息字原样还原，块名正确。"""
        for name, data in [("A", 0xDDEE), ("B", 0x1234), ("C", 0x005A),
                           ("C'", 0x0FFF), ("D", 0x4849)]:
            raw = R.encode_block(data, name)
            self.assertEqual(raw >> 10, data, f"{name} 信息字应保留")
            got_name, got_data = R.decode_block(raw)
            self.assertEqual(got_name, name, f"{name} 块名识别")
            self.assertEqual(got_data, data, f"{name} 信息字还原")

    def test_blocksync_finds_four_blocks(self):
        """合成一个完整 0A 组 → 块同步找到 A,B,C,D 四块。"""
        bits = R.build_0a_group(pi=0xDDEE, seg=0, chars2="MB", pty=3)
        groups = R.blocksync_from_bits(bits)
        self.assertEqual(len(groups), 1)
        g = groups[0]
        self.assertEqual(set(g.keys()), {"A", "B", "C", "D"})
        self.assertEqual(g["A"], 0xDDEE)


class TestCRC(unittest.TestCase):
    """10bit CRC：合法组通过；破坏 1bit 后 CRC 失败、组被丢弃。"""

    def test_valid_group_crc_passes(self):
        bits = R.build_0a_group(pi=0x1234, seg=1, chars2="DS")
        groups = R.blocksync_from_bits(bits)
        self.assertEqual(len(groups), 1, "合法组应通过 CRC 并被同步")

    def test_single_bit_error_rejected(self):
        bits = R.build_0a_group(pi=0x1234, seg=1, chars2="DS")
        # 翻转 D 块内 1 bit（位置 3*26+5）
        bad = list(bits)
        bad[3 * 26 + 5] ^= 1
        groups = R.blocksync_from_bits(bad)
        self.assertEqual(len(groups), 0, "破坏 1bit 后 CRC 应失败、组被丢弃")

    def test_syndrome_of_offset_word(self):
        """对偏移字求伴随式应等于其特征伴随式（block_sync.cc:156 对偶）。"""
        for name, off in R.OFFSET_WORDS.items():
            s = R.calculate_syndrome(off)
            self.assertEqual(R.SYNDROME_TO_OFFSET[s], name,
                             f"偏移字 {name} 的伴随式应命中自身")


class TestPSDecode(unittest.TestCase):
    """Type0A PS：4 组 → 拼出 8 字符节目名。来源: station.cc:244-333"""

    def test_ps_eight_chars(self):
        bits = []
        for seg, ch in enumerate(["MB", "DS", "DR", "  "]):
            bits += R.build_0a_group(pi=0xDDEE, seg=seg, chars2=ch, pty=3)
        groups = R.blocksync_from_bits(bits)
        parsed = R.rds_decode_groups(groups)
        summary = R.rds_extract_ps_rt(parsed)
        self.assertEqual(summary["ps"], "MBDSDR")
        self.assertEqual(summary["pi_hex"], "DDEE")

    def test_ps_segment_address_from_block_b(self):
        """段地址必须来自 Block B 低 2bit（station.cc:248），不是 Block C。"""
        bits = R.build_0a_group(pi=0x0001, seg=2, chars2="AB")
        parsed = R.rds_decode_groups(R.blocksync_from_bits(bits))
        self.assertEqual(parsed[0]["ps_segment"], 2)
        self.assertEqual(parsed[0]["ps_chars"], "AB")


class TestRTDecode(unittest.TestCase):
    """Type2A RT：16 组 → 拼出 64 字符无线电文本。来源: station.cc:418-481"""

    def test_rt_sixty_four_chars(self):
        msg = "NOW PLAYING: Roundtrip verification of real redsea RDS decode"
        msg = (msg + " " * 64)[:64]
        bits = []
        for addr in range(16):
            bits += R.build_2a_group(pi=0xABCD, addr=addr, ab=0,
                                     chars4=msg[addr * 4:addr * 4 + 4], pty=12)
        parsed = R.rds_decode_groups(R.blocksync_from_bits(bits))
        summary = R.rds_extract_ps_rt(parsed)
        self.assertEqual(summary["rt"], msg.rstrip())


class TestCommonFields(unittest.TestCase):
    """PI / PTY / TP / TA 字段提取。来源: station.cc:217-251"""

    def test_pi_pty_tp_ta(self):
        # TP=1, PTY=12, TA=1
        bits = R.build_0a_group(pi=0xBEEF, seg=0, chars2="X ", pty=12, tp=1, ta=1)
        parsed = R.rds_decode_groups(R.blocksync_from_bits(bits))[0]
        self.assertEqual(parsed["pi_hex"], "BEEF")
        self.assertEqual(parsed["pty"], 12)
        self.assertEqual(parsed["tp"], 1)
        self.assertEqual(parsed["ta"], 1)
        self.assertEqual(parsed["group"], "0A")

    def test_group_type_version(self):
        bits = R.build_2a_group(pi=0x0000, addr=0, ab=1, chars4="ABCD", pty=0)
        parsed = R.rds_decode_groups(R.blocksync_from_bits(bits))[0]
        self.assertEqual(parsed["group"], "2A")


class TestClockTime(unittest.TestCase):
    """Type4A 时钟时间解码。来源: station.cc:576-655"""

    def test_mjd_to_date(self):
        """MJD → 公历与 1858-11-17 历元一致。"""
        self.assertEqual(R.mjd_to_date(58119), (2018, 1, 1))
        self.assertEqual(R.mjd_to_date(60310), (2024, 1, 1))
        self.assertEqual(R.mjd_to_date(61307), (2026, 9, 24))

    def test_4a_clock_roundtrip(self):
        mjd, hour, minute, off_sign, off_mag = 61307, 13, 45, 0, 8  # UTC+4
        b_lo = ((mjd >> 16) & 1) << 1 | ((mjd >> 15) & 1)
        c = ((mjd & 0x7FFF) << 1) | ((hour >> 4) & 1)
        d = ((hour & 0xF) << 12) | ((minute & 0x3F) << 6) | \
            ((off_sign & 1) << 5) | (off_mag & 0x1F)
        bits = R.build_4a_group(pi=0xABCD, b=b_lo, c=c, d=d)
        parsed = R.rds_decode_groups(R.blocksync_from_bits(bits))[0]
        self.assertEqual(parsed["group"], "4A")
        self.assertEqual(parsed["ct_mjd"], mjd)
        self.assertEqual(parsed["ct_hour"], hour)
        self.assertEqual(parsed["ct_minute"], minute)
        self.assertAlmostEqual(parsed["ct_local_offset_h"], 4.0)
        summary = R.rds_extract_ps_rt([parsed])
        self.assertEqual(summary["clock"]["utc"], "2026-09-24T13:45:00Z")


class TestMPXRoundtrip(unittest.TestCase):
    """合成 MPX → 解调 → 块同步 → PS（端到端）。来源: subcarrier.cc"""

    def test_mpx_synth_demod(self):
        bits = []
        for seg, ch in enumerate(["MB", "DS", "DR", "  "]):
            bits += R.build_0a_group(pi=0xDDEE, seg=seg, chars2=ch, pty=3)
        fs = 171000
        mpx = R.synthesize_rds_mpx([bits], fs=fs, amplitude=0.4, noise_std=0.002)
        res = R.rds_decode_mpx(mpx, fs)
        self.assertTrue(res["rds_present"])
        self.assertEqual(res["pi_hex"], "DDEE")
        self.assertEqual(res["ps"], "MBDSDR")


if __name__ == "__main__":
    unittest.main()
