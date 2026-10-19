# SPDX-License-Identifier: MIT
"""
mobile_server.py 手机 WebSocket 服务——确定性离线测试

被测对象：
  - MobileHub.register/unregister：连接集合管理（未知连接 discard 幂等）；
  - broadcast：全员推送 + 发送失败的死客户端即时清除；ensure_ascii=False
    使中文原样传输不转义；
  - handle：进入即注册；坏 JSON 行跳过不炸；流中断 finally 注销；
  - on_message 三分支：handshake->hello；telemetry（fix 局部更新、None 忽略；
    heading 缺字段保旧）；chat->ai_command 回执；未知类型静默忽略；
  - compute_passes：以 min_elevation kwarg 调 orbit.visible_satellites
    （phase66 实证修复 min_el 拼写 bug——该 bug 曾令过境计算恒返回空列表），
    orbit 抛异常时返回空列表不泄错；
  - pointing_loop：无 fix 不推送；有 fix 按 interval 广播 satellite_passes；
  - serve：真实 websockets 服务器 + 真实客户端 127.0.0.1 端到端握手。

运行：python3 -m pytest mbdsdr_ai/tests/test_mobile_server.py -v
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai import mobile_server  # noqa: E402
from mbdsdr_ai.mobile_server import (  # noqa: E402
    DEFAULT_HOST,
    DEFAULT_PORT,
    MobileHub,
    compute_passes,
    pointing_loop,
)


# ---------------------------------------------------------------- 测试替身

class FakeWS:
    """最小 WebSocket 替身：可迭代收消息，send 记录且可置故障。"""

    def __init__(self, messages=(), raise_at=None, send_raises=False,
                 on_iter_start=None):
        self._messages = list(messages)
        self._raise_at = raise_at      # 非 None：消息发完即流中断（对端异常断开）
        self._send_raises = send_raises
        self._on_iter_start = on_iter_start
        self.sent = []

    async def send(self, data):
        if self._send_raises:
            raise ConnectionError("client gone")
        self.sent.append(data)

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        if self._on_iter_start is not None:
            self._on_iter_start(self)
        for m in self._messages:
            yield m
        if self._raise_at is not None:
            # 消息发完后流中断（真实连接：对端异常断开）
            raise ConnectionResetError("stream broke")


# ---------------------------------------------------------------- MobileHub 连接管理

def test_register_unregister():
    hub = MobileHub()
    ws = FakeWS()
    asyncio.run(hub.register(ws))
    assert ws in hub.clients
    asyncio.run(hub.unregister(ws))
    assert ws not in hub.clients


def test_unregister_unknown_is_noop():
    hub = MobileHub()
    asyncio.run(hub.unregister(FakeWS()))   # 不在集合内不得炸
    assert hub.clients == set()


def test_broadcast_all_clients():
    hub = MobileHub()
    a, b = FakeWS(), FakeWS()
    asyncio.run(hub.register(a))
    asyncio.run(hub.register(b))
    asyncio.run(hub.broadcast({"type": "t", "payload": {"x": 1}}))
    assert json.loads(a.sent[0]) == {"type": "t", "payload": {"x": 1}}
    assert json.loads(b.sent[0]) == {"type": "t", "payload": {"x": 1}}


def test_broadcast_removes_dead_client():
    """发送失败的死客户端即时清除，不影响存活客户端。"""
    hub = MobileHub()
    dead, alive = FakeWS(send_raises=True), FakeWS()
    asyncio.run(hub.register(dead))
    asyncio.run(hub.register(alive))
    asyncio.run(hub.broadcast({"type": "t"}))
    assert dead not in hub.clients
    assert alive in hub.clients
    assert len(alive.sent) == 1


def test_broadcast_unicode_not_escaped():
    """ensure_ascii=False：中文原样出现在线上字节里。"""
    hub = MobileHub()
    ws = FakeWS()
    asyncio.run(hub.register(ws))
    asyncio.run(hub.broadcast({"text": "卫星过境"}))
    assert "卫星过境" in ws.sent[0]
    assert "\\u" not in ws.sent[0]


# ---------------------------------------------------------------- handle

def test_handle_bad_json_skipped():
    """坏 JSON 行跳过（continue），后续正常消息仍被处理。"""
    hub = MobileHub()
    ws = FakeWS(["{not json", json.dumps({"type": "handshake",
                                          "payload": {"device": "p"}})])
    asyncio.run(hub.handle(ws))
    assert len(ws.sent) == 1
    assert json.loads(ws.sent[0]) == {"type": "hello", "ok": True}


def test_handle_registers_on_enter():
    """迭代开始（连接建立）时客户端已在集合内。"""
    hub = MobileHub()
    seen = {}

    def probe(w):
        seen["registered"] = w in hub.clients

    ws = FakeWS([json.dumps({"type": "handshake"})], on_iter_start=probe)
    asyncio.run(hub.handle(ws))
    assert seen["registered"] is True


def test_handle_unregisters_on_stream_error():
    """流中断时 finally 注销，不泄漏死连接。"""
    hub = MobileHub()
    ws = FakeWS([json.dumps({"type": "handshake"})], raise_at=1)
    with pytest.raises(ConnectionResetError):
        asyncio.run(hub.handle(ws))
    assert hub.clients == set()


# ---------------------------------------------------------------- on_message

def test_handshake_hello():
    hub = MobileHub()
    ws = FakeWS()
    asyncio.run(hub.on_message(ws, {"type": "handshake",
                                    "payload": {"device": "pixel"}}))
    assert json.loads(ws.sent[0]) == {"type": "hello", "ok": True}


def test_telemetry_fix_full_update():
    hub = MobileHub()
    ws = FakeWS()
    asyncio.run(hub.on_message(ws, {"type": "telemetry", "kind": "fix",
                                    "payload": {"lat": 1.5, "lon": 2.5,
                                                "alt": 3.5}}))
    assert hub.last_fix == {"lat": 1.5, "lon": 2.5, "alt": 3.5}
    assert ws.sent == []      # telemetry 不回复


def test_telemetry_fix_partial_none_ignored():
    """None 字段被忽略（局部更新），已有值不被清掉。"""
    hub = MobileHub()
    hub.last_fix = {"lat": 1.5, "lon": 2.5, "alt": 3.5}
    asyncio.run(hub.on_message(FakeWS(), {"type": "telemetry", "kind": "fix",
                                          "payload": {"lat": None, "lon": 9.0}}))
    assert hub.last_fix == {"lat": 1.5, "lon": 9.0, "alt": 3.5}


def test_telemetry_heading_update_and_default():
    hub = MobileHub()
    asyncio.run(hub.on_message(FakeWS(), {"type": "telemetry", "kind": "heading",
                                          "payload": {"deg": 123.0}}))
    assert hub.last_heading == 123.0
    # 缺 deg：保持旧值（不重置为 0）
    asyncio.run(hub.on_message(FakeWS(), {"type": "telemetry", "kind": "heading",
                                          "payload": {}}))
    assert hub.last_heading == 123.0


def test_telemetry_unknown_kind_ignored():
    hub = MobileHub()
    ws = FakeWS()
    asyncio.run(hub.on_message(ws, {"type": "telemetry", "kind": "bogus",
                                    "payload": {"lat": 1.0}}))
    assert hub.last_fix["lat"] is None
    assert ws.sent == []


def test_chat_reply_placeholder():
    hub = MobileHub()
    ws = FakeWS()
    asyncio.run(hub.on_message(ws, {"type": "chat",
                                    "payload": {"text": "hello"}}))
    out = json.loads(ws.sent[0])
    assert out["type"] == "ai_command"
    assert out["payload"]["text"] == "已收到：hello（AI 内核待接入）"


def test_chat_missing_text():
    hub = MobileHub()
    ws = FakeWS()
    asyncio.run(hub.on_message(ws, {"type": "chat", "payload": {}}))
    assert json.loads(ws.sent[0])["payload"]["text"] == "已收到：（AI 内核待接入）"


def test_unknown_type_ignored():
    hub = MobileHub()
    ws = FakeWS()
    asyncio.run(hub.on_message(ws, {"type": "bogus", "payload": {"x": 1}}))
    assert ws.sent == []


# ---------------------------------------------------------------- compute_passes

def test_compute_passes_uses_min_elevation_kwarg(monkeypatch):
    """回归钉：必须用 min_elevation kwarg 调 orbit（曾误写 min_el，
    致 TypeError 被 except 吞掉、过境计算恒返回空列表——phase66 修复）。"""
    from mbdsdr_ai import orbit

    calls = []

    def spy(observer_lat, observer_lon, observer_alt=0.0,
            min_elevation=0.0, satellite_type="all"):
        calls.append((observer_lat, observer_lon, observer_alt,
                      min_elevation, satellite_type))
        return [{"name": "spy-sat"}]

    monkeypatch.setattr(orbit, "visible_satellites", spy)
    out = compute_passes(39.9, 116.4)
    assert out == [{"name": "spy-sat"}]
    assert calls == [(39.9, 116.4, 0.0, 0.0, "all")]


def test_compute_passes_swallows_orbit_error(monkeypatch):
    """orbit 抛异常 -> 空列表，不把异常泄给推送循环。"""
    from mbdsdr_ai import orbit

    def boom(*a, **k):
        raise RuntimeError("tle fetch failed")

    monkeypatch.setattr(orbit, "visible_satellites", boom)
    assert compute_passes(39.9, 116.4) == []


def test_compute_passes_real_orbit_returns_list():
    """真 sgp4 链路（TLE 时效性决定非空与否，故只钉契约：list of dict）。"""
    res = compute_passes(39.9, 116.4)
    assert isinstance(res, list)
    for sat in res:
        assert isinstance(sat, dict)
        assert "elevation" in sat


# ---------------------------------------------------------------- pointing_loop

def test_pointing_loop_no_fix_no_push():
    """无定位 fix：循环空转，不广播任何东西。"""
    hub = MobileHub()
    ws = FakeWS()
    asyncio.run(hub.register(ws))

    async def scenario():
        task = asyncio.create_task(pointing_loop(hub, interval=0.01))
        await asyncio.sleep(0.06)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert ws.sent == []


def test_pointing_loop_pushes_passes(monkeypatch):
    """有 fix：按 interval 广播 satellite_passes（compute_passes 打桩保确定性）。"""
    monkeypatch.setattr(mobile_server, "compute_passes",
                        lambda lat, lon: [{"name": "ISS"}])
    hub = MobileHub()
    ws = FakeWS()
    asyncio.run(hub.register(ws))
    hub.last_fix = {"lat": 39.9, "lon": 116.4, "alt": None}

    async def scenario():
        task = asyncio.create_task(pointing_loop(hub, interval=0.01))
        for _ in range(300):
            if ws.sent:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert len(ws.sent) >= 1
    out = json.loads(ws.sent[0])
    assert out["type"] == "satellite_passes"
    assert out["payload"]["passes"] == [{"name": "ISS"}]


# ---------------------------------------------------------------- serve 端到端

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_serve_end_to_end_handshake():
    """真实 websockets 服务器 + 真实客户端：握手/聊天全链路（127.0.0.1）。"""
    import websockets

    port = _free_port()

    async def scenario():
        task = asyncio.create_task(mobile_server.serve("127.0.0.1", port))
        try:
            ws = None
            for _ in range(100):           # 等服务起来
                try:
                    ws = await websockets.connect(f"ws://127.0.0.1:{port}")
                    break
                except Exception:
                    await asyncio.sleep(0.05)
            assert ws is not None, "server did not come up"
            async with ws:
                await ws.send(json.dumps({"type": "handshake",
                                          "payload": {"device": "pytest"}}))
                hello = json.loads(await asyncio.wait_for(ws.recv(), 5))
                await ws.send(json.dumps({"type": "chat",
                                          "payload": {"text": "ping"}}))
                reply = json.loads(await asyncio.wait_for(ws.recv(), 5))
            return hello, reply
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    hello, reply = asyncio.run(scenario())
    assert hello == {"type": "hello", "ok": True}
    assert reply["type"] == "ai_command"
    assert reply["payload"]["text"] == "已收到：ping（AI 内核待接入）"


def test_default_host_port():
    assert DEFAULT_HOST == "0.0.0.0"
    assert DEFAULT_PORT == 8765


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
