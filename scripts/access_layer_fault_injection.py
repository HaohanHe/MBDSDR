#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""接入层故障注入回归（无硬件 / 无外网 / 不打开声卡）。

对 rtl_tcp 与 SpyServer 用本机 loopback 假服务，按脚本制造真实世界的中断：
拒绝连接、握手不对、半开、中途掉线、长时间无数据、RST 被踢、采样率不匹配；
再叠加重连状态机、USB 占用 / 拔插与数据健康度指标。

每行输出 "场景 → 期望 / 实测"，全部符合预期时退出码 0，否则 1。

用法（仓库根目录）::

    python scripts/access_layer_fault_injection.py
    python scripts/access_layer_fault_injection.py --json

纪律：脚本自身把 MBDSDR_NO_HW / MBDSDR_NULL_AUDIO / MBDSDR_OFFLINE 置 1
（外部显式设值时尊重外部值），从不碰真实 USB、真实网络与真实声卡。

口径说明：rtl_tcp / SpyServer 跑在 TCP 上，传输层本身不产生乱序，"丢包"
只能是"按期望速率应到而未到"的样本缺口估计（见 DataHealthTracker）；两种
协议都没有内置账号密码，所谓"鉴权失败"指服务端在握手/连接阶段明确拒绝，
归类为 ``auth`` 并给出放行建议。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import struct
import sys
import threading
import time
import types
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_OFFLINE", "1")

from mbdsdr_ai import link_state  # noqa: E402
from mbdsdr_ai.reconnection import ReconnectionManager  # noqa: E402
from mbdsdr_ai.rtltcp_client import RtlTcpClient  # noqa: E402
from mbdsdr_ai.sdr_backend import RTLSDRBackend  # noqa: E402
from mbdsdr_ai.spyserver_client import (  # noqa: E402
    SpyServerClient,
    SpyServerError,
)

_RTL_HEADER = b"RTL0" + struct.pack(">II", 5, 29)
_SPY_TEXT = b"Name=FakeSpyServer\0SampleRate=2048000\0Version=0\0"
_SPY_HANDSHAKE = (_SPY_TEXT + b"\0" * (1024 - len(_SPY_TEXT)))[:1024]


# ═══════════════════════════════════════════════════════════════════════
# 假 TCP 服务（仅本机回环）
# ═══════════════════════════════════════════════════════════════════════

def _iq_bytes(n_samples: int, seed: int = 1) -> bytes:
    """确定性 uint8 IQ 字节，只喂给 loopback 假源，不冒充真实测量。"""
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=n_samples * 2, dtype=np.uint8).tobytes()


@contextmanager
def _tcp_server(handler: Callable[[socket.socket, threading.Event], None]
                ) -> Iterator[int]:
    """启动单连接假 TCP 服务，yield 监听端口；退出时确保连接与线程收摊。"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = int(srv.getsockname()[1])
    stop = threading.Event()
    held: Dict[str, Optional[socket.socket]] = {"conn": None}

    def run() -> None:
        try:
            srv.settimeout(3.0)
            conn, _ = srv.accept()
        except OSError:
            return
        held["conn"] = conn
        try:
            handler(conn, stop)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    t = threading.Thread(target=run, name="fault-injection-server", daemon=True)
    t.start()
    try:
        yield port
    finally:
        stop.set()
        try:
            srv.close()
        except OSError:
            pass
        conn = held["conn"]
        if conn is not None:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        t.join(timeout=1.5)


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def _wait_until(pred: Callable[[], bool], timeout: float = 3.0,
                interval: float = 0.02) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(interval)
    return bool(pred())


@contextmanager
def _with_rtlsdr_module(rtl_cls: Any) -> Iterator[None]:
    """临时把 ``rtlsdr`` 模块替换成本地故障注入替身（不触真实 USB）。"""
    old = sys.modules.get("rtlsdr")
    mod = types.ModuleType("rtlsdr")
    mod.RtlSdr = rtl_cls
    sys.modules["rtlsdr"] = mod
    orig_probe = RTLSDRBackend._probe_tuner_type
    RTLSDRBackend._probe_tuner_type = staticmethod(lambda _idx: 5)
    try:
        yield
    finally:
        RTLSDRBackend._probe_tuner_type = orig_probe
        if old is not None:
            sys.modules["rtlsdr"] = old
        else:
            sys.modules.pop("rtlsdr", None)


class _StubDevice:
    """故障注入用最小设备替身；只服务拔插/占用场景，不进入生产路径。"""

    def __init__(self, device_index: int = 0, unplug_after: Optional[int] = None):
        self.tuner_type = 5
        self.closed = False
        self._unplug_after = unplug_after
        self._reads = 0
        self._center_freq = 0
        self._sample_rate = 2_048_000.0
        self._gain = 20.0
        self._freq_correction = 0
        self._bandwidth = 0

    @property
    def center_freq(self) -> int:
        return self._center_freq

    @center_freq.setter
    def center_freq(self, value: Any) -> None:
        self._center_freq = int(value)

    @property
    def sample_rate(self) -> float:
        return self._sample_rate

    @sample_rate.setter
    def sample_rate(self, value: Any) -> None:
        self._sample_rate = float(value)

    @property
    def gain(self) -> float:
        return self._gain

    @gain.setter
    def gain(self, value: Any) -> None:
        self._gain = float(value)

    @property
    def freq_correction(self) -> int:
        return self._freq_correction

    @freq_correction.setter
    def freq_correction(self, value: Any) -> None:
        self._freq_correction = int(value)

    @property
    def bandwidth(self) -> int:
        return self._bandwidth

    @bandwidth.setter
    def bandwidth(self, value: Any) -> None:
        self._bandwidth = int(value)

    def set_manual_gain_mode(self, _mode: Any) -> None:
        return None

    def set_agc_mode(self, _enabled: Any) -> None:
        return None

    def set_direct_sampling(self, _mode: Any) -> None:
        return None

    def set_bias_tee(self, _enabled: Any) -> None:
        return None

    def set_offset_tuning(self, _enabled: Any) -> None:
        return None

    def reset_buffer(self) -> None:
        return None

    def read_samples(self, n: int) -> np.ndarray:
        if self.closed:
            raise OSError("FakeRtlSdr: device closed")
        self._reads += 1
        if self._unplug_after is not None and self._reads > self._unplug_after:
            raise OSError("LIBUSB_ERROR_NO_DEVICE: device unplugged")
        # 仅用作"有数据在流动"的信号；不是对外测量结果。
        return np.ones(int(n), dtype=np.complex64)

    def close(self) -> None:
        self.closed = True


# ═══════════════════════════════════════════════════════════════════════
# 场景实现：返回 (expected, observed)；expected 值可为谓词
# ═══════════════════════════════════════════════════════════════════════

Expected = Dict[str, Any]
Scenario = Callable[[], Tuple[Expected, Dict[str, Any]]]


def _rtl_refused() -> Tuple[Expected, Dict[str, Any]]:
    c = RtlTcpClient("127.0.0.1", _free_port())
    c.connect()
    return (
        {"connected": False, "state": "failed", "category": "refused",
         "message_contains": lambda v: "连接失败" in v},
        {"connected": c.connected, "state": c.state, "category": c.error_category,
         "message_contains": c.error, "suggestion": c.suggestion,
         "sources": f"{c.host}:{c.port}"},
    )


def _rtl_wrong_magic() -> Tuple[Expected, Dict[str, Any]]:
    def handler(conn: socket.socket, stop: threading.Event) -> None:
        conn.sendall(b"XXXX" + struct.pack(">II", 5, 29))
        stop.wait(0.3)

    with _tcp_server(handler) as port:
        c = RtlTcpClient("127.0.0.1", port)
        c.connect()
        return (
            {"connected": False, "state": "failed", "category": "handshake",
             "message_contains": lambda v: "不是 rtl_tcp" in v},
            {"connected": c.connected, "state": c.state,
             "category": c.error_category, "message_contains": c.error},
        )


def _rtl_half_open() -> Tuple[Expected, Dict[str, Any]]:
    def handler(conn: socket.socket, stop: threading.Event) -> None:
        conn.sendall(b"RTL")   # 12 字节握手只发 3 字节
        stop.wait(0.5)

    with _tcp_server(handler) as port:
        c = RtlTcpClient("127.0.0.1", port, idle_timeout=0.5)
        t0 = time.time()
        ok = c.connect()
        elapsed = time.time() - t0
        return (
            {"connected": False, "state": "failed",
             "category": ("handshake", "timeout"),
             "bounded_seconds": lambda v: v < 2.0},
            {"connected": c.connected, "state": c.state,
             "category": c.error_category, "bounded_seconds": round(elapsed, 3),
             "message": c.error},
        )


def _rtl_idle_stale() -> Tuple[Expected, Dict[str, Any]]:
    resume = threading.Event()

    def handler(conn: socket.socket, stop: threading.Event) -> None:
        conn.sendall(_RTL_HEADER)
        while not stop.is_set() and not resume.is_set():
            time.sleep(0.02)
        while not stop.is_set():
            conn.sendall(_iq_bytes(1024, seed=7))
            time.sleep(0.01)

    with _tcp_server(handler) as port:
        c = RtlTcpClient("127.0.0.1", port, idle_timeout=0.5)
        c.connect()
        t0 = time.time()
        first = c.read_samples(1024)
        elapsed = time.time() - t0
        out: Dict[str, Any] = {
            "returned_none": first is None,
            "state_while_quiet": c.state,
            "connected_while_quiet": c.connected,
            "category": c.error_category,
            "elapsed_s": round(elapsed, 3),
        }
        resume.set()
        resumed = c.read_samples(1024)
        out["recovered_to_connected"] = (resumed is not None
                                         and c.state == link_state.STATE_CONNECTED)
    return (
        {"returned_none": True, "state_while_quiet": "stale",
         "connected_while_quiet": True, "category": "idle",
         "elapsed_s": lambda v: v < 2.0, "recovered_to_connected": True},
        out,
    )


def _rtl_mid_stream_drop() -> Tuple[Expected, Dict[str, Any]]:
    def handler(conn: socket.socket, stop: threading.Event) -> None:
        conn.sendall(_RTL_HEADER + _iq_bytes(64, seed=3))
        stop.wait(0.05)

    with _tcp_server(handler) as port:
        c = RtlTcpClient("127.0.0.1", port, idle_timeout=0.5)
        c.connect()
        got = 0
        for _ in range(20):
            s = c.read_samples(8)
            if s is None:
                break
            got += len(s)
        return (
            {"got_samples_before_drop": lambda v: v > 0,
             "state": "disconnected", "connected": False, "category": "eof"},
            {"got_samples_before_drop": got, "state": c.state,
             "connected": c.connected, "category": c.error_category,
             "message": c.error},
        )


def _rtl_kicked_rst() -> Tuple[Expected, Dict[str, Any]]:
    connected = threading.Event()

    def handler(conn: socket.socket, stop: threading.Event) -> None:
        conn.sendall(_RTL_HEADER)
        connected.wait(2.0)
        conn.sendall(_iq_bytes(32, seed=5))
        time.sleep(0.02)
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                        struct.pack("ii", 1, 0))
        conn.close()

    with _tcp_server(handler) as port:
        c = RtlTcpClient("127.0.0.1", port, idle_timeout=0.5)
        c.connect()
        connected.set()
        for _ in range(12):
            if c.read_samples(8) is None:
                break
            time.sleep(0.01)
        return (
            {"state": "disconnected", "connected": False,
             "category": ("reset", "unknown")},
            {"state": c.state, "connected": c.connected,
             "category": c.error_category, "message": c.error},
        )


def _rtl_throughput_mismatch() -> Tuple[Expected, Dict[str, Any]]:
    def handler(conn: socket.socket, stop: threading.Event) -> None:
        conn.sendall(_RTL_HEADER)
        while not stop.is_set():
            conn.sendall(_iq_bytes(1024, seed=9))
            time.sleep(0.01)

    with _tcp_server(handler) as port:
        c = RtlTcpClient("127.0.0.1", port, idle_timeout=0.5)
        c.connect()
        c.set_sample_rate(2_048_000)
        got = 0
        # 连续读够 ~200ms，让健康度口径有时间基（低于 0.15s 不结算速率）
        for _ in range(20):
            s = c.read_samples(1024)
            if s is not None:
                got += len(s)
        h = c.data_health()
        return (
            {"got_samples": lambda v: v > 0,
             "rate_ratio": lambda v: v is not None and v < 0.3,
             "measured_lt_expected": lambda v: v is True},
            {"got_samples": got, "rate_ratio": h["rate_ratio"],
             "observed_rate_hz": h["observed_rate_hz"],
             "expected_rate_hz": h["expected_rate_hz"],
             "measured_lt_expected": (
                 h["observed_rate_hz"] is not None
                 and h["observed_rate_hz"] < h["expected_rate_hz"]),
             "dropped_samples": h["dropped_samples"],
             "gap_events": h["gap_events"]},
        )


def _spy_handshake_fail() -> Tuple[Expected, Dict[str, Any]]:
    def handler(conn: socket.socket, stop: threading.Event) -> None:
        conn.sendall(b"\x00" * 1024)
        stop.wait(0.3)

    with _tcp_server(handler) as port:
        c = SpyServerClient("127.0.0.1", port, timeout=0.5)
        raised = ""
        try:
            c.start()
        except SpyServerError as e:
            raised = str(e)
        return (
            {"raised": True, "state": "failed", "category": "handshake",
             "connected": False,
             "message_contains": lambda v: "不是 SpyServer" in v},
            {"raised": bool(raised), "state": c.state,
             "category": c.error_category, "connected": c.connected,
             "message_contains": raised},
        )


def _spy_idle_stale() -> Tuple[Expected, Dict[str, Any]]:
    def handler(conn: socket.socket, stop: threading.Event) -> None:
        conn.sendall(_SPY_HANDSHAKE)
        stop.wait(1.0)

    with _tcp_server(handler) as port:
        c = SpyServerClient("127.0.0.1", port, timeout=0.5)
        c.start()
        raised = ""
        try:
            c.read_samples(16)
        except SpyServerError as e:
            raised = str(e)
        return (
            {"raised": True, "state": "stale", "connected": True,
             "category": "idle"},
            {"raised": bool(raised), "state": c.state, "connected": c.connected,
             "category": c.error_category, "message": c.error},
        )


def _spy_mid_stream_drop() -> Tuple[Expected, Dict[str, Any]]:
    def handler(conn: socket.socket, stop: threading.Event) -> None:
        conn.sendall(_SPY_HANDSHAKE + _iq_bytes(32, seed=19))
        stop.wait(0.05)

    with _tcp_server(handler) as port:
        c = SpyServerClient("127.0.0.1", port, timeout=0.5)
        c.start()
        for _ in range(8):
            try:
                c.read_samples(32)
            except SpyServerError:
                break
            time.sleep(0.02)
        return (
            {"state": "disconnected", "connected": False},
            {"state": c.state, "connected": c.connected,
             "category": c.error_category, "message": c.error},
        )


def _spy_rate_mismatch() -> Tuple[Expected, Dict[str, Any]]:
    def handler(conn: socket.socket, stop: threading.Event) -> None:
        conn.sendall(_SPY_HANDSHAKE)
        while not stop.is_set():
            try:
                conn.settimeout(0.2)
                conn.recv(4096)   # 丢弃客户端命令，保持连接
            except OSError:
                pass

    with _tcp_server(handler) as port:
        c = SpyServerClient("127.0.0.1", port, timeout=0.5)
        c.start()
        c.set_sample_rate(1_024_000)
        h = c.data_health()
        return (
            {"server_sample_rate_hz": 2_048_000,
             "requested_sample_rate_hz": 1_024_000,
             "sample_rate_mismatch": True, "state": "connected"},
            {"server_sample_rate_hz": h["server_sample_rate_hz"],
             "requested_sample_rate_hz": h["requested_sample_rate_hz"],
             "sample_rate_mismatch": h["sample_rate_mismatch"],
             "state": h["state"]},
        )


class _FakeStatus:
    def __init__(self) -> None:
        self.connected = False
        self.error = ""
        self.link_state = "disconnected"
        self.reconnect_count = 0


class _FlakyBackend:
    def __init__(self, fail_remaining: int) -> None:
        self.status = _FakeStatus()
        self.fail_remaining = int(fail_remaining)

    def get_status(self) -> _FakeStatus:
        return self.status

    def is_connected(self) -> bool:
        return self.status.connected

    def connect(self) -> bool:
        if self.fail_remaining > 0:
            self.fail_remaining -= 1
            self.status.connected = False
            self.status.error = "连接失败: 指令被拒绝"
            return False
        self.status.connected = True
        self.status.link_state = "connected"
        self.status.error = ""
        return True


def _reconnect_recovers() -> Tuple[Expected, Dict[str, Any]]:
    be = _FlakyBackend(fail_remaining=3)
    mgr = ReconnectionManager(base_delay=0.02, max_delay=0.05,
                              max_attempts=10, poll_interval=0.02,
                              source_kind="network")
    mgr.start(be)
    try:
        _wait_until(lambda: be.status.connected, 3.0)
        snap = mgr.snapshot()
        return (
            {"state": "connected", "attempts_total": 4,
             "reconnects_total": 1, "last_error": ""},
            {"state": snap["state"], "attempts_total": snap["attempts_total"],
             "reconnects_total": snap["reconnects_total"],
             "last_error": snap["last_error"]},
        )
    finally:
        mgr.stop()


def _reconnect_gives_up() -> Tuple[Expected, Dict[str, Any]]:
    be = _FlakyBackend(fail_remaining=99)
    mgr = ReconnectionManager(base_delay=0.01, max_delay=0.02,
                              max_attempts=3, poll_interval=0.02,
                              source_kind="network")
    mgr.start(be)
    try:
        _wait_until(lambda: mgr.state == "gave_up", 3.0)
        snap = mgr.snapshot()
        return (
            {"state": "gave_up", "gave_up": True, "max_attempts": 3,
             "suggestion_has_next_step": lambda v: isinstance(v, str)
             and "rtl_tcp" in v,
             "last_error": lambda v: bool(v)},
            {"state": snap["state"], "gave_up": snap["gave_up"],
             "max_attempts": snap["max_attempts"],
             "suggestion_has_next_step": snap["suggestion"],
             "last_error": snap["last_error"]},
        )
    finally:
        mgr.stop()


def _auth_classification() -> Tuple[Expected, Dict[str, Any]]:
    cat, msg, sug = link_state.classify_handshake_reason(
        "Access denied by server (auth)")
    return (
        {"category": "auth", "message_contains": lambda v: "连接失败" in v,
         "suggestion_actionable": lambda v: "放行" in v or "访问控制" in v},
        {"category": cat, "message_contains": msg,
         "suggestion_actionable": sug},
    )


def _usb_busy() -> Tuple[Expected, Dict[str, Any]]:
    class _BusyRtlSdr:
        def __init__(self, _idx: int = 0):
            raise OSError("usb_open error -3: Unable to open device")

    with _with_rtlsdr_module(_BusyRtlSdr):
        be = RTLSDRBackend(device_index=0)
        ok = be.connect()
        return (
            {"ok": False, "state": "failed", "category": "busy",
             "connected": False, "handle_released": True,
             "message_contains": lambda v: "设备被占用" in v},
            {"ok": ok, "state": be.status.link_state,
             "category": be.status.error_category,
             "connected": be.status.connected,
             "handle_released": be._sdr is None,
             "message_contains": be.status.error},
        )


def _usb_hotplug_replug() -> Tuple[Expected, Dict[str, Any]]:
    devices: List[_StubDevice] = []

    class _Factory:
        def __init__(self) -> None:
            self.count = 0

        def __call__(self, idx: int = 0) -> _StubDevice:
            self.count += 1
            dev = _StubDevice(
                device_index=idx,
                unplug_after=3 if self.count == 1 else None)
            devices.append(dev)
            return dev

    with _with_rtlsdr_module(_Factory()):
        be = RTLSDRBackend(device_index=0)
        ok = be.connect()
        first = devices[0] if devices else None
        unplugged = _wait_until(lambda: be.status.connected is False, 3.0)
        state_after_unplug = be.status.link_state
        message_after_unplug = be.status.error
        reconnected = False
        single_producer = False
        data_ok = False
        if unplugged:
            reconnected = be.connect()
            producers = [t for t in threading.enumerate()
                         if t.name == "mbdsdr-ring-producer" and t.is_alive()]
            single_producer = len(producers) == 1
            s = be.read_samples(4096)
            data_ok = s is not None and len(s) == 4096
        out = {
            "first_connect": ok,
            "unplug_detected": unplugged,
            "state_after_unplug": state_after_unplug,
            "message_after_unplug": message_after_unplug,
            "reconnected": reconnected,
            "old_handle_closed": bool(first is not None and first.closed),
            "single_producer": single_producer,
            "data_flows_after_replug": data_ok,
        }
        be.disconnect()
        return (
            {"first_connect": True, "unplug_detected": True,
             "state_after_unplug": "disconnected",
             "message_after_unplug": lambda v: "读设备失败" in v,
             "reconnected": True, "old_handle_closed": True,
             "single_producer": True, "data_flows_after_replug": True},
            out,
        )


def _metrics_and_buffer_levels() -> Tuple[Expected, Dict[str, Any]]:
    now = [100.0]
    tr = link_state.DataHealthTracker(
        sample_rate_hz=1_000_000, stale_after=0.5, gap_tolerance_s=0.005,
        clock=lambda: now[0])
    tr.on_connect(now=now[0])
    now[0] += 0.1
    tr.on_data(100_000, n_bytes=200_000, now=now[0])
    now[0] += 0.1
    tr.on_data(30_000, n_bytes=60_000, now=now[0])
    h = tr.snapshot(state="connected", now=now[0])

    levels = {
        "empty": link_state.classify_buffer_level(0, 1000)["level"],
        "low": link_state.classify_buffer_level(50, 1000)["level"],
        "ok": link_state.classify_buffer_level(500, 1000)["level"],
        "high": link_state.classify_buffer_level(900, 1000)["level"],
    }
    return (
        {"levels": {"empty": "empty", "low": "low",
                    "ok": "ok", "high": "high"},
         "drop_ratio": lambda v: isinstance(v, float) and v > 0.3,
         "rate_ratio": lambda v: v is not None and v < 0.8,
         "gap_events": lambda v: v >= 0},
        {"levels": levels, "drop_ratio": h["drop_ratio"],
         "dropped_samples": h["dropped_samples"],
         "rate_ratio": h["rate_ratio"], "gap_events": h["gap_events"],
         "reconnect_count": h["reconnect_count"]},
    )


SCENARIOS: List[Tuple[str, str, Scenario]] = [
    ("rtl_tcp/refused", "连接被拒 → failed/refused，不谎报在线", _rtl_refused),
    ("rtl_tcp/wrong-magic", "非 rtl_tcp 服务 → failed/handshake", _rtl_wrong_magic),
    ("rtl_tcp/half-open", "握手只发一半 → failed，且有界不挂死", _rtl_half_open),
    ("rtl_tcp/idle", "连上后长时间无数据 → stale/idle，仍连接", _rtl_idle_stale),
    ("rtl_tcp/mid-stream-drop", "传输中途 FIN → disconnected/eof", _rtl_mid_stream_drop),
    ("rtl_tcp/kicked-rst", "被服务端 RST 踢出 → disconnected", _rtl_kicked_rst),
    ("rtl_tcp/throughput", "实际吞吐远低于请求采样率 → rate_ratio 暴露", _rtl_throughput_mismatch),
    ("spyserver/handshake", "握手串无 key=value → failed/handshake", _spy_handshake_fail),
    ("spyserver/idle", "握手后无 IQ → stale/idle，仍连接", _spy_idle_stale),
    ("spyserver/mid-stream-drop", "中途 FIN → disconnected", _spy_mid_stream_drop),
    ("spyserver/rate-mismatch", "请求 1.024M / 服务端 2.048M → 明确不匹配", _spy_rate_mismatch),
    ("reconnect/retry-recover", "3 次失败后恢复 → connected + 真实计数", _reconnect_recovers),
    ("reconnect/give-up", "超上限 → gave_up + 下一步建议", _reconnect_gives_up),
    ("access/auth-classification", "服务端拒绝 → auth 类别 + 放行建议", _auth_classification),
    ("usb/busy", "usb_open error -3 → failed/busy 且句柄释放", _usb_busy),
    ("usb/hotplug-replug", "拔出被检测 → 插回可恢复且只留一个生产者", _usb_hotplug_replug),
    ("metrics/buffer-levels", "丢包率/缺口/重连计数/缓冲水位可读", _metrics_and_buffer_levels),
]


def _compare(expected: Expected, observed: Dict[str, Any]) -> List[str]:
    bad: List[str] = []
    for key, want in expected.items():
        got = observed.get(key)
        if callable(want):
            if not want(got):
                bad.append(f"{key}: expected predicate, got {got!r}")
        elif isinstance(want, tuple):
            if got not in want:
                bad.append(f"{key}: expected one of {want}, got {got!r}")
        elif got != want:
            bad.append(f"{key}: expected {want!r}, got {got!r}")
    return bad


def _describe(expected: Expected) -> str:
    parts = []
    for k, v in expected.items():
        if callable(v):
            parts.append(f"{k}=<predicate>")
        elif isinstance(v, tuple):
            parts.append(f"{k}∈{v}")
        else:
            parts.append(f"{k}={v!r}")
    return ", ".join(parts)


def main() -> int:
    # 故障场景会故意触发警告日志（含 traceback）；--json 输出必须保持纯 JSON。
    logging.basicConfig(level=logging.ERROR)

    ap = argparse.ArgumentParser(description="接入层故障注入回归（无硬件）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 结构化结果")
    args = ap.parse_args()

    results = []
    failed = 0
    for name, intent, fn in SCENARIOS:
        t0 = time.time()
        try:
            expected, observed = fn()
            bad = _compare(expected, observed)
        except Exception as e:  # noqa: BLE001
            expected, observed = {}, {"exception": f"{type(e).__name__}: {e}"}
            bad = [f"scenario raised {type(e).__name__}: {e}"]
        elapsed = round(time.time() - t0, 3)
        ok = not bad
        failed += 0 if ok else 1
        results.append({
            "name": name,
            "intent": intent,
            "ok": ok,
            "elapsed_s": elapsed,
            "expected": _describe(expected),
            "mismatch": bad,
            "observed": observed,
        })
        if not args.json:
            print(f"[{'PASS' if ok else 'FAIL'}] {name} ({elapsed}s) — {intent}")
            if bad:
                for b in bad:
                    print(f"         ✗ {b}")
                print(f"         observed={observed}")

    total = len(results)
    if args.json:
        print(json.dumps({"total": total, "failed": failed,
                          "passed": total - failed, "results": results},
                         ensure_ascii=False, indent=2))
    else:
        print(f"\n接入层故障注入：{total - failed}/{total} 符合预期"
              f"（无硬件 / 无声卡 / 无外网）")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
