# SPDX-License-Identifier: MIT
"""
test_dsp_demod_channelizer.py — deterministic tests for the Xlating FIR channelizer.
非硬件 / NOT HARDWARE: synthetic signal only.

Verifies that NCO mix -> low-pass -> decimation moves a signal to baseband while
keeping in-band energy.  Targets mbdsdr_ai/channelizer.py.
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.channelizer import XlatingFIR, lowpass_taps


def _bin_energy(x, sr, f0, bw=200.0):
    sp = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    fr = np.fft.rfftfreq(len(x), 1 / sr)
    m = (fr >= f0 - bw) & (fr <= f0 + bw)
    return float(np.sqrt(np.mean(sp[m] ** 2)))


def test_channelizer_moves_signal_to_baseband():
    """1MHz 采样、中心 +100kHz 的信号，offset=100k 后输出中心应在 0Hz。"""
    fs = 1_000_000.0
    t = np.arange(int(fs * 0.2)) / fs
    # 信号在 +100kHz
    sig = 0.3 * np.exp(1j * 2 * np.pi * 100_000 * t)
    ch = XlatingFIR(in_sr=fs, out_sr=fs / 4, bandwidth=12_500, offset=100_000)
    out = ch.process(sig)
    # 4 倍抽取
    assert len(out) == len(sig) // 4
    sr_out = fs / 4
    # 输出频谱主峰应在 0Hz 附近（基带）
    sp = np.abs(np.fft.fftshift(np.fft.fft(out)))
    fr = np.fft.fftshift(np.fft.fftfreq(len(out), 1 / sr_out))
    peak = fr[np.argmax(sp)]
    assert abs(peak) < 200.0, f"信道化后信号中心 {peak:.1f}Hz 偏离基带 0Hz"


def test_channelizer_decimates_4x():
    fs = 1_000_000.0
    t = np.arange(int(fs * 0.1)) / fs
    sig = 0.3 * np.exp(1j * 2 * np.pi * 100_000 * t)
    ch = XlatingFIR(in_sr=fs, out_sr=fs / 4, bandwidth=12_500, offset=100_000)
    out = ch.process(sig)
    assert len(out) == len(sig) // 4


def test_channelizer_rejects_out_of_band():
    """带外信号（距中心 > 带宽）应被低通滤除。"""
    fs = 1_000_000.0
    t = np.arange(int(fs * 0.2)) / fs
    # 带内：+100k 中心 5kHz
    in_band = 0.3 * np.exp(1j * 2 * np.pi * 100_005 * t)
    # 带外：+150k（距中心 50k > 12.5k 带宽）
    out_of_band = 0.3 * np.exp(1j * 2 * np.pi * 150_000 * t)
    ch = XlatingFIR(in_sr=fs, out_sr=fs / 4, bandwidth=12_500, offset=100_000)
    r_in = ch.process(in_band)
    ch.reset()
    r_out = ch.process(out_of_band)
    # 带内能量应显著大于带外
    e_in = np.sqrt(np.mean(np.abs(r_in) ** 2))
    e_out = np.sqrt(np.mean(np.abs(r_out) ** 2))
    assert e_in > 5 * e_out, f"带内 {e_in:.4f} 未显著大于带外 {e_out:.4f}"


def test_lowpass_taps_response():
    """低通抽头在通带平坦、阻带衰减。"""
    sr = 24_000.0
    taps = lowpass_taps(cutoff=1400.0, trans_width=140.0, sample_rate=sr)
    # 频响
    w = np.fft.rfft(taps, 4096)
    fr = np.fft.rfftfreq(4096, 1 / sr)
    # 通带 700Hz 增益接近 1
    passband = np.interp(700, fr, np.abs(w))
    # 阻带 3000Hz 应衰减
    stopband = np.interp(3000, fr, np.abs(w))
    assert passband > 0.9
    assert stopband < 0.1
