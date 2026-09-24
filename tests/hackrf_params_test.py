#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hackrf_params_test.py — 验证 hackrf_params.py 移植自 libhackrf/max2837 的真实参数。

红线：增益表必须是 libhackrf C 源码 + max2837 固件驱动里的真实离散档，不能编造。
本测试直接断言源码表的条数与端点（见每个断言的注释来源 file:line）。

真实来源（repos/hackrf 下）：
  - LNA(RX IF)  host/libhackrf/src/hackrf.c:2027,2031 ; firmware/common/max2837.c:344-371
  - VGA(RX BB)  host/libhackrf/src/hackrf.c:2054,2058 ; firmware/common/max2837.c:373-381
  - TXVGA       host/libhackrf/src/hackrf.c:2081      ; firmware/common/max2837.c:383-395
  - 频率        host/libhackrf/src/hackrf.h:235,662,670
  - 采样率      host/libhackrf/src/hackrf.h:247,1794,1813
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mbdsdr_ai import hackrf_params as hp


class TestGainTables(unittest.TestCase):
    """增益表条数与端点 —— 来源 hackrf.c / max2837.c。"""

    def test_lna_table(self):
        p = hp.HackRFParams()
        levels = p.lna_gain_levels_db()
        # max2837.c:344-371 switch(case 40/32/24/16/8/0) → 6 档
        self.assertEqual(len(levels), 6)
        self.assertEqual(levels, [0, 8, 16, 24, 32, 40])
        # hackrf.c:2031 value &= ~0x07 → 步长 8
        self.assertEqual(p.lna_step_db, 8)
        self.assertEqual(levels, sorted(levels))

    def test_vga_table(self):
        p = hp.HackRFParams()
        levels = p.vga_gain_levels_db()
        # hackrf.c:2054 (>62 非法), :2058 (value &= ~0x01) → 0,2,...,62 共 32 档
        self.assertEqual(len(levels), 32)
        self.assertEqual(levels[0], 0)
        self.assertEqual(levels[-1], 62)
        self.assertTrue(all(g % 2 == 0 for g in levels))  # 全偶数
        self.assertEqual(p.vga_step_db, 2)

    def test_txvga_table(self):
        p = hp.HackRFParams()
        levels = p.txvga_gain_levels_db()
        # hackrf.c:2081 (>47 非法，无掩码) → 0..47 共 48 档，1dB 步进
        self.assertEqual(len(levels), 48)
        self.assertEqual(levels[0], 0)
        self.assertEqual(levels[-1], 47)
        self.assertEqual(p.txvga_step_db, 1)

    def test_clamp_matches_c_masking(self):
        """clamp 函数必须与 C 侧 value &= ~0x07 / ~0x01 语义一致。"""
        p = hp.HackRFParams()
        # hackrf.c:2031 LNA 向下对齐到 8 的倍数
        self.assertEqual(p.clamp_lna_gain(35), 32)
        self.assertEqual(p.clamp_lna_gain(40), 40)
        self.assertEqual(p.clamp_lna_gain(45), 40)   # >40 钳到 40
        # hackrf.c:2058 VGA 向下对齐到偶数
        self.assertEqual(p.clamp_vga_gain(3), 2)
        self.assertEqual(p.clamp_vga_gain(62), 62)
        self.assertEqual(p.clamp_vga_gain(99), 62)
        # hackrf.c:2081 TXVGA 0-47
        self.assertEqual(p.clamp_txvga_gain(0), 0)
        self.assertEqual(p.clamp_txvga_gain(47), 47)
        self.assertEqual(p.clamp_txvga_gain(60), 47)


class TestFrequencyRange(unittest.TestCase):
    def test_freq_range(self):
        p = hp.HackRFParams()
        # hackrf.h:235,662,670 → 1 MHz - 6000 MHz
        self.assertEqual(p.min_freq_hz, 1_000_000)
        self.assertEqual(p.max_freq_hz, 6_000_000_000)
        self.assertTrue(p.is_valid_frequency(1_000_000))
        self.assertTrue(p.is_valid_frequency(6_000_000_000))
        self.assertTrue(p.is_valid_frequency(145_000_000))  # 2m 段
        self.assertFalse(p.is_valid_frequency(500_000))       # < 1 MHz
        self.assertFalse(p.is_valid_frequency(7_000_000_000))  # > 6 GHz


class TestSampleRate(unittest.TestCase):
    def test_sample_rate_range(self):
        p = hp.HackRFParams()
        # hackrf.h:247,1794 → 2-20 MHz；默认 10 MHz (hackrf.h:1813)
        self.assertEqual(p.min_sr_hz, 2_000_000)
        self.assertEqual(p.max_sr_hz, 20_000_000)
        self.assertEqual(p.default_sr_hz, 10_000_000)
        self.assertTrue(p.is_valid_sample_rate(2_000_000))
        self.assertTrue(p.is_valid_sample_rate(20_000_000))
        self.assertTrue(p.is_valid_sample_rate(10_000_000))
        self.assertFalse(p.is_valid_sample_rate(1_000_000))
        self.assertFalse(p.is_valid_sample_rate(25_000_000))

    def test_recommended_rates_all_valid(self):
        for r in hp.SUPPORTED_SAMPLE_RATES:
            self.assertTrue(hp.DEFAULT_PARAMS.is_valid_sample_rate(r), f"{r} 应合法")


class TestConstantsMatchSource(unittest.TestCase):
    """关键常量与 C 源码写死值一致（hackrf.h:511,517,255; hackrf.c:202,204）。"""

    def test_block_constants(self):
        self.assertEqual(hp.SAMPLES_PER_BLOCK, 8192)   # hackrf.h:511
        self.assertEqual(hp.BYTES_PER_BLOCK, 16384)    # hackrf.h:517
        self.assertEqual(hp.SAMPLE_BITS, 8)            # hackrf.h:350,965
        self.assertEqual(hp.SAMPLE_FORMAT, "sc8")

    def test_usb_ids(self):
        self.assertEqual(hp.USB_VID, 0x1d50)           # hackrf.c:202
        self.assertEqual(hp.USB_PID_HACKRF_ONE, 0x6089)  # hackrf.c:204

    def test_bias_tee(self):
        self.assertEqual(hp.BIAS_TEE_VOLTAGE_MV, 3300)   # hackrf.h:255,1888
        self.assertEqual(hp.BIAS_TEE_MAX_CURRENT_MA, 50)


class TestBackendNoDeviceNoCrash(unittest.TestCase):
    """无 libhackrf / 无设备时后端必须优雅返回 False/None，不崩溃、不造假。"""

    def test_connect_returns_false(self):
        b = hp.HackRFBackend()
        # 本机没装 libhackrf，connect 必须返回 False
        self.assertFalse(b.connect())
        self.assertFalse(b.connected)

    def test_setters_no_device_return_false(self):
        b = hp.HackRFBackend()
        self.assertFalse(b.set_frequency(100_000_000))
        self.assertFalse(b.set_sample_rate(8_000_000))
        self.assertFalse(b.set_lna_gain(24))
        self.assertFalse(b.set_vga_gain(30))
        self.assertFalse(b.set_txvga_gain(20))
        self.assertFalse(b.set_bias_tee(True))
        self.assertFalse(b.start_rx())
        self.assertIsNone(b.read_samples(1024))

    def test_status_dict_ok(self):
        b = hp.HackRFBackend()
        s = b.status_dict()
        self.assertIn("connected", s)
        self.assertIn("has_libhackrf", s)


class TestSdrBackendIntegration(unittest.TestCase):
    """sdr_backend.HackRFBackend 与 enumerate 必须用真实参数表。"""

    def test_backend_uses_real_ranges(self):
        from mbdsdr_ai.sdr_backend import HackRFBackend
        b = HackRFBackend(0)
        self.assertEqual(b.device.frequency_range,
                         (hp.HACKRF_MIN_FREQ_HZ, hp.HACKRF_MAX_FREQ_HZ))
        self.assertEqual(b.device.sample_rate_range,
                         (hp.HACKRF_MIN_SAMPLE_RATE_HZ, hp.HACKRF_MAX_SAMPLE_RATE_HZ))

    def test_enumerate_includes_hackrf(self):
        from mbdsdr_ai.sdr_backend import enumerate_all_sdr_devices
        devs = enumerate_all_sdr_devices()
        hackrfs = [d for d in devs if d.get("driver") == "hackrf"]
        self.assertEqual(len(hackrfs), 1)
        d = hackrfs[0]
        self.assertEqual(d["freq_range"], (1e6, 6e9))
        self.assertEqual(d["sample_rate_range"], (2e6, 20e6))
        self.assertEqual(d["vga_gain_range"], (0.0, 62.0))
        self.assertEqual(d["txvga_gain_range"], (0.0, 47.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
