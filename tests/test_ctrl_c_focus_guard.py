#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Ctrl+C 焦点感知测试
==================

操作逻辑红线：全局快捷键不得劫持系统惯例。
「连接设备」绑在 Ctrl+C 上——焦点在文本输入控件时必须让位给标准「复制」，
焦点在其它控件（工具栏/画布/表格）时才触发连接。

运行: QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_ctrl_c_focus_guard.py -v
"""
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


class _FakeShortcutOverride:
    """最小 ShortcutOverride 事件替身。"""

    def __init__(self):
        from PySide6.QtCore import Qt
        self._mod = Qt.ControlModifier
        self._key = Qt.Key_C
        self.accepted = False

    def type(self):
        from PySide6.QtCore import QEvent
        return QEvent.ShortcutOverride

    def modifiers(self):
        return self._mod

    def key(self):
        return self._key

    def accept(self):
        self.accepted = True


def _stub_self():
    # _handle_shortcut_override 不依赖 QObject 初始化，最小 stub 即可。
    return SimpleNamespace()


def _filter_call(stub, event):
    from main_window import MainWindow
    return MainWindow._handle_shortcut_override(stub, event)


def test_is_text_entry_widget_classification(qapp):
    from PySide6.QtWidgets import (
        QComboBox, QLabel, QLineEdit, QPlainTextEdit, QSpinBox, QTextEdit, QWidget,
    )
    import main_window as mw

    assert mw._is_text_entry_widget(QLineEdit()) is True
    assert mw._is_text_entry_widget(QTextEdit()) is True
    assert mw._is_text_entry_widget(QPlainTextEdit()) is True
    assert mw._is_text_entry_widget(QSpinBox()) is True
    assert mw._is_text_entry_widget(QWidget()) is False
    assert mw._is_text_entry_widget(QLabel()) is False
    assert mw._is_text_entry_widget(None) is False
    combo_editable = QComboBox()
    combo_editable.setEditable(True)
    assert mw._is_text_entry_widget(combo_editable) is True
    combo_ro = QComboBox()
    assert mw._is_text_entry_widget(combo_ro) is False


def test_ctrl_c_goes_to_text_widget_not_global_action(qapp, monkeypatch):
    """焦点在 QLineEdit：ShortcutOverride 必须被接受 → 复制语义生效。"""
    from PySide6.QtWidgets import QLineEdit
    import main_window as mw

    line = QLineEdit()
    monkeypatch.setattr(mw, "_focus_widget", lambda: line)

    ev = _FakeShortcutOverride()
    ret = _filter_call(_stub_self(), ev)
    assert ev.accepted is True, "文本框内必须接受 ShortcutOverride（放行复制）"
    assert ret is True


def test_ctrl_c_still_triggers_action_outside_text(qapp, monkeypatch):
    """焦点在普通控件：ShortcutOverride 不得被接受 → 全局快捷键照常触发。"""
    from PySide6.QtWidgets import QWidget
    import main_window as mw

    plain = QWidget()
    monkeypatch.setattr(mw, "_focus_widget", lambda: plain)

    ev = _FakeShortcutOverride()
    ret = _filter_call(_stub_self(), ev)
    assert ev.accepted is False, "非文本控件上 Ctrl+C 必须留给全局快捷键"
    assert ret is False


def test_ctrl_c_when_no_focus(qapp, monkeypatch):
    """无焦点控件：不得接受，全局快捷键照常。"""
    import main_window as mw

    monkeypatch.setattr(mw, "_focus_widget", lambda: None)
    ev = _FakeShortcutOverride()
    ret = _filter_call(_stub_self(), ev)
    assert ev.accepted is False
    assert ret is False
