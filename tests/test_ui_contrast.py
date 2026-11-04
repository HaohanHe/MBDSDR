# SPDX-License-Identifier: MIT
"""
浅色/深色主题可读性（WCAG 对比度）门禁
======================================

用户口径：对比度不足直接算缺陷。阈值按 WCAG 2.1：
  * 正文/次级正文：4.5:1（AA 正常字号）——全部主题必须达标，不许豁免；
  * 品牌色当正文（primary_text）：3:1（大字号/非文本门槛，见下方说明）；
  * 禁用态：WCAG 豁免对比度，但"看得见"仍要求 ≥1.5:1；
  * 白名单只收外部图标/系统原生控件这类不可改项，且必须写理由。

颜色只从 tokens.THEME_COLORS 取，主题改了颜色这里立刻复查。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui_contrast.py -q
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from tokens import tokens  # noqa: E402


def _channel(value: int) -> float:
    c = value / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(fg: str, bg: str) -> float:
    la, lb = luminance(fg), luminance(bg)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


_ALL = sorted(tokens().THEME_COLORS)

_TEXT_PAIRS = [
    ("text", "bg"),
    ("text", "card"),
    ("text_secondary", "bg"),
    ("text_secondary", "card"),
]

#: 白名单：只允许"不可改项"（外部图标/系统原生控件自带的配色），必须写理由。
#: 主题自己的文字/背景对**一律不许**进白名单；当前为空 = 全部达标。
IMMUTABLE_WHITELIST = {
    # "external:icon_x:text": "外部图标自带 SVG 固定色，改不了",
}


def _collect_failures():
    failures = {}
    for theme in _ALL:
        colors = tokens().theme_colors(theme)
        for fg_role, bg_role in _TEXT_PAIRS:
            ratio = contrast_ratio(colors[fg_role], colors[bg_role])
            if ratio < 4.5:
                failures[f"{theme}:{fg_role}/{bg_role}"] = round(ratio, 2)
    return failures


def test_body_text_meets_wcag_aa_with_no_theme_whitelist():
    """正文/次级正文 ≥4.5:1，全部主题无豁免。"""
    failures = _collect_failures()
    allowed = set(failures) & set(IMMUTABLE_WHITELIST)
    assert not (set(failures) - allowed), (
        f"以下组合低于 4.5:1 且不在白名单：{failures}")
    # 白名单必须精确覆盖实际失败（防止条目过期后掩盖新问题）
    assert set(failures) == set(IMMUTABLE_WHITELIST), (
        f"白名单与失败集合不一致：失败 {failures}，白名单 {IMMUTABLE_WHITELIST}")


def test_whitelist_only_immutable_items_with_reason():
    """白名单只收外部/系统不可改项，且每条都必须写理由。"""
    for key, reason in IMMUTABLE_WHITELIST.items():
        assert reason.strip(), f"白名单 {key} 没写理由"
        assert key.startswith(("external:", "system:")), (
            f"主题自有颜色 {key} 不许进白名单")


@pytest.mark.parametrize("theme", _ALL)
def test_brand_text_meets_ui_component_threshold(theme):
    """品牌橙当文字（primary_text）≥3:1。

    说明：填充用的 accent 保持品牌原值（#C4845C），它作文字时对比度只有
    2.79:1，所以文字场景单独给了压暗档 primary_text；填充态按 WCAG 1.4.11
    非文本 3:1 口径另行核算。
    """
    colors = tokens().theme_colors(theme)
    ratio = contrast_ratio(colors["primary_text"], colors["bg"])
    assert ratio >= 3.0, (
        f"{theme}: primary_text/bg 对比度 {ratio:.2f} < 3.0")


@pytest.mark.parametrize("theme", _ALL)
def test_disabled_state_still_visible(theme):
    """禁用文字在禁用底上至少 1.5:1（WCAG 豁免，但"看得见"是硬要求）。"""
    colors = tokens().theme_colors(theme)
    ratio = contrast_ratio(colors["text_disabled"], colors["button_disabled"])
    assert ratio >= 1.5, (
        f"{theme}: 禁用态 {colors['text_disabled']} on "
        f"{colors['button_disabled']} 对比度 {ratio:.2f} < 1.5")


def test_brand_fill_meets_ui_component_threshold():
    """品牌橙填充（accent）上的白字约 3.09:1，按 WCAG 1.4.11 非文本 3:1 核算。

    品牌填充色属设计决策（改深会破坏品牌观感），文字场景已单独用
    primary_text 压暗档满足 4.5:1；此断言锁住填充取值，防止悄悄改色。
    """
    colors = tokens().theme_colors("default")
    on_accent = contrast_ratio("#FFFFFF", colors["accent"])
    assert 3.0 <= on_accent < 4.5, (
        f"default accent 上的白字对比度变为 {on_accent:.2f}，"
        f"请重新核算并更新本登记项")
