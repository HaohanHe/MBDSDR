"""git_safe.py 的拦截面回归：破坏性操作无 reason 必拒并留痕；带 reason 放行并记种类。"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "git_safe.py"


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


def _run(repo, locks, *args, reason=None, digest=None):
    env = os.environ.copy()
    env.pop("MBDSDR_DESTRUCTIVE_REASON", None)
    env.pop("MBDSDR_DESTRUCTIVE_DIGEST", None)
    env["MBDSDR_LOCKS_DIR"] = str(locks)
    if reason is not None:
        env["MBDSDR_DESTRUCTIVE_REASON"] = reason
    if digest is not None:
        env["MBDSDR_DESTRUCTIVE_DIGEST"] = digest
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _records(locks):
    log = locks / "destructive_ops.log"
    if not log.exists():
        return []
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]


def _make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = os.environ.copy()
    _git(repo, "init", "-q", env=env)
    _git(repo, "config", "user.email", "safe@example.invalid", env=env)
    _git(repo, "config", "user.name", "safe-test", env=env)
    _git(repo, "config", "commit.gpgsign", "false", env=env)
    (repo / "a.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "a.txt", env=env)
    _git(repo, "commit", "-q", "-m", "base", env=env)
    (repo / "a.txt").write_text("v2\n", encoding="utf-8")
    _git(repo, "add", "a.txt", env=env)
    _git(repo, "commit", "-q", "-m", "second", env=env)
    return repo, env


def test_reset_hard_rejected_and_logged(tmp_path):
    repo, _env = _make_repo(tmp_path)
    locks = tmp_path / "locks"
    before = _git(repo, "rev-parse", "HEAD").stdout.strip()

    proc = _run(repo, locks, "reset", "--hard", "HEAD~1")

    out = proc.stdout + proc.stderr
    assert proc.returncode == 3, (proc.returncode, out)
    assert "REJECT" in out
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == before, "被拒时 HEAD 不能移动"
    recs = _records(locks)
    assert len(recs) == 1
    assert recs[0]["allowed"] is False
    assert recs[0]["kind"] == "hard"
    assert recs[0]["op"] == "reset"


def test_reset_hard_allowed_with_reason_logs_kind_and_hashes(tmp_path):
    repo, _env = _make_repo(tmp_path)
    locks = tmp_path / "locks"
    before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    target = _git(repo, "rev-parse", "HEAD~1").stdout.strip()

    proc = _run(
        repo,
        locks,
        "reset",
        "--hard",
        target,
        reason="回滚误提交 second，预期 main 回到 base，不影响他人",
        digest=before,
    )

    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, (proc.returncode, out)
    assert "ALLOW" in out
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == target
    rec = _records(locks)[0]
    assert rec["allowed"] is True
    assert rec["kind"] == "hard"
    assert rec["head_before"] == before
    assert rec["head_after"] == target
    assert rec["digest"] == before


def test_pathspec_reset_is_not_destructive(tmp_path):
    repo, _env = _make_repo(tmp_path)
    locks = tmp_path / "locks"
    (repo / "a.txt").write_text("v3\n", encoding="utf-8")
    _git(repo, "add", "a.txt")

    proc = _run(repo, locks, "reset", "--", "a.txt")

    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, (proc.returncode, out)
    assert not (locks / "destructive_ops.log").exists()


def _branch(repo):
    return _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def test_push_force_rejected_and_logged(tmp_path):
    repo, _env = _make_repo(tmp_path)
    locks = tmp_path / "locks"
    bare = tmp_path / "bare.git"
    _git(tmp_path, "init", "-q", "--bare", str(bare))
    _git(repo, "remote", "add", "origin", str(bare))
    branch = _branch(repo)
    _git(repo, "push", "-q", "-u", "origin", branch)

    proc = _run(repo, locks, "push", "--force", "origin", branch)

    out = proc.stdout + proc.stderr
    assert proc.returncode == 3, (proc.returncode, out)
    assert "REJECT" in out
    recs = _records(locks)
    assert recs and recs[0]["kind"] == "force"
    assert recs[0]["allowed"] is False


def test_push_force_with_reason_allowed(tmp_path):
    repo, _env = _make_repo(tmp_path)
    locks = tmp_path / "locks"
    bare = tmp_path / "bare.git"
    _git(tmp_path, "init", "-q", "--bare", str(bare))
    _git(repo, "remote", "add", "origin", str(bare))
    branch = _branch(repo)
    _git(repo, "push", "-q", "-u", "origin", branch)
    _git(repo, "reset", "-q", "--hard", "HEAD~1")

    proc = _run(
        repo,
        locks,
        "push",
        "--force",
        "origin",
        branch,
        reason="强制同步测试分支，影响仅本地 bare 远端，预期覆盖远端 main",
        digest="HEAD",
    )

    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, (proc.returncode, out)
    assert "ALLOW" in out
