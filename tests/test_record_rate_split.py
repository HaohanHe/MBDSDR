# SPDX-License-Identifier: MIT
"""
tests/test_record_rate_split.py
===============================
S4：录音中改采样率必须分段落盘，绝不把两种速率写进同一个 .iq。

守护：
  * 改率时旧段先收尾（sidecar 固定旧速率），新段用新速率；
  * 每个 sidecar 的 samples / sample_rate / duration_s 自洽；
  * 多段结束时每段 sidecar 都带 segments 清单，读取端可还原会话总时长；
  * 单段录音保持原行为（无 segments 清单）。
"""
import json
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))


class _Backend:
    def __init__(self, sr=1000000.0, freq=100000000.0):
        self.sr = sr
        self.freq = freq
        self.set_calls = []

    def get_sample_rate(self):
        return self.sr

    def set_sample_rate(self, rate):
        self.set_calls.append(float(rate))
        self.sr = float(rate)

    def get_frequency(self):
        return self.freq


class _Settings:
    def __init__(self):
        self.saved = {}

    def set(self, key, value):
        self.saved[key] = value

    def get(self, key, default=None):
        return self.saved.get(key, default)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture()
def window(qapp):
    from main_window import MainWindow
    w = MainWindow()
    w.settings = _Settings()
    w._active_sdr_backend = None
    w._recording = False
    w._stop_iq_streams = lambda: None
    w._start_iq_streams = lambda: None
    w._update_status_bar = lambda: None
    yield w
    w._disconnect()
    w.close()


def _prepare_recording(w, path, sr=1000000.0, center=100000000.0):
    w._recording = True
    w._record_session_path = str(path)
    w._record_segments = []
    w._record_segment_index = 1
    w._record_rate_changes = []
    w._record_seconds = 7
    w._record_gain_db = 0.0
    w._record_device = "Stub"
    w._record_driver = "StubBackend"
    w._record_start_time = "2026-11-05T00:00:00"
    w._open_record_segment(str(path), sr, center,
                           "2026-11-05T00:00:00", 1)


def test_rate_change_splits_segment_and_sidecars(window, tmp_path):
    first = tmp_path / "baseband_20261105_000000.iq"
    _prepare_recording(window, first)
    window._record_write_chunk(np.ones(1000, dtype=np.complex64))
    window._active_sdr_backend = _Backend()

    window._on_sample_rate_changed(2000000.0)

    second = tmp_path / "baseband_20261105_000000_part2.iq"
    assert second.exists(), "改率后应开新文件段"
    assert abs(window._record_sr - 2000000.0) < 1
    assert window._active_sdr_backend.set_calls == [2000000.0]

    meta1 = json.loads(first.with_suffix(".iq.json").read_text(encoding="utf-8"))
    assert meta1["sample_rate"] == 1000000.0
    assert meta1["samples"] == 1000
    assert meta1["duration_s"] == 0.001
    assert window._record_rate_changes[0]["from_sample_rate"] == 1000000.0
    assert window._record_rate_changes[0]["sample_rate"] == 2000000.0
    assert window._record_rate_changes[0]["start_sample"] == 1000

    window._record_write_chunk(np.ones(2000, dtype=np.complex64))
    window._stop_recording()

    meta1 = json.loads(first.with_suffix(".iq.json").read_text(encoding="utf-8"))
    meta2 = json.loads(second.with_suffix(".iq.json").read_text(encoding="utf-8"))
    assert meta1["segments"] == meta2["segments"]
    assert len(meta1["segments"]) == 2
    assert meta1["session_samples"] == 3000
    assert meta1["session_duration_s"] == 0.002
    assert meta2["sample_rate"] == 2000000.0
    assert meta2["samples"] == 2000
    assert meta2["duration_s"] == 0.001

    from mbdsdr_ai import baseband_io
    out = baseband_io.load_iq(str(first))
    assert out["duration_s"] == 0.001
    assert out["session_duration_s"] == 0.002
    assert out["mixed_sample_rates"] is True
    assert [s["path"] for s in out["segments"]] == [str(first), str(second)]


def test_single_segment_keeps_plain_sidecar(window, tmp_path):
    first = tmp_path / "baseband_20261105_000001.iq"
    _prepare_recording(window, first)
    window._record_write_chunk(np.ones(2000, dtype=np.complex64))
    window._stop_recording()

    meta = json.loads(first.with_suffix(".iq.json").read_text(encoding="utf-8"))
    assert meta["sample_rate"] == 1000000.0
    assert meta["samples"] == 2000
    assert meta["duration_s"] == 0.002
    assert "segments" not in meta, "单段录音不应伪造分段清单"
