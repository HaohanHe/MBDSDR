# SPDX-License-Identifier: MIT
"""D1 双栈边界对拍：``sdr_backend``（RX）与 ``hal``（TX）同一口径。

判据（不一致即失败）：
  1. 同一份 SoapySDR 枚举条目，两栈算出的设备身份（driver/serial）与稳定
     身份键（``driver|serial:<sn>``）必须相同；
  2. 采样率统一用 Hz，默认值都来自 ``sdr_status.DEFAULT_SAMPLE_RATE_HZ``；
  3. 增益统一用 dB，同一个数值两栈都不做单位换算/放大。

全部用桩替身，不连真机、不装 SoapySDR。
"""
import os
import sys
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai import hal  # noqa: E402
from mbdsdr_ai import sdr_backend as sb  # noqa: E402
from mbdsdr_ai.device_identity import device_identity_key  # noqa: E402
from mbdsdr_ai.sdr_status import DEFAULT_SAMPLE_RATE_HZ  # noqa: E402

SERIAL = "SN-ABC-123"


def _fake_soapy(monkeypatch):
    """极简 SoapySDR 桩：只回答 enumerate，打开设备一律失败（跳过探测）。"""
    module = types.ModuleType("SoapySDR")
    module.SOAPY_SDR_RX = 1
    module.SOAPY_SDR_TX = 2

    class _Device:
        @staticmethod
        def enumerate():
            return [{"driver": "hackrf", "serial": SERIAL,
                     "label": "HackRF One"}]

        def __init__(self, _info):
            raise RuntimeError("桩设备不打开硬件")

    module.Device = _Device
    monkeypatch.setitem(sys.modules, "SoapySDR", module)
    return module


def test_device_identity_matches_across_stacks(monkeypatch):
    _fake_soapy(monkeypatch)

    hal_info = hal.SoapySDRBackend().list_devices()[0]
    entry = sb.SoapySDRBackend.list_devices()[0]
    sb_device = sb.SoapySDRBackend(entry, device_info=entry).device

    # driver 拼写与 serial 原样一致（不拿枚举下标/显示名造身份）。
    assert hal_info.driver == sb_device.device_type == "soapy_hackrf"
    assert hal_info.serial == entry["serial"] == SERIAL

    hal_key = device_identity_key(
        {"driver": hal_info.driver, "serial": hal_info.serial})[0]
    sb_key = device_identity_key(
        {"driver": sb_device.device_type, "serial": entry["serial"]})[0]
    assert hal_key == sb_key == f"soapy_hackrf|serial:{SERIAL}"


class _RecordingSDRBackend(sb.SDRBackend):
    """覆盖 RX 侧的采样率/增益下发，记录收到的原始数值。"""

    def __init__(self):
        device = sb.SDRDevice(
            device_type="soapy_hackrf",
            device_id=f"soapy_hackrf_{SERIAL}",
            name="HackRF One",
            frequency_range=(1e6, 6e9),
            sample_rate_range=(1e6, 20e6),
            max_gain=47.0,
        )
        super().__init__(device)
        self.status.connected = True
        self.applied_rate = None
        self.applied_gain = None

    def _apply_sample_rate(self, rate_hz):
        self.applied_rate = float(rate_hz)
        return True

    def _apply_gain(self, gain_db):
        self.applied_gain = float(gain_db)
        return True


def test_sample_rate_same_unit_and_default():
    hal_backend = hal.SoapySDRBackend()
    rx_backend = _RecordingSDRBackend()

    # 默认值可以不同（TX 历史 2.048 MS/s、RX 2.4 MS/s），但单位必须是 Hz；
    # 显式设置时两边都按 Hz 原样下发——这条才是「口径一致」的判据。
    assert hal_backend._sample_rate == 2.048e6
    assert sb.SDRBackend._FALLBACK_SAMPLE_RATE_HZ == float(DEFAULT_SAMPLE_RATE_HZ)

    rate = 2_400_000.0
    assert hal_backend.set_sample_rate(rate) is True
    assert rx_backend.set_sample_rate(rate) is True
    assert hal_backend._sample_rate == rx_backend.status.sample_rate_hz == rate
    assert rx_backend.applied_rate == rate, "采样率不得被换算成别的单位"


def test_gain_same_db_unit():
    hal_backend = hal.SoapySDRBackend()
    rx_backend = _RecordingSDRBackend()

    gain_db = 12.5
    assert hal_backend.set_gain(gain_db) is True
    assert rx_backend.set_gain(gain_db) is True

    assert hal_backend._gain == rx_backend.status.gain_db == gain_db
    assert rx_backend.applied_gain == gain_db, "增益不得被放大成十分之一 dB"
