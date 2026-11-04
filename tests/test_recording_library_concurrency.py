# SPDX-License-Identifier: MIT
"""录音库并发（P9-3）：同时写、读时写、库被占用都不损坏、不丢记录。"""
import json
import os
import sys
import threading

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.recorder import IQRecorder  # noqa: E402
from mbdsdr_ai.recording_library import RecordingLibrary  # noqa: E402


def make_recording(root, name, *, n=8):
    rec = IQRecorder(str(root / name), sample_rate=1.0e6,
                     center_freq_hz=100.0e6, device="RTL-SDR")
    assert rec.open()
    rec.write(np.ones(n, dtype=np.complex64))
    assert rec.close()
    return str(root / (name + ".sigmf-data"))


def test_concurrent_adds_do_not_lose_records(tmp_path):
    files = [make_recording(tmp_path, f"r{i}", n=8) for i in range(20)]
    lib = RecordingLibrary(str(tmp_path))
    barrier = threading.Barrier(2)
    results = []

    def worker(chunk):
        barrier.wait(timeout=10)
        for path in chunk:
            results.append(lib.add_recording(path))

    threads = [
        threading.Thread(target=worker, args=(files[:10],)),
        threading.Thread(target=worker, args=(files[10:],)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert all(r.ok for r in results)
    loaded = lib.load()
    assert loaded.ok
    assert len(loaded.data["entries"]) == 20
    ids = {e["entry_id"] for e in loaded.data["entries"]}
    assert len(ids) == 20
    with open(os.path.join(str(tmp_path), "library.json"), "r",
              encoding="utf-8") as f:
        doc = json.load(f)
    assert len(doc["entries"]) == 20


def test_optimistic_update_conflict_is_explicit(tmp_path):
    data = make_recording(tmp_path, "one")
    lib = RecordingLibrary(str(tmp_path))
    entry_id = lib.add_recording(data).data["entry_id"]
    base = lib.load().data["entries"][0]
    assert base["revision"] == 1

    barrier = threading.Barrier(2)
    results = []

    def worker(note):
        barrier.wait(timeout=10)
        results.append(lib.update_entry(entry_id, expected_revision=1,
                                       notes=note))

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    ok_results = [r for r in results if r.ok]
    conflicts = [r for r in results if r.code == "revision_conflict"]
    assert len(ok_results) == 1 and len(conflicts) == 1
    assert conflicts[0].ok is False
    assert conflicts[0].data["current_revision"] == 2
    assert conflicts[0].data["expected_revision"] == 1
    assert conflicts[0].data["current"]["notes"] == ok_results[0].data["changed"]["notes"]["to"]

    entry = lib.load().data["entries"][0]
    assert entry["revision"] == 2
    assert entry["notes"] == ok_results[0].data["changed"]["notes"]["to"]


def test_last_writer_wins_is_labelled(tmp_path):
    data = make_recording(tmp_path, "lww")
    lib = RecordingLibrary(str(tmp_path))
    entry_id = lib.add_recording(data).data["entry_id"]
    barrier = threading.Barrier(2)
    results = []

    def worker(note):
        barrier.wait(timeout=10)
        results.append(lib.update_entry(entry_id, notes=note))

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("X", "Y")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert all(r.ok for r in results)
    assert all(r.data["mode"] == "last_writer_wins" for r in results)
    entry = lib.load().data["entries"][0]
    assert entry["revision"] == 3
    assert entry["notes"] in ("X", "Y")


def test_read_during_writes_never_sees_partial_library(tmp_path):
    files = [make_recording(tmp_path, f"w{i}", n=8) for i in range(30)]
    lib = RecordingLibrary(str(tmp_path))
    stop = threading.Event()
    read_errors = []

    def writer():
        for path in files:
            r = lib.add_recording(path)
            if not r.ok:
                read_errors.append(("write", r.code, r.message))

    def reader():
        while not stop.is_set():
            loaded = lib.load()
            if not loaded.ok and loaded.code != "not_found":
                read_errors.append(("read", loaded.code, loaded.message))
                return
            try:
                with open(lib.library_path, "r", encoding="utf-8") as f:
                    doc = json.load(f)
            except FileNotFoundError:
                continue
            except (OSError, json.JSONDecodeError) as exc:
                read_errors.append(("parse", type(exc).__name__, str(exc)))
                return
            assert isinstance(doc.get("entries"), list)

    w = threading.Thread(target=writer)
    r = threading.Thread(target=reader)
    w.start()
    r.start()
    w.join(timeout=60)
    stop.set()
    r.join(timeout=30)

    assert not read_errors, read_errors
    assert len(lib.load().data["entries"]) == 30


def test_locked_library_reports_busy(tmp_path):
    lib = RecordingLibrary(str(tmp_path))
    data = make_recording(tmp_path, "busy")
    holding = threading.Event()
    release = threading.Event()

    def holder():
        with lib._lock():
            holding.set()
            release.wait(timeout=10)

    t = threading.Thread(target=holder)
    t.start()
    assert holding.wait(timeout=10)
    try:
        res = RecordingLibrary(str(tmp_path), lock_timeout_s=0.1).add_recording(data)
        assert res.ok is False
        assert res.code == "library_busy"
    finally:
        release.set()
        t.join(timeout=10)


def test_update_rejects_unknown_field(tmp_path):
    data = make_recording(tmp_path, "fields")
    lib = RecordingLibrary(str(tmp_path))
    entry_id = lib.add_recording(data).data["entry_id"]
    res = lib.update_entry(entry_id, not_a_field="x")
    assert not res.ok and res.code == "bad_field"
