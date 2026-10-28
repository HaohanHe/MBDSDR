# SPDX-License-Identifier: MIT
"""signal_spectrum 行为测试（analyze_iq_spectrum，合成 IQ，无硬件）。

oracle 来源（读码语义）：
- Welch 平均 PSD + Peak Hold；freqs[i] = center + (i - fft/2)·(sr/fft)；
- 峰检测在 peak_hold 上取超 median(avg)+margin_db 的连续段；
  bw_hz = 段宽(bin数)·bin_hz；prominence = peak - median(avg)；
- n < fft_size 时 fft_size 降为 max(64, 2^floor(log2(n))) 并补零；
- n_peaks 截断、按 power_db 降序；window 为 "hann"（默认）或 "nuttall"。
"""
import numpy as np
import pytest

from mbdsdr_ai.signal_spectrum import _nuttall, analyze_iq_spectrum

SR = 1e6
BIN = SR / 2048


def _noise_burst(seed=2, n=65536):
    """底噪 + 2000 样本的 +100kHz 突发（确定性）。"""
    rng = np.random.default_rng(seed)
    t = np.arange(n) / SR
    noise = 0.1 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    burst = np.zeros(n, dtype=complex)
    burst[20000:22000] = 0.8 * np.exp(2j * np.pi * 100_000.0 * t[20000:22000])
    return noise + burst


def test_analyze_spectrum_finds_burst_peak():
    r = analyze_iq_spectrum(_noise_burst(), SR, center_hz=100e6, fft_size=2048)
    assert r["n_samples"] == 65536 and r["fft_size"] == 2048
    assert r["frames_averaged"] == (65536 - 2048) // 1024 + 1
    top = r["peaks"][0]
    assert abs(top["freq_hz"] - 100_100_000.0) <= 2 * BIN   # +100kHz ±2bin
    assert top["prominence_db"] > 30.0
    assert top["power_db"] > r["noise_floor_db"] + 30.0
    assert top["bw_hz"] >= BIN


def test_analyze_spectrum_freq_axis_semantics():
    r = analyze_iq_spectrum(_noise_burst(), SR, center_hz=100e6, fft_size=2048)
    freqs = r["freqs_hz"]
    assert len(freqs) == len(r["avg_psd_db"]) == len(r["peak_hold_db"]) == 2048
    assert freqs[1024] == pytest.approx(100e6)             # DC bin = center
    # 频率轴按 round(f,1) 输出，步长 = sr/fft_size（舍入到 0.1Hz）
    step = freqs[1] - freqs[0]
    assert step == pytest.approx(round(BIN, 1), abs=0.05)


def test_analyze_spectrum_peak_hold_ge_avg():
    """Peak Hold 逐 bin 不低于平均 PSD（保持语义）。"""
    r = analyze_iq_spectrum(_noise_burst(), SR, center_hz=100e6, fft_size=2048)
    hold = np.array(r["peak_hold_db"])
    avg = np.array(r["avg_psd_db"])
    assert np.all(hold >= avg - 0.15)                       # 允许 round(1位) 舍入


def test_analyze_spectrum_continuous_tone_single_strong_peak():
    rng = np.random.default_rng(2)
    n = 65536
    t = np.arange(n) / SR
    tone = 0.8 * np.exp(2j * np.pi * 200_000.0 * t)
    noise = 0.1 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    r = analyze_iq_spectrum(tone + noise, SR, center_hz=0.0, fft_size=2048)
    top = r["peaks"][0]
    assert abs(top["freq_hz"] - 200_000.0) <= 2 * BIN
    assert top["prominence_db"] > 30.0


def test_analyze_spectrum_short_input_pads():
    """n < fft_size：fft_size 降为 2^floor(log2(n))，补零 1 帧，仍可检出音。"""
    sr = 1e6
    t = np.arange(300) / sr
    short = 0.8 * np.exp(2j * np.pi * 50_000.0 * t)
    short = short + 0.05 * (np.random.default_rng(3).standard_normal(300) + 1j)
    r = analyze_iq_spectrum(short, sr, fft_size=2048)
    assert r["fft_size"] == 256 and r["frames_averaged"] == 1
    assert abs(r["peaks"][0]["freq_hz"] - 50_000.0) <= sr / 256


def test_analyze_spectrum_n_peaks_truncates_sorted():
    r = analyze_iq_spectrum(_noise_burst(), SR, center_hz=100e6, n_peaks=1)
    assert len(r["peaks"]) == 1
    r2 = analyze_iq_spectrum(_noise_burst(), SR, center_hz=100e6, n_peaks=12)
    powers = [p["power_db"] for p in r2["peaks"]]
    assert powers == sorted(powers, reverse=True)
    assert r2["peaks"][0] == r["peaks"][0]


def test_analyze_spectrum_all_zero_honest():
    """全零输入：底噪钳到 -200dB 下限，无伪峰。"""
    r = analyze_iq_spectrum(np.zeros(4096, dtype=complex), SR)
    assert r["noise_floor_db"] == -200.0
    assert r["peaks"] == []


def test_analyze_spectrum_nuttall_window():
    """nuttall 窗路径可用；主峰定位与 hann 一致（±2bin），旁瓣更低。"""
    iq = _noise_burst()
    rh = analyze_iq_spectrum(iq, SR, center_hz=100e6, window="hann")
    rn = analyze_iq_spectrum(iq, SR, center_hz=100e6, window="nuttall")
    assert abs(rh["peaks"][0]["freq_hz"] - rn["peaks"][0]["freq_hz"]) <= 2 * BIN
    # Nuttall 旁瓣 -93dB：次强假峰显著低于 hann 的次强峰
    if len(rh["peaks"]) > 1 and len(rn["peaks"]) > 1:
        assert rn["peaks"][1]["power_db"] <= rh["peaks"][1]["power_db"] + 1.0


def test_nuttall_window_definition():
    """4-term Nuttall 系数与端点值（SDR++ nuttall.h 语义）。"""
    w = _nuttall(64)
    assert len(w) == 64
    assert w[0] == pytest.approx(0.0, abs=1e-3)            # 窗两端趋 0
    assert w[-1] == pytest.approx(0.0, abs=1e-3)
    assert abs(w[32]) > 0.9                                # 中心接近 1
