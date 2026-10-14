# SPDX-License-Identifier: MIT
"""IQ 反相（共轭）测试——sdrpp IqInvert 机制对等，2026-10-14 销账。"""
import numpy as np
import pytest

from mbdsdr_ai.iq_correction import IQCorrector, dsp_iq_invert, inject_imbalance


def _tone(freq: float, n: int = 2048, fs: float = 48000.0) -> np.ndarray:
    """复基带单音：freq>0 在上边带。"""
    t = np.arange(n) / fs
    return np.exp(2j * np.pi * freq * t)


def test_conjugate_swaps_sidebands():
    """+1kHz 单音共轭后能量落在 -1kHz（FFT 峰翻转）。"""
    x = _tone(1000.0)
    y = dsp_iq_invert(x)
    assert np.allclose(y, np.conjugate(x))
    fx = np.fft.fftshift(np.fft.fft(x))
    fy = np.fft.fftshift(np.fft.fft(y))
    freqs = np.fft.fftshift(np.fft.fftfreq(len(x), 1 / 48000.0))
    px = freqs[int(np.argmax(np.abs(fx)))]
    py = freqs[int(np.argmax(np.abs(fy)))]
    assert abs(px - 1000.0) < 25.0 and abs(py + 1000.0) < 25.0


def test_no_invert_returns_input():
    x = _tone(1000.0, n=64)
    y = dsp_iq_invert(x, invert=False)
    assert y is x                      # 原样返回，不复制


def test_does_not_mutate_caller_buffer():
    x = _tone(1000.0, n=64)
    x_copy = x.copy()
    dsp_iq_invert(x)
    assert np.array_equal(x, x_copy)   # 绝不原地改动


def test_double_invert_is_identity():
    x = _tone(1000.0, n=64)
    y = dsp_iq_invert(dsp_iq_invert(x))
    assert np.allclose(y, x)


def test_real_input_untouched():
    """非复数输入（如实数组）不报错、原样返回。"""
    x = np.array([1.0, 2.0, 3.0])
    assert dsp_iq_invert(x) is x
    assert dsp_iq_invert(x, invert=False) is x


def test_empty_array():
    x = np.array([], dtype=complex)
    y = dsp_iq_invert(x)
    assert y.size == 0


def test_independent_of_iq_corrector():
    """与 IQCorrector 互不依赖：增益校正后的流仍可单独反相。"""
    x = _tone(1000.0, n=4096)
    bad = inject_imbalance(_tone(1000.0, n=4096), 1.03, 4.0)
    c = IQCorrector()
    c.set_imbalance(1.03, 4.0)
    fixed = c.process(bad)
    flipped = dsp_iq_invert(fixed)
    # 校正应压低镜像：+1k 与 -1k 的功率比显著改善
    def side_ratio(sig):
        sp = np.fft.fftshift(np.fft.fft(sig))
        fr = np.fft.fftshift(np.fft.fftfreq(len(sig), 1 / 48000.0))
        p = np.abs(sp) ** 2
        up = p[(fr > 900) & (fr < 1100)].sum()
        dn = p[(fr < -900) & (fr > -1100)].sum()
        return up / dn
    # 校正应压低镜像：+1k/-1k 功率比显著改善（fixed 比值远大）
    assert side_ratio(fixed) > side_ratio(bad) * 1.5
    # 反相当且仅当翻转 FFT bin 顺序，幅度多重集不变（|conj| = |x|）
    mag_fixed = np.sort(np.abs(np.fft.fft(fixed)))
    mag_flip = np.sort(np.abs(np.fft.fft(flipped)))
    assert np.allclose(mag_fixed, mag_flip)
