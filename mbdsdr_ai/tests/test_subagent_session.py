# SPDX-License-Identifier: MIT
"""子代理观测面测试（Sprint 6：独立 session + 事件冒泡 + hook 接通）。

背景：Sprint 6 之前子代理是黑盒——execute_task 内部的真 LLM 循环（创建/任务
起止/每步工具调用与结果/截断/错误）既不落盘也不上事件总线，父代理与 UI 无从
审计。本测试钉住升级后的三层观测契约：

A. Subagent._emit 单元：每个观测点写自己的独立 SessionLog（带 subagent_id/
   agent_type），并按 (subagent_id, event_type, fields) 向父代理冒泡；冒泡
   fields **不含** subagent_id（父代理自己加，防重复 kwarg）。
B. SubagentManager：create 落 "created"（含 session_log_path）、destroy 落
   "destroyed"；每个子代理一份物理隔离的 JSONL；enable_session_log=False 时
   不落盘但仍冒泡。
C. MBDSDRAgent._on_subagent_event：冒泡事件以 subagent.<type> 落父会话日志并
   带 subagent_id；hook 总线仅对有专门 SUBAGENT_* 枚举的类型触发（created/
   task_start/tool_call/tool_result/task_end/error），step/assistant_message/
   destroyed 只落日志不发 hook；fields=None 不崩。
D. 端到端：Manager 注入 agent._on_subagent_event 后，子代理活动同时落「独立
   JSONL」与「父会话日志（subagent.* 带 subagent_id）」，SUBAGENT_* hook 真触发
   ——子代理不再是黑盒。
E. 诚实语义：截断/错误在观测面同样诚实标注（truncated=True/status，绝不记成
   完成），与 execute_task 的返回值一致。

stub 手法复用 test_subagents.py（_ScriptedModel/_LoopingModel/_FakeRegistry/
_tool_call），不触网、不触硬件、不实例化真 SDRController；SessionLog 一律指向
tmp_path，绝不污染 ~/.mbdsdr。
"""
from __future__ import annotations

import json
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_TESTS_DIR))
sys.path.insert(0, _TESTS_DIR)

from mbdsdr_ai.hooks import EventType, HookManager  # noqa: E402
from mbdsdr_ai.session_log import SessionLog  # noqa: E402
from mbdsdr_ai.subagents import Subagent, SubagentManager, SubagentTask  # noqa: E402

# 复用既有 stub（同目录测试模块，已在 sys.path 上）
from test_subagents import (  # noqa: E402
    _FakeRegistry, _LoopingModel, _ScriptedModel, _tool_call,
)


# ---------------------------------------------------------------------------
# 冒泡收集器：复刻父代理 on_event 回调签名 (subagent_id, event_type, fields)
# ---------------------------------------------------------------------------
class _BubbleCollector:
    def __init__(self):
        self.events = []  # [(subagent_id, event_type, fields_dict)]

    def __call__(self, subagent_id, event_type, fields):
        self.events.append((subagent_id, event_type, dict(fields or {})))

    def types(self):
        return [t for _, t, _ in self.events]

    def by_type(self, event_type):
        return [(sid, f) for sid, t, f in self.events if t == event_type]


def _two_round_model():
    """先要一次工具、再给结论的两轮脚本（驱动完整观测序列）。"""
    return _ScriptedModel([
        {"success": True, "content": "先测频谱。", "usage": {},
         "tool_calls": [_tool_call("sdr_spectrum_analyze",
                                   {"frequency_hz": 100000000})]},
        {"success": True, "content": "结论：噪声底 -95dBm。", "tool_calls": []},
    ])


# ===========================================================================
# A. Subagent._emit 单元：独立 session log + 冒泡
# ===========================================================================
def test_emit_writes_own_session_log_and_bubbles(tmp_path):
    log = SessionLog("sub_a", directory=str(tmp_path))
    collector = _BubbleCollector()
    sub = Subagent(
        subagent_id="sub_a", agent_type="spectrum_analyzer",
        tool_registry=_FakeRegistry({"sdr_spectrum_analyze": "频谱分析"}),
        model_manager=_two_round_model(),
        allowed_tools=["sdr_spectrum_analyze"],
        session_log=log, on_event=collector,
    )
    result = sub.execute_task(SubagentTask(task_id="t1", goal="分析 100MHz"))
    assert result.success is True

    # 独立 session log：模型可见的每一步都落了账，且都带 subagent_id/agent_type
    events = log.read_events()
    types = [e["type"] for e in events]
    for expected in ("task_start", "step", "tool_call", "tool_result",
                     "assistant_message", "task_end"):
        assert expected in types, f"独立日志缺事件 {expected}"
    assert all(e["subagent_id"] == "sub_a" for e in events)
    assert all(e["agent_type"] == "spectrum_analyzer" for e in events)
    # seq 单调（append-only 完整性）
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))

    # 冒泡：父代理收到 (subagent_id, event_type, fields)，fields 不含 subagent_id
    assert collector.types() == types  # 冒泡与落盘同一序列
    for sid, etype, fields in collector.events:
        assert sid == "sub_a"
        assert "subagent_id" not in fields, "冒泡 fields 不得含 subagent_id（父代理自加）"
        assert fields.get("agent_type") == "spectrum_analyzer"


def test_emit_swallows_session_log_and_callback_errors(tmp_path):
    """观测通道任一坏掉都不拖垮 LLM 循环（对齐 SessionLog/_emit 红线）。"""
    class _BoomLog:
        path = None

        def emit(self, *a, **k):
            raise OSError("disk on fire")

    def _boom_cb(*a, **k):
        raise RuntimeError("callback exploded")

    sub = Subagent(
        subagent_id="sub_boom", agent_type="spectrum_analyzer",
        tool_registry=_FakeRegistry({"sdr_spectrum_analyze": "d"}),
        model_manager=_two_round_model(),
        allowed_tools=["sdr_spectrum_analyze"],
        session_log=_BoomLog(), on_event=_boom_cb,
    )
    # 两个观测通道都抛，execute_task 仍正常完成
    result = sub.execute_task(SubagentTask(task_id="tb", goal="g"))
    assert result.success is True and result.status == "ok"


def test_emit_without_observers_is_noop(tmp_path):
    """未注入 session_log/on_event 时 _emit 静默（既有用法零改）。"""
    sub = Subagent(
        subagent_id="sub_none", agent_type="spectrum_analyzer",
        tool_registry=_FakeRegistry({"sdr_spectrum_analyze": "d"}),
        model_manager=_two_round_model(),
        allowed_tools=["sdr_spectrum_analyze"],
    )
    assert sub.session_log is None and sub.on_event is None
    result = sub.execute_task(SubagentTask(task_id="tn", goal="g"))
    assert result.success is True  # 无观测面也照常跑


# ===========================================================================
# B. SubagentManager：created/destroyed + 物理隔离 JSONL + 开关
# ===========================================================================
def test_manager_create_writes_per_subagent_jsonl(tmp_path):
    collector = _BubbleCollector()
    sm = SubagentManager(
        tool_registry=_FakeRegistry({"sdr_spectrum_analyze": "d"}),
        model_manager=_two_round_model(),
        on_event=collector, enable_session_log=True,
        session_log_dir=str(tmp_path),
    )
    sid = sm.create(agent_type="spectrum_analyzer", subagent_id="sub_mgr")

    # 每个子代理一份独立 JSONL，落在指定目录
    path = tmp_path / "sub_mgr.jsonl"
    assert path.exists(), "create 应落一份独立 session JSONL"
    created = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[0])
    assert created["type"] == "created"
    assert created["subagent_id"] == "sub_mgr"
    assert created["agent_type"] == "spectrum_analyzer"
    assert created["session_log_path"] == str(path)
    assert created["allowed_tools"]  # 白名单或 "all"

    # created 也冒泡到父代理
    assert "created" in collector.types()


def test_manager_destroy_appends_destroyed_event(tmp_path):
    sm = SubagentManager(
        tool_registry=_FakeRegistry({"sdr_spectrum_analyze": "d"}),
        model_manager=_two_round_model(),
        enable_session_log=True, session_log_dir=str(tmp_path),
    )
    sid = sm.create(agent_type="spectrum_analyzer", subagent_id="sub_d")
    assert sm.destroy(sid) is True

    lines = (tmp_path / "sub_d.jsonl").read_text(
        encoding="utf-8").strip().splitlines()
    destroyed = json.loads(lines[-1])
    assert destroyed["type"] == "destroyed"
    assert destroyed["total_tasks_completed"] == 0
    assert destroyed["total_tasks_failed"] == 0


def test_manager_disable_session_log_skips_disk_but_still_bubbles(tmp_path):
    collector = _BubbleCollector()
    sm = SubagentManager(
        tool_registry=_FakeRegistry({"sdr_spectrum_analyze": "d"}),
        model_manager=_two_round_model(),
        on_event=collector, enable_session_log=False,
        session_log_dir=str(tmp_path),
    )
    sid = sm.create(agent_type="spectrum_analyzer", subagent_id="sub_off")
    sub = sm.get(sid)

    assert sub.session_log is None  # 不落盘
    assert not (tmp_path / "sub_off.jsonl").exists()
    assert "created" in collector.types()  # 仍冒泡

    res = sm.execute_task(sid, "分析频谱")
    assert res.success is True
    assert "task_start" in collector.types() and "task_end" in collector.types()


def test_manager_session_log_defaults_off_no_home_pollution(tmp_path, monkeypatch):
    """裸建 Manager 默认不落盘（opt-in），单测/CI 不污染 ~/.mbdsdr。

    生产路径由 MBDSDRAgent 显式按 config.enable_session_log 打开；这里钉住
    默认 False，防回归把默认改回 True 又往用户家目录写文件。用 monkeypatch
    把 SESSION_DIR 指到 tmp，即便默认被误改也不会真碰 ~/.mbdsdr。
    """
    import mbdsdr_ai.subagents as sa
    monkeypatch.setattr(sa, "SESSION_DIR", str(tmp_path))
    sm = SubagentManager(
        tool_registry=_FakeRegistry({"sdr_spectrum_analyze": "d"}),
        model_manager=_two_round_model(),
    )
    assert sm.enable_session_log is False  # 默认 opt-in
    sid = sm.create(agent_type="spectrum_analyzer", subagent_id="sub_default")
    assert sm.get(sid).session_log is None
    # 缺省目录（SESSION_DIR/subagents）下不应产生任何文件
    assert not (tmp_path / "subagents").exists()
    res = sm.execute_task(sid, "g")
    assert res.success is True  # 无观测面也照常跑



def test_manager_create_survives_unwritable_log_dir(tmp_path):
    """建独立日志失败（目录不可写）绝不阻断子代理创建（吞错降级 None）。"""
    bad = tmp_path / "afile"
    bad.write_text("not a dir", encoding="utf-8")
    sm = SubagentManager(
        tool_registry=_FakeRegistry({"sdr_spectrum_analyze": "d"}),
        model_manager=_two_round_model(),
        enable_session_log=True, session_log_dir=str(bad / "sub"),
    )
    sid = sm.create(agent_type="spectrum_analyzer", subagent_id="sub_bad")
    assert sm.get(sid) is not None  # 创建成功
    # SessionLog 自身对坏目录降级为 _broken，session_log 对象仍在但不写盘
    res = sm.execute_task(sid, "g")
    assert res.success is True  # 观测面坏掉不影响任务


# ===========================================================================
# C. MBDSDRAgent._on_subagent_event：父日志 subagent.* + 选择性 hook
# ===========================================================================
def _minimal_agent(tmp_path):
    """构造只够 _slog/_hook/_on_subagent_event 运转的最小 agent。

    用 object.__new__ 跳过重量级 __init__（同 test_agent_retry 手法）；
    hook_manager 用**真** HookManager 以便断言 SUBAGENT_* 真触发；
    _session_log 预置指向 tmp，避免 _slog 懒创建写到 ~/.mbdsdr。
    """
    from mbdsdr_ai.agent import MBDSDRAgent
    from mbdsdr_ai.config import AgentConfig

    agent = object.__new__(MBDSDRAgent)
    agent.config = AgentConfig(api_key="test-key-not-real")
    agent.conversation_id = "conv_parent"
    agent._session_log = SessionLog("conv_parent", directory=str(tmp_path))
    agent._session_log_conv = "conv_parent"
    agent.hook_manager = HookManager()
    return agent


def test_on_subagent_event_logs_and_fires_hook(tmp_path):
    agent = _minimal_agent(tmp_path)
    fired = []
    agent.hook_manager.register("*", lambda ev: fired.append(ev))

    agent._on_subagent_event(
        "sub_x", "created",
        {"agent_type": "spectrum_analyzer", "allowed_tools": "all"})

    # 父会话日志：subagent.created，带 subagent_id 与冒泡字段
    events = agent._session_log.read_events()
    assert len(events) == 1
    ev = events[0]
    assert ev["type"] == "subagent.created"
    assert ev["subagent_id"] == "sub_x"
    assert ev["agent_type"] == "spectrum_analyzer"

    # hook 总线：SUBAGENT_CREATED 真触发（按值路由 "subagent.created"）
    assert len(fired) == 1
    assert fired[0].event_type == EventType.SUBAGENT_CREATED.value
    assert fired[0].data["subagent_id"] == "sub_x"
    assert fired[0].source == "agent"


def test_on_subagent_event_all_six_types_fire_hooks(tmp_path):
    agent = _minimal_agent(tmp_path)
    fired = []
    agent.hook_manager.register("*", lambda ev: fired.append(ev.event_type))

    for etype in ("created", "task_start", "tool_call", "tool_result",
                  "task_end", "error"):
        agent._on_subagent_event("sub_y", etype, {"agent_type": "signal_decoder"})

    # 六个有专门 SUBAGENT_* 枚举的类型都发了 hook
    assert fired == [
        EventType.SUBAGENT_CREATED.value,
        EventType.SUBAGENT_TASK_START.value,
        EventType.SUBAGENT_TOOL_CALL.value,
        EventType.SUBAGENT_TOOL_RESULT.value,
        EventType.SUBAGENT_TASK_END.value,
        EventType.SUBAGENT_ERROR.value,
    ]
    # 父日志六条 subagent.* 全落
    types = [e["type"] for e in agent._session_log.read_events()]
    assert types == ["subagent.created", "subagent.task_start",
                     "subagent.tool_call", "subagent.tool_result",
                     "subagent.task_end", "subagent.error"]


def test_on_subagent_event_non_hook_types_log_only(tmp_path):
    """step/assistant_message/destroyed 无专门枚举：只落父日志，不发 hook。"""
    agent = _minimal_agent(tmp_path)
    fired = []
    agent.hook_manager.register("*", lambda ev: fired.append(ev.event_type))

    for etype in ("step", "assistant_message", "destroyed"):
        agent._on_subagent_event("sub_z", etype, {"agent_type": "satellite_tracker"})

    assert fired == [], "无 SUBAGENT_* 枚举的类型不得往总线灌无路由事件"
    types = [e["type"] for e in agent._session_log.read_events()]
    assert types == ["subagent.step", "subagent.assistant_message",
                     "subagent.destroyed"]


def test_on_subagent_event_none_fields_does_not_crash(tmp_path):
    agent = _minimal_agent(tmp_path)
    fired = []
    agent.hook_manager.register("*", lambda ev: fired.append(ev))
    # fields=None（回调允许缺省）
    agent._on_subagent_event("sub_n", "created", None)
    ev = agent._session_log.read_events()[0]
    assert ev["type"] == "subagent.created" and ev["subagent_id"] == "sub_n"
    assert len(fired) == 1


def test_on_subagent_event_swallows_hook_errors(tmp_path):
    """hook 回调抛异常不得冒泡出 _on_subagent_event（_hook 自身兜底）。"""
    agent = _minimal_agent(tmp_path)

    def _boom(ev):
        raise RuntimeError("hook exploded")

    agent.hook_manager.register("*", _boom)
    # 不应抛
    agent._on_subagent_event("sub_e", "task_end", {"status": "ok"})
    # 父日志仍落（_slog 与 _hook 各自独立）
    assert agent._session_log.read_events()[0]["type"] == "subagent.task_end"


# ===========================================================================
# D. 端到端：Manager 注入 agent._on_subagent_event → 双落地 + hook
# ===========================================================================
def test_end_to_end_subagent_not_a_black_box(tmp_path):
    agent = _minimal_agent(tmp_path)
    fired = []
    agent.hook_manager.register("*", lambda ev: fired.append(ev.event_type))

    sub_dir = tmp_path / "sublogs"
    sm = SubagentManager(
        tool_registry=_FakeRegistry({"sdr_spectrum_analyze": "d"}),
        model_manager=_two_round_model(),
        on_event=agent._on_subagent_event,  # 真接线：冒泡进父代理观测面
        enable_session_log=True, session_log_dir=str(sub_dir),
    )
    agent.subagent_manager = sm

    sid = sm.create(agent_type="spectrum_analyzer", subagent_id="sub_e2e")
    res = sm.execute_task(sid, "分析 100MHz 频谱")
    assert res.success is True
    sm.destroy(sid)

    # ① 子代理独立 JSONL：完整原始序列
    sub_lines = (sub_dir / "sub_e2e.jsonl").read_text(
        encoding="utf-8").strip().splitlines()
    sub_types = [json.loads(ln)["type"] for ln in sub_lines]
    assert sub_types[0] == "created"
    assert sub_types[-1] == "destroyed"
    for t in ("task_start", "step", "tool_call", "tool_result",
              "assistant_message", "task_end"):
        assert t in sub_types

    # ② 父会话日志：每条子代理活动以 subagent.* 落账并带 subagent_id
    parent_events = agent._session_log.read_events()
    parent_types = [e["type"] for e in parent_events]
    assert "subagent.created" in parent_types
    assert "subagent.task_start" in parent_types
    assert "subagent.tool_call" in parent_types
    assert "subagent.task_end" in parent_types
    assert "subagent.destroyed" in parent_types
    assert all(e["subagent_id"] == "sub_e2e" for e in parent_events)

    # ③ hook 总线：六个 SUBAGENT_* 类型都真触发过
    assert EventType.SUBAGENT_CREATED.value in fired
    assert EventType.SUBAGENT_TASK_START.value in fired
    assert EventType.SUBAGENT_TOOL_CALL.value in fired
    assert EventType.SUBAGENT_TOOL_RESULT.value in fired
    assert EventType.SUBAGENT_TASK_END.value in fired
    # step/assistant_message/destroyed 不发 hook（无专门枚举）
    assert "subagent.step" not in fired
    assert "subagent.assistant_message" not in fired
    assert "subagent.destroyed" not in fired


# ===========================================================================
# E. 诚实语义：截断/错误在观测面同样诚实
# ===========================================================================
def test_truncation_is_honestly_logged(tmp_path):
    collector = _BubbleCollector()
    log = SessionLog("sub_trunc", directory=str(tmp_path))
    sub = Subagent(
        subagent_id="sub_trunc", agent_type="spectrum_analyzer",
        tool_registry=_FakeRegistry({"sdr_spectrum_analyze": "d"}),
        model_manager=_LoopingModel("sdr_spectrum_analyze"),
        allowed_tools=["sdr_spectrum_analyze"],
        max_steps=2, session_log=log, on_event=collector,
    )
    result = sub.execute_task(SubagentTask(task_id="tt", goal="永远分析不完"))
    assert result.status == "truncated" and result.truncated is True

    # task_end 事件诚实标注 truncated/status/success，绝不记成完成
    task_end = [e for e in log.read_events() if e["type"] == "task_end"][0]
    assert task_end["status"] == "truncated"
    assert task_end["truncated"] is True
    assert task_end["success"] is False
    # 冒泡侧同样诚实
    bubbled = collector.by_type("task_end")[0][1]
    assert bubbled["truncated"] is True and bubbled["status"] == "truncated"


def test_error_is_honestly_logged(tmp_path):
    collector = _BubbleCollector()
    log = SessionLog("sub_err", directory=str(tmp_path))
    sub = Subagent(
        subagent_id="sub_err", agent_type="spectrum_analyzer",
        tool_registry=_FakeRegistry({"sdr_spectrum_analyze": "d"}),
        model_manager=_ScriptedModel([RuntimeError("连接被重置")]),
        allowed_tools=["sdr_spectrum_analyze"],
        session_log=log, on_event=collector,
    )
    result = sub.execute_task(SubagentTask(task_id="te", goal="g"))
    assert result.status == "error" and result.success is False

    types = [e["type"] for e in log.read_events()]
    assert "error" in types, "LLM 调用失败应落 error 事件"
    err_ev = [e for e in log.read_events() if e["type"] == "error"][0]
    assert err_ev["code"] == "RuntimeError"
    assert "连接被重置" in err_ev["detail"]
    # task_end 仍诚实标注 error 状态
    task_end = [e for e in log.read_events() if e["type"] == "task_end"][0]
    assert task_end["status"] == "error" and task_end["success"] is False
    assert "error" in collector.types()
