# SPDX-License-Identifier: MIT
"""反证：会话私有 index 的隔离是真的（两进程 + 共享 index 污染两种场景）。

场景 1：两个进程各自声明不同文件并发提交 → 每笔 tree 只能有自己那份，
        且共享 ``.git/index`` 始终干净。
场景 2：共享 index 先被别人 ``git add`` 污染 → 本脚本的提交不得带走它，
        污染项必须原样留在共享 index 里。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SAFE = REPO / "scripts" / "safe_commit.py"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, encoding="utf-8")


def _make_repo(tmp: Path) -> None:
    _git(tmp, "init", "-q")
    _git(tmp, "config", "user.email", "probe@example.invalid")
    _git(tmp, "config", "user.name", "probe")
    (tmp / "a.txt").write_text("a-base\n", encoding="utf-8")
    (tmp / "b.txt").write_text("b-base\n", encoding="utf-8")
    _git(tmp, "add", "a.txt", "b.txt")
    assert _git(tmp, "commit", "-qm", "base").returncode == 0


def _env(tmp: Path, session: str) -> dict:
    env = dict(os.environ)
    env.update({
        "MBDSDR_REPO_ROOT": str(tmp),
        "MBDSDR_LOCKS_DIR": str(tmp / ".locks"),
        "MBDSDR_SESSION_ID": session,
    })
    env.pop("GIT_INDEX_FILE", None)
    return env


def _run_safe(tmp: Path, session: str, files: list[str], message: str):
    msg = tmp / f"msg_{session}.txt"
    msg.write_text(message, encoding="utf-8")
    return subprocess.Popen(
        [sys.executable, str(SAFE), "--files", *files,
         "--message-file", str(msg), "--retries", "6"],
        env=_env(tmp, session), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8",
    )


def _commit_of(tmp: Path, subject: str) -> str:
    out = _git(tmp, "log", "--all", "--format=%H%x01%s").stdout
    for line in out.splitlines():
        sha, _, subj = line.partition("\x01")
        if subj == subject:
            return sha
    raise AssertionError(f"找不到提交 {subject!r}")


def _paths(tmp: Path, sha: str) -> list[str]:
    out = _git(tmp, "show", "--name-only", "--format=", sha).stdout
    return sorted(p for p in out.splitlines() if p)


def test_two_sessions_cannot_see_each_other(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    (tmp_path / "a.txt").write_text("a-A\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b-B\n", encoding="utf-8")

    # 共享 index 的条目不得被本脚本写过（它只用于告警，绝不写）
    before = _git(tmp_path, "ls-files", "-s").stdout
    a = _run_safe(tmp_path, "flowA", ["a.txt"], "commit A")
    b = _run_safe(tmp_path, "flowB", ["b.txt"], "commit B")
    out_a = a.communicate(timeout=120)[0]
    out_b = b.communicate(timeout=120)[0]
    assert a.returncode == 0, out_a
    assert b.returncode == 0, out_b

    sha_a, sha_b = _commit_of(tmp_path, "commit A"), _commit_of(tmp_path, "commit B")
    assert _paths(tmp_path, sha_a) == ["a.txt"]
    assert _paths(tmp_path, sha_b) == ["b.txt"]
    # A 的改动集里不能出现 b.txt（先后顺序不定，但「改了哪些文件」是确定的）
    assert "b.txt" not in _paths(tmp_path, sha_a)
    assert "a.txt" not in _paths(tmp_path, sha_b)
    # 共享 index 的条目逐项不变（本脚本只读它来告警，绝不写）
    assert _git(tmp_path, "ls-files", "-s").stdout == before


def test_foreign_staged_entry_is_not_committed(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    (tmp_path / "a.txt").write_text("a-only\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b-foreign\n", encoding="utf-8")
    # 模拟别线：往共享 index 暂存 b.txt（ddcecf76 事故的前置条件）
    assert _git(tmp_path, "add", "b.txt").returncode == 0
    before = _git(tmp_path, "ls-files", "-s").stdout

    proc = _run_safe(tmp_path, "flowA", ["a.txt"], "commit A only")
    out = proc.communicate(timeout=120)[0]
    assert proc.returncode == 0, out

    sha = _commit_of(tmp_path, "commit A only")
    assert _paths(tmp_path, sha) == ["a.txt"]
    assert _git(tmp_path, "show", f"{sha}:b.txt").stdout == "b-base\n"
    # 共享 index 的条目逐字节不变（本脚本只读它来告警，绝不写），
    # 别人的暂存项原样留在里面，没被吞。
    after = _git(tmp_path, "ls-files", "-s").stdout
    assert after == before
    # 别人的暂存项内容原样留在共享 index（`:b.txt` 读的就是共享 index）
    assert _git(tmp_path, "show", ":b.txt").stdout == "b-foreign\n"


def test_contaminated_shared_index_is_visible_to_same_assertion(tmp_path: Path) -> None:
    """阳性对照：同一组断言用在「共享 index 串味」上必须变红。

    没有这条，上面两条通过只说明断言恒真。这里完全不碰 safe_commit，只用
    普通 git 复刻 ``ddcecf76`` 的成因：A ``git add`` 两个文件、只提交声明路径
    之外的那笔被别人带走，``_paths`` 里就能看到属于对方的路径。
    """
    _make_repo(tmp_path)
    (tmp_path / "a.txt").write_text("a-A\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b-B\n", encoding="utf-8")
    # A 线把两处改动一起塞进共享 index，却只想提交 a.txt
    assert _git(tmp_path, "add", "a.txt", "b.txt").returncode == 0
    # B 线（共享 index）此时提交：b.txt 被按 B 的消息带走
    assert _git(tmp_path, "commit", "-qm", "commit B").returncode == 0

    sha_b = _commit_of(tmp_path, "commit B")
    assert _paths(tmp_path, sha_b) == ["a.txt", "b.txt"], "串味必须可见，否则断言无意义"
    assert _paths(tmp_path, sha_b) != ["b.txt"]


def test_commit_lock_serialises_and_keeps_foreign_file(tmp_path: Path) -> None:
    """并发提交串行化后，后提交的一方不得删掉先提交方刚加的文件。

    私有 index 建在旧 HEAD 上，若提交窗口不互斥，后一笔的 tree 里对方文件
    会消失（diff-tree 表现为删除）。这把锁要保证它还在。
    """
    _make_repo(tmp_path)
    (tmp_path / "a.txt").write_text("a-A\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b-B\n", encoding="utf-8")

    a = _run_safe(tmp_path, "flowA", ["a.txt"], "commit A")
    b = _run_safe(tmp_path, "flowB", ["b.txt"], "commit B")
    out_a = a.communicate(timeout=120)[0]
    out_b = b.communicate(timeout=120)[0]
    assert a.returncode == 0, out_a
    assert b.returncode == 0, out_b

    # 无论谁先落，最终 tree 里两份改动都在
    assert _git(tmp_path, "show", "HEAD:a.txt").stdout == "a-A\n"
    assert _git(tmp_path, "show", "HEAD:b.txt").stdout == "b-B\n"
    for sha in (_commit_of(tmp_path, "commit A"), _commit_of(tmp_path, "commit B")):
        assert len(_paths(tmp_path, sha)) == 1, sha
    # 提交锁不得残留
    assert not (tmp_path / ".locks" / "commit.lock").exists()
