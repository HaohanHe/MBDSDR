"""
RadioCAT 单元测试
=================

验证：
1. 无硬件时 connect('/dev/nonexistent', ...) 返回 False
2. set_frequency 在未连接时返回 False（不造假）
3. ICOM CI-V 帧构建正确（参考 Hamlib to_bcd / make_cmd_frame）
4. Yaesu NewCAT ASCII 帧构建正确（FA%09.0f;）
5. Kenwood 帧构建正确（F<vfo><11digits>;）
6. BCD 编解码往返

运行：
    cd <repo_root>
    python3 -m pytest tests/test_radio_control.py -v
"""

import os
import sys
import struct
import unittest

# 让 tests/ 能直接 import mbdsdr_ai
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from mbdsdr_ai.radio_control import (  # noqa: E402
    RadioCAT,
    _to_bcd_le,
    _from_bcd_le,
    _civ_frame,
    CIV_PREAMBLE, CIV_EOM, CIV_CTRL_ID,
    CIV_CMD_SET_FREQ, CIV_CMD_RD_FREQ,
)


class TestBcdCodec(unittest.TestCase):
    """BCD 小端编解码往返。来源: src/misc.c:146 to_bcd / :193 from_bcd"""

    def test_to_bcd_14074000(self):
        # 14,074,000 Hz → 10 位 BCD 小端
        # 数字(LSB→MSB): 0,0,0,0,4,7,0,4,1,0
        # 配对: (0,0)=0x00, (0,0)=0x00, (4,7)=0x74, (0,4)=0x40, (1,0)=0x01
        # 等等——重新数：14,074,000 = "14074000"
        #   10^0=0, 10^1=0, 10^2=0, 10^3=4, 10^4=7, 10^5=0, 10^6=4, 10^7=1
        # 10 位补零: 10^8=0, 10^9=0
        # pair0 (10^0,10^1) = (0,0) → 0x00
        # pair1 (10^2,10^3) = (0,4) → 0x40
        # pair2 (10^4,10^5) = (7,0) → 0x07
        # pair3 (10^6,10^7) = (4,1) → 0x14
        # pair4 (10^8,10^9) = (0,0) → 0x00
        b = _to_bcd_le(14074000, 5)
        self.assertEqual(b, bytes([0x00, 0x40, 0x07, 0x14, 0x00]),
                         f"14074000 BCD mismatch: {b.hex()}")

    def test_to_bcd_roundtrip(self):
        for f in (0, 1, 10, 100, 14074000, 28489000, 500000, 475000000):
            b = _to_bcd_le(f, 5)
            self.assertEqual(_from_bcd_le(b), f, f"roundtrip fail for {f}")


class TestIcomCivFrame(unittest.TestCase):
    """CI-V 帧格式。来源: rigs/icom/frame.c:52 make_cmd_frame"""

    def test_set_freq_frame_14074000_ic7300(self):
        # IC-7300 默认 CI-V 地址 0x94
        # 帧: FE FE 94 E0 05 00 40 07 14 00 FD
        frame = RadioCAT.build_icom_set_freq_frame(14074000, rig_addr=0x94)
        expected = bytes([
            0xFE, 0xFE,
            0x94,        # rig CI-V addr
            0xE0,        # controller addr
            0x05,        # C_SET_FREQ
            0x00, 0x40, 0x07, 0x14, 0x00,  # 5-byte little-endian BCD
            0xFD,        # EOM
        ])
        self.assertEqual(frame, expected,
                         f"ICOM set_freq frame mismatch:\n got={frame.hex()}\n exp={expected.hex()}")

    def test_read_freq_frame(self):
        # FE FE <rigaddr> E0 03 FD
        frame = _civ_frame(0x94, CIV_CMD_RD_FREQ)
        self.assertEqual(frame, bytes([0xFE, 0xFE, 0x94, 0xE0, 0x03, 0xFD]))

    def test_set_freq_other_models(self):
        # IC-705 = 0xA4
        f = RadioCAT.build_icom_set_freq_frame(14074000, rig_addr=0xA4)
        self.assertEqual(f[2], 0xA4)
        self.assertEqual(f[4], 0x05)  # cmd
        self.assertEqual(f[-1], 0xFD)


class TestYaesuFrame(unittest.TestCase):
    """Yaesu NewCAT ASCII。来源: rigs/yaesu/newcat.c:1647 FA%09.0f;"""

    def test_set_freq_14074000(self):
        cmd = RadioCAT.build_yaesu_set_freq_frame(14074000)
        # 14074000 共 8 位，补零到 9 位: "014074000"
        # 帧: FA + VFO('A') + 9 位 + ';'
        self.assertEqual(cmd, b"FAA014074000;")

    def test_set_freq_other(self):
        # 7,074,000 = 7 位 → 补零到 9: "007074000"
        self.assertEqual(RadioCAT.build_yaesu_set_freq_frame(7074000),
                         b"FAA007074000;")
        # 100,000,000 = 9 位
        self.assertEqual(RadioCAT.build_yaesu_set_freq_frame(100000000),
                         b"FAA100000000;")


class TestKenwoodFrame(unittest.TestCase):
    """Kenwood ASCII。来源: rigs/kenwood/kenwood.c:2042 F%c%011d;"""

    def test_set_freq_14074000(self):
        cmd = RadioCAT.build_kenwood_set_freq_frame(14074000)
        # 14074000 = 8 位，补零到 11 位: "00014074000"
        self.assertEqual(cmd, b"FA00014074000;")


class TestOfflineBehavior(unittest.TestCase):
    """无硬件 / 无串口时绝不假成功。"""

    def test_connect_nonexistent_returns_false(self):
        r = RadioCAT()
        # /dev/nonexistent 一定打不开；无论 pyserial 是否安装，都必须返回 False
        result = r.connect("/dev/nonexistent", 9600)
        self.assertFalse(result, "connect() 在不存在的串口上必须返回 False（不能假成功）")

    def test_set_freq_without_connect_returns_false(self):
        r = RadioCAT()
        # 从未 connect
        self.assertFalse(r.set_frequency(14074000))
        self.assertFalse(r.set_mode("USB"))
        self.assertFalse(r.set_ptt(True))
        self.assertIsNone(r.get_frequency())
        self.assertIsNone(r.get_ptt())

    def test_status_reports_not_connected(self):
        r = RadioCAT()
        st = r.get_status()
        self.assertFalse(st["connected"])
        self.assertIn("port", st)


class TestMorseUnchanged(unittest.TestCase):
    """Morse 编解码必须保持原有功能（红线：保留顶部 Morse 功能不动）。"""

    def test_morse_roundtrip(self):
        from mbdsdr_ai.radio_control import morse_encode, morse_decode
        text = "BI4MIB DE BI4MIB"
        self.assertEqual(morse_decode(morse_encode(text)), text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
