"""确定性单测：断连重连 / 指数退避 / 设置恢复 / 回调。

用 FakeBackend + 极短退避 + threading.Event 同步，避免真实等待。
"""
import threading
import time

import pytest

from mbdsdr_ai.reconnection import ReconnectionManager, compute_backoff


# ----------------------------------------------------------------------
# 纯函数：指数退避序列
# ----------------------------------------------------------------------
def test_backoff_sequence():
    delays = [compute_backoff(i, base_delay=1.0, max_delay=30.0)
              for i in range(1, 8)]
    assert delays[:5] == [1.0, 2.0, 4.0, 8.0, 16.0]
    # 封顶 30（32 被压到 30，之后一直 30）
    assert delays[5] == 30.0
    assert delays[6] == 30.0


def test_backoff_clamps_attempt_lt_one():
    assert compute_backoff(0) == compute_backoff(1)


# ----------------------------------------------------------------------
# 后端 mock
# ----------------------------------------------------------------------
class FakeStatus:
    def __init__(self, backend):
        self.connected = backend.connected
        self.frequency_hz = backend.freq
        self.sample_rate_hz = backend.sr
        self.gain_db = backend.gain


class FakeBackend:
    def __init__(self):
        self.connected = True
        self.freq = 98_000_000.0
        self.sr = 2_048_000.0
        self.gain = 20.0
        self.connect_calls = 0
        # 前 N 次 connect 失败（模拟设备还没插回）
        self.fail_connect_times = 0

    def get_status(self):
        return FakeStatus(self)

    def connect(self):
        self.connect_calls += 1
        if self.connect_calls <= self.fail_connect_times:
            return False
        # 物理重连 = 一个"新设备"实例：频率/增益回到默认，
        # 用来验证重连管理器是否恢复了断开前的设置
        self.connected = True
        self.freq = 100_000_000.0
        self.gain = 0.0
        return True

    def set_frequency(self, f): self.freq = f
    def set_sample_rate(self, s): self.sr = s
    def set_gain(self, g): self.gain = g


def _make_manager(**kw):
    return ReconnectionManager(
        poll_interval=0.02, base_delay=0.02, max_delay=0.05, **kw
    )


def test_disconnect_then_reconnect_restores_settings():
    backend = FakeBackend()
    backend.fail_connect_times = 1  # 第一次重连失败，第二次成功

    events = {"disconnected": 0, "reconnecting": [], "reconnected": 0}
    audio_stopped = []
    audio_started = []
    reconnected = threading.Event()

    mgr = _make_manager(
        on_disconnected=lambda: events.__setitem__("disconnected", 1),
        on_reconnecting=lambda a: events["reconnecting"].append(a),
        on_reconnected=lambda: (events.__setitem__("reconnected", 1), reconnected.set()),
        stop_audio=lambda: audio_stopped.append(1),
        start_audio=lambda: audio_started.append(1),
    )
    mgr.start(backend)
    try:
        # 模拟拔出 USB
        backend.connected = False
        assert reconnected.wait(3.0), "未在超时内完成重连"

        assert events["disconnected"] == 1
        assert audio_stopped == [1]          # 断开时停了音频
        assert events["reconnecting"] == [1, 2]  # 第一次失败、第二次成功
        assert events["reconnected"] == 1
        assert audio_started == [1]          # 重连后重启了音频

        # 关键：恢复了断开前的设置（98M/20dB），而非新设备默认的 100M/0dB
        assert backend.freq == pytest.approx(98_000_000.0)
        assert backend.gain == pytest.approx(20.0)
        assert backend.connected is True
    finally:
        mgr.stop()


def test_give_up_after_max_attempts():
    backend = FakeBackend()
    backend.fail_connect_times = 999  # 永远连不上
    backend.connected = True

    gave_up = threading.Event()
    events = {"reconnecting": []}

    mgr = _make_manager(
        max_attempts=3,
        on_reconnecting=lambda a: events["reconnecting"].append(a),
        on_give_up=gave_up.set,
    )
    mgr.start(backend)
    try:
        backend.connected = False  # 拔出
        assert gave_up.wait(3.0), "未触发 give_up"
        assert mgr.gave_up is True
        # 最多尝试 3 次
        assert events["reconnecting"] == [1, 2, 3]
    finally:
        mgr.stop()


def test_infinite_reconnect_default_no_give_up_event():
    backend = FakeBackend()
    backend.fail_connect_times = 999
    backend.connected = True
    mgr = _make_manager(max_attempts=None)  # 默认无限
    mgr.start(backend)
    try:
        backend.connected = False
        time.sleep(0.3)  # 让它重试几轮
        # 无限重试：不抛、不 give_up
        assert mgr.gave_up is False
        assert backend.connect_calls >= 2
    finally:
        mgr.stop()


def test_stop_is_idempotent_and_fast():
    mgr = _make_manager()
    mgr.stop()  # 未 start 也安全
    mgr.start(FakeBackend())
    mgr.stop()
    mgr.stop()  # 重复调用安全
    assert mgr.running is False
