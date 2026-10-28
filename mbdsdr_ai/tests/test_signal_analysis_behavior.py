# SPDX-License-Identifier: MIT
"""signal_analysis 行为测试（合成频谱/合成 IQ，无硬件）。

oracle 来源（读码语义）：
- detect_signals：噪声底=median(spectrum)，超 noise+threshold 的连续段，
  段带宽 = freqs[末]-freqs[首]，< min_bw 丢弃；峰值取段内 argmax；
  段延伸到数组尾时 stop_hz = freqs[-1]；
- identify_modulation：按 (amp_cv, freq_std) 阈值树判决
  AM: cv>0.3 且 fstd<0.2；FM: cv<0.1 且 0.05<fstd<0.8；
  SSB: 0.1<cv<0.3 且 fstd>0.05；QPSK/BPSK: cv<0.05 且 fstd>0.8 + 相位直方图；
- estimate_ber：到最近参考点距离 > mean(dists)*0.5 计错；
- extract_spectrum_features：-3dB 带宽=超 peak-3 的首末频差；
  papr_db = dynamic_range_db = peak - median（码内同源，钉住现状）；
- detect_interference：窄带(bw<10k 且 peak>20) / 宽带(bw>1M 且 peak>15)。
"""
import numpy as np
import pytest

from mbdsdr_ai.signal_analysis import (
    detect_interference,
    detect_signals,
    estimate_ber,
    extract_spectrum_features,
    identify_modulation,
)


# ── detect_signals ──────────────────────────────────────────

def _flat_spectrum(n=501, fmax=1e6, floor=-80.0):
    return np.full(n, floor), np.linspace(0, fmax, n)


def test_detect_signals_finds_segment_with_bandwidth():
    spec, freqs = _flat_spectrum()
    spec[100:110] = -50.0                      # 200k-220k，bin=2kHz
    sigs = detect_signals(spec, freqs, threshold_db=10.0, min_bw=1e3)
    assert len(sigs) == 1
    s = sigs[0]
    assert s["start_hz"] == pytest.approx(200_000.0)
    assert s["stop_hz"] == pytest.approx(218_000.0)
    assert s["bandwidth_hz"] == pytest.approx(18_000.0)
    assert s["peak_power_db"] == -50.0
    assert s["center_freq_hz"] == pytest.approx(200_000.0)   # 平顶段 argmax 取首个


def test_detect_signals_min_bw_filters_glitch():
    """单 bin 尖峰带宽为 0，被 min_bw=1k 丢弃；同谱上宽带段保留。"""
    spec, freqs = _flat_spectrum()
    spec[300] = -40.0                          # 600k 单 bin
    spec[100:110] = -50.0
    sigs = detect_signals(spec, freqs, threshold_db=10.0, min_bw=1e3)
    assert [s["start_hz"] for s in sigs] == [200_000.0]
    # min_bw=0 时单 bin 也收（带宽 0）
    sigs0 = detect_signals(spec, freqs, threshold_db=10.0, min_bw=0.0)
    assert len(sigs0) == 2
    assert sorted(s["center_freq_hz"] for s in sigs0) == [200_000.0, 600_000.0]


def test_detect_signals_tail_segment_included():
    """超门限段延伸到数组尾：走收尾分支，stop=freqs[-1]。"""
    spec, freqs = _flat_spectrum()
    spec[400:501] = -50.0
    sigs = detect_signals(spec, freqs, threshold_db=10.0, min_bw=1e3)
    assert len(sigs) == 1
    assert sigs[0]["stop_hz"] == pytest.approx(1e6)
    assert sigs[0]["start_hz"] == pytest.approx(800_000.0)


def test_detect_signals_noise_only_empty():
    """均匀底噪无超门限段 → 空列表。"""
    spec, freqs = _flat_spectrum()
    assert detect_signals(spec, freqs, threshold_db=10.0, min_bw=1e3) == []


# ── identify_modulation ─────────────────────────────────────

def test_identify_am_modulation():
    t = np.arange(4800) / 48000.0
    am = (1.0 + 0.8 * np.sin(2 * np.pi * 100 * t)) * np.exp(1j * 2 * np.pi * 1000 * t)
    r = identify_modulation(am, 48000.0)
    assert r["modulation"] == "AM"
    assert r["features"]["amplitude_cv"] > 0.3
    assert r["features"]["freq_std"] < 0.2
    assert 0.0 < r["confidence"] <= 0.9


def test_identify_fm_modulation():
    t = np.arange(4800) / 48000.0
    fm = np.exp(1j * 40.0 * np.sin(2 * np.pi * 80 * t))   # 大调制指数 → freq_std∈(0.05,0.8)
    r = identify_modulation(fm, 48000.0)
    assert r["modulation"] == "FM"
    assert r["features"]["amplitude_cv"] < 0.1
    assert 0.05 < r["features"]["freq_std"] < 0.8


def test_identify_carrier_unknown():
    """未调载波：恒幅 + 零频偏，规则树不命中 → unknown（诚实不硬猜）。"""
    t = np.arange(4800) / 48000.0
    cw = np.exp(1j * 2 * np.pi * 1000 * t)
    r = identify_modulation(cw, 48000.0)
    assert r["modulation"] == "unknown"
    assert r["confidence"] == 0.0


def test_identify_qpsk_and_bpsk():
    """恒幅 + 离散相位跳变：直方图 3+ 峰→QPSK，1-2 峰→BPSK。"""
    rng = np.random.default_rng(0)
    sym = np.array([1 + 1j, -1 + 1j, -1 - 1j, 1 - 1j]) / np.sqrt(2)
    bits = rng.integers(0, 4, 600)
    qpsk = np.repeat(sym[bits], 2)            # sps=2 → freq_std>0.8
    r = identify_modulation(qpsk, 48000.0)
    assert r["modulation"] == "QPSK"
    assert r["features"]["amplitude_cv"] < 0.05

    bpsk = np.repeat(np.where(rng.integers(0, 2, 600) == 0, 1.0, -1.0), 2)
    r2 = identify_modulation(bpsk.astype(complex), 48000.0)
    assert r2["modulation"] == "BPSK"


# ── estimate_ber ────────────────────────────────────────────

REF = np.array([1 + 1j, -1 + 1j, -1 - 1j, 1 - 1j]) / np.sqrt(2)


def test_estimate_ber_clean_and_noisy():
    clean = REF[[0, 1, 2, 3, 0, 1]]
    assert estimate_ber(clean, REF) == 0.0
    rng = np.random.default_rng(1)
    noisy = clean + 0.01 * (rng.standard_normal(6) + 1j * rng.standard_normal(6))
    assert estimate_ber(noisy, REF) == 0.0     # 小噪声在判决域内


def test_estimate_ber_far_points_all_wrong():
    far = REF * 3.0                            # 全部远离参考星座
    assert estimate_ber(far, REF) == 1.0
    mixed = np.concatenate([REF[:2], (REF * 3.0)[:2]])
    assert estimate_ber(mixed, REF) == pytest.approx(0.5)


def test_estimate_ber_empty_raises():
    """空星座/空参考点：显式 ValueError（修复前 ZeroDivisionError）。"""
    with pytest.raises(ValueError):
        estimate_ber(np.array([]), REF)
    with pytest.raises(ValueError):
        estimate_ber(REF, np.array([]))


# ── extract_spectrum_features ───────────────────────────────

def test_extract_spectrum_features_peak_and_bw():
    freqs = np.linspace(0, 1e6, 1001)          # 1kHz/bin
    spec = np.full(1001, -80.0)
    spec[480:521] = -50.0
    spec[500] = -30.0                          # 尖峰在 500kHz
    f = extract_spectrum_features(spec, freqs)
    assert f["center_freq_hz"] == pytest.approx(500_000.0)
    assert f["peak_power_db"] == -30.0
    assert f["noise_floor_db"] == -80.0
    # -3dB 带宽：只有峰值 bin 超过 -33，首末同 bin → 0
    assert f["bandwidth_hz"] == 0.0
    assert f["papr_db"] == pytest.approx(50.0)
    assert f["dynamic_range_db"] == pytest.approx(50.0)


def test_extract_spectrum_features_wide_peak():
    freqs = np.linspace(0, 1e6, 1001)
    spec = np.full(1001, -80.0)
    spec[400:601] = -30.0                      # 平顶 200kHz 宽
    f = extract_spectrum_features(spec, freqs)
    assert f["bandwidth_hz"] == pytest.approx(200_000.0)
    assert f["center_freq_hz"] == pytest.approx(400_000.0)  # 平顶 argmax 取首


# ── detect_interference ─────────────────────────────────────

def test_detect_interference_narrowband():
    freqs = np.linspace(0, 1e6, 1001)          # 1kHz/bin
    spec = np.full(1001, -80.0)
    spec[300:304] = 25.0                       # 3kHz 宽、25dB 峰
    out = detect_interference(spec, freqs)
    assert len(out) == 1
    assert out[0]["type"] == "narrowband"
    assert out[0]["bandwidth_hz"] == pytest.approx(3000.0)
    assert out[0]["confidence"] == 0.8


def test_detect_interference_broadband():
    freqs = np.linspace(0, 10e6, 1001)         # 10kHz/bin
    spec = np.full(1001, -80.0)
    spec[100:300] = 16.0                       # 2MHz 宽、16dB 峰
    out = detect_interference(spec, freqs)
    assert len(out) == 1
    assert out[0]["type"] == "broadband"
    assert out[0]["bandwidth_hz"] == pytest.approx(1_990_000.0)
    assert out[0]["confidence"] == 0.6


def test_detect_interference_midband_not_flagged():
    """800kHz 宽 / 16dB：既不窄带也不宽带 → 不误报。"""
    freqs = np.linspace(0, 1e6, 1001)
    spec = np.full(1001, -80.0)
    spec[100:900] = 16.0
    assert detect_interference(spec, freqs) == []
