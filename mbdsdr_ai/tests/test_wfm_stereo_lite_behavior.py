# SPDX-License-Identifier: MIT
"""wfm_stereo_lite 行为测试（合成 FM 立体声 IQ 往返解调，无硬件）。

oracle 来源（读码语义 + 复基带 DSP 推导）：
- synthesize_stereo_iq：mpx = (L+R)/2 + pilot + (L-R)/2·cos(2π38kt)，FM 调制；
- decode_stereo：鉴频 → 19k 窄带取 pilot 相位 θ → cos(2θ) 相干混频 →
  2·mpx·cos(2θ) 低通得 (L-R)/2（2× 混频增益补偿 cos² 检波的 ½，
  修复前 D 幅度只有一半，L/R 串扰相关 ~0.3）；
- pilot 能量占比 > 2% 判 stereo，否则 mono（l==r）；
- 样本数 < sr·0.05 → 空输出显式空态。
往返验收：L/R 各自音调在本通道相关 >0.85、串扰相关 <0.1。
"""
import numpy as np
import pytest

from mbdsdr_ai.wfm_stereo_lite import (
    AF_CUTOFF,
    CARRIER_HZ,
    PILOT_HZ,
    decode_stereo,
    synthesize_stereo_iq,
)

MPX_SR = 240000.0
AUD_SR = 48000.0


def _tones(duration_s=1.0, fl=997.0, fr=1301.0):
    t = np.arange(int(AUD_SR * duration_s)) / AUD_SR
    return np.sin(2 * np.pi * fl * t), np.sin(2 * np.pi * fr * t)


def _corr(a, b):
    a = a - a.mean()
    b = b - b.mean()
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))


def _mid(x, frac_lo=0.25, frac_hi=0.75):
    n = len(x)
    return x[int(n * frac_lo):int(n * frac_hi)]


# ── 立体声往返解调（核心行为 + 缺陷回归钉） ─────────────────

def test_stereo_roundtrip_recovers_left_and_right():
    """L=997Hz / R=1301Hz 往返：本通道高相关、串扰低（修复前串扰 ~0.31）。"""
    L, R = _tones()
    iq = synthesize_stereo_iq(L, R, audio_sr=AUD_SR, mpx_sr=MPX_SR)
    out = decode_stereo(iq, MPX_SR, out_sr=int(AUD_SR))
    assert out["stereo"] is True
    assert out["sample_rate"] == 48000
    assert len(out["l"]) == len(out["r"]) == len(L)

    l, r = out["l"], out["r"]
    i0 = len(L) // 4
    n = len(L) // 2
    assert _corr(l[i0:i0 + n], L[i0:i0 + n]) > 0.85      # 左声道还原
    assert _corr(r[i0:i0 + n], R[i0:i0 + n]) > 0.85      # 右声道还原
    assert abs(_corr(l[i0:i0 + n], R[i0:i0 + n])) < 0.10 # 左对右串扰
    assert abs(_corr(r[i0:i0 + n], L[i0:i0 + n])) < 0.10 # 右对左串扰


def test_stereo_channels_are_distinct_signals():
    """解调出的 l 与 r 必须可区分（不是同一信号的两份拷贝）。"""
    L, R = _tones()
    iq = synthesize_stereo_iq(L, R, audio_sr=AUD_SR, mpx_sr=MPX_SR)
    out = decode_stereo(iq, MPX_SR, out_sr=int(AUD_SR))
    assert _corr(out["l"], out["r"]) < 0.5


def test_stereo_output_amplitude_normalized():
    """归一化：峰值 ≤ 0.9，且非全零。"""
    L, R = _tones()
    iq = synthesize_stereo_iq(L, R, audio_sr=AUD_SR, mpx_sr=MPX_SR)
    out = decode_stereo(iq, MPX_SR, out_sr=int(AUD_SR))
    peak = max(np.max(np.abs(out["l"])), np.max(np.abs(out["r"])))
    assert 0.5 < peak <= 0.9 + 1e-6


def test_stereo_pilot_snr_reported():
    """立体声解码时 pilot_snr_db 为有限值（能量占比口径，负 dB 正常）。"""
    L, R = _tones()
    iq = synthesize_stereo_iq(L, R, audio_sr=AUD_SR, mpx_sr=MPX_SR)
    out = decode_stereo(iq, MPX_SR)
    assert out["pilot_snr_db"] is not None
    assert np.isfinite(out["pilot_snr_db"])
    assert out["duration_s"] == pytest.approx(len(out["l"]) / 48000.0, abs=0.01)


# ── mono 路径 ───────────────────────────────────────────────

def test_mono_no_pilot_decodes_l_equals_r():
    """pilot_amp=0 的合成（无 19k 导频）→ stereo=False，l==r（mono 语义）。"""
    L, R = _tones(fl=440.0, fr=440.0)
    iq = synthesize_stereo_iq(L, R, audio_sr=AUD_SR, mpx_sr=MPX_SR, pilot_amp=0.0)
    out = decode_stereo(iq, MPX_SR, out_sr=int(AUD_SR))
    assert out["stereo"] is False
    assert np.allclose(out["l"], out["r"])
    assert len(out["l"]) > 0


# ── 边界：短输入空态 ────────────────────────────────────────

def test_short_input_explicit_empty():
    """样本数 < sr·0.05：返回空数组 + duration 0，不猜测。"""
    iq = synthesize_stereo_iq(*_tones(duration_s=0.01), audio_sr=AUD_SR, mpx_sr=MPX_SR)
    # 0.01s@240k = 2400 样本 < 240000·0.05
    out = decode_stereo(iq, MPX_SR)
    assert len(out["l"]) == 0 and len(out["r"]) == 0
    assert out["duration_s"] == 0.0
    assert out["stereo"] is False and out["pilot_snr_db"] is None


# ── 合成器结构语义 ──────────────────────────────────────────

def test_synthesize_stereo_iq_constant_envelope():
    """FM 调制输出恒包络（|iq|≈1），长度 = 音频上采样后长度。"""
    L, R = _tones(duration_s=0.5)
    iq = synthesize_stereo_iq(L, R, audio_sr=AUD_SR, mpx_sr=MPX_SR)
    assert iq.dtype == np.complex64
    assert len(iq) == int(len(L) * MPX_SR / AUD_SR)
    assert np.allclose(np.abs(iq), 1.0, atol=1e-3)


def test_constants_match_broadcast_standard():
    """常量即广播制式：19k 导频 / 38k 副载波 / 15k 音频截止。"""
    assert PILOT_HZ == 19000.0
    assert CARRIER_HZ == 38000.0
    assert AF_CUTOFF == 15000.0
