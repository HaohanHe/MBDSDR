#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rtlsdr_params_test.py — 验证 rtlsdr_params.py 移植自 librtlsdr 的真实参数。

红线：增益表必须是 librtlsdr C 源码里的真实离散档，不能编造。
本测试直接断言源码表的条数与端点（见每个断言的注释来源）。

注意：rtlsdr_get_tuner_gains() 返回的是「驱动暴露的离散档」，不是把物理
量程线性铺开。因此：
  - E4000 真实暴露 14 档（librtlsdr.c:959-960），不是 53 档；
  - R820T 真实暴露 29 档（librtlsdr.c:966-969），不是 63 档。
物理量程（E4000 -1.0~42.0dB，R820T 0~49.6dB）由端点断言覆盖。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mbdsdr_ai import rtlsdr_params as rp


class TestGainTables(unittest.TestCase):
    """增益表条数与端点 —— 来源 librtlsdr src/librtlsdr.c:959-969。"""

    def test_e4000_table(self):
        gains = rp.get_gain_table("E4000")
        # librtlsdr.c:959-960 e4k_gains[] 共 14 个 0.1dB 值
        self.assertEqual(len(gains), 14)
        # 端点：-10 个 0.1dB = -1.0dB；420 个 0.1dB = 42.0dB
        self.assertAlmostEqual(gains[0], -1.0, places=2)
        self.assertAlmostEqual(gains[-1], 42.0, places=2)
        # 升序
        self.assertEqual(gains, sorted(gains))

    def test_r820t_table(self):
        gains = rp.get_gain_table("R820T")
        # librtlsdr.c:966-969 r82xx_gains[] 共 29 个 0.1dB 值
        self.assertEqual(len(gains), 29)
        # 端点：0 → 0.0dB；496 → 49.6dB
        self.assertAlmostEqual(gains[0], 0.0, places=2)
        self.assertAlmostEqual(gains[-1], 49.6, places=2)
        self.assertEqual(gains, sorted(gains))

    def test_r828d_shares_r82xx_table(self):
        # librtlsdr.c:991-993 R820T 与 R828D 共用 r82xx_gains
        self.assertEqual(rp.get_gain_table("R828D"), rp.get_gain_table("R820T"))

    def test_fc0012_table(self):
        gains = rp.get_gain_table("FC0012")
        # librtlsdr.c:961 fc0012_gains[] = {-99,-40,71,179,192} → 5 档
        self.assertEqual(len(gains), 5)
        self.assertAlmostEqual(gains[0], -9.9, places=2)
        self.assertAlmostEqual(gains[-1], 19.2, places=2)

    def test_fc0013_table(self):
        gains = rp.get_gain_table("FC0013")
        # librtlsdr.c:962-964 fc0013_gains[] 共 23 个值
        self.assertEqual(len(gains), 23)
        self.assertAlmostEqual(gains[0], -9.9, places=2)
        self.assertAlmostEqual(gains[-1], 19.7, places=2)

    def test_tenths_match_db(self):
        """0.1dB 整数表与 dB 浮点表必须一一对应。"""
        for name in rp.RTL_TUNER_PARAMS:
            tenths = rp.get_gain_table_tenths(name)
            db = rp.get_gain_table(name)
            self.assertEqual(len(tenths), len(db))
            for t, d in zip(tenths, db):
                self.assertAlmostEqual(t / 10.0, d, places=3)

    def test_unknown_tuner_empty(self):
        self.assertEqual(rp.get_gain_table("NOPE"), [])
        self.assertEqual(rp.get_frequency_range("NOPE"), (0.0, 0.0))


class TestFrequencyRanges(unittest.TestCase):
    def test_e4000_range(self):
        lo, hi = rp.get_frequency_range("E4000")
        # tuner_e4k.c:351-352 默认规格 64~1700 MHz
        self.assertAlmostEqual(lo, 64e6, delta=1e3)
        self.assertAlmostEqual(hi, 1700e6, delta=1e3)

    def test_r820t_range(self):
        lo, hi = rp.get_frequency_range("R820T")
        # 24 ~ 1766 MHz
        self.assertAlmostEqual(lo, 24e6, delta=1e3)
        self.assertAlmostEqual(hi, 1766e6, delta=1e3)

    def test_fc_ranges(self):
        for name in ("FC0012", "FC0013"):
            lo, hi = rp.get_frequency_range(name)
            self.assertAlmostEqual(lo, 22e6, delta=1e3)
            self.assertAlmostEqual(hi, 948.6e6, delta=1e3)


class TestSampleRates(unittest.TestCase):
    def test_recommended_list_nonempty(self):
        rates = rp.get_supported_sample_rates()
        self.assertGreater(len(rates), 5)
        # 全部落在合法区间
        for r in rates:
            self.assertTrue(rp.is_valid_sample_rate(r), f"{r} 应合法")

    def test_valid_rate_bounds(self):
        # librtlsdr.c:1100-1101
        self.assertTrue(rp.is_valid_sample_rate(250_000))    # 低段
        self.assertTrue(rp.is_valid_sample_rate(1_000_000))  # 高段
        self.assertTrue(rp.is_valid_sample_rate(3_200_000))   # 上限
        self.assertFalse(rp.is_valid_sample_rate(200_000))   # 太低
        self.assertFalse(rp.is_valid_sample_rate(4_000_000))   # 太高
        self.assertFalse(rp.is_valid_sample_rate(600_000))    # 死区 (300k,900k]

    def test_constants(self):
        self.assertEqual(rp.SAMPLE_RATE_MAX_HZ, 3_200_000)
        self.assertEqual(rp.DIRECT_SAMPLING_I, 1)
        self.assertEqual(rp.DIRECT_SAMPLING_Q, 2)
        self.assertEqual(rp.ASYNC_DEFAULT_BUF_NUM, 15)
        self.assertEqual(rp.ASYNC_DEFAULT_BUF_LEN, 16 * 32 * 512)


class TestNearestGain(unittest.TestCase):
    def test_snaps_to_discrete(self):
        # R820T 表含 48.0 与 49.6；请求 48.5 距 48.0=0.5、距 49.6=1.1 → 吸附 48.0
        g = rp.nearest_gain("R820T", 48.5)
        self.assertIn(g, rp.get_gain_table("R820T"))
        self.assertAlmostEqual(g, 48.0, places=1)

    def test_unknown_returns_none(self):
        self.assertIsNone(rp.nearest_gain("NOPE", 10.0))


class TestDeviceEnumerationNoCrash(unittest.TestCase):
    """无设备/无 pyrtlsdr 时枚举必须返回空列表而不是崩溃。"""

    def test_list_devices_empty_or_real(self):
        from mbdsdr_ai.sdr_backend import RTLSDRBackend
        devs = RTLSDRBackend.list_devices()
        self.assertIsInstance(devs, list)
        # 无 USB 棒、或未装 pyrtlsdr，都应返回 []
        for d in devs:
            self.assertIn("index", d)
            self.assertIn("tuner", d)


if __name__ == "__main__":
    unittest.main(verbosity=2)
