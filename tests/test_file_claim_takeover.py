#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""孤儿 claim 接管的判据：只看「心跳过期 + 无在途改动 + 有理由」，pid 不参与。

背景（2026-11-13）：`claim` 是一次性 CLI，写完就退出，`pid` 记的是那个短命
进程——「pid 已死」是默认状态。旧实现把它当接管硬前提，等于任何 claim 都能被
接管：`desktop/ai_panel.py`、`desktop/ai_sdr_session.py` 就是在 TTL 还剩 160+
分钟时被 orphan_takeover 抢走的。

本文件钉住新判据：
  (a) 事故形态：pid 已死、心跳未过期、工作区干净 → 拒绝接管；
  (b) 心跳过期 + 干净 + 写了理由 → 允许，日志记录判据与理由；
  (c) 心跳过期 + 有在途改动 → 拒绝（理由：在途改动）；
  (d) 不允许无理由接管；
  (e) pid 活着但心跳过期 → 允许（pid 不是判据，避免把「pid 存活」当忙碌信号）。

另外钉住「疑似死会话」提示（2026-11-13 裁定 A）：
  (f) 未过期 + 心跳过半 + pid 已不在 → status 标提示，但 takeover 仍拒绝；
  (g) 心跳未过半 → 不标；
  (h) pid 号还在但创建时刻对不上（被复用）→ 标；
  (i) 反证：把 TTL 压到已过期，接管仍必须过 30min 宽限——短 TTL 只改数字，
      不放宽机制。
"""
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


def _claim(locks: Path, root: Path, owner: str = "线A") -> None:
    claimed = run(
        locks, root, "claim", TARGET, "--owner", owner, "--ttl-min", "60",
        "--reason", "接管用例：覆盖跨过一次心跳的 60min 租期",
    )
    assert claimed.returncode == 0, claimed.stdout + claimed.stderr


def test_incident_shape_early_takeover_is_denied(tmp_path: Path) -> None:
    """(a) 事故形态：pid 已死、心跳未过期、工作区干净 → 必须拒绝接管。"""
    root = _repo(tmp_path)
    locks = tmp_path / "locks"
    _claim(locks, root)

    now = time.time()
    _rewrite(locks, pid=_dead_pid(), acquired_at=now - 60, heartbeat_at=now - 60, ttl_min=60)

    denied = run(
        locks, root, "takeover", TARGET, "--owner", "线B",
        "--reason", "需要改这个文件",
    )
    text = denied.stdout + denied.stderr
    assert denied.returncode == 2, text
    assert "TAKEOVER DENIED" in text
    assert "[PASS] 无在途改动" in text
    assert "[FAIL] 心跳过期" in text
    assert "未过期" in text
    assert "仅诊断" in text, "pid 只能作诊断，不能当接管信号"
    assert not (locks / "claim_takeovers.log").exists(), "被拒绝的接管不得留成功日志"

    status = run(locks, root, "status", TARGET)
    assert "owner=线A" in status.stdout, "原持有者必须保持不动"


def test_expired_clean_with_reason_allows_takeover(tmp_path: Path) -> None:
    """(b) 过期 ≥30min + 干净 + 理由 → 允许；刚过期的宽限期内仍拒绝。"""
    root = _repo(tmp_path)
    locks = tmp_path / "locks"
    _claim(locks, root)

    now = time.time()
    # 刚过期 2 分钟（TTL 60）→ 落在 §6.1.4 的 30 分钟宽限里，仍不许接管。
    _rewrite(locks, pid=_dead_pid(), acquired_at=now - 90 * 60, heartbeat_at=now - 62 * 60, ttl_min=60)
    early = run(locks, root, "takeover", TARGET, "--owner", "线B", "--reason", "等不及了")
    text = early.stdout + early.stderr
    assert early.returncode == 2, text
    assert "[FAIL] 心跳过期" in text
    assert "宽限" in text
    assert not (locks / "claim_takeovers.log").exists()

    # 过期 119 分钟 → 过完宽限，允许接管。
    _rewrite(locks, pid=_dead_pid(), acquired_at=now - 180 * 60, heartbeat_at=now - 120 * 60, ttl_min=1)
    taken = run(
        locks, root, "takeover", TARGET, "--owner", "线B",
        "--reason", "原 claim 心跳停在 120min 前，TTL 1min；工作区无改动",
    )
    assert taken.returncode == 0, taken.stdout + taken.stderr
    assert "TAKEOVER OK" in taken.stdout
    assert "expired=PASS" in taken.stdout
    assert "no_inflight=PASS" in taken.stdout
    assert "原持有者=线A" in taken.stdout and "新持有者=线B" in taken.stdout

    status = run(locks, root, "status", TARGET)
    assert "owner=线B" in status.stdout
    assert "线A" not in status.stdout

    records = [
        json.loads(line)
        for line in (locks / "claim_takeovers.log").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(records) == 1
    record = records[0]
    assert record["action"] == "orphan_takeover"
    assert record["old_owner"] == "线A"
    assert record["new_owner"] == "线B"
    assert record["reason"].strip(), "接管记录必须带理由"
    assert record["criteria"] == {"expired": True, "no_inflight": True}
    assert "pid" in record["pid_note"] and "仅诊断" in record["pid_note"]
    assert record["logged_at"]

    registry = json.loads((locks / "claims.json").read_text(encoding="utf-8"))
    entry = registry["claims"][TARGET]
    first = entry["history"][0]
    assert first["owner"] == "线A"
    assert first["closed_reason"] == "orphan_takeover"
    assert first["takeover"]["reason"].strip()
    assert entry["history"][1]["owner"] == "线B"


def test_expired_but_inflight_is_denied(tmp_path: Path) -> None:
    """(c) 心跳过期但有未提交改动 → 拒绝，并报「有在途改动」。"""
    root = _repo(tmp_path)
    locks = tmp_path / "locks"
    _claim(locks, root)

    (root / TARGET).write_text("modified\n", encoding="utf-8")
    now = time.time()
    _rewrite(locks, pid=_dead_pid(), acquired_at=now - 180 * 60, heartbeat_at=now - 120 * 60, ttl_min=1)

    denied = run(locks, root, "takeover", TARGET, "--owner", "线B", "--reason", "接一下")
    text = denied.stdout + denied.stderr
    assert denied.returncode == 2, text
    assert "[FAIL] 无在途改动" in text
    assert "有在途改动" in text
    assert not (locks / "claim_takeovers.log").exists()

    status = run(locks, root, "status", TARGET)
    assert "owner=线A" in status.stdout


def test_takeover_requires_reason(tmp_path: Path) -> None:
    """(d) 无 --reason → 参数层直接拒（没理由的接管不认）。"""
    root = _repo(tmp_path)
    locks = tmp_path / "locks"
    _claim(locks, root)
    now = time.time()
    _rewrite(locks, pid=_dead_pid(), acquired_at=now - 180 * 60, heartbeat_at=now - 120 * 60, ttl_min=1)

    denied = run(locks, root, "takeover", TARGET, "--owner", "线B")
    text = denied.stdout + denied.stderr
    assert denied.returncode == 2, text
    assert "--reason" in text
    assert not (locks / "claim_takeovers.log").exists()


def test_alive_pid_with_expired_heartbeat_allows_takeover(tmp_path: Path) -> None:
    """(e) pid 活着但心跳过期 + 干净 + 理由 → 允许（pid 不参与判定）。"""
    root = _repo(tmp_path)
    locks = tmp_path / "locks"
    _claim(locks, root)

    now = time.time()
    _rewrite(locks, pid=os.getpid(), acquired_at=now - 180 * 60, heartbeat_at=now - 120 * 60, ttl_min=1)

    taken = run(locks, root, "takeover", TARGET, "--owner", "线B", "--reason", "过期 120min，工作区干净")
    assert taken.returncode == 0, taken.stdout + taken.stderr
    assert "expired=PASS" in taken.stdout
    assert "no_inflight=PASS" in taken.stdout


def test_takeover_without_claim_is_denied(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    locks = tmp_path / "locks"
    denied = run(locks, root, "takeover", TARGET, "--owner", "线B", "--reason", "x")
    assert denied.returncode == 2
    assert "没有 claim" in (denied.stdout + denied.stderr)


def test_suspect_hint_marks_dead_pid_past_half_ttl(tmp_path: Path) -> None:
    """(f) 未过期 + 心跳过半 + pid 已不在 → status 标提示，但 takeover 仍拒绝。"""
    root = _repo(tmp_path)
    locks = tmp_path / "locks"
    _claim(locks, root)

    now = time.time()
    # TTL 60，心跳在 40 分钟前（>50%），仍未过期。
    _rewrite(locks, pid=_dead_pid(), acquired_at=now - 40 * 60, heartbeat_at=now - 40 * 60, ttl_min=60)

    status = run(locks, root, "status", TARGET)
    assert status.returncode == 0, status.stdout + status.stderr
    assert "疑似死会话" in status.stdout
    assert "仅提示" in status.stdout
    assert "EXPIRED" not in status.stdout

    # 提示不等于判据：心跳未过期时接管仍必须拒绝（同事故形态 a）。
    denied = run(locks, root, "takeover", TARGET, "--owner", "线B", "--reason", "看着像死了")
    text = denied.stdout + denied.stderr
    assert denied.returncode == 2, text
    assert "[FAIL] 心跳过期" in text
    assert not (locks / "claim_takeovers.log").exists()


def test_suspect_hint_absent_before_half_ttl(tmp_path: Path) -> None:
    """(g) 心跳未过半 → 不标提示（即使 pid 已死）。"""
    root = _repo(tmp_path)
    locks = tmp_path / "locks"
    _claim(locks, root)

    now = time.time()
    _rewrite(locks, pid=_dead_pid(), acquired_at=now - 10 * 60, heartbeat_at=now - 10 * 60, ttl_min=60)

    status = run(locks, root, "status", TARGET)
    assert status.returncode == 0, status.stdout + status.stderr
    assert "疑似死会话" not in status.stdout


def test_suspect_hint_marks_reused_pid(tmp_path: Path) -> None:
    """(h) pid 号还在、但创建时刻对不上（被复用）→ 标提示。"""
    import psutil

    root = _repo(tmp_path)
    locks = tmp_path / "locks"
    _claim(locks, root)

    actual = psutil.Process(os.getpid()).create_time()
    now = time.time()
    # 复用：号是本进程的，但记录的创建时刻在 1 小时前 → 不是同一个进程。
    _rewrite(
        locks,
        pid=os.getpid(),
        pid_create_time=actual - 3600,
        acquired_at=now - 40 * 60,
        heartbeat_at=now - 40 * 60,
        ttl_min=60,
    )

    status = run(locks, root, "status", TARGET)
    assert status.returncode == 0, status.stdout + status.stderr
    assert "疑似死会话" in status.stdout
    assert "复用" in status.stdout


def test_short_ttl_expiry_still_requires_grace(tmp_path: Path) -> None:
    """(i) 反证：TTL 压到已过期，接管仍必须过 30min 宽限，不因「短」放宽。"""
    root = _repo(tmp_path)
    locks = tmp_path / "locks"
    _claim(locks, root)

    now = time.time()
    # TTL 1 分钟、心跳停在 5 分钟前 → 过期 4 分钟，落在 §6.1.4 的 30min 宽限内。
    _rewrite(locks, pid=_dead_pid(), acquired_at=now - 6 * 60, heartbeat_at=now - 5 * 60, ttl_min=1)

    status = run(locks, root, "status", TARGET)
    assert "EXPIRED" in status.stdout

    denied = run(locks, root, "takeover", TARGET, "--owner", "线B", "--reason", "TTL 已经短到过期了")
    text = denied.stdout + denied.stderr
    assert denied.returncode == 2, text
    assert "[FAIL] 心跳过期" in text
    assert "宽限" in text
    assert not (locks / "claim_takeovers.log").exists()
