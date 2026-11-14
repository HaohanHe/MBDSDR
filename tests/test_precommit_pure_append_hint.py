#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""pre-commit 纯追加提示：只打印「本次为纯追加，可免 claim」，不改退出码。

判据（对应 2026-11-14 任务二）：

  * 本笔对 ``docs/COORDINATION.md`` **只有纯追加**（插入表格行）→ 打印该句，
    提交照常放行（rc=0）；
  * 有删除/改写 → 不打印该句，⑤ 仍按原规则拒绝（rc=1）；
  * 「不改退出码」由两条对照证明：纯追加在他人持有整档 claim 时仍 rc=0，
    非纯追加在无人持有时仍 rc=1——退出码由只增检查/claim 闸决定，提示不参与。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_SRC = REPO_ROOT / "scripts" / "hooks" / "pre-commit"
GATE_SRC = REPO_ROOT / "scripts" / "claim_gate.py"
APPEND_ONLY = REPO_ROOT / "scripts" / "check_coordination_append_only.py"

DOC_REL = "docs/COORDINATION.md"
HINT = "本次为纯追加，可免 claim"

BASE = """# 协调台账

### 6.1 当前占用

| 文件 | 线名 | 开始时间 | 预计提交 hash |
| --- | --- | --- | --- |
| `docs/other.md` | 别的线（背景） | 2026-11-13 01:00 | 随本笔 |
| `scripts/keep.py` | 另一条线（背景） | 2026-11-13 01:01 | 随本笔 |

### 6.2 下一节
"""

NEW_ROW = "| `scripts/note.py` | 甲线（追加一行测试） | 2026-11-14 11:00 | 本笔 |\n"

STUB = "import sys\nsys.exit(0)\n"


def _git(repo: Path, env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def _env(tmp_path: Path, repo: Path) -> dict:
    scope = tmp_path / "scope.py"
    scope.write_text(STUB, encoding="utf-8")
    scanner = tmp_path / "scanner.py"
    scanner.write_text(STUB, encoding="utf-8")
    missing_coord = tmp_path / "no_coord.py"

    env = os.environ.copy()
    env.pop("MBDSDR_CLAIM_OWNER", None)
    # ⑤ 的只增检查是独立脚本，按 MBDSDR_REPO_ROOT 定位仓库；不设就会读**本仓**，
    # 于是「删行」用例会拿真仓库的暂存区做判定（假绿/假红）。
    env["MBDSDR_REPO_ROOT"] = str(repo)
    env["MBDSDR_LOCKS_DIR"] = str(tmp_path / "locks")
    env["MBDSDR_CLAIM_ROOT"] = str(repo)
    env["MBDSDR_HOOK_MARKER_DIR"] = str(tmp_path / "hook_ok")
    env["MBDSDR_SCOPE_SCRIPT"] = str(scope)
    env["MBDSDR_SECRET_SCAN"] = str(scanner)
    env["MBDSDR_CLAIM_GATE"] = str(GATE_SRC)
    env["MBDSDR_CLAIM_COORD"] = str(missing_coord)   # ④ 降级为告警，不影响本测
    env["MBDSDR_COORD_APPEND_ONLY"] = str(APPEND_ONLY)
    return env


def _repo(tmp_path: Path) -> tuple[Path, dict]:
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / DOC_REL).write_text(BASE, encoding="utf-8")
    (repo / "scripts" / "keep.py").write_text("y = 2\n", encoding="utf-8")

    env = _env(tmp_path, repo)
    _git(repo, env, "init", "-q")
    _git(repo, env, "config", "user.email", "t@example.invalid")
    _git(repo, env, "config", "user.name", "t")
    _git(repo, env, "config", "commit.gpgsign", "false")
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HOOK_SRC, hooks / "pre-commit")
    os.chmod(hooks / "pre-commit", 0o755)
    _git(repo, env, "add", "-A")
    _git(repo, env, "commit", "-qm", "base")
    return repo, env


def _stage_doc(repo: Path, env: dict, text: str) -> None:
    (repo / DOC_REL).write_text(text, encoding="utf-8")
    _git(repo, env, "add", DOC_REL)


def _commit(repo: Path, env: dict) -> subprocess.CompletedProcess:
    return _git(repo, env, "commit", "-qm", "probe")


def test_pure_append_prints_hint_and_passes(tmp_path: Path) -> None:
    repo, env = _repo(tmp_path)
    _stage_doc(repo, env, BASE.replace("\n### 6.2", NEW_ROW + "\n### 6.2"))

    proc = _commit(repo, env)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert HINT in out, out
    assert "PURE_APPEND=yes" in out, out


def test_delete_row_has_no_hint_and_is_rejected(tmp_path: Path) -> None:
    repo, env = _repo(tmp_path)
    dropped = BASE.replace(
        "| `scripts/keep.py` | 另一条线（背景） | 2026-11-13 01:01 | 随本笔 |\n", "")
    _stage_doc(repo, env, dropped)

    proc = _commit(repo, env)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert HINT not in out, out
    assert "PURE_APPEND=no" in out, out
    assert "拒绝提交" in out, out


def test_hint_does_not_change_exit_code_with_foreign_claim(tmp_path: Path) -> None:
    """纯追加 + 他人整档 claim：claim 闸按窄锁放行，提示不参与退出码（仍 rc=0）。"""
    repo, env = _repo(tmp_path)
    # 直接写占位文件（与 file_claim 落盘格式一致），避免依赖 claim CLI 的目录约定。
    import hashlib
    import json
    import time

    key = os.path.normcase(DOC_REL.replace("\\", "/")).replace("\\", "/").lstrip("./")
    now = time.time()
    marker = Path(env["MBDSDR_LOCKS_DIR"]) / "claims"
    marker.mkdir(parents=True, exist_ok=True)
    (marker / (hashlib.sha1(key.encode("utf-8")).hexdigest()[:16] + ".claim")).write_text(
        json.dumps({
            "path": DOC_REL, "key": key, "owner": "别的线/cccc3333",
            "acquired_at": now - 60, "heartbeat_at": now - 60, "ttl_min": 180.0,
            "pid": os.getpid(),
        }, ensure_ascii=False), encoding="utf-8")

    _stage_doc(repo, env, BASE.replace("\n### 6.2", NEW_ROW + "\n### 6.2"))
    proc = _commit(repo, env)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert HINT in out, out
    assert "放行（登记面纯追加）" in out, out
