# SPDX-License-Identifier: MIT
"""发射人工门（P8-1）：未开/过期/撤销可区分，直调底层不可绕。

全部用注入的假后端，不需要真实硬件。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.tx_guard import (  # noqa: E402
    GATE_EXPIRED, GATE_NOT_ARMED, GATE_REVOKED, GATE_TOKEN_MISMATCH,
    GATE_UNKNOWN_TOKEN, get_tx_gate, reset_tx_gate, tx_authorization,
)


@pytest.fixture(autouse=True)
def _fresh_gate():
    gate = reset_tx_gate()
    yield gate
    gate.disarm()


def test_unarmed_rejected_with_code():
    d = get_tx_gate().authorize(arm_manual=False)
    assert d.allowed is False
    assert d.code == GATE_NOT_ARMED
    assert d.reason


def test_arm_token_allows_and_ttl_expires():
    gate = get_tx_gate()
    tok = gate.arm(ttl_s=10, now=0.0)
    assert gate.authorize(token=tok, now=5.0).allowed is True
    d = gate.authorize(token=tok, now=12.0)
    assert d.allowed is False
    assert d.code == GATE_EXPIRED
    assert gate.snapshot()["state"] == "expired"


def test_revoke_blocks_even_explicit_arm_manual_and_token():
    gate = get_tx_gate()
    tok = gate.arm(ttl_s=60)
    gate.revoke(reason="操作员撤销")
    d1 = gate.authorize(arm_manual=True)
    assert d1.allowed is False and d1.code == GATE_REVOKED
    d2 = gate.authorize(token=tok)
    assert d2.allowed is False and d2.code == GATE_REVOKED
    assert "操作员撤销" in d2.reason
    # 重新 arm 才恢复（显式人工动作）
    tok2 = gate.arm(ttl_s=60)
    assert gate.authorize(token=tok2).allowed is True


def test_unknown_and_mismatched_tokens_are_distinguishable():
    gate = get_tx_gate()
    d = gate.authorize(token="never-issued")
    assert d.code == GATE_UNKNOWN_TOKEN
    tok = gate.arm(ttl_s=60)
    d2 = gate.authorize(token="other")
    assert d2.code == GATE_TOKEN_MISMATCH
    assert d2.code != d.code


def test_disarm_revokes_token():
    gate = get_tx_gate()
    tok = gate.arm(ttl_s=60)
    gate.disarm()
    d = gate.authorize(token=tok)
    assert d.allowed is False and d.code == GATE_UNKNOWN_TOKEN


# ── 底层直调拦截 ────────────────────────────────────────────────────────
class _FakeTxStream:
    def __init__(self):
        self.writes = []

    def write(self, buff, n, flags, time_ns):
        self.writes.append(int(n))
        return int(n)


def test_direct_soapy_write_tx_without_arm_is_rejected():
    from mbdsdr_ai.hal import SoapySDRBackend
    be = SoapySDRBackend()
    be._device = object()
    be._tx_stream = _FakeTxStream()
    iq = np.ones(16, dtype=np.complex64)
    assert be.write_tx(iq) is False
    assert be._tx_stream.writes == [], "未授权时底层不得真正写波"
    assert be.last_tx_reject["code"] == GATE_NOT_ARMED
    assert be.tx_rejects == 1


def test_direct_soapy_write_tx_with_arm_context_writes():
    from mbdsdr_ai.hal import SoapySDRBackend
    be = SoapySDRBackend()
    be._device = object()
    be._tx_stream = _FakeTxStream()
    gate = get_tx_gate()
    with tx_authorization(gate.authorize(arm_manual=True)):
        assert be.write_tx(np.ones(16, dtype=np.complex64)) is True
    assert be._tx_stream.writes == [16]


class _FakeDriver:
    """模拟一个会自行检查授权上下文的真实驱动。"""

    def __init__(self):
        self._sample_rate = 2.0e6
        self.writes = 0
        self.freq = None

    def supports_tx(self):
        return True

    def set_frequency(self, hz):
        self.freq = hz

    def write_tx(self, iq):
        from mbdsdr_ai.tx_guard import reject_unless_authorized
        if not reject_unless_authorized(self):
            return False
        self.writes += 1
        return True


@pytest.fixture
def manager():
    from mbdsdr_ai.hal import HardwareManager
    mgr = HardwareManager()
    old = mgr._active_backend
    fake = _FakeDriver()
    mgr._active_backend = fake
    yield mgr, fake
    mgr._active_backend = old


def test_script_call_through_manager_without_arm_is_rejected(manager):
    mgr, fake = manager
    ok = mgr.transmit_cw(145e6, 0.1, 0.001)   # 脚本直调，无 arm
    assert ok is False
    assert fake.writes == 0
    assert mgr.last_tx_code == GATE_NOT_ARMED
    assert mgr.last_tx_error


def test_script_call_through_manager_with_arm_works(manager):
    mgr, fake = manager
    assert mgr.transmit_modulated(145e6, np.ones(8, dtype=np.complex64),
                                  0.1, arm_manual=True) is True
    assert fake.writes == 1
    assert fake.freq == 145e6


def test_manager_revoke_blocks_even_armed(manager):
    mgr, fake = manager
    get_tx_gate().revoke(reason="测试撤销")
    assert mgr.transmit_cw(145e6, 0.1, 0.001, arm_manual=True) is False
    assert mgr.last_tx_code == GATE_REVOKED
    assert fake.writes == 0


# ── TC 上行路径 ─────────────────────────────────────────────────────────
def test_tc_uplink_reports_distinct_gate_reasons():
    from mbdsdr_ai.tc_uplink import tc_uplink_transmit
    r0 = tc_uplink_transmit(arm_manual=False, iq=[0.0], freq_hz=435e6)
    assert r0["status"] == "GATED" and r0["reason_code"] == GATE_NOT_ARMED

    gate = get_tx_gate()
    tok = gate.arm(ttl_s=5)
    r1 = tc_uplink_transmit(iq=[0.0], freq_hz=435e6, tx_token=tok)
    # token 仍在有效期内
    assert r1["status"] == "NEED_HW"

    gate.revoke(reason="撤销")
    r2 = tc_uplink_transmit(arm_manual=True, iq=[0.0], freq_hz=435e6)
    assert r2["status"] == "GATED" and r2["reason_code"] == GATE_REVOKED
