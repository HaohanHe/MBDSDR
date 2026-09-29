# SPDX-License-Identifier: MIT
"""确定性集成测试：StartupSequence 8 步启动顺序 / 失败分类 / validate()。

全部用注入的 mock 协作者，不碰真硬件、不依赖 sounddevice / pyrtlsdr。
"""
import sys
from types import SimpleNamespace

import pytest

from mbdsdr_ai import startup_sequence as ss_mod
from mbdsdr_ai.error_handler import (
    DeviceBusyError,
    DeviceNotFoundError,
    DriverNotFoundError,
    FrequencyOutOfRangeError,
    NoAudioOutputError,
    SampleRateNotSupportedError,
    USBDisconnectedError,
    format_error,
)
from mbdsdr_ai.startup_sequence import (
    STEP_ORDER,
    STEP_SETUP_DRIVERS,
    STEP_ENUMERATE,
    STEP_SELECT,
    STEP_CONNECT,
    STEP_CONFIGURE,
    STEP_STREAM,
    STEP_AUDIO,
    STEP_CHAIN,
    StartupSequence,
)


# ----------------------------------------------------------------------
# Mock 协作者
# ----------------------------------------------------------------------
class FakeDevice:
    def __init__(self, name="RTL-SDR #0", sample_rates=(2_048_000.0, 1_024_000.0),
                 frequency_range=(24e6, 1.766e9)):
        self.name = name
        self.driver = "rtlsdr"
        self.serial = "fake-0001"
        self.sample_rates = sample_rates
        self.frequency_range = frequency_range


class FakeStatus:
    def __init__(self):
        self.connected = False
        self.error = ""


class FakeBackend:
    """鸭子类型 SDR 后端：connect / set_* / read_samples / disconnect。"""

    def __init__(self, connect_ok=True, connect_error="", read_raises=False):
        self.connect_ok = connect_ok
        self.connect_error = connect_error
        self.read_raises = read_raises
        self.status = FakeStatus()
        self.calls = []
        self.cfg = {}

    def connect(self):
        self.calls.append("connect")
        self.status.error = self.connect_error
        self.status.connected = self.connect_ok
        return self.connect_ok

    def set_frequency(self, f):
        self.cfg["freq"] = f
        return True

    def set_sample_rate(self, s):
        self.cfg["sr"] = s
        return True

    def set_gain(self, g):
        self.cfg["gain"] = g
        return True

    def read_samples(self, n):
        if self.read_raises:
            raise OSError("LIBUSB_ERROR_NO_DEVICE: device removed")
        import numpy as np
        return np.zeros(n, dtype=np.complex64)

    def disconnect(self):
        self.calls.append("disconnect")
        self.status.connected = False


class FakePlayer:
    def __init__(self, start_ok=True):
        self.start_ok = start_ok
        self.stopped = False

    def start(self):
        return self.start_ok

    def stop(self):
        self.stopped = True


class FakeChain:
    def __init__(self, fs_in, mode):
        self.fs_in = fs_in
        self.mode = mode


def _make_seq(backend=None, devices=None, audio_outputs=None, **kw):
    backend = backend if backend is not None else FakeBackend()
    devices = devices if devices is not None else [FakeDevice()]
    audio_outputs = audio_outputs if audio_outputs is not None else ["[0] 扬声器"]
    seq = StartupSequence(
        setup_drivers_fn=lambda: {"ok": True, "message": "driver ok"},
        enumerate_fn=lambda: devices,
        backend_factory=lambda idx, info: backend,
        audio_player_factory=lambda sr: FakePlayer(),
        chain_factory=lambda fs, mode: FakeChain(fs, mode),
        list_audio_outputs_fn=lambda: audio_outputs,
        **kw,
    )
    return seq, backend


# ----------------------------------------------------------------------
# 1. 快乐路径：8 步按顺序完成
# ----------------------------------------------------------------------
def test_happy_path_all_steps_in_order():
    seq, be = _make_seq()
    res = seq.run_all(freq_hz=98e6, sample_rate_hz=2_048_000.0, gain_db=20.0)
    assert res["ok"] is True
    assert res["failed_step"] is None

    # 每一步都 done
    for name in STEP_ORDER:
        assert seq.steps[name].status == "done", f"{name} 未完成"

    # 调用顺序：setup -> enumerate -> select -> connect -> configure -> stream -> audio -> chain
    assert be.calls == ["connect"]
    assert be.cfg == {"freq": 98e6, "sr": 2_048_000.0, "gain": 20.0}
    assert seq.player is not None
    assert seq.chain is not None
    assert seq.chain.fs_in == 2_048_000.0
    assert seq.chain.mode == "FM"


def test_step_order_constant_matches_doc():
    assert STEP_ORDER == [
        STEP_SETUP_DRIVERS, STEP_ENUMERATE, STEP_SELECT, STEP_CONNECT,
        STEP_CONFIGURE, STEP_STREAM, STEP_AUDIO, STEP_CHAIN,
    ]


# ----------------------------------------------------------------------
# 2. 各失败点 -> 正确的 SDRUserError
# ----------------------------------------------------------------------
def test_setup_drivers_fail_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    seq = StartupSequence(
        setup_drivers_fn=lambda: {"ok": False, "message": "未找到 rtlsdr.dll，请安装 SDR# 或 Zadig 驱动"},
        enumerate_fn=lambda: [], backend_factory=lambda i, d: FakeBackend(),
        audio_player_factory=lambda sr: FakePlayer(),
        chain_factory=lambda fs, m: FakeChain(fs, m),
        list_audio_outputs_fn=lambda: ["[0] x"],
    )
    with pytest.raises(DriverNotFoundError) as ei:
        seq.setup_drivers()
    assert "rtlsdr.dll" in ei.value.message
    assert "SDR#" in ei.value.suggestion
    assert "airspy.com" in ei.value.suggestion


def test_linux_skips_driver_step(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    called = {"n": 0}
    seq = StartupSequence(
        setup_drivers_fn=lambda: called.__setitem__("n", called["n"] + 1) or {"ok": True},
        enumerate_fn=lambda: [], backend_factory=lambda i, d: FakeBackend(),
        audio_player_factory=lambda sr: FakePlayer(),
        chain_factory=lambda fs, m: FakeChain(fs, m),
        list_audio_outputs_fn=lambda: ["[0] x"],
    )
    seq.setup_drivers()
    assert seq.steps[STEP_SETUP_DRIVERS].status == "done"
    assert called["n"] == 0  # Linux 根本不调 windows_setup


def test_no_devices_raises_device_not_found():
    seq, _ = _make_seq(devices=[])
    res = seq.run_all(98e6, 2_048_000.0, 20.0)
    assert res["ok"] is False
    assert res["failed_step"] == STEP_ENUMERATE
    assert isinstance(res["error"], DeviceNotFoundError)
    assert "USB" in res["error"].suggestion and "Zadig" in res["error"].suggestion


def test_select_out_of_range():
    seq, _ = _make_seq(devices=[FakeDevice("A"), FakeDevice("B")])
    res = seq.run_all(98e6, 2_048_000.0, 20.0, device_index=7)
    assert res["failed_step"] == STEP_SELECT
    assert isinstance(res["error"], DeviceNotFoundError)


def test_connect_busy_classified():
    be = FakeBackend(connect_ok=False,
                     connect_error="RTL-SDR 连接失败: OSError: libusb: busy 0")
    seq, _ = _make_seq(backend=be)
    res = seq.run_all(98e6, 2_048_000.0, 20.0)
    assert res["failed_step"] == STEP_CONNECT
    assert isinstance(res["error"], DeviceBusyError)
    assert "SDR#" in res["error"].suggestion and "GQRX" in res["error"].suggestion


def test_connect_driver_error_classified():
    be = FakeBackend(connect_ok=False,
                     connect_error="No module named 'rtlsdr'")
    seq, _ = _make_seq(backend=be)
    res = seq.run_all(98e6, 2_048_000.0, 20.0)
    assert isinstance(res["error"], DriverNotFoundError)


def test_unsupported_sample_rate():
    dev = FakeDevice(sample_rates=(2_048_000.0,))  # 只支持 2.048M
    seq, be = _make_seq(devices=[dev])
    res = seq.run_all(98e6, 5_000_000.0, 20.0)  # 5M 不支持
    assert res["failed_step"] == STEP_CONFIGURE
    assert isinstance(res["error"], SampleRateNotSupportedError)
    assert "2048000" in res["error"].suggestion


def test_frequency_out_of_range():
    dev = FakeDevice(frequency_range=(24e6, 1.766e9))
    seq, _ = _make_seq(devices=[dev])
    res = seq.run_all(9_000e6, 2_048_000.0, 20.0)  # 9 GHz 超范围
    assert res["failed_step"] == STEP_CONFIGURE
    assert isinstance(res["error"], FrequencyOutOfRangeError)
    assert "24.000" in res["error"].suggestion and "1766.000" in res["error"].suggestion


def test_stream_read_raises_is_usb_disconnected():
    be = FakeBackend(read_raises=True)
    seq, _ = _make_seq(backend=be)
    res = seq.run_all(98e6, 2_048_000.0, 20.0)
    assert res["failed_step"] == STEP_STREAM
    assert isinstance(res["error"], USBDisconnectedError)
    assert "重新插入" in res["error"].suggestion


def test_no_audio_outputs():
    seq, _ = _make_seq(audio_outputs=[])
    res = seq.run_all(98e6, 2_048_000.0, 20.0)
    assert res["failed_step"] == STEP_AUDIO
    assert isinstance(res["error"], NoAudioOutputError)
    assert "声卡" in res["error"].suggestion and "选择输出设备" in res["error"].suggestion


def test_audio_player_start_false():
    seq = StartupSequence(
        setup_drivers_fn=lambda: {"ok": True, "message": "ok"},
        enumerate_fn=lambda: [FakeDevice()],
        backend_factory=lambda i, d: FakeBackend(),
        audio_player_factory=lambda sr: FakePlayer(start_ok=False),
        chain_factory=lambda fs, m: FakeChain(fs, m),
        list_audio_outputs_fn=lambda: ["[0] 扬声器"],
    )
    res = seq.run_all(98e6, 2_048_000.0, 20.0)
    assert res["failed_step"] == STEP_AUDIO
    assert isinstance(res["error"], NoAudioOutputError)


# ----------------------------------------------------------------------
# 3. 失败后后续步骤保持 pending，shutdown 安全
# ----------------------------------------------------------------------
def test_failure_stops_later_steps_pending():
    seq, _ = _make_seq(devices=[])  # enumerate 就失败
    res = seq.run_all(98e6, 2_048_000.0, 20.0)
    assert res["ok"] is False
    for name in STEP_ORDER:
        if name in (STEP_SETUP_DRIVERS, STEP_ENUMERATE):
            continue
        assert seq.steps[name].status == "pending", f"{name} 应保持 pending"
    seq.shutdown()  # 无异常


# ----------------------------------------------------------------------
# 4. validate() 前置检查
# ----------------------------------------------------------------------
def test_validate_reports_missing():
    seq = StartupSequence(
        setup_drivers_fn=lambda: {"ok": True, "message": "ok"},
        enumerate_fn=lambda: [],          # 无设备
        backend_factory=lambda i, d: FakeBackend(),
        audio_player_factory=lambda sr: FakePlayer(),
        chain_factory=lambda fs, m: FakeChain(fs, m),
        list_audio_outputs_fn=lambda: [],  # 无音频
    )
    res = seq.validate()
    assert res["ok"] is False
    assert any("SDR 设备" in m for m in res["missing"])
    assert any("音频输出" in m for m in res["missing"])


def test_validate_ok_when_all_present():
    seq, _ = _make_seq()
    res = seq.validate()
    assert res["ok"] is True
    assert res["missing"] == []


# ----------------------------------------------------------------------
# 5. format_error：UI 文案包含关键词
# ----------------------------------------------------------------------
@pytest.mark.parametrize("err_cls, keyword", [
    (DriverNotFoundError, "rtlsdr.dll"),
    (DeviceNotFoundError, "未检测到 SDR 设备"),
    (DeviceBusyError, "占用"),
    (USBDisconnectedError, "USB"),
    (NoAudioOutputError, "音频输出设备"),
])
def test_format_error_keywords(err_cls, keyword):
    ui = format_error(err_cls())
    assert keyword in ui["message"] or keyword in ui["title"]
    assert ui["suggestion"]  # 每条错误都必须有可操作建议
