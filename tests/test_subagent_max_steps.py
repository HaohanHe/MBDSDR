# SPDX-License-Identifier: MIT
"""子代理 max_steps 继承链测试（#89：create(max_steps=N) 曾是死旋钮）。

背景（缺陷实证，probe_87_stopbutton.py 打印的真实数字）：
给 ``mgr.create(..., max_steps=4)``，任务却跑满 **8** 步。根因是
``SubagentTask.max_steps`` 默认 8，而 ``_resolve_max_steps`` 的优先级是
「execute_task 参数 > task.max_steps > self.max_steps」——task 的默认值
永远盖掉子代理自身上限。该字段自己的注释写着「<=0 = 用 Subagent 自身默认」，
默认值却与之矛盾（文档-实现冲突 = bug）。

影响面不止内部：``subagent_create`` 工具把 max_steps 暴露给模型
（"LLM 循环步数上限，默认 8"），模型设 3 步、实际跑 8 步——广告的限制
根本不存在，与 #84（timeout_s）/#85（max_concurrent）同族。

本测试钉住修好后的契约：
1. 子代理自身上限对**同步 execute_task / 异步 submit_task / 直调**三条路径
   都真生效（模型轮次数 == N，绝不是 8）；
2. 显式覆盖优先：task.max_steps > 0 / execute_task(max_steps=) / submit_task
   (max_steps=) 都能盖掉子代理上限；
3. 反证（零回归）：没设上限的子代理仍是 8 步；脏值（0/负数/None）= 继承
   而不是崩溃、也不是「0 步不跑」；
4. 诚实 reporting：截断标记里写的是**真实生效的**上限（max_steps=3 而非 8）。

计数手法：模型每轮都索取同一个工具，数 ``model.calls``（真实轮次），
不做墙钟断言。
"""
from __future__ import annotations

import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.subagents import (  # noqa: E402
    SubagentManager, SubagentTask,
)
from mbdsdr_ai.tool_registry import ToolRegistry, ToolResult  # noqa: E402


class _AlwaysToolModel:
    """每轮都索取工具 ⇒ 一定跑到步数上限（把上限变成可数量）。"""

    def __init__(self):
        self.calls = 0

    def chat(self, messages=None, tools=None, tool_choice="auto", **kw):
        self.calls += 1
        return {"success": True, "content": f"第{self.calls}轮",
                "tool_calls": [{"id": f"c{self.calls}", "type": "function",
                                "function": {"name": "t", "arguments": "{}"}}]}

    @staticmethod
    def parse_tool_calls_from_text(content, valid_names):
        return []


def _registry():
    reg = ToolRegistry()
    reg.register(name="t", description="探针工具",
                 parameters={"type": "object", "properties": {}},
                 handler=lambda a: ToolResult(success=True, content="ok",
                                              tool_name="t"),
                 category="test", write=False)
    return reg


def _mgr(model, **kw):
    return SubagentManager(tool_registry=_registry(), model_manager=model, **kw)


def _sub(mgr, max_steps=None, sid="s"):
    return mgr.create("spectrum_analyzer", subagent_id=sid,
                      allowed_tools=["t"], max_steps=max_steps)


def _wait_for(pred, timeout=5.0, interval=0.005):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(interval)
    return bool(pred())


# ---------------------------------------------------------------------------
# 1. 子代理自身上限三条路径都真生效
# ---------------------------------------------------------------------------
class TestInheritance:
    def test_sync_execute_honours_subagent_limit(self):
        model = _AlwaysToolModel()
        mgr = _mgr(model)
        sid = _sub(mgr, max_steps=3)
        res = mgr.execute_task(sid, "分析频谱")
        assert model.calls == 3, f"create(max_steps=3) 必须真限 3 步，实际 {model.calls}"
        assert res.status == "truncated" and res.truncated is True
        assert res.steps_used == 3

    def test_async_submit_honours_subagent_limit(self):
        model = _AlwaysToolModel()
        mgr = _mgr(model)
        sid = _sub(mgr, max_steps=3)
        mgr.submit_task(sid, "分析频谱")
        assert _wait_for(lambda: len(mgr.get(sid).task_history) >= 1)
        assert model.calls == 3, f"异步路径同样必须限 3 步，实际 {model.calls}"
        assert mgr.get(sid).task_history[0].result.steps_used == 3

    def test_direct_subagent_call_honours_limit(self):
        model = _AlwaysToolModel()
        mgr = _mgr(model)
        sid = _sub(mgr, max_steps=2)
        sub = mgr.get(sid)
        res = sub.execute_task(SubagentTask(task_id="A", goal="直调"))
        assert model.calls == 2
        assert res.steps_used == 2 and res.truncated is True

    def test_task_default_means_inherit_not_eight(self):
        """字段语义钉扎：默认 0 = 继承（修复前默认 8，永远盖掉子代理上限）。"""
        assert SubagentTask(task_id="A", goal="g").max_steps == 0

    def test_truncation_marker_reports_the_real_limit(self):
        """诚实 reporting：标记里写的必须是真生效的上限，不是兜底的 8。"""
        model = _AlwaysToolModel()
        mgr = _mgr(model)
        sid = _sub(mgr, max_steps=3)
        res = mgr.execute_task(sid, "g")
        assert "max_steps=3" in res.output
        assert "max_steps=8" not in res.output
        assert "max_steps=3" in res.error


# ---------------------------------------------------------------------------
# 2. 显式覆盖优先
# ---------------------------------------------------------------------------
class TestExplicitOverride:
    def test_submit_task_max_steps_overrides_subagent(self):
        model = _AlwaysToolModel()
        mgr = _mgr(model)
        sid = _sub(mgr, max_steps=3)
        mgr.submit_task(sid, "g", max_steps=5)
        assert _wait_for(lambda: len(mgr.get(sid).task_history) >= 1)
        assert model.calls == 5, "显式任务上限必须盖掉子代理上限"

    def test_execute_task_max_steps_overrides_subagent(self):
        model = _AlwaysToolModel()
        mgr = _mgr(model)
        sid = _sub(mgr, max_steps=3)
        res = mgr.execute_task(sid, "g", max_steps=2)
        assert model.calls == 2 and res.steps_used == 2

    def test_task_field_overrides_subagent(self):
        model = _AlwaysToolModel()
        mgr = _mgr(model)
        sid = _sub(mgr, max_steps=3)
        res = mgr.get(sid).execute_task(
            SubagentTask(task_id="A", goal="g", max_steps=4))
        assert model.calls == 4

    def test_execute_param_beats_task_field(self):
        model = _AlwaysToolModel()
        mgr = _mgr(model)
        sid = _sub(mgr, max_steps=3)
        res = mgr.get(sid).execute_task(
            SubagentTask(task_id="A", goal="g", max_steps=4), max_steps=2)
        assert model.calls == 2, "优先级：execute 参数 > task 字段 > 子代理"


# ---------------------------------------------------------------------------
# 3. 反证：零回归 + 脏值
# ---------------------------------------------------------------------------
class TestBackwardCompat:
    def test_default_subagent_still_eight(self):
        model = _AlwaysToolModel()
        mgr = _mgr(model)
        sid = _sub(mgr)                      # 不设上限
        res = mgr.execute_task(sid, "g")
        assert model.calls == 8, "没设上限时仍须是 8（既有行为零回归）"
        assert res.steps_used == 8

    def test_dirty_limits_inherit_not_crash(self):
        for dirty in (0, -3, None):
            model = _AlwaysToolModel()
            mgr = _mgr(model)
            sid = _sub(mgr, max_steps=3)
            res = mgr.execute_task(sid, "g", max_steps=dirty)
            assert model.calls == 3, f"max_steps={dirty!r} 应继承子代理上限"
            assert res.status == "truncated"

    def test_dirty_submit_limit_inherits(self):
        for dirty in (0, -1, None):
            model = _AlwaysToolModel()
            mgr = _mgr(model)
            sid = _sub(mgr, max_steps=2)
            mgr.submit_task(sid, "g", max_steps=dirty)
            assert _wait_for(lambda: len(mgr.get(sid).task_history) >= 1)
            assert model.calls == 2, f"submit max_steps={dirty!r} 应继承"

    def test_fast_task_unaffected_by_limit(self):
        """上限是保护性天花板：正常完成的任务不该被它影响。"""
        class _Done:
            def __init__(self):
                self.calls = 0

            def chat(self, messages=None, tools=None, tool_choice="auto", **kw):
                self.calls += 1
                return {"success": True, "content": "结论：完成", "tool_calls": []}

            @staticmethod
            def parse_tool_calls_from_text(content, valid_names):
                return []

        model = _Done()
        mgr = _mgr(model)
        sid = _sub(mgr, max_steps=3)
        res = mgr.execute_task(sid, "g")
        assert res.success is True and res.status == "ok"
        assert res.truncated is False and model.calls == 1

    def test_subagent_stats_reports_its_own_limit(self):
        """状态面报的必须是子代理真实上限（不是任务的兜底值）。"""
        mgr = _mgr(_AlwaysToolModel())
        sid = _sub(mgr, max_steps=3)
        listed = {s["subagent_id"]: s for s in mgr.list_subagents()}
        assert listed[sid]["max_steps"] == 3