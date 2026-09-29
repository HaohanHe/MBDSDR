# SPDX-License-Identifier: MIT
"""确定性单测：多用户调度 —— 引用计数、超时关闭、无设备安全。"""
import pytest

from mbdsdr_ai.multi_user import MultiUserManager


class FakeBackend:
    def __init__(self):
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


def test_refcount_starts_backend_on_first_client():
    be = FakeBackend()
    m = MultiUserManager(backend=be, timeout_s=10.0)
    m.add_client("u1")
    assert be.started == 1
    assert m.is_running
    m.add_client("u2")
    assert be.started == 1  # 不重复启动


def test_last_client_leaves_schedules_shutdown():
    be = FakeBackend()
    clock = [1000.0]
    m = MultiUserManager(backend=be, timeout_s=15.0, clock=lambda: clock[0])
    m.add_client("u1")
    m.remove_client("u1")
    # 立即 poll 不应关闭（未超时）
    assert not m.poll()
    assert be.stopped == 0
    # 推进时间超过 timeout
    clock[0] += 20.0
    assert m.poll() is True
    assert be.stopped == 1


def test_background_client_keeps_device():
    be = FakeBackend()
    clock = [1000.0]
    m = MultiUserManager(backend=be, timeout_s=5.0, clock=lambda: clock[0])
    m.add_client("u1", kind="user")
    m.add_client("bg1", kind="background")
    m.remove_client("u1")
    clock[0] += 100.0
    assert not m.poll()  # bg 还在，不关闭
    assert be.stopped == 0


def test_no_backend_safe():
    """无设备时接口可用但不造假。"""
    m = MultiUserManager(backend=None, timeout_s=1.0)
    m.add_client("u1")
    assert m.is_running  # 标记 running 但不崩
    m.remove_client("u1")
    m.force_stop()  # 不崩


def test_prune_idle():
    m = MultiUserManager(backend=None, timeout_s=10.0)
    clock = [1000.0]
    m._clock = lambda: clock[0]
    m.add_client("u1")
    clock[0] += 1000.0
    removed = m.prune_idle(idle_s=100.0)
    assert "u1" in removed
    assert m.client_count == 0
