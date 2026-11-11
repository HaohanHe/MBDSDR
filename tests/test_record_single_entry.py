# SPDX-License-Identifier: MIT
"""录音开关单一入口：一次用户动作只执行一次 _start_recording（R-02B）。

此前工具栏点击会先经 control_panel.record_toggled 执行一次，再由
_toggle_record 兜底执行一次；成功靠 _apply_recording 幂等掩盖，失败时
两次都真跑——弹窗弹两次。

反证：删掉 _on_record_toggled 里的 ``_record_guard`` 早退，失败用例的
``calls``/``dialogs`` 都会变成 2。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from main_window import MainWindow  # noqa: E402
from settings import DesktopSettings  # noqa: E402


@pytest.fixture()
def window(isolated_profile):
    w = MainWindow()
    w.settings = DesktopSettings.load(
        str(isolated_profile / "desktop_settings.json"))
    w._active_sdr_backend = None
    w._recording = False
    w._worker = None
    yield w
    w._disconnect()
    w.close()


@pytest.fixture()
def dialogs(monkeypatch):
    seen = []

    def fake_warning(parent, title, text, *a, **k):
        seen.append(title)
        return QMessageBox.Ok

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(fake_warning))
    return seen


def _spy_start(w):
    calls = []
    orig = w._start_recording

    def spy():
        calls.append(1)
        return orig()

    w._start_recording = spy
    return calls


def test_toolbar_click_success_starts_once(window, dialogs):
    calls = _spy_start(window)

    def fake_start():
        calls.append(1)
        window._recording = True

    window._start_recording = fake_start
    window._toggle_record()

    assert len(calls) == 1, f"_start_recording 执行了 {len(calls)} 次"
    assert dialogs == []
    assert window.record_btn.isChecked() is True


def test_toolbar_click_failure_starts_and_dialogs_once(window, dialogs):
    calls = _spy_start(window)
    window._active_sdr_backend = None

    window._toggle_record()

    assert len(calls) == 1, f"_start_recording 执行了 {len(calls)} 次"
    assert len(dialogs) == 1, f"弹窗弹了 {len(dialogs)} 次"
    assert window._recording is False
    assert window.record_btn.isChecked() is False
    assert window.control_panel.record_button.isChecked() is False


def test_panel_click_success_starts_once(window, dialogs):
    calls = _spy_start(window)

    def fake_start():
        calls.append(1)
        window._recording = True

    window._start_recording = fake_start
    window.control_panel.record_button.setChecked(True)

    assert len(calls) == 1, f"_start_recording 执行了 {len(calls)} 次"
    assert dialogs == []


def test_panel_click_failure_starts_and_dialogs_once(window, dialogs):
    calls = _spy_start(window)

    window.control_panel.record_button.setChecked(True)

    assert len(calls) == 1, f"_start_recording 执行了 {len(calls)} 次"
    assert len(dialogs) == 1, f"弹窗弹了 {len(dialogs)} 次"
    assert window._recording is False
    assert window.record_btn.isChecked() is False
