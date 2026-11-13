#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""claim_gate 拒绝留痕（任务三）：拒绝必须能被数出来。

原先 ② 拒绝时什么都不写，于是「因 claim 被拒的提交数」永远算不出来——
这是当天账里唯一的空格。现在每次拒绝追加一行 ``.locks/rejected.log``：
时间 / 路径 / 原因码 / 记名（``code`` 区分 BLOCKED 与 LEDGER，``pid`` 用于
把「一次提交的多行」与「多次提交」分开）。

反证 2 条：
  1. 制造一次拒绝（他人持有未过期）→ 落一行，字段齐全，退出码仍是 1；
  2. 正常提交（自己持有 / 无 claim）→ 不落行，退出码 0。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CLAIM = REPO_ROOT / "scripts" / "file_claim.py"
GATE = REPO_ROOT / "scripts" / "claim_gate.py"
OWNER_A = "甲线/aaaa1111"
OWNER_B = "乙线/bbbb2222"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, encoding="utf-8")


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "target.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo


def _env(locks: Path, repo: Path, owner: str) -> dict:
    env = os.environ.copy()
    env["MBDSDR_LOCKS_DIR"] = str(locks)
    env["MBDSDR_CLAIM_ROOT"] = str(repo)
    env.pop("PYTHONPATH", None)
    if owner:
        env["MBDSDR_CLAIM_OWNER"] = owner
    else:
        env.pop("MBDSDR_CLAIM_OWNER", None)
    return env


def _claim(locks: Path, repo: Path, owner: str) -> None:
    out = subprocess.run(
        [sys.executable, str(CLAIM), "claim", "scripts/target.py", "--owner", owner,
         "--reason", "拒绝留痕测试", "--ttl-min", "30"],
        cwd=repo, env=_env(locks, repo, owner), capture_output=True, text=True,
        encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr


def _stage_change(repo: Path) -> None:
    (repo / "scripts" / "target.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "add", "scripts/target.py")


def _gate(locks: Path, repo: Path, owner: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), "--staged"],
        cwd=repo, env=_env(locks, repo, owner), capture_output=True, text=True,
        encoding="utf-8", timeout=60)


def _rejected(locks: Path) -> list[dict]:
    path = locks / "rejected.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_blocked_commit_writes_one_line(tmp_path: Path) -> None:
    """他人持有未过期 → 拒（rc=1）且落一行；字段是「时间/路径/原因码/记名」。"""
    repo = _repo(tmp_path)
    locks = tmp_path / "locks"
    _claim(locks, repo, OWNER_A)
    _stage_change(repo)

    gate = _gate(locks, repo, OWNER_B)
    assert gate.returncode == 1, gate.stdout + gate.stderr

    rows = _rejected(locks)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["code"] == "BLOCKED"
    assert row["reason"] == "claim_held"
    assert row["path"].endswith("scripts/target.py")
    assert row["holder"] == OWNER_A
    assert row["identity"] == OWNER_B
    assert row["at"] and row["at_iso"] and row["pid"]


def test_normal_commit_writes_no_line(tmp_path: Path) -> None:
    """自己持有 → 放行（rc=0）且不落行。"""
    repo = _repo(tmp_path)
    locks = tmp_path / "locks"
    _claim(locks, repo, OWNER_A)
    _stage_change(repo)

    gate = _gate(locks, repo, OWNER_A)
    assert gate.returncode == 0, gate.stdout + gate.stderr
    assert _rejected(locks) == []
