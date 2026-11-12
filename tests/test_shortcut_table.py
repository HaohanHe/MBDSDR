# SPDX-License-Identifier: MIT
"""W104 守卫：快捷键文档表 + 键位冲突检测。

三件事各自可反证：
  1. `docs/shortcuts.md` 由注册表生成，**表行数 == 注册表条目数**（改表不改文档 → 红）；
  2. 冲突判据 = 同 scope 重复 **或** 两个非局部 scope 抢同一序列（注入重复 → 红）；
  3. 局部 scope 与全局同键仍然允许（A = 全天 / AM）——不为了「检查全绿」放宽成禁止。

运行:
    python -m pytest tests/test_shortcut_table.py -q
"""
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import pytest  # noqa: E402

import gen_shortcut_table  # noqa: E402
import shortcuts  # noqa: E402
from shortcuts import LOCAL_SCOPES, SHORTCUTS, Shortcut  # noqa: E402

_TABLE_PATH = os.path.join(_ROOT, "docs", "shortcuts.md")

#: 表行：第一列是 `登记 id`
_ROW_RE = re.compile(r"^\|\s*`([a-z][a-z0-9_]*\.[a-z0-9_.]+)`\s*\|", re.MULTILINE)


def _table_text() -> str:
    with open(_TABLE_PATH, encoding="utf-8") as fh:
        return fh.read()


def _table_ids():
    return _ROW_RE.findall(_table_text())


# ---------------------------------------------------------------------------
# 1. 表与注册表一致
# ---------------------------------------------------------------------------

def test_table_file_exists():
    assert os.path.exists(_TABLE_PATH), "docs/shortcuts.md 未生成（跑 scripts/gen_shortcut_table.py）"


def test_table_row_count_equals_registry_size():
    """表烂掉的第一种形态：加/删了登记，表没跟着变。"""
    assert len(_table_ids()) == len(SHORTCUTS)


def test_table_ids_match_registry_exactly():
    """只比数量不够——同数量下漏一条、多一条也会通过，所以还要比集合。"""
    assert set(_table_ids()) == {s.id for s in SHORTCUTS}


def test_table_is_up_to_date():
    """生成物必须与注册表渲染结果逐字一致（键位改动但没重跑脚本 → 红）。"""
    assert _table_text() == gen_shortcut_table.render()


def test_table_keeps_unbound_entries():
    """故意不配键的条目也要出现在表里，不能因为「没键」被过滤掉。"""
    ids = set(_table_ids())
    unbound = [s.id for s in SHORTCUTS if not (s.key or "").strip()]
    assert unbound, "当前注册表应存在故意不配键的条目（本断言防止测试失去意义）"
    assert set(unbound) <= ids


# ---------------------------------------------------------------------------
# 2. 冲突判据
# ---------------------------------------------------------------------------

def test_factory_registry_has_no_conflicts():
    """现状：出厂键位无冲突。若这条红了，说明真撞键，别放宽判据。"""
    assert shortcuts.conflicts({}) == []


def test_allowed_cross_scope_overlap_is_not_a_conflict():
    """A 同时是 view.all_sky（window）与 spectrum.mode.am（spectrum）——允许。"""
    assert "spectrum" in LOCAL_SCOPES
    overlaps = shortcuts.scope_overlaps({})
    pairs = {frozenset((a, b)) for _scope, _key, a, b in overlaps}
    assert frozenset(("view.all_sky", "spectrum.mode.am")) in pairs
    assert shortcuts.conflicts({}) == [], "局部与全局同键不得被算成冲突"


def test_same_scope_duplicate_is_reported(monkeypatch):
    """反证 A：同一 scope 下注入一条与已有键相同的注册 → 检查必须报。"""
    fake = Shortcut("file.connect_dup", "ctrl+q", "重复绑定（测试注入）")
    monkeypatch.setattr(shortcuts, "SHORTCUTS", SHORTCUTS + (fake,))

    found = shortcuts.conflicts({})
    ids = {pair for _scope, _key, a, b in found for pair in (a, b)}
    assert "file.connect_dup" in ids
    assert any(a == "file.quit" or b == "file.quit" for _s, _k, a, b in found)


def test_two_global_scopes_sharing_a_key_is_reported(monkeypatch):
    """反证 B：两个都非局部的 scope 绑同一序列 → 冲突（与装配顺序有关）。"""
    fake_a = Shortcut("headless.refresh", "Ctrl+Shift+J", "无头刷新", scope="window")
    fake_b = Shortcut("tray.toggle", "Ctrl+Shift+J", "托盘开关", scope="tray")
    monkeypatch.setattr(shortcuts, "SHORTCUTS", SHORTCUTS + (fake_a, fake_b))

    found = shortcuts.conflicts({})
    assert any(
        {a, b} == {"headless.refresh", "tray.toggle"} for _s, _k, a, b in found
    ), f"跨全局 scope 同键未报出：{found}"


def test_local_scope_duplicate_within_itself_is_reported(monkeypatch):
    """局部 scope 内部也不能重复（局部优先只解决「局部 vs 全局」）。"""
    fake = Shortcut("spectrum.mode.am_dup", "A", "AM 解调（测试注入）",
                    scope="spectrum")
    monkeypatch.setattr(shortcuts, "SHORTCUTS", SHORTCUTS + (fake,))

    found = shortcuts.conflicts({})
    assert any(
        {a, b} == {"spectrum.mode.am", "spectrum.mode.am_dup"}
        for _s, _k, a, b in found
    )


def test_user_override_conflict_is_reported():
    """用户把某条改成已占用的键 → 也要报（覆写层走同一判据）。"""
    trial = {"file.quit": "Ctrl+D"}
    found = shortcuts.conflicts(trial)
    assert any({a, b} == {"file.disconnect", "file.quit"} for _s, _k, a, b in found)


def test_unbound_overrides_are_not_conflicts():
    trial = {"file.quit": "", "file.disconnect": None}
    assert shortcuts.conflicts(trial) == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
