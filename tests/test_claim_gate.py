"""claim 机械闸（`scripts/claim_gate.py` + pre-commit）的放行/拒绝语义。

重点：全部用例都走**普通 `git commit`**（不是 `safe_commit.py`）——用户明确要求
「直接把 git commit 也要拒」，所以闸门必须落在 pre-commit 钩子上，而不是某个
提交脚本里。

钉住的行为（fail-closed）：
  ① pid 已死**不能**解锁别人的未过期 claim（claim 是一次性 CLI，pid 已死是
    默认状态；旧 `orphan_takeover` 就是因为这条被滥用的）；
  ② 存活、未过期的他人 claim → 拒，并报出持有者；
  ③ 台账坏 JSON → 拒，不能放行；
  ④ claim 占位文件坏 JSON → 拒；
  ⑤ 同一 owner 提交自己持有的文件 → 放行（身份用 `MBDSDR_CLAIM_OWNER` 声明）；
  ⑥ takeover 没写理由 → 不认（拒）；写了理由 → 新 owner 放行；
  ⑦ `action=audit_replay` 的审计行不算 takeover（写审计不能给自己发凭证），
    未知 action 失败关闭。
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK_SRC = ROOT / "scripts" / "hooks" / "pre-commit"
GATE_SRC = ROOT / "scripts" / "claim_gate.py"

#: 范围检查的替身：本文件只测 claim 闸，跨线/范围判据由 check_commit_scope 自己覆盖。
SCOPE_STUB = '''#!/usr/bin/env python
import sys
sys.exit(0)
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


def _commit(repo, env):
    """普通 git commit（走 .git/hooks/pre-commit，即待测的闸门本体）。"""
    return _git(repo, env, "commit", "-q", "-m", "probe", check=False)


def _head(repo, env):
    return _git(repo, env, "rev-parse", "HEAD").stdout.strip()


def _key(rel: str) -> str:
    # 与 file_claim.canonical_path 同一口径：normcase 会引入 `\`，再换回 `/`。
    return os.path.normcase(rel.replace("\\", "/")).replace("\\", "/").lstrip("./")


def _write_claim(locks, rel, owner, *, ttl_min=180.0, pid=None, cmdline=None,
                 create_time=None, acquired_at=None):
    """直接写 claim 占位文件（与 file_claim.py 的落盘格式一致）。"""
    now = time.time()
    acquired = now - 60.0 if acquired_at is None else acquired_at
    key = _key(rel)
    payload = {
        "path": rel,
        "key": key,
        "owner": owner,
        "acquired_at": acquired,
        "heartbeat_at": acquired,
        "ttl_min": ttl_min,
        "pid": os.getpid() if pid is None else pid,
        "pid_create_time": create_time,
        "pid_cmdline": cmdline,
    }
    directory = locks / "claims"
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    (directory / f"{digest}.claim").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


@pytest.fixture
def repo_env(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    locks = tmp_path / "locks"
    scope = tmp_path / "scope.py"
    scope.write_text(SCOPE_STUB, encoding="utf-8")

    env = os.environ.copy()
    env.pop("MBDSDR_CLAIM_OWNER", None)
    env["MBDSDR_LOCKS_DIR"] = str(locks)
    env["MBDSDR_CLAIM_ROOT"] = str(repo)
    env["MBDSDR_HOOK_MARKER_DIR"] = str(tmp_path / "hook_ok")
    env["MBDSDR_SCOPE_SCRIPT"] = str(scope)
    env["MBDSDR_CLAIM_GATE"] = str(GATE_SRC)

    _git(repo, env, "init", "-q")
    _git(repo, env, "config", "user.email", "GateTest@example.invalid")
    _git(repo, env, "config", "user.name", "gate-test")
    _git(repo, env, "config", "commit.gpgsign", "false")
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HOOK_SRC, hooks / "pre-commit")
    os.chmod(hooks / "pre-commit", 0o755)
    return repo, env, locks


def _stage(repo, env, rel="a.txt", text="v1\n"):
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _git(repo, env, "add", rel)
    return path


def test_dead_pid_does_not_unlock_foreign_claim(repo_env):
    """① pid 已死 + 未过期 + 无在途 → 仍是别人的持有，必须拒。

    这就是 2026-11-13 01:39 的事故形态：claim 是一次性 CLI，pid 早退出，
    旧判据把「pid 已死」当可接管信号，导致正常持有中的文件被抢。
    """
    repo, env, locks = repo_env
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    _write_claim(
        locks, "a.txt", "别的线/holder",
        pid=dead.pid, cmdline="python scripts/file_claim.py claim a.txt --owner 别的线/holder",
    )
    _stage(repo, env)

    proc = _commit(repo, env)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "持有者：别的线/holder" in out, out
    assert "未过期" in out, out
    assert "pid" in out and "仅诊断" in out, out


def test_live_foreign_claim_is_rejected_with_owner(repo_env):
    """② 存活、未过期的他人 claim → 拒，并报出持有者与三条出路。"""
    repo, env, locks = repo_env
    _write_claim(locks, "a.txt", "A03静默降级线/deadbeef", pid=os.getpid())
    _stage(repo, env)

    proc = _commit(repo, env)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "持有者：A03静默降级线/deadbeef" in out, out
    assert "到期时间" in out and "三条出路" in out, out
    assert "release" in out and "takeover" in out, out
    assert _git(repo, env, "rev-parse", "--verify", "HEAD", check=False).returncode != 0


def test_broken_registry_json_is_rejected(repo_env):
    """③ 台账坏 JSON → 拒（不能当「没人持有」）。"""
    repo, env, locks = repo_env
    locks.mkdir(parents=True, exist_ok=True)
    (locks / "claims.json").write_text("{not json", encoding="utf-8")
    _stage(repo, env)

    proc = _commit(repo, env)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "台账" in out and "失败关闭" in out, out
    assert _git(repo, env, "rev-parse", "--verify", "HEAD", check=False).returncode != 0


def test_broken_claim_marker_is_rejected(repo_env):
    """④ claim 占位文件坏 JSON → 拒（格式不认识）。"""
    repo, env, locks = repo_env
    directory = locks / "claims"
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(_key("a.txt").encode("utf-8")).hexdigest()[:16]
    (directory / f"{digest}.claim").write_text("{oops", encoding="utf-8")
    _stage(repo, env)

    proc = _commit(repo, env)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "格式不认识" in out, out


def test_same_owner_claim_passes(repo_env):
    """⑤ 同一 owner 提交自己持有的文件 → 放行（身份必须原文一致）。"""
    repo, env, locks = repo_env
    _write_claim(locks, "a.txt", "测试基建线/self", pid=os.getpid())
    env = dict(env, MBDSDR_CLAIM_OWNER="测试基建线/self")
    _stage(repo, env)

    proc = _commit(repo, env)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "自己持有 1" in out, out


def test_claim_expired_passes(repo_env):
    """claim 已过 TTL → 放行（视同无 claim）。"""
    repo, env, locks = repo_env
    _write_claim(locks, "a.txt", "别的线/stale", ttl_min=10.0,
                 acquired_at=time.time() - 3600.0)
    _stage(repo, env)

    proc = _commit(repo, env)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "已过 TTL" in out, out


def test_takeover_without_reason_is_rejected(repo_env):
    """⑥ takeover 没写理由 → 不认（新 owner 也拒）；补上理由 → 放行。"""
    repo, env, locks = repo_env
    acquired = time.time() - 60.0
    owner = "B线/接管线"
    _write_claim(locks, "a.txt", owner, pid=os.getpid(), acquired_at=acquired)
    locks.mkdir(parents=True, exist_ok=True)
    takeover_log = locks / "claim_takeovers.log"

    def _record(reason):
        takeover_log.write_text(
            json.dumps(
                {
                    "action": "orphan_takeover",
                    "logged_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "epoch": acquired,
                    "path": "a.txt",
                    "old_owner": "A线/原持有",
                    "new_owner": owner,
                    "reason": reason,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    env = dict(env, MBDSDR_CLAIM_OWNER=owner)
    _stage(repo, env)
    _record("")
    proc = _commit(repo, env)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "没写理由的 takeover" in out or "不认" in out, out

    _record("原 owner pid 已退出，文件无在途改动，K16 断言已提交")
    proc = _commit(repo, env)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "自己持有 1" in out, out


def test_unclaimed_commit_passes(repo_env):
    """没有 claim 的普通提交不受影响（闸门只在台账有记录时介入）。"""
    repo, env, _locks = repo_env
    _stage(repo, env)
    proc = _commit(repo, env)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "通过" in out, out


def test_audit_replay_row_does_not_excuse_foreign_claim(repo_env):
    """审计行（action=audit_replay）不能当接管凭证：别人仍持有就仍要拒。

    审计回放要把「某笔接管被判不合规」写回 `claim_takeovers.log`，那行同样带
    path/new_owner；若闸门把它当 takeover，任何人写一行审计就能给自己的持有效力。
    """
    repo, env, locks = repo_env
    acquired = time.time() - 60.0
    _write_claim(locks, "a.txt", "A03静默降级线/other", pid=os.getpid(), acquired_at=acquired)
    locks.mkdir(parents=True, exist_ok=True)
    (locks / "claim_takeovers.log").write_text(
        json.dumps(
            {
                "action": "audit_replay",
                "logged_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "epoch": acquired,
                "path": "a.txt",
                "new_owner": "我这条线",
                "reason": "审计回放：此次接管不合规",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    env = dict(env, MBDSDR_CLAIM_OWNER="我这条线")
    _stage(repo, env)

    proc = _commit(repo, env)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "持有者：A03静默降级线/other" in out, out


def test_unknown_action_in_takeover_log_is_rejected(repo_env):
    """takeover 日志出现不认识的 action → 失败关闭，不能当「没人持有」。"""
    repo, env, locks = repo_env
    locks.mkdir(parents=True, exist_ok=True)
    (locks / "claim_takeovers.log").write_text(
        json.dumps(
            {"action": "weird_action", "path": "a.txt", "new_owner": "谁"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    _stage(repo, env)

    proc = _commit(repo, env)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "action=" in out and "失败关闭" in out, out


def test_real_file_claim_cli_is_seen(repo_env):
    """用真的 `file_claim.py claim` 落一条 claim，另一个 owner 提交必须被拒。

    这条是端到端校验：闸门算出的 key 必须与 `file_claim.py` 落盘时用的 key 一致
    （手写 marker 的用例两侧都用同一个 `_key`，会掩盖 key 口径不一致的 bug）。
    同时它复现了真实形态：`claim` 是短命 CLI，pid 早已退出——这属于「pid 存活
    判不了」，按 fail-closed 拒绝，而不是当孤儿放行。
    """
    repo, env, locks = repo_env
    _stage(repo, env)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "file_claim.py"),
         "claim", "a.txt", "--owner", "A03静默降级线/realline", "--ttl-min", "180"],
        cwd=str(repo), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (locks / "claims").is_dir()

    commit = _commit(repo, env)
    out = commit.stdout + commit.stderr
    assert commit.returncode != 0, out
    assert "A03静默降级线/realline" in out, out
    assert "未过期" in out, out
