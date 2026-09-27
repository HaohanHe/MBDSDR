"""端到端测试：音频设备枚举 / 选择 / 拔出安全降级 / 无设备错误提示 / 测试音。

通过 monkeypatch 替换 ``mbdsdr_ai.audio_out.sd``（sounddevice），
不依赖真实声卡 / PortAudio，全部确定性。
"""
from types import SimpleNamespace

import numpy as np
import pytest

from mbdsdr_ai import audio_out as audio_out_mod
from mbdsdr_ai.error_handler import NoAudioOutputError, format_error
from mbdsdr_ai.startup_sequence import StartupSequence, list_audio_outputs


# ----------------------------------------------------------------------
# Mock sounddevice
# ----------------------------------------------------------------------
class FakeOutputStream:
    """记录创建时 PortAudio 实际选用的输出设备（sd.default.device[1]）。"""

    instances = []

    def __init__(self, samplerate=None, channels=None, dtype=None,
                 callback=None, blocksize=None, device=None):
        self.samplerate = samplerate
        self.channels = channels
        self.callback = callback
        self.started = False
        self.closed = False
        # PortAudio 在 device=None 时读 sd.default.device；记录此刻选中的输出设备
        self.selected_output = audio_out_mod.sd.default.device[1]
        FakeOutputStream.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        pass

    def close(self):
        self.closed = True


class _DefaultNS(SimpleNamespace):
    pass


def make_fake_sd(output_device_names, start_raises=False):
    """构造假 sounddevice 模块。

    output_device_names: 输出设备名列表（内置一个虚拟输入设备凑数）。
    """
    devices = []
    # 一个纯输入设备（应被枚举过滤掉）
    devices.append(SimpleNamespace(name="Microphone (input only)",
                                   max_output_channels=0))
    for i, name in enumerate(output_device_names):
        devices.append(SimpleNamespace(name=name, max_output_channels=2))

    fake = SimpleNamespace()
    fake._devices = devices
    fake._start_raises = start_raises
    fake.default = _DefaultNS(device=(None, 1))  # 默认选中第 2 个输出设备

    def query_devices(kind=None):
        if kind == "output":
            return [d for d in devices if d["max_output_channels"] > 0]
        return devices

    def OutputStream(**kw):
        if start_raises:
            raise RuntimeError("PortAudio error: device unavailable (disconnected)")
        return FakeOutputStream(**kw)

    fake.query_devices = query_devices
    fake.OutputStream = OutputStream
    return fake


@pytest.fixture
def patch_sd(monkeypatch):
    """把假 sounddevice 装进 audio_out 模块，返回 (audio_out_mod, fake_sd)。"""
    def _apply(output_names, start_raises=False):
        fake = make_fake_sd(output_names, start_raises=start_raises)
        monkeypatch.setattr(audio_out_mod, "sd", fake, raising=True)
        monkeypatch.setattr(audio_out_mod, "_SD_AVAILABLE", True, raising=True)
        FakeOutputStream.instances.clear()
        return fake
    return _apply


# ----------------------------------------------------------------------
# 1. 枚举输出设备
# ----------------------------------------------------------------------
def test_enumerate_output_devices(patch_sd):
    fake = patch_sd(["Speakers (Realtek)", "Headphones (USB Audio)"])
    names = list_audio_outputs(fake)
    assert len(names) == 2
    assert any("Speakers" in n for n in names)
    assert any("Headphones" in n for n in names)
    # 纯输入设备被过滤
    assert not any("Microphone" in n for n in names)


# ----------------------------------------------------------------------
# 2. 选择输出设备 -> AudioPlayer 用该设备启动流
# ----------------------------------------------------------------------
def test_select_output_player_uses_selected_device(patch_sd):
    fake = patch_sd(["Speakers", "Headphones", "HDMI"])
    # 用户在设置里选择第 3 个输出设备（index 2）
    fake.default.device = (None, 2)

    player = audio_out_mod.AudioPlayer(sample_rate=48000, channels=1)
    assert player.available is True
    assert player.start() is True

    assert len(FakeOutputStream.instances) == 1
    stream = FakeOutputStream.instances[0]
    assert stream.selected_output == 2, "AudioPlayer 应使用用户选中的输出设备"
    assert stream.samplerate == 48000
    player.stop()


# ----------------------------------------------------------------------
# 3. 运行中设备拔出 -> 安全降级不崩
# ----------------------------------------------------------------------
def test_device_unplug_degrades_gracefully(patch_sd):
    fake = patch_sd(["Speakers"], start_raises=True)
    player = audio_out_mod.AudioPlayer(sample_rate=48000)
    # start 打开流时设备已拔出：PortAudio 报错 -> available 置 False
    assert player.start() is False
    assert player.available is False
    # 之后 write 静默返回 0，绝不抛异常
    blk = np.zeros(480, dtype=np.float32)
    assert player.write(blk) == 0
    assert player.is_playing is False
    player.stop()  # 重复 stop 安全


# ----------------------------------------------------------------------
# 4. 无输出设备 -> 明确错误提示
# ----------------------------------------------------------------------
def test_no_output_device_clear_error(patch_sd):
    patch_sd([])  # 一个输出设备都没有
    assert list_audio_outputs(audio_out_mod.sd) == []

    # StartupSequence 整合层必须给出可操作提示
    seq = StartupSequence(
        setup_drivers_fn=lambda: {"ok": True, "message": "ok"},
        enumerate_fn=lambda: [object()],
        backend_factory=lambda i, d: object(),
        audio_player_factory=lambda sr: audio_out_mod.AudioPlayer(sample_rate=sr),
        chain_factory=lambda fs, m: object(),
        list_audio_outputs_fn=lambda: [],
    )
    # 直接调 start_audio（前面步骤不跑，专注音频错误）
    import pytest
    with pytest.raises(NoAudioOutputError) as ei:
        seq.start_audio()
    ui = format_error(ei.value)
    assert "音频输出设备" in ui["message"]
    assert "声卡" in ui["suggestion"] and "选择输出设备" in ui["suggestion"]


# ----------------------------------------------------------------------
# 5. 测试音播放（mock 流）：写队列成功
# ----------------------------------------------------------------------
def test_test_tone_write_queued(patch_sd):
    patch_sd(["Speakers"])
    player = audio_out_mod.AudioPlayer(sample_rate=48000, gain=1.0)
    assert player.start() is True
    # 1 秒 440Hz 正弦测试音
    t = np.arange(4800) / 48000.0
    tone = (0.3 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
    n = player.write(tone)
    assert n == 4800
    assert player._queued_samples == 4800  # mock 回调不消费，队列累积
    player.stop()


# ----------------------------------------------------------------------
# 6. sounddevice 整体不可用时 AudioPlayer 静态降级
# ----------------------------------------------------------------------
def test_sounddevice_absent_degrades(monkeypatch):
    monkeypatch.setattr(audio_out_mod, "sd", None, raising=True)
    monkeypatch.setattr(audio_out_mod, "_SD_AVAILABLE", False, raising=True)
    player = audio_out_mod.AudioPlayer(sample_rate=48000)
    assert player.available is False
    assert player.start() is False
    assert player.write(np.zeros(100, dtype=np.float32)) == 0
    assert player.is_playing is False
    player.stop()  # 不崩即可
