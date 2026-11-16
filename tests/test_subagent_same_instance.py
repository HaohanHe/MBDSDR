# SPDX-License-Identifier: MIT
"""同一子代理并发任务的上下文隔离测试（#86：子代理级串行闸）。

背景（缺陷实证）：``Subagent`` 类文档承诺「上下文（独立的对话历史，与主
agent / 其它子代理零共享）」「每个子代理有自己的…任务队列」，execute_task
文档更写明「与本代理的上个任务零共享」。但 ``self.context`` / ``self.status``
/ ``self.current_task`` 都是**实例级共享可变状态**：#85 的 max_concurrent 是
全局限流而非按子代理，同一子代理可被并发提交多个任务 → 两个 LLM 循环交替
读写同一个 context 列表（B 的 ``self.context = _build_task_context(...)`` 重置
后，A 继续往 B 的列表里 append），A 的下一轮模型调用会看到 B 的任务目标与
工具结果（跨任务污染）；status/current_task 互相覆盖，running_subagents 失真。

本测试钉住修好后的契约：
1. 同一子代理同一时刻只跑一个任务：并发直调/同步调用**立即**诚实报
   subagent_busy（不执行、不产结论、不记历史、不算失败任务）；
2. 任何一次模型调用看到的上下文只含**本任务**的目标标记——尤其是工具回灌
   后的那一轮：修复前 B 的重置会把 A 的 system/user 整段丢掉，A 于是对着
   B 的目标下结论（比「混在一起」更隐蔽），故判据是「A 每轮都只看到 A」；
3. 异步提交到同一子代理 = 排队串行，两个任务都要跑完（排队不是丢弃）；
4. 等子代理空闲期间**不占全局并发额度**（否则同一子代理的排队会饿死别人）；
5. 嵌套同一子代理快速 busy 而非死锁（非可重入锁，同线程也挡住）；
6. 锁绝不泄漏：异常/超时后同一子代理仍能接任务；
7. 反证：不同子代理之间仍真并发（串行闸不得过度串行化）。

同步手法：闸门 Event + 目标标记，断言的是**计数与不变量**（峰值并发、
每次 chat 看到的标记集合、槽位/锁归还），不是墙钟时长；等待一律带宽松上界。
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
# 同步脚手架
# ---------------------------------------------------------------------------
_MARKERS = ("任务A", "任务B", "X1", "X2", "Y1", "外层", "内层")


class _MarkerModel:
    """记录每次 chat 在上下文里看到的目标标记；可指定哪些目标卡在闸门上。

    ``calls`` 里每个元素是本次 chat 看到的标记元组——跨任务污染会表现为
    某个元素同时含两个标记（长度 > 1），这是本组测试的核心不变量。
    """

    def __init__(self, block_goals=(), gate_all=False):
        self.block_goals = set(block_goals)
        self.gate_all = gate_all
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.calls = []          # 每次 chat 看到的标记集合
        self.started = []        # 已起跑的目标标记（按顺序）
        self.live = 0
        self.peak = 0

    def _markers(self, messages):
        text = " ".join(str(m.get("content", "") or "") for m in (messages or []))
        return tuple(m for m in _MARKERS if m in text)

    def chat(self, messages=None, tools=None, tool_choice="auto", **kwargs):
        marks = self._markers(messages)
        with self.lock:
            self.calls.append(marks)
            self.started.extend(marks)
            self.live += 1
            self.peak = max(self.peak, self.live)
        try:
            if self.gate_all or (set(marks) & self.block_goals):
                if not self.release.wait(10.0):
                    return {"success": False, "error": "gate_timeout"}
            return {"success": True, "content": f"结论：{'/'.join(marks) or '无标记'} 完成。",
                    "tool_calls": []}
        finally:
            with self.lock:
                self.live -= 1

    @staticmethod
    def parse_tool_calls_from_text(content, valid_names):
        return []

    def saw(self, marker):
        with self.lock:
            return marker in self.started


class _ToolRoundModel(_MarkerModel):
    """任务A 第一轮索取工具（工具卡在闸门上），其余轮次给最终结论。

    用于钉住污染的**真实形态**（探针实测）：B 起跑时
    ``self.context = _build_task_context(...)`` 会把 A 的 system/user 消息
    整段丢掉，A 随后又往同一列表 append 自己的 assistant/tool 消息——于是
    A 的工具后那一轮读到的是 **B 的目标**，A 自己的目标凭空消失。故判据
    不是「同时看到两个标记」，而是「A 的每一轮都只看到 A」。
    """

    def __init__(self):
        super().__init__()
        self.tool_entered = threading.Event()
        self.release_tool = threading.Event()
        self.a_rounds = 0
        self.after_release = []   # 与 calls 对齐：该轮是否发生在工具放行后

    def chat(self, messages=None, tools=None, tool_choice="auto", **kwargs):
        marks = self._markers(messages)
        with self.lock:
            self.calls.append(marks)
            self.after_release.append(self.release_tool.is_set())
            self.started.extend(marks)
            self.live += 1
            self.peak = max(self.peak, self.live)
            first_a = ("任务A" in marks and self.a_rounds == 0)
            if "任务A" in marks:
                self.a_rounds += 1
        try:
            if first_a:
                return {"success": True, "content": "先测一下",
                        "tool_calls": [{"id": "c1", "type": "function",
                                        "function": {"name": "probe_tool",
                                                     "arguments": "{}"}}]}
            return {"success": True, "content": f"结论：{'/'.join(marks)} 完成。",
                    "tool_calls": []}
        finally:
            with self.lock:
                self.live -= 1


class _EmptyRegistry:
    def get_tool_names(self):
        return []

    def get_tool_definitions(self):
        return []

    def call_from_model(self, tool_call):
        return ToolResult(success=False, content="无工具", tool_name="")


def _wait_for(pred, timeout=5.0, interval=0.01):
    """带宽松上界的条件等待（只用于线程同步，不做时长断言）。"""
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(interval)
    return bool(pred())


def _mgr(model, max_concurrent=4, registry=None):
    return SubagentManager(tool_registry=registry or _EmptyRegistry(),
                           model_manager=model, max_concurrent=max_concurrent)


def _sub(mgr, sid="solo", allowed_tools=None):
    return mgr.create("spectrum_analyzer", subagent_id=sid,
                      allowed_tools=allowed_tools if allowed_tools is not None else [])


def _assert_no_cross_task_context(model):
    """核心不变量：任何一次模型调用都只看到一个任务的目标标记。"""
    for marks in model.calls:
        assert len(marks) <= 1, f"上下文被跨任务污染：一次 chat 看到 {marks}"


# ---------------------------------------------------------------------------
# 1. 并发直调：诚实 busy，绝不污染
# ---------------------------------------------------------------------------
class TestDirectCallSerialization:
    def test_second_concurrent_call_reports_busy(self):
        model = _MarkerModel(gate_all=True)
        mgr = _mgr(model)
        sid = _sub(mgr)
        sub = mgr.get(sid)

        done = []
        t = threading.Thread(
            target=lambda: done.append(sub.execute_task(
                SubagentTask(task_id="A", goal="任务A"))), daemon=True)
        t.start()
        assert _wait_for(lambda: model.live == 1), "第一个任务应已在 chat 内"

        rejected = sub.execute_task(SubagentTask(task_id="B", goal="任务B"))
        assert rejected.success is False
        assert rejected.status == "error"
        assert rejected.error.startswith("subagent_busy")
        assert rejected.data["busy"] is True
        assert rejected.data["reason"] == "subagent_serialized"
        assert rejected.data["running_task_id"] == "A"
        assert rejected.output == "", "没执行就绝不产出结论"
        assert rejected.task_id == "B"
        assert model.live == 1, "busy 时绝不起第二个 LLM 循环"
        assert sub.task_history == [], "未执行不得记进任务历史"
        assert sub.total_tasks_failed == 0, "没跑就不算失败任务"
        assert sub.total_tasks_completed == 0

        model.release.set()
        t.join(timeout=5.0)
        assert not t.is_alive()
        assert len(done) == 1 and done[0].success is True
        assert done[0].status == "ok"
        _assert_no_cross_task_context(model)

    def test_lock_released_after_success(self):
        model = _MarkerModel()
        mgr = _mgr(model)
        sub = mgr.get(_sub(mgr))
        for i in range(3):
            res = sub.execute_task(SubagentTask(task_id=f"A{i}", goal="任务A"))
            assert res.success is True, "成功路径必须归还串行闸，否则第二次就 busy"
        assert len(sub.task_history) == 3

    def test_lock_released_after_exception(self):
        class _BoomThenOk:
            def __init__(self):
                self.n = 0

            def chat(self, messages=None, tools=None, tool_choice="auto", **kw):
                self.n += 1
                if self.n == 1:
                    raise RuntimeError("首次调用炸了")
                return {"success": True, "content": "恢复了", "tool_calls": []}

            @staticmethod
            def parse_tool_calls_from_text(content, valid_names):
                return []

        mgr = _mgr(_BoomThenOk())
        sub = mgr.get(_sub(mgr))
        bad = sub.execute_task(SubagentTask(task_id="A", goal="任务A"))
        assert bad.status == "error" and "炸了" in bad.error
        good = sub.execute_task(SubagentTask(task_id="B", goal="任务B"))
        assert good.success is True and good.output == "恢复了", (
            "异常后串行闸没归还，同一子代理被永久堵死")

    def test_lock_released_after_timeout(self):
        """#84 墙钟超时后串行闸必须归还（超时任务不得永久占坑）。"""
        reg = ToolRegistry()

        def _slow(args):
            time.sleep(0.05)
            return ToolResult(success=True, content="测完了", tool_name="t")

        reg.register(name="t", description="慢工具",
                     parameters={"type": "object", "properties": {}},
                     handler=_slow, category="test", write=False)

        class _Loop:
            def chat(self, messages=None, tools=None, tool_choice="auto", **kw):
                return {"success": True, "content": "再测",
                        "tool_calls": [{"id": "c1", "type": "function",
                                        "function": {"name": "t",
                                                     "arguments": "{}"}}]}

            @staticmethod
            def parse_tool_calls_from_text(content, valid_names):
                return []

        mgr = SubagentManager(tool_registry=reg, model_manager=_Loop(),
                              max_concurrent=4)
        sid = _sub(mgr, allowed_tools=["t"])
        first = mgr.execute_task(sid, "任务A", timeout_s=0.2)
        assert first.status == "timeout" and first.timed_out is True
        after = mgr.execute_task(sid, "任务B", timeout_s=0.2)
        assert not after.error.startswith("subagent_busy"), (
            "超时后串行闸没归还，后续任务被永久堵死")


# ---------------------------------------------------------------------------
# 2. 上下文绝不跨任务混合
# ---------------------------------------------------------------------------
class TestContextIsolation:
    def test_async_same_subagent_never_mixes_context(self):
        model = _MarkerModel(block_goals=("任务A",))
        mgr = _mgr(model, max_concurrent=4)   # 全局额度充足：串行只能来自子代理闸
        sid = _sub(mgr)
        mgr.submit_task(sid, "任务A")
        assert _wait_for(lambda: model.saw("任务A"))
        mgr.submit_task(sid, "任务B")

        # 任务B 必须真被挡住（不是「还没轮到」的假象）
        assert not _wait_for(lambda: model.saw("任务B"), timeout=0.3)
        assert model.peak == 1
        assert mgr.get_stats()["running_subagents"] == 1

        model.release.set()
        sub = mgr.get(sid)
        assert _wait_for(lambda: len(sub.task_history) == 2), "排队不是丢弃"
        assert model.peak == 1, "同一子代理峰值并发必须是 1"
        _assert_no_cross_task_context(model)
        assert model.saw("任务A") and model.saw("任务B")
        for task in sub.task_history:
            assert task.result.success is True and task.result.status == "ok"
            assert task.status == SubagentStatus.COMPLETED
        assert mgr.task_queue == []
        assert sub.status == SubagentStatus.IDLE
        assert sub.current_task is None

    def test_current_task_tracks_the_running_one(self):
        model = _MarkerModel(gate_all=True)
        mgr = _mgr(model)
        sid = _sub(mgr)
        sub = mgr.get(sid)
        mgr.submit_task(sid, "任务A")
        assert _wait_for(lambda: sub.status == SubagentStatus.RUNNING)
        assert sub.current_task is not None
        assert sub.current_task.goal == "任务A"
        assert mgr.get_stats()["running_subagents"] == 1

        mgr.submit_task(sid, "任务B")
        assert _wait_for(lambda: len(mgr.task_queue) == 1)
        # 排队期间在跑的仍是 A，绝不被 B 覆盖
        assert sub.current_task.goal == "任务A"
        assert mgr.get_stats()["running_subagents"] == 1

        model.release.set()
        assert _wait_for(lambda: len(sub.task_history) == 2)
        assert sub.status == SubagentStatus.IDLE
        assert sub.current_task is None
        assert mgr.get_stats()["running_subagents"] == 0


# ---------------------------------------------------------------------------
# 3. 排队不浪费全局额度 + 不同子代理仍真并发
# ---------------------------------------------------------------------------
class TestGlobalSlotsNotWasted:
    def test_waiting_for_subagent_does_not_hold_global_slot(self):
        """X 的第二个任务在等 X 空闲时不得占槽位，否则 Y 被饿死。"""
        model = _MarkerModel(block_goals=("X1", "X2"))
        mgr = _mgr(model, max_concurrent=2)
        sid_x = _sub(mgr, "x")
        sid_y = _sub(mgr, "y")
        mgr.submit_task(sid_x, "X1")
        assert _wait_for(lambda: model.saw("X1"))
        mgr.submit_task(sid_x, "X2")
        assert _wait_for(lambda: len(mgr.task_queue) == 1)
        mgr.submit_task(sid_y, "Y1")

        assert _wait_for(lambda: model.saw("Y1"), timeout=3.0), (
            "等子代理空闲的任务占住了全局槽位，别的子代理被饿死")
        assert model.peak <= 2, "全局上限仍必须生效"

        model.release.set()
        assert _wait_for(lambda: len(mgr.get(sid_x).task_history) == 2
                         and len(mgr.get(sid_y).task_history) == 1)
        assert mgr.task_queue == []
        _assert_no_cross_task_context(model)

    def test_different_subagents_still_run_concurrently(self):
        """反证：串行闸只在子代理内部，不得把全局并发也串成 1。"""
        model = _MarkerModel(gate_all=True)
        mgr = _mgr(model, max_concurrent=2)
        sid_a = _sub(mgr, "a")
        sid_b = _sub(mgr, "b")
        mgr.submit_task(sid_a, "任务A")
        mgr.submit_task(sid_b, "任务B")
        assert _wait_for(lambda: model.live == 2), "不同子代理必须能真并发"
        assert model.peak == 2
        model.release.set()
        assert _wait_for(lambda: len(mgr.get(sid_a).task_history) == 1
                         and len(mgr.get(sid_b).task_history) == 1)
        _assert_no_cross_task_context(model)


# ---------------------------------------------------------------------------
# 4. 同步路径 + 嵌套同一子代理
# ---------------------------------------------------------------------------
class TestSyncAndNested:
    def test_sync_manager_reports_subagent_busy(self):
        model = _MarkerModel(gate_all=True)
        mgr = _mgr(model, max_concurrent=4)   # 全局槽位充足
        sid = _sub(mgr)
        mgr.submit_task(sid, "任务A")
        assert _wait_for(lambda: model.live == 1)

        res = mgr.execute_task(sid, "任务B")
        assert res.success is False and res.status == "error"
        assert res.error.startswith("subagent_busy")
        assert res.data["reason"] == "subagent_serialized"
        assert res.output == ""
        assert model.live == 1, "busy 时绝不起第二个 LLM 循环"
        assert len(mgr.get(sid).task_history) == 0

        model.release.set()
        assert _wait_for(lambda: len(mgr.get(sid).task_history) == 1)
        ok = mgr.execute_task(sid, "任务B")
        assert ok.success is True and ok.status == "ok"

    def test_nested_same_subagent_busy_not_deadlock(self):
        """嵌套调用**同一个**子代理：非可重入锁必须挡住（RLock 会放过去）。"""
        model = _MarkerModel()
        mgr = _mgr(model, max_concurrent=4)
        seen = {}
        reg = ToolRegistry()
        sid = _sub(mgr, "solo", allowed_tools=["nested_probe"])

        def _nested(args):
            r = mgr.execute_task(sid, "内层")
            seen["error"] = r.error
            seen["success"] = r.success
            seen["reason"] = r.data.get("reason")
            return ToolResult(success=True, content=f"内层: {r.error}",
                              tool_name="nested_probe")

        reg.register(name="nested_probe", description="嵌套探针",
                     parameters={"type": "object", "properties": {}},
                     handler=_nested, category="test", write=False)
        sub = mgr.get(sid)
        sub.tool_registry = reg

        class _OneToolThenDone:
            def __init__(self):
                self.n = 0

            def chat(self, messages=None, tools=None, tool_choice="auto", **kw):
                self.n += 1
                if self.n == 1:
                    return {"success": True, "content": "先探一下",
                            "tool_calls": [{"id": "c1", "type": "function",
                                            "function": {"name": "nested_probe",
                                                         "arguments": "{}"}}]}
                return {"success": True, "content": "外层结论", "tool_calls": []}

            @staticmethod
            def parse_tool_calls_from_text(content, valid_names):
                return []

        sub.model_manager = _OneToolThenDone()

        t0 = time.time()
        res = mgr.execute_task(sid, "外层", timeout_s=10.0)
        elapsed = time.time() - t0
        assert elapsed < 5.0, f"嵌套同一子代理疑似死锁（{elapsed:.2f}s）"
        assert res.success is True and res.output == "外层结论"
        assert seen["success"] is False
        assert seen["error"].startswith("subagent_busy")
        assert seen["reason"] == "subagent_serialized", (
            "同线程嵌套必须也被挡住（可重入锁会放过 → 上下文自我覆盖）")
        # 内层被拒后外层上下文仍是自己的，且串行闸已归还
        again = mgr.execute_task(sid, "任务B")
        assert again.success is True


# ---------------------------------------------------------------------------
# 5. 工具回灌轮次的上下文归属（污染的真实形态）
# ---------------------------------------------------------------------------
class TestToolRoundIsolation:
    def test_task_keeps_own_goal_across_tool_round(self):
        """A 索取工具期间提交 B：A 的工具后一轮必须仍读到 A 自己的目标。

        修复前 B 会重置 self.context，把 A 的 system/user 整段丢掉，A 的
        第二轮于是对着 B 的目标下结论（比「混在一起」更隐蔽、更危险）。
        """
        model = _ToolRoundModel()
        reg = ToolRegistry()

        def _tool(args):
            model.tool_entered.set()
            model.release_tool.wait(10.0)
            return ToolResult(success=True, content="测完了：信号在 100.1 MHz",
                              tool_name="probe_tool")

        reg.register(name="probe_tool", description="探针工具",
                     parameters={"type": "object", "properties": {}},
                     handler=_tool, category="test", write=False)

        mgr = SubagentManager(tool_registry=reg, model_manager=model,
                              max_concurrent=4)   # 全局额度充足
        sid = _sub(mgr, "solo", allowed_tools=["probe_tool"])
        sub = mgr.get(sid)

        mgr.submit_task(sid, "任务A")
        assert _wait_for(lambda: model.tool_entered.is_set()), "A 应已进入工具"
        mgr.submit_task(sid, "任务B")
        # 先记录 B 是否抢跑，再放行工具——这样即使 B 抢跑，也能走到下面
        # 的污染判据，让失败信息直接指出「A 读到了别人的目标」而不是只说
        # 「B 起早了」（两个不变量都钉，成因报得更具体）。
        b_raced = _wait_for(lambda: model.saw("任务B"), timeout=0.3)

        model.release_tool.set()
        assert _wait_for(lambda: len(sub.task_history) == 2), "排队不是丢弃"

        pairs = list(zip(model.calls, model.after_release))
        # A 的两轮（工具前 + 工具后）都只看到 A
        assert ("任务A",) in model.calls
        after_release = [marks for marks, after in pairs if after]
        assert after_release, "工具放行后应还有模型轮次"
        assert after_release[0] == ("任务A",), (
            f"A 的工具后一轮读到了别人的目标: {after_release[0]}（上下文被污染）")
        assert all(len(m) <= 1 for m in model.calls), "任何一轮都不得混任务"
        assert ("任务B",) in model.calls, "B 最终必须跑完并只看到自己"
        assert model.peak == 1
        assert all(t.result.success is True for t in sub.task_history)
        assert model.calls.count(("任务A",)) == 2, "A 恰好两轮，都归属自己"
        assert not b_raced, "B 必须等 A 跑完，绝不在 A 在途时重置上下文"