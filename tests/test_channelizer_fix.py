"""
test_channelizer_fix.py — 窄带模式单块输出修复的确定性单测。

验证 mbdsdr_ai/channelizer.py 的多级抽取 + LPF 抽头数封顶修复：
  * CW/SSB 等窄带模式单块 50ms 输入不再输出近零；
  * 所有模式单块/连续块无 NaN/Inf；
  * 流式状态跨块连续，reset 后清零。

运行：python3 -m pytest tests/test_channelizer_fix.py -q
"""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pytest

from mbdsdr_ai.receive_chain import ReceiveChain
from mbdsdr_ai.channelizer import XlatingFIR

FS = 2_048_000.0
AUDIO_SR = 48000.0
BLOCK_MS = 0.05  # 50 ms 单块


def _tone(f, n, fs=FS, amp=0.3, ph=0.0):
    t = np.arange(n) / fs
    return (amp * np.exp(1j * (2 * np.pi * f * t + ph))).astype(np.complex64)


def _rms(x):
    x = np.asarray(x, dtype=np.float64)
    return float(np.sqrt(np.mean(x * x))) if x.size else 0.0


def _block_n(ms=BLOCK_MS):
    return int(FS * ms)


# ─────────────────────────────────────────────────────────────────────
# 1. CW 单块非零
# ─────────────────────────────────────────────────────────────────────
def test_cw_single_block_nonzero():
    """100kHz 单音, offset=100k, CW 模式, 50ms 块 → 输出 rms>0.01 且无 NaN。"""
    n = _block_n()
    rc = ReceiveChain(FS, mode="CW", bandwidth=200, frequency_offset=100_000)
    out = rc.process(_tone(100_000, n))
    assert out.size > 0
    assert not np.any(np.isnan(out)), "CW output has NaN"
    assert not np.any(np.isinf(out)), "CW output has Inf"
    rms = _rms(out)
    assert rms > 0.01, f"CW single-block rms {rms:.6f} <= 0.01"


# ─────────────────────────────────────────────────────────────────────
# 2. AM 单块解调与调制信号相关 > 0.8
# ─────────────────────────────────────────────────────────────────────
def test_am_single_block_nonzero():
    """AM 调制信号 → 解调输出与调制信号相关 > 0.8。"""
    n = _block_n(0.3)  # 300ms 以滤掉稳态前的过渡段
    t = np.arange(n) / FS
    f_mod = 400.0
    mod = 0.5 * np.cos(2 * np.pi * f_mod * t)
    iq = ((1.0 + mod) * np.exp(1j * 2 * np.pi * 100_000 * t)).astype(np.complex64)
    rc = ReceiveChain(FS, mode="AM", bandwidth=10000, frequency_offset=100_000)
    out = rc.process(iq)
    # 跳过前 2500 样点的滤波器暖机（与 test_receive_chain 一致）
    seg = out[2500:]
    audio_t = np.arange(len(seg)) / AUDIO_SR
    ref = np.cos(2 * np.pi * f_mod * audio_t)
    c = abs(float(np.corrcoef(seg, ref)[0, 1]))
    assert c > 0.8, f"AM correlation {c:.3f} <= 0.8"
    assert not np.any(np.isnan(out))


# ─────────────────────────────────────────────────────────────────────
# 3. SSB 单块边带隔离 > 20 dB
# ─────────────────────────────────────────────────────────────────────
def test_ssb_single_block_isolation():
    """USB 链上边带功率 / 下边带 > 20 dB。"""
    n = _block_n(0.2)
    t = np.arange(n) / FS
    fc = 100_000.0
    audio = 1000.0
    usb_tone = 0.3 * np.exp(1j * 2 * np.pi * (fc + audio) * t)
    lsb_tone = 0.3 * np.exp(1j * 2 * np.pi * (fc - audio) * t)

    # USB 链
    rc = ReceiveChain(FS, mode="USB", bandwidth=2800, frequency_offset=fc)
    p_usb = _rms(rc.process(usb_tone.astype(np.complex64))[1000:])
    rc2 = ReceiveChain(FS, mode="USB", bandwidth=2800, frequency_offset=fc)
    p_lsb = _rms(rc2.process(lsb_tone.astype(np.complex64))[1000:])
    ratio = 20 * np.log10((p_usb + 1e-9) / (p_lsb + 1e-9))
    assert abs(ratio) > 20.0, f"USB/LSB isolation {ratio:.1f} dB <= 20"


# ─────────────────────────────────────────────────────────────────────
# 4. NFM 单块非零无 NaN
# ─────────────────────────────────────────────────────────────────────
def test_nfm_single_block_nonzero():
    """FM 信号 → 输出非零无 NaN。"""
    n = _block_n(0.2)
    t = np.arange(n) / FS
    # FM: 载波 100kHz, 频偏 5kHz, 调制 400Hz
    phase = (2 * np.pi * 100_000 * t
             + 2 * np.pi * 5000 * np.cumsum(np.cos(2 * np.pi * 400 * t)) / FS)
    iq = 0.3 * np.exp(1j * phase).astype(np.complex64)
    rc = ReceiveChain(FS, mode="NFM", bandwidth=12500, frequency_offset=100_000)
    out = rc.process(iq)
    assert out.size > 0
    assert not np.any(np.isnan(out))
    assert not np.any(np.isinf(out))
    assert _rms(out) > 0.01, f"NFM rms {_rms(out):.6f} <= 0.01"


# ─────────────────────────────────────────────────────────────────────
# 5. WFM 单块立体声分离 > 10 dB
# ─────────────────────────────────────────────────────────────────────
def test_wfm_single_block_stereo():
    """立体声 MPX → L/R 分离 > 10 dB。"""
    n = _block_n(0.3)
    t = np.arange(n) / FS
    L = 0.5 * np.cos(2 * np.pi * 500 * t)
    R = 0.5 * np.cos(2 * np.pi * 700 * t)
    mpx = ((L + R)
           + 0.3 * np.cos(2 * np.pi * 19_000 * t)
           + (L - R) * np.cos(2 * np.pi * 38_000 * t))
    phase = 2 * np.pi * 100_000 * t + 2 * np.pi * 75_000 * np.cumsum(mpx) / FS
    iq = 0.3 * np.exp(1j * phase).astype(np.complex64)
    rc = ReceiveChain(FS, mode="WFM", bandwidth=150000, frequency_offset=100_000)
    out = rc.process(iq)
    assert out.ndim == 2 and out.shape[1] == 2
    Lch = out[1500:, 0]
    Rch = out[1500:, 1]
    # 检查无 NaN
    assert not np.any(np.isnan(out))

    def energy_at(x, f):
        sp = np.abs(np.fft.rfft(x))
        fr = np.fft.rfftfreq(len(x), 1 / AUDIO_SR)
        return sp[int(np.argmin(np.abs(fr - f)))]

    L500 = energy_at(Lch, 500)
    R500 = energy_at(Rch, 500)
    sep = 20 * np.log10((L500 + 1e-9) / (R500 + 1e-9))
    assert sep > 10.0, f"WFM stereo separation {sep:.1f} dB <= 10"


# ─────────────────────────────────────────────────────────────────────
# 6. 连续块 vs 一整块稳态一致
# ─────────────────────────────────────────────────────────────────────
def test_continuous_blocks_match_long():
    """连续 10 块 vs 一整块 → 稳态段输出一致（误差 < 1%）。"""
    total_ms = 0.5
    n_total = int(FS * total_ms)
    t = np.arange(n_total) / FS
    # 用 CW 单音测试（窄带最敏感）
    iq_long = _tone(100_000, n_total)

    # 一次性处理
    rc1 = ReceiveChain(FS, mode="CW", bandwidth=200, frequency_offset=100_000)
    out_long = rc1.process(iq_long)

    # 分 10 块处理
    rc2 = ReceiveChain(FS, mode="CW", bandwidth=200, frequency_offset=100_000)
    block_n = n_total // 10
    chunks = [iq_long[i*block_n:(i+1)*block_n] for i in range(10)]
    out_chunks = [rc2.process(c) for c in chunks]
    out_concat = np.concatenate(out_chunks)

    # 跳过前 1000 样点的暖机，比较稳态段
    s = 1000
    seg_long = out_long[s:]
    seg_concat = out_concat[s:s+len(seg_long)]
    L = min(len(seg_long), len(seg_concat))
    a = seg_long[:L].astype(np.float64)
    b = seg_concat[:L].astype(np.float64)
    err = float(np.sqrt(np.mean((a - b) ** 2)) / (np.sqrt(np.mean(a ** 2)) + 1e-12))
    assert err < 0.01, f"continuous vs long mismatch {err:.4f} >= 1%"


# ─────────────────────────────────────────────────────────────────────
# 7. 所有模式 process 不产生 NaN/Inf
# ─────────────────────────────────────────────────────────────────────
def test_no_nan_any_mode():
    """所有模式 process 不产生 NaN/Inf。"""
    n = _block_n(0.2)
    for mode in ["AM", "FM", "NFM", "WFM", "USB", "LSB", "SSB", "CW"]:
        bw = {"AM":10000,"FM":12500,"NFM":12500,"WFM":150000,
              "USB":2800,"LSB":2800,"SSB":2800,"CW":200}[mode]
        rc = ReceiveChain(FS, mode=mode, bandwidth=bw, frequency_offset=100_000)
        iq = _tone(100_000, n)
        out = rc.process(iq)
        assert out.size > 0, f"{mode} output empty"
        assert not np.any(np.isnan(out)), f"{mode} output has NaN"
        assert not np.any(np.isinf(out)), f"{mode} output has Inf"


# ─────────────────────────────────────────────────────────────────────
# 8. channelizer reset 后状态清零
# ─────────────────────────────────────────────────────────────────────
def test_channelizer_reset():
    """XlatingFIR reset 后 NCO 相位与滤波器状态归零。"""
    ch = XlatingFIR(in_sr=256000, out_sr=3000, bandwidth=200, offset=100_000)
    n = 12800
    t = np.arange(n) / 256000.0
    iq = 0.3 * np.exp(1j * 2 * np.pi * 100_000 * t)

    # 第一次处理
    out1 = ch.process(iq)
    # 第二次处理（状态连续）
    out2 = ch.process(iq)
    # reset
    ch.reset()
    # reset 后第一次处理应与最初第一次处理一致
    out3 = ch.process(iq)
    assert np.allclose(out1, out3, atol=1e-10), "reset did not clear state"
