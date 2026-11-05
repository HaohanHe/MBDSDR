# SPDX-License-Identifier: MIT
"""回放读盘后台化（P1）验收：GUI 不冻结、可取消、截断必说明。

对应 docs/pending-patches/2026-11-06-handoff-p1-replay-main-window.md §4/§5。
"""
import logging
import os
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

app = QApplication.instance() or QApplication(sys.argv)

from main_window import MainWindow, REPLAY_MAX_SAMPLES  # noqa: E402
import mbdsdr_ai.baseband_io as baseband_io  # noqa: E402

_log = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def window(isolated_profile):
    w = MainWindow()
    yield w
    try:
        w._stop_replay()
    except Exception as exc:  # noqa: BLE001
        _log.warning("用例收尾停止回放失败：%s: %s", type(exc).__name__, exc)


def _wait_until(pred, timeout_s=3.0):
    """跨线程 queued signal 要靠 processEvents 轮询（offscreen 下 singleShot(0) 不触发）。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.02)
    app.processEvents()
    return pred()


def _patch_replay_entry(w, monkeypatch, fake_load):
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: ("D:/fake/capture.iq", "")))
    monkeypatch.setattr(baseband_io, "load_iq", fake_load)
    monkeypatch.setattr(w, "_replay_tick", lambda: None)  # 别让 50ms 定时器收尾
    w._replay_iq = None
    w._replay_timer = None
    w._replay_load_thread = None


def test_replay_load_does_not_block_gui(window, monkeypatch):
    """读盘 0.6s 必须发生在后台：_replay_recording 立即返回（<0.2s）。"""
    w = window

    def _slow(path, **kw):
        prog = kw.get("progress")
        if prog:
            prog(0, 4)
        time.sleep(0.6)
        if prog:
            prog(4, 4)
        return {"iq": np.zeros(4096, np.complex64), "sample_rate": 2_400_000.0,
                "center_freq_hz": 100e6, "duration_s": 1.0,
                "file_samples": 4096, "samples": 4096}

    _patch_replay_entry(w, monkeypatch, _slow)
    t0 = time.monotonic()
    w._replay_recording()
    elapsed = time.monotonic() - t0
    assert elapsed < 0.2, f"读盘仍在 GUI 线程（{elapsed:.2f}s ≥ 0.6s 的同步读盘）"
    assert w._replay_load_thread is not None and w._replay_load_thread.is_alive()
    assert _wait_until(lambda: w._replay_iq is not None, 3.0), \
        "queued 信号没有把结果送回 GUI 线程"
    w._stop_replay()


def test_replay_load_cancel_keeps_recording(window, monkeypatch):
    """点「取消」→ 停止读盘、不进回放，且录音状态不受影响。"""
    w = window

    def _cancellable(path, **kw):
        should_cancel = kw["should_cancel"]
        for _ in range(40):          # 最多 2 s
            if should_cancel():
                return {"cancelled": True, "cancel_reason": "user_cancel"}
            time.sleep(0.05)
        return {"iq": np.zeros(4096, np.complex64), "sample_rate": 2_400_000.0,
                "center_freq_hz": 100e6, "duration_s": 1.0,
                "file_samples": 4096, "samples": 4096}

    _patch_replay_entry(w, monkeypatch, _cancellable)
    w._recording = False
    w._replay_recording()
    assert w._replay_progress_dialog is not None
    # 真实用户动作：点进度框的「取消」按钮（QProgressDialog.cancel() 在
    # offscreen 未显示时不一定发 canceled，按钮 click 才是真实路径）
    from PySide6.QtWidgets import QPushButton
    btn = w._replay_progress_dialog.findChild(QPushButton)
    assert btn is not None
    btn.click()
    assert _wait_until(lambda: w._replay_iq is None
                       and w._replay_load_thread is None, 3.0)
    assert w._replay_progress_dialog is None
    assert "已取消读取录音" in w.statusBar().currentMessage()
    assert w._recording is False, "取消读盘不得改动录音状态"
    assert w._replay_timer is None, "取消后不得起播"


def test_replay_truncation_is_announced_before_playback(window, monkeypatch):
    """超过 24M 样本：先弹「只载入前 N 个」说明，再起播；不许静默截断。"""
    w = window
    seen = {}

    def _big(path, **kw):
        limit = kw.get("max_samples")
        truncated = limit is not None and 30_000_000 > int(limit)
        seen["max_samples"] = limit
        return {"iq": np.zeros(4096, np.complex64), "sample_rate": 2_400_000.0,
                "center_freq_hz": 100e6, "duration_s": 12.5,
                "file_samples": 30_000_000, "samples": 4096,
                "truncated": truncated, "truncation_reason": "max_samples"}

    messages = []

    def _capture_info(parent, title, text, *a, **k):
        messages.append((title, text))

    _patch_replay_entry(w, monkeypatch, _big)
    monkeypatch.setattr(QMessageBox, "information", _capture_info)
    w._replay_recording()
    assert seen.get("max_samples") == REPLAY_MAX_SAMPLES, \
        "load_iq 必须带 max_samples=REPLAY_MAX_SAMPLES"
    assert _wait_until(lambda: messages, 3.0), "截断必须先弹说明再起播"
    title, text = messages[0]
    assert title == "回放说明"
    assert "只载入前" in text and f"{REPLAY_MAX_SAMPLES:,}" in text
    assert "30,000,000" in text
    w._stop_replay()
