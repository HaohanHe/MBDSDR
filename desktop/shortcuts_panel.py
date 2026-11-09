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
from typing import List, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from shortcuts import (  # noqa: E402
    BY_ID, SHORTCUTS, Shortcut, bind, clear_all_overrides, clear_override,
    conflicts, is_overridden, load_overrides, set_override,
)

#: id 前缀 → 界面分区（顺序即面板分组顺序）
GROUP_BY_PREFIX: Tuple[Tuple[str, str], ...] = (
    ("file", "文件"),
    ("view", "视图"),
    ("tools", "工具"),
    ("help", "帮助"),
    ("panel", "面板"),
    ("layout", "顶栏"),
    ("vfo", "顶栏"),
    ("spectrum", "频谱视图（焦点内）"),
)
GROUP_ORDER: Tuple[str, ...] = tuple(
    dict.fromkeys(g for _prefix, g in GROUP_BY_PREFIX))
_MENU_PATH = {
    "文件": "文件菜单", "视图": "视图菜单", "工具": "工具菜单",
    "帮助": "帮助菜单", "面板": "面板菜单", "顶栏": "顶栏按钮",
    "频谱视图（焦点内）": "频谱控件获焦时",
}


def group_of(shortcut_id: str) -> str:
    """按 id 前缀（域.动作）归组；未识别前缀归「其他」。"""
    prefix = shortcut_id.split(".", 1)[0]
    return dict(GROUP_BY_PREFIX).get(prefix, "其他")


def key_text(shortcut: Shortcut) -> str:
    """面板里显示的键位文本：**当前生效**键位（用户改过就显示改后的）。

    未配键如实显示「无快捷键」；被用户主动解绑的条目也一样（改键界面里
    解绑等于「这条不要快捷键」，与出厂就没配是同一件事，界面上不区分）。
    """
    current = bind(shortcut.id)
    return current if current else "无快捷键"


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
    from PySide6.QtCore import Qt, Signal
    from PySide6.QtGui import QKeySequence
    from PySide6.QtWidgets import (
        QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QTreeWidget,
        QTreeWidgetItem, QPushButton, QAbstractItemView, QMessageBox,
    )

    class ShortcutsPanel(QDialog):
        """快捷键一览 + 改键（非模态；键位与覆写都来自 shortcuts.py）。"""

        #: 用户改完键后发出：主窗接住并立刻重设键位（不重启生效）
        shortcuts_changed = Signal()

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
            # 可选中才能改键：选中一行 → 底部「改键…」只改这一条。
            # 只允许选单选，避免「一次改多条」时冲突判断说不清。
            self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
            self.tree.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.tree.setFocusPolicy(Qt.StrongFocus)
            self.tree.itemSelectionChanged.connect(self._sync_buttons)
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

            edit_row = QHBoxLayout()
            self.edit_btn = QPushButton("改键…", self)
            self.edit_btn.setToolTip(
                "给选中的这一条换键。星象仪的 Esc 退出键不允许改（那是退出路径）")
            self.edit_btn.clicked.connect(self._on_edit)
            edit_row.addWidget(self.edit_btn)
            self.reset_btn = QPushButton("恢复默认", self)
            self.reset_btn.setToolTip("把选中的这一条恢复成出厂键位")
            self.reset_btn.clicked.connect(self._on_reset)
            edit_row.addWidget(self.reset_btn)
            self.reset_all_btn = QPushButton("全部恢复默认", self)
            self.reset_all_btn.setToolTip("清掉所有自定义键位，回到出厂设置")
            self.reset_all_btn.clicked.connect(self._on_reset_all)
            edit_row.addWidget(self.reset_all_btn)
            edit_row.addStretch(1)
            root.addLayout(edit_row)

            close_btn = QPushButton("关闭", self)
            close_btn.clicked.connect(self.close)
            root.addWidget(close_btn)

            self._refresh()
            self._sync_buttons()
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
                    # id 挂在行上：改键时按 id 精确定位，不靠标签反查
                    child.setData(0, Qt.UserRole, s.id)
                    if is_overridden(s.id):
                        child.setToolTip(
                            1, f"已自定义：{key_text(s)}（出厂是 "
                               f"{s.key or '无快捷键'}）")
                    top.addChild(child)
                top.setExpanded(True)
            self._sync_buttons()

        # ---- 改键 ------------------------------------------------------
        def _selected_id(self) -> str:
            items = self.tree.selectedItems()
            if not items:
                return ""
            return str(items[0].data(0, Qt.UserRole) or "")

        def _sync_buttons(self) -> None:
            sid = self._selected_id()
            is_group_row = bool(self.tree.selectedItems()) and not sid
            # 分组标题行没有 id：改键/恢复对它没有意义，直接禁掉
            self.edit_btn.setEnabled(bool(sid) and sid != "view.planetarium_exit")
            self.reset_btn.setEnabled(bool(sid) and is_overridden(sid))
            self.reset_all_btn.setEnabled(any(is_overridden(s.id) for s in SHORTCUTS))
            if is_group_row:
                self.edit_btn.setToolTip("先选一条具体功能（分组标题不能改键）")
            elif sid == "view.planetarium_exit":
                self.edit_btn.setToolTip(
                    "星象仪的 Esc 退出键不允许改：那是唯一的退出路径，"
                    "改成别的键会让用户被困在星象仪里")
            else:
                self.edit_btn.setToolTip(
                    "给选中的这一条换键。星象仪的 Esc 退出键不允许改")

        def _on_edit(self) -> None:
            sid = self._selected_id()
            if not sid:
                return
            entry = BY_ID[sid]
            from PySide6.QtWidgets import QDialog, QDialogButtonBox, QKeySequenceEdit
            dlg = QDialog(self)
            dlg.setWindowTitle(f"改键 · {entry.label}")
            lay = QVBoxLayout(dlg)
            tip = QLabel(
                f"当前生效：{key_text(entry)}\n出厂键位：{entry.key or '无快捷键'}\n"
                "按下组合键；清空＝恢复出厂键位。", dlg)
            tip.setWordWrap(True)
            lay.addWidget(tip)
            edit = QKeySequenceEdit(dlg)
            edit.setKeySequence(QKeySequence(bind(sid) or ""))
            lay.addWidget(edit)
            box = QDialogButtonBox(
                QDialogButtonBox.Save | QDialogButtonBox.Cancel, dlg)
            box.accepted.connect(dlg.accept)
            box.rejected.connect(dlg.reject)
            lay.addWidget(box)
            if dlg.exec() != QDialog.Accepted:
                return
            seq = edit.keySequence().toString()
            if seq and seq == entry.key:
                # 改回出厂值 = 删覆写，不要留一条「等于默认」的覆写记录
                clear_override(sid)
                self._after_change()
                return
            # 同 scope 冲突检查：跨 scope 同键是设计允许的（焦点在哪局部优先），
            # 同 scope 撞键会让其中一个永远按不出来，必须挡住。
            trial = dict(load_overrides())
            if seq:
                trial[sid] = seq
            else:
                trial.pop(sid, None)
            clash = [c for c in conflicts(trial) if sid in (c[2], c[3])]
            if clash:
                scope, key, a_id, b_id = clash[0]
                other = b_id if a_id == sid else a_id
                QMessageBox.warning(
                    self, "键位冲突",
                    f"{key} 已被「{BY_ID[other].label}」占用（同样是 "
                    f"{scope} 范围）。\n两者都按不出来，换一个组合键，"
                    "或先把那一条恢复默认。")
                return
            set_override(sid, seq)
            self._after_change()

        def _on_reset(self) -> None:
            sid = self._selected_id()
            if not sid:
                return
            clear_override(sid)
            self._after_change()

        def _on_reset_all(self) -> None:
            clear_all_overrides()
            self._after_change()

        def _after_change(self) -> None:
            """保存后立刻刷新列表并通知主窗重设键位（不重启就生效）。"""
            self._refresh()
            self.shortcuts_changed.emit()

    return ShortcutsPanel


def __getattr__(name: str):
    """PEP 562：``from shortcuts_panel import ShortcutsPanel`` 时才构造 Qt 类。"""
    if name == "ShortcutsPanel":
        return _build_dialog_class()
    raise AttributeError(name)
