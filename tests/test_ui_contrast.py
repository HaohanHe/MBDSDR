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
import re
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


#: 状态色 token：原先只在全局 COLORS 里、不随主题 remap。2026-11-12 实测
#: 浅底上 warning 2.21:1、danger 4.21:1，连图标级 3:1 都不够，于是每套
#: 主题各自持有同名键（只动明度，色相与饱和度不变）。
_STATUS_TOKENS = ("status_warning", "status_danger")
_STATUS_BG_ROLES = ("bg", "bg_alt", "card")


def _tightest_bg(colors, fg):
    """取与字色亮度最接近的背景 = 该配色下对比度最低的那个。

    浅色主题是 bg（#F5F3EF，比 card 白底暗）；深色主题是 card（最亮的
    卡片）。比固定取 bg 更严：将来新增主题换了明暗关系也不会漏检。
    """
    return min((colors[r] for r in _STATUS_BG_ROLES),
               key=lambda c: abs(luminance(c) - luminance(fg)))


@pytest.mark.parametrize("theme", _ALL)
@pytest.mark.parametrize("token_name", _STATUS_TOKENS)
def test_status_colors_readable_on_theme(theme, token_name):
    """每套主题的状态色在最严苛背景上 ≥4.5:1（正文级，不是 3:1）。

    阈值口径：这两个 token 当前全部用于状态**文字**（AI 状态标签、步骤卡
    状态字、工具卡状态字、TLE 日期行、PTT 能力提示），没有纯圆点/边框
    用法，所以按 WCAG AA 正文 4.5:1 卡，而不是非文本 3:1。
    """
    colors = tokens().theme_colors(theme)
    assert token_name in colors, f"{theme} 缺 {token_name} 键"
    fg = colors[token_name]
    bg = _tightest_bg(colors, fg)
    ratio = contrast_ratio(fg, bg)
    assert ratio >= 4.5, (
        f"{theme}: {token_name} {fg} on {bg} 对比度 {ratio:.2f} < 4.5"
        f"（状态文字，按正文级）")


def test_status_color_follows_theme():
    """统一取色入口要真的跟着主题走，不能仍返回全局旧值。"""
    t = tokens()
    assert t.status_color("warning", theme="default") == "#876B27"
    assert t.status_color("danger", theme="dark_car") == "#CF7B7B"
    # 缺键回落全局：构造一个没有该键的主题名（theme_colors 会退到 default）
    assert t.status_color("status_warning", theme="no-such-theme") == "#876B27"
    assert t.status_color("warning", theme="default") == \
        t.theme_colors("default")["status_warning"]


#: AI 对话面板是不随主题变的固定深色岛屿（见 ai_panel._chat_colors 与卡片样式）。
#: 三个底：系统消息 #241f16、面板/步骤卡 card_1 #1f1f1f、工具卡 rgba 叠深底后的
#: 合成值 #181C22。状态色必须按这些真实底色算，不能套主题的 bg。
_AI_PANEL_SURFACES = ("#241f16", "#1f1f1f", "#181C22")


@pytest.mark.parametrize("theme", _ALL)
@pytest.mark.parametrize("kind", _STATUS_TOKENS)
@pytest.mark.parametrize("bg", _AI_PANEL_SURFACES)
def test_status_color_readable_on_dark_panel(theme, kind, bg):
    """任何主题下，深色岛屿上的状态色都要 ≥4.5:1（浅色主题的压暗档会掉到 3.2）。"""
    color = tokens().status_color(kind, bg=bg, theme=theme)
    ratio = contrast_ratio(color, bg)
    assert ratio >= 4.5, (
        f"{theme}: {kind} {color} on 深色面板 {bg} 对比度 {ratio:.2f} < 4.5")


#: 直接读全局状态色 = 绕过主题 remap。已迁点必须走 tokens().status_color()。
#: 例外只收已登记待迁移的点位；迁移时连同这里一起删。
_ALLOWED_DIRECT_READS = {
    "desktop/main_window.py":
        "PTT 提示条已登记下一批；必须与同处 rgba(200,160,64,0.18) 的琥珀底一起改",
}
_DIRECT_READ = re.compile(r"""COLORS\s*\[\s*['"]status_(?:warning|danger)['"]\s*\]""")


def test_no_direct_status_token_reads():
    """全仓不允许再直接读 COLORS["status_warning"/"status_danger"]。"""
    hits = {}
    for dirpath, dirnames, filenames in os.walk(_ROOT):
        # 跳过隐藏目录（.git/.audit_tmp/.trae 等临时副本）与缓存目录：
        # 只对入库源码负责。
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d not in {"__pycache__", "tmp"}]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as fh:
                count = len(_DIRECT_READ.findall(fh.read()))
            if count:
                hits[os.path.relpath(path, _ROOT).replace(os.sep, "/")] = count

    unexpected = {k: v for k, v in hits.items() if k not in _ALLOWED_DIRECT_READS}
    assert not unexpected, (
        f"仍有直接读全局状态色的点位：{unexpected}；"
        f"请改走 tokens().status_color(kind[, bg=真实底色])")
    assert hits.get("desktop/main_window.py") == 1, (
        "main_window 的登记例外变了（已迁移就把 _ALLOWED_DIRECT_READS 里这条删掉）")


def _sdr_hint_css() -> str:
    """取 constellation_panel 里 ``sdr_hint.setStyleSheet(...)`` 的实参。

    括号配对扫描而不是正则截断：``palette(placeholder-text)`` 自带右括号，
    用 ``.*?\\)`` 会在角色名后提前收尾，等于没检查到后面的规则。
    """
    path = os.path.join(_ROOT, "desktop", "constellation_panel.py")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    start = src.find("self.sdr_hint.setStyleSheet(")
    assert start >= 0, "constellation_panel.sdr_hint 的 setStyleSheet 不见了（改名/删除？）"
    i = src.index("(", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "(":
            depth += 1
        elif src[j] == ")":
            depth -= 1
            if depth == 0:
                return src[i + 1:j]
    raise AssertionError("sdr_hint.setStyleSheet 括号未闭合")


_HINT_LITERAL = re.compile(r"#[0-9A-Fa-f]{3,8}\b|rgba?\s*\([^)]*\)")


def test_constellation_sdr_hint_uses_theme_secondary():
    """SDR 层说明字必须走主题 text_secondary，并在每套主题的底上 ≥4.5:1。

    该标签是普通提示文字（不是状态），所在表面是右栏普通 QWidget 底
    （``palette(window)`` = 主题 ``bg``），所以按 ``bg`` 算对比度。
    ``palette(placeholder-text)`` 在 themes._THEME_ROLE_MAP 里正好映射
    ``text_secondary``，因此切主题会随 QPalette 一起重解析。

    反证：把该行改回 ``"color: #8A93A6;"`` → 本用例必须红
    （浅色 bg 上只有 2.79:1）。
    """
    css = _sdr_hint_css()
    literal = _HINT_LITERAL.search(css)
    assert literal is None, (
        f"sdr_hint 仍写死颜色 {literal.group(0)}；"
        f"请改走 palette(placeholder-text)（= 主题 text_secondary）")
    assert "palette(placeholder-text)" in css, (
        f"sdr_hint 样式没走主题入口：{css!r}")

    for theme in _ALL:
        colors = tokens().theme_colors(theme)
        ratio = contrast_ratio(colors["text_secondary"], colors["bg"])
        assert ratio >= 4.5, (
            f"{theme}: text_secondary {colors['text_secondary']} on bg "
            f"{colors['bg']} 对比度 {ratio:.2f} < 4.5")
