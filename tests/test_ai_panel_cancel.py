# SPDX-License-Identifier: MIT
"""#88：AI 面板「取消」按钮名实相符（offscreen，不联网、不起真模型）。

缺陷原状（修复前，三条都可反证）：
  1. ``_AI_NEXT_STEPS["busy"]`` 让用户「先点『取消』中止当前请求」，可整个
     desktop/ai_panel.py 从来没有取消控件——空态文案指向不存在的入口；
  2. ``MBDSDRAgent.request_cancel()`` 没有任何生产调用方（只有测试在调），
     用户没有任何办法停掉一轮正在烧 token / 动硬件的请求；
  3. ``_on_ai_finished`` 从不读 ``result["cancelled"]``：用户主动中止被当成
     普通错误渲染（error_code="cancelled" 落到 unknown 类，文案让用户去查
     HTTP 状态码），因取消未执行的工具还被算进「N 个操作未成功，请处理后
     重试」——用户自己按的取消，却被要求去修一个不存在的错误。

本文件钉住修复后的可观察事实：控件真存在、只在真能取消时出现、点击真送到
agent（#87 起一并传到子代理）、取消结果如实呈现为「部分结果」而不是故障。

跑法：
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ai_panel_cancel.py -q
"""
import os
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402
from shiboken6 import isValid  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)  # noqa: F401

import ai_steps  # noqa: E402
from desktop import ai_panel as ap  # noqa: E402
from desktop.ai_panel import AIPanel  # noqa: E402
from mbdsdr_ai.tool_results import CANCEL_ERROR  # noqa: E402


# ── 替身：只替模型/桥接面，取消语义与真 agent 一致（返回是否命中）──

class _GatedAgent:
    """chat 卡在闸门上，让主线程有机会在「在途」状态下点取消。

    request_cancel 复刻真 agent 的返回值语义：有在跑的请求才 True。
    """

    def __init__(self, reply=None, raise_exc=None, cancel_hit=True):
        self.entered = threading.Event()
        self.gate = threading.Event()
        self.cancel_calls = 0
        self.cancel_hit = cancel_hit
        self.chat_calls = 0
        self._reply = reply if reply is not None else {
            "content": "部分结论", "tool_calls": [], "tool_results": [],
            "latency_ms": 12.0, "compacted": False, "error": None,
            "error_code": None, "cancelled": False,
        }
        self._raise = raise_exc
        self.config = type("C", (), {"api_key": "stub-key"})()
        self.model_manager = type("M", (), {"model": "stub/model-x"})()

    def chat(self, text, on_delta=None):
        self.chat_calls += 1
        self.entered.set()
        # 闸门由主线程开：断言不绑墙钟，只绑「事件真的发生了」
        self.gate.wait(10.0)
        if self._raise is not None:
            raise self._raise
        return dict(self._reply)

    def request_cancel(self):
        self.cancel_calls += 1
        return self.cancel_hit


class _NoCancelAgent:
    """老内核替身：没有 request_cancel（面板必须诚实说不能停）。"""

    def __init__(self):
        self.config = type("C", (), {"api_key": "stub-key"})()

    def chat(self, text, on_delta=None):
        return {"content": "x", "tool_calls": [], "tool_results": [],
                "error": None}


class _FakeBridge:
    """本地能力协商替身：handle 没有取消缝（与真 LocalAgentBridge 一致）。"""

    def __init__(self):
        self.calls = 0
        self.agent = None

    def refresh(self):
        pass

    def handle(self, text, confirm=False, only_tool=None, only_args=None):
        self.calls += 1
        return {"mode": "local", "status": "ok", "cards": [], "culture": [],
                "tasks": [], "hooks": [], "suggestions": [],
                "reply": "本地协商完成：%s" % text}

    def record_external_call(self, name, args, res):
        pass


#: 在途替身登记：用例中途断言失败时，fixture 也要把闸门开掉并等线程收尾。
#: 否则 QThread 还在跑就被销毁（"QThread: Destroyed while thread is still
#: running" → abort），把 pytest 的结论整段吞掉，反证探针拿不到证据。
_LIVE_AGENTS = []


@pytest.fixture
def panel():
    p = AIPanel()
    yield p
    for ag in list(_LIVE_AGENTS):
        ag.gate.set()
    _LIVE_AGENTS.clear()
    if isValid(p):
        _pump_until(lambda: not p._is_processing and not p._local_busy)
        for attr in ("_worker_thread", "_local_thread"):
            th = getattr(p, attr, None)
            # 正常收尾时面板自己会 deleteLater 掉线程，Python 引用还在但
            # C++ 对象已销毁；直接 wait 会抛 "already deleted"（teardown 假红）。
            if th is not None and isValid(th) and th.isRunning():
                th.wait(3000)
        p.close()
        p.deleteLater()


def _pump_until(cond, limit=600):
    """排 Qt 事件直到条件成立（条件驱动，不是定时断言）。

    上限给得很宽：真挂住时用例失败并留下现场，而不是靠紧阈值碰运气。
    """
    for _ in range(limit):
        app.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    return False


def _view(panel):
    return panel.conversation_view.toPlainText()


def _start_model_request(panel, agent):
    """走真入口 _call_ai（真 QThread + 真 AIWorker），返回是否派发成功。"""
    panel.agent = agent
    _LIVE_AGENTS.append(agent)
    ok = panel._call_ai("扫频找台")
    assert ok is True, "模型链路派发失败，后续断言无意义"
    # _call_ai 在起线程**之前**就置好忙态与取消可见性：同步可断言
    assert panel._is_processing is True
    assert agent.entered.wait(5.0), "AIWorker 没有真的跑起来"
    return ok


def _finish(panel, agent, reply=None):
    if reply is not None:
        agent._reply = reply
    agent.gate.set()
    assert _pump_until(lambda: not panel._is_processing), \
        "本轮没有收尾：忙锁没释放（取消/完成路径断了）"


# ---------------------------------------------------------------------------
# 1. 控件存在性 + 文案名实相符（缺陷 1 的钉子）
# ---------------------------------------------------------------------------
class TestCancelControlExists:
    def test_cancel_button_exists_and_hidden_when_idle(self, panel):
        assert isinstance(panel.cancel_btn, QPushButton)
        assert panel.cancel_btn.isHidden(), "空闲态不该常驻一个取消键"
        assert panel.send_btn.isEnabled() and panel.send_btn.text() == "发送"

    def test_busy_guidance_points_at_a_real_control(self, panel):
        """busy 文案提到的「取消」必须是面板里真有的控件（#88 缺陷原状）。"""
        text = ap._AI_NEXT_STEPS["busy"]
        assert "取消" in text
        assert hasattr(panel, "cancel_btn"), \
            "文案让用户点「取消」，面板却没有这个控件"
        assert "安全点" in text, "必须说清取消不是强杀"

    def test_cancel_tooltip_is_honest_about_safe_points(self, panel):
        tip = panel.cancel_btn.toolTip()
        assert "安全点" in tip and "强杀" in tip
        assert "子代理" in tip, "#87 起取消会传到子代理，tooltip 要说全"
        assert "部分结果" in tip


# ---------------------------------------------------------------------------
# 2. 显隐只跟「真能取消」走（不挂按了没用的按钮）
# ---------------------------------------------------------------------------
class TestCancelVisibility:
    def test_model_request_shows_cancel_and_click_reaches_agent(self, panel):
        agent = _GatedAgent()
        _start_model_request(panel, agent)
        assert not panel.cancel_btn.isHidden(), "在途却不给取消键"
        assert not panel.send_btn.isEnabled()

        panel.cancel_btn.click()
        assert agent.cancel_calls == 1, "点了取消却没送到 agent（死按钮）"
        assert not panel.cancel_btn.isEnabled(), "连点会刷屏，按下即置灰"
        assert "已请求中止" in _view(panel)

        _finish(panel, agent)
        assert panel.cancel_btn.isHidden(), "收尾后取消键必须收起"
        assert panel.send_btn.isEnabled() and panel.send_btn.text() == "发送"

    def test_local_negotiation_never_shows_cancel(self, panel, monkeypatch):
        """本地协商没有取消缝：亮一个按了没用的停止键比不亮更糟。"""
        bridge = _FakeBridge()
        monkeypatch.setattr(panel, "_bridge_obj", lambda: bridge)
        panel.agent = _GatedAgent()
        assert panel._local_dispatch("扫频找台") is True
        assert panel._local_busy is True
        assert panel.cancel_btn.isHidden(), "本地协商不该出现取消键"
        assert _pump_until(lambda: not panel._local_busy)
        assert panel.cancel_btn.isHidden()
        assert panel.send_btn.isEnabled() and panel.send_btn.text() == "发送"
        assert bridge.calls == 1

    def test_cancel_hidden_after_worker_error(self, panel):
        agent = _GatedAgent(raise_exc=RuntimeError("模型炸了"))
        _start_model_request(panel, agent)
        assert not panel.cancel_btn.isHidden()
        _finish(panel, agent)
        assert panel.cancel_btn.isHidden(), "异常收尾也要收起取消键"
        assert panel.send_btn.isEnabled() and panel.send_btn.text() == "发送"

    def test_cancel_available_signal_tracks_real_cancellability(
            self, panel, monkeypatch):
        seen = []
        panel.cancel_available.connect(lambda on: seen.append(bool(on)))
        agent = _GatedAgent()
        _start_model_request(panel, agent)
        assert seen and seen[-1] is True
        _finish(panel, agent)
        assert seen[-1] is False

        bridge = _FakeBridge()
        monkeypatch.setattr(panel, "_bridge_obj", lambda: bridge)
        panel._local_dispatch("再来一条")
        assert seen[-1] is False, "本地协商不得对外宣称可取消"
        assert _pump_until(lambda: not panel._local_busy)

    def test_idle_cancel_is_noop_and_returns_false(self, panel):
        agent = _GatedAgent()
        panel.agent = agent
        assert panel._on_cancel() is False
        assert agent.cancel_calls == 0
        assert panel.cancel_btn.isHidden()

    def test_cancel_miss_is_reported_honestly(self, panel):
        agent = _GatedAgent(cancel_hit=False)
        _start_model_request(panel, agent)
        assert panel._on_cancel() is False
        view = _view(panel)
        assert "没有命中" in view
        assert "已请求中止" not in view, "没命中却谎报已取消"
        assert panel.cancel_btn.isHidden()
        _finish(panel, agent)

    def test_kernel_without_cancel_support_is_honest(self, panel):
        panel.agent = _NoCancelAgent()
        panel._is_processing = True
        assert panel._on_cancel() is False
        assert "不支持中止" in _view(panel)
        assert panel.cancel_btn.isHidden()

    def test_cancel_delivery_failure_is_visible(self, panel):
        class _Boom(_GatedAgent):
            def request_cancel(self):
                raise RuntimeError("取消通道断了")

        agent = _Boom()
        _start_model_request(panel, agent)
        assert panel._on_cancel() is False
        assert "取消请求未能送达" in _view(panel)
        _finish(panel, agent)


# ---------------------------------------------------------------------------
# 3. 取消结果的呈现：部分结果，不是故障（缺陷 3 的钉子）
# ---------------------------------------------------------------------------
def _cancelled_reply(**kw):
    base = {
        "content": "已扫到 3 个台", "tool_calls": [], "tool_results": [],
        "latency_ms": 30.0, "compacted": False,
        "error": CANCEL_ERROR, "error_code": CANCEL_ERROR,
        "cancelled": True, "interrupted": True,
    }
    base.update(kw)
    return base


class TestCancelledPresentation:
    def test_cancelled_is_not_rendered_as_error(self, panel):
        panel._on_ai_finished(_cancelled_reply())
        view = _view(panel)
        assert "已按你的要求中止" in view and "部分结果" in view
        assert "[错误]" not in view, "用户主动取消不该被当成故障"
        assert "HTTP" not in view, "不该套用 unknown 类让人去查状态码"
        assert "本轮已取消" in view
        assert "已扫到 3 个台" in view, "中止前的部分结论要照常给用户"

    def test_cancelled_without_content_says_so(self, panel):
        got = []
        panel.reply_received.connect(lambda t: got.append(t))
        panel._on_ai_finished(_cancelled_reply(content=""))
        assert "未产出文本结论" in _view(panel)
        assert got and got[0].startswith("[已取消]"), \
            "覆盖卡不能收到空串（看起来像 AI 说了句空话）"

    def test_cancelled_tool_is_skipped_not_failed(self, panel, monkeypatch):
        monkeypatch.setattr(panel, "_bridge_obj", lambda: _FakeBridge())
        reply = _cancelled_reply(
            content="",
            tool_calls=[{"name": "sdr_spectrum_analyze", "arguments": {}}],
            tool_results=[{"success": False, "tool_name": "sdr_spectrum_analyze",
                           "content": "[已取消] 用户中途取消，该工具未执行。",
                           "error": CANCEL_ERROR, "latency_ms": 0.0}],
        )
        panel._on_ai_finished(reply)
        view = _view(panel)
        assert "个操作未成功" not in view, \
            "因取消未执行的工具不该被算成失败并要求用户去修"
        assert "1 个工具因取消未执行" in view
        steps = ai_steps.steps_from_chat(reply["tool_calls"],
                                         reply["tool_results"])
        assert steps[0].status == ai_steps.STATUS_SKIPPED

    def test_real_failure_still_reported_as_failed(self, panel, monkeypatch):
        """反向用例：真失败仍必须落 failed + 提醒，别把取消分支写成放行。"""
        monkeypatch.setattr(panel, "_bridge_obj", lambda: _FakeBridge())
        reply = _cancelled_reply(
            cancelled=False, error=None, error_code=None, content="",
            tool_calls=[{"name": "sdr_tune", "arguments": {}}],
            tool_results=[{"success": False, "tool_name": "sdr_tune",
                           "content": "错误: 设备未连接", "error": "not_connected",
                           "latency_ms": 1.0}],
        )
        panel._on_ai_finished(reply)
        assert "个操作未成功" in _view(panel)
        steps = ai_steps.steps_from_chat(reply["tool_calls"],
                                         reply["tool_results"])
        assert steps[0].status == ai_steps.STATUS_FAILED

    def test_success_is_never_flipped_by_cancel_branch(self):
        """成败判据仍是 success：取消分支只改展示类别，不改判据。"""
        steps = ai_steps.steps_from_chat(
            [{"name": "t", "arguments": {}}],
            [{"success": True, "content": "ok", "error": CANCEL_ERROR}])
        assert steps[0].status == ai_steps.STATUS_SUCCESS

    def test_streamed_partial_is_not_duplicated(self, panel):
        panel._streamed_any = True
        panel._stream_html = ""
        panel._on_stream_delta("已扫到 3 个台")
        panel._on_ai_finished(_cancelled_reply())
        assert _view(panel).count("已扫到 3 个台") == 1

    def test_cancelled_guidance_is_actionable_without_retry_nag(self):
        text = ap._failure_guidance(CANCEL_ERROR, CANCEL_ERROR)
        assert "下一步" in text
        assert "重试" not in text, "取消是用户自己的决定，不是要补救的故障"
        assert "部分结果" in text
        assert text != ap._failure_guidance("", "something odd")


# ---------------------------------------------------------------------------
# 4. 星象仪胶囊停止键：用户在哪就在哪能停（AI 面板此时不在眼前）
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def window(isolated_profile):
    import main_window as mw
    w = mw.MainWindow()
    yield w
    try:
        w._teardown_connection()
    except Exception:  # noqa: BLE001 - 收尾失败不拖垮用例
        pass
    w.close()


class TestCapsuleStopKey:
    def test_stop_key_hidden_when_idle(self, window):
        assert isinstance(window.cmd_stop_btn, QPushButton)
        assert window.cmd_stop_btn.isHidden()

    def test_stop_key_follows_cancel_available(self, window):
        window.ai_panel.cancel_available.emit(True)
        assert not window.cmd_stop_btn.isHidden()
        window.ai_panel.cancel_available.emit(False)
        assert window.cmd_stop_btn.isHidden()

    def test_stop_key_uses_the_same_cancel_path(self, window):
        agent = _GatedAgent()
        window.ai_panel.agent = agent
        window.ai_panel._is_processing = True
        window.ai_panel.cancel_available.emit(True)
        window.cmd_stop_btn.click()
        assert agent.cancel_calls == 1, "胶囊停止键没接到真取消路径"
        assert "已请求中止" in window.statusBar().currentMessage()
        window.ai_panel._is_processing = False
        window.ai_panel.cancel_available.emit(False)

    def test_stop_key_miss_is_not_reported_as_cancelled(self, window):
        agent = _GatedAgent(cancel_hit=False)
        window.ai_panel.agent = agent
        window.ai_panel._is_processing = True
        window.ai_panel.cancel_available.emit(True)
        window.cmd_stop_btn.click()
        msg = window.statusBar().currentMessage()
        assert "没有命中" in msg and "已请求中止" not in msg
        window.ai_panel._is_processing = False
        window.ai_panel.cancel_available.emit(False)

    def test_stop_key_tooltip_matches_panel_semantics(self, window):
        tip = window.cmd_stop_btn.toolTip()
        assert "安全点" in tip and "强杀" in tip and "子代理" in tip