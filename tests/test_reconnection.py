# SPDX-License-Identifier: MIT
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


def test_default_give_up_total_wait_is_61_seconds():
    """默认参数下用户最迟多久看到"放弃"：1+2+4+8+16+30 = 61s（不含单次 connect 耗时）。"""
    total = sum(compute_backoff(i) for i in range(1, 7))
    assert total == pytest.approx(61.0), (
        f"默认总退避等待变为 {total}s；文档与用户预期必须同步更新"
    )


# ----------------------------------------------------------------------
# 后端 mock
# ----------------------------------------------------------------------
class FakeStatus:
    def __init__(self, backend):
        self.connected = backend.connected
        self.frequency_hz = backend.freq
        self.sample_rate_hz = backend.sr
        self.gain_db = backend.gain
        self.demod_mode = backend.demod
        self.device_id = backend.device_id
        self.serial_number = backend.serial_number
        self.sn = backend.sn


class FakeBackend:
    def __init__(self):
        self.connected = True
        self.freq = 98_000_000.0
        self.sr = 2_048_000.0
        self.gain = 20.0
        self.demod = "WFM"
        self.device_id = "dev-A"
        self.serial_number = ""
        self.sn = ""
        #: 重连后若改成别的 id，模拟「插回来的是另一台设备」
        self.device_id_after_reconnect = None
        #: 重连后要改写的身份字段（模拟另一台设备 / 只剩一个字段可读）
        self.fields_after_reconnect = {}
        self.connect_calls = 0
        # 前 N 次 connect 失败（模拟设备还没插回）
        self.fail_connect_times = 0

    def get_status(self):
        return FakeStatus(self)

    def connect(self):
        self.connect_calls += 1
        if self.connect_calls <= self.fail_connect_times:
            return False
        # 物理重连 = 一个"新设备"实例：频率/模式回到默认，
        # 用来验证重连管理器是否恢复了断开前的设置
        self.connected = True
        self.freq = 100_000_000.0
        self.gain = 0.0
        self.demod = "WFM"
        if self.device_id_after_reconnect is not None:
            self.device_id = self.device_id_after_reconnect
        for key, value in self.fields_after_reconnect.items():
            setattr(self, key, value)
        return True

    def set_frequency(self, f): self.freq = f
    def set_sample_rate(self, s): self.sr = s
    def set_gain(self, g): self.gain = g
    def set_demod_mode(self, mode): self.demod = mode


def _make_manager(**kw):
    return ReconnectionManager(
        poll_interval=0.02, base_delay=0.02, max_delay=0.05, **kw
    )


def test_disconnect_then_reconnect_restores_settings():
    backend = FakeBackend()
    backend.demod = "NFM"
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

        # 关键：恢复了断开前的设置（98M/20dB/NFM），而非新设备默认的 100M/0dB/WFM
        assert backend.freq == pytest.approx(98_000_000.0)
        assert backend.gain == pytest.approx(20.0)
        assert backend.demod == "NFM", "重连后解调模式必须与断开前一致"
        assert backend.connected is True
    finally:
        mgr.stop()


def test_reconnect_does_not_apply_settings_to_a_different_device():
    """场景③：插回来的是另一台设备 → 不下发任何设置，显式报错并停止重试。"""
    backend = FakeBackend()
    backend.device_id_after_reconnect = "dev-B"

    events = {"reconnected": 0}
    audio_started = []
    gave_up = threading.Event()

    mgr = _make_manager(
        on_reconnected=lambda: events.__setitem__("reconnected", 1),
        on_give_up=gave_up.set,
        start_audio=lambda: audio_started.append(1),
    )
    mgr.start(backend)
    try:
        backend.connected = False
        assert gave_up.wait(3.0), "身份不匹配应给出放弃结论，而不是继续重试"

        # 一个设置都不许下发：新设备保持 connect() 后的默认值
        assert backend.freq == pytest.approx(100_000_000.0)
        assert backend.demod == "WFM"
        assert audio_started == [], "身份不匹配时不能重启音频"
        assert events["reconnected"] == 0

        snap = mgr.snapshot()
        assert snap["gave_up"] is True
        assert "身份不匹配" in snap["last_error"]
        assert "dev-A" in snap["last_error"] and "dev-B" in snap["last_error"]
        assert snap["suggestion"], "放弃必须带下一步动作"
    finally:
        mgr.stop()


def test_reconnect_without_identity_does_not_false_positive():
    """后端不报告身份时不猜：没有可比字段就照常恢复设置。"""
    backend = FakeBackend()
    backend.device_id = ""  # 断开前后都读不到身份

    reconnected = threading.Event()
    mgr = _make_manager(on_reconnected=reconnected.set)
    mgr.start(backend)
    try:
        backend.connected = False
        assert reconnected.wait(3.0)
        assert backend.freq == pytest.approx(98_000_000.0)
        assert mgr.gave_up is False
        assert mgr.snapshot()["last_error"] == ""
    finally:
        mgr.stop()


def test_identity_half_side_readable_is_not_a_mismatch():
    """断开前只有 serial_number、重连后只有 device_id → 不可比，必须不判定。

    这条钉住「半侧可读」：两个字段始终没有在同一侧同时出现，
    既不能判"同一台"，也不能判"换了设备"。
    """
    backend = FakeBackend()
    backend.device_id = ""
    backend.serial_number = "SN-1"
    # 重连后 serial 读不到，只报告 device_id
    backend.fields_after_reconnect = {"serial_number": "", "device_id": "dev-C"}

    reconnected = threading.Event()
    mgr = _make_manager(on_reconnected=reconnected.set)
    mgr.start(backend)
    try:
        backend.connected = False
        assert reconnected.wait(3.0), "半侧可读不应阻断重连"
        assert mgr.gave_up is False, "半侧可读被误判成换了设备"
        assert mgr.snapshot()["last_error"] == ""
        assert backend.freq == pytest.approx(98_000_000.0)
    finally:
        mgr.stop()


def test_worst_case_give_up_is_91_seconds():
    """默认参数下界面上要写的"最坏约 91 秒"：61s 退避 + 6×5s 建连。"""
    mgr = ReconnectionManager()  # 默认 base=1/max=30/attempts=6
    assert mgr.worst_case_give_up_s() == pytest.approx(91.0)


def test_cancel_stops_retry_immediately():
    """能立刻放弃：cancel() 后不再发起新的 connect，状态直接 gave_up。"""
    backend = FakeBackend()
    backend.fail_connect_times = 10_000  # 永远连不上，保证停在重试循环里

    gave_up = threading.Event()
    mgr = ReconnectionManager(
        poll_interval=0.02, on_give_up=gave_up.set,
        base_delay=0.05, max_delay=0.2, max_attempts=6,
    )
    mgr.start(backend)
    try:
        backend.connected = False
        # 等到真正进入 retrying（且至少发起过一次 connect）
        deadline = threading.Event()
        for _ in range(200):
            if mgr.state == "retrying" and backend.connect_calls >= 1:
                break
            deadline.wait(0.01)
        assert mgr.state == "retrying", "未进入重试态，用例前提不成立"

        calls_before = backend.connect_calls
        mgr.cancel()
        assert gave_up.wait(1.0), "cancel() 必须回调 on_give_up 让界面更新"
        assert mgr.gave_up is True
        assert mgr.running is False
        assert mgr.snapshot()["last_error"] == "用户取消重连"
        # 取消后不再发起任何 connect（退避等待被唤醒）
        threading.Event().wait(0.3)
        assert backend.connect_calls == calls_before
    finally:
        mgr.stop()


def test_retry_hint_tells_user_worst_case_and_how_to_stop():
    backend = FakeBackend()
    backend.fail_connect_times = 10_000
    mgr = ReconnectionManager(
        poll_interval=0.02, base_delay=0.2, max_delay=0.8, max_attempts=6,
    )
    mgr.start(backend)
    try:
        backend.connected = False
        for _ in range(200):
            if mgr.state == "retrying" and mgr.snapshot()["next_retry_in"] > 0:
                break
            threading.Event().wait(0.01)
        hint = mgr.snapshot()["hint"]
        assert "正在重试" in hint
        assert "最坏约" in hint and "停止" in hint
        # 最坏估值按本次参数换算：退避 0.2+0.4+0.8×4=3.8s，另加 6×5s 建连
        assert mgr.snapshot()["worst_case_s"] == pytest.approx(33.8)
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


def test_default_reconnect_is_bounded_not_infinite():
    """默认策略必须有上限：禁止"永远重试"让用户永远等不到结论。"""
    backend = FakeBackend()
    backend.fail_connect_times = 999
    backend.connected = True
    gave_up = threading.Event()
    mgr = ReconnectionManager(
        poll_interval=0.02, base_delay=0.001, max_delay=0.005,
        on_give_up=gave_up.set,
    )
    mgr.start(backend)
    try:
        backend.connected = False
        assert gave_up.wait(3.0), "默认策略未在有限时间内放弃重连"
        assert mgr.gave_up is True
        snap = mgr.snapshot()
        # 默认上限与默认总等待时间都是对外承诺，钉死防止被悄悄改回无限
        assert snap["max_attempts"] == 6
        assert snap["attempts_total"] <= 6
        assert backend.connect_calls <= 6
    finally:
        mgr.stop()


def test_stop_is_idempotent_and_fast():
    mgr = _make_manager()
    mgr.stop()  # 未 start 也安全
    mgr.start(FakeBackend())
    mgr.stop()
    mgr.stop()  # 重复调用安全
    assert mgr.running is False
