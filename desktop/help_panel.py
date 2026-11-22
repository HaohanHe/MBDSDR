# SPDX-License-Identifier: MIT
"""
帮助与能力目录面板
====================

三页，一页能看完：

  * **按目的选路**：看天空 / 听信号 / 回看已有录音，各自独立，不要求先连接
    SDR。保留「三步上手」页签名作为旧菜单的兼容入口；键位取自
    ``shortcuts.py``，导航复用真实命令与控件，不自动连接或播放。
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

#: 保留旧 API 的三元组结构及连接/天空位置；这些是独立目的，不是串行步骤。
#: 页面把天空放在首位；键位一律现取 shortcuts.py。
ONBOARDING_STEPS = (
    ("file.connect", "听信号",
     "需要真实 SDR 设备。先选设备，再点「连接」或按 {key}；"
     "连接不等于自动出声，之后由你在收听下拉中选择收听。"
     "在「频谱」页使用设备实际支持的频率和解调方式，不猜电台频率；"
     "顶栏的模式下拉就是真实解调方式：FM / WFM / NFM / AM / USB / LSB / CW。"
     "听模拟语音先用 FM/NFM，听短波与航空先用 AM。"
     "没有设备或真实信号时保持空态，不会伪造信号，也不保证有声音。"),
    ("vfo.replay", "回看已有录音",
     "不需要连接设备。点「回放」或按 {key}，选择已有录音文件；"
     "录音不是直播，播放的是文件中保存的内容，不代表现在收到信号。"),
    ("view.planetarium", "看天空",
     "不需要 SDR。先确认真实观测地点；示例位置不是你的位置。"
     "缺地点请选地点；看卫星还需要轨道数据（TLE），缺失或过期请更新轨道。"
     "左栏「星象仪模式」是天空页；菜单「视图 → 星象仪模式」"
     "或 {key} 切换的是独立星象仪窗口，两者不同。"
     "打开天空不代表已定位，也不代表收到信号。"),
)


#: Esc 语义的唯一口径：帮助页渲染与测试都读这一份，不再各写各的。
ESCAPE_LAYER_NOTE = (
    "Esc 取消进行中的操作（扫频 / 识别 / 回放）；若当前有覆盖层"
    "（右栏浮层、星象仪全天等），先按 Esc 退出覆盖层，再按一次才取消操作。")


def onboarding_steps() -> List[dict]:
    """返回独立路线文案；沿用旧字段/顺序，键位从 shortcuts.py 取。"""
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
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtWidgets import (
        QComboBox, QDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
        QScrollArea, QTabWidget, QTextBrowser, QTreeWidget, QTreeWidgetItem,
        QVBoxLayout, QWidget, QAbstractItemView,
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

        # ---------------- 按目的选路 ----------------
        def _build_quick_start(self) -> QWidget:
            page = QWidget(self)
            lay = QVBoxLayout(page)
            intro = QLabel("你想做什么？选一条路即可，不必依次完成。\n"
                           "Tab 选择入口，空格打开；Esc 关闭帮助。", page)
            intro.setWordWrap(True)
            lay.addWidget(intro)
            scroll = QScrollArea(page)
            scroll.setWidgetResizable(True)
            content = QWidget(scroll)
            routes = QVBoxLayout(content)
            self._route_entries = []
            self._commands = []
            self.last_route_result = {}
            steps = onboarding_steps()
            # 旧 API 保持顺序，界面优先服务不带 SDR 的看天用户。
            for index in (2, 0, 1):
                step = steps[index]
                card = QGroupBox(step["title"], content)
                body = QVBoxLayout(card)
                text = QLabel(step["body"], card)
                text.setWordWrap(True)
                body.addWidget(text)
                buttons = QHBoxLayout()
                body.addLayout(buttons)
                if index == 2:
                    self._add_route_entry(buttons, body, "打开左栏天空页", "tab:rf_sky")
                    self._add_route_entry(buttons, body, "打开独立星象仪", "menu:view.planetarium")
                    self._add_route_entry(buttons, body, "选地点", "control:_planetarium_location_btn")
                    self._add_route_entry(buttons, body, "更新轨道（TLE）", "menu:tools.sat_tle")
                elif index == 0:
                    self._add_route_entry(buttons, body, "去选设备（不连接）", "panel:device")
                    self._add_route_entry(buttons, body, "去频谱，自己选择收听", "tab:spectrum")
                else:
                    self._add_route_entry(buttons, body, "选择已有录音…", "control:replay_btn")
                routes.addWidget(card)
            routes.addStretch(1)
            scroll.setWidget(content)
            lay.addWidget(scroll, stretch=1)

            # 只读说明，不生成没有处理器的 HTML 链接。
            browser = QTextBrowser(page)
            browser.setOpenLinks(False)
            browser.setOpenExternalLinks(False)
            browser.setPlainText(f"主界面 Esc 键：{ESCAPE_LAYER_NOTE}")
            browser.setMaximumHeight(80)
            lay.addWidget(browser)
            row = QHBoxLayout()
            to_catalog = QPushButton("打开能力目录", page)
            to_catalog.clicked.connect(lambda: self.tabs.setCurrentIndex(1))
            to_shortcuts = QPushButton("查看快捷键一览", page)
            to_shortcuts.clicked.connect(self._open_shortcuts)
            row.addWidget(to_catalog)
            row.addWidget(to_shortcuts)
            row.addStretch(1)
            lay.addLayout(row)
            self._refresh_routes()
            return page

        def _add_route_entry(self, row, body, title: str, source: str) -> None:
            button = QPushButton(title, self)
            button.setObjectName(f"help:{source}")
            # QDialog 的隐式默认按钮不能在别的控件按回车时触发回放等动作。
            button.setAutoDefault(False)
            button.setFocusPolicy(Qt.StrongFocus)
            note = QLabel(self)
            note.setWordWrap(True)
            note.setTextFormat(Qt.PlainText)
            row.addWidget(button)
            body.addWidget(note)
            entry = (button, note, source)
            self._route_entries.append(entry)
            button.clicked.connect(lambda _checked=False: self._run_route(entry))

        def _resolve_route(self, source: str):
            """只读校验；命令取统一索引，非命令入口只用已存在的真实按钮。"""
            window = self.parentWidget()
            if window is None:
                return None, "没有主窗口，请从主窗口的帮助菜单打开。"
            kind, key = source.split(":", 1)
            if kind == "control":
                control = getattr(window, key, None)
                if control is None or not callable(getattr(control, "click", None)):
                    return None, "当前窗口没有这个按钮入口。"
                if key == "replay_btn":
                    # 原按钮在读取/回放中会取消/停止，不能当作「选择文件」。
                    # worker 已结束但 GUI 尚未消费结果时，也仍是取消入口。
                    if getattr(window, "_replay_load_thread", None) is not None:
                        return None, "正在读取录音，请在现有回放入口取消或等待。"
                    # timer 存在但不 active 是暂停，仍不能触发原按钮。
                    if getattr(window, "_replay_timer", None) is not None:
                        return None, "正在回放或已暂停，请先在回放条停止当前回放。"
                try:
                    if not control.isEnabled():
                        return None, "按钮当前不可用。" + (control.toolTip() or "")
                except RuntimeError:
                    return None, "按钮已关闭，请重新打开帮助页。"
                return control, ""
            if kind in ("tab", "panel"):
                if kind == "panel":
                    combo = getattr(getattr(window, "device_panel", None), "dev_combo", None)
                    if combo is None or not combo.isEnabled():
                        return None, "设备选择控件不存在或当前不可用。"
                if not callable(getattr(window, "select_tab", None)):
                    return None, "主窗口没有 select_tab 页签定位接口。"
                found = False
                for tab in (getattr(window, "left_tab", None),
                            getattr(window, "right_tab", None)):
                    if tab is None:
                        continue
                    for i in range(tab.count()):
                        if tab.tabBar().tabData(i) != key:
                            continue
                        found = True
                        enabled = getattr(tab, "isTabEnabled", lambda _i: True)(i)
                        target = tab.widget(i)
                        if not enabled or not tab.isEnabled() or target is None or not target.isEnabled():
                            return None, "目标页签当前不可用。"
                if not found:
                    return None, "当前窗口没有这个页签入口。"
                # 天空已搬到独立窗口时，左栏只是占位；不能谎称那里还有天空。
                if key == "rf_sky" and getattr(window, "_app_mode", "ide") == "planetarium":
                    return None, "天空已在独立星象仪窗口，请使用「打开独立星象仪」。"
                matches = [c for c in self._commands if c.source == source]
            else:
                from shortcuts import BY_ID
                label = BY_ID[key].label
                matches = [c for c in self._commands if c.group == "菜单" and c.title == label]
                if key == "view.planetarium" and getattr(window, "_app_mode", None) not in ("ide", "planetarium"):
                    return None, "无法确认当前星象仪状态，为避免反向切换暂不可用。"
            if len(matches) != 1:
                return None, "未找到唯一的真实命令入口。"
            command = matches[0]
            if not command.available:
                return None, command.reason or "命令当前不可用。"
            return command, ""

        def _refresh_routes(self) -> None:
            from command_palette import build_index
            self._commands = build_index(self.parentWidget())
            for button, note, source in self._route_entries:
                _target, reason = self._resolve_route(source)
                button.setEnabled(not reason)
                # 可用时也给出说明，而不是把 tooltip 清空（用户仍能看入口做什么）。
                button.setToolTip(reason or "入口可用：点击后按页面提示选择地点或文件")
                note.setText(f"{button.text()}：{reason}" if reason else "")
                note.setVisible(bool(reason))
                button.setAccessibleDescription(reason)

        def _run_route(self, entry) -> None:
            # 非模态期间主窗口状态可能已变：点击时重查，绝不执行过期闭包。
            self._refresh_routes()
            button, note, source = entry
            target, reason = self._resolve_route(source)
            result = {"ok": False, "status": "UNAVAILABLE", "reason": reason}
            if not reason:
                window = self.parentWidget()
                if source == "menu:view.planetarium" and window._app_mode == "planetarium":
                    # 菜单命令是 toggle。已在目标时不再 run，避免退回频谱。
                    sky_window = getattr(window, "_planetarium_window", None)
                    if sky_window is not None:
                        sky_window.show()
                        sky_window.raise_()
                        sky_window.activateWindow()
                    result = {"ok": True, "status": "DONE", "reason": "保持当前独立星象仪，不切换模式。"}
                elif source.startswith("control:"):
                    target.click()
                    result = {"ok": True, "status": "DONE", "reason": "已请求打开入口；请在目标页面确认地点或选择文件。"}
                else:
                    result = target.run()
                if result.get("ok"):
                    self.hide()
                    if source.startswith(("tab:", "panel:")):
                        window.raise_()
                        window.activateWindow()
            self.last_route_result = result
            note.setText(str(result.get("reason") or "入口执行失败，请在主窗口检查。"))
            note.setVisible(True)

        def showEvent(self, event) -> None:
            self._refresh_routes()
            super().showEvent(event)

        def changeEvent(self, event) -> None:
            if event.type() == QEvent.ActivationChange and self.isActiveWindow() and hasattr(self, "_route_entries"):
                self._refresh_routes()
            super().changeEvent(event)

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
