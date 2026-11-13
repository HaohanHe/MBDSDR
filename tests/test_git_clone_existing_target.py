# SPDX-License-Identifier: MIT
"""S4：``git_clone_repo`` 的目标已存在时，必须校验它是不是**可用仓库**。

事故（2026-11-13）：只判断 ``os.path.exists(dest)`` 就返回
``ToolResult(True, "已存在")``。``--depth 1`` 克隆中途失败会留下带 ``.git``
但没有完整提交 / 没有 origin 的残目录，用户以为仓库可用，后续读源码拿到
残缺内容。文案说了「已存在」所以不算静默，但结论是伪造的。

判据（两条都要过）：``rev-parse HEAD`` 能过、``remote.origin.url`` 与请求一致。
"""
import os
import subprocess
import sys

os.environ.setdefault("MBDSDR_NO_HW", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from mbdsdr_ai import AgentConfig, MBDSDRAgent  # noqa: E402
from mbdsdr_ai.agent import _clone_target_problem  # noqa: E402

URL = "https://github.com/example/sdr-tool.git"


@pytest.fixture()
def agent(tmp_path, monkeypatch):
    monkeypatch.delenv("MBDSDR_OFFLINE", raising=False)
    a = MBDSDRAgent(AgentConfig(api_key=""))
    a.workspace_root = str(tmp_path)
    return a


def _dest(tmp_path, name="sdr-tool"):
    d = os.path.join(tmp_path, "repos", name)
    os.makedirs(d, exist_ok=True)
    return d


def _clone(agent, url=URL, dest_name="sdr-tool"):
    return agent.tool_registry.call("git_clone_repo",
                                    {"url": url, "dest": dest_name})


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=True)


def _make_repo(path, origin=URL, commit=True):
    _git("init", cwd=path)
    if commit:
        _git("-c", "user.email=t@example.com", "-c", "user.name=t",
             "commit", "--allow-empty", "-m", "init", cwd=path)
    if origin:
        _git("remote", "add", "origin", origin, cwd=path)
    return path


def test_existing_empty_dir_is_rejected_not_reported_as_ready(agent, tmp_path):
    d = _dest(tmp_path)

    r = _clone(agent)

    assert r.success is False, "空目录不是可用仓库，不许报成功"
    assert "不是可用仓库" in r.content
    assert "rev-parse HEAD" in r.content, "要说清哪一步校验没过"
    assert d in r.content


def test_half_cloned_dir_without_commit_is_rejected(agent, tmp_path):
    """真实残目录：有 .git、有 origin，但 HEAD 无效（--depth 1 中断）。"""
    d = _make_repo(_dest(tmp_path), commit=False)

    r = _clone(agent)

    assert r.success is False
    assert "不是可用仓库" in r.content
    assert "rev-parse HEAD" in r.content


def test_repo_with_wrong_origin_is_rejected(agent, tmp_path):
    _make_repo(_dest(tmp_path), origin="https://github.com/other/thing.git")

    r = _clone(agent)

    assert r.success is False
    assert "origin 指向" in r.content and "example/sdr-tool" in r.content


def test_intact_repo_is_reported_as_ready(agent, tmp_path):
    _make_repo(_dest(tmp_path))

    r = _clone(agent)

    assert r.success is True
    assert "可用" in r.content


def test_clone_target_helpers_direct(tmp_path):
    """辅助函数直测：.git 后缀差异不该误判（同一仓库）。"""
    d = _make_repo(_dest(tmp_path))
    assert _clone_target_problem(d, URL) == ""
    assert _clone_target_problem(d, "https://github.com/example/sdr-tool") == ""
    assert "origin 指向" in _clone_target_problem(
        d, "https://github.com/example/another.git")
