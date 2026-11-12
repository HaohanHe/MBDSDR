"""删除条目「意图声明」机械闸测试（2026-11-12）。

背景：`35db7a13` 用一份基于旧 HEAD 的私有 index 提交，旧 HEAD 之后入库的
守卫测试在新树里表现为删除（D），直接进库；当时 `check_commit_scope.py`
在「未声明清单」分支里只 WARN。这里把该分支改成 fail-closed：**任何未声明
为意图删除的删除条目一律拒绝**。

覆盖：
- 普通修改提交（无删除、无声明）仍然放行（防止顺手改坏）；
- 删除但未声明 → 拒绝，且拒绝信息里给出确切的过法（命令与变量名）；
- `--intent-delete` / `MBDSDR_INTENT_DELETIONS` 声明后放行；
- 声明不全 → 剩下那条仍然拒；
- `--expect` 清单里的删除条目视为已声明（向后兼容）；
- 反证（真实钩子）：旧树 + 私有 index 造出的幻影删除必须被 pre-commit 挡下，
  HEAD 不动；
- 正向（真实钩子）：真删 + 意图声明能提交成功；
- `safe_commit.py --files` 自动导出意图删除集，正常删文件不用手写声明。
"""
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCOPE = ROOT / "scripts" / "check_commit_scope.py"
SAFE_COMMIT = ROOT / "scripts" / "safe_commit.py"

_CLEAN_ENV = (
    "GIT_INDEX_FILE",
    "MBDSDR_DECLARED_INDEX",
    "MBDSDR_COMMIT_PATHS",
    "MBDSDR_INTENT_DELETIONS",
    "MBDSDR_EXCLUDE_HUNKS",
)


def _clean_environ():
    env = os.environ.copy()
    for key in _CLEAN_ENV:
        env.pop(key, None)
    return env


def _run(repo, tmp, *args, env_extra=None, check=False):
    env = _clean_environ()
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
        env=env if env is not None else _clean_environ(),
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
    return repo


def _hook_env(tmp_path, extra=None):
    env = _clean_environ()
    env["MBDSDR_LOCKS_DIR"] = str(tmp_path / "locks")
    env["MBDSDR_HOOK_MARKER_DIR"] = str(tmp_path / "hook_ok")
    if extra:
        env.update(extra)
    return env


def test_plain_modification_without_declaration_allowed(tmp_path):
    """回归：无删除、无声明清单的普通修改提交必须仍然放行。"""
    repo = _make_repo(tmp_path)
    (repo / "a.txt").write_text("a2\n", encoding="utf-8")
    _git(repo, "add", "a.txt", check=True)

    proc = _run(repo, tmp_path, "--staged")

    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, (proc.returncode, out)
    assert "DELETE" not in out
    assert "拒绝" not in out


def test_delete_without_intent_rejected_with_actionable_hint(tmp_path):
    """真删但未声明 → 拒绝，并给出确切过法（命令 + 变量名）。"""
    repo = _make_repo(tmp_path)
    _git(repo, "rm", "-q", "a.txt", check=True)

    proc = _run(repo, tmp_path, "--staged")

    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, (proc.returncode, out)
    assert "DELETE  a.txt" in out
    assert "意图删除" in out
    assert "--intent-delete" in out
    assert "MBDSDR_INTENT_DELETIONS" in out
    assert "safe_commit.py" in out


def test_delete_with_cli_intent_allowed(tmp_path):
    repo = _make_repo(tmp_path)
    _git(repo, "rm", "-q", "a.txt", check=True)

    proc = _run(repo, tmp_path, "--staged", "--intent-delete", "a.txt")

    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, (proc.returncode, out)
    assert "DELETE" not in out


def test_delete_with_env_intent_allowed(tmp_path):
    repo = _make_repo(tmp_path)
    _git(repo, "rm", "-q", "a.txt", check=True)

    proc = _run(
        repo, tmp_path, "--staged",
        env_extra={"MBDSDR_INTENT_DELETIONS": "a.txt"},
    )

    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, (proc.returncode, out)


def test_partial_intent_still_rejects_remaining(tmp_path):
    """只声明了一个删除 → 另一个未声明的仍必须拒。"""
    repo = _make_repo(tmp_path)
    _git(repo, "rm", "-q", "a.txt", "b.txt", check=True)

    proc = _run(repo, tmp_path, "--staged", "--intent-delete", "a.txt")

    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, (proc.returncode, out)
    assert "DELETE  b.txt" in out
    assert "DELETE  a.txt" not in out


def test_declared_manifest_covers_delete(tmp_path):
    """向后兼容：`--expect` 清单里的删除条目视为已声明。"""
    repo = _make_repo(tmp_path)
    _git(repo, "rm", "-q", "a.txt", check=True)

    proc = _run(repo, tmp_path, "--staged", "--expect", "a.txt")

    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, (proc.returncode, out)


def _installer():
    """加载真实的安装脚本，钩子依赖清单只有它一处定义。"""
    spec = importlib.util.spec_from_file_location(
        "_install_git_hooks_for_test", ROOT / "scripts" / "install_git_hooks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _install_hooks(repo):
    """按 install_git_hooks 的同一份清单给临时仓装钩子。

    以前这里手抄要装的文件，漏了 claim_gate.py / file_claim.py，钩子
    找不到依赖就按 fail-closed 拒掉一切提交。现在钩子清单与支持文件
    清单都从 install_git_hooks 导入，补依赖不会再漏下一个。
    """
    installer = _installer()
    scripts = repo / "scripts"
    scripts.mkdir(exist_ok=True)
    shutil.copy2(SCOPE, scripts / "check_commit_scope.py")
    hooks = repo / ".git" / "hooks"
    for name in installer.HOOK_NAMES:
        dst = hooks / name
        shutil.copy2(installer.HOOKS_SRC / name, dst)
        os.chmod(dst, 0o755)
    for name in installer.SUPPORT_FILES:
        shutil.copy2(installer.ROOT / "scripts" / name, hooks / name)


def test_hook_blocks_phantom_delete_from_stale_index(tmp_path):
    """反证：旧树 + 私有 index 造出的幻影删除，pre-commit 必须挡下。

    复现 `35db7a13`：私有 index 基于旧 HEAD `read-tree`，旧 HEAD 之后入库的
    文件（这里是 c.txt / d.txt）在新树里表现为删除；未声明意图删除 → 拒绝，
    HEAD 不得前进。
    """
    repo = _make_repo(tmp_path)
    old = _git(repo, "rev-parse", "HEAD").stdout.strip()
    for name in ("c.txt", "d.txt"):
        (repo / name).write_text(f"{name}\n", encoding="utf-8")
    _git(repo, "add", "c.txt", "d.txt", check=True)
    _git(repo, "commit", "-q", "-m", "later commit", check=True)

    private = tmp_path / "idx_幻影_20261112_2200"
    stale_env = _clean_environ()
    stale_env["GIT_INDEX_FILE"] = str(private)
    _git(repo, "read-tree", old, env=stale_env, check=True)
    (repo / "a.txt").write_text("a2\n", encoding="utf-8")
    _git(repo, "add", "a.txt", env=stale_env, check=True)

    _install_hooks(repo)
    before = _git(repo, "rev-parse", "HEAD").stdout.strip()

    proc = _git(
        repo, "commit", "-q", "-m", "phantom delete",
        env=_hook_env(tmp_path, {"GIT_INDEX_FILE": str(private)}),
    )

    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, (proc.returncode, out)
    assert "DELETE" in out
    assert "c.txt" in out and "d.txt" in out
    assert "意图删除" in out
    after = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert after == before, "幻影删除被拒时 HEAD 不能前进"


def test_hook_allows_declared_intent_delete(tmp_path):
    """正向：真删 + `MBDSDR_INTENT_DELETIONS` → 提交成功，文件确实消失。"""
    repo = _make_repo(tmp_path)
    _install_hooks(repo)
    _git(repo, "rm", "-q", "a.txt", check=True)
    before = _git(repo, "rev-parse", "HEAD").stdout.strip()

    proc = _git(
        repo, "commit", "-q", "-m", "delete a.txt",
        env=_hook_env(tmp_path, {"MBDSDR_INTENT_DELETIONS": "a.txt"}),
    )

    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, (proc.returncode, out)
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() != before
    assert _git(repo, "cat-file", "-e", "HEAD:a.txt").returncode != 0


def test_safe_commit_auto_exports_intent_delete(tmp_path):
    """`safe_commit.py --files` 自动把「HEAD 有、新索引没有」的路径导出为意图删除集。"""
    repo = _make_repo(tmp_path)
    _install_hooks(repo)
    (repo / "a.txt").unlink()
    message = tmp_path / "msg.txt"
    message.write_text("delete a.txt\n", encoding="utf-8")
    env = _hook_env(tmp_path)
    env["MBDSDR_REPO_ROOT"] = str(repo)

    proc = subprocess.run(
        [sys.executable, str(SAFE_COMMIT),
         "--files", "a.txt", "--message-file", str(message)],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, (proc.returncode, out)
    assert "[intent-delete]" in out
    assert _git(repo, "cat-file", "-e", "HEAD:a.txt").returncode != 0
