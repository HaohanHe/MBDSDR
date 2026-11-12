# SPDX-License-Identifier: MIT
"""接入层健壮性：网络源中断分类、状态一致性、重连语义、热插拔与独占。

本文件全部使用**可注入的假源/假设备**，不依赖真机、不依赖真实网络：

- rtl_tcp / SpyServer：本机 loopback TCP 假服务，按脚本制造
  "拒绝连接 / 握手不对 / 半开 / 中途掉线 / 长时间无数据 / RST 被踢"；
- 数据健康度：注入假时钟，验证"状态与真实数据流一致"；
- 重连：假后端按次数失败，验证 retrying/gave_up/恢复 的真实状态；
- USB 热插拔/独占：复用 ``tests/fake_rtl.FakeRtlSdr``（仅测试用双棒）。

硬纪律：不打开真实声卡（MBDSDR_NULL_AUDIO=1 由 cpp/CMake 之外的测试环境注入）、
不打开真实 USB 设备、不伪造 IQ 数据。
"""

from __future__ import annotations

import logging
import socket
import struct
import sys
import threading
import time
import types
from contextlib import contextmanager
from typing import Optional

import numpy as np
import pytest

from mbdsdr_ai import error_handler, link_state
from mbdsdr_ai.reconnection import (
    ReconnectionManager,
    STATE_CONNECTED,
    STATE_DISCONNECTED,
    STATE_GAVE_UP,
    STATE_RETRYING,
    STATE_STOPPED,
)
from mbdsdr_ai.rtltcp_client import RtlTcpBackend, RtlTcpClient
from mbdsdr_ai.sdr_backend import RTLSDRBackend
from mbdsdr_ai.spyserver_client import (
    SpyServerBackend,
    SpyServerClient,
    SpyServerError,
)
from fake_rtl import FakeRtlSdr, fake_rtlsdr_package  # tests/ 专属双棒，绝不进生产路径


# ═══════════════════════════════════════════════════════════════════════
# 通用夹具
# ═══════════════════════════════════════════════════════════════════════

_RTL_HEADER = b"RTL0" + struct.pack(">II", 5, 29)   # tuner_type=5(R820T), gain_count=29
_SPY_HANDSHAKE = (
    b"Name=FakeSpyServer\0SampleRate=2048000\0"
    b"Version=0\0" + b"\0" * (1024 - len(b"Name=FakeSpyServer\0SampleRate=2048000\0Version=0\0"))
)[:1024]


def _iq_bytes(n_samples: int, seed: int = 1) -> bytes:
    """构造确定性的 uint8 IQ 字节（仅用于假源，不是伪硬件数据）。"""
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=n_samples * 2, dtype=np.uint8).tobytes()


@contextmanager
def _tcp_server(handler):
    """启动一个 loopback 假 TCP 服务，yield (port, stop_event, thread)。

    handler(conn, stop) 在下述线程内执行；测试结束时 stop 置位并关闭监听。
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = int(srv.getsockname()[1])
    stop = threading.Event()

    def run():
        conn = None
        try:
            srv.settimeout(3.0)
            conn, _ = srv.accept()
        except OSError:
            return
        try:
            handler(conn, stop)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    t = threading.Thread(target=run, name="fake-tcp-server", daemon=True)
    t.start()
    try:
        yield port, stop, t
    finally:
        stop.set()
        try:
            srv.close()
        except OSError:
            pass
        t.join(timeout=1.5)


def _wait_until(pred, timeout: float = 3.0, interval: float = 0.02) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(interval)
    return bool(pred())


def _find_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


# ═══════════════════════════════════════════════════════════════════════
# 1. rtl_tcp 错误分类（每种中断都可区分、都有下一步建议）
# ═══════════════════════════════════════════════════════════════════════

class TestRtlTcpErrorTaxonomy:
    def test_connection_refused_is_distinguishable(self):
        port = _find_free_port()   # 已关闭的端口 → 拒绝连接
        c = RtlTcpClient("127.0.0.1", port)
        assert c.connect() is False
        assert c.state == link_state.STATE_FAILED
        assert c.error_category == link_state.CATEGORY_REFUSED
        assert "连接失败" in c.error and str(port) in c.error
        assert c.suggestion and "启动" in c.suggestion
        assert c.connected is False

    def test_wrong_magic_is_handshake_failure_not_online(self, caplog):
        def handler(conn, stop):
            conn.sendall(b"XXXX" + struct.pack(">II", 5, 29))
            stop.wait(0.2)

        with _tcp_server(handler) as (port, _stop, _t):
            c = RtlTcpClient("127.0.0.1", port)
            assert c.connect() is False
            assert c.state == link_state.STATE_FAILED
            assert c.error_category == link_state.CATEGORY_HANDSHAKE
            assert "不是 rtl_tcp" in c.error
            assert c.connected is False

    def test_half_open_handshake_is_not_silently_online(self):
        def handler(conn, stop):
            conn.sendall(b"RTL")   # 只发 3/12 字节就挂起，模拟半开
            stop.wait(0.3)

        with _tcp_server(handler) as (port, _stop, _t):
            c = RtlTcpClient("127.0.0.1", port, idle_timeout=0.3)
            assert c.connect() is False
            assert c.state == link_state.STATE_FAILED
            assert c.error_category in (
                link_state.CATEGORY_HANDSHAKE, link_state.CATEGORY_TIMEOUT)
            assert "不完整" in c.error or "握手超时" in c.error
            assert c.connected is False

    def test_rejected_by_peer_is_auth_or_refusal_category(self):
        cat, msg, sug = link_state.classify_handshake_reason(
            "Access denied by server (auth)")
        assert cat == link_state.CATEGORY_AUTH
        assert "连接失败" in msg
        assert "访问控制" in sug or "放行" in sug


# ═══════════════════════════════════════════════════════════════════════
# 2. rtl_tcp 链路转换：stale（心跳缺失）/ 掉线 / 恢复
# ═══════════════════════════════════════════════════════════════════════

class TestRtlTcpLinkTransitions:
    def test_idle_becomes_stale_then_recovers_when_data_resumes(self):
        resume = threading.Event()

        def handler(conn, stop):
            conn.sendall(_RTL_HEADER)
            # 先静默，直到测试读到 stale
            while not stop.is_set() and not resume.is_set():
                time.sleep(0.02)
            while not stop.is_set():
                conn.sendall(_iq_bytes(1024, seed=7))
                time.sleep(0.01)

        with _tcp_server(handler) as (port, stop, _t):
            c = RtlTcpClient("127.0.0.1", port, idle_timeout=0.3)
            assert c.connect() is True
            assert c.state == link_state.STATE_CONNECTED

            t0 = time.time()
            assert c.read_samples(1024) is None
            # 超时而不是无限阻塞；连接仍在但明确降级
            assert time.time() - t0 < 2.0
            assert c.state == link_state.STATE_STALE
            assert c.connected is True          # socket 还连着，不谎报断开
            assert "心跳缺失" in c.error
            assert c.error_category == link_state.CATEGORY_IDLE

            resume.set()
            samples = c.read_samples(1024)
            assert samples is not None and len(samples) == 1024
            assert c.state == link_state.STATE_CONNECTED
            assert c.error == ""
            stop.set()

    def test_mid_stream_drop_sets_disconnected_and_not_connected(self):
        def handler(conn, stop):
            conn.sendall(_RTL_HEADER + _iq_bytes(64, seed=3))
            stop.wait(0.05)
            conn.close()   # 对端中途掉线（FIN）

        with _tcp_server(handler) as (port, stop, _t):
            c = RtlTcpClient("127.0.0.1", port, idle_timeout=0.5)
            assert c.connect() is True
            out = []
            for _ in range(20):
                s = c.read_samples(8)
                if s is None:
                    break
                out.append(len(s))
            assert sum(out) > 0, "掉线前应能读到已到达的数据"
            assert c.state == link_state.STATE_DISCONNECTED
            assert c.connected is False
            assert c.error_category == link_state.CATEGORY_EOF
            assert "对端关闭" in c.error

    def test_peer_reset_is_disconnected_with_reset_or_unknown_category(self):
        connected = threading.Event()

        def handler(conn, stop):
            conn.sendall(_RTL_HEADER)
            # 等客户端握手成功后再吐一点数据并 RST，避免把握手包也丢掉
            connected.wait(2.0)
            conn.sendall(_iq_bytes(32, seed=5))
            time.sleep(0.02)
            # SO_LINGER(0) → close 发 RST，模拟被服务端踢出
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                            struct.pack("ii", 1, 0))
            conn.close()

        with _tcp_server(handler) as (port, stop, _t):
            c = RtlTcpClient("127.0.0.1", port, idle_timeout=0.5)
            assert c.connect() is True
            connected.set()
            for _ in range(12):
                if c.read_samples(8) is None:
                    break
                time.sleep(0.01)
            assert c.state == link_state.STATE_DISCONNECTED
            assert c.connected is False
            assert c.error_category in (
                link_state.CATEGORY_RESET, link_state.CATEGORY_UNKNOWN)
            assert c.error

    def test_client_data_health_matches_real_bytes(self):
        def handler(conn, stop):
            conn.sendall(_RTL_HEADER)
            while not stop.is_set():
                conn.sendall(_iq_bytes(2048, seed=11))
                time.sleep(0.01)

        with _tcp_server(handler) as (port, stop, _t):
            c = RtlTcpClient("127.0.0.1", port, idle_timeout=0.5)
            assert c.connect() is True
            total = 0
            for _ in range(3):
                s = c.read_samples(2048)
                assert s is not None
                total += len(s)
            h = c.data_health()
            assert h["state"] == link_state.STATE_CONNECTED
            assert h["connected"] is True
            assert h["samples_total"] == total
            assert h["bytes_total"] == total * 2
            assert h["last_data_age_s"] is not None
            stop.set()


# ═══════════════════════════════════════════════════════════════════════
# 3. "状态与真实数据流一致"（后端级）
# ═══════════════════════════════════════════════════════════════════════

class TestBackendStatusMatchesStream:
    def test_rtl_tcp_backend_status_only_reflects_real_data(self):
        def handler(conn, stop):
            conn.sendall(_RTL_HEADER)
            while not stop.is_set():
                conn.sendall(_iq_bytes(4096, seed=13))
                time.sleep(0.005)

        with _tcp_server(handler) as (port, stop, _t):
            be = RtlTcpBackend("127.0.0.1", port)
            assert be.connect() is True
            assert be.status.link_state == "connected"
            assert be.status.connected is True

            got = 0
            deadline = time.time() + 2.0
            while got < 8192 and time.time() < deadline:
                s = be.read_samples(4096)
                if s is not None:
                    got += len(s)
                    if s is not None:
                        be._ring_reader.clear()
                time.sleep(0.01)
            assert got > 0, "假源真的发了数据，后端必须真的读到"

            st = be.get_status()
            h = be.data_health()
            assert st.samples_read >= got
            assert h["samples_total"] == st.samples_read   # 同一份真实计数
            assert h["state"] == "connected"
            assert h["connected"] is True
            assert h["buffer_fill"] >= 0
            assert h["dropped_samples"] >= 0

            # 对端掉线后：状态必须翻成 disconnected，且不再假装在收
            stop.set()
            assert _wait_until(lambda: be.status.connected is False, 3.0)
            h2 = be.data_health()
            assert h2["connected"] is False
            assert h2["state"] == "disconnected"
            assert h2["message"]
            cached = be.read_samples(4096)
            assert cached is None
            be.disconnect()
            assert be._ring_reader is None
            assert be.status.link_state == "disconnected"

    def test_stale_is_exposed_by_backend_without_faking_offline(self):
        resume = threading.Event()

        def handler(conn, stop):
            conn.sendall(_RTL_HEADER)
            while not stop.is_set() and not resume.is_set():
                time.sleep(0.02)
            while not stop.is_set():
                conn.sendall(_iq_bytes(1024, seed=17))
                time.sleep(0.01)

        with _tcp_server(handler) as (port, stop, _t):
            be = RtlTcpBackend("127.0.0.1", port, idle_timeout=0.6)
            assert be.connect() is True
            # 该客户端从连上起就用 0.6s 空闲阈值：测试在 2 秒内能看到 stale
            assert _wait_until(
                lambda: be.status.link_state == "stale", 2.0)
            assert be.status.connected is True       # 连接还在
            assert "心跳缺失" in be.status.error
            assert be.status.error_category == link_state.CATEGORY_IDLE

            resume.set()
            assert _wait_until(
                lambda: be.status.link_state == "connected", 2.0)
            assert be.status.error == ""
            stop.set()
            be.disconnect()
            assert be._ring_reader is None


# ═══════════════════════════════════════════════════════════════════════
# 4. 数据健康度：丢样/缺口/速率不匹配（假时钟）
# ═══════════════════════════════════════════════════════════════════════

class TestDataHealthTracker:
    def test_dropped_samples_and_rate_ratio_from_injected_clock(self):
        now = [100.0]
        tr = link_state.DataHealthTracker(
            sample_rate_hz=1_000_000, stale_after=0.5,
            gap_tolerance_s=0.005, clock=lambda: now[0])
        tr.on_connect(now=now[0])

        # 理想情况：0.1s 内送达 100k 样本 → 无丢样
        now[0] += 0.1
        tr.on_data(100_000, n_bytes=200_000, now=now[0])
        # 网络变差：0.1s 只送达 30k → 应到 100k，缺 70k
        now[0] += 0.1
        tr.on_data(30_000, n_bytes=60_000, now=now[0])

        h = tr.snapshot(state=link_state.STATE_CONNECTED, now=now[0])
        assert h["samples_total"] == 130_000
        assert h["bytes_total"] == 260_000
        assert h["dropped_samples"] > 60_000
        assert h["drop_ratio"] > 0.3
        assert h["observed_rate_hz"] == pytest.approx(650_000, rel=0.2)
        assert h["rate_ratio"] < 0.8, "实际吞吐明显低于期望采样率，必须暴露为不匹配"

    def test_stale_detection_and_gap_event(self):
        now = [0.0]
        tr = link_state.DataHealthTracker(
            sample_rate_hz=1000, stale_after=0.5, clock=lambda: now[0])
        tr.on_connect(now=0.0)
        now[0] = 0.6
        assert tr.is_stale(now=now[0]) is True
        assert tr.age_s(now=now[0]) == pytest.approx(0.6)
        tr.on_data(1000, now=now[0])
        h = tr.snapshot(now=now[0])
        assert h["gap_events"] >= 1, "跨越一次心跳缺失必须记一次空洞事件"
        assert h["stale"] is False
        assert h["last_data_age_s"] == pytest.approx(0.0)


# ═══════════════════════════════════════════════════════════════════════
# 5. SpyServer：握手/静默/掉线/采样率不一致
# ═══════════════════════════════════════════════════════════════════════

class TestSpyServerResilience:
    def test_handshake_failure_is_explicit(self):
        def handler(conn, stop):
            conn.sendall(b"\x00" * 1024)   # 无任何 key=value
            stop.wait(0.2)

        with _tcp_server(handler) as (port, stop, _t):
            c = SpyServerClient("127.0.0.1", port, timeout=1.0)
            with pytest.raises(SpyServerError):
                c.start()
            assert c.state == link_state.STATE_FAILED
            assert c.error_category == link_state.CATEGORY_HANDSHAKE
            assert "不是 SpyServer" in c.error
            assert c.connected is False

    def test_silence_after_handshake_is_stale_not_connected(self):
        def handler(conn, stop):
            conn.sendall(_SPY_HANDSHAKE)
            stop.wait(1.0)

        with _tcp_server(handler) as (port, stop, _t):
            c = SpyServerClient("127.0.0.1", port, timeout=0.3)
            c.start()
            assert c.state == link_state.STATE_CONNECTED
            with pytest.raises(SpyServerError):
                c.read_samples(16)
            assert c.state == link_state.STATE_STALE
            assert c.connected is True
            assert c.error_category == link_state.CATEGORY_IDLE
            assert "心跳缺失" in c.error
            stop.set()

    def test_mid_stream_drop_is_disconnected(self):
        def handler(conn, stop):
            conn.sendall(_SPY_HANDSHAKE + _iq_bytes(32, seed=19))
            stop.wait(0.05)
            conn.close()

        with _tcp_server(handler) as (port, stop, _t):
            c = SpyServerClient("127.0.0.1", port, timeout=0.5)
            c.start()
            for _ in range(8):
                try:
                    c.read_samples(32)
                except SpyServerError:
                    break
                time.sleep(0.02)
            assert c.state == link_state.STATE_DISCONNECTED
            assert c.connected is False
            stop.set()

    def test_sample_rate_mismatch_is_visible(self):
        def handler(conn, stop):
            conn.sendall(_SPY_HANDSHAKE)
            while not stop.is_set():
                try:
                    conn.settimeout(0.2)
                    conn.recv(4096)   # 丢弃客户端命令
                except OSError:
                    pass

        with _tcp_server(handler) as (port, stop, _t):
            c = SpyServerClient("127.0.0.1", port, timeout=0.5)
            c.start()
            c.set_sample_rate(1_024_000)   # 服务端声明 2.048M，请求 1.024M
            h = c.data_health()
            assert h["server_sample_rate_hz"] == 2_048_000
            assert h["requested_sample_rate_hz"] == 1_024_000
            assert h["sample_rate_mismatch"] is True
            assert h["state"] == link_state.STATE_CONNECTED
            stop.set()

    def test_spyserver_backend_status_matches_stream(self):
        def handler(conn, stop):
            conn.sendall(_SPY_HANDSHAKE)
            while not stop.is_set():
                conn.sendall(_iq_bytes(4096, seed=23))
                time.sleep(0.005)

        with _tcp_server(handler) as (port, stop, _t):
            be = SpyServerBackend("127.0.0.1", port)
            assert be.connect() is True
            assert be.status.link_state == "connected"
            got = 0
            deadline = time.time() + 2.0
            while got < 8192 and time.time() < deadline:
                s = be.read_samples(4096)
                if s is not None:
                    got += len(s)
                    be._ring_reader.clear()
                time.sleep(0.01)
            assert got > 0
            h = be.data_health()
            assert h["samples_total"] == be.get_status().samples_read
            assert h["connected"] is True
            assert h["server_sample_rate_hz"] == 2_048_000

            stop.set()
            assert _wait_until(lambda: be.status.connected is False, 3.0)
            h2 = be.data_health()
            assert h2["connected"] is False
            assert h2["message"]
            be.disconnect()
            assert be._ring_reader is None


# ═══════════════════════════════════════════════════════════════════════
# 6. 重连语义：状态、上限、退避、放弃建议、恢复
# ═══════════════════════════════════════════════════════════════════════

class _FakeStatus:
    def __init__(self):
        self.connected = False
        self.error = ""
        self.link_state = "disconnected"
        self.frequency_hz = 98_000_000.0
        self.sample_rate_hz = 2_048_000.0
        self.gain_db = 20.0
        self.reconnect_count = 0


class _FlakyBackend:
    def __init__(self, fail_remaining: int):
        self.status = _FakeStatus()
        self.fail_remaining = int(fail_remaining)
        self.connect_calls = 0

    def get_status(self):
        return self.status

    def is_connected(self):
        return self.status.connected

    def connect(self):
        self.connect_calls += 1
        if self.fail_remaining > 0:
            self.fail_remaining -= 1
            self.status.connected = False
            self.status.error = f"连接失败: 第 {self.connect_calls} 次被拒绝"
            return False
        self.status.connected = True
        self.status.error = ""
        self.status.link_state = "connected"
        return True


class TestReconnectionStateMachine:
    def test_retry_then_connected_exposes_real_counters(self):
        be = _FlakyBackend(fail_remaining=3)
        mgr = ReconnectionManager(
            base_delay=0.02, max_delay=0.05,
            max_attempts=10, poll_interval=0.02, source_kind="network")
        mgr.start(be)
        try:
            assert _wait_until(lambda: be.status.connected, 3.0)
            snap = mgr.snapshot()
            assert snap["state"] == STATE_CONNECTED
            # 前 3 次 connect 失败，第 4 次成功
            assert snap["attempts_total"] == 4
            assert snap["reconnects_total"] == 1
            assert snap["last_error"] == ""
            assert be.status.reconnect_count == 1
        finally:
            mgr.stop()
        assert mgr.state == STATE_STOPPED

    def test_give_up_after_limit_has_actionable_next_step(self, caplog):
        caplog.set_level(logging.INFO, logger="mbdsdr_ai.reconnection")
        be = _FlakyBackend(fail_remaining=99)
        mgr = ReconnectionManager(
            base_delay=0.01, max_delay=0.02,
            max_attempts=6, poll_interval=0.02, source_kind="network")
        mgr.start(be)
        try:
            assert _wait_until(lambda: mgr.state == STATE_GAVE_UP, 3.0)
            snap = mgr.snapshot()
            assert snap["gave_up"] is True
            assert snap["max_attempts"] == 6
            assert snap["suggestion"], "放弃时必须给出可操作下一步"
            assert "rtl_tcp" in snap["suggestion"]
            assert snap["last_error"], "放弃时要保留最后一次真实失败原因"
            # 日志抽稀：6 次尝试不应打印 6 条"次重连失败"
            noisy = [r for r in caplog.records if "次重连失败" in r.getMessage()]
            assert len(noisy) <= 3, f"重试日志疑似刷屏: {len(noisy)} 条"
        finally:
            mgr.stop()

    def test_give_up_does_not_auto_retry_again(self):
        """放弃后必须停手：不许每隔一段时间又偷偷重试一轮。"""
        be = _FlakyBackend(fail_remaining=99)
        mgr = ReconnectionManager(
            base_delay=0.01, max_delay=0.02,
            max_attempts=2, poll_interval=0.02, source_kind="network")
        mgr.start(be)
        try:
            assert _wait_until(lambda: mgr.state == STATE_GAVE_UP, 3.0)
            calls_at_give_up = be.connect_calls
            time.sleep(0.4)   # 超过几倍 poll_interval
            assert be.connect_calls == calls_at_give_up, (
                "放弃后仍在自动重试：用户会永远看到设备在刷重连")
            assert mgr.state == STATE_GAVE_UP
        finally:
            mgr.stop()

    def test_recovery_after_give_up_when_device_returns(self):
        be = _FlakyBackend(fail_remaining=99)
        mgr = ReconnectionManager(
            base_delay=0.01, max_delay=0.02,
            max_attempts=3, poll_interval=0.02, source_kind="usb")
        mgr.start(be)
        try:
            assert _wait_until(lambda: mgr.state == STATE_GAVE_UP, 3.0)
            # 放弃后不自动重试；用户重新插回设备并点"重连"才恢复
            be.fail_remaining = 0
            mgr.retry_now(be)
            assert _wait_until(lambda: mgr.state == STATE_CONNECTED, 3.0)
            snap = mgr.snapshot()
            assert snap["gave_up"] is False
            assert snap["reconnects_total"] >= 1
            assert snap["suggestion"] == ""
        finally:
            mgr.stop()

    def test_disconnect_transition_reports_disconnected_then_retrying(self):
        be = _FlakyBackend(fail_remaining=0)
        be.status.connected = True
        mgr = ReconnectionManager(
            base_delay=0.05, max_delay=0.1,
            max_attempts=2, poll_interval=0.02, source_kind="network")
        mgr.start(be)
        try:
            assert _wait_until(lambda: mgr.state == STATE_CONNECTED, 1.0)
            be.status.connected = False
            be.status.error = "读取 IQ 失败: 连接已被对端关闭"
            be.fail_remaining = 2
            assert _wait_until(
                lambda: mgr.state in (STATE_RETRYING, STATE_GAVE_UP), 2.0)
            assert mgr.snapshot()["last_error"]
        finally:
            mgr.stop()


# ═══════════════════════════════════════════════════════════════════════
# 7. 设备独占与热插拔
# ═══════════════════════════════════════════════════════════════════════

class TestDeviceExclusivityAndHotplug:
    def test_usb_open_error_minus3_maps_to_busy_not_missing(self):
        busy = error_handler.classify_connect_failure(
            OSError("usb_open error -3"), "usb_open error -3")
        assert isinstance(busy, error_handler.DeviceBusyError)
        # 「设备不在」必须落到另一个分支：否则「任何错误都返回 Busy」也会绿
        missing = error_handler.classify_connect_failure(
            OSError("No devices found"), "No devices found")
        assert isinstance(missing, error_handler.USBDisconnectedError)
        assert not isinstance(missing, error_handler.DeviceBusyError)

    def test_probe_occupant_is_honest_when_it_cannot_see(self):
        unknown = error_handler.probe_occupant()
        assert unknown["detected"] is False
        assert "无法探测" in unknown["reason"]

        found = error_handler.probe_occupant(
            ["explorer.exe", "SDRSharp.exe", "gqrx", "python.exe"])
        assert found["detected"] is True
        assert any("SDRSharp" in p for p in found["processes"])
        assert any("gqrx" in p for p in found["processes"])

    def test_busy_message_is_explicit_and_handle_is_released(self, monkeypatch):
        import mbdsdr_ai.sdr_backend as sb

        created = []

        class _BusyRtlSdr:
            def __init__(self, idx=0):
                created.append(self)
                raise OSError("usb_open error -3: Unable to open device")

        with fake_rtlsdr_package(_BusyRtlSdr):
            monkeypatch.setattr(
                RTLSDRBackend, "_probe_tuner_type",
                staticmethod(lambda idx: 5), raising=False)
            be = RTLSDRBackend(device_index=0)
            assert be.connect() is False
            assert be.status.link_state == "failed"
            assert be.status.error_category == "busy"
            assert "设备被占用" in be.status.error
            assert be.status.connected is False
            assert be._sdr is None, "打开失败的句柄必须被释放，不能残留"

    def test_hotplug_unplug_detected_then_reconnect_without_stale_threads(
            self, monkeypatch):
        import mbdsdr_ai.sdr_backend as sb

        devices = []

        class _UnplugRtlSdr(FakeRtlSdr):
            def __init__(self, device_index=0, unplug_after=3):
                super().__init__(device_index=device_index)
                self._unplug_after = unplug_after
                self._reads = 0
                self.unplugged = False

            def read_samples(self, num_samples):
                if self._closed:
                    raise OSError("FakeRtlSdr: device closed")
                self._reads += 1
                if self.unplugged or self._reads > self._unplug_after:
                    self.unplugged = True
                    raise OSError("LIBUSB_ERROR_NO_DEVICE: device unplugged")
                return super().read_samples(num_samples)

        class _Factory:
            """每次 connect 返回一支新棒；第一支在若干次读之后"被拔出"。"""

            def __init__(self):
                self.count = 0

            def __call__(self, idx=0):
                self.count += 1
                first = self.count == 1
                dev = _UnplugRtlSdr(
                    device_index=idx, unplug_after=3 if first else 10**9)
                devices.append(dev)
                return dev

        with fake_rtlsdr_package(_Factory()):
            monkeypatch.setattr(
                RTLSDRBackend, "_probe_tuner_type",
                staticmethod(lambda idx: 5), raising=False)
            be = RTLSDRBackend(device_index=0)
            assert be.connect() is True
            assert be.status.connected is True
            first = devices[0]

            # 模拟拔棒：生产者读到 LIBUSB_ERROR_NO_DEVICE → 必须诚实断开
            assert _wait_until(lambda: be.status.connected is False, 3.0)
            assert be.status.link_state == "disconnected"
            assert "读设备失败" in be.status.error
            assert be.status.error_category == "disconnected"

            # 重新插入 → connect() 先释放旧句柄，再开新棒，只留一个生产者线程
            old_rings = [
                t for t in threading.enumerate()
                if t.name == "mbdsdr-ring-producer" and t.is_alive()
            ]
            assert len(old_rings) <= 1
            assert be.connect() is True
            assert first.closed is True, "旧句柄必须被关闭，不能残留"
            assert len(devices) == 2
            assert be._sdr is devices[1]
            assert be.status.connected is True
            assert be.status.link_state == "connected"
            assert be.status.error == ""

            alive = [
                t for t in threading.enumerate()
                if t.name == "mbdsdr-ring-producer" and t.is_alive()
            ]
            assert len(alive) == 1, f"重连后不应残留旧生产者线程，实际 {len(alive)} 个"
            # 新棒能真的出数据（状态与真实数据流一致）
            s = be.read_samples(4096)
            assert s is not None and len(s) == 4096
            assert be.get_status().samples_read >= 4096
            be.disconnect()
            assert be._ring_reader is None
            assert be.status.link_state == "disconnected"
