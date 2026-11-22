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

from mbdsdr_ai.context_manager import SYSTEM_PROMPT
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

    def test_expert_write_tool_is_observed_not_rejected(self):
        mod = _load_smoke()
        notes = mod._observations("expert", [{
            "content": "直接说：145.500 MHz、NFM、带宽 12 kHz。",
            "tool_calls": ["set_frequency", "set_mode"],
        }])
        # set_frequency/set_mode 在写工具表里，但这是专家直接给参数的场景：
        # 探针只记录，不判红；这里验证记录本身可解释。
        assert any("写工具" in n for n in notes)

    def test_personas_cover_novice_expert_and_direct(self):
        mod = _load_smoke()
        assert set(mod.PERSONAS) == {"novice", "expert", "direct"}
        assert "天文望远镜" in mod.PERSONAS["novice"][0]
        assert "145.500" in mod.PERSONAS["expert"][0]
        assert "你直接来" in mod.PERSONAS["direct"][1]
