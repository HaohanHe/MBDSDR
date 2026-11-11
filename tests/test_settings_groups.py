# SPDX-License-Identifier: MIT
"""
设置节 6 组折叠钉扎测试 (tests/test_settings_groups.py)
======================================================

W51（D-10 裁定）：设置面板按**用途**切 6 节，折叠状态入库。本文件钉住：

  1. 6 节标题/用途/顺序固定；键集合恰好覆盖 42 个 exposed 项（不重不漏，
     漏一个就有设置项在面板上消失）。
  2. 组内顺序 = schema 顺序；行标题仍取自 schema（不改任何文案）。
  3. 折叠态落库：展开/收起后写 `settings_expanded_groups`，重建面板后还原。
  4. 坏值/未知标题/空值回落「只展开首组」；全收起用 `-` 记住。
  5. 标题可访问性：accessibleName=标题、accessibleDescription 含用途且随
     展开态更新，header.checked 与展开态一致。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

from PySide6.QtWidgets import QApplication, QGroupBox  # noqa: E402

import config_schema  # noqa: E402
import settings_panel as sp  # noqa: E402
from config_schema import ALL_ITEMS, EXPOSED_ITEMS, SCHEMA  # noqa: E402
from settings import DesktopSettings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def make_panel(qapp, tmp_path, monkeypatch, isolated_profile):
    """建面板：AI/GUI 配置都挪到 tmp，绝不碰用户真实配置。"""
    monkeypatch.setattr(sp, "AI_CONFIG_PATH", str(tmp_path / "ai.json"))
    monkeypatch.setattr(sp, "GUI_CONFIG_PATH", str(tmp_path / "gui.json"))
    (tmp_path / "ai.json").write_text("{}", encoding="utf-8")

    created = []

    def _make(store_name="desktop_settings.json"):
        # 必须走 load()：DesktopSettings(path=...) 只建内存默认值，不读盘，
        # 用它做「重建后还原」的断言会永远看到默认值。
        ds = DesktopSettings.load(path=str(tmp_path / store_name))
        panel = sp.SettingsPanel(settings=ds)
        created.append(panel)
        return ds, panel

    yield _make
    for panel in created:
        panel.close()
    qapp.processEvents()


#: 期望的 6 节（顺序也是契约）
EXPECTED_TITLES = ["外观与语言", "接收与频谱", "瀑布与标注",
                   "天文观测", "设备与接口", "数据与操作"]


def test_six_sections_exact_titles_and_key_coverage(make_panel):
    _ds, panel = make_panel()
    titles = [t for t, _s in panel.group_sections]
    assert titles == EXPECTED_TITLES

    covered = [k for t in titles for k in panel._group_keys[t]]
    assert len(covered) == len(set(covered)), "有设置项被分到两节"
    assert set(covered) == {it.key for it in EXPOSED_ITEMS}
    assert len(covered) == 42


def test_every_section_has_purpose_and_reaches_accessible_name(make_panel):
    _ds, panel = make_panel()
    for title, section in panel.group_sections:
        purpose = panel._section_purpose[title]
        assert purpose and len(purpose) <= 24      # 一句话
        header = section.header
        # accessibleName/Description 由 RightSection 维护；这里钉住它会播报
        assert header.accessibleName() == title
        state = "已展开" if section.is_expanded() else "已收起"
        assert title in header.accessibleDescription()
        assert state in header.accessibleDescription()
        assert header.toolTip() == purpose


def test_header_checked_matches_expanded_state(make_panel):
    _ds, panel = make_panel()
    for _title, section in panel.group_sections:
        assert section.header.isChecked() == section.is_expanded()


def test_rows_stay_in_schema_order_and_keep_schema_labels(make_panel):
    """组内顺序 = schema 顺序；行标题一律取自 schema（不改文案）。"""
    _ds, panel = make_panel()
    schema_order = [it.key for it in ALL_ITEMS]
    for title, _section in panel.group_sections:
        keys = panel._group_keys[title]
        assert keys == sorted(keys, key=schema_order.index)
        for key in keys:
            boxes = [b for b in panel._row_boxes[key].findChildren(QGroupBox)]
            assert panel._row_boxes[key].title() == SCHEMA[key].label
            assert boxes == []      # 行容器本身就是一个无嵌套的 QGroupBox


def test_default_expands_only_first_section(make_panel):
    _ds, panel = make_panel()
    expanded = [t for t, s in panel.group_sections if s.is_expanded()]
    assert expanded == ["外观与语言"]


def test_collapse_state_survives_rebuild(make_panel):
    ds, panel = make_panel()
    panel._group_sections["瀑布与标注"].set_expanded(True)
    panel._group_sections["外观与语言"].set_expanded(False)
    ds.flush()
    assert ds.get("settings_expanded_groups") == "瀑布与标注"

    _ds2, panel2 = make_panel()          # 同一个 store 文件
    expanded = [t for t, s in panel2.group_sections if s.is_expanded()]
    assert expanded == ["瀑布与标注"]


def test_all_collapsed_is_remembered_as_dash(make_panel):
    ds, panel = make_panel()
    for _t, section in panel.group_sections:
        section.set_expanded(False)
    ds.flush()
    assert ds.get("settings_expanded_groups") == "-"

    _ds2, panel2 = make_panel()
    assert not any(s.is_expanded() for _t, s in panel2.group_sections)


@pytest.mark.parametrize("bad", ["", "不存在的分组", "外观与语言,乱码", "   "])
def test_bad_or_unknown_value_falls_back_to_first_only(make_panel, bad):
    ds, _panel = make_panel()
    ds.set("settings_expanded_groups", bad)
    ds.flush()
    _ds2, panel2 = make_panel()
    expanded = [t for t, s in panel2.group_sections if s.is_expanded()]
    assert expanded == ["外观与语言"]


def test_search_expansion_does_not_overwrite_stored_state(make_panel):
    """搜索造成的自动展开不是用户选择，不许改写折叠习惯。"""
    ds, panel = make_panel()
    panel.search_edit.setText("波特率")
    for _t, section in panel._group_sections.items():
        section.set_expanded(True)
    assert ds.get("settings_expanded_groups") in ("", "外观与语言")
    assert panel._expanded_before_search is not None


def _sections_height(panel):
    """各节标题+正文的高度之和（折叠节的正文不参与）。"""
    total = 0
    for _t, section in panel.group_sections:
        lay = section.layout()
        if lay is not None:
            lay.invalidate()
        section.updateGeometry()
        total += section.sizeHint().height()
    return total


def test_section_height_shrinks_when_collapsed(make_panel):
    """折叠前后高度：全展开 vs 只展开首组（W51 的实测口径）。"""
    _ds, panel = make_panel()
    panel.resize(320, 2000)
    for _t, section in panel.group_sections:
        section.set_expanded(True)
    full = _sections_height(panel)

    for title, section in panel.group_sections:
        section.set_expanded(title == "外观与语言")
    collapsed = _sections_height(panel)

    assert collapsed < full, f"折叠没有变矮：{collapsed} vs {full}"
    # 每节标题 24px + 分隔线 1px：收起 5 节至少省掉 5 行标题
    assert full - collapsed >= 5 * 25
