# SPDX-License-Identifier: MIT
"""技能正文里引用的工具名必须真实存在（文档 ↔ 实现不许掰腕子）。

背景
----
`skills/*/SKILL.md` 是给模型的**操作指引**：正文里写 `` `tool_name` `` 就是在
教模型调用它。写错名字模型会调不到工具，而且这类错误不会在运行时自动暴露。
2026-11-22 审计发现 `sat-track` 里写着 `astronomy` / `cfo` 两个不存在的名字。

本文件钉住三件事：
1. 项目技能都被发现，name/description 非空且不重名；
2. 正文反引号里的 snake_case 名字必须在 agent 工具注册表里真实存在；
3. 「第一次看天空」技能保留「先判断用户水平」的分支与硬边界，不退化成固定向导。
"""
from __future__ import annotations

import functools
import os
import re

import pytest

from mbdsdr_ai.skill_registry import SkillRegistry

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SKILLS_DIR = os.path.join(_ROOT, "skills")

#: 反引号里只认「像工具名」的 token：小写开头、下划线连接。
_TOOL_TOKEN_RE = re.compile(r"`([a-z][a-z0-9_]+)`")


@functools.lru_cache(maxsize=1)
def _tool_names() -> frozenset:
    """agent 侧全量工具名（构造成本 ~1 s，本文件内只建一次）。

    构造时临时打开 MBDSDR_NO_HW，避免测试去碰真实 USB 设备。
    """
    from mbdsdr_ai.agent import MBDSDRAgent
    from mbdsdr_ai.config import AgentConfig

    old = os.environ.get("MBDSDR_NO_HW")
    os.environ["MBDSDR_NO_HW"] = "1"
    try:
        agent = MBDSDRAgent(AgentConfig(
            api_key="sk-offline",
            enable_self_evolution=False,
            enable_session_log=False,
        ))
        return frozenset(agent.tool_registry.tools)
    finally:
        if old is None:
            os.environ.pop("MBDSDR_NO_HW", None)
        else:
            os.environ["MBDSDR_NO_HW"] = old


def _registry() -> SkillRegistry:
    # 显式禁 user 根：测试只认仓库里的技能，避免本机个人技能影响门禁。
    return SkillRegistry(_SKILLS_DIR, user_dir="")


def test_project_skills_are_discovered_with_metadata():
    summaries = _registry().list()
    names = [s["name"] for s in summaries]
    assert len(names) == len(set(names)), f"技能重名: {names}"
    assert {
        "find-interference", "sat-track", "sstv-decode", "sky-first-light"
    } <= set(names)
    for s in summaries:
        assert s["description"].strip(), f"{s['name']} 缺 description"
        assert s["when_to_use"].strip(), f"{s['name']} 缺 when_to_use"


@pytest.mark.parametrize("skill_name", [
    "find-interference", "sat-track", "sstv-decode", "sky-first-light",
])
def test_backticked_tool_names_exist(skill_name):
    body = _registry().load(skill_name)
    assert body, f"技能 {skill_name} 读不到正文"
    refs = sorted(set(_TOOL_TOKEN_RE.findall(body)))
    missing = [r for r in refs if r not in _tool_names()]
    assert not missing, (
        f"{skill_name} 引用了不存在的工具 {missing}；"
        "要么改成注册表里的真名，要么去掉反引号（不要教模型调用不存在的工具）")


def test_sat_track_no_longer_cites_phantom_tools():
    """回归：曾把 `astronomy` / `cfo` 当工具名。"""
    body = _registry().load("sat-track") or ""
    assert "`astronomy`" not in body
    assert "`cfo`" not in body
    assert "`satellite_predict_pass`" in body
    assert "`doppler_calc`" in body


class TestSkyFirstLight:
    """引导深度由模型判断：技能只给判断依据与边界，不给固定问卷。"""

    def setup_method(self):
        self.body = _registry().load("sky-first-light") or ""

    def test_has_level_branches_not_a_fixed_wizard(self):
        assert "用户已经说出设备/动作" in self.body
        assert "用户只描述愿望" in self.body
        assert "用户说“你直接来”" in self.body
        # 不允许固定问题清单/编号问答
        assert "问题 1" not in self.body
        assert "问卷" not in self.body

    def test_keeps_honesty_boundaries(self):
        # 罗盘精度、GNSS 无航向、不假设型号/FOV
        assert "5–15°" in self.body
        assert "不给航向" in self.body
        assert "不假设望远镜型号" in self.body
        assert "FOV 未知时不要承诺能解算" in self.body
        # 指天视图依赖罗盘/IMU；不得宣称摄像头自动认星
        assert "指天视图依赖罗盘/IMU" in self.body
        assert "摄像头一对准就自动认星" in self.body
        assert "不描述本产品不存在的按钮" in self.body

    def test_paths_are_one_step_at_a_time(self):
        assert "一次只给当前一步" in self.body
        assert "不要从第一步讲起" in self.body

    def test_ui_facts_section_lists_only_verified_controls(self):
        """实测 8B 会编造绿色光晕/点击屏幕中央；技能必须给出已核实清单。"""
        assert "已确认的 UI 事实" in self.body
        assert "不存在的交互" in self.body
        assert "绿色光晕" in self.body
        assert "指天视图" in self.body
        assert "准星" in self.body and "距中心角距" in self.body

    def test_no_fake_heading_input_in_instructions(self):
        """实测 8B 曾照抄技能里的“手动输入方位”；源头不得再教不存在的控件。"""
        assert "手动输入方位" not in self.body
        assert "「左 5°」「右 5°」" in self.body
        assert "方位输入框" in self.body          # 只出现在“不存在/没有”清单里
        assert "滑动屏幕" in self.body            # 同上
        assert "准星自动追踪" in self.body
