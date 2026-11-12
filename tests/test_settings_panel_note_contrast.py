#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""设置面板提示标签的字色验收（主题入口 + 正文级对比度）。

config_schema 里有 8 个设置项会在行内渲染提示标签：2 个 requires_restart
（ui_language / log_level）+ 6 个 apply_note（gnss_port / gnss_baudrate /
perf_profile / record_dir / rc_port / web_port）。这些标签原先把 ``#D4956A``
写死在 settings_panel 里，在浅色主题 ``#F5F3EF`` 底上只有 2.29:1——属于
看不清的级别。

本测试钉住三件事：
  1. 字色取自 ``tokens().status_color("warning")``（主题入口），不写死色值；
  2. 每套主题（含浅色 default / 深色 dark）该色对主题底色 ≥ 4.5:1；
  3. 变异反证：把字色改回 ``#D4956A``，同一检查必须红；改回即绿。
"""
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import settings_panel as sp  # noqa: E402
from config_schema import ALL_ITEMS  # noqa: E402
from themes import THEMES, active_theme_name, contrast_ratio, set_active_theme  # noqa: E402
from tokens import tokens  # noqa: E402

#: 正文级 WCAG AA 门槛。
MIN_RATIO = 4.5
#: 回归基线：这个值在浅色底上只有 2.29:1，任何一处退回它都必须被测出来。
OLD_HEX = "#D4956A"


def _note_items():
    """渲染提示标签的设置项（requires_restart 与 apply_note 二者取一）。"""
    return [it for it in ALL_ITEMS
            if it.requires_restart or getattr(it, "apply_note", "")]


def _color_of(label) -> str:
    """从 QSS 里取出标签字色（没写颜色直接失败，不静默跳过）。"""
    m = re.search(r"color\s*:\s*(#[0-9A-Fa-f]{6})", label.styleSheet())
    assert m, f"提示标签没有设置字色：{label.styleSheet()!r}"
    return m.group(1).upper()


@pytest.fixture(scope="module")
def panel():
    previous = active_theme_name()
    p = sp.SettingsPanel(settings=None)
    p._ensure_ui_built()
    yield p
    set_active_theme(previous)
    p.deleteLater()


def _check_note_colors(panel, theme: str):
    """同一套门禁：字色 == 主题入口取值，且对主题底色 ≥ 4.5:1。"""
    set_active_theme(theme)
    panel._sync_note_colors()
    expected = tokens().status_color("warning").upper()
    bg = tokens().theme_colors(theme)["bg"]
    ratio = contrast_ratio(expected, bg)
    assert ratio >= MIN_RATIO, (
        f"提示字色 {expected} 在 {theme} 主题底色 {bg} 上只有 {ratio:.2f}:1，"
        f"低于 {MIN_RATIO}:1")
    for label in panel._note_labels:
        assert _color_of(label) == expected, (
            f"提示标签字色 {_color_of(label)} 不是主题入口的 status_warning "
            f"{expected}（{theme}）")
    return expected, ratio


def test_one_note_label_per_schema_item(panel):
    """提示标签数量必须与 schema 里需要提示的项一一对应（守卫不空转）。"""
    expected_keys = {it.key for it in _note_items()}
    assert len(expected_keys) == 8, f"需要提示的设置项变成 {len(expected_keys)} 个"
    assert len(panel._note_labels) == len(expected_keys), (
        f"面板渲染了 {len(panel._note_labels)} 个提示标签，"
        f"schema 需要 {len(expected_keys)} 个")


def test_note_color_comes_from_theme_entry(panel):
    """字色来自 tokens 主题入口，不是 settings_panel 里写死的十六进制。"""
    expected, _ratio = _check_note_colors(panel, "default")
    assert expected != OLD_HEX, "提示字色退回了看不清的旧值 #D4956A"


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_note_color_passes_contrast_in_every_theme(panel, theme):
    """浅色与深色（及其它主题）都要过正文级 4.5:1。"""
    _check_note_colors(panel, theme)


def test_old_hex_fails_contrast_on_light_bg():
    """回归基线：旧值在浅色底上确实不达标（2.29:1）。"""
    bg = tokens().theme_colors("default")["bg"]
    ratio = contrast_ratio(OLD_HEX, bg)
    assert ratio < MIN_RATIO, f"旧值 {OLD_HEX} 在 {bg} 上 {ratio:.2f}:1，前提已变"


def test_mutation_old_hex_turns_check_red_then_green(panel, monkeypatch):
    """变异反证：字色退回 #D4956A → 门禁红；恢复 → 绿。"""
    _check_note_colors(panel, "default")  # 基线：绿

    monkeypatch.setattr(
        sp.SettingsPanel, "_note_color", staticmethod(lambda: OLD_HEX))
    panel._sync_note_colors()
    with pytest.raises(AssertionError):
        _check_note_colors(panel, "default")

    monkeypatch.undo()
    _check_note_colors(panel, "default")  # 改回即绿
