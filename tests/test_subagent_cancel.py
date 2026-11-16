# SPDX-License-Identifier: MIT
"""子代理取消链测试（#87：CANCELLED 死枚举 + 停止真的传得到子代理）。

背景（缺陷实证）：``SubagentStatus.CANCELLED`` 声明后从未被赋值（死枚举）；
``agent.request_cancel()`` 只 set 自己的 ``_cancel_event``，正在
``subagent_execute`` 里跑的子代理完全看不到它——用户点停止后子代理仍会
继续烧 LLM 调用，直到 max_steps 或 #84 的墙钟预算耗尽。

本测试钉住修好后的契约（沿 #82/#84 已建的安全点口径）：
1. 取消在**安全点**生效：步边界 / 每个工具执行前 / 循环收尾；在途的单次
   chat/工具不强杀，但取消后绝不再起新一轮 chat、绝不再执行下一个工具；
2. 结果诚实：status=cancelled + cancelled=True + 带 [已取消] 标记的 partial，
   task.status=CANCELLED；与 truncated / timed_out **三者互斥**，成因分开报；
3. 取消是「本次任务」的事：任务起点清标志，空闲时取消是 no-op，取消后
   同一子代理仍能接下一个任务（串行闸与全局槽位都归还）；
4. 排队中的任务被取消后**绝不执行**（不是稍后偷跑）；
5. manager.cancel(单个/全部) 与 agent.request_cancel 的传播真的到位；
6. 展示层（desktop/ai_steps）按 cancelled 单独出成因说明，绝不混报。

同步手法：闸门 Event + 计数不变量（chat 轮数、工具执行次数），不绑墙钟。
"""
from __future__ import annotations

import os
import sys
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.subagents import (  # noqa: E402
    SubagentManager, SubagentStatus, SubagentTask,
)
from mbdsdr_ai.tool_registry import ToolRegistry, ToolResult  # noqa: E402


# ---------------------------------------------------------------------------
# 脚手架
# ---------------------------------------------------------------------------
def _tc(name, call_id="c1", args="{}"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": args}}


class _ScriptedModel:
    """按脚本逐轮返回；记录 chat 轮数与每轮看到的消息数。"""

    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.calls = 0
        self.lock = threading.Lock()

    def chat(self, messages=None, tools=None, tool_choice="auto", **kw):
        with self.lock:
            i = self.calls
            self.calls += 1
        if i < len(self.rounds):
            return self.rounds[i]
        return {"success": True, "content": "脚本用尽后的收尾结论", "tool_calls": []}

    @staticmethod
    def parse_tool_calls_from_text(content, valid_names):
        return []


class _RecordingRegistry:
    """记录真实执行过的工具名；可指定某个工具在执行时请求取消。"""

    def __init__(self, on_call=None):
        self.executed = []
        self.on_call = on_call
        self.lock = threading.Lock()

    def get_tool_names(self):
        return ["t1", "t2"]

    def get_tool_definitions(self):
        return []

    def call_from_model(self, tool_call, *, timeout_s=None):
        name = (tool_call.get("function") or {}).get("name", "")
        with self.lock:
            self.executed.append(name)
        if self.on_call is not None:
            self.on_call(name)
        return ToolResult(success=True, content=f"{name} 的真实结果",
                          tool_name=name)


def _wait_for(pred, timeout=5.0, interval=0.01):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(interval)
    return bool(pred())


def _mgr(model, registry, max_concurrent=4):
    return SubagentManager(tool_registry=registry, model_manager=model,
                           max_concurrent=max_concurrent)


def _setup(rounds, on_call=None, max_concurrent=4, allowed=("t1", "t2")):
    reg = _RecordingRegistry(on_call=on_call)
    model = _ScriptedModel(rounds)
    mgr = _mgr(model, reg, max_concurrent)
    sid = mgr.create("spectrum_analyzer", subagent_id="solo",
                     allowed_tools=list(allowed))
    return mgr, sid, model, reg


def _assert_pairs_well_formed(context):
    """assistant(tool_calls) 与 tool 消息必须成对（取消不得撕破配对）。"""
    pending = []
    for msg in context:
        role = msg.get("role")
        if role == "assistant" and msg.get("tool_calls"):
            pending = [tc.get("id") for tc in msg["tool_calls"]]
        elif role == "tool":
            assert pending, f"tool 消息没有对应的 assistant(tool_calls): {msg}"
            assert msg.get("tool_call_id") in pending
            pending.remove(msg.get("tool_call_id"))
    assert not pending, f"有 tool_calls 没等到 tool 回灌: {pending}"


# ---------------------------------------------------------------------------
# 1. 安全点：取消后不再起新轮次、不再执行下一个工具
# ---------------------------------------------------------------------------
class TestCancelSafePoints:
    def test_cancel_in_tool_stops_before_next_round(self):
        rounds = [{"success": True, "content": "先测一下",
                   "tool_calls": [_tc("t1")]},
                  {"success": True, "content": "第二轮不该出现", "tool_calls": []}]
        holder = {}

        def _on_call(name):
            holder["sub"].request_cancel()

        mgr, sid, model, reg = _setup(rounds, on_call=_on_call)
        sub = mgr.get(sid)
        holder["sub"] = sub
        res = sub.execute_task(SubagentTask(task_id="A", goal="任务A"))

        assert res.success is False
        assert res.status == "cancelled"
        assert res.cancelled is True
        assert res.truncated is False and res.timed_out is False
        assert model.calls == 1, "取消后绝不再起新一轮 chat"
        assert reg.executed == ["t1"], "在途工具不强杀，但只执行了这一个"
        assert res.tools_used == ["t1"]
        assert "[已取消]" in res.output
        assert "先测一下" in res.output, "取消前的部分结论必须保留"
        assert res.error.startswith("cancelled")
        assert sub.total_tasks_failed == 1 and sub.total_tasks_completed == 0
        assert sub.status == SubagentStatus.IDLE
        assert sub.current_task is None
        task = sub.task_history[0]
        assert task.status == SubagentStatus.CANCELLED
        assert task.result is res
        _assert_pairs_well_formed(sub.context)

    def test_cancel_skips_remaining_tools_in_batch(self):
        rounds = [{"success": True, "content": "批量测",
                   "tool_calls": [_tc("t1", "c1"), _tc("t2", "c2")]}]
        holder = {}

        def _on_call(name):
            if name == "t1":
                holder["sub"].request_cancel()

        mgr, sid, model, reg = _setup(rounds, on_call=_on_call)
        sub = mgr.get(sid)
        holder["sub"] = sub
        res = sub.execute_task(SubagentTask(task_id="A", goal="任务A"))

        assert res.status == "cancelled"
        assert reg.executed == ["t1"], "取消后同批次的下一个工具绝不执行"
        assert model.calls == 1
        # 被跳过的工具仍补一条诚实 tool 消息，配对完整
        tool_msgs = [m for m in sub.context if m.get("role") == "tool"]
        assert len(tool_msgs) == 2
        assert "未执行" in tool_msgs[1]["content"]
        _assert_pairs_well_formed(sub.context)

    def test_idle_cancel_does_not_poison_next_task(self):
        rounds = [{"success": True, "content": "正常结论", "tool_calls": []}]
        mgr, sid, model, reg = _setup(rounds)
        sub = mgr.get(sid)
        assert sub.request_cancel() is False, "空闲时取消是 no-op"
        res = sub.execute_task(SubagentTask(task_id="A", goal="任务A"))
        assert res.success is True and res.status == "ok", (
            "空闲期的取消请求不得毒害下一个任务（任务起点清标志）")
        assert res.cancelled is False
        assert model.calls == 1

    def test_cancel_with_no_text_is_honest(self):
        rounds = [{"success": True, "content": "", "tool_calls": [_tc("t1")]}]
        holder = {}
        mgr, sid, model, reg = _setup(rounds,
                                      on_call=lambda n: holder["sub"].request_cancel())
        sub = mgr.get(sid)
        holder["sub"] = sub
        res = sub.execute_task(SubagentTask(task_id="A", goal="任务A"))
        assert res.status == "cancelled"
        assert "[已取消]" in res.output
        assert "未产出任何文本结论" in res.output
        assert "t1" in res.output, "已执行的工具要如实列出"

    def test_cancel_wins_over_max_steps(self):
        """跑满 max_steps 且期间被取消 → 报 cancelled（用户显式意图优先）。"""
        rounds = [{"success": True, "content": f"第{i}轮",
                   "tool_calls": [_tc("t1", f"c{i}")]} for i in range(4)]
        mgr, sid, model, reg = _setup(rounds)
        sub = mgr.get(sid)

        def _on_call(name):
            # 最后一轮的工具里取消：循环收尾时 truncated 与 cancelled 同时成立
            if model.calls >= 2:
                sub.request_cancel()

        reg.on_call = _on_call
        res = sub.execute_task(SubagentTask(task_id="A", goal="任务A"), max_steps=2)
        assert res.status == "cancelled"
        assert res.cancelled is True and res.truncated is False
        assert res.timed_out is False
        assert res.data["cancelled"] is True
        assert res.data["truncated"] is False
        assert model.calls == 2 and reg.executed == ["t1", "t1"]

    def test_cancel_wins_over_timeout(self):
        """工具在途时预算耗尽且用户取消 → 报 cancelled，绝不混报 timeout。"""
        rounds = [{"success": True, "content": "先测", "tool_calls": [_tc("t1")]},
                  {"success": True, "content": "不该出现", "tool_calls": []}]
        mgr, sid, model, reg = _setup(rounds)
        sub = mgr.get(sid)

        def _slow_then_cancel(name):
            time.sleep(0.6)      # 远超 0.25s 预算：下一个安全点必然已过期
            sub.request_cancel()

        reg.on_call = _slow_then_cancel
        res = sub.execute_task(
            SubagentTask(task_id="A", goal="任务A", timeout_s=0.25))
        assert res.status == "cancelled"
        assert res.cancelled is True and res.timed_out is False
        assert res.truncated is False
        assert model.calls == 1, "取消后绝不再起新一轮"


# ---------------------------------------------------------------------------
# 2. 取消是本次任务的事：标志清理 + 资源归还
# ---------------------------------------------------------------------------
class TestCancelLifecycle:
    def test_next_task_after_cancel_runs_normally(self):
        rounds = [{"success": True, "content": "第一轮", "tool_calls": [_tc("t1")]},
                  {"success": True, "content": "不该出现", "tool_calls": []}]
        holder = {}
        mgr, sid, model, reg = _setup(
            rounds, on_call=lambda n: holder["sub"].request_cancel())
        sub = mgr.get(sid)
        holder["sub"] = sub
        first = sub.execute_task(SubagentTask(task_id="A", goal="任务A"))
        assert first.status == "cancelled"

        model.rounds = [{"success": True, "content": "新任务结论", "tool_calls": []}]
        model.calls = 0
        second = sub.execute_task(SubagentTask(task_id="B", goal="任务B"))
        assert second.success is True and second.status == "ok", (
            "上一个任务的取消标志不得毒害下一个任务")
        assert second.output == "新任务结论"
        assert second.cancelled is False

    def test_cancel_releases_serial_lock_and_global_slot(self):
        rounds = [{"success": True, "content": "占坑", "tool_calls": [_tc("t1")]}]
        holder = {}
        mgr, sid, model, reg = _setup(
            rounds, on_call=lambda n: holder["sub"].request_cancel(),
            max_concurrent=1)
        sub = mgr.get(sid)
        holder["sub"] = sub
        res = mgr.execute_task(sid, "任务A")
        assert res.status == "cancelled"
        # 串行闸与全局槽位都必须归还：同一子代理立刻能接新任务
        model.rounds = [{"success": True, "content": "后续结论", "tool_calls": []}]
        model.calls = 0
        again = mgr.execute_task(sid, "任务B")
        assert again.success is True, f"取消后资源没归还: {again.error}"
        assert mgr.get_stats()["running_subagents"] == 0

    def test_idle_cancel_is_noop_and_returns_false(self):
        mgr, sid, model, reg = _setup([])
        sub = mgr.get(sid)
        assert sub.request_cancel() is False
        assert sub.request_cancel() is False
        assert sub.status == SubagentStatus.IDLE


# ---------------------------------------------------------------------------
# 3. 观测面：事件、data、to_dict、展示层
# ---------------------------------------------------------------------------
class TestCancelObservability:
    def test_cancel_event_logged_and_bubbled(self):
        bubbled = []
        reg = _RecordingRegistry()
        model = _ScriptedModel([
            {"success": True, "content": "部分", "tool_calls": [_tc("t1")]}])
        mgr = SubagentManager(tool_registry=reg, model_manager=model,
                              max_concurrent=4,
                              on_event=lambda sid, et, f: bubbled.append((sid, et, f)))
        sid = mgr.create("spectrum_analyzer", subagent_id="solo",
                         allowed_tools=["t1"])
        sub = mgr.get(sid)
        reg.on_call = lambda n: sub.request_cancel()
        res = sub.execute_task(SubagentTask(task_id="A", goal="任务A"))
        assert res.status == "cancelled"
        types = [et for _sid, et, _f in bubbled]
        assert "cancelled" in types, f"取消必须落观测事件: {types}"
        for s, _et, fields in bubbled:
            assert s == "solo"
            assert "subagent_id" not in fields, "冒泡字段不得重复带 subagent_id"
        end = [f for _s, et, f in bubbled if et == "task_end"][-1]
        assert end["status"] == "cancelled" and end["cancelled"] is True

    def test_to_dict_carries_cancelled(self):
        mgr, sid, model, reg = _setup(
            [{"success": True, "content": "x", "tool_calls": [_tc("t1")]}],
            on_call=lambda n: mgr.get(sid).request_cancel())
        res = mgr.get(sid).execute_task(SubagentTask(task_id="A", goal="任务A"))
        d = res.to_dict()
        assert d["cancelled"] is True
        assert d["status"] == "cancelled"
        assert d["truncated"] is False and d["timed_out"] is False

    def test_ai_steps_reports_cancel_cause_not_truncation(self):
        from desktop.ai_steps import steps_from_subagent
        payload = {"success": False, "status": "cancelled", "cancelled": True,
                   "truncated": False, "timed_out": False,
                   "tools_used": ["t1"], "subagent_id": "solo", "error": "cancelled"}
        cards = steps_from_subagent(payload)
        assert len(cards) == 1
        assert "取消" in cards[0].summary
        assert "截断" not in cards[0].summary
        assert "墙钟" not in cards[0].summary


# ---------------------------------------------------------------------------
# 4. manager 级取消 + 排队任务取消 + agent 传播
# ---------------------------------------------------------------------------
class TestManagerCancel:
    def test_cancel_running_subagent(self):
        gate = threading.Event()
        entered = threading.Event()

        class _Gated:
            def chat(self, messages=None, tools=None, tool_choice="auto", **kw):
                entered.set()
                gate.wait(10.0)
                return {"success": True, "content": "结论", "tool_calls": []}

            @staticmethod
            def parse_tool_calls_from_text(content, valid_names):
                return []

        mgr = SubagentManager(tool_registry=_RecordingRegistry(),
                              model_manager=_Gated(), max_concurrent=4)
        sid = mgr.create("spectrum_analyzer", subagent_id="solo", allowed_tools=[])
        mgr.submit_task(sid, "任务A")
        assert entered.wait(5.0)
        assert mgr.cancel(sid) == 1, "在跑的子代理必须被取消到"
        gate.set()
        sub = mgr.get(sid)
        assert _wait_for(lambda: len(sub.task_history) == 1)
        # 在途 chat 不强杀：它返回后在安全点收尾为 cancelled
        assert sub.task_history[0].result.status == "cancelled"
        assert mgr.cancel(sid) == 0, "已空闲再取消是 no-op"

    def test_cancel_all_running(self):
        gate = threading.Event()
        entered = []
        lock = threading.Lock()

        class _Gated:
            def chat(self, messages=None, tools=None, tool_choice="auto", **kw):
                with lock:
                    entered.append(1)
                gate.wait(10.0)
                return {"success": True, "content": "结论", "tool_calls": []}

            @staticmethod
            def parse_tool_calls_from_text(content, valid_names):
                return []

        mgr = SubagentManager(tool_registry=_RecordingRegistry(),
                              model_manager=_Gated(), max_concurrent=4)
        sids = [mgr.create("spectrum_analyzer", subagent_id=f"s{i}",
                           allowed_tools=[]) for i in range(3)]
        for sid in sids:
            mgr.submit_task(sid, "任务")
        assert _wait_for(lambda: len(entered) == 3)
        assert mgr.cancel() == 3
        gate.set()
        assert _wait_for(lambda: all(len(mgr.get(s).task_history) == 1 for s in sids))
        for sid in sids:
            assert mgr.get(sid).task_history[0].result.status == "cancelled"

    def test_cancel_queued_task_never_executes(self):
        gate = threading.Event()
        entered = []
        lock = threading.Lock()

        class _Gated:
            def chat(self, messages=None, tools=None, tool_choice="auto", **kw):
                text = " ".join(str(m.get("content", "") or "") for m in (messages or []))
                with lock:
                    entered.append("任务B" if "任务B" in text else "任务A")
                gate.wait(10.0)
                return {"success": True, "content": "结论", "tool_calls": []}

            @staticmethod
            def parse_tool_calls_from_text(content, valid_names):
                return []

        mgr = SubagentManager(tool_registry=_RecordingRegistry(),
                              model_manager=_Gated(), max_concurrent=1)
        sid = mgr.create("spectrum_analyzer", subagent_id="solo", allowed_tools=[])
        mgr.submit_task(sid, "任务A")
        assert _wait_for(lambda: entered == ["任务A"])
        mgr.submit_task(sid, "任务B")
        assert _wait_for(lambda: len(mgr.task_queue) == 1)

        mgr.cancel()                      # 在跑的 A + 排队的 B 一起取消
        gate.set()
        sub = mgr.get(sid)
        assert _wait_for(lambda: len(sub.task_history) == 2)
        assert "任务B" not in entered, "排队任务被取消后绝不执行"
        assert mgr.task_queue == []
        by_goal = {t.goal: t for t in sub.task_history}
        assert by_goal["任务B"].status == SubagentStatus.CANCELLED
        rb = by_goal["任务B"].result
        assert rb.cancelled is True and rb.success is False
        assert rb.steps_used == 0 and rb.tools_used == []
        assert "排队" in rb.error, "必须说清是排队期间被取消（根本没跑）"
        assert by_goal["任务A"].result.status == "cancelled"

    def test_agent_request_cancel_propagates(self):
        from mbdsdr_ai.agent import AgentConfig, MBDSDRAgent
        agent = MBDSDRAgent(AgentConfig())
        sm = agent.subagent_manager
        gate = threading.Event()
        entered = threading.Event()

        class _Gated:
            def chat(self, messages=None, tools=None, tool_choice="auto", **kw):
                entered.set()
                gate.wait(10.0)
                return {"success": True, "content": "结论", "tool_calls": []}

            @staticmethod
            def parse_tool_calls_from_text(content, valid_names):
                return []

        sid = sm.create("spectrum_analyzer", subagent_id="solo", allowed_tools=[])
        sm.subagents[sid].model_manager = _Gated()
        sm.submit_task(sid, "任务A")
        assert entered.wait(5.0)
        try:
            agent.request_cancel()
            assert sm.get(sid)._cancel_event.is_set(), (
                "agent.request_cancel 必须传播到在跑的子代理")
        finally:
            gate.set()
        assert _wait_for(lambda: len(sm.get(sid).task_history) == 1)
        assert sm.get(sid).task_history[0].result.status == "cancelled"