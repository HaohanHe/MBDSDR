# SPDX-License-Identifier: MIT
"""发射链路多普勒预偏：CW 真发波必须走 link 唯一真相源。

反证要点：
  * 已知频偏下，开预偏的发波频率 − 标称 == 预测 Δ（符号：接近→压低）；
  * 显式关预偏时严格等于标称（不做任何“差不多”的偏移）；
  * 默认由状态推导：有跟踪目标自动预偏且读数明示，无目标不偏并写「无目标，未预偏」；
  * backend_comp_active 互斥闸对 TX 同样生效（未预偏 + 原因）。
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from mbdsdr_ai import doppler_link, sdr_tools
from mbdsdr_ai.doppler_link import get_compensator
from mbdsdr_ai.tc_uplink import tc_uplink_transmit
from mbdsdr_ai.tx_guard import get_tx_gate

NOMINAL_HZ = 145_800_000.0
C_LIGHT_MPS = 299_792_458.0


class _Tracker:
    def __init__(self, range_rate_km_s: float):
        self.range_rate_km_s = range_rate_km_s

    def current_position(self, at_unix=None):
        rr = self.range_rate_km_s
        return {
            "valid": True,
            "elevation": 30.0,
            "range_rate_km_s": rr,
            "doppler_hz": -NOMINAL_HZ * rr * 1000.0 / C_LIGHT_MPS,
        }


class _FakeTxBackend:
    """最小 HAL 后端：能力已知（HackRF 量程）、记录真正调谐到的发波频点。"""

    def __init__(self):
        self._sample_rate = 8000.0
        self.rx_active = False
        self.device_args = "soapyhackrf"
        self.sent_freqs = []
        self.gains = []            # tx_guard 的"校验过必须真下发"门
        self.bandwidths = []
        self.writes = 0

    def get_info(self):
        return SimpleNamespace(driver="soapyhackrf", name="FakeHackRF")

    def supports_tx(self):
        return True

    def set_frequency(self, hz):
        self.sent_freqs.append(float(hz))

    def set_gain(self, gain_db):
        """2026-11-06 补桩：tx_guard 1477 起要求 gain/bandwidth 真下发，
        缺 setter 一律判 `tx_set_gain_failed` 拒绝发射。旧假后端没有这两个
        方法，3 条 IQ 回放用例在 HEAD 就红成 KeyError('doppler')——不是
        多普勒算错，是测试夹具落后于产品的"不能只校验不下发"红线。
        返回 True 表示已落到（假）硬件，断言一字未放宽。
        """
        self.gains.append(float(gain_db))
        return True

    def set_bandwidth(self, bw_hz):
        self.bandwidths.append(float(bw_hz))
        return True

    def write_tx(self, block):
        self.writes += 1
        return True

    def device_alive(self):
        return True


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    doppler_link._reset_compensator_for_tests()
    get_tx_gate().disarm()
    monkeypatch.delenv("MBDSDR_TX_BANDS", raising=False)
    monkeypatch.delenv("MBDSDR_TX_MAX_DURATION_S", raising=False)
    yield
    doppler_link._reset_compensator_for_tests()


def _arm_target(comp, rr=-2.0):
    comp.tracker = _Tracker(rr)
    assert comp.arm_target("ISS (ZARYA)", NOMINAL_HZ) is True


def _transmit(monkeypatch, backend, **extra):
    """走真实 handler：假 HAL + 一次性 token。返回 (ToolResult, 发波频点)。"""
    monkeypatch.setattr("mbdsdr_ai.hal.HardwareManager",
                        lambda: SimpleNamespace(get_active_backend=lambda: backend))
    args = {"frequency_hz": NOMINAL_HZ, "amplitude": 0.1,
            "duration_sec": 0.05, "gain_db": 0.0}
    args.update(extra)
    token = get_tx_gate().arm(ttl_s=10)
    res = sdr_tools._sdr_transmit_cw({**args, "confirm_token": token})
    sent = backend.sent_freqs[-1] if backend.sent_freqs else None
    return res, sent


def test_cw_auto_precomp_when_target_armed(monkeypatch):
    """有跟踪目标 + 未显式指定 → 默认预偏；发波频点严格等于预测预偏值。"""
    backend = _FakeTxBackend()
    comp = get_compensator()
    _arm_target(comp, rr=-2.0)          # 接近 → TX 应压低

    res, sent = _transmit(monkeypatch, backend)
    assert res.success is True
    assert sent == pytest.approx(NOMINAL_HZ + _expected_delta(-2.0), abs=1e-6)
    assert sent < NOMINAL_HZ, "接近目标时发射频率必须压低"
    note = res.data["doppler"]
    assert note["applied"] is True and note["mode"] == "auto"
    assert note["delta_hz"] == pytest.approx(sent - NOMINAL_HZ, abs=1e-6)
    assert "多普勒预偏: 已预偏" in res.content and "自动" in res.content


def test_cw_explicit_on_with_known_offset(monkeypatch):
    """显式开：发波频率与标称的差值 == 预测值（同一符号约定）。"""
    backend = _FakeTxBackend()
    comp = get_compensator()
    _arm_target(comp, rr=+2.0)          # 远离 → TX 应抬高

    res, sent = _transmit(monkeypatch, backend, doppler_precomp=True)
    expected = NOMINAL_HZ + _expected_delta(2.0)
    assert res.data["doppler"]["applied"] is True
    assert sent == pytest.approx(expected, abs=1e-6)
    assert sent > NOMINAL_HZ
    assert sent - NOMINAL_HZ == pytest.approx(_expected_delta(2.0), abs=1e-6)


def test_cw_explicit_off_is_exactly_nominal(monkeypatch):
    """显式关：严格按标称频点发射，即使目标已锁定/预测有效。"""
    backend = _FakeTxBackend()
    comp = get_compensator()
    _arm_target(comp, rr=-2.0)

    res, sent = _transmit(monkeypatch, backend, doppler_precomp=False)
    assert res.data["doppler"]["applied"] is False
    assert sent == NOMINAL_HZ          # 严格相等，不是 approx
    assert "未预偏" in res.content


def test_cw_auto_without_target_is_nominal_and_says_so(monkeypatch):
    """无目标（默认自动）：不偏，读数明写「无目标，未预偏」。"""
    backend = _FakeTxBackend()
    res, sent = _transmit(monkeypatch, backend)
    assert sent == NOMINAL_HZ
    assert res.data["doppler"]["applied"] is False
    assert "无目标，未预偏" in res.data["doppler"]["reason"]
    assert "无目标，未预偏" in res.content


def test_cw_explicit_on_without_target_still_no_offset(monkeypatch):
    """显式开但无目标：仍不偏（不拿过期/空预测硬凑一个偏移）。"""
    backend = _FakeTxBackend()
    res, sent = _transmit(monkeypatch, backend, doppler_precomp=True)
    assert sent == NOMINAL_HZ
    assert res.data["doppler"]["applied"] is False
    assert res.data["doppler"]["reason"]


def test_cw_backend_gate_blocks_precomp(monkeypatch):
    """后端补偿已接管：TX 也不再追加预偏，并如实给原因。"""
    backend = _FakeTxBackend()
    comp = get_compensator()
    _arm_target(comp, rr=-2.0)
    comp.set_backend_compensation_active(True)

    res, sent = _transmit(monkeypatch, backend, doppler_precomp=True)
    assert sent == NOMINAL_HZ
    assert res.data["doppler"]["applied"] is False
    assert "后端" in res.data["doppler"]["reason"]


# ── IQ 发射：同一仲裁；回放已知录音必须显式关预偏 ────────────────────────────
def _transmit_iq(monkeypatch, backend, **extra):
    monkeypatch.setattr("mbdsdr_ai.hal.HardwareManager",
                        lambda: SimpleNamespace(get_active_backend=lambda: backend))
    args = {"frequency_hz": NOMINAL_HZ, "iq": [0.3, 0.0, 0.2, 0.0],
            "amplitude": 0.1, "duration_sec": 0.05, "gain_db": 0.0}
    args.update(extra)
    token = get_tx_gate().arm(ttl_s=10)
    res = sdr_tools._sdr_transmit_iq({**args, "confirm_token": token})
    sent = backend.sent_freqs[-1] if backend.sent_freqs else None
    return res, sent


def test_iq_auto_precomp_when_target_armed(monkeypatch):
    """IQ 发射同样走 tx_freq：有目标自动预偏，差值 == 预测 Δ。"""
    backend = _FakeTxBackend()
    comp = get_compensator()
    _arm_target(comp, rr=-2.0)

    res, sent = _transmit_iq(monkeypatch, backend)
    assert res.success is True
    assert sent == pytest.approx(NOMINAL_HZ + _expected_delta(-2.0), abs=1e-6)
    assert res.data["doppler"]["applied"] is True
    assert res.data["doppler"]["mode"] == "auto"
    assert "已预偏" in res.content


def test_iq_replay_explicit_off_is_exactly_nominal(monkeypatch):
    """回放已知录音：显式 doppler_precomp=false → 严格标称，不改录音原频率。"""
    backend = _FakeTxBackend()
    comp = get_compensator()
    _arm_target(comp, rr=-2.0)

    res, sent = _transmit_iq(monkeypatch, backend, doppler_precomp=False)
    assert res.data["doppler"]["applied"] is False
    assert sent == NOMINAL_HZ          # 严格相等
    assert "未预偏" in res.content


def test_iq_without_target_is_nominal(monkeypatch):
    backend = _FakeTxBackend()
    res, sent = _transmit_iq(monkeypatch, backend)
    assert sent == NOMINAL_HZ
    assert res.data["doppler"]["applied"] is False
    assert "无目标，未预偏" in res.data["doppler"]["reason"]


# ── TC 上行：同一个“默认由状态推导”规则 ──────────────────────────────────
def test_tc_default_precomp_follows_tracked_target():
    comp = get_compensator()
    _arm_target(comp, rr=-2.0)
    r = tc_uplink_transmit(iq=np.zeros(8, dtype=complex),
                           freq_hz=NOMINAL_HZ, tx_token=get_tx_gate().arm(ttl_s=10))
    assert r["status"] == "NEED_HW"
    assert r["doppler"]["requested"] is None       # 未显式给 → 自动
    assert r["doppler"]["applied"] is True
    assert r["freq_hz"] == pytest.approx(NOMINAL_HZ + _expected_delta(-2.0), abs=1e-6)


def test_tc_default_off_without_target():
    r = tc_uplink_transmit(iq=np.zeros(8, dtype=complex),
                           freq_hz=NOMINAL_HZ, tx_token=get_tx_gate().arm(ttl_s=10))
    assert r["freq_hz"] == NOMINAL_HZ
    assert r["doppler"]["applied"] is False
    assert "无目标，未预偏" in r["doppler"]["reason"]


# ── 录音回放发射：回放已知录音默认严格标称（不自动改频） ────────────────
def test_playback_default_is_strict_nominal_with_readout(tmp_path):
    """回放已录 IQ：即使有跟踪目标，默认也不预偏；读数必须给出 mode=off。

    回放改的是「那段录音本来要表达的频率」，所以默认必须显式关闭预偏。
    """
    from mbdsdr_ai.playback import transmit_iq_playback
    from mbdsdr_ai.recorder import IQRecorder
    from mbdsdr_ai.tx_guard import TxCapabilities

    caps = TxCapabilities(
        known=True, freq_range_hz=(1e6, 6e9), bandwidth_range_hz=(2e5, 20e6),
        gain_range_db=(0.0, 47.0), max_duration_s=600.0, duplex="half",
        source="测试参数表")
    rec = IQRecorder(str(tmp_path / "replay"), sample_rate=1e6,
                     center_freq_hz=NOMINAL_HZ)
    assert rec.open()
    rec.write(np.ones(32, dtype=np.complex64))
    rec.close()
    path = str(tmp_path / "replay.sigmf-data")

    backend = _FakeTxBackend()
    comp = get_compensator()
    _arm_target(comp, rr=-2.0)          # 若误用自动预偏，会差 ~973 Hz

    rep = transmit_iq_playback(path, backend=backend,
                               tx_token=get_tx_gate().arm(ttl_s=10),
                               caps=caps, chunk_samples=16)
    assert rep["state"] == "completed"
    assert backend.sent_freqs[-1] == NOMINAL_HZ      # 严格相等，不 approx
    assert rep["doppler"]["mode"] == "off"
    assert rep["doppler"]["applied"] is False


def _expected_delta(range_rate_km_s: float) -> float:
    """TX 预偏后相对标称的偏移：tx − nominal = rr·1000/c·f（接近为负）。"""
    return range_rate_km_s * 1000.0 / C_LIGHT_MPS * NOMINAL_HZ
