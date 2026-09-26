"""Spectrogram 单测。

验证 STFT 能量集中在正确频率，选框测量返回正确值。
对照 inspectrum ``spectrogramplot.cpp::getLine`` 与 ``tuner.cpp``。
"""
import numpy as np

from mbdsdr_ai.analysis.spectrogram import Spectrogram


SR = 1_000_000.0


def _make_tone(freq=50_000.0, duration=0.02):
    t = np.arange(int(SR * duration)) / SR
    return np.exp(1j * 2 * np.pi * freq * t)


def test_stft_energy_at_correct_frequency():
    """单音信号的 STFT 能量应集中在该频率。"""
    sig = _make_tone(50_000.0)
    spec = Spectrogram(sig, SR, fft_size=256, overlap=0.5)
    t_mid = spec.times[len(spec.times) // 2]

    e_50k = spec.energy_at(t_mid, 50_000.0)
    e_0 = spec.energy_at(t_mid, 0.0)
    e_100k = spec.energy_at(t_mid, 100_000.0)

    # 信号频率处能量应显著高于偏离处
    assert e_50k > e_0 + 20.0, f"50kHz={e_50k:.1f}dB, 0Hz={e_0:.1f}dB"
    assert e_50k > e_100k + 20.0, f"50kHz={e_50k:.1f}dB, 100kHz={e_100k:.1f}dB"


def test_selection_measurements():
    """选框返回正确的中心频率、带宽、持续时间。"""
    sig = _make_tone(50_000.0)
    spec = Spectrogram(sig, SR, fft_size=256, overlap=0.5)

    sel = spec.select(t_start=0.005, t_end=0.015,
                      f_low=40_000.0, f_high=60_000.0)

    assert abs(sel.center_freq - 50_000.0) < 1.0
    assert abs(sel.bandwidth - 20_000.0) < 1.0
    assert abs(sel.duration - 0.01) < 1e-3
    assert len(sel.iq) > 0


def test_selection_returns_iq():
    """选框导出的 IQ 样本应有效。"""
    sig = _make_tone(50_000.0)
    spec = Spectrogram(sig, SR, fft_size=256, overlap=0.5)
    sel = spec.select(0.005, 0.015, 40_000.0, 60_000.0)
    assert sel.iq.dtype == np.complex128
    assert len(sel.iq) > 1000
    assert np.mean(np.abs(sel.iq)) > 0.1


def test_auto_select_peak():
    """AI 增强：自动找峰值选框。"""
    sig = _make_tone(50_000.0)
    spec = Spectrogram(sig, SR, fft_size=256, overlap=0.5)
    sel = spec.auto_select_peak()
    # 选框中心应接近 50kHz
    assert abs(sel.center_freq - 50_000.0) < 10_000.0


def test_multitone_spectrogram():
    """两个单音的 STFT 应在两个频率都有能量。"""
    t = np.arange(int(SR * 0.02)) / SR
    sig = np.exp(1j * 2 * np.pi * 30_000.0 * t) + 0.5 * np.exp(1j * 2 * np.pi * 80_000.0 * t)
    spec = Spectrogram(sig, SR, fft_size=256, overlap=0.5)
    t_mid = spec.times[len(spec.times) // 2]

    e_30k = spec.energy_at(t_mid, 30_000.0)
    e_80k = spec.energy_at(t_mid, 80_000.0)
    e_mid = spec.energy_at(t_mid, 55_000.0)

    assert e_30k > e_mid + 10.0
    assert e_80k > e_mid + 10.0


def test_frequency_axis():
    """频率轴应覆盖 [-fs/2, fs/2]。"""
    sig = _make_tone()
    spec = Spectrogram(sig, SR, fft_size=256)
    assert abs(spec.freqs[0] - (-SR / 2)) < SR / 256
    assert abs(spec.freqs[-1] - SR / 2) < SR / 2 + SR / 256
