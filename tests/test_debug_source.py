# SPDX-License-Identifier: MIT
"""DebugSource 确定性测试：单音频率精度、噪声统计、扫频范围、接口兼容、Debug 标记。"""
import numpy as np
import pytest

from mbdsdr_ai.debug_source import DebugSource, VALID_SIGNAL_TYPES


def _new(fs=48000.0):
    src = DebugSource(sample_rate=fs, seed=42)
    src.connect()
    return src


def test_status_marks_debug_source():
    src = _new()
    st = src.get_status()
    assert st["connected"] is True
    # 红线：device 恒含 Debug
    assert "Debug" in st["device"]
    assert st["is_debug_source"] is True
    assert "调试" in st["device_label"] or "Debug" in st["device_label"]


def test_tone_frequency_precision():
    fs = 48000.0
    src = _new(fs)
    src.set_signal_type("tone")
    src.set_tone_frequency(1000.0)
    src.set_amplitude(0.5)
    blk = src.read_samples(4096)
    assert blk.dtype == np.complex64
    assert blk.shape == (4096,)
    # 幅度正确（复单音 |z|=amp）
    assert np.allclose(np.abs(blk), 0.5, atol=1e-6)
    # FFT 峰值应落在 1000Hz bin（复数信号用 fft + 频率搬移）
    spec = np.fft.fftshift(np.fft.fft(blk * np.hanning(len(blk))))
    freqs = np.fft.fftshift(np.fft.fftfreq(len(blk), 1 / fs))
    peak = freqs[np.argmax(np.abs(spec))]
    assert peak == pytest.approx(1000.0, abs=fs / len(blk))


def test_noise_statistics():
    fs = 48000.0
    src = _new(fs)
    src.set_signal_type("noise")
    src.set_amplitude(1.0)   # amp=1 → E|z|^2 = 1
    blk = src.read_samples(48000)  # 1 秒
    power = np.mean(np.abs(blk) ** 2)
    assert power == pytest.approx(1.0, abs=0.05)
    # I/Q 均值≈0
    assert np.abs(np.mean(blk.real)) < 0.02
    assert np.abs(np.mean(blk.imag)) < 0.02


def test_noise_deterministic_with_seed():
    a = DebugSource(sample_rate=48000, seed=7)
    a.connect(); a.set_signal_type("noise"); a.set_amplitude(0.5)
    b = DebugSource(sample_rate=48000, seed=7)
    b.connect(); b.set_signal_type("noise"); b.set_amplitude(0.5)
    assert np.allclose(a.read_samples(1000), b.read_samples(1000))


def test_sweep_range():
    fs = 48000.0
    src = _new(fs)
    src.set_signal_type("sweep")
    src.set_sweep(-5000.0, 5000.0, rate_hz_s=10000.0)
    src.set_amplitude(0.5)
    # 读 2 秒
    blk = src.read_samples(2 * 48000)
    # 瞬时频率应从 -5k 扫到 +15k（f0+rate*t, t=2s → -5k+20k=15k）
    # 用 STFT 粗查：起始段峰值≈-5k，末尾段峰值≈+15k
    seg0 = blk[:4096]
    seg1 = blk[-4096:]
    f0 = np.fft.fftshift(np.fft.fftfreq(4096, 1 / fs))
    p0 = f0[np.argmax(np.abs(np.fft.fftshift(np.fft.fft(seg0 * np.hanning(4096)))))]
    p1 = f0[np.argmax(np.abs(np.fft.fftshift(np.fft.fft(seg1 * np.hanning(4096)))))]
    # 首段覆盖 t=0..0.085s，频率在 [-5000,-4146] 间扫，峰值≈中段
    assert -5000.0 <= p0 <= -4000.0
    assert p1 == pytest.approx(15000.0, abs=600.0)


def test_am_fm_signals():
    fs = 48000.0
    src = _new(fs)
    src.set_modulation(1000.0, fm_deviation_hz=3000.0)
    src.set_tone_frequency(5000.0)
    src.set_amplitude(0.5)
    # AM
    src.set_signal_type("am")
    am = src.read_samples(8192)
    assert am.shape == (8192,)
    # 包络应在 0.25..0.75 之间（1+0.5cos，乘 0.5）
    env = np.abs(am)
    assert env.min() == pytest.approx(0.25, abs=0.05)
    assert env.max() == pytest.approx(0.75, abs=0.05)
    # FM
    src.set_signal_type("fm")
    fm = src.read_samples(8192)
    assert fm.shape == (8192,)
    assert np.allclose(np.abs(fm), 0.5, atol=1e-6)  # 恒包络


def test_invalid_signal_type_rejected():
    src = _new()
    assert src.set_signal_type("bogus") is False
    assert src.get_signal_type() == "tone"


def test_backend_compatible_interface():
    src = _new()
    assert src.get_frequency() > 0
    assert src.set_frequency(145e6) is True
    assert src.get_frequency() == 145e6
    assert src.set_sample_rate(96000) is True
    assert src.get_sample_rate() == 96000
    assert src.set_gain(20.0) is True
    assert src.get_gain() == 20.0
    src.set_agc(False)
    assert src.get_status()["agc_enabled"] is False


def test_disconnected_returns_none():
    src = DebugSource()
    src.disconnect()
    assert src.read_samples(100) is None


def test_auto_pick():
    src = _new()
    st = src.auto_pick_for("test_squelch")
    assert st == "noise"
    st = src.auto_pick_for("test_spectrum_fft")
    assert st == "sweep"
    st = src.auto_pick_for("test_am_demod")
    assert st == "tone"
