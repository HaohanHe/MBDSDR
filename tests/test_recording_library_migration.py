# SPDX-License-Identifier: MIT
"""录音库迁移/导出与格式版本（P9-5 + 兼容守卫）。"""
import json
import os
import shutil
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.recorder import IQRecorder  # noqa: E402
from mbdsdr_ai.recording_library import (  # noqa: E402
    LIBRARY_FORMAT_VERSION, RecordingLibrary,
)


def make_recording(root, name, *, n=8, sr=1.0e6):
    rec = IQRecorder(str(root / name), sample_rate=sr,
                     center_freq_hz=100.0e6, device="RTL-SDR")
    assert rec.open()
    rec.write(np.ones(n, dtype=np.complex64))
    assert rec.close()
    return str(root / (name + ".sigmf-data"))


def test_export_copies_files_and_index_is_usable(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    lib = RecordingLibrary(str(src))
    data = make_recording(src, "a")
    lib.add_recording(data)

    res = lib.export_to(str(dst))
    assert res.ok and res.code == "exported"
    assert os.path.exists(os.path.join(str(dst), "library.json"))
    assert os.path.exists(data)                       # 源库不动

    exported = RecordingLibrary(str(dst))
    loaded = exported.load()
    assert loaded.ok
    assert len(loaded.data["entries"]) == 1
    entry = loaded.data["entries"][0]
    assert entry["path"]["kind"] == "relative"
    assert os.path.exists(exported._resolve_path(entry["path"]))
    assert exported.reconcile().code == "clean"


def test_export_selected_ids_and_unknown_ids(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    lib = RecordingLibrary(str(src))
    ids = [lib.add_recording(make_recording(src, f"x{i}")).data["entry_id"]
           for i in range(2)]
    res = lib.export_to(str(dst), entry_ids=[ids[0], "not-exist"])
    assert res.ok
    assert res.data["unknown_entry_ids"] == ["not-exist"]
    loaded = RecordingLibrary(str(dst)).load()
    assert len(loaded.data["entries"]) == 1
    assert loaded.data["entries"][0]["entry_id"] == ids[0]


def test_export_reports_missing_source_without_failing(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    lib = RecordingLibrary(str(src))
    data = make_recording(src, "gone")
    lib.add_recording(data)
    os.remove(data)
    os.remove(os.path.splitext(data)[0] + ".sigmf-meta")
    res = lib.export_to(str(dst))
    assert res.ok
    assert res.data["skipped"][0]["reason"] == "file_missing"
    assert RecordingLibrary(str(dst)).load().data["entries"] == []


def test_export_refuses_to_clobber_existing_library(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    lib = RecordingLibrary(str(src))
    lib.add_recording(make_recording(src, "a"))
    with open(dst / "library.json", "w", encoding="utf-8") as f:
        json.dump({"format_version": 1, "entries": []}, f)
    res = lib.export_to(str(dst))
    assert not res.ok and res.code == "dest_not_empty"
    res2 = lib.export_to(str(dst), overwrite=True)
    assert res2.ok and res2.code == "exported"


def test_relocate_after_directory_move(tmp_path):
    old = tmp_path / "old_lib"
    new = tmp_path / "new_lib"
    old.mkdir()
    lib = RecordingLibrary(str(old))
    data = make_recording(old, "movable")
    entry_id = lib.add_recording(data).data["entry_id"]
    shutil.move(str(old), str(new))

    res = RecordingLibrary(str(old)).relocate(str(new))
    assert res.ok and res.code == "relocated"
    assert res.data["rebased"] == 0
    assert res.data["missing"] == []

    moved = RecordingLibrary(str(new))
    loaded = moved.load()
    assert loaded.ok
    entry = next(e for e in loaded.data["entries"]
                 if e["entry_id"] == entry_id)
    assert os.path.exists(moved._resolve_path(entry["path"]))
    assert moved.reconcile().code == "clean"
    assert moved.load().data["library"]["root"] == str(new)


def test_relocate_keeps_external_absolute_path(tmp_path):
    lib_root = tmp_path / "lib"
    lib_root.mkdir()
    external_dir = tmp_path / "external"
    external_dir.mkdir()
    external = make_recording(external_dir, "cell")
    lib = RecordingLibrary(str(lib_root))
    entry_id = lib.add_recording(external).data["entry_id"]
    entry = lib.load().data["entries"][0]
    assert entry["path"]["kind"] == "absolute"

    moved_root = tmp_path / "lib_moved"
    shutil.move(str(lib_root), str(moved_root))
    res = RecordingLibrary(str(lib_root)).relocate(str(moved_root))
    assert res.ok
    moved = RecordingLibrary(str(moved_root))
    entry = next(e for e in moved.load().data["entries"]
                 if e["entry_id"] == entry_id)
    assert entry["path"]["kind"] == "absolute"
    assert moved._resolve_path(entry["path"]) == os.path.abspath(external)
    assert os.path.exists(external)


def test_store_path_falls_back_to_absolute_when_relpath_raises(tmp_path, monkeypatch):
    lib_root = tmp_path / "lib"
    lib_root.mkdir()
    external = make_recording(tmp_path, "ext")
    lib = RecordingLibrary(str(lib_root))
    real_relpath = os.path.relpath

    def _raise(*args, **kwargs):
        raise ValueError("simulated cross-drive")

    monkeypatch.setattr(os.path, "relpath", _raise)
    res = lib.add_recording(external)
    monkeypatch.setattr(os.path, "relpath", real_relpath)
    assert res.ok
    entry = res.data["entry"]
    assert entry["path"]["kind"] == "absolute"
    assert entry["path"]["value"] == os.path.abspath(external)


def test_newer_format_version_is_refused_without_touching_file(tmp_path):
    root = tmp_path / "lib"
    root.mkdir()
    payload = {"format_version": 999, "entries": [], "revision": 7}
    with open(root / "library.json", "w", encoding="utf-8") as f:
        json.dump(payload, f)
    lib = RecordingLibrary(str(root))
    res = lib.load()
    assert not res.ok and res.code == "unsupported_version"
    assert str(LIBRARY_FORMAT_VERSION) in res.message

    fix = lib.reconcile(apply=True)
    assert not fix.ok and fix.code == "unsupported_version"
    with open(root / "library.json", "r", encoding="utf-8") as f:
        assert json.load(f)["format_version"] == 999


def test_legacy_library_without_version_is_read_and_upgraded(tmp_path):
    root = tmp_path / "lib"
    root.mkdir()
    data = make_recording(root, "legacy")
    legacy = {
        "entries": [{
            "id": "legacy-1",
            "path": "legacy.sigmf-data",
            "state": "ok",
        }],
    }
    with open(root / "library.json", "w", encoding="utf-8") as f:
        json.dump(legacy, f)

    lib = RecordingLibrary(str(root))
    loaded = lib.load()
    assert loaded.ok
    assert loaded.data["migrated_from"] == 0
    assert loaded.data["entries"][0]["entry_id"] == "legacy-1"

    up = lib.upgrade_format()
    assert up.ok and up.code == "upgraded"
    with open(root / "library.json", "r", encoding="utf-8") as f:
        doc = json.load(f)
    assert doc["format_version"] == LIBRARY_FORMAT_VERSION
    assert doc["entries"][0]["entry_id"] == "legacy-1"
    assert lib.upgrade_format().code == "up_to_date"
    assert os.path.exists(data)


def test_export_external_entry_goes_to_external_dir(tmp_path):
    lib_root = tmp_path / "lib"
    lib_root.mkdir()
    external_dir = tmp_path / "ext"
    external_dir.mkdir()
    external = make_recording(external_dir, "outside")
    lib = RecordingLibrary(str(lib_root))
    lib.add_recording(external)
    dst = tmp_path / "dst"
    res = lib.export_to(str(dst))
    assert res.ok
    exported = RecordingLibrary(str(dst)).load().data["entries"][0]
    assert exported["path"]["value"].startswith("_external/")
    assert os.path.exists(os.path.join(str(dst), exported["path"]["value"]))
    assert RecordingLibrary(str(dst)).reconcile().code == "clean"
