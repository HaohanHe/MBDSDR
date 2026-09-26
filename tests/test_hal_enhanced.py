"""确定性单测：设备健康检查 / 全零检测 / NaN 检测 / 系统信息。"""
import numpy as np
import pytest

from mbdsdr_ai.hal_enhanced import DeviceHealthChecker, PROBE_SAMPLES


class FakeBackend:
    def __init__(self, data, sr=2_048_000.0, raises=None):
        self._data = data
        self._sample_rate = sr
        self._raises = raises

    def read_rx(self, n):
        if self._raises is not None:
            raise self._raises
        return self._data[:n]


class FakePlayer:
    def __init__(self, playing=True, queued=1000, cap=48000):
        self.is_playing = playing
        self._queued_samples = queued
        self._MAX_QUEUED_SAMPLES = cap
        self.sample_rate = 48000


# ----------------------------------------------------------------------
# 设备健康
# ----------------------------------------------------------------------
def test_alive_with_real_signal():
    data = (np.random.default_rng(0).standard_normal(PROBE_SAMPLES)
            + 1j * np.random.default_rng(1).standard_normal(PROBE_SAMPLES))
    data = data.astype(np.complex64)
    chk = DeviceHealthChecker(backend=FakeBackend(data))
    res = chk.check_device()
    assert res["alive"] is True
    assert res["has_signal"] is True
    assert res["nan_detected"] is False
    assert res["error"] == ""


def test_all_zero_data_detected_as_no_signal():
    data = np.zeros(PROBE_SAMPLES, dtype=np.complex64)
    chk = DeviceHealthChecker(backend=FakeBackend(data))
    res = chk.check_device()
    assert res["alive"] is True          # 设备还能返回数据
    assert res["has_signal"] is False    # 但全零 -> 疑似天线断开


def test_nan_detected():
    data = np.full(PROBE_SAMPLES, np.nan + 0j, dtype=np.complex64)
    chk = DeviceHealthChecker(backend=FakeBackend(data))
    res = chk.check_device()
    assert res["nan_detected"] is True


def test_read_exception_marked_not_alive():
    chk = DeviceHealthChecker(
        backend=FakeBackend(None, raises=OSError("no device"))
    )
    res = chk.check_device()
    assert res["alive"] is False
    assert "read_rx" in res["error"]


def test_no_backend_error():
    res = DeviceHealthChecker(backend=None).check_device()
    assert res["alive"] is False
    assert res["error"]


def test_sample_rate_correctness():
    good = DeviceHealthChecker(
        backend=FakeBackend(np.ones(64, dtype=np.complex64), sr=2_048_000.0),
        expected_sample_rate=2_048_000.0,
    ).check_device()
    assert good["sample_rate_correct"] is True

    bad = DeviceHealthChecker(
        backend=FakeBackend(np.ones(64, dtype=np.complex64), sr=1_000_000.0),
        expected_sample_rate=2_048_000.0,
    ).check_device()
    assert bad["sample_rate_correct"] is False


# ----------------------------------------------------------------------
# 音频健康
# ----------------------------------------------------------------------
def test_check_audio_running():
    chk = DeviceHealthChecker(audio_player=FakePlayer(playing=True, queued=500))
    res = chk.check_audio()
    assert res["running"] is True
    assert res["backlog"] is False
    assert res["queue_depth_samples"] == 500


def test_check_audio_backlog():
    # 队列占用 >90% -> backlog
    chk = DeviceHealthChecker(audio_player=FakePlayer(playing=True, queued=47000, cap=48000))
    res = chk.check_audio()
    assert res["backlog"] is True


def test_check_audio_no_player():
    res = DeviceHealthChecker(audio_player=None).check_audio()
    assert res["running"] is False
    assert res["error"]


# ----------------------------------------------------------------------
# 系统信息
# ----------------------------------------------------------------------
def test_get_system_info_keys_present():
    chk = DeviceHealthChecker(audio_player=FakePlayer(queued=4800))
    info = chk.get_system_info()
    # 键一定齐全；psutil 缺失时数值为 None（不造假、不崩）
    assert set(info.keys()) >= {"cpu_percent", "memory_percent", "audio_latency_ms"}
    # 4800 samples @48k = 100ms
    assert info["audio_latency_ms"] == pytest.approx(100.0, abs=1e-6)


def test_get_system_info_no_player_no_crash():
    info = DeviceHealthChecker().get_system_info()
    assert info["audio_latency_ms"] is None
