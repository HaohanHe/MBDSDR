# SPDX-License-Identifier: MIT
"""
工具集预算与选择准确性（P7.1 / P7.3）钉扎测试
=============================================

背景：生产注册表 495 个工具，schema 全量约 218k 字符（启发式 ~9 万 token），
远超常见 32k 窗口。本轮把「发给模型的候选集」做成确定性预算裁剪：
核心工具恒留、按请求关键词与类目打分、超预算即裁掉、被裁工具可用 list_tools
重新发现。

本文件钉住：
  * 预算/打分常量不许悄悄放宽（MIN_SCORE 只能升不能降）；
  * 核心工具在极小预算下仍保留；
  * 同一请求的候选集完全确定；
  * 相关工具必留、无关危险工具不得混入；
  * 意图基准集准确率 100%（基准集扩容时只许提高，不许降）；
  * CI 无硬件、无真实模型也能跑。
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import pytest  # noqa: E402

from mbdsdr_ai import tool_budget  # noqa: E402
from mbdsdr_ai.tool_budget import (  # noqa: E402
    CORE_TOOLS,
    MIN_SCORE,
    select_tools,
    tool_budget_tokens,
)


# ---------------------------------------------------------------------------
# 常量钉扎（放宽即失败）
# ---------------------------------------------------------------------------
def test_budget_constants_pinned():
    assert tool_budget.DEFAULT_BUDGET_RATIO == 0.25
    assert tool_budget.MIN_BUDGET_TOKENS == 1000
    assert tool_budget.MIN_TOOLS == 8
    assert tool_budget.NAME_WEIGHT == 4
    assert tool_budget.CATEGORY_WEIGHT == 5
    assert tool_budget.DESC_WEIGHT == 1
    assert tool_budget.DESC_CAP == 3
    assert MIN_SCORE == 4
    assert CORE_TOOLS == ("list_tools", "context_status", "model_status", "tool_log")
    assert tool_budget_tokens(32768) == 8192
    assert tool_budget_tokens(2048) == 1000        # 25% 不足下限时取 1000
    assert tool_budget_tokens(0) == 1000


def test_intent_regression_gate_not_lowered():
    import ai_intent_regression as reg
    assert reg.MIN_ACCURACY == 0.95
    assert reg.DEFAULT_BUDGET == 8192


# ---------------------------------------------------------------------------
# 纯选择器行为
# ---------------------------------------------------------------------------
def _def(name: str, desc: str = "") -> dict:
    return {"type": "function",
            "function": {"name": name, "description": desc,
                         "parameters": {"type": "object", "properties": {}}}}


def test_core_survives_tiny_budget():
    defs = [_def("list_tools"), _def("context_status"), _def("model_status"),
            _def("tool_log"), _def("sdr_set_frequency", "设置接收频率")]
    sel = select_tools(defs, "把频率调到 145 MHz", budget_tokens=50)
    assert set(CORE_TOOLS) <= set(sel.kept_names)
    assert sel.trimmed is True
    assert "工具集预算" in sel.note


def test_selection_is_deterministic():
    defs = [_def(n) for n in ("list_tools", "sdr_record_start", "sdr_set_frequency",
                              "sdr_spectrum_peaks", "workflow_execute")]
    req = "看看频谱峰值"
    a = select_tools(defs, req, budget_tokens=300)
    b = select_tools(defs, req, budget_tokens=300)
    assert a.kept_names == b.kept_names
    assert a.tokens == b.tokens


def test_relevant_kept_and_trap_excluded():
    defs = [
        _def("list_tools"),
        _def("sdr_spectrum_peaks", "扫描频谱并给出峰值"),
        _def("spectrum_analyze_iq", "对 IQ 做频谱分析"),
        _def("sdr_record_start", "开始录制基带/音频。前置条件: 设备必须先已连接"),
        _def("sdr_connect", "连接 SDR 设备。所有频率/频谱/录制工具的前置条件"),
        _def("workflow_execute", "执行工作流"),
    ]
    sel = select_tools(defs, "帮我看一下 100MHz 附近的频谱峰值", budget_tokens=400)
    assert "sdr_spectrum_peaks" in sel.kept_names
    assert "sdr_record_start" not in sel.kept_names
    assert "workflow_execute" not in sel.kept_names


def test_empty_request_gets_fallback_tools():
    defs = [_def("list_tools")] + [_def(f"tool_{i}") for i in range(20)]
    sel = select_tools(defs, "", budget_tokens=2000)
    assert len(sel.kept_names) >= tool_budget.MIN_TOOLS
    assert len(sel.kept_names) <= len(defs)


def test_tokens_do_not_exceed_budget_unless_core_does():
    defs = [_def("list_tools", "x" * 200)] + [
        _def(f"sdr_tool_{i}", "频谱 扫描 峰值 瀑布") for i in range(40)]
    sel = select_tools(defs, "频谱峰值", budget_tokens=1500)
    assert sel.tokens <= 1500 + tool_budget.estimate_definitions_tokens(
        [_def("list_tools", "x" * 200)])   # 允许核心单条超限


# ---------------------------------------------------------------------------
# 真实注册表 + 意图基准集
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def real_catalog():
    from mbdsdr_ai.agent import MBDSDRAgent
    from mbdsdr_ai.config import AgentConfig

    agent = MBDSDRAgent(AgentConfig(api_key="sk-test"))
    defs = agent.tool_registry.get_tool_definitions(language="zh")
    cats = {n: str((m or {}).get("category", ""))
            for n, m in agent.tool_registry.tools.items()}
    return agent.tool_registry.tools, defs, cats


def test_registry_schema_fits_budget_after_trim(real_catalog):
    tools, defs, cats = real_catalog
    assert len(tools) >= 400                     # 全量确实远超窗口
    full = tool_budget.estimate_definitions_tokens(defs)
    assert full > 40000
    sel = select_tools(defs, "把接收频率调到 145.800 MHz", budget_tokens=8192,
                       categories=cats)
    assert sel.trimmed is True
    assert sel.tokens <= 8192
    assert "sdr_set_frequency" in sel.kept_names
    assert "list_tools" in sel.kept_names


def test_intent_corpus_accuracy_full(real_catalog):
    import ai_intent_regression as reg
    tools, defs, cats = real_catalog
    corpus = reg.load_corpus()
    report = reg.evaluate_cases(corpus["cases"], tools, defs,
                                reg.DEFAULT_BUDGET, cats)
    assert report["total"] >= 40
    assert report["accuracy"] == 1.0
    assert report["failures"] == []
    # 候选集规模必须显著小于全量：平均 40 个左右、token 在预算内
    kept = [c["kept_count"] for c in report["cases"]]
    assert max(kept) <= 60
    assert all(c["tokens"] <= reg.DEFAULT_BUDGET for c in report["cases"])


def test_corpus_has_domain_coverage():
    import ai_intent_regression as reg
    corpus = reg.load_corpus()
    cats = {c["category"] for c in corpus["cases"]}
    assert {"sky", "receive", "decode", "record", "device", "workflow"} <= cats
    ids = [c["id"] for c in corpus["cases"]]
    assert len(ids) == len(set(ids))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
