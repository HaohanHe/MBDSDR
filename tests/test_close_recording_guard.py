# SPDX-License-Identifier: MIT
"""UX-04：录音中关窗必须先确认，保存失败不许关窗。

反证：修前 ``closeEvent`` 不看 ``_recording``，没有任何对话框，
``event.accept()`` 无条件执行；下面第一条断言（必须询问）与
``isAccepted() is False`` 必红。
"""
import logging
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtGui import QCloseEvent  # noqa: E402

_LOGGER = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def window(qtapp):
    import main_window as mw
    w = mw.MainWindow()
    yield w, mw
    try:
        w._teardown_connection()
    except Exception:
        _LOGGER.exception("MainWindow teardown 失败")


def _fake_exec_factory(boxes, answer):
    def fake_exec(box):
        boxes.append(box)
        return int(answer)
    return fake_exec


def test_close_while_recording_asks_and_cancel_keeps_recording(window, monkeypatch):
    w, mw = window
    boxes = []
    monkeypatch.setattr(
        mw.QMessageBox, "exec",
        _fake_exec_factory(boxes, mw.QMessageBox.Cancel))
    w._recording = True
    w._record_file_path = r"D:\mbdsdr\recordings\baseband_test.iq"

    event = QCloseEvent()
    w.closeEvent(event)

    assert boxes, "录音中关窗必须先询问，不能直接关掉"
    box = boxes[0]
    assert box.button(mw.QMessageBox.Save).text() == "保存录音并退出"
    assert box.button(mw.QMessageBox.Cancel).text() == "取消（继续录音）"
    assert box.defaultButton() is box.button(mw.QMessageBox.Cancel)
    assert "正在录音" in box.text()
    assert w._record_file_path in box.text(), "必须写明保存到哪个文件"
    assert w._recording is True, "取消后必须继续录音"
    assert event.isAccepted() is False, "取消后窗口必须保持打开"


def test_close_while_recording_save_then_exit(window, monkeypatch):
    w, mw = window
    boxes = []
    monkeypatch.setattr(
        mw.QMessageBox, "exec",
        _fake_exec_factory(boxes, mw.QMessageBox.Save))
    applied = []
    monkeypatch.setattr(w, "_apply_recording", lambda on: applied.append(on))
    closed = []
    monkeypatch.setattr(w, "_disconnect", lambda: closed.append("disconnect"))
    monkeypatch.setattr(w, "_release_runtime_resources", lambda: None)
    w._recording = True
    w._record_file_path = r"D:\mbdsdr\recordings\baseband_test.iq"

    event = QCloseEvent()
    w.closeEvent(event)

    assert applied == [False], "选择保存后必须真正停止并保存录音"
    assert closed == ["disconnect"], "保存成功后才继续退出流程"
    assert event.isAccepted() is True


def test_close_while_recording_save_failure_keeps_window(window, monkeypatch):
    w, mw = window
    boxes = []
    monkeypatch.setattr(
        mw.QMessageBox, "exec",
        _fake_exec_factory(boxes, mw.QMessageBox.Save))
    warnings = []
    monkeypatch.setattr(
        mw.QMessageBox, "warning",
        lambda *a, **k: warnings.append(a))
    monkeypatch.setattr(w, "_release_runtime_resources", lambda: None)

    def boom(_on):
        raise RuntimeError("disk full")

    monkeypatch.setattr(w, "_apply_recording", boom)
    w._recording = True
    w._record_file_path = r"D:\mbdsdr\recordings\baseband_test.iq"

    event = QCloseEvent()
    w.closeEvent(event)

    assert warnings, "保存失败必须弹三段式说明"
    assert warnings[0][1] == "录音保存失败"
    text = str(warnings[0][2])
    assert "可能原因" in text and "下一步" in text
    assert w._recording is True, "保存失败时不得假装录音已停"
    assert event.isAccepted() is False, "保存失败时不许关窗"
