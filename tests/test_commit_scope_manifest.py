"""提交前「声明清单」机械闸测试。

规矩（§6.9.6）：本次提交的文件集合必须全部落在声明清单内，**多一个就拒**，
并把多出来的路径打出来。背景是私有 index 文件名撞车——两个会话用同一个
index 文件名，其中一个提交时把对方正在改的文件卷走。

覆盖：
- 清单外的暂存文件 → 拒绝（退出码 1），输出 EXTRA 与具体路径；
- 清单与暂存完全一致 → 放行；
- 未声明清单 → 不做「多一个就拒」的检查（保持旧行为，不假报）；
- 走 pre-commit 钩子的真实提交被拒，HEAD 不动。
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCOPE = ROOT / "scripts" / "check_commit_scope.py"
PRE_COMMIT = ROOT / "scripts" / "hooks" / "pre-commit"


def _run(repo, tmp, *args, env_extra=None, check=False):
    env = os.environ.copy()
    env.pop("GIT_INDEX_FILE", None)
    env["MBDSDR_REPO_ROOT"] = str(repo)
    env["MBDSDR_LOCKS_DIR"] = str(tmp / "locks")
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SCOPE), *args],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


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


def _make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", check=True)
    _git(repo, "config", "user.email", "scope-test@example.invalid", check=True)
    _git(repo, "config", "user.name", "scope-test", check=True)
    _git(repo, "config", "commit.gpgsign", "false", check=True)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    _git(repo, "add", "a.txt", "b.txt", check=True)
    _git(repo, "commit", "-q", "-m", "base", check=True)
    (repo / "a.txt").write_text("a2\n", encoding="utf-8")
    (repo / "b.txt").write_text("b2\n", encoding="utf-8")
    return repo


def test_extra_staged_file_rejected(tmp_path):
    repo = _make_repo(tmp_path)
    _git(repo, "add", "a.txt", "b.txt", check=True)

    proc = _run(repo, tmp_path, "--staged", "--expect", "a.txt")

    assert proc.returncode == 1, (proc.returncode, proc.stdout, proc.stderr)
    assert "EXTRA" in proc.stdout
    assert "b.txt" in proc.stdout
    assert "多一个就拒" in proc.stdout


def test_manifest_exact_match_allowed(tmp_path):
    repo = _make_repo(tmp_path)
    _git(repo, "add", "a.txt", "b.txt", check=True)

    proc = _run(repo, tmp_path, "--staged", "--expect", "a.txt", "b.txt")

    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "EXTRA" not in proc.stdout


def test_env_manifest_enforced(tmp_path):
    repo = _make_repo(tmp_path)
    _git(repo, "add", "a.txt", "b.txt", check=True)

    proc = _run(repo, tmp_path, "--staged", env_extra={"MBDSDR_COMMIT_PATHS": "a.txt"})

    assert proc.returncode == 1, (proc.returncode, proc.stdout, proc.stderr)
    assert "b.txt" in proc.stdout


def test_no_manifest_keeps_old_behavior(tmp_path):
    repo = _make_repo(tmp_path)
    _git(repo, "add", "a.txt", "b.txt", check=True)

    proc = _run(repo, tmp_path, "--staged", env_extra={"MBDSDR_COMMIT_PATHS": ""})

    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)


def test_pre_commit_blocks_extra_file(tmp_path):
    repo = _make_repo(tmp_path)
    scripts = repo / "scripts"
    scripts.mkdir()
    shutil.copy2(SCOPE, scripts / "check_commit_scope.py")
    hooks = repo / ".git" / "hooks"
    shutil.copy2(PRE_COMMIT, hooks / "pre-commit")
    os.chmod(hooks / "pre-commit", 0o755)

    _git(repo, "add", "a.txt", "b.txt", check=True)
    env = os.environ.copy()
    env.pop("GIT_INDEX_FILE", None)
    env["MBDSDR_COMMIT_PATHS"] = "a.txt"
    env["MBDSDR_LOCKS_DIR"] = str(tmp_path / "locks")
    env["MBDSDR_HOOK_MARKER_DIR"] = str(tmp_path / "hook_ok")
    before = _git(repo, "rev-parse", "HEAD").stdout.strip()

    proc = _git(repo, "commit", "-q", "-m", "should be blocked", env=env)

    output = proc.stdout + proc.stderr  # hook 的输出被 git 重定向到 stderr
    assert proc.returncode != 0, (proc.returncode, output)
    assert "b.txt" in output
    assert "EXTRA" in output
    after = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert after == before, "提交被拒时 HEAD 不能前进"
