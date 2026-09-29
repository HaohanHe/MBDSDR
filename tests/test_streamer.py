# SPDX-License-Identifier: MIT
"""
test_streamer.py — SpectrumStreamer / AudioStreamer 确定性单测。

验证：
  * FFT 峰值落在已知正弦信号对应 bin 附近
  * 峰值保留降采样不丢关键频点
  * 无数据时 latest()=None（红线：不造假）
  * 音频帧首字节 = 0x02
  * 多订阅者扇出 + 慢客户端背压
"""
import os
import sys
import queue
import threading

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.web.streamer import (
    SpectrumStreamer, AudioStreamer, decode_spectrum_frame,
    SPECTRUM_MARKER, AUDIO_MARKER,
)


def _synth_iq(offset_hz: float, samp_rate: float, n: int, amp=0.5):
    t = np.arange(n) / samp_rate
    return (amp * np.exp(2j * np.pi * offset_hz * t)).astype(np.complex64)


def test_fftt_peak_at_expected_bin():
    """在 100kHz 中心、1MHz 采样率下放 +250kHz 正弦（sr/4），FFT 峰值应在右 3/4 处。"""
    sr = 1_000_000.0
    cf = 100_000.0
    fft_size = 1024
    st = SpectrumStreamer(fft_size=fft_size, target_bins=256, fps=0.0)
    iq = _synth_iq(250_000.0, sr, fft_size)
    db = st.compute_spectrum_db(iq)
    assert len(db) == fft_size
    peak = int(np.argmax(db))
    # fftshift 后 DC 在 fft_size/2；+250kHz = sr/4 对应 +fft_size/4
    expected = fft_size // 2 + fft_size // 4
    assert abs(peak - expected) <= 2, f"peak {peak} far from {expected}"


def test_compress_preserves_peak_bin():
    """压缩（块最大值降采样）后，强信号所在输出 bin 仍为接近 255 的高值。"""
    sr = 1_000_000.0
    cf = 100_000.0
    fft_size, target = 1024, 256
    st = SpectrumStreamer(fft_size=fft_size, target_bins=target, fps=0.0)
    iq = _synth_iq(250_000.0, sr, fft_size)
    bins = st.compress(st.compute_spectrum_db(iq))
    assert len(bins) == target
    # +250kHz = sr/4 处，对应压缩后 bin = target*3/4（block=4）
    expected_bin = int(target * 3 / 4)
    assert bins[expected_bin] >= 200, f"peak bin {expected_bin}={bins[expected_bin]} too low"
    # 噪声底（远离信号）应明显更低
    assert bins[target // 4] < bins[expected_bin]


def test_no_data_returns_none():
    st = SpectrumStreamer()
    assert st.latest() is None
    assert st.has_frame() is False


def test_push_iq_frame_format_and_latest():
    sr, cf, n = 1_000_000.0, 100_000.0, 1024
    st = SpectrumStreamer(fft_size=n, target_bins=256, fps=0.0)
    iq = _synth_iq(50_000.0, sr, n)
    frame = st.push_iq(iq, cf, sr, force=True)
    assert frame is not None
    decoded = decode_spectrum_frame(frame)
    assert decoded["center_freq_hz"] == int(cf)
    assert decoded["samp_rate_hz"] == int(sr)
    assert len(decoded["bins"]) == 256
    latest = st.latest()
    assert latest is not None
    # 峰值频率应在 +50kHz 附近（容差一个 bin）
    bin_hz = sr / 256
    assert abs(latest["peak_freq_hz"] - (cf + 50_000.0)) <= bin_hz


def test_broadcast_to_multiple_subscribers():
    st = SpectrumStreamer(fft_size=512, target_bins=128, fps=0.0)

    class FakeSub:
        def __init__(self):
            self.out = queue.Queue(maxsize=8)
            self.closed = False
        def close(self):
            self.closed = True

    s1, s2 = FakeSub(), FakeSub()
    st.add_subscriber(s1)
    st.add_subscriber(s2)
    assert st.subscriber_count() == 2

    iq = _synth_iq(10_000.0, 500_000.0, 512)
    st.push_iq(iq, 100_000.0, 500_000.0, force=True)

    f1 = s1.out.get_nowait()
    f2 = s2.out.get_nowait()
    assert f1 == f2 and f1[0] == SPECTRUM_MARKER

    # 慢订阅者：队列满 -> 被摘掉
    slow = FakeSub()
    st.add_subscriber(slow)
    for _ in range(10):
        st.push_iq(iq, 100_000.0, 500_000.0, force=True)
    assert slow.closed is True


def test_audio_streamer_frame():
    au = AudioStreamer(sample_rate=48000)
    sub = type("S", (), {"out": queue.Queue(maxsize=8), "close": lambda self: None})()
    au.add_subscriber(sub)
    pcm = np.zeros(512, dtype=np.int16)
    frame = au.push_audio(pcm)
    assert frame[0] == AUDIO_MARKER
    assert len(frame) == 1 + 512 * 2
    got = sub.out.get_nowait()
    assert got == frame
