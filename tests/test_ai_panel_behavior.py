# SPDX-License-Identifier: MIT
"""desktop/ai_panel.py 行为补测（offscreen，不碰网络/模型，不编造"模型成功"）。

既有 tests/test_ai_panel_offscreen.py 是脚本式断言（模块级跑、不算 pytest 用例），
且只覆盖"未配置"一条路径；本文件用 pytest 覆盖 AI 面板其余诚实面：

1. 对话气泡富文本转义：用户原话/模型文本/系统提示里的 <>& 必须原文可见，
   不得被 Qt rich text 当标签吞掉（2026-10-29 实测缺陷，已修）；
2. 未配置 / 内核不可用时不谎报成功、不发射 tool_call_requested；
3. tool 结果为错误信封时步骤卡如实 FAIL，skipped 不进卡，UI 不二次转发；
4. _on_ai_finished/_on_ai_error 的失败呈现与 UI 状态复位；
5. MCP 工具注册（含空列表 = 未连接）的诚实空态；
6. 流式打字与 finished 的去重（不重复追加同一段内容）。

跑法：QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ai_panel_behavior.py
"""
import json
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)  # noqa: F401

import ai_steps  # noqa: E402
from desktop import ai_panel as ap  # noqa: E402
from desktop.ai_panel import AIPanel  # noqa: E402


# ── 测试替身：只替 model/调用面，不断言"模型成功"，所有返回都是显式信封 ──

class _StubModelManager:
    def __init__(self, model="stub/model-x"):
        self.model = model


class _StubContext:
    def __init__(self):
        self.current_tokens = 0
        self.max_tokens = 8192
        self.message_count = 0
        self.compression_count = 0
        self.cleared = False

    def clear_history(self):
        self.cleared = True


class _StubAgent:
    """MBDSDRAgent 的最小替身：chat/run_command 返回显式构造的信封。"""

    def __init__(self, reply=None, raise_exc=None, deltas=None,
                 command_reply="/help: 无"):
        self._reply = reply or {"content": "", "tool_calls": [],
                                "tool_results": [], "latency_ms": 0,
                                "compacted": False, "error": None}
        self._raise = raise_exc
        self._deltas = deltas or []
        self.command_reply = command_reply
        self.model_manager = _StubModelManager()
        self.context_manager = _StubContext()
        self.config = type("C", (), {"api_key": "stub-key",
                                     "base_url": "http://stub",
                                     "model": "stub/model-x"})()
        self.registered_mcp = None
        self.chat_inputs = []

    def chat(self, user_input, on_delta=None):
        self.chat_inputs.append(user_input)
        if self._raise is not None:
            raise self._raise
        for d in self._deltas:
            if on_delta is not None:
                on_delta(d)
        return dict(self._reply)

    def run_command(self, cmd):
        return self.command_reply

    def register_mcp_tools(self, tools, handler):
        self.registered_mcp = (list(tools or []), handler)


def _chat_result(content="", success=True, latency_ms=0.0, error="", args=None):
    """ToolResult.to_dict() 形状（与 mbdsdr_ai/tool_registry 一致）。"""
    return {"success": success, "content": content, "tool_name": "",
            "args": args or {}, "latency_ms": latency_ms, "error": error,
            "truncated": False, "data": {}, "next_step": ""}


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    """把 ~/.mbdsdr/config.json 重定向到 tmp，清掉真实 key/env 干扰。"""
    monkeypatch.setattr(AIPanel, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(AIPanel, "CONFIG_FILE", str(tmp_path / "config.json"))
    for k in ("MBDSDR_API_KEY", "MBDSDR_BASE_URL", "MBDSDR_MODEL",
              "MBDSDR_API_BASE"):
        monkeypatch.delenv(k, raising=False)
    yield


@pytest.fixture
def panel():
    p = AIPanel()
    yield p
    p.close()
    p.deleteLater()


def _card_labels(panel):
    """步骤卡里所有 QLabel 的文本（= 渲染用的 rich text 源码）。"""
    out = []
    for i in range(panel.step_list_layout.count()):
        w = panel.step_list_layout.itemAt(i).widget()
        if w is not None:
            out.extend(lbl.text() for lbl in w.findChildren(QLabel))
    return out


def _view_text(panel):
    return panel.conversation_view.toPlainText()


def _spin_until(predicate, timeout_s):
    """跑 Qt 事件循环直到条件成立（worker→UI 是 queued 信号，不能阻塞 wait）。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


class TestRichTextEscaping:
    """对话气泡不得把内容喂给 Qt rich text 解析器（吞字/走样 = 显示失真）。"""

    def test_user_text_angle_bracket_and_ampersand_verbatim(self, panel):
        text = 'a < b & c 呼叫 BI1AAA'
        panel._add_user_message(text)
        assert text in _view_text(panel), \
            "用户原话被 rich text 吞掉了一部分: %r" % _view_text(panel)

    def test_user_script_tag_rendered_as_text_not_markup(self, panel):
        text = "<script>alert(1)</script>"
        panel._add_user_message(text)
        assert text in _view_text(panel)
        # 原文进无害寒暄气泡，且不带出可执行标签
        assert "<script>" not in panel.conversation_view.toHtml()

    def test_user_img_onerror_attribute_not_parsed(self, panel):
        text = 'BI1AAA <img src="x" onerror="alert(1)">'
        panel._add_user_message(text)
        assert text in _view_text(panel)
        html = panel.conversation_view.toHtml()
        assert "<img" not in html, "出现了真的 img 标签: %r" % html[-200:]
        assert "&lt;img" in html

    def test_ai_json_payload_with_quotes_verbatim(self, panel):
        text = '工具回 {"ok": false, "html": "<b>&amp;</b>"}'
        panel._add_ai_message(text)
        assert text in _view_text(panel)

    def test_ai_message_keeps_line_breaks(self, panel):
        panel._add_ai_message("第一行\n第二行")
        view = _view_text(panel)
        assert "第一行" in view and "第二行" in view

    def test_system_message_verbatim(self, panel):
        text = "已注册 < 0 个工具 & 缓存 >（占位）"
        panel._add_system_message(text)
        assert text in _view_text(panel)

    def test_long_cjk_text_roundtrip(self, panel):
        text = "挪威的森林" * 60 + "<tag & more>"
        panel._add_user_message(text)
        assert text in _view_text(panel)

    def test_step_card_escapes_tool_and_summary(self, panel):
        step = ai_steps.StepRecord(
            tool="sdr_<script>_tune", status=ai_steps.STATUS_FAILED,
            summary='{"error": "<img src=x>"}', error="<b>注入</b>",
            elapsed_ms=1.0)
        panel._append_step_card(step)
        html = "".join(_card_labels(panel))
        assert "<script>" not in html and "<img" not in html and "<b>" not in html
        assert "&lt;script&gt;" in html and "&lt;img" in html


class TestUnconfiguredHonesty:
    def test_submit_without_agent_says_unconfigured_and_emits_no_tool_call(self, panel):
        tools, cmds = [], []
        panel.tool_call_requested.connect(lambda n, p: tools.append((n, p)))
        panel.command_submitted.connect(lambda t: cmds.append(t))
        panel.input_field.setText("扫频找台")
        panel._on_submit()
        view = _view_text(panel)
        assert "未配置" in view, "未配置时必须如实说明，不得假装执行: %r" % view[-200:]
        assert tools == [], "未配置时不得发射 tool_call_requested: %r" % tools
        assert cmds == ["扫频找台"]
        assert panel.input_field.text() == ""

    def test_blank_input_is_not_sent(self, panel):
        cmds = []
        panel.command_submitted.connect(lambda t: cmds.append(t))
        panel.input_field.setText("   ")
        panel._on_submit()
        assert cmds == []
        assert len(panel._conversation) == 1  # 只有欢迎语

    def test_status_label_reflects_real_core_state(self, panel):
        text = panel.ai_status_label.text()
        if ap.AI_CORE_AVAILABLE:
            assert text == panel.tr("未配置 API"), text
        else:
            assert text == panel.tr("AI 内核不可用"), text

    def test_status_label_shows_llm_model_when_agent_present(self, panel):
        panel.agent = _StubAgent()
        panel._update_ai_status()
        assert "model-x" in panel.ai_status_label.text()

    def test_slash_command_without_agent_is_honest(self, panel):
        panel.input_field.setText("/help")
        panel._on_submit()
        view = _view_text(panel)
        assert "AI 未初始化" in view
        assert "/help" in view

    def test_slash_command_with_agent_returns_real_reply(self, panel):
        agent = _StubAgent(command_reply="/help: 可用命令列表")
        panel.agent = agent
        panel.input_field.setText("/help")
        panel._on_submit()
        assert "/help: 可用命令列表" in _view_text(panel)
        assert agent.chat_inputs == []  # 斜杠命令不走 LLM 一轮 chat


class TestAgentInitAndMcpRegistration:
    def test_init_agent_without_key_leaves_agent_none(self, panel):
        panel.init_agent()
        assert panel.agent is None
        assert "未配置" in _view_text(panel)

    def test_init_agent_with_arg_key_builds_agent_and_names_source(
            self, panel, monkeypatch):
        monkeypatch.setattr(ap, "AI_CORE_AVAILABLE", True)
        sentinel = {}

        def _fake_agent(config):
            sentinel["config"] = config
            return _StubAgent()

        monkeypatch.setattr(ap, "MBDSDRAgent", _fake_agent)
        panel.init_agent(api_key="k-1", model="stub/model-x")
        assert isinstance(panel.agent, _StubAgent)
        assert sentinel["config"].model == "stub/model-x"
        assert "配置来源: 参数" in _view_text(panel)

    def test_init_agent_config_file_priority_before_env(self, panel, monkeypatch):
        monkeypatch.setattr(ap, "AI_CORE_AVAILABLE", True)
        panel._saved_config = {"api_key": "file-key", "model": "file/model"}
        monkeypatch.setenv("MBDSDR_API_KEY", "env-key")
        seen = {}

        def _fake_agent(config):
            seen["config"] = config
            return _StubAgent()

        monkeypatch.setattr(ap, "MBDSDRAgent", _fake_agent)
        panel.init_agent()
        assert seen["config"].api_key == "file-key"
        assert "配置来源: 配置文件" in _view_text(panel)

    def test_hardware_tools_cached_until_key_configured(self, panel, monkeypatch):
        monkeypatch.setattr(ap, "AI_CORE_AVAILABLE", True)
        tools = [{"name": "sdr_set_frequency"}]
        panel.register_mcp_tools(tools, lambda n, p: None)
        assert panel.agent is None
        assert "已缓存 1 个硬件工具 schema" in _view_text(panel)
        monkeypatch.setattr(ap, "MBDSDRAgent", lambda config: _StubAgent())
        panel.init_agent(api_key="k")
        assert panel.agent.registered_mcp is not None
        assert panel.agent.registered_mcp[0] == tools

    def test_empty_tool_list_without_agent_is_silent(self, panel):
        """未连接（空 schema）不得冒出"已注册 x 个工具"的假话。"""
        before = _view_text(panel)
        panel.register_mcp_tools([], lambda n, p: None)
        assert _view_text(panel) == before

    def test_empty_tool_list_with_agent_says_not_connected(self, panel):
        panel.agent = _StubAgent()
        panel.register_mcp_tools([], lambda n, p: None)
        assert "硬件未连接" in _view_text(panel)


class TestToolResultStepCards:
    def test_error_result_is_fail_card(self, panel):
        panel.on_tool_result("sdr_set_frequency", {"error": "未连接"})
        assert len(panel._tool_log) == 1
        step = panel._tool_log[0]
        assert step.status == ai_steps.STATUS_FAILED
        assert step.error == "未连接"
        assert panel.tool_log_group.isVisibleTo(panel)
        html = "".join(_card_labels(panel))
        assert "FAIL" in html and "未连接" in html

    def test_ok_false_envelope_is_not_painted_success(self, panel):
        panel.on_tool_result("radio_ptt", {"ok": False, "error": "只收电台"})
        assert panel._tool_log[0].status == ai_steps.STATUS_FAILED

    def test_skipped_result_adds_no_card(self, panel):
        panel.on_tool_result("workflow_list",
                             {"skipped": True, "reason": "软件工具不二次转发"})
        assert panel._tool_log == []
        assert not panel.tool_log_group.isVisibleTo(panel)

    def test_non_dict_result_is_rendered_not_dropped(self, panel):
        panel.on_tool_result("sdr_status", "ready")
        assert len(panel._tool_log) == 1
        assert panel._tool_log[0].tool == "sdr_status"
        assert "ready" in "".join(_card_labels(panel))

    def test_card_index_increments(self, panel):
        panel.on_tool_result("t1", {"ok": True})
        panel.on_tool_result("t2", {"ok": True})
        labels = _card_labels(panel)
        assert "01" in labels[0] and "02" in labels[1]

    def test_clear_conversation_removes_cards_and_rewelcomes(self, panel):
        panel.on_tool_result("t1", {"ok": True})
        panel.get_conversation()
        panel.clear_conversation()
        assert panel._tool_log == []
        assert panel.step_list_layout.count() == 1  # 只剩尾部 stretch
        assert "MBDSDR AI 智能体" in _view_text(panel)


class TestAiFinishedSemantics:
    def test_error_envelope_shows_error_prefix(self, panel):
        panel._on_ai_finished({"content": "", "tool_calls": [],
                               "tool_results": [], "latency_ms": 0,
                               "compacted": False, "error": "api_key_not_set"})
        view = _view_text(panel)
        assert "[错误]" in view and "api_key_not_set" in view
        assert panel._tool_log == []

    def test_failed_tool_result_card_and_empty_text_notice(self, panel):
        panel._on_ai_finished({
            "content": "", "latency_ms": 12.0, "compacted": False,
            "tool_calls": [{"name": "sdr_tune",
                            "arguments": '{"freq_mhz": 98.5}'}],
            "tool_results": [_chat_result('{"ok": false}', success=False,
                                          error="设备忙")],
        })
        step = panel._tool_log[0]
        assert step.status == ai_steps.STATUS_FAILED and step.error == "设备忙"
        assert "98.5" in step.args_preview
        assert "（AI 未返回文本内容" in _view_text(panel)

    def test_missing_result_card_is_running_not_invented(self, panel):
        panel._on_ai_finished({
            "content": "ok", "latency_ms": 0, "compacted": False,
            "tool_calls": [{"name": "sdr_status", "arguments": "{}"},
                           {"name": "get_gps", "arguments": "{}"}],
            "tool_results": [_chat_result('{"ok": true}')],
        })
        assert panel._tool_log[0].status == ai_steps.STATUS_SUCCESS
        assert panel._tool_log[1].status == ai_steps.STATUS_RUNNING
        assert "运行中" in "".join(_card_labels(panel))

    def test_compacted_is_stated(self, panel):
        panel._on_ai_finished({"content": "x", "tool_calls": [],
                               "tool_results": [], "latency_ms": 0,
                               "compacted": True})
        assert "上下文已自动压缩" in _view_text(panel)

    def test_forward_only_unexecuted_hardware_calls(self, panel):
        panel.register_mcp_tools([{"name": "sdr_set_frequency"}],
                                 lambda n, p: None)
        emitted = []
        panel.tool_call_requested.connect(lambda n, p: emitted.append((n, p)))
        panel._on_ai_finished({
            "content": "", "latency_ms": 0, "compacted": False,
            "tool_calls": [{"name": "sdr_set_frequency",
                            "arguments": '{"freq_mhz": 435.0}'},
                           {"name": "list_tools", "arguments": "{}"}],
            "tool_results": [],
        })
        # 只有"模型请求了但工具环没执行"的硬件工具才转发；软件工具不过二次转发
        assert emitted == [("sdr_set_frequency", {"freq_mhz": 435.0})]

    def test_executed_call_is_not_forwarded_again(self, panel):
        panel.register_mcp_tools([{"name": "sdr_set_frequency"}],
                                 lambda n, p: None)
        emitted = []
        panel.tool_call_requested.connect(lambda n, p: emitted.append((n, p)))
        panel._on_ai_finished({
            "content": "", "latency_ms": 0, "compacted": False,
            "tool_calls": [{"name": "sdr_set_frequency", "arguments": "{}"}],
            "tool_results": [_chat_result('{"ok": true}', latency_ms=3.0)],
        })
        assert emitted == [], "工具环已执行过的调用不得再转发一次: %r" % emitted
        assert panel._tool_log[0].elapsed_ms == 3.0

    def test_bad_arguments_json_falls_back_to_empty_args(self, panel):
        panel.register_mcp_tools([{"name": "sdr_set_frequency"}],
                                 lambda n, p: None)
        emitted = []
        panel.tool_call_requested.connect(lambda n, p: emitted.append((n, p)))
        panel._on_ai_finished({
            "content": "", "latency_ms": 0, "compacted": False,
            "tool_calls": [{"name": "sdr_set_frequency",
                            "arguments": "{not json"}],
            "tool_results": [],
        })
        assert emitted == [("sdr_set_frequency", {})]

    def test_streaming_content_is_not_duplicated(self, panel):
        panel._on_stream_delta("正在调谐…")
        panel._on_ai_finished({"content": "正在调谐…", "tool_calls": [],
                               "tool_results": [], "latency_ms": 5,
                               "compacted": False})
        assert _view_text(panel).count("正在调谐…") == 1

    def test_streaming_without_content_states_no_text(self, panel):
        panel._on_stream_delta("")
        panel._on_ai_finished({"content": "", "tool_calls": [],
                               "tool_results": [], "latency_ms": 5,
                               "compacted": False})
        assert "（AI 未返回文本内容" in _view_text(panel)

    def test_ai_error_slot_restores_ui_and_reports(self, panel):
        panel._is_processing = True
        panel.send_btn.setEnabled(False)
        panel._on_ai_error("TimeoutError: read timeout")
        view = _view_text(panel)
        assert "AI 调用出错" in view and "TimeoutError" in view
        assert panel._is_processing is False
        assert panel.send_btn.isEnabled() is True
        assert panel.send_btn.text() == panel.tr("发送")

    def test_submit_is_rejected_while_processing(self, panel):
        panel._is_processing = True
        cmds = []
        panel.command_submitted.connect(lambda t: cmds.append(t))
        panel.input_field.setText("再问一句")
        panel._on_submit()
        assert cmds == [] and panel.input_field.text() == "再问一句"


class TestBackgroundWorkerIntegration:
    def test_worker_thread_streams_and_finishes(self, panel, monkeypatch):
        """后台线程：delta 打字 -> finished -> UI 复位，且不重复追加。"""
        agent = _StubAgent(deltas=["正在", "扫频"],
                           reply={"content": "", "tool_calls": [],
                                  "tool_results": [], "latency_ms": 2.0,
                                  "compacted": False, "error": None})
        panel.agent = agent
        panel.input_field.setText("扫频找台")
        panel._on_submit()
        thread = panel._worker_thread
        assert thread is not None
        assert _spin_until(lambda: not panel._is_processing, 10.0), \
            "AI worker 线程未在 10s 内把结果交回 UI 线程"
        thread.wait(5000)
        view = _view_text(panel)
        assert "正在扫频" in view
        assert view.count("正在扫频") == 1
        assert agent.chat_inputs == ["扫频找台"]
        assert panel._is_processing is False
        assert panel.send_btn.isEnabled() is True

    def test_worker_exception_goes_to_error_slot(self, panel):
        agent = _StubAgent(raise_exc=RuntimeError("boom"))
        panel.agent = agent
        panel.input_field.setText("hi")
        panel._on_submit()
        thread = panel._worker_thread
        assert _spin_until(lambda: not panel._is_processing, 10.0), \
            "AI worker 线程未在 10s 内把错误交回 UI 线程"
        thread.wait(5000)
        assert "AI 调用出错" in _view_text(panel)
        assert "boom" in _view_text(panel)


class TestConversationAndConfig:
    def test_get_conversation_records_roles(self, panel):
        panel._add_user_message("你好 BI1AAA")
        panel._add_ai_message("收到")
        conv = panel.get_conversation()
        assert [c["role"] for c in conv] == ["assistant", "user", "assistant"]
        assert conv[1]["content"] == "你好 BI1AAA"
        assert conv[0]["content"].startswith("你好，我是 MBDSDR AI 智能体")

    def test_config_roundtrip_and_legacy_key_migration(self, panel):
        AIPanel._save_config({"api_key": "k", "api_base": "http://legacy"})
        raw = open(AIPanel.CONFIG_FILE, encoding="utf-8").read()
        loaded = AIPanel._load_config()
        if ap.normalize_config_keys is None:
            assert "api_base" in raw
            assert loaded["api_base"] == "http://legacy"
        else:
            assert loaded["base_url"] == "http://legacy"
            assert "api_base" not in loaded

    def test_quick_command_button_sends_first_command(self, panel):
        cmds = []
        panel.command_submitted.connect(lambda t: cmds.append(t))
        from PySide6.QtWidgets import QPushButton
        buttons = [b for b in panel.findChildren(QPushButton)
                   if b.text() in ap.QUICK_COMMANDS[:4]]
        assert len(buttons) == 4, "快捷指令只渲染前 4 条"
        buttons[0].click()
        assert cmds == [ap.QUICK_COMMANDS[0]]

    def test_toggle_log_button_switches_text_and_visibility(self, panel):
        # _toggle_log 读的是 isVisible()（含祖先可见性），故先把面板显出来
        panel.show()
        try:
            assert not panel.tool_log_group.isVisible()
            panel._toggle_log()
            assert panel.tool_log_group.isVisible()
            assert panel.toggle_log_btn.text() == "隐藏执行步骤"
            panel._toggle_log()
            assert not panel.tool_log_group.isVisible()
            assert panel.toggle_log_btn.text() == "显示执行步骤"
        finally:
            panel.hide()


class TestForwardedToolCallArguments:
    """转发给硬件的参数不许被静默吞掉（吞了就用空参数打真硬件）。"""

    def _emit(self, panel, arguments):
        panel.register_mcp_tools([{"name": "sdr_set_frequency"}],
                                 lambda n, p: None)
        emitted = []
        panel.tool_call_requested.connect(lambda n, p: emitted.append((n, p)))
        panel._on_ai_finished({
            "content": "", "latency_ms": 0, "compacted": False,
            "tool_calls": [{"name": "sdr_set_frequency",
                            "arguments": arguments}],
            "tool_results": [],
        })
        return emitted

    def test_dict_arguments_are_forwarded_intact(self, panel):
        """arguments 已是对象时（部分兼容端点/本地推理栈直接给 dict）照原样带。"""
        emitted = self._emit(panel, {"freq_mhz": 435.0, "bw_hz": 200000})
        assert emitted == [("sdr_set_frequency",
                            {"freq_mhz": 435.0, "bw_hz": 200000})]

    def test_json_string_arguments_are_parsed(self, panel):
        emitted = self._emit(panel, '{"freq_mhz": 435.0}')
        assert emitted == [("sdr_set_frequency", {"freq_mhz": 435.0})]

    def test_unparseable_arguments_fall_back_to_empty_params(self, panel):
        """真解析不了才给空参数（不猜、不伪造默认值）——弹药必须显式。"""
        assert self._emit(panel, "{not json") == [("sdr_set_frequency", {})]

    def test_list_or_number_arguments_fall_back_to_empty_params(self, panel):
        for bad in ([1, 2], 435.0, None, True):
            assert self._emit(panel, bad) == [("sdr_set_frequency", {})]

    def test_missing_arguments_key_is_empty_params(self, panel):
        emitted = []
        panel.register_mcp_tools([{"name": "sdr_set_frequency"}],
                                 lambda n, p: None)
        panel.tool_call_requested.connect(lambda n, p: emitted.append((n, p)))
        panel._on_ai_finished({
            "content": "", "latency_ms": 0, "compacted": False,
            "tool_calls": [{"name": "sdr_set_frequency"}],
            "tool_results": [],
        })
        assert emitted == [("sdr_set_frequency", {})]


class TestStepCardRendering:
    @staticmethod
    def _card(panel, idx):
        item = panel.step_list_layout.itemAt(idx)
        return item.widget() if item is not None else None

    def test_status_labels_cover_every_known_state(self, panel):
        for status, label in ((ai_steps.STATUS_SUCCESS, "OK"),
                              (ai_steps.STATUS_FAILED, "FAIL"),
                              (ai_steps.STATUS_RUNNING, "运行中"),
                              (ai_steps.STATUS_SKIPPED, "跳过")):
            panel._append_step_card(ai_steps.StepRecord(
                tool="t_" + status, status=status, summary="s", elapsed_ms=1.0))
            assert label in "".join(_card_labels(panel))

    def test_unknown_status_shows_raw_state_not_a_pass(self, panel):
        """未知状态原样显示，绝不落成 OK/FAIL 之类的假标签。"""
        panel._append_step_card(ai_steps.StepRecord(
            tool="t_weird", status="weird", summary="", elapsed_ms=0.0))
        html = "".join(_card_labels(panel))
        assert "weird" in html and "OK" not in html and "FAIL" not in html

    def test_elapsed_ms_is_printed_from_step(self, panel):
        panel._append_step_card(ai_steps.StepRecord(
            tool="t", status=ai_steps.STATUS_SUCCESS, elapsed_ms=137.6))
        assert "138ms" in "".join(_card_labels(panel))

    def test_child_step_card_is_indented(self, panel):
        panel._append_step_card(ai_steps.StepRecord(
            tool="parent", status=ai_steps.STATUS_SUCCESS))
        panel._append_step_card(ai_steps.StepRecord(
            tool="child", status=ai_steps.STATUS_SUCCESS, depth=1))
        assert self._card(panel, 0).layout().contentsMargins().left() == 6
        assert self._card(panel, 1).layout().contentsMargins().left() == 20

    def test_error_line_is_suppressed_when_already_in_summary(self, panel):
        """错误摘要已展示同一句时不重复贴一条（去重，不是删除信息）。"""
        panel._append_step_card(ai_steps.StepRecord(
            tool="t", status=ai_steps.STATUS_FAILED,
            summary="设备未连接", error="设备未连接"))
        html = "".join(_card_labels(panel))
        assert html.count("设备未连接") == 1

    def test_sub_step_from_workflow_is_rendered_as_child_card(self, panel):
        """_on_ai_finished 会把 workflow_execute 的多步结果展开成缩进子卡。"""
        payload = {"workflow_name": "wf", "success": False, "step_results": [
            {"step_id": 1, "tool": "sdr_ai_sweep", "success": True,
             "result": '{"peaks": 3}'},
            {"step_id": 2, "tool": "sdr_set_frequency", "success": False,
             "error": "未连接"},
        ]}
        panel._on_ai_finished({
            "content": "", "latency_ms": 0, "compacted": False,
            "tool_calls": [{"name": "workflow_execute", "arguments": "{}"}],
            "tool_results": [_chat_result(json.dumps(payload))],
        })
        assert [s.tool for s in panel._tool_log] == [
            "workflow_execute", "sdr_ai_sweep", "sdr_set_frequency"]
        assert panel._tool_log[1].status == ai_steps.STATUS_SUCCESS
        assert panel._tool_log[2].status == ai_steps.STATUS_FAILED
        html = "".join(_card_labels(panel))
        assert "OK" in html and "FAIL" in html and "未连接" in html
