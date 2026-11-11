# SPDX-License-Identifier: MIT
"""SpyServer 量程来自服务端能力（2026-11-11，工单 P1-32）。

旧行为：`SpyServerBackend` 的 frequency_range / sample_rate_range / max_gain
全部硬编码成 RTL-SDR 假设（500 k–1.766 G / 225 k–3.2 M / 49.6 dB）。服务端
挂的是 Airspy 或 HF 设备时，UI 给出的档位越界（选不中）或滑杆越界。

本文件钉：握手声明了就采信并标 probed；没声明就保持默认并标 declared。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT, _ROOT / "desktop"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from mbdsdr_ai.spyserver_client import SpyServerBackend  # noqa: E402


class _HandshakeOnlySocket:
    """只吐 1024 字节握手串，之后 EOF（不造 IQ）。"""

    def __init__(self, handshake: bytes):
        self._handshake = handshake
        self._pos = 0
        self.sent = bytearray()

    def settimeout(self, _t):
        pass

    def setsockopt(self, *_a, **_kw):
        pass

    def sendall(self, data: bytes):
        self.sent.extend(data)

    def recv(self, n: int) -> bytes:
        chunk = self._handshake[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def shutdown(self, *_a, **_kw):
        pass

    def close(self):
        pass


def _handshake(pairs) -> bytes:
    body = b"\x00".join(pairs) + b"\x00"
    return body.ljust(1024, b"\x00")


def _connect(handshake: bytes):
    sock = _HandshakeOnlySocket(handshake)
    backend = SpyServerBackend("127.0.0.1", 7355,
                               sock_factory=lambda _h, _p: sock,
                               idle_timeout=0.5)
    assert backend.connect() is True
    return backend


def test_server_declared_ranges_are_adopted():
    backend = _connect(_handshake([
        b"Name=SpyServer Airspy",
        b"SampleRate=6000000",
        b"MinFrequency=24000000",
        b"MaxFrequency=1700000000",
        b"MinSampleRate=250000",
        b"MaxSampleRate=10000000",
        b"MaxGain=31.5",
    ]))
    try:
        assert backend.device.frequency_range == (24_000_000, 1_700_000_000)
        assert backend.device.sample_rate_range == (250_000, 10_000_000)
        assert backend.device.max_gain == 31.5
        assert backend.status.sample_rate_hz == 6_000_000.0
        assert backend.capabilities_source == "server"
        # 值来自服务端握手，不是我方假设 → 必须标 probed 才能被 UI 当"已核实"
        for key in ("frequency_range", "sample_rate_range", "max_gain"):
            assert backend.device.capability_status[key] == "probed", key
    finally:
        backend.disconnect()


def test_undeclared_ranges_keep_defaults_and_stay_unknown():
    backend = _connect(_handshake([
        b"Name=SpyServer Legacy",
        b"SampleRate=2048000",
        b"Type=RTLSDR",
    ]))
    try:
        assert backend.device.frequency_range == (500_000, 1_766_000_000)
        assert backend.device.sample_rate_range == (225_001, 3_200_000)
        assert backend.device.max_gain == 49.6
        assert backend.capabilities_source == "declared"
        # 没声明绝不能标 probed（否则就是拿假设冒充实测）
        assert backend.device.capability_status.get("frequency_range") != "probed"
        assert backend.device.capability_status.get("max_gain") != "probed"
    finally:
        backend.disconnect()


def test_hf_only_server_clamps_default_tuning_into_range():
    backend = _connect(_handshake([
        b"Name=SpyServer HF+",
        b"SampleRate=768000",
        b"MinFrequency=100000",
        b"MaxFrequency=30000000",
        b"MinSampleRate=192000",
        b"MaxSampleRate=768000",
    ]))
    try:
        assert backend.device.frequency_range == (100_000, 30_000_000)
        lo, hi = backend.device.frequency_range
        assert lo <= backend.status.frequency_hz <= hi, (
            "默认起始频点必须落进服务端量程，否则一连上就报越界")
        assert backend.status.sample_rate_hz == 768_000.0
    finally:
        backend.disconnect()
