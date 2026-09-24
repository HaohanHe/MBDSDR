#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
limesuite_test.py — 验证 limesuite_params.py 移植自 LimeSuite/LMS7002M 的真实参数。

红线：增益表必须是 LimeSuite C 源码 + LMS7002M 驱动里的真实离散档，不能编造。
本测试直接断言源码表的条数与端点（见每个断言的注释来源 file:line）。

真实来源（repos/LimeSuite 下）：
  - LNA(RFE)  src/lms7002m/LMS7002M.cpp:789-837  SetRFELNA_dB / GetRFELNA_dB switch
  - TIA(RFE)  src/lms7002m/LMS7002M.cpp:890-914  SetRFETIA_dB / GetRFETIA_dB switch
  - PGA(RBB)  src/lms7002m/LMS7002M.cpp:763-787  SetRBBPGA_dB (G_PGA_RBB 5-bit)
  - 频率      src/API/lms7_device.cpp:1384 (USB 100e3-3.8e9)
              src/API/LimeSDR_mini.cpp:312 (Mini 10e6-3.5e9)
  - 采样率    src/API/lms7_device.cpp:690 (USB 100e3-61.44e6)
              src/API/LimeSDR_mini.cpp:307 (Mini 100e3-30.72e6)
  - 组合增益  src/lime/LimeSuite.h:382 [0,73]; src/API/lms7_device.cpp:1032 maxGain=74
  - 数据格式  src/lime/LimeSuite.h:1099-1104 (LMS_FMT_I12)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mbdsdr_ai import limesuite_params as lp


class TestGainTables(unittest.TestCase):
    """增益表条数与端点 —— 来源 LMS7002M.cpp。"""

    def test_lna_table(self):
        p = lp.LimeSDRParams()
        levels = p.lna_gain_levels_db()
        # LMS7002M.cpp:818-835 GetRFELNA_dB switch(case 1..15) → 15 档
        self.assertEqual(len(levels), 15)
        self.assertEqual(levels[0], 0)
        self.assertEqual(levels[-1], 30)
        # gmax=30 (LMS7002M.cpp:791)
        self.assertEqual(p.lna_gain_levels_db()[-1], lp.LNA_GAIN_MAX_DB)
        # 非线性步进: 0,3,6,9,12,15,18,21,24,25,26,27,28,29,30
        self.assertEqual(levels, [0, 3, 6, 9, 12, 15, 18, 21, 24, 25, 26, 27, 28, 29, 30])
        self.assertEqual(levels, sorted(levels))

    def test_tia_table(self):
        p = lp.LimeSDRParams()
        levels = p.tia_gain_levels_db()
        # LMS7002M.cpp:907-912 switch(case 1→0, case 2→9, case 3→12) → 3 档
        self.assertEqual(len(levels), 3)
        self.assertEqual(levels, [0, 9, 12])
        self.assertEqual(levels[0], 0)
        self.assertEqual(levels[-1], 12)

    def test_pga_table(self):
        p = lp.LimeSDRParams()
        regs = p.pga_gain_levels_reg()
        # LMS7002M.cpp:766 if (g_pga_rbb > 0x1f) = 31 → 5-bit 域 0..31，共 32 码
        self.assertEqual(len(regs), 32)
        self.assertEqual(regs[0], 0)
        self.assertEqual(regs[-1], 31)
        # G_PGA_RBB 是 5-bit
        self.assertEqual(lp.PGA_GAIN_MAX_REG, 31)

    def test_clamp_matches_c_tables(self):
        """clamp 函数必须与 C 侧 if-else 阶梯 / switch 语义一致。"""
        p = lp.LimeSDRParams()
        # LMS7002M.cpp:794-809 LNA 向下取档
        self.assertEqual(p.clamp_lna_gain(30), 30)   # >=30 → 30
        self.assertEqual(p.clamp_lna_gain(29.5), 29)  # >=29 → 29
        self.assertEqual(p.clamp_lna_gain(24.5), 24)  # >=24 → 24
        self.assertEqual(p.clamp_lna_gain(22), 21)   # >=21 → 21
        self.assertEqual(p.clamp_lna_gain(0), 0)
        self.assertEqual(p.clamp_lna_gain(-5), 0)    # <0 → 0
        # LMS7002M.cpp:895-898 TIA
        self.assertEqual(p.clamp_tia_gain(12), 12)
        self.assertEqual(p.clamp_tia_gain(10), 9)
        self.assertEqual(p.clamp_tia_gain(5), 0)
        self.assertEqual(p.clamp_tia_gain(0), 0)
        # lms7_device.cpp:1035-1038 组合增益钳位
        self.assertEqual(p.clamp_combined_gain(0), 0)
        self.assertEqual(p.clamp_combined_gain(73), 73)
        self.assertEqual(p.clamp_combined_gain(100), 73)


class TestFrequencyRange(unittest.TestCase):
    def test_freq_range_usb(self):
        p = lp.LimeSDRParams()
        # lms7_device.cpp:1384 → 100 kHz - 3.8 GHz
        self.assertEqual(p.min_freq_hz, 100_000)
        self.assertEqual(p.max_freq_hz, 3_800_000_000)
        self.assertTrue(p.is_valid_frequency(100_000))
        self.assertTrue(p.is_valid_frequency(3_800_000_000))
        self.assertTrue(p.is_valid_frequency(145_000_000))  # 2m 段
        self.assertFalse(p.is_valid_frequency(50_000))       # < 100 kHz
        self.assertFalse(p.is_valid_frequency(5_000_000_000))  # > 3.8 GHz

    def test_freq_range_mini(self):
        pm = lp.LimeSDRParams.for_mini()
        # LimeSDR_mini.cpp:312 → 10 MHz - 3.5 GHz
        self.assertEqual(pm.min_freq_hz, 10_000_000)
        self.assertEqual(pm.max_freq_hz, 3_500_000_000)
        self.assertTrue(pm.is_valid_frequency(10_000_000))
        self.assertTrue(pm.is_valid_frequency(3_500_000_000))
        self.assertFalse(pm.is_valid_frequency(1_000_000))  # Mini 下限 10 MHz


class TestSampleRate(unittest.TestCase):
    def test_sample_rate_range_usb(self):
        p = lp.LimeSDRParams()
        # lms7_device.cpp:690 → 100 kHz - 61.44 MHz
        self.assertEqual(p.min_sr_hz, 100_000)
        self.assertEqual(p.max_sr_hz, 61_440_000)
        self.assertTrue(p.is_valid_sample_rate(100_000))
        self.assertTrue(p.is_valid_sample_rate(61_440_000))
        self.assertTrue(p.is_valid_sample_rate(5_000_000))
        self.assertFalse(p.is_valid_sample_rate(50_000))
        self.assertFalse(p.is_valid_sample_rate(100_000_000))

    def test_sample_rate_range_mini(self):
        pm = lp.LimeSDRParams.for_mini()
        # LimeSDR_mini.cpp:307 → 100 kHz - 30.72 MHz
        self.assertEqual(pm.max_sr_hz, 30_720_000)
        self.assertTrue(pm.is_valid_sample_rate(30_720_000))
        self.assertFalse(pm.is_valid_sample_rate(61_440_000))

    def test_recommended_rates_all_valid(self):
        for r in lp.SUPPORTED_SAMPLE_RATES:
            self.assertTrue(lp.DEFAULT_PARAMS.is_valid_sample_rate(r), f"{r} 应合法")


class TestConstantsMatchSource(unittest.TestCase):
    """关键常量与 C 源码写死值一致（LimeSuite.h / lms7_device.cpp）。"""

    def test_combined_gain(self):
        # LimeSuite.h:382 "range [0, 73]"
        self.assertEqual(lp.COMBINED_GAIN_MIN_DB, 0)
        self.assertEqual(lp.COMBINED_GAIN_MAX_DB, 73)

    def test_antenna_paths(self):
        # LimeSuite.h:282-288
        self.assertEqual(lp.PATH_NONE, 0)
        self.assertEqual(lp.PATH_LNAH, 1)
        self.assertEqual(lp.PATH_LNAL, 2)
        self.assertEqual(lp.PATH_LNAW, 3)
        self.assertEqual(lp.PATH_TX1, 1)
        self.assertEqual(lp.PATH_TX2, 2)
        self.assertEqual(lp.PATH_AUTO, 255)

    def test_data_format(self):
        # LimeSuite.h:1101-1103
        self.assertEqual(lp.LMS_FMT_F32, 0)
        self.assertEqual(lp.LMS_FMT_I16, 1)
        self.assertEqual(lp.LMS_FMT_I12, 2)
        self.assertEqual(lp.SAMPLE_BITS, 12)

    def test_channel_constants(self):
        # LimeSuite.h:128-129
        self.assertTrue(lp.LMS_CH_TX)
        self.assertFalse(lp.LMS_CH_RX)
        self.assertEqual(lp.LMS_SUCCESS, 0)  # LimeSuite.h:64

    def test_oversample_values(self):
        # LimeSuite.h:197,604 合法过采样比
        self.assertIn(0, lp.SUPPORTED_OVERSAMPLE)
        self.assertIn(1, lp.SUPPORTED_OVERSAMPLE)
        self.assertIn(32, lp.SUPPORTED_OVERSAMPLE)


class TestBackendNoDeviceNoCrash(unittest.TestCase):
    """无 libLimeSuite / 无设备时后端必须优雅返回 False/None，不崩溃、不造假。"""

    def test_connect_returns_false(self):
        b = lp.LimeSDRBackend()
        # 本机没装 libLimeSuite，connect 必须返回 False
        self.assertFalse(b.connect())
        self.assertFalse(b.connected)

    def test_setters_no_device_return_false(self):
        b = lp.LimeSDRBackend()
        self.assertFalse(b.set_frequency(100_000_000))
        self.assertFalse(b.set_sample_rate(5_000_000))
        self.assertFalse(b.set_combined_gain(40))
        self.assertFalse(b.set_gain(lna_db=20, tia_db=9, pga_db=15))
        self.assertFalse(b.set_antenna("LNAL"))
        self.assertFalse(b.start_rx())
        self.assertIsNone(b.read_samples(1024))

    def test_status_dict_ok(self):
        b = lp.LimeSDRBackend()
        s = b.status_dict()
        self.assertIn("connected", s)
        self.assertIn("has_libLimeSuite", s)
        self.assertFalse(s["connected"])


class TestSdrBackendIntegration(unittest.TestCase):
    """sdr_backend.enumerate_all_sdr_devices 必须列出 LimeSDR。"""

    def test_enumerate_includes_limesdr(self):
        from mbdsdr_ai.sdr_backend import enumerate_all_sdr_devices
        devs = enumerate_all_sdr_devices()
        limesdrs = [d for d in devs if d.get("driver") == "limesdr"]
        self.assertEqual(len(limesdrs), 1)
        d = limesdrs[0]
        self.assertEqual(d["freq_range"], (1e5, 3.8e9))
        self.assertEqual(d["sample_rate_range"], (1e5, 61.44e6))
        self.assertEqual(d["gain_range"], (0.0, 73.0))
        self.assertEqual(d["lna_gain_range"], (0.0, 30.0))
        self.assertEqual(d["tia_gain_range"], (0.0, 12.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
