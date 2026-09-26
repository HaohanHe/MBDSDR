"""
新时空面板 AMR 实时流分析器单元测试
====================================

纯离线合成信号测试：用固定 seed 的复基带 IQ 喂 AMRStreamAnalyzer，
验证信号检测、标签映射、USB/LSB 区分与频谱特征。不接硬件、不接 UI。
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.amr import synthesize_modulation_iq
from mbdsdr_ai.new_spacetime_amr import AMRStreamAnalyzer, AMRStreamResult


FS = 100_000.0
N = 16384


def _analyzer():
    return AMRStreamAnalyzer(snr_threshold_db=6.0, min_bandwidth_hz=100.0)


def _tone_band(center_lo, center_hi, n_tones=30, n=N, fs=FS):
    """构造一段能量集中在 [center_lo, center_hi] Hz 的窄带复信号（叠加噪声）。"""
    t = np.arange(n) / fs
    sig = np.zeros(n, dtype=complex)
    for f in np.linspace(center_lo, center_hi, n_tones):
        sig += np.exp(1j * 2 * np.pi * f * t)
    sig = sig / np.sqrt(np.mean(np.abs(sig) ** 2))
    rng = np.random.default_rng(2026)
    noise_p = 10 ** (-25.0 / 10.0)
    w = np.sqrt(noise_p / 2.0) * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    return (sig + w).astype(np.complex128)


# ── 1. 纯噪声必须判为未检测到信号 ──────────────────────────────
def test_noise_returns_no_signal():
    an = _analyzer()
    for seed in (42, 7, 2026):
        rng = np.random.default_rng(seed)
        noise = synthesize_modulation_iq("NOISE", rng, fs=FS, n=N)
        res = an.analyze_iq(noise, FS)
        assert res.signal_detected is False
        assert res.modulation == "未检测到信号"
        assert res.confidence == 0.0
        assert res.bandwidth_hz == 0.0
        assert res.peak_count == 0


# ── 2. FM ────────────────────────────────────────────────────
def test_fm_classified_correctly():
    an = _analyzer()
    rng = np.random.default_rng(11)
    iq = synthesize_modulation_iq("FM", rng, snr_db=25, fs=FS, n=N)
    res = an.analyze_iq(iq, FS)
    assert res.signal_detected is True
    assert res.modulation == "FM"
    assert res.raw_label == "FM"


# ── 3. AM ────────────────────────────────────────────────────
def test_am_classified_correctly():
    an = _analyzer()
    rng = np.random.default_rng(12)
    iq = synthesize_modulation_iq("AM", rng, snr_db=25, fs=FS, n=N)
    res = an.analyze_iq(iq, FS)
    assert res.signal_detected is True
    assert res.modulation == "AM"
    assert res.raw_label == "AM"


# ── 4. CW 纯载波 ─────────────────────────────────────────────
def test_cw_classified_correctly():
    an = _analyzer()
    rng = np.random.default_rng(13)
    iq = synthesize_modulation_iq("CW", rng, snr_db=30, fs=FS, n=N)
    res = an.analyze_iq(iq, FS)
    assert res.signal_detected is True
    assert res.modulation == "CW"
    assert res.raw_label == "CW"


# ── 5. 数字调制映射为 "数字" ────────────────────────────────
def test_digital_mapped():
    an = _analyzer()
    rng = np.random.default_rng(14)
    iq = synthesize_modulation_iq("FSK", rng, snr_db=25, fs=FS, n=N)
    res = an.analyze_iq(iq, FS)
    assert res.signal_detected is True
    assert res.modulation == "数字"
    assert res.raw_label in ("FSK", "PSK", "QAM", "OFDM")


# ── 6. USB / LSB 区分 ────────────────────────────────────────
def test_usb_lsb_distinction():
    an = _analyzer()
    # 上边带：能量集中在正频率侧
    usb_iq = _tone_band(1000.0, 3000.0)
    res_usb = an.analyze_iq(usb_iq, FS)
    assert res_usb.signal_detected is True
    assert res_usb.modulation == "USB"

    # 下边带：能量集中在负频率侧
    lsb_iq = _tone_band(-3000.0, -1000.0)
    res_lsb = an.analyze_iq(lsb_iq, FS)
    assert res_lsb.signal_detected is True
    assert res_lsb.modulation == "LSB"


# ── 7. 仅频谱路径：平坦频谱判未检测到信号 ────────────────────
def test_spectrum_analysis_no_signal():
    an = _analyzer()
    bins = 2048
    freqs = np.linspace(-FS / 2.0, FS / 2.0, bins)
    flat_db = np.full(bins, -40.0)  # 完全平坦，无任何峰
    res = an.analyze_spectrum(flat_db, freqs)
    assert res.signal_detected is False
    assert res.modulation == "未检测到信号"
    assert res.bandwidth_hz == 0.0
    assert res.peak_count == 0


# ── 8. 已知带宽信号：带宽合理、至少一个峰 ─────────────────────
def test_bandwidth_peak_count():
    an = _analyzer()
    # 已知占据约 4 kHz 带宽（-2kHz ~ +2kHz）
    iq = _tone_band(-2000.0, 2000.0)
    res = an.analyze_iq(iq, FS)
    assert res.signal_detected is True
    assert res.peak_count >= 1
    # 平滑后 -3dB 带宽应落在 2kHz ~ 6kHz 的合理区间
    assert 2000.0 <= res.bandwidth_hz <= 6000.0
    # 中心偏移应接近 0（关于 DC 对称放置）
    assert abs(res.center_offset_hz) < 500.0
