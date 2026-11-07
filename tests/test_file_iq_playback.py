# SPDX-License-Identifier: MIT
"""FileIQBackend 诚实回放：连接失败必须写 status.error；录制端五种格式
（cu8/cf32/cs16/wav/csv）录完即可回放（审计交接单 B 节 P1）。

红线：
  * connect() 失败不再静默 return False——error 必须可见且含原因；
  * sidecar 损坏不再静默吞掉按默认值播（那是按错误采样率/频率解释数据）；
  * wav/csv 走**真实录制器** start_recording 落盘再回放对拍，不是手写假文件
    自证（cf32/cu8 两条阳性对照证明既有格式不被修坏）。
"""
import json
import os
import wave

import numpy as np
import pytest

from mbdsdr_ai.sdr_backend import FileIQBackend


def _make_source(tmp_path, n=4096, rate=48000, freq=137_100_000.0):
    """真 IQ 源：npy 文件 + FileIQBackend 已连接，供真实录制器边采边写。"""
    rng = np.random.default_rng(20261107)
    src = (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64)
    src *= np.float32(0.25)  # 留幅值余量，量化不削顶
    npy = str(tmp_path / "source.npy")
    np.save(npy, src)
    be = FileIQBackend(npy, sample_rate=rate, center_freq=freq, loop=True)
    assert be.connect()
    return be, src


def _record(be, dst, fmt, max_bytes=262144):
    """用产品自己的录制器录 0.15s（真实 _recording_loop 线程写盘+sidecar）。"""
    assert be.start_recording(dst, duration=0.15, fmt=fmt, max_bytes=max_bytes)
    be._recording_thread.join(timeout=10.0)
    assert not be._recording_thread.is_alive(), "录制线程未按时收尾"
    be.stop_recording()
    assert os.path.exists(dst) and os.path.getsize(dst) > 0
    assert os.path.exists(dst + ".json"), "录制器必须写 sidecar"


def _read_all(path, **kw):
    pb = FileIQBackend(path, loop=False, **kw)
    assert pb.connect(), f"回放连接失败: {pb.status.error}"
    chunks = []
    while True:
        x = pb.read_samples(65536)
        if x is None or len(x) == 0:
            break
        chunks.append(x)
    assert chunks, "回放读不到任何样本"
    return pb, np.concatenate(chunks)


class TestConnectHonestFailure:
    """连接失败必须写 status.error（旧实现全静默 return False）。"""

    def test_missing_file_writes_error(self, tmp_path):
        be = FileIQBackend(str(tmp_path / "不存在.cu8"))
        assert be.connect() is False
        assert be.status.connected is False
        assert "不存在.cu8" in be.status.error

    def test_unsupported_format_writes_error(self, tmp_path):
        p = tmp_path / "data.xyz"
        p.write_bytes(b"\x00\x01\x02\x03")
        be = FileIQBackend(str(p))
        assert be.connect() is False
        assert "xyz" in be.status.error

    def test_corrupt_sidecar_fails_loudly(self, tmp_path):
        p = tmp_path / "data.cu8"
        p.write_bytes(bytes([127, 128] * 64))
        (tmp_path / "data.cu8.json").write_text("{坏 JSON", encoding="utf-8")
        be = FileIQBackend(str(p))
        # 旧实现：sidecar 解析失败被 except: pass 吞掉，按默认采样率假连接成功
        assert be.connect() is False
        assert "sidecar" in be.status.error

    def test_sidecar_non_dict_fails_loudly(self, tmp_path):
        p = tmp_path / "data.cu8"
        p.write_bytes(bytes([127, 128] * 64))
        (tmp_path / "data.cu8.json").write_text("[1, 2]", encoding="utf-8")
        be = FileIQBackend(str(p))
        assert be.connect() is False
        assert "sidecar" in be.status.error

    def test_empty_file_fails(self, tmp_path):
        p = tmp_path / "empty.cu8"
        p.write_bytes(b"")
        be = FileIQBackend(str(p))
        # 旧实现：0 样本也 connected=True，回放永远空转
        assert be.connect() is False
        assert "样本" in be.status.error


class TestRecordPlaybackRoundtrip:
    """真实录制器落盘 → FileIQBackend 回放对拍（录完即可回放）。"""

    @pytest.mark.parametrize("fmt,atol", [
        ("cf32", 1e-6),   # 阳性对照：既有格式不被修坏
        ("cu8", 1.0 / 127.5 + 1e-6),  # 阳性对照
        ("wav", 2e-4),    # 修复前：回放端不认 wav，connect 静默 False
        ("csv", 5e-7),    # 修复前：回放端不认 csv
    ])
    def test_roundtrip(self, tmp_path, fmt, atol):
        be, src = _make_source(tmp_path)
        dst = str(tmp_path / f"rec.{fmt}")
        _record(be, dst, fmt)
        pb, got = _read_all(dst)
        expect = np.resize(src, len(got))  # 源从 cursor 0 顺序循环，录制逐块连续
        np.testing.assert_allclose(got.real, expect.real, atol=atol, rtol=0)
        np.testing.assert_allclose(got.imag, expect.imag, atol=atol, rtol=0)
        # sidecar 的速率/频率必须落到回放 status（不是构造默认值）
        assert pb.status.sample_rate_hz == 48000
        assert pb.status.frequency_hz == 137_100_000.0
        assert pb.status.error == ""
        be.disconnect()
        pb.disconnect()


class TestWavCsvDetails:
    def test_wav_header_rate_without_sidecar(self, tmp_path):
        be, _ = _make_source(tmp_path)
        dst = str(tmp_path / "rec.wav")
        _record(be, dst, "wav")
        os.remove(dst + ".json")
        # 无 sidecar 时 wav 头里的帧率是真实元数据，不许拿构造默认 2.4M 冒充
        pb = FileIQBackend(dst, loop=False)
        assert pb.connect(), pb.status.error
        assert pb.status.sample_rate_hz == 48000
        be.disconnect()
        pb.disconnect()

    def test_wav_scale_denormalized(self, tmp_path):
        # baseband_io 系 wav 归一化到满幅并写 wav_scale；回放必须反归一化
        p = tmp_path / "norm.wav"
        x = np.array([0.25, -0.125, 0.0625], dtype=np.float32)
        q = np.array([-0.25, 0.125, 0.0], dtype=np.float32)
        scale = 0.25
        ints = np.column_stack([x / scale, q / scale]) * 32767
        with wave.open(str(p), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(48000)
            wf.writeframes(ints.astype(np.int16).tobytes())
        (tmp_path / "norm.wav.json").write_text(json.dumps({
            "format": "wav", "sample_rate": 48000, "center_freq": 100e6,
            "wav_scale": scale}), encoding="utf-8")
        pb, got = _read_all(str(p))
        np.testing.assert_allclose(got.real, x, atol=2e-4, rtol=0)
        np.testing.assert_allclose(got.imag, q, atol=2e-4, rtol=0)
        pb.disconnect()

    def test_mono_wav_rejected(self, tmp_path):
        p = tmp_path / "mono.wav"
        with wave.open(str(p), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(48000)
            wf.writeframes(np.zeros(480, dtype=np.int16).tobytes())
        be = FileIQBackend(str(p))
        assert be.connect() is False
        assert "声道" in be.status.error

    def test_csv_wrong_columns_rejected(self, tmp_path):
        p = tmp_path / "bad.csv"
        p.write_text("I,Q,X\n0.1,0.2,0.3\n", encoding="utf-8")
        be = FileIQBackend(str(p))
        assert be.connect() is False
        assert "列" in be.status.error


class TestConsumerSurfacesError:
    def test_decode_noaa_apt_surfaces_connect_error(self, tmp_path):
        from mbdsdr_ai.decoders import decode_noaa_apt
        p = tmp_path / "data.xyz"
        p.write_bytes(b"\x00\x01\x02\x03")
        out = decode_noaa_apt(str(p), output_dir=str(tmp_path / "out"))
        # 旧实现：connect 静默失败 → 读不到样本 → 误报「IQ 文件无数据」
        assert "error" in out
        assert "xyz" in out["error"], f"必须透出真实原因，实际: {out['error']}"