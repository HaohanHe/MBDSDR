#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""无账红机检的反证：守卫返回 1 但没有 K 条目/豁免时，必须报出来。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "check_red_accounting.py"


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def _repo(tmp_path: Path, exit_code: int = 1) -> Path:
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "fake_guard.py").write_text(
        f"import sys\nsys.exit({exit_code})\n", encoding="utf-8"
    )
    (root / "docs" / "known-red-register.md").write_text("# 已知红台账\n", encoding="utf-8")
    return root


def _registry(root: Path, entry: dict) -> None:
    (root / "docs" / "guard-accounting.json").write_text(
        json.dumps({"version": 1, "guards": [entry]}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_red_without_account_is_unaccounted(tmp_path: Path) -> None:
    """反证：守卫 rc=1 且没有 K 条目/豁免 → 必须判「无账红」。"""
    root = _repo(tmp_path)
    _registry(root, {"id": "fake", "script": "fake_guard.py", "mode": "run"})
    proc = _run(root)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "无账红 fake" in proc.stdout
    assert "结论：FAIL" in proc.stdout


def test_red_with_k_entry_passes(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "docs" / "known-red-register.md").write_text(
        "## K99 — 假守卫（未修）\n", encoding="utf-8"
    )
    _registry(root, {"id": "fake", "script": "fake_guard.py", "mode": "run", "k": ["K99"]})
    proc = _run(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "-> K99" in proc.stdout


def test_k_range_heading_is_expanded(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "docs" / "known-red-register.md").write_text(
        "## K01–K04：假守卫组（存量）\n", encoding="utf-8"
    )
    _registry(root, {"id": "fake", "script": "fake_guard.py", "mode": "run", "k": ["K03"]})
    proc = _run(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_dangling_k_is_reported(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _registry(root, {"id": "fake", "script": "fake_guard.py", "mode": "run", "k": ["K99"]})
    proc = _run(root)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "账目悬空 fake" in proc.stdout
    assert "K99" in proc.stdout


def test_dated_exemption_passes(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _registry(root, {
        "id": "fake", "script": "fake_guard.py", "mode": "run",
        "exemption": {"reason": "外部边界，仓外文件不在本线范围", "owner": "安全 owner",
                      "review_by": "2099-12"},
    })
    proc = _run(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "豁免至 2099-12" in proc.stdout


def test_expired_exemption_is_reported(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _registry(root, {
        "id": "fake", "script": "fake_guard.py", "mode": "run",
        "exemption": {"reason": "外部边界，仓外文件不在本线范围", "owner": "安全 owner",
                      "review_by": "2000-01"},
    })
    proc = _run(root)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "豁免失效 fake" in proc.stdout


def test_manual_without_reason_is_reported(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _registry(root, {"id": "manual-one", "script": "fake_guard.py", "mode": "manual",
                     "review_by": "2099-12"})
    proc = _run(root)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "未跑无期限 manual-one" in proc.stdout


def test_green_guard_needs_no_account(tmp_path: Path) -> None:
    root = _repo(tmp_path, exit_code=0)
    _registry(root, {"id": "fake", "script": "fake_guard.py", "mode": "run"})
    proc = _run(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "GUARD fake: 绿 rc=0" in proc.stdout
