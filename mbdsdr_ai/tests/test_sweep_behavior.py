# SPDX-License-Identifier: MIT
"""sweep 行为测试（宽带扫频/活动扫描，合成 IQ，无硬件）。

oracle 来源（读码语义）：
- _segment_psd：Welch 平均周期图，功率归一到 (win_power·sr)；
- sweep_scan：阈值 = 带内中位数 + margin_db（或绝对 threshold_db）；
  调谐中心使第一段左缘覆盖 f_start、最后一段覆盖 f_stop；
- _extract_activities：超门限连续段按 merge_gap 合并，带宽 < min_bw_hz 丢弃，
  按 peak_db 降序；带宽 = 段内首个与末个超门限 bin 的频率差。
- 单 bin 输入是修复后的边界（原 freqs[1] 越界 IndexError）。
"""
import numpy as np
import pytest

from mbdsdr_ai.sweep import SweepResult, _extract_activities, _segment_psd, sweep_scan

SR = 1e6
FFT = 2048
BIN_HZ = SR / FFT


def _tone_acq(freqs_offset, amps, noise=0.01, seed=7):
    """确定性合成采集器：若干相对中心的单音 + 高斯噪声。"""
    rng = np.random.default_rng(seed)

    def acquire(center, srate, n):
        t = np.arange(n) / srate
        x = np.zeros(n, dtype=np.complex128)
        for f, a in zip(freqs_offset, amps):
            x += a * np.exp(2j * np.pi * f * t)
        return x + noise * (rng.standard_normal(n) + 1j * rng.standard_normal(n))

    return acquire


# ── _segment_psd ───────────────────────────────────────────

def test_segment_psd_tone_peak_at_expected_bin():
    """纯单音的 PSD 峰落在其频偏处，且峰显著高于其它 bin。"""
    n = 8192
    t = np.arange(n) / SR
    iq = 0.5 * np.exp(2j * np.pi * 100_000.0 * t)
    foff, pdb = _segment_psd(iq, SR, fft_size=FFT)
    peak_idx = int(np.argmax(pdb))
    assert abs(foff[peak_idx] - 100_000.0) <= BIN_HZ
    # 单音能量集中：峰值 PSD 比中位数高得多
    assert pdb[peak_idx] - np.median(pdb) > 30.0


def test_segment_psd_short_input_falls_back_to_smaller_fft():
    """样本数不足一个 FFT 帧时降级 FFT 长度并补零，不崩不空。
    降级语义：fft_size = max(64, 2^floor(log2(n)))，n=300 → 256。"""
    n = 300
    t = np.arange(n) / SR
    iq = 0.5 * np.exp(2j * np.pi * 50_000.0 * t)
    foff, pdb = _segment_psd(iq, SR, fft_size=FFT)
    assert len(foff) == len(pdb) == 256
    peak_idx = int(np.argmax(pdb))
    assert abs(foff[peak_idx] - 50_000.0) <= SR / 256


# ── sweep_scan：检出与几何 ─────────────────────────────────

def test_sweep_detects_two_tones_as_two_activities():
    """两个相距 >merge_gap 的单音 → 两个活动段，中心落在各自音附近。"""
    acq = _tone_acq([20_000.0, 40_000.0], [0.5, 0.5])
    r = sweep_scan(acq, 100e6 - 100e3, 100e6 + 100e3, SR,
                   fft_size=FFT, dwell_samples=8192, margin_db=12.0, min_bw_hz=1e3)
    assert len(r.activities) == 2
    centers = sorted(a.center_hz for a in r.activities)
    assert abs(centers[0] - (100e6 + 20_000.0)) < 3 * BIN_HZ
    assert abs(centers[1] - (100e6 + 40_000.0)) < 3 * BIN_HZ
    # 活动段排序按 peak_db 降序
    peaks = [a.peak_db for a in r.activities]
    assert peaks == sorted(peaks, reverse=True)
    assert r.threshold_db == pytest.approx(r.noise_floor_db + 12.0, abs=0.5)


def test_sweep_merge_gap_combines_nearby_tones():
    """merge_gap 大于两音间隔时合并为一个活动段。"""
    acq = _tone_acq([20_000.0, 40_000.0], [0.5, 0.5])
    r = sweep_scan(acq, 100e6 - 100e3, 100e6 + 100e3, SR,
                   fft_size=FFT, dwell_samples=8192, margin_db=12.0, min_bw_hz=1e3,
                   merge_gap_hz=25_000.0)
    assert len(r.activities) == 1
    a = r.activities[0]
    assert a.bandwidth_hz > 15_000.0             # 覆盖两个音的跨度
    assert 100e6 + 15_000.0 < a.center_hz < 100e6 + 45_000.0


def test_sweep_min_bw_drops_isolated_glitch():
    """min_bw_hz 大于单 bin 带宽时孤立尖峰被丢弃。"""
    acq = _tone_acq([20_000.0], [0.5])
    r = sweep_scan(acq, 100e6 - 100e3, 100e6 + 100e3, SR,
                   fft_size=FFT, dwell_samples=8192, margin_db=12.0,
                   min_bw_hz=1e9)                 # 任何段都小于 1GHz
    assert r.activities == []


def test_sweep_absolute_threshold_overrides_noise_floor():
    """threshold_db 给定时用绝对门限，不再按中位数+margin。"""
    acq = _tone_acq([20_000.0], [0.5], seed=11)
    r = sweep_scan(acq, 100e6 - 100e3, 100e6 + 100e3, SR,
                   fft_size=FFT, dwell_samples=8192, margin_db=30.0,
                   threshold_db=-200.0, min_bw_hz=1.0)
    assert r.threshold_db == -200.0
    assert len(r.activities) >= 1                 # 超低绝对门限必检出


def test_sweep_noise_only_no_activity():
    """纯噪声 + 宽 margin：无活动段，噪声底为有限值。"""
    rng = np.random.default_rng(3)

    def acq(center, srate, n):
        return 0.01 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))

    r = sweep_scan(acq, 100e6 - 200e3, 100e6 + 200e3, SR,
                   fft_size=FFT, dwell_samples=8192, margin_db=25.0)
    assert r.activities == []
    assert np.isfinite(r.noise_floor_db)
    assert r.threshold_db == pytest.approx(r.noise_floor_db + 25.0, abs=0.5)


def test_sweep_reversed_range_swapped():
    """f_stop < f_start 自动交换，仍覆盖同一频段。（音不放 DC：_segment_psd 会去均值）"""
    acq = _tone_acq([20_000.0], [0.5], seed=5)
    r = sweep_scan(acq, 100e6 + 100e3, 100e6 - 100e3, SR,
                   fft_size=FFT, dwell_samples=8192, margin_db=12.0, min_bw_hz=1.0)
    assert r.centers_scanned == sorted(r.centers_scanned)
    assert len(r.activities) == 1


# ── 边界：单 bin / 空（修复回归钉） ────────────────────────

def test_extract_activities_single_bin_above_threshold():
    """单 bin 超门限：修复前 freqs[1] 越界 IndexError；现应返回空（bw=0 < min_bw）。"""
    acts = _extract_activities(np.array([100e6]), np.array([50.0]),
                               threshold_db=0.0, merge_gap_hz=2.0, min_bw_hz=1e3)
    assert acts == []


def test_extract_activities_single_bin_small_min_bw():
    """单 bin + min_bw=0：产生 0 带宽活动段（low==high）。"""
    acts = _extract_activities(np.array([100e6]), np.array([50.0]),
                               threshold_db=0.0, merge_gap_hz=2.0, min_bw_hz=0.0)
    assert len(acts) == 1
    assert acts[0].low_hz == acts[0].high_hz == 100e6
    assert acts[0].bandwidth_hz == 0.0
    assert acts[0].peak_db == 50.0


def test_extract_activities_empty_input():
    """空频轴 → 空活动列表，不崩。"""
    assert _extract_activities(np.array([]), np.array([]), 0.0, 2.0, 1e3) == []


def test_sweep_zero_width_range_runs():
    """f_start == f_stop（零宽范围）不崩，结果结构完整。"""
    acq = _tone_acq([20_000.0], [0.5], seed=5)
    r = sweep_scan(acq, 100e6, 100e6, SR, fft_size=FFT, dwell_samples=8192)
    assert isinstance(r, SweepResult)
    assert len(r.freqs_hz) == len(r.power_db)


# ── 序列化 ──────────────────────────────────────────────────

def test_sweep_result_to_dict_downsamples():
    acq = _tone_acq([20_000.0], [0.5], seed=9)
    r = sweep_scan(acq, 100e6 - 100e3, 100e6 + 100e3, SR,
                   fft_size=FFT, dwell_samples=8192, margin_db=12.0, min_bw_hz=1.0)
    d = r.to_dict(max_points=64)
    assert len(d["freqs_mhz"]) == 64 == len(d["power_db"])
    assert isinstance(d["activities"], list)
    if d["activities"]:
        assert set(d["activities"][0]) == {
            "center_hz", "center_mhz", "bandwidth_hz", "peak_db", "mean_db",
            "low_mhz", "high_mhz",
        }
