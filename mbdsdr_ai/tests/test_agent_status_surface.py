# SPDX-License-Identifier: MIT
"""Sprint 7：Agent 状态面补齐观测层（session log / hooks / subagents）。

get_status()/get_status_text()/run_command() 是 Agent 的**自报面**——UI 与运维
据此读懂「这个 Agent 现在有什么、在记什么、挂了几个子代理」。Sprint 3/4/6 把
会话日志、hook 总线、子代理观测都接通了，但状态面还看不见它们。本次把三层接入
状态面，并加 /hooks /subagents /session 三个斜杠命令（含中文别名）。

红线：状态面是只读自报，任一观测子系统缺失/损坏都只**降级**（available=False /
active=False / broken=True），绝不抛异常拖垮 get_status；event_count 用
SessionLog._seq（O(1)），不每次全量读盘；SessionLog 一律指向 tmp_path 不污染
~/.mbdsdr。
"""
import os
import sys
import threading
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from mbdsdr_ai.agent import MBDSDRAgent  # noqa: E402
from mbdsdr_ai.config import AgentConfig  # noqa: E402
from mbdsdr_ai.context_manager import ContextManager, SYSTEM_PROMPT  # noqa: E402
from mbdsdr_ai.hooks import HookManager  # noqa: E402
from mbdsdr_ai.session_log import SessionLog  # noqa: E402
from mbdsdr_ai.subagents import SubagentManager  # noqa: E402
from mbdsdr_ai.tool_registry import ToolRegistry  # noqa: E402


def _make_status_agent(tmp_path, conversation_id="conv_status"):
    """构造跑 get_status/get_status_text/run_command 所需的最小 agent。

    观测三件套用**真实**对象（HookManager / SubagentManager / SessionLog→tmp），
    以便断言状态面真把它们读出来；其余子系统用 MagicMock（get_status_text 必须
    返回字符串，否则 join 会炸）。
    """
    agent = object.__new__(MBDSDRAgent)
    agent.config = AgentConfig(api_key="test-key-not-real")
    agent.context_manager = ContextManager(
        max_context_tokens=8192, system_prompt=SYSTEM_PROMPT)
    agent.model_manager = MagicMock()
    agent.model_manager.model = "test-model"
    agent.model_manager.get_status.return_value = {"model": "test-model"}
    agent.model_manager.get_status_text.return_value = "模型: test-model"
    agent.tool_registry = ToolRegistry()
    agent.memory = MagicMock()
    agent.memory.get_stats.return_value = {"total_memories": 0}
    agent.memory.get_status_text.return_value = "记忆: 空"
    agent.hook_manager = HookManager()
    # enable_session_log=False ⇒ create() 不写子代理 JSONL，不污染 ~/.mbdsdr
    agent.subagent_manager = SubagentManager(
        agent.tool_registry, model_manager=agent.model_manager,
        enable_session_log=False)
    agent.conversation_id = conversation_id
    agent.total_agent_calls = 0
    agent.last_error = None
    agent._cancel_event = threading.Event()
    agent._session_log = SessionLog(conversation_id, directory=str(tmp_path))
    agent._session_log_conv = conversation_id
    return agent


# ── get_status：三层观测键 ───────────────────────────────────────

class TestGetStatusObservability:
    def test_status_includes_three_observability_keys(self, tmp_path):
        agent = _make_status_agent(tmp_path)
        st = agent.get_status()
        for key in ("session_log", "hooks", "subagents"):
            assert key in st, f"get_status 缺观测键 {key}"
        # 既有键不受影响（additive，零回归）
        for key in ("conversation_id", "config", "context", "model",
                    "tools", "memory", "last_error"):
            assert key in st, f"get_status 丢了既有键 {key}"

    def test_session_log_status_reflects_real_log(self, tmp_path):
        agent = _make_status_agent(tmp_path)
        agent._slog("user_message", content="hi")
        agent._slog("assistant_message", content="yo")
        sl = agent.get_status()["session_log"]
        assert sl["active"] is True
        assert sl["broken"] is False
        assert sl["event_count"] == 2, "event_count 应等于 SessionLog._seq"
        assert sl["path"] and sl["path"].endswith(".jsonl")
        # 不读盘：event_count 来自 _seq，与 read_events 一致
        assert sl["event_count"] == len(agent._session_log.read_events())

    def test_hooks_status_counts_registered(self, tmp_path):
        agent = _make_status_agent(tmp_path)
        agent.hook_manager.register("agent.tool_call", lambda e: None, description="h1")
        agent.hook_manager.register("*", lambda e: None, description="h2")
        hk = agent.get_status()["hooks"]
        assert hk["available"] is True
        assert hk["total_hooks"] == 2
        assert hk["enabled_hooks"] == 2

    def test_subagents_status_counts_created(self, tmp_path):
        agent = _make_status_agent(tmp_path)
        assert agent.get_status()["subagents"]["count"] == 0
        agent.subagent_manager.create("analyst", subagent_id="sa_test")
        sa = agent.get_status()["subagents"]
        assert sa["available"] is True
        assert sa["count"] == 1
        assert sa["subagents"][0]["subagent_id"] == "sa_test"
        assert sa["subagents"][0]["agent_type"] == "analyst"


# ── get_status_text：可读观测段 ──────────────────────────────────

class TestStatusTextObservability:
    def test_status_text_has_observability_section(self, tmp_path):
        agent = _make_status_agent(tmp_path)
        txt = agent.get_status_text()
        assert "观测层" in txt
        assert "会话日志" in txt and "Hook 总线" in txt and "子代理" in txt

    def test_status_text_reflects_counts(self, tmp_path):
        agent = _make_status_agent(tmp_path)
        agent.hook_manager.register("agent.tool_call", lambda e: None)
        agent.subagent_manager.create("analyst", subagent_id="sa_x")
        txt = agent.get_status_text()
        assert "1 个钩子" in txt
        assert "1 个存活" in txt


# ── run_command：/hooks /subagents /session + 中文别名 ───────────

class TestRunCommandObservability:
    def test_hooks_command_lists_hooks(self, tmp_path):
        agent = _make_status_agent(tmp_path)
        agent.hook_manager.register("agent.tool_call", lambda e: None, description="我的钩子")
        out = agent.run_command("/hooks")
        assert "Hook 事件总线" in out
        assert "我的钩子" in out
        assert "agent.tool_call" in out

    def test_subagents_command(self, tmp_path):
        agent = _make_status_agent(tmp_path)
        agent.subagent_manager.create("analyst", subagent_id="sa_cmd")
        out = agent.run_command("/subagents")
        assert "子代理子系统" in out
        assert "sa_cmd" in out

    def test_session_command(self, tmp_path):
        agent = _make_status_agent(tmp_path)
        agent._slog("user_message", content="hi")
        out = agent.run_command("/session")
        assert "会话事件日志" in out
        assert "user_message" in out  # 最近事件类型回显

    def test_chinese_aliases(self, tmp_path):
        agent = _make_status_agent(tmp_path)
        assert "Hook 事件总线" in agent.run_command("/钩子")
        assert "子代理子系统" in agent.run_command("/子代理")
        assert "会话事件日志" in agent.run_command("/会话日志")

    def test_help_lists_new_commands(self, tmp_path):
        agent = _make_status_agent(tmp_path)
        h = agent.run_command("/help")
        for c in ("/hooks", "/subagents", "/session"):
            assert c in h, f"/help 未列出 {c}"


# ── 诚实降级：子系统缺失/损坏不拖垮状态面 ────────────────────────

class TestGracefulDegradation:
    def test_missing_subsystems_do_not_crash_status(self, tmp_path):
        agent = _make_status_agent(tmp_path)
        agent.hook_manager = None
        agent.subagent_manager = None
        agent._session_log = None
        st = agent.get_status()  # 不得抛
        assert st["hooks"]["available"] is False
        assert st["subagents"]["available"] is False
        assert st["session_log"]["active"] is False
        # 文本面与命令面同样降级
        assert "观测层" in agent.get_status_text()
        assert "不可用" in agent.run_command("/hooks")
        assert "不可用" in agent.run_command("/subagents")

    def test_broken_session_log_reported_honestly(self, tmp_path):
        agent = _make_status_agent(tmp_path)
        agent._session_log._broken = True
        sl = agent.get_status()["session_log"]
        assert sl["broken"] is True
        assert sl["active"] is False, "损坏的日志不得报成 active"
        assert "损坏" in agent.run_command("/session")
        assert "损坏" in agent.get_status_text()

    def test_session_disabled_in_config(self, tmp_path):
        agent = _make_status_agent(tmp_path)
        agent.config.enable_session_log = False
        agent._session_log = None
        sl = agent.get_status()["session_log"]
        assert sl["enabled"] is False
        assert "关闭" in agent.run_command("/session")
