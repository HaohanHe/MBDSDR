# SPDX-License-Identifier: MIT
"""
上下文预算与状态保全（P7.3）
============================

钉住三件事：

  1. **预算要算对的**：工具 schema 必须计入上下文 token（此前 builtin 工具
     注册后从未 set_tool_definitions，预算是系统性低估的）。
  2. **增长有界**：长会话 + 多轮工具调用后，压缩把总量压回窗口内，
     且消息条数有界，不会无限膨胀。
  3. **关键状态不假记得**：压缩时状态快照无条件拼进摘要；若快照真的丢了，
     state_health 必须报 missing 并给出「先用工具重新读取」的提示，Agent 会
     把该提示注入上下文。

全部离线：假模型 + 合成工具，不联网。
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from mbdsdr_ai.context_manager import ContextManager  # noqa: E402
from mbdsdr_ai.conversation import ConversationStore  # noqa: E402
from mbdsdr_ai.config import AgentConfig  # noqa: E402
from mbdsdr_ai.tool_registry import ToolRegistry  # noqa: E402


def _cm(max_tokens=2000, threshold=0.5, target=0.4, recent=1,
        on_compaction=None, tool_max=200):
    return ContextManager(
        max_context_tokens=max_tokens, compaction_threshold=threshold,
        compaction_target_ratio=target, system_prompt="你是测试助手。",
        tool_output_max_chars=tool_max, on_compaction=on_compaction,
        recent_turns_keep=recent)


# ---------------------------------------------------------------------------
# 1. 工具 schema 计入预算
# ---------------------------------------------------------------------------
def test_tool_definitions_counted_in_budget():
    cm = _cm(max_tokens=8192, threshold=0.9)
    base = cm.get_total_tokens()
    defs = [{"type": "function",
             "function": {"name": "sdr_set_frequency",
                          "description": "设置接收中心频率" * 10,
                          "parameters": {"type": "object", "properties": {}}}}]
    cm.set_tool_definitions(defs)
    assert cm.get_total_tokens() > base
    assert cm.get_system_tokens() >= cm.get_total_tokens() - cm.get_history_tokens()


def test_more_tools_more_tokens_and_cache_invalidates():
    cm = _cm(max_tokens=8192, threshold=0.9)
    one = [{"type": "function", "function": {"name": "a", "description": "x" * 40,
                                             "parameters": {}}}]
    two = one + [{"type": "function",
                  "function": {"name": "b", "description": "y" * 40, "parameters": {}}}]
    cm.set_tool_definitions(one)
    t1 = cm.get_system_tokens()
    cm.set_tool_definitions(two)
    t2 = cm.get_system_tokens()
    assert t2 > t1        # 缓存必须随工具集变化失效


# ---------------------------------------------------------------------------
# 2. 增长有界（Agent 主循环 + 压缩）
# ---------------------------------------------------------------------------
class _FakeModel:
    """每次返回一个工具调用，最后返回最终答复。"""

    model = "test-model"

    def __init__(self, tool_name, total_rounds, arguments='{"note": "step"}'):
        self._tool = tool_name
        self._args = arguments
        self._rounds = total_rounds
        self.calls = 0
        self.max_prompt_tokens = 0

    def chat_stream(self, messages, tools=None, **kw):
        self.calls += 1
        self.max_prompt_tokens = max(
            self.max_prompt_tokens, sum(len(str(m.get("content"))) for m in messages))
        if self.calls >= self._rounds:
            yield {"done": True, "success": True, "content": "完成。", "tool_calls": []}
            return
        yield {"done": True, "success": True, "content": "",
               "tool_calls": [{"id": f"c{self.calls}", "type": "function",
                               "function": {"name": self._tool,
                                            "arguments": self._args}}]}

    def parse_tool_calls_from_text(self, *_a, **_k):
        return []


def _bare_agent(cm, model):
    from mbdsdr_ai.agent import MBDSDRAgent
    a = object.__new__(MBDSDRAgent)
    a.config = AgentConfig(api_key="sk-test", model="test-model")
    a.context_manager = cm
    a.model_manager = model
    a.tool_registry = ToolRegistry()
    a.tool_registry.register(
        "demo_step", "记录一步",
        {"type": "object", "properties": {"note": {"type": "string"}}},
        lambda args: "ok", category="demo")
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


def test_long_session_growth_is_bounded():
    # 窗口取 4000：Agent 用的是模块级 SYSTEM_PROMPT（约 1.5k token），
    # 系统提示词 + 工具 schema 属于不可再压的固定开销；若窗口比它还小，
    # 无论历史怎么压总量都必然超窗，那是窗口设置问题而非裁剪失效。
    # 这里用贴近真实比例的窗口（系统开销约占 40%），验证历史被压回预算内。
    def run(rounds):
        cm = _cm(max_tokens=4000, threshold=0.6, target=0.45, recent=1,
                 on_compaction=lambda text, prev=None: "早期历史摘要",
                 tool_max=120)
        agent = _bare_agent(cm, _FakeModel("demo_step", total_rounds=rounds))
        r = agent.chat("连续做很多步", max_tool_rounds=rounds)
        assert r["error"] is None
        return cm, r

    cm, r = run(240)
    assert cm.compaction_count >= 1
    report = cm.last_compaction_report
    assert report and report["removed_messages"] > 0
    # 增长有界：总量不随轮数增长，且不超窗口
    assert cm.get_total_tokens() <= cm.max_context_tokens
    # 裁剪策略可解释：保留区不超过窗口硬上限（硬上限已扣除摘要预留）；
    # 唯一允许的越界是工具链完整性修正补回的一条 assistant(tool_calls)。
    assert report["kept_tokens"] <= report["hard_limit_tokens"] + 200
    assert (report["kept_tokens"] <= report["available_for_history"]
            or report["kept_over_soft_budget"])
    assert report["state_missing"] == []
    # 再跑更长的会话：总量不涨（有界性来自硬上限，而非轮数）
    cm2, _ = run(480)
    assert cm2.get_total_tokens() <= cm.max_context_tokens
    assert abs(cm2.get_total_tokens() - cm.get_total_tokens()) < 50


# ---------------------------------------------------------------------------
# 3. 状态保全与丢失检测
# ---------------------------------------------------------------------------
def test_compaction_preserves_state_even_with_bad_summary():
    cm = _cm(max_tokens=600, threshold=0.5, target=0.4, recent=1,
             on_compaction=lambda text, prev=None: "摘要失败但不丢状态",
             tool_max=100)
    cm.set_state("frequency_hz", 145800000, source="sdr_set_frequency")
    cm.set_state("mode", "nfm", source="sdr_set_demod")
    for i in range(12):
        cm.add_user_message(f"第 {i} 轮")
        cm.add_assistant_message("好的" * 20)
    assert cm.needs_compaction()
    assert cm.compact() is True
    summary_msg = cm.history[0]["content"]
    assert "[上下文压缩摘要" in summary_msg
    assert "[会话状态（跨压缩保留）]" in summary_msg
    assert "frequency_hz=145800000" in summary_msg
    assert 'mode="nfm"' in summary_msg
    assert cm.last_compaction_report["state_preserved"] == ["frequency_hz", "mode"]
    assert cm.state_health()["missing"] == []


def test_state_loss_detected_with_reacquire_hint():
    cm = _cm()
    cm.set_state("frequency_hz", 100e6, source="sdr_set_frequency")
    assert cm.state_health()["missing"] == []
    # 模拟状态快照被外部清掉（例如恢复会话只恢复了摘要）
    cm.state_snapshot.clear()
    health = cm.state_health()
    assert health["missing"] == ["frequency_hz"]
    assert "重新读取" in health["reacquire_hint"]
    # 显式要求某个从未登记过的键，也应按丢失处理
    assert cm.state_health(["sample_rate"])["missing"] == ["sample_rate"]


def test_agent_injects_reacquire_hint_after_compaction():
    cm = _cm(max_tokens=600, threshold=0.5, target=0.4, recent=1,
             on_compaction=lambda text, prev=None: "摘要",
             tool_max=100)
    agent = _bare_agent(cm, _FakeModel("demo_step", total_rounds=5))
    cm.set_state("mode", "nfm")
    cm.state_snapshot.clear()          # 模拟状态真的丢了，meta 还在
    agent._check_state_after_compaction()
    last = cm.history[-1]
    assert last["role"] == "system"
    assert "状态告警" in last["content"] and "mode" in last["content"]


def test_agent_captures_state_from_successful_tool():
    cm = _cm(max_tokens=4096, threshold=0.99)
    reg_holder = {}

    def build():
        a = _bare_agent(cm, _FakeModel("sdr_set_frequency", total_rounds=2,
                                       arguments='{"frequency_hz": 145800000}'))
        reg_holder["agent"] = a
        return a

    agent = build()
    # 换成真正的「设置频率」工具
    agent.tool_registry = ToolRegistry()
    agent.tool_registry.register(
        "sdr_set_frequency", "设置中心频率",
        {"type": "object", "properties": {"frequency_hz": {"type": "number"}}},
        lambda args: "已设置", category="sdr_frequency")
    r = agent.chat("把频率调到 145.8 MHz", max_tool_rounds=2)
    assert r["tool_results"][0]["success"] is True
    assert cm.state_snapshot["frequency_hz"] == 145.8e6
    assert cm.state_meta["frequency_hz"]["source"] == "sdr_set_frequency"


def test_clear_history_clears_state():
    cm = _cm()
    cm.set_state("frequency_hz", 100e6)
    cm.clear_history()
    assert cm.state_snapshot == {} and cm.state_meta == {}
    assert cm.state_health()["missing"] == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
