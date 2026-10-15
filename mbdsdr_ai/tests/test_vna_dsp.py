# SPDX-License-Identifier: MIT
"""vna_dsp 测试：双二阶/中值/电缆损耗陷波机制。"""
import numpy as np
import pytest

from mbdsdr_ai.vna_dsp import (
    Biquad,
    coax_loss_s11,
    median_filter,
    weighted_median_filter,
)

FS = 1000.0


def _sine(f, n=512, amp=1.0):
    t = np.arange(n) / FS
    return amp * np.sin(2 * np.pi * f * t)


def _freqz_mag(bq, f):
    """|H(e^{jω})| 直接求值（排除瞬态/加窗测量的伪差）。"""
    w = 2 * np.pi * f / FS
    z1 = np.exp(-1j * w)
    h = ((bq.b0 + bq.b1 * z1 + bq.b2 * z1 ** 2)
         / (1 + bq.a1 * z1 + bq.a2 * z1 ** 2))
    return float(np.abs(h))


def test_lowpass_dc_gain_and_cutoff():
    bq = Biquad.lowpass(100.0, FS, q=1 / np.sqrt(2))
    assert abs(_freqz_mag(bq, 1.0) - 1.0) < 1e-3          # DC 增益 1
    assert abs(_freqz_mag(bq, 100.0) - 1 / np.sqrt(2)) < 0.02   # -3dB @ fc


def test_highpass_blocks_dc():
    bq = Biquad.highpass(100.0, FS, q=1 / np.sqrt(2))
    assert _freqz_mag(bq, 0.0) < 1e-12                     # 真直流增益为 0
    assert _freqz_mag(bq, 1.0) < 5e-4                      # 1Hz ≈ (1/100)² 衰减
    assert abs(_freqz_mag(bq, 400.0) - 1.0) < 0.02


def test_notch_kills_target_freq():
    bq = Biquad.notch(200.0, FS, q=8.0)
    assert _freqz_mag(bq, 200.0) < 1e-3
    assert abs(_freqz_mag(bq, 100.0) - 1.0) < 0.05        # 远频几乎不动


def test_peak_boosts_center():
    bq = Biquad.peak(300.0, FS, q=2.0, gain_db=6.0)
    assert abs(_freqz_mag(bq, 300.0) - 2.0) < 0.05        # +6dB = x2
    assert abs(_freqz_mag(bq, 100.0) - 1.0) < 0.05


def test_prime_removes_step_transient():
    """常值信号经 prime 后从第一拍起即稳态（无起始瞬态）。"""
    bq = Biquad.lowpass(100.0, FS, q=1 / np.sqrt(2))
    x0 = 1.0 + 0.5j
    bq.prime(x0)
    ys = [bq.step(x0) for _ in range(64)]
    g = (bq.b0 + bq.b1 + bq.b2) / (1 + bq.a1 + bq.a2)
    assert np.allclose(ys, x0 * g, atol=1e-9)


def test_prime_constant_input_matches_unprimed_steady_state():
    bq = Biquad.lowpass(100.0, FS, q=0.8)
    steady = bq.filter(np.full(4096, 2.0))[-1]
    bq2 = Biquad.lowpass(100.0, FS, q=0.8)
    bq2.prime(2.0)
    assert abs(bq2.step(2.0) - steady) < 1e-9


def test_complex_signal_filtering():
    bq = Biquad.lowpass(100.0, FS, q=0.9)
    x = np.exp(1j * 2 * np.pi * 30.0 * np.arange(512) / FS)
    y = bq.filter(x)
    assert y.dtype == np.complex128 or np.iscomplexobj(y)
    assert np.max(np.abs(y[256:])) > 0.9                  # 30Hz 通过
    x2 = np.exp(1j * 2 * np.pi * 300.0 * np.arange(512) / FS)
    y2 = bq.filter(x2)
    assert np.max(np.abs(y2[256:])) < 0.1                 # 300Hz 抑制


def test_median_filter_kills_spike():
    x = np.zeros(32)
    x[16] = 10.0
    y = median_filter(x, n=2)
    assert np.allclose(y, np.zeros(32))


def test_median_filter_preserves_edges_constant():
    x = np.full(16, 3.3)
    assert np.allclose(median_filter(x, n=3), x)


def test_complex_median_independent_components():
    x = np.zeros(9, dtype=complex)
    x[4] = 100 + 50j
    y = median_filter(x, n=2)
    assert abs(y[4]) < 1e-9


def test_weighted_median_attenuates_impulse():
    """字面语义：剔一个中值元素后均值——孤脉冲衰减为 amp/窗宽、展宽一
    格（[1,5,1]→3），不根除；扫迹平滑够用，毛刺定位请用 median_filter。"""
    x = np.array([1.0, 1.0, 1.0, 1.0, 5.0, 1.0, 1.0, 1.0, 1.0])
    y = weighted_median_filter(x, n=1)
    assert abs(y[4] - 3.0) < 1e-9          # 脉冲 5 -> 3（衰减不根除）
    assert np.all(y <= 3.0 + 1e-9)         # 能量不外泄到 3 以上
    assert list(y).count(3.0) == 3         # 展宽宽度 = 窗宽


# ---- 电缆损耗陷波 -------------------------------------------------------------

def _ripple_fixture(n=512, k=40, ripple_amp=0.2, trend=True):
    """干净损耗趋势 + 已知 bin k 的周期波纹。返回 (s11, k, trend)。"""
    idx = np.arange(n)
    base = np.full(n, 0.5 - 0.01j)
    tr = -1e-4 * idx * (1 + 0.2j) if trend else np.zeros(n, dtype=complex)
    ripple = ripple_amp * np.exp(2j * np.pi * k * idx / n)
    return base + tr + ripple, k, base + tr


def test_coax_loss_detects_and_suppresses_ripple():
    s11, k, trend = _ripple_fixture()
    clean, detected = coax_loss_s11(s11)
    assert detected == k                                  # 基频 bin 检对
    spec_before = np.abs(np.fft.fft(s11 - trend))
    spec_after = np.abs(np.fft.fft(clean - trend))
    assert float(np.sqrt(np.mean(spec_after ** 2))) < \
        0.4 * float(np.sqrt(np.mean(spec_before ** 2)))   # 波纹显著压掉
    # 缓变趋势保留：滤波结果贴着无波纹的真值走（误差远小于原波纹幅）
    err_after = np.sqrt(np.mean(np.abs(clean - trend) ** 2))
    err_before = np.sqrt(np.mean(np.abs(s11 - trend) ** 2))
    assert err_after < 0.4 * err_before


def test_coax_loss_honest_when_clean():
    s11 = np.full(512, 0.5 - 0.01j) - 1e-4 * np.arange(512)
    clean, detected = coax_loss_s11(s11)
    assert detected is None                               # 无振铃不硬滤
    assert np.allclose(clean, s11)


def test_coax_loss_degenerate_inputs():
    short, det = coax_loss_s11(np.ones(4, dtype=complex))
    assert det is None and len(short) == 4
