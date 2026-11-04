# SPDX-License-Identifier: MIT
"""发射状态机（P8-3）：取消/掉线/欠载/撤门立即停发；回放发射同门同机。"""
import os
import sys
import time

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai import playback as pb_mod  # noqa: E402
from mbdsdr_ai.tx_guard import (  # noqa: E402
    TX_ABORTED, TX_CANCELLED, TX_COMPLETED, TX_DEVICE_LOST, TX_GATE_REVOKED,
    TX_GATE_EXPIRED, TX_UNDERRUN, TxCapabilities, TxSession, get_tx_gate,
    reset_tx_gate,
)

CAPS = TxCapabilities(
    known=True,
    freq_range_hz=(1_000_000.0, 6_000_000_000.0),
    bandwidth_range_hz=(2_000_000.0, 20_000_000.0),
    gain_range_db=(0.0, 47.0),
    max_duration_s=600.0,
    duplex="half",
    source="测试参数表",
)


class FakeTxBackend:
    """可注入假后端：记录每次写波，可挂 on_write / 掉线 / 欠载钩子。"""

    def __init__(self):
        self._sample_rate = 1.0e6
        self.writes = []
        self.freq = None
        self.stopped = 0
        self.on_write = None
        self._alive = True
        self._underrun = False

    def supports_tx(self):
        return True

    def set_frequency(self, hz):
        self.freq = hz

    def device_alive(self):
        return self._alive

    def is_underrun(self):
        return self._underrun

    def stop_tx(self):
        self.stopped += 1

    def write_tx(self, block):
        from mbdsdr_ai.tx_guard import tx_authorized
        if not tx_authorized():
            return False
        self.writes.append(int(np.asarray(block).size))
        if self.on_write:
            self.on_write(len(self.writes))
        return True


@pytest.fixture(autouse=True)
def _fresh_gate(monkeypatch):
    monkeypatch.delenv("MBDSDR_TX_BANDS", raising=False)
    monkeypatch.delenv("MBDSDR_TX_MAX_DURATION_S", raising=False)
    reset_tx_gate()
    yield
    reset_tx_gate()


def _arm_token(ttl_s: float = 120.0) -> str:
    """测试用外部确认通道：签发一次性 token（S13 后不再有 arm_manual 直通）。"""
    from mbdsdr_ai.tx_guard import get_tx_gate
    return get_tx_gate().arm(ttl_s=ttl_s)


def _run(backend, samples, **kw):
    sess = TxSession(backend)
    kw.setdefault("freq_hz", 145e6)
    if kw.pop("arm_manual", True):
        kw.setdefault("tx_token", _arm_token())
    kw.setdefault("caps", CAPS)
    return sess, sess.transmit(samples, **kw)


def test_complete_transmission_tracks_samples():
    be = FakeTxBackend()
    sess, rep = _run(be, np.ones(100, dtype=np.complex64), chunk_samples=10)
    assert rep["state"] == TX_COMPLETED
    assert rep["sent_samples"] == 100
    assert len(be.writes) == 10
    assert be.stopped == 0
    assert be.freq == 145e6


def test_unarmed_session_rejected_before_any_write():
    be = FakeTxBackend()
    _, rep = _run(be, np.ones(10, dtype=np.complex64), arm_manual=False)
    assert rep["state"] == "rejected"
    assert rep["reason_code"] == "gate_not_armed"
    assert be.writes == []


def test_cancel_mid_transmit_stops_immediately():
    be = FakeTxBackend()
    sess = TxSession(be)

    def on_write(n):
        if n == 3:
            sess.cancel("操作员中途取消")

    be.on_write = on_write
    rep = sess.transmit(np.ones(100, dtype=np.complex64), freq_hz=145e6,
                        tx_token=_arm_token(), caps=CAPS, chunk_samples=10)
    assert rep["state"] == TX_CANCELLED
    assert rep["reason_code"] == "tx_cancel_requested"
    assert rep["reason"] == "操作员中途取消"
    assert len(be.writes) == 3
    assert be.stopped == 1
    assert rep["sent_samples"] == 30


def test_device_lost_mid_transmit_stops():
    be = FakeTxBackend()

    def on_write(n):
        if n == 2:
            be._alive = False

    be.on_write = on_write
    _, rep = _run(be, np.ones(100, dtype=np.complex64), chunk_samples=10)
    assert rep["state"] == TX_ABORTED
    assert rep["reason_code"] == TX_DEVICE_LOST
    assert len(be.writes) == 2
    assert be.stopped == 1


def test_underrun_mid_transmit_stops():
    be = FakeTxBackend()

    def on_write(n):
        if n == 2:
            be._underrun = True

    be.on_write = on_write
    _, rep = _run(be, np.ones(100, dtype=np.complex64), chunk_samples=10)
    assert rep["state"] == TX_ABORTED
    assert rep["reason_code"] == TX_UNDERRUN
    assert len(be.writes) == 2
    assert be.stopped == 1


def test_gate_revoked_mid_transmit_stops():
    be = FakeTxBackend()
    gate = get_tx_gate()

    def on_write(n):
        if n == 2:
            gate.revoke(reason="中途撤销")

    be.on_write = on_write
    _, rep = _run(be, np.ones(100, dtype=np.complex64), chunk_samples=10)
    assert rep["state"] == TX_ABORTED
    assert rep["reason_code"] == TX_GATE_REVOKED
    assert "中途撤销" in rep["reason"]
    assert len(be.writes) == 2
    assert be.stopped == 1


def test_token_expiry_mid_transmit_stops():
    be = FakeTxBackend()
    gate = get_tx_gate()
    tok = gate.arm(ttl_s=0.15)

    def on_write(n):
        if n == 1:
            time.sleep(0.2)

    be.on_write = on_write
    sess = TxSession(be)
    rep = sess.transmit(np.ones(50, dtype=np.complex64), freq_hz=145e6,
                        tx_token=tok, caps=CAPS, chunk_samples=10)
    assert rep["state"] == TX_ABORTED
    assert rep["reason_code"] == TX_GATE_EXPIRED
    assert len(be.writes) == 1
    assert be.stopped == 1


def test_no_lingering_writer_after_abort():
    be = FakeTxBackend()
    be._alive = False
    _, rep = _run(be, np.ones(100, dtype=np.complex64), chunk_samples=10)
    assert rep["state"] == TX_ABORTED
    assert be.writes == []
    assert be.stopped == 1


# ── IQ 回放发射路径 ─────────────────────────────────────────────────────
def _make_recording(tmp_path, n=100, rate=1e6, center=145e6):
    from mbdsdr_ai.recorder import IQRecorder
    rec = IQRecorder(str(tmp_path / "txplay"), sample_rate=rate,
                     center_freq_hz=center)
    assert rec.open()
    rec.write(np.ones(n, dtype=np.complex64))
    rec.close()
    return str(tmp_path / "txplay.sigmf-data")


def test_playback_transmit_completes_and_uses_meta_freq(tmp_path):
    from mbdsdr_ai.playback import transmit_iq_playback
    path = _make_recording(tmp_path)
    be = FakeTxBackend()
    rep = transmit_iq_playback(path, backend=be, tx_token=_arm_token(), caps=CAPS,
                               chunk_samples=32)
    assert rep["state"] == TX_COMPLETED
    assert rep["sent_samples"] == 100
    assert be.freq == 145e6
    assert rep["playback"]["meta_valid"] is True


def test_playback_transmit_requires_gate(tmp_path):
    from mbdsdr_ai.playback import transmit_iq_playback
    path = _make_recording(tmp_path)
    be = FakeTxBackend()
    rep = transmit_iq_playback(path, backend=be, arm_manual=False, caps=CAPS)
    assert rep["state"] == "rejected"
    assert rep["reason_code"] == "gate_not_armed"
    assert be.writes == []


def test_playback_missing_or_truncated_is_rejected(tmp_path):
    from mbdsdr_ai.playback import transmit_iq_playback
    be = FakeTxBackend()
    rep = transmit_iq_playback(str(tmp_path / "nope.sigmf-data"),
                               backend=be, tx_token=_arm_token(), caps=CAPS)
    assert rep["state"] == "rejected"
    assert rep["reason_code"].startswith("playback_")
    assert be.writes == []

    from mbdsdr_ai.recorder import IQRecorder
    rec = IQRecorder(str(tmp_path / "bad"), sample_rate=1e6,
                     center_freq_hz=145e6)
    rec.open()
    rec.write(np.ones(10, dtype=np.complex64))
    rec.close()
    data = tmp_path / "bad.sigmf-data"
    raw = data.read_bytes()
    data.write_bytes(raw[:-5])          # 破坏样本对齐 → truncated
    rep2 = transmit_iq_playback(str(data), backend=be, tx_token=_arm_token(), caps=CAPS)
    assert rep2["state"] == "rejected"
    assert "truncated" in rep2["reason_code"] or "truncated" in rep2["reason"]


def test_playback_deleted_mid_read_is_aborted(tmp_path, monkeypatch):
    from mbdsdr_ai.playback import IQPlayback, transmit_iq_playback
    path = _make_recording(tmp_path)
    be = FakeTxBackend()

    def read_deleted(self, n):
        # 直接落到 read_samples 检测到文件被删时的同一状态（Windows 上
        # mmap 持有句柄时 os.remove 不一定成功，故用状态注入等价复现）。
        self.state = "deleted"
        self.error_code = "deleted"
        self.last_error = "回放文件在读取过程中被删除或移走"
        self.opened = False
        self._mmap = None
        return np.empty(0, dtype=np.complex64)

    monkeypatch.setattr(IQPlayback, "read_samples", read_deleted)
    rep = transmit_iq_playback(path, backend=be, tx_token=_arm_token(), caps=CAPS,
                               chunk_samples=32)
    assert rep["state"] == "aborted"
    assert rep["reason_code"] == "tx_source_deleted"
    assert "删除" in rep["reason"] or "移走" in rep["reason"]
