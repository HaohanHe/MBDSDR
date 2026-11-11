# SPDX-License-Identifier: MIT
"""取消/失败不得写「录音完成」（R-02）。

三条路径：目录不可用、用户取消、写盘 ENOSPC。面板与状态栏都必须
「不含『完成』且含对应原因」。

反证：把 ``control_panel._on_record_toggled(False)`` 改回无条件写
「录音完成 (N 秒)」，或主窗口失败分支不回写面板，本文件全红。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from main_window import MainWindow  # noqa: E402
from settings import DesktopSettings  # noqa: E402


class _StubBackend:
    """只满足 _start_recording 前置检查的假后端。"""

    class _Status:
        gain_db = 0.0

    class _Device:
        name = "Stub SDR"

    def __init__(self):
        self.status = self._Status()
        self.device = self._Device()

    def read_samples(self, n):
        return None

    def get_sample_rate(self):
        return 1_000_000.0

    def get_frequency(self):
        return 100_000_000.0


@pytest.fixture(autouse=True)
def _pin_default_config(isolated_profile, monkeypatch):
    """任何仍走默认路径的 DesktopSettings 一并钉到本模块临时目录。"""
    monkeypatch.setattr(
        "settings.default_config_path",
        lambda: isolated_profile / "desktop_settings.json")


@pytest.fixture()
def window(isolated_profile):
    w = MainWindow()
    w.settings = DesktopSettings.load(
        str(isolated_profile / "desktop_settings.json"))
    w._active_sdr_backend = None
    w._recording = False
    yield w
    w._disconnect()
    w.close()


def _panel(w):
    return w.control_panel.record_status.text()


def _status(w):
    return w.statusBar().currentMessage()


def _assert_no_completion(w):
    assert "完成" not in _panel(w), f"面板仍是完成态: {_panel(w)!r}"
    assert "完成" not in _status(w), f"状态栏仍是完成态: {_status(w)!r}"


def test_dir_unavailable_is_failure_with_reason(window):
    window._active_sdr_backend = _StubBackend()
    window._confirm_record_dir_safe = lambda: True
    window._show_error_dialog = lambda *a, **k: None
    window.settings.ensure_record_dir = lambda: (
        None, "[Errno 13] Permission denied: 'D:\\rec'")

    window._start_recording()

    _assert_no_completion(window)
    assert "录音失败" in _panel(window)
    assert "Permission denied" in _panel(window)
    assert "Permission denied" in _status(window)
    assert window.record_btn.isChecked() is False
    assert window.control_panel.record_button.isChecked() is False


def test_user_cancel_is_cancelled_not_completed(window):
    window._active_sdr_backend = _StubBackend()
    window._confirm_record_dir_safe = lambda: False

    window._start_recording()

    _assert_no_completion(window)
    assert "已取消" in _panel(window)
    assert "取消" in _status(window)
    assert window.record_btn.isChecked() is False
    assert window.control_panel.record_button.isChecked() is False


class _FailAfter:
    """委托真实文件对象，但第 ``ok`` 次之后的 write 抛磁盘满。"""

    def __init__(self, fh, ok=0):
        self._fh = fh
        self._ok = int(ok)

    def write(self, data):
        if self._ok <= 0:
            raise OSError(28, "No space left on device")
        self._ok -= 1
        return self._fh.write(data)

    def close(self):
        self._fh.close()

    def flush(self):
        self._fh.flush()


def _start_segment(w, path, sr=1_000_000.0, center=100_000_000.0):
    w._record_session_path = str(path)
    w._record_segments = []
    w._record_segment_index = 1
    w._record_rate_changes = []
    w._record_seconds = 0
    w._record_gain_db = 0.0
    w._record_device = "Stub"
    w._record_driver = "StubBackend"
    w._record_start_time = "2026-11-11T00:00:00"
    w._record_timer = None
    ok = w._open_record_segment(str(path), sr, center, "2026-11-11T00:00:00", 1)
    if ok:
        w._recording = True
        for btn in (w.record_btn, w.control_panel.record_button):
            btn.blockSignals(True)
            btn.setChecked(True)
            btn.blockSignals(False)
    return ok


def test_enospc_is_failure_with_reason(window, tmp_path):
    assert _start_segment(window, tmp_path / "full.iq") is True
    window._record_write_chunk(np.ones(1024, dtype=np.complex64))
    window._record_stream_fh = _FailAfter(window._record_stream_fh, ok=0)

    window._record_write_chunk(np.ones(1024, dtype=np.complex64))

    _assert_no_completion(window)
    assert "录音失败" in _panel(window)
    assert "No space left on device" in _panel(window)
    assert "No space left on device" in _status(window)
