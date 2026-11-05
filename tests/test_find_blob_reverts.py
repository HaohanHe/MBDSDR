#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""find_blob_reverts.py 的反证：两类已知回退必须命中，普通区间必须零命中。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "find_blob_reverts.py"


def run(*args: str, timeout: float = 180.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )


def test_detects_stale_index_writeback_f664638a() -> None:
    """`f664638a` 把 §6.10 前置规矩写回旧 blob：reflog 无 reset，但判据必须命中。"""
    out = run("--rev-range", "448d465f~1..4132d46f", "--paths", "docs/COORDINATION.md")
    assert out.returncode == 1, out.stdout + out.stderr
    assert "f664638a" in out.stdout
    assert "16f8cd09" in out.stdout
    assert "448d465f" in out.stdout
    assert "hits=1" in out.stdout


def test_detects_rolled_in_files_revert_09b53221() -> None:
    """`09b53221` 卷入的 4 个解码线文件被 `d8b40eb0` 退回：4 个路径各命中一次。"""
    out = run(
        "--rev-range",
        "09b53221~2..d8b40eb0",
        "--paths",
        "scripts/decode_sensitivity.py",
        "scripts/decoder_capability_report.py",
        "tests/test_decode_sensitivity.py",
        "docs/learn/decoder-capability-matrix.md",
    )
    assert out.returncode == 1, out.stdout + out.stderr
    assert "d8b40eb0" in out.stdout
    assert "09b53221" in out.stdout
    assert "hits=4" in out.stdout


def test_clean_range_has_zero_hits() -> None:
    """普通提交区间不得产生假阳性。"""
    out = run("--rev-range", "HEAD~20..HEAD")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "hits=0" in out.stdout
    assert "REVERT" not in out.stdout


def test_json_output_is_machine_readable() -> None:
    out = run("--rev-range", "448d465f~1..4132d46f", "--paths", "docs/COORDINATION.md", "--json")
    assert out.returncode == 1, out.stderr
    data = json.loads(out.stdout)
    assert data["hits"][0]["restore_commit"].startswith("f664638a")
    assert data["hits"][0]["undone_commit"].startswith("16f8cd09")
