# SPDX-License-Identifier: MIT
"""回放文件与录制落盘的坏路径：截断/非法头/采样率不符/中途删除/磁盘满。"""

from __future__ import annotations

import builtins
import errno
import json
import os

import numpy as np
import pytest

from mbdsdr_ai import playback
from mbdsdr_ai.playback import IQPlayback, PlaybackSource
from mbdsdr_ai.recorder import (IQRecorder, inspect_recording,
                                recover_recording_meta)


def _write_sigmf(root: str, values: np.ndarray,
                 sample_rate: float = 2_048_000.0,
                 declared_samples: int = 0,
                 datatype: str = "cf32_le") -> str:
    data = root + ".sigmf-data"
    values.astype(np.complex64).tofile(data)
    meta = {
        "global": {
            "core:datatype": datatype,
            "core:sample_rate": sample_rate,
            "core:frequency": 100e6,
            "core:total_num_samples": int(declared_samples or len(values)),
        },
        "captures": [{"core:sample_start": 0}],
    }
    with open(root + ".sigmf-meta", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    return data


def test_valid_playback_reads_real_samples(tmp_path):
    data = _write_sigmf(str(tmp_path / "ok"), np.arange(100, dtype=np.float32))
    pb = IQPlayback(loop=False)
    assert pb.open(data) is True
    assert pb.state == "open"
    assert pb.sample_rate == 2_048_000.0
    got = pb.read_samples(10)
    assert len(got) == 10
    assert got.real[0] == 0.0
    pb.close()


def test_truncated_file_refuses_to_look_normal(tmp_path):
    values = np.arange(100, dtype=np.float32)
    data = _write_sigmf(str(tmp_path / "short"), values,
                        declared_samples=100)
    # 实际只留 50 个样本（模拟复制中断）
    with open(data, "r+b") as f:
        f.truncate(50 * 8)
    pb = IQPlayback()
    assert pb.open(data) is False
    assert pb.state == "failed"
    assert pb.error_code == "truncated"
    assert pb.opened is False


def test_half_sample_file_is_corrupt(tmp_path):
    data = str(tmp_path / "half.sigmf-data")
    with open(data, "wb") as f:
        f.write(b"\x00" * 12)  # 12 不是 8 的整数倍
    pb = IQPlayback()
    assert pb.open(data) is False
    assert pb.error_code == "truncated"


def test_corrupt_meta_header_is_rejected(tmp_path):
    data = _write_sigmf(str(tmp_path / "badmeta"),
                        np.arange(20, dtype=np.float32))
    with open(str(tmp_path / "badmeta.sigmf-meta"), "w", encoding="utf-8") as f:
        f.write("{not json")
    pb = IQPlayback()
    assert pb.open(data) is False
    assert pb.error_code == "corrupt"


def test_unsupported_datatype_is_rejected(tmp_path):
    data = _write_sigmf(str(tmp_path / "cu8"),
                        np.arange(20, dtype=np.float32),
                        datatype="ci8_le")
    pb = IQPlayback()
    assert pb.open(data) is False
    assert pb.error_code == "datatype"


def test_empty_file_is_rejected(tmp_path):
    data = str(tmp_path / "empty.sigmf-data")
    open(data, "wb").close()
    pb = IQPlayback()
    assert pb.open(data) is False
    assert pb.error_code == "empty"


def test_playback_rate_cannot_be_rewritten(tmp_path):
    data = _write_sigmf(str(tmp_path / "rate"), np.arange(100, dtype=np.float32))
    src = PlaybackSource(data, loop=False)
    assert src.connect() is True
    assert src.set_sample_rate(2_048_000.0) is True
    assert src.set_sample_rate(1_024_000.0) is False
    st = src.get_status()
    assert st["error_code"] == "rate_mismatch"
    assert src._rate == 2_048_000.0
    src.disconnect()


def test_rate_unknown_file_refuses_guess(tmp_path):
    data = _write_sigmf(str(tmp_path / "unknown"),
                        np.arange(20, dtype=np.float32), sample_rate=0.0)
    src = PlaybackSource(data, loop=False)
    assert src.connect() is True
    assert src.set_sample_rate(2_048_000.0) is False
    assert src.get_status()["error_code"] == "rate_unknown"


def test_file_deleted_mid_read_is_reported(tmp_path, monkeypatch):
    data = _write_sigmf(str(tmp_path / "gone"), np.arange(50, dtype=np.float32))
    src = PlaybackSource(data, loop=False)
    assert src.connect() is True
    assert src.read_samples(10) is not None
    real_exists = os.path.exists
    monkeypatch.setattr(
        playback.os.path, "exists",
        lambda p: False if str(p) == data else real_exists(p))
    assert src.read_samples(10) is None
    st = src.get_status()
    assert st["playback_state"] == "deleted"
    assert st["error_code"] == "deleted"
    assert st["connected"] is False
    src.disconnect()


def test_eof_is_explicit(tmp_path):
    data = _write_sigmf(str(tmp_path / "eof"), np.arange(10, dtype=np.float32))
    src = PlaybackSource(data, loop=False)
    assert src.connect() is True
    assert src.read_samples(10) is not None
    assert src.read_samples(10) is None
    st = src.get_status()
    assert st["playback_state"] == "eof"
    assert st["error_code"] == "eof"
    src.disconnect()


class _FullDiskFile:
    """先写半截再抛 ENOSPC，模拟磁盘写满时的真实半写状态。"""

    def __init__(self, real):
        self._real = real
        self.bad = True

    def write(self, blob: bytes) -> int:
        if self.bad:
            self._real.write(blob[: len(blob) // 2])
            raise OSError(errno.ENOSPC, "No space left on device")
        return self._real.write(blob)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_disk_full_marks_failed_and_keeps_file_sample_aligned(tmp_path):
    data = str(tmp_path / "full.sigmf-data")
    rec = IQRecorder(data, sample_rate=2_048_000.0)
    assert rec.open() is True
    rec._fp = _FullDiskFile(rec._fp)
    assert rec.write(np.ones(100, dtype=np.complex64)) == 0
    assert rec.state == "failed"
    assert rec.error_code == "disk_full"
    assert os.path.getsize(data) == 0  # 半截样本被截掉，不冒充正常
    assert rec.close() is not None
    info = inspect_recording(data)
    # 磁盘满后没有留下任何完整样本：只能报 empty，绝不能报 ok
    assert info["integrity"] == "empty"
    assert info["integrity"] != "ok"


def test_unwritable_path_is_classified(tmp_path, monkeypatch):
    data = str(tmp_path / "denied.sigmf-data")
    real_open = builtins.open

    def fake_open(path, *args, **kwargs):
        if str(path) == data:
            raise PermissionError(errno.EACCES, "Permission denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)
    rec = IQRecorder(data, sample_rate=2_048_000.0)
    assert rec.open() is False
    assert rec.error_code == "not_writable"
    assert rec.last_error


def test_power_loss_orphan_data_can_be_recovered_honestly(tmp_path):
    data = str(tmp_path / "orphan.sigmf-data")
    np.zeros(100, dtype=np.complex64).tofile(data)
    info = inspect_recording(data)
    assert info["integrity"] == "missing-meta"
    assert info["samples_on_disk"] == 100

    meta = recover_recording_meta(data, sample_rate_hz=2_048_000.0,
                                  center_freq_hz=100e6)
    assert meta is not None
    with open(meta, "r", encoding="utf-8") as f:
        m = json.load(f)
    g = m["global"]
    assert g["core:total_num_samples"] == 100
    assert g["mbdsdr:complete"] is False
    assert g["mbdsdr:recovered"] is True
    assert inspect_recording(data)["integrity"] == "incomplete"

    pb = IQPlayback(loop=False)
    assert pb.open(data) is True
    assert pb.declared_samples == 100
    assert len(pb.read_samples(10)) == 10
    pb.close()


def test_recover_refuses_truncated_bytes(tmp_path):
    data = str(tmp_path / "bad.sigmf-data")
    with open(data, "wb") as f:
        f.write(b"\x00" * 12)
    assert recover_recording_meta(data, 2_048_000.0) is None


def test_inspect_detects_truncated_against_declared(tmp_path):
    data = str(tmp_path / "declared.sigmf-data")
    np.zeros(50, dtype=np.complex64).tofile(data)
    with open(str(tmp_path / "declared.sigmf-meta"), "w",
              encoding="utf-8") as f:
        json.dump({"global": {"core:datatype": "cf32_le",
                              "core:sample_rate": 2_000_000,
                              "core:total_num_samples": 100}}, f)
    info = inspect_recording(data)
    assert info["integrity"] == "truncated"
    assert info["samples_on_disk"] == 50
