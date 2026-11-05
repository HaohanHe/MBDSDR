# SPDX-License-Identifier: MIT
"""UX-09/10/11：无设备横幅说人话、连接框无设备时 OK 禁用、刷新有进行中反馈。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QDialog, QDialogButtonBox, QLabel, QPushButton,
)


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def window(qtapp):
    import main_window as mw
    w = mw.MainWindow()
    yield w, mw
    try:
        w._teardown_connection()
    except Exception:
        import logging
        logging.getLogger(__name__).exception("MainWindow teardown 失败")


def _empty_enumerate(monkeypatch):
    import mbdsdr_ai.sdr_backend as sb
    monkeypatch.setattr(sb, "enumerate_all_sdr_devices", lambda: [])


def test_no_device_banner_is_plain_and_actionable(window, monkeypatch):
    w, mw = window
    monkeypatch.delenv("MBDSDR_OFFLINE", raising=False)
    _empty_enumerate(monkeypatch)
    w._active_sdr_backend = None

    w._auto_enumerate_and_connect()

    # 不换行是板子的决定：wordWrap 会随窗口宽度变成 2~3 行，把下方频谱
    # 压到 320x228（频谱基线 320x240，见 test_ui_layout_locks）。
    # 单行省略 + tooltip 的替代方案另案提交，不在本笔。
    assert w._no_device_hint.wordWrap() is False
    text = w._no_device_hint.text()
    assert "没找到 SDR 设备" in text, f"横幅必须直说没找到设备，实际: {text}"
    assert "可能原因" in text and "下一步" in text, f"横幅必须给原因和下一步，实际: {text}"
    assert "GPL" not in text, f"横幅不得堆术语，实际: {text}"


def test_connect_dialog_no_device_disables_ok(window, monkeypatch):
    w, mw = window
    _empty_enumerate(monkeypatch)
    created = []

    def fake_exec(dlg):
        created.append(dlg)
        return int(QDialog.Rejected)

    monkeypatch.setattr(mw.QDialog, "exec", fake_exec)

    w._connect_dialog()

    assert created, "连接框必须创建"
    dlg = created[0]
    box = dlg.findChild(QDialogButtonBox)
    assert box is not None
    assert box.button(QDialogButtonBox.Ok).isEnabled() is False, (
        "没有真实设备时 OK 必须禁用（rtl_tcp 仍可选，但不能是默认项）")
    lbl = dlg.findChild(QLabel, "deviceResultLabel")
    assert lbl is not None and "未发现 SDR 设备" in lbl.text(), (
        "必须就地显示检测结果")
    combo = dlg.findChild(mw.QComboBox)
    assert combo is not None and combo.currentData() is None, (
        "无设备时默认选中不可选占位项")
    assert combo.model().item(0).isEnabled() is False


def test_connect_dialog_refresh_reports_progress(window, monkeypatch):
    w, mw = window
    _empty_enumerate(monkeypatch)
    created = []
    state = {"probe": False, "inside": {}}

    def fake_exec(dlg):
        created.append(dlg)
        return int(QDialog.Rejected)

    monkeypatch.setattr(mw.QDialog, "exec", fake_exec)
    import mbdsdr_ai.sdr_backend as sb

    def slow_enumerate():
        if state["probe"]:
            dlg = created[0]
            refresh = state["refresh"]
            state["inside"]["refresh_enabled"] = refresh.isEnabled()
            state["inside"]["label"] = dlg.findChild(
                QLabel, "deviceResultLabel").text()
        return []

    # 必须在 _connect_dialog 之前打桩：刷新闭包绑定的是对话框创建时的函数。
    monkeypatch.setattr(sb, "enumerate_all_sdr_devices", slow_enumerate)
    w._connect_dialog()
    dlg = created[0]
    refresh = [b for b in dlg.findChildren(QPushButton)
               if "刷新" in b.text()][0]
    lbl = dlg.findChild(QLabel, "deviceResultLabel")
    assert lbl is not None
    state["refresh"] = refresh
    state["probe"] = True
    refresh.click()
    inside = state["inside"]

    assert inside["refresh_enabled"] is False, "枚举期间刷新必须禁用"
    assert "正在查找设备" in inside["label"], (
        f"枚举期间必须显示进行中，实际: {inside['label']}")
    assert refresh.isEnabled() is True, "枚举结束必须恢复刷新"
    assert "未发现 SDR 设备" in lbl.text()
