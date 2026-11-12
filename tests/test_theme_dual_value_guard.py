# SPDX-License-Identifier: MIT
"""同名键双值守卫（只拒绝新增，不评价既有）。

背景
----
``THEME_COLORS[*]`` 与全局 ``COLORS`` 各有一套同名键。两份值互不联动：改主题
那份，全局不动；改全局那份，五套主题都不动。更麻烦的是 ``status_color()``
把全局那份当作 ``KeyError`` 守卫（``tokens.py`` 里 ``key not in self.COLORS``
直接抛错），所以「0 读取」的全局键也不能随手删。

本测试把「同名不同值」钉成白名单：既有键显式登记并附一行理由；新增一个未登记
的双值键即红；白名单里的键名写错、或已经不再是双值（垃圾条目）也红。

口径
----
* 只统计两边都有的键（同名）；
* 任意一套主题的值与全局值不同，即视为双值（主题彼此不同不算，那是主题设计）；
* 比较前统一转小写，避免 ``#ABCDEF`` / ``#abcdef`` 这类大小写误报。

运行：
    python -m pytest tests/test_theme_dual_value_guard.py -q
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from tokens import tokens  # noqa: E402


#: 已登记的同名双值键。新增键必须在这里写明理由；键名写错也会被反证抓住。
DUAL_VALUE_EXEMPTIONS = {
    "accent": (
        "主题那份是界面强调面（QSS 5 处随主题）；全局 #919CAC 是固定深色岛屿的"
        "强调色，计划改名为 island_accent，不并入主题。"),
    "success": (
        "主题那份运行时 0 读取、只剩门禁 PAIRS 一处；全局 #6BA89A 有 20 处消费，"
        "待 A2 拍板后删主题副本。"),
    "danger": (
        "主题那份随主题供墨/边框（dark #D47070、hc #FF4444）；全局 #B85C5C 供"
        "23 处直读与红底，A1 迁移中，红底等新令牌。"),
    "status_warning": (
        "主题那份是状态文字真值（7 处 status_color）；全局 #C8A040 只剩"
        "main_window:5273 一处直读，删它须先改 status_color 守卫。"),
    "status_danger": (
        "主题那份经 status_color 被 4 处消费；全局 #C05050 零直读，但仍是"
        "status_color 的 KeyError 守卫前提。"),
}


def _theme_key_union(theme_colors: dict[str, dict[str, str]]) -> set[str]:
    """所有主题键名的并集。"""
    keys: set[str] = set()
    for palette in theme_colors.values():
        keys.update(palette)
    return keys


def _dual_value_keys(
    theme_colors: dict[str, dict[str, str]],
    colors: dict[str, str],
) -> dict[str, set[str]]:
    """返回 {键: 与全局值不同的主题值集合}，只统计两边同名的键。"""
    found: dict[str, set[str]] = {}
    for key in sorted(_theme_key_union(theme_colors) & set(colors)):
        global_value = str(colors[key]).lower()
        differing = {
            str(palette[key]).lower()
            for palette in theme_colors.values()
            if key in palette and str(palette[key]).lower() != global_value
        }
        if differing:
            found[key] = differing
    return found


def _unknown_exemptions(
    exemptions: dict[str, str],
    theme_colors: dict[str, dict[str, str]],
    colors: dict[str, str],
) -> set[str]:
    """豁免表里那些「两边根本不存在」的键名（写错名的主要信号）。"""
    theme_keys = _theme_key_union(theme_colors)
    return {key for key in exemptions if key not in colors or key not in theme_keys}


def test_no_unregistered_dual_value_key():
    """新增同名双值键必须显式登记，否则红。"""
    t = tokens()
    actual = _dual_value_keys(t.THEME_COLORS, t.COLORS)
    missing = sorted(set(actual) - set(DUAL_VALUE_EXEMPTIONS))
    detail = "\n".join(
        f"  {key}: 主题值 {sorted(actual[key])} vs 全局 {str(t.COLORS[key]).lower()}"
        for key in missing
    )
    assert not missing, (
        "发现未登记的同名键双值（要么在 DUAL_VALUE_EXEMPTIONS 登记并写理由，"
        "要么把两份值改成联动）：\n" + detail)


def test_exemption_table_has_no_stale_entry():
    """豁免表里的键必须仍然是双值；已改一致或已删键的条目要清掉。"""
    t = tokens()
    actual = _dual_value_keys(t.THEME_COLORS, t.COLORS)
    stale = sorted(set(DUAL_VALUE_EXEMPTIONS) - set(actual))
    assert not stale, (
        "豁免表里有条目已不再是双值（值已一致或键已删除），请删掉，"
        f"别让白名单变成垃圾场：{stale}")


def test_exemption_keys_exist_in_both_namespaces():
    """豁免键名必须同时存在于主题与全局；写错名字直接红。"""
    t = tokens()
    unknown = _unknown_exemptions(DUAL_VALUE_EXEMPTIONS, t.THEME_COLORS, t.COLORS)
    assert not unknown, (
        "豁免表里的键名在 THEME_COLORS / COLORS 中找不到（拼写错误或键已改名）："
        f"{sorted(unknown)}")


def test_counterproof_new_global_dual_key_is_detected():
    """反证：人为新增一个同名不同值的键，判定函数必须报出来且未登记。"""
    theme_colors = {
        "default": {"brand_new": "#112233"},
        "dark": {"brand_new": "#112233"},
    }
    colors = {"brand_new": "#445566"}
    assert "brand_new" in _dual_value_keys(theme_colors, colors)
    assert "brand_new" not in DUAL_VALUE_EXEMPTIONS


def test_counterproof_misspelled_exemption_is_detected():
    """反证：把豁免表里的键名改错，必须被「两边都不存在」检查抓住。"""
    t = tokens()
    bad = dict(DUAL_VALUE_EXEMPTIONS)
    bad["acccent"] = bad.pop("accent")
    unknown = _unknown_exemptions(bad, t.THEME_COLORS, t.COLORS)
    assert unknown == {"acccent"}


def test_counterproof_same_value_is_not_dual():
    """反证：两份值完全相同时不算双值，避免守卫误报。"""
    theme_colors = {"default": {"same": "#ABCDEF"}, "dark": {"same": "#abcdef"}}
    colors = {"same": "#ABCDEF"}
    assert _dual_value_keys(theme_colors, colors) == {}
