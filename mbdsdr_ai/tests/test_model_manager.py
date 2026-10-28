#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
ModelManager 诚实语义测试（stub 注入，不出网）
=============================================

model_manager.py 是 agent 调 LLM 的唯一出口。红线（AGENTS.md §2）：
调用失败必须**如实上报**，绝不静默降级、绝不伪造 content 冒充成功。
本文件用假 session 注入，钉住错误信封 / 重试 / 统计 / 不降级的契约。

运行: python3 -m pytest mbdsdr_ai/tests/test_model_manager.py -v
"""
import os
import sys
from typing import Any, Dict, List

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

import requests  # noqa: E402

from mbdsdr_ai.model_manager import ModelManager  # noqa: E402


# ---------------------------------------------------------------------------
# 假 HTTP 层
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: Dict[str, Any] = None,
                 text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or str(payload or "")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"{self.status_code}", response=self)

    def json(self):
        return self._payload


class _FakeSession:
    """按脚本逐次吐出响应/异常。"""

    def __init__(self, script: List[Any]):
        self.script = list(script)
        self.calls = 0

    def post(self, url, **kwargs):
        self.calls += 1
        item = self.script.pop(0) if self.script else _FakeResponse(500)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url, **kwargs):
        return self.post(url, **kwargs)


def _ok_payload(content: str = "hello") -> Dict[str, Any]:
    return {
        "choices": [{"message": {"content": content, "tool_calls": []},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "model": "test-model",
    }


def _mgr(script, **kw) -> ModelManager:
    m = ModelManager(api_key="k", base_url="http://x/v1", model="test-model",
                     timeout=1, max_retries=kw.pop("max_retries", 1))
    m._session = _FakeSession(script)
    return m


# ---------------------------------------------------------------------------
# 失败必须诚实
# ---------------------------------------------------------------------------
def test_http_error_reports_failure_honestly():
    """HTTP 500 重试耗尽后：success=False、error 如实、content 绝不伪造。"""
    m = _mgr([_FakeResponse(500, text="boom"), _FakeResponse(500, text="boom")])
    out = m.chat([{"role": "user", "content": "hi"}])
    assert out["success"] is False
    assert "500" in out["error"], f"错误必须含状态码，实际: {out}"
    assert out["content"] == "", "失败时 content 必须为空，不得伪造"
    assert out["tool_calls"] == []


def test_no_silent_model_downgrade_on_failure():
    """调用失败绝不偷偷换模型（静默降级红线）。"""
    m = _mgr([_FakeResponse(500), _FakeResponse(500)], max_retries=1)
    before = m.model
    m.chat([{"role": "user", "content": "hi"}])
    assert m.model == before, f"失败后模型被改了：{before} -> {m.model}"


def test_timeout_reported_honestly():
    m = _mgr([requests.exceptions.Timeout("t"),
              requests.exceptions.Timeout("t")])
    out = m.chat([{"role": "user", "content": "hi"}])
    assert out["success"] is False
    assert "超时" in out["error"], f"实际: {out}"
    assert out["content"] == ""


def test_connection_error_reported_honestly():
    m = _mgr([requests.exceptions.ConnectionError("nope"),
              requests.exceptions.ConnectionError("nope")])
    out = m.chat([{"role": "user", "content": "hi"}])
    assert out["success"] is False
    assert "连接失败" in out["error"]


def test_unknown_error_stops_retrying():
    """非网络类未知错误直接放弃，不空转重试。"""
    m = _mgr([ValueError("weird")], max_retries=5)
    out = m.chat([{"role": "user", "content": "hi"}])
    assert out["success"] is False
    assert "未知错误" in out["error"]
    assert m._session.calls == 1, f"未知错误不该重试，实际调用 {m._session.calls} 次"


# ---------------------------------------------------------------------------
# 重试语义
# ---------------------------------------------------------------------------
def test_retry_then_success():
    """429 后重试成功：最终 success=True（重试是容错，不是造假）。"""
    m = _mgr([_FakeResponse(429, text="rate"), _FakeResponse(200, _ok_payload())])
    out = m.chat([{"role": "user", "content": "hi"}])
    assert out["success"] is True
    assert out["content"] == "hello"
    assert m._session.calls == 2


def test_client_error_4xx_not_retried():
    """400/401 等不可重试错误只调一次。"""
    m = _mgr([_FakeResponse(400, text="bad")], max_retries=3)
    out = m.chat([{"role": "user", "content": "hi"}])
    assert out["success"] is False
    assert m._session.calls == 1, f"4xx 不该重试，实际 {m._session.calls} 次"


def test_stats_counted_honestly():
    m = _mgr([_FakeResponse(200, _ok_payload()), _FakeResponse(500),
              _FakeResponse(500)])
    m.chat([{"role": "user", "content": "a"}])
    m.chat([{"role": "user", "content": "b"}])
    assert m.stats.total_calls == 2
    assert m.stats.successful_calls == 1
    assert m.stats.failed_calls == 1
    assert m.stats.total_prompt_tokens == 10


# ---------------------------------------------------------------------------
# 模型切换与参数
# ---------------------------------------------------------------------------
def test_switch_model_unknown_is_labeled_not_hidden():
    """切到未知模型允许但必须明说（不是静默接受悄悄生效）。"""
    ok, msg = ModelManager().switch_model("brand-new-model")
    assert ok is True
    assert "brand-new-model" in msg
    m = ModelManager()
    m.switch_model("brand-new-model")
    info = m.get_model_info("brand-new-model")
    assert info is not None
    assert "未知" in info.description or "用户指定" in info.description, \
        f"未知模型必须标注来历，实际: {info.description!r}"


def test_fetch_models_no_key_falls_back_and_says_so():
    """无 api_key：回退内置列表，但必须留下诚实说明，不是假装查过了。"""
    m = ModelManager(api_key="")
    before = list(m.list_models())
    got = m.fetch_models(force_refresh=True)
    assert len(got) > 0, "内置列表必须非空"
    assert m._last_error and "api_key" in m._last_error, \
        f"必须说明为何回退，实际: {m._last_error!r}"
    assert len(m.list_models()) == len(before)


def test_fetch_models_failure_keeps_old_list():
    """拉取失败不许清空已有列表，也不许伪造新列表。"""
    m = _mgr([requests.exceptions.ConnectionError("down")])
    old = list(m.list_models())
    got = m.fetch_models(force_refresh=True)
    assert len(got) == len(old)
    assert m._last_error, "失败必须留错误记录"


def test_set_params_clamps_documented_ranges():
    """参数限幅是既定契约（temperature 0-2 / top_p 0-1）——钉住防漂移。"""
    m = ModelManager()
    m.set_params(temperature=99, top_p=-1, max_output_tokens=0, timeout=0)
    assert m.temperature == 2.0
    assert m.top_p == 0.0
    assert m.max_output_tokens == 1
    assert m.timeout == 1


def test_success_response_shape():
    """成功信封字段齐全（消费方依赖这些键）。"""
    m = _mgr([_FakeResponse(200, _ok_payload("hi there"))])
    out = m.chat([{"role": "user", "content": "x"}])
    assert out["success"] is True
    assert out["content"] == "hi there"
    assert out["model"] == "test-model"
    assert out["usage"]["total_tokens"] == 15
    assert isinstance(out["latency_ms"], (int, float))
    assert "finish_reason" in out
