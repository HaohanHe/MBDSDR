# SPDX-License-Identifier: MIT
"""``scripts/head_symbol_check.py`` 的判据测试。

三类必须分开断言，混在一起就分不清是「跑不过用例」还是「符号不存在」：

* 真样本：``2b69e3df`` / ``ec3a964b`` 判红并点名缺失符号，``87b3a36d`` 判绿；
* 反证：同一批提交只跑烟雾用例时**全绿**——红是符号判据带来的，不是用例；
* 合成：现造一笔「调用了未定义方法」的提交，检查必须红且点名。
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "head_symbol_check", ROOT / "scripts" / "head_symbol_check.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


symbol = _load()


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _make_repo(tmp_path: Path, files: dict[str, str], message: str = "c") -> str:
    repo = tmp_path
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


# ---------------------------------------------------------------------------
# 真样本
# ---------------------------------------------------------------------------
def test_set_reassembly_quality_commit_is_red_and_named():
    """2b69e3df：调用侧先落地，StatusPanel 还没有 set_reassembly_quality。"""
    code, report = symbol.run(ROOT, "2b69e3df")
    assert code == symbol.EXIT_MISSING
    assert report["status"] == "red"
    names = [item["symbol"] for item in report["missing"]]
    assert any("set_reassembly_quality" in name for name in names), names
    assert any("desktop/main_window.py" == item["path"] for item in report["missing"])


def test_parser_stats_commit_is_red_and_named():
    """ec3a964b：parser.stats() 已调用，HRITParser 还没有 stats。"""
    code, report = symbol.run(ROOT, "ec3a964b")
    assert code == symbol.EXIT_MISSING
    assert report["status"] == "red"
    names = [item["symbol"] for item in report["missing"]]
    assert any(name.endswith("stats") for name in names), names
    assert any(item["path"] == "desktop/weather_panel.py" for item in report["missing"])


def test_definition_commit_is_green():
    """87b3a36d：两个定义补齐后同一判据转绿。"""
    code, report = symbol.run(ROOT, "87b3a36d")
    assert code == symbol.EXIT_OK
    assert report["status"] == "green"
    assert report["missing"] == []


# ---------------------------------------------------------------------------
# 反证：只跑用例时这两笔是绿的
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sha", ["2b69e3df", "ec3a964b"])
def test_smoke_tests_alone_are_green_on_the_red_commits(tmp_path, sha):
    """反证：这两笔的符号判据是红的，而只跑用例时是绿的——红只可能来自新判据。"""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "head_build_smoke.py"),
         "--repo", str(ROOT), "--commit", sha],
        capture_output=True, text=True,
        env={**os.environ,
             "MBDSDR_HEAD_BUILD_LOG": str(tmp_path / "smoke.log"),
             # 父进程（本 pytest）持有全局 Qt 锁但此刻正阻塞在 subprocess 上，
             # 没有在跑 Qt 代码；子进程用独立的锁文件即可，不必关互斥。
             "MBDSDR_TEST_LOCK_PATH": str(tmp_path / "qt_child.lock")},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "可构建" in proc.stdout, proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# 合成：现造一笔「调用未定义方法」的提交
# ---------------------------------------------------------------------------
def test_synthetic_undefined_method_commit_is_red(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo, {
        "core.py": "class Thing:\n    def known_method(self):\n        return 1\n",
        "app.py": "from core import Thing\n\n\ndef run():\n    thing = Thing()\n    return thing.known_method()\n",
    }, "base")
    # 第二笔：调用一个不存在的方法（带下划线的项目式命名）
    (repo / "app.py").write_text(
        "from core import Thing\n\n\ndef run():\n    thing = Thing()\n"
        "    thing.missing_helper()\n    return thing.known_method()\n",
        encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "调用未定义方法")
    code, report = symbol.run(repo, "HEAD")
    assert code == symbol.EXIT_MISSING
    assert [item["symbol"] for item in report["missing"]] == ["thing.missing_helper"]


def test_synthetic_defined_method_commit_is_green(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo, {
        "core.py": "class Thing:\n    def known_method(self):\n        return 1\n",
        "app.py": "from core import Thing\n\n\ndef run():\n    thing = Thing()\n    return thing.known_method()\n",
    }, "base")
    (repo / "app.py").write_text(
        "from core import Thing\n\n\ndef run():\n    thing = Thing()\n"
        "    return thing.known_method()\n",
        encoding="utf-8")
    (repo / "core.py").write_text(
        "class Thing:\n    def known_method(self):\n        return 1\n"
        "    def added_later(self):\n        return 2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "调用与定义同笔")
    code, report = symbol.run(repo, "HEAD")
    assert code == symbol.EXIT_OK, report


def test_unresolved_is_listed_not_counted_as_present(tmp_path):
    """解析不了 → 列进 unresolved，不计入存在数，也不判红。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo, {"app.py": "def run(payload):\n    return payload\n"}, "base")
    (repo / "app.py").write_text(
        "def run(payload):\n    return payload.format_now()\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "接收者类型未知")
    code, report = symbol.run(repo, "HEAD")
    assert code == symbol.EXIT_OK
    assert report["status"] == "green"
    assert report["ok"] == 0
    assert [item["symbol"] for item in report["unresolved"]] == ["payload.format_now"]


# ---------------------------------------------------------------------------
# 白名单：逐条理由、条数上限
# ---------------------------------------------------------------------------
def test_allowlist_needs_reason(tmp_path):
    path = tmp_path / "allow.json"
    path.write_text(json.dumps({"max_entries": 12,
                                "entries": [{"path": "a.py", "symbol": "x", "reason": ""}]}),
                    encoding="utf-8")
    with pytest.raises(ValueError, match="reason"):
        symbol._allowlist_load(path)


def test_allowlist_cap_is_enforced(tmp_path):
    path = tmp_path / "allow.json"
    entries = [{"path": "a.py", "symbol": f"x{i}", "reason": "r"} for i in range(3)]
    path.write_text(json.dumps({"max_entries": 2, "entries": entries}), encoding="utf-8")
    with pytest.raises(ValueError, match="超过上限"):
        symbol._allowlist_load(path)


def test_shipped_allowlist_is_small_and_justified():
    entries = symbol._allowlist_load(ROOT / "scripts" / "symbol_check_allowlist.json")
    assert len(entries) <= symbol.MAX_ALLOWLIST_ENTRIES
    assert all(item.get("reason", "").strip() for item in entries)
