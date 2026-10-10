# SPDX-License-Identifier: MIT
"""LRPT QPSK 解调 + 帧同步确定性互测（干净室，第①步）。

镜像 test_ft8_modem.py 风格：固定 seed round-trip + 频偏/时偏/噪声容忍 +
纯噪声 0 虚警诚实空态。无 ctypes / C 扩展依赖。
"""
from __future__ import annotations

import numpy as np
import pytest

from mbdsdr_ai.lrpt_modem import (
    FS_HZ,
    LRPT_SYNC_WORD,
    SPS,
    SYNC_BITS,
    SYNC_SYMBOLS,
    LrptDemodulator,
    LrptModulator,
    iq_to_interleaved,
)

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def mod() -> LrptModulator:
    return LrptModulator()


@pytest.fixture
def demod() -> LrptDemodulator:
    return LrptDemodulator(max_hamming=4)


@pytest.fixture
def frame_bits(mod: LrptModulator) -> np.ndarray:
    return mod.build_frame_bits()


# --------------------------------------------------------------------------- #
# 基础结构
# --------------------------------------------------------------------------- #

class TestFrameStructure:
    def test_sync_word_constant(self):
        """同步字 = SatDump module_meteor_lrpt_decoder.cpp:201 非差分支。"""
        assert LRPT_SYNC_WORD == 0xFCA2B63DB00D9794

    def test_sync_bits_count(self):
        assert SYNC_BITS == 64
        assert SYNC_SYMBOLS == 32  # QPSK 2 bit/symbol

    def test_frame_bits_length(self, frame_bits: np.ndarray):
        # 64 sync + 2048 payload = 2112
        assert frame_bits.size == 64 + 2048

    def test_modulate_output_shape(self, mod: LrptModulator, frame_bits: np.ndarray):
        iq = mod.modulate(frame_bits)
        # 符号数 = bits/2 = 1056，每符号 sps=8 样本
        expected_samples = (frame_bits.size // 2) * SPS
        assert iq.size == expected_samples
        assert iq.dtype == np.complex64

    def test_iq_to_interleaved(self, mod: LrptModulator, frame_bits: np.ndarray):
        iq = mod.modulate(frame_bits)
        inter = iq_to_interleaved(iq)
        assert inter.dtype == np.float32
        assert inter.size == 2 * iq.size
        # [I0,Q0,I1,Q1,...]
        np.testing.assert_array_equal(inter[0::2], iq.real.astype(np.float32))
        np.testing.assert_array_equal(inter[1::2], iq.imag.astype(np.float32))


# --------------------------------------------------------------------------- #
# Round-trip 闭环
# --------------------------------------------------------------------------- #

class TestRoundTrip:
    def test_noiseless_roundtrip(self, mod: LrptModulator, demod: LrptDemodulator,
                                 frame_bits: np.ndarray):
        """无噪合成 → 解调 → 帧同步字位置/内容匹配。"""
        iq = mod.modulate(frame_bits)
        frames = demod.process(iq)
        assert len(frames) >= 1, "无噪应至少找到一个帧同步"
        best = frames[0]
        assert best.hamming == 0, f"无噪汉明距应=0，实际={best.hamming}"
        assert best.sync_symbol_index == 0, "帧应在符号 0 处"
        # 同步字后应有 payload 字节
        assert len(best.frame_bytes) > 0

    def test_deterministic_same_seed(self, mod: LrptModulator, frame_bits: np.ndarray):
        """同一 seed 两次合成逐样本一致。"""
        iq1 = mod.modulate(frame_bits, awgn_snr_db=10.0, noise_seed=42)
        iq2 = mod.modulate(frame_bits, awgn_snr_db=10.0, noise_seed=42)
        np.testing.assert_array_equal(iq1, iq2)

    def test_different_seed_different_noise(self, mod: LrptModulator,
                                           frame_bits: np.ndarray):
        """不同 seed 噪声不同。"""
        iq1 = mod.modulate(frame_bits, awgn_snr_db=10.0, noise_seed=1)
        iq2 = mod.modulate(frame_bits, awgn_snr_db=10.0, noise_seed=2)
        assert not np.array_equal(iq1, iq2)


# --------------------------------------------------------------------------- #
# 频偏容忍
# --------------------------------------------------------------------------- #

class TestFrequencyOffset:
    @pytest.mark.parametrize("foff", [-50000, -20000, -10000, -5000, 0,
                                       5000, 10000, 20000, 50000])
    def test_cfo_acquisition(self, mod: LrptModulator, demod: LrptDemodulator,
                             frame_bits: np.ndarray, foff: int):
        """±50 kHz 频偏内 4 次方法 CFO 估计可捕获。"""
        iq = mod.modulate(frame_bits, freq_offset_hz=float(foff))
        frames = demod.process(iq)
        assert len(frames) >= 1, f"foff={foff} Hz 未捕获"
        assert frames[0].hamming <= 4, f"foff={foff} hamming={frames[0].hamming}"


# --------------------------------------------------------------------------- #
# 时偏容忍
# --------------------------------------------------------------------------- #

class TestTimeOffset:
    @pytest.mark.parametrize("toff", [0, 8, 16, 32, 64, 128, 256])
    def test_time_offset_corrected(self, mod: LrptModulator, demod: LrptDemodulator,
                                   frame_bits: np.ndarray, toff: int):
        """帧前补零时偏，同步位置应对应到符号索引。"""
        iq = mod.modulate(frame_bits, time_offset_samples=toff)
        frames = demod.process(iq)
        assert len(frames) >= 1, f"toff={toff} 样本未同步"
        # 帧应在 toff/sps 符号附近
        expected_sym = toff // SPS
        assert abs(frames[0].sync_symbol_index - expected_sym) <= 1, \
            f"toff={toff}: 期望符号≈{expected_sym}, 实际={frames[0].sync_symbol_index}"


# --------------------------------------------------------------------------- #
# 相位偏移
# --------------------------------------------------------------------------- #

class TestPhaseOffset:
    @pytest.mark.parametrize("phase_deg", [0, 30, 90, 180, 270])
    def test_phase_rotation(self, mod: LrptModulator, demod: LrptDemodulator,
                            frame_bits: np.ndarray, phase_deg: int):
        """QPSK 4 相位模糊由 8 假设同步相关覆盖。"""
        phase_rad = phase_deg * np.pi / 180.0
        iq = mod.modulate(frame_bits, phase_offset_rad=phase_rad)
        frames = demod.process(iq)
        assert len(frames) >= 1, f"phase={phase_deg}° 未同步"
        assert frames[0].hamming <= 4


# --------------------------------------------------------------------------- #
# 噪声容忍
# --------------------------------------------------------------------------- #

class TestNoiseTolerance:
    @pytest.mark.parametrize("snr_db", [20, 15, 10, 5])
    def test_snr_above_floor(self, mod: LrptModulator, demod: LrptDemodulator,
                             frame_bits: np.ndarray, snr_db: int):
        """SNR ≥ 5 dB 内可同步（max_hamming=4）。"""
        iq = mod.modulate(frame_bits, awgn_snr_db=float(snr_db), noise_seed=42)
        frames = demod.process(iq)
        assert len(frames) >= 1, f"SNR={snr_db} dB 未同步"
        assert frames[0].hamming <= 4


# --------------------------------------------------------------------------- #
# 纯噪声 0 虚警（诚实空态）
# --------------------------------------------------------------------------- #

class TestFalseAlarm:
    def test_pure_noise_zero_false_alarm(self, demod: LrptDemodulator):
        """纯噪声 → frames=[]（诚实空态，不伪造同步）。"""
        rng = np.random.default_rng(12345)
        for trial in range(5):
            noise = (rng.standard_normal(8448) + 1j * rng.standard_normal(8448)) \
                .astype(np.complex64)
            frames = demod.process(noise)
            assert frames == [], \
                f"trial {trial}: 纯噪声出现 {len(frames)} 个虚警帧（应空态）"

    def test_empty_input(self, demod: LrptDemodulator):
        """空输入 → 空列表（诚实空态）。"""
        assert demod.process(np.empty(0, dtype=np.complex64)) == []

    def test_too_short_input(self, demod: LrptDemodulator):
        """输入过短（不足一个同步字）→ 空列表。"""
        short = np.ones(SPS * SYNC_SYMBOLS // 2, dtype=np.complex64)
        assert demod.process(short) == []
