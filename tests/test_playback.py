"""录制回放确定性测试：IQPlayback + PlaybackSource。

用 recorder.IQRecorder 真实录一段 complex64 + SigMF meta，再用 playback 重放，
验证：正确读取、seek/tell、循环、SigMF meta 解析、PlaybackSource 接口兼容。
"""
import os

import numpy as np
import pytest

from mbdsdr_ai.recorder import IQRecorder
from mbdsdr_ai.playback import IQPlayback, PlaybackSource, parse_sigmf_meta


SR = 240_000.0
FREQ = 100_000_000.0
N = 240_000  # 1 秒 @ 240k


def _make_recording(tmp_path, tag="rec"):
    """真实录一段带单音的 IQ，返回 data 路径。"""
    t = np.arange(N) / SR
    tone = (0.5 * np.exp(2j * np.pi * 1000.0 * t)).astype(np.complex64)
    rec = IQRecorder(str(tmp_path / tag), sample_rate=SR,
                     center_freq_hz=FREQ, device="TestRTL")
    rec.open()
    rec.write(tone)
    rec.close()
    return rec.data_path


def test_open_reads_exact_samples(tmp_path):
    data = _make_recording(tmp_path)
    pb = IQPlayback()
    assert pb.open(data) is True
    # 读取一半
    half = N // 2
    out = pb.read_samples(half)
    assert out.shape == (half,)
    assert out.dtype == np.complex64
    # 再读另一半，拼接后总长度 = N
    out2 = pb.read_samples(N - half)
    assert out2.shape == (N - half,)
    # 非 loop：再读应为空
    assert pb.read_samples(10).size == 0
    pb.close()


def test_sigmf_meta_parsed(tmp_path):
    data = _make_recording(tmp_path)
    pb = IQPlayback()
    pb.open(data)
    assert pb.sample_rate == SR
    assert pb.center_freq_hz == FREQ
    assert pb.n_samples == N
    assert pb.get_duration() == pytest.approx(1.0)
    assert pb.datetime != ""  # 录制时落了 ISO 时间
    pb.close()


def test_seek_tell(tmp_path):
    data = _make_recording(tmp_path)
    pb = IQPlayback()
    pb.open(data)
    pb.seek(0.5)            # 定位到 0.5s
    assert pb.tell() == pytest.approx(0.5, abs=1e-6)
    out = pb.read_samples(100)
    assert out.size == 100
    assert pb.tell() == pytest.approx(0.5 + 100 / SR, abs=1e-6)
    # seek 超界钳位
    pb.seek(999.0)
    assert pb.tell() == pytest.approx((N - 1) / SR, abs=1e-3)
    pb.close()


def test_loop_playback(tmp_path):
    data = _make_recording(tmp_path)
    pb = IQPlayback(loop=True)
    pb.open(data)
    # 文件只有 N 个样本，请求 1.5*N 个 → 应跨循环补齐
    out = pb.read_samples(int(N * 1.5))
    assert out.size == int(N * 1.5)
    # 循环后位置应回绕到 0.5N 附近
    assert pb.tell() == pytest.approx(0.5, abs=1e-3)
    pb.close()


def test_no_file_returns_false(tmp_path):
    pb = IQPlayback()
    assert pb.open(str(tmp_path / "nope.sigmf-data")) is False
    assert pb.read_samples(10).size == 0


def test_playback_source_compatible(tmp_path):
    data = _make_recording(tmp_path)
    src = PlaybackSource(data, loop=False)
    assert src.connect() is True
    # 频率/采样率来自 SigMF meta，不造假
    assert src.get_frequency() == FREQ
    assert src.get_sample_rate() == SR
    # read_samples 与 SDRBackend 兼容
    blk = src.read_samples(1024)
    assert blk is not None and blk.shape == (1024,)
    # set_frequency 更新元数据
    assert src.set_frequency(145_000_000) is True
    assert src.get_frequency() == 145_000_000
    st = src.get_status()
    assert st["connected"] is True
    assert "Playback" in st["device"] or "Playback" == st["device"]
    assert st["duration_s"] == pytest.approx(1.0)
    src.disconnect()
    assert src.get_status()["connected"] is False


def test_playback_source_eof_returns_none(tmp_path):
    data = _make_recording(tmp_path)
    src = PlaybackSource(data, loop=False)
    src.connect()
    src.read_samples(N + 100)   # 一次读穿
    assert src.read_samples(10) is None


def test_parse_sigmf_meta_missing(tmp_path):
    m = parse_sigmf_meta(str(tmp_path / "nope.sigmf-meta"))
    assert m["sample_rate_hz"] == 0.0
    assert m["center_freq_hz"] == 0.0
