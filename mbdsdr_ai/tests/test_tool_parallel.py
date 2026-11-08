# SPDX-License-Identifier: MIT
"""Sprint 5：工具并行执行测试。

设计原则（对齐 parallel_safe 的 opt-in 零回归默认）：
- 默认 parallel_safe=False ⇒ 恒串行；仅当一轮内 tool_calls>1 且**全部**
  parallel_safe 且未取消时才走 ThreadPoolExecutor 并发（保序回收）。
- 并发证明用 threading.Barrier / 重叠计数，**不绑墙钟阈值**（避免 CI 抖动假红）。
- 收尾（上下文回写/状态捕获/重复守卫/日志）一律主线程串行，保证与既有串行
  逐字节同序。

覆盖：
1. 全 parallel_safe → 真并发（barrier 三方同时到达才放行）
2. 并发但结果按 tool_call 原序回收（完成序≠回写序）
3. 混合（有一个非 parallel_safe）→ 回退串行（重叠计数恒为 1）
4. 单工具 → 不开线程池（_run_tool_batch_parallel 不被调用）
5. call_log 并发写不丢条目/不越界（ToolRegistry._call_log_lock）
6. 并发下失败如实标注（不吞、不伪装成功，链路完整）
7. 并发途中取消 → 结果仍如实写回 + 轮末停循环
8. 并发下 retryable 重试语义与串行一致（每工具 1+2 次）
9. 并发路径可观测：tool_call 带 parallel=True，且预阶段日志先于后阶段
"""
import os
import sys
import threading
import time
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from mbdsdr_ai.agent import MBDSDRAgent  # noqa: E402
from mbdsdr_ai.config import AgentConfig  # noqa: E402
from mbdsdr_ai.context_manager import ContextManager, SYSTEM_PROMPT  # noqa: E402
from mbdsdr_ai.hooks import HookManager  # noqa: E402
from mbdsdr_ai.session_log import SessionLog  # noqa: E402
from mbdsdr_ai.tool_registry import ToolRegistry, ToolResult  # noqa: E402


# ── 测试脚手架 ────────────────────────────────────────────────────

def _make_agent(tmp_path, tools, conversation_id="conv_par"):
    """构造跑 _chat_impl 工具循环所需的最小 agent。

    tools: [(name, handler, parallel_safe), ...]，注册进真实 ToolRegistry。
    session log 一律指向 tmp_path（绝不污染 ~/.mbdsdr）；hook 用真实 HookManager
    以便收集事件。其余属性镜像 test_agent_retry._make_minimal_agent。
    """
    agent = object.__new__(MBDSDRAgent)
    agent.config = AgentConfig(api_key="test-key-not-real")
    agent.context_manager = ContextManager(
        max_context_tokens=8192, system_prompt=SYSTEM_PROMPT)
    agent.model_manager = MagicMock()
    agent.model_manager.parse_tool_calls_from_text.return_value = None
    agent.model_manager.model = "test-model"
    agent.tool_registry = ToolRegistry()
    for name, handler, psafe in tools:
        agent.tool_registry.register(
            name=name, description="测试工具 " + name,
            parameters={"type": "object", "properties": {}, "required": []},
            handler=handler, parallel_safe=psafe)
    agent.memory = MagicMock()
    agent.memory.build_memory_context.return_value = ""
    agent.hook_manager = HookManager()
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
    agent.conversation_id = conversation_id
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
    # 会话日志指向 tmp（可读回验证并发路径的可观测性）
    agent._session_log = SessionLog(conversation_id, directory=str(tmp_path))
    agent._session_log_conv = conversation_id
    return agent


def _multi_tool_model(tool_names):
    """第 1 轮：为每个名字发一个 tool_call；第 2 轮：纯文本收尾。"""
    state = {"n": 0}

    def fake_stream(messages, tools=None, tool_choice=None):
        state["n"] += 1
        if state["n"] == 1:
            tcs = [{"id": f"call_{nm}_{i}", "type": "function",
                    "function": {"name": nm, "arguments": "{}"}}
                   for i, nm in enumerate(tool_names)]
            yield {"done": True, "success": True, "content": "",
                   "tool_calls": tcs,
                   "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                             "total_tokens": 15},
                   "model": "test", "latency_ms": 1}
        else:
            yield {"done": True, "success": True, "content": "最终回复",
                   "tool_calls": [],
                   "usage": {"prompt_tokens": 20, "completion_tokens": 10,
                             "total_tokens": 30},
                   "model": "test", "latency_ms": 1}

    return fake_stream


def _ok_handler(nm, sleep_s=0.0, on_run=None):
    """成功 handler；可选 sleep 与「完成回调」（sleep 之后触发，记录完成序）。"""
    def h(args):
        if sleep_s:
            time.sleep(sleep_s)
        if on_run is not None:
            on_run(nm)  # 完成序：sleep 之后才记，方能反映真实结束先后
        return ToolResult(success=True, content=nm + " ok", tool_name=nm, args=args)
    return h


# ── 1. 真并发证明（barrier）──────────────────────────────────────

class TestParallelConcurrency:
    def test_all_parallel_safe_run_concurrently(self, tmp_path):
        """3 个 parallel_safe 工具：barrier(3) 只有三方同时到达才放行。

        串行则第一个 handler 独等 barrier 超时 → BrokenBarrierError → 失败。
        因此三方全 success=True 即证明真并发。
        """
        barrier = threading.Barrier(3, timeout=5)

        def make_handler(nm):
            def h(args):
                try:
                    barrier.wait()
                except threading.BrokenBarrierError:
                    return ToolResult(
                        success=False, tool_name=nm, args=args,
                        content=nm + " barrier broken（疑似串行）",
                        error="barrier_timeout")
                return ToolResult(success=True, content=nm + " ok",
                                  tool_name=nm, args=args)
            return h

        tools = [(f"t{i}", make_handler(f"t{i}"), True) for i in range(3)]
        agent = _make_agent(tmp_path, tools)
        agent.model_manager.chat_stream = _multi_tool_model(["t0", "t1", "t2"])

        with patch.object(agent, "_cancel_pending", return_value=False):
            result = agent._chat_impl("go", max_tool_rounds=5)

        assert result["content"] == "最终回复"
        successes = [r["success"] for r in result["tool_results"]]
        assert successes == [True, True, True], (
            f"barrier 三方同时到达才放行 ⇒ 证明并发；实得 {successes}")

    def test_parallel_preserves_input_order(self, tmp_path):
        """并发完成序（快→慢）≠ 回写序（恒为 tool_call 原序）。"""
        lock = threading.Lock()
        completion_order = []

        def record(nm):
            with lock:
                completion_order.append(nm)

        # 睡眠差异大：t1 最快、t0 最慢；并发下完成序应为 t1,t2,t0
        tools = [
            ("t0", _ok_handler("t0", sleep_s=0.30, on_run=record), True),
            ("t1", _ok_handler("t1", sleep_s=0.05, on_run=record), True),
            ("t2", _ok_handler("t2", sleep_s=0.15, on_run=record), True),
        ]
        agent = _make_agent(tmp_path, tools)
        agent.model_manager.chat_stream = _multi_tool_model(["t0", "t1", "t2"])

        with patch.object(agent, "_cancel_pending", return_value=False):
            result = agent._chat_impl("go", max_tool_rounds=5)

        # 回写序恒等于输入序（保序回收）
        assert [r["tool_name"] for r in result["tool_results"]] == ["t0", "t1", "t2"]
        # 最快的 t1 先于最慢的 t0 完成 ⇒ 确有重叠并发（不绑墙钟阈值）
        assert completion_order.index("t1") < completion_order.index("t0"), (
            f"并发下 t1(0.05s) 应先于 t0(0.30s) 完成；完成序={completion_order}")


# ── 3/4. 串行回退与单工具不开池 ──────────────────────────────────

class TestSerialFallback:
    def test_mixed_parallel_safe_falls_back_to_serial(self, tmp_path):
        """有一个非 parallel_safe ⇒ 整批串行（任意时刻至多 1 个 handler 在跑）。"""
        lock = threading.Lock()
        active = {"n": 0, "max_overlap": 0}

        def make_handler(nm):
            def h(args):
                with lock:
                    active["n"] += 1
                    active["max_overlap"] = max(active["max_overlap"], active["n"])
                time.sleep(0.1)
                with lock:
                    active["n"] -= 1
                return ToolResult(success=True, content=nm, tool_name=nm, args=args)
            return h

        tools = [("s0", make_handler("s0"), True),
                 ("s1", make_handler("s1"), False)]  # s1 非 parallel_safe
        agent = _make_agent(tmp_path, tools)
        agent.model_manager.chat_stream = _multi_tool_model(["s0", "s1"])

        with patch.object(agent, "_cancel_pending", return_value=False):
            result = agent._chat_impl("go", max_tool_rounds=5)

        assert active["max_overlap"] == 1, (
            f"混合批必须串行（无重叠）；实测最大重叠={active['max_overlap']}")
        assert [r["success"] for r in result["tool_results"]] == [True, True]

    def test_single_tool_never_spawns_pool(self, tmp_path):
        """tool_calls==1 即便 parallel_safe 也不开线程池（n_tc>1 门）。"""
        tools = [("only", _ok_handler("only"), True)]
        agent = _make_agent(tmp_path, tools)
        agent.model_manager.chat_stream = _multi_tool_model(["only"])

        with patch.object(agent, "_cancel_pending", return_value=False):
            with patch.object(agent, "_run_tool_batch_parallel",
                              wraps=agent._run_tool_batch_parallel) as spy:
                result = agent._chat_impl("go", max_tool_rounds=5)

        assert spy.call_count == 0, "单工具不得走并发批"
        assert [r["success"] for r in result["tool_results"]] == [True]


# ── 5. call_log 并发写安全 ───────────────────────────────────────

class TestCallLogThreadSafety:
    def test_concurrent_log_calls_never_lose_or_overflow(self):
        """远超 max_log_entries 的并发写：末态长度恰为上限，无异常、无越界。

        无锁时「超长即重切片赋值」是读改写复合操作，并发会丢条目/瞬态越界。
        """
        reg = ToolRegistry()
        reg.max_log_entries = 20
        reg.register("noop", "d", {"type": "object", "properties": {}, "required": []},
                     lambda a: ToolResult(success=True, content="ok", tool_name="noop"),
                     parallel_safe=True)

        errors = []
        n_workers, per_worker = 8, 30  # 240 次 >> 上限 20

        def worker():
            try:
                for _ in range(per_worker):
                    reg.call("noop", {})
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n_workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发写 call_log 抛异常：{errors[:3]}"
        assert len(reg.call_log) == reg.max_log_entries, (
            f"末态应恰为上限 {reg.max_log_entries}，实得 {len(reg.call_log)}")
        assert all(isinstance(e, type(reg.call_log[0])) for e in reg.call_log)


# ── 6. 并发下诚实失败 ────────────────────────────────────────────

class TestParallelHonesty:
    def test_failure_in_parallel_is_not_masked(self, tmp_path):
        """一成一败：失败的如实带错误码、不记成功；两条 tool 消息都回写（链完整）。"""
        def fail_handler(args):
            return ToolResult(success=False, content="boom", tool_name="bad",
                              args=args, error="device_busy")

        tools = [("good", _ok_handler("good"), True),
                 ("bad", fail_handler, True)]
        agent = _make_agent(tmp_path, tools)
        agent.model_manager.chat_stream = _multi_tool_model(["good", "bad"])

        # 两者均 parallel_safe ⇒ 走并发；device_busy 可重试，patch sleep 令重试瞬完
        with patch.object(agent, "_cancel_pending", return_value=False):
            with patch("time.sleep"):
                result = agent._chat_impl("go", max_tool_rounds=5)

        by_name = {r["tool_name"]: r for r in result["tool_results"]}
        assert by_name["good"]["success"] is True
        assert by_name["bad"]["success"] is False
        assert by_name["bad"]["error"] == "device_busy"
        # 链路完整：两个 tool_call_id 都应有回写消息
        assert len(result["tool_results"]) == 2

    def test_retry_semantics_preserved_in_parallel(self, tmp_path):
        """并发下 retryable 错误仍重试到 1+2 次（与串行逐字节一致的执行核）。"""
        counts = {"r0": 0, "r1": 0}
        lock = threading.Lock()

        def make_retry_handler(nm):
            def h(args):
                with lock:
                    counts[nm] += 1
                return ToolResult(success=False, content="busy", tool_name=nm,
                                  args=args, error="device_busy")
            return h

        tools = [("r0", make_retry_handler("r0"), True),
                 ("r1", make_retry_handler("r1"), True)]
        agent = _make_agent(tmp_path, tools)
        agent.model_manager.chat_stream = _multi_tool_model(["r0", "r1"])

        with patch.object(agent, "_cancel_pending", return_value=False):
            with patch("time.sleep"):
                agent._chat_impl("go", max_tool_rounds=5)

        assert counts["r0"] == 3, f"r0 应 1+2 次，实得 {counts['r0']}"
        assert counts["r1"] == 3, f"r1 应 1+2 次，实得 {counts['r1']}"


# ── 7. 并发途中取消 ──────────────────────────────────────────────

class TestParallelCancel:
    def test_cancel_during_parallel_writes_back_then_stops(self, tmp_path):
        """进入并发批（批前未取消）→ handler 置取消 → 批后检出：结果如实写回 + 轮末停。"""
        cancel_flag = {"v": False}

        def make_handler(nm):
            def h(args):
                cancel_flag["v"] = True  # 第一个跑起来的工具翻起取消
                return ToolResult(success=True, content=nm + " ok",
                                  tool_name=nm, args=args)
            return h

        tools = [("c0", make_handler("c0"), True), ("c1", make_handler("c1"), True)]
        agent = _make_agent(tmp_path, tools)
        agent.model_manager.chat_stream = _multi_tool_model(["c0", "c1"])

        with patch.object(agent, "_cancel_pending", side_effect=lambda: cancel_flag["v"]):
            result = agent._chat_impl("go", max_tool_rounds=5)

        # 批已整体派发 ⇒ 两个结果都真实写回（不是 [已取消] 占位）
        assert len(result["tool_results"]) == 2
        assert all(r["success"] for r in result["tool_results"])
        # 轮末检出取消 ⇒ 停循环
        assert result.get("cancelled") or result.get("interrupted")


# ── 9. 并发路径可观测 ────────────────────────────────────────────

class TestParallelObservability:
    def test_parallel_tool_call_logs_marked_and_ordered(self, tmp_path):
        """并发路径：tool_call 带 parallel=True；预阶段全部 tool_call 先于后阶段 tool_result。"""
        tools = [(f"o{i}", _ok_handler(f"o{i}"), True) for i in range(3)]
        agent = _make_agent(tmp_path, tools)
        agent.model_manager.chat_stream = _multi_tool_model(["o0", "o1", "o2"])

        with patch.object(agent, "_cancel_pending", return_value=False):
            agent._chat_impl("go", max_tool_rounds=5)

        events = agent._session_log.read_events()
        tool_call_ev = [e for e in events if e.get("type") == "tool_call"]
        tool_result_ev = [e for e in events if e.get("type") == "tool_result"]

        assert len(tool_call_ev) == 3 and len(tool_result_ev) == 3
        # 并发标记
        assert all(e.get("parallel") is True for e in tool_call_ev), (
            f"并发批 tool_call 应带 parallel=True；实得 {[e.get('parallel') for e in tool_call_ev]}")
        # 预阶段（tool_call）整体先于后阶段（tool_result）
        seqs_call = [e["seq"] for e in tool_call_ev]
        seqs_result = [e["seq"] for e in tool_result_ev]
        assert max(seqs_call) < min(seqs_result), (
            f"预阶段日志应先于后阶段；call seq={seqs_call} result seq={seqs_result}")
