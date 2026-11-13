"""pre-commit 的 ``[index]`` 归属提示：只提示，不改退出码。

背景（2026-11-13 归属事故）：§6.1 行与提交说明都被当成过归属证据，但提交
时刻真正能看见的是 **index 里暂存了哪些路径**。这一步逐条打印每个暂存路径
在台账里的**当前** owner：他人占用中 → 点名；查不到 → 「无 claim 记录」；
本线自己占的 → 不打印。提示不参与退出码，闸门该拒还是拒。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "scripts" / "hooks" / "pre-commit"
STUB = "import sys\nsys.exit(0)\n"


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for name in ("a.py", "b.py", "c.py"):
        (repo / name).write_text("v0\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "hook-test@example.invalid")
    _git(repo, "config", "user.name", "hook-test")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "add", "a.py", "b.py", "c.py")
    _git(repo, "commit", "-q", "-m", "base")
    for name in ("a.py", "b.py", "c.py"):
        (repo / name).write_text("v1\n", encoding="utf-8")
        _git(repo, "add", name)
    return repo


def _env(tmp_path, owner="乙", scope_rc=0):
    """①~⑤ 全指向桩；只留下要验证的 [index] 提示。"""
    env = os.environ.copy()
    env.pop("GIT_INDEX_FILE", None)
    env.pop("MBDSDR_BYPASS_REASON", None)
    env["MBDSDR_LOCKS_DIR"] = str(tmp_path / "locks")
    env["MBDSDR_HOOK_MARKER_DIR"] = str(tmp_path / "hook_ok")
    env["MBDSDR_CLAIM_OWNER"] = owner
    stub = tmp_path / "stub.py"
    body = STUB if scope_rc == 0 else f"import sys\nsys.exit({scope_rc})\n"
    stub.write_text(body, encoding="utf-8")
    env["MBDSDR_SCOPE_SCRIPT"] = str(stub)
    env["MBDSDR_CLAIM_GATE"] = str(stub)
    env["MBDSDR_SECRET_SCAN"] = str(stub)
    return env


def _claims(tmp_path, a_owner="甲", b_owner="乙"):
    locks = tmp_path / "locks"
    locks.mkdir(exist_ok=True)
    (locks / "claims.json").write_text(
        json.dumps(
            {
                "claims": {
                    "a.py": {"path": "a.py", "current": {"owner": a_owner}},
                    "b.py": {"path": "b.py", "current": {"owner": b_owner}},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _run(repo, env):
    return subprocess.run(
        [sys.executable, str(HOOK)], cwd=str(repo), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def test_index_hint_names_foreign_and_missing_claims(tmp_path):
    repo = _repo(tmp_path)
    env = _env(tmp_path, owner="乙")
    _claims(tmp_path)

    proc = _run(repo, env)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "[index] 他人占用中：a.py（owner: 甲）" in out, out
    assert "b.py（owner: 乙）" not in out, "本线自己占着的路径不许点名"
    assert "[index] 无 claim 记录：c.py" in out, out


def test_hint_does_not_change_exit_code(tmp_path):
    repo = _repo(tmp_path)
    env = _env(tmp_path, owner="乙")

    _claims(tmp_path)
    hinted = _run(repo, env)
    (tmp_path / "locks" / "claims.json").unlink()
    plain = _run(repo, env)
    assert hinted.returncode == plain.returncode == 0
    assert "[index] 他人占用中" not in plain.stdout + plain.stderr

    # 闸门拒绝时也一样：提示只多几行字，退出码逐字相同。
    env_fail = _env(tmp_path, owner="乙", scope_rc=1)
    _claims(tmp_path)
    hinted_fail = _run(repo, env_fail)
    (tmp_path / "locks" / "claims.json").unlink()
    plain_fail = _run(repo, env_fail)
    assert hinted_fail.returncode == plain_fail.returncode == 1
