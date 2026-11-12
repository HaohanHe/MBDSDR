#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""secret_scan.py 的反证：三条规则必须真实命中，豁免必须只放行被标注的那行。

契约（2026-11-13）：
  * 形状规则（假 ghp_）、argv 规则（--token / subprocess 传凭据变量）、
    日志规则（print api_key）各命中一条，且报告里只出现「文件:行 + 规则名」，
    绝不出现假值本身。
  * 给其中一行加 ``pragma: allowlist secret`` 后，只有那条消失，其余仍然命中。
  * 熵告警单独一档，不拦截（退出码仍由 BLOCK 决定）。
  * --staged 读 index 而不是工作树：工作树洗掉凭据也不能放过已暂存的内容。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "secret_scan.py"
FAKE = "ghp_" + "A" * 36
BLOB = "Qm9ndXNTdHJpbmdXaXRoRW5vdWdoRW50cm9weTEyMzQ1Njc4OTBhYmNkZWY"


def run_scan(root: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(cwd or REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def _leaky_text(with_allowlist: bool = False) -> str:
    log_line = 'print(f"api_key={TOKEN}")\n'
    if with_allowlist:
        log_line = 'print(f"api_key={TOKEN}")  # pragma: allowlist secret\n'
    return (
        "import argparse\n"
        "import subprocess\n"
        f'TOKEN = "{FAKE}"\n'
        "p = argparse.ArgumentParser()\n"
        'p.add_argument("--token")\n'
        'subprocess.run(["curl", "-H", f"Authorization: token {TOKEN}"])\n'
        f"{log_line}"
        f'BLOB = "{BLOB}"\n'
    )


def test_three_rules_hit_and_output_never_leaks_value(tmp_path: Path) -> None:
    leaky = tmp_path / "leaky.py"
    leaky.write_text(_leaky_text(), encoding="utf-8")

    result = run_scan(tmp_path, "--root", str(tmp_path))
    text = result.stdout + result.stderr

    assert result.returncode == 1, text
    assert f"leaky.py:3 github-pat-classic" in text
    assert "leaky.py:5 argv-cli-credential-option" in text
    assert "leaky.py:6 argv-credential-in-command" in text
    assert "leaky.py:7 log-credential-value" in text
    assert "leaky.py:8 high-entropy-string" in text

    # 扫描器自己的输出不能成为新的泄露面
    assert FAKE not in text
    assert BLOB not in text


def test_inline_allowlist_silences_only_that_line(tmp_path: Path) -> None:
    (tmp_path / "leaky.py").write_text(_leaky_text(with_allowlist=True), encoding="utf-8")

    result = run_scan(tmp_path, "--root", str(tmp_path))
    text = result.stdout + result.stderr

    assert "log-credential-value" not in text, text
    # 其余三条仍然必须命中：豁免范围不能外溢
    assert "github-pat-classic" in text
    assert "argv-cli-credential-option" in text
    assert "argv-credential-in-command" in text
    assert "high-entropy-string" in text
    assert FAKE not in text


def test_staged_mode_checks_index_not_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    git("init", "-q")
    leaky = repo / "leaky.py"
    leaky.write_text(f'TOKEN = "{FAKE}"\n', encoding="utf-8")
    git("add", "leaky.py")
    # 工作树洗掉凭据，但暂存区仍保留——闸门必须看 index
    leaky.write_text("TOKEN = None\n", encoding="utf-8")

    result = run_scan(repo, "--staged", cwd=repo)
    text = result.stdout + result.stderr
    assert result.returncode == 1, text
    assert "leaky.py:1 github-pat-classic" in text
    assert FAKE not in text
