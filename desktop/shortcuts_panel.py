# SPDX-License-Identifier: MIT
"""
快捷键一览面板
================

键位**不在这里手抄**：全部来自 ``desktop/shortcuts.py``（唯一事实源），
本模块只负责分组、渲染与搜索。分组按 id 前缀映射到菜单分区，因此新增
快捷键后本面板自动出现；``key=None`` 的条目会如实显示「无快捷键」并给出
原因（原因同样来自 shortcuts.py 的 note）。

``tests/test_discoverability_help.py`` 钉死三件事：
  * 登记表里每一条都必须在面板里恰好出现一次；
  * 面板显示的键位串与 ``shortcuts.py`` 完全一致；
  * 面板没有任何一份独立维护的「键位清单」。
"""

from __future__ import annotations

import os
import sys
from typing import List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from shortcuts import SHORTCUTS, Shortcut  # noqa: E402

#: id 前缀 → 界面分区（顺序即面板分组顺序）
GROUP_BY_PREFIX: Tuple[Tuple[str, str], ...] = (
    ("file", "文件"),
    ("view", "视图"),
    ("tools", "工具"),
    ("help", "帮助"),
    ("panel", "面板"),
    ("layout", "顶栏"),
    ("vfo", "顶栏"),
)
GROUP_ORDER: Tuple[str, ...] = tuple(
    dict.fromkeys(g for _prefix, g in GROUP_BY_PREFIX))
_MENU_PATH = {
    "文件": "文件菜单", "视图": "视图菜单", "工具": "工具菜单",
    "帮助": "帮助菜单", "面板": "面板菜单", "顶栏": "顶栏按钮",
}


def group_of(shortcut_id: str) -> str:
    """按 id 前缀（域.动作）归组；未识别前缀归「其他」。"""
    prefix = shortcut_id.split(".", 1)[0]
    return dict(GROUP_BY_PREFIX).get(prefix, "其他")


def key_text(shortcut: Shortcut) -> str:
    """面板里显示的键位文本；未配键如实显示并保留原因。"""
    return shortcut.key if shortcut.key else "无快捷键"


def grouped_shortcuts(query: str = "") -> List[Tuple[str, List[Shortcut]]]:
    """按分区返回快捷键；query 非空时按 id/标签/键位/说明过滤。"""
    q = (query or "").strip().lower()
    groups: List[Tuple[str, List[Shortcut]]] = []
    for group in GROUP_ORDER:
        rows = [s for s in SHORTCUTS if group_of(s.id) == group]
        if q:
            rows = [s for s in rows
                    if q in (s.id + " " + s.label + " " + (s.key or "")
                             + " " + s.note).lower()]
        if rows:
            groups.append((group, rows))
    other = [s for s in SHORTCUTS if group_of(s.id) == "其他"]
    if q:
        other = [s for s in other
                 if q in (s.id + " " + s.label + " " + (s.key or "")
                          + " " + s.note).lower()]
    if other:
        groups.append(("其他", other))
    return groups


def shortcut_total() -> int:
    return len(SHORTCUTS)


def shortcut_bound_count() -> int:
    return sum(1 for s in SHORTCUTS if s.key)


def trigger_text(shortcut: Shortcut) -> str:
    """人类可读的触发路径（分区 + 标签），键位单独显示。"""
    group = group_of(shortcut.id)
    return f"{_MENU_PATH.get(group, group)} → {shortcut.label}"


def explain_text(shortcut: Shortcut) -> str:
    """说明列：有键时说明副作用/限制；无键时说明为什么不配键。"""
    if shortcut.key:
        return shortcut.note or "—"
    return shortcut.note or "未配快捷键"


# ---------------------------------------------------------------------------
# Qt 界面
# ---------------------------------------------------------------------------
def _build_dialog_class():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QTreeWidget,
        QTreeWidgetItem, QPushButton, QAbstractItemView,
    )

    class ShortcutsPanel(QDialog):
        """快捷键一览（非模态；内容实时来自 shortcuts.py）。"""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("快捷键一览")
            self.resize(720, 620)
            self.setModal(False)

            root = QVBoxLayout(self)
            head = QHBoxLayout()
            title = QLabel(
                f"共 {shortcut_total()} 项，其中 {shortcut_bound_count()} 项已配键；"
                "键位与菜单显示一致")
            title.setWordWrap(True)
            head.addWidget(title, stretch=1)
            root.addLayout(head)

            self.search_edit = QLineEdit(self)
            self.search_edit.setPlaceholderText(
                "搜索功能名/键位/说明（例如：录音、Ctrl+R、卫星）")
            self.search_edit.textChanged.connect(self._refresh)
            root.addWidget(self.search_edit)

            self.tree = QTreeWidget(self)
            self.tree.setColumnCount(3)
            self.tree.setHeaderLabels(["功能", "键位", "怎么触发 / 说明"])
            self.tree.setRootIsDecorated(True)
            self.tree.setSelectionMode(QAbstractItemView.NoSelection)
            self.tree.setFocusPolicy(Qt.NoFocus)
            root.addWidget(self.tree, stretch=1)

            self.empty_label = QLabel(self)
            self.empty_label.setWordWrap(True)
            self.empty_label.setText(
                "没有匹配的快捷键。换个关键词试试（例如「录音」「卫星」），"
                "或清空搜索框。")
            self.empty_label.setVisible(False)
            root.addWidget(self.empty_label)

            foot = QLabel(
                "键位来自 desktop/shortcuts.py（唯一事实源）；标「无快捷键」的"
                "项只能从菜单/按钮触发，原因写在说明列。")
            foot.setWordWrap(True)
            root.addWidget(foot)

            close_btn = QPushButton("关闭", self)
            close_btn.clicked.connect(self.close)
            root.addWidget(close_btn)

            self._refresh()

        def _refresh(self) -> None:
            self.tree.clear()
            groups = grouped_shortcuts(self.search_edit.text())
            self.empty_label.setVisible(not groups)
            self.tree.setVisible(bool(groups))
            for group, rows in groups:
                top = QTreeWidgetItem([f"{group}（{len(rows)}）", "", ""])
                self.tree.addTopLevelItem(top)
                for s in rows:
                    child = QTreeWidgetItem([
                        s.label, key_text(s),
                        f"{trigger_text(s)} · {explain_text(s)}"])
                    top.addChild(child)
                top.setExpanded(True)

    return ShortcutsPanel


def __getattr__(name: str):
    """PEP 562：``from shortcuts_panel import ShortcutsPanel`` 时才构造 Qt 类。"""
    if name == "ShortcutsPanel":
        return _build_dialog_class()
    raise AttributeError(name)
