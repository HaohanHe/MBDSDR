# SPDX-License-Identifier: MIT
"""本地推理端点（无 API Key）必须真的能用。

背景：端点体检能显示「可用」，但此前三条路径都把「没有 Key」当成未配置：

1. ``ModelManager.fetch_models`` 空 Key 直接返回内置云端列表，看不到本地模型；
2. ``ModelManager.chat`` / ``chat_stream`` 始终发 ``Authorization: Bearer ``；
3. ``MBDSDRAgent.chat`` 空 Key 立刻返回 ``api_key_not_set``；
4. AI 面板保存配置时空 Key 会把 agent 置空。

本地 Ollama / vLLM / LM Studio 默认无鉴权，所以「本地 DeepSeek 可用」这条线
必须能在不填 Key 时走通。本文件用 127.0.0.1 上的桩服务做端到端验证（不是
mock 掉 HTTP 层），并钉住「云端端点缺 Key 仍然拒绝」不被放宽。
"""

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from mbdsdr_ai.model_manager import (  # noqa: E402
    BUILTIN_MODELS, ModelManager, is_local_endpoint)

_LOCAL = "http://127.0.0.1:11434/v1"
_REMOTE = "https://api.example.invalid/v1"


# --------------------------------------------------------------------------
# 端点判定
# --------------------------------------------------------------------------
class TestIsLocalEndpoint:
    @pytest.mark.parametrize("url", [
        "http://127.0.0.1:11434/v1",
        "http://localhost:1234/v1",
        "http://[::1]:8000/v1",
        "http://192.168.1.9:8000/v1",
        "http://10.0.0.5/v1",
        "http://172.16.3.4:8080/v1",
        "http://host.docker.internal:11434/v1",
        "http://box.local:8000/v1",
        "127.0.0.1:11434/v1",          # 漏写 scheme 也要认
    ])
    def test_local_hosts(self, url):
        assert is_local_endpoint(url) is True

    @pytest.mark.parametrize("url", [
        "",
        "   ",
        "https://api.siliconflow.cn/v1",
        "https://api.openai.com/v1",
        "http://8.8.8.8/v1",
        "http://api.example.invalid/v1",
        "http://[2001:4860:4860::8888]/v1",
    ])
    def test_remote_hosts(self, url):
        assert is_local_endpoint(url) is False


# --------------------------------------------------------------------------
# 桩服务：OpenAI 兼容 /v1/models + /v1/chat/completions（含 SSE）
# --------------------------------------------------------------------------
class _StubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen: list = []

    def log_message(self, *args):  # 静音
        pass

    def _json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        type(self).seen.append({"method": "GET", "path": self.path,
                                "headers": dict(self.headers)})
        if self.path == "/v1/models":
            self._json({"object": "list",
                        "data": [{"id": "deepseek-r1:7b", "object": "model"}]})
        else:
            self.send_error(404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(n) or b"{}")
        type(self).seen.append({"method": "POST", "path": self.path,
                                "headers": dict(self.headers),
                                "payload": payload})
        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            for piece in ("你", "好"):
                chunk = {"choices": [{"delta": {"content": piece}}]}
                line = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                self.wfile.write(line.encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            self.close_connection = True
            return
        self._json({
            "choices": [{"message": {"content": "本地回答", "tool_calls": []},
                         "finish_reason": "stop"}],
            "model": payload.get("model", ""),
            "usage": {"prompt_tokens": 1, "completion_tokens": 2,
                      "total_tokens": 3},
        })


@pytest.fixture()
def stub():
    _StubHandler.seen = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    try:
        yield base
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """测试进程可能带 MBDSDR_OFFLINE / 代理 / Key；这里统一清干净。"""
    for name in ("MBDSDR_OFFLINE", "MBDSDR_API_KEY", "OPENAI_API_KEY",
                 "MBDSDR_BASE_URL", "MBDSDR_API_BASE", "OPENAI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                 "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


# --------------------------------------------------------------------------
# ModelManager：空 Key + 本机端点
# --------------------------------------------------------------------------
class TestModelManagerKeylessLocal:
    def test_fetch_models_queries_local_endpoint(self, stub):
        mgr = ModelManager(api_key="", base_url=stub)
        models = mgr.fetch_models(force_refresh=True)
        assert [m.id for m in models] == ["deepseek-r1:7b"]
        # 不能把内置云端列表当成"本地模型"
        assert [m.id for m in models] != [m.id for m in BUILTIN_MODELS]

    def test_fetch_models_sends_no_authorization_without_key(self, stub):
        ModelManager(api_key="", base_url=stub).fetch_models(force_refresh=True)
        get = [s for s in _StubHandler.seen if s["method"] == "GET"][0]
        assert "Authorization" not in get["headers"]

    def test_chat_works_without_key(self, stub):
        mgr = ModelManager(api_key="", base_url=stub, model="deepseek-r1:7b")
        r = mgr.chat([{"role": "user", "content": "你好"}])
        assert r["success"] is True
        assert r["content"] == "本地回答"
        post = [s for s in _StubHandler.seen if s["method"] == "POST"][0]
        assert "Authorization" not in post["headers"]
        assert post["payload"]["model"] == "deepseek-r1:7b"

    def test_chat_stream_works_without_key(self, stub):
        mgr = ModelManager(api_key="", base_url=stub, model="deepseek-r1:7b")
        deltas, final = [], None
        for ev in mgr.chat_stream([{"role": "user", "content": "你好"}]):
            if ev.get("done"):
                final = ev
            else:
                deltas.append(ev["delta"])
        assert "".join(deltas) == "你好"
        assert final["success"] is True
        assert final["content"] == "你好"

    def test_remote_keyless_still_refuses_without_network(self):
        mgr = ModelManager(api_key="", base_url=_REMOTE)
        called = []

        def _boom(*a, **k):
            called.append(1)
            raise AssertionError("云端端点缺 Key 不应发起请求")

        mgr._session.get = _boom
        models = mgr.fetch_models(force_refresh=True)
        assert called == []
        assert [m.id for m in models] == [m.id for m in BUILTIN_MODELS]
        assert "api_key" in (mgr._last_error or "")

    def test_offline_still_wins_for_local_endpoint(self, stub, monkeypatch):
        monkeypatch.setenv("MBDSDR_OFFLINE", "1")
        mgr = ModelManager(api_key="", base_url=stub)
        models = mgr.fetch_models(force_refresh=True)
        assert [m.id for m in models] == [m.id for m in BUILTIN_MODELS]
        assert _StubHandler.seen == []
        assert "离线" in (mgr._last_error or "")
        r = mgr.chat([{"role": "user", "content": "你好"}])
        assert r["success"] is False and r["error_code"] == "llm_offline"


# --------------------------------------------------------------------------
# Agent：空 Key + 本机端点不得被 api_key_not_set 拦掉
# --------------------------------------------------------------------------
def _bare_agent(config, model_manager):
    from mbdsdr_ai.agent import MBDSDRAgent
    from mbdsdr_ai.conversation import ConversationStore
    from mbdsdr_ai.context_manager import ContextManager
    from unittest.mock import MagicMock

    agent = object.__new__(MBDSDRAgent)
    agent.config = config
    agent.context_manager = ContextManager(
        max_context_tokens=4096, compaction_threshold=0.99,
        compaction_target_ratio=0.5, system_prompt="你是测试助手。")
    agent.model_manager = model_manager
    agent.model_manager.parse_tool_calls_from_text = MagicMock(return_value=[])
    agent.tool_registry = MagicMock()
    agent.tool_registry.get_tool_names.return_value = ["get_status"]
    agent.tool_registry.get_tool_definitions.return_value = []
    agent.memory = MagicMock()
    agent.memory.build_memory_context.return_value = ""
    agent.total_agent_calls = 0
    agent.last_error = None
    agent._repeat_key = None
    agent._repeat_count = 0
    agent._mcp_client = None
    agent.conversation_store = ConversationStore(
        tempfile.mkdtemp(prefix="mbdsdr_conv_test_"))
    return agent


class TestAgentKeylessLocal:
    def test_keyless_local_reaches_model(self):
        from unittest.mock import MagicMock
        from mbdsdr_ai.config import AgentConfig

        mm = MagicMock()
        mm.chat_stream.return_value = iter([{
            "done": True, "success": True, "content": "本地回答",
            "tool_calls": [], "usage": {}, "model": "deepseek-r1:7b",
        }])
        agent = _bare_agent(
            AgentConfig(api_key="", base_url=_LOCAL, model="deepseek-r1:7b"), mm)
        result = agent.chat("你好", max_tool_rounds=1)
        assert result.get("error_code") != "api_key_not_set", result
        assert mm.chat_stream.called

    def test_keyless_remote_still_blocked(self):
        from unittest.mock import MagicMock
        from mbdsdr_ai.config import AgentConfig

        mm = MagicMock()
        agent = _bare_agent(
            AgentConfig(api_key="", base_url=_REMOTE, model="gpt-x"), mm)
        result = agent.chat("你好")
        assert result["error_code"] == "api_key_not_set"
        assert "API key" in result["content"]
        mm.chat_stream.assert_not_called()


# --------------------------------------------------------------------------
# AI 面板：本地端点空 Key 不再把 agent 置空
# --------------------------------------------------------------------------
def _panel(monkeypatch):
    from desktop.ai_panel import AIPanel
    panel = AIPanel()
    made = {}

    def _fake_create(config):
        made["config"] = config
        return object()

    monkeypatch.setattr(panel, "_save_config", lambda cfg: True)
    monkeypatch.setattr(panel, "_create_agent", _fake_create)
    monkeypatch.setattr(panel, "_apply_pending_mcp_tools", lambda: None)
    monkeypatch.setattr(panel, "_update_ai_status", lambda: None)
    monkeypatch.setattr(panel, "_add_system_message", lambda *a, **k: None)
    return panel, made


class TestPanelKeylessLocal:
    def test_local_endpoint_keeps_agent(self, monkeypatch):
        panel, made = _panel(monkeypatch)
        panel._apply_config_from_dialog("", _LOCAL, "deepseek-r1:7b")
        assert panel.agent is not None
        assert made["config"].base_url == _LOCAL
        assert made["config"].api_key == ""

    def test_remote_endpoint_without_key_still_blocks(self, monkeypatch):
        from PySide6.QtWidgets import QMessageBox
        monkeypatch.setattr(QMessageBox, "information",
                            staticmethod(lambda *a, **k: QMessageBox.Ok))
        panel, made = _panel(monkeypatch)
        panel.agent = object()          # 先放一个，确认会被置空
        panel._apply_config_from_dialog("", _REMOTE, "gpt-x")
        assert panel.agent is None
        assert made == {}
