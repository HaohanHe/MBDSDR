#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""
osmosdr_source_test.py — 验证统一 SDR 源抽象的离线行为。

覆盖：
  1. 设备字符串解析："rtl=0" -> driver=rtl, index=0；
  2. 增益范围：各后端 LNA/VGA/IF/RF 范围与量化吸附；
  3. 无设备枚举：DeviceEnumerator.enumerate() 返回 list 不崩溃；
  4. 设备路由：根据字符串选中正确后端；
  5. 参数验证：采样率吸附到已知离散档。

红线：无硬件时不造假——read_samples 必须抛 RuntimeError。
所有测试均为离线/合成，标注「非硬件 / NOT HARDWARE」。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mbdsdr_ai import osmosdr_source as osmo


class TestDeviceStringParse(unittest.TestCase):
    """设备字符串解析。"""

    def test_rtl_index(self):
        parsed = osmo.parse_device_string("rtl=0")
        self.assertIn("rtl", parsed)
        self.assertEqual(parsed["rtl"]["rtl"], "0")

    def test_hackrf_serial(self):
        parsed = osmo.parse_device_string("hackrf=aa11bb22")
        self.assertEqual(parsed["hackrf"]["hackrf"], "aa11bb22")

    def test_bladerf(self):
        parsed = osmo.parse_device_string("bladerf=0")
        self.assertEqual(parsed["bladerf"]["bladerf"], "0")

    def test_soapy_with_driver(self):
        parsed = osmo.parse_device_string("soapy=0,driver=rtlsdr")
        self.assertEqual(parsed["soapy"]["soapy"], "0")
        self.assertEqual(parsed["soapy"]["driver"], "rtlsdr")

    def test_uhd_addr(self):
        parsed = osmo.parse_device_string("uhd,type=b200")
        self.assertIn("uhd", parsed)
        self.assertEqual(parsed["uhd"]["type"], "b200")

    def test_quoted_label(self):
        # 单引号包裹的值应去掉引号。
        parsed = osmo.parse_device_string("rtl=0,label='My Stick'")
        self.assertEqual(parsed["rtl"]["label"], "My Stick")

    def test_multi_device_space_separated(self):
        parsed = osmo.parse_device_string("rtl=0 hackrf=aa11bb")
        self.assertIn("rtl", parsed)
        self.assertIn("hackrf", parsed)


class TestDeviceRouting(unittest.TestCase):
    """根据设备字符串路由到正确后端。"""

    def test_route_rtl(self):
        s = osmo.OsmoSDRSource("rtl=0")
        self.assertEqual(s.driver, "rtl")
        self.assertEqual(s.device_index, "0")

    def test_route_hackrf(self):
        s = osmo.OsmoSDRSource("hackrf=0")
        self.assertEqual(s.driver, "hackrf")

    def test_route_bladerf(self):
        s = osmo.OsmoSDRSource("bladerf=0")
        self.assertEqual(s.driver, "bladerf")

    def test_route_soapy(self):
        s = osmo.OsmoSDRSource("soapy=0,driver=rtlsdr")
        self.assertEqual(s.driver, "soapy")

    def test_route_uhd(self):
        s = osmo.OsmoSDRSource("uhd,type=b200")
        self.assertEqual(s.driver, "uhd")


class TestGainRanges(unittest.TestCase):
    """各后端增益分级与范围（数据手册标称值）。"""

    def test_hackrf_gain_stages(self):
        # gain names RF/IF/BB
        s = osmo.OsmoSDRSource("hackrf=0")
        self.assertEqual(s.get_gain_names(), ["RF", "IF", "BB"])
        # RF(前置放大) 0..14 step14
        self.assertEqual(s.get_gain_range("RF").to_pp_string(), "(0, 14, 14)")
        # IF(LNA) 0..40 step8
        self.assertEqual(s.get_gain_range("IF").to_pp_string(), "(0, 40, 8)")
        # BB(VGA) 0..62 step2
        self.assertEqual(s.get_gain_range("BB").to_pp_string(), "(0, 62, 2)")

    def test_hackrf_gain_clip_step(self):
        # 0..62 step2：33 应吸附到 34（半值远离零取整）
        s = osmo.OsmoSDRSource("hackrf=0")
        self.assertEqual(s.set_gain(33, "BB"), 34.0)
        # 越界夹到端点
        self.assertEqual(s.set_gain(-5, "BB"), 0.0)
        self.assertEqual(s.set_gain(99, "BB"), 62.0)

    def test_bladerf_gain_stages(self):
        # LNA/VGA1/VGA2
        s = osmo.OsmoSDRSource("bladerf=0")
        self.assertEqual(s.get_gain_names(), ["LNA", "VGA1", "VGA2"])
        self.assertEqual(s.get_gain_range("LNA").to_pp_string(), "(0, 6, 3)")
        self.assertEqual(s.get_gain_range("VGA1").to_pp_string(), "(5, 30, 1)")
        self.assertEqual(s.get_gain_range("VGA2").to_pp_string(), "(0, 30, 3)")

    def test_rtl_gain_names(self):
        # 总有 LNA；E4000 额外有 IF
        s = osmo.OsmoSDRSource("rtl=0")
        self.assertIn("LNA", s.get_gain_names())
        self.assertEqual(
            osmo.RTL_E4000_IF_GAIN_RANGE.to_pp_string(), "(3, 56, 1)")


class TestSampleRateFreqRanges(unittest.TestCase):
    """采样率/频率标称范围。"""

    def test_rtl_sample_rates(self):
        self.assertEqual(len(osmo.RTL_SAMPLE_RATES_HZ), 9)
        self.assertIn(1_024_000.0, osmo.RTL_SAMPLE_RATES_HZ)
        self.assertEqual(osmo.RTL_DEFAULT_SAMPLE_RATE_HZ, 1_024_000.0)

    def test_rtl_tuner_freq_ranges(self):
        # R820T 24MHz..1766MHz
        r820 = osmo.RTL_TUNER_FREQ_RANGES["R820T"][0]
        self.assertAlmostEqual(r820.start, 24e6)
        self.assertAlmostEqual(r820.stop, 1766e6)
        # E4000 52MHz..2.2GHz
        e4k = osmo.RTL_TUNER_FREQ_RANGES["E4000"][0]
        self.assertAlmostEqual(e4k.start, 52e6)
        self.assertAlmostEqual(e4k.stop, 2.2e9)

    def test_hackrf_sample_rates(self):
        self.assertEqual(list(osmo.HACKRF_SAMPLE_RATES_HZ),
                         [8e6, 10e6, 12.5e6, 16e6, 20e6])
        self.assertEqual(osmo.HACKRF_DEFAULT_SAMPLE_RATE_HZ, 8e6)

    def test_sample_rate_adhere(self):
        # set_sample_rate 应吸附到已知离散档（最近档）
        s = osmo.OsmoSDRSource("rtl=0")
        # 1.5MHz 距 1.8MHz(0.3M) 比 1.024MHz(0.476M) 更近 -> 1.8M
        self.assertEqual(s.set_sample_rate(1_500_000), 1_800_000.0)
        s2 = osmo.OsmoSDRSource("hackrf=0")
        self.assertEqual(s2.set_sample_rate(15e6), 16e6)  # 最近档


class TestRangeClass(unittest.TestCase):
    """GainRange/MetaRange 范围吸附语义。"""

    def test_stop_lt_start_raises(self):
        with self.assertRaises(ValueError):
            osmo.GainRange(100, 50)

    def test_clip_in_range(self):
        r = osmo.GainRange(0, 62, 2)
        self.assertEqual(r.clip(30), 30)

    def test_meta_range_clip_gap(self):
        # 两段不连续区间：落在间隙里应夹到最近段端点
        m = osmo.MetaRange([osmo.GainRange(0, 10), osmo.GainRange(100, 200)])
        self.assertEqual(m.clip(50), 10)   # 离 10 更近
        self.assertEqual(m.clip(60), 100)  # 离 100 更近


class TestNoDeviceNoFake(unittest.TestCase):
    """红线：无设备不造假。"""

    def test_enumerate_returns_list(self):
        # 无后端库时必须返回 list（可能为空），不抛异常
        devs = osmo.DeviceEnumerator.enumerate()
        self.assertIsInstance(devs, list)

    def test_read_samples_no_hw_raises(self):
        # 不连真实硬件时，绝不能返回假 IQ
        s = osmo.OsmoSDRSource("rtl=0")
        with self.assertRaises(RuntimeError):
            s.read_samples(1024)

    def test_backends_have_source_note(self):
        # uhd/soapy 范围靠运行时探测，必须标注而不是编死数值
        self.assertIn("UHD", osmo.UHD_NOTE)
        self.assertIn("SoapySDR", osmo.SOAPY_NOTE)


if __name__ == "__main__":
    unittest.main()
