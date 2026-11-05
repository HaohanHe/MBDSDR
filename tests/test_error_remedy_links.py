# SPDX-License-Identifier: MIT
"""
错误码 → 「查看怎么办」入口守门
==============================

钉住的红线：
  * ``error_catalog`` 的每个 code 要么有帮助条目，要么显式登记不出入口的
    原因；新增 code 漏登记 → ``mapping_gaps()`` 非空（反证）；
  * 没有条目的 code 绝不出现链接（状态栏标签 / 弹窗 / open_remedy 都拒绝）；
  * 链接按 code 精确定位到「怎么办」条目，而不是模糊搜索或落到空页；
  * 弹窗只追加一行链接：不新增按钮、不改变默认焦点。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import error_remedy  # noqa: E402
from mbdsdr_ai.error_catalog import ERRORS  # noqa: E402
from troubleshooting import CURATED_CODES  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_every_catalog_code_is_registered():
    """每个 error_catalog code：有条目，或显式登记「不出入口 + 原因」。"""
    assert error_remedy.mapping_gaps() == []
    assert error_remedy.mapping_overlaps() == []
    assert set(error_remedy.REMEDY_BY_CODE) == set(CURATED_CODES)
    for code, reason in error_remedy.NO_ENTRY_REASONS.items():
        assert code in ERRORS, f"{code} 不在 error_catalog 里"
        assert reason.strip(), f"{code} 的「不出入口」原因为空"


def test_new_unregistered_code_makes_mapping_red(monkeypatch):
    """反证：error_catalog 新增一个没登记的 code，缺口必须非空。"""
    from mbdsdr_ai.error_catalog import ErrorSpec
    fake = ErrorSpec("brand_new_code", "ui", False, "新错误", "新错误", "无")
    monkeypatch.setitem(ERRORS, "brand_new_code", fake)
    assert "brand_new_code" in error_remedy.mapping_gaps()


def test_remedy_mapping_is_one_to_one():
    names = {}
    for code, remedy in error_remedy.REMEDY_BY_CODE.items():
        assert remedy.code == code
        assert remedy.symptom and remedy.cause and remedy.next_step
        name = error_remedy.entry_name(code)
        assert name
        assert name not in names, f"两个 code 映射到同一条目：{name}"
        names[name] = code


def test_codes_without_entry_have_no_link(qapp):
    """反证：没有条目的 code 不出现「查看怎么办」。"""
    code = "llm_rate_limited"  # 真实存在、但登记为不出入口
    assert error_remedy.remedy_for_code(code) is None
    assert error_remedy.no_entry_reason(code)

    label = error_remedy.build_link_label()
    assert label.set_code(code) is False
    assert label.isHidden()
    assert "查看怎么办" not in label.text()

    from PySide6.QtWidgets import QMessageBox, QLabel
    box = QMessageBox()
    assert error_remedy.attach_remedy_link(box, code) is None
    assert not any("查看怎么办" in lab.text() for lab in box.findChildren(QLabel))
    box.deleteLater()


def test_status_link_shows_and_opens_help_at_code(qapp, monkeypatch):
    from PySide6.QtWidgets import QStatusBar
    opened = []
    monkeypatch.setattr(error_remedy, "open_remedy",
                        lambda parent, code: opened.append(code) or True)

    bar = QStatusBar()
    label = error_remedy.build_link_label(bar)
    bar.addPermanentWidget(label)
    bar.showMessage("连接失败：设备被其它进程占用", 6000)
    assert label.set_code("device_busy") is True
    assert not label.isHidden()
    assert "查看怎么办" in label.text()

    label.linkActivated.emit("remedy:device_busy")
    assert opened == ["device_busy"]

    label.clear_code()
    assert label.isHidden()
    bar.deleteLater()


def test_dialog_link_keeps_buttons_and_default_focus(qapp):
    from PySide6.QtWidgets import QAbstractButton, QLabel, QMessageBox

    box = QMessageBox()
    box.setStandardButtons(QMessageBox.Ok)
    box.setDefaultButton(QMessageBox.Ok)
    buttons_before = list(box.findChildren(QAbstractButton))
    default_before = box.defaultButton()

    label = error_remedy.attach_remedy_link(box, "device_busy")
    assert label is not None
    assert "查看怎么办" in label.text()

    assert box.defaultButton() is default_before, "默认焦点被改变了"
    buttons_after = list(box.findChildren(QAbstractButton))
    assert len(buttons_after) == len(buttons_before), "多出了按钮"
    assert box.standardButtons() == QMessageBox.Ok
    assert any(lab is label for lab in box.findChildren(QLabel))

    # 同一弹窗再挂一个无条目 code：不应再出现第二条链接
    assert error_remedy.attach_remedy_link(box, "llm_timeout") is None
    box.deleteLater()


def test_help_panel_locates_entry_by_code(qapp):
    from help_panel import HelpPanel
    dlg = HelpPanel(initial_tab=2)
    assert dlg.show_remedy_code("device_busy") is True
    assert dlg.tabs.currentIndex() == 2
    current = dlg.trouble_tree.currentItem()
    assert current is not None
    assert current.data(0, 0x0100) == "device_busy"  # Qt.UserRole
    assert current.text(0) == ERRORS["device_busy"].message

    # 先搜一个别的词，定位时必须清掉过滤，否则条目被隐藏
    dlg.trouble_search_edit.setText("磁盘")
    assert dlg.show_remedy_code("empty_signal") is True
    assert dlg.trouble_search_edit.text() == ""

    assert dlg.show_remedy_code("llm_rate_limited") is False
    dlg.deleteLater()


def test_open_remedy_refuses_codes_without_entry(qapp):
    assert error_remedy.open_remedy(None, "llm_rate_limited") is False
    assert error_remedy.open_remedy(None, "") is False


def test_main_window_status_link_and_message_clear(qapp):
    """状态栏入口接到真实主窗口：普通消息一到，旧链接必须消失。"""
    from main_window import MainWindow

    win = MainWindow()
    win._show_error_status("连接失败：设备被其它进程占用", "device_busy", 6000)
    assert win._remedy_link.code() == "device_busy"
    assert not win._remedy_link.isHidden()
    assert "查看怎么办" in win._remedy_link.text()

    win.statusBar().showMessage("已连接", 1000)  # 普通瞬态消息
    assert win._remedy_link.code() == ""
    assert win._remedy_link.isHidden()

    # 无条目的 code：消息照常显示，但不出现链接
    win._show_error_status("模型限流，请稍后重试", "llm_rate_limited")
    assert win._remedy_link.isHidden()
    win.close()


def test_connect_and_record_dir_classification():
    assert error_remedy.classify_connect_error(
        "No module named 'SoapySDR'") == "missing_dependency"
    assert error_remedy.classify_connect_error(
        "[Errno 111] Connection refused") == "network_unreachable"
    assert error_remedy.classify_connect_error(
        "LIBUSB_ERROR_ACCESS device busy") == "device_busy"
    assert error_remedy.classify_connect_error(
        "No devices found") == "device_not_found"
    assert error_remedy.classify_connect_error(
        "librtlsdr.dll not found") == "driver_not_found"
    # 权限不足在 error_catalog 里没有独立条目 → 不出链接（None）
    assert error_remedy.classify_connect_error(
        "permission denied") is None

    assert error_remedy.classify_record_dir_error(
        "No space left on device") == "disk_full"
    assert error_remedy.classify_record_dir_error(
        "Permission denied") == "not_writable"
