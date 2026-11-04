# SPDX-License-Identifier: MIT
"""
并发与取消（P7.4）
==================

钉住四件事：

  1. **并发不串台**：同一 Agent 同时只允许一个 chat；第二个请求立刻返回
     ``agent_busy``，不排队、不写上下文、不与第一个请求共享状态。
  2. **取消在安全点生效**：流式读取中、轮次之间、工具批次之间、工具重试
     等待前都会检查取消；已发出的 assistant(tool_calls) 仍会补写
     「已取消」的 tool 结果，保证历史仍是合法请求。
  3. **取消不等于成功**：返回 ``cancelled=True`` / ``interrupted=True``，
     不把半截输出冒充完整回复。
  4. **取消后不留后台任务**：流生成器被显式关闭，不再继续消费模型输出，
     也不再开启下一轮。

全部离线：假模型 + 线程事件，不联网。
"""
import os
import sys
import tempfile
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

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


def _tool_chain_ok(history):
    """每条带 tool_calls 的 assistant 消息，都必须有对应 id 的 tool 结果。"""
    wanted = set()
    for m in history:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            wanted |= {tc.get("id") for tc in m["tool_calls"]}
    got = {m.get("tool_call_id") for m in history if m.get("role") == "tool"}
    return wanted == got


# ---------------------------------------------------------------------------
# 1. 并发：第二个请求被明确拒绝，不写上下文
# ---------------------------------------------------------------------------
class _BlockingModel:
    """第一轮阻塞到 release 被 set，才返回最终回复。"""

    model = "test-model"

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def chat_stream(self, messages, tools=None, **kw):
        self.calls += 1
        self.entered.set()
        self.release.wait(5)
        yield {"done": True, "success": True, "content": "完成。", "tool_calls": []}

    def parse_tool_calls_from_text(self, *_a, **_k):
        return []


def test_second_concurrent_request_is_rejected_busy():
    cm = _cm()
    model = _BlockingModel()
    agent = _bare_agent(cm, model)
    out = {}

    t = threading.Thread(target=lambda: out.update(agent.chat("第一条请求")))
    t.start()
    assert model.entered.wait(3), "第一个请求应已进入模型调用"
    assert agent.is_busy() is True

    r2 = agent.chat("第二条请求")
    assert r2["error"] == "agent_busy"
    assert r2["busy"] is True
    assert r2["cancelled"] is False
    # 被拒请求不得写入共享上下文
    assert not any("第二条请求" in str(m.get("content", "")) for m in cm.history)

    model.release.set()
    t.join(5)
    assert out["error"] is None
    assert out["content"] == "完成。"
    assert agent.is_busy() is False
    # 只有第一个请求的 user 消息 + assistant 回复
    assert [m.get("role") for m in cm.history] == ["user", "assistant"]


# ---------------------------------------------------------------------------
# 2. 取消：轮次之间 / 工具批次之间
# ---------------------------------------------------------------------------
class _ToolLoopModel:
    """每轮返回一批工具调用，直到最后一轮返回最终回复。"""

    model = "test-model"

    def __init__(self, batch, rounds):
        self._batch = batch          # 每轮下发的 tool_calls 列表
        self._rounds = rounds
        self.calls = 0

    def chat_stream(self, messages, tools=None, **kw):
        self.calls += 1
        if self.calls >= self._rounds:
            yield {"done": True, "success": True, "content": "完成。", "tool_calls": []}
            return
        tcs = []
        for i, name in enumerate(self._batch):
            tcs.append({"id": f"c{self.calls}_{i}", "type": "function",
                        "function": {"name": name,
                                     "arguments": '{"note": "step"}'}})
        yield {"done": True, "success": True, "content": "", "tool_calls": tcs}

    def parse_tool_calls_from_text(self, *_a, **_k):
        return []


def test_cancel_between_tool_calls_stops_batch_and_keeps_chain():
    cm = _cm()
    model = _ToolLoopModel(batch=["demo_step", "after_step"], rounds=5)
    agent = _bare_agent(cm, model)
    executed = {"demo_step": 0, "after_step": 0}

    def on_demo(args):
        executed["demo_step"] += 1
        agent.request_cancel()      # 第一个工具执行后立刻请求取消
        return "ok"

    def on_after(args):
        executed["after_step"] += 1
        return "ok"

    agent.tool_registry.register(
        "demo_step", "记录一步",
        {"type": "object", "properties": {"note": {"type": "string"}}},
        on_demo, category="demo")
    agent.tool_registry.register(
        "after_step", "后续一步",
        {"type": "object", "properties": {"note": {"type": "string"}}},
        on_after, category="demo")

    r = agent.chat("连续做很多步", max_tool_rounds=5)

    assert r["cancelled"] is True
    assert r["interrupted"] is True
    assert r["error"] == "cancelled"
    # 取消后不再开新一轮模型调用；本批第二个工具不执行
    assert model.calls == 1
    assert executed == {"demo_step": 1, "after_step": 0}
    # 未执行的工具也有「已取消」回执，历史仍是合法请求链
    assert _tool_chain_ok(cm.history)
    cancelled_msgs = [m for m in cm.history
                      if m.get("role") == "tool" and "已取消" in (m.get("content") or "")]
    assert len(cancelled_msgs) == 1
    # 「取消」不是成功：没有把 cancelled 结果算成 success
    assert [x["success"] for x in r["tool_results"]] == [True, False]
    assert agent.is_busy() is False


def test_cancel_between_rounds_skips_next_model_call():
    cm = _cm()
    model = _ToolLoopModel(batch=["demo_step"], rounds=6)
    agent = _bare_agent(cm, model)
    agent.tool_registry.register(
        "demo_step", "记录一步",
        {"type": "object", "properties": {"note": {"type": "string"}}},
        lambda args: (agent.request_cancel(), "ok")[1], category="demo")

    r = agent.chat("连续做很多步", max_tool_rounds=6)
    assert r["cancelled"] is True
    assert model.calls == 1            # 取消后不再请求模型
    assert _tool_chain_ok(cm.history)


# ---------------------------------------------------------------------------
# 3. 取消：流式读取中（生成器必须被关闭）
# ---------------------------------------------------------------------------
class _StreamingModel:
    """持续吐字；测试在收到第一个 delta 时请求取消。"""

    model = "test-model"

    def __init__(self):
        self.closed = False
        self.yielded = 0

    def chat_stream(self, messages, tools=None, **kw):
        try:
            for i in range(60):
                self.yielded += 1
                yield {"done": False, "delta": f"块{i}"}
                time.sleep(0.005)
        finally:
            self.closed = True

    def parse_tool_calls_from_text(self, *_a, **_k):
        return []

def test_cancel_during_stream_closes_generator_and_reports_partial():
    cm = _cm()
    model = _StreamingModel()
    agent = _bare_agent(cm, model)

    r = agent.chat("讲一段长内容", on_delta=lambda _d: agent.request_cancel())

    assert r["cancelled"] is True and r["interrupted"] is True
    assert r["error"] == "cancelled"
    assert model.closed is True                 # 没有留在后台的流
    assert model.yielded <= 3                   # 取消后不再继续消费
    assert "已按请求取消" in r["content"]        # 半截内容如实标注未完成
    assert "本轮请求未完成" in r["content"]


# ---------------------------------------------------------------------------
# 4. 取消的生命周期：空闲取消是 no-op，下一次请求恢复
# ---------------------------------------------------------------------------
class _SimpleModel:
    model = "test-model"

    def __init__(self):
        self.calls = 0

    def chat_stream(self, messages, tools=None, **kw):
        self.calls += 1
        yield {"done": True, "success": True, "content": "完成。", "tool_calls": []}

    def parse_tool_calls_from_text(self, *_a, **_k):
        return []


def test_cancel_when_idle_is_noop_and_next_request_works():
    cm = _cm()
    agent = _bare_agent(cm, _SimpleModel())
    assert agent.request_cancel() is False       # 空闲时取消：no-op
    assert agent.is_busy() is False

    r = agent.chat("正常请求")
    assert r["error"] is None
    assert r["cancelled"] is False
    assert r["content"] == "完成。"
    # 取消标志不残留到下一次请求
    assert agent.is_busy() is False
    r2 = agent.chat("第二次请求")
    assert r2["error"] is None and r2["cancelled"] is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
