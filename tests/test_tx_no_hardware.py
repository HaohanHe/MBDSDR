# SPDX-License-Identifier: MIT
"""P8-5 无硬件全路径回归（CI 可跑）。

覆盖四种入口：工具调用、脚本直调、底层驱动直调、IQ 回放发射；
另含 TC 上行门与「设备掉线立即停发」。全部使用注入假后端，不接真硬件。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.tx_guard import (  # noqa: E402
    GATE_NOT_ARMED, GATE_REVOKED, get_tx_gate, reset_tx_gate, tx_authorization,
)
from mbdsdr_ai.hal import DeviceInfo  # noqa: E402


class FakeHalBackend:
    """假 HAL 后端：模拟真实驱动的授权检查、掉线与停发。"""

    def __init__(self, name="HackRF One", driver="soapy"):
        self._sample_rate = 1.0e6
        self._name = name
        self._driver = driver
        self.writes = 0
        self.stopped = 0
        self.freq = None
        self.rx_active = False
        self.paused = False
        self.pause_calls = 0
        self.resume_calls = 0
        self.alive_after = None      # 写满 N 次后掉线
        self.underrun_after = None

    def get_info(self):
        return DeviceInfo(name=self._name, driver=self._driver)

    def supports_tx(self):
        return True

    def set_frequency(self, hz):
        self.freq = hz

    def device_alive(self):
        if self.alive_after is None:
            return True
        return self.writes < self.alive_after

    def is_underrun(self):
        if self.underrun_after is None:
            return False
        return self.writes >= self.underrun_after

    def stop_tx(self):
        self.stopped += 1

    def pause_rx(self):
        self.pause_calls += 1
        self.paused = True
        return True

    def resume_rx(self):
        self.resume_calls += 1
        self.paused = False

    def write_tx(self, iq):
        from mbdsdr_ai.tx_guard import reject_unless_authorized
        if not reject_unless_authorized(self):
            return False
        self.writes += 1
        return True


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("MBDSDR_NO_HW", "1")
    monkeypatch.setenv("MBDSDR_NULL_AUDIO", "1")
    monkeypatch.delenv("MBDSDR_TX_BANDS", raising=False)
    monkeypatch.delenv("MBDSDR_TX_MAX_DURATION_S", raising=False)
    reset_tx_gate()
    yield
    reset_tx_gate()


@pytest.fixture
def mgr():
    from mbdsdr_ai.hal import HardwareManager
    manager = HardwareManager()
    fake = FakeHalBackend()
    old = manager._active_backend
    manager._active_backend = fake
    yield manager, fake
    manager._active_backend = old


@pytest.fixture
def recording(tmp_path):
    from mbdsdr_ai.recorder import IQRecorder
    rec = IQRecorder(str(tmp_path / "e2e"), sample_rate=1e6,
                     center_freq_hz=145e6)
    assert rec.open()
    rec.write(np.ones(64, dtype=np.complex64))
    rec.close()
    return str(tmp_path / "e2e.sigmf-data")


def _tool_iq(fake, **extra):
    from mbdsdr_ai.sdr_tools import _sdr_transmit_iq
    # 12000 个交织实数 = 6000 复样本 > 4096 分块，验证「中途」掉线/欠载
    args = {"frequency_hz": 145e6, "iq": [0.1, -0.1] * 6000}
    args.update(extra)
    return _sdr_transmit_iq(args)


def _tool_cw(arm_manual=False):
    from mbdsdr_ai.sdr_tools import _sdr_transmit_cw
    return _sdr_transmit_cw({"frequency_hz": 145e6, "duration_sec": 0.01,
                             "amplitude": 0.1, "arm_manual": arm_manual})


def test_every_entry_point_denies_without_gate(mgr, recording):
    manager, fake = mgr
    # 1) 工具调用
    assert _tool_iq(fake, arm_manual=False).success is False
    assert _tool_cw(arm_manual=False).success is False
    # 2) 脚本直调
    assert manager.transmit_modulated(
        145e6, np.ones(8, dtype=np.complex64), 0.1) is False
    assert manager.transmit_cw(145e6, 0.1, 0.01) is False
    # 3) 底层驱动直调
    assert fake.write_tx(np.ones(8, dtype=np.complex64)) is False
    assert fake.last_tx_reject["code"] == GATE_NOT_ARMED
    # 4) IQ 回放发射
    from mbdsdr_ai.playback import transmit_iq_playback
    rep = transmit_iq_playback(recording, backend=fake, arm_manual=False)
    assert rep["state"] == "rejected"
    # TC 上行
    from mbdsdr_ai.tc_uplink import tc_uplink_transmit
    assert tc_uplink_transmit(iq=[0.1], freq_hz=145e6)["status"] == "GATED"
    assert fake.writes == 0, "任何未授权入口都不得写波"


def test_every_entry_point_works_when_armed(mgr, recording):
    manager, fake = mgr
    assert _tool_iq(fake, arm_manual=True).success is True
    assert _tool_cw(arm_manual=True).success is True
    assert manager.transmit_modulated(
        145e6, np.ones(8, dtype=np.complex64), 0.1, arm_manual=True) is True
    assert manager.transmit_cw(145e6, 0.1, 0.01, arm_manual=True) is True
    with tx_authorization(get_tx_gate().authorize(arm_manual=True)):
        assert fake.write_tx(np.ones(8, dtype=np.complex64)) is True
    from mbdsdr_ai.playback import transmit_iq_playback
    rep = transmit_iq_playback(recording, backend=fake, arm_manual=True,
                               chunk_samples=32)
    assert rep["state"] == "completed"
    assert fake.writes >= 1


def test_revoked_gate_blocks_every_entry_point(mgr, recording):
    manager, fake = mgr
    gate = get_tx_gate()
    gate.revoke(reason="紧急撤销")
    assert _tool_iq(fake, arm_manual=True).success is False
    assert _tool_cw(arm_manual=True).success is False
    assert manager.transmit_cw(145e6, 0.1, 0.01, arm_manual=True) is False
    assert manager.last_tx_code == GATE_REVOKED
    assert fake.write_tx(np.ones(8, dtype=np.complex64)) is False
    from mbdsdr_ai.playback import transmit_iq_playback
    rep = transmit_iq_playback(recording, backend=fake, arm_manual=True)
    assert rep["state"] == "rejected" and rep["reason_code"] == GATE_REVOKED
    assert fake.writes == 0


def test_tool_stops_on_device_lost(mgr):
    _, fake = mgr
    fake.alive_after = 1        # 写第 1 块后掉线
    r = _tool_iq(fake, arm_manual=True)
    assert r.success is False
    assert r.data["reason_code"] == "tx_device_lost"
    assert fake.writes == 1
    assert fake.stopped == 1


def test_tool_stops_on_underrun(mgr):
    _, fake = mgr
    fake.underrun_after = 1
    r = _tool_iq(fake, arm_manual=True)
    assert r.success is False
    assert r.data["reason_code"] == "tx_underrun"
    assert fake.writes == 1
    assert fake.stopped == 1


def test_tool_refuses_unknown_device_capability(mgr):
    manager, fake = mgr
    fake._name = "AI SDR"
    fake._driver = "soapy"
    reset_tx_gate()
    r = _tool_iq(fake, arm_manual=True)
    assert r.success is False
    assert "capability_unknown" in r.content
    assert fake.writes == 0
