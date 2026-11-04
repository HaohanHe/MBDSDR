# SPDX-License-Identifier: MIT
"""发射越界防护（P8-2）：设备能力/法规范围，未知能力不假设。"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai import tx_guard as tg  # noqa: E402
from mbdsdr_ai.tx_guard import (  # noqa: E402
    RegulatoryBand, RegulatoryPolicy, TxCapabilities, check_tx_request,
    capabilities_from_device, policy_from_env, reset_tx_gate,
)

CAPS = TxCapabilities(
    known=True,
    freq_range_hz=(1_000_000.0, 6_000_000_000.0),
    bandwidth_range_hz=(2_000_000.0, 20_000_000.0),
    gain_range_db=(0.0, 47.0),
    max_duration_s=60.0,
    duplex="half",
    device_name="HackRF One",
    source="测试参数表",
)


@pytest.fixture(autouse=True)
def _fresh_gate():
    yield reset_tx_gate()


def test_frequency_out_of_range_rejected_with_basis():
    r = check_tx_request(CAPS, freq_hz=6_500_000_000.0)
    assert r.ok is False
    assert r.code == tg.LIMIT_FREQ
    assert "超出设备 TX 频率范围" in r.reason
    assert any("6" in b for b in r.basis), r.basis


def test_bandwidth_out_of_range_rejected():
    r = check_tx_request(CAPS, freq_hz=145e6, bandwidth_hz=25e6)
    assert r.ok is False and r.code == tg.LIMIT_BANDWIDTH


def test_gain_out_of_range_rejected_not_clamped():
    r = check_tx_request(CAPS, freq_hz=145e6, gain_db=60.0)
    assert r.ok is False and r.code == tg.LIMIT_GAIN
    assert "47" in r.reason


def test_duration_out_of_range_by_device_and_policy():
    r = check_tx_request(CAPS, freq_hz=145e6, duration_s=120.0)
    assert r.ok is False and r.code == tg.LIMIT_DURATION
    policy = RegulatoryPolicy("test", [RegulatoryBand(144e6, 148e6, "144-148M")],
                              max_tx_duration_s=3.0)
    r2 = check_tx_request(CAPS, freq_hz=145e6, duration_s=5.0, regulatory=policy)
    assert r2.ok is False and r2.code == tg.LIMIT_DURATION
    assert "3" in r2.reason


def test_unknown_capability_is_reported_honestly():
    r = check_tx_request(TxCapabilities(known=False), freq_hz=145e6)
    assert r.ok is False and r.code == tg.LIMIT_CAP_UNKNOWN
    assert "未知" in r.reason and "用户确认" in r.reason
    r2 = check_tx_request(CAPS, freq_hz=145e6, bandwidth_hz=5e6)
    assert r2.ok is True, "已知带宽区间内应放行"


def test_requested_field_unknown_blocks():
    caps = TxCapabilities(known=True, freq_range_hz=(1e6, 6e9))
    r = check_tx_request(caps, freq_hz=145e6, bandwidth_hz=5e6)
    assert r.ok is False and r.code == tg.LIMIT_CAP_UNKNOWN
    assert r.field_status["bandwidth"] == "capability_unknown"
    r2 = check_tx_request(caps, freq_hz=145e6, gain_db=10)
    assert r2.ok is False and r2.code == tg.LIMIT_CAP_UNKNOWN


def test_regulatory_band_rejects_outside_and_accepts_inside():
    policy = RegulatoryPolicy("operator", [
        RegulatoryBand(144e6, 148e6, "144-148MHz", "操作员声明")])
    bad = check_tx_request(CAPS, freq_hz=433e6, regulatory=policy)
    assert bad.ok is False and bad.code == tg.LIMIT_REGULATORY
    assert "144-148MHz" in bad.reason or "允许频段" in " ".join(bad.basis)
    good = check_tx_request(CAPS, freq_hz=145e6, regulatory=policy)
    assert good.ok is True
    assert "144-148MHz" in " ".join(good.basis)


def test_require_regulatory_without_config_is_rejected():
    r = check_tx_request(CAPS, freq_hz=145e6, require_regulatory=True)
    assert r.ok is False and r.code == tg.LIMIT_REGULATORY
    assert "未配置" in r.reason


def test_env_policy_parse():
    p = policy_from_env({"MBDSDR_TX_BANDS": "144000000-148000000,430000000-440000000",
                         "MBDSDR_TX_MAX_DURATION_S": "10"})
    assert p is not None and len(p.bands) == 2
    assert p.max_tx_duration_s == 10.0
    assert policy_from_env({}) is None


def test_capabilities_from_known_device_profile():
    from mbdsdr_ai.sdr_backend import SDRDevice
    dev = SDRDevice(device_type="hackrf", device_id="x", name="HackRF One",
                    frequency_range=(1e6, 6e9), sample_rate_range=(2e6, 20e6),
                    max_gain=40, supports_tx=True)
    caps = capabilities_from_device(dev)
    assert caps.known is True
    assert caps.freq_range_hz == (1e6, 6e9)
    assert caps.gain_range_db == (0.0, 47.0)
    assert caps.duplex == "half"
    assert caps.source


def test_capabilities_from_unknown_device_stays_unknown():
    from mbdsdr_ai.sdr_backend import SDRDevice
    dev = SDRDevice(device_type="ai_sdr_mini", device_id="x", name="AI SDR",
                    frequency_range=(1e6, 6e9), sample_rate_range=(2e6, 20e6),
                    max_gain=40, supports_tx=True)
    caps = capabilities_from_device(dev)
    assert caps.known is False
    assert "未知" in caps.source


# ── 工具层：越界拒绝且在拒绝前不写波 ─────────────────────────────────────
class _FakeDriver:
    def __init__(self):
        self._sample_rate = 2.0e6
        self.writes = 0
        self.freq = None

    def supports_tx(self):
        return True

    def set_frequency(self, hz):
        self.freq = hz

    def get_info(self):
        from mbdsdr_ai.hal import DeviceInfo
        return DeviceInfo(name="HackRF One", driver="soapy")

    def write_tx(self, iq):
        from mbdsdr_ai.tx_guard import reject_unless_authorized
        if not reject_unless_authorized(self):
            return False
        self.writes += 1
        return True


@pytest.fixture
def fake_manager(monkeypatch):
    from mbdsdr_ai.hal import HardwareManager
    mgr = HardwareManager()
    fake = _FakeDriver()
    old = mgr._active_backend
    mgr._active_backend = fake
    monkeypatch.setattr(mgr, "get_active_backend", lambda: fake)
    yield mgr, fake
    mgr._active_backend = old


def test_tool_rejects_out_of_range_frequency_before_write(fake_manager):
    from mbdsdr_ai.sdr_tools import _sdr_transmit_iq
    mgr, fake = fake_manager
    r = _sdr_transmit_iq({"frequency_hz": 6_500_000_000.0,
                          "iq": [0.1, -0.1] * 20,
                          "arm_manual": True})
    assert r.success is False
    assert "frequency_out_of_range" in r.content
    assert fake.writes == 0


def test_tool_rejects_unknown_bandwidth_before_write(fake_manager):
    from mbdsdr_ai.sdr_tools import _sdr_transmit_iq
    mgr, fake = fake_manager
    r = _sdr_transmit_iq({"frequency_hz": 145e6,
                          "iq": [0.1, -0.1] * 20,
                          "bandwidth_hz": 5e6,
                          "arm_manual": True})
    assert r.success is False
    assert "capability_unknown" in r.content
    assert fake.writes == 0


def test_tool_in_range_path_reaches_driver(fake_manager):
    from mbdsdr_ai.sdr_tools import _sdr_transmit_iq
    mgr, fake = fake_manager
    r = _sdr_transmit_iq({"frequency_hz": 145e6,
                          "iq": [0.1, -0.1] * 20,
                          "arm_manual": True})
    assert r.success is True, r.content[:200]
    assert fake.writes == 1
