# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""GatedRecorder / ChannelDemod 离线测试：只在有信号时录干净分段。"""
import os
import numpy as np
import pytest
from scipy.io import wavfile

from mbdsdr_ai.gated_recorder import ChannelDemod, GatedRecorder


def _dominant_freq(audio, sr):
    X = np.abs(np.fft.rfft(audio * np.hanning(len(audio))))
    f = np.fft.rfftfreq(len(audio), 1 / sr)
    return f[int(np.argmax(X))], float(np.max(X))


def test_gated_recorder_core(tmp_path):
    sr = 48000
    rec = GatedRecorder(sr, "AM", str(tmp_path), frequency_hz=119450000)
    blk = sr // 10  # 0.1s
    t = np.arange(blk) / sr
    noise = 0.02 * np.random.default_rng(1).standard_normal(blk)
    tone = 0.8 * np.sin(2 * np.pi * 1000 * t)

    # 1s 纯噪声 -> 无文件
    for _ in range(10):
        rec.feed(noise.astype(np.float32), False)
    assert rec.segments == []
    # 1s 有信号（门开）
    for _ in range(10):
        rec.feed(tone.astype(np.float32), True)
    # 载波消失超过 hang(1.5s) -> 自动切段
    for _ in range(20):
        rec.feed(noise.astype(np.float32), False)
    assert len(rec.segments) == 1
    seg = rec.segments[0]
    assert seg["mode"] == "AM"
    sr_r, audio = wavfile.read(os.path.join(str(tmp_path), seg["file"]))
    df, peak = _dominant_freq(audio.astype(np.float32), sr_r)
    assert abs(df - 1000) < 30 and peak > 1.0


def test_am_channel_end_to_end(tmp_path):
    native_sr = 192000
    block_n = 4800  # 25ms
    total_s = 20.0
    nblk = int(total_s * native_sr / block_n)
    bursts = [(2.0, 4.0), (8.0, 9.5), (14.0, 17.0)]

    demod = ChannelDemod(native_sr, "AM", audio_sr=48000)
    base = 1_700_000_000
    rec = GatedRecorder(48000, "AM", str(tmp_path),
                        frequency_hz=119450000,
                        clock=lambda: base)
    rng = np.random.default_rng(0)
    for b in range(nblk):
        g = np.arange(b * block_n, (b + 1) * block_n)
        tt = g / native_sr
        on = np.zeros_like(tt, dtype=bool)
        for a, z in bursts:
            on |= (tt >= a) & (tt < z)
        mod = 0.8 * np.sin(2 * np.pi * 1000 * tt)
        carrier = np.where(on, 1.0 + mod, 0.0)
        iq = ((carrier + 0.02 * rng.standard_normal(block_n))
              + 1j * 0.02 * rng.standard_normal(block_n)).astype(np.complex64)
        mono, gate = demod.process(iq)
        now = base + (b + 1) * block_n / native_sr
        rec.feed(mono, gate, now=now)
    rec.flush(now=base + total_s)

    assert len(rec.segments) == 3, rec.segments
    total_dur = sum(e["duration_s"] for e in rec.segments)
    # 三段时长 ≈ 2 + 1.5 + 3 = 6.5s，长噪声间隙被剔除
    assert 5.5 <= total_dur <= 8.0, total_dur
    for e, (a, z) in zip(rec.segments, bursts):
        assert os.path.exists(os.path.join(str(tmp_path), e["file"]))
        sr_r, audio = wavfile.read(os.path.join(str(tmp_path), e["file"]))
        df, peak = _dominant_freq(audio.astype(np.float32), sr_r)
        assert abs(df - 1000) < 40
        assert abs(e["duration_s"] - (z - a)) < 0.7


def test_wfm_channel_end_to_end(tmp_path):
    native_sr = 480000
    block_n = 48000  # 0.1s
    total_s = 12.0
    nblk = int(total_s * native_sr / block_n)
    demod = ChannelDemod(native_sr, "WFM", audio_sr=48000)
    base = 1_700_000_000
    rec = GatedRecorder(48000, "WFM", str(tmp_path),
                        frequency_hz=103300000, clock=lambda: base)
    rng = np.random.default_rng(2)
    phase = 0.0
    for b in range(nblk):
        g = np.arange(b * block_n, (b + 1) * block_n)
        tt = g / native_sr
        on = (tt >= 3.0) & (tt < 7.0)
        inst = np.where(on, 75000.0 * np.sin(2 * np.pi * 1000 * tt), 0.0)
        ph = phase + 2 * np.pi * np.concatenate(
            ([0], np.cumsum(inst[:-1]) / native_sr))
        phase = ph[-1] + 2 * np.pi * inst[-1] / native_sr
        iq = ((np.exp(1j * ph) + 0.01 * rng.standard_normal(block_n))
              + 1j * 0.01 * rng.standard_normal(block_n)).astype(np.complex64)
        mono, gate = demod.process(iq)
        rec.feed(mono, gate, now=base + (b + 1) * block_n / native_sr)
    rec.flush(now=base + total_s)
    assert len(rec.segments) == 1, rec.segments
    e = rec.segments[0]
    assert abs(e["duration_s"] - 4.0) < 0.5
    sr_r, audio = wavfile.read(os.path.join(str(tmp_path), e["file"]))
    df, peak = _dominant_freq(audio.astype(np.float32), sr_r)
    assert abs(df - 1000) < 40 and peak > 1.0


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
