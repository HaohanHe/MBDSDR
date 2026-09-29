# SPDX-License-Identifier: MIT
"""SpyServerClient 确定性测试。

对照 mbdsdr_ai/spyserver_client.py（上游 sdrpp rtl_tcp_client.cpp）。
红线：连接失败抛 SpyServerError，不造假 IQ。
"""
import socket
import struct

import numpy as np
import pytest

from mbdsdr_ai.spyserver_client import SpyServerClient, SpyServerError


class _FakeSocket:
    """模拟 SpyServer 服务器：握手发 1024 字节 ASCII 配置，之后回 IQ 字节。"""

    def __init__(self, host: str, port: int, handshake: bytes, iq_payload: bytes):
        self._handshake = handshake
        self._iq = iq_payload
        self._sent = bytearray()
        self._recv_pos = 0
        self._closed = False

    # 模拟 socket API
    def settimeout(self, t):
        pass

    def setsockopt(self, *a, **kw):
        pass

    def sendall(self, data: bytes):
        self._sent.extend(data)

    def recv(self, n: int) -> bytes:
        # 先吐握手，再吐 IQ
        if self._recv_pos < len(self._handshake):
            chunk = self._handshake[self._recv_pos:self._recv_pos + n]
            self._recv_pos += len(chunk)
            return chunk
        if self._recv_pos < len(self._handshake) + len(self._iq):
            off = self._recv_pos - len(self._handshake)
            chunk = self._iq[off:off + n]
            self._recv_pos += len(chunk)
            return chunk
        return b""  # EOF

    def shutdown(self, *a, **kw):
        pass

    def close(self):
        self._closed = True


def _make_handshake() -> bytes:
    """构造 1024 字节 ASCII key=value\\0 握手串。"""
    pairs = [
        b"Name=SpyServer Test",
        b"SampleRate=2048000",
        b"Gain=20.0",
        b"Type=RTLSDR",
    ]
    body = b"\x00".join(pairs) + b"\x00"
    return body.ljust(1024, b"\x00")


def _make_iq(n: int) -> bytes:
    """生成 n 个 uint8 I/Q 交织样本（I=200, Q=100）。"""
    out = bytearray()
    for _ in range(n):
        out.append(200)
        out.append(100)
    return bytes(out)


def test_handshake_parses_config():
    hs = _make_handshake()
    iq = _make_iq(100)
    c = SpyServerClient("fake", 7355, sock_factory=lambda h, p: _FakeSocket(h, p, hs, iq))
    c.start()
    assert c.server_info.get("Name") == "SpyServer Test"
    assert c.server_info.get("SampleRate") == "2048000"
    c.stop()


def test_iq_parsing():
    hs = _make_handshake()
    iq = _make_iq(100)
    c = SpyServerClient("fake", 7355, sock_factory=lambda h, p: _FakeSocket(h, p, hs, iq))
    c.start()
    out = c.read_samples(100)
    assert out.shape == (100,)
    assert out.dtype == np.complex64
    # I=200 -> real=(200-128)/128=0.5625; Q=100 -> imag=(100-128)/128=-0.21875
    assert np.isclose(out[0].real, 0.5625, atol=1e-6)
    assert np.isclose(out[0].imag, -0.21875, atol=1e-6)
    c.stop()


def test_command_packet_format():
    """set_frequency 必须发 5 字节 >BI 包。"""
    hs = _make_handshake()
    iq = _make_iq(100)
    fake = _FakeSocket("h", 1, hs, iq)
    c = SpyServerClient("fake", 7355, sock_factory=lambda h, p: fake)
    c.start()
    fake._sent.clear()
    c.set_frequency(98_000_000)
    # 期望 5 字节 = struct.pack(">BI", 1, 98000000)
    expected = struct.pack(">BI", 1, 98_000_000)
    assert bytes(fake._sent) == expected
    c.stop()


def test_connection_failure_raises():
    """连不上（factory 抛 OSError）必须抛 SpyServerError。"""
    def _fail(h, p):
        raise OSError("refused")
    c = SpyServerClient("127.0.0.1", 1, sock_factory=_fail)
    with pytest.raises(SpyServerError):
        c.start()


def test_bad_handshake_raises():
    """握手串不是有效 key=value 时必须抛 SpyServerError。"""
    bad = b"\x00" * 1024  # 全零，解析不出任何 pair
    c = SpyServerClient("fake", 1, sock_factory=lambda h, p: _FakeSocket(h, p, bad, b""))
    with pytest.raises(SpyServerError):
        c.start()
