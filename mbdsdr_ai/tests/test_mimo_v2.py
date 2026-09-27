"""mimo_v2.py 确定性测试：多设备管理 / 同步调谐 / 相位相干测量。

红线验证：无设备时返回空、不造假 IQ。
"""
from __future__ import annotations

import numpy as np
import pytest

from mbdsdr_ai.mimo_v2 import MultiDeviceManager, PhaseCoherence


class FakeBackend:
    """鸭型 SDRBackend：记录调用、返回确定性 IQ。"""

    def __init__(self, idx: int, hw_time: bool = False):
        self.idx = idx
        self.freq = 0.0
        self.sr = 0.0
        self.gain = 0.0
        self.started = False
        self._hw = hw_time
        self.t0 = 1000.0

    def set_frequency(self, hz):
        self.freq = float(hz)

    def set_sample_rate(self, sr):
        self.sr = float(sr)

    def set_gain(self, db):
        self.gain = float(db)

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def get_hw_time(self):
        return self.t0 + self.idx * 1e-6

    def read_samples(self, n):
        t = np.arange(n)
        return np.exp(1j * 2 * np.pi * 0.05 * t).astype(np.complex128)


class BrokenBackend(FakeBackend):
    def read_samples(self, n):
        raise OSError("device unplugged")


# --------------------------------------------------------------------------- #
# MultiDeviceManager
# --------------------------------------------------------------------------- #
def test_empty_manager_no_fake_data():
    m = MultiDeviceManager()
    assert m.list_devices() == []
    assert len(m) == 0
    assert m.set_frequency_all(100e6) == []
    assert m.set_sample_rate_all(2e6) == []
    assert m.start_all() == []
    assert m.stop_all() == []
    # 红线：无设备读同步 -> []，不合成假 IQ
    assert m.read_synchronized(1024) == []


def test_add_remove_list():
    m = MultiDeviceManager()
    i0 = m.add_device(FakeBackend(0))
    i1 = m.add_device(FakeBackend(1))
    assert (i0, i1) == (0, 1)
    devs = m.list_devices()
    assert [d["index"] for d in devs] == [0, 1]
    assert m.remove_device(0) is True
    assert [d["index"] for d in m.list_devices()] == [1]
    assert m.remove_device(99) is False  # 越界不炸


def test_synchronized_tuning():
    m = MultiDeviceManager()
    b0, b1 = FakeBackend(0), FakeBackend(1)
    m.add_device(b0)
    m.add_device(b1)
    ok = m.set_frequency_all(98_000_000)
    assert ok == [0, 1]
    assert b0.freq == b1.freq == 98_000_000
    ok = m.set_sample_rate_all(2_400_000)
    assert ok == [0, 1]
    assert b0.sr == b1.sr == 2_400_000


def test_start_stop_all():
    m = MultiDeviceManager()
    b0, b1 = FakeBackend(0), FakeBackend(1)
    m.add_device(b0)
    m.add_device(b1)
    assert m.start_all() == [0, 1]
    assert b0.started and b1.started
    assert m.stop_all() == [0, 1]
    assert not b0.started and not b1.started


def test_read_synchronized_returns_iq_and_timestamp():
    m = MultiDeviceManager()
    b0 = FakeBackend(0, hw_time=True)
    b1 = FakeBackend(1, hw_time=True)
    m.add_device(b0)
    m.add_device(b1)
    out = m.read_synchronized(512)
    assert len(out) == 2
    iq0, ts0 = out[0]
    iq1, ts1 = out[1]
    assert iq0.shape == (512,)
    assert np.iscomplexobj(iq0)
    # 硬件时间戳：两设备应有微小差（1us）
    assert ts1 - ts0 == pytest.approx(1e-6, abs=1e-9)


def test_read_survives_broken_device():
    m = MultiDeviceManager()
    m.add_device(BrokenBackend(0))   # 读就抛
    good = FakeBackend(1)
    m.add_device(good)
    out = m.read_synchronized(256)
    # 坏设备被摘除，好设备仍返回
    assert len(out) == 1
    iq, _ = out[0]
    assert iq.shape == (256,)


# --------------------------------------------------------------------------- #
# PhaseCoherence
# --------------------------------------------------------------------------- #
def test_phase_diff_known_rotation():
    """iq2 = iq1 * exp(j*0.7) -> 测得相位差 ≈ 0.7 rad。"""
    t = np.arange(4096)
    ref = np.exp(1j * 2 * np.pi * 0.03 * t)
    phi0 = 0.7
    other = ref * np.exp(1j * phi0)
    pc = PhaseCoherence()
    meas = pc.measure_phase_diff(ref, other)
    assert meas == pytest.approx(phi0, abs=0.02)


def test_phase_diff_with_time_delay():
    """带整数采样延迟 + 固定相位：互相关对齐整数 lag 后，剩余相位自洽。

    对单音 exp(j*2πf0 t)，延迟 d 样点等价于载波附加相移 2πf0 d；
    所以测得相位 = φ0 - 2π f0 d（这正是 MIMO 里要的剩余载波相位）。
    """
    f0 = 0.04
    d = 7
    t = np.arange(4096)
    ref = np.exp(1j * 2 * np.pi * f0 * t)
    phi0 = -1.3
    other = np.roll(ref * np.exp(1j * phi0), d)  # 延迟 d 样点
    pc = PhaseCoherence()
    meas = pc.measure_phase_diff(ref, other)
    expected = phi0 - 2 * np.pi * f0 * d
    # wrap 到 [-pi, pi]
    expected = (expected + np.pi) % (2 * np.pi) - np.pi
    assert meas == pytest.approx(expected, abs=0.05)


def test_phase_calibrate_no_signal_no_fake():
    pc = PhaseCoherence()
    # 红线：不给信号 -> {}，不造假
    assert pc.phase_calibrate() == {}
    assert pc.calibration == {}


def test_phase_calibrate_and_apply():
    t = np.arange(2048)
    ref = np.exp(1j * 2 * np.pi * 0.06 * t)
    phi0 = 0.9
    other = ref * np.exp(1j * phi0)
    pc = PhaseCoherence()
    got = pc.phase_calibrate(ref, other, pair_id=(0, 1))
    assert (0, 1) in got
    assert got[(0, 1)] == pytest.approx(phi0, abs=0.02)
    # apply 后 other 旋转掉偏移 -> 与 ref 同相
    aligned = pc.apply_calibration(other, pair_id=(0, 1))
    resid = np.angle(np.mean(aligned * np.conj(ref)))
    assert resid == pytest.approx(0.0, abs=0.02)
