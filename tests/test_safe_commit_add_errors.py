"""``git add`` 失败分流：永久错误只试一次（1），瞬时错误重试后归 2。

判据是**错误类型**，不是次数：路径不存在/权限不足是清单写错，重试三次也不会
变好；索引锁冲突/并发改名是别人一瞬间的动作，等一轮就没了。把两者混在一起，
「路径写错」会被报成「重试耗尽」，人会去查环境而不是改清单。

反证：把 ``_attempt`` 里的 ``if not _transient_add_error(...)`` 去掉（一律按
瞬时重试），``test_missing_path_returns_1_without_retry`` 转红（退出码变 2）。
"""
import importlib.util
import os
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAFE = ROOT / "scripts" / "safe_commit.py"


def _git(repo, *args, check=False):
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=check,
    )


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", check=True)
    _git(repo, "config", "user.email", "add-error@example.invalid", check=True)
    _git(repo, "config", "user.name", "add-error", check=True)
    _git(repo, "config", "commit.gpgsign", "false", check=True)
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "a.txt", check=True)
    _git(repo, "commit", "-q", "-m", "base", check=True)
    return repo


def _env(repo, locks):
    env = os.environ.copy()
    env.pop("GIT_INDEX_FILE", None)
    env.pop("MBDSDR_CLAIM_OWNER", None)
    env["MBDSDR_REPO_ROOT"] = str(repo)
    env["MBDSDR_CLAIM_ROOT"] = str(repo)
    env["MBDSDR_LOCKS_DIR"] = str(locks)
    env["MBDSDR_SESSION_ID"] = "sessionA"
    return env


def test_missing_path_returns_1_without_retry(tmp_path):
    """永久错误：不存在的路径 → 退出码 1，且只出现 ``[try 1]``。"""
    repo = _repo(tmp_path)
    locks = tmp_path / "locks"
    msg = tmp_path / "msg.txt"
    msg.write_text("test: 不该落库\n", encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(SAFE), "--files", "no/such/file.py",
         "--message-file", str(msg)],
        cwd=str(repo), env=_env(repo, locks), capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert "[try 1]" in out and "[try 2]" not in out, out
    assert "永久错误，不重试" in out, out
    assert _git(repo, "rev-list", "--count", "HEAD").stdout.strip() == "1"


def test_add_error_classification():
    """分流判据本身：锁冲突/并发改名 → 瞬时；路径不存在/权限 → 永久。"""
    spec = importlib.util.spec_from_file_location("safe_commit_probe", SAFE)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)

    assert mod._transient_add_error(
        "fatal: Unable to create '/x/index.lock': File exists.")
    assert mod._transient_add_error(
        "fatal: cannot lock ref 'HEAD': Unable to create '/x/HEAD.lock'")
    assert not mod._transient_add_error(
        "fatal: pathspec 'no/such/file.py' did not match any files")
    assert not mod._transient_add_error(
        "error: open('a.txt'): Permission denied")
    assert not mod._transient_add_error(
        "fatal: a.txt: 'a.txt' is outside repository")


def test_transient_add_error_retries_then_2(tmp_path, capsys):
    """瞬时错误：每轮都失败 → 重试到耗尽，退出码 2。"""
    repo = _repo(tmp_path)
    locks = tmp_path / "locks"
    msg = tmp_path / "msg.txt"
    msg.write_text("test: 不该落库\n", encoding="utf-8")

    env = _env(repo, locks)
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        name = f"safe_commit_probe_{uuid.uuid4().hex}"
        spec = importlib.util.spec_from_file_location(name, SAFE)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)

        real_run = mod._run

        def fake_run(args, env, stdin=None):
            if args[:1] == ["add"]:
                return subprocess.CompletedProcess(
                    ["git", "add"], 128, b"",
                    b"fatal: Unable to create '/tmp/x.index.lock': File exists.\n",
                )
            return real_run(args, env, stdin)

        mod._run = fake_run
        code = mod.main(["--files", "a.txt", "--message-file", str(msg)])
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    out = capsys.readouterr().out
    assert code == 2, out
    assert "[try 3]" in out, out
    assert "瞬时失败（可重试）" in out, out
    assert _git(repo, "rev-list", "--count", "HEAD").stdout.strip() == "1"
