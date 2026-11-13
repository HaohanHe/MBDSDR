"""§6.1 登记行不许掉队：闸看暂存区，safe_commit 连工作区一起看。

``ac232bf5`` 实证：人用 ``append_coordination_row.py`` 把登记行追加到工作区，
但这行没进暂存区，闸看见的暂存区是干净的。三种形态必须都成立：

* 工作区没有行 → 拒绝，且打印的命令粘贴执行后能通过；
* 工作区有行、暂存区没有 → 只带这一行进本笔，提交后工作区干净；
* 同时有别人写的另一行 → 别人的那行必须留在工作区，不进本笔。

工具**不生成**登记行内容：测试里那一行是用 ``append_coordination_row.py``
写的（与人手同一个工具），不是测试替工具签名。
"""
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAFE = ROOT / "scripts" / "safe_commit.py"
CLAIM = ROOT / "scripts" / "file_claim.py"
APPEND = ROOT / "scripts" / "append_coordination_row.py"
CODE = "scripts/foo.py"
DOC = "docs/COORDINATION.md"
OWNER = "我的线/abc123"
FOREIGN = "别的线/def456"

DOC_TEXT = """# 协调

### 6.1 当前占用

| 文件 | 线名 | 开始时间 | 预计提交 hash |
| --- | --- | --- | --- |
| `desktop/main_window.py` | 老线/000000（历史） | 2026-11-13 01:48 | 已提交 |

## 7 其他
"""


def _git(repo, *args, check=False):
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=check,
    )


def _repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "docs").mkdir(parents=True)
    _git(repo, "init", "-q", check=True)
    _git(repo, "config", "user.email", "safe-commit@example.invalid", check=True)
    _git(repo, "config", "user.name", "safe-commit", check=True)
    _git(repo, "config", "commit.gpgsign", "false", check=True)
    (repo / CODE).write_text("def foo():\n    return 1\n", encoding="utf-8")
    (repo / DOC).write_text(DOC_TEXT, encoding="utf-8", newline="")
    # 「补登记」命令打印的是 `python scripts/append_coordination_row.py ...`：
    # 把这几个脚本放进临时树，命令才能原样粘贴执行（不靠测试改写命令）。
    for name in ("append_coordination_row.py", "file_claim.py", "claim_window.py"):
        shutil.copy2(ROOT / "scripts" / name, repo / "scripts" / name)
    (repo / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    _git(repo, "add", CODE, DOC, ".gitignore", "scripts/append_coordination_row.py",
         "scripts/file_claim.py", "scripts/claim_window.py", check=True)
    _git(repo, "commit", "-q", "-m", "base", check=True)
    return repo


def _env(repo, locks, owner=""):
    env = os.environ.copy()
    env.pop("GIT_INDEX_FILE", None)
    env["MBDSDR_REPO_ROOT"] = str(repo)
    env["MBDSDR_CLAIM_ROOT"] = str(repo)
    env["MBDSDR_LOCKS_DIR"] = str(locks)
    env["MBDSDR_HOOK_MARKER_DIR"] = str(locks / "hook_ok")
    env["MBDSDR_SESSION_ID"] = "sessionA"
    if owner:
        env["MBDSDR_CLAIM_OWNER"] = owner
    return env


def _claim(repo, locks, owner):
    return subprocess.run(
        [sys.executable, str(CLAIM), "claim", CODE, "--owner", owner],
        cwd=str(repo), env=_env(repo, locks), capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )


def _safe(repo, locks, tmp_path, message="test: 改动\n"):
    msg = tmp_path / "msg.txt"
    msg.write_text(message, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SAFE), "--files", CODE, "--message-file", str(msg)],
        cwd=str(repo), env=_env(repo, locks, OWNER),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def _head(repo):
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _printable_command(out):
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("python scripts/append_coordination_row.py"):
            return line
    return ""


def _run_command(repo, locks, command):
    argv = shlex.split(command)
    argv[0] = sys.executable
    return subprocess.run(
        argv, cwd=str(repo), env=_env(repo, locks), capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )


def _add_foreign_row(repo):
    """模拟另一条线往同一文档写了自己的登记行（不经过任何校验）。"""
    text = (repo / DOC).read_text(encoding="utf-8")
    row = f"| `desktop/other.py` | {FOREIGN}（另一条线的登记） | 2026-11-13 20:10 | 本笔 |\n"
    marker = "| --- | --- | --- | --- |\n"
    text = text.replace(marker, marker + row, 1)
    (repo / DOC).write_text(text, encoding="utf-8", newline="")


def test_worktree_row_is_rejected_then_brought_along(tmp_path):
    """工作区没有行 → 拒绝并给可执行命令；粘贴执行后同一条命令就能通过。"""
    locks = tmp_path / "locks"
    repo = _repo(tmp_path)
    assert _claim(repo, locks, OWNER).returncode == 0
    (repo / CODE).write_text("def foo():\n    return 2\n", encoding="utf-8")
    before = _head(repo)

    proc = _safe(repo, locks, tmp_path)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 5, out
    assert "§6.1 还没有登记行" in out, out
    assert _head(repo) == before, "缺行时不许提交"

    command = _printable_command(out)
    assert command, out
    assert "--root" in command, command
    append = _run_command(repo, locks, command)
    assert append.returncode == 0, append.stdout + append.stderr
    assert CODE in (repo / DOC).read_text(encoding="utf-8")

    proc = _safe(repo, locks, tmp_path)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "只带这 1 行进本笔" in out, out
    committed = _git(repo, "show", f"HEAD:{DOC}").stdout
    assert f"`{CODE}`" in committed and OWNER in committed, committed
    # 私有 index 提交后共享 index 仍是旧内容（形态⑤，用 git_sync_index.py 同步），
    # 但工作区必须与 HEAD 一致：不许留悬挂改动等下一笔顺手带走。
    assert _git(repo, "diff", "HEAD", "--name-only").stdout.strip() == "", \
        _git(repo, "diff", "HEAD").stdout


def test_only_own_row_is_taken_and_foreign_row_stays(tmp_path):
    """工作区里同时有别人的行：只带自己那一行，别人的留在工作区。"""
    locks = tmp_path / "locks"
    repo = _repo(tmp_path)
    assert _claim(repo, locks, OWNER).returncode == 0
    (repo / CODE).write_text("def foo():\n    return 2\n", encoding="utf-8")

    command = _printable_command(_safe(repo, locks, tmp_path).stdout)
    assert command
    assert _run_command(repo, locks, command).returncode == 0
    _add_foreign_row(repo)
    worktree = (repo / DOC).read_text(encoding="utf-8")
    assert FOREIGN in worktree

    proc = _safe(repo, locks, tmp_path)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "只带这 1 行进本笔" in out, out

    committed = _git(repo, "show", f"HEAD:{DOC}").stdout
    assert f"`{CODE}`" in committed and OWNER in committed, committed
    assert FOREIGN not in committed, "别人的登记行不许被顺手带走"
    assert FOREIGN in (repo / DOC).read_text(encoding="utf-8"), "别人的行必须留在工作区"
    # 以 HEAD 为基准看工作区：只剩「别人的那行」这一处未提交改动
    left = _git(repo, "diff", "HEAD", "--name-only").stdout.split()
    assert left == [DOC], left
    assert FOREIGN in _git(repo, "diff", "HEAD", "--", DOC).stdout


def test_row_already_staged_is_left_alone(tmp_path):
    """行已经在暂存区（整份文档随本笔带）：这里不该再重复插一行。"""
    locks = tmp_path / "locks"
    repo = _repo(tmp_path)
    assert _claim(repo, locks, OWNER).returncode == 0
    (repo / CODE).write_text("def foo():\n    return 2\n", encoding="utf-8")

    command = _printable_command(_safe(repo, locks, tmp_path).stdout)
    assert _run_command(repo, locks, command).returncode == 0
    rows_before = (repo / DOC).read_text(encoding="utf-8").count(f"`{CODE}`")

    msg = tmp_path / "msg.txt"
    msg.write_text("test: 文档一起带\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(SAFE), "--files", CODE, DOC,
         "--message-file", str(msg)],
        cwd=str(repo), env=_env(repo, locks, OWNER),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "[coord]" not in out, out
    committed = _git(repo, "show", f"HEAD:{DOC}").stdout
    assert committed.count(f"`{CODE}`") == rows_before, "不许重复插行"


def test_insert_rows_tolerates_prose_before_table():
    """§6.1 标题与表格之间允许说明文字（真实文档就是这样）。

    回归靶子：原实现只容忍空行，文档在标题下加一段说明后 safe_commit 直接
    报「下面没有表格」，逼人手工插行。工具不该把正常文档结构当成错误。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("safe_commit_probe", SAFE)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)

    text = (
        "# 协调\n\n### 6.1 当前占用\n\n"
        "改共享文件前先写一行，**提交后删行**。\n\n"
        "**追加登记行不必占整文件**：说明文字也可以有多段。\n\n"
        "| 文件 | 线名 | 开始时间 | 预计提交 hash |\n"
        "| --- | --- | --- | --- |\n"
        "| `desktop/main_window.py` | 老线/000000（历史） | 2026-11-13 01:48 | 已提交 |\n\n"
        "## 7 其他\n"
    )
    row = f"| `{CODE}` | {OWNER} | 2026-11-13 20:40 | 本笔 |"
    out = mod._insert_table_rows(text, [row])
    lines = out.splitlines()
    assert "**追加登记行不必占整文件**：说明文字也可以有多段。" in lines, "说明文字必须原样保留"
    assert row in lines
    assert lines.index(row) == lines.index(
        "| `desktop/main_window.py` | 老线/000000（历史） | 2026-11-13 01:48 | 已提交 |") + 1

