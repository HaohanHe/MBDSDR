# SPDX-License-Identifier: MIT
"""子代理 timeout_s 真落地测试（#84：清死参数 + 诚实 partial）。

背景（缺陷实证）：``subagent_execute`` 工具 schema 向模型广告
``"timeout_s": {"description": "超时秒数，默认 120"}``，模块文档也写
「SubagentTask：子代理任务，包含目标、输入、超时、回调」，但
``Subagent._run_llm_loop`` 的 ``for step in range(1, limit + 1)`` 全程
**没有任何墙钟约束**——timeout_s 一路传到 execute_task 就被丢弃，子代理
可以无限跑；``SubagentStatus.TIMEOUT`` 是个从没被赋值过的死枚举值。

本测试钉住修好后的契约（沿 agent.request_cancel / 工作流取消同一安全点口径）：
1. 步边界 + 每个工具执行前检查 deadline，耗尽 → status=timeout /
   timed_out=True / success=False / task.status=TIMEOUT / 带 [超时] 标记 partial；
2. 预算余额经 registry per-call timeout_s 下发 → 挂死工具真被约束（真注册表实证）；
3. 反证：timeout_s<=0 / 脏值 = 不限时；快任务不受影响；max_steps 截断与超时
   成因分开报（truncated 与 timed_out 互斥，绝不混报）；
4. duck-type 旧注册表（call_from_model 只收一参）零改可用，且绝不因探测签名重跑工具；
5. 展示层（desktop/ai_steps）按真实成因出摘要；工具 schema 文档与实现一致。

时钟手法：除「真注册表约束挂死工具」一例外，全部用**确定性假时钟**
（patch subagents.time，由测试显式推进），绝不把断言绑在真实墙钟上。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time as _real_time
from unittest.mock import patch

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from mbdsdr_ai.subagents import (  # noqa: E402
    Subagent, SubagentManager, SubagentResult, SubagentStatus, SubagentTask,
)
from mbdsdr_ai.tool_registry import ToolRegistry, ToolResult  # noqa: E402

import ai_steps  # noqa: E402

_TOOL = "sdr_spectrum_analyze"


# ---------------------------------------------------------------------------
# 确定性脚手架
# ---------------------------------------------------------------------------
class _FakeClock:
    """假时钟：只暴露 time()，由测试显式推进（subagents 只用 time.time）。"""

    def __init__(self, start: float = 1000.0):
        self.now = float(start)

    def time(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += float(dt)


def _tool_call(name=_TOOL, args=None, call_id="call_1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args or {})}}


class _SlowLoopModel:
    """每轮索取工具并把假时钟推进 chat_cost 秒（模拟慢模型/停不下来的子代理）。

    rounds 非 None 时，第 rounds+1 轮给出最终结论（无 tool_calls）。
    """

    def __init__(self, clock, chat_cost=10.0, rounds=None,
                 content="再测一次。", final="结论：噪声底 -95dBm。",
                 tool_calls_per_round=1):
        self.clock = clock
        self.chat_cost = chat_cost
        self.rounds = rounds
        self.content = content
        self.final = final
        self.tool_calls_per_round = tool_calls_per_round
        self.calls = []

    def chat(self, messages=None, tools=None, tool_choice="auto", **kwargs):
        self.calls.append([dict(m) for m in (messages or [])])
        self.clock.advance(self.chat_cost)
        if self.rounds is not None and len(self.calls) > self.rounds:
            return {"success": True, "content": self.final, "tool_calls": []}
        return {"success": True, "content": self.content,
                "tool_calls": [_tool_call(call_id=f"call_{len(self.calls)}_{i}")
                               for i in range(self.tool_calls_per_round)]}

    @staticmethod
    def parse_tool_calls_from_text(content, valid_names):
        return []


class _BudgetRegistry:
    """duck-type 注册表：支持 timeout_s=，记录每次收到的余额。"""

    def __init__(self, clock=None, tool_cost=0.0, tools=(_TOOL,)):
        self.clock = clock
        self.tool_cost = tool_cost
        self.tools = tuple(tools)
        self.calls = []          # [(name, timeout_s)]

    def get_tool_names(self):
        return list(self.tools)

    def get_tool_definitions(self):
        return [{"type": "function",
                 "function": {"name": n, "description": d,
                              "parameters": {"type": "object", "properties": {}}}}
                for n, d in ((t, "频谱分析") for t in self.tools)]

    def call_from_model(self, tool_call, *, timeout_s=None):
        name = (tool_call.get("function") or {}).get("name", "")
        self.calls.append((name, timeout_s))
        if self.clock is not None and self.tool_cost:
            self.clock.advance(self.tool_cost)
        return ToolResult(success=True, content=f"[stub] {name} 已执行",
                          tool_name=name)


class _LegacyRegistry(_BudgetRegistry):
    """旧式 duck-type 注册表：call_from_model 只收一参（不支持 timeout_s）。"""

    def call_from_model(self, tool_call):  # noqa: D102 - 故意旧签名
        return super().call_from_model(tool_call)


def _sub(model, registry, **kw):
    return Subagent(subagent_id="sub_t", agent_type="spectrum_analyzer",
                    tool_registry=registry, model_manager=model,
                    allowed_tools=[_TOOL], **kw)


def _run(sub, clock, timeout_s=120.0, max_steps=8, goal="分析 100MHz 频谱"):
    """在假时钟下跑一个任务，返回 (result, task)。"""
    task = SubagentTask(task_id="t1", goal=goal, timeout_s=timeout_s)
    with patch("mbdsdr_ai.subagents.time", clock):
        res = sub.execute_task(task, max_steps=max_steps)
    return res, task


def _assert_pairs_well_formed(ctx):
    """assistant(tool_calls) 与紧随的 tool 消息必须成对（超时也不留半对）。"""
    i = 0
    while i < len(ctx):
        m = ctx[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            n, j = 0, i + 1
            while j < len(ctx) and ctx[j].get("role") == "tool":
                n += 1
                j += 1
            assert n == len(m["tool_calls"]), (
                f"第 {i} 条 assistant 有 {len(m['tool_calls'])} 个 tool_calls，"
                f"只跟了 {n} 条 tool 消息")
            i = j
        else:
            i += 1


# ---------------------------------------------------------------------------
# 1. 超时真发生（钉扎：此前 timeout_s 完全无效）
# ---------------------------------------------------------------------------
class TestTimeoutReallyBounds:
    def test_deadline_stops_loop_and_reports_timeout(self):
        """25s 预算 / 每轮 chat 10s：第 4 轮起不来，诚实报 timeout。"""
        clock = _FakeClock()
        model = _SlowLoopModel(clock, chat_cost=10.0)
        reg = _BudgetRegistry(clock)
        res, task = _run(_sub(model, reg), clock, timeout_s=25.0, max_steps=8)

        assert res.success is False
        assert res.status == "timeout"
        assert res.timed_out is True
        assert res.truncated is False, "超时与 max_steps 截断成因必须分开"
        assert res.error.startswith("timeout_exhausted")
        assert "[超时]" in res.output and "不得视为完成" in res.output
        # 真停下来了：8 步上限只跑了 3 轮 chat（反证「循环无限跑」）
        assert len(model.calls) == 3
        assert res.steps_used == 3 and res.steps_executed == 3
        # 第 3 轮 chat 后预算已耗尽 → 该轮工具被跳过，只执行了 2 次
        assert [n for n, _ in reg.calls] == [_TOOL, _TOOL]
        assert res.tools_used == [_TOOL, _TOOL]
        assert task.status == SubagentStatus.TIMEOUT
        assert res.data["timed_out"] is True
        assert res.data["truncated"] is False
        assert res.data["timeout_s"] == 25.0
        assert res.data["elapsed_s"] == 30.0

    def test_default_120_budget_is_enforced(self):
        """默认 120s 也是真约束：单轮 chat 200s → 1 步后即超时。"""
        clock = _FakeClock()
        model = _SlowLoopModel(clock, chat_cost=200.0)
        reg = _BudgetRegistry(clock)
        task = SubagentTask(task_id="t_default", goal="默认预算")
        assert task.timeout_s == 120.0
        with patch("mbdsdr_ai.subagents.time", clock):
            res = _sub(model, reg).execute_task(task)
        assert res.status == "timeout" and res.timed_out is True
        assert res.steps_used == 1
        assert reg.calls == [], "预算耗尽后工具不得执行"

    def test_timeout_keeps_previous_partial_text(self):
        """超时前已有的文本结论必须原样保留 + 追加标记（不丢也不伪造）。"""
        clock = _FakeClock()
        model = _SlowLoopModel(clock, chat_cost=10.0, content="第一轮：已见弱信号。")
        res, _ = _run(_sub(model, _BudgetRegistry(clock)), clock, timeout_s=15.0)
        assert res.status == "timeout"
        assert res.output.startswith("第一轮：已见弱信号。")
        assert "[超时]" in res.output

    def test_timeout_without_any_text_is_honest(self):
        """从没产出文本就超时：明说没结论，绝不编一个。"""
        clock = _FakeClock()
        model = _SlowLoopModel(clock, chat_cost=10.0, content="")
        res, _ = _run(_sub(model, _BudgetRegistry(clock)), clock, timeout_s=5.0)
        assert res.status == "timeout"
        assert "未产出任何文本结论" in res.output
        assert res.output.startswith("[超时]")

    def test_timeout_counts_as_failed_not_completed(self):
        clock = _FakeClock()
        model = _SlowLoopModel(clock, chat_cost=10.0)
        sub = _sub(model, _BudgetRegistry(clock))
        res, task = _run(sub, clock, timeout_s=5.0)
        assert res.success is False
        assert sub.total_tasks_failed == 1
        assert sub.total_tasks_completed == 0
        assert sub.status == SubagentStatus.IDLE, "收尾必须回到 IDLE，不留 RUNNING"
        assert sub.current_task is None
        assert task.result is res and task.completed_at >= task.started_at

    def test_timeout_emits_events_and_task_end_fields(self):
        """观测面：timeout 事件 + task_end 诚实标注（绝不记成完成）。"""
        clock = _FakeClock()
        model = _SlowLoopModel(clock, chat_cost=10.0)
        events = []
        sub = _sub(model, _BudgetRegistry(clock),
                   on_event=lambda sid, et, f: events.append((sid, et, dict(f))))
        _run(sub, clock, timeout_s=5.0)
        types = [et for _, et, _ in events]
        assert "timeout" in types
        end = [f for _, et, f in events if et == "task_end"][0]
        assert end["status"] == "timeout"
        assert end["success"] is False
        assert end["timed_out"] is True
        assert end["truncated"] is False
        start = [f for _, et, f in events if et == "task_start"][0]
        assert start["timeout_s"] == 5.0, "起点就该把预算落账，便于事后归因"
        # 冒泡字段不带 subagent_id（父代理自己加，防重复）
        assert all("subagent_id" not in f for _, _, f in events)

    def test_context_pairs_intact_after_timeout(self):
        """超时中止也不留半对 assistant/tool（OpenAI 配对约定）。"""
        clock = _FakeClock()
        model = _SlowLoopModel(clock, chat_cost=10.0, tool_calls_per_round=2)
        sub = _sub(model, _BudgetRegistry(clock))
        _run(sub, clock, timeout_s=15.0)
        _assert_pairs_well_formed(sub.context)


# ---------------------------------------------------------------------------
# 2. 反证：不限时 / 快任务 / 与 max_steps 截断互不混淆
# ---------------------------------------------------------------------------
class TestUnboundedAndDistinct:
    @pytest.mark.parametrize("budget", [0, -1, -0.5])
    def test_non_positive_budget_means_unbounded(self, budget):
        """<=0 = 不限时：时钟推进 1000s/轮也照跑满 max_steps（向后兼容）。"""
        clock = _FakeClock()
        model = _SlowLoopModel(clock, chat_cost=1000.0)
        res, task = _run(_sub(model, _BudgetRegistry(clock)), clock,
                         timeout_s=budget, max_steps=3)
        assert res.timed_out is False
        assert res.status == "truncated", "不限时时应由 max_steps 收口"
        assert res.truncated is True
        assert len(model.calls) == 3
        assert task.status == SubagentStatus.FAILED

    @pytest.mark.parametrize("dirty", [None, "abc", float("nan"), float("inf")])
    def test_dirty_budget_means_unbounded_not_instant_timeout(self, dirty):
        """脏值按不限时处理：既不抛错，也不当成「0 秒立即超时」。"""
        clock = _FakeClock()
        sub = _sub(_SlowLoopModel(clock), _BudgetRegistry(clock))
        task = SubagentTask(task_id="t_dirty", goal="脏值", timeout_s=dirty)
        assert sub._resolve_timeout_s(task) is None
        model = _SlowLoopModel(clock, chat_cost=1000.0, rounds=1)
        res, _ = _run(_sub(model, _BudgetRegistry(clock)), clock,
                      timeout_s=dirty, max_steps=4)
        assert res.status == "ok" and res.timed_out is False

    def test_positive_budget_resolves_to_float(self):
        sub = _sub(_SlowLoopModel(_FakeClock()), _BudgetRegistry())
        assert sub._resolve_timeout_s(
            SubagentTask(task_id="t", goal="g", timeout_s=12.5)) == 12.5
        assert sub._resolve_timeout_s(
            SubagentTask(task_id="t", goal="g", timeout_s="30")) == 30.0

    def test_fast_task_unaffected(self):
        """快任务（默认 120s 预算，每轮 0.01s）完全不受影响。"""
        clock = _FakeClock()
        model = _SlowLoopModel(clock, chat_cost=0.01, rounds=2)
        res, task = _run(_sub(model, _BudgetRegistry(clock)), clock)
        assert res.success is True and res.status == "ok"
        assert res.timed_out is False and res.truncated is False
        assert res.output == "结论：噪声底 -95dBm。"
        assert res.data["timed_out"] is False
        assert task.status == SubagentStatus.COMPLETED

    def test_max_steps_truncation_still_distinct(self):
        """时钟不动 + 步数耗尽 → 仍是 truncated，绝不能被改报成 timeout。"""
        clock = _FakeClock()
        model = _SlowLoopModel(clock, chat_cost=0.0)
        res, task = _run(_sub(model, _BudgetRegistry(clock)), clock,
                         timeout_s=120.0, max_steps=2)
        assert res.status == "truncated"
        assert res.truncated is True and res.timed_out is False
        assert "[已截断]" in res.output and "[超时]" not in res.output
        assert res.error.startswith("max_steps_exhausted")
        assert res.data["timed_out"] is False
        assert task.status == SubagentStatus.FAILED

    def test_both_limits_hit_reports_more_specific_timeout(self):
        """跑满步数时预算也已耗尽 → 报 timeout（更具体的停机成因）。"""
        clock = _FakeClock()
        model = _SlowLoopModel(clock, chat_cost=10.0)
        res, _ = _run(_sub(model, _BudgetRegistry(clock)), clock,
                      timeout_s=25.0, max_steps=3)
        assert len(model.calls) == 3
        assert res.status == "timeout" and res.timed_out is True
        assert res.truncated is False

    def test_llm_error_still_error_not_timeout(self):
        """模型调用失败仍是 error（超时不得吞掉真实失败成因）。"""
        clock = _FakeClock()

        class _Boom:
            calls = []

            def chat(self, messages=None, tools=None, tool_choice="auto", **kw):
                self.calls.append(1)
                clock.advance(1.0)
                raise RuntimeError("连接被重置")

            @staticmethod
            def parse_tool_calls_from_text(content, valid_names):
                return []

        res, _ = _run(_sub(_Boom(), _BudgetRegistry(clock)), clock, timeout_s=5.0)
        assert res.status == "error"
        assert res.timed_out is False and res.truncated is False
        assert res.output == ""
        assert "连接被重置" in res.error


# ---------------------------------------------------------------------------
# 3. 预算余额下发给工具（挂死工具真被约束）
# ---------------------------------------------------------------------------
class TestToolBudgetForwarding:
    def test_remaining_budget_forwarded_and_shrinks(self):
        clock = _FakeClock()
        model = _SlowLoopModel(clock, chat_cost=10.0, rounds=2)
        reg = _BudgetRegistry(clock)
        res, _ = _run(_sub(model, reg), clock, timeout_s=100.0)
        assert res.status == "ok"
        assert [t for _, t in reg.calls] == [90.0, 80.0], (
            "每次工具调用拿到的必须是**当时余额**，不是固定值")

    def test_no_budget_means_no_timeout_kwarg(self):
        """不限时（deadline=None）→ 不下发 timeout_s，沿用工具注册值。"""
        clock = _FakeClock()
        model = _SlowLoopModel(clock, chat_cost=10.0, rounds=1)
        reg = _BudgetRegistry(clock)
        _run(_sub(model, reg), clock, timeout_s=0)
        assert [t for _, t in reg.calls] == [None]

    def test_legacy_registry_still_works_single_call_each(self):
        """旧式一参 call_from_model 零改可用，且每工具恰好一次（不重跑）。"""
        clock = _FakeClock()
        model = _SlowLoopModel(clock, chat_cost=10.0, rounds=2)
        reg = _LegacyRegistry(clock)
        res, _ = _run(_sub(model, reg), clock, timeout_s=100.0)
        assert res.status == "ok"
        assert [n for n, _ in reg.calls] == [_TOOL, _TOOL]
        assert res.tools_used == [_TOOL, _TOOL]

    def test_tool_skipped_when_budget_exhausted_mid_batch(self):
        """同一轮两个工具：第一个吃光预算 → 第二个不执行但补诚实 tool 消息。"""
        clock = _FakeClock()
        model = _SlowLoopModel(clock, chat_cost=1.0, tool_calls_per_round=2)
        reg = _BudgetRegistry(clock, tool_cost=50.0)
        sub = _sub(model, reg)
        res, _ = _run(sub, clock, timeout_s=20.0)
        assert res.status == "timeout"
        assert [n for n, _ in reg.calls] == [_TOOL], "第二个工具绝不执行"
        assert res.tools_used == [_TOOL]
        tool_msgs = [m for m in sub.context if m.get("role") == "tool"]
        assert len(tool_msgs) == 2, "配对必须补齐，不留半对"
        assert "未执行" in tool_msgs[1]["content"]
        _assert_pairs_well_formed(sub.context)

    def test_real_registry_bounds_hanging_tool(self):
        """真 ToolRegistry + 真挂死工具：预算把它掐断，任务诚实报 timeout。

        这一例必须用真实墙钟（registry 的超时是 join 真线程），故只给宽松
        上界，不做紧阈值断言。
        """
        gate = threading.Event()
        reg = ToolRegistry()

        def _hang(args):
            gate.wait(10.0)
            return ToolResult(success=True, content="不该到达")

        reg.register(name=_TOOL, description="挂死工具",
                     parameters={"type": "object", "properties": {}},
                     handler=_hang, category="test", write=False)
        clock_free_model = _SlowLoopModel(_FakeClock(), chat_cost=0.0)
        sub = Subagent(subagent_id="sub_real", agent_type="spectrum_analyzer",
                       tool_registry=reg, model_manager=clock_free_model,
                       allowed_tools=[_TOOL])
        task = SubagentTask(task_id="t_hang", goal="挂死", timeout_s=0.3)
        t0 = _real_time.time()
        try:
            res = sub.execute_task(task, max_steps=8)
        finally:
            gate.set()      # 放掉守护线程，不留悬挂
        elapsed = _real_time.time() - t0

        assert res.status == "timeout" and res.timed_out is True
        assert res.success is False
        assert elapsed < 5.0, f"预算 0.3s 却跑了 {elapsed:.2f}s，超时没生效"
        assert len(clock_free_model.calls) < 8, "循环必须被掐断，不得跑满 8 步"
        logged = reg.get_call_log(limit=5, tool_name=_TOOL)
        assert logged and logged[0]["error"] == "tool_timeout", (
            "挂死工具必须被 registry 的 per-call 超时掐断并如实记错")
        assert task.status == SubagentStatus.TIMEOUT


# ---------------------------------------------------------------------------
# 4. 序列化 / 展示层 / 工具 schema 文档一致性
# ---------------------------------------------------------------------------
class TestSurfaces:
    def test_to_dict_roundtrip_carries_timed_out(self):
        clock = _FakeClock()
        model = _SlowLoopModel(clock, chat_cost=10.0)
        res, _ = _run(_sub(model, _BudgetRegistry(clock)), clock, timeout_s=5.0)
        back = json.loads(json.dumps(res.to_dict(), ensure_ascii=False))
        assert back["timed_out"] is True
        assert back["status"] == "timeout"
        assert back["truncated"] is False
        assert SubagentResult(success=True).to_dict()["timed_out"] is False

    def test_ai_steps_note_reports_timeout_cause(self):
        payload = {"success": False, "status": "timeout", "timed_out": True,
                   "truncated": False, "tools_used": [_TOOL],
                   "subagent_id": "sub_t", "error": "timeout_exhausted: ..."}
        cards = ai_steps.steps_from_subagent(payload)
        assert len(cards) == 1
        assert "墙钟预算" in cards[0].summary
        assert "max_steps" not in cards[0].summary, "成因不得混报"
        assert cards[0].status == ai_steps.STATUS_FAILED

    def test_ai_steps_note_still_reports_max_steps_cause(self):
        payload = {"success": False, "status": "truncated", "truncated": True,
                   "timed_out": False, "tools_used": [_TOOL]}
        cards = ai_steps.steps_from_subagent(payload)
        assert "max_steps" in cards[0].summary
        assert "墙钟预算" not in cards[0].summary

    def test_manager_sync_path_honours_timeout(self):
        """Manager.execute_task（LLM 工具走的就是这条）也真受预算约束。"""
        clock = _FakeClock()
        model = _SlowLoopModel(clock, chat_cost=10.0)
        mgr = SubagentManager(tool_registry=_BudgetRegistry(clock),
                              model_manager=model)
        sid = mgr.create("spectrum_analyzer", allowed_tools=[_TOOL])
        with patch("mbdsdr_ai.subagents.time", clock):
            res = mgr.execute_task(sid, "分析频谱", timeout_s=15.0)
        assert res.status == "timeout" and res.timed_out is True
        assert mgr.get_task_result(sid, res.task_id) is res

    def test_tool_schema_documents_real_timeout_semantics(self):
        """工具面文档必须与实现一致（§2：文档-实现冲突即 bug）。"""
        from mbdsdr_ai.agent import AgentConfig, MBDSDRAgent

        agent = MBDSDRAgent(AgentConfig())
        defs = {d["function"]["name"]: d["function"]
                for d in agent.tool_registry.get_tool_definitions()}
        spec = defs["subagent_execute"]
        props = spec["parameters"]["properties"]
        assert props["timeout_s"]["default"] == 120
        assert "墙钟预算" in props["timeout_s"]["description"]
        assert "status=timeout" in spec["description"]
        assert "timed_out=true" in spec["description"]

    def test_agent_handler_forwards_timeout_s(self):
        """LLM 传的 timeout_s 必须真的送到 Manager（此前送了也被丢弃）。"""
        from mbdsdr_ai.agent import AgentConfig, MBDSDRAgent

        agent = MBDSDRAgent(AgentConfig())
        seen = {}

        def _fake_exec(subagent_id, goal, input_data=None, timeout_s=120.0,
                       max_steps=None):
            seen.update(sid=subagent_id, goal=goal, timeout_s=timeout_s,
                        max_steps=max_steps)
            return SubagentResult(success=True, output="ok", status="ok",
                                  subagent_id=subagent_id, task_id="task_1")

        agent.subagent_manager.create("spectrum_analyzer", subagent_id="sub_x")
        with patch.object(agent.subagent_manager, "execute_task", _fake_exec):
            res = agent.tool_registry.call(
                "subagent_execute",
                {"subagent_id": "sub_x", "goal": "分析", "timeout_s": 7.5,
                 "max_steps": 3})
        assert res.success is True
        assert seen == {"sid": "sub_x", "goal": "分析", "timeout_s": 7.5,
                        "max_steps": 3}