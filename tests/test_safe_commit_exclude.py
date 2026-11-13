"""safe_commit --exclude-hunk：只从私有 index 撤掉声明 hunk，工作树不动。

背景（2026-11-12 裁定）：共享文件里同时有自己和他人的在途改动时，允许提交
自己那部分，但必须逐文件声明排除了哪些 hunk。这条测试验证声明真的生效：
提交里只有自己的改动，别人的 hunk 留在工作树，提交说明带 excluded-hunks 段。
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAFE = ROOT / "scripts" / "safe_commit.py"


def _git(repo, *args, check=False):
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=check,
    )


def _make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", check=True)
    _git(repo, "config", "user.email", "safe-commit@example.invalid", check=True)
    _git(repo, "config", "user.name", "safe-commit", check=True)
    _git(repo, "config", "commit.gpgsign", "false", check=True)
    lines = [f"line{i}" for i in range(1, 7)]
    (repo / "doc.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _git(repo, "add", "doc.md", check=True)
    _git(repo, "commit", "-q", "-m", "base", check=True)
    # 第 2 行是我的改动，第 5 行是别线的在途改动
    lines[1] = "line2 mine"
    lines[4] = "line5 other"
    (repo / "doc.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return repo


def test_exclude_hunk_keeps_other_change_out(tmp_path):
    repo = _make_repo(tmp_path)
    msg = tmp_path / "msg.txt"
    msg.write_text("test: exclude other hunk\n", encoding="utf-8")
    env = os.environ.copy()
    env.pop("GIT_INDEX_FILE", None)
    env["MBDSDR_REPO_ROOT"] = str(repo)

    proc = subprocess.run(
        [
            sys.executable, str(SAFE),
            "--files", "doc.md",
            "--message-file", str(msg),
            "--exclude-hunk", "doc.md:5-5",
            "--exclude-owner", "别线",
        ],
        cwd=str(repo), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out

    committed = _git(repo, "show", "HEAD:doc.md").stdout
    assert "line2 mine" in committed
    assert "line5 other" not in committed
    assert "line5\n" in committed

    assert "line5 other" in (repo / "doc.md").read_text(
        encoding="utf-8").splitlines(), "工作树不能被动"

    log = _git(repo, "log", "-1", "--format=%B").stdout
    assert "excluded-hunks" in log
    assert "doc.md:5-5" in log
    assert "别线" in log


def test_exclude_pure_deletion_hunk(tmp_path):
    """别人的 hunk 是纯删除（``@@ … +b,0``）也要能排除。

    纯删除没有新版行，声明按删除点 b 定位（``+b,0`` 的 b，new 行号语义）；
    排除后提交里应保留被删的那行，工作树仍保持删掉的状态。
    """
    repo = _make_repo(tmp_path)
    lines = [f"line{i}" for i in range(1, 7)]
    lines[1] = "line2 mine"
    del lines[4]                       # 别线：删掉第 5 行 → @@ … +4,0 @@
    (repo / "doc.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    msg = tmp_path / "msg.txt"
    msg.write_text("test: exclude pure deletion\n", encoding="utf-8")
    env = os.environ.copy()
    env.pop("GIT_INDEX_FILE", None)
    env["MBDSDR_REPO_ROOT"] = str(repo)

    proc = subprocess.run(
        [
            sys.executable, str(SAFE),
            "--files", "doc.md",
            "--message-file", str(msg),
            "--exclude-hunk", "doc.md:4-4",
            "--exclude-owner", "别线",
        ],
        cwd=str(repo), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out

    committed = _git(repo, "show", "HEAD:doc.md").stdout
    assert "line2 mine" in committed
    assert "line5\n" in committed, "别人的删除不能被带走"
    tree = (repo / "doc.md").read_text(encoding="utf-8")
    # 这是临时仓库里的运行期文件，不是源码：按行成员断言，不做整份文本子串匹配。
    assert "line5" not in set(tree.splitlines()), "工作树不能被动"
    assert "line6" in set(tree.splitlines())


def test_exclude_range_must_cover_whole_hunk(tmp_path):
    """声明范围必须完整覆盖 hunk；跨边界/对不上 → 前置失败、不提交。"""
    repo = _make_repo(tmp_path)
    # 再把第 6 行也改掉，让第 5、6 行合成一个 5-6 的 hunk
    lines = (repo / "doc.md").read_text(encoding="utf-8").splitlines()
    lines[5] = "line6 other2"
    (repo / "doc.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    msg = tmp_path / "msg.txt"
    msg.write_text("test: bad exclusion\n", encoding="utf-8")
    env = os.environ.copy()
    env.pop("GIT_INDEX_FILE", None)
    env["MBDSDR_REPO_ROOT"] = str(repo)
    before = _git(repo, "rev-parse", "HEAD").stdout.strip()

    proc = subprocess.run(
        [
            sys.executable, str(SAFE),
            "--files", "doc.md",
            "--message-file", str(msg),
            "--exclude-hunk", "doc.md:5-5",
        ],
        cwd=str(repo), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "未完整覆盖" in out
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == before


def test_multiple_exclusion_hunks_survive_line_count_shift(tmp_path):
    """一个文件里排除多个 hunk：提交里只能有自己那一份，别线一个都不能带。

    形状：自己的改动在第 2 行；别线在第 5 行后插两行（行数 +2），又在原第
    9 行（新文件第 11 行）改了一行——两个排除 hunk 的行数变化方向不同。

    真实故障记录：2026-11-13 在 ``desktop/main_window.py`` 上排除 31 个 hunk
    时，旧实现「所有 hunk 拼成一个 patch 一次 apply -R」报
    ``patch failed: main_window.py:8401``（-U0 无上下文，靠前 hunk 的行数
    变化把后面的 hunk 挤偏）。现改为自下而上逐个应用。本用例锁住多 hunk
    语义本身（含行数 +2 的形状），真实文件上的验收记在提交说明里。
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", check=True)
    _git(repo, "config", "user.email", "safe-commit@example.invalid", check=True)
    _git(repo, "config", "user.name", "safe-commit", check=True)
    _git(repo, "config", "commit.gpgsign", "false", check=True)
    lines = [f"line{i}" for i in range(1, 10)]
    (repo / "doc.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _git(repo, "add", "doc.md", check=True)
    _git(repo, "commit", "-q", "-m", "base", check=True)

    lines[1] = "line2 mine"
    lines[4:5] = ["line5", "other5a", "other5b"]      # 别线：+5,2（行数变多）
    lines[10] = "line9 other"                          # 别线：+11,1
    (repo / "doc.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    msg = tmp_path / "msg.txt"
    msg.write_text("test: multi-hunk exclusion\n", encoding="utf-8")
    env = os.environ.copy()
    env.pop("GIT_INDEX_FILE", None)
    env["MBDSDR_REPO_ROOT"] = str(repo)

    proc = subprocess.run(
        [
            sys.executable, str(SAFE),
            "--files", "doc.md",
            "--message-file", str(msg),
            "--exclude-hunk", "doc.md:6-7",
            "--exclude-hunk", "doc.md:11-11",
            "--exclude-owner", "别线",
        ],
        cwd=str(repo), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out

    committed = _git(repo, "show", "HEAD:doc.md").stdout.splitlines()
    assert "line2 mine" in committed
    assert "other5a" not in committed and "other5b" not in committed
    assert "line9 other" not in committed
    assert "line5" in committed and "line9" in committed

    tree = (repo / "doc.md").read_text(encoding="utf-8").splitlines()
    assert "other5a" in tree and "line9 other" in tree, "工作树不能被动"


def test_repeated_files_flag_commits_every_declared_file(tmp_path):
    """反证：``--files A --files B`` 不许只提交最后一个。

    ``nargs='+'`` 的默认语义是「重复传参只取最后一次」，而 ``--files`` 清单就是
    作用域断言的全部依据——被悄悄截短的声明集合会让「只交了一个文件」也全绿。
    现在用 ``action='extend'`` 合并：两个文件必须都进这一笔。
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", check=True)
    _git(repo, "config", "user.email", "safe-commit@example.invalid", check=True)
    _git(repo, "config", "user.name", "safe-commit", check=True)
    _git(repo, "config", "commit.gpgsign", "false", check=True)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    _git(repo, "add", "a.txt", "b.txt", check=True)
    _git(repo, "commit", "-q", "-m", "base", check=True)
    (repo / "a.txt").write_text("a2\n", encoding="utf-8")
    (repo / "b.txt").write_text("b2\n", encoding="utf-8")

    msg = tmp_path / "msg.txt"
    msg.write_text("test: repeated --files\n", encoding="utf-8")
    env = os.environ.copy()
    env.pop("GIT_INDEX_FILE", None)
    env["MBDSDR_REPO_ROOT"] = str(repo)

    proc = subprocess.run(
        [
            sys.executable, str(SAFE),
            "--files", "a.txt",
            "--files", "b.txt",
            "--message-file", str(msg),
        ],
        cwd=str(repo), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "本次声明 2 个文件" in out, out
    names = _git(repo, "show", "--pretty=", "--name-only", "HEAD").stdout.split()
    assert sorted(names) == ["a.txt", "b.txt"], out

