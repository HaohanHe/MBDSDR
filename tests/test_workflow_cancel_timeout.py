# SPDX-License-Identifier: MIT
"""
tests/test_workflow_cancel_timeout.py
=====================================
#82 回归锁：工作流引擎「可取消」+「step.timeout 真落地」。

修的两个真缺陷（都在 LLM 实际走的 workflow_engine 路径上）：
  ① 无取消——chat 的 request_cancel()/_cancel_event 只在顶层工具间安全点
     生效；workflow_execute 是单个工具，但内部多步同步跑到底，用户中途停不了。
  ② step.timeout 死参数——WorkflowStep 声明 timeout=30 且序列化，但 execute()
     从不消费它；tool_registry.call 此前无 per-call 超时覆盖，default_timeout_s
     默认 None（opt-in off），挂死步骤会永久阻塞主循环（AGENTS.md §2 死参数=bug）。

钉住的事实（每条都配反向用例，绝不只测 happy path）：
  A. 取消：开始前取消→0 完成；中途取消→在途工具跑完落账、后续步骤标记
     cancelled；重试等待前取消→不再重试；cancel_event=None / 未置位→旧行为
     逐字节不变；取消不计入 success_count。
  B. step.timeout：支持 timeout_s 的执行器真收到 step.timeout（含默认 30）；
     外部两参执行器仍按两参调用、每步只调一次（绝不 try/except 重跑）。
  C. registry per-call 覆盖：显式 timeout_s 真把挂死 handler 限时返回
     tool_timeout；None 不启用超时；显式 <=0 关闭超时（即便 registry 默认为正）；
     per-call 覆盖工具注册值。
  D. 端到端：工作流按 step.timeout 限时一个挂死工具，整体判失败而非永久阻塞。
  E. agent 接线：_workflow_tool_executor 透传 timeout_s；workflow_execute
     handler 把 self._cancel_event 传进引擎。

运行:
    PYTHONUTF8=1 QT_QPA_PLATFORM=offscreen MBDSDR_OFFLINE=1 \
        python -m pytest tests/test_workflow_cancel_timeout.py -v
"""
import json
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.workflow_engine import (  # noqa: E402
    Workflow, WorkflowStep, WorkflowEngine, accepts_kwarg,
)
from mbdsdr_ai.tool_registry import ToolRegistry, ToolResult  # noqa: E402


# ── 公共脚手架 ──────────────────────────────────────────────────

class _R:
    """最小 ToolResult 鸭子类型（execute 读 .success/.content/.data）。"""
    def __init__(self, content="ok"):
        self.success = True
        self.content = content
        self.data = {}


def _engine_with_steps(tmp_path, n=3, timeout=30,
                       max_retries=0, retry_on_failure=False):
    """造一个注入 n 步工作流的引擎（不碰用户真实 workflows_dir）。"""
    we = WorkflowEngine(workflows_dir=str(tmp_path))
    steps = [
        WorkflowStep(step_id=i + 1, tool_name=f"t{i + 1}", timeout=timeout,
                     max_retries=max_retries, retry_on_failure=retry_on_failure)
        for i in range(n)
    ]
    we.workflows["flow"] = Workflow(name="flow", steps=steps)
    return we


def _reg_tool(reg, name, handler, timeout_s=None):
    """注册一个非写工具（write=False 避开确认门），返回 name。"""
    reg.register(
        name=name, description="测试工具",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=handler, write=False, timeout_s=timeout_s,
    )
    return name


# ── A. 取消 ─────────────────────────────────────────────────────

class TestCancel:
    def test_cancel_before_start_zero_completed(self, tmp_path):
        """开始前就取消：执行器一次都不调，全部步骤标记 cancelled。"""
        we = _engine_with_steps(tmp_path, n=3)
        calls = []

        def _exec(name, params):
            calls.append(name)
            return _R()

        we.set_tool_executor(_exec)
        ev = threading.Event()
        ev.set()
        res = we.execute("flow", cancel_event=ev)

        assert res.success is False
        assert res.completed_steps == 0
        assert res.error_code == "cancelled"
        assert calls == [], "取消后执行器不应被调用"
        assert len(res.step_results) == 3
        assert all(s.get("cancelled") is True for s in res.step_results)

    def test_cancel_mid_workflow_records_inflight_then_stops(self, tmp_path):
        """安全点哲学：在途工具跑完并落账，后续步骤不再启动。"""
        we = _engine_with_steps(tmp_path, n=3)
        ev = threading.Event()
        calls = []

        def _exec(name, params):
            calls.append(name)
            if len(calls) == 1:
                ev.set()  # 用户在第 1 步工具执行期间点了取消
            return _R()

        we.set_tool_executor(_exec)
        res = we.execute("flow", cancel_event=ev)

        assert res.success is False
        assert res.completed_steps == 1, "第 1 步已跑完应落账"
        assert calls == ["t1"], "第 2/3 步不应启动"
        assert res.error_code == "cancelled"
        assert res.step_results[0]["success"] is True
        assert res.step_results[1].get("cancelled") is True
        assert res.step_results[2].get("cancelled") is True

    def test_cancel_during_retry_wait_no_retry(self, tmp_path):
        """安全点②：重试等待前取消→只调一次，不再 sleep/重试。"""
        we = _engine_with_steps(tmp_path, n=1, max_retries=2,
                                retry_on_failure=True)
        ev = threading.Event()
        calls = []

        def _exec(name, params):
            calls.append(name)
            ev.set()  # 取消在首次失败后、重试等待前生效
            raise RuntimeError("boom")

        we.set_tool_executor(_exec)
        res = we.execute("flow", cancel_event=ev)

        assert calls == ["t1"], "取消后不应重试（只调一次）"
        assert res.success is False
        assert res.completed_steps == 0
        assert res.error_code == "cancelled"
        assert res.step_results[0].get("cancelled") is True

    def test_cancel_none_unchanged_behavior(self, tmp_path):
        """反向：cancel_event=None→旧行为逐字节不变（全跑、成功、无码）。"""
        we = _engine_with_steps(tmp_path, n=3)
        calls = []

        def _exec(name, params):
            calls.append(name)
            return _R()

        we.set_tool_executor(_exec)
        res = we.execute("flow")  # 不传 cancel_event

        assert res.success is True
        assert res.completed_steps == 3
        assert calls == ["t1", "t2", "t3"]
        assert res.error_code == ""

    def test_cancel_event_unset_runs_all(self, tmp_path):
        """反向：传一个未置位的 Event≠取消，照常全跑。"""
        we = _engine_with_steps(tmp_path, n=3)
        calls = []

        def _exec(name, params):
            calls.append(name)
            return _R()

        we.set_tool_executor(_exec)
        res = we.execute("flow", cancel_event=threading.Event())

        assert res.success is True
        assert res.completed_steps == 3
        assert calls == ["t1", "t2", "t3"]

    def test_cancel_not_counted_as_success(self, tmp_path):
        """取消的运行 usage_count+1 但 success_count 不动。"""
        we = _engine_with_steps(tmp_path, n=2)

        def _exec(name, params):
            return _R()

        we.set_tool_executor(_exec)
        ev = threading.Event()
        ev.set()
        we.execute("flow", cancel_event=ev)

        wf = we.workflows["flow"]
        assert wf.usage_count == 1
        assert wf.success_count == 0, "取消不得计入成功统计"


# ── B. step.timeout 下发 ────────────────────────────────────────

class TestStepTimeoutForwarding:
    def test_timeout_forwarded_to_supporting_executor(self, tmp_path):
        """死参数修复核心钉：支持 timeout_s 的执行器真收到 step.timeout。"""
        we = _engine_with_steps(tmp_path, n=2, timeout=7)
        seen = []

        def _exec(name, params, timeout_s=None):
            seen.append((name, timeout_s))
            return _R()

        we.set_tool_executor(_exec)
        assert we._executor_accepts_timeout is True
        res = we.execute("flow")

        assert res.success is True
        assert seen == [("t1", 7), ("t2", 7)], "step.timeout 必须被消费"

    def test_default_timeout_30_forwarded(self, tmp_path):
        """未显式设 timeout 的步骤，默认 30 也要下发（不被丢弃）。"""
        we = WorkflowEngine(workflows_dir=str(tmp_path))
        we.workflows["flow"] = Workflow(
            name="flow", steps=[WorkflowStep(step_id=1, tool_name="t1")])
        seen = []

        def _exec(name, params, timeout_s=None):
            seen.append(timeout_s)
            return _R()

        we.set_tool_executor(_exec)
        we.execute("flow")
        assert seen == [30]

    def test_two_arg_executor_still_works_no_double_call(self, tmp_path):
        """反向：外部两参执行器仍按两参调用，每步只调一次（无 TypeError 重跑）。"""
        we = _engine_with_steps(tmp_path, n=2, timeout=7)
        calls = []

        def _exec(name, params):  # 没有 timeout_s
            calls.append(name)
            return _R()

        we.set_tool_executor(_exec)
        assert we._executor_accepts_timeout is False
        res = we.execute("flow")

        assert res.success is True
        assert res.completed_steps == 2
        assert calls == ["t1", "t2"], "每步恰好一次，绝不因探测签名而重跑"

    def test_accepts_kwarg_detection(self):
        """accepts_kwarg 一次性签名探测的各形态。"""
        def two(name, params):
            pass

        def with_kw(name, params, timeout_s=None):
            pass

        def kw_only(name, params, *, timeout_s=None):
            pass

        def var_kw(name, params, **kw):
            pass

        assert accepts_kwarg(two, "timeout_s") is False
        assert accepts_kwarg(with_kw, "timeout_s") is True
        assert accepts_kwarg(kw_only, "timeout_s") is True
        assert accepts_kwarg(var_kw, "timeout_s") is True
        # 内建无签名：保守判 False（走两参），绝不抛
        assert accepts_kwarg(len, "timeout_s") is False


# ── C. registry per-call 超时覆盖 ───────────────────────────────

class TestRegistryTimeoutOverride:
    def test_per_call_timeout_bounds_hang(self):
        """显式 timeout_s 把挂死 handler 限时返回 tool_timeout（真靶）。"""
        reg = ToolRegistry()  # default_timeout_s=None（off）
        gate = threading.Event()

        def hung(args):
            gate.wait(5.0)  # 模拟硬件挂死
            return ToolResult(success=True, content="late")

        _reg_tool(reg, "hung", hung)
        t0 = time.time()
        try:
            res = reg.call("hung", {}, timeout_s=0.15)
        finally:
            gate.set()  # 放行 daemon 线程，避免 lingering
        elapsed = time.time() - t0

        assert res.success is False
        assert res.error == "tool_timeout"
        assert elapsed < 2.0, f"应在 ~0.15s 返回而非等满 5s 挂死，实测 {elapsed:.2f}s"

    def test_none_does_not_enable_timeout(self):
        """反向：timeout_s=None 且 registry 默认 None→内联跑完，不超时。"""
        reg = ToolRegistry()

        def slowish(args):
            time.sleep(0.2)
            return ToolResult(success=True, content="ok")

        _reg_tool(reg, "slowish", slowish)
        res = reg.call("slowish", {}, timeout_s=None)
        assert res.success is True

    def test_explicit_zero_disables_timeout_even_with_positive_default(self):
        """反向：显式 <=0 关闭超时，即便 registry 默认为正也不限时。"""
        reg = ToolRegistry(default_timeout_s=0.1)

        def slowish(args):
            time.sleep(0.25)
            return ToolResult(success=True, content="ok")

        _reg_tool(reg, "slowish", slowish)
        res = reg.call("slowish", {}, timeout_s=0)  # 显式 0 覆盖默认 0.1
        assert res.success is True, "显式 0 应内联跑完，不被默认 0.1 掐断"

    def test_per_call_overrides_tool_registered_timeout(self):
        """per-call 覆盖优先于工具注册值：注册 5s，调用 0.1s→按 0.1s 超时。"""
        reg = ToolRegistry()
        gate = threading.Event()

        def hung(args):
            gate.wait(5.0)
            return ToolResult(success=True, content="late")

        _reg_tool(reg, "hung", hung, timeout_s=5.0)
        t0 = time.time()
        try:
            res = reg.call("hung", {}, timeout_s=0.1)
        finally:
            gate.set()
        elapsed = time.time() - t0

        assert res.success is False
        assert res.error == "tool_timeout"
        assert elapsed < 2.0


# ── D. 端到端：工作流限时挂死工具 ───────────────────────────────

class TestWorkflowBoundsHang:
    def test_step_timeout_bounds_hanging_tool(self, tmp_path):
        """工作流按 step.timeout 限时挂死工具→整体判失败，而非永久阻塞。"""
        reg = ToolRegistry()
        gate = threading.Event()

        def hung(args):
            gate.wait(10.0)
            return ToolResult(success=True, content="late")

        _reg_tool(reg, "hung", hung)

        we = WorkflowEngine(workflows_dir=str(tmp_path))
        we.workflows["flow"] = Workflow(
            name="flow",
            steps=[WorkflowStep(step_id=1, tool_name="hung", timeout=1,
                                max_retries=0, retry_on_failure=False)])

        def adapter(name, params, timeout_s=None):
            # 镜像 agent._workflow_tool_executor：透传 timeout_s，失败抛异常
            r = reg.call(name, params, timeout_s=timeout_s)
            if r.success:
                return r
            raise Exception(r.error or "fail")

        we.set_tool_executor(adapter)
        t0 = time.time()
        try:
            res = we.execute("flow")
        finally:
            gate.set()
        elapsed = time.time() - t0

        assert res.success is False
        assert res.completed_steps == 0
        assert res.failed_step == 1
        assert "tool_timeout" in res.step_results[0]["error"]
        assert elapsed < 3.0, f"应被 step.timeout=1 限时，实测 {elapsed:.2f}s"


# ── E. agent 接线 ───────────────────────────────────────────────

@pytest.fixture()
def agent():
    from mbdsdr_ai.agent import MBDSDRAgent
    from mbdsdr_ai.config import AgentConfig
    return MBDSDRAgent(AgentConfig())


class TestAgentWiring:
    def test_workflow_executor_forwards_timeout(self, agent):
        """_workflow_tool_executor 透传 timeout_s→registry 限时→失败抛异常。"""
        gate = threading.Event()

        def hung(args):
            gate.wait(5.0)
            return ToolResult(success=True, content="late")

        _reg_tool(agent.tool_registry, "hung_wf", hung)
        t0 = time.time()
        try:
            with pytest.raises(Exception):
                agent._workflow_tool_executor("hung_wf", {}, timeout_s=0.15)
        finally:
            gate.set()
        assert time.time() - t0 < 2.0, "超时应被透传并限时，而非等满 5s"

    def test_workflow_executor_two_arg_call_still_works(self, agent):
        """反向：scheduler 式两参调用（timeout_s 默认 None）照常成功。"""
        def fast(args):
            return ToolResult(success=True, content="ok")

        _reg_tool(agent.tool_registry, "fast_wf", fast)
        r = agent._workflow_tool_executor("fast_wf", {})  # 不传 timeout_s
        assert r.success is True

    def test_workflow_execute_handler_passes_cancel_event(self, agent):
        """workflow_execute handler 把 self._cancel_event 传进引擎。"""
        agent._ensure_concurrency_state()  # 惰性创建 _cancel_event
        ev = agent._cancel_event
        agent.workflow_engine.workflows["cancel_flow"] = Workflow(
            name="cancel_flow",
            steps=[WorkflowStep(step_id=i + 1, tool_name=f"t{i + 1}")
                   for i in range(3)])
        calls = []

        def _exec(name, params, timeout_s=None):
            calls.append(name)
            if len(calls) == 1:
                ev.set()  # 第 1 步执行期间用户取消
            return _R()

        agent.workflow_engine.set_tool_executor(_exec)
        handler = agent.tool_registry.tools["workflow_execute"]["handler"]
        res = handler({"workflow_name": "cancel_flow"})
        data = json.loads(res.content)

        assert data["error_code"] == "cancelled"
        assert data["completed_steps"] == 1
        assert calls == ["t1"], "handler 必须把取消事件接进引擎"