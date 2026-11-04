# SPDX-License-Identifier: MIT
"""收发互斥（P8-4）：语义统一在接入层，半双工切换耗时如实上报。"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.tx_guard import (  # noqa: E402
    DUPLEX_CAPABILITY_UNKNOWN, DUPLEX_FULL, DUPLEX_HALF, DUPLEX_UNKNOWN,
    RX_CONFLICT, RX_IDLE, RX_KEPT, RX_PAUSED, TxCapabilities,
    TxRxCoordinator, TxSession, reset_tx_gate,
)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def test_half_duplex_pauses_rx_and_reports_switch_time():
    clock = FakeClock()
    rx = {"paused": False, "calls": []}

    def pause():
        rx["paused"] = True
        rx["calls"].append("pause")
        clock.advance(0.02)
        return True

    def resume():
        rx["paused"] = False
        rx["calls"].append("resume")
        clock.advance(0.005)

    c = TxRxCoordinator(clock=clock)
    d = c.acquire(duplex=DUPLEX_HALF, rx_active=True,
                  pause_rx=pause, resume_rx=resume)
    assert d.allowed is True and d.mode == RX_PAUSED
    assert d.switch_time_s == pytest.approx(0.02)
    assert rx["paused"] is True
    resume_s = c.release()
    assert resume_s == pytest.approx(0.005)
    assert rx["paused"] is False
    assert rx["calls"] == ["pause", "resume"]


def test_half_duplex_without_pause_control_is_refused():
    d = TxRxCoordinator().acquire(duplex=DUPLEX_HALF, rx_active=True)
    assert d.allowed is False and d.code == RX_CONFLICT
    assert "无法暂停接收" in d.reason


def test_full_duplex_keeps_rx():
    called = {"pause": 0}
    d = TxRxCoordinator().acquire(
        duplex=DUPLEX_FULL, rx_active=True,
        pause_rx=lambda: called.__setitem__("pause", called["pause"] + 1))
    assert d.allowed is True and d.mode == RX_KEPT
    assert called["pause"] == 0


def test_unknown_duplex_with_active_rx_is_refused_not_assumed():
    d = TxRxCoordinator().acquire(duplex=DUPLEX_UNKNOWN, rx_active=True,
                                  pause_rx=lambda: True,
                                  resume_rx=lambda: None)
    assert d.allowed is False and d.code == DUPLEX_CAPABILITY_UNKNOWN
    assert "未知" in d.reason and "用户确认" in d.reason


def test_idle_rx_needs_no_switch():
    d = TxRxCoordinator().acquire(duplex=DUPLEX_HALF, rx_active=False)
    assert d.allowed is True and d.mode == RX_IDLE
    assert d.switch_time_s == 0.0


# ── TxSession 集成 ──────────────────────────────────────────────────────
CAPS_HALF = TxCapabilities(
    known=True, freq_range_hz=(1e6, 6e9), gain_range_db=(0, 47),
    duplex=DUPLEX_HALF, source="测试")
CAPS_FULL = TxCapabilities(
    known=True, freq_range_hz=(1e6, 6e9), gain_range_db=(0, 47),
    duplex=DUPLEX_FULL, source="测试")
CAPS_UNKNOWN = TxCapabilities(
    known=True, freq_range_hz=(1e6, 6e9), gain_range_db=(0, 47),
    duplex=DUPLEX_UNKNOWN, source="测试")


class RxCapableBackend:
    def __init__(self, allow_pause=True):
        self._sample_rate = 1e6
        self.rx_active = True
        self.paused = False
        self.pause_calls = 0
        self.resume_calls = 0
        self.paused_at_write = []
        self.writes = 0
        self._allow_pause = allow_pause

    def supports_tx(self):
        return True

    def set_frequency(self, hz):
        pass

    def device_alive(self):
        return True

    def is_underrun(self):
        return False

    def stop_tx(self):
        pass

    def pause_rx(self):
        self.pause_calls += 1
        if not self._allow_pause:
            return False
        self.paused = True
        return True

    def resume_rx(self):
        self.resume_calls += 1
        self.paused = False

    def write_tx(self, block):
        from mbdsdr_ai.tx_guard import tx_authorized
        if not tx_authorized():
            return False
        self.writes += 1
        self.paused_at_write.append(self.paused)
        return True


@pytest.fixture(autouse=True)
def _fresh_gate():
    reset_tx_gate()
    yield
    reset_tx_gate()


def _session(be, caps=CAPS_HALF, rx_active=True):
    from mbdsdr_ai.tx_guard import get_tx_gate
    return TxSession(be).transmit(
        np.ones(30, dtype=np.complex64), freq_hz=145e6,
        tx_token=get_tx_gate().arm(ttl_s=10),
        caps=caps, chunk_samples=10, rx_control=be, rx_active=rx_active)


def test_session_pauses_rx_during_half_duplex_tx():
    be = RxCapableBackend()
    rep = _session(be)
    assert rep["state"] == "completed"
    assert rep["duplex"]["mode"] == RX_PAUSED
    assert rep["duplex"]["switch_time_s"] >= 0.0
    assert be.pause_calls == 1 and be.resume_calls == 1
    assert all(be.paused_at_write), "每次写波都应在接收暂停期间"
    assert be.paused is False, "发射结束后必须恢复接收"


def test_session_resumes_rx_even_on_abort():
    be = RxCapableBackend()
    orig = be.write_tx

    def write(block):
        ok = orig(block)
        return ok

    be.write_tx = write
    be.device_alive = lambda: be.writes < 2
    rep = _session(be)
    assert rep["state"] == "aborted"
    assert rep["reason_code"] == "tx_device_lost"
    assert rep["duplex"]["mode"] == RX_PAUSED
    assert be.resume_calls == 1
    assert be.paused is False


def test_session_refuses_when_half_duplex_cannot_pause_rx():
    be = RxCapableBackend(allow_pause=False)
    rep = _session(be)
    assert rep["state"] == "rejected"
    assert rep["reason_code"] == RX_CONFLICT
    assert be.writes == 0
    assert be.pause_calls == 1


def test_session_full_duplex_keeps_rx():
    be = RxCapableBackend()
    rep = _session(be, caps=CAPS_FULL)
    assert rep["state"] == "completed"
    assert rep["duplex"]["mode"] == RX_KEPT
    assert be.pause_calls == 0


def test_session_unknown_duplex_with_active_rx_is_refused():
    be = RxCapableBackend()
    rep = _session(be, caps=CAPS_UNKNOWN)
    assert rep["state"] == "rejected"
    assert rep["reason_code"] == DUPLEX_CAPABILITY_UNKNOWN
    assert be.writes == 0
    assert be.pause_calls == 0


def test_session_idle_rx_has_no_switch():
    be = RxCapableBackend()
    rep = _session(be, rx_active=False)
    assert rep["state"] == "completed"
    assert rep["duplex"]["mode"] == RX_IDLE
    assert be.pause_calls == 0
