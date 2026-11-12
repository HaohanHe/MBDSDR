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
    # 精选 15 条必须仍在；其余是「桌面 UI 真会渲染 code 或目录 message」的可见码。
    assert set(CURATED_CODES) <= set(error_remedy.REMEDY_BY_CODE)
    for code, reason in error_remedy.NO_ENTRY_REASONS.items():
        assert code in ERRORS, f"{code} 不在 error_catalog 里"
        assert reason.strip(), f"{code} 的「不出入口」原因为空"


def test_new_unregistered_code_makes_mapping_red(monkeypatch):
    """反证：error_catalog 新增一个没登记的 code，缺口必须非空。"""
    from mbdsdr_ai.error_catalog import ErrorSpec
    fake = ErrorSpec("brand_new_code", "ui", False, "新错误", "新错误", "无")
    monkeypatch.setitem(ERRORS, "brand_new_code", fake)
    assert "brand_new_code" in error_remedy.mapping_gaps()


def test_deleting_an_existing_entry_makes_mapping_red(monkeypatch):
    """反证：删掉任一条目而目录 code 仍在 → mapping_gaps() 立刻点名。

    这是「删条目必须红」的直接自检，不依赖新增 code 的间接推导：
    REMEDY_BY_CODE 少一条、NO_ENTRY_REASONS 也没登记，就会被
    test_every_catalog_code_is_registered 的 ``== []`` 判红。实测删
    ``device_busy`` 后 gaps 返回 ``['device_busy']``。
    """
    code = "device_busy"
    assert code in error_remedy.REMEDY_BY_CODE
    assert code not in error_remedy.NO_ENTRY_REASONS
    monkeypatch.delitem(error_remedy.REMEDY_BY_CODE, code)
    assert error_remedy.mapping_gaps() == [code]


def test_remedy_mapping_is_one_to_one():
    names = {}
    for code, remedy in error_remedy.REMEDY_BY_CODE.items():
        assert remedy.code == code
        assert remedy.symptom and remedy.cause and remedy.next_step
        name = error_remedy.entry_name(code)
        assert name
        assert name not in names, f"两个 code 映射到同一条目：{name}"
        names[name] = code


def test_visible_catalog_entries_copy_message_and_meaning_verbatim():
    """可见码新增条目：现象/原因必须逐字取目录，下一步必须点到控件。"""
    from troubleshooting import (
        VISIBLE_CODES, build_remedies, validate_remedies,
    )
    for code in VISIBLE_CODES:
        remedy = error_remedy.remedy_for_code(code)
        assert remedy is not None, f"{code} 登记为可见却没有条目"
        assert remedy.symptom == ERRORS[code].message
        assert remedy.cause == ERRORS[code].meaning
    assert validate_remedies(build_remedies()) == []


def test_message_channel_codes_are_remedied():
    """状态栏/AI 对话显示目录 message 原文的码，必须有「怎么办」条目。

    这些 code 用户看不到字面量，但看得到 message 原文（同样算可见）：
      * 设备调谐：sdr_backend.status.error → main_window._handle_backend_lost
        → 状态栏「设备已断开：{message}」；
      * AI 模型：model_manager 返回 error=user_message(code) → ai_panel
        「[错误] {error}」；
      * 工具结果：tool_registry 用 user_message("missing_error_detail") 兜底
        正文 → 步骤卡摘要。
    删掉任一族都会让这些码退回「没人解释的错」，本用例据此判红。
    """
    from troubleshooting import VISIBLE_BY_FAMILY
    for group in ("设备调谐", "AI 模型", "工具结果"):
        for code in VISIBLE_BY_FAMILY[group]:
            assert error_remedy.remedy_for_code(code) is not None, (
                f"{group}/{code} 的 message 会显示给用户，却没有「怎么办」条目")
            assert code not in error_remedy.NO_ENTRY_REASONS, (
                f"{code} 同时登记为可见与不出入口")


def test_codes_without_entry_have_no_link(qapp):
    """反证：没有条目的 code 不出现「查看怎么办」。"""
    code = "bad_command"  # 真实存在、但登记为不出入口（仅 Web 服务端返回值）
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


def test_decoder_not_streamable_remedy_is_actionable():
    """P0-03 新增的 decoder_not_streamable 必须有可点的下一步。

    AI 步骤卡失败时直接显示 ``错误 {code}``，用户看到码却无条目 = 看得见
    的错没人解释。下一步必须点到真控件：顶栏「回放」、以及仅有的两个
    流式入口面板「ADS-B 航路」「APRS 数据包」。
    """
    from troubleshooting import CODE_NEXT_STEPS, build_remedies, validate_remedies
    code = "decoder_not_streamable"
    remedy = error_remedy.remedy_for_code(code)
    assert remedy is not None, "AI 步骤卡会显示该 code，却没有「怎么办」条目"
    assert remedy.symptom == ERRORS[code].message
    assert remedy.cause == ERRORS[code].meaning
    assert remedy.next_step == CODE_NEXT_STEPS[code]
    for ref in ("「回放」", "「ADS-B 航路」", "「APRS 数据包」"):
        assert ref in remedy.next_step, f"下一步没点到 {ref}：{remedy.next_step}"
    assert validate_remedies([remedy]) == []
    assert any(r.code == code for r in build_remedies())


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


def test_status_link_is_keyboard_reachable_and_enter_opens(qapp, monkeypatch):
    """红线：不碰鼠标也能打开「查看怎么办」。

    改回 ``Qt.LinksAccessibleByMouse`` 时第一条断言红（链接不在 Tab 链上 /
    不认键盘）；删掉 ``keyPressEvent`` 里的 Enter 分支时第二条红（回车没反应）。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QStatusBar

    opened = []
    monkeypatch.setattr(error_remedy, "open_remedy",
                        lambda parent, code: opened.append(code) or True)

    bar = QStatusBar()
    label = error_remedy.build_link_label(bar)
    bar.addPermanentWidget(label)
    bar.show()
    assert label.set_code("device_busy") is True
    qapp.processEvents()

    # 1) 键盘可达：链接声明键盘可激活，标签本身能进 Tab 链
    assert label.textInteractionFlags() & Qt.LinksAccessibleByKeyboard, \
        "链接只允许鼠标：键盘用户看得见却点不开"
    assert label.focusPolicy() & Qt.TabFocus, "标签不在 Tab 链上"

    # 2) 回车 = 点击：焦点落到链接后按 Return 必须触发
    label.setFocus(Qt.OtherFocusReason)
    qapp.processEvents()
    assert bar.focusWidget() is label, "焦点没落到链接上"
    QTest.keyClick(label, Qt.Key_Return)
    qapp.processEvents()
    assert opened == ["device_busy"], f"回车没打开「怎么办」: {opened}"

    # 没有条目的 code 不得凭回车凭空打开
    label.clear_code()
    QTest.keyClick(label, Qt.Key_Return)
    qapp.processEvents()
    assert opened == ["device_busy"]
    bar.deleteLater()


def test_diagnostics_link_keyboard_and_click(qapp):
    """「查看诊断」与「查看怎么办」共用一套键盘路径：Tab 可达、回车即开。"""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QStatusBar

    opened = []
    bar = QStatusBar()
    label = error_remedy.build_diagnostics_link(bar)
    bar.addPermanentWidget(label)
    bar.show()
    assert label.isHidden()

    label.set_diagnostics(lambda: opened.append("open"))
    qapp.processEvents()
    assert not label.isHidden()
    assert "查看诊断" in label.text()
    assert label.textInteractionFlags() & Qt.LinksAccessibleByKeyboard
    assert label.focusPolicy() & Qt.TabFocus

    label.setFocus(Qt.OtherFocusReason)
    qapp.processEvents()
    QTest.keyClick(label, Qt.Key_Return)
    qapp.processEvents()
    assert opened == ["open"], f"回车没打开诊断页: {opened}"

    label.linkActivated.emit("diagnostics:open")
    assert opened == ["open", "open"], "鼠标点击路径没打开诊断页"
    bar.deleteLater()


def test_persistence_error_status_offers_diagnostics(
        qapp, isolated_profile, monkeypatch):
    """持久化错误 → 状态栏 + 「查看诊断」；普通消息一到链接就消失。"""
    from main_window import MainWindow

    win = MainWindow()
    opened = []
    monkeypatch.setattr(win, "_show_diagnostics_panel",
                        lambda: opened.append(True))

    win._show_persistence_error_status("收藏写入失败：磁盘已满", 6000)
    assert not win._diagnostics_link.isHidden(), "持久化错误没有诊断入口"
    assert "查看诊断" in win._diagnostics_link.text()
    win._diagnostics_link.linkActivated.emit("diagnostics:open")
    assert opened == [True], "链接没有打开诊断页"

    win.statusBar().showMessage("已连接", 1000)
    assert win._diagnostics_link.isHidden(), "普通消息没有清掉旧链接"
    win.close()


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
    assert error_remedy.attach_remedy_link(box, "bad_command") is None
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

    assert dlg.show_remedy_code("bad_command") is False
    dlg.deleteLater()


def test_open_remedy_refuses_codes_without_entry(qapp):
    assert error_remedy.open_remedy(None, "bad_command") is False
    assert error_remedy.open_remedy(None, "") is False


def test_main_window_status_link_and_message_clear(qapp, isolated_profile):
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
    win._show_error_status("模型限流，请稍后重试", "bad_command")
    assert win._remedy_link.isHidden()
    win.close()


def test_connect_and_record_dir_classification():
    assert error_remedy.classify_connect_error(
        "No module named 'SoapySDR'") == "missing_dependency"
    assert error_remedy.classify_connect_error(
        "[Errno 111] Connection refused") == "network_unreachable"
    # EACCES（libusb ACCESS / errno 13）优先于同串里的泛化 "busy"：
    # 权限不足要改系统设置，判成占用会让用户去关别人的程序。
    assert error_remedy.classify_connect_error(
        "LIBUSB_ERROR_ACCESS device busy") == "usb_permission"
    assert error_remedy.classify_connect_error(
        "Resource busy: device is used by another program") == "device_busy"
    assert error_remedy.classify_connect_error(
        "No devices found") == "device_not_found"
    assert error_remedy.classify_connect_error(
        "librtlsdr.dll not found") == "driver_not_found"
    # 权限不足现在有独立条目，补救链接指向 udev/WinUSB 而不是「设备被占用」
    assert error_remedy.classify_connect_error(
        "permission denied") == "usb_permission"

    assert error_remedy.classify_record_dir_error(
        "No space left on device") == "disk_full"
    assert error_remedy.classify_record_dir_error(
        "Permission denied") == "not_writable"
