# SPDX-License-Identifier: MIT
"""录音库（P9-1）：索引与磁盘双向核对，修复留痕、不删用户数据。

全部使用临时目录里的真实小文件，不依赖硬件。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.recorder import IQRecorder  # noqa: E402
from mbdsdr_ai.recording_library import RecordingLibrary  # noqa: E402


def make_recording(root, name, *, n=32, sr=1.0e6, freq=100.0e6, device=""):
    """用真实 IQRecorder 生成一份完整 SigMF 录制。"""
    rec = IQRecorder(str(root / name), sample_rate=sr, center_freq_hz=freq,
                     device=device)
    assert rec.open()
    rec.write(np.ones(n, dtype=np.complex64))
    assert rec.close()
    return str(root / (name + ".sigmf-data"))


def write_orphan_iq(root, name, *, n=16):
    """只写数据、不写 meta，模拟录制中断电。"""
    path = str(root / (name + ".sigmf-data"))
    with open(path, "wb") as f:
        f.write(np.zeros(n, dtype=np.complex64).tobytes())
    return path


# ── P9-1 索引与磁盘一致 ────────────────────────────────────────────────
def test_add_recording_reads_honest_metadata(tmp_path):
    data = make_recording(tmp_path, "rec1", n=64, sr=2.0e6, freq=100.0e6,
                          device="RTL-SDR")
    lib = RecordingLibrary(str(tmp_path))
    res = lib.add_recording(data)
    assert res.ok and res.code == "ok"
    entry = res.data["entry"]
    assert entry["sample_rate_hz"] == pytest.approx(2.0e6)
    assert entry["center_freq_hz"] == pytest.approx(100.0e6)
    assert entry["device"] == "RTL-SDR"
    assert entry["num_samples"] == 64
    assert entry["duration_s"] == pytest.approx(64 / 2.0e6)
    assert entry["complete"] is True
    assert entry["state"] == "ok"
    assert entry["unknown_fields"] == []


def test_external_delete_is_detected_and_marked_not_removed(tmp_path):
    data = make_recording(tmp_path, "gone", n=32)
    lib = RecordingLibrary(str(tmp_path))
    entry_id = lib.add_recording(data).data["entry_id"]
    os.remove(data)
    os.remove(os.path.splitext(data)[0] + ".sigmf-meta")

    dry = lib.reconcile()
    assert dry.ok and dry.code == "dry_run"
    assert any(a["action"] == "mark_missing" for a in dry.data["actions"])

    applied = lib.reconcile(apply=True)
    assert applied.ok and applied.code == "applied"
    loaded = lib.load()
    assert loaded.ok
    assert len(loaded.data["entries"]) == 1          # 条目保留，不静默删除
    assert loaded.data["entries"][0]["entry_id"] == entry_id
    assert loaded.data["entries"][0]["state"] == "missing"

    records = lib.journal().data["records"]
    assert any(r["action"] == "mark_missing" and r["entry_id"] == entry_id
               for r in records)


def test_external_rename_is_adopted_by_fingerprint(tmp_path):
    data = make_recording(tmp_path, "old_name", n=40)
    lib = RecordingLibrary(str(tmp_path))
    entry_id = lib.add_recording(data).data["entry_id"]
    meta = os.path.splitext(data)[0] + ".sigmf-meta"
    new_data = str(tmp_path / "new_name.sigmf-data")
    new_meta = str(tmp_path / "new_name.sigmf-meta")
    os.replace(data, new_data)
    os.replace(meta, new_meta)

    applied = lib.reconcile(apply=True)
    assert applied.ok
    moved = [a for a in applied.data["actions"] if a["action"] == "adopt_moved"]
    assert moved and moved[0]["entry_id"] == entry_id
    assert moved[0]["detail"]["match"] == "size+fingerprint"

    entry = next(e for e in lib.load().data["entries"]
                 if e["entry_id"] == entry_id)
    assert entry["path"]["value"] == "new_name.sigmf-data"
    assert entry["state"] == "ok"
    assert os.path.exists(new_data)


def test_untracked_disk_file_is_adopted(tmp_path):
    first = make_recording(tmp_path, "one", n=16)
    lib = RecordingLibrary(str(tmp_path))
    lib.add_recording(first)
    second = make_recording(tmp_path, "two", n=16)

    applied = lib.reconcile(apply=True)
    assert applied.ok
    adopted = [a for a in applied.data["actions"] if a["action"] == "adopt_new"]
    assert len(adopted) == 1
    assert os.path.abspath(adopted[0]["path"]) == os.path.abspath(second)
    paths = {e["path"]["value"] for e in lib.load().data["entries"]}
    assert {"one.sigmf-data", "two.sigmf-data"} <= paths


def test_external_change_refreshes_entry_and_reconcile_is_idempotent(tmp_path):
    data = make_recording(tmp_path, "grow", n=16)
    lib = RecordingLibrary(str(tmp_path))
    lib.add_recording(data)
    with open(data, "ab") as f:
        f.write(np.zeros(8, dtype=np.complex64).tobytes())

    dry = lib.reconcile()
    assert dry.code == "dry_run"
    assert any(a["action"] == "refresh_entry" for a in dry.data["actions"])
    lib.reconcile(apply=True)
    entry = lib.load().data["entries"][0]
    assert entry["size_bytes"] == 24 * 8
    assert entry["duration_s"] == pytest.approx(24 / 1e6)
    # 应用后的第二次核对必须直接判 clean，不能反复改
    assert lib.reconcile().code == "clean"


def test_reconcile_never_deletes_files(tmp_path):
    data = make_recording(tmp_path, "keep", n=8)
    lib = RecordingLibrary(str(tmp_path))
    lib.add_recording(data)
    os.remove(data)
    os.remove(os.path.splitext(data)[0] + ".sigmf-meta")
    stray = write_orphan_iq(tmp_path, "stray", n=8)
    lib.reconcile(apply=True)
    assert os.path.exists(stray)                 # 新发现文件原样保留
    assert not os.path.exists(data)              # 已被外部删除的不可能被本库复活
    assert lib.journal().ok


