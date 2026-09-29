# SPDX-License-Identifier: MIT
"""HD Radio (NRSC-5) OFDM 解调 + 帧解析 + HDC 骨架 往返验证（合成向量，非硬件）。

依据公开 NRSC-5 规范逐字段验证 mbdsdr_ai/nrsc5_lite.py。
theori-io/nrsc5 (https://github.com/theori-io/nrsc5) 仅作技术参考与致谢，本仓未包含其源代码。
"""

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai import nrsc5_lite as N  # noqa: E402


class TestConstants(unittest.TestCase):
    """参数验证：必须与 nrsc5 defines.h / include/nrsc5.h 完全一致。"""

    def test_fft_cp_match_defines(self):
        self.assertEqual(N.FFT_FM, 2048)          #
        self.assertEqual(N.CP_FM, 112)             #
        self.assertEqual(N.FFTCP_FM, 2160)        #
        self.assertEqual(N.BLKSZ, 32)              #

    def test_subcarrier_layout(self):
        self.assertEqual(N.LB_START, 478)          #
        self.assertEqual(N.UB_END, 1570)           # +546
        self.assertEqual(N.PARTITION_WIDTH_FM, 19)   #
        self.assertEqual(N.PARTITION_DATA_CARRIERS, 18)  #
        self.assertEqual(N.PM_PARTITIONS, 10)      #

    def test_sample_rates(self):
        self.assertEqual(N.NRSC5_SAMPLE_RATE_NATIVE_FM, 744187.5)  #
        self.assertEqual(N.NRSC5_SAMPLE_RATE_AUDIO, 44100.0)      #

    def test_conv_code(self):
        self.assertEqual(N.CONV_K7_GEN, (0o133, 0o171, 0o165))  #

    def test_ofdm_geometry(self):
        ofdm = N.HDRadioOFDM()
        # 每边带 10 partition * 18 数据子载波 * 2 边带 = 360
        self.assertEqual(len(ofdm.data_idx), 360)
        # 每边带 11 个参考边界 * 2 = 22（去重后 20：DC 附近不重叠）
        self.assertGreaterEqual(len(ofdm.ref_idx), 20)
        self.assertEqual(ofdm.bits_per_symbol, 2 * 360)


class TestQPSKRoundtrip(unittest.TestCase):
    """QPSK 星座往返。"""

    def test_qpsk_modem(self):
        rng = np.random.default_rng(0)
        bits = rng.integers(0, 2, 400, dtype=np.uint8)
        sym = N.qpsk_mod(bits)
        rec = N.qpsk_demod(sym)
        np.testing.assert_array_equal(rec, bits)


class TestOFDMNoiseRoundtrip(unittest.TestCase):
    """OFDM 往返 @15dB SNR：BER < 1%。"""

    def test_ber_under_one_percent(self):
        rng = np.random.default_rng(123)
        ofdm = N.HDRadioOFDM()
        n_sym = 32
        bits = rng.integers(0, 2, ofdm.bits_per_symbol * n_sym, dtype=np.uint8)
        tx = ofdm.modulate(bits, n_sym)
        # 加 15dB AWGN
        sig_p = np.mean(np.abs(tx) ** 2)
        noise_p = sig_p / (10 ** (15 / 10))
        noise = ((rng.standard_normal(len(tx)) +
                  1j * rng.standard_normal(len(tx))) *
                 np.sqrt(noise_p / 2))
        rx = tx + noise
        res = N.hdradio_ofdm_demod(rx)
        n = min(len(res["bits"]), len(bits))
        ber = np.mean(res["bits"][:n] != bits[:n])
        self.assertLess(ber, 0.01, f"15dB 下 BER={ber:.4f} 应 < 1%")


class TestSyncWithOffsetAndCFO(unittest.TestCase):
    """同步：带定时偏移 + 小频偏 → 同步检测并恢复比特。"""

    def test_sync_recovers_shifted_signal(self):
        rng = np.random.default_rng(9)
        ofdm = N.HDRadioOFDM()
        n_sym = 34
        bits = rng.integers(0, 2, ofdm.bits_per_symbol * n_sym, dtype=np.uint8)
        tx = ofdm.modulate(bits, n_sym)
        # 小频偏（归一化，对应接收端粗 CFO 估计后的残余）
        cfo = 0.00002
        t = np.arange(len(tx))
        tx = tx * np.exp(1j * 2 * np.pi * cfo * t)
        # 定时偏移 30 采样
        shift = 30
        rx = np.concatenate([np.zeros(shift, dtype=complex), tx])
        # 轻度噪声
        sig_p = np.mean(np.abs(rx) ** 2)
        noise = (rng.standard_normal(len(rx)) +
                 1j * rng.standard_normal(len(rx))) * np.sqrt(sig_p / 100)
        rx = rx + noise
        res = N.hdradio_ofdm_demod(rx)
        n = min(len(res["bits"]), len(bits))
        ber = np.mean(res["bits"][:n] != bits[:n])
        self.assertLess(ber, 0.01, f"带偏移+CFO 恢复 BER={ber:.4f} 应 < 1%")

    def test_coarse_sync_finds_peak(self):
        rng = np.random.default_rng(5)
        ofdm = N.HDRadioOFDM()
        bits = rng.integers(0, 2, ofdm.bits_per_symbol * 34, dtype=np.uint8)
        tx = ofdm.modulate(bits, 34)
        # 无前缀，粗同步应在 0 附近（循环意义上，落在 CP 区间内）
        off = ofdm.coarse_sync(tx)
        # 循环距离到 0
        circ = min(off, ofdm.fftcp - off)
        self.assertLess(circ, ofdm.cp + 4,
                        f"无偏移粗同步应落在 CP 区间，得到 off={off}")


class TestFramePSD(unittest.TestCase):
    """HDLC/PSD 帧解析。"""

    def test_psd_roundtrip(self):
        fr = N.HDRadioFrame()
        title = "MBDSDR-FM HD Radio"
        blob = fr.build_psd_frame(title)
        psd = fr.extract_psd(blob)
        self.assertEqual(len(psd), 1)
        self.assertEqual(psd[0].title, title)

    def test_bad_fcs_rejected(self):
        fr = N.HDRadioFrame()
        blob = bytearray(fr.build_psd_frame("HELLO"))
        blob[-3] ^= 0xFF  # 破坏 FCS 区域
        psd = fr.extract_psd(bytes(blob))
        self.assertEqual(len(psd), 0, "FCS 损坏的帧应被丢弃")

    def test_hdlc_unescape(self):
        # 0x7D 0x5F 应还原为 0x7F（0x5F ^ 0x20 = 0x7F）
        self.assertEqual(N.hdlc_unescape(bytes([0x7D, 0x5F])), bytes([0x7F]))


class TestPCI(unittest.TestCase):
    """24bit PCI 模糊匹配。"""

    def test_pci_exact(self):
        fr = N.HDRadioFrame()
        self.assertEqual(fr.fuzzy_pci(N.PCI_AUDIO), N.PCI_AUDIO)

    def test_pci_within_4_errors(self):
        fr = N.HDRadioFrame()
        # 翻转 3 个 bit 仍应命中 PCI_AUDIO
        noisy = N.PCI_AUDIO ^ 0x12345 & 0x7
        self.assertEqual(fr.fuzzy_pci(noisy), N.PCI_AUDIO)


class TestHDCSkeleton(unittest.TestCase):
    """HDC 骨架：frame header 参数提取。"""

    def test_parse_header(self):
        h = N.HDCDecoder()
        # buf[8]=0x13 → codec_mode=3, stream_id=1
        buf = bytes([0] * 8) + bytes([0x13]) + bytes([0] * 5)
        p = h.parse_header(buf)
        self.assertEqual(p.codec_mode, 3)
        self.assertEqual(p.stream_id, 1)
        self.assertEqual(p.sample_rate, 44100.0)

    def test_identify_audio(self):
        h = N.HDCDecoder()
        self.assertTrue(h.identify_audio_stream(N.PCI_AUDIO))
        self.assertFalse(h.identify_audio_stream(0x000000))


class TestTopLevelHelpers(unittest.TestCase):
    """ToolRegistry 注册的三个入口。"""

    def test_hdradio_decode_iq(self):
        rng = np.random.default_rng(3)
        ofdm = N.HDRadioOFDM()
        bits = rng.integers(0, 2, ofdm.bits_per_symbol * 8, dtype=np.uint8)
        tx = ofdm.modulate(bits, 8)
        out = N.hdradio_decode_iq(tx)
        self.assertGreater(out["n_bits"], 0)
        self.assertEqual(out["fft_size"], 2048)


if __name__ == "__main__":
    unittest.main(verbosity=2)
