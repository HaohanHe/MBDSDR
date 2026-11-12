# SPDX-License-Identifier: MIT
"""
tests/test_audio_route_anr.py
==============================
验证音频路由与 ANR 修复（2026-11-02 SDR 审计）:

  1. AudioPlayer.preferred_device 真传 sounddevice OutputStream 的 device 参数;
  2. 未设置偏好时不传 device（走系统默认）;
  3. 主解调路接入 audio_tap（ANR 后处理缝存在且主窗口已接线）。
"""
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

import mbdsdr_ai.audio_out as ao  # noqa: E402


class _FakeStream:
    kwargs_seen = []

    def __init__(self, **kw):
        _FakeStream.kwargs_seen.append(kw)
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False


def test_preferred_device_forwarded(monkeypatch):
    monkeypatch.setattr(ao, "sd", types.SimpleNamespace(OutputStream=_FakeStream))
    monkeypatch.setattr(ao, "sd_available", lambda: True)
    p = ao.AudioPlayer(sample_rate=48000)
    p.preferred_device = 3
    assert p.start() is True
    kw = _FakeStream.kwargs_seen[-1]
    assert kw.get("device") == 3, kw


def test_default_device_not_forced(monkeypatch):
    _FakeStream.kwargs_seen.clear()
    monkeypatch.setattr(ao, "sd", types.SimpleNamespace(OutputStream=_FakeStream))
    monkeypatch.setattr(ao, "sd_available", lambda: True)
    p = ao.AudioPlayer(sample_rate=48000)
    assert p.start() is True
    assert "device" not in _FakeStream.kwargs_seen[-1]


class _FakePlayer:
    """记录真正写进声卡的每一帧（用值可区分的常量证明 tap 生效）。"""

    def __init__(self):
        self.writes = []

    def write(self, audio):
        self.writes.append(np.array(audio, copy=True))


@pytest.fixture(scope="module")
def mw_window(isolated_profile):
    from PySide6.QtWidgets import QApplication
    from main_window import MainWindow
    QApplication.instance() or QApplication([])
    w = MainWindow()
    yield w
    try:
        w._disconnect()
    except Exception:  # noqa: BLE001 - 收尾失败不拖垮用例
        pass
    w.close()


def test_anr_tap_wired_in_main_path(mw_window, monkeypatch):
    """主听路必须经过 audio_tap：写卡内容 = tap 的返回值，主窗口已接上自己的后处理。

    旧写法数源码里 `self.audio_tap(audio)` 出现几次——2026-11 两条写卡路径
    合并进 `_push_audio` 后该断言假红（实际功能完好）。这里改为驱动真实
    DemodWorker 写卡，看 tap 有没有真的被调用、返回值有没有真的落到声卡。
    """
    from receive_pipeline import DemodWorker, SharedDemodConfig

    cfg = SharedDemodConfig(mode="FM")
    cfg.agc_speed = "off"
    player = _FakePlayer()
    dw = DemodWorker(None, None, player, cfg, 48_000.0)

    seen = []

    def tap(audio):
        seen.append(np.array(audio, copy=True))
        return np.full_like(np.asarray(audio), 0.25, dtype=np.float32)

    dw.audio_tap = tap
    dw._push_audio(np.zeros(64, dtype=np.float32), player, "FM")
    assert len(seen) == 1, "写声卡前没有经过 audio_tap（ANR/分发缝被绕过）"
    assert player.writes[-1][0] == pytest.approx(0.25), \
        "写卡用的不是 tap 处理后的音频（tap 返回值被丢弃）"

    dw.audio_tap = None
    dw._push_audio(np.zeros(8, dtype=np.float32), player, "FM")
    assert player.writes[-1][0] == pytest.approx(0.0), \
        "无 tap 时应直通原音频"


class _FakeDemod1:
    def __init__(self):
        self.audio_tap = None


class _FakePipeline:
    """只保留主窗口真正用到的接口（信号 + start + _demod1）。"""

    last = None

    def __init__(self, **kwargs):
        from PySide6.QtCore import QObject, Signal

        class _Sig(QObject):
            frame_ready = Signal(object)
            read_failed = Signal(str)

        self._sig = _Sig()
        self.frame_ready = self._sig.frame_ready
        self.read_failed = self._sig.read_failed
        self._demod1 = _FakeDemod1()
        self.started = False
        _FakePipeline.last = self

    def start(self):
        self.started = True

    def shutdown(self):
        self.started = False


def test_main_window_binds_tap_to_demod_worker(mw_window, monkeypatch):
    """主窗口建流水线后，主听 worker 的 audio_tap 必须是自己的统一后处理。"""
    import main_window as mwm

    monkeypatch.setattr(mwm, "ReceivePipeline", _FakePipeline)
    monkeypatch.setattr(mw_window, "_active_read_samples",
                        lambda where="pipeline": (lambda n: None, 48_000.0))
    mw_window._build_pipeline()

    pipe = _FakePipeline.last
    assert pipe is not None and pipe.started, "流水线没被真正启动"
    tap = pipe._demod1.audio_tap
    assert callable(tap), \
        "主听 worker 的 audio_tap 没接上（ANR/APRS/云图都收不到音频）"
    assert getattr(tap, "__self__", None) is mw_window, \
        "audio_tap 绑的不是主窗口自己的统一后处理"
    out = tap(np.zeros(256, dtype=np.float32))
    assert isinstance(out, np.ndarray) and len(out) == 256, \
        "主窗口后处理没有返回可供写卡的音频"
