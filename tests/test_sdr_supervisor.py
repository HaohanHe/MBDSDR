# SPDX-License-Identifier: MIT
"""SDR 常驻重连监督器（Task 75）的守卫。

全部使用假 connect / 假时钟 / 新注入的 RuntimeSources：
不打开串口、不碰真实 SDR、不联网，也不依赖桌面 UI。
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.core.controller import SDRController  # noqa: E402
from mbdsdr_ai.runtime_sources import RuntimeSources  # noqa: E402
from mbdsdr_ai.sdr_link_supervisor import (  # noqa: E402
    SdrLinkSupervisor,
    compute_backoff,
)

RECONNECT_KEYS = (
    "enabled", "attempts", "max_attempts", "next_retry_in_s",
    "last_error", "stopped_reason",
)


class _FakeLink:
    """按脚本成功/失败的连接入口；不涉及任何真实设备。"""

    def __init__(self, results):
        self.results = list(results)
        self.connected = False
        self.calls = 0

    def connect(self) -> bool:
        self.calls += 1
        ok = bool(self.results.pop(0)) if self.results else False
        self.connected = ok
        return ok


def _supervisor(link: _FakeLink, now, **kwargs) -> SdrLinkSupervisor:
    return SdrLinkSupervisor(
        connect_fn=link.connect,
        is_connected_fn=lambda: link.connected,
        clock=lambda: now[0],
        sleeper=lambda _s: None,
        **kwargs,
    )


def test_backoff_is_bounded_exponential():
    assert [compute_backoff(i) for i in (1, 2, 3, 4, 5)] == [1.0, 2.0, 4.0, 8.0, 16.0]
    assert compute_backoff(6) == 30.0
    assert compute_backoff(60) == 30.0


def test_two_failures_then_success_resets_attempts():
    now = [100.0]
    link = _FakeLink([False, False, True])
    sup = _supervisor(link, now)
    sup.enable()

    assert sup.tick() is False
    assert link.calls == 1
    snap = sup.status()
    assert snap["attempts"] == 1
    assert snap["connected"] is False
    assert snap["next_retry_in_s"] == 1.0
    assert snap["last_error"]

    # 未到期：不得再调 connect（不忙等）
    assert sup.tick() is False
    assert link.calls == 1

    now[0] += 1.0
    assert sup.tick() is False
    assert link.calls == 2
    assert sup.status()["attempts"] == 2

    now[0] += 2.0
    assert sup.tick() is True
    snap = sup.status()
    assert link.calls == 3
    assert snap["connected"] is True
    assert snap["attempts"] == 0
    assert snap["last_error"] == ""
    assert snap["next_retry_in_s"] == 0.0


def test_failures_reach_limit_then_stop_with_reason():
    now = [0.0]
    link = _FakeLink([False] * 10)
    sup = _supervisor(link, now, max_attempts=3)
    sup.enable()

    sup.tick()
    now[0] += 1.0
    sup.tick()
    now[0] += 2.0
    assert sup.tick() is False

    snap = sup.status()
    assert link.calls == 3
    assert snap["attempts"] == 3
    assert snap["max_attempts"] == 3
    assert snap["stopped_reason"], "达到上限必须给出停止原因"
    assert snap["next_retry_in_s"] == 0.0

    # 停止后即使时间继续前进也不再尝试
    now[0] += 10_000.0
    sup.tick()
    assert link.calls == 3


def test_disable_prevents_further_attempts():
    now = [0.0]
    link = _FakeLink([True])
    sup = _supervisor(link, now)
    sup.enable()
    sup.disable()

    sup.tick()
    assert link.calls == 0
    snap = sup.status()
    assert snap["enabled"] is False
    assert snap["next_retry_in_s"] == 0.0


def test_thread_mode_stops_immediately_without_busy_wait():
    class _ImmediateLink:
        def __init__(self):
            self.connected = False
            self.calls = 0

        def connect(self) -> bool:
            self.calls += 1
            self.connected = True
            return True

    link = _ImmediateLink()
    sup = SdrLinkSupervisor(link.connect, lambda: link.connected,
                            poll_interval_s=30.0)
    sup.enable()
    sup.start()
    deadline = time.time() + 2.0
    while link.calls == 0 and time.time() < deadline:
        time.sleep(0.01)
    assert link.calls == 1
    assert sup.status()["connected"] is True

    started = time.time()
    sup.stop()
    assert time.time() - started < 1.0, "stop() 必须立即唤醒 Event.wait 并退出"
    assert sup.status()["running"] is False


def test_runtime_snapshot_has_reconnect_without_touching_connected(tmp_path):
    sources = RuntimeSources()
    ctrl = SDRController(backend=None, config_dir=str(tmp_path),
                         runtime_sources=sources)

    # 默认禁用：不启用就不会连接任何设备
    base = ctrl.get_runtime_sources()["sdr"]
    for key in ("connected", "device", "reason", "last_update_utc"):
        assert key in base, f"sdr 缺既有字段 {key}: {base}"
    assert base["connected"] is False
    assert base["reason"]
    for key in RECONNECT_KEYS:
        assert key in base["reconnect"], f"reconnect 缺字段 {key}"
    assert base["reconnect"]["enabled"] is False

    # 显式启用（不跑线程）：只改 reconnect，不伪造 connected
    status = ctrl.enable_sdr_autoreconnect("debug", start_thread=False)
    assert status["enabled"] is True
    snap = ctrl.get_runtime_sources()["sdr"]
    assert snap["connected"] is False
    assert snap["reconnect"]["enabled"] is True
    assert snap["reconnect"]["attempts"] == 0
    assert snap["reconnect"]["max_attempts"] == 5

    # 关闭后 enabled 回落，connected 语义不受影响
    closed = ctrl.disable_sdr_autoreconnect()
    assert closed["enabled"] is False
    snap = ctrl.get_runtime_sources()["sdr"]
    assert snap["connected"] is False
    assert snap["reconnect"]["enabled"] is False
