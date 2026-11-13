#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""文档面窄锁化（任务二）：纯追加免整档 claim，非追加退回原要求。

裁定原文：``docs/COORDINATION.md`` 的**追加行**路径不再要求整档 claim
（``append_coordination_row.py`` 自己的窄锁就是互斥）；但整篇重写 / 删除已有行
仍然要 claim。判据必须能区分这两类。

四条反证（+1 条退路）：
  A. A 持该文件 claim 时 B 仍能 append（rc=0），且 B 用纯追加提交时 ② 放行；
  B. B 无 claim 做纯追加 → ④ 通过（rc=0）；
  C. B 无 claim 删一行 → 拒绝（④ rc=3；② 也按普通文件拦，rc=1）；
  D. B 无 claim 改一行 → 拒绝（同上）；
  E. B **持有** claim 时删一行 → ④ 通过（「退回原要求」这条路确实通）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CLAIM = REPO_ROOT / "scripts" / "file_claim.py"
APPEND = REPO_ROOT / "scripts" / "append_coordination_row.py"
CHECK = REPO_ROOT / "scripts" / "check_claim_coordination.py"
GATE = REPO_ROOT / "scripts" / "claim_gate.py"

OWNER_A = "甲线/aaaa1111"
OWNER_B = "乙线/bbbb2222"
DOC_REL = "docs/COORDINATION.md"

BASE = """# 协调台账

### 6.1 当前占用

| 文件 | 线名 | 开始时间 | 预计提交 hash |
| --- | --- | --- | --- |
| `docs/other.md` | 别的线（背景） | 2026-11-13 01:00 | 随本笔 |
| `scripts/keep.py` | 另一条线（背景） | 2026-11-13 01:01 | 随本笔 |

### 6.2 下一节
"""

NEW_ROW = "| `scripts/note.py` | 乙线（追加一行测试） | 2026-11-13 21:40 | 本笔 |\n"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, encoding="utf-8")


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / DOC_REL).write_text(BASE, encoding="utf-8")
    (repo / "scripts" / "note.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "scripts" / "keep.py").write_text("y = 2\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo


def _env(locks: Path, repo: Path, owner: str = "") -> dict:
    env = os.environ.copy()
    env["MBDSDR_LOCKS_DIR"] = str(locks)
    env["MBDSDR_CLAIM_ROOT"] = str(repo)
    env.pop("PYTHONPATH", None)
    if owner:
        env["MBDSDR_CLAIM_OWNER"] = owner
    else:
        env.pop("MBDSDR_CLAIM_OWNER", None)
    return env


def _claim(locks: Path, repo: Path, path: str, owner: str) -> None:
    out = subprocess.run(
        [sys.executable, str(CLAIM), "claim", path, "--owner", owner,
         "--reason", "窄锁测试", "--ttl-min", "30"],
        cwd=repo, env=_env(locks, repo, owner), capture_output=True, text=True,
        encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr


def _stage_doc(repo: Path, text: str) -> None:
    (repo / DOC_REL).write_text(text, encoding="utf-8")
    _git(repo, "add", DOC_REL)


def _check(locks: Path, repo: Path, owner: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECK), "--staged",
         "--doc", str(repo / DOC_REL),
         "--registry", str(locks / "claims.json"),
         "--markers", str(locks / "claims")],
        cwd=repo, env=_env(locks, repo, owner), capture_output=True, text=True,
        encoding="utf-8", timeout=60)


def _gate(locks: Path, repo: Path, owner: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), "--staged"],
        cwd=repo, env=_env(locks, repo, owner), capture_output=True, text=True,
        encoding="utf-8", timeout=60)


def test_append_works_even_when_someone_holds_the_doc(tmp_path: Path) -> None:
    """A：A 持该文件 claim 时 B 仍能 append（rc=0）。"""
    repo = _repo(tmp_path)
    locks = tmp_path / "locks"
    _claim(locks, repo, DOC_REL, OWNER_A)
    _claim(locks, repo, "scripts/note.py", OWNER_B)

    out = subprocess.run(
        [sys.executable, str(APPEND), "--path", "scripts/note.py",
         "--owner", OWNER_B, "--note", "追加不需要整档 claim",
         "--root", str(repo)],
        cwd=REPO_ROOT, env=_env(locks, repo, OWNER_B), capture_output=True,
        text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "APPEND OK" in out.stdout
    assert "追加不需要整档 claim" in (repo / DOC_REL).read_text(encoding="utf-8")


def test_pure_append_passes_without_claim(tmp_path: Path) -> None:
    """B：B 无 claim 纯追加 → ④ 通过；② 也放行（A 的整档 claim 不挡）。"""
    repo = _repo(tmp_path)
    locks = tmp_path / "locks"
    _claim(locks, repo, DOC_REL, OWNER_A)
    _stage_doc(repo, BASE.replace("\n### 6.2", NEW_ROW + "\n### 6.2"))

    check = _check(locks, repo, OWNER_B)
    assert check.returncode == 0, check.stdout + check.stderr

    gate = _gate(locks, repo, OWNER_B)
    assert gate.returncode == 0, gate.stdout + gate.stderr
    assert "放行（登记面纯追加）" in gate.stdout


def test_delete_row_without_claim_is_rejected(tmp_path: Path) -> None:
    """C：B 无 claim 删一行 → ④ 拒（3），② 也按普通文件拒（1）。"""
    repo = _repo(tmp_path)
    locks = tmp_path / "locks"
    _claim(locks, repo, DOC_REL, OWNER_A)
    dropped = BASE.replace("| `scripts/keep.py` | 另一条线（背景） | 2026-11-13 01:01 | 随本笔 |\n", "")
    _stage_doc(repo, dropped)

    check = _check(locks, repo, OWNER_B)
    assert check.returncode == 3, check.stdout + check.stderr
    assert "不是「纯追加」" in check.stdout

    gate = _gate(locks, repo, OWNER_B)
    assert gate.returncode == 1, gate.stdout + gate.stderr


def test_rewrite_row_without_claim_is_rejected(tmp_path: Path) -> None:
    """D：B 无 claim 改一行 → ④ 拒（3）。"""
    repo = _repo(tmp_path)
    locks = tmp_path / "locks"
    _claim(locks, repo, DOC_REL, OWNER_A)
    rewritten = BASE.replace("另一条线（背景）", "另一条线（已被改写）")
    _stage_doc(repo, rewritten)

    check = _check(locks, repo, OWNER_B)
    assert check.returncode == 3, check.stdout + check.stderr
    assert "不是「纯追加」" in check.stdout


def test_delete_row_with_own_claim_passes(tmp_path: Path) -> None:
    """E：退回原要求确实可行——B 自己持有 claim 时删行，④ 放行。"""
    repo = _repo(tmp_path)
    locks = tmp_path / "locks"
    _claim(locks, repo, DOC_REL, OWNER_B)
    dropped = BASE.replace("| `scripts/keep.py` | 另一条线（背景） | 2026-11-13 01:01 | 随本笔 |\n", "")
    _stage_doc(repo, dropped)

    check = _check(locks, repo, OWNER_B)
    assert check.returncode == 0, check.stdout + check.stderr
