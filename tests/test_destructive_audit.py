"""破坏性操作事后审计：绕过包装的 reset 必须被当场报「未审计」。"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "scripts" / "check_destructive_ops.py"
SAFE = ROOT / "scripts" / "git_safe.py"


def _git(repo, *args, env=None, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = os.environ.copy()
    _git(repo, "init", "-q", env=env)
    _git(repo, "config", "user.email", "audit@example.invalid", env=env)
    _git(repo, "config", "user.name", "audit-test", env=env)
    _git(repo, "config", "commit.gpgsign", "false", env=env)
    (repo / "a.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "a.txt", env=env)
    _git(repo, "commit", "-q", "-m", "base", env=env)
    (repo / "a.txt").write_text("v2\n", encoding="utf-8")
    _git(repo, "add", "a.txt", env=env)
    _git(repo, "commit", "-q", "-m", "second", env=env)
    return repo, env


def _audit(repo, locks):
    env = os.environ.copy()
    env["MBDSDR_LOCKS_DIR"] = str(locks)
    return subprocess.run(
        [sys.executable, str(AUDIT), "--repo", str(repo)],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_bypass_reset_is_reported_unaudited(tmp_path):
    repo, env = _repo(tmp_path)
    locks = tmp_path / "locks"
    old_head = _git(repo, "rev-parse", "HEAD").stdout.strip()

    # 绕过包装直接 reset（没有 reason、没有日志）
    _git(repo, "reset", "-q", "--hard", "HEAD~1", env=env)

    proc = _audit(repo, locks)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, (proc.returncode, out)
    assert "UNAUDITED" in out
    assert old_head[:10] in out, "必须指出被移出的提交"


def test_wrapped_reset_is_audited(tmp_path):
    repo, env = _repo(tmp_path)
    locks = tmp_path / "locks"
    target = _git(repo, "rev-parse", "HEAD~1").stdout.strip()
    env = env.copy()
    env["MBDSDR_LOCKS_DIR"] = str(locks)
    env["MBDSDR_DESTRUCTIVE_REASON"] = "回滚误提交 second，预期 main 回到 base，不影响他人"
    env["MBDSDR_DESTRUCTIVE_DIGEST"] = target

    safe = subprocess.run(
        [sys.executable, str(SAFE), "reset", "--hard", target],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert safe.returncode == 0, (safe.returncode, safe.stdout, safe.stderr)

    proc = _audit(repo, locks)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, (proc.returncode, out)
    assert "UNAUDITED" not in out
    assert "未审计 0 笔" in out
