# SPDX-License-Identifier: MIT
"""``mbdsdr_ai/mount.py`` 的行为测试：能力位门 + 位置来源 + 错误码。

用 ``http.server`` 在后台线程起假 Alpaca 服务（不是 mock 掉 HTTP 层），
锁死三件事：

1. 能力位为 False 时，客户端**一个 PUT 都不发**（核心反证：先探测、后拒绝）；
2. 正常指向发出的参数名是 ``RightAscension`` / ``Declination`` /
   ``Azimuth`` / ``Altitude``，不是自定义字段；
3. ``position().source`` 在无编码器声明时只能是 ``"model"``；读失败抛错，
   不返回 0 冒充坐标。

假服务通过只证明协议与门禁正确，**不证明任何真机可用**。
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.mount import (  # noqa: E402
    AlpacaMount,
    MountCapabilities,
    MountError,
    MountPosition,
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
        self.error = None             # (ErrorNumber, ErrorMessage)
        self.status = 200
        self.broken_members = set()

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

        if fake.status != 200:
            self._respond(fake.status, {"ErrorNumber": 500,
                                        "ErrorMessage": "内部故障"})
            return
        if fake.error is not None:
            number, message = fake.error
            self._respond(200, {"ErrorNumber": number,
                                "ErrorMessage": message})
            return
        if member in fake.broken_members:
            self._respond(200, {"ErrorNumber": 1024,
                                "ErrorMessage": f"{member} 读取失败"})
            return

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
def alpaca(server):
    """每个用例一份干净设备状态；服务线程整模块复用。"""
    server.fake.reset()
    return server.fake, server.server_address[1]


def _client(port, timeout_s=1.0):
    return AlpacaMount("127.0.0.1", port, timeout_s=timeout_s)


@contextmanager
def _unused_port():
    """拿一个当前没人监听的本机端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        yield sock.getsockname()[1]


# ---------------------------------------------------------------------------
# 能力位
# ---------------------------------------------------------------------------
class TestCapabilities:
    def test_connect_then_all_capabilities_true(self, alpaca):
        fake, port = alpaca
        client = _client(port)
        assert client.connect() is True
        assert fake.put_params("connected") is not None

        caps = client.capabilities()
        assert isinstance(caps, MountCapabilities)
        assert caps.can_slew_radec is True
        assert caps.can_slew_altaz is True
        assert caps.can_abort is True
        assert caps.can_track is True
        assert caps.can_park is True
        assert caps.can_read_position is True
        # 未声明编码器 → 只能按模型推算
        assert caps.has_encoder is False
        assert caps.raw["canslew"] is True
        assert caps.raw["hasencoder"] is False

    def test_capability_probe_failure_is_not_guessed_true(self, alpaca):
        fake, port = alpaca
        fake.status = 500
        with pytest.raises(MountError) as exc:
            _client(port).capabilities()
        assert exc.value.code == "http_500"


# ---------------------------------------------------------------------------
# 能力位门：False 时一个 PUT 都不许发
# ---------------------------------------------------------------------------
class TestCapabilityGates:
    def test_slew_radec_refused_without_put(self, alpaca):
        fake, port = alpaca
        fake.capabilities["canslew"] = False
        with pytest.raises(MountError) as exc:
            _client(port).slew_to_radec(5.5, -12.25)
        assert exc.value.code == "capability_missing:canSlew"
        assert fake.puts == [], f"能力位为 False 仍发了 PUT：{fake.puts}"

    def test_slew_altaz_refused_without_put(self, alpaca):
        fake, port = alpaca
        fake.capabilities["canslewaltaz"] = False
        with pytest.raises(MountError) as exc:
            _client(port).slew_to_altaz(120.0, 45.0)
        assert exc.value.code == "capability_missing:canSlewAltAz"
        assert fake.puts == [], f"能力位为 False 仍发了 PUT：{fake.puts}"

    def test_abort_refused_without_put(self, alpaca):
        fake, port = alpaca
        fake.capabilities["canabort"] = False
        with pytest.raises(MountError) as exc:
            _client(port).abort_slew()
        assert exc.value.code == "capability_missing:canabort"
        assert fake.puts == []

    def test_tracking_refused_without_put(self, alpaca):
        fake, port = alpaca
        fake.capabilities["cantrack"] = False
        with pytest.raises(MountError) as exc:
            _client(port).tracking(True)
        assert exc.value.code == "capability_missing:canTrack"
        assert fake.puts == []

    def test_park_refused_without_put(self, alpaca):
        fake, port = alpaca
        fake.capabilities["canpark"] = False
        with pytest.raises(MountError) as exc:
            _client(port).park()
        assert exc.value.code == "capability_missing:canPark"
        assert fake.puts == []


# ---------------------------------------------------------------------------
# 正常指向：参数名必须是 Alpaca 官方成员
# ---------------------------------------------------------------------------
class TestSlewParameters:
    def test_slew_radec_puts_official_parameters(self, alpaca):
        fake, port = alpaca
        _client(port).slew_to_radec(5.5, -12.25)
        params = fake.put_params("slewtocoordinatesasync")
        assert params is not None, "没有发出 slewtocoordinatesasync"
        assert float(params["RightAscension"]) == pytest.approx(5.5)
        assert float(params["Declination"]) == pytest.approx(-12.25)

    def test_slew_altaz_puts_official_parameters(self, alpaca):
        fake, port = alpaca
        _client(port).slew_to_altaz(120.0, 45.5)
        params = fake.put_params("slewtoaltazasync")
        assert params is not None, "没有发出 slewtoaltazasync"
        assert float(params["Azimuth"]) == pytest.approx(120.0)
        assert float(params["Altitude"]) == pytest.approx(45.5)

    def test_park_and_tracking_put_after_gate(self, alpaca):
        fake, port = alpaca
        client = _client(port)
        client.park()
        client.unpark()
        client.tracking(True)
        assert fake.put_params("park") is not None
        assert fake.put_params("unpark") is not None
        assert fake.put_params("tracking")["Tracking"] == "true"


# ---------------------------------------------------------------------------
# 错误码映射
# ---------------------------------------------------------------------------
class TestErrorMapping:
    def test_server_error_number_is_bad_envelope_with_message(self, alpaca):
        fake, port = alpaca
        fake.error = (1024, "motor stalled")
        with pytest.raises(MountError) as exc:
            _client(port).position()
        assert exc.value.code == "bad_envelope"
        assert "motor stalled" in str(exc.value)

    def test_http_500_maps_to_http_500(self, alpaca):
        fake, port = alpaca
        fake.status = 500
        with pytest.raises(MountError) as exc:
            _client(port).position()
        assert exc.value.code == "http_500"

    def test_unreachable_port_maps_to_timeout_quickly(self):
        with _unused_port() as port:
            started = time.monotonic()
            with pytest.raises(MountError) as exc:
                _client(port, timeout_s=0.2).capabilities()
            elapsed = time.monotonic() - started
        assert exc.value.code == "timeout"
        assert elapsed < 3.0, f"超时用例耗时 {elapsed:.2f}s，不应卡默认 3s"


# ---------------------------------------------------------------------------
# 位置来源与读失败
# ---------------------------------------------------------------------------
class TestPositionHonesty:
    def test_source_is_model_without_encoder(self, alpaca):
        fake, port = alpaca
        pos = _client(port).position()
        assert isinstance(pos, MountPosition)
        assert pos.source == "model"
        assert pos.ra_deg == pytest.approx(10.5 * 15.0)  # 小时 → 度
        assert pos.dec_deg == pytest.approx(-20.0)
        assert pos.slewing is False
        assert pos.read_at_utc.endswith("+00:00")

    def test_source_is_encoder_when_device_declares_it(self, alpaca):
        fake, port = alpaca
        fake.capabilities["hasencoder"] = True
        pos = _client(port).position()
        assert pos.source == "encoder"

    def test_position_read_failure_raises_instead_of_zero(self, alpaca):
        fake, port = alpaca
        fake.broken_members = {"declination"}
        with pytest.raises(MountError) as exc:
            _client(port).position()
        assert exc.value.code == "bad_envelope"
        assert "读取失败" in str(exc.value)
