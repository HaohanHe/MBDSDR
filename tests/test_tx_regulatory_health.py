# SPDX-License-Identifier: MIT
"""D1 发射法规口径：默认未配置时必须明示，且无 token 一律拒绝。

判据：
  1. ``require_regulatory`` 默认 False；未配置 ``MBDSDR_TX_BANDS`` 时健康
     快照必须写「发射法规校验未启用」，不把「没检查」伪装成「已合规」；
  2. 默认配置下没有一次性 token 的发射请求必须被 `tx_guard` 拒绝，且不会
     触达后端 ``write_tx``。
"""
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.tx_guard import (  # noqa: E402
    GATE_NOT_ARMED,
    REQUIRE_REGULATORY_DEFAULT,
    TxSession,
    get_tx_gate,
    reset_tx_audit,
    reset_tx_gate,
    tx_regulatory_status,
)


def _isolate_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("MBDSDR_TX_AUDIT_PATH", str(tmp_path / "tx_audit.jsonl"))
    reset_tx_audit()


class _CountingBackend:
    def __init__(self):
        self.writes = 0

    def supports_tx(self):
        return True

    def write_tx(self, _iq):
        self.writes += 1
        return True


def test_regulatory_default_is_false_and_health_says_disabled(tmp_path,
                                                              monkeypatch):
    _isolate_audit(tmp_path, monkeypatch)
    monkeypatch.delenv("MBDSDR_TX_BANDS", raising=False)
    monkeypatch.delenv("MBDSDR_TX_MAX_DURATION_S", raising=False)
    reset_tx_gate()

    assert REQUIRE_REGULATORY_DEFAULT is False
    status = tx_regulatory_status()
    assert status["enabled"] is False
    assert status["require_regulatory"] is False
    assert "发射法规校验未启用" in status["message"]

    snap = get_tx_gate().snapshot()
    assert snap["regulatory"]["enabled"] is False
    assert "发射法规校验未启用" in snap["regulatory"]["message"]


def test_configured_bands_turn_regulatory_health_on(tmp_path, monkeypatch):
    _isolate_audit(tmp_path, monkeypatch)
    monkeypatch.setenv("MBDSDR_TX_BANDS", "144000000-148000000")
    reset_tx_gate()

    status = tx_regulatory_status()
    assert status["enabled"] is True
    assert status["bands"] == ["144000000-148000000"]
    assert "发射法规校验已启用" in status["message"]


def test_default_no_token_transmit_is_rejected_before_backend(
        tmp_path, monkeypatch):
    _isolate_audit(tmp_path, monkeypatch)
    monkeypatch.delenv("MBDSDR_TX_BANDS", raising=False)
    reset_tx_gate()
    backend = _CountingBackend()

    rep = TxSession(backend).transmit(
        [np.zeros(16, dtype=np.complex64)], freq_hz=145_000_000.0)

    assert rep["state"] == "rejected"
    assert rep["reason_code"] == GATE_NOT_ARMED
    assert backend.writes == 0, "未授权不得触达真实 write_tx"


def test_tool_entry_without_token_is_refused_before_any_hardware(
        tmp_path, monkeypatch):
    _isolate_audit(tmp_path, monkeypatch)
    monkeypatch.delenv("MBDSDR_TX_BANDS", raising=False)
    reset_tx_gate()

    from mbdsdr_ai.sdr_tools import _sdr_transmit_cw

    res = _sdr_transmit_cw({"frequency_hz": 145_000_000.0})

    assert res.success is False
    assert "未执行 CW 发射" in res.content
    assert res.data["reason_code"] == GATE_NOT_ARMED
