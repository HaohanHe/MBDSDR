# SPDX-License-Identifier: MIT
"""录音库保留策略（P9-4）：规则可解释、dry-run 可预演、执行需确认、不静默删。"""
import os
import sys
from datetime import datetime, timezone

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.recorder import IQRecorder  # noqa: E402
from mbdsdr_ai.recording_library import (  # noqa: E402
    RecordingLibrary, RetentionPolicy,
)

NOW = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)


def make_recording(root, name, *, n=8):
    rec = IQRecorder(str(root / name), sample_rate=1.0e6,
                     center_freq_hz=100.0e6, device="RTL-SDR")
    assert rec.open()
    rec.write(np.ones(n, dtype=np.complex64))
    assert rec.close()
    return str(root / (name + ".sigmf-data"))


def set_start_time(lib, entry_id, iso):
    res = lib.update_entry(entry_id, start_time=iso)
    assert res.ok
    return res


def test_capacity_dry_run_reports_candidates_without_changes(tmp_path):
    lib = RecordingLibrary(str(tmp_path))
    ids = []
    for i, days in enumerate((3.0, 2.0, 1.0)):
        data = make_recording(tmp_path, f"r{i}")
        entry_id = lib.add_recording(data).data["entry_id"]
        ids.append(entry_id)
        set_start_time(lib, entry_id,
                       (NOW.replace(hour=12) - _days(days)).isoformat())
    before = open(lib.library_path, "r", encoding="utf-8").read()

    policy = RetentionPolicy(max_total_bytes=100)   # 每条 64 字节，共 192
    plan = lib.plan_retention(policy, now=NOW)
    assert plan.ok and plan.code == "dry_run"
    data = plan.data
    assert data["active_bytes"] == 192
    assert data["would_free_bytes"] == 128          # 移出最旧两条
    assert [c["entry_id"] for c in data["candidates"]] == ids[:2]
    assert data["candidates"][0]["age_basis"] == "start_time"
    assert any("容量" in r for r in data["candidates"][0]["reasons"])
    assert data["kept_entries"] == 1
    # dry-run 不改索引、不动文件
    assert open(lib.library_path, "r", encoding="utf-8").read() == before
    for i in range(3):
        assert os.path.exists(str(tmp_path / f"r{i}.sigmf-data"))


def test_apply_requires_explicit_confirmation(tmp_path):
    data = make_recording(tmp_path, "c1")
    lib = RecordingLibrary(str(tmp_path))
    entry_id = lib.add_recording(data).data["entry_id"]
    set_start_time(lib, entry_id, "2026-01-01T00:00:00+00:00")
    policy = RetentionPolicy(max_age_days=30)
    res = lib.apply_retention(policy, now=NOW)
    assert not res.ok and res.code == "confirmation_required"
    assert res.data["plan"]["candidates"]
    assert os.path.exists(data)
    entry = lib.load().data["entries"][0]
    assert entry["state"] == "ok"


def test_apply_moves_to_trash_keeps_entry_and_trace(tmp_path):
    data = make_recording(tmp_path, "old")
    lib = RecordingLibrary(str(tmp_path))
    entry_id = lib.add_recording(data).data["entry_id"]
    set_start_time(lib, entry_id, "2026-01-01T00:00:00+00:00")

    policy = RetentionPolicy(max_age_days=30)
    res = lib.apply_retention(policy, confirm=True, now=NOW)
    assert res.ok and res.code == "applied"
    moved = res.data["moved"]
    assert len(moved) == 1 and moved[0]["entry_id"] == entry_id
    assert not os.path.exists(data)                  # 原位置已移走
    assert os.path.exists(moved[0]["to"])            # 但在回收站里还在
    meta_trash = os.path.splitext(moved[0]["to"])[0] + ".sigmf-meta"
    assert os.path.exists(meta_trash)

    entry = next(e for e in lib.load().data["entries"]
                 if e["entry_id"] == entry_id)
    assert entry["state"] == "trashed"
    assert entry["trash_path"] == moved[0]["to"]
    assert entry["revision"] > 1
    listed = lib.list_entries().data["entries"]
    assert all(e["state"] != "trashed" for e in listed)
    records = lib.journal().data["records"]
    assert any(r["action"] == "retention_move" and r["entry_id"] == entry_id
               for r in records)


def test_stale_plan_is_rejected(tmp_path):
    for i in range(2):
        data = make_recording(tmp_path, f"s{i}")
        lib = RecordingLibrary(str(tmp_path))
        entry_id = lib.add_recording(data).data["entry_id"]
        set_start_time(lib, entry_id, "2026-01-01T00:00:00+00:00" if i == 0
                       else "2026-02-01T00:00:00+00:00")
    policy = RetentionPolicy(max_entries=1)
    plan = lib.plan_retention(policy, now=NOW).data

    extra = make_recording(tmp_path, "extra")
    lib.add_recording(extra)
    res = lib.apply_retention(policy, confirm=True, plan=plan, now=NOW)
    assert not res.ok and res.code == "plan_stale"
    assert os.path.exists(extra)
    assert lib.load().data["entries"][0]["state"] == "ok"


def test_age_basis_is_reported_honestly(tmp_path):
    lib = RecordingLibrary(str(tmp_path))
    recorded = make_recording(tmp_path, "with_meta")
    rid = lib.add_recording(recorded).data["entry_id"]

    orphan = str(tmp_path / "no_meta.sigmf-data")
    with open(orphan, "wb") as f:
        f.write(np.zeros(8, dtype=np.complex64).tobytes())
    oid = lib.add_recording(orphan).data["entry_id"]
    old_ts = datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()
    os.utime(orphan, (old_ts, old_ts))
    lib.reconcile(apply=True)          # 刷新外部改动后的文件 mtime
    # 记录时间的修正放在核对之后，避免被磁盘 meta 回填覆盖
    set_start_time(lib, rid, "2026-09-01T00:00:00+00:00")

    plan = lib.plan_retention(RetentionPolicy(max_age_days=30), now=NOW).data
    cands = {c["entry_id"]: c for c in plan["candidates"]}
    assert set(cands) == {rid, oid}
    assert cands[rid]["age_basis"] == "start_time"
    assert cands[oid]["age_basis"] == "file_mtime"   # 没有录制时间，如实标注依据


def test_max_entries_rule(tmp_path):
    lib = RecordingLibrary(str(tmp_path))
    ids = []
    for i in range(3):
        data = make_recording(tmp_path, f"e{i}")
        entry_id = lib.add_recording(data).data["entry_id"]
        ids.append(entry_id)
        set_start_time(lib, entry_id, f"2026-0{i + 1}-01T00:00:00+00:00")
    plan = lib.plan_retention(RetentionPolicy(max_entries=1), now=NOW)
    assert plan.ok and plan.code == "dry_run"
    got = [c["entry_id"] for c in plan.data["candidates"]]
    assert got == ids[:2]
    assert any("条目数" in r for r in plan.data["candidates"][0]["reasons"])


def test_missing_entries_are_listed_but_never_candidates(tmp_path):
    data = make_recording(tmp_path, "gone")
    lib = RecordingLibrary(str(tmp_path))
    entry_id = lib.add_recording(data).data["entry_id"]
    set_start_time(lib, entry_id, "2025-01-01T00:00:00+00:00")
    os.remove(data)
    os.remove(os.path.splitext(data)[0] + ".sigmf-meta")
    lib.reconcile(apply=True)

    plan = lib.plan_retention(RetentionPolicy(max_age_days=1), now=NOW).data
    assert plan["candidates"] == []
    reasons = {u["entry_id"]: u["reason"] for u in plan["unavailable"]}
    assert reasons[entry_id] == "file_missing"
    res = lib.apply_retention(RetentionPolicy(max_age_days=1),
                              confirm=True, now=NOW)
    assert res.ok and res.code == "no_action"
    entry = next(e for e in lib.load().data["entries"]
                 if e["entry_id"] == entry_id)
    assert entry["state"] == "missing"


def test_negative_policy_rejected(tmp_path):
    lib = RecordingLibrary(str(tmp_path))
    res = lib.plan_retention(RetentionPolicy(max_total_bytes=-1))
    assert not res.ok and res.code == "bad_param"


def _days(value):
    from datetime import timedelta
    return timedelta(days=value)
