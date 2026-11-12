# SPDX-License-Identifier: MIT
"""设置页的 Tab 尾巴（2026-11-12，键盘普查「提示 16」）。

`settings_panel.py:1098-1101` 只定义了「搜索框 → 第一项」这一段；从**最后
一个设置项**按 Tab 出去落到哪，从来没有被钉住。本文件只钉现状、不改行为：
实测（offscreen，`SettingsPanel()` 未注入 settings、展开末组「数据与操作」）：

    ai_timeout 的输入框
      → 该行的「默认」按钮
      → 导出配置 → 导入配置 → 打开配置目录
      → 恢复全部默认
      → 搜索框（在面板内回环）

判据按**控件名字/文案**断言，不按 Tab 序号硬编码。落点仍在面板内（没有跳到
顶栏），因此不需要在注释里挂「待与第 1 条一起修」；但它确实把「恢复全部
默认」这颗破坏性按钮放在尾巴上——Tab 本身不触发，回车才会，保持现状、只钉住。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_settings_tab_tail.py -q
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QComboBox, QLineEdit, QPushButton, QToolButton,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture()
def panel(qapp):
    import settings_panel as sp

    p = sp.SettingsPanel(settings=None)
    p.resize(400, 900)
    p.show()
    qapp.processEvents()
    yield p
    p.deleteLater()
    qapp.processEvents()


def _tab_from(widget):
    """按一次 Tab，返回新的焦点控件。"""
    QTest.keyClick(widget, Qt.Key_Tab)
    QApplication.processEvents()
    return QApplication.focusWidget()


def _label(widget):
    """控件的身份：类型 + 文案（按钮/下拉/输入框各有取值口）。"""
    if widget is None:
        return "<None>"
    if isinstance(widget, (QPushButton, QToolButton)):
        return f"{type(widget).__name__}:{widget.text()}"
    if isinstance(widget, QComboBox):
        return f"QComboBox:{widget.currentText()}"
    if isinstance(widget, QLineEdit):
        return f"QLineEdit:{widget.placeholderText()}"
    return type(widget).__name__


def test_search_box_tabs_to_first_visible_control(panel):
    """既有契约：搜索框 → 第一项（按名字比，不写控件序号）。"""
    first = panel._first_visible_control()
    assert first is not None
    panel.search_edit.setFocus()
    QApplication.processEvents()
    assert _tab_from(panel.search_edit) is first


def test_tab_tail_of_last_setting_item(qapp, panel):
    """最后一个设置项 → … → 搜索框：整条尾巴的落点逐个钉住。"""
    title, section = panel.group_sections[-1]
    section.set_expanded(True)
    qapp.processEvents()

    last_key = panel._group_keys[title][-1]
    last_ctrl = panel._rows[last_key]
    assert last_ctrl is not None

    last_ctrl.setFocus()
    qapp.processEvents()
    assert QApplication.focusWidget() is last_ctrl

    chain = []
    fw = last_ctrl
    for _ in range(8):
        fw = _tab_from(fw)
        chain.append(_label(fw))
        if fw is panel.search_edit:
            break

    assert chain == [
        "QToolButton:默认",
        "QPushButton:导出配置",
        "QPushButton:导入配置",
        "QPushButton:打开配置目录",
        "QPushButton:恢复全部默认",
        f"QLineEdit:{panel.search_edit.placeholderText()}",
    ], f"设置页 Tab 尾巴变了：实际 {chain}"

    # 落点必须还在设置面板内（不能凭空跳到顶栏/别的面板）。
    assert fw is not None and panel.isAncestorOf(fw), (
        f"Tab 尾巴跑出了设置面板：{_label(fw)}")
