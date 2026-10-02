# SPDX-License-Identifier: MIT
"""
Q3：多普勒补偿模块（NCO 数字下混频）确定性离线测试

不联网、无硬件：
  - 恒定频偏：apply 后谱峰落在 fd，remove 后回到 DC；
  - 时变 chirp：apply→remove 精确回原（浮点量级误差）；
  - 标量 / 逐样本数组 / callable 三种频移规格等价；
  - 零频偏 = 恒等；同输入两次输出逐点一致（确定性）；
  - 错误长度数组抛 ValueError。

运行：python3 -m pytest mbdsdr_ai/tests/test_doppler_compensation.py -v
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai.doppler_compensation import (  # noqa: E402
    remove_doppler_shift,
    apply_doppler_shift,
    doppler_phase_radians,
)

FS = 2_000_000.0


def _bin_hz(n: int) -> float:
    return FS / n


def _peak_hz(x: np.ndarray) -> float:
    n = len(x)
    spec = np.fft.fftshift(np.fft.fft(x))
    freqs = np.fft.fftshift(np.fft.fftfreq(n, d=1.0 / FS))
    return float(freqs[int(np.argmax(np.abs(spec)))])


def test_constant_shift_moves_peak_and_remove_returns_to_dc():
    n = 32768
    clean = np.ones(n, dtype=np.complex128)
    dop = apply_doppler_shift(clean, FS, 500.0)
    assert abs(_peak_hz(dop) - 500.0) < _bin_hz(n)
    back = remove_doppler_shift(dop, FS, 500.0)
    assert abs(_peak_hz(back) - 0.0) < _bin_hz(n)


def test_chirp_roundtrip_is_exact():
    n = 20000
    clean = np.ones(n, dtype=np.complex128)
    T = n / FS
    f = lambda t: 3000.0 * (2.0 * (t / T) - 1.0)   # -1500..+1500 Hz
    dop = apply_doppler_shift(clean, FS, f)
    back = remove_doppler_shift(dop, FS, f)
    assert np.max(np.abs(back - 1.0)) < 1e-4


def test_callable_matches_per_sample_array():
    n = 20000
    clean = np.ones(n, dtype=np.complex128)
    T = n / FS
    t = np.arange(n) / FS
    arr = 3000.0 * (2.0 * (t / T) - 1.0)
    f = lambda ti: float(np.interp(ti, t, arr))
    out_arr = remove_doppler_shift(clean, FS, arr)
    out_call = remove_doppler_shift(clean, FS, f)
    assert np.max(np.abs(out_arr - out_call)) < 1e-9


def test_zero_doppler_is_identity():
    rng = np.random.default_rng(7)
    x = rng.standard_normal(1000) + 1j * rng.standard_normal(1000)
    out = remove_doppler_shift(x, FS, 0.0)
    assert np.array_equal(out, x.astype(np.complex128))


def test_deterministic():
    rng = np.random.default_rng(1)
    x = rng.standard_normal(500) + 1j * rng.standard_normal(500)
    a = remove_doppler_shift(x, FS, 250.0)
    b = remove_doppler_shift(x, FS, 250.0)
    assert np.array_equal(a, b)


def test_wrong_length_array_raises():
    x = np.ones(100, dtype=np.complex128)
    with pytest.raises(ValueError):
        remove_doppler_shift(x, FS, np.array([1.0, 2.0, 3.0]))


def test_empty_input_identity():
    x = np.empty(0, dtype=np.complex128)
    assert remove_doppler_shift(x, FS, 100.0).size == 0
