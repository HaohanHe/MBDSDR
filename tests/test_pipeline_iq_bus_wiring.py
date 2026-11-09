# SPDX-License-Identifier: MIT
"""IQ 总线真接线：真正跑的 pipeline 帧入口必须发布 IQ。

`ReceivePipeline` 的 FFT worker 直接把谱线喂给 `_plot`，**不走**
`SpectrumPanel.set_iq_data`；而 `AdsbMapPanel` 上一轮改成自订阅 `iq_bus`，
发布点却只在 `set_iq_data` 里 → 总线永远没有数据，1090 地图永久空态。
`_iq_poll_timer` 只有 connect / stop，没有 start，也救不了它。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from test_main_window_hw_truthfulness import window  # noqa: E402, F401


@pytest.fixture
def w_stubbed(window, monkeypatch):
    """只保留帧入口的分发逻辑，把无关的 UI/状态刷新桩掉。"""
    w = window
    for name in ("_update_rssi_from_iq", "_refresh_vfo_panel",
                 "_update_status_bar", "_tap_new_spacetime_iq",
                 "_ensure_repaint_timer"):
        monkeypatch.setattr(w, name, lambda *a, **k: None, raising=False)
    monkeypatch.setattr(w.spectrum, "_refresh_status_labels",
                        lambda *a, **k: None, raising=False)
    w._read_fail_signals = 0
    w._rssi_last_t = 0.0
    w._backend_status_tick = 0
    w._vfo_refresh_tick = 0
    w._nst_tick = 0
    return w


def _frame():
    return np.ones(4096, dtype=np.complex64) * 0.1


def test_pipeline_frame_publishes_to_iq_bus(w_stubbed):
    from desktop.iq_bus import get_iq_bus, reset_iq_bus
    bus = reset_iq_bus()
    assert bus.latest() is None

    iq = _frame()
    w_stubbed._on_pipeline_frame_ready(iq, 2.4e6)

    frame = bus.latest()
    assert frame is not None, "frame_ready 必须发布 IQ，否则地图面板永远空态"
    assert frame.iq is iq               # 单槽总线只存引用，不 copy
    assert frame.sample_rate == 2.4e6
    assert frame.center_hz == float(w_stubbed.spectrum.generator.center_freq_hz)


def test_pipeline_frame_feeds_adsb_list_when_visible(w_stubbed, monkeypatch):
    from types import SimpleNamespace
    fed = []

    class _FakeAdsb:
        def feed_iq(self, iq, sr):
            fed.append((iq, sr))

    monkeypatch.setattr(w_stubbed, "digital_adsb_panel", _FakeAdsb(),
                        raising=False)
    monkeypatch.setattr(w_stubbed, "adsb_dock",
                        SimpleNamespace(isVisible=lambda: True), raising=False)

    iq = _frame()
    w_stubbed._on_pipeline_frame_ready(iq, 2.4e6)
    assert len(fed) == 1 and fed[0][0] is iq and fed[0][1] == 2.4e6


def test_pipeline_frame_skips_adsb_list_when_hidden(w_stubbed, monkeypatch):
    from types import SimpleNamespace
    fed = []

    class _FakeAdsb:
        def feed_iq(self, iq, sr):
            fed.append((iq, sr))

    monkeypatch.setattr(w_stubbed, "digital_adsb_panel", _FakeAdsb(),
                        raising=False)
    monkeypatch.setattr(w_stubbed, "adsb_dock",
                        SimpleNamespace(isVisible=lambda: False), raising=False)
    w_stubbed._on_pipeline_frame_ready(_frame(), 2.4e6)
    assert fed == [], "页签不可见时不解码，零开销"


def test_source_guard_publish_is_in_frame_entry_not_only_in_spectrum(
        w_stubbed, monkeypatch):
    """帧入口必须同时发布 IQ 并喂 ADS-B 列表（旧版是源码文本 pin，已退役）。

    上面两条用例分别钉住 ``get_iq_bus().publish`` 与
    ``digital_adsb_panel.feed_iq``；这里在同一个真实帧入口上同时验两件事，
    防止有人把发布点挪回「只有 set_iq_data 才走」的路径。断言的是对象
    状态与调用记录，不是源码里有没有某串字符。
    """
    from types import SimpleNamespace

    from desktop.iq_bus import reset_iq_bus

    bus = reset_iq_bus()
    fed = []

    class _FakeAdsb:
        def feed_iq(self, iq, sr):
            fed.append((iq, sr))

    monkeypatch.setattr(w_stubbed, "digital_adsb_panel", _FakeAdsb(),
                        raising=False)
    monkeypatch.setattr(w_stubbed, "adsb_dock",
                        SimpleNamespace(isVisible=lambda: True), raising=False)
    iq = _frame()
    w_stubbed._on_pipeline_frame_ready(iq, 2.4e6)

    latest = bus.latest()
    assert latest is not None and latest.iq is iq, "帧入口没发布 IQ"
    assert len(fed) == 1 and fed[0][0] is iq and fed[0][1] == 2.4e6, (
        "帧入口没把同一帧喂给 ADS-B 列表")
