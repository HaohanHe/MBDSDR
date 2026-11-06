# SPDX-License-Identifier: MIT
"""``mbdsdr_ai/web_bridge_host.py`` 行为测试（离线、无硬件、只绑回环）。

钉死的不是「能跑」，而是四条安全不变量：
1. 只绑 127.0.0.1，拒绝 0.0.0.0；
2. Host / Origin / Referer / Sec-Fetch-Site 同源校验，跨源一律 403；
3. 除 `/health` 与 `/bridge.js` 外，所有端点都要一次性 token；
4. **写工具仍然恒 gated**——本地 host 不是打开写开关的后门。

运行：python -m pytest tests/test_web_bridge_host.py -q
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.web_bridge import WebBridge  # noqa: E402
from mbdsdr_ai.web_bridge_host import ALLOWED_METHODS, BridgeHost  # noqa: E402


class FakeResult:
    def __init__(self, **fields):
        self._d = dict(fields)

    def to_dict(self):
        return dict(self._d)


class FakeRegistry:
    def __init__(self):
        self.tools = {}
        self.calls = []

    def register(self, name, *, write=False):
        self.tools[name] = {"name": name, "description": "desc-" + name,
                            "category": "test", "available": True, "write": write}

    def get_tool_names(self):
        return list(self.tools)

    def list_tools(self, include_unavailable=False):
        return [dict(t) for t in self.tools.values()]

    def is_write_tool(self, name):
        return bool(self.tools.get(name, {}).get("write"))

    def call(self, name, args):
        self.calls.append((name, dict(args)))
        return FakeResult(success=True, content="ok:" + name, tool_name=name,
                          args=dict(args), data={"echo": dict(args)},
                          latency_ms=1.0, error="", truncated=False, next_step="")


def make_host(**kw) -> BridgeHost:
    reg = FakeRegistry()
    reg.register("read_spectrum")
    reg.register("set_frequency", write=True)
    return BridgeHost(WebBridge(registry=reg), **kw), reg


def post(host: BridgeHost, name: str, payload=None, token: bool = True, headers=None):
    h = dict(headers or {})
    if token:
        h["X-MBDSDR-Bridge"] = host.token
    body = json.dumps(payload or {}).encode()
    return host.dispatch("POST", f"/bridge/{name}", body, h)


def get(host: BridgeHost, path: str, token: bool = False, headers=None):
    h = dict(headers or {})
    if token:
        h["X-MBDSDR-Bridge"] = host.token
    return host.dispatch("GET", path, b"", h)


# ---------- 1. 绑定地址 ----------

@pytest.mark.parametrize("bad", ["0.0.0.0", "192.168.1.10", "::", "example.com"])
def test_refuses_non_loopback_bind(bad):
    with pytest.raises(ValueError):
        BridgeHost(WebBridge(), host=bad)


def test_start_background_binds_loopback_only():
    host, _ = make_host(port=0)
    host.start_background()
    try:
        assert host._server is not None
        assert host._server.server_address[0] == "127.0.0.1"
        assert host.port > 0
        with urllib.request.urlopen(host.url + "health", timeout=5) as r:
            body = json.loads(r.read().decode())
        assert body["ok"] is True and body["host"] == "loopback"
    finally:
        host.shutdown()


# ---------- 2. 同源校验 ----------

def test_cross_origin_blocked():
    host, _ = make_host(port=8770)
    code, body, _ = post(host, "invokeTool", {"name": "read_spectrum"},
                         headers={"Origin": "http://evil.example"})
    assert code == 403
    assert json.loads(body)["error"] == "forbidden"


def test_cross_site_script_fetch_blocked():
    host, _ = make_host(port=8770)
    code, _, _ = get(host, "/bridge.js", headers={"Sec-Fetch-Site": "cross-site"})
    assert code == 403


def test_dns_rebinding_host_header_blocked():
    host, _ = make_host(port=8770)
    code, _, _ = get(host, "/bridge.js", headers={"Host": "attacker.example:8770"})
    assert code == 403


def test_same_origin_allowed():
    host, _ = make_host(port=8770)
    code, body, ctype = get(host, "/bridge.js", headers={
        "Host": "127.0.0.1:8770", "Sec-Fetch-Site": "same-origin"})
    assert code == 200 and "javascript" in ctype
    assert b"window.mbdsdrBridge" in body


# ---------- 3. token ----------

@pytest.mark.parametrize("method", ["invokeTool", "getCapabilities", "readSpectrum", "getTle"])
def test_bridge_endpoints_require_token(method):
    host, _ = make_host(port=8770)
    code, body, _ = post(host, method, {}, token=False)
    assert code == 403
    assert json.loads(body)["error"] == "bad_token"


def test_manifest_requires_token():
    host, _ = make_host(port=8770)
    assert get(host, "/manifest", token=False)[0] == 403
    assert get(host, "/manifest", token=True)[0] == 200


def test_health_needs_no_token():
    host, _ = make_host(port=8770)
    code, body, _ = get(host, "/health")
    assert code == 200 and json.loads(body)["ok"] is True


# ---------- 4. 写工具仍然恒 gated（最重要） ----------

def test_write_tool_still_gated_through_host():
    host, reg = make_host(port=8770)
    code, body, _ = post(host, "invokeTool",
                         {"name": "set_frequency", "args": {"hz": 145_900_000}})
    assert code == 200
    env = json.loads(body)
    assert env["ok"] is False
    assert env["gated"] is True
    assert env["error"] in ("manual_gate_required", "manual_gate_unavailable")
    assert reg.calls == [], "写工具绝不能经宿主落到 registry.call()"


def test_self_report_arm_manual_is_stripped_and_rejected():
    host, reg = make_host(port=8770)
    code, body, _ = post(host, "invokeTool",
                         {"name": "set_frequency",
                          "args": {"hz": 1, "arm_manual": True}})
    env = json.loads(body)
    assert env["gated"] is True
    assert env.get("selfReportRejected") == ["arm_manual"]
    assert reg.calls == []


def test_read_tool_reaches_registry():
    host, reg = make_host(port=8770)
    code, body, _ = post(host, "invokeTool", {"name": "read_spectrum", "args": {}})
    env = json.loads(body)
    assert code == 200 and env["ok"] is True
    assert reg.calls == [("read_spectrum", {})]


def test_unknown_method_not_reflective():
    host, _ = make_host(port=8770)
    code, body, _ = post(host, "os.system", {"cmd": "whoami"})
    assert code == 404
    assert json.loads(body)["error"] == "unknown_method"
    assert "os.system" not in ALLOWED_METHODS


# ---------- 5. shim 形状与前端契约 ----------

def test_bridge_js_has_frontend_shape():
    host, _ = make_host(port=8770)
    js = host.bootstrap_js()
    for marker in ("window.mbdsdrBridge", "invoke:", "getTle:", "getPasses:",
                   "readSpectrum:", "satellites:", "isLive:", "detectSource()"):
        assert marker in js, marker
    # getTle 必须同步（前端 50-dialog.js 当真值判断）
    assert "return Object.prototype.hasOwnProperty.call(bridge.tle" in js


def test_satellites_and_tle_are_embedded():
    reg = FakeRegistry()
    host = BridgeHost(WebBridge(
        registry=reg,
        satellites=[{"norad": "25338", "name": "NOAA 15"}],
        tle_provider=lambda n: f"TLE-{n}",
    ), port=8770)
    boot = host.bootstrap()
    assert boot["satellites"][0]["norad"] == "25338"
    assert boot["tle"] == {"25338": "TLE-25338"}
    assert "TLE-25338" in host.bootstrap_js()


def test_tle_provider_failure_is_honest_null():
    def boom(_n):
        raise RuntimeError("provider down")

    reg = FakeRegistry()
    host = BridgeHost(WebBridge(
        registry=reg, satellites=[{"norad": "1"}], tle_provider=boom), port=8770)
    assert host.bootstrap()["tle"] == {"1": None}


# ---------- 6. dist 注入 ----------

def test_injects_bridge_script_once(tmp_path):
    dist = tmp_path / "index.html"
    dist.write_text("<!DOCTYPE html><html><head><title>x</title></head>"
                    "<body><div id='app'></div></body></html>", encoding="utf-8")
    host = BridgeHost(WebBridge(), dist=dist, port=8770)
    code, html, ctype = get(host, "/")
    assert code == 200 and "text/html" in ctype
    text = html.decode()
    assert text.count('src="/bridge.js"') == 1
    assert text.index("/bridge.js") < text.index("</head>")
    # 二次请求不重复注入
    assert get(host, "/")[1].decode().count('src="/bridge.js"') == 1


def test_missing_dist_returns_503(tmp_path):
    host = BridgeHost(WebBridge(), dist=tmp_path / "nope.html", port=8770)
    code, body, _ = get(host, "/")
    assert code == 503
    assert json.loads(body)["error"] == "dist_missing"


# ---------- 7. 离线诚实空态 ----------

def test_shim_is_live_reflects_registry_truth():
    """桥挂上 ≠ 内核可用：空注册表时 shim 必须报 false，前端不得显示在线。"""
    empty = BridgeHost(WebBridge(), port=8770)
    assert "isLive: function () { return false; }" in empty.bootstrap_js()
    reg = FakeRegistry()
    reg.register("read_spectrum")
    live = BridgeHost(WebBridge(registry=reg), port=8770)
    assert "isLive: function () { return true; }" in live.bootstrap_js()


def test_offline_bridge_is_honest():
    host = BridgeHost(WebBridge(), port=8770)
    code, body, _ = post(host, "invokeTool", {"name": "read_spectrum"})
    env = json.loads(body)
    assert code == 200 and env["ok"] is False
    assert env["error"] == "not_connected"
    assert get(host, "/health")[1] and json.loads(get(host, "/health")[1])["live"] is False
