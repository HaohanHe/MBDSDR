# SPDX-License-Identifier: MIT
"""#90：destroy() 必须真的停掉子代理的活，而不是只把台账摘掉。

缺陷原状（读码实证，可反证）：``SubagentManager.destroy()`` 只做
``self.subagents.pop(sid)`` + 落一条 destroyed 事件。而 ``submit_task`` 起的
daemon worker 持有 **subagent 对象的直接引用**，于是：

  1. destroy 返回 True 之后，在跑的 LLM 循环继续烧 token、继续调工具
     （白名单里可能有写工具），用户以为「已经删掉了」；
  2. ``task_queue`` 里属于它的 WAITING 任务照样会被 worker 执行；
  3. 此后 ``cancel(sid)`` 因 id 不在注册表返回 0，``cancel()`` 遍历
     ``self.subagents.values()`` 也找不到它 —— 孤儿任务变成**不可取消**；
  4. ``get_stats()["queued_tasks"]`` 继续把这些孤儿排队任务算进去，状态面说谎。

与 #87「停止按钮传不到子代理」同族：都是「用户以为停了，其实没停」。

本文件钉住修好后的契约：destroy 在**与 worker 起跑声明互斥的同一个锁窗口**里
先掐在跑、再判死排队、最后摘台账；协作式取消口径不变（在途单次 chat/工具不
强杀，partial 结果仍走 task.callback）；绝不误伤别的子代理；销毁后用同一 id
重建不被上一轮的取消信号毒害。

同步手法：闸门 Event + 台账计数不变量（task_history 长度 / chat 轮数），
不绑墙钟。drain 条件 ``len(task_history) == N`` 在修复前后两个世界都成立
（修复前任务真跑完也会 append），故反证探针能观察到「孤儿真的跑了」。

跑法：python -m pytest tests/test_subagent_destroy.py -q
"""
from __future__ import annotations

import os
import sys
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.subagents import (  # noqa: E402
    SubagentManager, SubagentStatus,
)
from mbdsdr_ai.tool_registry import ToolResult  # noqa: E402


# ---------------------------------------------------------------------------
# 脚手架（与 tests/test_subagent_cancel.py 同一套口径）
# ---------------------------------------------------------------------------
class _RecordingRegistry:
    """记录真实执行过的工具名（子代理循环只用到这三个方法）。"""

    def __init__(self):
        self.executed = []
        self.lock = threading.Lock()

    def get_tool_names(self):
        return ["t1", "t2"]

    def get_tool_definitions(self):
        return []

    def call_from_model(self, tool_call, *, timeout_s=None):
        name = (tool_call.get("function") or {}).get("name", "")
        with self.lock:
            self.executed.append(name)
        return ToolResult(success=True, content=f"{name} 的真实结果",
                          tool_name=name)


class _GatedModel:
    """chat 卡在闸门上：让主线程有机会在「在途」状态下 destroy。

    按任务目标记录进入顺序（goal 出现在 messages 里），据此断言
    「排队任务被销毁后绝没执行」。
    """

    def __init__(self):
        self.gate = threading.Event()
        self.entered = []
        self.rounds = 0
        self.lock = threading.Lock()

    def chat(self, messages=None, tools=None, tool_choice="auto", **kw):
        text = " ".join(str(m.get("content", "") or "") for m in (messages or []))
        with self.lock:
            self.rounds += 1
            self.entered.append(text)
        self.gate.wait(10.0)
        return {"success": True, "content": "结论", "tool_calls": []}

    @staticmethod
    def parse_tool_calls_from_text(content, valid_names):
        return []

    def saw(self, goal):
        with self.lock:
            return any(goal in t for t in self.entered)


def _wait_for(pred, timeout=5.0, interval=0.01):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(interval)
    return bool(pred())


def _mgr(model=None, max_concurrent=4, events=None):
    return SubagentManager(
        tool_registry=_RecordingRegistry(),
        model_manager=model if model is not None else _GatedModel(),
        max_concurrent=max_concurrent,
        on_event=(lambda sid, et, fields: events.append((sid, et, dict(fields))))
        if events is not None else None,
    )


def _events_of(events, etype, sid=None):
    return [f for (s, t, f) in events
            if t == etype and (sid is None or s == sid)]


# ---------------------------------------------------------------------------
# 1. 空闲销毁：既有契约不许被改坏（修复前也绿，是反向护栏）
# ---------------------------------------------------------------------------
class TestDestroyIdle:
    def test_destroy_idle_removes_it(self):
        mgr = _mgr()
        sid = mgr.create("spectrum_analyzer", subagent_id="solo", allowed_tools=[])
        assert mgr.destroy(sid) is True
        assert mgr.get(sid) is None
        assert mgr.list_subagents() == []

    def test_destroy_unknown_id_is_false_not_raise(self):
        mgr = _mgr()
        assert mgr.destroy("不存在的id") is False

    def test_destroy_is_idempotent(self):
        mgr = _mgr()
        sid = mgr.create("spectrum_analyzer", subagent_id="solo", allowed_tools=[])
        assert mgr.destroy(sid) is True
        assert mgr.destroy(sid) is False

    def test_submit_after_destroy_is_honest_error(self):
        mgr = _mgr()
        sid = mgr.create("spectrum_analyzer", subagent_id="solo", allowed_tools=[])
        mgr.destroy(sid)
        try:
            mgr.submit_task(sid, "任务")
        except ValueError as e:
            assert "不存在" in str(e)
        else:
            raise AssertionError("销毁后还能提交任务 = 台账与执行面不一致")

    def test_cancel_without_destroy_still_works(self):
        """反向护栏：#87 的取消路径不许被 #90 改坏。"""
        model = _GatedModel()
        mgr = _mgr(model)
        sid = mgr.create("spectrum_analyzer", subagent_id="solo", allowed_tools=[])
        mgr.submit_task(sid, "任务A")
        assert _wait_for(lambda: model.rounds == 1)
        try:
            assert mgr.cancel(sid) == 1
        finally:
            model.gate.set()
        sub = mgr.get(sid)
        assert _wait_for(lambda: len(sub.task_history) == 1)
        assert sub.task_history[0].result.status == "cancelled"


# ---------------------------------------------------------------------------
# 2. 在跑时销毁：必须真停，且停的动作发生在 destroy 里（不是靠事后 cancel）
# ---------------------------------------------------------------------------
class TestDestroyRunning:
    def test_destroy_stops_running_task(self):
        model = _GatedModel()
        mgr = _mgr(model)
        sid = mgr.create("spectrum_analyzer", subagent_id="solo", allowed_tools=[])
        sub = mgr.get(sid)                      # 销毁后注册表就查不到了，先留引用
        mgr.submit_task(sid, "任务A")
        assert _wait_for(lambda: model.rounds == 1), "任务没真起跑"
        try:
            assert mgr.destroy(sid) is True
            assert sub._cancel_event.is_set(), "destroy 没给在跑任务置取消信号"
        finally:
            model.gate.set()
        assert _wait_for(lambda: len(sub.task_history) == 1), "任务没收尾"
        res = sub.task_history[0].result
        assert res.status == "cancelled" and res.cancelled is True
        assert res.success is False
        # 在途单次 chat 不强杀（安全点口径），但绝不再起新一轮
        assert model.rounds == 1, "销毁后还在烧新的模型轮次"

    def test_destroy_makes_orphan_uncancellable_is_fixed(self):
        """缺陷核心：destroy 之后 cancel() 找不到它 —— 所以 destroy 自己必须停。"""
        model = _GatedModel()
        mgr = _mgr(model)
        sid = mgr.create("spectrum_analyzer", subagent_id="solo", allowed_tools=[])
        sub = mgr.get(sid)
        mgr.submit_task(sid, "任务A")
        assert _wait_for(lambda: model.rounds == 1)
        mgr.destroy(sid)
        # 注册表已空：事后任何 cancel 都够不着这个孤儿
        assert mgr.cancel(sid) == 0 and mgr.cancel() == 0
        model.gate.set()
        assert _wait_for(lambda: len(sub.task_history) == 1)
        assert sub.task_history[0].result.status == "cancelled", (
            "destroy 没停活，事后又取消不到 = 不可取消的孤儿任务")

    def test_destroy_delivers_partial_result_to_callback(self):
        model = _GatedModel()
        mgr = _mgr(model)
        sid = mgr.create("spectrum_analyzer", subagent_id="solo", allowed_tools=[])
        got = []
        mgr.submit_task(sid, "任务A", callback=got.append)
        assert _wait_for(lambda: model.rounds == 1)
        mgr.destroy(sid)
        model.gate.set()
        assert _wait_for(lambda: len(got) == 1), "销毁后调用方拿不到任何结果"
        assert got[0].cancelled is True and got[0].success is False
        assert got[0].task_id and got[0].subagent_id == sid

    def test_destroy_does_not_touch_other_subagents(self):
        model = _GatedModel()
        mgr = _mgr(model)
        a = mgr.create("spectrum_analyzer", subagent_id="A", allowed_tools=[])
        b = mgr.create("spectrum_analyzer", subagent_id="B", allowed_tools=[])
        sub_b = mgr.get(b)
        mgr.submit_task(a, "任务A")
        mgr.submit_task(b, "任务B")
        assert _wait_for(lambda: model.rounds == 2)
        try:
            mgr.destroy(a)
            assert not sub_b._cancel_event.is_set(), "销毁 A 误伤了 B"
            assert sub_b.status == SubagentStatus.RUNNING
        finally:
            model.gate.set()
        assert _wait_for(lambda: len(sub_b.task_history) == 1)
        assert sub_b.task_history[0].result.status == "ok", "B 被误报成取消"

    def test_destroyed_event_reports_honest_counts(self):
        model = _GatedModel()
        events = []
        mgr = _mgr(model, events=events)
        sid = mgr.create("spectrum_analyzer", subagent_id="solo", allowed_tools=[])
        mgr.submit_task(sid, "任务A")
        assert _wait_for(lambda: model.rounds == 1)
        mgr.destroy(sid)
        model.gate.set()
        destroyed = _events_of(events, "destroyed", sid)
        assert len(destroyed) == 1
        assert destroyed[0]["cancelled_running"] == 1
        assert destroyed[0]["cancelled_queued"] == 0


# ---------------------------------------------------------------------------
# 3. 排队时销毁：判死 + 出队 + 绝不偷跑 + 状态面不说谎
# ---------------------------------------------------------------------------
class TestDestroyQueued:
    def _blocked(self, max_concurrent=1):
        """一个占满槽位的在跑任务 + 一个只有排队任务的受害子代理。"""
        model = _GatedModel()
        events = []
        mgr = _mgr(model, max_concurrent=max_concurrent, events=events)
        hold = mgr.create("spectrum_analyzer", subagent_id="hold", allowed_tools=[])
        victim = mgr.create("spectrum_analyzer", subagent_id="victim",
                            allowed_tools=[])
        mgr.submit_task(hold, "占位任务")
        assert _wait_for(lambda: model.rounds == 1), "占位任务没起跑"
        return model, events, mgr, hold, victim

    def test_destroy_cancels_queued_tasks_and_they_never_run(self):
        model, events, mgr, hold, victim = self._blocked()
        sub = mgr.get(victim)
        got = []
        mgr.submit_task(victim, "受害任务1", callback=got.append)
        mgr.submit_task(victim, "受害任务2", callback=got.append)
        assert _wait_for(lambda: len(mgr.task_queue) == 2)
        try:
            assert mgr.destroy(victim) is True
            assert mgr.task_queue == [], "孤儿排队任务没出队"
            assert len(got) == 2, "排队任务的调用方没拿到诚实结果"
            for res in got:
                assert res.cancelled is True and res.success is False
                assert res.steps_used == 0 and res.tools_used == []
                assert res.data.get("cancelled_before_start") is True
                assert "排队" in res.error
        finally:
            model.gate.set()
        # drain：修复前这两个任务会真跑完并 append，修复后 destroy 已 append
        assert _wait_for(lambda: len(sub.task_history) == 2), "排队任务没收尾"
        assert not model.saw("受害任务1") and not model.saw("受害任务2"), (
            "销毁后排队任务仍被偷偷执行")
        assert all(t.status == SubagentStatus.CANCELLED for t in sub.task_history)

    def test_destroy_queued_only_claims_zero_running(self):
        model, events, mgr, hold, victim = self._blocked()
        mgr.submit_task(victim, "受害任务1")
        assert _wait_for(lambda: len(mgr.task_queue) == 1)
        mgr.destroy(victim)
        model.gate.set()
        destroyed = _events_of(events, "destroyed", victim)
        assert len(destroyed) == 1
        assert destroyed[0]["cancelled_running"] == 0, "没在跑却报掐了 1 个"
        assert destroyed[0]["cancelled_queued"] == 1

    def test_stats_queued_count_is_honest_after_destroy(self):
        model, events, mgr, hold, victim = self._blocked()
        mgr.submit_task(victim, "受害任务1")
        mgr.submit_task(victim, "受害任务2")
        assert _wait_for(lambda: mgr.get_stats()["queued_tasks"] == 2)
        mgr.destroy(victim)
        model.gate.set()
        assert mgr.get_stats()["queued_tasks"] == 0, (
            "状态面还在把已销毁子代理的孤儿排队任务算进去")
        assert mgr.get_stats()["total_subagents"] == 1

    def test_destroy_queued_does_not_cancel_the_slot_holder(self):
        model, events, mgr, hold, victim = self._blocked()
        sub_hold = mgr.get(hold)
        mgr.submit_task(victim, "受害任务1")
        assert _wait_for(lambda: len(mgr.task_queue) == 1)
        mgr.destroy(victim)
        assert not sub_hold._cancel_event.is_set(), "销毁 victim 误伤占位任务"
        model.gate.set()
        assert _wait_for(lambda: len(sub_hold.task_history) == 1)
        assert sub_hold.task_history[0].result.status == "ok"


# ---------------------------------------------------------------------------
# 4. 不留毒：销毁后同 id 重建必须能正常跑
# ---------------------------------------------------------------------------
class TestNoCancelPoisoning:
    def test_recreate_same_id_runs_normally(self):
        model = _GatedModel()
        mgr = _mgr(model)
        sid = mgr.create("spectrum_analyzer", subagent_id="solo", allowed_tools=[])
        mgr.submit_task(sid, "旧任务")
        assert _wait_for(lambda: model.rounds == 1)
        mgr.destroy(sid)
        model.gate.set()

        fresh = _GatedModel()
        fresh.gate.set()                        # 新任务不该被任何闸门/信号挡住
        sid2 = mgr.create("spectrum_analyzer", subagent_id="solo", allowed_tools=[])
        mgr.get(sid2).model_manager = fresh
        res = mgr.execute_task(sid2, "新任务")
        assert res.status == "ok" and res.success is True, (
            "上一轮的取消信号毒害了重建后的新任务")
        assert res.cancelled is False