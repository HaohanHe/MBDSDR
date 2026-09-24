#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
osmosdr_source_test.py — 验证 gr-osmosdr 通用 SDR 源抽象的移植正确性。

覆盖：
  1. 设备字符串解析："rtl=0" -> driver=rtl, index=0（arg_helpers.h + source_impl.cc）
  2. 增益范围：各后端 LNA/VGA/IF/RF 范围与 C++ 一致（ranges.cc + 各后端）
  3. 无设备枚举：DeviceEnumerator.enumerate() 返回 [] 不崩溃
  4. 设备路由：根据字符串选中正确后端类
  5. 参数验证：采样率/频率范围与各后端一致

红线：无硬件时不造假——read_samples 必须抛 RuntimeError。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mbdsdr_ai import osmosdr_source as osmo


class TestDeviceStringParse(unittest.TestCase):
    """设备字符串解析 —— 来源 arg_helpers.h:48-110 + source_impl.cc:271-397。"""

    def test_rtl_index(self):
        # "rtl=0" -> dict {rtl:0}；路由到 rtl。
        # 来源 rtl_source_c.cc:385 get_devices() 产出 "rtl=<i>"
        parsed = osmo.parse_device_string("rtl=0")
        self.assertIn("rtl", parsed)
        self.assertEqual(parsed["rtl"]["rtl"], "0")

    def test_hackrf_serial(self):
        # "hackrf=<serial>"。来源 hackrf_common.cc:216
        parsed = osmo.parse_device_string("hackrf=aa11bb22")
        self.assertEqual(parsed["hackrf"]["hackrf"], "aa11bb22")

    def test_bladerf(self):
        # "bladerf=<instance>"。来源 bladerf_common.cc:428
        parsed = osmo.parse_device_string("bladerf=0")
        self.assertEqual(parsed["bladerf"]["bladerf"], "0")

    def test_soapy_with_driver(self):
        # "soapy=<i>,driver=rtlsdr"。来源 soapy_source_c.cc:118
        parsed = osmo.parse_device_string("soapy=0,driver=rtlsdr")
        self.assertEqual(parsed["soapy"]["soapy"], "0")
        self.assertEqual(parsed["soapy"]["driver"], "rtlsdr")

    def test_uhd_addr(self):
        # "uhd,type=b200"。来源 uhd_source_c.cc:140
        parsed = osmo.parse_device_string("uhd,type=b200")
        self.assertIn("uhd", parsed)
        self.assertEqual(parsed["uhd"]["type"], "b200")

    def test_quoted_label(self):
        # 单引号包裹的值应去掉引号。来源 arg_helpers.h:104-105
        parsed = osmo.parse_device_string("rtl=0,label='My Stick'")
        self.assertEqual(parsed["rtl"]["label"], "My Stick")

    def test_multi_device_space_separated(self):
        # 空格分隔多设备。来源 arg_helpers.h:52
        parsed = osmo.parse_device_string("rtl=0 hackrf=aa11bb")
        self.assertIn("rtl", parsed)
        self.assertIn("hackrf", parsed)


class TestDeviceRouting(unittest.TestCase):
    """根据设备字符串路由到正确后端 —— source_impl.cc:296-397。"""

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
    """各后端增益范围 —— 与 C++ 源码端点一致。"""

    def test_hackrf_gain_stages(self):
        # hackrf_source_c.cc:307  gain names RF/IF/BB
        s = osmo.OsmoSDRSource("hackrf=0")
        self.assertEqual(s.get_gain_names(), ["RF", "IF", "BB"])
        # RF(AMP) 0..14 step14 —— hackrf_source_c.cc:318
        self.assertEqual(s.get_gain_range("RF").to_pp_string(), "(0, 14, 14)")
        # IF(LNA) 0..40 step8 —— hackrf_source_c.cc:322
        self.assertEqual(s.get_gain_range("IF").to_pp_string(), "(0, 40, 8)")
        # BB(VGA) 0..62 step2 —— hackrf_source_c.cc:326
        self.assertEqual(s.get_gain_range("BB").to_pp_string(), "(0, 62, 2)")

    def test_hackrf_gain_clip_step(self):
        # 0..62 step2：33 应吸附到 34（boost round .5 远离零）
        s = osmo.OsmoSDRSource("hackrf=0")
        self.assertEqual(s.set_gain(33, "BB"), 34.0)
        # 越界夹到端点
        self.assertEqual(s.set_gain(-5, "BB"), 0.0)
        self.assertEqual(s.set_gain(99, "BB"), 62.0)

    def test_bladerf_gain_stages(self):
        # bladerf_common.cc:756  LNA/VGA1/VGA2
        s = osmo.OsmoSDRSource("bladerf=0")
        self.assertEqual(s.get_gain_names(), ["LNA", "VGA1", "VGA2"])
        # LNA 0..6 step3 —— bladerf_common.cc:792
        self.assertEqual(s.get_gain_range("LNA").to_pp_string(), "(0, 6, 3)")
        # VGA1 5..30 step1 —— bladerf_common.cc:794
        self.assertEqual(s.get_gain_range("VGA1").to_pp_string(), "(5, 30, 1)")
        # VGA2 0..30 step3 —— bladerf_common.cc:796
        self.assertEqual(s.get_gain_range("VGA2").to_pp_string(), "(0, 30, 3)")

    def test_rtl_gain_names(self):
        # rtl_source_c.cc:531-536  总有 LNA；E4000 才有 IF
        s = osmo.OsmoSDRSource("rtl=0")
        self.assertIn("LNA", s.get_gain_names())
        # E4000 IF 范围 3..56 step1 —— rtl_source_c.cc:565
        self.assertEqual(
            osmo.RTL_E4000_IF_GAIN_RANGE.to_pp_string(), "(3, 56, 1)")


class TestSampleRateFreqRanges(unittest.TestCase):
    """采样率/频率范围 —— 与 C++ 一致。"""

    def test_rtl_sample_rates(self):
        # rtl_source_c.cc:421-429  9 个已知可用档
        self.assertEqual(len(osmo.RTL_SAMPLE_RATES_HZ), 9)
        self.assertIn(1_024_000.0, osmo.RTL_SAMPLE_RATES_HZ)
        # 默认 1024000 —— rtl_source_c.cc:198
        self.assertEqual(osmo.RTL_DEFAULT_SAMPLE_RATE_HZ, 1_024_000.0)

    def test_rtl_tuner_freq_ranges(self):
        # R820T 24MHz..1766MHz —— rtl_source_c.cc:484
        r820 = osmo.RTL_TUNER_FREQ_RANGES["R820T"][0]
        self.assertAlmostEqual(r820.start, 24e6)
        self.assertAlmostEqual(r820.stop, 1766e6)
        # E4000 52MHz..2.2GHz —— rtl_source_c.cc:475
        e4k = osmo.RTL_TUNER_FREQ_RANGES["E4000"][0]
        self.assertAlmostEqual(e4k.start, 52e6)
        self.assertAlmostEqual(e4k.stop, 2.2e9)

    def test_hackrf_sample_rates(self):
        # hackrf_common.cc:250-254
        self.assertEqual(list(osmo.HACKRF_SAMPLE_RATES_HZ),
                         [8e6, 10e6, 12.5e6, 16e6, 20e6])
        # 默认 = start = 8e6 —— hackrf_source_c.cc:101
        self.assertEqual(osmo.HACKRF_DEFAULT_SAMPLE_RATE_HZ, 8e6)

    def test_sample_rate_adhere(self):
        # set_sample_rate 应吸附到已知离散档（最近档）
        s = osmo.OsmoSDRSource("rtl=0")
        # 1.5MHz 距 1.8MHz(0.3M) 比 1.024MHz(0.476M) 更近 -> 1.8M
        self.assertEqual(s.set_sample_rate(1_500_000), 1_800_000.0)
        s2 = osmo.OsmoSDRSource("hackrf=0")
        self.assertEqual(s2.set_sample_rate(15e6), 16e6)  # 最近档


class TestRangeClass(unittest.TestCase):
    """GainRange/MetaRange —— 移植自 ranges.cc。"""

    def test_stop_lt_start_raises(self):
        # ranges.cc:49-51
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
