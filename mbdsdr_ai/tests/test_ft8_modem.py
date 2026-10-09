# SPDX-License-Identifier: MIT
"""FT8 第①步：8-FSK 调制器 + Costas 粗同步器确定性互测（干净室，仅公开协议）。

覆盖（机制证据见 docs/learn/phase63/ft8-mechanism-study.md）：
  - 调制器输出形状/类型/确定性（同 seed 逐样本一致）；
  - 无噪零偏 round-trip：Costas 检出精确 79 符号（与注入逐符号相等）；
  - ±50 Hz 频偏注入 → 估计误差 < 1.6 Hz（3.125 Hz bin 半宽）；
  - 时偏注入 → 检出正确符号（帧内位置对齐）；
  - AWGN 0 dB / 10 dB（固定 seed）→ 检出率达标（诚实记录）；
  - 纯噪声 → synced=False 空态（无假检测）；
  - 格雷映射 tone↔码字 round-trip；
  - S7 块位置检测（Costas 序列落在符号 0/36/72）；
  - encode_message() 第②步占位 → NotImplementedError。

断言用 numpy.testing / pytest.approx，禁止静默容差糊弄。
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from mbdsdr_ai.ft8_modem import (  # noqa: E402
    FS_HZ,
    NSPS,
    TONE_SPACING_HZ,
    N_TONES,
    N_SYMBOLS,
    N_DATA_SYMBOLS,
    GRAY_MAP,
    COSTAS_SEQ,
    WINDOW_SEC,
    Ft8Modulator,
    Ft8CostasSync,
    gray_to_tone,
    tone_to_gray,
    iq_to_interleaved,
)

CAPTURE = int(WINDOW_SEC * FS_HZ)     # 15 s 窗 = 180000 样本
FRAME_SAMPLES = N_SYMBOLS * NSPS      # 79*1920 = 151680
MOD = Ft8Modulator()
SYNC = Ft8CostasSync()


def _capture(iq, at=0):
    """把一段帧 IQ 放进 15 s 捕获窗（后部补零，模拟真实 T/R 槽）。"""
    buf = np.zeros(CAPTURE, dtype=np.complex64)
    buf[at:at + iq.size] = iq
    return buf


# --------------------------------------------------------------------------- #
# 协议常量自洽
# --------------------------------------------------------------------------- #
def test_protocol_constants():
    assert FS_HZ == 12_000
    assert NSPS == 1_920
    assert NSPS / FS_HZ == pytest.approx(0.16)
    assert TONE_SPACING_HZ == pytest.approx(6.25)
    assert N_TONES == 8
    assert N_SYMBOLS == 79
    assert N_DATA_SYMBOLS == 58
    assert list(GRAY_MAP) == [0, 1, 3, 2, 5, 6, 4, 7]
    assert list(COSTAS_SEQ) == [3, 1, 4, 0, 6, 5, 2]


def test_frame_layout_s7d29s7d29s7():
    itone = MOD.build_frame_symbols()
    assert itone.size == N_SYMBOLS
    # 三段 Costas 块在符号 0-6 / 36-42 / 72-78
    np.testing.assert_array_equal(itone[0:7], COSTAS_SEQ)
    np.testing.assert_array_equal(itone[36:43], COSTAS_SEQ)
    np.testing.assert_array_equal(itone[72:79], COSTAS_SEQ)
    # 两段数据各 29 符号
    assert itone[7:36].size == 29
    assert itone[43:72].size == 29


# --------------------------------------------------------------------------- #
# 调制器输出与确定性
# --------------------------------------------------------------------------- #
def test_modulator_output_shape_dtype():
    itone = MOD.build_frame_symbols()
    iq = MOD.modulate(itone)
    assert iq.dtype == np.complex64
    assert iq.size == FRAME_SAMPLES
    # 单位幅度连续相位 8-FSK：|iq| ≈ 1
    assert float(np.mean(np.abs(iq))) == pytest.approx(1.0, abs=1e-3)


def test_modulator_deterministic():
    itone = MOD.build_frame_symbols()
    a = MOD.modulate(itone, awgn_snr_db=10.0, noise_seed=12345)
    b = MOD.modulate(itone, awgn_snr_db=10.0, noise_seed=12345)
    np.testing.assert_array_equal(a, b)


def test_modulator_placeholder_data_deterministic():
    d1 = MOD.placeholder_data_symbols(seed=20261010)
    d2 = MOD.placeholder_data_symbols(seed=20261010)
    np.testing.assert_array_equal(d1, d2)
    assert d1.size == N_DATA_SYMBOLS
    assert d1.min() >= 0 and d1.max() < N_TONES


def test_encode_message_not_implemented():
    with pytest.raises(NotImplementedError):
        MOD.encode_message("CQ CALL1 GRID")


def test_iq_to_interleaved():
    iq = np.array([1 + 2j, 3 + 4j], dtype=np.complex64)
    inter = iq_to_interleaved(iq)
    np.testing.assert_array_equal(inter, np.array([1, 2, 3, 4], dtype=np.float32))


# --------------------------------------------------------------------------- #
# 格雷映射 round-trip
# --------------------------------------------------------------------------- #
def test_gray_map_roundtrip():
    # 协议表值（genft8.f90:15）与正/逆映射自洽。
    assert list(GRAY_MAP) == [0, 1, 3, 2, 5, 6, 4, 7]
    for word in range(8):
        tone = gray_to_tone(word)
        assert 0 <= tone < 8
        assert tone_to_gray(tone) == word
    # tone 集合为 0..7 的排列
    assert sorted(GRAY_MAP) == list(range(8))


# --------------------------------------------------------------------------- #
# 同步 round-trip
# --------------------------------------------------------------------------- #
def test_sync_roundtrip_noiseless():
    itone = MOD.build_frame_symbols()
    r = SYNC.process(_capture(MOD.modulate(itone)))
    assert r.synced is True
    np.testing.assert_array_equal(r.symbols, itone)
    assert r.freq_offset_hz == pytest.approx(0.0, abs=1.6)
    assert r.time_offset_samples == 0


@pytest.mark.parametrize("fo", [50.0, -50.0])
def test_sync_freq_offset(fo):
    itone = MOD.build_frame_symbols()
    r = SYNC.process(_capture(MOD.modulate(itone, freq_offset_hz=fo)))
    assert r.synced is True
    # bin 半宽 = 3.125/2 = 1.5625 Hz
    assert abs(r.freq_offset_hz - fo) < 1.6
    np.testing.assert_array_equal(r.symbols, itone)


@pytest.mark.parametrize("toff", [0, 1920, 4800, 9600])
def test_sync_time_offset(toff):
    itone = MOD.build_frame_symbols()
    iq = MOD.modulate(itone, time_offset_samples=toff)
    # modulate 已前置 toff 个零；直接放进 15 s 窗
    buf = np.zeros(CAPTURE, dtype=np.complex64)
    buf[:iq.size] = iq
    r = SYNC.process(buf)
    assert r.synced is True
    assert r.time_offset_samples == toff
    np.testing.assert_array_equal(r.symbols, itone)


def test_s7_position_detected():
    """Costas 序列应在帧符号 0/36/72 处被检出。"""
    itone = MOD.build_frame_symbols()
    r = SYNC.process(_capture(MOD.modulate(itone)))
    assert r.synced is True
    np.testing.assert_array_equal(r.symbols[0:7], COSTAS_SEQ)
    np.testing.assert_array_equal(r.symbols[36:43], COSTAS_SEQ)
    np.testing.assert_array_equal(r.symbols[72:79], COSTAS_SEQ)


# --------------------------------------------------------------------------- #
# AWGN 检出率（固定 seed，诚实记录阈值）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("snr_db,min_rate", [(10.0, 1.0), (0.0, 1.0)])
def test_sync_awgn_detection_rate(snr_db, min_rate, trials=8):
    itone = MOD.build_frame_symbols()
    ok = 0
    for t in range(trials):
        fo = float((t - 4) * 13.0)           # 覆盖 ±52 Hz
        at = int(t * 1713 % (CAPTURE - FRAME_SAMPLES - 500))
        iq = MOD.modulate(itone, freq_offset_hz=fo, awgn_snr_db=snr_db,
                          noise_seed=7000 + t)
        buf = np.zeros(CAPTURE, dtype=np.complex64)
        buf[at:at + FRAME_SAMPLES] = iq
        r = SYNC.process(buf)
        if r.synced and np.array_equal(r.symbols, itone):
            ok += 1
    rate = ok / trials
    assert rate >= min_rate, f"SNR={snr_db}dB 检出率 {rate:.2f} < {min_rate}"


def test_sync_pure_noise_honest_empty():
    rng = np.random.default_rng(4242)
    fa = 0
    for _ in range(20):
        noise = (rng.standard_normal(CAPTURE) + 1j * rng.standard_normal(CAPTURE)) \
            / np.sqrt(2.0)
        r = SYNC.process(noise.astype(np.complex64))
        assert r.synced is False
        assert r.symbols.size == 0       # 诚实空态：不编造符号
        if r.sync_quality >= SYNC.quality_threshold:
            fa += 1
    assert fa == 0, f"纯噪声虚警 {fa}/20"
