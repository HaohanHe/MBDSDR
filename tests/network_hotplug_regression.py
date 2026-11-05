#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
r"""热插拔 / 重连回归：四条路径一条命令复跑。

四条路径（与 docs/network-layer.md §2/§3/§7 的口径一致）：

  1. ``usb-unplug``    真机 USB 拔线：需要物理拔插，脚本不伪造 —— 无设备或无
                       ``MBDSDR_NO_HW=1`` 时输出 **SKIP + 原因**。
  2. ``port-switch``   换端口：服务端从 A 口迁到 B 口，新后端连 B 并取首帧，
                       打印两次 connect→首帧耗时。
  3. ``backend-crash`` 后端进程被杀（Windows TerminateProcess）：测发现耗时、
                       发现方式与状态机终态。
  4. ``half-open``     半开连接（服务端死、TCP 仍保持）：rtl_tcp 与 SpyServer
                       各测一遍，默认 ``idle_timeout=5s``，要求 ``2×idle`` 内硬判。

用法（PowerShell，仓库根执行）：

    $env:MBDSDR_NO_HW='1'; $env:MBDSDR_OFFLINE='1'; $env:MBDSDR_NULL_AUDIO='1'
    $env:PYTHONPATH='d:\mbdsdr\MBDSDR'
    python tests/network_hotplug_regression.py

输出每行 ``[hotplug] case=... verdict=PASS|FAIL|SKIP elapsed=...s detail=...``；
退出码 0 = 没有 FAIL（SKIP 不算通过），1 = 有 FAIL。

约定：假服务与 HTTP 无关，全部走本机 loopback；临时进程用 ``python -c`` 内联，
不落脚本文件；失败也回收进程/线程/客户端。SKIP 必须写明原因，不许冒充 PASS。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mbdsdr_ai import link_state                       # noqa: E402
from mbdsdr_ai.spyserver_client import SpyServerBackend  # noqa: E402
from test_network_layer_matrix import (                # noqa: E402
    _RTL_HEADER,
    _SPY_HANDSHAKE,
    _assert_frame_is_normal,
    _find_free_port,
    _iq_bytes,
    _make_rtl_backend,
    _measure,
    _read_frame_until,
    _tcp_server_loop,
)

_DEFAULT_IDLE = 5.0


def _rtl_stream_handler(conn, idx, stop):
    conn.sendall(_RTL_HEADER)
    while not stop.is_set():
        conn.sendall(_iq_bytes(2048, seed=81 + idx))
        time.sleep(0.005)


# ── 1. 真机 USB 拔线 ───────────────────────────────────────────────────
def case_usb_unplug():
    if os.environ.get("MBDSDR_NO_HW") == "1":
        return "SKIP", "MBDSDR_NO_HW=1（本进程禁止碰真硬件）；拔线需人工执行"
    try:
        from rtlsdr import RtlSdr          # noqa: F401
    except Exception as exc:
        return "SKIP", (f"pyrtlsdr/librtlsdr 不可用："
                        f"{exc.__class__.__name__}: {exc}")
    try:
        count = int(RtlSdr.get_device_count())
    except Exception as exc:
        return "SKIP", f"RTL-SDR 枚举失败：{exc.__class__.__name__}: {exc}"
    if count <= 0:
        return "SKIP", "未枚举到 RTL-SDR 设备；拔线路径需要真实硬件"
    return "SKIP", (
        f"枚举到 {count} 个 RTL-SDR，但脚本无法程序化拔插 USB；"
        "需人工拔线并观察 connected→False / link_state→disconnected / "
        "重连后首帧正常——本脚本不把人工步骤冒充 PASS")


# ── 2. 换端口 ──────────────────────────────────────────────────────────
def case_port_switch():
    t_all = time.perf_counter()
    with _tcp_server_loop(_rtl_stream_handler, accept_count=1) as (port_a, stop_a, _t):
        t0 = time.perf_counter()
        be_a = _make_rtl_backend(port_a, idle=_DEFAULT_IDLE)
        try:
            assert be_a.connect() is True, f"端口 {port_a} 建连失败"
            frame_a = _read_frame_until(be_a, 1024, 3.0)
            _assert_frame_is_normal(frame_a, 1024)
            t_a = time.perf_counter() - t0
        finally:
            be_a.disconnect()
            stop_a.set()

    with _tcp_server_loop(_rtl_stream_handler, accept_count=1) as (port_b, stop_b, _t):
        assert port_b != port_a, "换端口用例没有真正换端口"
        t1 = time.perf_counter()
        be_b = _make_rtl_backend(port_b, idle=_DEFAULT_IDLE)
        try:
            assert be_b.connect() is True, f"端口 {port_b} 建连失败"
            frame_b = _read_frame_until(be_b, 1024, 3.0)
            _assert_frame_is_normal(frame_b, 1024)
            t_b = time.perf_counter() - t1
        finally:
            be_b.disconnect()
            stop_b.set()

    total = time.perf_counter() - t_all
    return "PASS", (f"port {port_a}→{port_b}; connect→首帧 "
                    f"{t_a:.3f}s / {t_b:.3f}s; 两条首帧均为 1024 点 complex64")


# ── 3. 后端进程崩溃 ───────────────────────────────────────────────────
_CRASH_SERVER_CODE = (
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


def case_backend_crash():
    port = _find_free_port()
    proc = subprocess.Popen(
        [sys.executable, "-c", _CRASH_SERVER_CODE, str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    be = None
    try:
        assert proc.stdout.readline().strip() == "READY", "假服务未就绪"
        t_all = time.perf_counter()
        be = _make_rtl_backend(port, idle=_DEFAULT_IDLE)
        assert be.connect() is True
        assert _read_frame_until(be, 1024, 3.0) is not None, "崩溃前没拿到首帧"
        t0 = time.perf_counter()
        proc.kill()                        # Windows TerminateProcess
        proc.wait(timeout=3)
        t_detect = _measure(lambda: be.status.connected is False, 3.0)
        assert t_detect is not None, "后端进程被杀后 3s 内仍未发现"
        assert be.status.link_state == link_state.STATE_DISCONNECTED
        assert be.status.error_category in (
            link_state.CATEGORY_EOF, link_state.CATEGORY_RESET,
            link_state.CATEGORY_UNKNOWN), f"发现方式异常: {be.status.error_category}"
        total = time.perf_counter() - t_all
        return "PASS", (f"detect={t_detect:.3f}s via={be.status.error_category} "
                        f"idle={_DEFAULT_IDLE}s state={be.status.link_state}")
    finally:
        if be is not None:
            be.disconnect()
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        if proc.stdout is not None:
            proc.stdout.close()


# ── 4. 半开连接：默认 5s 心跳，2×idle 内硬判 ─────────────────────────────
def _half_open_once(kind: str, idle: float):
    alive = threading.Event()
    alive.set()
    header = _RTL_HEADER if kind == "rtl" else _SPY_HANDSHAKE

    def handler(conn, idx, stop):
        conn.sendall(header)
        conn.sendall(_iq_bytes(20_000, seed=91))
        while not stop.is_set():
            if not alive.is_set():
                time.sleep(0.02)           # 保持 TCP，不转发任何 IQ
                continue
            conn.sendall(_iq_bytes(2048, seed=93))
            time.sleep(0.05)

    with _tcp_server_loop(handler, accept_count=1) as (port, stop, _t):
        be = (_make_rtl_backend(port, idle=idle) if kind == "rtl"
              else SpyServerBackend("127.0.0.1", port, idle_timeout=idle))
        try:
            assert be.connect() is True
            assert _read_frame_until(be, 1024, 4.0) is not None, "半开前没拿到首帧"
            alive.clear()
            t_stale = _measure(
                lambda: be.status.link_state == link_state.STATE_STALE, idle + 2.0)
            assert t_stale is not None, f"{kind} 半开没有心跳兜底"
            t_hard = _measure(lambda: be.status.connected is False, idle + 2.0)
            assert t_hard is not None, f"{kind} 半开 stale 后没有硬判"
            total = t_stale + t_hard
            assert 2 * idle - 0.5 <= total <= 2 * idle + 0.9, (
                f"{kind} 硬判越界：{total:.3f}s，应落在 "
                f"[{2 * idle - 0.5:.1f}, {2 * idle + 0.9:.1f}]s")
            assert be.status.error_category == link_state.CATEGORY_IDLE, (
                f"{kind} 硬判分类不是 idle: {be.status.error_category}")
            return t_stale, total
        finally:
            be.disconnect()
            stop.set()


def case_half_open():
    t_all = time.perf_counter()
    rtl_stale, rtl_hard = _half_open_once("rtl", _DEFAULT_IDLE)
    spy_stale, spy_hard = _half_open_once("spy", _DEFAULT_IDLE)
    total = time.perf_counter() - t_all
    return "PASS", (f"idle={_DEFAULT_IDLE:.0f}s rtl stale={rtl_stale:.3f}s "
                    f"hard={rtl_hard:.3f}s; spy stale={spy_stale:.3f}s "
                    f"hard={spy_hard:.3f}s（硬判基准 2×idle={2 * _DEFAULT_IDLE:.1f}s，"
                    f"容差 +0.9s）")


CASES = (
    ("usb-unplug", case_usb_unplug),
    ("port-switch", case_port_switch),
    ("backend-crash", case_backend_crash),
    ("half-open", case_half_open),
)


def main() -> int:
    print("[hotplug] 热插拔/重连回归：4 条路径（本机 loopback 假服务，真机 USB 仅 SKIP 口径）")
    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    for name, fn in CASES:
        t0 = time.perf_counter()
        try:
            verdict, detail = fn()
        except AssertionError as exc:
            verdict, detail = "FAIL", f"断言失败: {exc}"
        except Exception as exc:            # noqa: BLE001 - 任何异常都必须如实报红
            verdict, detail = "FAIL", (
                f"{exc.__class__.__name__}: {exc} | "
                f"{traceback.format_exc().strip().splitlines()[-1]}")
        elapsed = time.perf_counter() - t0
        counts[verdict] = counts.get(verdict, 0) + 1
        print(f"[hotplug] case={name:<14} verdict={verdict:<5} "
              f"elapsed={elapsed:6.3f}s detail={detail}")

    print(f"[hotplug] summary: PASS={counts['PASS']} FAIL={counts['FAIL']} "
          f"SKIP={counts['SKIP']} (SKIP=需人工拔插，不算通过)")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
