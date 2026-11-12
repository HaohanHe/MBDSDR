# SPDX-License-Identifier: MIT
"""接入层失败模式普查：rtl_tcp / SpyServer 的每一种"断法"都要有现象。

口径：本机 loopback 假服务端 + 真实 socket，不连公网、不碰真硬件。
每个用例只回答一件事——**当前代码遇到这种断法会怎么表现**：

  连不上 / 端口被别的服务占着 / 回了非预期格式（magic、结构不符）/
  半包（握手未读满就断）/ 连上后服务端不回数据 / 中途断流 /
  半帧后关闭 / 重复重连 / 慢滴握手（永远卡在「连接中」）。

用例按**期望的不变量**断言（不是按现状），因此修复前红、修复后绿；
红的那几条就是普查要修的问题清单。

复跑（PowerShell）：
    $env:MBDSDR_TEST_LOCK='0'   # 纯 socket 用例，不加载 Qt
    python -m pytest -q -s tests/test_network_failure_modes.py
"""

from __future__ import annotations

import socket
import struct
import threading
import time
from contextlib import contextmanager
from typing import Callable, List, Optional

import numpy as np
import pytest

from mbdsdr_ai import link_state
from mbdsdr_ai.rtltcp_client import RtlTcpBackend, RtlTcpClient
from mbdsdr_ai.spyserver_client import (
    SpyServerBackend,
    SpyServerClient,
    SpyServerError,
)

_RTL_HEADER = b"RTL0" + struct.pack(">II", 5, 29)
_SPY_KEYS = b"Name=FakeSpyServer\0SampleRate=2048000\0Version=0\0"
_SPY_HANDSHAKE = (_SPY_KEYS + b"\0" * (1024 - len(_SPY_KEYS)))[:1024]


# ---------------------------------------------------------------------------
# 假服务端工具
# ---------------------------------------------------------------------------
def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def _iq_bytes(n_samples: int, seed: int = 1) -> bytes:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=n_samples * 2, dtype=np.uint8).tobytes()


@contextmanager
def _server(handler: Callable[[socket.socket, int, threading.Event], None],
            accept_count: int = 1, backlog: int = 1):
    """loopback 假服务端：按顺序接受 ``accept_count`` 个连接。

    记录所有已接受连接，退出时统一 shutdown，保证客户端不会挂在 recv 上。
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(backlog)
    port = int(srv.getsockname()[1])
    stop = threading.Event()
    conns: List[socket.socket] = []

    def _serve() -> None:
        for idx in range(accept_count):
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            conns.append(conn)
            threading.Thread(
                target=_run_handler, args=(handler, conn, idx, stop),
                daemon=True,
            ).start()

    threading.Thread(target=_serve, daemon=True).start()
    try:
        yield port, stop, conns
    finally:
        stop.set()
        for c in conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                c.close()
            except OSError:
                pass
        try:
            srv.close()
        except OSError:
            pass


def _run_handler(handler, conn, idx, stop) -> None:
    try:
        handler(conn, idx, stop)
    except (OSError, ValueError):
        pass


def _wait(pred: Callable[[], bool], timeout: float = 3.0,
          interval: float = 0.01) -> Optional[float]:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        if pred():
            return time.perf_counter() - t0
        time.sleep(interval)
    return None


def _send(conn: socket.socket, data: bytes) -> None:
    conn.sendall(data)


# ---------------------------------------------------------------------------
# 1. rtl_tcp
# ---------------------------------------------------------------------------
class TestRtlTcpFailureModes:
    def test_connect_refused_is_immediate_and_actionable(self, monkeypatch):
        """连不上：在有界时间内给出 refused/timeout，不重试、不伪造 IQ。

        本机实测：Windows loopback 关闭端口在短超时下报 timeout、默认 5s 下
        才拿到 WSAECONNREFUSED——两种都算「连不上」，关键是**有界且可见**。
        """
        monkeypatch.setattr(RtlTcpClient, "CONNECT_TIMEOUT", 1.0)
        port = _free_port()
        be = RtlTcpBackend("127.0.0.1", port, idle_timeout=0.4)
        t0 = time.perf_counter()
        assert be.connect() is False
        elapsed = time.perf_counter() - t0
        assert elapsed < 3.0, f"拒绝连接拖了 {elapsed:.2f}s"
        assert be.status.error_category in (
            link_state.CATEGORY_REFUSED, link_state.CATEGORY_TIMEOUT)
        assert "连接失败" in be.status.error
        assert be.status.link_state == link_state.STATE_FAILED
        assert be.read_samples(64) is None

    def test_wrong_magic_is_handshake_failure(self, monkeypatch):
        """端口被别的服务占着：TCP 连上了但 magic 不是 RTL0。"""
        monkeypatch.setattr(RtlTcpClient, "CONNECT_TIMEOUT", 0.5)

        def handler(conn, idx, stop):
            _send(conn, b"HTTP/1.1 200")   # 12 字节，但不是 rtl_tcp
            time.sleep(0.2)

        with _server(handler) as (port, _stop, _conns):
            c = RtlTcpClient("127.0.0.1", port, idle_timeout=0.4)
            t0 = time.perf_counter()
            assert c.connect() is False
            assert time.perf_counter() - t0 < 2.0
            assert c.state == link_state.STATE_FAILED
            assert c.error_category == link_state.CATEGORY_HANDSHAKE
            assert "magic" in c.error and "RTL0" in c.error

    def test_partial_header_then_close_is_handshake_failure(self, monkeypatch):
        """半包：12 字节头只到 6 字节就 FIN。"""
        monkeypatch.setattr(RtlTcpClient, "CONNECT_TIMEOUT", 0.5)

        def handler(conn, idx, stop):
            _send(conn, _RTL_HEADER[:6])
            time.sleep(0.05)
            conn.close()

        with _server(handler) as (port, _stop, _conns):
            c = RtlTcpClient("127.0.0.1", port, idle_timeout=0.4)
            assert c.connect() is False
            assert c.error_category == link_state.CATEGORY_HANDSHAKE
            assert "不完整" in c.error

    def test_silence_after_handshake_goes_stale_then_hard_disconnect(self):
        """连上后服务端不回数据：stale → 2×idle 硬判断开，不永远「已连接」。"""
        def handler(conn, idx, stop):
            _send(conn, _RTL_HEADER)
            stop.wait(3.0)

        with _server(handler) as (port, _stop, _conns):
            c = RtlTcpClient("127.0.0.1", port, idle_timeout=0.4)
            assert c.connect() is True
            outs = []
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < 3.0:
                outs.append(c.read_samples(64))
                if c.state == link_state.STATE_DISCONNECTED:
                    break
            assert outs and all(o is None for o in outs), "无数据时不许返回数组"
            assert c.state == link_state.STATE_DISCONNECTED
            assert c.error_category == link_state.CATEGORY_IDLE
            assert c.error_code == "link_half_open"
            assert c._sock is None

    def test_eof_mid_stream_reports_eof_not_zero_iq(self):
        """中途断流：读到一半对端 FIN。已读到的样本真实，之后 None。"""
        def handler(conn, idx, stop):
            _send(conn, _RTL_HEADER + _iq_bytes(4096, seed=7))
            time.sleep(0.05)
            conn.close()

        with _server(handler) as (port, _stop, _conns):
            c = RtlTcpClient("127.0.0.1", port, idle_timeout=0.4)
            assert c.connect() is True
            got = c.read_samples(1024)
            assert got is not None and got.dtype == np.complex64
            assert np.all(np.abs(got.real) <= 1.0)
            assert np.all(np.abs(got.imag) <= 1.0)
            tail = c.read_samples(4096)
            assert tail is None, "断流后不许补零数组"
            assert c.state == link_state.STATE_DISCONNECTED
            assert c.error_category == link_state.CATEGORY_EOF

    def test_second_client_when_slot_busy_fails_bounded(self, monkeypatch):
        """端口被占（rtl_tcp 单客户端名额）：第二个客户端不无限等。"""
        monkeypatch.setattr(RtlTcpClient, "CONNECT_TIMEOUT", 0.5)

        def handler(conn, idx, stop):
            _send(conn, _RTL_HEADER + _iq_bytes(4096, seed=9))
            stop.wait(3.0)

        with _server(handler) as (port, _stop, _conns):
            c1 = RtlTcpClient("127.0.0.1", port, idle_timeout=0.4)
            assert c1.connect() is True
            c2 = RtlTcpClient("127.0.0.1", port, idle_timeout=0.4)
            t0 = time.perf_counter()
            assert c2.connect() is False
            elapsed = time.perf_counter() - t0
            assert elapsed < 2.0, f"占位时第二个客户端等了 {elapsed:.2f}s"
            assert c2.error_category in (
                link_state.CATEGORY_TIMEOUT, link_state.CATEGORY_HANDSHAKE)
            assert c2.error and c2.suggestion
            c1.close()

    def test_slow_drip_handshake_cannot_hold_connect_forever(self, monkeypatch):
        """慢滴握手：每字节都小于超时，但总时长必须被硬上限截断。"""
        monkeypatch.setattr(RtlTcpClient, "CONNECT_TIMEOUT", 0.5)

        def handler(conn, idx, stop):
            for b in _RTL_HEADER:
                if stop.is_set():
                    return
                _send(conn, bytes([b]))
                time.sleep(0.25)   # < socket 超时，单次 recv 永远不会超时

        with _server(handler) as (port, _stop, _conns):
            c = RtlTcpClient("127.0.0.1", port, idle_timeout=0.4)
            t0 = time.perf_counter()
            ok = c.connect()
            elapsed = time.perf_counter() - t0
            assert elapsed < 1.5, (
                f"慢滴握手 12 字节就要 {12 * 0.25:.1f}s，实测 {elapsed:.2f}s，"
                "握手没有总时限")
            assert ok is False
            assert c.error_category in (
                link_state.CATEGORY_TIMEOUT, link_state.CATEGORY_HANDSHAKE)
            c.close()


# ---------------------------------------------------------------------------
# 2. SpyServer
# ---------------------------------------------------------------------------
class TestSpyServerFailureModes:
    def test_connect_refused_is_immediate_and_actionable(self):
        port = _free_port()
        be = SpyServerBackend("127.0.0.1", port, idle_timeout=0.4)
        t0 = time.perf_counter()
        assert be.connect() is False
        assert time.perf_counter() - t0 < 1.5
        assert be.status.error_category in (
            link_state.CATEGORY_REFUSED, link_state.CATEGORY_TIMEOUT)
        assert "连接失败" in be.status.error
        assert be.read_samples(64) is None

    def test_http_service_on_port_is_rejected(self):
        """端口被 HTTP 服务占着：1024 字节里没有 key=value → 拒绝。"""
        def handler(conn, idx, stop):
            body = (b"HTTP/1.1 400 Bad Request\r\nServer: nginx\r\n"
                    b"Content-Length: 0\r\n\r\n")
            _send(conn, (body + b"\0" * 1024)[:1024])
            time.sleep(0.2)

        with _server(handler) as (port, _stop, _conns):
            c = SpyServerClient("127.0.0.1", port, timeout=0.4)
            t0 = time.perf_counter()
            with pytest.raises(SpyServerError):
                c.start()
            assert time.perf_counter() - t0 < 2.0
            assert c.state == link_state.STATE_FAILED
            assert c.error_category == link_state.CATEGORY_HANDSHAKE

    def test_binary_handshake_with_stray_equals_is_rejected(self):
        """结构不符：二进制/新协议头里碰巧有 '='，不许当成 SpyServer 配置串。"""
        def handler(conn, idx, stop):
            blob = bytes(range(1, 256)) * 4        # 含 0x3D '='，无 \x00 分隔
            _send(conn, (blob + b"\0" * 1024)[:1024])
            time.sleep(0.2)

        with _server(handler) as (port, _stop, _conns):
            c = SpyServerClient("127.0.0.1", port, timeout=0.4)
            with pytest.raises(SpyServerError):
                c.start()
            assert c.state == link_state.STATE_FAILED
            assert c.error_category == link_state.CATEGORY_HANDSHAKE
            assert c.server_info == {} or not c.connected

    def test_partial_handshake_reports_bytes_received(self):
        """半包：1024 字节配置串只到 300 字节就 FIN，错误里要写清收到多少。"""
        def handler(conn, idx, stop):
            _send(conn, (b"Name=Partial\0" * 24)[:300])   # 300 字节 < 1024
            time.sleep(0.05)
            conn.close()

        with _server(handler) as (port, _stop, _conns):
            c = SpyServerClient("127.0.0.1", port, timeout=0.4)
            with pytest.raises(SpyServerError):
                c.start()
            assert c.error_category == link_state.CATEGORY_HANDSHAKE
            assert "/1024" in c.error
            got = c.error.split("/1024")[0].split("（")[-1]
            assert got.strip() != "0", (
                f"半包错误把已收到的字节数报成了 0：{c.error}")

    def test_silence_after_handshake_goes_stale_then_hard_disconnect(self):
        def handler(conn, idx, stop):
            _send(conn, _SPY_HANDSHAKE)
            stop.wait(3.0)

        with _server(handler) as (port, _stop, _conns):
            c = SpyServerClient("127.0.0.1", port, timeout=0.4)
            c.start()
            t0 = time.perf_counter()
            states = []
            while time.perf_counter() - t0 < 3.0:
                try:
                    c.read_samples(64)
                except SpyServerError:
                    states.append(c.state)
                    if c.state == link_state.STATE_DISCONNECTED:
                        break
                else:
                    states.append(c.state)
            assert link_state.STATE_STALE in states, f"没有 stale 阶段：{states}"
            assert c.state == link_state.STATE_DISCONNECTED
            assert c.error_category == link_state.CATEGORY_IDLE
            assert c._sock is None

    def test_half_frame_then_close_raises_not_zero_iq(self):
        """中途断流且帧不完整：抛错，不许返回半帧/零数组。"""
        def handler(conn, idx, stop):
            _send(conn, _SPY_HANDSHAKE + b"\x01\x02\x03")
            time.sleep(0.05)
            conn.close()

        with _server(handler) as (port, _stop, _conns):
            c = SpyServerClient("127.0.0.1", port, timeout=0.4)
            c.start()
            with pytest.raises(SpyServerError):
                c.read_samples(16)
            assert c.state == link_state.STATE_DISCONNECTED
            assert c.error_category == link_state.CATEGORY_EOF

    def test_slow_drip_handshake_cannot_hold_connect_forever(self):
        """慢滴握手：对端每 0.3s 一字节且永不主动停，握手仍须在总时限内失败。

        对端由本测试通过 ``stop`` 控制，没有「发完 1024 字节就结束」这种自然
        终点，所以 connect 在预算内返回只可能来自握手总时限（逐字节 recv 各自
        0.4s 超时，慢滴每 0.3s 一字节恰好绕过它）。实现若退化成只靠逐字节
        超时，这里会在预算处失败并报出已收字节数，不会挂死。
        """
        result = {}
        budget = 2.0

        def handler(conn, idx, stop):
            sent = 0
            while not stop.is_set():          # 与测试等待赛跑，自己不会结束
                try:
                    _send(conn, bytes([_SPY_HANDSHAKE[sent % len(_SPY_HANDSHAKE)]]))
                except OSError:
                    return
                sent += 1
                time.sleep(0.3)               # < 0.4s 单次 recv 超时

        with _server(handler) as (port, stop, _conns):
            c = SpyServerClient("127.0.0.1", port, timeout=0.4)

            def run():
                try:
                    c.start()
                    result["ok"] = True
                except Exception as e:          # noqa: BLE001 - 记录现象
                    result["error"] = e

            th = threading.Thread(target=run, daemon=True)
            t0 = time.perf_counter()
            th.start()
            th.join(budget)
            elapsed = time.perf_counter() - t0
            finished = not th.is_alive()
            reached = c._last_recv_bytes
            stop.set()
            assert finished, (
                f"connect 在预算 {budget:.1f}s 内没有返回（已收 {reached}/1024 "
                f"字节，耗时 {elapsed:.1f}s）：握手缺少总时限，被慢滴对端拖住")
            assert "error" in result, "慢滴握手不该连成功"
            assert c.error_category in (
                link_state.CATEGORY_TIMEOUT, link_state.CATEGORY_HANDSHAKE)
            assert 0 < reached < 1024, (
                f"握手应在读满 1024 字节前被总时限截断，实际收到 {reached} 字节")


# ---------------------------------------------------------------------------
# 3. 重复重连：只允许一个监控线程
# ---------------------------------------------------------------------------
class TestDuplicateReconnect:
    def test_start_twice_keeps_single_monitor_thread(self):
        from mbdsdr_ai.reconnection import ReconnectionManager

        class _Backend:
            def get_status(self):
                class S:
                    connected = False
                    error = ""
                return S()

            def connect(self):
                return False

        mgr = ReconnectionManager(max_attempts=1, poll_interval=0.05)
        backend = _Backend()
        try:
            mgr.start(backend)
            mgr.start(backend)
            alive = [t for t in threading.enumerate()
                     if t.name == "reconnection-manager" and t.is_alive()]
            assert len(alive) == 1, f"重复 start 留下 {len(alive)} 个监控线程"
        finally:
            mgr.stop()
