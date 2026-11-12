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
