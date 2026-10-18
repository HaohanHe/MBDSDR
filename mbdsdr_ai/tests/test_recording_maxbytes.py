# SPDX-License-Identifier: MIT
"""录制 max_bytes 防爆盘软上限测试（doc04 §6 缺口销账）。

覆盖：到限按样本帧对齐自动收尾（不写半个样本）、sidecar bytes/samples
如实记录截断、无上限（max_bytes=0）时不截断。
"""
import json
import os
import sys
import time

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai.sdr_backend import SDRBackend, SDRDevice


class _FeedReader(SDRBackend):
    """恢复型 fake 后端：每次读返回固定幅度正弦，供录制环定义性测试。"""

    def __init__(self):
        super().__init__(SDRDevice(
            device_type="test_feed", device_id="test", name="test feed",
            frequency_range=(1e6, 2e9), sample_rate_range=(1e5, 2e6),
            max_gain=30.0))
        self.status.connected = True
        self.status.sample_rate_hz = 1_000_000
        self.status.frequency_hz = 100_000_000
        self._n = 0

    def read_samples(self, num_samples):
        self._n += num_samples
        t = np.arange(num_samples) / 1e6
        return (0.5 * np.exp(2j * np.pi * 1000 * t)).astype(np.complex64)


def _wait_stop(backend, timeout=10.0):
    t0 = time.time()
    while backend.status.recording and time.time() - t0 < timeout:
        time.sleep(0.02)
    assert not backend.status.recording, "recording did not stop in time"


def test_max_bytes_truncates_aligned(tmp_path):
    """max_bytes=16KB 时 cf32（8B/样本）写入不超上限，截断后 bytes 与 samples 自洽。"""
    b = _FeedReader()
    path = str(tmp_path / "cap.cf32")
    limit = 16 * 1024
    ok = b.start_recording(path, duration=0, fmt="cf32", max_bytes=limit)
    assert ok is True
    _wait_stop(b)
    size = os.path.getsize(path)
    assert size <= limit, f"size {size} exceeds max_bytes {limit}"
    assert size % 8 == 0, "must be sample-frame aligned"
    assert size > 0
    with open(path + ".json", encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["bytes"] == size
    assert meta["samples"] == size // 8


def test_max_bytes_zero_means_unlimited(tmp_path):
    """max_bytes=0 不截断：按时长停止，字节数等于样本数*8。"""
    b = _FeedReader()
    path = str(tmp_path / "nocap.cf32")
    ok = b.start_recording(path, duration=0.3, fmt="cf32", max_bytes=0)
    assert ok is True
    _wait_stop(b)
    size = os.path.getsize(path)
    with open(path + ".json", encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["bytes"] == size
    assert meta["samples"] * 8 == size
