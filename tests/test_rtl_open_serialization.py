# SPDX-License-Identifier: MIT
"""进程内 rtlsdr_open 串行化：冷启动并发打开不再误报 ACCESS(-3)。

真机背景（2026-11-19）：桌面冷启动时三条路径会同时打开同一台 RTL-SDR——
`module_panel` 构造即枚举、`device_panel` 400 ms 首枚举、`main_window`
200 ms 自动连接。libusb 对同一 WinUSB 设备的第二次 open 返回
``LIBUSB_ERROR_ACCESS(-3)``，与「权限不足」同码，用户被误导去装 Zadig。
实测冷启动 5 次里 3 次失败。

修法两条，本文件各钉一半：
1. ``RTLSDRBackend._RTL_OPEN_LOCK`` 让 probe / 枚举 / connect 的 open→close
   互斥；
2. ``_open_rtlsdr_with_retry`` 吃掉跨进程占用的瞬时碰撞（锁管不了别的进程）。
"""
import os
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_OFFLINE", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from mbdsdr_ai.sdr_backend import RTLSDRBackend  # noqa: E402


def _access_error():
    """构造一条与 pyrtlsdr 真机文案同形的 -3 异常。"""
    return RuntimeError(
        "<LIBUSB_ERROR_ACCESS (-3): Access denied (insufficient permissions)> "
        '"Could not open SDR (device index = 0)"')


# ---------------------------------------------------------------------------
# 一、重试语义
# ---------------------------------------------------------------------------

def test_retry_recovers_from_transient_access_error():
    calls = {"n": 0}
    sentinel = object()

    def open_fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _access_error()
        return sentinel

    assert RTLSDRBackend._open_rtlsdr_with_retry(
        open_fn, attempts=3, delay_s=0.0) is sentinel
    assert calls["n"] == 3


def test_retry_does_not_mask_non_access_errors():
    """驱动缺失 / 设备不存在这类错误必须立刻抛，重试只会拖慢发现。"""
    calls = {"n": 0}

    def open_fn():
        calls["n"] += 1
        raise RuntimeError("No module named 'rtlsdr'")

    with pytest.raises(RuntimeError, match="No module named"):
        RTLSDRBackend._open_rtlsdr_with_retry(open_fn, attempts=3, delay_s=0.0)
    assert calls["n"] == 1


def test_retry_gives_up_after_budget_and_raises_last_error():
    calls = {"n": 0}

    def open_fn():
        calls["n"] += 1
        raise _access_error()

    with pytest.raises(RuntimeError, match="LIBUSB_ERROR_ACCESS"):
        RTLSDRBackend._open_rtlsdr_with_retry(open_fn, attempts=3, delay_s=0.0)
    assert calls["n"] == 3


# ---------------------------------------------------------------------------
# 二、open 串行性（锁的真实效果，不是「锁对象存在」）
# ---------------------------------------------------------------------------

class _SlowFakeLib:
    """记录同一时刻有多少条线程停留在 open 内部。"""

    def __init__(self, hold_s: float = 0.08):
        self.hold_s = hold_s
        self.in_open = 0
        self.max_in_open = 0
        self._guard = threading.Lock()

    def rtlsdr_open(self, dev_ref, index):
        with self._guard:
            self.in_open += 1
            self.max_in_open = max(self.max_in_open, self.in_open)
        time.sleep(self.hold_s)
        with self._guard:
            self.in_open -= 1
        return 0

    def rtlsdr_get_tuner_type(self, dev):
        return 2

    def rtlsdr_close(self, dev):
        return 0


def test_probe_serializes_concurrent_open(monkeypatch):
    """两个线程同时探测不同设备：任何时刻 open 内只能有 1 条线程。"""
    import rtlsdr as _rtlsdr

    fake = _SlowFakeLib()
    monkeypatch.setattr(_rtlsdr, "librtlsdr", fake, raising=True)
    monkeypatch.setattr(RTLSDRBackend, "_TUNER_PROBE_CACHE", {})

    errors = []

    def worker(idx):
        try:
            RTLSDRBackend._probe_tuner_type(idx, serial=f"s{idx}")
        except Exception as exc:  # noqa: BLE001 - 测试里只记录不吞
            errors.append(exc)

    ths = [threading.Thread(target=worker, args=(i,)) for i in (0, 1, 2)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(timeout=5)

    assert not errors, errors
    assert fake.max_in_open == 1, (
        f"open 未串行化：同一时刻最多 {fake.max_in_open} 条线程在 open 内")


def test_enumerate_serializes_with_probe(monkeypatch):
    """枚举（list）与探测走同一把锁，不再互相抢设备。"""
    import rtlsdr as _rtlsdr

    fake = _SlowFakeLib()
    monkeypatch.setattr(_rtlsdr, "librtlsdr", fake, raising=True)
    monkeypatch.setattr(RTLSDRBackend, "_TUNER_PROBE_CACHE", {})

    # enumerate 走的是另一个内部函数，这里直接驱动它依赖的同一把锁 +
    # 同一 open 调用点：两条线程一条 probe、一条模拟枚举的 open。
    def enum_like():
        with RTLSDRBackend._RTL_OPEN_LOCK:
            dev = __import__("ctypes").c_void_p()
            if int(fake.rtlsdr_open(__import__("ctypes").byref(dev), 0)) == 0:
                fake.rtlsdr_close(dev)

    ths = [threading.Thread(target=RTLSDRBackend._probe_tuner_type,
                            args=(1, "x")),
           threading.Thread(target=enum_like)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(timeout=5)

    assert fake.max_in_open == 1, (
        f"probe 与枚举未串行：同一时刻最多 {fake.max_in_open} 条线程在 open 内")
