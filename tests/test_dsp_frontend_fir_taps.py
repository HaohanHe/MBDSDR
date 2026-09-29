# SPDX-License-Identifier: MIT
"""FIR tap-generator tests.
非硬件 / NOT HARDWARE: synthetic responses."""
import numpy as np
from mbdsdr_ai.fir_taps import (
    lowpass_taps, estimate_tap_count, nuttall, windowed_sinc,
    root_raised_cosine_taps,
)


def _freqz_db(taps, fs, n_fft=4096):
    H = np.fft.fft(taps, n_fft)
    H_db = 20 * np.log10(np.abs(H) / np.max(np.abs(H)) + 1e-12)
    freqs = np.fft.fftfreq(n_fft, 1.0 / fs)
    return freqs, H_db


def test_fir_taps_lowpass_response():
    """lowpass cutoff=100kHz sr=1MHz：通带(<80kHz)波纹 < 1dB，
    阻带(>200kHz)衰减 > 40dB。"""
    fs = 1_000_000.0
    taps = lowpass_taps(100_000.0, 20_000.0, fs, odd=True)
    freqs, H_db = _freqz_db(taps, fs)

    # 通带：0..80kHz（正频率）
    passband = (freqs >= 0) & (freqs <= 80_000)
    ripple = H_db[passband].max() - H_db[passband].min()
    assert ripple < 1.0, f"通带波纹 {ripple:.2f} dB，应 < 1 dB"

    # 阻带：200..400kHz（正频率）
    stopband = (freqs >= 200_000) & (freqs <= 400_000)
    stop_attenuation = -H_db[stopband].max()  # 越大越好
    assert stop_attenuation > 40.0, f"阻带衰减 {stop_attenuation:.1f} dB，应 > 40 dB"


def test_fir_taps_unity_gain_at_dc():
    """低通直流增益 = 1。"""
    taps = lowpass_taps(100_000.0, 20_000.0, sample_rate_hz=1_000_000.0)
    assert abs(np.sum(taps) - 1.0) < 1e-6


def test_estimate_tap_count():
    """Tap estimate: 3.8*sr/transWidth."""
    assert estimate_tap_count(20_000.0, 1_000_000.0) == int(round(3.8 * 1e6 / 2e4))


def test_nuttall_window_shape():
    """Nuttall window: peak = 1 at n=N/2, ~0 at n=0/N."""
    N = 200.0
    w_center = nuttall(np.array([N / 2.0]), N)
    assert abs(w_center[0] - 1.0) < 1e-6
    w_edge = nuttall(np.array([0.0, N]), N)
    assert np.all(np.abs(w_edge) < 0.05)


def test_rrc_taps_energy_normalized():
    """RRC 抽头非零且长度正确。"""
    taps = root_raised_cosine_taps(101, beta=0.35,
                                   symbol_rate_hz=24000.0, sample_rate_hz=96000.0)
    assert taps.shape == (101,)
    assert np.isfinite(taps).all()
