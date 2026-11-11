#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""P0-17 独立复现脚本：手动连接设备是否仍在 GUI 线程同步阻塞。

roadmap 断言（原文）：
    「连接设备在 GUI 线程同步 2.0 s，只 ``processEvents()`` 一次」
    证据等级：代码内实测注释 + 静态；本机无 SDR 未复测。

做法（offscreen + 独立 profile + 真 MainWindow）：
  1. 装一个 50 ms 心跳 QTimer；
  2. 先把事件循环空转 2 s（正例，心跳应累计 ≥20 次）；
  3. 再用一个 ``connect()`` 睡 2.0 s 的后端桩调真实
     ``MainWindow._connect_backend``，统计**阻塞期间**心跳次数；
  4. 阻塞期间 ≤1 次 = GUI 线程被占满（产品代码在 connect 前只
     ``QApplication.processEvents()`` 一次，见 ``main_window.py`` 注释）。

退出码（三态）：
    0  复现成功   —— connect 耗时 ≥1.5 s 且期间心跳 ≤1 次
    1  未复现     —— 期间心跳 ≥5 次（连接已不阻塞 GUI）
    2  无法判定   —— 构造/连接失败，或心跳探针本身失效

说明：本机无 SDR，用「睡 2.0 s 的 connect 桩」等价替代原生 connect 阻塞；
断言的是 GUI 线程是否被同步调用占住，不是驱动行为。

用法：
    python scripts/repro/2026-11-11-p0-17-connect-blocks-gui.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import types

ROOT = os.environ.get("MBDSDR_REPRO_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP_ROOT = r"D:\mbdsdr\output\tmp"


def _hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


class _SlowBackend:
    """connect() 睡 2.0 s，模拟本机 RTL-SDR 原生连接的阻塞。"""

    def __init__(self, delay=2.0):
        self.delay = float(delay)
        self.status = types.SimpleNamespace(error="", gain_db=20.0,
                                            demod_mode="FM", connected=False)
        self.freq = 145.5e6
        self.sr = 2.048e6
        self.connect_calls = 0

    def connect(self):
        self.connect_calls += 1
        time.sleep(self.delay)
        self.status.connected = True
        return True

    def disconnect(self):
        self.status.connected = False

    def get_status(self):
        return self.status

    def get_sample_rate(self):
        return self.sr

    def set_sample_rate(self, r):
        self.sr = float(r)

    def get_frequency(self):
        return self.freq

    def set_frequency(self, f):
        self.freq = float(f)

    def set_bandwidth(self, _b):
        pass

    def set_ppm(self, _p):
        pass

    def set_agc(self, _b):
        return True

    def set_gain(self, _g):
        pass

    def set_demod(self, _m):
        pass

    def listGains(self, *_a, **_k):
        return []

    def read_samples(self, n):
        import numpy as np
        return np.zeros(int(n), dtype=np.complex64)

    def subscribe_iq(self, _name):
        return types.SimpleNamespace(read=self.read_samples)


def main() -> int:
    profile = tempfile.mkdtemp(prefix="p0-17-profile-", dir=TMP_ROOT)
    for k in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        os.environ[k] = profile
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ.setdefault("MBDSDR_OFFLINE", "1")
    os.environ.setdefault("MBDSDR_NO_HW", "1")
    os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "desktop"))

    try:
        from PySide6.QtCore import QSettings, QTimer
        from PySide6.QtWidgets import QApplication
        QSettings.setDefaultFormat(QSettings.IniFormat)
        for scope in (QSettings.UserScope, QSettings.SystemScope):
            QSettings.setPath(QSettings.IniFormat, scope,
                              os.path.join(profile, "qsettings"))
        app = QApplication.instance() or QApplication(sys.argv[:1])
        import main_window as mw
        window = mw.MainWindow()
        app.processEvents()
    except Exception as exc:  # noqa: BLE001
        print(f"[无法判定] Qt/MainWindow 构造失败：{type(exc).__name__}: {exc}")
        return 2

    print(f"repo   : {ROOT}")
    print(f"python : {sys.version.split()[0]}")
    print(f"临时档案: {profile}")
    print(f"异步启动连接入口存在: "
          f"{hasattr(window, '_auto_enumerate_and_connect_async')}")

    counter = {"n": 0}
    timer = QTimer()
    timer.setInterval(50)
    timer.timeout.connect(lambda: counter.__setitem__("n", counter["n"] + 1))

    _hr("正例：心跳探针本身有效（空转 2 s，应累计 ≥20 次）")
    counter["n"] = 0
    timer.start()
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 2.0:
        app.processEvents()
        time.sleep(0.01)
    timer.stop()
    idle_ticks = counter["n"]
    print(f"  空转 2 s 心跳次数：{idle_ticks}")
    if idle_ticks < 20:
        print("[无法判定] 心跳探针失效（空转都收不到 20 次）。")
        return 2

    _hr("实测：调用真实 _connect_backend，connect 桩阻塞 2.0 s")
    backend = _SlowBackend(2.0)
    counter["n"] = 0
    timer.start()
    t0 = time.perf_counter()
    ok = False
    err = None
    try:
        ok = window._connect_backend(backend)
        app.processEvents()
    except Exception as exc:  # noqa: BLE001
        err = exc
    connect_ms = (time.perf_counter() - t0) * 1000.0
    timer.stop()
    blocked_ticks = counter["n"]
    print(f"  返回值        : {ok}（异常={err!r}）")
    print(f"  调用总耗时    : {connect_ms:.0f} ms")
    print(f"  期间心跳次数  : {blocked_ticks}")
    print(f"  connect 调用  : {backend.connect_calls} 次")

    try:
        if hasattr(window, "_stop_iq_streams"):
            window._stop_iq_streams()
        if hasattr(window, "_teardown_connection"):
            window._teardown_connection()
        window.close()
    except Exception:  # noqa: BLE001
        pass
    shutil.rmtree(profile, ignore_errors=True)

    if err is not None or not ok:
        print(f"[无法判定] 连接流程未成功走完（ok={ok}, err={err!r}）。")
        return 2

    reproduced = connect_ms >= 1500.0 and blocked_ticks <= 1
    print(f"\n结论：当前实现复现 P0-17 : {reproduced}")
    if reproduced:
        print(f"[复现] 手动连接在 GUI 线程阻塞 {connect_ms:.0f} ms，期间事件循环"
              f"只跑了 {blocked_ticks} 次心跳；启动自动连接已异步，但手动路径未改。")
        return 0
    if blocked_ticks >= 5:
        print(f"[未复现] 连接期间事件循环仍在跑（{blocked_ticks} 次心跳）。")
        return 1
    print(f"[无法判定] 阻塞 {connect_ms:.0f} ms 但心跳 {blocked_ticks} 次，"
          "不满足任一判据。")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
