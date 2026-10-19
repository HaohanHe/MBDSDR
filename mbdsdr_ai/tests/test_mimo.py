# SPDX-License-Identifier: MIT
"""
mimo.py 多设备同步控制器——确定性离线测试

不联网、无硬件（fake handle 注入）：
  - MIMODevice.connected：handle=None 即未连接（诚实空态，不假装同步）；
  - add/remove/list 设备管理；
  - all_synced：空集合 vacuously True；有未连接设备即 False；
  - read_stream：未同步返回空 dict（不造假数据）；同步后按 device 读取并
    叠加 time_offset_s 校准；
  - measure_phase_coherence 互相关口径（实测约定，测试钉死）：
      * b 落后 a d 个样本 → time_delay_s = -d/fs；
      * b = a·exp(jθ) → phase_diff_deg = -θ（共轭口径 a·conj(b)）；
      * 同信号 → 时差 0、相位 0、相关峰 ≈1；
      * 不等长输入按 min 长度对齐，不崩。

运行：python3 -m pytest mbdsdr_ai/tests/test_mimo.py -v
"""
from __future__ import annotations

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai.mimo import MIMOController, MIMODevice  # noqa: E402


class FakeHandle:
    """假硬件句柄：read_stream 回放预置 IQ。"""

    def __init__(self, iq, ts):
        self._iq = np.asarray(iq)
        self._ts = ts
        self.calls = 0

    def read_stream(self, duration_s):
        self.calls += 1
        return self._iq, self._ts


def _iq(n, seed):
    rng = np.random.default_rng(seed)
    return rng.standard_normal(n) + 1j * rng.standard_normal(n)


# ---------------------------------------------------------------- 设备管理

def test_device_connected_requires_handle():
    d_off = MIMODevice(device_id="A", sample_rate=1e6, center_hz=100e6)
    d_on = MIMODevice(device_id="B", sample_rate=1e6, center_hz=100e6,
                      handle=object())
    assert d_off.connected is False
    assert d_on.connected is True


def test_add_remove_list_devices():
    c = MIMOController()
    a = MIMODevice(device_id="A", sample_rate=1e6, center_hz=100e6)
    b = MIMODevice(device_id="B", sample_rate=1e6, center_hz=100e6)
    c.add_device(a)
    c.add_device(b)
    assert {d.device_id for d in c.devices} == {"A", "B"}
    c.remove_device("A")
    assert [d.device_id for d in c.devices] == ["B"]
    c.remove_device("nonexistent")                     # 幂等删除不炸
    assert [d.device_id for d in c.devices] == ["B"]


def test_all_synced_empty_is_vacuously_true():
    assert MIMOController().all_synced() is True       # 无设备谈不上未同步


def test_all_synced_false_with_unconnected():
    c = MIMOController()
    c.add_device(MIMODevice(device_id="A", sample_rate=1e6, center_hz=100e6,
                            handle=object()))
    c.add_device(MIMODevice(device_id="B", sample_rate=1e6, center_hz=100e6))
    assert c.all_synced() is False                     # B 未连接


# ---------------------------------------------------------------- 读取流

def test_read_stream_unsynced_returns_empty_no_fake_data():
    c = MIMOController()
    c.add_device(MIMODevice(device_id="A", sample_rate=1e6, center_hz=100e6))
    assert c.read_stream(0.1) == {}                    # 不合成假 IQ


def test_read_stream_applies_time_offset_calibration():
    c = MIMOController()
    iq = _iq(64, 1)
    c.add_device(MIMODevice(device_id="A", sample_rate=1e6, center_hz=100e6,
                            handle=FakeHandle(iq, ts=100.0),
                            time_offset_s=0.5))
    out = c.read_stream(0.1)
    assert set(out) == {"A"}
    got_iq, got_ts = out["A"]
    assert np.array_equal(got_iq, iq)
    assert got_ts == 100.5                             # ts + time_offset_s


def test_read_stream_skips_handle_without_read_stream():
    """句柄存在但没有 read_stream 能力的设备被跳过（不假装读到）。"""
    c = MIMOController()

    class DumbHandle:
        pass

    c.add_device(MIMODevice(device_id="A", sample_rate=1e6, center_hz=100e6,
                            handle=DumbHandle()))
    assert c.read_stream(0.1) == {}


# ---------------------------------------------------------------- 相位相干

def test_coherence_identical_signals_zero_delay_zero_phase():
    a = _iq(512, 2)
    r = MIMOController().measure_phase_coherence(a, a.copy(), 1000.0)
    assert r.time_delay_s == 0.0
    assert abs(r.phase_diff_deg) < 1e-6
    assert r.correlation_peak > 0.99


def test_coherence_delay_sign_convention():
    """b 落后 a d 个样本 → time_delay_s = -d/fs（实测口径，钉死防回归）。"""
    n, d, fs = 512, 7, 1000.0
    a = _iq(n, 3)
    b = np.zeros(n, dtype=complex)
    b[d:] = a[:n - d]
    r = MIMOController().measure_phase_coherence(a, b, fs)
    assert abs(r.time_delay_s * fs + d) < 1e-9
    assert abs(r.phase_diff_deg) < 1e-6


def test_coherence_phase_conjugate_convention():
    """b = a·exp(jθ) → phase_diff_deg = -θ（共轭口径，钉死防回归）。"""
    a = _iq(512, 4)
    c = a * np.exp(1j * np.deg2rad(30.0))
    r = MIMOController().measure_phase_coherence(a, c, 1000.0)
    assert abs(r.phase_diff_deg + 30.0) < 1e-6
    assert abs(r.time_delay_s) < 1e-9


def test_coherence_unequal_lengths_uses_min():
    a = _iq(512, 5)
    b = a.copy()
    r = MIMOController().measure_phase_coherence(a[:100], b[:300], 1000.0)
    assert r.correlation_peak > 0.99                   # 前缀相同 → 峰≈1
    assert r.time_delay_s == 0.0


def test_coherence_uncorrelated_low_peak():
    a = _iq(512, 6)
    b = _iq(512, 7)
    r = MIMOController().measure_phase_coherence(a, b, 1000.0)
    assert r.correlation_peak < 0.2                    # 独立噪声 → 低相关
