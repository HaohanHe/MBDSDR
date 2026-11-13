"""``--exclude-hunk`` 的排除粒度从整 hunk 降到行。

现场（2026-11-13）：§6.1 里三行相邻新增，只有一行是自己的。整块排除只能把
自己的行挪到别处让 diff 断开——提交干净了，台账时间序被打乱。**为了让工具
能用而改数据，代价不能接受。**

所以这里钉三件事：

1. 三行相邻新增，只排别人那两行 → 提交里只有自己那一行，且**位置不动**；
2. 整 hunk 声明的老入口照旧可用；
3. 排除的每一行仍必须在 index blob 里原地逐字存在：漂移就拒（粒度变细
   不等于校验放松）。
"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAFE = ROOT / "scripts" / "safe_commit.py"
DOC = "doc.md"
MINE = "| `mine.py` | 我的线/abc123（自己那行） | 2026-11-13 20:17 | 本笔 |"
OTHER_A = "| `a.md` | 文档引用复核线/6ae85f56（别人的一行） | 2026-11-13 20:20 | 本笔 |"
OTHER_B = "| `b.md` | 文档引用复核线/6ae85f56（别人的一行） | 2026-11-13 20:20 | 本笔 |"

BASE = [
    "# 协调",
    "",
    "### 6.1 当前占用",
    "",
    "| 文件 | 线名 | 开始时间 | 预计提交 hash |",
    "| --- | --- | --- | --- |",
    "| `old.py` | 老线/000000（历史） | 2026-11-13 01:48 | 已提交 |",
    "",
    "## 7 其他",
]


def _git(repo, *args, check=False):
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=check,
    )


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", check=True)
    _git(repo, "config", "user.email", "safe-commit@example.invalid", check=True)
    _git(repo, "config", "user.name", "safe-commit", check=True)
    _git(repo, "config", "commit.gpgsign", "false", check=True)
    (repo / DOC).write_text("\n".join(BASE) + "\n", encoding="utf-8", newline="")
    _git(repo, "add", DOC, check=True)
    _git(repo, "commit", "-q", "-m", "base", check=True)
    return repo


def _env(repo, locks):
    env = os.environ.copy()
    env.pop("GIT_INDEX_FILE", None)
    env["MBDSDR_REPO_ROOT"] = str(repo)
    env["MBDSDR_LOCKS_DIR"] = str(locks)
    env["MBDSDR_HOOK_MARKER_DIR"] = str(locks / "hook_ok")
    env["MBDSDR_SESSION_ID"] = "sessionA"
    env["MBDSDR_CLAIM_OWNER"] = "我的线/abc123"
    return env


def _safe(repo, locks, tmp_path, *extra, message="test: 登记行\n"):
    msg = tmp_path / "msg.txt"
    msg.write_text(message, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SAFE), "--files", DOC,
         "--message-file", str(msg), *extra],
        cwd=str(repo), env=_env(repo, locks),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def _head_lines(repo):
    return _git(repo, "show", f"HEAD:{DOC}").stdout.splitlines()


def _add_three_adjacent_rows(repo):
    """三行相邻新增：自己那行在前，别人两行紧跟其后（同一个 -U0 hunk）。"""
    lines = list(BASE)
    lines[7:7] = [MINE, OTHER_A, OTHER_B]
    (repo / DOC).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
    return lines


def test_partial_hunk_exclusion_keeps_own_line_in_place(tmp_path):
    """只排别人那两行：提交里只有自己那一行，且仍在第 8 行（原位置）。"""
    locks = tmp_path / "locks"
    repo = _repo(tmp_path)
    _add_three_adjacent_rows(repo)

    # 自己那行在 1-based 第 8 行，别人两行是 9-10
    proc = _safe(repo, locks, tmp_path, "--exclude-hunk", f"{DOC}:9-10")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out

    committed = _head_lines(repo)
    assert committed[7] == MINE, committed[6:10]
    assert OTHER_A not in committed and OTHER_B not in committed, committed
    # 位置不动：原来第 11 行的「## 7 其他」被前移了吗？没有——只删了两行，
    # 自己那行的下标必须还是 7（第 8 行）。
    assert committed.index(MINE) == 7, committed
    assert committed[8] == "", committed[7:10]
    assert committed[9] == "## 7 其他", committed[7:11]

    # 工作区仍保留别人的两行，等他们自己提交
    worktree = (repo / DOC).read_text(encoding="utf-8")
    assert OTHER_A in worktree and OTHER_B in worktree
    assert _git(repo, "diff", "HEAD", "--name-only").stdout.split() == [DOC]

    log = _git(repo, "log", "-1", "--format=%B").stdout
    assert f"- {DOC}:9-10（行级）" in log, log


def test_full_hunk_exclusion_still_works(tmp_path):
    """老入口保留：范围完整覆盖 hunk 就是整块排除（我的一处改动 + 别人的一整块）。"""
    locks = tmp_path / "locks"
    repo = _repo(tmp_path)
    lines = list(BASE)
    lines[7:7] = [OTHER_A, OTHER_B]          # 别人的两行，第 8-9 行（一个 hunk）
    lines[-1] = "## 7 其他（改）"             # 我的改动，第 11 行（另一个 hunk）
    (repo / DOC).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")

    proc = _safe(repo, locks, tmp_path, "--exclude-hunk", f"{DOC}:8-9")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    committed = _head_lines(repo)
    assert OTHER_A not in committed and OTHER_B not in committed
    assert committed[-1] == "## 7 其他（改）", committed[-3:]
    log = _git(repo, "log", "-1", "--format=%B").stdout
    assert "（行级）" not in log, log


def test_excluded_line_must_match_blob_verbatim(tmp_path):
    """粒度变细不放宽校验：blob 里那几行对不上就拒，不许静默改写。"""
    locks = tmp_path / "locks"
    repo = _repo(tmp_path)
    _add_three_adjacent_rows(repo)

    spec = importlib.util.spec_from_file_location("safe_commit_line_range", SAFE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.ROOT = repo
    mod.LOCKS_DIR = locks
    env = _env(repo, locks)

    # 先在私有 index 里把文件装好（与 _attempt 里 git add 之后的状态一致）
    index = locks / "probe.index"
    index.parent.mkdir(parents=True, exist_ok=True)
    env["GIT_INDEX_FILE"] = str(index)
    subprocess.run(["git", "-C", str(repo), "read-tree", "HEAD"], env=env, check=True)
    subprocess.run(["git", "-C", str(repo), "add", "--", DOC], env=env, check=True)

    real_run = mod._run

    def fake_run(args, run_env, stdin=None):
        if args[:2] == ["show", f":{DOC}"]:
            proc = real_run(args, run_env, stdin)
            # 只改一行内容，模拟「声明的那行在 blob 里已经不是原文」
            return subprocess.CompletedProcess(
                proc.args, proc.returncode,
                proc.stdout.replace(OTHER_A.encode(), "| `x.md` | 被改过 |".encode()),
                proc.stderr)
        return real_run(args, run_env, stdin)

    mod._run = fake_run
    try:
        try:
            mod._revert_index_hunks(
                env, DOC, [{"path": DOC, "start": 9, "end": 10}])
        except ValueError as exc:
            message = str(exc)
        else:
            raise AssertionError("blob 与声明行对不上时必须拒绝")
    finally:
        mod._run = real_run

    assert "对不上" in message, message
    assert _git(repo, "log", "-1", "--format=%s").stdout.strip() == "base"


def test_line_range_spanning_two_hunks_is_rejected(tmp_path):
    """跨 hunk 的行范围属于「多撤」：拒绝，别猜。"""
    locks = tmp_path / "locks"
    repo = _repo(tmp_path)
    lines = list(BASE)
    lines[7:7] = [MINE]           # 第 8 行
    lines[9:9] = [OTHER_A]        # 第 10 行，中间隔一行原有内容 → 两个 hunk
    (repo / DOC).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
    # 让中间那行与 HEAD 不同，才会真的断开成两个 hunk
    text = (repo / DOC).read_text(encoding="utf-8").replace("## 7 其他", "## 7 其他（改）")
    (repo / DOC).write_text(text, encoding="utf-8", newline="")

    proc = _safe(repo, locks, tmp_path, "--exclude-hunk", f"{DOC}:8-10")
    out = proc.stdout + proc.stderr
    # 确定性拒绝走前置校验码 1（main 会按既有策略重试，耗尽后 2）
    assert proc.returncode in (1, 2), out
    assert "只相交一部分" in out or "未完整覆盖" in out or "没对上" in out, out
    assert _git(repo, "log", "-1", "--format=%s").stdout.strip() == "base"
