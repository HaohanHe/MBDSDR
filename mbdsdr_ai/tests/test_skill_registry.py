# SPDX-License-Identifier: MIT
"""skill_registry 测试：目录 mtime 缓存 + user 双根 + 激活事件 + 向后兼容。

背景：2026-10-14 kimi-code 审计第五落点。原实现每次 list()/load() 前全量
os.listdir 重扫单一项目根、无激活事件。本测试钉住升级后的语义：

  * 缓存命中不重扫（monkeypatch os.listdir 计数，零 I/O 复用）；
  * 根目录 mtime 变化（os.utime 驱动）才触发重扫，新技能被发现；
  * user 根 ~/.mbdsdr/skills 可注入/默认发现，同名技能项目根优先，
    summary 带 source 字段区分来源；
  * 空目录/不存在目录诚实返回空列表；
  * load() 成功时向 HookManager 发 agent.tool_call，data 含
    {skill, path, source}；未命中不发事件；无 hook_manager 静默；
  * 向后兼容：既有单参构造 SkillRegistry(dir) 与 list()/load() 调用方式
    全部仍通过（agent.py、tests/test_modularization_audit.py 的用法）。

所有技能根一律注入 tmp_path，不触碰真实 ~/.mbdsdr（默认根解析测试经
monkeypatch USERPROFILE 重定向，同样 hermetic）。
"""
from __future__ import annotations

import os
import time

import pytest

from mbdsdr_ai import skill_registry
from mbdsdr_ai.hooks import EventType, HookManager
from mbdsdr_ai.skill_registry import SkillRegistry


# ---------------------------------------------------------------------------
# 助手
# ---------------------------------------------------------------------------
def _make_skill(root, name: str, desc: str, body: str = "") -> None:
    """在 root 下造一个目录包技能 <name>/SKILL.md（正文留痕区分来源）。"""
    d = os.path.join(str(root), name)
    os.makedirs(d, exist_ok=True)
    text = (f"---\nname: {name}\ndescription: {desc}\n---\n\n"
            f"{body or f'{name} 正文'}\n")
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(text)


def _listdir_spy(monkeypatch) -> list:
    """给 skill_registry 用的 os.listdir 装计数器，返回调用路径列表。"""
    calls: list = []
    real_listdir = os.listdir

    def spy(path):
        calls.append(path)
        return real_listdir(path)

    monkeypatch.setattr(skill_registry.os, "listdir", spy)
    return calls


def _touch_mtime(path: str, delta_s: float = 120.0) -> None:
    """显式把目录 mtime 推到未来（绕开文件系统 mtime 粒度不确定性）。"""
    t = time.time() + delta_s
    os.utime(path, (t, t))


# ---------------------------------------------------------------------------
# ① 缓存命中不重扫
# ---------------------------------------------------------------------------
def test_cache_hit_does_not_rescan(tmp_path, monkeypatch):
    proj = tmp_path / "skills"
    _make_skill(proj, "alpha", "技能 A", body="A 正文")
    _make_skill(proj, "beta", "技能 B")
    sr = SkillRegistry(str(proj), user_dir="")

    with monkeypatch.context() as m:
        calls = _listdir_spy(m)
        first = {s["name"] for s in sr.list()}
        assert first == {"alpha", "beta"}
        assert len(calls) == 1, "首次扫描应恰好 listdir 一次"
        # 再次 list()/load() 命中缓存：不再碰 listdir（零 I/O）
        sr.list()
        sr.load("alpha")
        assert len(calls) == 1, "缓存命中不应重复扫描"


# ---------------------------------------------------------------------------
# ② mtime 变化触发重扫
# ---------------------------------------------------------------------------
def test_mtime_change_triggers_rescan(tmp_path, monkeypatch):
    proj = tmp_path / "skills"
    _make_skill(proj, "alpha", "技能 A")
    sr = SkillRegistry(str(proj), user_dir="")

    with monkeypatch.context() as m:
        calls = _listdir_spy(m)
        sr.list()
        assert len(calls) == 1
        # mtime 不动 → 复用缓存
        sr.list()
        assert len(calls) == 1
        # 新增技能 + 显式推进根目录 mtime → 触发重扫并发现新技能
        _make_skill(proj, "gamma", "技能 C")
        _touch_mtime(str(proj))
        names = {s["name"] for s in sr.list()}
        assert "gamma" in names
        assert len(calls) == 2, "mtime 变化应触发重扫"


# ---------------------------------------------------------------------------
# ③ user 根被发现且项目根同名优先
# ---------------------------------------------------------------------------
def test_user_root_discovered_project_priority(tmp_path):
    proj = tmp_path / "proj_skills"
    user = tmp_path / "user_skills"
    _make_skill(proj, "shared", "项目根版本", body="项目正文")
    _make_skill(user, "shared", "user 根版本", body="user 正文")
    _make_skill(user, "only-user", "仅 user 根有", body="user 独有正文")

    sr = SkillRegistry(str(proj), user_dir=str(user))
    listed = {s["name"]: s for s in sr.list()}

    # user 独有技能被发现，source=user
    assert "only-user" in listed
    assert listed["only-user"]["source"] == "user"
    assert listed["only-user"]["description"] == "仅 user 根有"

    # 同名技能：项目根优先（summary 描述与 load 正文都是项目根版本）
    assert listed["shared"]["description"] == "项目根版本"
    assert listed["shared"]["source"] == "project"
    body = sr.load("shared")
    assert body is not None and "项目正文" in body

    # summary 键集合含 source，且不丢既有键
    assert set(listed["shared"]) == {"name", "description",
                                     "when_to_use", "source"}


def test_default_user_root_discovered(tmp_path, monkeypatch):
    """user_dir=None（默认）解析到 ~/.mbdsdr/skills 并参与扫描。"""
    home = tmp_path / "home"
    _make_skill(home / ".mbdsdr" / "skills", "personal", "个人技能")
    monkeypatch.setenv("USERPROFILE", str(home))

    sr = SkillRegistry(str(tmp_path / "no_project"))  # 项目根不存在
    assert sr.user_dir == os.path.normpath(os.path.expanduser("~/.mbdsdr/skills"))
    listed = {s["name"]: s for s in sr.list()}
    assert listed["personal"]["source"] == "user"
    assert sr.load("personal")


# ---------------------------------------------------------------------------
# ④ 空/不存在目录诚实空列表
# ---------------------------------------------------------------------------
def test_missing_and_empty_dirs_are_honest_empty(tmp_path):
    missing = tmp_path / "nope"
    empty = tmp_path / "empty"
    empty.mkdir()

    sr = SkillRegistry(str(missing), user_dir=str(tmp_path / "nope2"))
    assert sr.list() == []
    assert sr.load("whatever") is None

    sr2 = SkillRegistry(str(empty), user_dir="")
    assert sr2.list() == []


# ---------------------------------------------------------------------------
# ⑤ 激活事件
# ---------------------------------------------------------------------------
def test_activation_event_emitted(tmp_path):
    proj = tmp_path / "skills"
    _make_skill(proj, "demo", "演示技能", body="演示正文")

    hm = HookManager()
    seen: list = []
    hm.register(EventType.AGENT_TOOL_CALL.value, lambda e: seen.append(e))

    sr = SkillRegistry(str(proj), user_dir="", hook_manager=hm)
    body = sr.load("demo")
    assert body is not None and "演示正文" in body

    assert len(seen) == 1
    ev = seen[0]
    assert ev.event_type == EventType.AGENT_TOOL_CALL.value
    assert ev.source == "skill_registry"
    assert ev.data == {
        "skill": "demo",
        "path": os.path.join(str(proj), "demo", "SKILL.md"),
        "source": "project",
    }

    # 未命中的 load 不发事件
    assert sr.load("missing") is None
    assert len(seen) == 1


def test_no_hook_manager_is_silent(tmp_path):
    """未注入 HookManager 时 load 照常工作（向后兼容主路径）。"""
    proj = tmp_path / "skills"
    _make_skill(proj, "demo", "演示技能")
    sr = SkillRegistry(str(proj), user_dir="", hook_manager=None)
    assert sr.load("demo") is not None


def test_failing_hook_manager_does_not_break_load(tmp_path):
    """hook 触发异常被静默吞掉，不影响 load 主流程。"""

    class _Boom:
        def trigger(self, event):
            raise RuntimeError("hook 爆炸")

    proj = tmp_path / "skills"
    _make_skill(proj, "demo", "演示技能", body="演示正文")
    sr = SkillRegistry(str(proj), user_dir="", hook_manager=_Boom())
    assert sr.load("demo") == "演示正文"


def test_user_root_activation_event_source(tmp_path):
    """user 根技能的激活事件 source 字段为 user。"""
    user = tmp_path / "user_skills"
    _make_skill(user, "personal", "个人技能")
    hm = HookManager()
    seen: list = []
    hm.register(EventType.AGENT_TOOL_CALL.value, lambda e: seen.append(e))

    sr = SkillRegistry(str(tmp_path / "no_project"), user_dir=str(user),
                       hook_manager=hm)
    assert sr.load("personal") is not None
    assert len(seen) == 1
    assert seen[0].data["source"] == "user"
    assert seen[0].data["path"] == os.path.join(str(user), "personal",
                                                "SKILL.md")


# ---------------------------------------------------------------------------
# ⑥ 向后兼容：既有单参构造与调用方式
# ---------------------------------------------------------------------------
def test_legacy_single_arg_constructor(tmp_path, monkeypatch):
    # 重定向 HOME 让默认 user 根指向 tmp，保持 hermetic
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))

    proj = tmp_path / "skills"
    _make_skill(proj, "legacy", "旧式单参构造技能", body="legacy 正文")

    # 既有调用方式（agent.py:523 / test_modularization_audit.py:102 同形状）：
    # 只传项目根一个位置参数
    sr = SkillRegistry(str(proj))
    assert sr.skills_dir == str(proj)
    listed = sr.list()
    assert len(listed) == 1 and listed[0]["name"] == "legacy"
    assert sr.load("legacy") == "legacy 正文"
    # 默认 user 根解析但不干扰（tmp home 下不存在）
    assert sr.user_dir == os.path.join(str(home), ".mbdsdr", "skills")


def test_summary_keys_backward_compatible(tmp_path):
    """既有消费方只读 name/description/when_to_use，新增 source 不改旧键。"""
    proj = tmp_path / "skills"
    _make_skill(proj, "alpha", "技能 A", body="A 正文")
    sr = SkillRegistry(str(proj), user_dir="")
    summary = sr.list()[0]
    for key in ("name", "description", "when_to_use"):
        assert key in summary
    assert summary["name"] == "alpha"
    assert summary["description"] == "技能 A"
    assert summary["when_to_use"] == ""
