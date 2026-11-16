# SPDX-License-Identifier: MIT
"""#91：模型侧子代理生命周期工具（subagent_cancel / subagent_destroy）。

缺口原状（读码实证）：``agent._register_subagent_tools`` 只注册
create / list / execute / stats 四个工具——模型能造子代理、能派任务，却
**没有任何办法停掉或回收**它。长会话里子代理只增不减（每个还带一份独立
SessionLog），跑飞的子代理只能等用户按面板停止（#88）一起全掐。

本文件钉住补齐后的契约：
1. 两个工具真注册、真能停/真能回收（不是对着空气喊话）；
2. 计数诚实：cancelled_running / cancelled_queued 分开报，没命中就说没命中，
   绝不把「处理了取消请求」写成「已杀掉任务」；
3. id 不存在走 error_catalog 的 subagent_not_found（结构化失败，不抛裸异常）；
4. **停机/回收不设 confirm_token 硬门**：让「停」比「跑」还难违背 #88 的
   可达性口径；两者都不发射/不删文件/不回滚代码，不属硬门家族；
5. 递归守卫：子代理自己的工具视图里看不到 subagent_*（含新加的两个）。

同步手法：闸门 Event + 台账计数，不绑墙钟。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.agent import AgentConfig, MBDSDRAgent  # noqa: E402
from mbdsdr_ai.subagents import SubagentStatus  # noqa: E402


class _GatedModel:
    def __init__(self):
        self.gate = threading.Event()
        self.rounds = 0
        self.lock = threading.Lock()

    def chat(self, messages=None, tools=None, tool_choice="auto", **kw):
        with self.lock:
            self.rounds += 1
        self.gate.wait(10.0)
        return {"success": True, "content": "结论", "tool_calls": []}

    @staticmethod
    def parse_tool_calls_from_text(content, valid_names):
        return []


def _wait_for(pred, timeout=5.0, interval=0.01):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(interval)
    return bool(pred())


def _agent():
    return MBDSDRAgent(AgentConfig())


def _call(agent, name, args=None):
    return agent.tool_registry.call_from_model({
        "id": "c1", "type": "function",
        "function": {"name": name, "arguments": json.dumps(args or {})},
    })


def _running(agent, sid="solo", goal="任务A"):
    """造一个真在跑（卡在闸门上）的子代理任务，返回 (model, sub)。"""
    sm = agent.subagent_manager
    sm.create("spectrum_analyzer", subagent_id=sid, allowed_tools=[])
    sub = sm.get(sid)
    model = _GatedModel()
    sub.model_manager = model
    sm.submit_task(sid, goal)
    assert _wait_for(lambda: model.rounds == 1), "任务没真起跑"
    return model, sub


class TestRegistration:
    def test_both_tools_registered(self):
        agent = _agent()
        names = agent.tool_registry.get_tool_names()
        assert "subagent_cancel" in names and "subagent_destroy" in names
        for n in ("subagent_cancel", "subagent_destroy"):
            tool = agent.tool_registry.tools[n]
            assert tool["category"] == "subagent"
            desc = tool["definition"]["function"]["description"]
            assert desc and "子代理" in desc

    def test_stop_and_reclaim_are_not_hard_gated(self):
        """停机/回收不该比发起还难：不设写门，模型通道无需 confirm_token。"""
        agent = _agent()
        for n in ("subagent_cancel", "subagent_destroy"):
            assert agent.tool_registry.is_write_tool(n) is False, (
                f"{n} 被当成写工具 → 模型停不下来，违背 #88 可达性口径")

    def test_subagent_view_excludes_new_lifecycle_tools(self):
        agent = _agent()
        sm = agent.subagent_manager
        sid = sm.create("spectrum_analyzer", subagent_id="solo")  # allowed=None
        sub = sm.get(sid)
        visible = sub._effective_allowed_names()
        assert not any(n.startswith("subagent_") for n in visible), (
            "子代理视图里出现 subagent_* = 递归自激入口")


class TestCancelTool:
    def test_cancel_running_reports_one(self):
        agent = _agent()
        model, sub = _running(agent)
        try:
            res = _call(agent, "subagent_cancel", {"subagent_id": "solo"})
            assert res.success is True
            assert res.data["cancelled_running"] == 1
            assert sub._cancel_event.is_set()
        finally:
            model.gate.set()
        assert _wait_for(lambda: len(sub.task_history) == 1)
        assert sub.task_history[0].result.status == "cancelled"

    def test_cancel_all_when_id_omitted(self):
        agent = _agent()
        m1, s1 = _running(agent, sid="a", goal="任务A")
        m2, s2 = _running(agent, sid="b", goal="任务B")
        try:
            res = _call(agent, "subagent_cancel", {})
            assert res.success is True
            assert res.data["cancelled_running"] == 2
        finally:
            m1.gate.set()
            m2.gate.set()
        assert _wait_for(lambda: len(s1.task_history) == 1
                         and len(s2.task_history) == 1)
        assert s1.task_history[0].result.status == "cancelled"
        assert s2.task_history[0].result.status == "cancelled"

    def test_cancel_nothing_running_is_honest_zero(self):
        agent = _agent()
        agent.subagent_manager.create("spectrum_analyzer", subagent_id="idle",
                                      allowed_tools=[])
        res = _call(agent, "subagent_cancel", {"subagent_id": "idle"})
        assert res.success is True
        assert res.data["cancelled_running"] == 0
        assert res.data["cancelled_queued"] == 0
        assert "没有" in res.content, "没命中却不说清 = 谎报已取消"

    def test_cancel_unknown_id_is_structured_failure(self):
        agent = _agent()
        res = _call(agent, "subagent_cancel", {"subagent_id": "不存在"})
        assert res.success is False
        assert res.error == "subagent_not_found"
        assert res.next_step

    def test_cancel_queued_counts_separately(self):
        agent = _agent()
        sm = agent.subagent_manager
        sm.max_concurrent = 1
        sm._slots = threading.BoundedSemaphore(1)
        m1, s1 = _running(agent, sid="hold", goal="占位任务")
        sm.create("spectrum_analyzer", subagent_id="victim", allowed_tools=[])
        victim = sm.get("victim")
        victim.model_manager = _GatedModel()
        got = []
        sm.submit_task("victim", "排队任务", callback=got.append)
        assert _wait_for(lambda: len(sm.task_queue) == 1)
        try:
            res = _call(agent, "subagent_cancel", {"subagent_id": "victim"})
            assert res.success is True
            assert res.data["cancelled_running"] == 0
            assert res.data["cancelled_queued"] == 1
            assert sm.task_queue == []
            assert len(got) == 1 and got[0].cancelled is True
            assert got[0].data.get("cancelled_before_start") is True
        finally:
            m1.gate.set()
        assert _wait_for(lambda: len(s1.task_history) == 1)
        assert victim.model_manager.rounds == 0, "排队任务被取消后仍执行了"


class TestDestroyTool:
    def test_destroy_running_stops_the_work(self):
        agent = _agent()
        model, sub = _running(agent)
        try:
            res = _call(agent, "subagent_destroy", {"subagent_id": "solo"})
            assert res.success is True
            assert agent.subagent_manager.get("solo") is None
            assert sub._cancel_event.is_set(), "destroy 工具没真停活（#90）"
        finally:
            model.gate.set()
        assert _wait_for(lambda: len(sub.task_history) == 1)
        assert sub.task_history[0].result.status == "cancelled"

    def test_destroy_unknown_id_is_structured_failure(self):
        agent = _agent()
        res = _call(agent, "subagent_destroy", {"subagent_id": "不存在"})
        assert res.success is False
        assert res.error == "subagent_not_found"

    def test_destroy_requires_id(self):
        agent = _agent()
        res = _call(agent, "subagent_destroy", {})
        assert res.success is False, "缺必填参数却执行了 = 参数门失效"
        # 必须红在「参数门」上，而不是「工具压根不存在」——后者是另一回事，
        # 只断言 success is False 会让工具面缺口在这条用例上假绿。
        assert res.error == "missing_required_params"

    def test_destroy_content_is_honest_about_safe_points(self):
        agent = _agent()
        agent.subagent_manager.create("spectrum_analyzer", subagent_id="idle",
                                      allowed_tools=[])
        res = _call(agent, "subagent_destroy", {"subagent_id": "idle"})
        assert res.success is True
        assert "安全点" in res.content or "不强杀" in res.content, (
            "销毁文案必须说清协作式取消口径，不能让用户以为在途调用被强杀")