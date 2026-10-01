# SPDX-License-Identifier: MIT
"""
Phase4 (P4) 缺口清扫针对性测试
==============================
覆盖 A2 遗留项在 P4 的处置：
- D6: workflow_execute 跨步 {{var}} 回填（executor 返回 ToolResult + .data sidecar）
- D7: pose_update_imu/gps 诚实标注（不再宣称硬件来源）
- D3: learning_learn / learning_suggestion LLM 面摘除
- D9: subagent_create 描述收窄到真实支持的类型
- D10: scheduler cron（含星期映射 周日=0 修复）
- sdr_tools: sdr_set_frequency/set_sample_rate/set_demod 单调用 + 无设备诚实失败
"""

import time
import pytest

from mbdsdr_ai.config import AgentConfig
from mbdsdr_ai.agent import MBDSDRAgent
from mbdsdr_ai.workflow_engine import WorkflowEngine, Workflow
from mbdsdr_ai.tool_registry import ToolResult
from mbdsdr_ai.scheduler import Scheduler


@pytest.fixture(scope="module")
def agent():
    config = AgentConfig(api_key="sk-test", model="test-model")
    return MBDSDRAgent(config)


# ── D6: 工作流跨步变量回填 ──────────────────────────────────────

def test_d6_workflow_backfill_from_toolresult_data(tmp_path):
    """executor 返回 ToolResult 时，.data 字典应回填后续步骤的 {{var}} 模板。"""
    wf = WorkflowEngine(workflows_dir=str(tmp_path / "wf"))
    calls = {}

    def executor(name, params):
        calls[name] = params
        if name == "a":
            return ToolResult(success=True, content="录制中",
                               data={"recording_path": "/tmp/rec.cf32",
                                     "doppler_freq": 137100000.5,
                                     "rssi_by_azimuth": {"0": -80.0, "90": -70.0}})
        return ToolResult(success=True, content="ok")

    wf.workflows["t"] = Workflow.from_dict({
        "name": "t",
        "steps": [
            {"step_id": 1, "tool_name": "a", "params": {}, "retry_on_failure": False},
            {"step_id": 2, "tool_name": "b",
             "params": {"freq": "{{doppler_freq}}", "path": "{{recording_path}}",
                        "rssi": "{{rssi_by_azimuth}}"}, "retry_on_failure": False},
        ],
        "parameters": {},
    })
    wf.set_tool_executor(executor)
    r = wf.execute("t", {})
    assert r.success, r.error
    # dict/list 不被 str() 化，原样透传
    assert calls["b"]["freq"] == 137100000.5
    assert calls["b"]["path"] == "/tmp/rec.cf32"
    assert calls["b"]["rssi"] == {"0": -80.0, "90": -70.0}


def test_d6_workflow_backfill_mixed_string_template(tmp_path):
    """混合字符串模板仍 str() 化（整串模板之外的场景）。"""
    wf = WorkflowEngine(workflows_dir=str(tmp_path / "wf2"))
    calls = {}

    def executor(name, params):
        calls[name] = params
        if name == "a":
            return ToolResult(success=True, content="x", data={"doppler_freq": 137100000.5})
        return ToolResult(success=True, content="y")

    wf.workflows["t"] = Workflow.from_dict({
        "name": "t",
        "steps": [
            {"step_id": 1, "tool_name": "a", "params": {}, "retry_on_failure": False},
            {"step_id": 2, "tool_name": "b", "params": {"label": "freq={{doppler_freq}}"},
             "retry_on_failure": False},
        ],
        "parameters": {},
    })
    wf.set_tool_executor(executor)
    wf.execute("t", {})
    assert calls["b"]["label"] == "freq=137100000.5"


def test_d6_agent_executor_returns_toolresult(agent):
    """_workflow_tool_executor 成功时返回 ToolResult 本体（含 .data），不再是散文 str。"""
    agent.tool_registry.register(
        name="_p4_d6_probe",
        description="probe",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=lambda args: ToolResult(success=True, content="散文",
                                        data={"k": "v"}),
    )
    out = agent._workflow_tool_executor("_p4_d6_probe", {})
    assert isinstance(out, ToolResult)
    assert out.content == "散文"
    assert out.data == {"k": "v"}


# ── D7: pose 诚实标注 ─────────────────────────────────────────

def test_d7_pose_descriptions_honest(agent):
    for tname in ("pose_update_imu", "pose_update_gps"):
        tool = agent.tool_registry.tools[tname]
        desc = tool["definition"]["function"]["description"]
        # 不得再宣称"数据来自"某硬件模块（假来源声明）
        assert "数据来自" not in desc
        # 必须有诚实标注；芯片名仅允许在"无硬件驱动/需接硬件"的否定语境出现
        assert "诚实标注" in desc
        assert "手动喂入" in desc


# ── D3: learning 假闭环工具摘除 ───────────────────────────────

def test_d3_learning_fake_tools_removed(agent):
    names = set(agent.tool_registry.tools.keys())
    assert "learning_learn" not in names
    assert "learning_suggestion" not in names
    # 真实经验日志/只读统计保留
    assert "learning_record" in names
    assert "learning_experiences" in names
    assert "learning_stats" in names


# ── D9: subagent 描述收窄 ────────────────────────────────────

def test_d9_subagent_description_narrowed(agent):
    desc = agent.tool_registry.tools["subagent_create"]["definition"]["function"]["description"]
    # 不再宣传 7 类全可用；明确只 3 类内置 + 通用 tool_name 路径
    assert "spectrum_analyzer" in desc
    assert "satellite_tracker" in desc
    assert "baseband_recorder" in desc
    assert "NotImplementedError" in desc


# ── D10: scheduler cron ──────────────────────────────────────

def test_d10_cron_weekday_sunday_zero():
    """cron 周字段 0=周日：'0 12 * * 0' 应命中周日 12:00（isoweekday=7）。"""
    # 2026-10-04 是周日
    import datetime
    sun = datetime.datetime(2026, 10, 4, 11, 0, 0).timestamp()
    nxt = Scheduler._next_cron_run("0 12 * * 0", sun)
    dt = datetime.datetime.fromtimestamp(nxt)
    assert dt.isoweekday() == 7
    assert dt.hour == 12 and dt.minute == 0


def test_d10_cron_weekday_monday_one():
    """cron 周字段 1=周一：'0 9 * * 1' 应命中周一 09:00。"""
    import datetime
    mon = datetime.datetime(2026, 10, 5, 8, 0, 0).timestamp()  # 周一 08:00
    nxt = Scheduler._next_cron_run("0 9 * * 1", mon)
    dt = datetime.datetime.fromtimestamp(nxt)
    assert dt.isoweekday() == 1
    assert dt.hour == 9 and dt.minute == 0


def test_d10_scheduler_add_cron_next_run(tmp_path):
    """add_task cron 类型初值走 _next_cron_run，而非 interval_seconds。"""
    s = Scheduler(tasks_file=str(tmp_path / "t.json"))
    before = time.time()
    t = s.add_task(name="c1", task_type="tool", target="x",
                   schedule_type="cron", cron_expression="30 3 * * *")
    assert t.next_run >= before
    # 下次应落在 03:30
    import datetime
    dt = datetime.datetime.fromtimestamp(t.next_run)
    assert dt.hour == 3 and dt.minute == 30


# ── sdr_tools: 单调用 + 无设备诚实失败 ────────────────────────

class _FakeBackend:
    """计数 set_frequency 调用次数的假后端。"""
    def __init__(self):
        self.status = type("S", (), {"connected": True, "recording": False})()
        self.freq = 0.0
        self.calls = 0

    def set_frequency(self, f):
        self.calls += 1
        self.freq = f
        return True


def test_sdr_set_frequency_called_once(agent):
    """无设备之外：set_frequency 副作用只触发一次（原实现 success/content 各调一次）。"""
    fb = _FakeBackend()
    agent.sdr_manager.active_backend = fb
    r = agent.tool_registry.call("sdr_set_frequency", {"frequency_hz": 101800000})
    assert r.success
    assert fb.calls == 1, f"set_frequency 被调 {fb.calls} 次"
    assert fb.freq == 101800000


def test_sdr_set_frequency_no_device_honest(agent):
    """无连接设备时返回诚实失败 ToolResult，不抛 AttributeError。"""
    agent.sdr_manager.active_backend = None
    r = agent.tool_registry.call("sdr_set_frequency", {"frequency_hz": 100000000})
    assert not r.success
    assert "设备未连接" in r.content or "无连接设备" in r.content


def test_sdr_set_demod_no_device_honest(agent):
    agent.sdr_manager.active_backend = None
    r = agent.tool_registry.call("sdr_set_demod", {"mode": "NFM"})
    assert not r.success
    assert "设备" in r.content


def test_astro_pointing_guidance_no_crash(agent):
    """A2 B2 残留：AntennaParams(beamwidth_deg=) 曾导致 TypeError；用户给波束宽度时应正常。"""
    r = agent.tool_registry.call("astro_pointing_guidance", {
        "target_alt": 30, "target_az": 120, "current_alt": 28, "current_az": 118,
        "beamwidth_deg": 10})
    assert r.success
    assert "in_beam" in r.content


def test_ntrip_connect_no_nameerror():
    """rtklib_adapter 曾把 connect()/close() 写成 onnect()/lose() 导致 NameError。"""
    from mbdsdr_ai.rtklib_adapter import ntrip_connect
    r = ntrip_connect("127.0.0.1", 2101, "x")
    assert "handshake_ok" in r
