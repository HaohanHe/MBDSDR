# SPDX-License-Identifier: MIT
"""Agent 侧 Hook 发射测试（Sprint 4）。

_chat_impl 在真实节点通过 self._hook() 向 HookManager 事件总线发射生产事件，
把 hook_manager 从「对着空气喊话」变成活的观测层。本测试用真实 HookManager
挂通配钩子，断言一轮工具调用至少发出 AGENT_TOOL_CALL / AGENT_TOOL_RESULT /
AGENT_MESSAGE，且钩子异常绝不拖垮 chat。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from unittest.mock import patch  # noqa: E402

from mbdsdr_ai.hooks import HookManager, EventType  # noqa: E402
from test_agent_retry import _make_minimal_agent, _mock_model_responses  # noqa: E402


def _agent_with_hooks(tmp_path):
    agent, _ = _make_minimal_agent(None, "")
    agent.model_manager.chat_stream = _mock_model_responses("test_tool")
    agent.hook_manager = HookManager()
    # 会话日志指到临时目录，避免污染 ~/.mbdsdr
    from mbdsdr_ai.session_log import SessionLog
    agent._session_log = SessionLog(agent.conversation_id, directory=str(tmp_path))
    agent._session_log_conv = agent.conversation_id
    return agent


def test_chat_emits_tool_call_and_result_events(tmp_path):
    agent = _agent_with_hooks(tmp_path)
    seen = []
    agent.hook_manager.register("*", lambda e: seen.append(e.event_type))
    with patch.object(agent, "_cancel_pending", return_value=False):
        agent._chat_impl("打开频谱", max_tool_rounds=5)

    assert EventType.AGENT_TOOL_CALL in seen
    assert EventType.AGENT_TOOL_RESULT in seen
    assert EventType.AGENT_MESSAGE in seen


def test_tool_result_event_carries_outcome(tmp_path):
    agent = _agent_with_hooks(tmp_path)
    results = []
    agent.hook_manager.register(EventType.AGENT_TOOL_RESULT,
                                lambda e: results.append(e.data))
    with patch.object(agent, "_cancel_pending", return_value=False):
        agent._chat_impl("打开频谱", max_tool_rounds=5)

    assert results, "应至少发一条 tool_result 事件"
    r = results[0]
    assert r["tool"] == "test_tool"
    assert r["outcome"] in ("executed", "error")
    assert "ok" in r


def test_hook_exception_does_not_break_chat(tmp_path):
    """钩子自己炸，chat 主流程照常返回最终回复。"""
    agent = _agent_with_hooks(tmp_path)
    agent.hook_manager.register("*", lambda e: 1 / 0)  # 每个事件都炸
    with patch.object(agent, "_cancel_pending", return_value=False):
        result = agent._chat_impl("打开频谱", max_tool_rounds=5)
    assert result["content"] == "最终回复"


def test_error_event_emitted_on_model_failure(tmp_path):
    """模型调用失败 → 发 AGENT_ERROR。"""
    agent, _ = _make_minimal_agent(None, "")
    agent.hook_manager = HookManager()
    from mbdsdr_ai.session_log import SessionLog
    agent._session_log = SessionLog(agent.conversation_id, directory=str(tmp_path))
    agent._session_log_conv = agent.conversation_id

    def failing_stream(messages, tools=None, tool_choice=None):
        yield {"done": True, "success": False, "content": "",
               "tool_calls": [], "usage": {}, "error": "boom"}

    agent.model_manager.chat_stream = failing_stream
    errors = []
    agent.hook_manager.register(EventType.AGENT_ERROR,
                                lambda e: errors.append(e.data))
    with patch.object(agent, "_cancel_pending", return_value=False):
        agent._chat_impl("x", max_tool_rounds=3)
    assert errors and errors[0]["code"] == "boom"
