"""WebSpectra 瀑布编/解码单测（移植自 OpenWebRX FFT 链 + ADPCM）。

上游对照：docs/learn/porting_2026_09_27.md §2
"""
import numpy as np

from mbdsdr_ai.webspectra import (
    WebSpectraEncoder,
    WebSpectraDecoder,
    SPECTRUM_MARKER,
)


def test_frame_header_marker():
    """帧首字节必须是 0x01（OpenWebRX connection.py:373）。"""
    fs = 2000.0
    t = np.arange(1024) / fs
    iq = np.exp(1j * 2 * np.pi * 100 * t)
    enc = WebSpectraEncoder(compression="none")
    res = enc.encode(iq, center_freq_hz=100e6, sample_rate_hz=fs)
    assert res.frame[0] == SPECTRUM_MARKER == 0x01


def test_roundtrip_no_compression():
    """已知 FFT → 编码 → 解码 → uint8 完全一致。"""
    fs = 2000.0
    t = np.arange(1024) / fs
    rng = np.random.default_rng(0)
    iq = np.exp(1j * 2 * np.pi * (-150) * t) + 0.01 * rng.standard_normal(1024)
    enc = WebSpectraEncoder(fft_size=1024, target_bins=256,
                            db_min=-120, db_max=-20, compression="none")
    res = enc.encode(iq, 100e6, fs)
    dec = WebSpectraDecoder.decode(res.frame)
    assert np.array_equal(dec.bins_u8, res.bins_u8)
    assert dec.center_freq_hz == 100_000_000
    assert dec.sample_rate_hz == 2000


def test_roundtrip_adpcm_error_under_1db():
    """ADPCM 压缩后 dB 误差 < 1 dB（对比量化后重建值）。"""
    fs = 2000.0
    t = np.arange(1024) / fs
    rng = np.random.default_rng(1)
    # 平滑频谱（避免削波）：多个弱音 + 噪声
    iq = np.exp(1j * 2 * np.pi * 50 * t) + 0.5 * np.exp(1j * 2 * np.pi * 300 * t)
    iq += 0.01 * rng.standard_normal(1024)
    enc = WebSpectraEncoder(fft_size=1024, target_bins=256,
                            db_min=-120, db_max=-20, compression="adpcm")
    res = enc.encode(iq, 100e6, fs)
    dec = WebSpectraDecoder.decode(res.frame)
    # 量化后的"真值" dB（从 bins_u8 重建）
    truth_db = res.bins_u8.astype(np.float32) / 255.0 * (enc.db_max - enc.db_min) + enc.db_min
    err = np.abs(dec.db - truth_db)
    # ADPCM 4-bit：均值误差 < 1 dB；瞬态过放宽到 15 dB
    assert err.mean() < 1.0, f"mean err={err.mean()}"
    assert err.max() < 15.0


def test_peak_preservation():
    """峰值保留降采样：窄带单音在 target_bins 中应有一个明显峰。"""
    fs = 8000.0
    t = np.arange(2048) / fs
    iq = np.exp(1j * 2 * np.pi * 1000 * t)
    enc = WebSpectraEncoder(fft_size=2048, target_bins=256)
    res = enc.encode(iq, 0, fs)
    # 最大 bin 应显著高于中位数
    peak = res.bins_u8.max()
    med = np.median(res.bins_u8)
    assert peak > med + 100


def test_decode_rejects_wrong_marker():
    """非 0x01 帧应抛错。"""
    import pytest
    bad = bytes([0x02]) + b"\x00" * 20
    with pytest.raises(ValueError):
        WebSpectraDecoder.decode(bad)
