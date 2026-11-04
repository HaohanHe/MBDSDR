# SPDX-License-Identifier: MIT
"""
延迟与成本观测（P7.5）
======================

钉住：

  1. 没有数据时明确 `available=False` / `no_data`，不编造数字。
  2. token 优先用模型返回的 usage；缺失时按字符估算并标 `estimated`。
  3. 逐轮模型耗时、工具耗时、工具调用次数是真实墙钟/计数。
  4. by_tool 汇总调用与失败次数。
  5. 指标缓冲有界（环形，上限可配置）。

全部离线：假模型 + 假工具，不联网。
"""
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from mbdsdr_ai.agent import _percentile  # noqa: E402
from mbdsdr_ai.config import AgentConfig  # noqa: E402
from mbdsdr_ai.context_manager import ContextManager  # noqa: E402
from mbdsdr_ai.conversation import ConversationStore  # noqa: E402
from mbdsdr_ai.tool_registry import ToolRegistry  # noqa: E402


def _cm():
    return ContextManager(
        max_context_tokens=8192, compaction_threshold=0.99,
        compaction_target_ratio=0.5, system_prompt="你是测试助手。",
        tool_output_max_chars=200, recent_turns_keep=2)


def _bare_agent(cm, model):
    from mbdsdr_ai.agent import MBDSDRAgent
    a = object.__new__(MBDSDRAgent)
    a.config = AgentConfig(api_key="sk-test", model="test-model")
    a.context_manager = cm
    a.model_manager = model
    a.tool_registry = ToolRegistry()
    a.memory = type("M", (), {"build_memory_context": lambda self, q: "",
                              "forget_old": lambda self: None})()
    a._repeat_key = None
    a._repeat_count = 0
    a._mcp_client = None
    a.last_tool_selection = None
    a.total_agent_calls = 0
    a.last_error = None
    a.conversation_store = ConversationStore(tempfile.mkdtemp(prefix="mbdsdr_ai_test_"))
    return a


class _UsageModel:
    """直接返回带 usage 的最终回复。"""
    model = "test-model"

    def __init__(self, usage=None, content="完成。"):
        self._usage = usage
        self._content = content
        self.calls = 0

    def chat_stream(self, messages, tools=None, **kw):
        self.calls += 1
        ev = {"done": True, "success": True, "content": self._content,
              "tool_calls": []}
        if self._usage is not None:
            ev["usage"] = dict(self._usage)
        yield ev

    def parse_tool_calls_from_text(self, *_a, **_k):
        return []


class _ToolThenDoneModel:
    """第一轮发起一次工具调用，第二轮返回最终回复。"""
    model = "test-model"

    def __init__(self, usage=None):
        self._usage = usage
        self.calls = 0

    def chat_stream(self, messages, tools=None, **kw):
        self.calls += 1
        ev = {"done": True, "success": True, "content": "", "tool_calls": []}
        if self.calls == 1:
            ev["tool_calls"] = [{"id": "c1", "type": "function",
                                 "function": {"name": "slow_step",
                                              "arguments": '{"note": "x"}'}}]
        else:
            ev["content"] = "完成。"
        if self._usage is not None:
            ev["usage"] = dict(self._usage)
        yield ev

    def parse_tool_calls_from_text(self, *_a, **_k):
        return []


# ---------------------------------------------------------------------------
# 1. 无数据：明确 no_data，不编造
# ---------------------------------------------------------------------------
def test_metrics_without_data_reports_no_data():
    agent = _bare_agent(_cm(), _UsageModel())
    m = agent.get_ai_metrics()
    assert m["available"] is False
    assert m["requests"] == 0
    assert "no_data" in m["note"]
    assert m["window"] == 0


# ---------------------------------------------------------------------------
# 2. 真实 usage 原样统计，不估算
# ---------------------------------------------------------------------------
def test_metrics_use_real_usage_and_latency():
    cm = _cm()
    agent = _bare_agent(cm, _UsageModel(
        usage={"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}))
    r = agent.chat("你好")
    assert r["error"] is None
    assert r["usage"]["total_tokens"] == 150
    assert r["metrics"]["estimated"] is False

    m = agent.get_ai_metrics()
    assert m["available"] is True and m["requests"] == 1
    assert m["totals"]["prompt_tokens"] == 120
    assert m["totals"]["completion_tokens"] == 30
    assert m["totals"]["total_tokens"] == 150
    assert m["totals"]["estimated_requests"] == 0
    assert m["totals"]["rounds"] == 1
    # 耗时是真实墙钟（非 None、非负）
    assert m["latency_ms"]["requests"]["p50"] >= 0
    assert m["latency_ms"]["model_round"]["p50"] >= 0
    assert m["last"]["rounds_detail"][0]["prompt_tokens"] == 120


# ---------------------------------------------------------------------------
# 3. 无 usage 时按字符估算并如实标记
# ---------------------------------------------------------------------------
def test_metrics_mark_estimated_when_usage_missing():
    agent = _bare_agent(_cm(), _UsageModel(content="这是一段用来估算 token 的回复。"))
    r = agent.chat("你好")
    assert r["usage"]["estimated"] is True
    assert r["usage"]["total_tokens"] > 0
    m = agent.get_ai_metrics()
    assert m["totals"]["estimated_requests"] == 1
    assert m["last"]["estimated"] is True
    assert "estimated" in m["note"]


# ---------------------------------------------------------------------------
# 4. 逐轮工具耗时与 by_tool 汇总
# ---------------------------------------------------------------------------
def test_metrics_track_tool_latency_and_by_tool():
    cm = _cm()
    agent = _bare_agent(cm, _ToolThenDoneModel(
        usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}))

    def slow_handler(_args):
        time.sleep(0.02)
        return "ok"

    agent.tool_registry.register(
        "slow_step", "慢工具",
        {"type": "object", "properties": {"note": {"type": "string"}}},
        slow_handler, category="demo")

    r = agent.chat("跑一步", max_tool_rounds=2)
    assert r["error"] is None
    assert r["metrics"]["rounds"] == 2      # 工具轮 + 最终轮
    assert r["metrics"]["tool_calls"] == 1

    first = r["metrics"]["rounds_detail"][0]
    assert first["tool_calls"] == 1
    assert first["tool_latency_ms"] >= 15   # 实测墙钟，handler sleep 20ms
    assert r["metrics"]["rounds_detail"][1]["tool_calls"] == 0

    m = agent.get_ai_metrics()
    assert m["by_tool"]["slow_step"] == {"calls": 1, "failures": 0}
    assert m["totals"]["tool_calls"] == 1
    assert m["totals"]["rounds"] == 2


# ---------------------------------------------------------------------------
# 5. 缓冲有界 + busy 拒单也计数
# ---------------------------------------------------------------------------
def test_metrics_window_is_bounded_and_busy_counted():
    cm = _cm()
    agent = _bare_agent(cm, _UsageModel(content="好"))
    agent.metrics_history_limit = 3

    for i in range(5):
        assert agent.chat(f"第{i}次")["error"] is None
    m = agent.get_ai_metrics()
    assert m["window"] == 3
    assert m["requests"] == 3
    assert m["totals"]["rounds"] == 3

    # 手工占住锁 → 第二个请求被拒，busy_rejections 真实 +1
    agent._ensure_concurrency_state()
    assert agent._chat_lock.acquire(blocking=False)
    try:
        busy = agent.chat("并发请求")
    finally:
        agent._chat_lock.release()
    assert busy["error"] == "agent_busy"
    assert agent.get_ai_metrics()["busy_rejections"] == 1


# ---------------------------------------------------------------------------
# 6. 分位数工具：空列表返回 None，不是 0
# ---------------------------------------------------------------------------
def test_percentile_returns_none_for_empty_and_interpolates():
    assert _percentile([], 0.5) is None
    assert _percentile([10], 0.5) == 10
    assert _percentile([10, 20, 30], 0.5) == 20
    assert _percentile([10, 20, 30, 40], 1.0) == 40


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
