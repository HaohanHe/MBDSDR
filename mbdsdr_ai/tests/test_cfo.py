# SPDX-License-Identifier: MIT
"""
cfo.py 载波频偏估计与校正——确定性离线测试

不联网、无硬件，全部用合成信号（固定 seed）：
  - FFT 粗估恢复已知频偏，精度约一个 bin（fs/N）；
  - search_hz 邻域限制抗强邻台（只认 f_expected 附近的峰）；
  - Kay 精估在高 SNR 下远精于 bin（相位差分加权，近似 MLE）；
  - correct_cfo 复混频把频偏搬回预期频率（峰值回 DC）；
  - estimate_and_correct 全链路：高 SNR 采用精估、残余远小于 bin；
  - 低 SNR 相干性门限：Kay 不被采用（fine_applied=False），稳健回退粗估；
  - 短输入（m<2）Kay 返回 0.0 不崩；
  - offset_ppm_100mhz 以 100 MHz 参考折算 ppm。

运行：python3 -m pytest mbdsdr_ai/tests/test_cfo.py -v
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai.cfo import (  # noqa: E402
    CFOResult,
    _parabolic_subbin,
    correct_cfo,
    estimate_and_correct,
    estimate_cfo_fft,
    estimate_cfo_kay,
)

FS = 2_000_000.0


def _tone(n: int, fs: float, f_hz: float, amp: float = 1.0,
          phase: float = 0.0) -> np.ndarray:
    t = np.arange(n) / fs
    return amp * np.exp(1j * (2.0 * np.pi * f_hz * t + phase))


def _peak_hz(x: np.ndarray, fs: float) -> float:
    n = len(x)
    spec = np.fft.fftshift(np.fft.fft(x))
    freqs = np.fft.fftshift(np.fft.fftfreq(n, d=1.0 / fs))
    return float(freqs[int(np.argmax(np.abs(spec)))])


def _bin_hz(n: int, fs: float = FS) -> float:
    return fs / n


# ---------------------------------------------------------------- FFT 粗估

def test_estimate_cfo_fft_recovers_offset_within_one_bin():
    n = 32768
    off = 1234.5
    x = _tone(n, FS, off)
    est, peak = estimate_cfo_fft(x, FS, f_expected=0.0)
    assert abs(est - off) < _bin_hz(n)
    assert abs(peak - off) < _bin_hz(n)


def test_estimate_cfo_fft_relative_to_expected():
    # f_expected 约定在可观测基带内（真实用法：SDR 已把载波调到零中频附近，
    # offset 是晶体拉偏的小残量）。带外 f_expected 不在模块契约内。
    n = 32768
    f_if = 100e3
    true_off = -800.0          # 晶体拉偏：实际频率低于标称
    x = _tone(n, FS, f_if + true_off)
    est, peak = estimate_cfo_fft(x, FS, f_expected=f_if)
    assert abs(est - true_off) < _bin_hz(n)
    assert abs(peak - (f_if + true_off)) < _bin_hz(n)


def test_estimate_cfo_fft_search_window_rejects_strong_neighbor():
    """强邻台在预期频率远处时，search_hz 邻域内找峰不被带偏。"""
    n = 65536
    true_off = 500.0
    neighbor = 50_000.0        # 幅度 10 倍的邻台
    x = _tone(n, FS, true_off) + 10.0 * _tone(n, FS, neighbor, phase=1.0)
    est_narrow, _ = estimate_cfo_fft(x, FS, f_expected=0.0, search_hz=5000.0)
    assert abs(est_narrow - true_off) < _bin_hz(n)
    est_wide, _ = estimate_cfo_fft(x, FS, f_expected=0.0)
    assert abs(est_wide - neighbor) < _bin_hz(n)   # 不限邻域时全局峰是邻台


# ---------------------------------------------------------------- Kay 精估

def test_estimate_cfo_kay_high_snr_far_better_than_bin():
    n = 16384
    off = 321.0
    x = _tone(n, FS, off)
    est = estimate_cfo_kay(x, FS)
    assert abs(est - off) < 1.0                    # 远精于 bin（122 Hz）
    assert abs(est - off) < _bin_hz(n) / 100.0


def test_estimate_cfo_kay_short_input_returns_zero():
    assert estimate_cfo_kay(_tone(1, FS, 100.0), FS) == 0.0
    assert estimate_cfo_kay(np.zeros(0, dtype=np.complex128), FS) == 0.0


# ---------------------------------------------------------------- 校正

def test_correct_cfo_moves_peak_back_to_expected():
    n = 32768
    off = 2000.0
    x = _tone(n, FS, off)
    y = correct_cfo(x, FS, off)
    assert abs(_peak_hz(y, FS)) < _bin_hz(n)


def test_correct_cfo_zero_offset_is_identity():
    rng = np.random.default_rng(7)
    x = rng.standard_normal(1000) + 1j * rng.standard_normal(1000)
    assert np.array_equal(correct_cfo(x, FS, 0.0), x.astype(np.complex128))


def test_correct_cfo_deterministic():
    rng = np.random.default_rng(1)
    x = rng.standard_normal(500) + 1j * rng.standard_normal(500)
    a = correct_cfo(x, FS, 250.0)
    b = correct_cfo(x, FS, 250.0)
    assert np.array_equal(a, b)


# ---------------------------------------------------------------- 全链路

def test_estimate_and_correct_high_snr_applies_fine_and_small_residual():
    n = 32768
    off = 4321.0
    x = _tone(n, FS, off)
    res = estimate_and_correct(x, FS, f_expected=0.0)
    assert isinstance(res, CFOResult)
    assert res.fine_applied is True                  # 相干性达标
    assert res.coherence >= 0.9
    assert abs(res.offset_hz - off) < _bin_hz(n)
    assert abs(res.residual_hz) < _bin_hz(n) / 4.0   # 校正后残余远小于 bin
    assert abs(_peak_hz(res.corrected, FS)) < _bin_hz(n)


def test_estimate_and_correct_low_snr_keeps_robust_fallback():
    """噪声主导时相干性门限不达标：不采用 Kay，只信粗估（诚实稳健）。"""
    n = 8192
    off = 700.0
    rng = np.random.default_rng(42)
    x = 0.05 * _tone(n, FS, off) + 0.5 * (
        rng.standard_normal(n) + 1j * rng.standard_normal(n))
    res = estimate_and_correct(x, FS, f_expected=0.0)
    assert res.fine_applied is False
    assert res.fine_hz == 0.0
    assert res.coherence < 0.9
    # 回退后仍应大致对准（粗估 bin 级精度），绝不假装精估结果
    assert abs(res.offset_hz - off) < 5 * _bin_hz(n)


def test_estimate_and_correct_fine_disabled_by_flag():
    n = 16384
    x = _tone(n, FS, 1000.0)
    res = estimate_and_correct(x, FS, f_expected=0.0, fine=False)
    assert res.fine_applied is False
    assert res.fine_hz == 0.0
    assert abs(res.offset_hz - 1000.0) < _bin_hz(n)


def test_estimate_and_correct_respects_search_window():
    n = 65536
    true_off = 500.0
    x = _tone(n, FS, true_off) + 10.0 * _tone(n, FS, 50_000.0, phase=1.0)
    res = estimate_and_correct(x, FS, f_expected=0.0, search_hz=5000.0)
    assert abs(res.offset_hz - true_off) < 5 * _bin_hz(n)


# ---------------------------------------------------------------- 工具函数

def test_parabolic_subbin_boundary_and_flat_peak():
    flat = np.ones(9)
    assert _parabolic_subbin(flat, 4) == 0.0        # 平顶：分母近零
    assert _parabolic_subbin(flat, 0) == 0.0        # 左边界
    assert _parabolic_subbin(flat, len(flat) - 1) == 0.0   # 右边界


def test_parabolic_subbin_symmetric_peak_centered():
    # 以 k 为中心的对称三点：插值偏移应为 0
    p = np.array([0.0, 0.0, 1.0, 3.0, 1.0, 0.0, 0.0])
    assert abs(_parabolic_subbin(p, 3)) < 1e-12


def test_offset_ppm_reference_math():
    res = CFOResult(offset_hz=2500.0, coarse_hz=2500.0, fine_hz=0.0,
                    residual_hz=0.0, f_expected=100e6, fs=FS)
    assert abs(res.offset_ppm_100mhz - 25.0) < 1e-9   # 2500Hz/100MHz = 25ppm


@pytest.mark.parametrize("n", [1024, 4096, 16384])
def test_fft_estimate_never_worse_than_two_bins(n):
    """高 SNR 单音下粗估至多两个 bin 误差（回归护栏，非精度标语）。"""
    off = float(137 * (n % 7 + 1))
    x = _tone(n, FS, off)
    est, _ = estimate_cfo_fft(x, FS, f_expected=0.0)
    assert abs(est - off) < 2 * _bin_hz(n)
