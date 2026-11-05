# SPDX-License-Identifier: MIT
"""SDRController setter 真值回归：后端拒绝时 get_* 不得回谎。

背景：控制器曾先把请求值写进本地缓存再调后端，且不消费后端返回的 False。
于是「UI/MCP 看到的是请求值、硬件没动」——与 SDR 侧状态撒谎同一形态。
本文件钉住：失败不更新缓存、get_* 以后端回读为准、set_bandwidth 真下发。
"""
import sys
import os
from types import SimpleNamespace

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mbdsdr_ai.core.controller import SDRController, _RCShim  # noqa: E402
from mbdsdr_ai.core.errors import (  # noqa: E402
    SampleRateNotSupportedError, SDRUserError,
)


class _FakeRadio:
    """最小 duck-type 后端；可逐项模拟「显式拒绝」。"""

    def __init__(self, freq=98e6, sr=2.4e6, gain=20.0, bw=12.5e3):
        self.freq = float(freq)
        self.sr = float(sr)
        self.gain = float(gain)
        self.bw = float(bw)
        self.reject_freq = False
        self.reject_gain = False
        self.reject_sr = False
        self.reject_bw = False
        self.status = SimpleNamespace(error="")
        self.bw_set_calls = []

    def get_frequency(self):
        return self.freq

    def get_gain(self):
        return self.gain

    def get_sample_rate(self):
        return self.sr

    def set_frequency(self, hz):
        if self.reject_freq:
            self.status.error = "调谐器未接受该频率，硬件仍停在旧频"
            return False
        self.freq = float(hz)
        return True

    def set_gain(self, db):
        if self.reject_gain:
            self.status.error = "增益设置未落到硬件"
            return False
        self.gain = float(db)
        return True

    def set_sample_rate(self, sr):
        if self.reject_sr:
            self.status.error = "设备未接受该采样率"
            return False
        self.sr = float(sr)
        return True

    def set_bandwidth(self, hz):
        self.bw_set_calls.append(float(hz))
        if self.reject_bw:
            self.status.error = "带宽设置未落到硬件"
            return False
        self.bw = float(hz)
        return True


def _mk(fake):
    c = SDRController(backend=fake)
    c._connected = True
    return c


def test_set_frequency_failure_does_not_lie():
    fake = _FakeRadio(freq=98e6)
    c = _mk(fake)
    fake.reject_freq = True

    assert c.set_frequency(145e6) is False
    assert c.get_frequency() == 98e6, "失败后不得返回请求值"
    assert c.get_status().frequency_hz == 98e6
    assert "未接受该频率" in fake.status.error


def test_set_gain_failure_does_not_lie():
    fake = _FakeRadio(gain=20.0)
    c = _mk(fake)
    fake.reject_gain = True

    assert c.set_gain(40.0) is False
    assert c.get_gain() == 20.0
    assert c.get_status().gain_db == 20.0


def test_set_sample_rate_failure_does_not_lie():
    fake = _FakeRadio(sr=2.4e6)
    c = _mk(fake)
    fake.reject_sr = True

    with pytest.raises(SampleRateNotSupportedError):
        c.set_sample_rate(8e6)
    assert c.get_sample_rate() == 2.4e6, "抛错也不得把请求值写进缓存"
    assert c.get_status().sample_rate_hz == 2.4e6


def test_set_bandwidth_reaches_backend_and_failure_keeps_old_value():
    fake = _FakeRadio(bw=12.5e3)
    c = _mk(fake)

    assert c.set_bandwidth(15e3) is True
    assert fake.bw_set_calls == [15e3], "set_bandwidth 必须真下发后端"
    assert fake.bw == 15e3

    fake.reject_bw = True
    assert c.set_bandwidth(20e3) is False
    assert fake.bw == 15e3
    assert c._bandwidth_hz == 15e3, "失败不得覆盖本地缓存"


def test_rc_shim_reports_backend_truth():
    fake = _FakeRadio(freq=98e6, gain=20.0)
    c = _mk(fake)
    shim = _RCShim(c)
    fake.reject_freq = True

    assert shim.set_freq(145e6) is False
    assert shim.get_freq() == 98e6
    assert shim.get_gain() == 20.0


def test_connect_fails_when_initial_params_not_applied(monkeypatch):
    """设备打开了但参数没落到硬件：不许返回 True 并标记已连接。"""
    import mbdsdr_ai.device_manager as dm

    fake = _FakeRadio(sr=2.4e6)
    fake.reject_sr = True

    class _DM:
        @staticmethod
        def list_devices():
            return [SimpleNamespace(device_key="fake", serial="", name="FakeRadio")]

        @staticmethod
        def open(target):
            return fake

    monkeypatch.setattr(dm, "DeviceManager", _DM)
    c = SDRController()

    with pytest.raises(SDRUserError, match="初始参数未能生效"):
        c.connect("fake")
    assert c.is_connected() is False
    assert c._backend is None
