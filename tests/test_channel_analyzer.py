# SPDX-License-Identifier: MIT
"""确定性单测：信道分析器 —— 已知信号 → 正确带宽/功率测量。"""
import numpy as np
import pytest

from mbdsdr_ai.channel_analyzer import ChannelAnalyzer, analyze_iq


def _tone(fs, freq_offset, duration=1.0, amp=0.5, noise=0.005):
    t = np.arange(int(fs * duration)) / fs
    return amp * np.exp(2j * np.pi * freq_offset * t) + noise * (np.random.randn(len(t)) + 1j * np.random.randn(len(t)))


def test_finds_single_tone_power_and_bandwidth():
    fs = 200_000
    iq = _tone(fs, 5000.0, duration=1.0)
    r = analyze_iq(iq, fs, center_hz=100e6, fft_size=2048, threshold_db=6.0)
    # 应检测到 1 个信号
    assert len(r.signals) >= 1
    sig = max(r.signals, key=lambda s: s["peak_power_db"])
    # 中心频率在 5 kHz 偏移 ± 一个 bin 内
    bin_hz = fs / 2048
    assert abs(sig["center_hz"] - (100e6 + 5000.0)) < bin_hz * 2
    # 带宽是窄带（CW/单音），应 < 2 kHz
    assert sig["bandwidth_hz"] < 2000
    # 峰值应显著高于底噪
    assert sig["peak_power_db"] > r.noise_floor_db + 10


def test_occupancy_between_0_and_1():
    fs = 200_000
    iq = _tone(fs, 0.0, duration=0.5, noise=0.01)
    r = analyze_iq(iq, fs, fft_size=1024, threshold_db=6.0)
    assert 0.0 <= r.occupancy <= 1.0


def test_quiet_input_no_signals():
    fs = 200_000
    rng = np.random.default_rng(42)
    iq = 0.001 * (rng.standard_normal(fs // 4) + 1j * rng.standard_normal(fs // 4))
    r = analyze_iq(iq, fs, fft_size=1024, threshold_db=20.0)
    # 极高阈值下不应检出信号
    assert all(s["peak_power_db"] < r.noise_floor_db + 20 for s in r.signals)


def test_fm_demod_output_length():
    fs = 200_000
    iq = _tone(fs, 10_000.0, duration=0.2)
    ca = ChannelAnalyzer(fft_size=1024)
    r = ca.analyze(iq, fs, demod="fm")
    assert r.demod_audio is not None
    assert len(r.demod_audio) == len(iq) - 1
