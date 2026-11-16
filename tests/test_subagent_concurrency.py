# SPDX-License-Identifier: MIT
"""子代理 max_concurrent 真限流测试（#85：清死参数 + 排队可观测）。

背景（缺陷实证）：``SubagentManager.max_concurrent=4`` 被 agent 状态面当事实
展示（"并发上限 4"），但 ``submit_task`` 无条件 ``threading.Thread(...).start()``
——广告的上限根本不存在；``task_queue`` 声明后从未使用（死字段）；
``SubagentStatus.WAITING`` 是从未赋值过的死枚举。

本测试钉住修好后的契约：
1. 异步 submit 真限流：满槽排队（WAITING + task_queue 台账），峰值并发 never
   超过上限，放行后排队任务一定被执行（排队不是丢弃）；
2. 同步 execute_task 满槽**立即**诚实报 subagent_busy（不阻塞、不起 LLM 循环）；
3. 槽位绝不泄漏：成功/异常/超时后都归还（反证：限流越跑越紧直到堵死）；
4. 嵌套子代理不死锁（内层快速 busy 失败，外层照常完成）；
5. 反证：max_concurrent<=0 / 脏值 = 不限并发，行为与修复前一致；
6. 状态面（get_stats / agent 观测层 / /subagents）报的是真排队数。

同步手法：用闸门 Event 精确控制「同时在跑」的任务数，断言的是**计数与不变量**
（峰值并发、排队数、槽位归还），不是墙钟时长；等待一律带宽松上界。
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
class _GatedModel:
    """chat 卡在闸门上：让「同时在跑」的任务数可被测试精确控制。"""

    def __init__(self):
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.entered = 0        # 累计进入 chat 次数
        self.live = 0           # 当前在 chat 内的任务数
        self.peak = 0           # 观测到的并发峰值

    def chat(self, messages=None, tools=None, tool_choice="auto", **kwargs):
        with self.lock:
            self.entered += 1
            self.live += 1
            self.peak = max(self.peak, self.live)
        try:
            if not self.release.wait(10.0):
                return {"success": False, "error": "gate_timeout"}
            return {"success": True, "content": "结论：完成。", "tool_calls": []}
        finally:
            with self.lock:
                self.live -= 1

    @staticmethod
    def parse_tool_calls_from_text(content, valid_names):
        return []


class _EmptyRegistry:
    """无工具注册表（本组测试只关心并发，不关心工具）。"""

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


def _mgr(model, max_concurrent=2, registry=None):
    return SubagentManager(tool_registry=registry or _EmptyRegistry(),
                           model_manager=model, max_concurrent=max_concurrent)


def _spawn(mgr, n, prefix="sub"):
    """建 n 个**不同**子代理（同一子代理并发跑会共享 context，另案处理）。"""
    return [mgr.create("spectrum_analyzer", subagent_id=f"{prefix}_{i}",
                       allowed_tools=[]) for i in range(n)]


def _all_done(mgr, sids, n=1):
    return all(len(mgr.get(s).task_history) >= n for s in sids)


# ---------------------------------------------------------------------------
# 1. 异步限流真生效
# ---------------------------------------------------------------------------
class TestAsyncLimit:
    def test_peak_concurrency_never_exceeds_limit(self):
        model = _GatedModel()
        mgr = _mgr(model, max_concurrent=2)
        sids = _spawn(mgr, 4)
        for sid in sids:
            mgr.submit_task(sid, "分析频谱")

        assert _wait_for(lambda: model.entered == 2), "应有 2 个任务起跑"
        # 另外 2 个必须真被挡住（不是「还没轮到」的假象）
        assert not _wait_for(lambda: model.entered > 2, timeout=0.3)
        stats = mgr.get_stats()
        assert stats["queued_tasks"] == 2, "排队台账必须只留真在等的任务"
        assert stats["running_subagents"] == 2
        assert stats["concurrency_enforced"] is True
        assert model.peak == 2

        model.release.set()
        assert _wait_for(lambda: _all_done(mgr, sids)), "排队任务必须都被执行"
        assert model.entered == 4, "排队不是丢弃：4 个任务一个都不能少"
        assert model.peak == 2, "放行后峰值仍不得突破上限"
        assert mgr.get_stats()["queued_tasks"] == 0
        for sid in sids:
            res = mgr.get(sid).task_history[0].result
            assert res.success is True and res.status == "ok"

    def test_queued_task_status_is_waiting_then_completed(self):
        model = _GatedModel()
        mgr = _mgr(model, max_concurrent=1)
        sids = _spawn(mgr, 2)
        mgr.submit_task(sids[0], "第一个")
        assert _wait_for(lambda: model.entered == 1)
        mgr.submit_task(sids[1], "第二个")
        assert _wait_for(lambda: len(mgr.task_queue) == 1)
        queued = mgr.task_queue[0]
        assert queued.status == SubagentStatus.WAITING, "WAITING 必须是真状态"
        assert queued.goal == "第二个"

        model.release.set()
        assert _wait_for(lambda: _all_done(mgr, sids))
        assert queued.status == SubagentStatus.COMPLETED
        assert mgr.task_queue == [], "跑完不留僵尸台账"

    def test_limit_one_serializes_everything(self):
        model = _GatedModel()
        mgr = _mgr(model, max_concurrent=1)
        sids = _spawn(mgr, 3)
        for sid in sids:
            mgr.submit_task(sid, "串行")
        assert _wait_for(lambda: model.entered == 1)
        assert not _wait_for(lambda: model.entered > 1, timeout=0.3)
        model.release.set()
        assert _wait_for(lambda: _all_done(mgr, sids))
        assert model.peak == 1 and model.entered == 3

    def test_unbounded_when_limit_zero(self):
        """反证：<=0 = 不限并发，4 个任务同时起跑（与修复前行为一致）。"""
        model = _GatedModel()
        mgr = _mgr(model, max_concurrent=0)
        sids = _spawn(mgr, 4)
        for sid in sids:
            mgr.submit_task(sid, "不限并发")
        assert _wait_for(lambda: model.entered == 4)
        assert model.peak == 4
        assert mgr.get_stats()["concurrency_enforced"] is False
        assert mgr.get_stats()["queued_tasks"] == 0, "不限并发时不该有排队"
        model.release.set()
        assert _wait_for(lambda: _all_done(mgr, sids))

    def test_dirty_limit_means_unbounded_not_crash(self):
        for dirty in (None, "abc", -3):
            model = _GatedModel()
            model.release.set()
            mgr = _mgr(model, max_concurrent=dirty)
            sid = _spawn(mgr, 1)[0]
            res = mgr.execute_task(sid, "脏值上限")
            assert res.success is True, f"max_concurrent={dirty!r} 不该影响执行"
            assert mgr.get_stats()["concurrency_enforced"] is False

    def test_submit_unknown_subagent_still_raises(self):
        mgr = _mgr(_GatedModel())
        try:
            mgr.submit_task("不存在", "g")
        except ValueError as e:
            assert "不存在" in str(e)
        else:
            raise AssertionError("未知子代理必须仍抛 ValueError（旧契约）")


# ---------------------------------------------------------------------------
# 2. 同步路径：满槽诚实 busy + 槽位绝不泄漏
# ---------------------------------------------------------------------------
class TestSyncPath:
    def test_sync_reports_busy_immediately_when_full(self):
        model = _GatedModel()
        mgr = _mgr(model, max_concurrent=1)
        sids = _spawn(mgr, 2)
        mgr.submit_task(sids[0], "占住槽位")
        assert _wait_for(lambda: model.entered == 1)

        res = mgr.execute_task(sids[1], "满槽时同步调用")
        assert res.success is False
        assert res.status == "error"
        assert res.error.startswith("subagent_busy")
        assert res.data["busy"] is True
        assert res.data["max_concurrent"] == 1
        assert res.output == "", "没执行就绝不产出结论"
        assert model.entered == 1, "busy 时绝不起第二个 LLM 循环"
        assert mgr.get(sids[1]).task_history == [], "未执行不得记进任务历史"
        assert mgr.get(sids[1]).total_tasks_failed == 0, "没跑就不算失败任务"

        model.release.set()
        assert _wait_for(lambda: _all_done(mgr, [sids[0]]))
        # 槽位归还后同步调用恢复正常
        ok = mgr.execute_task(sids[1], "槽位空了")
        assert ok.success is True and ok.status == "ok"

    def test_sync_slot_released_after_success(self):
        model = _GatedModel()
        model.release.set()
        mgr = _mgr(model, max_concurrent=1)
        sid = _spawn(mgr, 1)[0]
        for i in range(3):
            res = mgr.execute_task(sid, f"第 {i} 次")
            assert res.success is True, "成功路径必须归还槽位，否则第二次就 busy"

    def test_sync_slot_released_after_exception(self):
        """反证：任务抛异常也得归还槽位（否则限流越跑越紧直到堵死）。"""
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

        mgr = _mgr(_BoomThenOk(), max_concurrent=1)
        sid = _spawn(mgr, 1)[0]
        bad = mgr.execute_task(sid, "炸")
        assert bad.status == "error" and "炸了" in bad.error
        good = mgr.execute_task(sid, "再试")
        assert good.success is True and good.output == "恢复了"

    def test_sync_slot_released_after_timeout(self):
        """#84 墙钟超时后槽位必须归还（超时任务不得永久占坑）。"""
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
                              max_concurrent=1)
        sid = mgr.create("spectrum_analyzer", subagent_id="sub_to",
                         allowed_tools=["t"])
        res = mgr.execute_task(sid, "超时", timeout_s=0.2)
        assert res.status == "timeout" and res.timed_out is True
        after = mgr.execute_task(sid, "超时后再来", timeout_s=0.2)
        assert after.status != "error" or not after.error.startswith("subagent_busy"), (
            "超时后槽位没归还，后续任务被永久堵死")

    def test_nested_sync_does_not_deadlock(self):
        """嵌套子代理（显式白名单放行）内层快速 busy，外层照常完成。"""
        model = _GatedModel()
        model.release.set()
        mgr = _mgr(model, max_concurrent=1)
        inner_sid = mgr.create("spectrum_analyzer", subagent_id="inner",
                               allowed_tools=[])
        seen = {}

        reg = ToolRegistry()

        def _nested(args):
            r = mgr.execute_task(inner_sid, "内层")
            seen["error"] = r.error
            seen["success"] = r.success
            return ToolResult(success=True, content=f"内层: {r.error}",
                              tool_name="nested_probe")

        reg.register(name="nested_probe", description="嵌套探针",
                     parameters={"type": "object", "properties": {}},
                     handler=_nested, category="test", write=False)
        mgr.tool_registry = reg

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

        outer_sid = mgr.create("spectrum_analyzer", subagent_id="outer",
                               allowed_tools=["nested_probe"])
        mgr.subagents[outer_sid].model_manager = _OneToolThenDone()
        mgr.subagents[outer_sid].tool_registry = reg

        t0 = time.time()
        res = mgr.execute_task(outer_sid, "外层", timeout_s=10.0)
        elapsed = time.time() - t0
        assert elapsed < 5.0, f"嵌套调用疑似死锁（{elapsed:.2f}s）"
        assert res.success is True and res.output == "外层结论"
        assert seen["success"] is False
        assert seen["error"].startswith("subagent_busy"), "内层应快速 busy 而非死等"


# ---------------------------------------------------------------------------
# 3. 观测面：报的必须是真数
# ---------------------------------------------------------------------------
class TestObservability:
    def test_stats_keys_and_truthfulness(self):
        model = _GatedModel()
        model.release.set()
        mgr = _mgr(model, max_concurrent=3)
        stats = mgr.get_stats()
        for key in ("max_concurrent", "queued_tasks", "concurrency_enforced",
                    "running_subagents", "total_subagents"):
            assert key in stats, f"get_stats 缺 {key}"
        assert stats["max_concurrent"] == 3
        assert stats["queued_tasks"] == 0
        assert stats["concurrency_enforced"] is True

    def test_agent_status_reports_queue(self):
        from mbdsdr_ai.agent import AgentConfig, MBDSDRAgent

        agent = MBDSDRAgent(AgentConfig())
        sa = agent._subagents_status()
        assert sa["available"] is True
        assert "queued_tasks" in sa and sa["queued_tasks"] == 0
        text = agent._observability_status_text()
        assert "并发上限" in text and "排队" in text
        cmd = agent.run_command("/subagents")
        assert "并发上限" in cmd and "排队" in cmd

    def test_task_queue_holds_only_waiters(self):
        """台账语义钉扎：起跑即出队，task_queue 不是「已提交未完成」。"""
        model = _GatedModel()
        mgr = _mgr(model, max_concurrent=1)
        sids = _spawn(mgr, 3)
        for sid in sids:
            mgr.submit_task(sid, "g")
        assert _wait_for(lambda: model.entered == 1)
        assert _wait_for(lambda: len(mgr.task_queue) == 2)
        assert all(t.status == SubagentStatus.WAITING for t in mgr.task_queue)
        model.release.set()
        assert _wait_for(lambda: _all_done(mgr, sids))
        assert mgr.task_queue == []
        assert isinstance(mgr.task_queue, list)
        assert all(isinstance(t, SubagentTask) for t in mgr.task_queue)