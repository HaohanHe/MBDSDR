# SPDX-License-Identifier: MIT
"""``DeviceManager`` → ``OpenDevice`` 栈的调谐器越界预检 + 故障态保护。

背景（2026-11-20 真机复现，见 docs/pending-patches/2026-11-06-limitation-adsb-1090-fc0012.md）：
``sdr_backend.RTLSDRBackend`` 栈早在 2026-11-06 就加了越界预检，但 controller /
CLI / MCP 走的 ``OpenDevice`` 没有。本机 FC0012 上：:

    ctrl.set_frequency(1090_000_000)  # LIBUSB_ERROR_IO，句柄写坏
    ctrl.set_gain(30)                 # access violation reading 0x8
    ctrl._read_raw_iq(8192)           # access violation reading 0x8

本文件钉住：越界请求绝不触达硬件；范围内容错失败后句柄判死，后续任何访问
不再碰原生层，且错误文案告诉用户「重新连接」。
"""

from __future__ import annotations

import pytest

from mbdsdr_ai import rtlsdr_params
from mbdsdr_ai.core.controller import SDRController
from mbdsdr_ai.core.errors import FrequencyOutOfRangeError
from mbdsdr_ai.device_manager import (
    DeviceDisconnectedError,
    DeviceInfo,
    OpenDevice,
)
from mbdsdr_ai.sdr_backend import rtlsdr_native_entry


def _info(tuner: str = "FC0012") -> DeviceInfo:
    return DeviceInfo(
        name=f"RTL-SDR #0 ({tuner})",
        driver="rtlsdr",
        serial="77771111153705700",
        identity={"driver": "rtlsdr", "product": tuner},
    )


class _NoTouchBackend:
    """任何写频率都视作「触达了硬件」并立刻失败。"""

    def __init__(self):
        self.freq_writes: list[int] = []
        self.read_calls = 0

    @property
    def center_freq(self) -> int:
        return 100_000_000

    @center_freq.setter
    def center_freq(self, value: int) -> None:
        self.freq_writes.append(int(value))
        raise AssertionError("不允许触达硬件")

    def read_bytes(self, n: int) -> bytes:
        self.read_calls += 1
        return b"\x80" * n

    def close(self) -> None:
        pass


class _RecordingBackend:
    """记录写入值的最小替身。"""

    def __init__(self):
        self._center = 100_000_000
        self.freq_writes: list[int] = []

    @property
    def center_freq(self) -> int:
        return self._center

    @center_freq.setter
    def center_freq(self, value: int) -> None:
        self._center = int(value)
        self.freq_writes.append(int(value))

    def read_bytes(self, n: int) -> bytes:
        return b"\x80" * n

    def close(self) -> None:
        pass


class _OsErrorBackend:
    """写频率抛原生异常；读样本若被调用会计数（用于证明不再触达）。"""

    def __init__(self):
        self.read_calls = 0

    @property
    def center_freq(self) -> int:
        return 100_000_000

    @center_freq.setter
    def center_freq(self, value: int) -> None:
        raise OSError(
            'LIBUSB_ERROR_IO (-1): Input/output error '
            '"Could not set center_freq to %d Hz"' % int(value))

    def read_bytes(self, n: int) -> bytes:
        self.read_calls += 1
        return b"\x80" * n

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# OpenDevice：越界预检
# ---------------------------------------------------------------------------
def test_fc0012_1090_mhz_rejected_without_touching_hardware():
    backend = _NoTouchBackend()
    dev = OpenDevice(_info("FC0012"), backend)
    assert dev.tuner_name == "FC0012"
    assert dev.tuning_range_hz == (22_000_000.0, 948_500_000.0)

    with pytest.raises(FrequencyOutOfRangeError) as ei:
        dev.set_frequency(1090e6)

    assert backend.freq_writes == [], "越界请求不得触达硬件（写频失败会写坏句柄）"
    err = ei.value
    assert "FC0012" in err.message and "1090.000" in err.message
    assert err.min_freq == 22_000_000.0
    assert err.max_freq == 948_500_000.0
    assert err.technical_detail is None
    dev.close()


def test_fc0012_boundary_allows_948_5_and_rejects_948_6():
    backend = _RecordingBackend()
    dev = OpenDevice(_info("FC0012"), backend)

    dev.set_frequency(948_500_000)
    assert backend.freq_writes == [948_500_000]

    with pytest.raises(FrequencyOutOfRangeError):
        dev.set_frequency(948_600_000)
    assert backend.freq_writes == [948_500_000], "948.6 MHz 越界不得下发"
    dev.close()


# ---------------------------------------------------------------------------
# OpenDevice：故障态保护
# ---------------------------------------------------------------------------
def test_hardware_failure_marks_closed_and_blocks_later_reads():
    backend = _OsErrorBackend()
    dev = OpenDevice(_info("Unknown"), backend)
    assert dev.tuner_name == "Unknown"
    assert dev.tuning_range_hz == (0.0, 0.0), "型号未知时不预检"

    with pytest.raises(DeviceDisconnectedError) as ei:
        dev.set_frequency(100e6)
    assert "重新连接" in str(ei.value)
    assert dev._closed is True

    with pytest.raises(DeviceDisconnectedError) as ei2:
        dev.read_samples(1024)
    assert "重新连接" in str(ei2.value)
    assert backend.read_calls == 0, "句柄判死后不得再触达原生读"
    dev.close()


# ---------------------------------------------------------------------------
# 统一枚举条目：freq_range 按真实调谐器收窄
# ---------------------------------------------------------------------------
def test_native_entry_freq_range_follows_tuner():
    fc = rtlsdr_native_entry({"index": 0, "serial": "SN", "tuner": "FC0012"})
    assert fc["freq_range"] == (22_000_000.0, 948_500_000.0)
    assert fc["capability_status"]["freq_range"] == "declared"
    assert "FC0012" in fc["capability_note"]["freq_range"]

    r820 = rtlsdr_native_entry({"index": 0, "serial": "SN", "tuner": "R820T"})
    assert r820["freq_range"] == (24_000_000.0, 1_766_000_000.0)
    assert r820["capability_status"]["freq_range"] == "declared"

    unknown = rtlsdr_native_entry({"index": 0, "serial": "SN", "tuner": "Unknown"})
    assert unknown["freq_range"] == (0.0, 0.0)
    assert unknown["capability_status"]["freq_range"] == "unknown"
    assert unknown["capability_note"]["freq_range"]


# ---------------------------------------------------------------------------
# 枚举映射回归：2 == FC0012（此前被写成 FC0013 / 不存在的 FC0025）
# ---------------------------------------------------------------------------
def test_tuner_enum_mapping_matches_official_header():
    assert rtlsdr_params.TUNER_ENUM_TO_NAME[2] == "FC0012"
    assert rtlsdr_params.TUNER_ENUM_TO_NAME[3] == "FC0013"


# ---------------------------------------------------------------------------
# controller：setter 异常原因不能丢
# ---------------------------------------------------------------------------
class _RaisingRadio:
    def __init__(self):
        self.status = type("_S", (), {"error": ""})()

    def set_frequency(self, hz: float) -> bool:
        raise OSError("boom")

    def get_frequency(self) -> float:
        return 100e6


def test_controller_keeps_setter_exception_reason():
    ctrl = SDRController(backend=_RaisingRadio())
    ctrl._connected = True

    assert ctrl.set_frequency(100e6) is False
    text = ctrl.setter_failure_text("调谐")
    assert "boom" in text
    assert "OSError" in text
