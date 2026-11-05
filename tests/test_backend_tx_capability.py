# SPDX-License-Identifier: MIT
"""桌面 SDRBackend 发射能力矩阵：声明/实现/就绪必须一致，写流必须真落样本。

对应产品缺口：HackRF/USRP/Pluto 声明 supports_tx=True 却没有 write_tx，
PTT 永远置灰（"后端没有 TX 通道"）。本文件钉住三件事：
  1) 没有 write_tx 的后端，supports_tx() 必须是 False（不许只有声明）；
  2) 有 write_tx 的后端，在 mock 硬件上真调用一次并把样本交到驱动；
  3) 未过人工确认授权的直调一律拒绝（不静默发波）。
"""
import sys
import types

import numpy as np
import pytest

from mbdsdr_ai.sdr_backend import (
    AISDRMiniBackend, FileIQBackend, HackRFBackend, NetworkIQBackend,
    PlutoSDRBackend, RTLSDRBackend, SDRBackend, SDRDevice, SoapySDRBackend,
    USRPBackend,
)
from mbdsdr_ai.tx_guard import GateDecision, tx_authorization

ALLOWED = GateDecision(allowed=True, code="ok", reason="测试授权")


def _authorized(call):
    with tx_authorization(ALLOWED):
        return call()


def _rx_only_instances():
    return [
        RTLSDRBackend(0),
        AISDRMiniBackend(),
        SoapySDRBackend(),
        FileIQBackend(path="not_exist.iq"),
        NetworkIQBackend("tcp", "127.0.0.1", 1, 2_000_000.0, 1_000_000.0),
    ]


def test_declared_without_write_tx_is_false():
    """基类层面的最低诚实性：只声明、没实现的类 supports_tx() 必须 False。"""

    class OnlyDeclared(SDRBackend):
        def __init__(self):
            super().__init__(SDRDevice(
                device_type="fake", device_id="fake", name="fake",
                frequency_range=(1e6, 6e9), sample_rate_range=(1e6, 20e6),
                max_gain=0.0, supports_iq=True, supports_tx=True))

        def connect(self):
            return True

        def read_samples(self, num_samples):
            return None

    be = OnlyDeclared()
    assert be.device.supports_tx is True
    assert be.supports_tx() is False
    cap = be.tx_capability()
    assert cap["declared"] is True and cap["implemented"] is False
    assert cap["ready"] is False


def test_rx_only_backends_are_not_tx_capable():
    for be in _rx_only_instances():
        assert "write_tx" not in type(be).__dict__, type(be).__name__
        assert be.device.supports_tx is False, type(be).__name__
        assert be.supports_tx() is False, type(be).__name__


def test_tx_backends_implement_write_tx_and_are_honest_before_connect():
    for cls in (HackRFBackend, USRPBackend, PlutoSDRBackend):
        be = cls()
        assert callable(getattr(type(be), "write_tx", None)), cls.__name__
        assert be.device.supports_tx is True, cls.__name__
        assert be.device.tx_verified is False, cls.__name__  # 未上真机
        # 未连接时运行时就绪为 False：PTT 不会假装可用
        assert be.supports_tx() is False, cls.__name__
        cap = be.tx_capability()
        assert cap["implemented"] is True and cap["verified"] is False


class _FakeHackRFDriver:
    def __init__(self):
        self.writes = []
        self.tx = 0

    def write_tx(self, iq):
        self.writes.append(np.asarray(iq))
        self.tx += 1
        return True


def test_hackrf_write_tx_hands_samples_to_driver():
    be = HackRFBackend(0)
    be._hackrf = _FakeHackRFDriver()
    be.status.connected = True

    block = (np.arange(8, dtype=np.float32) / 8.0).astype(np.complex64)
    assert _authorized(lambda: be.write_tx(block)) is True
    assert be._hackrf.tx == 1
    assert len(be._hackrf.writes) == 1
    np.testing.assert_array_equal(be._hackrf.writes[0], block)


def test_hackrf_write_tx_without_authorization_rejects():
    be = HackRFBackend(0)
    be._hackrf = _FakeHackRFDriver()
    be.status.connected = True

    assert be.write_tx(np.ones(4, dtype=np.complex64)) is False
    assert be._hackrf.writes == []  # 没授权就一个样本都不许写
    assert "发射被拒" in be.status.error  # 拒绝原因如实落状态


class _FakeIioBuffer:
    def __init__(self):
        self.data = None
        self.pushes = 0

    def write(self, array):
        self.data = np.asarray(array)

    def push(self, samples_count=None):
        self.pushes += 1


class _FakeIioTxDevice:
    def __init__(self):
        self.buffers = []

    def create_buffer(self, samples_count, cyclic=False):
        self.buffers.append(_FakeIioBuffer())
        return self.buffers[-1]


def test_pluto_iio_write_tx_pushes_interleaved_int16():
    be = PlutoSDRBackend()
    be._backend_lib = "iio"
    be._tx_stream_dev = _FakeIioTxDevice()
    be.status.connected = True

    iq = np.array([0.5 + 0.25j, -0.5 - 0.25j], dtype=np.complex64)
    assert _authorized(lambda: be.write_tx(iq)) is True

    buf = be._tx_stream_dev.buffers[-1]
    assert buf.pushes == 1
    got = buf.data
    assert got.dtype == np.int16
    assert got.tolist() == [16383, 8191, -16383, -8191]


class _FakeSoapyStatus:
    def __init__(self, ret):
        self.ret = ret


class _FakeSoapyPluto:
    def __init__(self, ret):
        self.ret = ret
        self.written = []

    def setFrequency(self, direction, channel, hz):
        self.freq = hz

    def setSampleRate(self, direction, channel, rate):
        self.rate = rate

    def writeStream(self, stream, buffs, num_elems, flags, time_ns, timeoutUs=0):
        self.written.append(np.asarray(buffs[0])[:num_elems])
        return _FakeSoapyStatus(self.ret)


def test_pluto_soapy_write_tx_calls_write_stream():
    be = PlutoSDRBackend()
    be._backend_lib = "soapy"
    be._tx_dir = 2
    be._tx_stream = object()
    be._soapy = _FakeSoapyPluto(ret=4)
    be.status.connected = True

    iq = np.ones(4, dtype=np.complex64)
    assert _authorized(lambda: be.write_tx(iq)) is True
    assert len(be._soapy.written) == 1
    np.testing.assert_array_equal(be._soapy.written[0], iq)


def _install_fake_uhd(monkeypatch):
    usrp_mod = types.SimpleNamespace()

    class StreamArgs:
        def __init__(self, cpu_fmt, otw_fmt):
            self.cpu_fmt = cpu_fmt
            self.otw_fmt = otw_fmt
            self.channels = []

    usrp_mod.StreamArgs = StreamArgs

    types_mod = types.SimpleNamespace()

    class TXMetadata:
        def __init__(self):
            self.start_of_burst = True
            self.end_of_burst = True
            self.has_time_spec = False

    types_mod.TXMetadata = TXMetadata

    libpyuhd_types = types.SimpleNamespace(tune_request=lambda hz: hz)
    uhd_mod = types.SimpleNamespace(
        usrp=usrp_mod, types=types_mod,
        libpyuhd=types.SimpleNamespace(types=libpyuhd_types))
    monkeypatch.setitem(sys.modules, "uhd", uhd_mod)


class _FakeUhdStreamer:
    def __init__(self):
        self.sent = []

    def send(self, metadata, buff):
        self.sent.append(np.asarray(buff).copy())
        return int(len(buff))


class _FakeUhdUsrp:
    def __init__(self):
        self.streamer = _FakeUhdStreamer()
        self.tx_freqs = []

    def get_tx_stream(self, args):
        return self.streamer

    def set_tx_rate(self, rate):
        self.rate = rate

    def set_tx_gain(self, gain):
        self.gain = gain

    def set_tx_freq(self, req):
        self.tx_freqs.append(req)


def test_usrp_write_tx_sends_samples_through_uhd_stream(monkeypatch):
    _install_fake_uhd(monkeypatch)
    be = USRPBackend()
    be._usrp = _FakeUhdUsrp()
    be.status.connected = True

    iq = np.ones(6, dtype=np.complex64)
    assert _authorized(lambda: be.write_tx(iq)) is True
    assert len(be._usrp.streamer.sent) == 1
    np.testing.assert_array_equal(be._usrp.streamer.sent[0], iq)


def test_hackrf_params_write_tx_enqueues_int8_and_starts_stream(monkeypatch):
    """ctypes 层：样本按 int8 交织入队，并真的调用 start_tx 启动发送流。"""
    from mbdsdr_ai.hackrf_params import HackRFBackend as CtlsHackRF

    be = CtlsHackRF(0)
    started = {"n": 0}

    def _fake_start_tx():
        started["n"] += 1
        be._tx_running = True
        return True

    monkeypatch.setattr(be, "start_tx", _fake_start_tx)
    be.connected = True
    be._lib = object()

    iq = np.array([1.0 + 0.0j, -1.0 + 0.5j], dtype=np.complex64)
    assert be.write_tx(iq) is True
    assert started["n"] == 1
    assert be._tx_queue.qsize() == 1
    raw = be._tx_queue.get_nowait()
    decoded = np.frombuffer(raw, dtype=np.int8)
    assert decoded[0] == 127 and decoded[1] == 0
    assert decoded[2] == -127  # 字节 129 是 int8 的 -127（二补码）


def test_hackrf_params_write_tx_requires_connected():
    from mbdsdr_ai.hackrf_params import HackRFBackend as CtlsHackRF

    be = CtlsHackRF(0)
    assert be.write_tx(np.ones(4, dtype=np.complex64)) is False
    assert be.is_underrun() is False


def test_tx_capability_marks_all_three_unverified():
    caps = {cls.__name__: cls().tx_capability()
            for cls in (HackRFBackend, USRPBackend, PlutoSDRBackend)}
    for name, cap in caps.items():
        assert cap["declared"] is True, name
        assert cap["implemented"] is True, name
        assert cap["verified"] is False, name


def test_soapy_generic_reports_driver_capable_third_state():
    """Soapy 通用路径不能说「没有 TX 通道」：驱动可发与本路径未接要分开。"""
    be = SoapySDRBackend()
    # 未探测 → 未知（不臆断为"不能发"）
    assert be.supports_tx() is False
    cap = be.tx_capability()
    assert cap["driver_capable"] is None
    assert cap["blocked_reason"] == "driver_capability_unknown"

    # 驱动上报 2 个 TX 通道（如 HackRF/USRP 经 Soapy）：驱动级可发，本路径未接
    be._num_tx_channels = 2
    assert be.supports_tx() is False, "本类没有 write_tx，仍不许声称可发射"
    cap = be.tx_capability()
    assert cap["declared"] is False and cap["implemented"] is False
    assert cap["driver_capable"] is True
    assert cap["driver_tx_channels"] == 2
    assert cap["blocked_reason"] == "driver_capable_path_not_implemented"
    assert "驱动级可发" in cap["notice"]
    assert "本路径不能发射" in cap["notice"]

    # 驱动上报 0 个 TX 通道 → 明确只能收
    be._num_tx_channels = 0
    cap = be.tx_capability()
    assert cap["driver_capable"] is False
    assert cap["blocked_reason"] == "no_tx_channels"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
