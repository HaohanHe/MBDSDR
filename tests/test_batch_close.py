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
#: cleanup-stale --json 的最小可解析输出（没有待办，rc=0）。
CLEANUP_OK = (
    "import json, sys\n"
    'print(json.dumps({"total": 4,\n'
    '  "summary": {"releasable": 0, "suspect": 0, "undecidable": 4,\n'
    '              "live": 0, "wait": 0},\n'
    '  "undecidable": {"count": 4, "ratio": 1.0,\n'
    '                  "reasons": {"post_claim_commit": 4}},\n'
    '  "rows": []}, ensure_ascii=False))\n'
    "sys.exit(0)\n"
)
#: rc=3 = 有可释放未释放：NOTE，不许把批次判红。
CLEANUP_TODO = (
    "import json, sys\n"
    'print(json.dumps({"total": 2,\n'
    '  "summary": {"releasable": 1, "suspect": 0, "undecidable": 1,\n'
    '              "live": 0, "wait": 0},\n'
    '  "undecidable": {"count": 1, "ratio": 0.5,\n'
    '                  "reasons": {"post_claim_commit": 1}},\n'
    '  "rows": []}, ensure_ascii=False))\n'
    "sys.exit(3)\n"
)
#: 一条 SUSPECT：只报告缺口 + 补法，不影响红绿。
CLEANUP_SUSPECT = (
    "import json, sys\n"
    'print(json.dumps({"total": 1,\n'
    '  "summary": {"releasable": 0, "suspect": 1, "undecidable": 0,\n'
    '              "live": 0, "wait": 0},\n'
    '  "undecidable": {"count": 0, "ratio": 0.0, "reasons": {}},\n'
    '  "rows": [{"path": "a.py", "owner": "线A/abc", "category": "suspect",\n'
    '            "expired_min": 42.0, "gaps": ["content(GAP: 无基线)"],\n'
    '            "hint": "确认文件未被改动后 release 并重新 claim"}]},\n'
    "  ensure_ascii=False))\n"
    "sys.exit(0)\n"
)
#: 检查器自己抛异常：rc=1 + stderr 回溯，与「真判红」共用退出码。
CRASH = 'raise RuntimeError("boom")\n'
#: rc=1 但没有回溯：这是真的判红，不能被启发式吞掉。
RED_WITH_STDERR = 'import sys\nprint("real red", file=sys.stderr)\nsys.exit(1)\n'
#: 把回溯当**数据**打到 stdout：不在 stderr，不能触发启发式。
TRACEBACK_IN_STDOUT = (
    'print("Traceback (most recent call last):")\n'
    'print("RuntimeError: just data")\n'
    "raise SystemExit(1)\n"
)


def _write_step(root: Path, script: str, body: str) -> None:
    scripts = root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / script).write_text(body, encoding="utf-8")


def _green_root(root: Path) -> None:
    _write_step(root, "audit_evidence.py", GREEN.format(name="evidence"))
    _write_step(root, "check_red_lifecycle.py", RED_LIFECYCLE_MARKER)
    _write_step(root, "check_roadmap_blocking.py", ROADMAP_MARKER)
    _write_step(root, "file_claim.py", CLEANUP_OK)


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
    assert "cleanup" in proc.stderr
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
        "cleanup",
    ]
    assert payload["steps"][-1]["status"] == "NOTE"
    assert payload["steps"][-1]["metrics"]["undecidable"] == 4
    records = list((tmp_path / ".tmp" / "batch_close").glob("*.json"))
    assert len(records) == 1
    saved = json.loads(records[0].read_text(encoding="utf-8"))
    assert saved["exit_code"] == 0


def test_rc1_with_traceback_is_heuristic_not_red(tmp_path: Path) -> None:
    """检查器崩溃（stderr 回溯）归到 3，并在输出里写明是启发式。"""
    _green_root(tmp_path)
    _write_step(tmp_path, "audit_evidence.py", CRASH)
    proc = _run(tmp_path)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "[ERROR] evidence" in proc.stdout
    assert "RuntimeError" in proc.stdout
    assert "启发式" in proc.stdout
    assert "结论不成立" in proc.stdout


def test_rc1_without_traceback_stays_red(tmp_path: Path) -> None:
    """只有 rc=1 + 普通 stderr 时仍按真红处理，启发式不许扩大。"""
    _green_root(tmp_path)
    _write_step(tmp_path, "audit_evidence.py", RED_WITH_STDERR)
    proc = _run(tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "[FAIL] evidence" in proc.stdout
    assert "启发式" not in proc.stdout


def test_traceback_in_stdout_does_not_trigger_heuristic(tmp_path: Path) -> None:
    """回溯只认 stderr：stdout 里的 Traceback 可能只是被检查的数据。"""
    _green_root(tmp_path)
    _write_step(tmp_path, "audit_evidence.py", TRACEBACK_IN_STDOUT)
    proc = _run(tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "[FAIL] evidence" in proc.stdout


def test_json_records_crash_heuristic(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _green_root(root)
    _write_step(root, "audit_evidence.py", CRASH)
    proc = _run(root, "--json")
    assert proc.returncode == 3, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    step = payload["steps"][0]
    assert step["status"] == "ERROR"
    assert step["rc"] == 1
    assert "RuntimeError" in step["heuristic"]


def test_cleanup_todo_is_note_not_red(tmp_path: Path) -> None:
    """cleanup 的 rc=3（有可释放未释放）只是 NOTE：批次不因「没人清理」变红。"""
    _green_root(tmp_path)
    _write_step(tmp_path, "file_claim.py", CLEANUP_TODO)
    proc = _run(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[NOTE] cleanup" in proc.stdout
    assert "rc=3" in proc.stdout
    assert "待办·可释放未释放 1" in proc.stdout
    assert "UNDECIDABLE 1（50.0%）" in proc.stdout
    assert "结论：可以收" in proc.stdout


def test_cleanup_suspect_rows_report_gaps_and_hints(tmp_path: Path) -> None:
    _green_root(tmp_path)
    _write_step(tmp_path, "file_claim.py", CLEANUP_SUSPECT)
    proc = _run(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[NOTE] cleanup" in proc.stdout
    assert "SUSPECT a.py" in proc.stdout
    assert "content(GAP: 无基线)" in proc.stdout
    assert "补法：确认文件未被改动后 release 并重新 claim" in proc.stdout


def test_cleanup_crash_is_error(tmp_path: Path) -> None:
    """cleanup 自己崩了（stderr 回溯）→ 没跑成，不能算「没有待办」。"""
    _green_root(tmp_path)
    _write_step(tmp_path, "file_claim.py", CRASH)
    proc = _run(tmp_path)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "[ERROR] cleanup" in proc.stdout
    assert "RuntimeError" in proc.stdout


def test_cleanup_unreadable_output_is_error(tmp_path: Path) -> None:
    """rc 在约定内但输出不是 JSON：无法确认待办 → ERROR，不许吞成 NOTE。"""
    _green_root(tmp_path)
    _write_step(tmp_path, "file_claim.py", 'import sys\nprint("not json")\nsys.exit(0)\n')
    proc = _run(tmp_path)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "[ERROR] cleanup" in proc.stdout
    assert "无法确认待办" in proc.stdout


def test_cleanup_missing_script_is_error(tmp_path: Path) -> None:
    _green_root(tmp_path)
    (tmp_path / "scripts" / "file_claim.py").unlink()
    proc = _run(tmp_path)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "[ERROR] cleanup" in proc.stdout
    assert "缺文件" in proc.stdout
    assert "file_claim.py" in proc.stdout
