# SPDX-License-Identifier: MIT
"""
AudioPlayer 无设备安全降级测试
==============================

对照上游：
- sdrpp/sink_modules/new_portaudio_sink/src/main.cpp:106-109
    Pa_OpenStream 失败时 flog::error 并 return（不崩溃、不假成功）。
- audio_out.py:149-174 start()：打开失败时 available=False 并 return False。

测试在无声卡（CI 沙箱）环境下：
- AudioPlayer.available 可能为 False（sounddevice 不可用或无设备）
- write()/start()/stop() 不崩溃，返回 0/False
- 即便 available=True，未 start() 时 write() 也返回 0
"""
import os
import sys

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai.audio_out import AudioPlayer


def test_player_start_returns_bool():
    player = AudioPlayer(sample_rate=48000)
    result = player.start()
    assert isinstance(result, bool)
    # 无论是否有设备，都不应抛异常
    player.stop()


def test_player_write_no_stream_returns_zero():
    """未 start() 时 write() 返回 0，不崩。"""
    player = AudioPlayer(sample_rate=48000)
    # 强制未启动
    player._stream = None
    block = np.zeros(1024, dtype=np.float32)
    n = player.write(block)
    assert n == 0


def test_player_write_empty_block_returns_zero():
    """空块不崩，返回 0。"""
    player = AudioPlayer(sample_rate=48000)
    n = player.write(np.zeros(0, dtype=np.float32))
    assert n == 0


def test_player_write_none_safe():
    """write(None) 不崩，返回 0。"""
    player = AudioPlayer(sample_rate=48000)
    n = player.write(None)  # type: ignore[arg-type]
    assert n == 0


def test_player_stop_idempotent():
    """stop() 可重复调用，不崩。"""
    player = AudioPlayer(sample_rate=48000)
    player.stop()
    player.stop()
    player.stop()


def test_player_set_gain_clamp():
    """set_gain 越界被 clip 到 [0,5]。"""
    player = AudioPlayer(sample_rate=48000)
    player.set_gain(10.0)
    assert player.gain == 5.0
    player.set_gain(-1.0)
    assert player.gain == 0.0
    player.set_gain(0.7)
    assert player.gain == 0.7


def test_player_mute_ramp_state():
    """set_muted 切换不崩，is_muted 反映状态。"""
    player = AudioPlayer(sample_rate=48000)
    player.set_muted(True)
    assert player.is_muted is True
    player.set_muted(False)
    assert player.is_muted is False


def test_player_resampler_lazy_builds():
    """write(block, sr=12000) 在无 stream 时不崩（返回 0）；
    不触发真实重采样。"""
    player = AudioPlayer(sample_rate=48000)
    player._stream = None
    block = np.zeros(1024, dtype=np.float32)
    n = player.write(block, sr=12000)
    assert n == 0
