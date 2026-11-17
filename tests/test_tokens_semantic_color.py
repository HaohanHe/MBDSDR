# SPDX-License-Identifier: MIT
"""``tokens().semantic_color`` 的口径（tests/test_tokens_semantic_color.py）
========================================================================

绘制路径不再直读全局 ``COLORS``，统一走 ``semantic_color(key)``。它的约定
只有三条，全部来自 ``status_color`` 的既有口径：

1. **当前主题优先**：主题调色板里有这个键（accent/success/danger/grid…），
   就返回当前主题的值——浅色主题的 accent 是压暗档，深色主题是提亮档，
   不能拿一个值硬扛两套底。
2. **中性档回落全局**：``gray_400`` / ``card_2`` 这类两套主题共用的中性色
   不在主题调色板里，回落 ``COLORS``，保持像素不变。
3. **未知键响亮失败**：点错 spell 不许静默返回空串（那会画出透明线）。

反证：把实现改回「总是 COLORS[key]」→ 用例 1 红；把未知键改成返回 "" →
用例 3 红。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tokens import tokens  # noqa: E402


@pytest.fixture()
def t():
    tk = tokens()
    original = getattr(tk, "_theme_name", "")
    yield tk
    tk.set_theme_name(original)


def test_accent_follows_current_theme(t):
    """同一 key 在浅/深两套主题下必须给出各自的值（不是全局那一个）。"""
    t.set_theme_name("default")
    light = t.semantic_color("accent")
    t.set_theme_name("dark")
    dark = t.semantic_color("accent")

    assert light == t.theme_colors("default")["accent"]
    assert dark == t.theme_colors("dark")["accent"]
    assert light != dark, "accent 没有随主题切换，浅色主题会拿到深色值"
    assert dark != t.COLORS["accent"] or light != t.COLORS["accent"]


def test_grid_comes_from_theme_palette(t):
    """grid 只在主题调色板里：没有主题时也要回落默认调色板，不能 KeyError。"""
    t.set_theme_name("dark")
    assert t.semantic_color("grid") == t.theme_colors("dark")["grid"]
    t.set_theme_name("")
    assert t.semantic_color("grid") == t.theme_colors("default")["grid"]


def test_neutral_steps_fall_back_to_global(t):
    """gray_400/card_2 不在主题调色板里，回落全局 COLORS（像素不变）。"""
    for theme in ("", "default", "dark", "high_contrast"):
        t.set_theme_name(theme)
        assert t.semantic_color("gray_400") == t.COLORS["gray_400"]
        assert t.semantic_color("card_2") == t.COLORS["card_2"]


def test_unknown_key_fails_loudly(t):
    """拼错/不存在的 token 必须抛 KeyError，不能返回空串画出透明线。"""
    t.set_theme_name("dark")
    with pytest.raises(KeyError):
        t.semantic_color("accentt")
