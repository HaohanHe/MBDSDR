# SPDX-License-Identifier: MIT
"""赤道仪 AI 工具面：注册、写门分级与假 Alpaca 服务行为。

钉住六件事：

1. 三个工具都已注册；``mount_slew`` 分级 ``hard_gate``、``mount_abort``
   分级 ``low_risk``、``mount_status`` 不在写工具表；
2. ``mount_slew`` 成功时服务端收到 Alpaca 官方字段
   （``RightAscension`` / ``Declination`` 或 ``Azimuth`` / ``Altitude``）；
3. 能力位为 false 时 ``mount_slew`` 返回失败、消息含 ``capability_missing``，
   服务端 PUT 次数为 0；
4. ``mount_abort`` 在 ``can_abort=true`` 时发出请求，false 时如实报错；
5. ``mount_status`` 在无编码器时 ``source`` 文案含 ``model``（模型推算）；
6. 成功文案只写「已下发指向指令（未验证对准）」，不得出现「已对准」。

假服务通过只证明协议与门禁，**不证明任何真机可用**。
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.tool_registry import (  # noqa: E402
    WRITE_TOOL_GRADE_HARD_GATE,
    WRITE_TOOL_GRADE_LOW_RISK,
    write_tool_grade,
)


# ---------------------------------------------------------------------------
# 假 Alpaca 服务
# ---------------------------------------------------------------------------
class _FakeAlpaca:
    def __init__(self):
        self.reset()

    def reset(self):
        self.capabilities = {
            "canslew": True,
            "canslewaltaz": True,
            "canabort": True,
            "cantrack": True,
            "canpark": True,
            "canreadposition": True,
            "hasencoder": False,
        }
        self.position = {
            "rightascension": 10.5,   # 小时
            "declination": -20.0,     # 度
            "slewing": False,
        }
        self.requests = []            # [(method, member, params)]

    @property
    def puts(self):
        return [row for row in self.requests if row[0] == "PUT"]

    def put_params(self, member):
        for method, name, params in self.requests:
            if method == "PUT" and name == member:
                return params
        return None


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # 静音
        pass

    def _respond(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _dispatch(self, method):
        fake = self.server.fake
        member = self.path.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        params = {}
        if method == "PUT":
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            params = {key: values[-1] for key, values in parse_qs(raw).items()}
        fake.requests.append((method, member, params))

        if method == "GET":
            if member == "connected":
                value = True
            elif member in fake.capabilities:
                value = fake.capabilities[member]
            elif member in fake.position:
                value = fake.position[member]
            else:
                self._respond(400, {"ErrorNumber": 400,
                                    "ErrorMessage": "unknown property"})
                return
        else:
            value = True
        self._respond(200, {"Value": value, "ErrorNumber": 0,
                            "ErrorMessage": ""})

    def do_GET(self):
        self._dispatch("GET")

    def do_PUT(self):
        self._dispatch("PUT")


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.daemon_threads = True
    httpd.fake = _FakeAlpaca()
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.02},
        daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


@pytest.fixture
def fake(server):
    server.fake.reset()
    return server.fake


@pytest.fixture(scope="module")
def agent():
    from mbdsdr_ai import model_manager as _mm
    from mbdsdr_ai.agent import MBDSDRAgent
    from mbdsdr_ai.config import AgentConfig

    original = _mm.ModelManager.fetch_models
    _mm.ModelManager.fetch_models = lambda self, *a, **k: None
    try:
        yield MBDSDRAgent(AgentConfig(api_key="sk-test", model="test-model"))
    finally:
        _mm.ModelManager.fetch_models = original


def _handler(agent, name):
    return agent.tool_registry.tools[name]["handler"]


@contextmanager
def _unused_port():
    """拿一个当前没人监听的本机端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        yield sock.getsockname()[1]


# ---------------------------------------------------------------------------
# 注册与分级
# ---------------------------------------------------------------------------
class TestRegistration:
    def test_three_tools_registered_and_graded(self, agent):
        reg = agent.tool_registry
        for name in ("mount_status", "mount_slew", "mount_abort"):
            assert name in reg.tools, f"{name} 未注册"

        assert reg.tools["mount_status"]["write"] is False
        assert "mount_status" not in {
            n for n, t in reg.tools.items() if t.get("write")}

        assert reg.tools["mount_slew"]["write"] is True
        assert write_tool_grade("mount_slew") == WRITE_TOOL_GRADE_HARD_GATE

        assert reg.tools["mount_abort"]["write"] is True
        assert write_tool_grade("mount_abort") == WRITE_TOOL_GRADE_LOW_RISK


# ---------------------------------------------------------------------------
# mount_slew
# ---------------------------------------------------------------------------
class TestMountSlew:
    def test_radec_sends_official_fields(self, agent, fake, server):
        port = server.server_address[1]
        res = _handler(agent, "mount_slew")({
            "mode": "radec", "ra_hours": 5.5, "dec_deg": -12.25,
            "host": "127.0.0.1", "port": port,
        })
        assert res.success is True
        params = fake.put_params("slewtocoordinatesasync")
        assert params is not None, "没有发出 slewtocoordinatesasync"
        assert float(params["RightAscension"]) == pytest.approx(5.5)
        assert float(params["Declination"]) == pytest.approx(-12.25)

    def test_altaz_sends_official_fields(self, agent, fake, server):
        port = server.server_address[1]
        res = _handler(agent, "mount_slew")({
            "mode": "altaz", "az_deg": 120.0, "el_deg": 45.5,
            "host": "127.0.0.1", "port": port,
        })
        assert res.success is True
        params = fake.put_params("slewtoaltazasync")
        assert params is not None, "没有发出 slewtoaltazasync"
        assert float(params["Azimuth"]) == pytest.approx(120.0)
        assert float(params["Altitude"]) == pytest.approx(45.5)

    def test_success_text_never_claims_aligned(self, agent, fake, server):
        port = server.server_address[1]
        for args in ({"mode": "radec", "ra_hours": 1.0, "dec_deg": 2.0},
                     {"mode": "altaz", "az_deg": 10.0, "el_deg": 20.0}):
            res = _handler(agent, "mount_slew")(
                dict(args, host="127.0.0.1", port=port))
            assert res.success is True
            assert "已下发指向指令（未验证对准）" in res.content
            assert "已对准" not in res.content
            assert "已锁定" not in res.content

    def test_capability_missing_returns_false_and_no_put(self, agent, fake,
                                                        server):
        fake.capabilities["canslew"] = False
        port = server.server_address[1]
        res = _handler(agent, "mount_slew")({
            "mode": "radec", "ra_hours": 5.5, "dec_deg": -12.25,
            "host": "127.0.0.1", "port": port,
        })
        assert res.success is False
        assert "capability_missing" in res.content
        assert "canSlew" in res.content
        assert fake.puts == [], f"能力位为 False 仍发了 PUT：{fake.puts}"

    def test_missing_coordinate_is_refused(self, agent, fake, server):
        port = server.server_address[1]
        res = _handler(agent, "mount_slew")({
            "mode": "radec", "host": "127.0.0.1", "port": port,
        })
        assert res.success is False
        assert "ra_hours" in res.content and "dec_deg" in res.content
        assert fake.puts == []


# ---------------------------------------------------------------------------
# mount_abort
# ---------------------------------------------------------------------------
class TestMountAbort:
    def test_abort_sends_request_when_capable(self, agent, fake, server):
        port = server.server_address[1]
        res = _handler(agent, "mount_abort")({
            "host": "127.0.0.1", "port": port,
        })
        assert res.success is True
        assert fake.put_params("abortslew") is not None

    def test_abort_reports_capability_missing_honestly(self, agent, fake,
                                                       server):
        fake.capabilities["canabort"] = False
        port = server.server_address[1]
        res = _handler(agent, "mount_abort")({
            "host": "127.0.0.1", "port": port,
        })
        assert res.success is False
        assert "capability_missing" in res.content
        assert fake.puts == []


# ---------------------------------------------------------------------------
# mount_status
# ---------------------------------------------------------------------------
class TestMountStatus:
    def test_no_encoder_source_is_model(self, agent, fake, server):
        port = server.server_address[1]
        res = _handler(agent, "mount_status")({
            "host": "127.0.0.1", "port": port,
        })
        assert res.success is True
        assert "model" in res.content
        assert "模型推算" in res.content
        assert "slewing=false" in res.content
        assert "RA 157.5000°" in res.content  # 10.5h × 15

    def test_encoder_source_when_declared(self, agent, fake, server):
        fake.capabilities["hasencoder"] = True
        port = server.server_address[1]
        res = _handler(agent, "mount_status")({
            "host": "127.0.0.1", "port": port,
        })
        assert res.success is True
        assert "encoder" in res.content

    def test_unreachable_is_not_reported_as_zero(self, agent):
        with _unused_port() as port:
            res = _handler(agent, "mount_status")({
                "host": "127.0.0.1", "port": port,
            })
        assert res.success is False
        assert res.content.startswith("赤道仪未连接：")
        assert "RA 0.0000" not in res.content
        assert "Dec 0.0000" not in res.content
