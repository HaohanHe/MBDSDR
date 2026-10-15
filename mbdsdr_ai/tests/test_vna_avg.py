# SPDX-License-Identifier: MIT
"""vna_avg 测试：扫间平均 + 历史回溯。"""
import numpy as np
import pytest

from mbdsdr_ai.vna_avg import SweepAverager

N = 64
F = np.linspace(1e6, 3e9, N)


def _sweep(phase_deg=0.0, mag=1.0, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    ph = np.radians(phase_deg) + 2 * np.pi * F / 3e9 * np.pi
    z = mag * np.exp(1j * ph)
    if noise:
        z = z + noise * (rng.standard_normal(N)
                         + 1j * rng.standard_normal(N))
    return z


def test_constant_sweep_average_unchanged():
    avg = SweepAverager(N)
    s = _sweep(30.0, 0.8)
    for _ in range(7):
        avg.push(s)
    out = avg.push(s, avg_len=8)
    assert np.allclose(out, s, atol=1e-12)


def test_alternating_frames_average():
    avg = SweepAverager(N)
    a, b = _sweep(0.0, 1.0), _sweep(90.0, 1.0)
    avg.push(a)
    out = avg.push(b, avg_len=2)
    # mp 模式：|Z| 均 1，相对 a 的相位差均 45°（夹具自带随频率的公共底相）
    assert np.allclose(np.abs(out), 1.0, atol=1e-9)
    d = np.angle(out) - np.angle(a)
    assert np.allclose(np.unwrap(d), np.radians(45.0), atol=1e-9)


def test_avg_len_must_be_power_of_two():
    avg = SweepAverager(N)
    avg.push(_sweep())
    with pytest.raises(ValueError):
        avg.push(_sweep(), avg_len=3)


def test_avg_len_beyond_available_honest():
    avg = SweepAverager(N)
    avg.push(_sweep())
    with pytest.raises(ValueError):
        avg.average(avg_len=4)


def test_ring_wraparound_overwrites_oldest():
    avg = SweepAverager(N, max_depth=4)
    frames = [_sweep(float(i), 1.0) for i in range(6)]
    for f in frames:
        avg.push(f)
    assert len(avg) == 4
    # 最旧两帧（0、1）已被 4、5 挤出：回放 0 = 最新 = 帧 5
    assert np.allclose(avg.history(0), frames[5], atol=1e-12)
    assert np.allclose(avg.history(3), frames[2], atol=1e-12)


def test_history_bounds_honest():
    avg = SweepAverager(N)
    avg.push(_sweep())
    with pytest.raises(IndexError):
        avg.history(1)
    with pytest.raises(IndexError):
        avg.history(-1)


def test_iq_vs_mp_with_phase_drift():
    """相位漂移帧：mp 模式（幅度²+相位均值）与 iq 模式口径不同但各自稳定。"""
    avg_mp = SweepAverager(N, mode="mp")
    avg_iq = SweepAverager(N, mode="iq")
    drift = [_sweep(float(k * 5), 1.0) for k in range(8)]   # 相位线性漂移
    for fr in drift[:-1]:
        avg_mp.push(fr)
        avg_iq.push(fr)
    out_mp = avg_mp.push(drift[-1], avg_len=8)
    out_iq = avg_iq.push(drift[-1], avg_len=8)
    # mp：相对帧 0 的平均相位差 = 各帧漂移均值（8 帧线性漂移 5° -> 17.5°）
    d_mp = np.unwrap(np.angle(out_mp) - np.angle(drift[0]))
    assert np.allclose(d_mp, np.radians(17.5), atol=1e-9)
    # iq：向量均值相位 = 17.5° 但幅度因失相干 < 1
    assert np.all(np.abs(out_iq) < 1.0 - 1e-6)
    # mp 幅度不衰减（幅度²平均再开方）
    assert np.allclose(np.abs(out_mp), 1.0, atol=1e-9)


def test_noise_variance_reduction():
    """零均值复噪声平均 64 帧：与真值偏差应降到单帧噪声的 ~1/8。"""
    truth = _sweep(10.0, 0.7)
    avg = SweepAverager(N, mode="iq")
    for k in range(63):
        rng = np.random.default_rng(k)
        avg.push(truth + 0.05 * (rng.standard_normal(N)
                                 + 1j * rng.standard_normal(N)))
    rng = np.random.default_rng(63)
    out = avg.push(truth + 0.05 * (rng.standard_normal(N)
                                   + 1j * rng.standard_normal(N)),
                   avg_len=64)
    err = np.abs(out - truth)
    assert np.std(err) < 0.05 / 4.0          # 1/√64 理论，留 2x 余量


def test_shape_mismatch_honest():
    avg = SweepAverager(N)
    with pytest.raises(ValueError):
        avg.push(np.ones(N + 1))


def test_bad_mode_honest():
    with pytest.raises(ValueError):
        SweepAverager(N, mode="mag")
