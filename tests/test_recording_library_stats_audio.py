# SPDX-License-Identifier: MIT
"""录音库补充覆盖：WAV 音频、健康摘要、损坏库拒绝改写（非阻塞加固）。"""
import os
import sys
import wave as _wave
from datetime import datetime, timezone

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.recorder import IQRecorder  # noqa: E402
from mbdsdr_ai.recording_library import (  # noqa: E402
    LIBRARY_FORMAT_VERSION, RecordingLibrary, RetentionPolicy,
)

NOW = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)


def make_recording(root, name, *, n=8):
    rec = IQRecorder(str(root / name), sample_rate=1.0e6,
                     center_freq_hz=100.0e6, device="RTL-SDR")
    assert rec.open()
    rec.write(np.ones(n, dtype=np.complex64))
    assert rec.close()
    return str(root / (name + ".sigmf-data"))


def make_wav(root, name, *, rate=8000, frames=8000):
    path = str(root / (name + ".wav"))
    with _wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * frames)
    return path


def test_wav_entry_metadata_is_honest_and_reconciles_clean(tmp_path):
    path = make_wav(tmp_path, "voice")
    lib = RecordingLibrary(str(tmp_path))
    res = lib.add_recording(path)
    assert res.ok
    entry = res.data["entry"]
    assert entry["kind"] == "audio"
    assert entry["sample_rate_hz"] == 8000.0
    assert entry["num_samples"] == 8000
    assert entry["duration_s"] == 1.0
    assert entry["start_time"] is None
    assert entry["device"] is None
    assert entry["center_freq_hz"] is None
    assert set(entry["unknown_fields"]) == {
        "center_freq_hz", "start_time", "device"}
    assert lib.reconcile().code == "clean"


def test_stats_summarizes_states_and_trace(tmp_path):
    lib = RecordingLibrary(str(tmp_path))
    items = []
    for i in range(3):
        data = make_recording(tmp_path, f"st{i}")
        entry_id = lib.add_recording(data).data["entry_id"]
        items.append((entry_id, data))
    os.remove(items[1][1])
    os.remove(os.path.splitext(items[1][1])[0] + ".sigmf-meta")
    lib.reconcile(apply=True)
    # 核对之后再写旧时间，避免被磁盘 meta 回填覆盖
    assert lib.update_entry(items[0][0],
                            start_time="2026-01-01T00:00:00+00:00").ok
    moved = lib.apply_retention(RetentionPolicy(max_age_days=30),
                                confirm=True, now=NOW)
    assert moved.ok and moved.code == "applied"

    stats = lib.stats()
    assert stats.ok
    d = stats.data
    assert d["entries_total"] == 3
    assert d["by_state"] == {"ok": 1, "missing": 1, "trashed": 1}
    assert d["active_entries"] == 1
    assert d["active_bytes"] == 64
    assert d["missing_entries"] == 1
    assert d["journal_records"] >= 2              # mark_missing + retention_move
    assert d["format_version"] == LIBRARY_FORMAT_VERSION


def test_corrupt_library_is_reported_and_never_overwritten(tmp_path):
    root = tmp_path / "lib"
    root.mkdir()
    path = root / "library.json"
    path.write_text("{ this is not json", encoding="utf-8")
    lib = RecordingLibrary(str(root))
    res = lib.load()
    assert not res.ok and res.code == "corrupt_library"
    fix = lib.reconcile(apply=True)
    assert not fix.ok and fix.code == "corrupt_library"
    assert path.read_text(encoding="utf-8") == "{ this is not json"
