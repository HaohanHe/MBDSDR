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

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "secret_scan.py"
HOOK = REPO / "scripts" / "hooks" / "commit-msg"
FAKE = "ghp_" + "A" * 36
#: 提交信息通道用另一个值，避免和文件扫描用例串味。
MSG_FAKE = "ghp_" + "C" * 36
BLOB = "Qm9ndXNTdHJpbmdXaXRoRW5vdWdoRW50cm9weTEyMzQ1Njc4OTBhYmNkZWY"


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> str:
        proc = subprocess.run(
            ["git", *args], cwd=repo, check=True, capture_output=True,
            text=True, encoding="utf-8",
        )
        return proc.stdout

    git("init", "-q")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    return repo


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


def test_large_file_still_gets_shape_rules(tmp_path: Path) -> None:
    """超过 2MB 只是不做熵检查：形状规则必须照跑，且流式读不整块进内存。"""
    big = tmp_path / "big.csv"
    filler = "a,b,c\n" * ((3 * 1024 * 1024) // 6)
    big.write_text(filler + f'"{FAKE}",x\n', encoding="utf-8")

    result = run_scan(tmp_path, "--root", str(tmp_path))
    text = result.stdout + result.stderr
    assert result.returncode == 1, text
    assert "big.csv" in text, text
    assert "github-pat-classic" in text, text
    assert "大文件仅形状检查 1 个" in text, text
    assert FAKE not in text

    # 删掉那一行：同一体积下必须干净，证明拦的是形状而不是「文件大」。
    big.write_text(filler, encoding="utf-8")
    clean = run_scan(tmp_path, "--root", str(tmp_path))
    text2 = clean.stdout + clean.stderr
    assert clean.returncode == 0, text2
    assert "github-pat-classic" not in text2, text2


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


def test_history_mode_still_sees_secret_deleted_from_head(tmp_path: Path) -> None:
    """工作树 0 BLOCK ≠ 没泄露过：凭据提交过再删掉，历史模式必须仍报。

    反证同时钉住两件事：命中提交仍可从 HEAD 到达（删掉内容的提交是 HEAD
    的祖先），以及输出里仍然只有路径/行号/规则/提交号，没有值。
    """
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    leaky = repo / "leaky.py"
    leaky.write_text(f'TOKEN = "{FAKE}"\n', encoding="utf-8")
    git("add", "leaky.py")
    git("commit", "-q", "-m", "leak")
    leaky.write_text("TOKEN = None\n", encoding="utf-8")
    git("add", "leaky.py")
    git("commit", "-q", "-m", "fix")

    clean = run_scan(repo, "--root", str(repo), cwd=repo)
    assert clean.returncode == 0, clean.stdout + clean.stderr

    history = run_scan(repo, "--history", cwd=repo)
    text = history.stdout + history.stderr
    assert history.returncode == 0, text
    assert "HISTORY leaky.py:1 github-pat-classic" in text
    assert "blob 扫描 2 个" in text
    assert "路径=1 个，提交=1 个" in text
    assert "仍可从 HEAD 到达 1 个" in text
    assert FAKE not in text


# ----------------------------------------------------------------------
# 提交信息通道：commit message / annotated tag / notes
# ----------------------------------------------------------------------
def test_history_mode_sees_secret_in_commit_message(tmp_path: Path) -> None:
    """`git commit -m "ghp_…"` 不产生 blob：blob 扫描看不见，消息扫描必须看见。"""
    repo = _init_repo(tmp_path)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", f"rotate token {MSG_FAKE}"],
        cwd=repo, check=True, capture_output=True,
    )
    leaked = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True,
        text=True,
    ).stdout.strip()
    (repo / "a.txt").write_text("b\n", encoding="utf-8")
    subprocess.run(
        ["git", "commit", "-q", "-am", "clean follow-up"],
        cwd=repo, check=True, capture_output=True,
    )

    clean = run_scan(repo, "--root", str(repo), cwd=repo)
    assert clean.returncode == 0, clean.stdout + clean.stderr

    history = run_scan(repo, "--history", cwd=repo)
    text = history.stdout + history.stderr
    assert history.returncode == 0, text
    # 报出的是提交号 + 行号 + 规则名，不是路径（消息不属于任何文件）
    assert f"HISTORY-MSG {leaked[:12]}:1 github-pat-classic" in text, text
    assert "commit message 2 条" in text, text
    assert "提交信息命中：1 条" in text, text
    assert MSG_FAKE not in text


def test_history_mode_sees_secret_in_annotated_tag(tmp_path: Path) -> None:
    """annotated tag 的 message 是独立对象，blob 扫描同样看不见。"""
    repo = _init_repo(tmp_path)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "clean message"],
        cwd=repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "tag", "-a", "v1.0", "-m", f"release {MSG_FAKE}"],
        cwd=repo, check=True, capture_output=True,
    )

    history = run_scan(repo, "--history", cwd=repo)
    text = history.stdout + history.stderr
    assert history.returncode == 0, text
    assert "HISTORY-MSG tag:v1.0:1 github-pat-classic" in text, text
    assert "annotated tag 1 个" in text, text
    assert "提交信息命中：1 条" in text, text
    assert MSG_FAKE not in text


def test_history_mode_sees_secret_in_notes(tmp_path: Path) -> None:
    """`git notes` 也是独立 blob，且不在 rev-list 的路径索引里。"""
    repo = _init_repo(tmp_path)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "clean message"],
        cwd=repo, check=True, capture_output=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "notes", "add", "-m", f"deploy {MSG_FAKE}", "HEAD"],
        cwd=repo, check=True, capture_output=True,
    )

    history = run_scan(repo, "--history", cwd=repo)
    text = history.stdout + history.stderr
    assert history.returncode == 0, text
    assert f"HISTORY-MSG note:{head[:12]}:1 github-pat-classic" in text, text
    assert "notes 1 条" in text, text
    assert "提交信息命中：1 条" in text, text
    assert MSG_FAKE not in text


def test_history_mode_message_scan_is_zero_when_clean(tmp_path: Path) -> None:
    """清干净后必须是 0 条：证明上面三条命中不是「有消息就报」。"""
    repo = _init_repo(tmp_path)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "clean message"],
        cwd=repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "tag", "-a", "v1.0", "-m", "release notes"],
        cwd=repo, check=True, capture_output=True,
    )

    history = run_scan(repo, "--history", cwd=repo)
    text = history.stdout + history.stderr
    assert history.returncode == 0, text
    assert "HISTORY-MSG" not in text, text
    assert "提交信息命中：0 条" in text, text
    assert "commit message 1 条" in text, text
    assert "annotated tag 1 个" in text, text


# ----------------------------------------------------------------------
# commit-msg 闸：写入侧拦截（pre-commit 看不见这条通道）
# ----------------------------------------------------------------------
def test_commit_msg_mode_blocks_value_and_no_false_positive_on_prose(
    tmp_path: Path,
) -> None:
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text(f"fix: rotate token\n\nleaked {MSG_FAKE}\n", encoding="utf-8")

    bad = run_scan(tmp_path, "--commit-msg", str(msg))
    text = bad.stdout + bad.stderr
    assert bad.returncode == 1, text
    assert ":3 github-pat-classic" in text, text
    assert MSG_FAKE not in text

    # 只写「不要用 ghp_ 开头」这类说明文字：形状规则要求完整 token，不该误报。
    msg.write_text(
        "docs: 说明不要用 ghp_ 开头的明文凭据，也不要贴 AKIA 样例全串\n",
        encoding="utf-8",
    )
    prose = run_scan(tmp_path, "--commit-msg", str(msg))
    assert prose.returncode == 0, prose.stdout + prose.stderr
    assert "BLOCK=0" in prose.stdout


def test_commit_msg_hook_blocks_real_commit(tmp_path: Path) -> None:
    """把钩子真的装进临时仓库：`git commit -m "ghp_…"` 必须被拒绝。"""
    repo = _init_repo(tmp_path)
    hook = repo / ".git" / "hooks" / "commit-msg"
    hook.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HOOK, hook)
    os.chmod(hook, 0o755)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True, capture_output=True)

    env = dict(os.environ, MBDSDR_SECRET_SCAN=str(SCRIPT))
    blocked = subprocess.run(
        ["git", "commit", "-q", "-m", f"leak {MSG_FAKE}"],
        cwd=repo, capture_output=True, text=True, encoding="utf-8", env=env,
    )
    assert blocked.returncode != 0
    assert "拒绝提交" in (blocked.stdout + blocked.stderr)
    assert MSG_FAKE not in (blocked.stdout + blocked.stderr)
    count = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"], cwd=repo, capture_output=True,
        check=False, text=True,
    )
    # 没有任何提交被创建（HEAD 还不存在）
    assert count.returncode != 0

    ok = subprocess.run(
        ["git", "commit", "-q", "-m", "clean message"],
        cwd=repo, capture_output=True, text=True, encoding="utf-8", env=env,
    )
    assert ok.returncode == 0, ok.stdout + ok.stderr
