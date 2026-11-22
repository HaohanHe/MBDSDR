# SPDX-License-Identifier: MIT
"""Pure-data archive fixtures, NOT hardware recordings or RF evidence."""
from copy import deepcopy
from datetime import datetime, timezone
import builtins
import io
import json
import os
from pathlib import Path
import sys

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from desktop.recording_observation import export_desktop_observations
from mbdsdr_ai.observation_record import (
    MAX_TIMELINE_EVENTS, ObservationRecord, load_record, replay_plan,
)


@pytest.fixture
def snapshot():
    return {
        "recording_started_at": "2026-11-20T08:00:00+08:00",
        "time": {"source": "system", "time_source": "none",
                 "last_update_utc": None},
        "site": {"source": "manual", "lat_deg": 0.0, "lon_deg": 0.0,
                 "alt_m": None, "reason": "neutral fixture site"},
        "sdr": {"connected": False, "device": "fixture only"},
    }


def _segment(tmp_path, *, index=1, samples=80, rate=40,
             start="2026-11-20T08:00:00+08:00", changes=None):
    path = tmp_path / f"fixture-{index}.iq"
    # Tiny on-disk fixture only: no receiver, decoder, or IQ library involved.
    path.write_bytes(b"\0" * (samples * 8))
    meta = {"sample_rate": rate, "samples": samples,
            "duration_s": round(samples / rate, 3), "center_freq_hz": 100_000_000,
            "format": "cf32", "start_time": start, "complete": True,
            "segment_index": index, "gain_db": 7, "device": "fixture device",
            "driver": "fixture driver"}
    if changes is not None:
        meta["center_freq_changes"] = changes
        if changes:
            meta["center_freq_hz"] = changes[-1]["to_hz"]
    sidecar = Path(str(path) + ".json")
    sidecar.write_text(json.dumps(meta), encoding="utf-8")
    segment = {"path": str(path), "sidecar": str(sidecar), "samples": samples,
               "sample_rate": rate, "start_time": start, "segment_index": index,
               "size_bytes": samples * 8, "complete": True, "aborted_reason": ""}
    return segment, meta


def _save(segment, meta):
    Path(segment["sidecar"]).write_text(json.dumps(meta), encoding="utf-8")


def _change(sample=20, before=100_000_000, after=101_000_000, **extra):
    return {"start_sample": sample, "from_hz": before, "to_hz": after,
            "at_time": "2026-11-20T08:00:01", **extra}


def _load(result):
    assert result["errors"] == [], result
    assert len(result["archives"]) == 1
    archive = result["archives"][0]
    assert Path(archive).is_absolute()
    record, error = load_record(archive)
    assert error is None
    assert record is not None
    assert record.validate_for_export() == []
    return record


def _assert_failed(result, segment=None):
    assert result["archives"] == []
    assert result["errors"] and all(result["errors"])
    if segment:
        assert segment["path"] in result["errors"][0]
        assert not Path(segment["path"]).with_suffix(".observation.json").exists()


def test_single_segment_roundtrip_uses_samples_and_honest_sources(tmp_path, snapshot):
    changes = [_change(), _change(60, 101_000_000, 99_000_000)]
    segment, _ = _segment(tmp_path, changes=changes)
    original_snapshot = deepcopy(snapshot)
    original_segment = deepcopy(segment)
    data_before = Path(segment["path"]).read_bytes()
    meta_before = Path(segment["sidecar"]).read_bytes()
    record = _load(export_desktop_observations(
        [segment], snapshot, ended_utc="2026-11-20T08:00:03+08:00"))
    assert record.evidence == "unverified"
    assert record.started_utc == "2026-11-20T00:00:00+00:00"
    assert record.ended_utc == "2026-11-20T00:00:02+00:00"
    assert record.time["session_ended_utc"] == "2026-11-20T00:00:03+00:00"
    assert record.time["end_utc_source"] == "sample_offset"
    assert record.tuning["center_freq_hz"] == 100_000_000
    assert record.device["source"] == "desktop_sidecar"
    assert record.device["name"] == "fixture device"
    assert record.device["serial"] is None
    assert record.products[0]["path"] == segment["sidecar"]
    assert record.products[0]["data_path"] == segment["path"]
    assert record.products[0]["kind"] == "desktop_sidecar"
    assert record.products[0]["final_center_freq_hz"] == 99_000_000
    assert record.products[0]["sha256"] is None
    assert "SigMF" not in record.to_json()
    assert [event["kind"] for event in record.timeline] == [
        "record_start", "tune", "tune", "record_stop"]
    assert [event["samples"] for event in record.timeline] == [0, 20, 60, 80]
    assert all(event["detail"]["source"] == "desktop_sidecar" for event in record.timeline)
    assert record.timeline[1]["detail"]["at_time"] == changes[0]["at_time"]
    plan = replay_plan(record)
    assert plan["can_replay"], plan["blockers"]
    assert plan["data_paths"] == [segment["path"]]
    assert plan["total_samples"] == 80
    assert plan["duration_s"] == 2
    assert [step["sample"] for step in plan["steps"]] == [0, 20, 60, 80]
    assert [step["at_s"] for step in plan["steps"]] == [0, 0.5, 1.5, 2]
    assert [step["state"]["center_freq_hz"] for step in plan["steps"]] == [
        100_000_000, 101_000_000, 99_000_000, 99_000_000]
    assert all(step["index_source"] == "samples" for step in plan["steps"])
    assert snapshot == original_snapshot and segment == original_segment
    assert Path(segment["path"]).read_bytes() == data_before
    assert Path(segment["sidecar"]).read_bytes() == meta_before


def test_multirate_segments_keep_own_start_count_and_rate(tmp_path, snapshot):
    first, _ = _segment(tmp_path, start="2026-11-20T08:00:00")
    second, second_meta = _segment(
        tmp_path, index=2, samples=100, rate=25, start="2026-11-20T08:00:10",
        changes=[_change(25, segment_index=2)])
    # Session totals/global offsets are NOT a segment's replay coordinates.
    second_meta.update(session_samples=180, session_duration_s=6,
                       segments=[{"start_sample": 80, "samples": 100}])
    _save(second, second_meta)
    result = export_desktop_observations(
        [first, second], snapshot, ended_utc="2026-11-20T00:00:15Z")
    assert result["errors"] == []
    assert len(result["archives"]) == 2
    records = [load_record(path)[0] for path in result["archives"]]
    assert [record.started_utc for record in records] == [
        "2026-11-20T00:00:00+00:00", "2026-11-20T00:00:10+00:00"]
    assert [record.ended_utc for record in records] == [
        "2026-11-20T00:00:02+00:00", "2026-11-20T00:00:14+00:00"]
    plans = [replay_plan(record) for record in records]
    assert all(plan["can_replay"] for plan in plans)
    assert [plan["sample_rate_hz"] for plan in plans] == [40, 25]
    assert [plan["total_samples"] for plan in plans] == [80, 100]
    assert [step["sample"] for step in plans[1]["steps"]] == [0, 25, 100]
    assert [step["at_s"] for step in plans[1]["steps"]] == [0, 1, 4]


def test_naive_time_requires_explicit_start_offset_not_gnss_last_update(tmp_path, snapshot):
    segment, _ = _segment(tmp_path, start="2026-11-20T08:00:00")
    snapshot.pop("recording_started_at")
    snapshot["time"] = {"source": "gnss", "time_source": "nmea",
                        "last_update_utc": "2026-11-20T00:00:00Z"}
    result = export_desktop_observations([segment], snapshot)
    _assert_failed(result, segment)
    assert "recording_started_at" in result["errors"][0]
    snapshot["recording_started_at"] = "2026-11-20T08:00:00+08:00"
    record = _load(export_desktop_observations([segment], snapshot))
    assert record.started_utc == "2026-11-20T00:00:00+00:00"
    assert record.time["source"] == "system"
    assert record.time["runtime_snapshot_time"]["source"] == "gnss"
    assert "recording_started_at.offset" in record.time["start_utc_source"]
    assert any("无时区" in note for note in record.notes)


@pytest.mark.parametrize("site", [None, {}, {"source": "none", "lat_deg": 12, "lon_deg": 34},
                                  {"source": "manual", "lat_deg": None, "lon_deg": None}])
def test_unknown_site_remains_replay_blocker_but_iq_is_available(tmp_path, snapshot, site):
    segment, _ = _segment(tmp_path)
    snapshot["site"] = site
    record = _load(export_desktop_observations([segment], snapshot))
    assert record.site["source"] == "none"
    assert record.site["lat_deg"] is None and record.site["lon_deg"] is None
    plan = replay_plan(record)
    assert not plan["can_replay"]
    assert any("站址" in blocker for blocker in plan["blockers"])
    assert plan["data_paths"] == [segment["path"]]


def test_aware_sidecar_needs_no_snapshot_and_accepts_datetime_stop(tmp_path):
    segment, _ = _segment(tmp_path)
    record = _load(export_desktop_observations(
        [segment], None, ended_utc=datetime(2026, 11, 20, 0, 0, 3, tzinfo=timezone.utc)))
    assert record.started_utc == "2026-11-20T00:00:00+00:00"
    assert [event["kind"] for event in record.timeline] == ["record_start", "record_stop"]


@pytest.mark.parametrize("field,value", [
    ("complete", False), ("complete", "true"), ("aborted_reason", "fixture write failure"),
    ("format", "cu8"), ("samples", 0), ("samples", 1.5), ("samples", True),
    ("sample_rate", 0), ("sample_rate", -1), ("sample_rate", float("inf")),
    ("sample_rate", float("nan")), ("sample_rate", "40"),
    ("center_freq_hz", 0), ("center_freq_hz", float("nan")), ("gain_db", float("inf")),
    ("duration_s", 3), ("start_time", "not-a-date"), ("segment_index", 0),
])
def test_invalid_metadata_is_not_downgraded(tmp_path, snapshot, field, value):
    segment, meta = _segment(tmp_path)
    meta[field] = value
    _save(segment, meta)
    _assert_failed(export_desktop_observations([segment], snapshot), segment)


@pytest.mark.parametrize("changes", [
    None, {}, [None], [_change(-1)], [_change(81)], [_change(0.5)], [_change(True)],
    [_change(60), _change(20, 101_000_000, 102_000_000)],
    [_change(), _change(40, 99_000_000, 102_000_000)],
    [_change(after=100_000_001)], [_change(at_time="bad-time")],
    [_change(segment_index=2)], [_change(actual_hz=102_000_000)],
    [_change(target_hz=float("nan"))], [_change(before=0)],
    [{"start_sample": 20, "to_hz": 101_000_000, "at_time": "2026-11-20T08:00:01"}],
    [_change()] * MAX_TIMELINE_EVENTS,
])
def test_bad_frequency_history_refuses_entire_segment(tmp_path, snapshot, changes):
    segment, meta = _segment(tmp_path)
    meta["center_freq_hz"] = 101_000_000
    meta["center_freq_changes"] = changes
    _save(segment, meta)
    _assert_failed(export_desktop_observations([segment], snapshot), segment)


def test_tunes_at_segment_boundaries_keep_sample_order(tmp_path, snapshot):
    segment, _ = _segment(tmp_path, changes=[
        _change(0), _change(0, 101_000_000, 102_000_000),
        _change(80, 102_000_000, 103_000_000)])
    record = _load(export_desktop_observations([segment], snapshot))
    plan = replay_plan(record)
    assert plan["can_replay"]
    assert [step["sample"] for step in plan["steps"]] == [0, 0, 0, 80, 80]
    assert [step["kind"] for step in plan["steps"]] == [
        "record_start", "tune", "tune", "tune", "record_stop"]


@pytest.mark.parametrize("field,value", [
    ("samples", 79), ("sample_rate", 41), ("size_bytes", 1), ("segment_index", 2),
    ("start_time", "2026-11-20T09:00:00"), ("complete", False),
    ("aborted_reason", "fixture aborted"), ("sidecar", None),
])
def test_segment_disagreement_is_visible(tmp_path, snapshot, field, value):
    segment, _ = _segment(tmp_path)
    segment[field] = value
    _assert_failed(export_desktop_observations([segment], snapshot), segment)


@pytest.mark.parametrize("content", ["{", "[]", "null", '{"samples":1,"samples":2}', "\udcff"])
def test_unreadable_or_illegal_sidecar(tmp_path, snapshot, content):
    segment, _ = _segment(tmp_path)
    Path(segment["sidecar"]).write_bytes(content.encode("utf-8", errors="surrogateescape"))
    _assert_failed(export_desktop_observations([segment], snapshot), segment)


@pytest.mark.parametrize("missing", ["path", "sidecar"])
def test_missing_source_file(tmp_path, snapshot, missing):
    segment, _ = _segment(tmp_path)
    Path(segment[missing]).unlink()
    _assert_failed(export_desktop_observations([segment], snapshot), segment)


@pytest.mark.parametrize("size", [0, 639, 641, 648])
def test_iq_stat_mismatch_does_not_repair_original(tmp_path, snapshot, size):
    segment, _ = _segment(tmp_path)
    Path(segment["path"]).write_bytes(b"\0" * size)
    before = Path(segment["sidecar"]).read_bytes()
    _assert_failed(export_desktop_observations([segment], snapshot), segment)
    assert Path(segment["path"]).stat().st_size == size
    assert Path(segment["sidecar"]).read_bytes() == before


def test_partial_result_keeps_valid_archives_and_failure_reason(tmp_path, snapshot):
    first, _ = _segment(tmp_path)
    broken, meta = _segment(tmp_path, index=2)
    meta.update(complete=False, aborted_reason="fixture disk failure")
    _save(broken, meta)
    third, _ = _segment(tmp_path, index=3)
    result = export_desktop_observations([first, broken, third], snapshot)
    assert len(result["archives"]) == 2
    assert len(result["errors"]) == 1
    assert broken["path"] in result["errors"][0]
    assert "fixture disk failure" in result["errors"][0]
    assert not Path(broken["path"]).with_suffix(".observation.json").exists()
    assert all(load_record(path)[1] is None for path in result["archives"])


@pytest.mark.parametrize("segments", [[], None, "not-segments", [None]])
def test_no_segments_or_bad_envelope(segments, snapshot):
    _assert_failed(export_desktop_observations(segments, snapshot))


def test_naive_stop_rejected(tmp_path, snapshot):
    segment, _ = _segment(tmp_path)
    _assert_failed(export_desktop_observations(
        [segment], snapshot, ended_utc="2026-11-20T08:00:03"))


def test_export_never_opens_iq_or_scans_directory(tmp_path, snapshot, monkeypatch):
    segment, _ = _segment(tmp_path)
    real_open, real_io_open = builtins.open, io.open
    real_walk, real_scandir = os.walk, os.scandir
    data = Path(segment["path"]).resolve()

    def guarded(delegate):
        def open_file(file, *args, **kwargs):
            if not isinstance(file, int):
                assert Path(file).resolve() != data, "export must stat IQ, never open it"
            return delegate(file, *args, **kwargs)
        return open_file

    def no_scan(*args, **kwargs):
        pytest.fail("export must not discover recordings by scanning directories")

    def private_cleanup_only(delegate):
        def scan(path, *args, **kwargs):
            # Python 3.14 Windows rmtree uses walk, older versions scandir.
            # Neither may inspect the recording directory or other sources.
            root = Path(path)
            assert root.parent == tmp_path
            assert root.name.startswith(".desktop-observation-")
            return delegate(path, *args, **kwargs)
        return scan

    monkeypatch.setattr(builtins, "open", guarded(real_open))
    monkeypatch.setattr(io, "open", guarded(real_io_open))
    monkeypatch.setattr(os, "listdir", no_scan)
    monkeypatch.setattr(os, "walk", private_cleanup_only(real_walk))
    monkeypatch.setattr(os, "scandir", private_cleanup_only(real_scandir))
    monkeypatch.setattr(Path, "glob", no_scan)
    monkeypatch.setattr(Path, "rglob", no_scan)
    _load(export_desktop_observations([segment], snapshot))


def test_atomic_writer_is_reused_and_only_finished_archive_is_published(tmp_path, snapshot, monkeypatch):
    segment, _ = _segment(tmp_path)
    destination = Path(segment["path"]).with_suffix(".observation.json")
    replace, link = os.replace, os.link
    calls = []

    def atomic_replace(src, dst):
        assert not destination.exists()
        calls.append("write_json.replace")
        return replace(src, dst)

    def publish(src, dst):
        assert calls == ["write_json.replace"]
        assert Path(dst) == destination
        assert not destination.exists()
        assert load_record(str(src))[1] is None
        calls.append("publish")
        return link(src, dst)

    monkeypatch.setattr(os, "replace", atomic_replace)
    monkeypatch.setattr(os, "link", publish)
    _load(export_desktop_observations([segment], snapshot))
    assert calls == ["write_json.replace", "publish"]
    assert not list(tmp_path.glob(".desktop-observation-*"))


def test_existing_archive_and_unrelated_tmp_are_never_overwritten(tmp_path, snapshot):
    segment, _ = _segment(tmp_path)
    destination = Path(segment["path"]).with_suffix(".observation.json")
    old_tmp = Path(str(destination) + ".tmp")
    old_tmp.write_text("unrelated temporary content", encoding="utf-8")
    _load(export_desktop_observations([segment], snapshot))
    original = destination.read_bytes()
    result = export_desktop_observations([segment], snapshot)
    assert result["archives"] == [] and result["errors"]
    assert destination.read_bytes() == original
    assert old_tmp.read_text(encoding="utf-8") == "unrelated temporary content"


def test_racing_destination_is_not_clobbered(tmp_path, snapshot, monkeypatch):
    segment, _ = _segment(tmp_path)
    link = os.link
    destination = Path(segment["path"]).with_suffix(".observation.json")

    def race(src, dst):
        Path(dst).write_text("another writer", encoding="utf-8")
        return link(src, dst)

    monkeypatch.setattr(os, "link", race)
    result = export_desktop_observations([segment], snapshot)
    assert result["archives"] == [] and result["errors"]
    assert destination.read_text(encoding="utf-8") == "another writer"
    assert not list(tmp_path.glob(".desktop-observation-*"))


@pytest.mark.parametrize("failure", ["write", "publish", "changed_source"])
def test_failed_atomic_export_is_visible_and_leaves_no_archive(tmp_path, snapshot, monkeypatch, failure):
    segment, _ = _segment(tmp_path)
    write_json = ObservationRecord.write_json

    if failure == "write":
        monkeypatch.setattr(ObservationRecord, "write_json",
                            lambda self, path: (False, "fixture write failure"))
    elif failure == "publish":
        def fail_link(*args, **kwargs):
            raise OSError("fixture publication failure")
        monkeypatch.setattr(os, "link", fail_link)
    else:
        def change_source(self, path):
            result = write_json(self, path)
            with Path(segment["path"]).open("ab") as stream:
                stream.write(b"\0" * 8)
            return result
        monkeypatch.setattr(ObservationRecord, "write_json", change_source)
    _assert_failed(export_desktop_observations([segment], snapshot), segment)
    assert not list(tmp_path.glob(".desktop-observation-*"))


def test_wrong_sidecar_cannot_be_used_for_another_iq(tmp_path, snapshot):
    segment, _ = _segment(tmp_path)
    other, _ = _segment(tmp_path, index=2)
    segment["sidecar"] = other["sidecar"]
    _assert_failed(export_desktop_observations([segment], snapshot), segment)


def test_oversized_metadata_is_refused_before_open(tmp_path, snapshot, monkeypatch):
    segment, _ = _segment(tmp_path)
    sidecar = Path(segment["sidecar"])
    sidecar.write_bytes(b" " * ((1 << 20) + 1))
    open_file = Path.open

    def no_metadata_read(self, *args, **kwargs):
        assert self != sidecar
        return open_file(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", no_metadata_read)
    result = export_desktop_observations([segment], snapshot)
    _assert_failed(result, segment)
    assert "过大" in result["errors"][0]


def test_snapshot_nested_data_is_detached_before_model_io(tmp_path, snapshot, monkeypatch):
    segment, _ = _segment(tmp_path)
    snapshot["time"]["details"] = {"fixture": ["original"]}
    original = deepcopy(snapshot)
    write_json = ObservationRecord.write_json

    def mutate_record(self, path):
        self.time["runtime_snapshot_time"]["details"]["fixture"].append("record-only")
        self.site["lat_deg"] = None
        return write_json(self, path)

    monkeypatch.setattr(ObservationRecord, "write_json", mutate_record)
    _load(export_desktop_observations([segment], snapshot))
    assert snapshot == original


@pytest.mark.parametrize("value", ["2026-11-20T08:00:00", "bad", None])
def test_bad_start_anchor_does_not_guess_timezone(tmp_path, snapshot, value):
    segment, _ = _segment(tmp_path, start="2026-11-20T08:00:00")
    snapshot["recording_started_at"] = value
    _assert_failed(export_desktop_observations([segment], snapshot), segment)


def test_session_stop_before_segment_start_is_rejected(tmp_path, snapshot):
    segment, _ = _segment(tmp_path)
    result = export_desktop_observations(
        [segment], snapshot, ended_utc="2026-11-19T00:00:00Z")
    _assert_failed(result, segment)
    assert "早于" in result["errors"][0]


def test_model_validation_failure_is_not_bypassed(tmp_path, snapshot, monkeypatch):
    segment, _ = _segment(tmp_path)
    monkeypatch.setattr(ObservationRecord, "validate_for_export",
                        lambda self: ["fixture validation rejected"])
    result = export_desktop_observations([segment], snapshot)
    _assert_failed(result, segment)
    assert "fixture validation rejected" in result["errors"][0]
