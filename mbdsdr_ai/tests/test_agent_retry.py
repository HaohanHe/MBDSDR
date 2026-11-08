# SPDX-License-Identifier: MIT
"""重试判据测试：error_catalog.retryable 驱动，不再猜子串。

验证：
1. retryable=True 的错误码触发重试（handler 被调多次）
2. retryable=False 的错误码不触发重试（handler 只调一次）
3. 无错误码（空串）不触发重试
"""
import os
import sys
import threading
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from mbdsdr_ai.error_catalog import retryable  # noqa: E402
from mbdsdr_ai.tool_registry import ToolRegistry, ToolResult  # noqa: E402


# ── error_catalog.retryable 单元测试 ──────────────────────────────

class TestRetryableCatalog:
    """error_catalog 中已登记码的 retryable 字段符合语义。"""

    @pytest.mark.parametrize("code", [
        "tool_not_found",
        "tool_unavailable",
        "invalid_json_arguments",
        "missing_required_params",
        "invalid_arguments_type",
        "manual_gate_required",
    ])
    def test_non_retryable_codes(self, code):
        assert retryable(code) is False, f"{code} 应为不可重试"

    @pytest.mark.parametrize("code", [
        "device_busy",
        "usb_disconnected",
        "link_timeout",
        "agent_busy",
    ])
    def test_retryable_codes(self, code):
        assert retryable(code) is True, f"{code} 应为可重试"

    def test_unknown_code_not_retryable(self):
        assert retryable("some_random_unknown_code") is False

    def test_exception_family_is_retryable(self):
        assert retryable("exception:ValueError") is True


# ── Agent 重试行为集成测试 ────────────────────────────────────────

def _make_minimal_agent(tool_handler, error_code):
    """构造最小 agent 用于测试 _chat_impl 的重试逻辑。

    tool_handler: 每次被调时返回 ToolResult(success=False, error=error_code)
    """
    from mbdsdr_ai.agent import MBDSDRAgent
    from mbdsdr_ai.config import AgentConfig
    from mbdsdr_ai.context_manager import ContextManager, SYSTEM_PROMPT

    agent = object.__new__(MBDSDRAgent)
    agent.config = AgentConfig(api_key="test-key-not-real")
    agent.context_manager = ContextManager(
        max_context_tokens=8192,
        system_prompt=SYSTEM_PROMPT,
    )
    agent.model_manager = MagicMock()
    agent.model_manager.parse_tool_calls_from_text.return_value = None
    agent.model_manager.model = "test-model"
    agent.tool_registry = ToolRegistry()
    agent.memory = MagicMock()
    agent.memory.build_memory_context.return_value = ""
    agent.hook_manager = MagicMock()
    agent.hook_manager.emit.return_value = []
    agent.total_agent_calls = 0
    agent.last_error = None
    agent.last_tool_selection = None
    agent._metrics_history = []
    agent._busy_rejections = 0
    agent.metrics_history_limit = 50
    agent._repeat_key = None
    agent._repeat_count = 0
    agent._mcp_client = None
    agent.conversation_store = MagicMock()
    agent.conversation_id = "conv_test"
    agent._cancel_event = threading.Event()
    agent._chat_active = False
    agent._chat_lock = threading.Lock()
    agent.guardian = MagicMock()
    agent.workflow_engine = MagicMock()
    agent.scheduler = MagicMock()
    agent.subagent_manager = MagicMock()
    agent.plugin_manager = MagicMock()
    agent.orchestrator = MagicMock()
    agent.code_editor = MagicMock()
    agent.observer = MagicMock()
    agent.amr_classifier = MagicMock()
    agent.evolution = None
    agent.pose_fusion = MagicMock()
    agent.ar_projector = MagicMock()
    agent.workflow_recorder = MagicMock()
    agent.file_tracker = MagicMock()
    agent.llm_judge = MagicMock()
    agent.self_learning = MagicMock()
    agent.evolution = None

    call_count = {"n": 0}

    def counting_handler(args):
        call_count["n"] += 1
        return ToolResult(
            success=False,
            content=f"失败（第 {call_count['n']} 次）",
            tool_name="test_tool",
            args=args,
            error=error_code,
        )

    agent.tool_registry.register(
        name="test_tool",
        description="测试用工具",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=counting_handler,
    )

    return agent, call_count


def _mock_model_responses(tool_name="test_tool"):
    """生成 model_manager.chat_stream 的 mock 返回序列。

    第一次调用返回 tool_call，第二次返回纯文本（结束循环）。
    """
    call_idx = {"n": 0}

    def fake_stream(messages, tools=None, tool_choice=None):
        call_idx["n"] += 1
        if call_idx["n"] == 1:
            yield {
                "done": True,
                "success": True,
                "content": "",
                "tool_calls": [{
                    "id": "call_test_1",
                    "type": "function",
                    "function": {"name": tool_name, "arguments": "{}"},
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                "model": "test",
                "latency_ms": 1,
            }
        else:
            yield {
                "done": True,
                "success": True,
                "content": "最终回复",
                "tool_calls": [],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
                "model": "test",
                "latency_ms": 1,
            }

    return fake_stream


class TestAgentRetryBehavior:
    """agent._chat_impl 的重试行为由 error_catalog.retryable 驱动。"""

    def test_retryable_error_triggers_retry(self):
        """device_busy (retryable=True) → handler 被调 3 次（1 初始 + 2 重试）。"""
        agent, call_count = _make_minimal_agent(None, "device_busy")
        agent.model_manager.chat_stream = _mock_model_responses()

        with patch.object(agent, "_cancel_pending", return_value=False):
            with patch("time.sleep"):
                result = agent._chat_impl("测试", max_tool_rounds=5)

        assert call_count["n"] == 3, (
            f"retryable 错误应重试 2 次（共 3 次调用），实际 {call_count['n']} 次")
        assert result["content"] == "最终回复"

    def test_non_retryable_error_no_retry(self):
        """tool_not_found (retryable=False) → handler 只调 1 次。"""
        agent, call_count = _make_minimal_agent(None, "tool_not_found")
        agent.model_manager.chat_stream = _mock_model_responses()

        with patch.object(agent, "_cancel_pending", return_value=False):
            result = agent._chat_impl("测试", max_tool_rounds=5)

        assert call_count["n"] == 1, (
            f"non-retryable 错误不应重试，实际调了 {call_count['n']} 次")

    def test_empty_error_code_no_retry(self):
        """error="" → 不重试（保守策略）。"""
        agent, call_count = _make_minimal_agent(None, "")
        agent.model_manager.chat_stream = _mock_model_responses()

        with patch.object(agent, "_cancel_pending", return_value=False):
            result = agent._chat_impl("测试", max_tool_rounds=5)

        assert call_count["n"] == 1

    def test_cancel_stops_retry(self):
        """取消后不再重试。"""
        agent, call_count = _make_minimal_agent(None, "device_busy")
        agent.model_manager.chat_stream = _mock_model_responses()

        cancel_after_first = {"done": False}

        def fake_cancel():
            if call_count["n"] >= 1:
                return True
            return False

        with patch.object(agent, "_cancel_pending", side_effect=fake_cancel):
            with patch("time.sleep"):
                result = agent._chat_impl("测试", max_tool_rounds=5)

        assert call_count["n"] <= 2, "取消后不应继续重试"
        assert result.get("cancelled") or result.get("interrupted")
