# SPDX-License-Identifier: MIT
"""
统一搜索（provider 化）用例
============================

钉住的红线：
  * 搜索源是**声明式注册表**：``search_providers.PROVIDERS`` 少一行，对应结果
    就消失（反证真跑）；
  * 每类 provider 各有用例：命令 / 设置项 / 卫星；
  * 设置项搜到的是**那一行**（清过滤态、展开分组、焦点落到控件），不是只开面板；
  * 卫星无 TLE 时「该类当前不可用」要说明原因，并与「没有匹配」分开表达；
  * 结果按来源分组显示、组标题不可选中；键盘路径（输入→↓→回车→Esc）能走完。
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


class _FakeTracker:
    """最小卫星目录：只提供 provider 与主窗口接缝用到的字段/方法。"""

    tle_date = "2026-11-13"

    def list_satellites(self):
        return [
            {"name": "ISS (ZARYA)", "norad_id": 25544,
             "tle_tier_label": "新", "tle_age_days": 0.2},
            {"name": "AO-91", "norad_id": 43017,
             "tle_tier_label": "新", "tle_age_days": 0.2},
        ]

    def downlink_freq_for(self, name):
        return 437_800_000.0


# ---------------------------------------------------------------------------
# 注册表本身
# ---------------------------------------------------------------------------
def test_registry_is_declarative_and_declares_groups():
    import command_palette as cp
    import search_providers as sp

    groups = [p.group for p in sp.PROVIDERS]
    assert groups == ["菜单", "页签", "面板", "能力", "快捷键", "设置项", "卫星"]
    assert set(groups) <= set(cp.GROUPS)
    # 每个 provider 都能独立回答「现在能不能用」。
    assert all(hasattr(p, "status") and hasattr(p, "commands")
               for p in sp.PROVIDERS)


def test_menu_provider_still_serves_real_actions(main_window):
    from command_palette import build_index
    index = build_index(main_window)
    menu = [c for c in index if c.group == "菜单"]
    assert menu, "菜单 provider 没有产出任何真实菜单动作"
    assert all(c.source.startswith("menu:") for c in menu)


# ---------------------------------------------------------------------------
# 设置项：落到那一行
# ---------------------------------------------------------------------------
def test_settings_provider_reveals_the_actual_row(main_window, qapp):
    from command_palette import build_index

    index = build_index(main_window)
    cmd = next(c for c in index if c.source == "settings:record_dir")
    assert cmd.group == "设置项" and cmd.title == "录音目录"
    assert cmd.run is not None

    panel = main_window.settings_dock
    panel._ensure_ui_built()
    # 过滤态会把未命中的行藏起来——定位前必须能自己清掉，否则焦点落不上。
    panel.search_edit.setText("zzz_只用来把行藏起来")
    qapp.processEvents()
    assert panel._row_boxes["record_dir"].isHidden(), (
        "前置条件不成立：过滤态没有隐藏目标行，测不出 reveal 的清场动作")

    main_window.activateWindow()
    main_window.raise_()
    qapp.processEvents()
    try:
        res = cmd.run()
        assert res["ok"] is True, res
        qapp.processEvents()
        qapp.processEvents()

        assert panel.search_edit.text() == "", "定位设置项后过滤词没清空"
        assert not panel._row_boxes["record_dir"].isHidden(), "目标行仍被藏起来"
        assert panel._group_sections["数据与操作"].is_expanded(), (
            "目标行所在分组没展开")
        assert main_window.focusWidget() is panel._rows["record_dir"], (
            "焦点没落在设置行控件，而是 %s"
            % type(main_window.focusWidget()).__name__)
        rt = main_window.right_tab
        assert rt.tabBar().tabData(rt.currentIndex()) == "settings"
    finally:
        if getattr(main_window, "_right_overlay", None) is not None:
            main_window._close_right_overlay()
        qapp.processEvents()


# ---------------------------------------------------------------------------
# 卫星：名字 / 编号 → 选中该星
# ---------------------------------------------------------------------------
def test_satellite_provider_jumps_to_satellite(monkeypatch, main_window, qapp):
    from command_palette import build_index

    monkeypatch.setattr(main_window, "_loop_sat_tracker", _FakeTracker())
    panel = main_window.sat_track_panel
    panel.populate_satellites(["ISS (ZARYA)", "AO-91"])
    qapp.processEvents()

    index = build_index(main_window)
    cmd = next(c for c in index if c.source == "sat:25544")
    assert cmd.group == "卫星"
    assert cmd.title == "ISS (ZARYA)"
    assert "25544" in cmd.trigger, "卫星条目没标明编号"

    combo = panel.sat_combo
    combo.setCurrentIndex(combo.findData("AO-91"))
    qapp.processEvents()
    assert combo.currentData() == "AO-91"     # 前置：先站在另一颗上

    main_window.activateWindow()
    main_window.raise_()
    qapp.processEvents()
    try:
        res = cmd.run()
        assert res["ok"] is True, res
        qapp.processEvents()
        qapp.processEvents()
        assert combo.currentData() == "ISS (ZARYA)", "下拉框没切到目标卫星"
        assert main_window.focusWidget() is combo, (
            "焦点没落在卫星下拉框，而是 %s"
            % type(main_window.focusWidget()).__name__)
        rt = main_window.right_tab
        assert rt.tabBar().tabData(rt.currentIndex()) == "sat_track"
    finally:
        if getattr(main_window, "_right_overlay", None) is not None:
            main_window._close_right_overlay()
        qapp.processEvents()


# ---------------------------------------------------------------------------
# 「不可用」与「空结果」必须分开表达
# ---------------------------------------------------------------------------
def test_unavailable_satellite_is_reported_with_reason(
        monkeypatch, main_window, qapp):
    import command_palette as cp

    monkeypatch.setattr(main_window, "_loop_sat_tracker", None)
    gaps = cp.provider_gaps(main_window)
    assert ("卫星", "卫星库未加载：TLE 未就绪") in gaps, gaps

    index = cp.build_index(main_window)
    assert not [c for c in index if c.group == "卫星"], (
        "卫星库不可用时仍产出了卫星条目（会变成点了没反应的空壳）")

    dlg = cp.CommandPalette(window=main_window, parent=main_window)
    # 用「NORAD 编号」那种长查询：tracker 为 None 时索引里没有任何条目能
    # 命中它（短数字会被模糊子序列误命中别的说明文本）。
    dlg.search_edit.setText("NORAD 25544")
    assert dlg.visible_count() == 0
    text = dlg.reason_label.text()
    assert "没有匹配" in text, f"空结果没有如实说明：{text}"
    assert "卫星库未加载：TLE 未就绪" in text, (
        f"「该类不可用」的原因没写出来：{text}")
    assert "另有" in text, f"不可用与空结果没有分开表达：{text}"
    dlg.deleteLater()
    qapp.processEvents()


def test_empty_result_is_not_mislabelled_as_unavailable(
        monkeypatch, main_window, qapp):
    import command_palette as cp

    monkeypatch.setattr(main_window, "_loop_sat_tracker", _FakeTracker())
    assert cp.provider_gaps(main_window) == [], "前置条件：此时不应有不可用类"

    dlg = cp.CommandPalette(window=main_window, parent=main_window)
    dlg.search_edit.setText("zzz_根本不存在")
    assert dlg.visible_count() == 0
    text = dlg.reason_label.text()
    assert "没有匹配" in text
    assert "卫星库未加载" not in text, (
        f"空结果被误报成「卫星不可用」：{text}")
    assert "不可用" not in text, (
        f"没有不可用来源时不该出现「不可用」字样：{text}")
    dlg.deleteLater()
    qapp.processEvents()


# ---------------------------------------------------------------------------
# 分组显示 + 来源类型
# ---------------------------------------------------------------------------
def test_results_render_grouped_with_source_labels(main_window, qapp):
    from PySide6.QtCore import Qt
    import command_palette as cp

    dlg = cp.CommandPalette(window=main_window, parent=main_window)
    menu_cmd = next(c for c in dlg._commands
                    if c.group == "菜单" and c.available)
    setting_cmd = next(c for c in dlg._commands
                       if c.group == "设置项" and c.available)
    dlg._results = [menu_cmd, setting_cmd]
    dlg._render_results()

    count = dlg.list_widget.count()
    assert count == 4, f"两组两条结果应有 2 个组标题 + 2 行，实际 {count}"

    header = dlg.list_widget.item(0)
    assert header.text() == "菜单（1）"
    assert header.data(Qt.UserRole) is None
    assert not (header.flags() & Qt.ItemIsSelectable), "组标题不该可选中"
    assert "[菜单]" in dlg.list_widget.item(1).text(), "结果行没标明来源类型"
    assert dlg.list_widget.item(2).text() == "设置项（1）"
    assert "[设置项]" in dlg.list_widget.item(3).text()

    assert dlg.list_widget.currentRow() == 1, "默认选中行应跳过组标题"
    assert dlg._selected_command() is menu_cmd
    dlg.deleteLater()
    qapp.processEvents()


# ---------------------------------------------------------------------------
# 键盘全流程 + 入口提示
# ---------------------------------------------------------------------------
def test_keyboard_flow_input_select_enter_escape(main_window, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    import command_palette as cp

    # Esc 先测：此时面板是刚显示出来的顶层窗口，按键走真实分发路径。
    dlg = cp.CommandPalette(window=main_window, parent=main_window)
    dlg.show()
    dlg.activateWindow()
    qapp.processEvents()
    dlg.search_edit.setFocus()
    qapp.processEvents()
    assert "设置项" in dlg.search_edit.placeholderText()
    assert "卫星" in dlg.search_edit.placeholderText()
    assert "Esc" in dlg.reason_label.text(), "空态没给出入口/退出提示"
    QTest.keyClick(dlg.search_edit, Qt.Key_Escape)
    qapp.processEvents()
    assert not dlg.isVisible(), "Esc 没关掉命令面板"

    # 输入 → ↓ 进列表 → 回车执行。
    dlg2 = cp.CommandPalette(window=main_window, parent=main_window)
    dlg2.show()
    dlg2.activateWindow()
    qapp.processEvents()
    dlg2.search_edit.setFocus()
    dlg2.search_edit.setText("录音目录")
    qapp.processEvents()
    assert dlg2.visible_commands()[0].source == "settings:record_dir"

    QTest.keyClick(dlg2.search_edit, Qt.Key_Down)      # 进入列表
    qapp.processEvents()
    assert dlg2.focusWidget() is dlg2.list_widget

    QTest.keyClick(dlg2.list_widget, Qt.Key_Return)    # 回车执行
    qapp.processEvents()
    qapp.processEvents()
    assert dlg2.last_result.get("ok") is True, dlg2.last_result
    assert not dlg2.isVisible(), "执行成功后命令面板应关闭"

    dlg.deleteLater()
    dlg2.deleteLater()
    qapp.processEvents()


# ---------------------------------------------------------------------------
# 反证：注册真的在起作用（不是硬编码）
# ---------------------------------------------------------------------------
def test_removing_provider_registration_removes_its_results(
        monkeypatch, main_window):
    import command_palette as cp
    import search_providers as sp

    before = {c.source for c in cp.build_index(main_window)
              if c.group == "设置项"}
    assert before, "前置条件：注册表里本该有设置项"

    kept = tuple(p for p in sp.PROVIDERS if p.group != "设置项")
    monkeypatch.setattr(sp, "PROVIDERS", kept)

    index = cp.build_index(main_window)
    after = {c.source for c in index if c.group == "设置项"}
    assert after == set(), f"去掉注册后设置项结果没消失：{sorted(after)[:5]}"
    assert {c.group for c in index} >= {"菜单", "页签"}, (
        "去掉一类注册不应把别的搜索源一起弄没")
