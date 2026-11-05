"""post-commit 提交后自检（STALE_INDEX_SUSPECT）回归测试。

场景构造：先改文件，再把「旧内容」放进索引，然后用普通 `git commit`
提交索引——提交吃进旧内容、工作区仍是新内容，post-commit 必须记一笔
STALE_INDEX_SUSPECT（只记录、不阻断）；正常的 `--only` / `git add` 提交
工作区与 HEAD 一致，不得误报。
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK_SRC = ROOT / "scripts" / "hooks" / "post-commit"


def _git(repo, env, *args, check=True):
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


@pytest.fixture
def repo_env(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = os.environ.copy()
    env["MBDSDR_HOOK_LOG"] = str(tmp_path / "hook.log")
    env["MBDSDR_HOOK_MARKER_DIR"] = str(tmp_path / "hook_ok")
    env["MBDSDR_LOCKS_DIR"] = str(tmp_path / "locks")
    _git(repo, env, "init", "-q")
    _git(repo, env, "config", "user.email", "hook-test@example.invalid")
    _git(repo, env, "config", "user.name", "hook-test")
    _git(repo, env, "config", "commit.gpgsign", "false")
    hook_dir = repo / ".git" / "hooks"
    hook_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HOOK_SRC, hook_dir / "post-commit")
    os.chmod(hook_dir / "post-commit", 0o755)
    return repo, env


def _records(tmp_path):
    log = tmp_path / "hook.log"
    if not log.exists():
        return []
    return [
        json.loads(line)
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _stale_records(tmp_path):
    return [x for x in _records(tmp_path) if x.get("type") == "STALE_INDEX_SUSPECT"]


def test_stale_index_suspect_recorded(tmp_path, repo_env):
    repo, env = repo_env
    (repo / "a.txt").write_text("v0\n", encoding="utf-8")
    _git(repo, env, "add", "a.txt")
    _git(repo, env, "commit", "-q", "-m", "base")

    # 索引停在 v1，工作区继续改成 v2：接下来的普通 commit 吃进旧索引
    (repo / "a.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, env, "add", "a.txt")
    (repo / "a.txt").write_text("v2 newer\n", encoding="utf-8")

    _git(repo, env, "commit", "-q", "-m", "commit stale index")
    head = _git(repo, env, "rev-parse", "HEAD").stdout.strip()

    recs = _stale_records(tmp_path)
    assert len(recs) == 1, recs
    rec = recs[0]
    assert rec["path"] == "a.txt"
    assert rec["commit"] == head
    assert rec["diff_lines"] > 0
    assert len(rec["summary"]) <= 10
    assert any("+v2 newer" in line for line in rec["summary"]), rec["summary"]
    # 提交没被阻断，工作区仍停在新内容
    assert (repo / "a.txt").read_text(encoding="utf-8") == "v2 newer\n"


def test_normal_only_commit_no_false_positive(tmp_path, repo_env):
    repo, env = repo_env
    (repo / "a.txt").write_text("v0\n", encoding="utf-8")
    _git(repo, env, "add", "a.txt")
    _git(repo, env, "commit", "-q", "-m", "base")

    (repo / "a.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, env, "commit", "-q", "--only", "-m", "only a", "--", "a.txt")
    head = _git(repo, env, "rev-parse", "HEAD").stdout.strip()

    assert _stale_records(tmp_path) == []
    status = _git(repo, env, "status", "--porcelain", "--", "a.txt").stdout.strip()
    assert status == ""
    # 无 marker 的提交仍照常留 bypass 记录（避免把自检当成闸门）
    bypass = [x for x in _records(tmp_path) if x.get("commit") == head and "type" not in x]
    assert len(bypass) == 1
