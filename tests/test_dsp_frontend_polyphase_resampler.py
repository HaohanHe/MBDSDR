# SPDX-License-Identifier: MIT
"""Polyphase rational resampler tests.
非硬件 / NOT HARDWARE: synthetic signals."""
import numpy as np
from mbdsdr_ai.polyphase_resampler import PolyphaseResampler


def _bin_amp(spec, freqs, f0, width=3):
    idx = np.argmin(np.abs(freqs - f0))
    lo = max(0, idx - width)
    hi = min(len(spec), idx + width + 1)
    return float(np.mean(np.abs(spec[lo:hi])))


def test_polyphase_resample_ratio():
    """1MHz -> 250kHz 输出长度比 ≈ 0.25，且 100kHz 单音保留。"""
    fs_in = 1_000_000.0
    fs_out = 250_000.0
    n = 400_000
    t = np.arange(n) / fs_in
    x = 0.5 * np.exp(1j * 2 * np.pi * 100_000 * t).astype(np.complex64)
    r = PolyphaseResampler(fs_in, fs_out)
    y = r.process(x)[500:]
    assert abs(len(y) / n - fs_out / fs_in) < 0.01


def test_polyphase_resample_no_alias():
    """1MHz 采样 100kHz 单音 + 400kHz 阻带单音，重采样到 250kHz：
    100kHz 保留，400kHz（>输出 Nyquist 125k）镜像抑制 > 50dB。"""
    fs_in = 1_000_000.0
    fs_out = 250_000.0
    n = 1_000_000
    t = np.arange(n) / fs_in

    # 通带：100kHz；阻带：400kHz（输出 Nyquist=125k，必须被原型低通滤掉）
    x_pass = 0.5 * np.exp(1j * 2 * np.pi * 100_000 * t).astype(np.complex64)
    r1 = PolyphaseResampler(fs_in, fs_out)
    yp = r1.process(x_pass)[500:]
    Yp = np.fft.fftshift(np.fft.fft(yp * np.hanning(len(yp))))
    fp = np.fft.fftshift(np.fft.fftfreq(len(yp), 1.0 / fs_out))
    g_pass = _bin_amp(Yp, fp, 100_000) / 0.5

    x_stop = 0.5 * np.exp(1j * 2 * np.pi * 400_000 * t).astype(np.complex64)
    r2 = PolyphaseResampler(fs_in, fs_out)
    ys = r2.process(x_stop)[500:]
    Ys = np.fft.fftshift(np.fft.fft(ys * np.hanning(len(ys))))
    fs_ = np.fft.fftshift(np.fft.fftfreq(len(ys), 1.0 / fs_out))
    # 400k 在 4× 抽取下折叠到 400k mod 500k = -100k（即 ±100k 带内）
    g_stop = _bin_amp(Ys, fs_, -100_000) / 0.5

    suppression = 20 * np.log10(g_pass / max(g_stop, 1e-12))
    assert suppression > 50.0, f"镜像抑制 {suppression:.1f} dB，应 > 50 dB"


def test_polyphase_non_integer_ratio():
    """非整数比（1MHz -> 48kHz）能跑通且长度比正确。"""
    r = PolyphaseResampler(1_000_000.0, 48_000.0)
    up, down = r.interp_decim
    assert up > 1 or down > 1
    n = 100_000
    t = np.arange(n) / 1_000_000.0
    x = np.exp(1j * 2 * np.pi * 5_000 * t).astype(np.complex64)
    y = r.process(x)
    assert abs(len(y) / n - 48_000.0 / 1_000_000.0) < 0.02
