"""提交前「声明清单」机械闸测试。

规矩（§6.9.6）：本次提交的文件集合必须全部落在声明清单内，**多一个就拒**，
并把多出来的路径打出来。背景是私有 index 文件名撞车——两个会话用同一个
index 文件名，其中一个提交时把对方正在改的文件卷走。

覆盖：
- 清单外的暂存文件 → 拒绝（退出码 1），输出 EXTRA 与具体路径；
- 清单与暂存完全一致 → 放行；
- 未声明清单 → 不做「多一个就拒」的检查（保持旧行为，不假报）；
- 暂存 blob 与工作树不一致 → 拒绝（事故 `bc0c75e1` 的旧快照回退）；
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
    env.pop("MBDSDR_DECLARED_INDEX", None)
    env.pop("MBDSDR_COMMIT_PATHS", None)
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


def test_declared_private_index_but_shared_index_rejected(tmp_path):
    """反证：声明了私有 index，但 `$env:` 没落到子进程，闸门读到共享 index。

    事故 `16f075f3`：计划只提交 10 个文件，闸门读共享 index，一次夹带
    26 个别线文件。闸门必须拒绝，并把 DECLARED / ACTUAL 两个路径打出来。
    """
    repo = _make_repo(tmp_path)
    _git(repo, "add", "a.txt", "b.txt", check=True)
    private = tmp_path / "idx_机制线_20261106_0300"

    # 只声明，不给 GIT_INDEX_FILE —— 模拟 PowerShell 变量没落到子进程
    proc = _run(
        repo,
        tmp_path,
        "--staged",
        "--declared-index",
        str(private),
        env_extra={"MBDSDR_DECLARED_INDEX": str(private)},
    )

    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, (proc.returncode, out)
    assert "DECLARED" in out
    assert "ACTUAL" in out
    assert "idx_机制线_20261106_0300" in out
    assert ".git" in out and "index" in out
    assert "GIT_INDEX_FILE" in out


def test_declared_index_matching_actual_allowed(tmp_path):
    """声明与实际一致（GIT_INDEX_FILE 真落到子进程）→ 放行。"""
    repo = _make_repo(tmp_path)
    private = tmp_path / "idx_机制线_20261106_0301"
    env = os.environ.copy()
    env.pop("GIT_INDEX_FILE", None)
    env["GIT_INDEX_FILE"] = str(private)
    env["MBDSDR_REPO_ROOT"] = str(repo)
    _git(repo, "add", "a.txt", "b.txt", env=env, check=True)

    proc = _run(
        repo,
        tmp_path,
        "--staged",
        "--declared-index",
        str(private),
        env_extra={"GIT_INDEX_FILE": str(private)},
    )

    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, (proc.returncode, out)
    assert "INDEX 实际读取" in out
    assert ".git" not in out.split("INDEX 实际读取", 1)[1].splitlines()[0]


def test_index_path_always_printed(tmp_path):
    """未声明私有 index 时也要打印实际读到的 index 路径（便于事后取证）。"""
    repo = _make_repo(tmp_path)
    _git(repo, "add", "a.txt", "b.txt", check=True)

    proc = _run(repo, tmp_path, "--staged")

    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "INDEX 实际读取" in proc.stdout


def _stale_private_index(repo, tmp_path, name, old_commit):
    """按旧 HEAD 建一份私有 index（复现「陈旧私有 index」）。"""
    private = tmp_path / name
    env = os.environ.copy()
    env.pop("GIT_INDEX_FILE", None)
    env["GIT_INDEX_FILE"] = str(private)
    _git(repo, "read-tree", old_commit, env=env, check=True)
    return private, env


def test_staged_old_blob_new_worktree_rejected(tmp_path):
    """反证：暂存的是旧 blob、工作树是新内容 → 必须拒（事故 `bc0c75e1`）。

    事故形态：共享 index 里留着 4 份别人文档的旧快照，工作树已被重定位成新
    内容；提交按 index 走，把行号重定位整体回退。这条路径**没有活跃 claim**，
    所以只有「index 必须等于工作树」这一条能拦住。
    """
    repo = _make_repo(tmp_path)
    _git(repo, "add", "a.txt", check=True)  # index 里是 a2
    (repo / "a.txt").write_text("a3\n", encoding="utf-8")  # 工作树改成 a3，不 add

    proc = _run(repo, tmp_path, "--staged")

    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, (proc.returncode, out)
    assert "DIRTY" in out
    assert "a.txt" in out
    assert "bc0c75e1" in out


def test_worktree_reblobbed_into_index_allowed(tmp_path):
    """反修：把工作树的新内容重新 add 进 index → 一致，正常放行。"""
    repo = _make_repo(tmp_path)
    (repo / "a.txt").write_text("a3\n", encoding="utf-8")
    _git(repo, "add", "a.txt", check=True)

    proc = _run(repo, tmp_path, "--staged")

    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, (proc.returncode, out)
    assert "DIRTY" not in out


def test_stale_private_index_phantom_deletions_rejected(tmp_path):
    """反证：私有 index 基于旧 HEAD 建 → 之后入库的文件变成「删除」，闸门拒。

    事故形态八（文案线实测）：`read-tree` 时的 HEAD 还没包含机制线刚提交的
    3 个文件，那 3 个文件在它的索引里表现为删除并被计入暂存。
    """
    repo = _make_repo(tmp_path)
    old = _git(repo, "rev-parse", "HEAD").stdout.strip()
    for name in ("c.txt", "d.txt", "e.txt"):
        (repo / name).write_text(f"{name}\n", encoding="utf-8")
    _git(repo, "add", "c.txt", "d.txt", "e.txt", check=True)
    _git(repo, "commit", "-q", "-m", "later commit", check=True)

    private, env = _stale_private_index(repo, tmp_path, "idx_文案线_20261106_0220", old)
    (repo / "a.txt").write_text("a3\n", encoding="utf-8")
    _git(repo, "add", "a.txt", env=env, check=True)

    proc = _run(
        repo,
        tmp_path,
        "--staged",
        "--expect",
        "a.txt",
        env_extra={"GIT_INDEX_FILE": str(private)},
    )

    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, (proc.returncode, out)
    assert "DELETE" in out
    assert "幻影删除" in out
    for name in ("c.txt", "d.txt", "e.txt"):
        assert name in out
    assert "read-tree HEAD" in out


def test_stale_private_index_rebuilt_from_head_allowed(tmp_path):
    """反修：基于当前 HEAD 重建私有 index → 幻影删除消失，正常放行。"""
    repo = _make_repo(tmp_path)
    old = _git(repo, "rev-parse", "HEAD").stdout.strip()
    for name in ("c.txt", "d.txt", "e.txt"):
        (repo / name).write_text(f"{name}\n", encoding="utf-8")
    _git(repo, "add", "c.txt", "d.txt", "e.txt", check=True)
    _git(repo, "commit", "-q", "-m", "later commit", check=True)

    private, env = _stale_private_index(repo, tmp_path, "idx_文案线_20261106_0221", old)
    _git(repo, "read-tree", "HEAD", env=env, check=True)
    (repo / "a.txt").write_text("a3\n", encoding="utf-8")
    _git(repo, "add", "a.txt", env=env, check=True)

    proc = _run(
        repo,
        tmp_path,
        "--staged",
        "--expect",
        "a.txt",
        env_extra={"GIT_INDEX_FILE": str(private)},
    )

    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, (proc.returncode, out)
    assert "DELETE" not in out
    assert "幻影删除" not in out
