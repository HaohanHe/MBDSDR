# SPDX-License-Identifier: MIT
"""接入层「连断循环」资源泄漏普查：rtl_tcp / SpyServer 各 N=200 次连接→收样→断开。

口径（写死，不按"看起来没涨"）：
  - 每 20 次循环采一个窗口（共 10 个窗口）；
  - 判据：**最后窗口（第 200 次）的读数 ≤ 第 2 个窗口（第 40 次）的读数 + 容差**；
    第 1 个窗口只作预热展示，不参与判定；
  - 容差：
      handles / open_files    +2   —— 一个泄漏的 socket 只多 1 个句柄，160 次
                                      窗口间隔内真泄漏会是 +160，+2 只吸收
                                      psutil/日志的瞬时句柄抖动；
      threading.active_count  +1   —— 采样时允许 1 个生产者线程刚收到 _stop 尚
                                      未完全退出；按循环泄漏会是 +160；
      psutil.num_threads      +2   —— 同上，OS 侧计数多留 1 个余量；
      RSS                     +8MB —— 窗口间隔 160 次循环，最小的可疑泄漏
                                      （每连接 64KB 读块）也有 160×64KB≈10MB，
                                      +8MB 不会把 64KB/循环 的泄漏放过去，只
                                      吸收 numpy/Python arena 的保留量。
  - RSS 采样前做一次 gc.collect()：循环垃圾不算"占着不放"；真泄漏（活线程、
    未关 socket、持续增长的容器）不会被 gc 回收，仍在读数里。

测量手段：
  - 本地 loopback 假服务端，**单线程顺序服务**（一个服务线程串行处理 200 个
    连接），保证 threading.active_count() 的增长只可能来自被测代码；
  - 服务端先发协议握手，再持续推 8-bit IQ；客户端每轮读 4096 个样本后断开。

复跑：
    $env:MBDSDR_TEST_LOCK='0'   # 纯 socket 用例，不加载 Qt
    python -m pytest -q -s tests/test_network_resource_leaks.py
"""

from __future__ import annotations

import gc
import socket
import struct
import threading
import time
from contextlib import contextmanager
from typing import Callable, List, Optional

import numpy as np
import psutil
import pytest

from mbdsdr_ai import rtltcp_client as _rtltcp_module
from mbdsdr_ai.reconnection import ReconnectionManager, compute_backoff
from mbdsdr_ai.rtltcp_client import RtlTcpBackend, RtlTcpClient
from mbdsdr_ai.sdr_backend import _ThreadedRingReader
from mbdsdr_ai.spyserver_client import SpyServerBackend

#: 连断循环次数与采样步长（判据依赖这两个数，别随手改）
N_CYCLES = 200
SAMPLE_EVERY = 20

#: 判据容差（理由见模块 docstring）
TOLERANCE = {
    "handles": 2,
    "open_files": 2,
    "threads_active": 1,
    "num_threads": 2,
    "rss_mb": 8.0,
}

_RTL_HEADER = b"RTL0" + struct.pack(">II", 5, 29)
_SPY_KEYS = b"Name=FakeSpyServer\0SampleRate=2048000\0Version=0\0"
_SPY_HANDSHAKE = (_SPY_KEYS + b"\0" * (1024 - len(_SPY_KEYS)))[:1024]
#: 每块 16384 字节 = 8192 个复样本，正好一个 _ThreadedRingReader 读块。
_IQ_BLOCK = np.arange(16384, dtype=np.uint8).tobytes()


# ---------------------------------------------------------------------------
# 单线程顺序假服务端
# ---------------------------------------------------------------------------
class _SequentialStreamServer:
    """一次只服务一个连接：发握手 → 持续推 IQ → 客户端关闭后接下一个。

    只占 **1 个线程**（不按连接新建 handler 线程），所以被测进程的线程数
    增长不可能来自测试脚手架。
    """

    def __init__(self, handshake: bytes, block: bytes = _IQ_BLOCK,
                 stream: bool = True):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(8)
        self._srv = srv
        self._handshake = handshake
        self._block = block
        self._stream = bool(stream)
        self.port = int(srv.getsockname()[1])
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._conn: Optional[socket.socket] = None
        self.accepted = 0

    def start(self) -> "_SequentialStreamServer":
        self._thread = threading.Thread(
            target=self._loop, name="leak-fake-server", daemon=True)
        self._thread.start()
        return self

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            self._conn = conn
            self.accepted += 1
            try:
                conn.sendall(self._handshake)
                if self._stream:
                    while not self._stop.is_set():
                        conn.sendall(self._block)
                else:
                    # 静默：只吸收客户端命令，不推 IQ（生产者会阻塞在 recv）
                    while not self._stop.is_set():
                        if not conn.recv(4096):
                            break
            except OSError:
                # 客户端已断开（RST/FIN）：进入下一个 accept
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
                self._conn = None

    def stop(self) -> None:
        self._stop.set()
        conn = self._conn
        if conn is not None:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass
        try:
            self._srv.close()
        except OSError:
            pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


@contextmanager
def _server(handshake: bytes, stream: bool = True):
    srv = _SequentialStreamServer(handshake, stream=stream).start()
    try:
        yield srv
    finally:
        srv.stop()


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


# ---------------------------------------------------------------------------
# 采样与判据
# ---------------------------------------------------------------------------
def _sample() -> dict:
    """固定口径采一次进程资源读数。"""
    gc.collect()
    proc = psutil.Process()
    try:
        handles = int(proc.num_handles())
    except psutil.Error:
        handles = -1
    try:
        open_files = len(proc.open_files())
    except psutil.Error:
        open_files = -1
    return {
        "handles": handles,
        "open_files": open_files,
        "threads_active": int(threading.active_count()),
        "num_threads": int(proc.num_threads()),
        "rss_mb": round(proc.memory_info().rss / (1024 * 1024), 2),
    }


def _fmt(window: int, s: dict) -> str:
    return (f"window {window:2d} (cycles {window * SAMPLE_EVERY:3d}): "
            f"handles={s['handles']} open_files={s['open_files']} "
            f"threads_active={s['threads_active']} "
            f"num_threads={s['num_threads']} rss={s['rss_mb']}MB")


def _assert_no_growth(series: List[dict], label: str) -> None:
    """最后窗口 ≤ 第 2 个窗口 + 容差（模块 docstring 的写死判据）。"""
    baseline = series[1]          # 第 2 个 20 次窗口
    final = series[-1]            # 最后 20 次窗口
    failures = []
    for key, tol in TOLERANCE.items():
        growth = final[key] - baseline[key]
        if growth > tol:
            failures.append(f"{key}: {baseline[key]} → {final[key]} "
                            f"(+{growth}, 容差 {tol})")
    assert not failures, (
        f"[{label}] 资源随连断循环增长，判定泄漏：\n  " + "\n  ".join(failures)
        + "\n完整采样序列：\n  "
        + "\n  ".join(_fmt(i + 1, s) for i, s in enumerate(series)))


# ---------------------------------------------------------------------------
# 单轮：连接 → 收一小段 → 断开
# ---------------------------------------------------------------------------
def _cycle(make_backend: Callable[[], object], read_n: int = 4096) -> None:
    be = make_backend()
    assert be.connect() is True, f"connect() 失败: {getattr(be, 'status', None)}"
    deadline = time.monotonic() + 3.0
    got = 0
    while time.monotonic() < deadline:
        chunk = be.read_samples(read_n)
        if chunk is not None and len(chunk) > 0:
            got = len(chunk)
            break
        time.sleep(0.005)
    assert got > 0, "连接成功但一个样本也没收到，无法验证'收一小段后断开'"
    be.disconnect()


def _run_census(make_backend: Callable[[], object], label: str) -> List[dict]:
    series: List[dict] = []
    for i in range(1, N_CYCLES + 1):
        _cycle(make_backend)
        if i % SAMPLE_EVERY == 0:
            time.sleep(0.15)     # 让刚 stop 的生产者线程走完退出
            s = _sample()
            series.append(s)
            print(f"[leak] {label} " + _fmt(len(series), s))
    _assert_no_growth(series, label)
    return series


# ---------------------------------------------------------------------------
# 1/2. 两条链路的连断循环
# ---------------------------------------------------------------------------
def test_rtl_tcp_connect_disconnect_200_cycles_no_resource_growth(monkeypatch):
    """rtl_tcp：200 次连接→收样→断开，句柄/线程/RSS 不得单调增长。

    断连冷却（RECONNECT_COOLDOWN_S=2.5s，服务端单客户端名额）只是时间等待，
    不持有任何资源；为把 200 轮压在可测时长内置 0，冷却本身由
    tests/test_network_failure_modes.py 的握手/断连用例覆盖。
    """
    monkeypatch.setattr(_rtltcp_module, "RECONNECT_COOLDOWN_S", 0.0)
    with _server(_RTL_HEADER) as srv:
        _run_census(
            lambda: RtlTcpBackend("127.0.0.1", srv.port, idle_timeout=0.5),
            "rtl_tcp")
    assert srv.accepted >= N_CYCLES - 1, f"服务端只接到 {srv.accepted} 个连接"


def test_spyserver_connect_disconnect_200_cycles_no_resource_growth():
    """SpyServer：200 次连接→收样→断开，句柄/线程/RSS 不得单调增长。"""
    with _server(_SPY_HANDSHAKE) as srv:
        _run_census(
            lambda: SpyServerBackend("127.0.0.1", srv.port, idle_timeout=0.5),
            "spyserver")
    assert srv.accepted >= N_CYCLES - 1, f"服务端只接到 {srv.accepted} 个连接"


# ---------------------------------------------------------------------------
# 2.5 断连时生产者正阻塞在 recv（服务端握手后静默）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kind,handshake", [
    ("rtl_tcp", _RTL_HEADER),
    ("spyserver", _SPY_HANDSHAKE),
])
def test_disconnect_while_producer_blocked_returns_promptly(
        kind, handshake, monkeypatch):
    """服务端握手后不再发数据时断开：disconnect() 不许等满 join 超时。

    修复前实测：生产者阻塞在 recv（socket 超时 = idle_timeout=5s），
    disconnect() 先 join 生产者 → 每次白等 2.0s（_ThreadedRingReader.stop
    的 join 上限），且 join 超时后线程引用被丢弃，10 轮里有 1 轮在
    disconnect() 返回后仍采到存活的 mbdsdr-ring-producer。
    """
    monkeypatch.setattr(_rtltcp_module, "RECONNECT_COOLDOWN_S", 0.0)
    with _server(handshake, stream=False) as srv:
        def make():
            if kind == "rtl_tcp":
                return RtlTcpBackend("127.0.0.1", srv.port, idle_timeout=5.0)
            return SpyServerBackend("127.0.0.1", srv.port, idle_timeout=5.0)

        baseline = _sample()
        durations: List[float] = []
        samples: List[dict] = []
        for i in range(10):
            be = make()
            assert be.connect() is True
            time.sleep(0.2)      # 让生产者进入 recv 阻塞
            t0 = time.perf_counter()
            be.disconnect()
            durations.append(time.perf_counter() - t0)
            stragglers = [t.name for t in threading.enumerate()
                          if t.name == "mbdsdr-ring-producer"]
            assert not stragglers, (
                f"第 {i + 1} 轮 disconnect() 返回后生产者仍存活：{stragglers}"
                f"（_ThreadedRingReader.stop 的 join 超时丢掉了线程引用）")
            if (i + 1) % 5 == 0:
                s = _sample()
                samples.append(s)
                print(f"[blocked:{kind}] cycle {i + 1}: {s}")

        assert max(durations) < 0.5, (
            f"disconnect 最长 {max(durations):.3f}s（修复前 2.0s = join 超时；"
            f"应先关 socket 唤醒生产者再 join）全部："
            f"{[round(d, 3) for d in durations]}")
        _assert_no_growth([baseline] + samples, f"blocked:{kind}")


# ---------------------------------------------------------------------------
# 4. 已知缺陷立案（不在本轮修复）：_ThreadedRingReader.stop() 丢线程引用
# ---------------------------------------------------------------------------
# 现状（sdr_backend.py:1515-1521）：
#     def stop(self):
#         self._stop = True
#         with self._cond: self._cond.notify_all()
#         if self._thread is not None:
#             self._thread.join(timeout=2.0)   # 上限 2.0s
#             self._thread = None               # ← join 超时也置 None
#
# 什么条件下会丢线程引用（清单）：
#   - 条件：read_fn 单次阻塞 > 2.0s。生产者只在两个位置阻塞——设备读
#     （RTLSDRBackend._ring_read → pyrtlsdr read_samples / RtlTcpDeviceAdapter
#     的 socket recv）和写侧 _cond（这里是内存操作，可忽略）。
#   - 走这条路径的后端（都 new 了 _ThreadedRingReader，block_size=8192）：
#       1) RTLSDRBackend（sdr_backend.py:2069）——本地 USB bulk 传输，
#          设备拔出/固件卡死时 read_samples 会超过 2s；
#          host 模式下 _sdr=RtlTcpDeviceAdapter，socket 超时 = idle_timeout
#          （默认 5s），服务端静默时必然 > 2s（本轮已实测 2.005s）；
#       2) RtlTcpBackend（rtltcp_client.py:737）——同 5s socket 超时；
#       3) SpyServerBackend（spyserver_client.py:666）——同 5s socket 超时。
#     其中 2)/3) 的 disconnect() 本轮已改成「先关 socket 再 join」，正常情况下
#     join 不再超时（tests 里 blocked-disconnect 用例守住了）。
#   - join 上限：2.0s（_ThreadedRingReader.stop 的硬编码值）。
#   - 丢的后果：
#       a) 生产者线程越界存活，stop() 返回后仍在跑，且调用方已拿不到引用，
#          无法再 join 收尸（线程对象与它持有的 ring buffer 一起滞留）；
#       b) 同一个 reader 再 start() 时 `_thread is None`，会无视旧线程再起
#          一个生产者 → 两个生产者并发写同一 ring（_write 有锁不致撕裂，但
#          重复读数/重复丢样记账，且线程数越堆越多）。
#
# 编码方式：2026-11-12 曾用 xfail(strict=True) 立案（当时不许改 sdr_backend.py）。
# 该文件按 docs/COORDINATION.md 6.1.4 记录型接管后，stop() 已改为「join 超时
# 只在 _thread 确实退出时才置 None」，本用例随之转成普通断言：
#     python -m pytest -q tests/test_network_resource_leaks.py -k ring_reader
def test_ring_reader_stop_timeout_keeps_thread_reference():
    """join 超时的 stop() 必须保留线程引用（否则越界线程无法收尸/会双生产者）。"""
    gate = threading.Event()
    entered = threading.Event()

    def read_fn(_n):
        entered.set()
        gate.wait(30.0)      # 模拟只能在外部解除的阻塞（bulk 传输 / recv）
        return None

    reader = _ThreadedRingReader(read_fn, block_size=8192, ring_size=1024)
    reader.start()
    try:
        assert entered.wait(2.0), "生产者没有进入 read_fn"
        t0 = time.perf_counter()
        reader.stop()        # join(2.0) 必然超时
        elapsed = time.perf_counter() - t0

        live = [t for t in threading.enumerate()
                if t.name == "mbdsdr-ring-producer"]
        assert live, "前置条件：read_fn 未被解除时生产者应仍存活"
        assert elapsed >= 1.9, f"stop() 应等满 join 上限，实测 {elapsed:.2f}s"
        # 以下是本该为绿的判据（当前红）：
        assert reader._thread is not None, (
            "stop() join 超时后把 _thread 置 None：线程越界存活且引用丢失，"
            "调用方再也无法 join 收尸；再 start() 还会再起一个生产者")
        reader.start()
        live_after_restart = [t for t in threading.enumerate()
                              if t.name == "mbdsdr-ring-producer"]
        assert len(live_after_restart) == 1, (
            f"重复 start() 后生产者线程 {len(live_after_restart)} 个（应为 1）")
    finally:
        gate.set()
        for t in threading.enumerate():
            if t.name == "mbdsdr-ring-producer":
                t.join(2.0)


# ---------------------------------------------------------------------------
# 5. 重连风暴：服务端拒绝后连续重试 50 轮
# ---------------------------------------------------------------------------
def _short_connect(addr, timeout=None, **kwargs):
    """把建连超时压到 50ms 的仍走真实内核的替身（Windows 拒绝端口按超时返回）。"""
    real = socket.create_connection
    return real(addr, timeout=min(0.05, timeout or 0.05), **kwargs)


def _wait(pred: Callable[[], bool], timeout: float,
          interval: float = 0.005) -> Optional[float]:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        if pred():
            return time.perf_counter() - t0
        time.sleep(interval)
    return None


class _TimedBackend:
    """透传后端，只额外记录每次 connect() 返回的时刻。

    退避只能量「上一次 connect 返回到下一次 on_reconnecting」的间隔；
    直接看相邻 on_reconnecting 的时间差会把 connect 自身耗时（本例 ~50ms）
    混进退避里，读数失真。
    """

    def __init__(self, inner):
        self.inner = inner
        self.connect_returns: List[float] = []

    def connect(self) -> bool:
        try:
            return bool(self.inner.connect())
        finally:
            self.connect_returns.append(time.perf_counter())

    def get_status(self):
        return self.inner.get_status()

    def is_connected(self) -> bool:
        return bool(self.inner.is_connected())


@pytest.mark.parametrize("kind", ["rtl_tcp", "spyserver"])
def test_reconnect_storm_50_rounds_bounded_with_backoff(monkeypatch, kind):
    """服务端拒绝时连打 50 轮重试：退避真实发生、次数有上限、线程不堆积。

    每轮 = ReconnectionManager 对关闭端口最多重试 3 次后 give_up；50 轮后
    线程数必须回到基线，句柄不得随轮次增长。
    """
    monkeypatch.setattr(socket, "create_connection", _short_connect)
    monkeypatch.setattr(_rtltcp_module, "RECONNECT_COOLDOWN_S", 0.0)
    port = _free_port()          # 无人监听 → 连接被拒/超时

    if kind == "rtl_tcp":
        backend = _TimedBackend(
            RtlTcpBackend("127.0.0.1", port, idle_timeout=0.5))
    else:
        backend = _TimedBackend(
            SpyServerBackend("127.0.0.1", port, idle_timeout=0.5))

    # base=50ms：Windows 定时器粒度约 15.6ms，太小（如 20ms）量不出 1×/2×
    # 的差别；50ms/100ms 的退避差足够跨过粒度噪声。
    base_delay, max_delay, max_attempts = 0.05, 0.4, 3
    stamps: List[float] = []
    manager = ReconnectionManager(
        on_reconnecting=lambda attempt: stamps.append(time.perf_counter()),
        base_delay=base_delay,
        max_delay=max_delay,
        max_attempts=max_attempts,
        poll_interval=0.01,
        source_kind="network",
    )

    baseline = _sample()
    samples: List[dict] = []
    peak_threads = baseline["threads_active"]
    per_round_min_backoff = sum(
        compute_backoff(i, base_delay, max_delay)
        for i in range(1, max_attempts + 1))

    for round_idx in range(50):
        t_round = time.perf_counter()
        before = len(stamps)
        manager.start(backend)
        assert _wait(lambda: manager.state == "gave_up", timeout=5.0) is not None, (
            f"第 {round_idx + 1} 轮未在 5s 内 give_up（可能无限重试）")
        elapsed = time.perf_counter() - t_round
        assert len(stamps) - before == max_attempts, (
            f"第 {round_idx + 1} 轮重试 {len(stamps) - before} 次，"
            f"上限应为 {max_attempts}")
        assert manager.snapshot()["attempts_total"] == max_attempts
        # 退避必须真的等出来（至少合计退避的 80%）
        assert elapsed >= per_round_min_backoff * 0.8, (
            f"第 {round_idx + 1} 轮 {elapsed:.3f}s 未体现退避 "
            f"（应 ≥ {per_round_min_backoff:.3f}s×0.8）")
        manager.stop()
        peak_threads = max(peak_threads, threading.active_count())
        assert not [t for t in threading.enumerate()
                    if t.name == "reconnection-manager"], (
            f"第 {round_idx + 1} 轮 stop() 后仍有 reconnection-manager 线程")
        if (round_idx + 1) % 10 == 0:
            s = _sample()
            samples.append(s)
            print(f"[storm:{kind}] round {round_idx + 1:2d}: {s}")

    # 线程：除唯一的监控线程外不得堆积
    assert peak_threads <= baseline["threads_active"] + 1, (
        f"重连风暴期间线程数峰值 {peak_threads} > 基线 "
        f"{baseline['threads_active']}+1")
    _assert_no_growth([baseline] + samples, f"storm:{kind}")

    # 退避真实发生：上次 connect 返回到下一次 on_reconnecting 的间隔，
    # 第 2/3 次尝试前应分别为 compute_backoff(1)=1×base、compute_backoff(2)=2×base
    for round_idx in range(50):
        start = round_idx * max_attempts
        for attempt in range(2, max_attempts + 1):
            wait = stamps[start + attempt - 1] - backend.connect_returns[start + attempt - 2]
            # 第 attempt 次尝试前的等待 = 上一次（attempt-1）失败后的退避
            expected = compute_backoff(attempt - 1, base_delay, max_delay)
            assert wait >= expected * 0.7, (
                f"第 {round_idx + 1} 轮第 {attempt} 次重试前只等了 {wait:.3f}s，"
                f"退避应为 {expected:.3f}s（未退避 = 风暴式重试）")
        # 退避必须递增（不是固定间隔）
        wait1 = stamps[start + 1] - backend.connect_returns[start]
        wait2 = stamps[start + 2] - backend.connect_returns[start + 1]
        assert wait2 >= wait1 * 1.4, (
            f"第 {round_idx + 1} 轮退避未增长："
            f"wait1={wait1:.3f}s wait2={wait2:.3f}s")
