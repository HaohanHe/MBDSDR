# SPDX-License-Identifier: MIT
"""MCP HTTP 传输的回环绕代理（实测修好的真 bug）。

本机设了 HTTPS_PROXY 时，urllib 默认 ProxyHandler 会把 127.0.0.1 的本地
MCP 请求也发给代理，代理对 localhost 回 502，于是真实连接失败被伪装成
http_error —— test_http_unreachable_server_envelope 因此长期红，并被误判为
"本机代理导致的环境问题"。实际是代码 bug：回环永远不该走代理。
"""
import urllib.request

from mbdsdr_ai.mcp_client import _opener_for


def _proxy_handlers(opener):
    return [h for h in opener.handlers
            if isinstance(h, urllib.request.ProxyHandler)]


def test_loopback_always_bypasses_proxy(monkeypatch):
    """即使环境里配了代理，127.0.0.1 / localhost 也必须绕过。"""
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9999")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9999")

    for url in ("http://127.0.0.1:1/mcp",
                "http://localhost:8080/mcp",
                "http://[::1]:8080/mcp"):
        ph = _proxy_handlers(_opener_for(url))
        # 实测：回环路径下 ProxyHandler 干脆不装（= 完全不走代理）。
        # 两种形态都算绕过：没装 ProxyHandler，或装了但代理表为空。
        assert not ph or ph[0].proxies == {}, \
            f"{url} 回环必须绕过代理，实际: {[(type(h).__name__, getattr(h, 'proxies', None)) for h in _opener_for(url).handlers]}"


def test_non_loopback_still_respects_proxy_env(monkeypatch):
    """非回环地址仍要尊重环境代理设置 —— 不许把代理全局关掉糊弄过去。"""
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9999")

    ph = _proxy_handlers(_opener_for("https://example.com/mcp"))
    assert ph and ph[0].proxies, \
        f"非回环应保留环境代理，实际: {ph[0].proxies}"
