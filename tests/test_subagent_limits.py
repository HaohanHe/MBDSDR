# SPDX-License-Identifier: MIT
"""Task 37/38：子 agent 并发与递归深度限额（不调真实模型）。

Task 37：`SubagentManager.submit_task` 用真实信号量限流，超限任务排队；
状态面只在实际生效时宣称并发上限。
Task 38：`execute_task` 通过 contextvars 递增深度；达到 `MAX_SUBAGENT_DEPTH`
后 `subagent_*` 工具从视图移除且调用本地拒绝，显式白名单也不能绕过。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.agent import MBDSDRAgent  # noqa: E402
from mbdsdr_ai.subagents import (  # noqa: E402
    MAX_SUBAGENT_DEPTH,
    Subagent,
    SubagentManager,
    SubagentTask,
    _SUBAGENT_DEPTH,
    current_subagent_depth,
)
from mbdsdr_ai.tool_results import ToolResult  # noqa: E402


def _tool_call(name, args=None, call_id="call_1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args or {})}}


class _ScriptedModel:
    """按脚本逐条返回 chat 响应的替身（不触网、不调真模型）。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, messages=None, tools=None, tool_choice="auto", **kwargs):
        self.calls.append({"messages": list(messages or []),
                           "tools": list(tools or [])})
        item = self.script.pop(0) if self.script else {
            "success": True, "content": "完成", "tool_calls": []}
        return item

    @staticmethod
    def parse_tool_calls_from_text(content, valid_names):
        return []


class _FakeRegistry:
    """duck-type registry；subagent_execute 可挂自定义回调。"""

    def __init__(self, tools=None, on_subagent=None):
        self.tools = dict(tools or {})
        self.on_subagent = on_subagent
        self.calls = []

    def get_tool_names(self):
        return list(self.tools)

    def get_tool_definitions(self):
        return [{"type": "function",
                 "function": {"name": n, "description": d,
                              "parameters": {"type": "object",
                                             "properties": {}}}}
                for n, d in self.tools.items()]

    def call_from_model(self, tool_call):
        name = (tool_call.get("function") or {}).get("name", "")
        self.calls.append(name)
        if name.startswith("subagent_") and self.on_subagent is not None:
            return self.on_subagent(tool_call)
        return ToolResult(success=True, content=f"[stub] {name}")


# ---------------------------------------------------------------------------
# Task 37：并发限流
# ---------------------------------------------------------------------------
def test_submit_task_enforces_max_concurrent_and_queues_excess():
    sm = SubagentManager(tool_registry=None, model_manager=None,
                         max_concurrent=2)
    sid = sm.create("analyst", subagent_id="sa_limit")
    sub = sm.get(sid)

    lock = threading.Lock()
    started = threading.Semaphore(0)
    release = threading.Event()
    state = {"active": 0, "max_active": 0, "finished": 0}

    def fake_execute(task, max_steps=None):
        with lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        started.release()
        release.wait(3)
        with lock:
            state["active"] -= 1
            state["finished"] += 1
        return None

    sub.execute_task = fake_execute  # 假执行器：不进入 LLM 循环

    for i in range(5):
        sm.submit_task(sid, f"goal-{i}")

    # 前两个先真正开跑，其余三个必须仍在信号量上排队。
    assert started.acquire(timeout=2) and started.acquire(timeout=2)
    deadline = time.time() + 2
    while time.time() < deadline and sm.get_stats()["active_tasks"] < 2:
        time.sleep(0.01)
    stats = sm.get_stats()
    assert stats["concurrency_enforced"] is True
    assert stats["active_tasks"] == 2
    assert stats["queued_tasks"] == 3, stats
    assert state["max_active"] == 2

    release.set()
    deadline = time.time() + 3
    while time.time() < deadline and sm.get_stats()["queued_tasks"]:
        time.sleep(0.01)
    deadline = time.time() + 3
    while time.time() < deadline and state["finished"] < 5:
        time.sleep(0.01)
    assert state["finished"] == 5
    assert state["max_active"] <= 2, "信号量失效：并发超过 max_concurrent"


def test_nested_submission_does_not_deadlock_when_permits_exhausted():
    """父任务持有唯一许可并等待子任务时，子任务不得再排队等同一许可。"""
    sm = SubagentManager(tool_registry=None, model_manager=None,
                         max_concurrent=1)
    sid = sm.create("analyst", subagent_id="sa_nested")
    sub = sm.get(sid)

    child_done = threading.Event()

    def fake_execute(task, max_steps=None):
        if task.goal == "root":
            token = _SUBAGENT_DEPTH.set(1)
            try:
                sm.submit_task(sid, "child")
            finally:
                _SUBAGENT_DEPTH.reset(token)
            # 父任务等子任务完成：若子任务在等父任务释放许可就永远等不到。
            assert child_done.wait(2), "嵌套子任务被并发许可卡死"
        else:
            child_done.set()
        return None

    sub.execute_task = fake_execute
    sm.submit_task(sid, "root")
    assert child_done.wait(2), "嵌套子任务没有执行"


def test_status_only_claims_limit_when_enforced():
    enforced = SubagentManager(tool_registry=None, max_concurrent=3)
    agent = object.__new__(MBDSDRAgent)
    agent.subagent_manager = enforced
    sa = agent._subagents_status()
    assert sa["concurrency_enforced"] is True
    assert sa["max_concurrent"] == 3

    unlimited = SubagentManager(tool_registry=None, max_concurrent=0)
    agent.subagent_manager = unlimited
    sa = agent._subagents_status()
    assert sa["concurrency_enforced"] is False
    assert sa["max_concurrent"] is None


# ---------------------------------------------------------------------------
# Task 38：深度限制
# ---------------------------------------------------------------------------
def test_depth_increments_for_root_subagent_and_resets():
    model = _ScriptedModel([
        {"success": True, "content": "ok", "tool_calls": []},
    ])
    sub = Subagent(
        subagent_id="sub_depth",
        agent_type="analyst",
        tool_registry=_FakeRegistry({"sdr_status": "状态"}),
        model_manager=model,
        allowed_tools=["sdr_status"],
    )
    result = sub.execute_task(SubagentTask(task_id="t_depth", goal="g"))
    assert result.success is True
    assert result.depth == 1
    assert result.data["depth"] == 1
    assert current_subagent_depth() == 0, "执行结束后深度必须复位"


def test_nested_depth_limit_rejects_even_with_explicit_whitelist():
    """主 agent→子 agent(depth1)→孙 agent(depth2)；孙 agent 再想嵌套必须被拒。"""
    parent_registry = _FakeRegistry({"subagent_execute": "嵌套入口"})
    child_registry = _FakeRegistry({"subagent_execute": "嵌套入口"})
    manager = SubagentManager(tool_registry=child_registry,
                              model_manager=None, max_concurrent=2)

    # 孙 agent（depth2）：显式把 subagent_execute 放进白名单试图绕过。
    child_model = _ScriptedModel([
        {"success": True, "content": "再嵌套一次。", "tool_calls": [
            _tool_call("subagent_execute",
                       {"subagent_id": "x", "goal": "y"}, call_id="deep")]},
        {"success": True, "content": "被拒后收尾。", "tool_calls": []},
    ])
    child_id = manager.create("analyst", subagent_id="child_depth",
                              allowed_tools=["subagent_execute"])
    child = manager.get(child_id)
    child.model_manager = child_model

    # 子 agent（depth1）：允许嵌套，回调真的调用 manager.execute_task。
    def run_child(tool_call):
        return manager.execute_task(child_id, "nested")

    parent_registry.on_subagent = run_child
    parent_model = _ScriptedModel([
        {"success": True, "content": "交给子代理。", "tool_calls": [
            _tool_call("subagent_execute",
                       {"subagent_id": child_id, "goal": "nested"},
                       call_id="nest")]},
        {"success": True, "content": "父代理完成。", "tool_calls": []},
    ])
    parent = Subagent(
        subagent_id="parent_depth",
        agent_type="analyst",
        tool_registry=parent_registry,
        model_manager=parent_model,
        allowed_tools=["subagent_execute"],
    )

    result = parent.execute_task(SubagentTask(task_id="t_nested", goal="g"))
    assert result.success is True
    # depth1 的显式白名单允许嵌套：parent registry 收到调用。
    assert parent_registry.calls == ["subagent_execute"]

    # depth2 的孙 agent 再调用 subagent_execute：白名单写了也不行。
    assert child_registry.calls == [], "深度超限时不得触达 registry"
    denied = [m for m in child.context
              if m.get("role") == "tool"
              and "subagent_depth_exceeded" in (m.get("content") or "")]
    assert denied, "必须把结构化深度拒绝回灌给子代理"
    offered = {t["function"]["name"] for t in (child_model.calls[0]["tools"] or [])}
    assert "subagent_execute" not in offered, "深度超限后视图不再广告 subagent_*"


def test_max_depth_constant_default_is_two():
    assert MAX_SUBAGENT_DEPTH == 2
