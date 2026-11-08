#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
chat_stream 流式重试冗余测试（Sprint 8，stub 注入，不出网）
=============================================================

chat_stream 是 agent 主循环实际走的 LLM 出口。Sprint 8 前它零重试：单次瞬时
429/5xx/timeout/断连就整轮失败。本文件钉住补齐后的契约：

1. **首字节前**的瞬时失败（还没向调用方 yield 过任何增量）→ 按
   error_catalog.retryable 退避重试，重试对调用方不可见（无重复输出）。
2. 一旦已 emit（content 或 reasoning 增量已 yield）→ **绝不重试**，按
   interrupted+partial 诚实回报（重试会让 UI 看到重复输出）。
3. 非瞬时码（4xx / 未预期异常）→ 立即放弃，不烧剩余重试额度。
4. tool_calls 累积**不算** emit（累积期间不向调用方 yield），故首字节前
   tool 流中断仍可安全重试。
5. 统计诚实：一次逻辑调用 total_calls==1，成功/失败只计其一，重试不重复计数。
6. 零回归：无瞬时失败时，干净的流逐字保持原行为（deltas→done success）。

运行: python -m pytest mbdsdr_ai/tests/test_model_manager_stream_retry.py -v
"""
import json
import os
import sys
import time
from typing import Any, List

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

import requests  # noqa: E402

from mbdsdr_ai.model_manager import ModelManager  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_offline_gate(monkeypatch):
    """全程用假 session，不出网；清掉离线门，否则 chat_stream 直接拒绝。"""
    monkeypatch.delenv("MBDSDR_OFFLINE", raising=False)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """重试退避走 time.sleep；测试里替换为 no-op，避免真实等待。

    model_manager 以 ``import time; time.sleep(...)`` 形态访问（调用时在 time
    模块对象上查属性），故 patch time 模块的 sleep 即对消费方生效。
    """
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)


# ---------------------------------------------------------------------------
# 假流式 HTTP 层
# ---------------------------------------------------------------------------
class _FakeStreamResponse:
    """流式假响应。

    events 是一串「逐行吐出」的项：bytes → 作为一行 SSE yield；Exception →
    在该点抛出（模拟首字节前/中途断流）。raise_for_status 按状态码抛 HTTPError。
    """

    def __init__(self, status_code: int = 200, events: List[Any] = None):
        self.status_code = status_code
        self._events = list(events or [])
        self.text = ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"{self.status_code}", response=self)

    def iter_lines(self):
        for ev in self._events:
            if isinstance(ev, Exception):
                raise ev
            yield ev


class _FakeSession:
    """按脚本逐次吐出响应/异常（post 时抛 = 首字节前失败）。"""

    def __init__(self, script: List[Any]):
        self.script = list(script)
        self.calls = 0

    def post(self, url, **kwargs):
        self.calls += 1
        item = self.script.pop(0) if self.script else _FakeStreamResponse(500)
        if isinstance(item, Exception):
            raise item
        return item


# ── SSE 行构造 ────────────────────────────────────────────────
def _sse(obj: dict) -> bytes:
    return b"data: " + json.dumps(obj, ensure_ascii=False).encode("utf-8")


def _done() -> bytes:
    return b"data: [DONE]"


def _content(text: str) -> bytes:
    return _sse({"choices": [{"delta": {"content": text}}]})


def _reasoning(text: str) -> bytes:
    return _sse({"choices": [{"delta": {"reasoning_content": text}}]})


def _tool(index: int, id: str = None, name: str = None,
          args: str = None) -> bytes:
    fn = {}
    if name is not None:
        fn["name"] = name
    if args is not None:
        fn["arguments"] = args
    tc = {"index": index, "function": fn}
    if id is not None:
        tc["id"] = id
    return _sse({"choices": [{"delta": {"tool_calls": [tc]}}]})


def _ok_stream(content: str = "hello") -> _FakeStreamResponse:
    return _FakeStreamResponse(200, [_content(content), _done()])


def _smgr(script, max_retries: int = 2) -> ModelManager:
    m = ModelManager(api_key="k", base_url="http://x/v1", model="test-model",
                     timeout=1, max_retries=max_retries)
    m._session = _FakeSession(script)
    return m


def _consume(gen):
    """消费 chat_stream，返回 (content_deltas, reasoning_deltas, done_event)。"""
    deltas: List[str] = []
    reasoning: List[str] = []
    done = None
    for ev in gen:
        if ev.get("done"):
            done = ev
        elif "reasoning_delta" in ev:
            reasoning.append(ev.get("reasoning_delta", ""))
        elif ev.get("delta"):
            deltas.append(ev["delta"])
    return deltas, reasoning, done


# ---------------------------------------------------------------------------
# 1. 首字节前瞬时失败 → 重试成功，对调用方不可见（无重复）
# ---------------------------------------------------------------------------
def test_pre_first_byte_429_then_success_retries_invisibly():
    """429（raise_for_status 抛，首字节前）后重试成功：调用方只见一份内容。"""
    m = _smgr([_FakeStreamResponse(429), _ok_stream("hello")])
    deltas, _reasoning, done = _consume(
        m.chat_stream([{"role": "user", "content": "hi"}]))
    assert done is not None and done["success"] is True
    assert done["content"] == "hello"
    assert deltas == ["hello"], f"重试不得产生重复输出，实际 deltas={deltas}"
    assert m._session.calls == 2, "429 应触发一次重试（共 2 次请求）"


def test_pre_first_byte_connection_error_then_success():
    """post 时断连（首字节前）→ llm_unreachable 可重试 → 第二次成功。"""
    m = _smgr([requests.exceptions.ConnectionError("down"), _ok_stream("ok")])
    deltas, _r, done = _consume(m.chat_stream([{"role": "user", "content": "hi"}]))
    assert done["success"] is True
    assert done["content"] == "ok"
    assert deltas == ["ok"]
    assert m._session.calls == 2


def test_pre_first_byte_timeout_then_success():
    """post 时超时（首字节前）→ llm_timeout 可重试 → 第二次成功。"""
    m = _smgr([requests.exceptions.Timeout("t"), _ok_stream("late")])
    _d, _r, done = _consume(m.chat_stream([{"role": "user", "content": "hi"}]))
    assert done["success"] is True
    assert done["content"] == "late"
    assert m._session.calls == 2


def test_pre_first_byte_5xx_exhausts_then_honest_failure():
    """首字节前 503 重试耗尽：诚实失败、不伪造内容、请求数 = 1+max_retries。"""
    m = _smgr([_FakeStreamResponse(503), _FakeStreamResponse(503),
               _FakeStreamResponse(503)], max_retries=2)
    deltas, _r, done = _consume(m.chat_stream([{"role": "user", "content": "hi"}]))
    assert done["success"] is False
    assert done["error_code"] == "llm_http_5xx"
    assert done["content"] == "", "失败时不得伪造内容"
    assert done["partial"] is False
    assert done["interrupted"] is True
    assert deltas == []
    assert m._session.calls == 3, "max_retries=2 → 共 3 次请求"


# ---------------------------------------------------------------------------
# 2. 已 emit 后绝不重试（避免重复输出）
# ---------------------------------------------------------------------------
def test_mid_stream_failure_after_content_emit_not_retried():
    """已 yield content 增量后断流：不重试，interrupted+partial 如实保留。"""
    drop = _FakeStreamResponse(
        200, [_content("partial answer"), requests.exceptions.ConnectionError("drop")])
    m = _smgr([drop], max_retries=3)
    deltas, _r, done = _consume(m.chat_stream([{"role": "user", "content": "hi"}]))
    assert m._session.calls == 1, "已 emit 后绝不重试（否则 UI 见重复输出）"
    assert done["success"] is False
    assert done["interrupted"] is True
    assert done["partial"] is True
    assert done["content"] == "partial answer", "已收到的部分内容不得丢弃"
    assert deltas == ["partial answer"]


def test_mid_stream_failure_after_reasoning_emit_not_retried():
    """reasoning 增量也算 emit：其后断流不重试。"""
    drop = _FakeStreamResponse(
        200, [_reasoning("thinking"), requests.exceptions.ConnectionError("drop")])
    m = _smgr([drop], max_retries=3)
    deltas, reasoning, done = _consume(
        m.chat_stream([{"role": "user", "content": "hi"}]))
    assert m._session.calls == 1, "reasoning 已 emit → 不重试"
    assert reasoning == ["thinking"], "reasoning 增量须如实交给调用方"
    assert deltas == []
    assert done["success"] is False
    assert done["interrupted"] is True
    # 只 emit 了 reasoning、无 content → partial 按 content 判定为 False
    assert done["partial"] is False


# ---------------------------------------------------------------------------
# 3. 非瞬时码立即放弃（不烧重试额度）
# ---------------------------------------------------------------------------
def test_pre_first_byte_4xx_not_retried():
    """400（llm_http_4xx，catalog 判不可重试）→ 只请求一次，快速失败。"""
    m = _smgr([_FakeStreamResponse(400)], max_retries=3)
    _d, _r, done = _consume(m.chat_stream([{"role": "user", "content": "hi"}]))
    assert done["success"] is False
    assert done["error_code"] == "llm_http_4xx"
    assert m._session.calls == 1, "4xx 不该重试"


def test_unexpected_exception_not_retried():
    """非网络类未预期异常（llm_unexpected）→ 不空转重试，错误码可追溯。"""
    m = _smgr([ValueError("weird")], max_retries=5)
    _d, _r, done = _consume(m.chat_stream([{"role": "user", "content": "hi"}]))
    assert done["success"] is False
    assert done["error_code"] == "llm_unexpected"
    assert "ValueError" in done["error"], "必须带原始异常类型"
    assert m._session.calls == 1


# ---------------------------------------------------------------------------
# 4. tool_calls 累积不算 emit → 首字节前 tool 流中断可安全重试
# ---------------------------------------------------------------------------
def test_tool_accumulation_before_content_is_not_emission():
    """tool 累积期间不向调用方 yield；首字节前 tool 流中断 → 重试拿到完整 tool_calls。"""
    attempt0 = _FakeStreamResponse(
        200, [_tool(0, id="c1", name="get_"),
              requests.exceptions.ConnectionError("drop")])
    attempt1 = _FakeStreamResponse(
        200, [_tool(0, id="c1", name="get_weather", args='{"city":"X"}'), _done()])
    m = _smgr([attempt0, attempt1], max_retries=2)
    deltas, _r, done = _consume(m.chat_stream([{"role": "user", "content": "hi"}]))
    assert m._session.calls == 2, "tool 累积不算 emit，首字节前中断应重试"
    assert deltas == [], "tool 累积期间调用方不应看到任何增量"
    assert done["success"] is True
    assert done["tool_calls"] == [{
        "id": "c1", "type": "function",
        "function": {"name": "get_weather", "arguments": '{"city":"X"}'}}]


# ---------------------------------------------------------------------------
# 5. 统计诚实：重试不重复计数
# ---------------------------------------------------------------------------
def test_stats_counted_once_on_retry_success():
    """首字节前重试成功：total_calls==1、successful==1、failed==0。"""
    m = _smgr([_FakeStreamResponse(503), _ok_stream("hi")])
    _consume(m.chat_stream([{"role": "user", "content": "x"}]))
    assert m.stats.total_calls == 1, "一次逻辑调用只计一次 total_calls"
    assert m.stats.successful_calls == 1
    assert m.stats.failed_calls == 0


def test_stats_counted_once_on_retry_exhaustion():
    """重试耗尽失败：total_calls==1、failed==1、successful==0。"""
    m = _smgr([_FakeStreamResponse(503), _FakeStreamResponse(503),
               _FakeStreamResponse(503)], max_retries=2)
    _consume(m.chat_stream([{"role": "user", "content": "x"}]))
    assert m.stats.total_calls == 1
    assert m.stats.failed_calls == 1
    assert m.stats.successful_calls == 0


# ---------------------------------------------------------------------------
# 6. 零回归：干净流逐字保持原行为
# ---------------------------------------------------------------------------
def test_clean_stream_unchanged_no_retry():
    """无瞬时失败：deltas 顺序交出、tool_calls 累积、done success，只请求一次。"""
    stream = _FakeStreamResponse(200, [
        _content("a"), _content("b"),
        _tool(0, id="t1", name="foo"), _tool(0, args='{"x":1}'),
        _sse({"usage": {"prompt_tokens": 7, "completion_tokens": 3,
                        "total_tokens": 10}}),
        _done(),
    ])
    m = _smgr([stream])
    deltas, _r, done = _consume(m.chat_stream([{"role": "user", "content": "x"}]))
    assert m._session.calls == 1, "干净流不得触发任何重试"
    assert deltas == ["a", "b"]
    assert done["success"] is True
    assert done["content"] == "ab"
    assert done["tool_calls"] == [{
        "id": "t1", "type": "function",
        "function": {"name": "foo", "arguments": '{"x":1}'}}]
    assert done["usage"]["total_tokens"] == 10
