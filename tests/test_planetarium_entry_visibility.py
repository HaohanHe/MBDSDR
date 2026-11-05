# SPDX-License-Identifier: MIT
"""UX-07/12：星象仪退出入口可读，录音指示只读状态不自起计时。"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QLabel, QPushButton  # noqa: E402


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def window(qtapp):
    import main_window as mw
    w = mw.MainWindow()
    w.resize(1280, 800)
    w.show()
    yield w, mw
    try:
        w._teardown_connection()
    except Exception:
        import logging
        logging.getLogger(__name__).exception("MainWindow teardown 失败")


def test_planetarium_exit_entry_is_readable(window):
    w, mw = window
    strip = w._planetarium_strip
    assert strip.width() >= 120, f"窄条必须够宽，实际: {strip.width()}"
    btn = strip.findChild(QPushButton, "planetariumExit")
    assert btn is not None, "退出按钮必须有稳定 objectName"
    text = btn.text()
    assert "退出星象仪" in text, f"按钮文案必须说人话，实际: {text}"
    assert "Esc" in text
    assert btn.minimumHeight() >= 44, "触控目标必须够高"
    assert btn.sizeHint().width() <= strip.width() - 8, (
        f"最小宽度下文本要完整显示，sizeHint={btn.sizeHint().width()}")


def test_planetarium_recording_indicator_reads_state(window):
    w, mw = window
    page = w._planetarium_page
    rec = page.findChild(QLabel, "planetariumRecIndicator")
    assert rec is not None, "星象仪必须有独立录音指示"
    assert rec.isHidden() is True, "未录音时指示必须隐藏"

    w._recording = True
    w._record_seconds = 65
    w._sync_planetarium_recording()
    assert rec.isHidden() is False
    assert "01:05" in rec.text()

    w._record_seconds = 66
    w._sync_planetarium_recording()
    assert "01:06" in rec.text(), "文本必须跟随 _record_seconds 变化"

    before = rec.text()
    time.sleep(0.3)
    assert rec.text() == before, "指示不得自起计时（必须只读状态）"

    w._recording = False
    w._sync_planetarium_recording()
    assert rec.isHidden() is True, "停止录音后必须隐藏"
