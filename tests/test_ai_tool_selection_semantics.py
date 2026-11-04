# SPDX-License-Identifier: MIT
"""工具选择的语义因果探针（P12）
================================

证明两件**可离线验证**的事（不依赖真实 LLM）：

  1. **候选集真的改变行为**：同一请求、同一提示词，预算从 8192 降到 1024，
     峰值专用工具被裁掉后，规则替身从「直接调用专用工具」变成「先 list_tools
     发现工具名」。
  2. **提示词真的改变行为**：同一请求、同一候选集，仅把 system prompt 里的
     「[工具集预算] … 先调用 list_tools」声明删掉，第一动作从 list_tools
     变成通用 sdr_spectrum_analyze。

做法：规则替身 `_rule_choose` 只消费**模型实际收到的** system prompt 与
候选 schema 名字（由 Agent 真实构建，不是手搓字符串），按固定偏好选择；
没有打分、没有语料准确率、没有「基准集」。

边界：这里证明的是「提示词与候选集是行为变量」这一接线事实；真实模型是否
按语义选对工具仍未验证，见 docs/limitations-register.md 的 L13。
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from mbdsdr_ai.tool_budget import select_tools  # noqa: E402

_REQUEST = "帮我看看频谱的峰值在哪里"
#: 规则替身的固定偏好顺序（专用 -> 次专用 -> 通用），刻意不用分数
_SPECIFIC_ORDER = ("sdr_spectrum_peaks", "sdr_spectrum_find_signals",
                   "sdr_spectrum_analyze")
_NOTE_MARK = "先调用 list_tools"


def _make_agent():
    from mbdsdr_ai import model_manager as _mm
    from mbdsdr_ai.agent import MBDSDRAgent
    from mbdsdr_ai.config import AgentConfig
    original = _mm.ModelManager.fetch_models
    _mm.ModelManager.fetch_models = lambda self, *a, **k: None  # 不发起网络请求
    try:
        return MBDSDRAgent(AgentConfig(api_key="sk-test", model="test-model"))
    finally:
        _mm.ModelManager.fetch_models = original


def _catalog(agent):
    defs = agent.tool_registry.get_tool_definitions(language="zh")
    cats = {n: str((m or {}).get("category", ""))
            for n, m in agent.tool_registry.tools.items()}
    return defs, cats


def _names(selection):
    return [d["function"]["name"] for d in selection.definitions]


def _rule_choose(request, candidate_names, system_prompt):
    """规则替身：按固定偏好消费候选集与提示词，不做打分。

    - 候选里有更专用的频谱工具就选它；
    - 没有专用工具时，看提示词是否声明「先调用 list_tools」：有则先发现，
      无则退通用 analyze。
    """
    if "频谱" not in request:
        raise AssertionError("本探针只覆盖频谱请求")
    for name in _SPECIFIC_ORDER[:2]:
        if name in candidate_names:
            return name
    if _NOTE_MARK in (system_prompt or ""):
        return "list_tools"
    assert "sdr_spectrum_analyze" in candidate_names
    return "sdr_spectrum_analyze"


class _CaptureModel:
    """不真调模型：只记录本轮实际收到的 messages 与 candidate schema。"""
    model = "test-model"

    def __init__(self):
        self.calls = []

    def chat_stream(self, messages, tools=None, **kw):
        self.calls.append({
            "messages": [dict(m) for m in (messages or [])],
            "tools": list(tools or []),
        })
        yield {"delta": "收到。"}
        yield {"done": True, "success": True, "content": "收到。",
               "tool_calls": [], "usage": {"total_tokens": 3}}

    def parse_tool_calls_from_text(self, *_a, **_k):
        return []


@pytest.fixture(scope="module")
def agent():
    return _make_agent()


@pytest.fixture(scope="module")
def catalog(agent):
    return _catalog(agent)


# ---------------------------------------------------------------------------
# 1. 候选集驱动：同一请求，预算不同 -> 选择不同
# ---------------------------------------------------------------------------
def test_candidate_set_changes_choice(catalog):
    defs, cats = catalog
    wide = select_tools(defs, _REQUEST, budget_tokens=8192, categories=cats)
    narrow = select_tools(defs, _REQUEST, budget_tokens=1024, categories=cats)

    wide_names, narrow_names = _names(wide), _names(narrow)
    assert "sdr_spectrum_peaks" in wide_names
    assert "sdr_spectrum_peaks" not in narrow_names

    prompt = "[工具集预算] " + wide.note
    assert _rule_choose(_REQUEST, wide_names, prompt) == "sdr_spectrum_peaks"
    assert _rule_choose(_REQUEST, narrow_names, prompt) == "list_tools"


def test_all_rule_choices_are_registered(agent, catalog):
    """规则替身可能选到的名字必须真实注册，避免探针自说自话。"""
    defs, cats = catalog
    names = set(agent.tool_registry.get_tool_names())
    for budget in (1024, 2048, 8192):
        sel = select_tools(defs, _REQUEST, budget_tokens=budget, categories=cats)
        choice = _rule_choose(_REQUEST, _names(sel), sel.note)
        assert choice in names
        assert choice in _names(sel) or choice == "list_tools"


# ---------------------------------------------------------------------------
# 2. 提示词驱动：同一候选集，仅裁剪声明有无 -> 第一动作不同
# ---------------------------------------------------------------------------
def test_prompt_note_changes_first_action(agent, catalog, monkeypatch):
    defs, cats = catalog
    budget = 1024
    selection = select_tools(defs, _REQUEST, budget_tokens=budget, categories=cats)
    candidate_names = _names(selection)
    assert "sdr_spectrum_peaks" not in candidate_names

    # 走真实 Agent 路径：让它按同一请求/预算构建 system prompt 与候选 schema
    import mbdsdr_ai.agent as agent_mod
    monkeypatch.setattr(agent_mod, "tool_budget_tokens", lambda _n: budget)
    model = _CaptureModel()
    agent.model_manager = model
    result = agent.chat(_REQUEST)
    assert result.get("error") in (None, "")
    assert len(model.calls) == 1

    sent = model.calls[0]
    prompt = "\n".join(str(m.get("content") or "") for m in sent["messages"])
    sent_names = [(t.get("function") or {}).get("name") for t in sent["tools"]]
    assert sent_names == candidate_names          # 发的就是裁剪后的候选集
    assert _NOTE_MARK in prompt                   # 裁剪声明确实进了提示词

    assert _rule_choose(_REQUEST, sent_names, prompt) == "list_tools"
    without_note = prompt.replace("\n\n" + selection.note, "")
    assert _NOTE_MARK not in without_note
    assert _rule_choose(_REQUEST, sent_names, without_note) == "sdr_spectrum_analyze"


# ---------------------------------------------------------------------------
# 3. 录制回放：把「不同候选集应有的不同选择」钉成表
# ---------------------------------------------------------------------------
def test_replay_table(catalog):
    fixture_path = os.path.join(_ROOT, "tests", "fixtures",
                                "ai_tool_selection_replay.json")
    with open(fixture_path, encoding="utf-8") as f:
        replay = json.load(f)
    assert replay["request"] == _REQUEST

    defs, cats = catalog
    for case in replay["cases"]:
        sel = select_tools(defs, replay["request"],
                           budget_tokens=case["budget_tokens"], categories=cats)
        names = _names(sel)
        assert sel.trimmed is case["expect_trimmed"], case["id"]
        assert ("sdr_spectrum_peaks" in names) is case["expect_has_peaks"], case["id"]
        choice = _rule_choose(replay["request"], names, sel.note)
        assert choice == case["expect_tool"], (case["id"], choice)
