#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""孤儿 claim 接管的反证：进程已死才许接管，进程活着一律拒绝。"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "file_claim.py"
TARGET = "tracked.txt"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "test")
    (root / TARGET).write_text("base\n", encoding="utf-8")
    _git(root, "add", TARGET)
    _git(root, "commit", "-q", "-m", "base")
    return root


def _env(locks: Path, root: Path) -> dict:
    env = os.environ.copy()
    env["MBDSDR_LOCKS_DIR"] = str(locks)
    env["MBDSDR_CLAIM_ROOT"] = str(root)
    env.pop("PYTHONPATH", None)
    return env


def run(locks: Path, root: Path, *args: str, timeout: float = 60.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=root,
        env=_env(locks, root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )


def _marker(locks: Path, display: str = TARGET) -> Path:
    key = os.path.normcase(display).replace("\\", "/")
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return locks / "claims" / f"{digest}.claim"


def _rewrite(locks: Path, **changes) -> dict:
    path = _marker(locks)
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("pid_create_time", None)
    data.update(changes)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def _dead_pid() -> int:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=30)
    return child.pid


def _self_create_time():
    try:
        import psutil

        return psutil.Process(os.getpid()).create_time()
    except Exception:
        return None


def _claim(locks: Path, root: Path, owner: str = "线A") -> None:
    claimed = run(locks, root, "claim", TARGET, "--owner", owner, "--ttl-min", "60")
    assert claimed.returncode == 0, claimed.stdout + claimed.stderr


def test_dead_pid_and_expired_heartbeat_allows_takeover_and_logs(tmp_path: Path) -> None:
    """反证 (a)：pid 死 + 心跳过期 → 允许接管，且日志逐条记录判据。"""
    root = _repo(tmp_path)
    locks = tmp_path / "locks"
    _claim(locks, root)

    now = time.time()
    _rewrite(locks, pid=_dead_pid(), acquired_at=now - 180, heartbeat_at=now - 120, ttl_min=1)

    taken = run(locks, root, "takeover", TARGET, "--owner", "线B", "--reason", "原进程已死")
    assert taken.returncode == 0, taken.stdout + taken.stderr
    assert "TAKEOVER OK" in taken.stdout
    assert "原持有者=线A" in taken.stdout
    assert "新持有者=线B" in taken.stdout

    status = run(locks, root, "status", TARGET)
    assert "owner=线B" in status.stdout
    assert "线A" not in status.stdout

    log_path = locks / "claim_takeovers.log"
    assert log_path.exists(), "接管必须强制留痕"
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(records) == 1
    record = records[0]
    assert record["action"] == "orphan_takeover"
    assert record["old_owner"] == "线A"
    assert record["new_owner"] == "线B"
    assert record["reason"] == "原进程已死"
    assert record["criteria"]["pid_dead_or_reused"] is True
    assert record["criteria"]["expired"] is True
    assert record["criteria"]["no_inflight"] is True
    assert record["logged_at"]

    registry = json.loads((locks / "claims.json").read_text(encoding="utf-8"))
    entry = registry["claims"][TARGET]
    first = entry["history"][0]
    assert first["owner"] == "线A"
    assert first["closed_reason"] == "orphan_takeover"
    assert first["takeover"]["old_owner"] == "线A"
    assert entry["history"][1]["owner"] == "线B"


def test_alive_pid_but_expired_heartbeat_is_denied(tmp_path: Path) -> None:
    """反证 (b)：pid 活着但心跳过期（疑似繁忙）→ 必须拒绝，且不写日志。"""
    root = _repo(tmp_path)
    locks = tmp_path / "locks"
    _claim(locks, root)

    now = time.time()
    data = _rewrite(locks, pid=os.getpid(), acquired_at=now - 180, heartbeat_at=now - 120, ttl_min=1)
    create = _self_create_time()
    if create is not None:
        data["pid_create_time"] = create
        _marker(locks).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    denied = run(locks, root, "takeover", TARGET, "--owner", "线B")
    assert denied.returncode == 2, denied.stdout + denied.stderr
    text = denied.stdout + denied.stderr
    assert "TAKEOVER DENIED" in text
    assert "[FAIL] pid 已死/被复用" in text
    assert "pid 仍" in text or "疑似繁忙" in text

    assert not (locks / "claim_takeovers.log").exists(), "被拒绝的接管不得留成功日志"
    status = run(locks, root, "status", TARGET)
    assert "owner=线A" in status.stdout


def test_inflight_changes_takeover_warns_hunks_only(tmp_path: Path) -> None:
    """pid 死 + 过期可接管，但在途改动必须告警「只能按 hunk 进 index」（对齐 §6.1.1）。"""
    root = _repo(tmp_path)
    locks = tmp_path / "locks"
    _claim(locks, root)

    (root / TARGET).write_text("modified\n", encoding="utf-8")
    now = time.time()
    _rewrite(locks, pid=_dead_pid(), acquired_at=now - 180, heartbeat_at=now - 120, ttl_min=1)

    taken = run(locks, root, "takeover", TARGET, "--owner", "线B")
    assert taken.returncode == 0, taken.stdout + taken.stderr
    text = taken.stdout + taken.stderr
    assert "no_inflight=FAIL" in taken.stdout
    assert "WARN 工作区有在途改动" in text
    assert "禁止整文件 add" in text

    record = json.loads((locks / "claim_takeovers.log").read_text(encoding="utf-8").splitlines()[0])
    assert record["criteria"]["no_inflight"] is False
    assert "有在途改动" in record["detail"]["no_inflight"]


def test_dead_pid_and_clean_workspace_allows_early_takeover(tmp_path: Path) -> None:
    """pid 死 + 无在途改动（心跳未过期）也满足「至少两条」→ 允许接管。"""
    root = _repo(tmp_path)
    locks = tmp_path / "locks"
    _claim(locks, root)

    now = time.time()
    _rewrite(locks, pid=_dead_pid(), acquired_at=now, heartbeat_at=now, ttl_min=60)

    taken = run(locks, root, "takeover", TARGET, "--owner", "线B")
    assert taken.returncode == 0, taken.stdout + taken.stderr
    assert "expired=FAIL" in taken.stdout
    assert "pid_dead_or_reused=PASS" in taken.stdout
    assert "no_inflight=PASS" in taken.stdout


def test_takeover_without_claim_is_denied(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    locks = tmp_path / "locks"
    denied = run(locks, root, "takeover", TARGET, "--owner", "线B")
    assert denied.returncode == 2
    assert "没有 claim" in (denied.stdout + denied.stderr)
