# SPDX-License-Identifier: MIT
"""
test_dsp_demod_modes.py — deterministic per-mode demodulation tests.
非硬件 / NOT HARDWARE: synthetic signals, no UI / hardware.

Covers: AM envelope, USB/LSB separation, NFM discriminator + de-emphasis,
WFM stereo, CW BFO beat.  Targets mbdsdr_ai/demod_{am,ssb,nfm,wfm,cw}.py.
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.demod_am import DemodAM
from mbdsdr_ai.demod_ssb import DemodSSB
from mbdsdr_ai.demod_nfm import DemodNFM
from mbdsdr_ai.demod_wfm import DemodWFM
from mbdsdr_ai.demod_cw import DemodCW


def _bin_db(x, sr, f0, bw=80.0):
    sp = np.abs(np.fft.rfft(np.asarray(x, dtype=np.float64) * np.hanning(len(x))))
    fr = np.fft.rfftfreq(len(x), 1 / sr)
    m = (fr >= f0 - bw) & (fr <= f0 + bw)
    return 20.0 * np.log10(float(np.sqrt(np.mean(sp[m] ** 2))) + 1e-12)


# ── AM ──────────────────────────────────────────────────────────

def test_demod_am_envelope():
    """注入 m=0.5、1kHz 调制的 AM（载波在 0Hz），解调应含 1kHz 分量 > -3dB。"""
    sr = 15_000.0
    t = np.arange(int(sr * 0.5)) / sr
    # AM: (1 + m·cos(2π·1k·t))·e^{j0}，载波在基带 0Hz
    am = (1.0 + 0.5 * np.cos(2 * np.pi * 1000 * t)) * np.exp(1j * 2 * np.pi * 0 * t)
    d = DemodAM(if_sr=sr, bandwidth=10_000)
    audio = d.process(am)
    # 1kHz 分量应显著
    db = _bin_db(audio, sr, 1000)
    assert db > -3, f"AM 解调 1kHz 分量 {db:.1f}dB 不足"
    # 载波（0Hz）应被 DC blocker 抑制
    db_dc = _bin_db(audio, sr, 0, bw=30.0)
    assert db_dc < db - 20, f"AM 载波泄漏 {db_dc:.1f}dB 未抑制"


# ── USB / LSB 真区分 ─────────────────────────────────────────────

def test_demod_usb_vs_lsb_isolation():
    """USB 信号（上边带 1kHz）：USB 解调输出 1kHz > -3dB；LSB 解调 1kHz < -20dB。
    反之亦然。证明 USB/LSB 是相反 BFO 方向的真区分，不是改标签。"""
    sr = 24_000.0
    bw = 2_800.0
    t = np.arange(int(sr * 1.0)) / sr
    # Sideband geometry: USB occupies the lower half [-BW/2,0],
    # LSB occupies the upper half [0,BW/2].
    # USB 信号（音频 1kHz）：复音在 f_in = -BW/2 + 1000 = -400Hz
    usb_sig = np.exp(1j * 2 * np.pi * (-400) * t)
    # LSB 信号（音频 1kHz）：复音在 f_in = +BW/2 - 1000 = +400Hz
    lsb_sig = np.exp(1j * 2 * np.pi * (400) * t)

    usb_d = DemodSSB("usb", if_sr=sr, bandwidth=bw)
    lsb_d = DemodSSB("lsb", if_sr=sr, bandwidth=bw)

    # USB 信号
    au = usb_d.process(usb_sig)
    usb_d.reset()
    al = lsb_d.process(usb_sig)
    lsb_d.reset()
    db_usb_correct = _bin_db(au, sr, 1000)
    db_usb_wrong = _bin_db(al, sr, 1000)
    assert db_usb_correct > -3, f"USB 解调 USB 信号 1kHz {db_usb_correct:.1f}dB 不足"
    assert db_usb_wrong < -20, f"LSB 解调 USB 信号 1kHz {db_usb_wrong:.1f}dB 未抑制"

    # LSB 信号
    au2 = usb_d.process(lsb_sig)
    usb_d.reset()
    al2 = lsb_d.process(lsb_sig)
    db_lsb_wrong = _bin_db(au2, sr, 1000)
    db_lsb_correct = _bin_db(al2, sr, 1000)
    assert db_lsb_correct > -3, f"LSB 解调 LSB 信号 1kHz {db_lsb_correct:.1f}dB 不足"
    assert db_lsb_wrong < -20, f"USB 解调 LSB 信号 1kHz {db_lsb_wrong:.1f}dB 未抑制"


def test_demod_ssb_opposite_bfo_sign():
    """USB and LSB use the opposite BFO translation direction."""
    d_usb = DemodSSB("usb", if_sr=24000, bandwidth=2800)
    d_lsb = DemodSSB("lsb", if_sr=24000, bandwidth=2800)
    assert d_usb.translation == +2800.0 / 2.0
    assert d_lsb.translation == -2800.0 / 2.0
    assert d_usb.translation == -d_lsb.translation


# ── NFM ─────────────────────────────────────────────────────────

def test_demod_nfm_discriminator():
    """注入 1kHz 调制、频偏 5kHz 的 NFM，解调应含 1kHz 分量。"""
    sr = 50_000.0
    t = np.arange(int(sr * 0.5)) / sr
    iq = np.exp(1j * 2 * np.pi * 5000 * np.cumsum(np.cos(2 * np.pi * 1000 * t)) / sr)
    d = DemodNFM(if_sr=sr, bandwidth=12_500, deemph_tau=0)
    audio = d.process(iq)
    db = _bin_db(audio, sr, 1000)
    assert db > -3, f"NFM 解调 1kHz {db:.1f}dB 不足"


def test_demod_nfm_deemphasis_attenuates_high():
    """去加重 IIR 应衰减高频（50μs 在 1kHz vs 5kHz 增益比）。"""
    sr = 50_000.0
    t = np.arange(int(sr * 0.5)) / sr
    # 两个单音：1kHz 和 5kHz
    iq1 = np.exp(1j * 2 * np.pi * 5000 * np.cumsum(np.cos(2 * np.pi * 1000 * t)) / sr)
    iq5 = np.exp(1j * 2 * np.pi * 5000 * np.cumsum(np.cos(2 * np.pi * 5000 * t)) / sr)
    d = DemodNFM(if_sr=sr, bandwidth=12_500, deemph_tau=50e-6)
    a1 = d.process(iq1)
    d.reset()
    a5 = d.process(iq5)
    db1 = _bin_db(a1, sr, 1000)
    db5 = _bin_db(a5, sr, 5000)
    # 去加重应使 5kHz 相对 1kHz 衰减（低频保留、高频滚降）
    assert db5 < db1 - 5, f"去加重未衰减高频：1k={db1:.1f}, 5k={db5:.1f}"


# ── WFM 真立体声 ─────────────────────────────────────────────────

def test_demod_wfm_stereo_separation():
    """注入 L=1kHz, R=2kHz, pilot 19kHz 的立体声 WFM。
    左声道 1kHz > -3dB 且 2kHz < -20dB；右声道反之。"""
    sr = 250_000.0
    t = np.arange(int(sr * 1.0)) / sr
    L = 0.45 * np.cos(2 * np.pi * 1000 * t)
    R = 0.45 * np.cos(2 * np.pi * 2000 * t)
    mpx = (0.5 * (L + R)
           + 0.1 * np.cos(2 * np.pi * 19_000 * t)
           + 0.5 * (L - R) * np.cos(2 * np.pi * 38_000 * t))
    iq = np.exp(1j * 2 * np.pi * 75_000 * np.cumsum(mpx) / sr)
    d = DemodWFM(if_sr=sr, bandwidth=150_000, stereo=True, deemph_tau=0)
    l, r = d.process(iq)
    # 左声道：1kHz 强，2kHz 抑制
    assert _bin_db(l, sr, 1000) > -3, f"WFM L@1k {_bin_db(l, sr, 1000):.1f}dB 不足"
    assert _bin_db(l, sr, 2000) < -20, f"WFM L@2k {_bin_db(l, sr, 2000):.1f}dB 未抑制"
    # 右声道：2kHz 强，1kHz 抑制
    assert _bin_db(r, sr, 2000) > -3, f"WFM R@2k {_bin_db(r, sr, 2000):.1f}dB 不足"
    assert _bin_db(r, sr, 1000) < -20, f"WFM R@1k {_bin_db(r, sr, 1000):.1f}dB 未抑制"


# ── CW ───────────────────────────────────────────────────────────

def test_demod_cw_beat_tone():
    """注入零频 CW 载波，BFO=700Hz 差拍应输出 700Hz 音调。"""
    sr = 3_000.0
    t = np.arange(int(sr * 1.0)) / sr
    cw = np.exp(1j * 2 * np.pi * 0 * t)  # 载波在 0Hz
    d = DemodCW(if_sr=sr, tone=700)
    audio = d.process(cw)
    db = _bin_db(audio, sr, 700, bw=40.0)
    assert db > -3, f"CW 差拍 700Hz {db:.1f}dB 不足"
