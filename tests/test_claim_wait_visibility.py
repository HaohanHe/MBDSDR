#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""等待可见（方案 1）：被挡的人不再隐形。

契约（2026-11-13 裁定）：

* **只有冲突才写** ``.locks/waiting.log``（路径 / 时间 / 尝试者记名）；
  正常 claim / 续租不新增行。
* 持有者下次**续租**或 **append 成功**时打印一行「另有 N 个进程在此文件上
  等待过（最早 <时间>）」，打印后该路径的计数清零（读出即删）。
* **只提示不拦**：退出码与判定不因这张表改变。

为什么要有：持有方付 0 成本、被挡方付全部成本、等待不可见——占位对持有者
是隐形的，于是「不 claim」是理性选择。这张表只补「看得见」这一件事。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLAIM = REPO / "scripts" / "file_claim.py"
APPEND = REPO / "scripts" / "append_coordination_row.py"
SHARED = "desktop/main_window.py"
OWNER_A = "甲线/aaaa1111"
OWNER_B = "乙线/bbbb2222"

DOC = """# 协调台账

### 6.1 当前占用

| 文件 | 线名 | 开始时间 | 预计提交 hash |
| --- | --- | --- | --- |
| `docs/other.md` | 别的线（背景） | 2026-11-13 01:00 | 随本笔 |

### 6.2 下一节
"""


def _env(locks: Path, root: Path = REPO) -> dict:
    env = os.environ.copy()
    env["MBDSDR_LOCKS_DIR"] = str(locks)
    env["MBDSDR_CLAIM_ROOT"] = str(root)
    env.pop("PYTHONPATH", None)
    return env


def run_claim(locks: Path, *args: str, root: Path = REPO) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CLAIM), *args],
        cwd=root,
        env=_env(locks, root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


def run_append(locks: Path, root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(APPEND),
         "--path", "scripts/note.py", "--owner", OWNER_A,
         "--note", "等待可见测试", "--root", str(root)],
        cwd=REPO,
        env=_env(locks, root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


def _waits(locks: Path) -> list[dict]:
    path = locks / "waiting.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_conflict_writes_one_wait_line(tmp_path: Path) -> None:
    """两进程抢同一路径 → 等待方落一行；被拒的退出码仍是 2（只留痕不拦）。"""
    locks = tmp_path / "locks"
    first = run_claim(locks, "claim", SHARED, "--owner", OWNER_A)
    assert first.returncode == 0, first.stderr
    second = run_claim(locks, "claim", SHARED, "--owner", OWNER_B)
    assert second.returncode == 2, f"冲突的退出码不许变：{second.stdout}{second.stderr}"

    waits = _waits(locks)
    assert len(waits) == 1, waits
    rec = waits[0]
    assert rec["path"].endswith("desktop/main_window.py")
    assert rec["waiter"] == OWNER_B
    assert rec["holder"] == OWNER_A
    assert rec["kind"] == "held"
    assert rec["at"] and rec["at_iso"]


def test_renew_reports_and_clears_waits(tmp_path: Path) -> None:
    """持有方释放前（续租时）能看到 N≥1；打印后清零。"""
    locks = tmp_path / "locks"
    assert run_claim(locks, "claim", SHARED, "--owner", OWNER_A).returncode == 0
    assert run_claim(locks, "claim", SHARED, "--owner", OWNER_B).returncode == 2
    assert len(_waits(locks)) == 1

    renewed = run_claim(locks, "claim", SHARED, "--owner", OWNER_A, "--reason", "继续改这一笔")
    assert renewed.returncode == 0, renewed.stderr
    assert "另有 1 个进程在此文件上等待过" in renewed.stdout
    assert "最早" in renewed.stdout and OWNER_B in renewed.stdout
    assert _waits(locks) == [], "打印完必须清零，否则每次续租都在念旧账"


def test_no_conflict_adds_no_lines(tmp_path: Path) -> None:
    """没有冲突时该文件不新增行（claim + 续租都不写）。"""
    locks = tmp_path / "locks"
    assert run_claim(locks, "claim", SHARED, "--owner", OWNER_A).returncode == 0
    renewed = run_claim(locks, "claim", SHARED, "--owner", OWNER_A, "--reason", "续租")
    assert renewed.returncode == 0, renewed.stderr
    assert _waits(locks) == []
    assert "WAIT" not in renewed.stdout


def test_append_success_reports_waits(tmp_path: Path) -> None:
    """append 成功时同样打印等待人，并把该路径的计数清零。"""
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "docs" / "COORDINATION.md").write_text(DOC, encoding="utf-8")
    (root / "scripts" / "note.py").write_text("x = 1\n", encoding="utf-8")
    locks = tmp_path / "locks"

    claimed = run_claim(locks, "claim", "scripts/note.py", "--owner", OWNER_A, root=root)
    assert claimed.returncode == 0, claimed.stderr
    locks.mkdir(parents=True, exist_ok=True)
    (locks / "waiting.log").write_text(json.dumps({
        "at": time.time(), "at_iso": "2026-11-13 21:00:00",
        "path": "docs/COORDINATION.md", "key": "docs/coordination.md",
        "waiter": OWNER_B, "holder": OWNER_A, "kind": "held",
    }, ensure_ascii=False) + "\n", encoding="utf-8")

    out = run_append(locks, root)
    assert out.returncode == 0, f"{out.stdout}\n{out.stderr}"
    assert "另有 1 个进程在此文件上等待过" in out.stdout
    assert _waits(locks) == []
