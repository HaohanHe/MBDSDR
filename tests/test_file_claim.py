#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""file_claim.py 行为测试：真实起进程，不用同进程调两次冒充并发。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "file_claim.py"
SHARED = "desktop/main_window.py"


def _env(locks: Path) -> dict:
    env = os.environ.copy()
    env["MBDSDR_LOCKS_DIR"] = str(locks)
    env.pop("PYTHONPATH", None)
    return env


def run(locks: Path, *args: str, timeout: float = 60.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO,
        env=_env(locks),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )


def spawn(locks: Path, *args: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO,
        env=_env(locks),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )


def _registry(locks: Path) -> dict:
    path = locks / "claims.json"
    assert path.exists(), f"台账未生成：{path}"
    return json.loads(path.read_text(encoding="utf-8"))


def test_two_processes_race_exactly_one_wins(tmp_path: Path) -> None:
    """两个真进程并发 claim 同一文件，恰好一个成功，另一个失败并报持有者。"""
    locks = tmp_path / "locks"
    first = spawn(locks, "claim", SHARED, "--owner", "线A")
    second = spawn(locks, "claim", SHARED, "--owner", "线B")
    out_a, err_a = first.communicate(timeout=60)
    out_b, err_b = second.communicate(timeout=60)

    codes = [first.returncode, second.returncode]
    assert sorted(codes) == [0, 2], f"期望恰好一胜一败，实际 {codes}\nA={out_a}{err_a}\nB={out_b}{err_b}"

    winner_out = out_a if first.returncode == 0 else out_b
    loser_err = err_b if first.returncode == 0 else err_a
    loser_out = out_b if first.returncode == 0 else out_a
    winner_owner = "线A" if first.returncode == 0 else "线B"
    loser_owner = "线B" if first.returncode == 0 else "线A"

    assert "CLAIM OK" in winner_out
    assert winner_owner in winner_out
    denied_text = loser_err + loser_out
    assert "CLAIM DENIED" in denied_text
    assert f"当前持有者={winner_owner}" in denied_text
    assert "已持有=" in denied_text

    status = run(locks, "status", SHARED)
    assert status.returncode == 0
    assert f"owner={winner_owner}" in status.stdout
    assert loser_owner not in status.stdout


def test_same_owner_claim_renews(tmp_path: Path) -> None:
    locks = tmp_path / "locks"
    first = run(locks, "claim", SHARED, "--owner", "线A", "--ttl-min", "0.01")
    assert first.returncode == 0, first.stderr
    time.sleep(0.2)
    again = run(locks, "claim", SHARED, "--owner", "线A", "--ttl-min", "10")
    assert again.returncode == 0, again.stderr
    assert "CLAIM RENEWED" in again.stdout
    status = run(locks, "status", SHARED)
    assert "owner=线A" in status.stdout


def test_expired_claim_can_be_stolen(tmp_path: Path) -> None:
    locks = tmp_path / "locks"
    first = run(locks, "claim", SHARED, "--owner", "线A", "--ttl-min", "0.01")
    assert first.returncode == 0, first.stderr
    time.sleep(1.0)  # TTL = 0.6s

    stolen = run(locks, "claim", SHARED, "--owner", "线B", "--ttl-min", "10")
    assert stolen.returncode == 0, stolen.stderr
    combined = stolen.stdout + stolen.stderr
    assert "原持有者 线A 已超时" in combined
    assert "CLAIM OK (steal)" in stolen.stdout

    status = run(locks, "status", SHARED)
    assert "owner=线B" in status.stdout

    history = _registry(locks)["claims"]
    entry = next(iter(history.values()))
    assert [item["owner"] for item in entry["history"]][:2] == ["线A", "线B"]
    assert entry["history"][0]["closed_reason"] == "expired_steal"


def test_release_by_non_owner_is_denied(tmp_path: Path) -> None:
    locks = tmp_path / "locks"
    assert run(locks, "claim", SHARED, "--owner", "线A").returncode == 0

    denied = run(locks, "release", SHARED, "--owner", "线B")
    assert denied.returncode == 2
    text = denied.stdout + denied.stderr
    assert "RELEASE DENIED" in text
    assert "当前持有者=线A" in text

    still = run(locks, "status", SHARED)
    assert "owner=线A" in still.stdout

    ok = run(locks, "release", SHARED, "--owner", "线A")
    assert ok.returncode == 0, ok.stderr
    assert "RELEASE OK" in ok.stdout
    assert run(locks, "status", SHARED).stdout.find("未占用") >= 0


def test_release_without_claim_fails(tmp_path: Path) -> None:
    locks = tmp_path / "locks"
    result = run(locks, "release", SHARED, "--owner", "线A")
    assert result.returncode == 2
    assert "没有 claim" in (result.stdout + result.stderr)


def test_path_normalization_relative_and_absolute(tmp_path: Path) -> None:
    """desktop/main_window.py 与 D:\\mbdsdr\\MBDSDR\\desktop\\main_window.py 同一文件。"""
    locks = tmp_path / "locks"
    assert run(locks, "claim", SHARED, "--owner", "线A").returncode == 0

    absolute = str(REPO / "desktop" / "main_window.py")
    assert run(locks, "claim", absolute, "--owner", "线B").returncode == 2

    forward = (REPO / "desktop/main_window.py").as_posix()
    status = run(locks, "status", forward)
    assert "owner=线A" in status.stdout

    # 反斜杠相对路径同样归一
    assert run(locks, "claim", "desktop\\main_window.py", "--owner", "线B").returncode == 2


def test_status_lists_claims_and_registry_location(tmp_path: Path) -> None:
    locks = tmp_path / "locks"
    assert run(locks, "claim", SHARED, "--owner", "线A").returncode == 0
    assert run(locks, "claim", "desktop/spectrum_widget.py", "--owner", "线B").returncode == 0

    status = run(locks, "status")
    assert status.returncode == 0
    assert "owner=线A" in status.stdout
    assert "owner=线B" in status.stdout
    assert "2 条 claim" in status.stdout

    data = _registry(locks)
    assert json.loads((locks / "claims.json").read_text(encoding="utf-8")) == data
    assert len(data["claims"]) == 2
    # 占位文件与台账都在指定锁目录内（不碰真实 D:\mbdsdr\.locks）
    assert any((locks / "claims").glob("*.claim"))
