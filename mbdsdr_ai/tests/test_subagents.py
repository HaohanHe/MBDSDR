# SPDX-License-Identifier: MIT
"""subagents 隔离执行测试（2026-10-14 kimi-code 审计 Top-4 落地面）。

背景：本仓自审计 docs/audit_r2/34_subagents_orchestrator.md 早已登记「骨架在，
缺隔离上下文 + 真循环」（F3 allowed_tools 假隔离 / F5 无 LLM 循环 / X4 执行
路径零测试覆盖）。本测试钉住 execute_task 升级后的契约：

1. 两轮脚本 stub（先要工具、后给结论）验证真 LLM 循环 + 上下文隔离
   （system=类型提示+任务描述、assistant/tool 成对回灌、每任务重置上下文）；
2. max_steps 截断诚实（status=truncated + truncated=True + 带标记 partial）；
3. allowed_tools 越界本地被拒（不触达 registry）+ subagent_* 递归防护；
4. model_manager 缺失 / 模型调用失败 / chat 抛异常 → status=error 诚实报错，
   output 为空不伪造；
5. 既有 API/调用方不破（Manager CRUD、新旧字段镜像、to_dict 键集合、
   can_use_tool 旧语义、SubagentTask 旧式构造）；
6. 上下文预算超限时最老完整轮次成对摘除（system+首条 user 永保留）。

stub 手法同 test_tool_result_spill.py：小类 duck-type（SimpleNamespace 风格），
不触网、不触硬件、不实例化真 SDRController。
"""
from __future__ import annotations

import json

from mbdsdr_ai.subagents import (SUBAGENT_PROMPTS, Subagent, SubagentManager,
                                 SubagentStatus, SubagentTask)
from mbdsdr_ai.tool_registry import ToolResult


# ---------------------------------------------------------------------------
# stub 助手
# ---------------------------------------------------------------------------
def _tool_call(name, args=None, call_id="call_1"):
    """构造 OpenAI function calling 格式的工具调用。"""
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args or {})}}


class _ScriptedModel:
    """按脚本逐条返回 chat 响应的 stub model_manager（不触网）。

    脚本项可以是 dict（直接作为响应）或 Exception（chat 抛出，模拟客户端
    异常）。记录每次调用的 messages/tools，供隔离与视图断言。
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, messages=None, tools=None, tool_choice="auto", **kwargs):
        self.calls.append({
            "messages": [dict(m) for m in (messages or [])],
            "tools": list(tools or []),
        })
        item = self.script.pop(0) if self.script else {
            "success": True, "content": "（脚本已耗尽，默认收尾）",
            "tool_calls": []}
        if isinstance(item, Exception):
            raise item
        return item

    @staticmethod
    def parse_tool_calls_from_text(content, valid_names):
        return []


class _LoopingModel(_ScriptedModel):
    """每一步都返回同一个工具调用的 stub（模拟停不下来的子代理）。"""

    def __init__(self, tool_name, content="再测一次。"):
        super().__init__([])
        self.tool_name = tool_name
        self.content = content

    def chat(self, messages=None, tools=None, tool_choice="auto", **kwargs):
        self.calls.append({
            "messages": [dict(m) for m in (messages or [])],
            "tools": list(tools or []),
        })
        return {"success": True, "content": self.content,
                "tool_calls": [_tool_call(
                    self.tool_name, call_id=f"call_{len(self.calls)}")]}


class _FakeRegistry:
    """duck-type tool_registry：只实现子代理循环会调到的三个方法。"""

    def __init__(self, tools=None, content=None):
        self.tools = dict(tools or {})
        self.content = content
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
        fn = tool_call.get("function", {})
        name = fn.get("name", "")
        self.calls.append((name, fn.get("arguments", "")))
        return ToolResult(
            success=True,
            content=self.content or f"[stub] {name} 已执行",
            tool_name=name)


def _make_subagent(model, tools=None, allowed=("sdr_spectrum_analyze",)):
    """造一个 spectrum_analyzer 子代理（默认只放行 sdr_spectrum_analyze）。"""
    return Subagent(
        subagent_id="sub_test",
        agent_type="spectrum_analyzer",
        tool_registry=_FakeRegistry(
            tools if tools is not None else {"sdr_spectrum_analyze": "频谱分析"}),
        model_manager=model,
        allowed_tools=list(allowed) if allowed is not None else None,
    )


def _assert_tool_pairs_well_formed(ctx):
    """断言 assistant(tool_calls) 与其 tool 消息条数相等（无落了单的半对）。"""
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
                f"但只跟了 {n} 条 tool 消息")
            i = j
        else:
            i += 1


# ---------------------------------------------------------------------------
# 1. 两轮循环 + 上下文隔离
# ---------------------------------------------------------------------------
def test_two_round_llm_loop_and_context_isolation():
    model = _ScriptedModel([
        {"success": True, "content": "先测一下频谱。", "usage": {},
         "tool_calls": [_tool_call("sdr_spectrum_analyze",
                                   {"frequency_hz": 100000000})]},
        {"success": True, "content": "结论：噪声底 -95dBm，未见强信号。",
         "tool_calls": []},
    ])
    sub = _make_subagent(model)
    task = SubagentTask(task_id="t1", goal="分析 100MHz 频谱")
    result = sub.execute_task(task)

    # 循环完成：两步、最终文本原样回收、无截断无错误
    assert result.success is True
    assert result.status == "ok"
    assert result.truncated is False
    assert result.output == "结论：噪声底 -95dBm，未见强信号。"
    assert result.error == ""
    assert result.steps_used == 2 and result.steps_executed == 2
    assert result.tools_used == ["sdr_spectrum_analyze"]
    assert result.tools_called == ["sdr_spectrum_analyze"]

    # 工具真被调用一次（经 tool_registry.call_from_model 既有分发）
    assert [n for n, _ in sub.tool_registry.calls] == ["sdr_spectrum_analyze"]

    # 上下文隔离：system=类型提示+任务描述；user=goal；assistant/tool 成对回灌
    ctx = sub.context
    assert ctx[0]["role"] == "system"
    assert SUBAGENT_PROMPTS["spectrum_analyzer"][:20] in ctx[0]["content"]
    assert "分析 100MHz 频谱" in ctx[0]["content"]
    assert ctx[1]["role"] == "user" and "分析 100MHz 频谱" in ctx[1]["content"]
    roles = [m["role"] for m in ctx]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    tool_msg = ctx[3]
    assert tool_msg["tool_call_id"] == "call_1"
    assert tool_msg["name"] == "sdr_spectrum_analyze"
    assert "[stub] sdr_spectrum_analyze 已执行" in tool_msg["content"]
    _assert_tool_pairs_well_formed(ctx)

    # 第二轮 chat 看到的是回灌后的完整上下文（隔离且自洽；快照取自 chat
    # 时刻，故不含其后才追加的最终 assistant 结论）
    assert [m["role"] for m in model.calls[1]["messages"]] == roles[:4]

    # 每任务重置：第二个任务不继承上个任务的对话，首个任务结果不受影响
    model.script.append({"success": True, "content": "第二次结论",
                         "tool_calls": []})
    result2 = sub.execute_task(SubagentTask(task_id="t2", goal="第二次任务"))
    assert result2.output == "第二次结论"
    assert len(sub.context) == 3  # system + user + 最终 assistant
    assert "第二次任务" in sub.context[1]["content"]
    assert result.output == "结论：噪声底 -95dBm，未见强信号。"

    # 记账/生命周期照旧
    assert sub.total_tasks_completed == 2 and sub.total_tasks_failed == 0
    assert task.status == SubagentStatus.COMPLETED
    assert sub.task_history and sub.task_history[-1].result is result2
    assert sub.status == SubagentStatus.IDLE and sub.current_task is None


def test_input_data_enters_isolated_context():
    model = _ScriptedModel([
        {"success": True, "content": "ok", "tool_calls": []},
    ])
    sub = _make_subagent(model)
    result = sub.execute_task(SubagentTask(
        task_id="t_in", goal="分析频谱",
        input_data={"frequency_hz": 144500000, "span_hz": 20000}))

    assert result.success is True
    joined = json.dumps(sub.context, ensure_ascii=False)
    assert "144500000" in joined and "20000" in joined


# ---------------------------------------------------------------------------
# 2. max_steps 截断诚实
# ---------------------------------------------------------------------------
def test_max_steps_truncation_is_honest():
    model = _LoopingModel("sdr_spectrum_analyze")
    sub = _make_subagent(model)
    task = SubagentTask(task_id="t_trunc", goal="永远分析不完的频谱", max_steps=2)
    result = sub.execute_task(task)

    assert result.success is False
    assert result.status == "truncated"
    assert result.truncated is True
    assert result.steps_used == 2
    assert "[已截断]" in result.output and "max_steps=2" in result.output
    assert "再测一次。" in result.output  # partial 内容保留，不静默丢弃
    assert "max_steps_exhausted" in result.error
    assert len(model.calls) == 2
    assert len(sub.tool_registry.calls) == 2
    # 截断不计入完成； Manager 视角的 task 状态同步为 FAILED
    assert sub.total_tasks_failed == 1 and sub.total_tasks_completed == 0
    assert task.status == SubagentStatus.FAILED
    d = result.to_dict()
    assert d["truncated"] is True and d["status"] == "truncated"


def test_max_steps_call_param_overrides_task_default():
    model = _LoopingModel("sdr_spectrum_analyze")
    sub = _make_subagent(model)
    result = sub.execute_task(SubagentTask(task_id="t3", goal="g"), max_steps=3)
    assert result.steps_used == 3 and result.truncated is True


def test_max_steps_subagent_default_is_eight():
    model = _LoopingModel("sdr_spectrum_analyze")
    sub = _make_subagent(model)
    result = sub.execute_task(SubagentTask(task_id="t3b", goal="g"))
    assert result.steps_used == 8 and result.truncated is True


# ---------------------------------------------------------------------------
# 3. 工具过滤：越界拒绝 + 递归防护
# ---------------------------------------------------------------------------
def test_out_of_whitelist_tool_call_denied_locally():
    model = _ScriptedModel([
        {"success": True, "content": "我想越界。", "tool_calls": [
            _tool_call("sdr_spectrum_analyze", call_id="ok_1"),
            _tool_call("guardian_rollback", {"snapshot": "x"}, call_id="bad_1"),
        ]},
        {"success": True, "content": "收到拒绝，改用白名单工具完成。",
         "tool_calls": []},
    ])
    sub = _make_subagent(model, allowed=["sdr_spectrum_analyze"])
    result = sub.execute_task(SubagentTask(task_id="t4", goal="g"))

    # 越界调用未触达 registry、循环不上抛、最终结论照常回收
    assert result.success is True
    assert result.output == "收到拒绝，改用白名单工具完成。"
    assert [n for n, _ in sub.tool_registry.calls] == ["sdr_spectrum_analyze"]
    assert result.tools_used == ["sdr_spectrum_analyze"]

    # 拒绝原因回灌成 tool 消息（LLM 可见、配对完好）
    denied = [m for m in sub.context
              if m.get("role") == "tool" and m.get("name") == "guardian_rollback"]
    assert len(denied) == 1 and "无权调用" in denied[0]["content"]
    assert denied[0]["tool_call_id"] == "bad_1"
    _assert_tool_pairs_well_formed(sub.context)


def test_recursion_guard_excludes_subagent_tools_when_allow_all():
    model = _ScriptedModel([
        {"success": True, "content": "递归一下。", "tool_calls": [
            _tool_call("subagent_execute", {"subagent_id": "x", "goal": "y"},
                       call_id="rec_1")]},
        {"success": True, "content": "递归被拒，自行完成。", "tool_calls": []},
    ])
    tools = {"sdr_spectrum_analyze": "频谱分析", "subagent_execute": "递归入口"}
    sub = _make_subagent(model, tools=tools, allowed=None)
    result = sub.execute_task(SubagentTask(task_id="t5", goal="g"))

    assert result.success is True
    assert sub.tool_registry.calls == []  # 未触达 registry
    denied = [m for m in sub.context
              if m.get("role") == "tool"
              and m.get("name") == "subagent_execute"]
    assert len(denied) == 1 and "无权调用" in denied[0]["content"]
    # 视图里也不广告 subagent_* 工具（白名单工具仍广告）
    offered = {t["function"]["name"] for t in (model.calls[0]["tools"] or [])}
    assert "sdr_spectrum_analyze" in offered
    assert not any(n.startswith("subagent_") for n in offered)


# ---------------------------------------------------------------------------
# 4. 诚实错误：model_manager 缺失 / 调用失败 / 抛异常
# ---------------------------------------------------------------------------
def test_missing_model_manager_is_honest_error():
    reg = _FakeRegistry({"sdr_spectrum_analyze": "频谱分析"})
    sub = _make_subagent(None, allowed=["sdr_spectrum_analyze"])
    result = sub.execute_task(SubagentTask(task_id="t6", goal="分析 100MHz"))

    assert result.success is False and result.status == "error"
    assert "model_manager" in result.error
    assert result.output == ""  # 绝不伪造输出
    assert result.steps_used == 0 and result.tools_used == []
    assert reg.calls == [] and sub.context == []
    assert sub.total_tasks_failed == 1 and sub.total_tasks_completed == 0


def test_model_call_failure_is_honest_error():
    model = _ScriptedModel([
        {"success": False, "error": "HTTP 502: bad gateway",
         "content": "", "tool_calls": []},
    ])
    sub = _make_subagent(model)
    result = sub.execute_task(SubagentTask(task_id="t7", goal="g"))

    assert result.success is False and result.status == "error"
    assert "HTTP 502" in result.error
    assert result.output == ""  # 绝不伪造输出
    assert sub.tool_registry.calls == []


def test_model_chat_exception_is_honest_error():
    model = _ScriptedModel([RuntimeError("连接被重置")])
    sub = _make_subagent(model)
    result = sub.execute_task(SubagentTask(task_id="t8", goal="g"))

    assert result.success is False and result.status == "error"
    assert "连接被重置" in result.error
    assert "调用异常" in result.error
    assert result.output == ""


# ---------------------------------------------------------------------------
# 5. 既有 API/调用方不破
# ---------------------------------------------------------------------------
def test_existing_api_and_callers_not_broken():
    sm = SubagentManager(
        tool_registry=_FakeRegistry({"sdr_spectrum_analyze": "d"}),
        model_manager=_ScriptedModel([]))

    # Manager CRUD 原样
    sid = sm.create(agent_type="spectrum_analyzer")
    assert sm.get(sid) is not None
    assert len(sm.list_subagents()) >= 1
    assert "total_subagents" in sm.get_stats()
    assert sm.destroy(sid) is True and sm.get(sid) is None

    # Manager 同步执行接线 + to_dict 新旧键集合
    model = _ScriptedModel([
        {"success": True, "content": "ok",
         "tool_calls": [_tool_call("sdr_spectrum_analyze")]},
        {"success": True, "content": "完成：底部 -95dBm", "tool_calls": []},
    ])
    sm2 = SubagentManager(
        tool_registry=_FakeRegistry({"sdr_spectrum_analyze": "d"}),
        model_manager=model)
    sid2 = sm2.create(agent_type="spectrum_analyzer")
    res = sm2.execute_task(sid2, "分析频谱")
    d = res.to_dict()
    assert d["success"] is True and d["output"] == "完成：底部 -95dBm"
    for key in ("status", "steps_used", "tools_used", "truncated"):
        assert key in d, f"to_dict 缺少新字段 {key}"
    for key in ("steps_executed", "tools_called", "execution_time",
                "subagent_id", "task_id", "data", "error"):
        assert key in d, f"to_dict 丢了旧字段 {key}"
    assert d["steps_executed"] == d["steps_used"] == 2
    assert d["tools_called"] == d["tools_used"] == ["sdr_spectrum_analyze"]
    assert d["truncated"] is False and d["status"] == "ok"

    # get_task_result / task_history 照旧
    assert sm2.get_task_result(sid2, res.task_id) is res

    # can_use_tool 公开语义不变（None=全部；非空=集合判定）
    sub = sm2.get(sid2)
    assert sub.can_use_tool("sdr_spectrum_analyze") is True
    assert sub.can_use_tool("不存在工具") is False
    sub.allowed_tools = None
    assert sub.can_use_tool("不存在工具") is True

    # SubagentTask 旧式构造照常（max_steps 默认与循环默认一致=8）
    t = SubagentTask("tid", "goal")
    assert t.max_steps == 8 and t.timeout_s == 120.0
    assert SubagentStatus.IDLE.value == "idle"


def test_manager_execute_without_model_manager_returns_error_result():
    sm = SubagentManager(
        tool_registry=_FakeRegistry({"sdr_spectrum_analyze": "d"}),
        model_manager=None)
    sid = sm.create(agent_type="spectrum_analyzer")
    res = sm.execute_task(sid, "分析频谱")
    assert res.success is False and res.status == "error"
    assert "model_manager" in res.error


# ---------------------------------------------------------------------------
# 6. 上下文预算：最老完整轮次成对摘除
# ---------------------------------------------------------------------------
def test_context_budget_trims_oldest_full_round():
    model = _ScriptedModel([
        {"success": True, "content": "round1",
         "tool_calls": [_tool_call("sdr_spectrum_analyze", call_id="c1")]},
        {"success": True, "content": "x" * 8000,
         "tool_calls": [_tool_call("sdr_spectrum_analyze", call_id="c2")]},
        {"success": True, "content": "最终结论", "tool_calls": []},
    ])
    sub = Subagent(
        subagent_id="sub_budget",
        agent_type="spectrum_analyzer",
        tool_registry=_FakeRegistry({"sdr_spectrum_analyze": "d"},
                                    content="y" * 8000),
        model_manager=model,
        allowed_tools=["sdr_spectrum_analyze"],
        max_context_tokens=1200,
    )
    result = sub.execute_task(SubagentTask(task_id="t9", goal="g"))

    assert result.success is True
    assert result.data["context_trimmed"] is True
    # system 与首条 user（任务目标）永保留
    assert sub.context[0]["role"] == "system"
    assert sub.context[1]["role"] == "user" and "g" in sub.context[1]["content"]
    # 摘除必须是完整轮次（assistant+其 tool 消息成对消失）
    _assert_tool_pairs_well_formed(sub.context)
