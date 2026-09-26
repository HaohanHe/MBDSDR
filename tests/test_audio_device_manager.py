"""确定性单测：sounddevice 设备枚举/选择/测试音/无设备安全。

全部用 FakeSD 注入，不触碰真实声卡。
"""
import threading

import numpy as np

from mbdsdr_ai.audio_device_manager import (
    AudioDeviceManager,
    make_test_tone,
    TEST_TONE_FREQ,
    TEST_TONE_AMPLITUDE,
)


class _FakeDefault:
    def __init__(self, in_idx=0, out_idx=0):
        self.device = (in_idx, out_idx)


class FakeSD:
    """模拟 sounddevice 模块的最小接口。"""

    def __init__(self, devices):
        # devices: list of dict，每个含 name/default_samplerate/max_output_channels/max_input_channels
        self._devices = [dict(d) for d in devices]
        self.default = _FakeDefault(0, 0)
        self.ostreams = []

    def query_devices(self, idx=None):
        if idx is None:
            return [dict(d) for d in self._devices]
        return dict(self._devices[idx])

    class OutputStream:
        def __init__(self, sd_self, **kw):
            self._sd = sd_self
            self.kw = kw
            self.started = False
        def start(self):
            self.started = True
        def write(self, data):
            self.data = np.asarray(data)
        def stop(self):
            pass
        def close(self):
            pass

    def OutputStream(self, **kw):  # noqa: N802 - 模仿 sd.OutputStream
        st = FakeSD.OutputStream(self, **kw)
        self.ostreams.append(st)
        return st


OUTPUT_DEVICES = [
    {"name": "Speakers (Realtek)", "default_samplerate": 48000.0,
     "max_output_channels": 2, "max_input_channels": 0},
    {"name": "Microphone (Mic)", "default_samplerate": 44100.0,
     "max_output_channels": 0, "max_input_channels": 2},
    {"name": "USB DAC", "default_samplerate": 96000.0,
     "max_output_channels": 2, "max_input_channels": 2},
]


def test_list_output_devices_filters_output_only():
    mgr = AudioDeviceManager(sd_module=FakeSD(OUTPUT_DEVICES))
    outs = mgr.list_output_devices()
    names = [d["name"] for d in outs]
    assert "Microphone (Mic)" not in names  # 纯输入设备被过滤
    assert "Speakers (Realtek)" in names
    assert "USB DAC" in names
    # 字段完整
    spk = next(d for d in outs if d["name"] == "Speakers (Realtek)")
    assert spk["index"] == 0
    assert spk["channels"] == 2
    assert spk["default_samplerate"] == 48000.0


def test_list_input_devices_filters_input_only():
    mgr = AudioDeviceManager(sd_module=FakeSD(OUTPUT_DEVICES))
    ins = mgr.list_input_devices()
    names = [d["name"] for d in ins]
    assert "Microphone (Mic)" in names
    assert "USB DAC" in names  # 双向设备既出现在输出也出现在输入
    assert "Speakers (Realtek)" not in names


def test_no_devices_returns_empty_not_fake():
    # sd 不可用（None）：枚举必须返回空列表，绝不造假
    mgr = AudioDeviceManager(sd_module=None)
    assert mgr.available is False
    assert mgr.list_output_devices() == []
    assert mgr.list_input_devices() == []
    assert mgr.get_default_output() is None
    assert mgr.set_output_device(0) is False
    assert mgr.test_device(0) is False


def test_query_devices_exception_returns_empty():
    class ExplodingSD:
        def query_devices(self, idx=None):
            raise RuntimeError("PortAudio not initialized")
    mgr = AudioDeviceManager(sd_module=ExplodingSD())
    assert mgr.list_output_devices() == []


def test_get_default_output():
    sd = FakeSD(OUTPUT_DEVICES)
    sd.default = _FakeDefault(0, 2)  # 默认输出 = USB DAC (idx 2)
    mgr = AudioDeviceManager(sd_module=sd)
    out = mgr.get_default_output()
    assert out is not None
    assert out["index"] == 2
    assert out["name"] == "USB DAC"


def test_set_output_device_updates_default_and_restarts_player():
    sd = FakeSD(OUTPUT_DEVICES)
    started = []
    stopped = []

    class FakePlayer:
        def stop(self): stopped.append(1)
        def start(self): started.append(1)

    player = FakePlayer()
    mgr = AudioDeviceManager(sd_module=sd, player=player)
    assert mgr.set_output_device(2) is True
    assert sd.default.device[1] == 2
    # 切换后重启了 player
    assert len(stopped) == 1
    assert len(started) == 1


def test_make_test_tone_shape_and_level():
    tone = make_test_tone(sample_rate=48000.0, duration=1.0,
                          freq=TEST_TONE_FREQ, amplitude=TEST_TONE_AMPLITUDE)
    assert tone.dtype == np.float32
    assert len(tone) == 48000
    # 幅度被控制在 ±amplitude 附近（不削波）
    assert np.max(np.abs(tone)) <= TEST_TONE_AMPLITUDE + 1e-6


def test_test_device_success(monkeypatch):
    sd = FakeSD(OUTPUT_DEVICES)
    mgr = AudioDeviceManager(sd_module=sd)
    played = {}

    def fake_play(index, sr, samples):
        played["index"] = index
        played["sr"] = sr
        played["n"] = len(samples)

    monkeypatch.setattr(mgr, "_play_blocking", fake_play)
    assert mgr.test_device(2) is True
    assert played["index"] == 2
    assert played["sr"] == 96000.0
    assert played["n"] == 96000  # 1 秒 @96k


def test_test_device_failure_returns_false(monkeypatch):
    sd = FakeSD(OUTPUT_DEVICES)
    mgr = AudioDeviceManager(sd_module=sd)

    def boom(index, sr, samples):
        raise RuntimeError("device busy")

    monkeypatch.setattr(mgr, "_play_blocking", boom)
    assert mgr.test_device(0) is False


def test_start_monitor_detects_hotplug():
    devices = [dict(OUTPUT_DEVICES[0])]
    sd = FakeSD(devices)
    mgr = AudioDeviceManager(sd_module=sd)

    events = []
    got = threading.Event()

    def cb(added, removed):
        events.append((set(added), set(removed)))
        got.set()

    assert mgr.start_monitor(cb, interval=0.02) is True
    try:
        # 第二次轮询时新增一个设备
        sd._devices.append(dict(OUTPUT_DEVICES[2]))
        assert got.wait(2.0), "热插拔回调未触发"
        assert len(events) == 1
        added, removed = events[0]
        assert any("USB DAC" in a[0] for a in added)
        assert removed == set()
    finally:
        mgr.stop_monitor()
