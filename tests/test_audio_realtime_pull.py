# SPDX-License-Identifier: MIT
"""``read_audio`` 不得退化为同步串行：即使没开声卡也要走「读线程 + 链线程」。

真机证据（2026-11-22，RTL-SDR FC0012，2.4 MS/s NFM）：只用 ``read_audio``
的拉流/录制路径，每轮在调用线程里同步「读 192k 样本 + 跑 ReceiveChain」，
读与算的耗时叠加，实测产出仅 0.675× 实时——R6 的 30 s 录制窗口只落 21 s
音频。声卡线程（``start_audio``）本是并行读+算，但纯拉流不走那条路。

反证：把 ``read_audio`` 改回「线程没跑就自己 read_samples + chain.process」，
第一个用例立即红——同步分支每轮只读一次 ``max(n*4, 8192)`` 样本（16384 字节），
而解耦后读线程按 16384 样本整块（32768 字节）持续供给，断言直接对不上。
"""
from __future__ import annotations

import os
import sys
import threading
import time

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.device_manager import DeviceInfo, OpenDevice  # noqa: E402


def _info() -> DeviceInfo:
    return DeviceInfo(name="Fake RTL-SDR", driver="rtlsdr", serial="FAKE")


class _RecordingBackend:
    """记录每次设备读的字节数；返回全零 IQ（128=零）。"""

    def __init__(self):
        self.sizes: list[int] = []
        self.threads: list[str] = []

    def read_bytes(self, n: int) -> bytes:
        self.sizes.append(int(n))
        self.threads.append(threading.current_thread().name)
        return bytes([128, 128] * (n // 2))

    def close(self):
        pass


class _FixedChain:
    """每块固定产出 300 个音频样本（与真实 48k 抽取量同量级）。"""

    is_stereo = False

    def __init__(self):
        self.blocks = 0

    def process(self, iq):
        self.blocks += 1
        return np.ones(300, dtype=np.float32)


def _make_device(chain):
    dev = OpenDevice(_info(), _RecordingBackend(), read_timeout_s=1.0)
    dev._ensure_chain = lambda mode=None: chain  # 绕开滤波器设计，只测数据面
    return dev


def test_read_audio_runs_on_decoupled_worker_threads():
    chain = _FixedChain()
    dev = _make_device(chain)
    try:
        assert dev._backend.sizes == [], "构造 OpenDevice 不应触碰设备"
        out = dev.read_audio(1024, "NFM")
        assert out is not None and len(out) == 1024
        assert dev._reader_thread is not None and dev._reader_thread.is_alive()
        assert dev._audio_thread is not None and dev._audio_thread.is_alive()
        # 旧同步回退每轮只读 max(n*4, 8192)=8192 样本（16384 字节）且恰好一次；
        # 解耦后读线程按 16384 样本整块持续供给（32768 字节，且不止一块）。
        assert set(dev._backend.sizes) == {32768}, (
            f"设备读必须是读线程整块 16384 样本；实测 {dev._backend.sizes}")
        assert len(dev._backend.sizes) >= 2
    finally:
        dev.stop_audio()
        dev.close()


class _AliveThread:
    def is_alive(self):
        return True

    def join(self, timeout=None):
        return None


def test_read_audio_keeps_block_remainder_for_next_call():
    """块级多余样本不能丢：请求 1000、手里有 1200 时只交 1000，剩 200 留队首。"""
    chain = _FixedChain()
    dev = _make_device(chain)
    try:
        # 只验证收集/截断逻辑：假装工作线程已在跑，手工塞一块 1200 样本。
        dev._audio_thread = _AliveThread()
        dev._reader_thread = _AliveThread()
        with dev._audio_lock:
            dev._audio_out_q.append(
                np.arange(1200, dtype=np.float32))
        out = dev.read_audio(1000)
        assert len(out) == 1000
        assert float(out[-1]) == 999.0
        with dev._audio_lock:
            assert len(dev._audio_out_q) == 1
            rest = dev._audio_out_q[0]
        assert len(rest) == 200
        assert float(rest[0]) == 1000.0
    finally:
        dev.stop_audio()
        dev.close()


def test_start_audio_attaches_player_to_running_pull_worker(monkeypatch):
    """「线程在跑」≠「有声音」：纯拉流起过线程后，start_audio 必须补建声卡。"""
    class _FakePlayer:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.started = False

        def start(self):
            self.started = True
            return True

        def write(self, audio):
            return len(audio)

        def stop(self):
            pass

    monkeypatch.setattr("mbdsdr_ai.audio_out.AudioPlayer", _FakePlayer)
    chain = _FixedChain()
    dev = _make_device(chain)
    try:
        assert dev.read_audio(256, "NFM") is not None
        assert dev._audio_player is None, "纯拉流不应自动打开声卡"
        assert dev.start_audio("NFM") is True
        assert isinstance(dev._audio_player, _FakePlayer)
        assert dev._audio_player.started is True
    finally:
        dev.stop_audio()
        dev.close()


def test_idle_pull_worker_yields_device(monkeypatch):
    """没人再拉流时必须停机：设备读是单通道，闲着占住会饿死读谱/录制。"""
    import mbdsdr_ai.device_manager as dm_mod

    monkeypatch.setattr(dm_mod, "AUDIO_PULL_IDLE_S", 0.05)
    chain = _FixedChain()
    dev = _make_device(chain)
    try:
        out = dev.read_audio(256, "NFM")
        assert out is not None and len(out) == 256
        assert dev._audio_threads_alive()
        time.sleep(0.6)
        assert not dev._reader_thread.is_alive(), "空闲后读线程必须退出"
        assert not dev._audio_thread.is_alive(), "空闲后链线程必须退出"
        # 下一次调用要能无缝重启，而不是永久退化
        bg = dev._backend
        before = len(bg.sizes)
        out = dev.read_audio(256, "NFM")
        assert out is not None and len(out) == 256
        assert len(bg.sizes) > before, "重启后必须重新从设备取数"
    finally:
        dev.stop_audio()
        dev.close()
