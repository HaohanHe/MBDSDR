# SPDX-License-Identifier: MIT
"""
tests/test_sky_capability_negotiation.py
========================================
天空模式「动作 → 协商硬件 → 决策 → 执行」闭环回归锁。

背景（审计确认）：天空视图的「跟踪 / 调到该星 / 云台跟随」曾由按钮直连
主窗口驱动硬件，完全绕过 agent；系统提示词也没有「动手前先确认硬件现实」
的指令。本测试锁定改造后的四条契约：

  1. 闭环：按钮动作携带真实目标上下文进入会话，模型问 / 答 / 决策后经
     动作总线执行（用可注入的假模型 + 假执行器）；
  2. 缺硬件如实降级：缺云台时不调用云台动作，只做能做的，并在回复里
     说明缺什么；
  3. 不重复追问：同一会话内已确认的硬件不再次询问，用户改口可覆盖；
  4. 自由文本多硬件一次抽取：一句话描述多件硬件时一次写全；
  5. 没有预制问卷：上下文只陈述事实与约束，不生成问题清单 / 表单。

运行（定向，不跑全量）：
    $env:QT_QPA_PLATFORM="offscreen"; $env:MBDSDR_NULL_AUDIO="1"; `
    $env:MBDSDR_NO_HW="1"; python -m pytest `
      tests/test_sky_capability_negotiation.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_NO_HW", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from mbdsdr_ai.sky_actions import (SkyActionBus, _reset_sky_bus_for_tests,  # noqa: E402
                                   get_sky_bus, validate_sky_action)
from mbdsdr_ai.sky_negotiation import (CAPABILITY_SLOTS, CapabilityLedger,  # noqa: E402
                                       SatelliteActionContext,
                                       build_action_prompt,
                                       build_capability_system_section,
                                       jd_to_utc_iso)
from mbdsdr_ai.gimbal_follow import GimbalFollowLimiter  # noqa: E402


def _sat_context(**overrides):
    base = {
        "action": "track",
        "satellite_name": "NOAA 19",
        "norad_id": 33591,
        "azimuth_deg": 123.4,
        "elevation_deg": 31.5,
        "nominal_freq_hz": 137.1e6,
        "tuned_freq_hz": 137.1035e6,
        "doppler_hz": 3500.0,
        "mode": "NFM",
        "pass_rise_utc": "2026-11-04T10:00:00Z",
        "pass_set_utc": "2026-11-04T10:14:00Z",
        "tle_epoch": "2026-11-03T12:00:00Z",
        "observer": "40.0000,116.0000",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 账本：有 / 无 / 未知，可覆盖，可序列化
# ---------------------------------------------------------------------------
class TestCapabilityLedger:
    def test_unknown_by_default_no_assumption(self):
        ledger = CapabilityLedger()
        assert ledger.unknown_slots() == list(CAPABILITY_SLOTS.keys())
        for slot in CAPABILITY_SLOTS:
            assert ledger.state(slot) == "unknown"
            assert not ledger.known(slot)

    def test_update_and_user_revision_overrides(self):
        ledger = CapabilityLedger()
        assert ledger.update("gimbal", "missing", "用户：我没有云台")
        assert ledger.state("gimbal") == "missing"
        # 用户改口：以后又说买了云台 → 覆盖旧结论
        assert ledger.update("gimbal", "available", "用户：现在接上了云台")
        assert ledger.state("gimbal") == "available"
        assert "现在接上了云台" in ledger.evidence["gimbal"]

    def test_batch_update_extracts_multiple_hardware_in_one_sentence(self):
        ledger = CapabilityLedger()
        sentence = "我有 RTL-SDR，没有云台，也没有 GNSS 模块"
        ok, rejected = ledger.update_many([
            {"slot": "sdr", "state": "available", "evidence": sentence},
            {"slot": "gimbal", "state": "missing", "evidence": sentence},
            {"slot": "gnss", "state": "missing", "evidence": sentence},
        ])
        assert ok == 3 and rejected == []
        assert ledger.state("sdr") == "available"
        assert ledger.state("gimbal") == "missing"
        assert ledger.state("gnss") == "missing"
        # 一句话的证据原样保留，便于会话里回溯
        assert ledger.evidence["sdr"] == sentence

    def test_invalid_slot_or_state_rejected(self):
        ledger = CapabilityLedger()
        assert not ledger.update("toaster", "available")
        assert not ledger.update("sdr", "maybe")
        assert ledger.state("sdr") == "unknown"

    def test_serialization_roundtrip(self):
        ledger = CapabilityLedger()
        ledger.update("sdr", "available", "探测到 RTL-SDR")
        ledger.update("gimbal", "missing", "用户说没有")
        ledger.record_decision("track", "NOAA 19", "仅软件跟踪，云台不可用")
        restored = CapabilityLedger.from_dict(ledger.to_dict())
        assert restored.state("sdr") == "available"
        assert restored.state("gimbal") == "missing"
        assert restored.evidence["sdr"] == "探测到 RTL-SDR"
        assert "仅软件跟踪" in restored.decision_for("track", "NOAA 19")


# ---------------------------------------------------------------------------
# 上下文文案：只陈述事实，不生成问卷
# ---------------------------------------------------------------------------
class TestActionPrompt:
    def test_carries_real_target_context(self):
        prompt = build_action_prompt(_sat_context(), CapabilityLedger())
        for needle in ("NOAA 19", "33591", "123.4", "31.5", "137.1000 MHz",
                       "3500 Hz", "2026-11-04T10:00:00Z", "NFM"):
            assert needle in prompt

    def test_missing_target_fields_are_honest_not_fabricated(self):
        ctx = {"action": "track", "satellite_name": "NOAA 19"}
        prompt = build_action_prompt(ctx, CapabilityLedger())
        assert "未获取" in prompt
        assert "0.0 MHz" not in prompt  # 不把缺项写成 0

    def test_known_hardware_is_not_asked_again(self):
        ledger = CapabilityLedger()
        ledger.update("sdr", "available", "用户：有 RTL-SDR")
        ledger.update("gimbal", "missing", "用户：没有云台")
        prompt = build_action_prompt(_sat_context(), ledger)
        assert "- SDR 接收设备: 有；依据：用户：有 RTL-SDR" in prompt
        assert "- 云台/旋转器: 无；依据：用户：没有云台" in prompt
        unknown_part = prompt.split("【仍未确认的硬件】", 1)[1]
        unknown_part = unknown_part.split("【执行要求】", 1)[0]
        assert "SDR 接收设备" not in unknown_part
        assert "云台/旋转器" not in unknown_part

    def test_when_all_known_no_remaining_question(self):
        ledger = CapabilityLedger()
        for slot in CAPABILITY_SLOTS:
            ledger.update(slot, "available", "tool probe")
        prompt = build_action_prompt(_sat_context(), ledger)
        assert "无（本会话涉及的硬件状态均已确认）" in prompt

    def test_prompt_is_not_a_questionnaire(self):
        """机械锁：上下文本身不提问、不含固定问题清单/编号表单。"""
        prompt = build_action_prompt(_sat_context(), CapabilityLedger())
        assert "不是问卷" in prompt
        assert "？" not in prompt
        assert "问题 1" not in prompt and "Question 1" not in prompt
        # 问什么由模型判断：文案只给约束，不给逐项问题
        assert "不要逐项念清单" in prompt

    def test_system_section_states_three_reality_levels(self):
        ledger = CapabilityLedger()
        ledger.update("gimbal", "missing", "用户：没有云台")
        text = build_capability_system_section(ledger)
        assert "有 / 无 / 未知" in text
        assert "不得假设型号" in text
        assert "- 云台/旋转器: 无" in text

    def test_jd_to_utc_iso_honest_on_garbage(self):
        assert jd_to_utc_iso(2461000.0)
        assert jd_to_utc_iso(None) == ""
        assert jd_to_utc_iso("not-a-jd") == ""


# ---------------------------------------------------------------------------
# 动作总线：白名单、排队、真实执行/诚实失败
# ---------------------------------------------------------------------------
class TestSkyActionBus:
    def setup_method(self):
        _reset_sky_bus_for_tests()

    def teardown_method(self):
        _reset_sky_bus_for_tests()

    def test_validation_whitelist(self):
        assert validate_sky_action({"action": "laser", "satellite": "X"})
        assert validate_sky_action({"action": "track", "satellite": ""})
        assert validate_sky_action({"action": "track", "satellite": "X",
                                    "on": "yes"})
        assert validate_sky_action({"action": "tune", "satellite": "X",
                                    "frequency_hz": -1}) is not None
        assert validate_sky_action({"action": "tune", "satellite": "X",
                                    "frequency_hz": 137.1e6}) is None

    def test_enqueue_without_executor_is_honest(self):
        bus = SkyActionBus()
        res = bus.enqueue({"action": "track", "satellite": "NOAA 19"})
        assert res["ok"] is False and res["status"] == "UNAVAILABLE"

    def test_drain_executes_and_reports(self):
        seen = []

        def executor(action):
            seen.append(action)
            return {"ok": True, "status": "DONE"}

        bus = SkyActionBus(executor=executor)
        queued = bus.enqueue({"action": "tune", "satellite": "NOAA 19"})
        assert queued["status"] == "QUEUED"
        results = bus.drain()
        assert seen == [{"action": "tune", "satellite": "NOAA 19"}]
        assert results[0]["status"] == "DONE"

    def test_executor_exception_does_not_lie(self):
        def executor(_action):
            raise RuntimeError("backend exploded")

        bus = SkyActionBus(executor=executor)
        bus.enqueue({"action": "track", "satellite": "NOAA 19"})
        res = bus.drain()[0]
        assert res["ok"] is False
        assert "backend exploded" in res["reason"]


# ---------------------------------------------------------------------------
# 云台跟随整形：死区 / 最小间隔 / 速率上限 / 复位
# ---------------------------------------------------------------------------
class TestGimbalLimiter:
    def test_first_command_is_full_target(self):
        lim = GimbalFollowLimiter()
        assert lim.plan(123.0, 30.0, now=100.0) == (123.0, 30.0)

    def test_second_command_too_soon_suppressed(self):
        lim = GimbalFollowLimiter(min_interval_s=1.0)
        lim.plan(10.0, 10.0, now=100.0)
        assert lim.plan(40.0, 40.0, now=100.5) is None

    def test_deadband_suppresses_small_moves(self):
        lim = GimbalFollowLimiter(min_interval_s=0.5, deadband_deg=2.0)
        lim.plan(10.0, 10.0, now=100.0)
        assert lim.plan(10.8, 10.5, now=101.0) is None

    def test_rate_limit_clamps_large_jump(self):
        lim = GimbalFollowLimiter(min_interval_s=0.5, deadband_deg=0.5,
                                  max_rate_deg_s=10.0)
        lim.plan(0.0, 0.0, now=100.0)
        cmd = lim.plan(180.0, 90.0, now=101.0)
        assert cmd is not None
        # dt=1s，最大步进 10°（方位差 180° 被拆成多拍）
        assert cmd[0] == pytest.approx(10.0)
        assert cmd[1] == pytest.approx(10.0)

    def test_angle_wrap_uses_short_path(self):
        lim = GimbalFollowLimiter(min_interval_s=0.5, deadband_deg=0.5,
                                  max_rate_deg_s=5.0)
        lim.plan(359.0, 10.0, now=100.0)
        cmd = lim.plan(1.0, 10.0, now=101.0)
        assert cmd is not None
        # 359° → 1° 只差 2°，不应被当成 -358°；限速 5° 内直接到位
        assert cmd[0] == pytest.approx(361.0) or cmd[0] == pytest.approx(1.0)

    def test_reset_reanchors(self):
        lim = GimbalFollowLimiter(min_interval_s=1.0)
        lim.plan(10.0, 10.0, now=100.0)
        lim.reset()
        assert lim.plan(50.0, 50.0, now=100.1) == (50.0, 50.0)


# ---------------------------------------------------------------------------
# 工具面：账本写入 / 动作入队（桌面执行器由主窗口挂载）
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def real_agent():
    from mbdsdr_ai import AgentConfig, MBDSDRAgent
    _reset_sky_bus_for_tests()
    agent = MBDSDRAgent(AgentConfig(api_key="", base_url="", model=""))
    yield agent
    _reset_sky_bus_for_tests()


class TestSkyTools:
    def test_capability_report_batch_and_state(self, real_agent):
        real_agent.sky_ledger = CapabilityLedger()
        real_agent.context_manager.capability_ledger = real_agent.sky_ledger
        r = real_agent.tool_registry.call("sky_capability_report", {
            "items": [
                {"slot": "sdr", "state": "available",
                 "evidence": "我有 RTL-SDR，没有云台"},
                {"slot": "gimbal", "state": "missing",
                 "evidence": "我有 RTL-SDR，没有云台"},
                {"slot": "gnss", "state": "missing",
                 "evidence": "我有 RTL-SDR，没有云台"},
            ],
            "decisions": [{"action": "track", "satellite": "NOAA 19",
                           "decision": "仅软件跟踪"}],
        })
        assert r.success is True
        assert r.data["slots"]["sdr"]["state"] == "available"
        assert r.data["slots"]["gimbal"]["state"] == "missing"
        assert r.data["decisions_recorded"] == 1
        state = real_agent.tool_registry.call("sky_capability_state", {})
        assert state.success is True
        assert state.data["slots"]["gnss"]["state"] == "missing"

    def test_report_rejects_bad_state_without_writing(self, real_agent):
        real_agent.sky_ledger = CapabilityLedger()
        r = real_agent.tool_registry.call("sky_capability_report", {
            "items": [{"slot": "gimbal", "state": "probably"}],
        })
        assert r.success is False
        assert real_agent.sky_ledger.state("gimbal") == "unknown"
        assert "非法状态" in r.error

    def test_track_tool_enqueues_and_executor_runs(self, real_agent):
        seen = []
        bus = get_sky_bus()
        bus.executor = lambda a: (seen.append(a) or
                                 {"ok": True, "status": "DONE"})
        r = real_agent.tool_registry.call("sky_track_satellite", {
            "satellite_name": "NOAA 19", "on": True,
            "downlink_hz": 137.1e6})
        assert r.success is True and r.data["status"] == "QUEUED"
        results = bus.drain()
        assert seen == [{"action": "track", "satellite": "NOAA 19",
                         "on": True, "frequency_hz": 137.1e6}]
        assert results[0]["status"] == "DONE"

    def test_gimbal_tool_requires_target(self, real_agent):
        r = real_agent.tool_registry.call("sky_gimbal_follow",
                                          {"satellite_name": ""})
        assert r.success is False
        assert "satellite" in r.error


# ---------------------------------------------------------------------------
# 闭环：按钮上下文 → 模型（假）→ 问/答 → 决策 → 工具 → 总线执行
# ---------------------------------------------------------------------------
class _FakeModel:
    """可注入的假模型：把「模型判断」写成可断言的确定性逻辑。

    它只消费生产代码产出的 prompt/ledger/工具面，不替生产代码做判断。
    """

    def __init__(self, agent):
        self.agent = agent
        self.asked = 0
        self.unknown_at_request = []

    def chat(self, text):
        ledger = self.agent.sky_ledger
        unknown = ledger.unknown_slots()
        self.unknown_at_request.append(list(unknown))
        if "天空动作请求" in text:
            # 动作相关且尚未确认的项才问；已确认项不重复追问（模型判断）
            if ledger.state("sdr") == "unknown":
                self.asked += 1
                return {"content": "先确认一下：手头有哪些可用硬件（SDR/云台/GNSS）？",
                        "tool_calls": []}
            # 已有结论 → 直接决策执行，不再重复问
            self.agent.tool_registry.call(
                "sky_track_satellite",
                {"satellite_name": "NOAA 19", "on": True,
                 "downlink_hz": 137.1e6})
            return {"content": "硬件结论已在本会话确认，直接执行；无需重复询问。",
                    "tool_calls": [{"name": "sky_track_satellite"}]}
        # 用户自由文本回答：一次抽取多件硬件，缺什么说什么，只执行能做的
        if "我有" in text or "没有" in text:
            items = [
                {"slot": "sdr", "state": "available", "evidence": text},
                {"slot": "gimbal", "state": "missing", "evidence": text},
                {"slot": "gnss", "state": "missing", "evidence": text},
            ]
            self.agent.tool_registry.call(
                "sky_capability_report", {"items": items})
            self.agent.tool_registry.call(
                "sky_capability_report", {"items": [],
                                          "decisions": [{
                                              "action": "track",
                                              "satellite": "NOAA 19",
                                              "decision": "仅软件跟踪，云台缺失"}]})
            self.agent.tool_registry.call(
                "sky_track_satellite",
                {"satellite_name": "NOAA 19", "on": True,
                 "downlink_hz": 137.1e6})
            return {"content": "已开始软件跟踪；没有云台，无法机械指向。",
                    "tool_calls": [{"name": "sky_track_satellite"}]}
        return {"content": "（待命）", "tool_calls": []}


class TestNegotiationLoop:
    def setup_method(self):
        from ai_panel import AIPanel
        self.panel = AIPanel()
        self.panel.agent = None

    def teardown_method(self):
        self.panel.close()

    @pytest.fixture
    def agent(self, real_agent):
        real_agent.sky_ledger = CapabilityLedger()
        real_agent.context_manager.capability_ledger = real_agent.sky_ledger
        return real_agent

    def _wire(self, fake):
        sent, notices = [], []
        self.panel.agent = fake
        self.panel._call_ai = lambda text: sent.append(text)  # 注入 Worker
        self.panel._add_system_message = lambda text: notices.append(text)
        return sent, notices

    def test_without_ai_is_refused_not_direct_hardware(self):
        executed = []
        notices = []
        get_sky_bus().executor = lambda a: executed.append(a)
        self.panel._add_system_message = lambda t: notices.append(t)
        assert self.panel.request_sky_action(_sat_context()) is False
        assert executed == []
        assert any("AI 未配置" in text for text in notices)

    def test_action_context_goes_to_agent_once(self, agent):
        fake = _FakeModel(agent)
        sent, notices = self._wire(fake)
        assert self.panel.request_sky_action(_sat_context()) is True
        assert len(sent) == 1
        assert "NOAA 19" in sent[0] and "33591" in sent[0]
        assert "不是问卷" in sent[0] and "？" not in sent[0]
        # 只发一条上下文提醒，不生成多轮固定问题
        assert len(notices) == 1 and "天空动作" in notices[0]
        assert fake.chat(sent[0])["content"].find("先确认") >= 0
        assert fake.asked == 1

    def test_question_answer_decide_execute_closed_loop(self, agent):
        fake = _FakeModel(agent)
        sent, _ = self._wire(fake)
        self.panel.request_sky_action(_sat_context())
        first = fake.chat(sent[0])
        assert first["tool_calls"] == []          # 还没确认硬件 → 不执行
        assert fake.asked == 1

        answer = "我有 RTL-SDR，没有云台，也没有 GNSS 模块"
        reply = fake.chat(answer)                  # 用户自由文本回答
        assert "没有云台" in reply["content"]
        assert fake.agent.sky_ledger.state("sdr") == "available"
        assert fake.agent.sky_ledger.state("gimbal") == "missing"
        assert fake.agent.sky_ledger.state("gnss") == "missing"
        assert fake.agent.sky_ledger.evidence["sdr"] == answer

        # 模型决定执行 track → 工具入队 → 总线执行
        bus = get_sky_bus()
        executed = []
        bus.executor = lambda a: (executed.append(a) or
                                 {"ok": True, "status": "DONE"})
        bus.drain()
        self.panel._resolve_pending_sky(reply["tool_calls"])
        assert executed and executed[0]["action"] == "track"

    def test_missing_hardware_degrades_honestly(self, agent):
        fake = _FakeModel(agent)
        self._wire(fake)
        bus = get_sky_bus()
        executed = []
        bus.executor = lambda a: (executed.append(a) or
                                 {"ok": True, "status": "DONE"})
        reply = fake.chat("我有 RTL-SDR，没有云台，也没有 GNSS 模块")
        bus.drain()
        assert agent.sky_ledger.state("gimbal") == "missing"
        # 缺云台 → 绝不提交云台动作；只提交能做的软件跟踪
        assert [a["action"] for a in executed] == ["track"]
        assert "没有云台" in reply["content"]
        assert "无法机械指向" in reply["content"]

    def test_no_repeat_question_within_session(self, agent):
        fake = _FakeModel(agent)
        sent, _ = self._wire(fake)
        self.panel.request_sky_action(_sat_context())
        fake.chat(sent[0])                       # 第一次问
        assert fake.asked == 1
        fake.chat("我有 RTL-SDR，没有云台，也没有 GNSS")  # 用户回答并记录
        # 第二次动作：账本里已有结论 → 不再追问（模型据上下文直接决策）
        self.panel.request_sky_action(_sat_context(action="tune"))
        second = fake.chat(sent[-1])
        assert fake.asked == 1                   # 没有新增追问
        assert "先确认" not in second["content"]
        unknown = fake.unknown_at_request[-1]
        assert "sdr" not in unknown and "gimbal" not in unknown

    def test_user_revision_updates_session_ledger(self, agent):
        fake = _FakeModel(agent)
        fake.chat("我有 RTL-SDR，没有云台")
        assert agent.sky_ledger.state("gimbal") == "missing"
        r = agent.tool_registry.call("sky_capability_report", {
            "items": [{"slot": "gimbal", "state": "available",
                       "evidence": "用户改口：现在接上了云台"}],
        })
        assert r.success is True
        assert agent.sky_ledger.state("gimbal") == "available"
        assert "改口" in agent.sky_ledger.evidence["gimbal"]


# ---------------------------------------------------------------------------
# 主窗口缝：按钮 → 协商（不再直连硬件）；停止/循环执行仍走既有执行器
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def mw():
    from main_window import MainWindow
    w = MainWindow()
    w.show()
    from PySide6.QtCore import QTimer
    QTimer.singleShot(120, app.quit)
    app.exec()
    yield w
    try:
        w._disconnect()
    except Exception:
        pass
    w.close()


class TestMainWindowSeam:
    def test_start_routes_to_agent_not_hardware(self, mw, monkeypatch):
        started, captured = [], []
        monkeypatch.setattr(mw, "_start_satellite_tracking",
                            lambda *a: started.append(a))
        monkeypatch.setattr(mw, "_sky_action_context",
                            lambda action: _sat_context(action=action))
        monkeypatch.setattr(mw.ai_panel, "request_sky_action",
                            lambda ctx: captured.append(ctx) or True)
        mw.sky_view.set_tracking_checked(True)
        mw._on_sky_track_requested(True, "NOAA 19", 137.1e6)
        assert started == []                     # 未直连硬件
        assert captured and captured[0]["satellite_name"] == "NOAA 19"
        assert captured[0]["action"] == "track"
        mw.sky_view.set_tracking_checked(False)
        monkeypatch.undo()

    def test_refused_negotiation_resets_button(self, mw, monkeypatch):
        started = []
        monkeypatch.setattr(mw, "_start_satellite_tracking",
                            lambda *a: started.append(a))
        monkeypatch.setattr(mw, "_sky_action_context",
                            lambda action: _sat_context(action=action))
        monkeypatch.setattr(mw.ai_panel, "request_sky_action",
                            lambda ctx: False)
        mw.sky_view.set_tracking_checked(True)
        mw._on_sky_track_requested(True, "NOAA 19", 137.1e6)
        assert not mw.sky_view._track_btn.isChecked()   # 不残留假勾选
        assert started == []
        monkeypatch.undo()

    def test_stop_is_direct_no_negotiation(self, mw, monkeypatch):
        stopped, captured = [], []
        monkeypatch.setattr(mw, "_stop_satellite_tracking",
                            lambda: stopped.append(1))
        monkeypatch.setattr(mw.ai_panel, "request_sky_action",
                            lambda ctx: captured.append(ctx) or True)
        mw._on_sky_track_requested(False, "NOAA 19", 137.1e6)
        assert stopped == [1]
        assert captured == []
        monkeypatch.undo()

    def test_executor_rejects_stale_target(self, mw):
        mw.sky_view._selected_info = {"title": "NOAA 19"}
        res = mw._execute_sky_action({"action": "track",
                                      "satellite": "NOAA 18"})
        assert res["ok"] is False
        assert res["status"] == "TARGET_MISMATCH"

    def test_executor_drives_existing_hardware_path(self, mw, monkeypatch):
        calls = []
        monkeypatch.setattr(mw, "_on_sky_track_toggled",
                            lambda on, name, hz: calls.append((on, name, hz)))
        mw.sky_view._selected_info = {"title": "NOAA 19"}
        monkeypatch.setattr(mw.sky_view, "_compute_tune",
                            lambda: {"satellite": "NOAA 19",
                                     "base_freq_hz": 137.1e6})
        res = mw._execute_sky_action({"action": "track",
                                      "satellite": "NOAA 19", "on": True})
        assert calls == [(True, "NOAA 19", 137.1e6)]
        assert res["status"] in ("DONE", "REFUSED")
        monkeypatch.undo()
