"""形态⑩（私有 index + `git commit -- <paths>`，工作区覆盖 index）的机械闸测试。

事故：pathspec 提交会按**工作区**取内容，绕过私有 index；私有 index 的 blob
是对的（`git diff --cached` 只有自己的 hunk），提交却把别线在途改动一起带走。

覆盖：
- 私有 index 只装一行、工作区多一行，用 pathspec 提交 → `--post-commit` 必红，
  打印两侧 blob 与不一致的 path；
- 工作区与私有 index 一致 → 放行；
- 未声明私有 index → 明确 SKIP，不假报；
- `git_safe.py` 在私有 index 模式下拒 `commit` 带 pathspec，不带 pathspec 时放行。
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCOPE = ROOT / "scripts" / "check_commit_scope.py"
SAFE = ROOT / "scripts" / "git_safe.py"


def _git(repo, *args, env=None, check=False):
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        env=env or os.environ.copy(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def _env(repo, tmp, priv):
    env = os.environ.copy()
    env["MBDSDR_REPO_ROOT"] = str(repo)
    env["MBDSDR_LOCKS_DIR"] = str(tmp / "locks")
    env["GIT_INDEX_FILE"] = str(priv)
    env["MBDSDR_DECLARED_INDEX"] = str(priv)
    return env


def _commit_env(repo, tmp):
    """提交本身用**共享 index**、按 pathspec 从工作区取内容——事故现场就是这样。"""
    env = os.environ.copy()
    env.pop("GIT_INDEX_FILE", None)
    env["MBDSDR_REPO_ROOT"] = str(repo)
    env["MBDSDR_LOCKS_DIR"] = str(tmp / "locks")
    return env


def _make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", check=True)
    _git(repo, "config", "user.email", "scope-test@example.invalid", check=True)
    _git(repo, "config", "user.name", "scope-test", check=True)
    _git(repo, "config", "commit.gpgsign", "false", check=True)
    (repo / "a.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "a.txt", check=True)
    _git(repo, "commit", "-q", "-m", "base", check=True)
    return repo


def _prime_private_index(repo, tmp, text):
    """私有 index 只装 a.txt 的 text；工作区内容另行写入。"""
    priv = tmp / "private.index"
    env = _env(repo, tmp, priv)
    _git(repo, "read-tree", "HEAD", env=env, check=True)
    (repo / "a.txt").write_text(text, encoding="utf-8")
    _git(repo, "add", "a.txt", env=env, check=True)
    return priv


def _run_scope(repo, tmp, priv, *args):
    return subprocess.run(
        [sys.executable, str(SCOPE), *args, "--declared-index", str(priv)],
        cwd=str(repo),
        env=_env(repo, tmp, priv),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_pathspec_commit_carrying_worktree_line_is_red(tmp_path):
    repo = _make_repo(tmp_path)
    priv = _prime_private_index(repo, tmp_path, "mine\n")
    # 工作区多出一行（别线在途改动）——pathspec 提交会把它一起带走
    (repo / "a.txt").write_text("mine\ntheir\n", encoding="utf-8")
    _git(
        repo,
        "commit",
        "-m",
        "partial",
        "--",
        "a.txt",
        env=_commit_env(repo, tmp_path),
        check=True,
    )

    proc = _run_scope(repo, tmp_path, priv, "--post-commit", "HEAD")

    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert "FAIL" in proc.stdout
    assert "a.txt" in proc.stdout
    assert "HEAD" in proc.stdout and "INDEX" in proc.stdout
    assert "形态⑩" in proc.stdout
    head_blob = _git(repo, "rev-parse", "HEAD:a.txt").stdout.strip()
    index_blob = _git(
        repo, "rev-parse", ":a.txt", env=_env(repo, tmp_path, priv)
    ).stdout.strip()
    assert head_blob != index_blob
    assert head_blob in proc.stdout and index_blob in proc.stdout


def test_clean_commit_matches_private_index(tmp_path):
    repo = _make_repo(tmp_path)
    priv = _prime_private_index(repo, tmp_path, "mine\n")
    _git(
        repo,
        "commit",
        "-m",
        "clean",
        "--",
        "a.txt",
        env=_commit_env(repo, tmp_path),
        check=True,
    )

    proc = _run_scope(repo, tmp_path, priv, "--post-commit", "HEAD")

    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "全部 path 与私有 index 一致" in proc.stdout


def test_no_declared_index_is_explicit_skip(tmp_path):
    repo = _make_repo(tmp_path)
    priv = _prime_private_index(repo, tmp_path, "mine\n")
    env = _env(repo, tmp_path, priv)
    env.pop("GIT_INDEX_FILE", None)
    env.pop("MBDSDR_DECLARED_INDEX", None)
    proc = subprocess.run(
        [sys.executable, str(SCOPE), "--post-commit", "HEAD"],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0
    assert "SKIP" in proc.stdout


def test_git_safe_rejects_pathspec_in_private_index_mode(tmp_path):
    repo = _make_repo(tmp_path)
    priv = _prime_private_index(repo, tmp_path, "mine\n")
    env = _env(repo, tmp_path, priv)

    blocked = subprocess.run(
        [sys.executable, str(SAFE), "commit", "-m", "x", "--", "a.txt"],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert blocked.returncode == 3, (blocked.stdout, blocked.stderr)
    assert "pathspec" in blocked.stdout
    assert "unstage" in blocked.stdout

    allowed = subprocess.run(
        [sys.executable, str(SAFE), "commit", "-m", "ok"],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert allowed.returncode == 0, (allowed.stdout, allowed.stderr)
