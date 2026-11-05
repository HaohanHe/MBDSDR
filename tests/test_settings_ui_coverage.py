#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
schema ↔ UI 双向一致性守卫（tests/test_settings_ui_coverage.py）
================================================================

这张网防的是"配置漂移"：schema 里有字段但用户改不到，或 UI 读某个键但
schema 不认它。四个方向各自钉住：

  1. 正向：`SCHEMA` 每个字段都有真实 UI 入口，或登记了明确理由；
     缺任一 → 红，并打印「字段 → 位置」表。
  2. 反向：`desktop/` 里所有 `settings.get("k")` / `settings.set("k")` /
     `_settings[...]` 的字面键必须在 SCHEMA 声明；未声明（幽灵键）→ 红，
     并打印「键 → 位置 → 是否在 schema」表。
  3. 真实控件：实际构造设置面板/服务面板（offscreen），逐项断言
     `_rows` 里有控件且控件类型与 `kind` 匹配（枚举项数 == choices 数）。
  4. 往返：每个 settings 后端字段 `set → flush → 新实例 load → 读回`，
     断言值、迁移说明、其他字段未丢；禁止只读自己写的那份 JSON 对象。
     另外对 13 个枚举验证"手改写法 → 归一留痕 → 落盘后规范值持久化"。

计数（字段数 / 有 UI 入口数 / 幽灵键数）不写死在测试里：由
`scripts/settings_inventory.py` 生成并写进 `docs/settings-inventory.md`，
本文件的 `test_inventory_table_matches_script` 整块比对，手改即红。
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import pytest  # noqa: E402

from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox,
)

import config_schema  # noqa: E402
import settings_inventory  # noqa: E402
import settings_panel  # noqa: E402
from config_schema import EXPOSED_ITEMS, GROUP_ORDER, SCHEMA, validate_value  # noqa: E402
from settings import DesktopSettings  # noqa: E402

_SETTINGS_ITEMS = tuple(it for it in config_schema.ALL_ITEMS
                        if it.storage == "settings")
_ENUM_ITEMS = tuple(it for it in config_schema.ALL_ITEMS if it.kind == "enum")


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def _probe_value(item):
    """一个与默认值不同、且本身合法的探针值（用于往返）。"""
    if item.kind == "bool":
        return not bool(item.default)
    if item.kind == "enum":
        for value in item.choice_values:
            if value != item.default:
                return value
        return item.default
    if item.kind == "text":
        return f"roundtrip-{item.key}"
    base = float(item.default)
    step = float(item.step or 1.0)
    candidate = base + step
    if item.maximum is not None and candidate > float(item.maximum):
        candidate = base - step
    if item.minimum is not None and candidate < float(item.minimum):
        candidate = base
    return int(round(candidate)) if item.kind == "int" else candidate


def _expected_widget_kind(item) -> str:
    if item.kind == "bool":
        return "check"
    if item.kind == "enum":
        return "combo"
    if item.kind == "int":
        return "spin"
    if item.kind == "float":
        return "line" if item.default is None else "double"
    return "line"


def _widget_kind(widget) -> str:
    if isinstance(widget, QCheckBox):
        return "check"
    if isinstance(widget, QComboBox):
        return "combo"
    if isinstance(widget, QSpinBox):
        return "spin"
    if isinstance(widget, QDoubleSpinBox):
        return "double"
    if isinstance(widget, QLineEdit):
        return "line"
    return type(widget).__name__


# ---------------------------------------------------------------------------
# 1. 正向：字段 → UI 入口（或明确理由）
# ---------------------------------------------------------------------------
def test_forward_every_field_has_ui_entry_or_reason():
    report = settings_inventory.measure()
    print("\n== 正向：字段 → UI 入口 ==")
    print(settings_inventory.render_forward_table(report))
    assert not report.missing, (
        "以下 schema 字段既没有 UI 入口也没有登记理由（配置漂移源头）：\n"
        "  " + "、".join(report.missing) +
        "\n请在 desktop/ 补控件，或在此处 INTERNAL_REASONS 写明理由。")
    assert report.fields == len(config_schema.ALL_ITEMS)
    assert report.ui_entries + report.reasons == report.fields


# ---------------------------------------------------------------------------
# 2. 反向：幽灵键（设置读取未在 schema 声明）
# ---------------------------------------------------------------------------
def test_reverse_no_ghost_settings_keys():
    report = settings_inventory.measure()
    print("\n== 反向：键 → 位置 → 是否在 schema ==")
    print(settings_inventory.render_reverse_table(report))
    assert not report.ghosts, (
        "desktop/ 读写了未在 config_schema 声明设置的键（幽灵键）：\n" + "\n".join(
            f"  {h.key} @ {h.path}:{h.line} ({h.access})" for h in report.ghosts) +
        "\n要么在 schema 声明（并说明默认值出处），要么删掉该读取。")
    for hit in report.reverse_rows:
        assert hit.key in SCHEMA, f"{hit.path}:{hit.line} 的键 {hit.key} 不在 schema"


# ---------------------------------------------------------------------------
# 3. 真实控件：构造面板，逐项断言控件存在且类型匹配
# ---------------------------------------------------------------------------
def test_settings_panel_builds_real_widget_for_every_exposed_item(
        tmp_path, qapp, monkeypatch):
    monkeypatch.setattr(settings_panel, "AI_CONFIG_PATH", str(tmp_path / "ai.json"))
    monkeypatch.setattr(settings_panel, "GUI_CONFIG_PATH", str(tmp_path / "gui.json"))
    ds = DesktopSettings(path=str(tmp_path / "desktop_settings.json"))
    panel = settings_panel.SettingsPanel(settings=ds)      # host=None：只验证渲染
    svc = settings_panel.ServiceSettingsPanel(settings=ds)
    try:
        problems = []
        for item in EXPOSED_ITEMS:
            assert item.group in GROUP_ORDER, (
                f"{item.key} 的分组「{item.group}」不在 GROUP_ORDER，永远不会渲染")
            if item.group == "服务与远程访问":
                attr = settings_inventory.SERVICE_WIDGETS.get(item.key)
                widget = getattr(svc, attr, None) if attr else None
                location = "ServiceSettingsPanel." + str(attr)
            else:
                widget = panel._rows.get(item.key)
                location = "SettingsPanel._rows"
            if widget is None:
                problems.append(f"{item.key}: {location} 无控件")
                continue
            want = _expected_widget_kind(item)
            got = _widget_kind(widget)
            if got != want:
                problems.append(f"{item.key}: 期望 {want} 实得 {got}（{location}）")
            elif want == "combo" and widget.count() != len(item.choice_values):
                problems.append(
                    f"{item.key}: 下拉 {widget.count()} 项 != choices "
                    f"{len(item.choice_values)} 项")
        assert not problems, "设置面板控件与 schema 不一致：\n" + "\n".join(problems)
    finally:
        panel.deleteLater()
        svc.deleteLater()


# ---------------------------------------------------------------------------
# 4a. 往返：set → flush → 新实例 load → 读回
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("key", [it.key for it in _SETTINGS_ITEMS])
def test_settings_field_roundtrips_via_flush_and_fresh_load(key, tmp_path, qapp):
    item = SCHEMA[key]
    probe = _probe_value(item)
    expected_norm, reason = validate_value(item, probe)
    assert reason is None, f"探针值 {probe!r} 对 {key} 本身应合法，却产生说明：{reason}"

    path = str(tmp_path / "desktop_settings.json")
    ds = DesktopSettings(path=path)
    ds.set(key, probe)
    ds.flush()

    # 必须是新实例重新读文件，不能复用 ds（否则只验证了自己的内存）
    loaded = DesktopSettings.load(path=path)
    assert loaded.get(key) == expected_norm, (
        f"{key}: 落盘 {probe!r} 读回 {loaded.get(key)!r}，期望 {expected_norm!r}")
    assert not loaded.migration_notes, (
        f"{key}: 合法值往返不该出现迁移说明：{loaded.migration_notes!r}")

    expected_all = dict(config_schema.defaults("settings"))
    expected_all[key] = expected_norm
    assert loaded.values() == expected_all, (
        f"{key}: 往返丢了/改了其他字段：" +
        str({k: (v, expected_all.get(k)) for k, v in loaded.values().items()
              if v != expected_all.get(k)}))


# ---------------------------------------------------------------------------
# 4b. 枚举：手改写法 → 归一留痕 → 落盘后规范值持久化
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("key", [it.key for it in _ENUM_ITEMS])
def test_enum_raw_edit_normalizes_then_persists(key, tmp_path, qapp):
    item = SCHEMA[key]
    canonical = _probe_value(item)
    if isinstance(canonical, str):
        raw = f"  {canonical.lower()}  "
    else:
        raw = f"  {canonical}  "
    assert raw != canonical, f"{key}: 构造的写法变体与原值相同，测不到归一"

    path = str(tmp_path / "hand_edited.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({key: raw}, f)

    loaded = DesktopSettings.load(path=path)
    notes = " ".join(loaded.migration_notes or [])
    assert loaded.get(key) == canonical, (
        f"{key}: 写法变体 {raw!r} 读回 {loaded.get(key)!r}，期望归一为 {canonical!r}")
    assert loaded.migration_notes and "归一" in notes and raw.strip() in notes, (
        f"{key}: 归一没有留痕（{loaded.migration_notes!r}）——用户必须知道文件被改写")

    # 归一后的规范值再落盘，新实例读回：值稳定，且不再需要归一说明。
    loaded.set(key, canonical)
    loaded.flush()
    again = DesktopSettings.load(path=path)
    assert again.get(key) == canonical
    assert not again.migration_notes, (
        f"{key}: 规范值二次读回仍在归一：{again.migration_notes!r}")

    expected_all = dict(config_schema.defaults("settings"))
    expected_all[key] = canonical
    assert again.values() == expected_all, f"{key}: 归一落盘丢了其他字段"


# ---------------------------------------------------------------------------
# 5. 文档数字表由脚本生成，手改即红
# ---------------------------------------------------------------------------
def test_inventory_table_matches_script():
    report = settings_inventory.measure()
    with open(settings_inventory.DOC_PATH, "r", encoding="utf-8") as f:
        doc = f.read()
    current = settings_inventory.extract_block(doc)
    assert current is not None, (
        "docs/settings-inventory.md 缺少由脚本生成的数字块；"
        "运行 python scripts/settings_inventory.py --write")
    assert current == settings_inventory.render_block(report), (
        "docs/settings-inventory.md 的数字表与脚本盘点结果不一致"
        "（手改即红）：运行 python scripts/settings_inventory.py --write")
    assert report.fields == len(config_schema.ALL_ITEMS)
    assert report.source_commit in current
