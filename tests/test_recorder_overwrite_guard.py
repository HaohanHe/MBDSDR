# SPDX-License-Identifier: MIT
"""录制是证据，不是缓存：同名文件默认绝不覆盖（2026-11-06）。

主窗口工具栏有 `_unique_record_path` 防呆，但 MCP/CLI 走
`SDRController.start_recording(path)`，路径由调用方直接给；旧实现
`open(path, "wb")` 会把上一段真实录音静默抹掉，且失败时一律回一句
「无法打开 {path} 写入」，磁盘满/无权限/同名互相冒充。

反证：把 `overwrite` 默认值改回 True，本文件所有"拒绝覆盖"用例立即红。
"""
import os
import sys

import numpy as np  # noqa: E402
import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.core.controller import SDRController  # noqa: E402
from mbdsdr_ai.recorder import AudioRecorder, IQRecorder  # noqa: E402


class TestIQRecorderGuard:
    def test_existing_data_is_not_touched(self, tmp_path):
        data = tmp_path / "run.sigmf-data"
        payload = np.arange(16, dtype=np.complex64).tobytes()
        data.write_bytes(payload)
        rec = IQRecorder(str(data), sample_rate=1e6)
        assert rec.open() is False
        assert rec.state == "failed"
        assert rec.error_code == "already_exists"
        assert "拒绝覆盖" in rec.last_error
        assert data.read_bytes() == payload, "旧证据被改写了"
        assert rec.close() is None          # 没开始录制就不许写 meta

    def test_orphan_meta_alone_also_blocks(self, tmp_path):
        """只有 meta 没有 data 也要拒绝：否则新 data 会配上旧 meta。"""
        meta = tmp_path / "run.sigmf-meta"
        meta.write_text('{"global": {}}', encoding="utf-8")
        rec = IQRecorder(str(tmp_path / "run.sigmf-data"), sample_rate=1e6)
        assert rec.open() is False
        assert "拒绝覆盖" in rec.last_error
        assert meta.read_text(encoding="utf-8") == '{"global": {}}'

    def test_explicit_overwrite_is_allowed(self, tmp_path):
        data = tmp_path / "run.sigmf-data"
        data.write_bytes(b"old evidence")
        rec = IQRecorder(str(data), sample_rate=1e6, overwrite=True)
        assert rec.open() is True
        assert rec.write(np.ones(8, dtype=np.complex64)) == 8
        meta = rec.close()
        assert meta and os.path.isfile(meta)
        assert data.stat().st_size == 64      # 8 × complex64

    def test_fresh_path_still_works(self, tmp_path):
        rec = IQRecorder(str(tmp_path / "new"), sample_rate=1e6)
        assert rec.open() is True
        assert rec.write(np.ones(4, dtype=np.complex64)) == 4
        assert rec.state == "recording"
        assert rec.close() is not None


class TestAudioRecorderGuard:
    def test_existing_wav_is_not_touched(self, tmp_path):
        wav = tmp_path / "voice.wav"
        wav.write_bytes(b"RIFF-old")
        rec = AudioRecorder(str(wav), sample_rate=48000)
        assert rec.open() is False
        assert rec.state == "failed"
        assert rec.error_code == "already_exists"
        assert "拒绝覆盖" in rec.last_error
        assert wav.read_bytes() == b"RIFF-old"

    def test_explicit_overwrite_is_allowed(self, tmp_path):
        wav = tmp_path / "voice.wav"
        wav.write_bytes(b"RIFF-old")
        rec = AudioRecorder(str(wav), sample_rate=48000, overwrite=True)
        assert rec.open() is True
        assert rec.write(np.zeros(100, dtype=np.float32)) > 0
        rec.close()
        assert wav.read_bytes().startswith(b"RIFF")
        assert wav.read_bytes() != b"RIFF-old"


class TestControllerReason:
    def test_open_failure_exposes_real_reason(self, tmp_path):
        data = tmp_path / "run.sigmf-data"
        data.write_bytes(b"keep me")
        c = SDRController(config_dir=str(tmp_path / "cfg"))
        c._connected = True
        before = getattr(c, "_recorder_thread", None)
        res = c.start_recording(str(data))
        assert res["error"] == "open_failed"
        assert res["error_code"] == "already_exists"
        assert "拒绝覆盖" in res["suggestion"]
        assert getattr(c, "_recorder_thread", None) is before, "失败还起了录制线程"
        assert data.read_bytes() == b"keep me"

    def test_not_connected_still_short_circuits(self, tmp_path):
        c = SDRController(config_dir=str(tmp_path / "cfg"))
        res = c.start_recording(str(tmp_path / "x"))
        assert res["error"] == "not_connected"
