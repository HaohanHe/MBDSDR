# SPDX-License-Identifier: MIT
"""自检：测试里弹真模态必须被 conftest 拦下且用例不挂。

背景：``QMessageBox.exec()`` 在 offscreen 下永不返回，两次把整条 Qt 互斥锁
占死（第一次 50 分钟，第二次是录音用例只打桩了 ``QMessageBox.warning``、
真实路径 ``_show_error_dialog -> box.exec()`` 没被覆盖）。

``tests/conftest.py`` 因此默认全局打桩 ``QDialog/QMessageBox/QFileDialog/
QInputDialog`` 的 ``exec()`` 与静态便捷方法：被调到就记一条「测试里出现真模态」
并立即返回取消/空路径；需要真模态的用例必须显式 ``@pytest.mark.allow_modal``。

本文件故意弹真模态，验证拦得住、记录得到、用例不挂。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_message_box_exec_is_blocked(qapp, modal_guard):
    """QMessageBox.exec() 必须立即返回 No，并记录一条真模态。"""
    from PySide6.QtWidgets import QMessageBox

    before = len(modal_guard["hits"])
    box = QMessageBox()
    box.setWindowTitle("自检")
    box.setText("这个弹窗必须被测试设施拦下，不许开事件循环")
    ret = box.exec()

    assert ret == QMessageBox.No, "真模态没有被拦下：exec() 没有立即返回 No"
    assert len(modal_guard["hits"]) == before + 1, (
        f"没有记录真模态调用，hits={modal_guard['hits'][before:]}")
    assert modal_guard["hits"][-1].startswith("QMessageBox"), (
        f"记录里没有 QMessageBox：{modal_guard['hits'][-1]}")


def test_dialog_exec_is_blocked(qapp, modal_guard):
    """任意 QDialog.exec() 同样被拦下，返回 Rejected。"""
    from PySide6.QtWidgets import QDialog

    before = len(modal_guard["hits"])
    dlg = QDialog()
    ret = dlg.exec()

    assert ret == QDialog.Rejected
    assert len(modal_guard["hits"]) == before + 1
    assert modal_guard["hits"][-1].startswith("QDialog")


def test_file_dialog_static_is_blocked(qapp, modal_guard):
    """QFileDialog 静态取路径方法返回空路径，不开模态。"""
    from PySide6.QtWidgets import QFileDialog

    before = len(modal_guard["hits"])
    path, selected_filter = QFileDialog.getOpenFileName(
        None, "自检", "", "All Files (*)")

    assert path == "" and selected_filter == "", (
        f"静态 QFileDialog 没有被拦下: {(path, selected_filter)!r}")
    assert len(modal_guard["hits"]) == before + 1
    assert modal_guard["hits"][-1].startswith("QFileDialog")
