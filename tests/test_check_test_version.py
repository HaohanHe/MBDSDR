"""check_test_version.py 反证：改测试销红必须被识破。

场景：临时仓里 `test_math.py` 在 HEAD 红；工作区把断言改绿。
  - 脚本必须 WARN「被测版本不同，绿的结论无效」并退出 1；
  - 把工作区测试改回 HEAD（覆盖为原内容）后，工作区重新变红，
    脚本不再报警、退出 0。
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_test_version.py"


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def _run(repo):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "test_math.py"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "tv@example.invalid")
    _git(repo, "config", "user.name", "tv-test")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "test_math.py").write_text(
        "def test_red():\n    assert 1 == 2\n", encoding="utf-8"
    )
    _git(repo, "add", "test_math.py")
    _git(repo, "commit", "-q", "-m", "red at HEAD")
    return repo


def test_modified_test_to_green_is_flagged(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "test_math.py").write_text(
        "def test_red():\n    assert 1 == 1\n", encoding="utf-8"
    )

    proc = _run(repo)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, (proc.returncode, out)
    assert "被测版本不同，绿的结论无效" in out
    assert "HEAD" in out and "红" in out


def test_reverting_test_makes_it_red_and_no_warning(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "test_math.py").write_text(
        "def test_red():\n    assert 1 == 1\n", encoding="utf-8"
    )
    _run(repo)

    # 改回 HEAD 版本
    _git(repo, "checkout", "--", "test_math.py")
    proc = _run(repo)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, (proc.returncode, out)
    assert "被测版本不同" not in out
    # 工作区确实重新变红
    direct = subprocess.run(
        [sys.executable, "-m", "pytest", "test_math.py", "-q", "--tb=no"],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    assert direct.returncode != 0
