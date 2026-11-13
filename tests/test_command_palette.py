# SPDX-License-Identifier: MIT
"""
全局命令面板：统一索引 + 定位/执行 + 守门
=========================================

钉住的红线：
  * 索引必须覆盖「菜单动作 + 面板页签 + 能力目录 + 快捷键」四类真实入口，
    不做第二份手抄清单；
  * 不可用项必须写原因且不可执行（不许点了没反应）；
  * 搜「设置」→ 第一条就是设置页签，执行后右栏真的切到它；
  * 搜「全天」→ 执行后 ``sky_view.all_sky_active`` 真的为 True；
  * 主窗口必须提供 ``select_tab(tabData)`` 定位接口并登记 Ctrl+K。

反证：把 select_tab 改成只改勾选不切页、把能力索引换成硬编码清单、
或让不可用项也带 run，本文件立刻红。
"""
import os
import sys

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


@pytest.fixture(autouse=True)
def _fresh_recent(tmp_path, monkeypatch):
    """每条用例用独立的「最近使用」文件，互不串味、不碰真实配置。"""
    import command_palette as cp
    monkeypatch.setattr(cp, "_recent_path", lambda: tmp_path / "recent.json")
    cp.reset_recent()
    yield
    cp.reset_recent()


@pytest.fixture(scope="module")
def main_window(qapp, isolated_profile):
    from main_window import MainWindow
    w = MainWindow()
    w.show()
    w.activateWindow()
    qapp.processEvents()
    yield w
    if hasattr(w, "_disconnect"):
        w._disconnect()
    w.close()


@pytest.fixture(scope="module")
def index(main_window):
    from command_palette import build_index
    return build_index(main_window)


# ---------------------------------------------------------------------------
# 索引 = 真实入口
# ---------------------------------------------------------------------------
def test_index_covers_all_four_sources(index, main_window):
    from capability_catalog import build_catalog
    from shortcuts import SHORTCUTS

    sources = {c.source for c in index}
    cap_sources = {c.source for c in build_catalog()}
    assert cap_sources <= sources, (
        f"能力目录条目没进索引: {sorted(cap_sources - sources)[:5]}")

    for tab in (main_window.left_tab, main_window.right_tab):
        for i in range(tab.count()):
            key = tab.tabBar().tabData(i)
            assert f"tab:{key}" in sources, f"页签 {key} 没进索引"

    titles = {c.title for c in index}
    for required in ("连接设备...", "全天模式（160°）", "命令面板"):
        assert required in titles, f"菜单命令「{required}」没进索引"

    uncovered = [s.id for s in SHORTCUTS if s.key
                 and f"shortcut:{s.id}" not in sources
                 and not any(c.title == s.label and s.key in c.trigger
                             for c in index)]
    assert not uncovered, f"快捷键没进索引也没被菜单项覆盖: {uncovered}"


def test_index_groups_are_declared(index):
    from command_palette import GROUPS
    assert {c.group for c in index} <= set(GROUPS)
    for c in index:
        assert c.title.strip()
        assert c.description.strip(), f"{c.title} 没写「这是什么/什么时候用」"


def test_unavailable_entries_explain_and_refuse(index):
    for c in index:
        if c.status == "unavailable":
            assert c.reason.strip(), f"不可用项「{c.title}」没写原因"
            assert c.run is None, f"不可用项「{c.title}」还带执行入口"


# ---------------------------------------------------------------------------
# 搜索 / 定位 / 执行
# ---------------------------------------------------------------------------
def test_search_settings_locates_settings_tab(index, main_window, qapp):
    from command_palette import search_commands
    hits = search_commands(index, "设置")
    assert hits, "搜「设置」没有任何结果"
    first = hits[0]
    assert first.group == "页签" and first.title == "设置", (
        f"搜「设置」第一条不是设置页签，而是 [{first.group}] {first.title}")

    result = first.run()
    assert result["ok"] is True, f"设置页签定位失败: {result}"
    qapp.processEvents()

    rt = main_window.right_tab
    cur = rt.currentIndex()
    assert rt.tabBar().tabData(cur) == "settings", (
        f"执行后右栏当前页签是 {rt.tabText(cur)!r}，不是设置")
    assert rt.isTabVisible(cur)


def test_search_all_sky_really_enters_all_sky(index, main_window, qapp):
    from command_palette import search_commands
    hits = search_commands(index, "全天")
    assert hits, "搜「全天」没有任何结果"
    target = hits[0]
    assert "全天" in target.title

    sv = main_window.sky_view
    sv.set_all_sky_mode(False)
    main_window.left_tab.setFocus()
    qapp.processEvents()

    result = target.run()
    assert result["ok"] is True, f"全天命令执行失败: {result}"
    qapp.processEvents()
    assert sv.all_sky_active is True, "命令面板执行了但没有真的进入全天模式"
    sv.set_all_sky_mode(False)


def test_palette_smoke_filters_and_runs(main_window, index, qapp):
    from command_palette import CommandPalette
    dlg = CommandPalette(window=main_window, parent=main_window)
    assert not dlg.isModal()
    assert not dlg.isVisible()
    executable = [c for c in index if c.available]
    assert dlg.visible_count() == len(executable), (
        "空查询必须只列可执行动作，不得混入不可用项")
    assert all(c.available for c in dlg.visible_commands())
    assert "回车" in dlg.reason_label.text() or "Enter" in dlg.reason_label.text()

    dlg.search_edit.setText("设置")
    assert 0 < dlg.visible_count() < len(index)
    assert dlg.visible_commands()[0].title == "设置"

    dlg.search_edit.setText("不存在的词_zzz")
    assert dlg.visible_count() == 0
    assert "没有匹配" in dlg.reason_label.text()

    dlg.search_edit.setText("恢复出厂布局")
    assert dlg.visible_count() >= 1
    dlg.deleteLater()
    qapp.processEvents()


def test_palette_blocks_unavailable_with_reason(main_window, index):
    from command_palette import CommandPalette
    unavailable = next(c for c in index if c.status == "unavailable")
    dlg = CommandPalette(window=main_window, parent=main_window)
    dlg.search_edit.setText(unavailable.title)
    assert dlg.visible_count() >= 1
    result = dlg.run_first(unavailable.title)
    assert result["ok"] is False
    assert unavailable.reason in result["reason"]
    dlg.deleteLater()


def _press_enter(widget):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    QTest.keyClick(widget, Qt.Key_Return)


def test_enter_in_search_box_runs_selected_command(main_window, qapp):
    """G-02：焦点在搜索框时按回车必须直接执行，不必先 Tab 进列表。"""
    from command_palette import CommandPalette
    dlg = CommandPalette(window=main_window, parent=main_window)
    dlg.show()
    qapp.processEvents()
    dlg.search_edit.setFocus()
    dlg.search_edit.setText("设置")
    assert dlg.visible_commands()[0].title == "设置"

    _press_enter(dlg.search_edit)
    qapp.processEvents()

    assert dlg.last_result.get("ok") is True, f"回车没执行条目: {dlg.last_result}"
    assert not dlg.isVisible(), "执行成功后命令面板应关闭"
    rt = main_window.right_tab
    assert rt.tabBar().tabData(rt.currentIndex()) == "settings", (
        "回车执行了「设置」但右栏没切到设置页签")
    dlg.deleteLater()
    qapp.processEvents()


def test_down_arrow_moves_focus_into_list(main_window, qapp):
    """G-02：↓ 从搜索框进入结果列表，不丢焦点。"""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from command_palette import CommandPalette
    dlg = CommandPalette(window=main_window, parent=main_window)
    dlg.show()
    qapp.processEvents()
    dlg.search_edit.setFocus()
    dlg.search_edit.setText("设置")

    QTest.keyClick(dlg.search_edit, Qt.Key_Down)
    qapp.processEvents()
    assert dlg.focusWidget() is dlg.list_widget, (
        f"↓ 没把焦点送进列表，当前焦点是 {type(dlg.focusWidget()).__name__}")
    dlg.deleteLater()
    qapp.processEvents()


def test_enter_on_unavailable_keeps_panel_open_with_reason(main_window, index, qapp):
    """G-03：不可用项回车后面板必须留在原地并显示原因。"""
    from command_palette import CommandPalette
    unavailable = next(c for c in index if c.status == "unavailable")
    dlg = CommandPalette(window=main_window, parent=main_window)
    dlg.show()
    qapp.processEvents()
    dlg.search_edit.setFocus()
    dlg.search_edit.setText(unavailable.title)
    assert dlg.visible_commands()[0].title == unavailable.title

    _press_enter(dlg.search_edit)
    qapp.processEvents()

    assert dlg.last_result.get("ok") is False
    assert dlg.isVisible(), "执行失败后面板被关闭，用户看不到失败原因"
    assert unavailable.reason in dlg.reason_label.text()
    dlg.deleteLater()
    qapp.processEvents()


def test_runtime_failure_keeps_panel_open(main_window, qapp):
    """G-03：条目可执行但执行结果 ok=False 时，面板不得先关闭。"""
    from command_palette import Command, CommandPalette
    dlg = CommandPalette(window=main_window, parent=main_window)
    dlg._commands.insert(0, Command(
        title="__失败样例__", group="菜单", description="执行必失败的样例",
        run=lambda: {"ok": False, "status": "FAILED", "reason": "设备未连接"}))
    dlg.show()
    qapp.processEvents()

    result = dlg.run_first("__失败样例__")
    assert result["ok"] is False
    assert dlg.isVisible(), "执行失败后面板被关闭，用户看不到原因"
    assert "设备未连接" in dlg.reason_label.text()
    dlg.deleteLater()
    qapp.processEvents()


# ---------------------------------------------------------------------------
# 第二阶段：模糊匹配 / 最近使用 / 空态与无匹配态 / 键盘路径
# ---------------------------------------------------------------------------
def test_fuzzy_subsequence_matches_non_contiguous():
    """「命板」这种不连续输入也要命中「命令面板」，但顺序不能乱。"""
    from command_palette import Command, search_commands
    cmds = [
        Command(title="命令面板", group="菜单", description="打开全局命令面板"),
        Command(title="恢复出厂布局", group="菜单", description="重置窗口布局"),
    ]
    assert [c.title for c in search_commands(cmds, "命板")] == ["命令面板"]
    assert search_commands(cmds, "板命") == [], (
        "子序列必须按顺序匹配，反序不该命中")


def test_recent_commands_rank_first_in_same_tier():
    """同一相关度档位内，最近用过的排前；空查询也按最近使用排。"""
    from command_palette import Command, search_commands
    cmds = [
        Command(title="甲页", group="页签", description="a", source="tab:a",
                run=lambda: {"ok": True}),
        Command(title="乙页", group="页签", description="b", source="tab:b",
                run=lambda: {"ok": True}),
        Command(title="丙页", group="页签", description="c", source="tab:c",
                run=lambda: {"ok": True}),
    ]
    empty = [c.source for c in search_commands(cmds, "", recent=["tab:c", "tab:a"])]
    assert empty == ["tab:c", "tab:a", "tab:b"], f"空查询没按最近使用排: {empty}"

    hits = search_commands(cmds, "页签", recent=["tab:b"])
    assert [c.source for c in hits][0] == "tab:b", (
        f"同档位内最近使用没排前: {[c.source for c in hits]}")


def test_empty_query_hides_unavailable_actions(main_window, index):
    """空查询只给可执行动作；不可用项要靠输入关键词才出现（带原因）。"""
    from command_palette import CommandPalette
    dlg = CommandPalette(window=main_window, parent=main_window)
    shown = dlg.visible_commands()
    assert shown and all(c.available for c in shown)
    assert len(shown) == len([c for c in index if c.available])
    unavailable = next(c for c in index if c.status == "unavailable")
    dlg.search_edit.setText(unavailable.title)
    assert any(c.source == unavailable.source and not c.available
               for c in dlg.visible_commands()), (
        "输入关键词后应能搜到不可用项（用于看原因）")
    dlg.deleteLater()


def test_no_match_hint_is_actionable(main_window, index):
    """无匹配不是干巴巴「未找到」：给真实可执行样例 + 键盘提示。"""
    from command_palette import CommandPalette
    dlg = CommandPalette(window=main_window, parent=main_window)
    dlg.search_edit.setText("zzz_不存在")
    text = dlg.reason_label.text()
    assert "没有匹配" in text
    assert "试试" in text and "Enter" in text and "↑↓" in text, text
    real = [c.title for c in index if c.available and f"「{c.title}」" in text]
    assert real, f"提示里的样例不是索引里的真实条目: {text}"
    dlg.deleteLater()


#: 键盘可达性契约：从触发到执行最多 3 个按键动作。
#: 「输入关键词」算一个动作（中文靠输入法，测试走同一条 textChanged 链路）。
KEY_PATH_CONTRACT = [
    ("最近使用项", ["Ctrl+K", "Enter"]),
    ("检索命中首条", ["Ctrl+K", "输入关键词", "Enter"]),
    ("列表下移一格", ["Ctrl+K", "↓", "Enter"]),
]


def test_key_path_contract_within_three_keys():
    for label, seq in KEY_PATH_CONTRACT:
        assert len(seq) <= 3, f"{label} 要 {len(seq)} 个键：{seq}"


def test_mru_item_runs_with_two_keys(main_window, qapp):
    """Ctrl+K → Enter：最近使用项两键可执行（且它确实被顶到首条）。"""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from command_palette import remember_recent, search_commands
    assert search_commands(main_window_command_index(main_window), "")[0].source \
        != "tab:settings", "前提不成立：设置页签本来就排第一，测不出 MRU 效果"
    remember_recent("tab:settings", persist=False)

    main_window._show_command_palette()
    qapp.processEvents()
    dlg = main_window._command_palette_dialog
    assert dlg.visible_commands()[0].source == "tab:settings", "最近使用项没排第一"

    QTest.keyClick(dlg.search_edit, Qt.Key_Return)
    qapp.processEvents()
    assert dlg.last_result.get("ok") is True
    rt = main_window.right_tab
    assert rt.tabBar().tabData(rt.currentIndex()) == "settings"


def test_search_then_enter_runs_with_three_keys(main_window, qapp):
    """Ctrl+K → 输入「全天」→ Enter：三步行到执行。"""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    sv = main_window.sky_view
    sv.set_all_sky_mode(False)
    qapp.processEvents()

    main_window._show_command_palette()
    qapp.processEvents()
    dlg = main_window._command_palette_dialog
    dlg.search_edit.setFocus()
    dlg.search_edit.setText("全天")          # 输入法一次输入 = 一个动作
    qapp.processEvents()
    assert "全天" in dlg.visible_commands()[0].title, (
        f"「全天」没排到首条: {[c.title for c in dlg.visible_commands()[:3]]}")

    QTest.keyClick(dlg.search_edit, Qt.Key_Return)
    qapp.processEvents()
    assert dlg.last_result.get("ok") is True, dlg.last_result
    assert sv.all_sky_active is True
    sv.set_all_sky_mode(False)


def main_window_command_index(window):
    from command_palette import build_index
    return build_index(window)


# ---------------------------------------------------------------------------
# 主窗口接缝 + 登记
# ---------------------------------------------------------------------------
def test_main_window_exposes_locator_and_registered_shortcut(main_window, qapp):
    assert callable(getattr(main_window, "select_tab", None))
    assert callable(getattr(main_window, "_show_command_palette", None))

    from PySide6.QtGui import QAction
    from shortcuts import BY_ID
    entry = BY_ID["help.command_palette"]
    assert entry.key == "Ctrl+K" and entry.label == "命令面板"

    # 行为断言：菜单里真有一个 Ctrl+K 的动作，触发后真的打开命令面板
    matches = [a for a in main_window.findChildren(QAction)
               if a.shortcut().toString() == entry.key]
    assert matches, "菜单栏里没有登记 Ctrl+K 的动作（命令面板入口消失）"
    matches[0].trigger()
    qapp.processEvents()

    dlg = getattr(main_window, "_command_palette_dialog", None)
    if dlg is None:
        from PySide6.QtWidgets import QDialog
        raise AssertionError(
            "触发 Ctrl+K 菜单项没有真的打开命令面板；窗口下现存对话框："
            f"{[(type(x).__name__, x.isVisible()) for x in main_window.findChildren(QDialog)]}")
    assert dlg.isVisible(), "命令面板打开后不可见"
    assert getattr(dlg, "_commands", []), "命令面板打开但索引为空（空壳入口）"
    dlg.close()
    qapp.processEvents()


# ---------------------------------------------------------------------------
# 焦点返还（第 8 条：执行成功后不能把焦点丢给系统）
# ---------------------------------------------------------------------------
def _toy_window(qapp, names):
    """一个不依赖主窗口的玩具窗：只验证焦点返还契约，不搬整套 MainWindow。

    用 --noconftest 单跑时不能依赖 conftest 的 isolated_profile，所以这里
    自建 QMainWindow；契约本身与主窗无关（谁打开面板、命令挪没挪焦点）。
    """
    from PySide6.QtWidgets import QMainWindow, QPushButton, QVBoxLayout, QWidget
    win = QMainWindow()
    central = QWidget(win)
    lay = QVBoxLayout(central)
    buttons = []
    for name in names:
        btn = QPushButton(name, central)
        lay.addWidget(btn)
        buttons.append(btn)
    win.setCentralWidget(central)
    win.show()
    win.activateWindow()
    qapp.processEvents()
    return win, buttons


def test_success_returns_focus_to_widget_that_opened_palette(qapp):
    """执行成功关闭面板后，焦点回到打开前那颗控件。

    命令自己会把焦点挪走（真实「设置」命令就调 tab.setFocus()），所以
    「关掉面板后焦点还在原处」只能靠显式返还。
    反证：删掉 run_command 里的 ``_restore_return_focus()``，焦点会停在
    命令挪去的那颗控件上，最后一条断言必红。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from command_palette import Command, CommandPalette

    win, (opener, moved) = _toy_window(qapp, ["打开前焦点", "命令把焦点挪到这里"])
    opener.setFocus(Qt.OtherFocusReason)
    qapp.processEvents()
    assert QApplication.focusWidget() is opener, "前置条件：焦点没放到打开前的控件"

    dlg = CommandPalette(window=win, parent=win)
    assert dlg._return_focus is opener, "打开面板时没记住原焦点控件"

    def _run():
        moved.setFocus(Qt.OtherFocusReason)   # 模拟「设置」命令的 tab.setFocus()
        return {"ok": True, "status": "DONE", "reason": "已执行"}

    dlg._commands.insert(0, Command(
        title="__成功样例__", group="菜单", description="执行必成功的样例",
        source="test:ok", run=_run))
    dlg.show()
    dlg.activateWindow()
    qapp.processEvents()
    result = dlg.run_first("__成功样例__")
    qapp.processEvents()

    assert result["ok"] is True
    assert not dlg.isVisible()
    assert QApplication.focusWidget() is opener, (
        f"执行后焦点落在 {type(QApplication.focusWidget()).__name__}，"
        "没回到打开面板前的控件")
    win.close()
    dlg.deleteLater()
    qapp.processEvents()


def test_destroyed_opener_falls_back_into_main_window(qapp):
    """原焦点控件已销毁时，回主窗一个确定落点，而不是把焦点丢给系统。"""
    import shiboken6
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from command_palette import Command, CommandPalette

    win, (keep, gone) = _toy_window(qapp, ["常驻控件", "__临时焦点控件__"])
    gone.setFocus(Qt.OtherFocusReason)
    qapp.processEvents()
    assert QApplication.focusWidget() is gone

    dlg = CommandPalette(window=win, parent=win)
    assert dlg._return_focus is gone
    shiboken6.delete(gone)          # 面板开着时控件被销毁（设置页重建同构）

    dlg._commands.insert(0, Command(
        title="__成功样例__", group="菜单", description="执行必成功的样例",
        source="test:ok", run=lambda: {"ok": True, "status": "DONE", "reason": "已执行"}))
    dlg.show()
    dlg.activateWindow()
    qapp.processEvents()
    assert dlg.run_first("__成功样例__")["ok"] is True
    qapp.processEvents()

    fw = QApplication.focusWidget()
    assert fw is not None, "焦点被丢给系统（focusWidget 为 None）"
    assert not dlg.isAncestorOf(fw), "焦点还在已关闭的面板里"
    assert win.isAncestorOf(fw), (
        f"兜底落点不在主窗内：{type(fw).__name__}")
    assert fw is keep, f"兜底没落到焦点链第一颗可用控件，而是 {type(fw).__name__}"
    win.close()
    dlg.deleteLater()
    qapp.processEvents()


# ---------------------------------------------------------------------------
# 右栏深处的两条直接入口（2026-11-13：不新增快捷键，走命令面板）
# ---------------------------------------------------------------------------
def test_panel_jump_commands_land_on_target_widgets(index, main_window, qapp):
    """「跳到设备」「跳到增益」必须真的把焦点落到设备下拉 / 增益滑杆。

    反证：删掉 ``_jump_to_right`` 里的 ``QTimer.singleShot(0, _focus)``，
    焦点会被 run_command 的「返还打开前位置」覆盖，两条断言都会红。
    """
    from PySide6.QtWidgets import QApplication

    by_title = {c.title: c for c in index}
    for title in ("跳到设备", "跳到增益"):
        cmd = by_title.get(title)
        assert cmd is not None, f"命令面板缺「{title}」"
        assert cmd.group == "面板" and cmd.run is not None, (
            f"「{title}」不是可执行的右栏定位入口")

    dp = main_window.device_panel
    assert dp is not None, "设备面板没建出来，本用例前提不成立"
    # 无硬件时热插拔轮询会把注入的假设备清空，定位期间停掉
    dp._hotplug_timer.stop()
    dp._apply_devices([
        {"driver": "rtlsdr", "label": "Fake A", "index": 0,
         "serial": "AAA", "tuner": "R820T"},
    ])
    # 增益滑杆未连接时是禁用的，setFocus 会被 Qt 忽略；先进入「已连接」态
    main_window.control_panel.set_sdr_connected(True)
    main_window.activateWindow()
    main_window.raise_()
    qapp.processEvents()
    try:
        res = by_title["跳到设备"].run()
        assert res["ok"] is True, res
        qapp.processEvents()
        qapp.processEvents()
        assert main_window.focusWidget() is dp.dev_combo, (
            "跳到设备后焦点没落在设备下拉，而是 %s"
            % type(main_window.focusWidget()).__name__)

        res = by_title["跳到增益"].run()
        assert res["ok"] is True, res
        qapp.processEvents()
        qapp.processEvents()
        gain = main_window.control_panel.gain_slider
        assert gain.isVisible(), "跳到增益后滑杆仍隐藏（「接收」组没展开）"
        assert main_window.focusWidget() is gain, (
            "跳到增益后焦点没落在增益滑杆，而是 %s"
            % type(main_window.focusWidget()).__name__)
    finally:
        if getattr(main_window, "_right_overlay", None) is not None:
            main_window._close_right_overlay()
        qapp.processEvents()


# ---------------------------------------------------------------------------
# P1-40 的两条入口（2026-11-13：设置/导出没有菜单入口，先由命令面板兜住）
# ---------------------------------------------------------------------------
def test_settings_and_export_commands_land_on_operable_controls(
        index, main_window, qapp):
    """「打开设置」落在搜索框、「导出」落在导出按钮——都不是节标题。

    反证：把 ``_jump_to_right`` 的 ``prepare`` 去掉（不展开「数据与操作」组），
    导出按钮处于隐藏态、``setFocus`` 被忽略，第二条断言会红。
    """
    from PySide6.QtWidgets import QPushButton

    by_title = {c.title: c for c in index}
    for title in ("打开设置", "导出"):
        cmd = by_title.get(title)
        assert cmd is not None, f"命令面板缺「{title}」"
        assert cmd.group == "面板" and cmd.run is not None, (
            f"「{title}」不是可执行的入口")

    panel = getattr(main_window, "settings_dock", None)
    assert panel is not None, "设置面板没建出来，本用例前提不成立"
    main_window.activateWindow()
    main_window.raise_()
    qapp.processEvents()
    try:
        res = by_title["打开设置"].run()
        assert res["ok"] is True, res
        qapp.processEvents()
        qapp.processEvents()
        section = next(
            s for s in main_window.right_tab._sections
            if s.header.text().strip() == "设置")
        assert section.is_expanded(), "打开设置后「设置」节仍是收起态"
        assert panel.search_edit.isVisible(), "打开设置后搜索框不可见"
        assert main_window.focusWidget() is panel.search_edit, (
            "打开设置后焦点没落在设置搜索框，而是 %s"
            % type(main_window.focusWidget()).__name__)

        res = by_title["导出"].run()
        assert res["ok"] is True, res
        qapp.processEvents()
        qapp.processEvents()
        group = panel._group_sections["数据与操作"]
        assert group.is_expanded(), "导出前「数据与操作」组没展开"
        export_btn = panel.export_btn
        assert isinstance(export_btn, QPushButton)
        assert export_btn.isVisible(), "导出按钮仍隐藏，焦点落不上"
        assert main_window.focusWidget() is export_btn, (
            "导出后焦点没落在「导出配置」按钮，而是 %s"
            % type(main_window.focusWidget()).__name__)
    finally:
        if getattr(main_window, "_right_overlay", None) is not None:
            main_window._close_right_overlay()
        qapp.processEvents()
