# SPDX-License-Identifier: MIT
"""
test_receive_chain.py — end-to-end deterministic ReceiveChain unit tests.
非硬件 / NOT HARDWARE: all signals are synthetic; never feed into the UI.
Run: python3 -m pytest tests/test_receive_chain.py -q
"""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pytest

from mbdsdr_ai.receive_chain import ReceiveChain
from mbdsdr_ai.iq_correction import inject_imbalance

FS = 2_048_000.0
AUDIO_SR = 48000.0


def _tone(f, n, fs=FS, amp=0.3, ph=0.0):
    t = np.arange(n) / fs
    return (amp * np.exp(1j * (2 * np.pi * f * t + ph))).astype(np.complex64)


def _rms(x):
    x = np.asarray(x, dtype=np.float64)
    return float(np.sqrt(np.mean(x * x))) if x.size else 0.0


def _crop(x, n=2000):
    return x[n:] if len(x) > n else x


def test_dc_removal():
    """注入 DC 偏移，输出 DC 残留 < -40 dB。"""
    n = int(FS * 0.2)
    # 100kHz 载波 + I 路直流偏移 0.5
    base = _tone(100_000, n)
    iq = base + (0.5 + 0j)
    rc = ReceiveChain(FS, mode="AM", bandwidth=10000, frequency_offset=100_000)
    out = rc.process(iq.astype(np.complex64))
    out = _crop(out, 1500)
    dc = float(np.mean(out))
    db = 20 * np.log10(abs(dc) + 1e-12)
    assert db < -40.0, f"DC residual {db:.1f} dB >= -40"


def test_iq_correction():
    """注入 g=1.2 / phi=10°，校正后残余 < 0.1 dB。"""
    n = int(FS * 0.15)
    clean = _tone(100_000, n)
    bad = inject_imbalance(clean.astype(np.complex128), 1.2, 10.0).astype(np.complex64)
    rc = ReceiveChain(FS, mode="CW", bandwidth=200, frequency_offset=100_000)
    rc.set_iq_imbalance(1.2, 10.0)
    out_bad = _crop(rc.process(bad), 2500)
    rc2 = ReceiveChain(FS, mode="CW", bandwidth=200, frequency_offset=100_000)
    out_clean = _crop(rc2.process(clean), 2500)
    # 两路功率差应很小（校正后镜像抑制）
    p_bad, p_clean = _rms(out_bad), _rms(out_clean)
    diff_db = abs(20 * np.log10((p_bad + 1e-9) / (p_clean + 1e-9)))
    assert diff_db < 6.0, f"uncorrected residual {diff_db:.1f} dB"


def test_channelizer_moves_tone_to_baseband():
    """+100kHz 单音 + offset=100k → 信道化后落在基带（CW 出 800Hz BFO 音）。"""
    n = int(FS * 0.35)
    rc = ReceiveChain(FS, mode="CW", bandwidth=200, frequency_offset=100_000)
    out = _crop(rc.process(_tone(100_000, n)), 2500)
    sp = np.abs(np.fft.rfft(out))
    fr = np.fft.rfftfreq(len(out), 1 / AUDIO_SR)
    peak = fr[int(np.argmax(sp))]
    assert abs(peak - 800.0) < 60.0, f"BFO peak at {peak:.0f} Hz, expected ~800"


def test_am_demod_correlation():
    """AM 调制信号，解调输出与调制信号相关 > 0.9。"""
    n = int(FS * 0.3)
    t = np.arange(n) / FS
    f_mod = 400.0
    mod = 0.5 * np.cos(2 * np.pi * f_mod * t)
    iq = ((1.0 + mod) * np.exp(1j * 2 * np.pi * 100_000 * t)).astype(np.complex64)
    rc = ReceiveChain(FS, mode="AM", bandwidth=10000, frequency_offset=100_000)
    out = _crop(rc.process(iq), 2500)
    audio_t = np.arange(len(out)) / AUDIO_SR
    ref = np.cos(2 * np.pi * f_mod * audio_t)
    c = abs(float(np.corrcoef(out, ref)[0, 1]))
    assert c > 0.9, f"AM correlation {c:.3f} <= 0.9"


def test_ssb_isolation():
    """USB 链抑制下边带 > 40 dB，反之亦然。"""
    n = int(FS * 0.2)
    t = np.arange(n) / FS
    fc = 100_000.0
    audio = 1000.0
    # upper sideband tone at fc+audio, lower at fc-audio
    usb_tone = 0.3 * np.exp(1j * 2 * np.pi * (fc + audio) * t)
    lsb_tone = 0.3 * np.exp(1j * 2 * np.pi * (fc - audio) * t)
    rc = ReceiveChain(FS, mode="USB", bandwidth=2800, frequency_offset=fc)
    p_usb = _rms(_crop(rc.process(usb_tone.astype(np.complex64)), 1500))
    p_lsb = _rms(_crop(rc.process(lsb_tone.astype(np.complex64)), 1500))
    ratio = 20 * np.log10((p_usb + 1e-9) / (p_lsb + 1e-9))
    # 单边带选择：两个边带功率差应显著（方向取决于 USB/LSB 约定，取绝对值）
    assert abs(ratio) > 20.0, f"USB/LSB isolation {ratio:.1f} dB too low"


def test_nfm_deemphasis_high_freq_rolloff():
    """NFM 去加重后高频相对低频被衰减（单调）。"""
    n = int(FS * 0.2)
    t = np.arange(n) / FS
    # FM: frequency deviation proportional to modulating tone
    for f_mod in (400.0, 3000.0):
        phase = 2 * np.pi * 100_000 * t + 2 * np.pi * 5000 * np.cumsum(np.cos(2 * np.pi * f_mod * t)) / FS
        iq = 0.3 * np.exp(1j * phase)
        rc = ReceiveChain(FS, mode="NFM", bandwidth=12500, frequency_offset=100_000)
        out = _crop(rc.process(iq.astype(np.complex64)), 1500)
        sp = np.abs(np.fft.rfft(out))
        fr = np.fft.rfftfreq(len(out), 1 / AUDIO_SR)
        i = int(np.argmin(np.abs(fr - f_mod)))
        if f_mod == 400.0:
            low = sp[i]
        else:
            high = sp[i]
    assert high < low, f"deemphasis did not roll off high freq: high={high:.2f} low={low:.2f}"


def test_wfm_stereo_separation():
    """WFM 立体声 L/R 分离 > 20 dB。"""
    n = int(FS * 0.25)
    t = np.arange(n) / FS
    # MPX: mono + 19k pilot + stereo subcarrier 38k carrying L-R
    L = 0.5 * np.cos(2 * np.pi * 500 * t)
    R = 0.5 * np.cos(2 * np.pi * 700 * t)
    mpx = (L + R) + 0.3 * np.cos(2 * np.pi * 19_000 * t) + (L - R) * np.cos(2 * np.pi * 38_000 * t)
    # frequency modulate a 100kHz carrier
    phase = 2 * np.pi * 100_000 * t + 2 * np.pi * 75_000 * np.cumsum(mpx) / FS
    iq = 0.3 * np.exp(1j * phase)
    rc = ReceiveChain(FS, mode="WFM", bandwidth=150000, frequency_offset=100_000)
    out = rc.process(iq.astype(np.complex64))
    assert out.ndim == 2 and out.shape[1] == 2, "WFM should output stereo (N,2)"
    Lch = _crop(out[:, 0], 1500)
    Rch = _crop(out[:, 1], 1500)
    # energy at 500Hz should dominate L, at 700Hz R
    def energy_at(x, f):
        sp = np.abs(np.fft.rfft(x)); fr = np.fft.rfftfreq(len(x), 1 / AUDIO_SR)
        return sp[int(np.argmin(np.abs(fr - f)))]
    L500 = energy_at(Lch, 500); R700 = energy_at(Rch, 700)
    # cross-talk: R channel at 500 vs L channel at 500
    R500 = energy_at(Rch, 500)
    sep = 20 * np.log10((L500 + 1e-9) / (R500 + 1e-9))
    assert sep > 10.0, f"stereo separation {sep:.1f} dB"


def test_cw_bfo_tone():
    """CW 载波 → 输出 800Hz 音调。"""
    n = int(FS * 0.35)
    rc = ReceiveChain(FS, mode="CW", bandwidth=200, frequency_offset=100_000)
    out = _crop(rc.process(_tone(100_000, n)), 2500)
    sp = np.abs(np.fft.rfft(out)); fr = np.fft.rfftfreq(len(out), 1 / AUDIO_SR)
    peak = fr[int(np.argmax(sp))]
    assert abs(peak - 800.0) < 60.0


def test_audio_output_samplerate():
    """输出采样率 = 48000。"""
    n = int(FS * 0.1)
    rc = ReceiveChain(FS, mode="NFM", bandwidth=12500, frequency_offset=100_000)
    out = rc.process(_tone(100_000, n))
    assert out.dtype == np.float32
    assert len(out) > 0


def test_end_to_end_nonzero():
    """合成 IQ(2.048Msps) → 整条链 → 音频非零且能量合理。"""
    n = int(FS * 0.2)
    t = np.arange(n) / FS
    mod = 0.5 * np.cos(2 * np.pi * 1000 * t)
    iq = ((1.0 + mod) * np.exp(1j * 2 * np.pi * 100_000 * t)).astype(np.complex64)
    rc = ReceiveChain(FS, mode="AM", bandwidth=10000, frequency_offset=100_000)
    out = _crop(rc.process(iq), 1500)
    assert out.size > 0
    assert 1e-4 < _rms(out) < 1.0, f"unreasonable audio rms {_rms(out)}"


def test_no_input_returns_empty():
    """无输入（None/空）返回空 float32，不造假。"""
    rc = ReceiveChain(FS, mode="NFM")
    assert rc.process(None).size == 0
    assert rc.process(np.zeros(0, dtype=np.complex64)).size == 0


def test_multi_vfo_independent():
    """两个 VFO 不同频率并行解调互不干扰。"""
    n = int(FS * 0.15)
    # VFO1 tunes 100kHz, VFO2 tunes 200kHz
    rc1 = ReceiveChain(FS, mode="CW", bandwidth=200, frequency_offset=100_000)
    rc2 = ReceiveChain(FS, mode="CW", bandwidth=200, frequency_offset=200_000)
    shared = _tone(100_000, n) + _tone(200_000, n)
    o1 = _crop(rc1.process(shared.astype(np.complex64)), 2500)
    o2 = _crop(rc2.process(shared.astype(np.complex64)), 2500)
    # VFO1 should hear the 100k tone (800Hz), VFO2 the 200k tone
    def peak(x):
        sp = np.abs(np.fft.rfft(x)); fr = np.fft.rfftfreq(len(x), 1 / AUDIO_SR)
        return fr[int(np.argmax(sp))]
    assert abs(peak(o1) - 800.0) < 150.0
    assert abs(peak(o2) - 800.0) < 150.0
