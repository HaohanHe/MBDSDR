"""端到端测试：USB 拔出 -> 看门狗检测 -> 停止音频 -> 指数退避重试 -> 插回恢复。

用 mock 后端串起 USBWatchdog + ReconnectionManager + error_handler，
确定性验证：
  1. 正常连接 + 接收；
  2. USB 拔出 -> watchdog 检测 -> 停止读线程/音频 -> 断连错误提示上屏；
  3. 重连失败（设备未插回）-> 指数退避（多次 attempt）；
  4. 设备插回 -> 重连成功 -> 频率/增益恢复到断开前 -> 重启音频；
  5. give_up 路径（永不插回）。

时间敏感性做法与既有 tests/test_reconnection.py 一致：极短退避 +
threading.Event 同步，失败次数预置为 1，attempts 序列确定性为 [1, 2]。
"""
import threading
import time

import numpy as np
import pytest

from mbdsdr_ai.error_handler import USBDisconnectedError, format_error
from mbdsdr_ai.reconnection import ReconnectionManager, compute_backoff
from mbdsdr_ai.usb_watchdog import USBWatchdog


# ----------------------------------------------------------------------
# Mock 后端
# ----------------------------------------------------------------------
class FakeStatus:
    def __init__(self, be):
        self.connected = be.connected
        self.frequency_hz = be.freq
        self.sample_rate_hz = be.sr
        self.gain_db = be.gain
        self.demod_mode = be.mode


class FakeSDR:
    """模拟一个可被物理拔出的 RTL-SDR 后端。

    ``present`` 控制硬件在位（read_rx 抛 LIBUSB_ERROR_NO_DEVICE，喂给 watchdog）；
    ``connected`` 控制逻辑连接状态（喂给 ReconnectionManager 探测）。
    connect() 成功时把 freq/gain 重置为"新设备出厂默认"，
    用来验证 ReconnectionManager 是否真的恢复了断开前设置。
    """

    def __init__(self):
        self.present = True
        self.connected = True
        self.freq = 98_000_000.0
        self.sr = 2_048_000.0
        self.gain = 20.0
        self.mode = "FM"
        self.connect_calls = 0
        self.fail_connect_times = 0
        self.read_calls = 0

    # -- 看门狗探活 / 数据面 -------------------------------------------------
    def read_rx(self, n):
        if not self.present:
            raise OSError("LIBUSB_ERROR_NO_DEVICE: device removed")
        self.read_calls += 1
        return np.zeros(n, dtype=np.complex64)

    # -- 重连管理面 ----------------------------------------------------------
    def get_status(self):
        return FakeStatus(self)

    def connect(self):
        self.connect_calls += 1
        if self.connect_calls <= self.fail_connect_times:
            self.connected = False
            return False
        # 物理重连 = 全新设备句柄：设置回到出厂默认
        self.connected = True
        self.freq = 100_000_000.0
        self.gain = 0.0
        return True

    def set_frequency(self, f): self.freq = float(f)
    def set_sample_rate(self, s): self.sr = float(s)
    def set_gain(self, g): self.gain = float(g)


def _on_removed(events):
    events["watchdog_removed"] += 1
    events["ui_msg"] = format_error(USBDisconnectedError())


def _wire(be, events, audio_stopped, audio_started, give_up_ev=None):
    """同时拉起 watchdog + reconnection manager。"""
    wd = USBWatchdog(
        be, interval=0.02,
        alive_fn=lambda b: b.present,
        stop_reading=lambda: events.__setitem__(
            "read_stopped", events.get("read_stopped", 0) + 1),
        stop_audio=lambda: audio_stopped.append(1),
        on_device_removed=lambda: _on_removed(events),
    )
    recon = ReconnectionManager(
        poll_interval=0.02, base_delay=0.02, max_delay=0.05,
        on_disconnected=lambda: events.__setitem__(
            "recon_disconnected", events.get("recon_disconnected", 0) + 1),
        on_reconnecting=lambda a: events["attempts"].append(a),
        on_reconnected=lambda: (events.__setitem__("reconnected",
                                                   events.get("reconnected", 0) + 1),
                                events["reconnected_ev"].set()),
        on_give_up=(give_up_ev.set if give_up_ev else None),
        stop_audio=lambda: audio_stopped.append(1),
        start_audio=lambda: audio_started.append(1),
    )
    return wd, recon


# ----------------------------------------------------------------------
# 1. 完整断连 -> 重试失败 -> 插回 -> 设置恢复
# ----------------------------------------------------------------------
def test_full_disconnect_reconnect_flow_restores_settings():
    be = FakeSDR()
    be.fail_connect_times = 1  # 第 1 次重连失败，第 2 次成功（确定性）
    events = {
        "attempts": [],
        "reconnected_ev": threading.Event(),
        "watchdog_removed": 0,
        "ui_msg": {},
    }
    audio_stopped, audio_started = [], []

    wd, recon = _wire(be, events, audio_stopped, audio_started)
    recon.start(be)
    wd.start()
    try:
        # ① 正常状态：看门狗探活返回存活，后端逻辑连接在位
        time.sleep(0.08)
        assert wd.is_alive is True, "正常状态下看门狗应判定设备存活"
        assert be.connected is True

        # ② USB 拔出：硬件消失 + 逻辑断开
        be.present = False
        be.connected = False

        # 等看门狗上报拔出
        deadline = 3.0
        while events["watchdog_removed"] == 0 and deadline > 0:
            time.sleep(0.02)
            deadline -= 0.02
        assert events["watchdog_removed"] >= 1, "USBWatchdog 未检测到拔出"

        # 断连错误提示上屏（给 UI 的文案必须可操作）
        assert "USB" in events["ui_msg"].get("message", "")
        assert "重新插入" in events["ui_msg"].get("suggestion", "")

        # ③ 等重连成功（attempt1 失败 -> 退避 -> attempt2 成功）
        assert events["reconnected_ev"].wait(5.0), "未在超时内重连成功"

        # ④ 确定性尝试序列：失败 1 次 + 成功 1 次
        assert events["attempts"] == [1, 2]

        # 断连时音频被停过；重连后音频重启
        assert len(audio_stopped) >= 1, "断连时应停止音频"
        assert audio_started == [1], "重连后应重启音频"

        # ⑤ 关键：恢复了断开前的设置（98M / 20dB），而非新设备默认 100M / 0dB
        assert be.freq == pytest.approx(98_000_000.0)
        assert be.gain == pytest.approx(20.0)
        assert be.connected is True

        # ⑥ 插回后 watchdog 检测到设备重新就位
        be.present = True
        added = threading.Event()
        wd.on_device_added = added.set
        assert added.wait(3.0), "未检测到设备插回"
    finally:
        wd.stop()
        recon.stop()


# ----------------------------------------------------------------------
# 2. 永不插回 -> 达到 max_attempts -> on_give_up
# ----------------------------------------------------------------------
def test_give_up_when_device_never_replugged():
    be = FakeSDR()
    be.fail_connect_times = 999  # 永远连不上
    events = {"attempts": []}
    audio_stopped, audio_started = [], []
    give_up = threading.Event()

    wd, recon = _wire(be, events, audio_stopped, audio_started, give_up_ev=give_up)
    recon._max_attempts = 3  # 构造时默认无限，这里压成 3 次
    recon.start(be)
    wd.start()
    try:
        be.present = False
        be.connected = False
        assert give_up.wait(5.0), "未触发 give_up"
        assert recon.gave_up is True
        assert events["attempts"] == [1, 2, 3]
        # give_up 后不会重启音频
        assert audio_started == []
    finally:
        wd.stop()
        recon.stop()


# ----------------------------------------------------------------------
# 3. 指数退避序列本身（纯函数，确定性）
# ----------------------------------------------------------------------
def test_backoff_is_exponential_then_clamped():
    seq = [compute_backoff(i, base_delay=1.0, max_delay=30.0) for i in range(1, 8)]
    assert seq[:5] == [1.0, 2.0, 4.0, 8.0, 16.0]
    assert seq[5] == 30.0 and seq[6] == 30.0


# ----------------------------------------------------------------------
# 4. 拔出瞬间 read_rx 抛错被 watchdog 吞掉，不崩
# ----------------------------------------------------------------------
def test_watchdog_survives_exploding_read():
    be = FakeSDR()
    removed = threading.Event()
    wd = USBWatchdog(be, interval=0.02, on_device_removed=removed.set)
    wd.start()
    try:
        be.present = False  # read_rx 抛 OSError
        assert removed.wait(3.0)
        assert wd.check_alive() is False
    finally:
        wd.stop()
