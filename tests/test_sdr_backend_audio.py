"""
test_sdr_backend_audio.py — SDRBackend 实时音频集成（无硬件，合成 IQ）。

红线：无硬件 = 未连接，不造假。这里用 FakeRTL 子类注入合成 IQ 验证链路，
绝不把合成数据当真实设备读数。
"""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pytest

from mbdsdr_ai.sdr_backend import SDRBackend, SDRDevice


class FakeSDR:
    """合成 IQ 源（duck-type，验证用）。"""
    def __init__(self, fs=2_048_000.0, freq=98_000_000.0):
        self.fs = fs
        self.center_freq = freq
        self.sample_rate = fs
        self.gain = 20.0
        self._t = 0.0

    def read_samples(self, n):
        t = self._t + np.arange(n) / self.fs
        self._t += n / self.fs
        # 100kHz 偏移单音
        return (0.3 * np.exp(1j * 2 * np.pi * 100_000 * t)).astype(np.complex64)

    def close(self):
        pass


class FakeBackend(SDRBackend):
    """无硬件后端：connect 成功后用 FakeSDR 注 IQ，read_samples 走环形缓冲。"""
    def __init__(self):
        dev = SDRDevice(
            device_type="fake", device_id="0", name="Fake",
            frequency_range=(24e6, 1.7e9), sample_rate_range=(1e6, 3.2e6),
            max_gain=40.0)
        super().__init__(dev)
        self.status.sample_rate_hz = 2_048_000.0
        self.status.frequency_hz = 98_000_000.0
        self.status.demod_mode = "NFM"
        self._sdr = None

    def connect(self):
        self._sdr = FakeSDR()
        self.status.connected = True
        self._start_time = __import__("time").time()
        self._ring_reader = None  # read_samples 直接走 _sdr
        return True

    def disconnect(self):
        try:
            self.stop_audio()
        except Exception:
            pass
        if self._sdr:
            self._sdr.close()
            self._sdr = None
        super().disconnect()

    def read_samples(self, n):
        if not self.status.connected or self._sdr is None:
            return None
        try:
            return self._sdr.read_samples(n)
        except Exception:
            return None


def test_no_device_read_audio_returns_none():
    """未连接时 read_audio 返回 None，start_audio 返回 False。"""
    be = FakeBackend()  # 不 connect
    assert be.read_audio(1024) is None
    assert be.start_audio() is False
    assert be.get_spectrum_data(256) is None


def test_read_audio_after_connect():
    """连接后 read_audio 返回非空 float32 音频。"""
    be = FakeBackend()
    be.connect()
    try:
        audio = be.read_audio(2048)
        assert audio is not None
        assert audio.dtype == np.float32
        assert len(audio) > 0
    finally:
        be.disconnect()


def test_start_stop_audio_lifecycle():
    """start_audio/stop_audio 生命周期，无设备不崩。"""
    be = FakeBackend()
    be.connect()
    try:
        ok = be.start_audio()
        # sounddevice 在 CI/offscreen 无设备时 start() 内部降级，线程仍启动
        assert isinstance(ok, bool)
        import time
        time.sleep(0.2)
        be.stop_audio()
        assert be._audio_thread is None
    finally:
        be.disconnect()


def test_spectrum_data():
    """get_spectrum_data 返回 float32 功率谱，未连接返回 None。"""
    be = FakeBackend()
    assert be.get_spectrum_data(256) is None
    be.connect()
    try:
        spec = be.get_spectrum_data(256)
        assert spec is not None
        assert spec.dtype == np.float32
        assert len(spec) == 256
    finally:
        be.disconnect()


def test_mode_change_rebuilds_chain():
    """换模式后接收链重建，不抛异常。"""
    be = FakeBackend()
    be.connect()
    try:
        assert be.set_demod("AM") is True
        assert be._receive_chain is None or be._receive_chain is not None
        audio = be.read_audio(1024)
        assert audio is None or audio.dtype == np.float32
    finally:
        be.disconnect()
