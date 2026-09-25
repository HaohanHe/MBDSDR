"""
FY-4 DVB-S2 物理层 + FY-3 X 波段 AHRPT 测试
=============================================

覆盖（移植自 SatDump GPL-3.0，来源标注见 fengyun_sat.py 各段注释）：
  1. DVB-S2 SOF 26-bit 硬匹配检测
  2. PLFRAME 定界（normal=64890 bit / short=16290 bit）
  3. PLS code 编解码往返（modcod / short / pilots）
  4. DVB-S2 BB 解扰 PRBS 往返（自逆）
  5. fy4_dvbs2_sync 端到端：SOF→PLS→解扰
  6. FY-3 AHRPT ASM(0x1ACFFC1D) 帧同步
  7. FY-3 CCSDS 解扰往返
  8. FY-3 AVHRR 通道提取 shape

运行: python3 -m pytest tests/test_fengyun_dvbs2.py -v
"""

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.fengyun_sat import (  # noqa: E402
    DVBS2_SOF_VALUE,
    DVBS2_SOF_LEN,
    DVBS2_PLHEADER_LEN,
    DVBS2_FRAME_NORMAL_BITS,
    DVBS2_FRAME_SHORT_BITS,
    _DVBS2_PLS_CODEWORDS,
    detect_sof,
    extract_plframe,
    decode_pls,
    descramble_dvbs2,
    fy4_dvbs2_sync,
    FY3_AHRPT_ASM,
    FY3_AHRPT_CADU_LEN,
    fy3_ahrpt_sync,
    fy3_descramble,
    fy3_extract_channels,
)


def _sof_bits() -> np.ndarray:
    """把 26-bit SOF_VALUE 按 MSB-first 转成比特数组。"""
    return np.array([(DVBS2_SOF_VALUE >> (DVBS2_SOF_LEN - 1 - i)) & 1
                     for i in range(DVBS2_SOF_LEN)], dtype=np.uint8)


def _codeword_bits(index: int) -> np.ndarray:
    """把 64-bit PLS codeword 按 MSB-first 转成比特数组。"""
    cw = _DVBS2_PLS_CODEWORDS[index]
    return np.array([(cw >> (63 - i)) & 1 for i in range(64)], dtype=np.uint8)


class TestDvbS2Sync(unittest.TestCase):
    def test_sof_detection(self):
        """构造含 SOF 的比特流，验证检测到正确位置。"""
        rng = np.random.default_rng(0)
        preamble = rng.integers(0, 2, size=53).astype(np.uint8)
        sof = _sof_bits()
        tail = rng.integers(0, 2, size=200).astype(np.uint8)
        stream = np.concatenate([preamble, sof, tail])
        hits = detect_sof(stream)
        self.assertIn(len(preamble), hits, f"未检测到 SOF，hits={hits}")

    def test_plframe_extraction(self):
        """从已知位置提取 PLFRAME，验证长度。"""
        rng = np.random.default_rng(1)
        sof = _sof_bits()
        body = rng.integers(0, 2, size=DVBS2_FRAME_NORMAL_BITS).astype(np.uint8)
        header = np.concatenate([sof, np.zeros(64, dtype=np.uint8)])
        stream = np.concatenate([np.zeros(10, dtype=np.uint8), header, body])
        frame = extract_plframe(stream, 10, "normal")
        self.assertEqual(len(frame), DVBS2_PLHEADER_LEN + DVBS2_FRAME_NORMAL_BITS)
        short_frame = extract_plframe(
            np.concatenate([np.zeros(5, dtype=np.uint8), header,
                            rng.integers(0, 2, size=DVBS2_FRAME_SHORT_BITS)
                            .astype(np.uint8)]),
            5, "short")
        self.assertEqual(len(short_frame),
                         DVBS2_PLHEADER_LEN + DVBS2_FRAME_SHORT_BITS)

    def test_pls_roundtrip(self):
        """选一个 PLS index，编码成 64 bit，decode_pls 应还原 modcod/short/pilots。"""
        # modcod=4 (QPSK 1/2), normal, no pilots -> index = 4<<2 = 16
        index = 16
        pls_bits = _codeword_bits(index)
        out = decode_pls(pls_bits)
        self.assertEqual(out["modcod"], 4)
        self.assertEqual(out["coderate"], "1/2")
        self.assertEqual(out["constellation"], "QPSK")
        self.assertFalse(out["short_frame"])
        self.assertFalse(out["pilots"])
        # modcod=3 (QPSK 2/5), short, pilots -> index = (3<<2)|1|1 = 15
        pls_bits2 = _codeword_bits(15)
        out2 = decode_pls(pls_bits2)
        self.assertEqual(out2["modcod"], 3)
        self.assertTrue(out2["short_frame"])
        self.assertTrue(out2["pilots"])

    def test_dvbs2_descramble(self):
        """解扰往返：先扰再解扰应还原（PRBS 自逆）。"""
        rng = np.random.default_rng(2)
        data = rng.integers(0, 2, size=DVBS2_FRAME_NORMAL_BITS).astype(np.uint8)
        scrambled = descramble_dvbs2(data)
        self.assertFalse(np.array_equal(scrambled, data))
        recovered = descramble_dvbs2(scrambled)
        np.testing.assert_array_equal(recovered, data)

    def test_fy4_dvbs2_sync_e2e(self):
        """端到端：构造 SOF+PLS+数据 的 PLFRAME，fy4_dvbs2_sync 应解出。"""
        rng = np.random.default_rng(3)
        sof = _sof_bits()
        # modcod=4 QPSK 1/2, normal, no pilots
        pls = _codeword_bits(16)
        payload = rng.integers(0, 2, size=DVBS2_FRAME_NORMAL_BITS).astype(np.uint8)
        scrambled_payload = descramble_dvbs2(payload)
        stream = np.concatenate([
            np.zeros(7, dtype=np.uint8),
            sof, pls, scrambled_payload,
            np.zeros(50, dtype=np.uint8),
        ])
        frames = fy4_dvbs2_sync(stream)
        self.assertGreaterEqual(len(frames), 1)
        f0 = frames[0]
        self.assertEqual(f0["pos"], 7)
        self.assertEqual(f0["frame_type"], "normal")
        self.assertEqual(f0["pls"]["modcod"], 4)
        np.testing.assert_array_equal(f0["descrambled_bits"], payload)


class TestFy3AHRPT(unittest.TestCase):
    def test_fy3_ahrpt_sync(self):
        """构造含 ASM 的字节流，验证切出 1024B CADU。"""
        asm = FY3_AHRPT_ASM.to_bytes(4, "big")
        cadu1 = asm + bytes(range(256)) * ((FY3_AHRPT_CADU_LEN - 4) // 256 + 1)
        cadu1 = cadu1[:FY3_AHRPT_CADU_LEN]
        cadu2 = asm + bytes(100, ) * (FY3_AHRPT_CADU_LEN - 4)
        stream = b"\x00\x11\x22" + cadu1 + b"\xAA" + cadu2
        frames = fy3_ahrpt_sync(stream)
        self.assertEqual(len(frames), 2)
        self.assertEqual(len(frames[0]), FY3_AHRPT_CADU_LEN)
        self.assertEqual(len(frames[1]), FY3_AHRPT_CADU_LEN)
        self.assertEqual(int.from_bytes(frames[0][:4], "big"), FY3_AHRPT_ASM)

    def test_fy3_descramble(self):
        """CCSDS 解扰往返（PN 自逆）。"""
        rng = np.random.default_rng(4)
        data = bytes(rng.integers(0, 256, size=1020).tolist())
        scrambled = fy3_descramble(data)
        self.assertNotEqual(scrambled, data)
        recovered = fy3_descramble(scrambled)
        self.assertEqual(recovered, data)

    def test_fy3_channel_extraction(self):
        """构造模拟 CADU，验证通道提取 shape 与 VCID。"""
        # 期望的解扰后内容：6B VCDU 头（vcid=5）+ 1014B 载荷（共 1020B 数据区）
        derand = bytearray(1020)
        derand[0] = 0x00
        derand[1] = 0x05            # VCID = 5 (低6位)
        payload = np.arange(1014, dtype=np.uint8).tobytes()
        derand[6:] = payload
        # 传输时要先解扰（自逆）→ 还原成发送端已扰码的数据区
        tx_data = fy3_descramble(bytes(derand))
        cadu = FY3_AHRPT_ASM.to_bytes(4, "big") + tx_data
        self.assertEqual(len(cadu), FY3_AHRPT_CADU_LEN)
        out = fy3_extract_channels(cadu)
        self.assertEqual(out["vcid"], 5)
        third = 1014 // 3
        self.assertEqual(len(out["ch1"]), third)
        self.assertEqual(len(out["ch2"]), third)
        self.assertEqual(len(out["ch4"]), third)
        np.testing.assert_array_equal(
            out["ch1"], np.frombuffer(payload[0:third], dtype=np.uint8))


if __name__ == "__main__":
    unittest.main()
