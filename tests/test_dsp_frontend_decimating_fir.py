# SPDX-License-Identifier: MIT
"""Anti-alias decimating FIR tests.
非硬件 / NOT HARDWARE: synthetic signals."""
import numpy as np
from mbdsdr_ai.decimating_fir import DecimatingFIR, design_decimation_taps


def _bin_amp(spec, freqs, f0, width=3):
    idx = np.argmin(np.abs(freqs - f0))
    lo = max(0, idx - width)
    hi = min(len(spec), idx + width + 1)
    return float(np.mean(np.abs(spec[lo:hi])))


def _run(taps, D, x):
    d = DecimatingFIR(taps, D)
    y = d.process(x)[500:]  # 去暂态
    Y = np.fft.fftshift(np.fft.fft(y * np.hanning(len(y))))
    freqs = np.fft.fftshift(np.fft.fftfreq(len(y), 1.0 / (1e6 / D)))
    return Y, freqs


def test_decimating_fir_anti_alias():
    """1MHz 采样含 100kHz(通带)+600kHz(阻带)，4× 抽到 250kHz：
    600kHz 折叠分量被抗混叠滤除 > 40dB（相对通带增益）。"""
    fs = 1_000_000.0
    n = 500_000
    t = np.arange(n) / fs
    taps = design_decimation_taps(4, fs)

    # 通带：100kHz 单音（幅度 0.5）
    x_pass = 0.5 * np.exp(1j * 2 * np.pi * 100_000 * t).astype(np.complex64)
    Yp, fp = _run(taps, 4, x_pass)
    g_pass = _bin_amp(Yp, fp, 100_000) / 0.5

    # 阻带：600kHz 单音（幅度 0.3）。4× 抽取下折叠位置 = 600k mod 500k = 100k
    x_stop = 0.3 * np.exp(1j * 2 * np.pi * 600_000 * t).astype(np.complex64)
    Ys, fs_ = _run(taps, 4, x_stop)
    g_stop = _bin_amp(Ys, fs_, 100_000) / 0.3  # 泄漏到 100k bin 的增益

    suppression = 20 * np.log10(g_pass / max(g_stop, 1e-12))
    assert suppression > 40.0, f"抗混叠抑制 {suppression:.1f} dB，应 > 40 dB"


def test_decimating_fir_length_ratio():
    """4× 抽取后长度 ≈ 输入/4。"""
    fs = 1_000_000.0
    n = 100_000
    t = np.arange(n) / fs
    x = np.exp(1j * 2 * np.pi * 50_000 * t).astype(np.complex64)
    taps = design_decimation_taps(4, fs)
    y = DecimatingFIR(taps, 4).process(x)
    assert abs(len(y) - n / 4) <= 2


def test_decimating_fir_stateful():
    """分块处理与一次性处理结果一致。"""
    fs = 1_000_000.0
    n = 40_000
    t = np.arange(n) / fs
    x = np.exp(1j * 2 * np.pi * 30_000 * t).astype(np.complex64)
    taps = design_decimation_taps(4, fs)
    y1 = DecimatingFIR(taps, 4).process(x)
    d2 = DecimatingFIR(taps, 4)
    ya = d2.process(x[:10_000])
    yb = d2.process(x[10_000:])
    y2 = np.concatenate([ya, yb])
    assert np.allclose(y1, y2, atol=1e-10)
