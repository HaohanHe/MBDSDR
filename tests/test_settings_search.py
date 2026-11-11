# SPDX-License-Identifier: MIT
"""
设置项搜索框钉扎测试 (tests/test_settings_search.py)
===================================================

W58：设置面板上方加搜索框，输入即过滤、清空即恢复。本文件钉住四条：

  1. **覆盖全量 53 项**：本页渲染 38 行 + 服务设置 4 项 + 内部键 11 项 = 53。
     后两类改不了但必须搜得到——报「无匹配」等于把「改不了」说成「不存在」。
  2. **过滤是真的**：命中项每个都真的含该词（不是把整组留下充数）；
     不存在的词必须显式说「无匹配」，不能留一片空白。
  3. **清空即恢复**：行、分组、展开状态回到搜索前，不留空组。
  4. **键盘可达**：Tab 从搜索框直达第一项；Esc 清空；回车跳到首项。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import settings_panel  # noqa: E402
from config_schema import ALL_ITEMS, GROUP_ORDER, SCHEMA  # noqa: E402

#: 6 个带 apply_note 的项（重连 / 重启服务 / 下次录音，不是重启应用）。
APPLY_NOTE_KEYS = frozenset((
    "gnss_port", "gnss_baudrate", "perf_profile", "record_dir",
    "rc_port", "web_port",
))


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def panel(qapp):
    p = settings_panel.SettingsPanel()
    p.show()
    p.activateWindow()
    QTest.qWait(50)
    yield p
    p.close()
    # _on_group_expanded 用 singleShot(0) 报高度：面板销毁后事件才到会报
    # 「C++ object already deleted」。测试里先跑完事件队列再销毁。
    qapp.processEvents()


def _visible_groups(panel):
    return [g for g, section in panel._group_sections.items()
            if not section.isHidden()]


def _hidden_rows(panel):
    return [k for k, box in panel._row_boxes.items() if box.isHidden()]


# ---------------------------------------------------------------------------
# 1. 索引覆盖全量 53 项
# ---------------------------------------------------------------------------
def test_index_covers_every_schema_item(panel):
    """本页 42 行 + 其余 schema 键（内部运行态等）全部可检索，一个都不许漏。"""
    indexed = set(panel._search_index) | set(panel._other_index)
    assert indexed == {it.key for it in ALL_ITEMS}
    assert len(panel._search_index) == 42
    assert len(indexed) == len(ALL_ITEMS)
    # 4 个服务开关项现在并入「设备与接口」，直接可检索
    for key in ("rc_enabled", "rc_port", "web_enabled", "web_port"):
        assert key in panel._search_index


def test_six_apply_note_items_are_all_searchable(panel):
    """6 项 apply_note 全部可检索（W51 后服务开关也并入本页）。"""
    for key in APPLY_NOTE_KEYS:
        assert key in panel._search_index or key in panel._other_index
        text = panel._search_index.get(key) or panel._other_index.get(key)
        assert SCHEMA[key].apply_note.lower() in text


def test_every_item_label_finds_something(panel):
    """按每一项的 label 搜，都必须有非空结果（命中行或如实点名在别处）。"""
    for item in ALL_ITEMS:
        # 10 个内部运行态键 label 为空串，按键名搜；其余按界面上看到的名称搜。
        term = item.label or item.key
        # 两项可能同 label（如「中心频率」），重复 setText 不会触发 textChanged
        panel.search_edit.clear()
        panel.search_edit.setText(term)
        text = panel.search_status.text()
        assert text, f"{item.key} 搜索后状态为空"
        assert "无匹配" not in text, f"{item.key}（{term}）被当成不存在"
        assert item.key in panel._search_hits or term in text


# ---------------------------------------------------------------------------
# 2. 过滤是真的（反证）
# ---------------------------------------------------------------------------
def test_baudrate_search_hits_really_contain_it(panel):
    """「波特率」只出现在设置里：命中数 >0，且每一项都真的含这个词。"""
    panel.search_edit.setText("波特率")
    hits = panel._search_hits
    assert hits, "搜「波特率」应当有命中"
    assert "gnss_baudrate" in hits
    for key in hits:
        assert "波特率" in panel._search_index[key], f"{key} 不含该词却留在结果里"
        item = SCHEMA[key]
        haystack = " ".join((item.label, item.help, item.apply_note,
                             item.restart_note))
        assert "波特率" in haystack


def test_group_titles_follow_visible_rows_and_never_empty(panel):
    """分组标题随可见项收起/展开，且不出现「标题在、内容空」的空组。"""
    panel.search_edit.setText("波特率")
    hit_groups = {panel._key_section[k] for k in panel._search_hits}
    for group, section in panel._group_sections.items():
        if group in hit_groups:
            assert not section.isHidden(), f"{group} 有命中却被藏起来"
            assert section.is_expanded(), f"{group} 有命中却没展开"
        else:
            assert section.isHidden(), f"{group} 无命中却还占着一行标题"
    assert _hidden_rows(panel), "过滤后不该所有行都还在"


def test_unknown_word_reports_explicit_no_match(panel):
    """搜不存在的词：显式「无匹配」，不是一片空白。"""
    panel.search_edit.setText("zzz-不存在的设置项-zzz")
    assert not panel._search_hits
    assert panel.search_status.isVisible()
    assert panel.search_status.text().startswith("无匹配")
    assert _visible_groups(panel) == []
    assert len(_hidden_rows(panel)) == len(panel._row_boxes)


# ---------------------------------------------------------------------------
# 3. 清空即恢复
# ---------------------------------------------------------------------------
def test_clear_restores_rows_groups_and_expanded_state(panel):
    before_expanded = {g: s.is_expanded()
                       for g, s in panel._group_sections.items()}
    panel.search_edit.setText("波特率")
    assert _hidden_rows(panel)

    panel.search_edit.clear()
    assert _hidden_rows(panel) == []
    assert len(_visible_groups(panel)) == len(panel._group_sections)  # 6 节全在
    assert {g: s.is_expanded()
            for g, s in panel._group_sections.items()} == before_expanded
    assert panel._search_hits == set()
    assert not panel.search_status.isVisible()


def test_non_schema_extras_hide_and_come_back(panel):
    """存储页的路径/导入导出按钮、外观方块都不是 schema 行，也要跟着过滤。"""
    extras = panel._search_extras
    assert extras.get("数据与操作"), "数据页附加控件没有登记"
    panel.search_edit.setText("波特率")
    assert all(w.isHidden() for w in extras["数据与操作"])
    panel.search_edit.clear()
    assert not any(w.isHidden() for w in extras["数据与操作"])


# ---------------------------------------------------------------------------
# 4. 键盘可达
# ---------------------------------------------------------------------------
def test_tab_from_search_reaches_first_item(panel):
    first_key = panel._group_keys["外观与语言"][0]
    panel.search_edit.setFocus()
    QTest.qWait(20)
    assert panel.focusWidget() is panel.search_edit
    QTest.keyClick(panel.search_edit, Qt.Key_Tab)
    assert panel.focusWidget() is panel._rows[first_key]


def test_escape_clears_the_search(panel):
    panel.search_edit.setFocus()
    QTest.qWait(20)
    panel.search_edit.setText("波特率")
    assert panel._search_hits
    QTest.keyClick(panel.search_edit, Qt.Key_Escape)
    assert panel.search_edit.text() == ""
    assert panel._search_hits == set()


def test_enter_jumps_to_first_match(panel):
    panel.search_edit.setText("波特率")
    panel.search_edit.setFocus()
    QTest.qWait(20)
    QTest.keyClick(panel.search_edit, Qt.Key_Return)
    assert panel.focusWidget() is panel._rows["gnss_baudrate"]
