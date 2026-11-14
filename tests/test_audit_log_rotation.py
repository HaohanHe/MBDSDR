# SPDX-License-Identifier: MIT
"""审计账本轮转：跨文件可查、并发写不丢行/不出半行、保留有上限。

判据（用户指定）：
* 写满触发轮转后，轮转前后的记录都能从**同一个查询入口**
  （:func:`mbdsdr_ai.tool_guard.query_audit_events` / ``AuditLog.query``）
  按时间范围查到，调用方不需要自己拼 ``.1`` / ``.2``；
* 两个进程同时写同一账本，不产生半行、不丢行。

反证：把 ``rotating_jsonl.RotatingJsonlLog._rotate`` 改成「直接截断当前文件」
（``open(path, "w").close()``），``test_rotation_keeps_all_records_queryable``
必须红——轮转前的记录不再可查。
"""
from __future__ import annotations

import json
import multiprocessing
import os

import pytest

from mbdsdr_ai.hooks import Event, EventType
from mbdsdr_ai.rotating_jsonl import FileLockTimeout, RotatingJsonlLog
from mbdsdr_ai.tool_guard import (AUDIT_LOG_DISK_CAP_BYTES, AuditLog,
                                  query_audit_events)

BASE_TS = 1_760_000_000.0


def _event(ts: float, seq: str) -> Event:
    return Event(event_type=EventType.AGENT_TOOL_RESULT,
                 data={"tool": "t", "ok": True, "seq": seq},
                 timestamp=ts, source="test")


def _write(log: AuditLog, count: int, prefix: str = "") -> None:
    for i in range(count):
        log.record(_event(BASE_TS + i, f"{prefix}{i}"))


def test_rotation_keeps_all_records_queryable(tmp_path):
    ledger = tmp_path / "audit.jsonl"
    # 份数留够，只考察「轮转本身丢不丢」；保留上限由下一条用例单独钉。
    log = AuditLog(str(ledger), max_bytes=4096, keep_files=40,
                   retention_days=0)
    _write(log, 120)

    assert log.files() and len(log.files()) >= 2, "写满必须轮转出新文件"

    events, skipped = log.query()
    assert skipped == 0
    assert len(events) == 120, "轮转前后的记录一条都不能少"
    stamps = [ev["timestamp"] for ev in events]
    assert stamps == sorted(stamps)
    assert stamps[0] == BASE_TS and stamps[-1] == BASE_TS + 119

    # 同一个入口（模块级函数）拿到的是同一批记录。
    same, _ = query_audit_events(path=str(ledger))
    assert [ev["data"]["seq"] for ev in same] == \
        [ev["data"]["seq"] for ev in events]


def test_query_by_time_range_spans_rotated_files(tmp_path):
    ledger = tmp_path / "audit.jsonl"
    log = AuditLog(str(ledger), max_bytes=2048, keep_files=20,
                   retention_days=0)
    _write(log, 90)
    assert len(log.files()) >= 3, "要让时间范围真的跨文件"

    picked, _ = log.query(BASE_TS + 20, BASE_TS + 30)
    assert [ev["timestamp"] for ev in picked] == \
        [BASE_TS + i for i in range(20, 31)]

    # epoch 秒 / datetime / ISO-8601 三种写法等价。
    from datetime import datetime, timezone
    dt_start = datetime.fromtimestamp(BASE_TS + 20, tz=timezone.utc)
    by_dt, _ = log.query(dt_start, BASE_TS + 30)
    by_iso, _ = log.query(dt_start.isoformat(), "2025-10-09T08:53:50+00:00")
    assert len(by_dt) == len(picked) == len(by_iso)


def test_retention_cap_and_oldest_only(tmp_path):
    ledger = tmp_path / "audit.jsonl"
    log = AuditLog(str(ledger), max_bytes=4096, keep_files=3,
                   retention_days=0)
    _write(log, 300)

    # 份数上限：当前 + .1 + .2，绝不会有 .3。
    assert not os.path.exists(str(ledger) + ".3")
    paths = log.files()
    assert len(paths) <= 3

    total = sum(os.path.getsize(p) for p in paths)
    assert total <= AUDIT_LOG_DISK_CAP_BYTES
    assert total <= 4096 * 3 + 4096, "单文件最坏只超一行，总量有硬上界"

    # 保留的是「最新的一段」：删掉的只能是更早的记录。
    events, _ = log.query()
    assert events, "轮转后账本不能空"
    seqs = [int(ev["data"]["seq"]) for ev in events]
    assert seqs == list(range(seqs[0], 300)), "保留的必须是连续的最新尾部"
    assert seqs[-1] == 299


# ── 并发写：两个进程同时写同一账本 ────────────────────────────────────────
def _writer_proc(path: str, prefix: str, count: int) -> None:
    log = AuditLog(path, max_bytes=4096, keep_files=50, retention_days=0)
    for i in range(count):
        log.record(_event(BASE_TS + i, f"{prefix}-{i}"))


def test_concurrent_writers_no_partial_or_lost_lines(tmp_path):
    ledger = tmp_path / "audit.jsonl"
    ctx = multiprocessing.get_context("spawn")
    procs = [
        ctx.Process(target=_writer_proc,
                    args=(str(ledger), prefix, 150))
        for prefix in ("a", "b")
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0, f"写进程失败：{p.exitcode}"

    seqs = []
    bad_lines = 0
    for path in RotatingJsonlLog(str(ledger), max_bytes=4096,
                                 backups=49).files():
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    seqs.append(json.loads(raw)["data"]["seq"])
                except ValueError:
                    bad_lines += 1

    assert bad_lines == 0, "并发写出现了半行"
    assert len(seqs) == 300, f"并发写丢行：只拿到 {len(seqs)} 条"
    assert len(set(seqs)) == 300, "并发写出现重复/覆盖"
    assert {f"a-{i}" for i in range(150)} <= set(seqs)
    assert {f"b-{i}" for i in range(150)} <= set(seqs)


def test_lock_timeout_is_explicit_not_silent(tmp_path):
    """等不到写锁会抛 FileLockTimeout（调用方决定 fail-open/fail-closed）。"""
    log = RotatingJsonlLog(str(tmp_path / "x.jsonl"), max_bytes=4096,
                           backups=2)
    with pytest.raises(FileLockTimeout):
        # 锁文件被占住时（用另一个句柄拿锁），超时必须显式报错。
        from mbdsdr_ai import rotating_jsonl
        with rotating_jsonl.file_lock(log.path, timeout=None):
            log.lock_timeout = 0.05
            log.append_line("{}")
