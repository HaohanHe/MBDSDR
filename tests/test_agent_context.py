#!/usr/bin/env python3
"""
MBDSDR Agent 层测试：上下文压缩、工具闭环、多轮对话。
所有 LLM 调用均 mock，不依赖真实 API。
"""
import os
import sys
import json
import time
import tempfile
import shutil
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai.context_manager import (
    ContextManager,
    get_model_context_window,
    estimate_tokens,
)
from mbdsdr_ai.conversation import ConversationStore, ConversationMeta
from mbdsdr_ai.config import AgentConfig
from mbdsdr_ai.tool_registry import ToolResult


# ── 辅助：构造一个"裸" Agent（绕过重型 __init__）────────────────────
def _make_bare_agent(config=None, model_manager=None, tool_registry=None,
                     memory=None, ctx_max_tokens=4096, ctx_threshold=0.99):
    """
    用 object.__new__ 绕过 MBDSDRAgent.__init__（它会注册 100+ 工具并 import 大量硬件模块），
    只设置 chat() 真正访问的属性。
    """
    from mbdsdr_ai.agent import MBDSDRAgent
    agent = object.__new__(MBDSDRAgent)
    agent.config = config or AgentConfig(api_key="sk-test", model="test-model")
    agent.context_manager = ContextManager(
        max_context_tokens=ctx_max_tokens,
        compaction_threshold=ctx_threshold,
        compaction_target_ratio=0.5,
        system_prompt="你是测试助手。",
    )
    agent.model_manager = model_manager or MagicMock()
    agent.model_manager.parse_tool_calls_from_text = MagicMock(return_value=[])
    agent.tool_registry = tool_registry or MagicMock()
    agent.tool_registry.get_tool_names.return_value = ["get_status", "list_tools"]
    agent.tool_registry.get_tool_definitions.return_value = []
    agent.memory = memory or MagicMock()
    agent.memory.build_memory_context.return_value = ""
    agent.total_agent_calls = 0
    agent.last_error = None
    agent._repeat_key = None
    agent._repeat_count = 0
    agent._mcp_client = None
    agent.conversation_store = ConversationStore(tempfile.mkdtemp(prefix="mbdsdr_conv_test_"))
    return agent


def _stream_event(**kw):
    """构造一个 chat_stream 的 done 事件。"""
    ev = {"done": True, "success": True, "content": "", "tool_calls": [],
          "usage": {}, "model": "test-model", "latency_ms": 0}
    ev.update(kw)
    return ev


# ── T1: 模型感知上下文窗口 ────────────────────────────────────────

def test_model_aware_context_window():
    """T1: 根据模型名返回正确的上下文窗口大小。"""
    assert get_model_context_window("Qwen/Qwen3.6-35B-A3B") == 32768
    assert get_model_context_window("deepseek-ai/DeepSeek-V3") == 128000
    assert get_model_context_window("some-unknown-model-xyz") == 8192
    assert get_model_context_window("") == 8192


# ── T2: 长对话压缩是摘要而非截断 ──────────────────────────────────

def test_long_conversation_compression_summarizes_not_truncates():
    """T2: 触发压缩后历史开头出现摘要 system 消息，总 token 降下来，最近消息不丢。"""
    cm = ContextManager(
        max_context_tokens=1000,
        compaction_threshold=0.8,
        compaction_target_ratio=0.5,
        system_prompt="你是助手。",  # 小 system_prompt，避免占满预算
        recent_turns_keep=3,
    )
    # 加 20 条长用户消息（英文，token 数可预测）
    for i in range(20):
        cm.add_user_message(
            f"Message number {i}. " * 10  # ~200 字符
        )

    assert cm.needs_compaction() is True, "压缩前应已超过阈值"
    old_count = cm.compaction_count
    old_epoch = cm.epoch

    ok = cm.compact()
    assert ok is True

    # 第一条历史应是 system 摘要消息
    assert len(cm.history) > 0
    first = cm.history[0]
    assert first["role"] == "system"
    assert ("摘要" in first["content"]) or ("压缩" in first["content"])

    # 压缩后总 token 应低于 max * 0.6
    total = cm.get_total_tokens()
    assert total < 1000 * 0.6, f"压缩后 token {total} 应 < 600"

    # 最近一条消息内容仍在历史中
    last_msg_text = "Message number 19."
    found = any(last_msg_text in (m.get("content") or "") for m in cm.history)
    assert found, "最近的消息不应被完全截断丢失"

    # compaction_count 与 epoch 递增
    assert cm.compaction_count == old_count + 1
    assert cm.epoch == old_epoch + 1


# ── T3: 压缩保留最近 N 轮 ─────────────────────────────────────────

def test_compaction_keeps_recent_turns():
    """T3: 压缩后至少保留 recent_turns_keep 轮 user 消息。"""
    cm = ContextManager(
        max_context_tokens=1000,
        compaction_threshold=0.8,
        compaction_target_ratio=0.5,
        system_prompt="你是助手。",
        recent_turns_keep=4,
    )
    # 10 轮 user+assistant
    for i in range(10):
        cm.add_user_message(f"用户第{i}轮消息 " + ("填充" * 30))
        cm.add_assistant_message(f"助手第{i}轮回复 " + ("填充" * 30))

    cm.compact()

    # 统计保留下来的 user 消息数
    kept_users = [m for m in cm.history if m.get("role") == "user"]
    assert len(kept_users) >= 4, \
        f"至少应保留 4 轮 user 消息，实际 {len(kept_users)}"

    # 最近一轮（第 9 轮）的内容必须在
    last_text = "用户第9轮消息"
    assert any(last_text in (m.get("content") or "") for m in cm.history)


# ── T4: 工具结果进入上下文 ────────────────────────────────────────

def test_tool_results_enter_context():
    """T4: 第一次 LLM 返回 tool_call，工具结果以 role=tool 进入上下文后喂给第二次 LLM。"""
    captured_messages = []

    def fake_stream(messages, tools=None, tool_choice=None):
        captured_messages.append(messages)
        round_idx = len(captured_messages)
        if round_idx == 1:
            # 第一轮：返回工具调用
            yield _stream_event(
                content="",
                tool_calls=[{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_status", "arguments": "{}"},
                }],
            )
        else:
            # 第二轮：看到工具结果后给出最终回复
            yield _stream_event(content="设备已就绪，频率 100MHz。")

    mm = MagicMock()
    mm.chat_stream = fake_stream
    mm.model = "test-model"

    tr = MagicMock()
    tr.call_from_model.return_value = ToolResult(
        success=True, content="SDR 已连接，当前频率 100.0 MHz", tool_name="get_status")

    agent = _make_bare_agent(model_manager=mm, tool_registry=tr)
    result = agent.chat("查看状态")

    # 最终回复非空
    assert result["content"], "最终回复不应为空"
    assert not result["error"], f"不应有错误: {result['error']}"

    # 第二次 LLM 调用的 messages 中应包含 role=tool 消息
    assert len(captured_messages) >= 2, "应至少调用两次 LLM"
    second_call_msgs = captured_messages[1]
    tool_msgs = [m for m in second_call_msgs if m.get("role") == "tool"]
    assert len(tool_msgs) >= 1, "第二次调用应包含 role=tool 消息"
    assert "SDR 已连接" in tool_msgs[0]["content"], \
        "工具返回值应真的进入上下文"


# ── T5: 工具失败时错误信息进上下文 ────────────────────────────────

def test_tool_failure_llm_receives_error():
    """T5: 工具返回失败时，错误内容以 role=tool 喂回 LLM。"""
    captured_messages = []

    def fake_stream(messages, tools=None, tool_choice=None):
        captured_messages.append(messages)
        if len(captured_messages) == 1:
            yield _stream_event(
                content="",
                tool_calls=[{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_status", "arguments": "{}"},
                }],
            )
        else:
            yield _stream_event(content="设备未连接，请检查线缆。")

    mm = MagicMock()
    mm.chat_stream = fake_stream
    mm.model = "test-model"

    tr = MagicMock()
    tr.call_from_model.return_value = ToolResult(
        success=False, content="设备未连接", error="not_connected", tool_name="get_status")

    agent = _make_bare_agent(model_manager=mm, tool_registry=tr)
    result = agent.chat("查看状态")

    # 第二次调用的 tool 消息应包含错误内容
    second_call_msgs = captured_messages[1]
    tool_msgs = [m for m in second_call_msgs if m.get("role") == "tool"]
    assert len(tool_msgs) >= 1
    assert ("设备未连接" in tool_msgs[0]["content"]
            or "失败" in tool_msgs[0]["content"]), \
        f"工具失败信息应进上下文，实际: {tool_msgs[0]['content']}"


# ── T6: 工具循环耗尽不静默 ────────────────────────────────────────

def test_tool_loop_exhaustion_not_silent():
    """T6: LLM 一直返回 tool_call 不给最终回复时，返回明确提示而非空字符串。"""
    def fake_stream(messages, tools=None, tool_choice=None):
        # 永远返回 tool_call，不给最终文本
        yield _stream_event(
            content="",
            tool_calls=[{
                "id": f"call_{int(time.time()*1000)}",
                "type": "function",
                "function": {"name": "get_status", "arguments": "{}"},
            }],
        )

    mm = MagicMock()
    mm.chat_stream = fake_stream
    mm.model = "test-model"

    tr = MagicMock()
    tr.call_from_model.return_value = ToolResult(
        success=True, content="ok", tool_name="get_status")

    agent = _make_bare_agent(model_manager=mm, tool_registry=tr)
    result = agent.chat("查看状态", max_tool_rounds=3)

    assert result["content"], "耗尽时不应返回空 content"
    assert "最大轮次" in result["content"] or "未能生成最终回复" in result["content"]
    assert result["error"] == "tool_rounds_exhausted"


# ── T7: 无 API key 不假装回答 ─────────────────────────────────────

def test_no_api_key_no_fake():
    """T7: api_key 为空时 chat() 返回明确错误，不调用 LLM。"""
    cfg = AgentConfig(api_key="", model="test-model")
    mm = MagicMock()
    agent = _make_bare_agent(config=cfg, model_manager=mm)
    result = agent.chat("你好")

    assert result["error"] == "api_key_not_set"
    assert "API key" in result["content"] or "未配置" in result["content"]
    # 不应调用任何 LLM
    mm.chat_stream.assert_not_called()


# ── T8: 流式 usage 为空时 token 估算非零 ──────────────────────────

def test_token_estimation_nonzero():
    """T8: 流式 API 不返回 usage 时，用字符数估算，total_tokens > 0。"""
    def fake_stream(messages, tools=None, tool_choice=None):
        # usage 为空，模拟流式 API 不返回用量
        yield _stream_event(content="这是一段测试回复，用来验证 token 估算是否生效。",
                            tool_calls=[], usage={})

    mm = MagicMock()
    mm.chat_stream = fake_stream
    mm.model = "test-model"

    agent = _make_bare_agent(model_manager=mm)
    result = agent.chat("你好")

    assert result["usage"]["total_tokens"] > 0, \
        f"total_tokens 应 > 0，实际 {result['usage']}"
    assert result["usage"].get("estimated") is True


# ── T9: 对话持久化（ContextManager 文件存取）─────────────────────

def test_conversation_persistence():
    """T9: 保存到临时文件再加载，消息数量与内容一致。"""
    cm = ContextManager(max_context_tokens=2048, system_prompt="你是助手。")
    cm.add_user_message("第一条用户消息")
    cm.add_assistant_message("第一条助手回复")
    cm.add_user_message("第二条用户消息")
    cm.add_assistant_message("第二条助手回复", tool_calls=[
        {"id": "c1", "type": "function",
         "function": {"name": "get_status", "arguments": "{}"}}
    ])

    tmpdir = tempfile.mkdtemp(prefix="mbdsdr_ctx_test_")
    try:
        path = os.path.join(tmpdir, "conv_test.json")
        cm.save_to_file(path)
        assert os.path.exists(path)

        # 加载到新的 ContextManager
        cm2 = ContextManager(max_context_tokens=2048, system_prompt="你是助手。")
        n = cm2.load_from_file(path)
        assert n == len(cm.history)
        assert len(cm2.history) == len(cm.history)
        # 内容逐条比对
        for a, b in zip(cm.export_messages(), cm2.export_messages()):
            assert a == b
        # epoch 恢复
        assert cm2.epoch == cm.epoch
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── T10: ConversationStore 增删查改 ───────────────────────────────

def test_conversation_store_save_load_list_delete():
    """T10: ConversationStore 保存/列表/加载/删除全流程。"""
    tmpdir = tempfile.mkdtemp(prefix="mbdsdr_store_test_")
    try:
        store = ConversationStore(directory=tmpdir)

        msgs = [
            {"role": "user", "content": "你好，请帮我调谐到 FM 广播"},
            {"role": "assistant", "content": "已调谐到 98.5 MHz"},
        ]
        path = store.save("conv_alpha", msgs, title="测试对话")
        assert os.path.exists(path)

        # list 能看到
        listed = store.list_conversations()
        assert len(listed) == 1
        assert listed[0].conversation_id == "conv_alpha"
        assert listed[0].title == "测试对话"
        assert listed[0].message_count == 2

        # load 回来内容一致
        loaded = store.load("conv_alpha")
        assert len(loaded) == 2
        assert loaded[0]["content"] == "你好，请帮我调谐到 FM 广播"

        # 再存一个
        store.save("conv_beta", [{"role": "user", "content": "第二条对话的开头消息内容"}])
        listed = store.list_conversations()
        assert len(listed) == 2
        # title 自动从首条 user 消息截取
        beta = [m for m in listed if m.conversation_id == "conv_beta"][0]
        assert "第二条对话" in beta.title

        # delete 后 list 减少
        assert store.delete("conv_alpha") is True
        listed = store.list_conversations()
        assert len(listed) == 1
        assert listed[0].conversation_id == "conv_beta"

        # 删除不存在的返回 False
        assert store.delete("conv_nope") is False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── 额外：工具结果不被截断地保留在压缩区间内 ──────────────────────

def test_tool_messages_kept_intact_in_compaction():
    """附加：压缩时若保留区间含 role=tool 消息，其配对的 assistant(tool_calls) 也保留。"""
    cm = ContextManager(
        max_context_tokens=1000,
        compaction_threshold=0.8,
        compaction_target_ratio=0.5,
        system_prompt="你是助手。",
        recent_turns_keep=2,
    )
    # 构造一段含工具调用的对话
    for i in range(8):
        cm.add_user_message(f"用户消息{i} " + ("填充文字" * 20))
        if i == 5:
            cm.add_assistant_message("", tool_calls=[{
                "id": "call_x", "type": "function",
                "function": {"name": "get_status", "arguments": "{}"},
            }])
            cm.add_tool_message("call_x", "工具返回的设备状态内容", tool_name="get_status")
        else:
            cm.add_assistant_message(f"助手回复{i} " + ("填充文字" * 20))

    cm.compact()

    # 若 tool 消息被保留，则它前面必须有带 tool_calls 的 assistant 消息
    for idx, m in enumerate(cm.history):
        if m.get("role") == "tool":
            # 向前找最近的 assistant 消息
            prev = None
            for j in range(idx - 1, -1, -1):
                if cm.history[j].get("role") == "assistant":
                    prev = cm.history[j]
                    break
            assert prev is not None, "tool 消息必须有配对的 assistant 消息在前"
            assert prev.get("tool_calls"), \
                "配对的 assistant 消息应带 tool_calls"
