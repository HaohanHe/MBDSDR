"""声明前已在工作区的他线改动：看得见，且拦得住（退出码 8）。

⑰ 的时间窗从 ``--declare`` 那一刻起算，所以声明**之前**就躺在工作区的他线内容
会原样进快照，两处一致就通过——这是同一天出现六次的形态。判据：

* ``--declare`` 按台账枚举 ``--files`` 里不属于本笔的块，打印规模与清单；
* 提交时这些块仍会被带入 → 必须 ``--exclude-hunk`` / ``--exclude-owner`` 排掉，
  或 ``--allow-foreign-changes "<理由>"`` 留痕（进 ``foreign-before-declare`` 段）；
* 两者都没有 → 退出码 8，未提交。

反证：把 ``_attempt`` 里的 ``return 8`` 分支改成放行，
``test_foreign_block_blocks_commit`` 必须转红（退出码变 0）。
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAFE = ROOT / "scripts" / "safe_commit.py"
DOC = "docs/COORDINATION.md"
ME = "A线"
OTHER = "B线"


def _git(repo, *args, check=False):
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=check,
    )


def _repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    _git(repo, "init", "-q", check=True)
    _git(repo, "config", "user.email", "foreign@example.invalid", check=True)
    _git(repo, "config", "user.name", "foreign", check=True)
    _git(repo, "config", "commit.gpgsign", "false", check=True)
    (repo / DOC).write_text(
        "### 6.1 当前占用\n\n"
        "| 文件 | 线名 | 时间 | 本笔 |\n"
        "| --- | --- | --- | --- |\n"
        "| `scripts/base.py` | A线（基线） | 2026-11-14 08:00 | 是 |\n",
        encoding="utf-8",
    )
    (repo / "notes.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", DOC, "notes.txt", check=True)
    _git(repo, "commit", "-q", "-m", "base", check=True)
    return repo


def _env(repo, locks, owner=ME):
    env = os.environ.copy()
    for key in ("GIT_INDEX_FILE", "MBDSDR_CLAIM_OWNER", "MBDSDR_COORD_DOC"):
        env.pop(key, None)
    env["MBDSDR_REPO_ROOT"] = str(repo)
    env["MBDSDR_CLAIM_ROOT"] = str(repo)
    env["MBDSDR_CLAIM_OWNER"] = owner
    env["MBDSDR_LOCKS_DIR"] = str(locks)
    env["MBDSDR_SESSION_ID"] = "sessionA"
    return env


def _foreign_in_worktree(repo):
    """声明前就在工作区：他线一行 + 本笔一行（与 HEAD 相比都是新增）。"""
    text = (repo / DOC).read_text(encoding="utf-8")
    text += f"| `notes.txt` | {OTHER}（他线在途） | 2026-11-14 09:00 | 否 |\n"
    text += f"| `{DOC}` | {ME}（本笔） | 2026-11-14 09:05 | 是 |\n"
    (repo / DOC).write_text(text, encoding="utf-8")


def _msg(tmp_path):
    path = tmp_path / "msg.txt"
    path.write_text("test: 声明前他线改动分流\n", encoding="utf-8")
    return path


def _safe(repo, locks, *args, owner=ME):
    return subprocess.run(
        [sys.executable, str(SAFE), *args],
        cwd=str(repo), env=_env(repo, locks, owner), capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )


def test_declare_lists_foreign_blocks_and_scope(tmp_path):
    """声明时按规模列清单；只比 --files，不动全仓。"""
    repo = _repo(tmp_path)
    locks = tmp_path / "locks"
    _foreign_in_worktree(repo)
    (repo / "notes.txt").write_text("base\nunrelated\n", encoding="utf-8")

    proc = _safe(repo, locks, "--files", DOC, "--declare")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "1 个文件 / 1 个块 / 共 1 行" in out, out
    assert f"{DOC}:6-6" in out and OTHER in out, out
    # 不在 --files 里的脏文件不参与枚举
    assert "unrelated" not in out, out


def test_foreign_block_blocks_commit(tmp_path):
    """声明前他线块会被带入、又没排除没留痕 → 退出码 8，且点名该块。"""
    repo = _repo(tmp_path)
    locks = tmp_path / "locks"
    _foreign_in_worktree(repo)

    assert _safe(repo, locks, "--files", DOC, "--declare").returncode == 0
    proc = _safe(repo, locks, "--files", DOC, "--message-file", str(_msg(tmp_path)))
    out = proc.stdout + proc.stderr
    assert proc.returncode == 8, out
    assert "声明前已经在工作区的他线改动会被带入本笔" in out, out
    assert OTHER in out, out
    assert "1 个文件 / 1 个块 / 共 1 行" in out, out
    assert _git(repo, "rev-list", "--count", "HEAD").stdout.strip() == "1"


def test_allow_foreign_changes_passes_and_is_traceable(tmp_path):
    """显式 allow → 通过，理由与块清单写进 foreign-before-declare 段。"""
    repo = _repo(tmp_path)
    locks = tmp_path / "locks"
    _foreign_in_worktree(repo)

    assert _safe(repo, locks, "--files", DOC, "--declare").returncode == 0
    proc = _safe(
        repo, locks, "--files", DOC, "--message-file", str(_msg(tmp_path)),
        "--allow-foreign-changes", "B 线一行已当面核对，随本笔一起落库",
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert _git(repo, "rev-list", "--count", "HEAD").stdout.strip() == "2"
    body = _git(repo, "log", "-1", "--format=%B").stdout
    assert "foreign-before-declare: B 线一行已当面核对，随本笔一起落库" in body
    assert f"{DOC}:" in body and OTHER in body
    assert OTHER in _git(repo, "show", f"HEAD:{DOC}").stdout


def test_exclude_hunk_keeps_block_out(tmp_path):
    """排除该块 → 通过，且他线那行不进本笔。"""
    repo = _repo(tmp_path)
    locks = tmp_path / "locks"
    _foreign_in_worktree(repo)

    assert _safe(repo, locks, "--files", DOC, "--declare").returncode == 0
    staged_doc = _git(repo, "show", f"HEAD:{DOC}").stdout.splitlines()
    target = len(staged_doc) + 1          # 他线行紧跟 HEAD 最后一行
    proc = _safe(
        repo, locks, "--files", DOC, "--message-file", str(_msg(tmp_path)),
        "--exclude-hunk", f"{DOC}:{target}-{target}",
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    committed = _git(repo, "show", f"HEAD:{DOC}").stdout
    assert f"{ME}（本笔）" in committed
    assert f"{OTHER}（他线在途）" not in committed
