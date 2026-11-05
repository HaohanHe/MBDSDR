"""可发现性护栏：能力目录 / 快捷键一览 / 三步上手必须与真实注册表一致。

钉住的红线（docs/discoverability.md）：
  * 目录里每个 source 都必须在对应注册表里真实存在，不许手抄会漂移的清单；
  * 触发不了的条目必须标 unavailable + 写原因，不许出现点了没反应的能力；
  * 快捷键面板的键位必须逐字等于 desktop/shortcuts.py，且每条恰好出现一次；
  * 三步上手引用的键位必须仍登记在 shortcuts.py；
  * 帮助面板非模态、默认不弹出，不遮挡主界面。

反证：删掉 panel_registry 的 summary、把 help_panel 换成硬编码清单、
给 shortcuts.py 的条目改名而不改面板、或让 PTT 能力失去 `tx_button`，
本文件都会红。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def catalog():
    from capability_catalog import build_catalog
    return build_catalog()


def _by_prefix(items, prefix):
    return [x for x in items if x.source.startswith(prefix)]


# ---------------------------------------------------------------------------
# 目录 = 注册表
# ---------------------------------------------------------------------------
def test_catalog_sources_come_from_real_registries(catalog):
    from panel_registry import registry
    import mode_registry
    from mbdsdr_ai import decoder_registry
    from mbdsdr_ai.decode_router import DIGITAL_MODE_CAPABILITIES
    from mbdsdr_ai.core.mcp_server import ALL_DOMAIN_NAMES
    from capability_catalog import SHORTCUT_CAPABILITIES

    panel_ids = {s.id for s in registry().list()}
    assert ({x.source.split(":", 1)[1] for x in _by_prefix(catalog, "panel_registry:")}
            == panel_ids)
    assert ({x.source.split(":", 1)[1] for x in _by_prefix(catalog, "mode_registry:")}
            == {m.mode for m in mode_registry.list_modes()})
    assert ({x.source.split(":", 1)[1] for x in _by_prefix(catalog, "decode_router:")}
            == set(DIGITAL_MODE_CAPABILITIES))
    assert ({x.source.split(":", 1)[1] for x in _by_prefix(catalog, "decoder_registry:")}
            == {d.mode for d in decoder_registry.list_decoders()})
    assert ({x.source.split(":", 1)[1] for x in _by_prefix(catalog, "mcp:")}
            == set(ALL_DOMAIN_NAMES))
    assert ({x.source.split(":", 1)[1] for x in _by_prefix(catalog, "shortcut:")}
            == {sid for sid, _c, _d in SHORTCUT_CAPABILITIES})


def test_every_panel_row_uses_registry_summary(catalog):
    from panel_registry import registry
    specs = {s.id: s for s in registry().list()}
    for cap in _by_prefix(catalog, "panel_registry:"):
        spec = specs[cap.source.split(":", 1)[1]]
        assert cap.title == spec.name
        assert spec.summary, f"面板 {spec.id} 缺 summary，能力目录会变成空话"
        assert cap.description == spec.summary


def test_catalog_has_all_categories(catalog):
    from capability_catalog import CATEGORIES
    seen = {x.category for x in catalog}
    assert seen == set(CATEGORIES)


# ---------------------------------------------------------------------------
# 诚实性：不可用必须写原因；可用必须有真实触发
# ---------------------------------------------------------------------------
def test_status_and_reason_contract(catalog):
    from capability_catalog import CATEGORIES
    for cap in catalog:
        assert cap.category in CATEGORIES
        assert cap.status in ("available", "conditional", "unavailable")
        if cap.status == "unavailable":
            assert cap.note, f"{cap.title} 标了不可用却没写原因"
            assert cap.trigger.startswith("无"), f"{cap.title} 不可用却给了入口"
        if cap.status == "conditional":
            assert cap.note, f"{cap.title} 需条件却没写条件"
        if cap.status == "available":
            assert cap.trigger and not cap.trigger.startswith("无"), \
                f"{cap.title} 写可用却没有触发方式"


def test_unwired_digital_modes_are_marked_unavailable(catalog):
    from mbdsdr_ai.decode_router import DIGITAL_MODE_CAPABILITIES
    rows = {x.title: x for x in _by_prefix(catalog, "decode_router:")}
    for key, cap in DIGITAL_MODE_CAPABILITIES.items():
        row = rows[cap.mode.upper()]
        if not cap.dependency:
            assert row.status == "unavailable"
            assert "not_implemented" in row.note
        else:
            assert row.status == "available"
            assert "--decode-file" in row.trigger


def test_decoder_registry_reports_wiring_gap(catalog):
    rows = {x.title: x for x in _by_prefix(catalog, "decoder_registry:")}
    assert rows["VOR"].status == "unavailable"
    assert "未接线" in rows["VOR"].note


def test_static_capabilities_symbols_exist(catalog):
    """无注册表可查的两条：用源码符号反证，不靠口说。"""
    sky = [x for x in catalog if x.source.endswith(":_build_search_widgets")]
    tx = [x for x in catalog if x.source.endswith(":_tx_block_reason")]
    assert sky and tx

    with open(os.path.join(_ROOT, "desktop", "rf_sky_view.py"), encoding="utf-8") as f:
        assert "_build_search_widgets" in f.read()
    with open(os.path.join(_ROOT, "desktop", "main_window.py"), encoding="utf-8") as f:
        src = f.read()
    assert "self.tx_button" in src and "_tx_block_reason" in src


def test_unavailable_panels_would_be_caught(qtapp):
    """不存在于注册表的面板不能出现在目录（防止手抄旧清单）。"""
    from panel_registry import registry
    ids = {s.id for s in registry().list()}
    from capability_catalog import build_catalog
    for cap in build_catalog():
        if cap.source.startswith("panel_registry:"):
            assert cap.source.split(":", 1)[1] in ids


# ---------------------------------------------------------------------------
# 搜索 / 分类过滤
# ---------------------------------------------------------------------------
def test_search_and_category_filter(catalog):
    from capability_catalog import search_capabilities
    assert len(search_capabilities(catalog, "")) == len(catalog)
    ft8 = search_capabilities(catalog, "ft8")
    assert ft8 and any("FT8" in x.title for x in ft8)
    spec = search_capabilities(catalog, "频谱")
    assert any("频谱" in x.title for x in spec)
    decode_only = search_capabilities(catalog, "", "解码")
    assert decode_only and all(x.category == "解码" for x in decode_only)
    assert not search_capabilities(catalog, "不存在的词_zzz")


# ---------------------------------------------------------------------------
# 快捷键面板：单一事实源
# ---------------------------------------------------------------------------
def test_shortcuts_panel_is_generated_from_single_source():
    from shortcuts import SHORTCUTS
    from shortcuts_panel import grouped_shortcuts, key_text, trigger_text

    rows = [s for _g, group in grouped_shortcuts() for s in group]
    assert len(rows) == len(SHORTCUTS)
    assert {s.id for s in rows} == {s.id for s in SHORTCUTS}
    for s in rows:
        assert key_text(s) == (s.key or "无快捷键")
        assert s.label in trigger_text(s)
    # 无键项必须有原因（否则面板会出现「无快捷键」但不知为什么）
    for s in SHORTCUTS:
        if not s.key:
            assert s.note, f"{s.id} 无键也无说明"


def test_shortcut_search_filters():
    from shortcuts_panel import grouped_shortcuts
    rows = [s for _g, group in grouped_shortcuts("录音") for s in group]
    assert rows and all("录音" in (s.label + s.note + (s.key or "")) for s in rows)
    assert not grouped_shortcuts("不存在_zzz")


# ---------------------------------------------------------------------------
# 三步上手
# ---------------------------------------------------------------------------
def test_onboarding_steps_use_registered_shortcuts():
    from help_panel import onboarding_steps
    from shortcuts import BY_ID
    steps = onboarding_steps()
    assert len(steps) == 3
    ids = [s["shortcut_id"] for s in steps if s["shortcut_id"]]
    for sid in ids:
        assert sid in BY_ID
        assert BY_ID[sid].key
        assert BY_ID[sid].key in next(s["body"] for s in steps
                                      if s["shortcut_id"] == sid)
    assert "伪造" in steps[0]["body"]  # 明确不演示假数据


# ---------------------------------------------------------------------------
# Qt 冒烟
# ---------------------------------------------------------------------------
def test_help_panel_smoke(qtapp, catalog):
    from help_panel import HelpPanel
    dlg = HelpPanel(initial_tab=1)
    assert not dlg.isModal()          # 不强制弹窗
    assert not dlg.isVisible()        # 构造时不抢焦点
    assert dlg.visible_row_count() == len(catalog)

    dlg.search_edit.setText("ft8")
    assert 0 < dlg.visible_row_count() < len(catalog)

    dlg.search_edit.setText("不存在的词_zzz")
    assert dlg.visible_row_count() == 0
    assert dlg.tree.isHidden()            # 无结果时列表隐藏
    assert not dlg.empty_label.isHidden()  # 空态文案出现
    assert "换个关键词" in dlg.empty_label.text()
    dlg.deleteLater()


def test_shortcuts_panel_smoke(qtapp):
    from shortcuts_panel import ShortcutsPanel
    from shortcuts import SHORTCUTS
    dlg = ShortcutsPanel()
    assert not dlg.isModal()
    assert not dlg.isVisible()
    assert sum(t.childCount() for t in
               (dlg.tree.topLevelItem(i)
                for i in range(dlg.tree.topLevelItemCount()))) == len(SHORTCUTS)
    dlg.search_edit.setText("卫星")
    assert 0 < sum(t.childCount() for t in
                   (dlg.tree.topLevelItem(i)
                    for i in range(dlg.tree.topLevelItemCount())))
    dlg.deleteLater()


def test_help_menu_is_wired_in_main_window():
    with open(os.path.join(_ROOT, "desktop", "main_window.py"), encoding="utf-8") as f:
        src = f.read()
    for label in ("三步上手", "能力目录", "快捷键一览"):
        assert f'QAction("{label}"' in src
    assert "_show_help_panel(0)" in src and "_show_help_panel(1)" in src
    assert "_show_shortcuts_panel" in src
