# SPDX-License-Identifier: MIT
"""引导深度策略：由模型判断，不写成固定问卷/固定步骤表。

背景（2026-11-22 用户明确要求）
------------------------------
「引导也需要模型决定——用户非常明白那就不需要引导了。」因此：
* 系统提示词只给**判断依据与边界**（专家/新手/未知/直接来四种情形），
  不给固定问题清单或固定步骤；
* 真实模型是否照做，由 `scripts/ai_guidance_smoke.py` 在**有 key 时**跑；
  本文件只钉住不依赖网络的那半：提示词策略与探针的判定辅助。
"""
from __future__ import annotations

import importlib.util
import os
from types import SimpleNamespace

from mbdsdr_ai.context_manager import SYSTEM_PROMPT
from mbdsdr_ai.skill_registry import SkillRegistry
from mbdsdr_ai.sky_negotiation import (
    CapabilityLedger,
    build_capability_system_section,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_smoke():
    path = os.path.join(_ROOT, "scripts", "ai_guidance_smoke.py")
    spec = importlib.util.spec_from_file_location("ai_guidance_smoke", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _guidance_section() -> str:
    start = SYSTEM_PROMPT.index("【引导深度由你判断")
    end = SYSTEM_PROMPT.index("【当前硬件】", start)
    return SYSTEM_PROMPT[start:end]


class TestGuidancePolicy:
    def test_policy_is_model_decided_not_scripted(self):
        section = _guidance_section()
        assert "由你判断" in section
        assert "不要套用固定问卷或固定步骤表" in section
        # 不允许编号步骤表或固定问题
        assert "步骤一" not in section
        assert "1." not in section
        assert "问题 1" not in section
        assert "？" not in section

    def test_both_expert_and_novice_branches_are_explicit(self):
        section = _guidance_section()
        # 专家：最短路径、不重复基础、不重复确认已知硬件
        assert "用户明确、专业" in section
        assert "最短路径" in section
        assert "不重复基础术语" in section
        assert "不逐项确认本会话已知的硬件" in section
        # 新手：只给当前一步、不灌整套流程
        assert "用户不懂" in section
        assert "只给当前一步" in section
        assert "不一次性灌整套流程" in section
        # 「直接来」时必须切到执行/结论为主
        assert "你直接来" in section
        assert "以执行/结论为主" in section

    def test_unknown_level_gets_one_step_and_an_optional_branch(self):
        section = _guidance_section()
        assert "用户水平未知" in section
        assert "先给最小可行的一步" in section
        assert "可选分支" in section

    def test_capability_section_stays_non_questionnaire(self):
        text = build_capability_system_section(CapabilityLedger())
        assert "？" not in text
        assert "问题 1" not in text
        # 既有口径仍在：用自然语言问，不生成表单
        assert "不要生成表单" in SYSTEM_PROMPT

    def test_sky_identity_cannot_be_refused_as_out_of_scope(self):
        """实测弱模型曾以「我专注无线电」拒绝天文问题；身份段必须堵住。"""
        assert "你也是星空与观测助手" in SYSTEM_PROMPT
        assert "不得以“我专注无线电 / 不属于我的范围”拒绝" in SYSTEM_PROMPT \
            or "不得以" in SYSTEM_PROMPT
        assert "把手机架上望远镜" in SYSTEM_PROMPT
        # 实测模型把用户推给 Star Walk/SkyView，必须明确禁止替代本产品
        assert "Star Walk" in SYSTEM_PROMPT and "SkyView" in SYSTEM_PROMPT
        assert "替代" in SYSTEM_PROMPT

    def test_gate_rejection_must_not_invent_token_steps(self):
        """实测模型让用户输入 arm_manual 并编造 confirm_token，必须钉死。"""
        assert "arm_manual" in SYSTEM_PROMPT
        assert "不得" in SYSTEM_PROMPT and "编造" in SYSTEM_PROMPT
        assert "本机界面点击确认" in SYSTEM_PROMPT

    def test_unknown_is_not_missing_and_no_fake_camera_claim(self):
        """实测模型把 unknown 说成未连接，并宣称摄像头对准即自动认星。"""
        assert "`unknown` 只能读作「未确认」" in SYSTEM_PROMPT
        assert "不得" in SYSTEM_PROMPT and "未连接 / 没有" in SYSTEM_PROMPT
        assert "摄像头一对准就能自动认星" in SYSTEM_PROMPT
        assert "依赖罗盘/IMU" in SYSTEM_PROMPT

    def test_no_invented_weather_numbers_without_tool_result(self):
        """实测模型没调 sky_tonight 却报「云量 30%/月光 22%/流星雨 10 颗」。"""
        assert "没有调用 sky_tonight" in SYSTEM_PROMPT
        assert "云量、月光、流星雨、极光" in SYSTEM_PROMPT
        assert "没有工具结果就不报数字" in SYSTEM_PROMPT

    def test_no_numbered_list_style(self):
        assert "不要出现 \"1.\" \"2.\" \"3.\"" in SYSTEM_PROMPT
        assert "只给当前一步" in SYSTEM_PROMPT

    def test_skill_catalog_is_injected_and_lists_first_light(self):
        """技能目录要进系统提示词，否则弱模型不会主动调 skill_list。"""
        assert "【技能目录（按需加载）】" in SYSTEM_PROMPT
        catalog = SkillRegistry(
            os.path.join(_ROOT, "skills"), user_dir="").catalog_prompt()
        assert "sky-first-light" in catalog
        assert "第一次看天空" in catalog
        # 目录只给触发条件，不把正文倒进常驻提示词
        assert "<skill_content" not in catalog
        assert "## 先判断" not in catalog


class TestVerifiedUiFactsInPrompt:
    """8B 会编造 UI 手势/输入框；提示词只给已核实控件，未列出的答“以实际界面为准”。

    真实 8B 三轮复跑暴露「滑动屏幕」「手动输入方位/方位角」「屏幕两侧」，
    其中“手动输入方位”是从技能正文照抄的。这里钉住源头：常驻提示词必须是
    白名单，而不是让模型自由想象。
    """

    def test_prompt_whitelists_verified_controls(self):
        assert "本机界面事实（只许讲这些）" in SYSTEM_PROMPT
        assert "准星下方显示星名 + 距中心角距" in SYSTEM_PROMPT
        assert "「左 5°」「右 5°」「复位」" in SYSTEM_PROMPT
        assert "60°→25°→8°" in SYSTEM_PROMPT
        assert "底部一行" in SYSTEM_PROMPT
        # 入口图标已按 sky_page.dart 的 IconButton 核实（Icons.explore +
        # tooltip「指天视图」），写进白名单，避免模型猜成别的图标。
        assert "顶部右上角" in SYSTEM_PROMPT
        assert "罗盘样式" in SYSTEM_PROMPT
        assert "提示文字「指天视图」" in SYSTEM_PROMPT

    def test_prompt_forbids_inventing_unlisted_ui(self):
        assert "以实际界面为准" in SYSTEM_PROMPT
        assert "不得编造按钮、手势、图标" in SYSTEM_PROMPT
        assert "输入框或提示语" in SYSTEM_PROMPT

    def test_sky_query_routes_to_whats_here_not_pointing_check(self):
        assert "`sky_whats_here`" in SYSTEM_PROMPT
        assert "不要用 `sky_pointing_check`" in SYSTEM_PROMPT
        assert "不会自动切换" in SYSTEM_PROMPT


class TestGnssInjectionHelpers:
    """真实模型探针的 GNSS 注入：站址写进 agent.observer，坐标不打印。"""

    def test_fix_from_position_keeps_fields_without_coords_leak(self):
        mod = _load_smoke()
        pos = SimpleNamespace(
            lat=1.5, lon=2.5, alt=30.0,
            utc_time="120000.00",
            utc_datetime="2026-11-22T12:00:00+00:00",
            date="221126", sats=9, hdop=1.3,
            antenna="open", timestamp=123.0)
        fix = mod.fix_from_position(pos, "COM10")
        assert fix["source"] == "real"
        assert fix["latitude"] == 1.5
        assert fix["longitude"] == 2.5
        assert fix["altitude_m"] == 30.0
        assert fix["satellites"] == 9
        assert fix["hdop"] == 1.3
        assert fix["antenna"] == "open"
        assert fix["port"] == "COM10"

    def test_apply_fix_to_agent_sets_observer_or_refuses(self):
        from mbdsdr_ai.astronomy import Observer

        mod = _load_smoke()
        agent = SimpleNamespace(observer=Observer())
        assert mod.apply_fix_to_agent(
            agent, {"latitude": 1.0, "longitude": 2.0, "altitude_m": 3.0})
        assert agent.observer.latitude_deg == 1.0
        assert agent.observer.longitude_deg == 2.0
        assert agent.observer.height_m == 3.0
        # 无坐标不得把 observer 写成 (0,0)
        assert mod.apply_fix_to_agent(agent, {"latitude": None}) is False
        assert agent.observer.latitude_deg == 1.0


class TestSmokeHeuristics:
    """探针的软观察要能指出「写工具 / 一次灌太多 / 对专家讲基础」。"""

    def test_write_tool_is_flagged_even_with_plain_text(self):
        mod = _load_smoke()
        notes = mod._observations("novice", [{
            "content": "好的。",
            "tool_calls": ["set_frequency"],
        }])
        assert any("写工具" in n for n in notes)

    def test_long_answer_and_many_questions_are_flagged(self):
        mod = _load_smoke()
        notes = mod._observations("novice", [{
            "content": "问？" * 3 + "很长" * 700,
            "tool_calls": [],
        }])
        assert any("回复" in n for n in notes)
        assert any("连续提问" in n for n in notes)

    def test_basics_to_expert_are_flagged(self):
        mod = _load_smoke()
        notes = mod._observations("expert", [{
            "content": "先了解什么是 SDR，基础很重要。",
            "tool_calls": [],
        }])
        assert any("基础词" in n for n in notes)

    def test_external_app_and_invented_token_are_flagged(self):
        mod = _load_smoke()
        notes = mod._observations("novice", [{
            "content": "安装 Star Walk 2，然后输入 arm_manual 获取 confirm_token。",
            "tool_calls": [],
        }])
        assert any("外部 App" in n for n in notes)
        assert any("令牌" in n for n in notes)

    def test_numbered_list_is_flagged_but_frequency_is_not(self):
        mod = _load_smoke()
        numbered = mod._observations("novice", [{
            "content": "1. 打开相机\n2. 对准天空\n3. 等十秒",
            "tool_calls": [],
        }])
        assert any("编号清单" in n for n in numbered)
        freq = mod._observations("novice", [{
            "content": "把频率调到 145.500 MHz，带宽 12.5 kHz。",
            "tool_calls": [],
        }])
        assert not any("编号清单" in n for n in freq)

    def test_expert_write_tool_is_observed_not_rejected(self):
        mod = _load_smoke()
        notes = mod._observations("expert", [{
            "content": "直接说：145.500 MHz、NFM、带宽 12 kHz。",
            "tool_calls": ["set_frequency", "set_mode"],
        }])
        # set_frequency/set_mode 在写工具表里，但这是专家直接给参数的场景：
        # 探针只记录，不判红；这里验证记录本身可解释。
        assert any("写工具" in n for n in notes)

    def test_unverified_ui_affordances_are_flagged(self):
        """真实 8B 编造过滑动/输入方位/罗盘图标/自动追踪；探针要能看见。"""
        mod = _load_smoke()
        notes = mod._observations("novice", [{
            "content": "左右滑动屏幕，或手动输入方位角，看到罗盘图标后自动追踪。",
            "tool_calls": [],
        }])
        assert any("UI 交互" in n for n in notes)

    def test_real_ui_actions_are_not_flagged(self):
        mod = _load_smoke()
        notes = mod._observations("novice", [{
            "content": "点击底部「左 5°」「右 5°」按钮，转动手机对准亮星。",
            "tool_calls": [],
        }])
        assert not any("UI 交互" in n for n in notes)

    def test_personas_cover_novice_expert_and_direct(self):
        mod = _load_smoke()
        assert set(mod.PERSONAS) == {"novice", "expert", "direct"}
        assert "天文望远镜" in mod.PERSONAS["novice"][0]
        assert "145.500" in mod.PERSONAS["expert"][0]
        assert "你直接来" in mod.PERSONAS["direct"][1]
