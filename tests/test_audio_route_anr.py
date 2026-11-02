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
    monkeypatch.setattr(ao, "_SD_AVAILABLE", True)
    p = ao.AudioPlayer(sample_rate=48000)
    p.preferred_device = 3
    assert p.start() is True
    kw = _FakeStream.kwargs_seen[-1]
    assert kw.get("device") == 3, kw


def test_default_device_not_forced(monkeypatch):
    _FakeStream.kwargs_seen.clear()
    monkeypatch.setattr(ao, "sd", types.SimpleNamespace(OutputStream=_FakeStream))
    monkeypatch.setattr(ao, "_SD_AVAILABLE", True)
    p = ao.AudioPlayer(sample_rate=48000)
    assert p.start() is True
    assert "device" not in _FakeStream.kwargs_seen[-1]


def test_anr_tap_wired_in_main_path():
    src = open(os.path.join(_ROOT, "desktop", "receive_pipeline.py"),
               encoding="utf-8").read()
    assert src.count("self.audio_tap(audio)") >= 2, \
        "两条写声卡路径都应走 audio_tap"
    mw = open(os.path.join(_ROOT, "desktop", "main_window.py"),
              encoding="utf-8").read()
    assert "pipe._demod1.audio_tap = self._tap_demod_audio" in mw
