"""git_sync_index.py 回归测试。

重点覆盖「报告段」：`_index_vs_head` 的 diff 条目是三元组
`(path, why, sha)`，报告循环必须按三元组解包。3060f6ed 里写成二元组，
导致每笔用私有 index 的提交都在 post-commit 抛 ValueError——而当时
没有测试走这条路径，所以没被发现。

断言口径：
- 造一个 index 倒退（反向暂存）的临时仓，`--check` 必须**不抛异常**，
  退出码 1，且 stdout 同时出现被差异的 path 与 why；
- 按路径同步只清指定条目，别人的条目原样保留并返回 2；
- `--all` 全量同步后退出码 0、`git diff --cached` 为空。
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "git_sync_index.py"


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def _run(repo, *args):
    env = os.environ.copy()
    env.pop("GIT_INDEX_FILE", None)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "sync-test@example.invalid")
    _git(path, "config", "user.name", "sync-test")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "a.txt").write_text("a0\n", encoding="utf-8")
    (path / "b.txt").write_text("b0\n", encoding="utf-8")
    _git(path, "add", "a.txt", "b.txt")
    _git(path, "commit", "-q", "-m", "base")
    (path / "a.txt").write_text("a1\n", encoding="utf-8")
    (path / "b.txt").write_text("b1\n", encoding="utf-8")
    _git(path, "commit", "-q", "-am", "second")
    return path


def _reverse_stage(repo):
    """把共享 index 退回 HEAD~1，制造「反向暂存」（index 与 HEAD 不一致）。"""
    _git(repo, "read-tree", "HEAD~1")
    assert _git(repo, "diff", "--cached", "--name-only").stdout.strip()


def test_check_reports_diff_without_crashing(repo):
    _reverse_stage(repo)
    proc = _run(repo, "--check")
    out = proc.stdout
    assert proc.returncode == 1, (proc.returncode, out, proc.stderr)
    assert "ValueError" not in proc.stderr
    # 报告段必须真的打印出 path 与 why
    assert "a.txt" in out and "b.txt" in out
    assert "!= HEAD" in out
    assert "STAGED" in out


def test_paths_sync_only_touches_given_paths(repo):
    _reverse_stage(repo)
    proc = _run(repo, "--paths", "a.txt")
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    remaining = _git(repo, "diff", "--cached", "--name-only").stdout.split()
    assert remaining == ["b.txt"], remaining
    assert "未动" in proc.stderr


def test_all_sync_clears_everything(repo):
    _reverse_stage(repo)
    proc = _run(repo, "--all")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert _git(repo, "diff", "--cached", "--name-only").stdout.strip() == ""


def test_check_clean_repo_returns_zero(repo):
    proc = _run(repo, "--check")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "共享 index 与 HEAD 一致" in proc.stdout
