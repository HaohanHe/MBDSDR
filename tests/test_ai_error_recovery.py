# SPDX-License-Identifier: MIT
"""
AI 错误恢复回归锁（P7.2）
=========================

每种失败都必须有**确定行为**，且不许把错误吞成「成功」：

  * 工具参数非法 JSON / 非对象 -> 明确错误，handler 不执行；
  * 参数类型不符 / 越界 / 不在枚举 -> 明确错误，handler 不执行（不重试坏参数）；
  * 数字字符串 -> 就地转数字（弱模型常见，属可接受归一化）；
  * 工具名不存在 -> tool_not_found；
  * HTTP 500/502/503/504 -> 有限重试；400 -> 不重试（不白烧配额）；
  * 流式输出中断 -> success=False + interrupted=True + 保留已收到的部分内容；
  * Agent 层把中断如实告知（不把半截答案当完整成功）。

不联网：HTTP 用可注入假 session，模型用假 manager。
"""
import json
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402
import requests  # noqa: E402

from mbdsdr_ai.context_manager import ContextManager  # noqa: E402
from mbdsdr_ai.conversation import ConversationStore  # noqa: E402
from mbdsdr_ai.config import AgentConfig  # noqa: E402
from mbdsdr_ai.model_manager import ModelManager  # noqa: E402
from mbdsdr_ai.tool_registry import ToolRegistry  # noqa: E402


# ---------------------------------------------------------------------------
# 工具层：参数错误不执行、不谎报
# ---------------------------------------------------------------------------
@pytest.fixture
def reg():
    r = ToolRegistry()
    calls = []

    def handler(args):
        calls.append(dict(args))
        return "ok"

    r.register(
        "demo_set", "设置演示",
        {"type": "object",
         "properties": {
             "frequency_hz": {"type": "number", "minimum": 0, "maximum": 1e9},
             "mode": {"type": "string", "enum": ["nfm", "wfm"]},
             "gain_db": {"type": "integer", "minimum": 0, "maximum": 73},
             "labels": {"type": "array", "maxItems": 3},
         },
         "required": ["frequency_hz"]},
        handler, category="demo")
    r.calls = calls
    return r


def test_invalid_json_never_executes_handler(reg):
    result = reg.call_from_model(
        {"function": {"name": "demo_set", "arguments": "{'frequency_hz': 145800000, "}})
    assert result.success is False
    assert result.error == "invalid_json_arguments"
    assert "未执行" in result.content
    assert reg.calls == []


def test_non_object_arguments_rejected(reg):
    result = reg.call_from_model(
        {"function": {"name": "demo_set", "arguments": json.dumps([1, 2, 3])}})
    assert result.success is False
    assert result.error == "invalid_arguments_type"
    assert reg.calls == []


def test_out_of_range_rejected(reg):
    result = reg.call("demo_set", {"frequency_hz": 2.4e9, "mode": "nfm"})
    assert result.success is False
    assert result.error == "param_out_of_range"
    assert "上限" in result.content
    assert reg.calls == []


def test_type_and_enum_rejected(reg):
    r1 = reg.call("demo_set", {"frequency_hz": 145e6, "mode": "am"})
    assert r1.success is False and r1.error == "param_not_in_enum"
    r2 = reg.call("demo_set", {"frequency_hz": 145e6, "gain_db": 1.5})
    assert r2.success is False and r2.error == "param_type_mismatch"
    r3 = reg.call("demo_set", {"frequency_hz": 145e6, "labels": "a,b"})
    assert r3.success is False and r3.error == "param_type_mismatch"
    assert reg.calls == []


def test_numeric_string_coerced(reg):
    result = reg.call("demo_set", {"frequency_hz": "145800000", "gain_db": "30"})
    assert result.success is True
    assert reg.calls == [{"frequency_hz": 145800000.0, "gain_db": 30}]


def test_unknown_tool_is_explicit_failure(reg):
    result = reg.call("no_such_tool", {})
    assert result.success is False
    assert result.error == "tool_not_found"
    assert "no_such_tool" in result.content


# ---------------------------------------------------------------------------
# 模型层：HTTP 重试与流中断
# ---------------------------------------------------------------------------
class _Resp:
    def __init__(self, status=200, data=None, lines=None, fail_after=None):
        self.status_code = status
        self._data = data or {}
        self._lines = lines or []
        self._fail_after = fail_after

    @property
    def text(self):
        return json.dumps(self._data)[:200]

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.exceptions.HTTPError(f"HTTP {self.status_code}")
            err.response = self
            raise err

    def json(self):
        return self._data

    def iter_lines(self):
        for i, line in enumerate(self._lines):
            if self._fail_after is not None and i >= self._fail_after:
                raise requests.exceptions.ConnectionError("connection reset by peer")
            yield line


class _Session:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def post(self, *a, **kw):
        idx = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        return self._responses[idx]


def _mgr(responses, max_retries=2):
    m = ModelManager(api_key="sk-test", base_url="https://x.invalid/v1",
                     max_retries=max_retries)
    m._session = _Session(responses)
    return m


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    import mbdsdr_ai.model_manager as mm
    monkeypatch.setattr(mm.time, "sleep", lambda *_a, **_k: None)


def test_http_500_retries_then_succeeds():
    ok = _Resp(data={"choices": [{"message": {"content": "hello"}}],
                     "usage": {"total_tokens": 3}})
    m = _mgr([_Resp(500), ok])
    r = m.chat([{"role": "user", "content": "hi"}])
    assert r["success"] is True and r["content"] == "hello"
    assert m._session.calls == 2


def test_http_500_exhausted_is_honest_failure():
    m = _mgr([_Resp(500), _Resp(500), _Resp(500)])
    r = m.chat([{"role": "user", "content": "hi"}])
    assert r["success"] is False
    assert "HTTP 500" in r["error"]
    assert m._session.calls == 3          # max_retries=2 -> 3 次尝试


def test_http_400_not_retried():
    m = _mgr([_Resp(400, data={"error": "bad request"}), _Resp(400)])
    r = m.chat([{"role": "user", "content": "hi"}])
    assert r["success"] is False
    assert m._session.calls == 1          # 400 不重试，不白烧配额


def test_stream_interruption_preserves_partial_and_flags():
    lines = [
        'data: {"choices":[{"delta":{"content":"前半段"}}]}',
        'data: {"choices":[{"delta":{"content":"，继续"}}]}',
        'data: {"choices":[{"delta":{"content":"（这行不该出现）"}}]}',
    ]
    m = _mgr([_Resp(lines=lines, fail_after=2)])
    events = list(m.chat_stream([{"role": "user", "content": "hi"}]))
    done = events[-1]
    assert done["done"] is True
    assert done["success"] is False
    assert done["interrupted"] is True
    assert done["partial"] is True
    assert done["content"] == "前半段，继续"
    assert done["tool_calls"] == []       # 半截 tool_calls 绝不返回


# ---------------------------------------------------------------------------
# Agent 层：中断如实告知、坏工具参数不吞成成功
# ---------------------------------------------------------------------------
class _FakeModel:
    model = "test-model"

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = 0

    def chat_stream(self, messages, tools=None, **kw):
        idx = min(self.calls, len(self._replies) - 1)
        self.calls += 1
        yield self._replies[idx]

    def parse_tool_calls_from_text(self, *_a, **_k):
        return []


def _agent(tool_registry=None, model=None, ctx_max=4096):
    from mbdsdr_ai.agent import MBDSDRAgent
    a = object.__new__(MBDSDRAgent)
    a.config = AgentConfig(api_key="sk-test", model="test-model")
    a.context_manager = ContextManager(max_context_tokens=ctx_max,
                                       compaction_threshold=0.99,
                                       system_prompt="你是测试助手。")
    a.model_manager = model or _FakeModel([{"done": True, "success": True,
                                            "content": "ok", "tool_calls": []}])
    a.tool_registry = tool_registry or ToolRegistry()
    if isinstance(a.tool_registry, ToolRegistry) and not a.tool_registry.tools:
        a.tool_registry.register("list_tools", "列出工具",
                                 {"type": "object", "properties": {}},
                                 lambda args: [])
    a.memory = type("M", (), {"build_memory_context": lambda self, q: ""})()
    a._repeat_key = None
    a._repeat_count = 0
    a._mcp_client = None
    a.last_tool_selection = None
    a.total_agent_calls = 0
    a.last_error = None
    a.conversation_store = ConversationStore(tempfile.mkdtemp(prefix="mbdsdr_ai_test_"))
    return a


def test_agent_reports_stream_interruption():
    a = _agent(model=_FakeModel([{
        "done": True, "success": False, "interrupted": True,
        "content": "我先看一下当前频率", "error": "connection reset", "tool_calls": [],
    }]))
    r = a.chat("帮我看频率")
    assert r["error"] == "connection reset"
    assert r["interrupted"] is True
    assert "我先看一下当前频率" in r["content"]
    assert "未完成" in r["content"]
    assert "connection reset" in r["content"]


def test_agent_invalid_json_tool_call_is_failure_not_success():
    reg = ToolRegistry()
    seen = []
    reg.register("demo_set", "设置", {"type": "object",
                                      "properties": {"frequency_hz": {"type": "number"}}},
                 lambda args: seen.append(dict(args)) or "ok")
    a = _agent(tool_registry=reg, model=_FakeModel([
        {"done": True, "success": True, "content": "",
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "demo_set", "arguments": "{bad json"}}]},
        {"done": True, "success": True, "content": "参数有误，已停止。", "tool_calls": []},
    ]))
    r = a.chat("把频率设成 145.8")
    assert len(r["tool_results"]) == 1
    assert r["tool_results"][0]["success"] is False
    assert r["tool_results"][0]["error"] == "invalid_json_arguments"
    assert seen == []
    assert r["error"] is None            # 模型已如实解释失败，本轮回复成功
    assert r["interrupted"] is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
