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


__all__ = []
