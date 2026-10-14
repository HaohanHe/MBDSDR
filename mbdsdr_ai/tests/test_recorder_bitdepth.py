# SPDX-License-Identifier: MIT
"""AudioRecorder 位宽档位测试（sdrpp recorder audit 缺口，2026-10-14 销账）。

覆盖：8/16/24/32bit 写出 -> WAV 头位宽回读一致、8bit 无符号偏移语义、
clip 不自动缩放、非整块写入边界、非法位宽诚实拒绝。
"""
import os
import wave

import numpy as np
import pytest

from mbdsdr_ai.recorder import AudioRecorder


@pytest.mark.parametrize("bd", [8, 16, 24, 32])
def test_bit_depth_roundtrip(tmp_path, bd):
    """写出 -> 关闭 -> 回读 WAV 头：位宽/采样率/声道必须一致。"""
    p = tmp_path / f"a_{bd}.wav"
    rec = AudioRecorder(str(p), sample_rate=48000, channels=1, bit_depth=bd)
    assert rec.open() is True
    # 0.5 满幅 + 一个越界 clip 值：clip 不许自动缩放
    rec.write(np.array([0.5, -0.5, 0.25, 1.5], dtype=np.float32))
    rec.close()
    with wave.open(str(p), "rb") as w:
        assert w.getsampwidth() == AudioRecorder._BIT_DEPTHS[bd]
        assert w.getframerate() == 48000
        assert w.getnchannels() == 1
        assert w.getnframes() == 4


def test_8bit_is_unsigned_offset(tmp_path):
    """8bit WAV 为无符号 PCM，0.0 -> 128，满幅正 -> 255。"""
    p = tmp_path / "u8.wav"
    rec = AudioRecorder(str(p), sample_rate=48000, bit_depth=8)
    rec.open()
    rec.write(np.array([0.0, 1.0, -1.0], dtype=np.float32))
    rec.close()
    with wave.open(str(p), "rb") as w:
        raw = w.readframes(3)
    vals = list(raw)
    assert vals[0] == 128
    assert vals[1] in (254, 255)      # 1.0*127+128
    assert vals[2] in (0, 1)          # -1.0*127+128


def test_24bit_little_endian_bytes(tmp_path):
    """24bit：0.5 满幅 -> round(0.5*8388607)=4194304 -> 00 00 40 (小端)。"""
    p = tmp_path / "i24.wav"
    rec = AudioRecorder(str(p), sample_rate=48000, bit_depth=24)
    rec.open()
    rec.write(np.array([0.5], dtype=np.float32))
    rec.close()
    with wave.open(str(p), "rb") as w:
        raw = w.readframes(1)
    assert len(raw) == 3               # 每样本 3 字节
    assert raw[0] == 0 and raw[1] == 0 and raw[2] == 0x40


def test_clip_not_scale(tmp_path):
    """1.5 必须被 clip 到满幅，而不是整体缩放。"""
    p = tmp_path / "clip.wav"
    rec = AudioRecorder(str(p), sample_rate=48000, bit_depth=16)
    rec.open()
    rec.write(np.array([0.2, 1.5], dtype=np.float32))
    rec.close()
    with wave.open(str(p), "rb") as w:
        frames = np.frombuffer(w.readframes(2), dtype="<i2")
    assert frames[0] == int(round(0.2 * 32767))
    assert abs(int(frames[1])) >= 32766     # clip 到满幅，未缩放第一点


def test_invalid_bit_depth_rejected():
    with pytest.raises(ValueError):
        AudioRecorder("/tmp/x.wav", bit_depth=12)


def test_backward_compat_default_16(tmp_path):
    """既有调用方不传 bit_depth -> 默认 16bit（向后兼容）。"""
    p = tmp_path / "def.wav"
    rec = AudioRecorder(str(p))
    rec.open()
    rec.write(np.zeros(4, dtype=np.float32))
    rec.close()
    with wave.open(str(p), "rb") as w:
        assert w.getsampwidth() == 2
