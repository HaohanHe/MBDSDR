"""
录制器测试：IQ complex64 raw + SigMF sidecar；音频 WAV 48k/16bit
=============================================================

对照上游：
- sdrpp/misc_modules/recorder/src/main.cpp:166-210  流式录制
- SigMF 规范：core:datatype / core:sample_rate / core:frequency / captures[datetime]

测试：
1. IQ：写 1 秒 complex64，文件大小正确（8 bytes/sample），sidecar JSON 含
   sample_rate/frequency/datatype，可被 np.fromfile 读回。
2. WAV：写 1 秒 48k 音频，wave 模块读回头正确。
"""
import os
import sys
import json
import wave as _wave

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai.recorder import IQRecorder, AudioRecorder, _sigmf_paths


def test_iq_recorder_writes_complex64_and_sigmf(tmp_path):
    """写 1 秒 complex64 IQ + SigMF sidecar；断言文件大小/JSON 字段/可读回。"""
    data_path = str(tmp_path / "rec")
    paths = _sigmf_paths(data_path)
    fs = 2.4e6
    n = int(fs)  # 1 秒
    rng = np.random.default_rng(0)
    iq = (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64)

    with IQRecorder(data_path, sample_rate=fs, center_freq_hz=100e6,
                    gain_db=20.0, device="RTL-SDR #0", driver="rtlsdr") as rec:
        # 分块写
        for i in range(24):
            rec.write(iq[i * 100000:(i + 1) * 100000])

    # 数据文件存在且大小 = n * 8 bytes
    assert os.path.exists(paths["data"])
    size = os.path.getsize(paths["data"])
    assert size == n * 8, f"complex64 文件大小应为 {n*8}，实际 {size}"

    # sidecar JSON 存在且含 SigMF 字段
    assert os.path.exists(paths["meta"])
    with open(paths["meta"], encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["global"]["core:datatype"] == "cf32_le"
    assert meta["global"]["core:sample_rate"] == pytest.approx(fs)
    assert meta["global"]["core:frequency"] == pytest.approx(100e6)
    assert len(meta["captures"]) == 1
    assert "core:datetime" in meta["captures"][0]

    # 可被 np.fromfile 读回 complex64
    back = np.fromfile(paths["data"], dtype=np.complex64)
    assert len(back) == n


def test_iq_recorder_real_interleaved_input(tmp_path):
    """传入实数交织 [re,im,re,im] 也能正确录成 complex64。"""
    data_path = str(tmp_path / "iq_real")
    paths = _sigmf_paths(data_path)
    rng = np.random.default_rng(1)
    iq = (rng.standard_normal(10000) + 1j * rng.standard_normal(10000)).astype(np.complex64)
    interleaved = np.empty(20000, dtype=np.float32)
    interleaved[0::2] = iq.real
    interleaved[1::2] = iq.imag

    with IQRecorder(data_path, sample_rate=1e6, center_freq_hz=100e6) as rec:
        rec.write(interleaved)
    back = np.fromfile(paths["data"], dtype=np.complex64)
    assert len(back) == 10000


def test_audio_recorder_wav_header(tmp_path):
    """写 1 秒 48kHz 音频；wave 模块读回头正确。"""
    wav_path = str(tmp_path / "voice.wav")
    fs = 48000
    t = np.arange(fs) / fs
    audio = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

    with AudioRecorder(wav_path, sample_rate=fs, channels=1) as rec:
        for i in range(48):
            rec.write(audio[i * 1000:(i + 1) * 1000])

    with _wave.open(wav_path, "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2       # 16bit
        assert wf.getframerate() == fs
        assert wf.getnframes() == fs         # 1 秒


def test_audio_recorder_clip_overflow(tmp_path):
    """超出 [-1,1] 的输入被 clip，不溢出 int16。"""
    wav_path = str(tmp_path / "clip.wav")
    with AudioRecorder(wav_path, sample_rate=48000, channels=1) as rec:
        big = np.array([2.0, -2.0, 0.5], dtype=np.float32)  # 超出 ±1
        rec.write(big)
    with _wave.open(wav_path, "rb") as wf:
        raw = wf.readframes(wf.getnframes())
    pcm = np.frombuffer(raw, dtype=np.int16)
    assert pcm[0] == 32767
    assert pcm[1] == -32767
