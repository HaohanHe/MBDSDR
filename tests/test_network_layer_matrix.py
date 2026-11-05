# SPDX-License-Identifier: MIT
"""接入层实测矩阵：rtl_tcp / SpyServer 四类中断的真实发现时间。

全部用本机 loopback 假服务 / 假进程 / 黑洞代理，不连公网、不碰真硬件：

  1. 正常断开（对端 FIN）            → 读错误（EOF）
  2. 服务端进程被杀（TerminateProcess）→ 读错误（EOF/RST）
  3. 网络闪断（源变慢 + 停顿）        → 心跳超时（stale）+ 速率/缺口指标
  4. 半开连接（服务端已死、TCP 仍被保持）→ 心跳超时（stale）→ 硬判断开

矩阵每行都实测：多久发现、发现方式、恢复后首帧、静噪链是否重建、
句柄/线程是否泄漏。数值由 ``-s`` 打印的 ``[matrix]`` 行给出，
``docs/network-layer.md`` 记录同一次实测的结果。

纪律：临时假服务端进程用 ``python -c`` 内联代码，不落脚本文件；
失败也必须回收进程 / socket / 生产者线程。
"""

from __future__ import annotations

import inspect
import os
import random
import socket
import struct
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from typing import Callable, Optional

import numpy as np
import pytest

from mbdsdr_ai import link_state
from mbdsdr_ai.reconnection import (
    DEFAULT_MAX_ATTEMPTS,
    ReconnectionManager,
    compute_backoff,
)
from mbdsdr_ai.rtltcp_client import RtlTcpBackend, RtlTcpClient
from mbdsdr_ai.sdr_backend import _ThreadedRingReader
from mbdsdr_ai.spyserver_client import SpyServerBackend, SpyServerClient

_RTL_HEADER = b"RTL0" + struct.pack(">II", 5, 29)
_SPY_KEYS = b"Name=FakeSpyServer\0SampleRate=2048000\0Version=0\0"
_SPY_HANDSHAKE = (_SPY_KEYS + b"\0" * (1024 - len(_SPY_KEYS)))[:1024]


def _iq_bytes(n_samples: int, seed: int = 1) -> bytes:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=n_samples * 2, dtype=np.uint8).tobytes()


def _find_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def _measure(pred: Callable[[], bool], timeout: float = 3.0,
             interval: float = 0.005) -> Optional[float]:
    """返回 pred 首次为真的耗时（秒）；超时返回 None。"""
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        if pred():
            return time.perf_counter() - t0
        time.sleep(interval)
    return None


def _wait_until(pred: Callable[[], bool], timeout: float = 3.0,
                interval: float = 0.01) -> bool:
    return _measure(pred, timeout, interval) is not None


def _ring_threads() -> list:
    return [t for t in threading.enumerate()
            if t.name == "mbdsdr-ring-producer" and t.is_alive()]


def _handles() -> int:
    import psutil
    return int(psutil.Process().num_handles())


def _tcp_sockets() -> int:
    """本进程当前存活的 TCP socket 数（排除 TIME_WAIT 僵尸态）。

    比 ``num_handles()`` 更能说明"socket 句柄泄漏"：两者都含假服务端在
    本进程内的监听/连接，所以基线与终态同一口径可比。
    """
    import psutil
    pid = os.getpid()
    count = 0
    for c in psutil.net_connections(kind="tcp"):
        if c.pid == pid and c.status != psutil.CONN_TIME_WAIT:
            count += 1
    return count


@contextmanager
def _tcp_server_loop(handler: Callable[..., None], accept_count: int = 1):
    """loopback 假服务：按顺序接受 ``accept_count`` 个连接。

    handler(conn, index, stop)：index 从 0 开始，测试可用它区分第几次重连。
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = int(srv.getsockname()[1])
    stop = threading.Event()

    workers: list = []

    def _serve(conn, idx: int) -> None:
        try:
            handler(conn, idx, stop)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def run():
        srv.settimeout(4.0)
        for idx in range(accept_count):
            try:
                conn, _ = srv.accept()
            except OSError:
                break
            # 每个连接独立线程：重连时上一路可能仍在流数据，串行 accept 会把
            # 后续重连晾在 backlog 里（假服务自身的缺陷，不是被测行为）。
            t = threading.Thread(target=_serve, args=(conn, idx),
                                 name=f"fake-tcp-{idx}", daemon=True)
            t.start()
            workers.append(t)

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
        for w in workers:
            w.join(timeout=1.5)


def _stream_forever(conn, header: bytes, stop: threading.Event,
                    chunk: int = 2048, delay: float = 0.005, seed: int = 1):
    conn.sendall(header)
    while not stop.is_set():
        conn.sendall(_iq_bytes(chunk, seed=seed))
        time.sleep(delay)


def _drain(conn) -> None:
    """读掉客户端下发的命令后再关。

    Windows 上如果服务端还有未读入站数据就 close()，内核发的是 RST 而不是
    FIN；"正常断开"要测的是 EOF，所以先把入站数据排空。
    """
    try:
        conn.setblocking(False)
        while True:
            if not conn.recv(4096):
                break
    except (BlockingIOError, OSError):
        pass
    finally:
        try:
            conn.setblocking(True)
        except OSError:
            pass


def _read_frame_until(be, n: int = 4096, timeout: float = 2.0):
    """等到后端真的给出首帧（返回 ndarray 或 None=超时）。"""
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        s = be.read_samples(n)
        if s is not None and len(s) == n:
            return s
        time.sleep(0.01)
    return None


def _assert_frame_is_normal(frame, n: int = 4096):
    """恢复后首帧：真实 ndarray、长度正确、全部有限、幅度在 cu8 换算域内。"""
    assert frame is not None, "恢复后没有收到首帧"
    assert len(frame) == n
    assert frame.dtype == np.complex64
    assert np.all(np.isfinite(frame))
    # cu8 → 每轴 [-1, 1]，复幅度上界 sqrt(2)
    assert float(np.max(np.abs(frame))) <= 2 ** 0.5 + 1e-6
    assert float(np.std(frame)) > 0.0, "首帧不能是常量占位"


def _make_rtl_backend(port: int, idle: float) -> RtlTcpBackend:
    be = RtlTcpBackend("127.0.0.1", port, idle_timeout=idle)
    # 静噪链要按余量口径重建，这里先设一个真实余量值（-50≈6 dB）。
    be.status.squelch_db = -50.0
    return be


# ═══════════════════════════════════════════════════════════════════════
# 1. rtl_tcp 矩阵
# ═══════════════════════════════════════════════════════════════════════

class TestRtlTcpMatrix:
    def test_normal_close_then_reconnect_first_frame_and_chain(self):
        """正常断开：对端 FIN → 读错误（EOF），重连后首帧正常、静噪链全新。"""
        # 首块必须多于生产者一次读取的 8192 样点，否则生产者读不满整块
        # （收到 EOF 即判断开），测试拿不到任何"断开前"的真数据。
        def handler(conn, idx, stop):
            conn.sendall(_RTL_HEADER + _iq_bytes(20_000, seed=31))
            if idx == 0:
                time.sleep(0.1)
                _drain(conn)
                return                     # 排空后返回 → 优雅关闭（FIN）
            _stream_forever(conn, b"", stop, seed=33)

        old_chain = old_squelch = None
        with _tcp_server_loop(handler, accept_count=6) as (port, stop, _t):
            base_handles = _handles()      # 静默基线：还没建立任何客户端连接
            base_socks = _tcp_sockets()
            be = _make_rtl_backend(port, idle=0.5)
            assert be.connect() is True
            frame = _read_frame_until(be, 1024)
            assert frame is not None
            be.read_audio(512)             # 建链（读取链 + BaselineSquelch）
            old_chain = be._receive_chain
            assert old_chain is not None
            old_squelch = old_chain._squelch
            assert old_squelch is not None

            # 服务端已经返回 → 关闭；等待后端诚实翻成 disconnected
            t_detect = _measure(lambda: be.status.connected is False, 3.0)
            assert t_detect is not None, "正常断开后 3s 内仍未发现"
            assert be.status.link_state == link_state.STATE_DISCONNECTED
            assert be.status.error_category == link_state.CATEGORY_EOF
            old_client = be._client
            be.disconnect()
            assert old_client is None or old_client._sock is None, "旧 socket 泄漏"
            assert be._client is None and be._ring_reader is None
            assert be._receive_chain is None, "断开后必须废弃旧接收链"

            # 重连到同一个假服务（第 2 次 accept）
            assert be.connect() is True
            frame2 = _read_frame_until(be)
            _assert_frame_is_normal(frame2)
            be.read_audio(512)
            new_chain = be._receive_chain
            assert new_chain is not None and new_chain is not old_chain
            assert new_chain._squelch is not old_squelch
            assert new_chain._squelch_db == pytest.approx(-50.0)
            print(f"[matrix] rtl_tcp normal-close detect={t_detect:.3f}s "
                  f"via=EOF state={be.status.link_state}")

            # 句柄泄漏：3 轮断开/重连后回基线（生产者线程与 socket 不累积）
            for _ in range(3):
                be.disconnect()
                assert _wait_until(lambda: not _ring_threads(), 2.0)
                assert be.connect() is True
                assert _read_frame_until(be, 512, 2.0) is not None
                assert len(_ring_threads()) == 1
            be.disconnect()
            assert _wait_until(lambda: not _ring_threads(), 2.0)
            assert be._client is None and be._ring_reader is None
            # 让假服务也把它的连接收掉，否则 psutil 数的是**本进程内**服务端
            # 连接句柄，会把正常的路由连接误记成客户端泄漏。
            stop.set()
            time.sleep(0.25)
            end_socks = _tcp_sockets()
            assert end_socks <= base_socks, (
                f"重连 3 轮后存活 TCP socket {base_socks}→{end_socks}（疑似泄漏）")
            print(f"[matrix] rtl_tcp sockets baseline={base_socks} "
                  f"after3cycles={end_socks} "
                  f"proc_handles {base_handles}→{_handles()}")

    def test_server_process_killed_detected_fast(self):
        """服务端进程被杀（TerminateProcess）：读错误，秒级发现。"""
        port = _find_free_port()
        code = (
            "import socket,struct,sys,time\n"
            "p=int(sys.argv[1]); s=socket.socket();"
            "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
            "s.bind(('127.0.0.1',p)); s.listen(1); print('READY',flush=True)\n"
            "c,_=s.accept(); c.sendall(b'RTL0'+struct.pack('>II',5,29))\n"
            "try:\n"
            "  while True:\n"
            "    c.sendall(bytes([0,255,128,127])*1024); time.sleep(0.01)\n"
            "except OSError:\n"
            "  pass\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", code, str(port)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        proc2 = None
        be = None
        try:
            assert proc.stdout.readline().strip() == "READY", "假服务未就绪"
            be = _make_rtl_backend(port, idle=1.0)
            assert be.connect() is True
            frame = _read_frame_until(be, 1024)
            assert frame is not None
            t0 = time.perf_counter()
            proc.kill()                    # Windows TerminateProcess
            proc.wait(timeout=3)
            t_detect = _measure(lambda: be.status.connected is False, 3.0)
            assert t_detect is not None, "进程被杀后 3s 内仍未发现"
            assert be.status.link_state == link_state.STATE_DISCONNECTED
            assert be.status.error_category in (
                link_state.CATEGORY_EOF, link_state.CATEGORY_RESET,
                link_state.CATEGORY_UNKNOWN)
            be.disconnect()
            assert be._client is None and be._ring_reader is None
            print(f"[matrix] rtl_tcp process-kill detect={t_detect:.3f}s "
                  f"via={be.status.error_category}")

            # 同一端口重启服务 → 恢复后首帧正常
            proc2 = subprocess.Popen(
                [sys.executable, "-c", code, str(port)],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            assert proc2.stdout.readline().strip() == "READY", "重启的假服务未就绪"
            assert be.connect() is True
            frame2 = _read_frame_until(be)
            _assert_frame_is_normal(frame2)
            print("[matrix] rtl_tcp after-restart first-frame=ok")
        finally:
            # 必须收掉生产者线程：别的用例会数 mbdsdr-ring-producer 线程
            if be is not None:
                be.disconnect()
            for p in (proc, proc2):
                if p is not None and p.poll() is None:
                    p.kill()
            for p in (proc, proc2):
                if p is not None:
                    try:
                        p.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
                    if p.stdout is not None:
                        p.stdout.close()

    def test_slow_source_and_stall_exposes_stale_gap_and_rate(self):
        """网络闪断：源变慢 + 停顿 → 心跳超时 stale，真实缺口/速率可见。"""
        pause = threading.Event()

        def handler(conn, idx, stop):
            conn.sendall(_RTL_HEADER)
            while not stop.is_set():
                if pause.is_set():
                    time.sleep(0.02)       # 停发（等价于链路黑洞）
                    continue
                # 半速：0.1s 只给 50k 样本（客户端期望 1 MS/s → 实际 0.5）
                conn.sendall(_iq_bytes(50_000, seed=37))
                time.sleep(0.1)

        with _tcp_server_loop(handler, accept_count=1) as (port, stop, _t):
            c = RtlTcpClient("127.0.0.1", port, idle_timeout=0.5)
            assert c.connect() is True
            c.set_sample_rate(1_000_000)   # 只改客户端期望速率（假服务不发命令回执）
            # 先在半速源上真实读 0.4s，让观测吞吐有足够的会话时间
            slow_deadline = time.perf_counter() + 0.4
            while time.perf_counter() < slow_deadline:
                assert c.read_samples(8192) is not None
            pause.set()
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < 2.5:
                if c.read_samples(8192) is None:
                    break
            t_stale = time.perf_counter() - t0
            assert c.state == link_state.STATE_STALE, "停发后必须降级 stale"
            assert t_stale < 2.5, "心跳缺失必须由 idle_timeout 兜底，不能永久阻塞"
            assert c.connected is True     # socket 还在：不谎报断开
            assert c.error_category == link_state.CATEGORY_IDLE
            assert "心跳缺失" in c.error
            print(f"[matrix] rtl_tcp stall detect={t_stale:.3f}s via=idle-timeout")

            pause.clear()                  # 网络恢复
            deadline = time.perf_counter() + 2.0
            samples = None
            while time.perf_counter() < deadline:
                samples = c.read_samples(8192)
                if samples is not None:
                    break
                time.sleep(0.01)
            assert samples is not None
            assert c.state == link_state.STATE_CONNECTED
            h = c.data_health()
            assert h["gap_events"] >= 1, "跨越一次心跳缺失必须记空洞"
            assert h["last_data_age_s"] == pytest.approx(0.0, abs=0.2)
            # 源半速送达 → 观测吞吐明显低于期望采样率（不许静默）
            assert h["observed_rate_hz"] is not None
            assert h["rate_ratio"] < 0.9, f"rate_ratio={h['rate_ratio']}"
            print(f"[matrix] rtl_tcp slow-source rate_ratio={h['rate_ratio']:.2f} "
                  f"gap_events={h['gap_events']} dropped={h['dropped_samples']}")
            stop.set()

    def test_half_open_after_kill_reports_within_n_seconds(self):
        """半开连接反证：上游进程已死、TCP 被代理保持 → 必须在 N 秒内报错。"""
        upstream_port = _find_free_port()
        code = (
            "import socket,struct,sys,time\n"
            "p=int(sys.argv[1]); s=socket.socket();"
            "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
            "s.bind(('127.0.0.1',p)); s.listen(1); print('READY',flush=True)\n"
            "c,_=s.accept(); c.sendall(b'RTL0'+struct.pack('>II',5,29))\n"
            "try:\n"
            "  while True:\n"
            "    c.sendall(bytes([0,255,128,127])*1024); time.sleep(0.01)\n"
            "except OSError:\n"
            "  pass\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", code, str(upstream_port)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        freeze = threading.Event()
        stop = threading.Event()
        proxy_thread = None
        proxy_srv = None
        try:
            assert proc.stdout.readline().strip() == "READY"

            proxy_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            proxy_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            proxy_srv.bind(("127.0.0.1", 0))
            proxy_srv.listen(1)
            proxy_port = int(proxy_srv.getsockname()[1])

            def pump(src, dst):
                src.settimeout(0.1)
                while not stop.is_set():
                    if freeze.is_set():
                        time.sleep(0.02)   # 保持连接但不转发（黑洞）
                        continue
                    try:
                        data = src.recv(4096)
                    except socket.timeout:
                        continue
                    except OSError:
                        return
                    if not data:
                        return
                    try:
                        dst.sendall(data)
                    except OSError:
                        return

            def run_proxy():
                conn = None
                up = None
                try:
                    conn, _ = proxy_srv.accept()
                    up = socket.create_connection(
                        ("127.0.0.1", upstream_port), timeout=2)
                    t1 = threading.Thread(target=pump, args=(up, conn), daemon=True)
                    t2 = threading.Thread(target=pump, args=(conn, up), daemon=True)
                    t1.start()
                    t2.start()
                    t1.join()
                    t2.join()
                except OSError:
                    pass
                finally:
                    for s in (conn, up):
                        if s is not None:
                            try:
                                s.close()
                            except OSError:
                                pass

            proxy_thread = threading.Thread(
                target=run_proxy, name="fake-blackhole-proxy", daemon=True)
            proxy_thread.start()

            idle = 0.5
            be = _make_rtl_backend(proxy_port, idle=idle)
            assert be.connect() is True
            frame = _read_frame_until(be, 2048)
            assert frame is not None, "代理转发路径没有真实数据"

            freeze.set()
            proc.kill()                    # 上游真的死了，但代理不关客户端 socket
            proc.wait(timeout=3)
            t0 = time.perf_counter()
            t_stale = _measure(
                lambda: be.status.link_state == link_state.STATE_STALE, 2.0)
            assert t_stale is not None, "半开连接没有任何心跳兜底"
            t_dead = _measure(lambda: be.status.connected is False, 2.0)
            assert t_dead is not None, (
                "半开连接永远显示已连接：缺少 stale→disconnected 升级")
            total_dead = t_stale + t_dead
            assert total_dead <= 2 * idle + 0.5, (
                f"半开判定过慢：{total_dead:.2f}s（应 ≤ {2 * idle + 0.5:.2f}s）")
            assert be.status.link_state == link_state.STATE_DISCONNECTED
            assert be.status.error, "报错必须有可读原因"
            assert be.status.error_category == link_state.CATEGORY_IDLE
            h = be.data_health()
            assert h["connected"] is False
            assert h["last_data_age_s"] is not None
            print(f"[matrix] rtl_tcp half-open stale={t_stale:.3f}s "
                  f"hard-disconnect={total_dead:.3f}s (idle={idle}s) "
                  f"category={be.status.error_category}")
            be.disconnect()
        finally:
            stop.set()
            freeze.set()
            if proxy_srv is not None:
                try:
                    proxy_srv.close()
                except OSError:
                    pass
            if proxy_thread is not None:
                proxy_thread.join(timeout=2.0)
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            if proc.stdout is not None:
                proc.stdout.close()


# ═══════════════════════════════════════════════════════════════════════
# 2. SpyServer 矩阵（同类场景 + 与 rtl_tcp 的差异点）
# ═══════════════════════════════════════════════════════════════════════

class TestSpyServerMatrix:
    def test_normal_close_then_reconnect_first_frame_and_chain(self):
        def handler(conn, idx, stop):
            conn.sendall(_SPY_HANDSHAKE + _iq_bytes(20_000, seed=41))
            if idx == 0:
                time.sleep(0.1)
                _drain(conn)
                return
            _stream_forever(conn, b"", stop, seed=43)

        old_chain = old_squelch = None
        with _tcp_server_loop(handler, accept_count=2) as (port, stop, _t):
            be = SpyServerBackend("127.0.0.1", port, idle_timeout=0.5)
            be.status.squelch_db = -50.0
            assert be.connect() is True
            frame = _read_frame_until(be, 1024)
            assert frame is not None
            be.read_audio(512)
            old_chain = be._receive_chain
            assert old_chain is not None and old_chain._squelch is not None
            old_squelch = old_chain._squelch

            t_detect = _measure(lambda: be.status.connected is False, 3.0)
            assert t_detect is not None, "SpyServer 正常断开 3s 内仍未发现"
            assert be.status.link_state == link_state.STATE_DISCONNECTED
            assert be.status.error_category == link_state.CATEGORY_EOF
            old_client = be._client
            be.disconnect()
            assert old_client is None or old_client._sock is None
            assert be._receive_chain is None

            assert be.connect() is True
            frame2 = _read_frame_until(be)
            _assert_frame_is_normal(frame2)
            be.read_audio(512)
            assert be._receive_chain is not None
            assert be._receive_chain is not old_chain
            assert be._receive_chain._squelch is not old_squelch
            print(f"[matrix] spyserver normal-close detect={t_detect:.3f}s via=EOF")
            be.disconnect()
            stop.set()

    def test_spyserver_process_killed_then_half_open(self):
        """进程被杀：干净 FIN 快发现；半开（代理保持 TCP）→ N 秒内硬判。"""
        port = _find_free_port()
        code = (
            "import socket,struct,sys,time\n"
            "p=int(sys.argv[1]); s=socket.socket();"
            "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
            "s.bind(('127.0.0.1',p)); s.listen(1); print('READY',flush=True)\n"
            "c,_=s.accept()\n"
            "h=b'Name=FakeSpyServer\\0SampleRate=2048000\\0Version=0\\0'\n"
            "c.sendall(h+b'\\0'*(1024-len(h)))\n"
            "try:\n"
            "  while True:\n"
            "    c.sendall(bytes([0,255,128,127])*1024); time.sleep(0.01)\n"
            "except OSError:\n"
            "  pass\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", code, str(port)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            assert proc.stdout.readline().strip() == "READY"
            be = SpyServerBackend("127.0.0.1", port, idle_timeout=0.5)
            assert be.connect() is True
            assert _read_frame_until(be, 1024) is not None
            proc.kill()
            proc.wait(timeout=3)
            t_detect = _measure(lambda: be.status.connected is False, 3.0)
            assert t_detect is not None
            assert be.status.link_state == link_state.STATE_DISCONNECTED
            assert be.status.error_category in (
                link_state.CATEGORY_EOF, link_state.CATEGORY_RESET,
                link_state.CATEGORY_UNKNOWN)
            print(f"[matrix] spyserver process-kill detect={t_detect:.3f}s "
                  f"via={be.status.error_category}")
            be.disconnect()
        finally:
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            if proc.stdout is not None:
                proc.stdout.close()

    def test_spyserver_half_open_reported_within_n_seconds(self):
        """半开：服务端停发但 TCP 仍在 → stale 后 2×idle 内硬判断开。"""
        alive = threading.Event()
        alive.set()

        def handler(conn, idx, stop):
            conn.sendall(_SPY_HANDSHAKE + _iq_bytes(20_000, seed=51))
            while not stop.is_set():
                if alive.is_set():
                    conn.sendall(_iq_bytes(2048, seed=53))
                    time.sleep(0.005)
                else:
                    time.sleep(0.02)   # 不再发数据，但 socket 一直保持

        with _tcp_server_loop(handler, accept_count=1) as (port, stop, _t):
            idle = 0.5
            be = SpyServerBackend("127.0.0.1", port, idle_timeout=idle)
            assert be.connect() is True
            assert _read_frame_until(be, 1024) is not None
            alive.clear()
            t_stale = _measure(
                lambda: be.status.link_state == link_state.STATE_STALE, 2.0)
            assert t_stale is not None, "SpyServer 半开连接没有心跳兜底"
            t_dead = _measure(lambda: be.status.connected is False, 2.0)
            assert t_dead is not None, "SpyServer 半开连接永远显示已连接"
            total_dead = t_stale + t_dead
            assert total_dead <= 2 * idle + 0.5, (
                f"SpyServer 半开判定过慢：{total_dead:.2f}s")
            assert be.status.link_state == link_state.STATE_DISCONNECTED
            assert be.status.error and be.status.error_category == "idle"
            print(f"[matrix] spyserver half-open stale={t_stale:.3f}s "
                  f"hard-disconnect={total_dead:.3f}s (idle={idle}s)")
            be.disconnect()
            stop.set()

    def test_spyserver_protocol_differences_are_real(self):
        """与 rtl_tcp 的差异点：握手长度/异常语义/采样率不匹配都要显式。"""
        def handler(conn, idx, stop):
            conn.sendall(_SPY_HANDSHAKE)
            while not stop.is_set():
                time.sleep(0.01)

        with _tcp_server_loop(handler, accept_count=1) as (port, stop, _t):
            c = SpyServerClient("127.0.0.1", port, timeout=0.4)
            c.start()
            c.set_sample_rate(1_024_000)
            h = c.data_health()
            assert h["server_sample_rate_hz"] == 2_048_000
            assert h["sample_rate_mismatch"] is True

            # 差异 1：SpyServer 读超时抛异常（rtl_tcp 返回 None）
            with pytest.raises(Exception):
                c.read_samples(16)
            assert c.state == link_state.STATE_STALE
            c.stop()
            stop.set()

        # 差异 2：握手 12 字节 vs 1024 字节（常量对拍）
        assert RtlTcpClient._DONGLE_INFO_LEN == 12
        assert SpyServerClient._HANDSHAKE_BYTES == 1024
        print("[matrix] spyserver vs rtl_tcp: handshake=1024B vs 12B; "
              "read-error=exception vs None; rate-mismatch=explicit")


# ═══════════════════════════════════════════════════════════════════════
# 3. 超时 / 重试预算：真实秒数，不许无限重试，不许静默放弃
# ═══════════════════════════════════════════════════════════════════════

class TestTimeoutRetryBudget:
    def test_default_timeouts_are_pinned(self):
        rtl_sig = inspect.signature(RtlTcpClient.__init__).parameters
        spy_sig = inspect.signature(SpyServerClient.__init__).parameters
        assert rtl_sig["idle_timeout"].default == pytest.approx(5.0)
        assert spy_sig["timeout"].default == pytest.approx(5.0)
        assert RtlTcpClient.CONNECT_TIMEOUT == pytest.approx(5.0)

    def test_user_wait_budget_in_real_seconds(self):
        """用户最迟多久看到结论：退避 61s + 每次 connect 最长 5s × 6。"""
        budget = sum(compute_backoff(i)
                     for i in range(1, DEFAULT_MAX_ATTEMPTS + 1))
        assert budget == pytest.approx(61.0)
        worst_connect = DEFAULT_MAX_ATTEMPTS * RtlTcpClient.CONNECT_TIMEOUT
        worst_total = budget + worst_connect
        assert worst_total == pytest.approx(91.0)
        print(f"[matrix] give-up budget: backoff={budget:.0f}s + "
              f"connect {DEFAULT_MAX_ATTEMPTS}x5s = worst {worst_total:.0f}s")

    def test_give_up_without_callback_is_not_silent(self):
        """不传 on_give_up 也必须能从 snapshot 读到 gave_up + 下一步。"""
        class _Dead:
            def get_status(self):
                class S:
                    connected = False
                    error = "连接失败: 对端拒绝"
                return S()
            def is_connected(self):
                return False
            def connect(self):
                return False

        mgr = ReconnectionManager(
            poll_interval=0.01, base_delay=0.001, max_delay=0.002,
            source_kind="network")
        mgr.start(_Dead())
        try:
            assert _wait_until(lambda: mgr.snapshot()["gave_up"], 3.0)
            snap = mgr.snapshot()
            assert snap["state"] == "gave_up"
            assert snap["attempts_total"] == DEFAULT_MAX_ATTEMPTS
            assert snap["suggestion"], "放弃必须给可操作下一步"
            assert snap["last_error"]
        finally:
            mgr.stop()


# ═══════════════════════════════════════════════════════════════════════
# 4. 弱网矩阵：四类劣化 × 两种心跳阈值，数「误判次数」与「最大空档」
# ═══════════════════════════════════════════════════════════════════════

def _impaired_stream(conn, stop, *, loss: float = 0.0,
                     delay_s: float = 0.0, jitter_s: float = 0.0,
                     reorder: float = 0.0, reorder_hold_s: float = 0.05,
                     stall_s: float = 0.0, period_s: float = 0.0,
                     seed: int = 1, chunk: int = 1024,
                     interval: float = 0.005) -> None:
    """在本机假服务上制造四类弱网，只改「数据到达时间」，不改数据内容。

    - ``loss``：按概率整块丢弃。TCP 层等价于一次丢包重传，客户端只看到
      该块晚到，字节序与总量最终仍是完整的。
    - ``delay_s + jitter_s``：固定管道延迟（首个字节晚到，之后每 ``interval``
      匀速到达）。``jitter_s`` 取 [0.05, jitter_s] 的一次性偏移，避免实测点
      正好压在阈值边界上（3s 档实测落在 3.05~3.30s，必然 >3s 且远小于 5s）。
    - ``reorder``：按比例触发一次「等重传」停顿 ``reorder_hold_s``。TCP 保证
      字节序，客户端**观测不到**真正的乱序；乱序在客户端可观测的后果就是
      缺片等待重传造成的短停顿，这里按这个后果等价模拟。同一 0.5s 内最多
      触发一次，避免把吞吐压成黑洞（那是带宽档的事）。
    - ``stall_s / period_s``：周期性完全停发（拥塞窗口塌陷 / 重传退避的极端
      形态），停发期不补发，客户端在该窗口内收不到任何字节。
    """
    conn.sendall(_RTL_HEADER)
    rng = random.Random(seed)
    t0 = time.perf_counter()
    offset = delay_s + (rng.uniform(0.05, jitter_s) if jitter_s > 0 else 0.0)
    next_due = t0 + offset
    last_reorder = 0.0
    nonce = 0
    while not stop.is_set():
        now = time.perf_counter()
        if period_s > 0.0 and (now - t0) % period_s < stall_s:
            time.sleep(0.02)
            next_due = max(next_due, time.perf_counter() + interval)
            continue
        if now < next_due:
            time.sleep(max(0.001, min(next_due - now, 0.005)))
            continue
        next_due = now + interval
        payload = _iq_bytes(chunk, seed=seed + nonce)
        nonce += 1
        if loss > 0.0 and rng.random() < loss:
            continue
        if (reorder > 0.0 and rng.random() < reorder
                and now - last_reorder >= 0.5):
            last_reorder = now
            time.sleep(reorder_hold_s)      # 等重传：整条流一起停
            continue
        conn.sendall(payload)


def _run_weak_cell(idle: float, duration: float, seed: int,
                   **impair) -> dict:
    """跑一档弱网：统计 stale / disconnected 转换次数、最大空档与真实耗时。"""
    stats = {"stale": 0, "disconnected": 0, "reads": 0, "samples": 0,
             "max_gap_s": 0.0, "elapsed": 0.0, "rate_ratio": None}

    def handler(conn, idx, srv_stop):
        _impaired_stream(conn, srv_stop, seed=seed, **impair)

    with _tcp_server_loop(handler, accept_count=1) as (port, stop, _t):
        c = RtlTcpClient("127.0.0.1", port, idle_timeout=idle)
        try:
            assert c.connect() is True
            c.set_sample_rate(1_000_000)   # 只设期望速率，供 rate_ratio 观测
            prev = c.state
            t0 = time.perf_counter()
            last_ok = t0
            while time.perf_counter() - t0 < duration:
                frame = c.read_samples(2048)
                now = time.perf_counter()
                if frame is not None:
                    # 相邻两次读成功的间隔是「无字节」的上界：静默期内不可能
                    # 读出整帧。首帧把「连接建立起就一直没有数据」也算进去，
                    # 否则 3s 延迟档的初始空档会被首个成功读隐藏掉。
                    stats["max_gap_s"] = max(stats["max_gap_s"],
                                             now - (last_ok if stats["reads"]
                                                    else t0))
                    stats["reads"] += 1
                    stats["samples"] += len(frame)
                    last_ok = now
                state = c.state
                if state != prev:
                    if state == link_state.STATE_STALE:
                        stats["stale"] += 1
                    elif state == link_state.STATE_DISCONNECTED:
                        stats["disconnected"] += 1
                    prev = state
                if state == link_state.STATE_DISCONNECTED:
                    break
            stats["elapsed"] = time.perf_counter() - t0
            stats["rate_ratio"] = c.data_health().get("rate_ratio")
        finally:
            c.close()
            stop.set()
    return stats


#: (名称, 劣化参数, idle=3s 时预期的 stale 次数, idle=2s 时预期次数)。
#: idle=5s 一列全部预期 0。
_WEAK_CASES = [
    ("loss-1%", dict(loss=0.01), 0, 0),
    ("loss-5%", dict(loss=0.05), 0, 0),
    ("loss-10%", dict(loss=0.10), 0, 0),
    ("delay-200ms", dict(delay_s=0.2, jitter_s=0.3), 0, 0),
    # 1s 级停发占用 2s 阈值的 57%，但仍不越线（实测 max_gap 1.141s）
    ("delay-1s", dict(delay_s=1.0, jitter_s=0.3), 0, 0),
    # 3s 延迟在 3s/2s 阈值下必然触发一次 stale；5s 阈值下 3.05~3.30s 仍有余量
    ("delay-3s", dict(delay_s=3.0, jitter_s=0.3), 1, 1),
    ("reorder-5%", dict(reorder=0.05), 0, 0),
    ("reorder-10%", dict(reorder=0.10), 0, 0),
    ("reorder-20%", dict(reorder=0.20), 0, 0),
    # 拥塞窗口塌陷：周期 4×stall（停发占空比 25%），客户端在 stall 窗口内
    # 收不到任何字节。周期必须 > 窗口，否则窗口末尾的读会跨进下一轮停发，
    # 把「窗口外的新事件」误记成「窗口内的误判」。
    ("bwjit-200ms", dict(stall_s=0.2, period_s=0.8), 0, 0),
    ("bwjit-1s", dict(stall_s=1.0, period_s=4.0), 0, 0),
    ("bwjit-3.2s", dict(stall_s=3.2, period_s=12.8), 1, 1),
    # 匀速涓流（标称 1 MS/s 的 0.13%）：字节一直在流，心跳不该响
    ("bwjit-trickle", dict(chunk=256, interval=0.2), 0, 0),
]

_WEAK_CASE_IDS = [c[0] for c in _WEAK_CASES]


class TestWeakNetworkMatrix:
    """四类弱网 × 三种阈值（5s / 3s / 2s）：窗口取 2×idle + 0.8s。

    窗口必须覆盖「连续 2 次心跳超时」的时间尺度，否则 5s 一列根本不可能
    触发升级，测出来的 0 次是结构性的、没有意义。3s/2s 一列同样覆盖
    2×idle 的硬判尺度。
    """

    @pytest.mark.parametrize("idle", [5.0, 3.0, 2.0])
    @pytest.mark.parametrize("name,impair,expect_stale_3s,expect_stale_2s",
                             _WEAK_CASES, ids=_WEAK_CASE_IDS)
    def test_weak_cell_stale_and_gap(self, idle, name, impair,
                                     expect_stale_3s, expect_stale_2s):
        duration = 2 * idle + 0.8
        stats = _run_weak_cell(idle, duration, seed=7, **impair)
        if idle == 5.0:
            expected = 0
        elif idle == 3.0:
            expected = expect_stale_3s
        else:
            expected = expect_stale_2s

        assert stats["samples"] > 0, "档位把数据流彻底掐死了，不是弱网"
        assert stats["disconnected"] == 0, (
            f"{name} idle={idle}s 不该触发硬断开（"
            f"max_gap={stats['max_gap_s']:.3f}s）")
        assert stats["stale"] == expected, (
            f"{name} idle={idle}s 误判 {stats['stale']} 次（预期 {expected}）")
        # 硬断开要求连续 2×idle 无字节；实测最大空档必须低于这条线
        assert stats["max_gap_s"] < 2 * idle, (
            f"{name} idle={idle}s 最大空档 {stats['max_gap_s']:.3f}s ≥ "
            f"{2 * idle:.1f}s")
        assert stats["elapsed"] >= duration - 0.05, (
            f"{name} idle={idle}s 只跑了 {stats['elapsed']:.2f}s，"
            f"窗口未覆盖 {duration:.1f}s")
        rate = stats["rate_ratio"]
        print(f"[weak] case={name:<14} idle={idle:.0f}s "
              f"stale={stats['stale']} disc={stats['disconnected']} "
              f"max_gap={stats['max_gap_s']:.3f}s "
              f"elapsed={stats['elapsed']:.2f}s reads={stats['reads']} "
              f"rate_ratio={rate if rate is None else round(rate, 4)}")


# ═══════════════════════════════════════════════════════════════════════
# 5. 真实阈值下的半开硬判：5s/10s、3s/6s 与 2s/4s，两种客户端
# ═══════════════════════════════════════════════════════════════════════

class TestRealThresholdLatency:
    """不缩短超时：直接用产品候选阈值 5.0s / 3.0s / 2.0s 跑半开连接。

    假服务先真实吐数据，然后保留 TCP、停止转发；客户端必须
    在 ``idle`` 内转 stale、在 ``2×idle`` 内硬判 disconnected。
    """

    @pytest.mark.parametrize("kind", ["rtl", "spy"])
    @pytest.mark.parametrize("idle", [5.0, 3.0, 2.0])
    def test_half_open_hard_disconnect_within_two_heartbeats(self, kind, idle):
        alive = threading.Event()
        alive.set()
        header = _RTL_HEADER if kind == "rtl" else _SPY_HANDSHAKE

        def handler(conn, idx, stop):
            conn.sendall(header)
            while not stop.is_set():
                if not alive.is_set():
                    time.sleep(0.02)   # 保持 TCP，不转发任何 IQ
                    continue
                conn.sendall(_iq_bytes(2048, seed=71))
                time.sleep(0.05)       # 40.9 kS/s：不撑满 1M 样点的环

        with _tcp_server_loop(handler, accept_count=1) as (port, stop, _t):
            if kind == "rtl":
                be = _make_rtl_backend(port, idle=idle)
            else:
                be = SpyServerBackend("127.0.0.1", port, idle_timeout=idle)
            try:
                assert be.connect() is True
                assert _read_frame_until(be, 1024, 4.0) is not None
                alive.clear()
                t0 = time.perf_counter()
                t_stale = _measure(
                    lambda: be.status.link_state == link_state.STATE_STALE,
                    idle + 2.0)
                assert t_stale is not None, (
                    f"{kind} idle={idle}s：半开连接没有任何心跳兜底")
                t_hard = _measure(
                    lambda: be.status.connected is False, idle + 2.0)
                assert t_hard is not None, (
                    f"{kind} idle={idle}s：stale 之后没有硬判，永远停在半开")
                total = t_stale + t_hard
                assert total <= 2 * idle + 0.9, (
                    f"{kind} idle={idle}s 硬判过慢：{total:.2f}s "
                    f"（应 ≤ {2 * idle + 0.9:.1f}s）")
                assert total >= 2 * idle - 0.5, (
                    f"{kind} idle={idle}s 硬判过早：{total:.2f}s "
                    f"（应 ≥ {2 * idle - 0.5:.1f}s，次数不足就是放宽了断言）")
                assert be.status.link_state == link_state.STATE_DISCONNECTED
                assert be.status.connected is False
                assert be.status.error, "硬判必须给可读原因"
                assert be.status.error_category == link_state.CATEGORY_IDLE
                assert be.data_health()["connected"] is False
                print(f"[weak] half-open kind={kind} idle={idle:.0f}s "
                      f"stale={t_stale:.3f}s hard={total:.3f}s "
                      f"category={be.status.error_category}")
            finally:
                be.disconnect()
                stop.set()


# ═══════════════════════════════════════════════════════════════════════
# 6. 阈值决策回归锁：常量一旦被改成 3s/6s 必须在这里报红
# ═══════════════════════════════════════════════════════════════════════

#: 弱网矩阵一次实测的极值（docs/network-layer.md §0.2，2026-11-05 本机回环）。
#: 这里只**引用**实测结果做决策边界，不在单测里重跑 13 档（约 4.5 分钟）；
#: 档位与预期次数的结构性证据钉在 ``_WEAK_CASES`` 上（见下）。
_RECORDED_WORST_GAP_S = 3.212        # bwjit-3.2s / delay-3s 档的最大空档
_RECORDED_HALF_OPEN_HARD_S = (10.024, 10.027)   # 5s 档 rtl / spy 硬判实测


class TestWeakThresholdDecisionLock:
    """把「保持 5s/10s、不改 3s/6s」钉成会失败的断言。

    依据（本机假服务 + 本机回环，13 档弱网各跑 ``2×idle + 0.8s`` 窗口）：

    - ``idle=5s``：13/13 档 stale 次数 0、硬断开 0 次；
    - ``idle=3s``：2/13 档（15.4%）各误判 1 次 stale —— ``delay-3s`` 与
      ``bwjit-3.2s``；其余 11 档 0 次。
    - 半开连接（保 TCP 不转发）实测硬判 10.024s / 10.027s（rtl / spy）。

    因此默认值改为 3.0s（或 ``_STALE_ESCALATE_AFTER`` 改成别的倍数）时，
    下面的等值断言与档位计数会直接报红，而不是靠文档口头约定。
    """

    def test_default_idle_timeout_is_five_seconds(self):
        rtl_sig = inspect.signature(RtlTcpClient.__init__).parameters
        spy_sig = inspect.signature(SpyServerClient.__init__).parameters
        rtl_be_sig = inspect.signature(RtlTcpBackend.__init__).parameters
        spy_be_sig = inspect.signature(SpyServerBackend.__init__).parameters

        assert rtl_sig["idle_timeout"].default == pytest.approx(5.0)
        assert spy_sig["timeout"].default == pytest.approx(5.0)
        assert rtl_be_sig["idle_timeout"].default == pytest.approx(5.0)
        assert spy_be_sig["idle_timeout"].default == pytest.approx(5.0)
        assert RtlTcpClient.CONNECT_TIMEOUT == pytest.approx(5.0)

        print("[lock] defaults: rtl idle=5.0s spy timeout=5.0s "
              "rtl_backend=5.0s spy_backend=5.0s connect_timeout=5.0s")

    def test_hard_verdict_is_two_heartbeats(self):
        assert RtlTcpClient._STALE_ESCALATE_AFTER == 2
        assert SpyServerClient._STALE_ESCALATE_AFTER == 2

        rtl = RtlTcpClient("127.0.0.1", 1)
        spy = SpyServerClient("127.0.0.1", 1)
        assert rtl._idle_timeout * rtl._STALE_ESCALATE_AFTER == pytest.approx(10.0)
        assert spy.idle_timeout * spy._STALE_ESCALATE_AFTER == pytest.approx(10.0)

        # 3s/6s 与 2s/4s 只是「同一公式的另一个入参」，不是另一套机制：
        alt = RtlTcpClient("127.0.0.1", 1, idle_timeout=3.0)
        assert alt._idle_timeout * alt._STALE_ESCALATE_AFTER == pytest.approx(6.0)
        alt2 = RtlTcpClient("127.0.0.1", 1, idle_timeout=2.0)
        assert alt2._idle_timeout * alt2._STALE_ESCALATE_AFTER == pytest.approx(4.0)
        print("[lock] hard-disconnect: idle=5s → 10.0s; idle=3s → 6.0s; "
              "idle=2s → 4.0s (2×heartbeat)")

    def test_measured_evidence_still_supports_five_seconds(self):
        """把「5s 0/13、3s 2/13、2s 2/13」的档位证据钉在测试里。"""
        assert len(_WEAK_CASES) == 13, (
            f"弱网档位数变了：{len(_WEAK_CASES)}，实测结论按 13 档记录，"
            f"改档位必须重跑矩阵并同步更新 docs/network-layer.md §0")
        positives = [(name, e3) for name, _impair, e3, _e2 in _WEAK_CASES
                     if e3]
        assert [name for name, _e in positives] == ["delay-3s", "bwjit-3.2s"], (
            "3s 档的误判档位与实测记录不一致："
            f"{[name for name, _e in positives]}")
        assert sum(e3 for _n, _i, e3, _e2 in _WEAK_CASES) == 2

        # 2s 档与 3s 档在本矩阵上同档位误判（2/13），没有额外误报
        positives_2s = [(name, e2) for name, _impair, _e3, e2 in _WEAK_CASES
                        if e2]
        assert [name for name, _e in positives_2s] == ["delay-3s", "bwjit-3.2s"]
        assert sum(e2 for _n, _i, _e3, e2 in _WEAK_CASES) == 2

        # 决策边界：最坏空档 3.212s 对 5s 是 64% 占用，对 3s 是 107% 越线
        assert _RECORDED_WORST_GAP_S < 5.0
        assert _RECORDED_WORST_GAP_S > 3.0
        # 半开硬判实测值落在 2×5s 的 ±0.5s 内（不是推演）
        for t in _RECORDED_HALF_OPEN_HARD_S:
            assert 9.5 <= t <= 10.5

        print(f"[lock] measured: 13 档 idle=5s → 0/13 stale、0 hard-disconnect; "
              f"idle=3s → 2/13 (15.4%): delay-3s、bwjit-3.2s; "
              f"idle=2s → 2/13 (15.4%): 同两档（无额外误报）; "
              f"worst_gap={_RECORDED_WORST_GAP_S:.3f}s; "
              f"half-open hard={_RECORDED_HALF_OPEN_HARD_S[0]:.3f}s/"
              f"{_RECORDED_HALF_OPEN_HARD_S[1]:.3f}s (rtl/spy)")

    def test_three_second_config_would_be_red_on_two_cells(self):
        """反证：若默认改成 3s，13 档里有 2 档会转 stale —— 这就是报红的依据。"""
        with_3s = sum(e3 for _n, _i, e3, _e2 in _WEAK_CASES)
        assert with_3s == 2
        assert with_3s / len(_WEAK_CASES) == pytest.approx(0.153846, abs=1e-6)
        print(f"[lock] counter-evidence: 3s/6s = {with_3s}/{len(_WEAK_CASES)} "
              f"= {with_3s / len(_WEAK_CASES) * 100:.1f}% 档位误判 (vs 5s/10s 0%)")


# ═══════════════════════════════════════════════════════════════════════
# 7. 环形缓冲读超时自死锁：复现旧形态即红
# ═══════════════════════════════════════════════════════════════════════

class TestRingReaderTimeoutDeadlock:
    """``_FanOutSub.read`` 超时分支不得重入 ``r._lock``。

    历史 bug：``Condition`` 绑定的是非可重入 ``threading.Lock``；``read`` 外层
    已持有该锁，超时分支若再 ``with r._lock`` 就自锁 —— 表现是「缓冲暂时空，
    读永远不返回」（矩阵用例首跑卡死 70s+，faulthandler 栈见
    ``docs/network-layer.md`` §5.1）。修复是直接 ``r._underruns += 1``。

    本用例让读线程走超时分支，它必须按时返回；若有人把嵌套锁加回去，
    线程会永久停在 ``with r._lock``，``join(2.0)`` 后仍存活 → 断言报红。
    用 daemon 线程 + 失败时不碰锁，保证红的时候不会把 pytest 挂死。
    """

    def test_timeout_branch_returns_instead_of_self_deadlocking(self):
        def _no_data(_n):
            return None

        ring = _ThreadedRingReader(_no_data, block_size=1024, ring_size=4096)
        # 前提：Condition 用的就是这把非可重入 Lock（不是 RLock）
        assert ring._cond._lock is ring._lock
        assert not isinstance(ring._lock, type(threading.RLock()))

        sub = ring.subscribe("deadlock-probe")
        result: dict = {}

        def _reader():
            result["value"] = sub.read(256, timeout=0.2)
            result["done"] = True

        t = threading.Thread(target=_reader, name="fanout-deadlock-probe",
                             daemon=True)
        t0 = time.perf_counter()
        t.start()
        t.join(2.0)
        stuck = t.is_alive()
        if not stuck:
            ring.stop()            # 只有线程退出后才安全拿这把锁

        assert not stuck, (
            "读超时路径自死锁：线程 2s 内未返回，"
            f"卡在 {getattr(t, 'name', '?')}；"
            "超时分支不许再 with r._lock（见 docs/network-layer.md §5.1）")
        assert result.get("done") is True
        assert result.get("value") is None
        assert ring.underruns_total() == 1

        # 超时后锁必须已释放：主线程再读一次仍能按时返回（不是只解开一次）
        t1 = time.perf_counter()
        assert sub.read(256, timeout=0.05) is None
        assert time.perf_counter() - t1 < 1.0
        print(f"[deadlock] timeout-branch returned in "
              f"{time.perf_counter() - t0:.3f}s, underruns=2, lock released")


__all__ = []
