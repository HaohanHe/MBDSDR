# SPDX-License-Identifier: MIT
"""本地 LLM 端点体检测试：五种状态必须分得开，且不许拿内置列表冒充可用。

背景：本机没有 DeepSeek 权重、也没有常驻推理服务。用户切到本地
``base_url`` 时，如果只看到「不可用」，无法判断该去启动服务、pull 模型，
还是改配置。本文件把每种失败钉成可核对的结论。
"""

import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai import local_ai_probe as probe  # noqa: E402


@pytest.fixture(autouse=True)
def _pretend_online(monkeypatch):
    """测试机常带 MBDSDR_OFFLINE：除离线用例外，统一假装在线。

    离线分支由 TestOffline 自己再把 network_disabled 掰回 True。
    """
    from mbdsdr_ai import net_policy

    monkeypatch.setattr(net_policy, "network_disabled", lambda: False)


def _ok(payload):
    return lambda url, headers, timeout: payload


class TestStatuses:
    def test_not_running_on_connection_error(self):
        def boom(url, headers, timeout):
            raise requests.exceptions.ConnectionError("Connection refused")

        r = probe.probe_endpoint("http://127.0.0.1:11434/v1",
                                 http_get=boom)
        assert r["status"] == "not_running"
        assert r["reachable"] is False
        assert "没启动" in r["detail"]

    def test_not_running_on_timeout(self):
        def slow(url, headers, timeout):
            raise requests.exceptions.Timeout("timed out")

        r = probe.probe_endpoint("http://127.0.0.1:8000/v1", http_get=slow)
        assert r["status"] == "not_running"

    def test_empty_model_list(self):
        r = probe.probe_endpoint("http://127.0.0.1:11434/v1",
                                 http_get=_ok({"data": []}))
        assert r["status"] == "no_models"
        assert r["reachable"] is True
        assert "没有加载任何模型" in r["detail"]

    def test_ready_with_model_present(self):
        r = probe.probe_endpoint(
            "http://127.0.0.1:11434/v1", model="deepseek-r1:7b",
            http_get=_ok({"data": [{"id": "qwen3:4b"},
                                   {"id": "deepseek-r1:7b"}]}))
        assert r["status"] == "ready"
        assert r["model_present"] is True
        assert r["models"] == ["qwen3:4b", "deepseek-r1:7b"]

    def test_ready_but_wanted_model_absent(self):
        r = probe.probe_endpoint(
            "http://127.0.0.1:11434/v1", model="deepseek-r1",
            http_get=_ok({"data": [{"id": "qwen3:4b"}]}))
        assert r["status"] == "ready"
        assert r["model_present"] is False
        assert "deepseek-r1" in r["detail"]
        assert "还没 pull" in r["detail"]

    def test_prefix_match_accepts_tag_and_namespace(self):
        r = probe.probe_endpoint(
            "http://x/v1", model="deepseek-r1",
            http_get=_ok({"data": [{"id": "deepseek-r1:7b"}]}))
        assert r["model_present"] is True

    def test_prefix_match_rejects_longer_family_name(self):
        """`deepseek-r1` 不能把 `deepseek-r1-distill` 认成同一个模型。"""
        r = probe.probe_endpoint(
            "http://x/v1", model="deepseek-r1",
            http_get=_ok({"data": [{"id": "deepseek-r1-distill:7b"}]}))
        assert r["model_present"] is False

    def test_unauthorized_is_not_reported_as_down(self):
        resp = requests.Response()
        resp.status_code = 401

        def denied(url, headers, timeout):
            raise requests.exceptions.HTTPError("401", response=resp)

        r = probe.probe_endpoint("http://127.0.0.1:11434/v1",
                                 http_get=denied)
        assert r["status"] == "unauthorized"
        assert r["reachable"] is True
        assert "密钥" in r["detail"]

    def test_non_openai_payload_is_error(self):
        r = probe.probe_endpoint("http://x/v1", http_get=_ok({"hello": 1}))
        assert r["status"] == "error"
        assert "OpenAI 兼容" in r["detail"]

    def test_empty_base_url_is_error_without_calling_http(self):
        def must_not_call(url, headers, timeout):
            raise AssertionError("空 base_url 不该发起请求")

        r = probe.probe_endpoint("", http_get=must_not_call)
        assert r["status"] == "error"
        assert "base_url 为空" in r["detail"]


class TestOffline:
    def test_offline_skips_network(self, monkeypatch):
        from mbdsdr_ai import net_policy

        monkeypatch.setattr(net_policy, "network_disabled", lambda: True)

        def must_not_call(url, headers, timeout):
            raise AssertionError("离线模式不该发起请求")

        r = probe.probe_endpoint("http://127.0.0.1:11434/v1",
                                 http_get=must_not_call)
        assert r["status"] == "offline"
        assert "MBDSDR_OFFLINE" in r["detail"]
        assert "不是「服务坏了」" in r["detail"]


class TestFormat:
    def test_format_has_chinese_status_and_detail(self):
        r = probe.probe_endpoint("http://x/v1", http_get=_ok({"data": []}))
        text = probe.format_probe(r)
        assert text.startswith("本地模型端点：服务在，但没有模型")
        assert "——" in text

    def test_all_statuses_have_labels(self):
        for s in probe.STATUSES:
            text = probe.format_probe({"status": s, "detail": ""})
            assert "未知状态" not in text, s
