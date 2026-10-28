# SPDX-License-Identifier: MIT
"""
time_sync.py（NTP 校时 / 模拟时钟偏移）行为测试
===============================================

不访问外网：用回环 UDP 上的合成 NTP 应答（真实 socket 真实字节，
收/发时间戳取不同值）钉解析语义——NTP 偏差必须用**传输时间戳**
（字节 40-47），旧实现读的是接收时间戳（字节 32-39）。
短/畸形应答走错误信封（旧实现在 try 外 struct.unpack 直接崩）。
simulation_clock_offset 用真实 TimeEngine（时钟旅行后恢复）。

运行：python -m pytest mbdsdr_ai/tests/test_time_sync_behavior.py -q
"""
from __future__ import annotations

import os
import socket
import struct
import sys
import threading
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai.time_sync import (  # noqa: E402
    _NTP_DELTA, ntp_offset, simulation_clock_offset,
)

# 合成应答：接收时刻与传输时刻相差 3600s（旧实现读错字段会差一小时）
RX_SEC = 3_900_000_000
TX_SEC = 3_900_003_600


def _serve_once(response: bytes) -> int:
    """起一次性回环 NTP 服务，返回端口。"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]

    def run():
        try:
            data, addr = srv.recvfrom(1024)
            srv.sendto(response, addr)
        except OSError:
            pass

    threading.Thread(target=run, daemon=True).start()
    return port


def _ntp_packet(rx_sec: int, tx_sec: int) -> bytes:
    pkt = bytearray(48)
    pkt[0] = 0x1c  # LI=0, VN=4, Mode=4(server)
    struct.pack_into("!II", pkt, 32, rx_sec, 0)  # Receive Timestamp
    struct.pack_into("!II", pkt, 40, tx_sec, 0)  # Transmit Timestamp
    return bytes(pkt)


# ─────────────────────────── ntp_offset ───────────────────────────

def test_ntp_uses_transmit_timestamp_not_receive():
    port = _serve_once(_ntp_packet(RX_SEC, TX_SEC))
    r = ntp_offset("127.0.0.1", port=port, timeout=2.0)
    assert r["reachable"] is True
    want_ntp_sec = TX_SEC - _NTP_DELTA
    assert r["ntp_time"] == time.strftime(
        "%Y-%m-%d %H:%M:%S", time.gmtime(want_ntp_sec))
    # 偏差 = 应答传输时刻 - 本地收发中点（量级由 TX 决定，与 RX 无关）
    assert abs(r["local_clock_skew_s"] - (want_ntp_sec - time.time())) < 5.0
    assert r["round_trip_s"] >= 0.0


def test_ntp_malformed_short_response_envelope():
    # 旧实现 struct.unpack 在 try 外，短包直接崩
    port = _serve_once(b"\x1c" + b"\0" * 10)
    r = ntp_offset("127.0.0.1", port=port, timeout=2.0)
    assert r["reachable"] is False
    assert "error" in r and r["error"]


def test_ntp_unreachable_envelope():
    # 回环上的关闭端口：超时/连接重置都归入诚实错误信封
    r = ntp_offset("127.0.0.1", port=1, timeout=0.3)
    assert r["reachable"] is False
    assert "error" in r and r["error"]
    assert "local_clock_skew_s" not in r


# ─────────────────────────── simulation_clock_offset ───────────────────────────

def test_simulation_clock_offset_following_system():
    from mbdsdr_ai.new_spacetime import get_time_engine
    eng = get_time_engine()
    try:
        eng.set_time_now()
        r = simulation_clock_offset()
        assert r["following_system"] is True
        assert abs(r["offset_seconds"]) < 1.0
        assert r["rate"] == eng.get_rate()
    finally:
        eng.set_time_now()


def test_simulation_clock_offset_after_time_travel():
    from mbdsdr_ai.new_spacetime import get_time_engine
    eng = get_time_engine()
    try:
        eng.set_unix(time.time() + 3600.0)  # 穿越到 1 小时后
        r = simulation_clock_offset()
        assert r["following_system"] is False
        assert abs(r["offset_seconds"] - 3600.0) < 2.0
    finally:
        eng.set_time_now()  # 恢复，不污染同进程其它用例
    r2 = simulation_clock_offset()
    assert r2["following_system"] is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
