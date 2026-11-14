#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""归线凭据（``claim-line:``）：生成只复述本人当前 claim，审计分桶可核对。

判据（对应 2026-11-14 任务一）：

  * 有本人活跃 claim → 说明末尾出现 ``claim-line: <线名>``；
  * 无 claim / 别人的 claim / 身份未声明 → **不写**（不写本身是信号）；
  * 手写的 ``claim-line:`` 不被工具重写；
  * 审计脚本把「有 claim 但提交信息没这行」与「这行与台账不符」分开报；
  * **反证**：手写一个与台账不符的线名，必须被审计检出（这行与台账不符）。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
SAFE = SCRIPTS / "safe_commit.py"
AUDIT = SCRIPTS / "audit_claim_lines.py"

OWNER = "甲线/aaaa1111"
OTHER = "乙线/bbbb2222"


def _key(rel: str) -> str:
    return os.path.normcase(rel.replace("\\", "/")).replace("\\", "/").lstrip("./")


def _write_marker(locks: Path, rel: str, owner: str, *, ttl_min: float = 180.0) -> None:
    now = time.time()
    payload = {
        "path": rel,
        "key": _key(rel),
        "owner": owner,
        "acquired_at": now - 30.0,
        "heartbeat_at": now - 30.0,
        "ttl_min": ttl_min,
        "pid": os.getpid(),
    }
    directory = locks / "claims"
    directory.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha1(_key(rel).encode("utf-8")).hexdigest()[:16] + ".claim"
    (directory / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _env(repo: Path, locks: Path, owner: str = "") -> dict:
    env = os.environ.copy()
    env["MBDSDR_REPO_ROOT"] = str(repo)
    env["MBDSDR_CLAIM_ROOT"] = str(repo)
    env["MBDSDR_LOCKS_DIR"] = str(locks)
    env.pop("PYTHONPATH", None)
    if owner:
        env["MBDSDR_CLAIM_OWNER"] = owner
    else:
        env.pop("MBDSDR_CLAIM_OWNER", None)
    return env


def _trailer(repo: Path, locks: Path, owner: str, files: list[str],
             message: bytes = b"feat: probe\n") -> subprocess.CompletedProcess:
    """在子进程里导入 safe_commit，直接跑归线尾段生成（避免模块级常量串环境）。"""
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(SCRIPTS)!r})\n"
        "import safe_commit\n"
        f"out = safe_commit._claim_line_trailer({message!r}, {files!r})\n"
        "sys.stdout.write('---BEGIN---\\n' + out.decode('utf-8') + '---END---\\n')\n"
    )
    return subprocess.run(
        [sys.executable, "-c", code], cwd=str(repo), env=_env(repo, locks, owner),
        capture_output=True, text=True, encoding="utf-8", timeout=60)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    return repo


def test_own_active_claim_writes_line_name(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    locks = tmp_path / "locks"
    _write_marker(locks, "a.py", OWNER)

    proc = _trailer(repo, locks, OWNER, ["a.py"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "claim-line: 甲线" in proc.stdout, proc.stdout
    assert "写入本人当前持有的 claim 线名" in proc.stdout, proc.stdout


def test_no_claim_writes_nothing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    locks = tmp_path / "locks"

    proc = _trailer(repo, locks, OWNER, ["a.py"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "claim-line:" not in proc.stdout, proc.stdout
    assert "不写 claim-line" in proc.stdout, proc.stdout


def test_foreign_claim_writes_nothing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    locks = tmp_path / "locks"
    _write_marker(locks, "a.py", OTHER)

    proc = _trailer(repo, locks, OWNER, ["a.py"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "claim-line: 甲线" not in proc.stdout, proc.stdout
    assert "不写 claim-line" in proc.stdout, proc.stdout


def test_unknown_identity_writes_nothing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    locks = tmp_path / "locks"
    _write_marker(locks, "a.py", OWNER)

    proc = _trailer(repo, locks, "", ["a.py"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "claim-line:" not in proc.stdout, proc.stdout
    assert "身份未声明" in proc.stdout, proc.stdout


def test_handwritten_line_is_not_rewritten(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    locks = tmp_path / "locks"
    _write_marker(locks, "a.py", OWNER)

    proc = _trailer(repo, locks, OWNER, ["a.py"],
                    message=b"feat: probe\n\nclaim-line: \xe5\x81\x87\xe7\xba\xbf\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    body = proc.stdout.split("---BEGIN---", 1)[1].split("---END---", 1)[0]
    assert body.count("claim-line:") == 1, body
    assert "claim-line: 假线" in body, body
    assert "claim-line: 甲线" not in body, body
    assert "工具不重写" in proc.stdout, proc.stdout


# ---------------------------------------------------------------------------
# 审计分桶 + 反证
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True,
                          text=True, encoding="utf-8")


def _commit(repo: Path, message: str, at: float) -> str:
    env = os.environ.copy()
    stamp = f"@{int(at)} +0000"
    env["GIT_AUTHOR_DATE"] = stamp
    env["GIT_COMMITTER_DATE"] = stamp
    # 每笔都真的改 a.py：审计按 tree 改动路径找 claim，空提交没有可比对的路径。
    (repo / "a.py").write_text(f"x = {int(at)}\n", encoding="utf-8")
    _git(repo, "add", "a.py")
    proc = _git(repo, "commit", "-q", "-m", message, env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def test_audit_reports_three_buckets_and_counter_evidence(tmp_path: Path) -> None:
    """反证：手写 `claim-line: 假线` 必须落在「这行与台账不符」。"""
    repo = _repo(tmp_path)
    locks = tmp_path / "locks"
    now = time.time()
    start, end = now - 50.0, now + 1000.0

    # 基线提交在 claim 区间之外：不应该被算成「有 claim 没行」。
    _commit(repo, "chore: base", now - 300.0)
    # 1) 台账有 claim，说明里没行
    sha_no_line = _commit(repo, "feat: no line", now - 30.0)
    # 2) 反证：手写与台账不符的线名
    sha_bad = _commit(repo, "feat: wrong line\n\nclaim-line: 假线", now - 20.0)
    # 3) 与台账相符
    _commit(repo, "feat: right line\n\nclaim-line: 甲线", now - 10.0)

    (locks).mkdir(parents=True, exist_ok=True)
    (locks / "claims.json").write_text(json.dumps({
        "version": 1,
        "claims": {
            "a.py": {
                "path": "a.py",
                "current": None,
                "history": [{
                    "owner": OWNER,
                    "acquired_at": start,
                    "released_at": end,
                    "last_heartbeat_at": start,
                }],
            }
        },
    }, ensure_ascii=False), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(AUDIT)], cwd=repo, env=_env(repo, locks),
        capture_output=True, text=True, encoding="utf-8", timeout=120)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "有 claim 但提交信息没这行" in out and " 1 笔" in out, out
    assert "这行与台账不符" in out and " 1 笔" in out, out
    assert "行与台账相符" in out and " 1 笔" in out, out
    assert sha_no_line[:12] in out, out
    assert sha_bad[:12] in out, out
    assert "行名不在台账期望里：假线" in out, out
    assert "甲线" in out and "假线" in out, out
