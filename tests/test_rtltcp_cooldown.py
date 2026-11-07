# SPDX-License-Identifier: MIT
"""rtl_tcp 断连冷却（审计 B 节 P2）。

病灶：rtl_tcp 服务器单客户端（backlog=1），断连后服务端要 2–3 s 才释放
名额（真机基线 runbook §3.5）；客户端断连后立即重连会被拒/吃名额，还把
冷却期的拒绝冒充成「服务不在」。

契约（本轮钉扎）：
  - 真握过手的连接关闭后，同 (host, port) 建连必须等完冷却窗口；
  - 冷却按目标记账：换目标不受牵连；
  - 没握过手（连接被拒）不启动冷却；
  - reconnect_cooldown_remaining() 如实报剩余窗口。

假件：monkeypatch socket.create_connection，握手喂 12 字节 dongle info
（RTL0 + tuner_type/gain_count），无网络确定性。冷却常量调小保测试速度。
"""
import socket
import struct
import time

import pytest

from mbdsdr_ai import rtltcp_client as rc
from mbdsdr_ai.rtltcp_client import RtlTcpClient

_HDR = b"RTL0" + struct.pack(">II", 5, 29)


def _remaining(host, port):
    """旧代码没有该函数时按 0.0 处理——测试红，而不是收集错误。"""
    fn = getattr(rc, "reconnect_cooldown_remaining", None)
    return fn(host, port) if fn else 0.0


class _FakeSock:
    def settimeout(self, _t):
        pass

    def recv(self, n):
        return _HDR[:n]

    def shutdown(self, _how):
        pass

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _clean_registry():
    reg = getattr(rc, "_last_close_ts", None)
    if reg is not None:
        reg.clear()
    yield
    if reg is not None:
        reg.clear()


@pytest.fixture(autouse=True)
def _fast_cooldown(monkeypatch):
    monkeypatch.setattr(rc, "RECONNECT_COOLDOWN_S", 0.4, raising=False)


def _fake_ok(monkeypatch):
    monkeypatch.setattr(socket, "create_connection",
                        lambda *a, **k: _FakeSock())


def _mk(host="127.0.0.1", port=1234):
    return RtlTcpClient(host, port)


class TestCooldown:
    def test_reconnect_waits_cooldown(self, monkeypatch):
        _fake_ok(monkeypatch)
        c = _mk()
        assert c.connect() is True
        c.close()
        t0 = time.monotonic()
        c2 = _mk()
        assert c2.connect() is True
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.3, \
            f"断连后立刻建连没有等冷却窗口（elapsed={elapsed:.3f}s）"
        c2.close()

    def test_cooldown_expires_then_fast(self, monkeypatch):
        _fake_ok(monkeypatch)
        c = _mk()
        assert c.connect() is True
        c.close()
        time.sleep(0.5)                       # 窗口过完
        t0 = time.monotonic()
        c2 = _mk()
        assert c2.connect() is True
        assert time.monotonic() - t0 < 0.3
        c2.close()

    def test_other_target_not_blocked(self, monkeypatch):
        _fake_ok(monkeypatch)
        a = _mk(port=1234)
        assert a.connect() is True
        a.close()
        t0 = time.monotonic()
        b = _mk(port=2345)                    # 另一台 rtl_tcp
        assert b.connect() is True
        assert time.monotonic() - t0 < 0.3
        b.close()

    def test_failed_connect_does_not_arm_cooldown(self, monkeypatch):
        def _refuse(*_a, **_k):
            raise OSError("connection refused")
        monkeypatch.setattr(socket, "create_connection", _refuse)
        c = _mk()
        assert c.connect() is False
        assert _remaining("127.0.0.1", 1234) == 0.0
        _fake_ok(monkeypatch)
        t0 = time.monotonic()
        c2 = _mk()
        assert c2.connect() is True
        assert time.monotonic() - t0 < 0.3
        c2.close()

    def test_remaining_reports_window(self, monkeypatch):
        _fake_ok(monkeypatch)
        assert _remaining("127.0.0.1", 1234) == 0.0
        c = _mk()
        assert c.connect() is True
        c.close()
        left = _remaining("127.0.0.1", 1234)
        assert 0.0 < left <= 0.4

    def test_close_without_socket_does_not_arm(self):
        c = _mk()                              # 从未建连
        c.close()
        assert _remaining("127.0.0.1", 1234) == 0.0
