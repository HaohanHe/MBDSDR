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


@pytest.fixture(scope="module")
def main_window(qapp):
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
    assert dlg.visible_count() == len(index)

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


# ---------------------------------------------------------------------------
# 主窗口接缝 + 登记
# ---------------------------------------------------------------------------
def test_main_window_exposes_locator_and_registered_shortcut(main_window):
    assert callable(getattr(main_window, "select_tab", None))
    assert callable(getattr(main_window, "_show_command_palette", None))

    from shortcuts import BY_ID
    entry = BY_ID["help.command_palette"]
    assert entry.key == "Ctrl+K" and entry.label == "命令面板"

    src = open(os.path.join(_ROOT, "desktop", "main_window.py"),
               encoding="utf-8").read()
    assert '_key("help.command_palette")' in src
    assert '_show_command_palette' in src
