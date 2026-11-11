# SPDX-License-Identifier: MIT
"""
帮助与能力目录面板
====================

三页，一页能看完：

  * **三步上手**：连接设备 → 看频谱/听声音 → 打开星象仪模式。步骤里的键位
    从 ``shortcuts.py`` 取，不手抄；没有硬件时明确说明「不会伪造信号」，
    不演示假数据。
  * **能力目录**：可搜索的分类列表，内容由 ``desktop/capability_catalog.py``
    从真实注册表（面板/解调/数字解码/长尾解码器/AI 工具域/快捷键）实时生成；
    触发不了的条目如实标注「暂不可用」并写原因。
  * **怎么办**：现象/可能原因/下一步，条目逐字取自 ``mbdsdr_ai/error_catalog``
    的真实错误条目（见 ``desktop/troubleshooting.py``），下一步必须点到
    具体控件名或设置项键名，空话会被测试拦红。

面板默认非模态、只在用户主动点菜单时出现，不遮挡主界面操作。
"""

from __future__ import annotations

import os
import sys
from typing import List

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from shortcuts import bind as _key  # noqa: E402
from capability_catalog import (  # noqa: E402
    CATEGORIES, Capability, build_catalog, catalog_stats, search_capabilities,
)
from troubleshooting import build_remedies, search_remedies  # noqa: E402

#: 三步上手：键位一律现取 shortcuts.py；步骤顺序是产品约定。
ONBOARDING_STEPS = (
    ("file.connect", "连接设备",
     "点顶栏「连接」或按 {key}。软件只枚举真实设备；没有设备就显示未连接，"
     "不会伪造频谱或信号。"),
    (None, "看频谱、听声音",
     "在「频谱」页签拖动谱面改频、滚轮缩放；在顶栏「解调」下拉"
     "（FM / WFM / NFM / AM / USB / LSB / CW）里选解调方式，音量也在顶栏。"
     "只有收到真实信号才会出声。"),
    ("view.planetarium", "打开星象仪模式",
     "点左栏「星象仪模式」页签，或菜单「视图 → 星象仪模式」，也可按 {key}。"
     "顶部搜索框输入 ISS / Vega / "
     "M42，选中后会居中并弹出信息卡。"),
)


def onboarding_steps() -> List[dict]:
    """返回三步上手文案（键位从 shortcuts.py 取，取不到就抛 KeyError）。"""
    steps = []
    for sid, title, body in ONBOARDING_STEPS:
        key = _key(sid) if sid else ""
        steps.append({
            "shortcut_id": sid or "",
            "title": title,
            "body": body.format(key=key or "对应快捷键") if key else body,
        })
    return steps


def _status_suffix(cap: Capability) -> str:
    if cap.status == "unavailable":
        return "（暂不可用）"
    if cap.status == "conditional":
        return "（需条件）"
    return ""


def _build_dialog_class():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
        QTabWidget, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
        QWidget, QAbstractItemView,
    )

    class HelpPanel(QDialog):
        """帮助与能力目录（非模态）。"""

        def __init__(self, parent=None, initial_tab: int = 0):
            super().__init__(parent)
            self.setWindowTitle("帮助与能力目录")
            self.resize(900, 640)
            self.setModal(False)
            self._catalog = build_catalog()
            self._remedies = build_remedies()

            root = QVBoxLayout(self)
            self.tabs = QTabWidget(self)
            root.addWidget(self.tabs, stretch=1)

            self.tabs.addTab(self._build_quick_start(), "三步上手")
            self.tabs.addTab(self._build_catalog_tab(), "能力目录")
            self.tabs.addTab(self._build_troubleshooting_tab(), "怎么办")
            if initial_tab:
                self.tabs.setCurrentIndex(min(initial_tab, self.tabs.count() - 1))

            close_btn = QPushButton("关闭", self)
            close_btn.clicked.connect(self.close)
            root.addWidget(close_btn)
            self._refresh_catalog()
            self._refresh_troubleshooting()

        # ---------------- 三步上手 ----------------
        def _build_quick_start(self) -> QWidget:
            page = QWidget(self)
            lay = QVBoxLayout(page)
            html = ["<h3>三步上手（全程真实数据，不演示假信号）</h3>"]
            for i, step in enumerate(onboarding_steps(), 1):
                html.append(
                    f"<p><b>{i}. {step['title']}</b><br>{step['body']}</p>")
            html.append(
                "<p><b>没有硬件也能先看</b>：星象仪模式、能力目录、快捷键一览"
                "不依赖设备；频谱/音频没有数据时会如实显示空态。</p>")
            browser = QTextBrowser(page)
            browser.setOpenExternalLinks(False)
            browser.setHtml("".join(html))
            lay.addWidget(browser, stretch=1)

            row = QHBoxLayout()
            to_catalog = QPushButton("打开能力目录", page)
            to_catalog.clicked.connect(lambda: self.tabs.setCurrentIndex(1))
            to_shortcuts = QPushButton("查看快捷键一览", page)
            to_shortcuts.clicked.connect(self._open_shortcuts)
            row.addWidget(to_catalog)
            row.addWidget(to_shortcuts)
            row.addStretch(1)
            lay.addLayout(row)
            return page

        def _open_shortcuts(self) -> None:
            from shortcuts_panel import ShortcutsPanel
            dlg = ShortcutsPanel(self)
            dlg.setAttribute(Qt.WA_DeleteOnClose, True)
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
            self._shortcuts_dialog = dlg

        # ---------------- 能力目录 ----------------
        def _build_catalog_tab(self) -> QWidget:
            page = QWidget(self)
            lay = QVBoxLayout(page)

            row = QHBoxLayout()
            self.search_edit = QLineEdit(page)
            self.search_edit.setPlaceholderText(
                "搜索能做什么 / 工具名 / 频段 / 快捷键（例如：频谱、FT8、set_frequency）")
            self.search_edit.textChanged.connect(self._refresh_catalog)
            row.addWidget(self.search_edit, stretch=1)
            self.category_combo = QComboBox(page)
            self.category_combo.addItem("全部")
            for c in CATEGORIES:
                self.category_combo.addItem(c)
            self.category_combo.currentTextChanged.connect(self._refresh_catalog)
            row.addWidget(self.category_combo)
            lay.addLayout(row)

            self.count_label = QLabel(page)
            self.count_label.setWordWrap(True)
            lay.addWidget(self.count_label)

            self.tree = QTreeWidget(page)
            self.tree.setColumnCount(3)
            self.tree.setHeaderLabels(["能力", "说明", "怎么触发"])
            self.tree.setRootIsDecorated(True)
            self.tree.setSelectionMode(QAbstractItemView.NoSelection)
            self.tree.setFocusPolicy(Qt.NoFocus)
            self.tree.setColumnWidth(0, 220)
            lay.addWidget(self.tree, stretch=1)

            self.empty_label = QLabel(page)
            self.empty_label.setWordWrap(True)
            self.empty_label.setText(
                "没有匹配的能力。换个关键词（例如「FT8」「卫星」），"
                "或把分类切回「全部」再试。")
            self.empty_label.setVisible(False)
            lay.addWidget(self.empty_label)

            foot = QLabel(page)
            foot.setWordWrap(True)
            foot.setText(
                "本目录由注册表生成：面板 / 解调模式 / 数字解码能力表 / "
                "长尾解码器 / AI 工具域随注册表自动更新；快捷键与发射、录制、"
                "书签等静态条目随版本更新。"
                "标「暂不可用」的条目按原因处理，不会点了没反应。")
            lay.addWidget(foot)
            return page

        def _refresh_catalog(self) -> None:
            query = self.search_edit.text()
            category = self.category_combo.currentText()
            rows = search_capabilities(self._catalog, query, category)
            stats = catalog_stats(self._catalog)
            self.count_label.setText(
                f"显示 {len(rows)} / 共 {stats['total']} 项 · "
                f"可用 {stats['available']} · 有条件 {stats['conditional']} · "
                f"暂不可用 {stats['unavailable']}")
            self.tree.clear()
            self.empty_label.setVisible(not rows)
            self.tree.setVisible(bool(rows))
            for cap in rows:
                top = QTreeWidgetItem([cap.category, "", ""])
                self.tree.addTopLevelItem(top)
                desc = cap.description
                if cap.note:
                    desc += f"（{cap.note}）"
                child = QTreeWidgetItem([
                    f"{cap.title}{_status_suffix(cap)}", desc, cap.trigger])
                top.addChild(child)
                top.setExpanded(True)
                if not cap.available:
                    for col in range(3):
                        child.setForeground(col, Qt.gray)

        # ---------------- 怎么办 ----------------
        def _build_troubleshooting_tab(self) -> QWidget:
            page = QWidget(self)
            lay = QVBoxLayout(page)

            row = QHBoxLayout()
            self.trouble_search_edit = QLineEdit(page)
            self.trouble_search_edit.setPlaceholderText(
                "搜索症状或关键词（例如：没声音、静噪、连接失败、超时、磁盘满）")
            self.trouble_search_edit.textChanged.connect(
                self._refresh_troubleshooting)
            row.addWidget(self.trouble_search_edit, stretch=1)
            lay.addLayout(row)

            self.trouble_count_label = QLabel(page)
            self.trouble_count_label.setWordWrap(True)
            lay.addWidget(self.trouble_count_label)

            self.trouble_tree = QTreeWidget(page)
            self.trouble_tree.setColumnCount(3)
            self.trouble_tree.setHeaderLabels(
                ["现象（界面原文）", "可能原因", "下一步"])
            self.trouble_tree.setRootIsDecorated(True)
            self.trouble_tree.setSelectionMode(QAbstractItemView.NoSelection)
            self.trouble_tree.setFocusPolicy(Qt.NoFocus)
            self.trouble_tree.setColumnWidth(0, 300)
            lay.addWidget(self.trouble_tree, stretch=1)

            self.trouble_empty_label = QLabel(page)
            self.trouble_empty_label.setWordWrap(True)
            self.trouble_empty_label.setText(
                "没有匹配的条目。换个词（例如「没声音」「静噪」「采样率」），"
                "或清空搜索框看全部。")
            self.trouble_empty_label.setVisible(False)
            lay.addWidget(self.trouble_empty_label)

            foot = QLabel(page)
            foot.setWordWrap(True)
            foot.setText(
                "条目逐字取自 error_catalog 的真实错误；下一步必须点到具体"
                "控件名或设置项键名，写「请检查配置」这类空话会被测试拦下。")
            lay.addWidget(foot)
            return page

        def _refresh_troubleshooting(self) -> None:
            rows = search_remedies(self._remedies,
                                   self.trouble_search_edit.text())
            self.trouble_count_label.setText(
                f"显示 {len(rows)} / 共 {len(self._remedies)} 条 · "
                "每条含现象（界面原文）/ 可能原因 / 下一步")
            self.trouble_tree.clear()
            self.trouble_empty_label.setVisible(not rows)
            self.trouble_tree.setVisible(bool(rows))
            groups: dict = {}
            for r in rows:
                groups.setdefault(r.group, []).append(r)
            for group, items in groups.items():
                top = QTreeWidgetItem([group, "", ""])
                self.trouble_tree.addTopLevelItem(top)
                top.setExpanded(True)
                for r in items:
                    child = QTreeWidgetItem(
                        [r.symptom, r.cause, r.next_step])
                    # 错误码挂在条目上：状态栏/弹窗的「查看怎么办」按 code 定位，
                    # 不靠模糊搜索（搜「连接」可能落到别的条目）。
                    child.setData(0, Qt.UserRole, r.code)
                    top.addChild(child)

        # ---------------- 按错误码定位 ----------------
        def show_remedy_code(self, code: str) -> bool:
            """切到「怎么办」并按错误码选中对应条目；没有条目返回 False。

            状态栏/弹窗的「查看怎么办」走这里——按 code 精确命中，不做模糊
            搜索，也不落到「未找到」空页（调用方在无条目时根本不出链接）。
            """
            self.tabs.setCurrentIndex(2)
            if self.trouble_search_edit.text():
                self.trouble_search_edit.clear()
            for i in range(self.trouble_tree.topLevelItemCount()):
                top = self.trouble_tree.topLevelItem(i)
                for j in range(top.childCount()):
                    child = top.child(j)
                    if child.data(0, Qt.UserRole) == code:
                        top.setExpanded(True)
                        self.trouble_tree.setCurrentItem(child)
                        self.trouble_tree.scrollToItem(child)
                        return True
            return False

        # ---------------- 测试/自动化接口 ----------------
        def visible_row_count(self) -> int:
            return sum(top.childCount() for top in
                       (self.tree.topLevelItem(i)
                        for i in range(self.tree.topLevelItemCount())))

        def visible_remedy_count(self) -> int:
            return sum(top.childCount() for top in
                       (self.trouble_tree.topLevelItem(i)
                        for i in range(self.trouble_tree.topLevelItemCount())))

    return HelpPanel


def __getattr__(name: str):
    """PEP 562：``from help_panel import HelpPanel`` 时才构造 Qt 类，
    让只读目录数据（测试/CLI）不必先起 QApplication。"""
    if name == "HelpPanel":
        return _build_dialog_class()
    raise AttributeError(name)
