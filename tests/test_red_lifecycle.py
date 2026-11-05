#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""红项生命周期机检的反证：没举证就判红。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "check_red_lifecycle.py"


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
    (root / "docs" / "placeholder.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "docs/placeholder.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=root, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    return root


def _write(root: Path, name: str, text: str) -> str:
    path = root / "docs" / name
    path.write_text(text, encoding="utf-8")
    return f"docs/{name}"


def _sha(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_fixed_without_hash_is_red(tmp_path: Path) -> None:
    """反证 (a)：写「已修复」但没有 commit hash → R1 FAIL。"""
    root = _repo(tmp_path)
    path = _write(root, "a.md", "已修复：书签删除确认框不再早退。\n")
    proc = _run(root, "--paths", path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "[R1]" in proc.stdout


def test_bad_hash_is_red(tmp_path: Path) -> None:
    """反证 (b)：hash 打错一个字符 → R2 FAIL。"""
    root = _repo(tmp_path)
    path = _write(root, "b.md", "已修 `abc1234`：频谱上限拆分。\n")
    proc = _run(root, "--paths", path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "[R2]" in proc.stdout
    assert "abc1234" in proc.stdout


def test_not_verified_without_reason_is_red(tmp_path: Path) -> None:
    """反证 (c)：标 `not_verified` 但没写为什么没验/缺什么 → R4 FAIL。"""
    root = _repo(tmp_path)
    path = _write(root, "c.md", "频谱 8 槽：`not_verified`。\n")
    proc = _run(root, "--paths", path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "[R4]" in proc.stdout


def test_cross_doc_status_conflict_is_red(tmp_path: Path) -> None:
    """反证 (d)：同一测试名在两份文档里状态打架 → R5 FAIL。"""
    root = _repo(tmp_path)
    sha = _sha(root)
    good = _write(
        root,
        "d1.md",
        f"| `test_foo` | 已修 `{sha}` | 复跑 `pytest tests/test_foo.py` |\n",
    )
    bad = _write(root, "d2.md", "`test_foo` 标 `not_verified`，缺真机，无法复验。\n")
    proc = _run(root, "--paths", good, bad)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "[R5]" in proc.stdout
    assert "test_foo" in proc.stdout
    assert "d1.md" in proc.stdout and "d2.md" in proc.stdout


def test_well_formed_table_passes(tmp_path: Path) -> None:
    """真表口径：已修 + 有效 hash + 复跑命令 → PASS（WARN 不阻断）。"""
    root = _repo(tmp_path)
    sha = _sha(root)
    path = _write(
        root,
        "good.md",
        "| 用例 | 状态 | 复跑 |\n"
        "|---|---|---|\n"
        f"| `display::reRenderHistoryOnPaletteSwitch` | 已修 `{sha}` | "
        '`ctest -R "^(spectrum_display|spectrum_autorange)$"` |\n',
    )
    proc = _run(root, "--paths", path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS" in proc.stdout


def test_json_output_shape(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    path = _write(root, "j.md", "已修复：没有 hash。\n")
    proc = _run(root, "--paths", path, "--json")
    payload = json.loads(proc.stdout)
    assert payload["fail_count"] >= 1
    assert payload["findings"][0]["rule"] == "R1"
    assert payload["findings"][0]["path"] == "docs/j.md"
