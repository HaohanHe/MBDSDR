#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""§6.1 登记闸（pre-commit ④）升阻塞后的两条反证。

1. **漏登记必须被拒，且拒绝信息里的命令能直接跑通**：暂存一个仍有活跃 claim
   的文件而不写 §6.1 行 → 闸退出码 3，并打印一条
   ``append_coordination_row.py`` 命令；原样执行这条命令 → 退出码 0；再跑闸 → 0。
2. **60 分钟窗口两侧同一条判据**：55 分钟前本人释放 → 闸要求补行、工具接受；
   61 分钟前 → 闸不再要求、工具也拒收。两边任何一侧单独改，本用例就红。

命令必须「原样能跑」：测试树里放一份 ``scripts/append_coordination_row.py``
（连同它 import 的 ``claim_window.py``），打印出来的命令行按 cwd=仓库根执行，
不做字符串替换——替换过就不算验证了。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "append_coordination_row.py"
WINDOW = REPO / "scripts" / "claim_window.py"
CHECK = REPO / "scripts" / "check_claim_coordination.py"

OWNER = "测试线/abcdef12"
TARGET = "scripts/target.py"

DOC_HEAD = """# 协调台账

### 6.1 当前占用

| 文件 | 线名 | 开始时间 | 预计提交 hash |
| --- | --- | --- | --- |
| `docs/other.md` | 别的线（背景） | 2026-11-13 01:00 | 随本笔 |

### 6.2 下一节
"""


def _norm(path: str) -> str:
    return path.replace("\\", "/").lower()


def _marker(repo: Path, path: str) -> None:
    markers = repo.parent / "locks" / "claims"
    markers.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(_norm(path).encode("utf-8")).hexdigest()[:16]
    (markers / f"{digest}.claim").write_text(json.dumps({
        "path": _norm(path), "owner": OWNER,
        "acquired_at": time.time(), "heartbeat_at": time.time(), "ttl_min": 60,
    }), encoding="utf-8")


def _registry(repo: Path, released_ago_s: float | None) -> Path:
    locks = repo.parent / "locks"
    locks.mkdir(exist_ok=True)
    entry: dict = {"path": _norm(TARGET), "current": None, "history": []}
    if released_ago_s is not None:
        entry["history"].append({
            "owner": OWNER, "released_at": time.time() - released_ago_s,
        })
    path = locks / "claims.json"
    path.write_text(json.dumps({"version": 1, "claims": {_norm(TARGET): entry}}),
                    encoding="utf-8")
    return path


def _tree(tmp_path: Path, *, active: bool, released_ago_s: float | None = None) -> Path:
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "scripts").mkdir()
    shutil.copy2(SCRIPT, repo / "scripts" / "append_coordination_row.py")
    shutil.copy2(WINDOW, repo / "scripts" / "claim_window.py")
    shutil.copy2(REPO / "scripts" / "file_claim.py", repo / "scripts" / "file_claim.py")
    (repo / "docs" / "COORDINATION.md").write_text(DOC_HEAD, encoding="utf-8", newline="")
    (repo / TARGET).write_text("# target\n", encoding="utf-8", newline="")
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(["git", "add", TARGET], cwd=str(repo), check=True)
    if active:
        _marker(repo, TARGET)
    _registry(repo, released_ago_s)
    return repo


def _run_check(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECK), "--staged",
         "--doc", str(repo / "docs" / "COORDINATION.md"),
         "--registry", str(repo.parent / "locks" / "claims.json"),
         "--markers", str(repo.parent / "locks" / "claims")],
        cwd=str(repo), capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "MBDSDR_CLAIM_OWNER": OWNER},
    )


def _run_append(repo: Path, note: str = "窗口用例") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(repo / "scripts" / "append_coordination_row.py"),
         "--path", TARGET, "--owner", OWNER, "--note", note],
        cwd=str(repo), capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "MBDSDR_LOCKS_DIR": str(repo.parent / "locks")},
    )


def _hook_env(repo: Path) -> dict:
    scripts = REPO / "scripts"
    return {
        **os.environ,
        "MBDSDR_CLAIM_OWNER": OWNER,
        "MBDSDR_LOCKS_DIR": str(repo.parent / "locks"),
        "MBDSDR_HOOK_MARKER_DIR": str(repo.parent / "hook_ok"),
        "MBDSDR_SCOPE_SCRIPT": str(scripts / "check_commit_scope.py"),
        "MBDSDR_CLAIM_GATE": str(scripts / "claim_gate.py"),
        "MBDSDR_SECRET_SCAN": str(scripts / "secret_scan.py"),
        "MBDSDR_CLAIM_COORD": str(scripts / "check_claim_coordination.py"),
        "MBDSDR_COORD_DOC": str(repo / "docs" / "COORDINATION.md"),
        "MBDSDR_COORD_REGISTRY": str(repo.parent / "locks" / "claims.json"),
        "MBDSDR_COORD_MARKERS": str(repo.parent / "locks" / "claims"),
    }


def _run_hook(repo: Path) -> subprocess.CompletedProcess:
    """真的跑一遍 pre-commit 钩子（不是在测试里复述它的判断）。"""
    return subprocess.run(
        [sys.executable, str(REPO / "scripts" / "hooks" / "pre-commit")],
        cwd=str(repo), capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=_hook_env(repo),
    )


def test_missing_row_blocks_and_printed_command_runs(tmp_path):
    """漏登记 → 拒绝；拒绝信息里的命令原样执行 → 补上；同一笔再跑 → 放行。"""
    repo = _tree(tmp_path, active=True)

    first = _run_check(repo)
    assert first.returncode == 3, first.stdout + first.stderr
    printed = re.search(r"python scripts/append_coordination_row\.py --path .*", first.stdout)
    assert printed, f"拒绝信息里没有可执行命令：{first.stdout}"

    # 钩子层的拒绝（④ 走的就是这条路径，超预算降级也在这里）
    blocked = _run_hook(repo)
    assert blocked.returncode == 3, blocked.stdout + blocked.stderr

    # 原样执行打印出来的那条命令（cwd=仓库根，不做任何替换）
    argv = shlex.split(printed.group(0))
    assert argv[0] == "python", argv
    fix = subprocess.run(
        [sys.executable, *argv[1:]], cwd=str(repo),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "MBDSDR_LOCKS_DIR": str(repo.parent / "locks")},
    )
    assert fix.returncode == 0, fix.stdout + fix.stderr

    second = _run_check(repo)
    assert second.returncode == 0, second.stdout + second.stderr
    passed = _run_hook(repo)
    assert passed.returncode == 0, passed.stdout + passed.stderr


def test_coordination_doc_itself_needs_no_row(tmp_path):
    """登记面不登记自己：COORDINATION.md 被活跃 claim 持有时仍可直接提交。

    回归靶子：豁免原来只写在「60 分钟释放窗口」那条分支，活跃 claim 分支漏了，
    于是提交登记表要先给自己写一行临时行、提交后再删掉——工具逼人制造失真。
    """
    repo = _tree(tmp_path, active=False)
    subprocess.run(["git", "add", "docs/COORDINATION.md"], cwd=str(repo), check=True)
    _marker(repo, "docs/COORDINATION.md")
    result = _run_check(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_release_window_is_one_shared_judgement(tmp_path):
    """55 分钟前释放：闸要行 + 工具接受；61 分钟前：闸不要 + 工具也不收。"""
    inside = _tree(tmp_path / "inside", active=False, released_ago_s=55 * 60)
    assert _run_check(inside).returncode == 3
    append_inside = _run_append(inside)
    assert append_inside.returncode == 0, append_inside.stderr
    assert _run_check(inside).returncode == 0

    outside = _tree(tmp_path / "outside", active=False, released_ago_s=61 * 60)
    assert _run_check(outside).returncode == 0
    append_outside = _run_append(outside)
    assert append_outside.returncode != 0
    assert "既没有活跃 claim" in append_outside.stderr
