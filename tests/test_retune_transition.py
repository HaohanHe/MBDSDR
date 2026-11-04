# SPDX-License-Identifier: MIT
"""采样率/带宽切换瞬态：旧缓冲丢弃、新流对齐、状态如实（无硬件）。"""

from __future__ import annotations

from typing import List, Optional

import numpy as np

from mbdsdr_ai.sdr_backend import SDRBackend, SDRDevice


class _FakeRing:
    """最小环形缓冲替身：只暴露可用量/清空/取数。"""

    def __init__(self) -> None:
        self.old_values: List[float] = []
        self.new_values: List[float] = []

    def available(self) -> int:
        return len(self.old_values)

    def clear(self) -> None:
        self.old_values.clear()

    def take(self, n: int) -> np.ndarray:
        src = self.new_values if not self.old_values else self.old_values
        out = np.asarray(src[:n], dtype=np.complex64)
        del src[:n]
        return out


class _RetuneBackend(SDRBackend):
    def __init__(self) -> None:
        dev = SDRDevice(
            device_type="fake", device_id="fake-retune", name="FakeRetune",
            frequency_range=(1e6, 2e9), sample_rate_range=(1e5, 4e6),
            max_gain=40.0)
        super().__init__(dev)
        self.status.connected = True
        self.status.sample_rate_hz = 2_048_000.0
        self.status.bandwidth_hz = 2_000_000.0
        self._ring_reader = _FakeRing()
        self.applied_rates: List[float] = []
        self.applied_bws: List[float] = []
        self.fail_rate = False
        self.fail_bw = False
        self._stream_next = 100  # 切换后新流的真实样点从 100 继续

    def _apply_sample_rate(self, rate_hz: float) -> bool:
        self.applied_rates.append(float(rate_hz))
        return not self.fail_rate

    def _apply_bandwidth(self, bw_hz: float) -> bool:
        self.applied_bws.append(float(bw_hz))
        return not self.fail_bw

    def read_samples(self, n: int) -> Optional[np.ndarray]:
        return self._ring_reader.take(n)


def _seed_old_stream(be: _RetuneBackend, start: int, stop: int) -> None:
    be._ring_reader.old_values = [float(i) for i in range(start, stop)]


def test_sample_rate_switch_drops_old_buffer_and_advances_generation():
    be = _RetuneBackend()
    _seed_old_stream(be, 0, 100)
    assert be.read_samples(50)[0] == 0

    assert be.set_sample_rate(1_024_000) is True
    assert be.status.sample_rate_hz == 1_024_000
    assert be.status.retune_state == "completed"
    assert be.status.retune_kind == "sample_rate"
    assert be.status.retune_generation == 1
    assert be.status.retune_dropped_samples == 50  # 未消费的旧流样点
    assert be.status.retune_error == ""
    assert be.status.retune_elapsed_ms >= 0.0

    # 旧流剩余样点已被丢弃；新流从连续序号 100 开始，无重复无空洞。
    be._ring_reader.new_values = [float(i) for i in range(100, 200)]
    got = be.read_samples(50)
    assert got[0] == 100
    assert np.array_equal(np.asarray(got), np.arange(100, 150))


def test_failed_sample_rate_switch_keeps_old_rate_and_old_buffer():
    be = _RetuneBackend()
    _seed_old_stream(be, 0, 40)
    be.fail_rate = True
    before_gen = be.status.retune_generation
    assert be.set_sample_rate(1_024_000) is False

    assert be.status.retune_state == "failed"
    assert be.status.retune_kind == "sample_rate"
    assert be.status.retune_error
    assert be.status.sample_rate_hz == 2_048_000.0
    assert be.status.retune_generation == before_gen
    assert be.status.retune_dropped_samples == 0
    # 失败不能清掉仍可用的旧流（不制造空档）
    assert be.read_samples(10)[0] == 0


def test_bandwidth_switch_exposes_state_and_drops_old_buffer():
    be = _RetuneBackend()
    _seed_old_stream(be, 0, 20)
    assert be.set_bandwidth(1_000_000) is True
    assert be.status.bandwidth_hz == 1_000_000.0
    assert be.status.retune_state == "completed"
    assert be.status.retune_kind == "bandwidth"
    assert be.status.retune_generation == 1
    assert be.status.retune_dropped_samples == 20
    assert be._ring_reader.old_values == []

    be.fail_bw = True
    assert be.set_bandwidth(500_000) is False
    assert be.status.retune_state == "failed"
    assert be.status.bandwidth_hz == 1_000_000.0
    assert be.status.retune_generation == 1


def test_concurrent_switch_is_rejected_without_crossing_streams():
    be = _RetuneBackend()
    assert be._retune_lock.acquire(blocking=False)
    try:
        assert be.set_sample_rate(1_024_000) is False
        assert be.status.retune_state == "failed"
        assert "尚未完成" in be.status.retune_error
        assert be.status.sample_rate_hz == 2_048_000.0
    finally:
        be._retune_lock.release()

    assert be.set_sample_rate(1_024_000) is True
    assert be.status.retune_state == "completed"
    assert be.status.retune_generation == 1
    # 被拒绝的那次没有触达硬件，只有真正切换的一次下发
    assert be.applied_rates == [1_024_000.0]


def test_read_after_switch_has_no_duplicate_samples():
    be = _RetuneBackend()
    _seed_old_stream(be, 0, 60)
    first = be.read_samples(30)
    be._ring_reader.new_values = [float(i) for i in range(60, 120)]
    assert be.set_sample_rate(1_024_000) is True
    # 被丢弃的 30..59 已计入 retune_dropped_samples；新流只能从 60 起，
    # 旧序号绝不会再次出现（无重复），缺口有账可查（不是静默空档）。
    assert be.status.retune_dropped_samples == 30
    second = be.read_samples(30)
    fields = np.concatenate([np.asarray(first), np.asarray(second)])
    assert len(np.unique(fields)) == len(fields) == 60
    assert second[0] == 60 and first[-1] == 29
    assert np.all(fields.real[30:] >= 60)
