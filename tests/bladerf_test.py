#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bladerf_test.py — 验证 bladerf_params.py 移植自 libbladeRF 的真实参数。

红线：增益表必须是 libbladeRF C 源码里的真实离散档，不能编造。
本测试直接断言源码表的条数与端点（见每个断言的注释来源 file:line）。

真实来源（repos/bladeRF 下）：
  - RXVGA1 5-30dB(26档)  host/libraries/libbladeRF/include/bladeRF1.h:154,160
  - RXVGA2 0-30dB(31档)  host/libraries/libbladeRF/include/bladeRF1.h:166,172
  - TXVGA1 -35..-4dB     host/libraries/libbladeRF/include/bladeRF1.h:178,184
  - TXVGA2 0-25dB        host/libraries/libbladeRF/include/bladeRF1.h:190,196
  - LNA 三档 0/3/6 dB    host/libraries/libbladeRF/include/bladeRF1.h:203-222
  - bladeRF2 RX 70M-6G   fpga_common/include/bladerf2_common.h:550-555
  - bladeRF2 SR 520834-61.44M  fpga_common/include/bladerf2_common.h:518-523
  - bladeRF2 BW 200k-56M       fpga_common/include/bladerf2_common.h:542-547
  - ADC/DAC 12-bit       host/libraries/libbladeRF/include/libbladeRF.h:2144
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mbdsdr_ai import bladerf_params as bp


class TestGainTables(unittest.TestCase):
    """Legacy VGA 分级条数与端点 —— 来源 bladeRF1.h:154-196。"""

    def test_rxvga1_table(self):
        p = bp.BladeRFParams()
        levels = p.rxvga1_levels_db()
        # bladeRF1.h:154 MIN=5, :160 MAX=30, 1dB 步进 → 26 档
        self.assertEqual(len(levels), 26)
        self.assertEqual(levels[0], 5)
        self.assertEqual(levels[-1], 30)
        self.assertEqual(p.rxvga1_min_db, 5)
        self.assertEqual(p.rxvga1_max_db, 30)

    def test_rxvga2_table(self):
        p = bp.BladeRFParams()
        levels = p.rxvga2_levels_db()
        # bladeRF1.h:166 MIN=0, :172 MAX=30, 1dB 步进 → 31 档
        self.assertEqual(len(levels), 31)
        self.assertEqual(levels[0], 0)
        self.assertEqual(levels[-1], 30)
        self.assertEqual(p.rxvga2_min_db, 0)
        self.assertEqual(p.rxvga2_max_db, 30)

    def test_total_rx_gain_range(self):
        """RXVGA1+RXVGA2 合计 = 5-60 dB。"""
        self.assertEqual(bp.RXVGA1_GAIN_MIN_DB + bp.RXVGA2_GAIN_MIN_DB, 5)
        self.assertEqual(bp.RXVGA1_GAIN_MAX_DB + bp.RXVGA2_GAIN_MAX_DB, 60)

    def test_txvga1_table(self):
        p = bp.BladeRFParams()
        levels = p.txvga1_levels_db()
        # bladeRF1.h:178 MIN=-35, :184 MAX=-4, 1dB 步进 → 32 档
        self.assertEqual(len(levels), 32)
        self.assertEqual(levels[0], -35)
        self.assertEqual(levels[-1], -4)

    def test_txvga2_table(self):
        p = bp.BladeRFParams()
        levels = p.txvga2_levels_db()
        # bladeRF1.h:190 MIN=0, :196 MAX=25, 1dB 步进 → 26 档
        self.assertEqual(len(levels), 26)
        self.assertEqual(levels[0], 0)
        self.assertEqual(levels[-1], 25)

    def test_lna_three_levels(self):
        p = bp.BladeRFParams()
        # bladeRF1.h:203-208 enum, :215 MID=3, :222 MAX=6
        self.assertEqual(p.lna_levels_db(), [0, 3, 6])

    def test_clamp(self):
        p = bp.BladeRFParams()
        # bladeRF1.h:154,160 — RXVGA1 钳到 5-30
        self.assertEqual(p.clamp_rxvga1(0), 5)
        self.assertEqual(p.clamp_rxvga1(20), 20)
        self.assertEqual(p.clamp_rxvga1(99), 30)
        # bladeRF1.h:166,172 — RXVGA2 钳到 0-30
        self.assertEqual(p.clamp_rxvga2(-5), 0)
        self.assertEqual(p.clamp_rxvga2(15), 15)
        self.assertEqual(p.clamp_rxvga2(99), 30)
        # bladeRF1.h:178,184 — TXVGA1 钳到 -35..-4
        self.assertEqual(p.clamp_txvga1(-99), -35)
        self.assertEqual(p.clamp_txvga1(-20), -20)
        self.assertEqual(p.clamp_txvga1(0), -4)


class TestFrequencyRange(unittest.TestCase):
    def test_bladerf2_rx_freq_range(self):
        p = bp.BladeRFParams()
        # bladerf2_common.h:550-555 → RX 70 MHz - 6 GHz
        self.assertEqual(p.min_freq_hz, 70_000_000)
        self.assertEqual(p.max_freq_hz, 6_000_000_000)
        self.assertTrue(p.is_valid_frequency(70_000_000))
        self.assertTrue(p.is_valid_frequency(6_000_000_000))
        self.assertTrue(p.is_valid_frequency(1_000_000_000))
        self.assertFalse(p.is_valid_frequency(10_000_000))   # < 70 MHz
        self.assertFalse(p.is_valid_frequency(7_000_000_000))  # > 6 GHz

    def test_bladerf2_tx_freq_range(self):
        # bladerf2_common.h:557-562 → TX 47 MHz - 6 GHz
        self.assertEqual(bp.BLADERF2_MIN_TX_FREQ_HZ, 47_000_000)

    def test_bladerf1_legacy_freq_range_present(self):
        # bladeRF1.h:84,90 → 237.5 MHz - 3.8 GHz（bladeRF 1.0 常量仍在表里）
        self.assertEqual(bp.BLADERF1_MIN_FREQ_HZ, 237_500_000)
        self.assertEqual(bp.BLADERF1_MAX_FREQ_HZ, 3_800_000_000)


class TestSampleRate(unittest.TestCase):
    def test_bladerf2_sample_rate_range(self):
        p = bp.BladeRFParams()
        # bladerf2_common.h:518-523 → 520834 - 61440000 Hz
        self.assertEqual(p.min_sr_hz, 520_834)
        self.assertEqual(p.max_sr_hz, 61_440_000)
        self.assertTrue(p.is_valid_sample_rate(520_834))
        self.assertTrue(p.is_valid_sample_rate(61_440_000))
        self.assertTrue(p.is_valid_sample_rate(2_000_000))
        self.assertFalse(p.is_valid_sample_rate(100_000))     # < 520834
        self.assertFalse(p.is_valid_sample_rate(100_000_000))  # > 61.44 MHz

    def test_bladerf1_legacy_sample_rate_present(self):
        # bladeRF1.h:47,54 → 80 kSPS min, 40 MSPS rec max
        self.assertEqual(bp.BLADERF1_MIN_SAMPLERATE_HZ, 80_000)
        self.assertEqual(bp.BLADERF1_REC_MAX_SAMPLERATE_HZ, 40_000_000)


class TestBandwidth(unittest.TestCase):
    def test_bladerf2_bandwidth_range(self):
        p = bp.BladeRFParams()
        # bladerf2_common.h:542-547 → 200 kHz - 56 MHz
        self.assertEqual(p.min_bw_hz, 200_000)
        self.assertEqual(p.max_bw_hz, 56_000_000)
        self.assertTrue(p.is_valid_bandwidth(200_000))
        self.assertTrue(p.is_valid_bandwidth(56_000_000))
        self.assertFalse(p.is_valid_bandwidth(100_000))
        self.assertFalse(p.is_valid_bandwidth(100_000_000))

    def test_bladerf1_legacy_bandwidth_present(self):
        # bladeRF1.h:60,66 → 1.5 MHz - 28 MHz
        self.assertEqual(bp.BLADERF1_MIN_BANDWIDTH_HZ, 1_500_000)
        self.assertEqual(bp.BLADERF1_MAX_BANDWIDTH_HZ, 28_000_000)


class TestDataFormat(unittest.TestCase):
    def test_adc_dac_12bit(self):
        # libbladeRF.h:2144 — "12-bit Q11 intermediate format"
        self.assertEqual(bp.SAMPLE_BITS_ADC_DAC, 12)
        self.assertEqual(bp.SAMPLE_BITS_NATIVE, 16)  # 主机侧 SC16_Q11
        self.assertEqual(bp.BYTES_PER_IQ_PAIR, 4)   # I16 + Q16


class TestBackendNoDeviceNoCrash(unittest.TestCase):
    """无 libbladeRF / 无设备时后端必须优雅返回 False/None，不崩溃、不造假。"""

    def test_connect_returns_false(self):
        b = bp.BladeRFBackend()
        # 本机没装 libbladeRF，connect 必须返回 False
        self.assertFalse(b.connect())
        self.assertFalse(b.connected)

    def test_setters_no_device_return_false(self):
        b = bp.BladeRFBackend()
        self.assertFalse(b.set_frequency(100_000_000))
        self.assertFalse(b.set_sample_rate(2_000_000))
        self.assertFalse(b.set_bandwidth(5_000_000))
        self.assertFalse(b.set_gain(20))
        self.assertFalse(b.set_rxvga1(20))
        self.assertFalse(b.set_rxvga2(15))
        self.assertFalse(b.start_rx())
        self.assertIsNone(b.read_samples(1024))

    def test_status_dict_ok(self):
        b = bp.BladeRFBackend()
        s = b.status_dict()
        self.assertIn("connected", s)
        self.assertIn("has_libbladeRF", s)
        self.assertFalse(s["connected"])


class TestSdrBackendIntegration(unittest.TestCase):
    """sdr_backend.enumerate_all_sdr_devices() 必须包含 bladeRF 条目。"""

    def test_enumerate_absent_without_hardware(self):
        # 无真实 bladeRF 时枚举不得包含 bladerf 条目（不再无条件列出假设备）
        from mbdsdr_ai.sdr_backend import enumerate_all_sdr_devices
        devs = enumerate_all_sdr_devices()
        self.assertEqual(
            [d for d in devs if d.get("driver") == "bladerf"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
