# SPDX-License-Identifier: MIT
"""
测试信号发生器单元测试
========================

确定性测试：固定 seed，断言噪声统计特性、单音频率精度、扫频范围。
不接硬件、不接 UI。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from mbdsdr_ai.signal_generator import (
    SignalGenerator, SignalSpec, dbfs_to_amplitude,
)


# ── 1. 噪声统计特性 ────────────────────────────────────────────

def test_noise_power_matches_dbfs():
    gen = SignalGenerator(seed=123)
    z = gen.noise(duration_s=1.0, sample_rate=48000.0, power_dbfs=-20.0)
    # 复噪声功率 E|z|^2
    p = float(np.mean(np.abs(z) ** 2))
    dbfs = 10.0 * np.log10(p)
    # 长序列下应接近 -20 dBFS（容差 1.5 dB）
    assert abs(dbfs - (-20.0)) < 1.5, f"got {dbfs:.2f} dBFS"


def test_noise_mean_zero():
    gen = SignalGenerator(seed=7)
    z = gen.noise(0.5, 48000.0, -30.0)
    # I/Q 均值都应接近 0
    assert abs(float(np.mean(z.real))) < 0.01
    assert abs(float(np.mean(z.imag))) < 0.01


def test_noise_deterministic_with_seed():
    a = SignalGenerator(seed=99).noise(0.1, 48000.0, -20.0)
    b = SignalGenerator(seed=99).noise(0.1, 48000.0, -20.0)
    np.testing.assert_array_equal(a, b)


# ── 2. 单音频率精度 ────────────────────────────────────────────

def test_tone_frequency_accuracy():
    fs = 48000.0
    f0 = 1200.0
    gen = SignalGenerator(seed=0)
    tone = gen.tone(duration_s=0.5, sample_rate=fs, freq_hz=f0,
                    power_dbfs=-20.0)
    # FFT 找峰值 bin（复信号用 fft/fftfreq）
    spec = np.fft.fft(tone * np.hanning(tone.size))
    freqs = np.fft.fftfreq(tone.size, d=1.0 / fs)
    peak_bin = int(np.argmax(np.abs(spec)))
    peak_freq = freqs[peak_bin]
    # 容差一个 bin 分辨率
    res = fs / tone.size
    assert abs(peak_freq - f0) <= res, f"peak at {peak_freq}, want {f0}"


def test_tone_power():
    gen = SignalGenerator(seed=0)
    tone = gen.tone(0.2, 48000.0, 1000.0, power_dbfs=-10.0)
    p = float(np.mean(np.abs(tone) ** 2))
    dbfs = 10.0 * np.log10(p)
    # 复正弦 |exp|=1，RMS 功率 = amp^2
    assert abs(dbfs - (-10.0)) < 0.5


# ── 3. 扫频范围 ────────────────────────────────────────────────

def test_sweep_covers_range():
    fs = 48000.0
    f0, f1 = -5000.0, 5000.0
    gen = SignalGenerator(seed=0)
    sw = gen.sweep(duration_s=1.0, sample_rate=fs,
                   start_freq_hz=f0, end_freq_hz=f1, power_dbfs=-20.0)
    # 瞬时频率：开头段应接近 f0，结尾段接近 f1。用短时相位差分验证。
    # 取前 1000 样本和后 1000 样本的相位斜率。
    def inst_freq(x):
        phase = np.unwrap(np.angle(x))
        return float(np.mean(np.diff(phase)) * fs / (2.0 * np.pi))
    f_start = inst_freq(sw[:2000])
    f_end = inst_freq(sw[-2000:])
    # 窗口内瞬时频率随时间线性爬升，平均值偏离端点 ~ (f1-f0)/2 * (window/T)
    assert abs(f_start - f0) < 600.0, f"start inst f={f_start}"
    assert abs(f_end - f1) < 600.0, f"end inst f={f_end}"


# ── 4. AI auto_pick ───────────────────────────────────────────

def test_auto_pick_mapping():
    assert SignalGenerator.auto_pick("squelch").kind == "noise"
    assert SignalGenerator.auto_pick("channelizer").kind == "sweep"
    assert SignalGenerator.auto_pick("agc").kind == "noise"
    assert SignalGenerator.auto_pick("demod_fm").kind == "tone"
    assert SignalGenerator.auto_pick("fft_display").kind == "sweep"
