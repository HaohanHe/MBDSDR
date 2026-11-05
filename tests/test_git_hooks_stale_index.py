"""post-commit 回归测试：STALE_INDEX_SUSPECT + 无 marker 提交的三类归因。

场景构造：先改文件，再把「旧内容」放进索引，然后用普通 `git commit`
提交索引——提交吃进旧内容、工作区仍是新内容，post-commit 必须记一笔
STALE_INDEX_SUSPECT（只记录、不阻断）。

另钉住 bypass 归因：无 marker 的提交必须能分开
  explicit（带 `MBDSDR_BYPASS_REASON`）→ `git_hook_bypass.log`
  hook_error（无该变量 + 钩子侧故障证据）→ `git_hook_bypass.log`
  unattributed（无该变量也无证据，含 `--no-verify`）→ `unaudited_commits.log`
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK_SRC = ROOT / "scripts" / "hooks" / "post-commit"

#: 测试用 pre-commit 桩：只写「闸门已通过」标记（含 MANAGED 字样，
#: 让 post-commit 的故障证据判定认出这是本仓管理的钩子）。
STUB_PRE_COMMIT = '''#!/usr/bin/env python
"""MBDSDR-MANAGED-HOOK pre-commit（测试桩：只登记通过标记）"""
import os
import subprocess
from pathlib import Path

tree = subprocess.run(["git", "write-tree"], capture_output=True, text=True).stdout.strip()
d = Path(os.environ.get("MBDSDR_HOOK_MARKER_DIR") or r"D:\\mbdsdr\\.locks\\hook_ok")
d.mkdir(parents=True, exist_ok=True)
(d / f"{tree}.json").write_text("{}", encoding="utf-8")
'''


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
    env.pop("MBDSDR_BYPASS_REASON", None)
    env.pop("MBDSDR_COMMIT_PATHS", None)
    env["MBDSDR_HOOK_LOG"] = str(tmp_path / "hook.log")
    env["MBDSDR_UNAUDITED_LOG"] = str(tmp_path / "unaudited.log")
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
    pre = hook_dir / "pre-commit"
    pre.write_text(STUB_PRE_COMMIT, encoding="utf-8")
    os.chmod(pre, 0o755)
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


def _bypass_records(tmp_path):
    return [x for x in _records(tmp_path) if x.get("type") == "HOOK_BYPASS"]


def _unaudited_records(tmp_path):
    log = tmp_path / "unaudited.log"
    if not log.exists():
        return []
    return [
        json.loads(line)
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


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
    # 闸门确实跑过且 marker 命中（实测 `--only` 的 marker 树 = 提交树），
    # 因此既不得记 bypass，也不得落未审计日志（不把自检当成闸门）。
    assert _bypass_records(tmp_path) == []
    assert [x for x in _unaudited_records(tmp_path) if x.get("commit") == head] == []


def test_explicit_bypass_is_classified_with_reason(tmp_path, repo_env):
    """带 MBDSDR_BYPASS_REASON 的 --no-verify → bypass 日志 explicit，并记录声明差集。"""
    repo, env = repo_env
    (repo / "a.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, env, "add", "a.txt")
    reason = "事由：生成物被别线持有；文件：a.txt；预期 owner：设置架构线"
    env = dict(env, MBDSDR_BYPASS_REASON=reason,
               MBDSDR_COMMIT_PATHS="a.txt", MBDSDR_DECLARED_INDEX="<private-index>")
    _git(repo, env, "commit", "-q", "--no-verify", "-m", "explicit bypass")
    head = _git(repo, env, "rev-parse", "HEAD").stdout.strip()

    recs = [x for x in _bypass_records(tmp_path) if x.get("commit") == head]
    assert len(recs) == 1, _records(tmp_path)
    rec = recs[0]
    assert rec["bypass_kind"] == "explicit"
    assert rec["reason"] == reason
    assert rec["declared_files"] == ["a.txt"]
    assert rec["declared_files_mismatch"] == []
    assert rec["declared_index"] == "<private-index>"
    assert _unaudited_records(tmp_path) == []


def test_explicit_bypass_records_declared_mismatch(tmp_path, repo_env):
    """声明要提交的文件与实际提交不符 → 差集必须落盘，不许静默。"""
    repo, env = repo_env
    (repo / "a.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, env, "add", "a.txt")
    env = dict(env, MBDSDR_BYPASS_REASON="事由：测差集；文件：b.txt；预期 owner：无",
               MBDSDR_COMMIT_PATHS="b.txt")
    _git(repo, env, "commit", "-q", "--no-verify", "-m", "mismatch")
    head = _git(repo, env, "rev-parse", "HEAD").stdout.strip()

    rec = [x for x in _bypass_records(tmp_path) if x.get("commit") == head][0]
    assert rec["declared_files_mismatch"] == ["a.txt", "b.txt"], rec


def test_silent_bypass_goes_to_unaudited_log(tmp_path, repo_env):
    """无 reason 的 --no-verify → 不进 bypass 日志，进 unaudited 并标未审计。"""
    repo, env = repo_env
    (repo / "a.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, env, "add", "a.txt")
    _git(repo, env, "commit", "-q", "--no-verify", "-m", "silent")
    head = _git(repo, env, "rev-parse", "HEAD").stdout.strip()

    assert [x for x in _bypass_records(tmp_path) if x.get("commit") == head] == []
    recs = [x for x in _unaudited_records(tmp_path) if x.get("commit") == head]
    assert len(recs) == 1, _unaudited_records(tmp_path)
    rec = recs[0]
    assert rec["type"] == "UNAUDITED_COMMIT"
    assert rec["bypass_kind"] == "unattributed"
    assert rec["audited"] is False
    assert "MBDSDR_BYPASS_REASON" in rec["reason"]


def test_hook_error_is_classified_by_evidence(tmp_path, repo_env):
    """pre-commit 被删（钩子侧故障证据）→ bypass 日志 hook_error，不是 explicit。"""
    repo, env = repo_env
    (repo / ".git" / "hooks" / "pre-commit").unlink()
    (repo / "a.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, env, "add", "a.txt")
    _git(repo, env, "commit", "-q", "-m", "no pre-commit installed")
    head = _git(repo, env, "rev-parse", "HEAD").stdout.strip()

    recs = [x for x in _bypass_records(tmp_path) if x.get("commit") == head]
    assert len(recs) == 1, _records(tmp_path)
    rec = recs[0]
    assert rec["bypass_kind"] == "hook_error"
    assert any("不存在" in item for item in rec["hook_evidence"]), rec
    assert _unaudited_records(tmp_path) == []
