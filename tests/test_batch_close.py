#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""scripts/batch_close.py 的退出码语义与「不许吞成成功」行为。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "batch_close.py"

GREEN = 'import sys\nprint("STEP-OK {name}")\nsys.exit(0)\n'
RED = 'import sys\nprint("STEP-RED {name}")\nsys.exit(1)\n'
USAGE_ERROR = 'import sys\nprint("git error", file=sys.stderr)\nsys.exit(2)\n'
SLOW = "import time\ntime.sleep(5)\n"
RED_LIFECYCLE_MARKER = 'import sys\nprint("RED_LIFECYCLE-RAN")\nsys.exit(0)\n'
ROADMAP_MARKER = 'import sys\nprint("ROADMAP-RAN")\nsys.exit(0)\n'


def _write_step(root: Path, script: str, body: str) -> None:
    scripts = root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / script).write_text(body, encoding="utf-8")


def _green_root(root: Path) -> None:
    _write_step(root, "audit_evidence.py", GREEN.format(name="evidence"))
    _write_step(root, "check_red_lifecycle.py", RED_LIFECYCLE_MARKER)
    _write_step(root, "check_roadmap_blocking.py", ROADMAP_MARKER)


def _run(root: Path, *args: str, record: bool = False) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SCRIPT), "--root", str(root)]
    if not record:
        cmd.append("--no-record")
    cmd.extend(args)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def test_all_green_exits_zero(tmp_path: Path) -> None:
    _green_root(tmp_path)
    proc = _run(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[PASS] evidence" in proc.stdout
    assert "[PASS] red_lifecycle" in proc.stdout
    assert "[NOTE] roadmap" in proc.stdout


def test_red_does_not_hide_later_steps(tmp_path: Path) -> None:
    _green_root(tmp_path)
    _write_step(tmp_path, "audit_evidence.py", RED.format(name="evidence"))
    proc = _run(tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "[FAIL] evidence" in proc.stdout
    # 第一道判红后，后两道仍然跑了（各自的标记都要出现）
    assert "RED_LIFECYCLE-RAN" in proc.stdout
    assert "ROADMAP-RAN" in proc.stdout


def test_missing_script_exits_three_without_traceback(tmp_path: Path) -> None:
    _green_root(tmp_path)
    (tmp_path / "scripts" / "check_red_lifecycle.py").unlink()
    proc = _run(tmp_path)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "缺文件" in proc.stdout
    assert "check_red_lifecycle.py" in proc.stdout
    assert "Traceback" not in proc.stderr


def test_timeout_exits_three(tmp_path: Path) -> None:
    _green_root(tmp_path)
    _write_step(tmp_path, "audit_evidence.py", SLOW)
    proc = _run(tmp_path, "--timeout", "1")
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "超时" in proc.stdout


def test_unknown_step_exits_two_with_valid_names(tmp_path: Path) -> None:
    _green_root(tmp_path)
    proc = _run(tmp_path, "--steps", "nope")
    assert proc.returncode == 2
    assert "未知步骤" in proc.stderr
    assert "evidence" in proc.stderr
    assert "red_lifecycle" in proc.stderr
    assert "roadmap" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_unexpected_exit_code_is_not_run_not_red(tmp_path: Path) -> None:
    _green_root(tmp_path)
    _write_step(tmp_path, "check_red_lifecycle.py", USAGE_ERROR)
    proc = _run(tmp_path)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "[ERROR] red_lifecycle" in proc.stdout
    assert "退出码 2 不在约定内" in proc.stdout
    assert "git error" in proc.stdout


def test_not_run_outranks_red(tmp_path: Path) -> None:
    _green_root(tmp_path)
    _write_step(tmp_path, "audit_evidence.py", RED.format(name="evidence"))
    _write_step(tmp_path, "check_red_lifecycle.py", USAGE_ERROR)
    proc = _run(tmp_path)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "结论不成立" in proc.stdout


def test_json_mode_and_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _green_root(root)
    proc = _run(root, "--json", record=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["exit_code"] == 0
    assert [step["name"] for step in payload["steps"]] == [
        "evidence",
        "red_lifecycle",
        "roadmap",
    ]
    records = list((tmp_path / ".tmp" / "batch_close").glob("*.json"))
    assert len(records) == 1
    saved = json.loads(records[0].read_text(encoding="utf-8"))
    assert saved["exit_code"] == 0
