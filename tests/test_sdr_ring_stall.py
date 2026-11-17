# SPDX-License-Identifier: MIT
"""后端读停顿必须是有界、可见的状态（Task 91/92）。

真机证据（2026-11-18）：RTL-SDR 已连接、已调谐后 ``read_bytes(16384)``
12 秒无返回。环缓冲生产线程原先把驱动读直接放在循环里，设备卡死时
``stop()``/``disconnect()`` 也会被一起拖住，且外部看不到「连着但没数据」。

本文件只用假设备（不碰硬件、不联网）：
1. 生产读永久阻塞 → ``stalled=True``，原因含「超时」，close 在 ~3 s 内返回；
2. 正常读 → 不被误标 stalled；
3. 运行时快照 ``sdr.stalled`` / ``sdr.last_read_reason`` 与 HUD 文案。

反证：把 ``_produce_loop`` 改回直接 ``self._read_fn(...)``，第 1 条会卡死；
把 ``_sources_text`` 的 stalled 分支删掉，HUD 用例立刻红（会显示「已连接」）。
"""
from __future__ import annotations

import os
import sys
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from mbdsdr_ai.runtime_sources import RuntimeSources  # noqa: E402
from mbdsdr_ai.sdr_backend import _ThreadedRingReader  # noqa: E402
from mbdsdr_ai.sky_hud import build_hud  # noqa: E402

SIZE = (800.0, 600.0)
VIEW = {"center_az": 120.0, "center_alt": 45.0, "fov_deg": 2.3,
        "rotation_deg": 0.0, "min_fov": 0.5}
POSE = {"valid": True, "az_deg": 120.0, "alt_deg": 45.0,
        "roll_deg": 0.0, "fov_deg": 2.3}


def _wait(pred, timeout=3.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return pred()


class _BlockingRead:
    """永远不返回的驱动读（模拟 librtlsdr 的 rtlsdr_read_sync 卡死）。"""

    def __init__(self):
        self.release = threading.Event()
        self.reads = 0

    def __call__(self, _n):
        self.reads += 1
        self.release.wait()
        return np.zeros(4, dtype=np.complex64)


# ---------------------------------------------------------------- 生产线程
def test_blocked_producer_marks_stalled_with_timeout_reason():
    read = _BlockingRead()
    ring = _ThreadedRingReader(read, block_size=64, ring_size=1024,
                               read_timeout_s=0.2)
    ring.start()
    try:
        assert _wait(lambda: ring.stalled), "生产读阻塞后必须标记 stalled"
        reason = ring.last_read_reason
        assert reason is not None
        assert reason["code"] == "read_timeout"
        assert "超时" in reason["message"], reason
        assert reason["timeout_s"] == pytest.approx(0.2)
        assert reason["requested_samples"] == 64
    finally:
        read.release.set()
        ring.close()


def test_close_returns_within_three_seconds_when_read_is_stuck():
    read = _BlockingRead()
    ring = _ThreadedRingReader(read, block_size=64, ring_size=1024,
                               read_timeout_s=0.2)
    ring.start()
    try:
        assert _wait(lambda: ring.stalled)
        t0 = time.monotonic()
        ring.close()
        elapsed = time.monotonic() - t0
        assert elapsed < 3.0, f"close() 被阻塞读拖住，耗时 {elapsed:.2f}s"
    finally:
        read.release.set()
        # 收尸：解除阻塞后允许生产者退出，调用方不再等待也不该崩。
        for t in threading.enumerate():
            if t.name == "mbdsdr-ring-producer":
                t.join(2.0)


def test_normal_read_is_not_marked_stalled():
    ring = _ThreadedRingReader(
        lambda n: np.ones(n, dtype=np.complex64),
        block_size=64, ring_size=1024, read_timeout_s=0.5)
    ring.start()
    try:
        assert _wait(lambda: ring.available() >= 64), "正常读应产出样本"
        assert ring.stalled is False
        assert ring.last_read_reason is None
    finally:
        ring.close()


# ------------------------------------------------------- 快照 / HUD 文案
def test_backend_exposes_ring_stall_state():
    """RTLSDR 后端的 stalled/last_read_reason 来自环缓冲生产者。"""
    from mbdsdr_ai.sdr_backend import RTLSDRBackend

    be = RTLSDRBackend.__new__(RTLSDRBackend)
    be._ring_reader = SimpleNamespace(
        stalled=True,
        last_read_reason={"code": "read_timeout",
                          "message": "超时未收到样本（2.0s 内无返回）"},
    )
    assert be.stalled is True
    assert "超时" in be.last_read_reason["message"]

    be._ring_reader = None
    assert be.stalled is False
    assert be.last_read_reason is None


class _Status:
    connected = True
    link_state = "connected"
    error = ""
    last_data_age_s = 0.5


class _FakeBackend:
    """最小后端面：连接在、但设备读超时。"""

    def __init__(self, stalled=True, reason=None):
        self.status = _Status()
        self.device = "Fake SDR"
        self._stalled = stalled
        self._reason = reason

    def is_connected(self):
        return True

    @property
    def stalled(self):
        return self._stalled

    @property
    def last_read_reason(self):
        return self._reason


def _snapshot(backend):
    return RuntimeSources(sdr=backend).snapshot(now_utc=1_700_000_000.0)


def test_snapshot_exposes_stalled_and_reason():
    backend = _FakeBackend(reason={
        "code": "read_timeout",
        "message": "超时未收到样本（0.2s 内读 64 字节无返回）",
        "timeout_s": 0.2,
        "requested_samples": 64,
    })
    sdr = _snapshot(backend)["sdr"]
    assert sdr["connected"] is True, "connected 语义不变：连着就报连着"
    assert sdr["stalled"] is True
    assert "超时" in sdr["last_read_reason"]
    assert "64" in sdr["last_read_reason"]


def test_snapshot_without_backend_state_is_false_and_empty():
    sdr = _snapshot(_FakeBackend(stalled=False, reason=None))["sdr"]
    assert sdr["connected"] is True
    assert sdr["stalled"] is False
    assert sdr["last_read_reason"] == ""


def test_hud_shows_stalled_instead_of_connected():
    backend = _FakeBackend(reason={
        "code": "read_timeout", "message": "超时未收到样本（2.0s 内无返回）",
    })
    hud = build_hud(POSE, [], "L3", VIEW, SIZE,
                    runtime_sources=_snapshot(backend))
    text = hud["sources_text"]
    assert "SDR 无样本（超时）" in text
    assert "已连接" not in text
    # 原始结构仍在，便于排查。
    assert hud["sources"]["sdr"]["stalled"] is True


def test_hud_unchanged_when_sdr_not_stalled():
    hud = build_hud(POSE, [], "L3", VIEW, SIZE,
                    runtime_sources=_snapshot(_FakeBackend(stalled=False)))
    assert "SDR 已连接" in hud["sources_text"]
