# SPDX-License-Identifier: MIT
"""HackRF/USRP connect() 必须显式下发初始化序列并回读（审计 B 节 P1）。

缺陷形态（修复前）：两个后端 connect() 只翻 connected=True，不下发
频率/采样率/增益 → status 显示 98 MHz / 8(1) MS/s / 0 dB 全是软件默认值，
硬件停在出厂/上次残留状态；USRP 连构造失败都静默 return False 不写 error。
对照 RTL/RtlTcp/Pluto 已有的「打开后逐项显式下发 + 回读」纪律。

断言分三层：
- 行为层：假驱动逐条记录下发——connect 后频率/采样率/增益真的发了出去；
- 回读层：驱动把请求值改写成另一档（UHD coerce / 驱动吸附）时，status
  必须显示回读值而不是请求值；
- 失败层：任一下发失败 → connect False + status.error 可见 + connected False，
  绝不带着半配置硬件上线。

反证在仓库外探针（旧实现 connect 后假驱动一条命令都没收到）。
"""

import os
import sys
import types

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai.sdr_backend import HackRFBackend, USRPBackend  # noqa: E402


class _FakeHackrfCtls:
    """hackrf_params.HackRFBackend 风格替身：bool setter + 成功才更新的缓存 getter。"""

    def __init__(self, fail=()):
        self.calls = []
        self.connected = False
        self._freq = 0.0
        self._rate = 0.0
        self._lna = 0
        self._vga = 0
        self._fail = set(fail)

    def connect(self):
        self.connected = True
        return True

    def disconnect(self):
        self.connected = False

    def set_frequency(self, hz):
        self.calls.append(("freq", float(hz)))
        if "freq" in self._fail:
            return False
        self._freq = float(hz)
        return True

    def get_frequency(self):
        return self._freq

    def set_sample_rate(self, hz):
        self.calls.append(("rate", float(hz)))
        if "rate" in self._fail:
            return False
        self._rate = float(hz)
        return True

    def get_sample_rate(self):
        return self._rate

    def set_lna_gain(self, db):
        self.calls.append(("lna", int(db)))
        if "lna" in self._fail:
            return False
        self._lna = int(db)
        return True

    def set_vga_gain(self, db):
        self.calls.append(("vga", int(db)))
        if "vga" in self._fail:
            return False
        self._vga = int(db)
        return True


class TestHackRFConnectInit:
    def test_connect_dispatches_rate_freq_gain(self):
        be = HackRFBackend()
        fake = _FakeHackrfCtls()
        be._hackrf = fake
        try:
            assert be.connect() is True
            kinds = [k for k, _ in fake.calls]
            assert "rate" in kinds, "connect 必须下发采样率"
            assert "freq" in kinds, "connect 必须下发中心频率"
            assert "lna" in kinds and "vga" in kinds, "connect 必须下发增益"
            assert be.status.sample_rate_hz == fake.get_sample_rate()
            assert be.status.frequency_hz == fake.get_frequency()
            assert be.status.error == ""
        finally:
            be.disconnect()

    def test_connect_honors_preconfigured_frequency(self):
        be = HackRFBackend()
        be.status.frequency_hz = 433_000_000.0
        fake = _FakeHackrfCtls()
        be._hackrf = fake
        try:
            assert be.connect() is True
            assert ("freq", 433_000_000.0) in fake.calls
            assert be.status.frequency_hz == 433_000_000.0
        finally:
            be.disconnect()

    @pytest.mark.parametrize("stage", ["rate", "freq", "lna", "vga"])
    def test_init_failure_connects_false_with_error(self, stage):
        be = HackRFBackend()
        fake = _FakeHackrfCtls(fail=(stage,))
        be._hackrf = fake
        try:
            assert be.connect() is False
            assert be.status.connected is False
            assert be.status.error, "初始化下发失败必须可见，不许静默"
        finally:
            be.disconnect()


class _FakeMultiUSRP:
    """UHD MultiUSRP 替身：记录下发；coerce_rate 模拟硬件改写请求值。"""

    def __init__(self, args="", fail=(), coerce_rate=None):
        self.calls = []
        self._rate = 0.0
        self._freq = 0.0
        self._gain = 0.0
        self._fail = set(fail)
        self._coerce_rate = coerce_rate

    def set_rx_rate(self, rate):
        self.calls.append(("rate", float(rate)))
        if "rate" in self._fail:
            raise RuntimeError("uhd set_rx_rate 拒绝")
        self._rate = float(self._coerce_rate if self._coerce_rate else rate)

    def get_rx_rate(self):
        return self._rate

    def set_rx_freq(self, req):
        self.calls.append(("freq", float(req)))
        if "freq" in self._fail:
            raise RuntimeError("uhd set_rx_freq 拒绝")
        self._freq = float(req)

    def get_rx_freq(self):
        return self._freq

    def set_rx_gain(self, gain):
        self.calls.append(("gain", float(gain)))
        if "gain" in self._fail:
            raise RuntimeError("uhd set_rx_gain 拒绝")
        self._gain = float(gain)

    def get_rx_gain(self):
        return self._gain


def _install_uhd(monkeypatch, factory):
    usrp_mod = types.SimpleNamespace(MultiUSRP=factory)
    libpyuhd_types = types.SimpleNamespace(tune_request=lambda hz: hz)
    uhd_mod = types.SimpleNamespace(
        usrp=usrp_mod,
        libpyuhd=types.SimpleNamespace(types=libpyuhd_types))
    monkeypatch.setitem(sys.modules, "uhd", uhd_mod)


class TestUSRPConnectInit:
    def test_connect_dispatches_rate_freq_gain(self, monkeypatch):
        made = []

        def factory(args=""):
            dev = _FakeMultiUSRP(args)
            made.append(dev)
            return dev

        _install_uhd(monkeypatch, factory)
        be = USRPBackend()
        try:
            assert be.connect() is True
            assert made, "必须真的构造 MultiUSRP"
            kinds = [k for k, _ in made[0].calls]
            assert "rate" in kinds and "freq" in kinds and "gain" in kinds
            assert be.status.sample_rate_hz == made[0].get_rx_rate()
            assert be.status.frequency_hz == made[0].get_rx_freq()
            assert be.status.gain_db == made[0].get_rx_gain()
            assert be.status.error == ""
        finally:
            be.disconnect()

    def test_status_shows_coerced_rate_not_request(self, monkeypatch):
        """UHD 把 1.000M 改写成 1.024M 时，status 必须显示回读值。"""
        _install_uhd(monkeypatch,
                     lambda args="": _FakeMultiUSRP(args, coerce_rate=1_024_000.0))
        be = USRPBackend()
        try:
            assert be.connect() is True
            assert be.status.sample_rate_hz == 1_024_000.0
        finally:
            be.disconnect()

    def test_constructor_failure_writes_error(self, monkeypatch):
        def factory(args=""):
            raise RuntimeError("No UHD device found")

        _install_uhd(monkeypatch, factory)
        be = USRPBackend()
        assert be.connect() is False
        assert be.status.connected is False
        assert be.status.error, "构造失败不许静默 return False"

    @pytest.mark.parametrize("stage", ["rate", "freq", "gain"])
    def test_init_failure_connects_false_with_error(self, monkeypatch, stage):
        _install_uhd(monkeypatch,
                     lambda args="": _FakeMultiUSRP(args, fail=(stage,)))
        be = USRPBackend()
        try:
            assert be.connect() is False
            assert be.status.connected is False
            assert be.status.error
        finally:
            be.disconnect()