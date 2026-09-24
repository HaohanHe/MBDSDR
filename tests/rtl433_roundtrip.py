"""
rtl_433 真实移植 - 往返验证测试
=================================

验证 mbdsdr_ai/rtl433_decoder.py 的每一级都真实可用：
  1. 每个设备解码器：已知合法比特/字节流 → 解出正确物理量
  2. 曼彻斯特编码 → 解码 往返一致
  3. OOK 脉冲检测：合成包络 → 正确提取 pulse/gap
  4. PPM/PWM 宽度切片 → 正确比特

载荷均为「能通过对应 C 版校验和/奇偶校验」的合法帧，字段按源码公式反推。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai import rtl433_decoder as R  # noqa: E402


class TestManchesterRoundtrip(unittest.TestCase):
    def test_encode_decode_roundtrip(self):
        """曼彻斯特编码→解码往返。来源: src/bitbuffer.c:255-280"""
        data = [1, 0, 1, 1, 0, 0, 1, 0, 1, 1, 0, 0, 1, 0]
        enc = R.manchester_encode_bits(data)
        # 编码后每个数据位变 2 位
        self.assertEqual(len(enc), 2 * len(data))
        dec = R.manchester_decode_bits(enc)
        self.assertEqual(dec, data)

    def test_manchester_pair_rule(self):
        """'10'→0，'01'→1，'00'/'11' 非法停止。来源: src/bitbuffer.c:266-277"""
        self.assertEqual(R.manchester_decode_bits([1, 0]), [0])
        self.assertEqual(R.manchester_decode_bits([0, 1]), [1])
        # '00' 非法，停止
        self.assertEqual(R.manchester_decode_bits([0, 0, 1, 0]), [])

    def test_bits_bytes_roundtrip(self):
        data = bytes([0xAB, 0x0F, 0x12])
        bits = R.bytes_to_bits(data)
        self.assertEqual(R.bits_to_bytes(bits), data)


class TestPulseDemodulator(unittest.TestCase):
    def test_synthetic_ook_pulses_extracted(self):
        """合成 OOK 包络 → 正确提取脉冲/间隔时长。
        阈值/迟滞状态机来源: src/pulse_detect.c:300-430。"""
        sr = 250_000
        us = 1e6 / sr  # 4 us/sample
        noise, high = 100.0, 1000.0
        env = [noise] * 3000  # 前导噪声让低电平估计收敛
        pulse_samp = 30  # 120 us
        gap_samp = 60    # 240 us
        for _ in range(8):
            env += [high] * pulse_samp
            env += [noise] * gap_samp
        env += [noise] * 500

        demod = R.PulseDemodulator(sample_rate=sr)
        pulses = demod.detect(env)

        self.assertGreaterEqual(len(pulses), 5)
        for p in pulses:
            # 容差 ±15%（阈值迟滞会吃掉首尾几个采样点）
            self.assertAlmostEqual(p.pulse_us, pulse_samp * us, delta=pulse_samp * us * 0.15 + us)
            self.assertAlmostEqual(p.gap_us, gap_samp * us, delta=gap_samp * us * 0.15 + us)

    def test_ppm_slicer(self):
        """PPM: 短间隔=0, 长间隔=1。来源: src/pulse_slicer.c:310-318"""
        cfg = R.DeviceConfig("Nexus", R.OOK_PULSE_PPM, short_width=1000, long_width=2000)
        pulses = [R.Pulse(500, 1000), R.Pulse(500, 2000), R.Pulse(500, 2000), R.Pulse(500, 1000)]
        self.assertEqual(R.slice_ppm(pulses, cfg), [0, 1, 1, 0])


class TestDeviceDecoders(unittest.TestCase):
    """每个设备一条已知合法消息 → 正确物理量。"""

    def test_nexus(self):
        """Nexus: id=0x45, ch1, bat OK, 22.5°C, 45%RH。来源: devices/nexus.c:90-96"""
        out = R.NexusDecoder().decode_message(bytes.fromhex("4580E1F2D0"))
        self.assertIsNotNone(out)
        self.assertEqual(out["id"], 0x45)
        self.assertEqual(out["channel"], 1)
        self.assertTrue(out["battery_ok"])
        self.assertAlmostEqual(out["temperature_c"], 22.5, places=1)
        self.assertEqual(out["humidity"], 45)

    def test_acurite_5n1(self):
        """Acurite 5n1 temp/hum 帧。输入为切片原始比特，解码器内部做 bitbuffer_invert。
        来源: devices/acurite.c:1349,607,649,658"""
        v = bytes([0xC1, 0x23, 0x78, 0x00, 0x09, 0x00, 0xB7, 0x1C])
        raw = bytes([~x & 0xFF for x in v])  # 还原成切片原始输出
        out = R.Acurite5n1Decoder().decode_message(raw)
        self.assertIsNotNone(out)
        self.assertEqual(out["id"], 0x123)
        self.assertAlmostEqual(out["temperature_c"], 24.0, places=1)
        self.assertEqual(out["humidity"], 55)
        self.assertTrue(out["battery_ok"])
        # 错误校验和必须被拒
        bad = bytes([~x & 0xFF for x in [0xC1, 0x23, 0x78, 0, 9, 0, 0xB7, 0x1D]])
        self.assertIsNone(R.Acurite5n1Decoder().decode_message(bad))

    def test_ambient_weather(self):
        """Ambient F007TH: id=0x37, ch1, 20.0°C, 48%RH。来源: devices/ambient_weather.c:59-64"""
        body = bytes([0x05, 0x37, 0x04, 0x38, 0x30])
        b5 = R.lfsr_digest8(body, 0x98, 0x3e) ^ 0x64
        out = R.AmbientWeatherTHDecoder().decode_message(body + bytes([b5]))
        self.assertIsNotNone(out)
        self.assertEqual(out["id"], 0x37)
        self.assertEqual(out["channel"], 1)
        self.assertAlmostEqual(out["temperature_c"], 20.0, places=1)
        self.assertEqual(out["humidity"], 48)

    def test_lacrosse_tx(self):
        """LaCrosse TX: id=21, 22.5°C。来源: devices/lacrosse.c:121-138"""
        out = R.LaCrosseTXDecoder().decode_message(bytes.fromhex("0A02A72572D0"))
        self.assertIsNotNone(out)
        self.assertEqual(out["id"], 21)
        self.assertAlmostEqual(out["temperature_c"], 22.5, places=1)

    def test_oregon_thgr122n(self):
        """Oregon THGR122N: id=0x35, ch1, 22.5°C, 45%RH。
        先构造 reflect 后的 msg 域字节，再 reflect_nibbles 还原成输入。
        来源: devices/oregon_scientific.c:52-87,233,240-243"""
        # msg(post-reflect) = [1D 20 <ch<<4|id_lo> <id_hi<<4|battery> 52 20 54 ...]
        # device_id = (msg2&0x0F)|(msg3&0xF0) = 0x35 → msg2 low=0x5, msg3 high=0x3
        msg = bytearray([0x1D, 0x20, 0x05, 0x30, 0x52, 0x20, 0x54, 0x00, 0x00])
        tot = 0
        for i in range(7):
            tot += (msg[i] >> 4) + (msg[i] & 0x0F)
        tot += msg[7] >> 4  # nibbles_in_checksum=15 (odd)
        msg[7] = (0 << 4) | (tot % 16)
        msg[8] = (tot // 16) << 4
        inp = bytes(R.reflect_nibbles(msg))
        out = R.OregonTHGRDecoder().decode_message(inp)
        self.assertIsNotNone(out)
        self.assertEqual(out["id"], 0x35)
        self.assertEqual(out["channel"], 1)
        self.assertAlmostEqual(out["temperature_c"], 22.5, places=1)
        self.assertEqual(out["humidity"], 45)

    def test_tpms_citroen(self):
        """Citroen TPMS: id=deadbeef, ~220kPa, 25°C。来源: devices/tpms_citroen.c:58-85"""
        b = bytearray([0x00, 0xDE, 0xAD, 0xBE, 0xEF, 0x00, 0xA1, 0x4B, 0x01])
        crc = 0
        for x in b[1:]:
            crc ^= x
        out = R.TpmsCitroenDecoder().decode_message(bytes(b) + bytes([crc]))
        self.assertIsNotNone(out)
        self.assertEqual(out["id"], "deadbeef")
        self.assertAlmostEqual(out["pressure_kpa"], 0xA1 * 1.364, places=1)
        self.assertAlmostEqual(out["temperature_c"], 25.0, places=1)

    def test_tpms_elantra(self):
        """Elantra2012 TPMS: id=01020304, 240kPa, 27°C。来源: devices/tpms_elantra2012.c:63-72"""
        b = bytearray([180, 77, 1, 2, 3, 4, 0x40, 0])
        b[7] = R.crc8(bytes(b[:7]), 0x07, 0x00)
        out = R.TpmsElantraDecoder().decode_message(bytes(b))
        self.assertIsNotNone(out)
        self.assertEqual(out["id"], "01020304")
        self.assertEqual(out["pressure_kpa"], 240)
        self.assertEqual(out["temperature_c"], 27)
        self.assertTrue(out["battery_ok"])

    def test_reject_bad_crc(self):
        """篡改一字节应让 CRC/XOR 校验失败返回 None。"""
        b = bytearray([180, 77, 1, 2, 3, 4, 0x40, 0])
        b[7] = R.crc8(bytes(b[:7]), 0x07, 0x00)
        good = bytes(b)
        bad = bytearray(good)
        bad[2] ^= 0xFF
        self.assertIsNone(R.TpmsElantraDecoder().decode_message(bytes(bad)))


class TestRegistryAndTools(unittest.TestCase):
    def test_all_devices_registered(self):
        devs = R.list_devices()
        types = {d["device_type"] for d in devs}
        for need in ["acurite_5n1", "ambient_weather_f007th", "lacrosse_tx",
                     "oregon_thgr122n", "nexus", "tpms_citroen", "tpms_elantra2012"]:
            self.assertIn(need, types)

    def test_tool_registration(self):
        """注册到 ToolRegistry：rtl433_list_devices / decode_pulses / decode_iq。"""
        from mbdsdr_ai.tool_registry import ToolRegistry
        reg = ToolRegistry()
        R.register_rtl433_tools(reg)
        for name in ["rtl433_list_devices", "rtl433_decode_pulses", "rtl433_decode_iq"]:
            self.assertTrue(reg.has_tool(name), f"missing tool {name}")
        res = reg.call("rtl433_list_devices", {})
        self.assertTrue(res.success)
        # 用已知合法帧调 decode_pulses
        r2 = reg.call("rtl433_decode_pulses",
                      {"device_type": "nexus", "payload_hex": "4580E1F2D0"})
        self.assertTrue(r2.success, r2.content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
