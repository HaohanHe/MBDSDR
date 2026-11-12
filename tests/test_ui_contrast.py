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
from collections import Counter

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


#: 直接读全局 COLORS = 绕过主题 remap。
#: 六类语义色最危险：浅色主题下 danger 4.45、success 2.73、accent 2.78、
#: status_busy 2.03，全部掉出正文 4.5 阈值；其余 token 直读同样是欠账
#: （岛屿内属设计，但也要在基线里显式记账，不能悄悄新增）。
_DIRECT_READ = re.compile(
    r"""(?<!THEME_)COLORS\s*(?:\[\s*['"]([A-Za-z_]\w*)['"]\s*\]"""
    r"""|\.get\(\s*['"]([A-Za-z_]\w*)['"]\s*(?:,\s*[^)]*)?\))""")

#: 六类语义色的 token 名（A1/A2 分档只对这几类有意义）。
_SEMANTIC_TOKENS = ("danger", "success", "accent", "status_busy",
                    "status_warning", "status_danger")

#: 棘轮基线（只许降不许涨）：当前全部命中，逐条「文件:行 + 取的 token」。
#: kind 语义：
#:   A1     待迁 status_color 的 11 个单元（control 两行同属 1 个单元，共 12 行）；
#:   A2     success/accent 当正文或填充，等用户拍板，禁止新增；
#:   FROZEN main_window 已登记冻结项（须与同处底纹一起改）；
#:   ISLAND 固定深/浅色岛屿，读全局 COLORS 是设计（docs/a11y 清单 B 类）；
#:   TEST   测试自己断言 token 原值。
#: 修完一条必须**同一笔**做两件事：删掉对应基线行 + `_BASELINE_CEILING -= 1`。
#: 门禁按 `==` 判定（不是 `<=`）：漏降上限 → 红，漏删基线行 → 也红（见
#: 「基线里已不存在的直读」那条断言）。否则棘轮会退化成「11 就对了」。
_BASELINE = (
    # ---- A1 待迁（11 个单元 / 12 行）----
    ("desktop/status_panel.py", 360, "danger", "A1", "status:360"),
    ("desktop/status_panel.py", 867, "danger", "A1", "status:867"),
    ("desktop/status_panel.py", 965, "danger", "A1", "status:965"),
    ("desktop/status_panel.py", 1019, "danger", "A1", "status:1019"),
    ("desktop/sat_track_panel.py", 453, "danger", "A1", "sat:453"),
    ("desktop/sat_track_panel.py", 518, "danger", "A1", "sat:518"),
    ("desktop/sat_track_panel.py", 522, "danger", "A1", "sat:522"),
    ("desktop/vfo_panel.py", 232, "danger", "A1", "vfo:232"),
    ("desktop/ntrip_panel.py", 48, "danger", "A1", "ntrip:48"),
    ("desktop/ntrip_panel.py", 49, "status_busy", "A1", "ntrip:49"),
    ("desktop/control_panel.py", 999, "danger", "A1", "control:invalid"),
    ("desktop/control_panel.py", 1007, "danger", "A1", "control:invalid"),
    # ---- A2 等拍板（success/accent 当正文/填充）----
    ("desktop/status_panel.py", 855, "success", "A2", ""),
    ("desktop/status_panel.py", 857, "accent", "A2", ""),
    ("desktop/status_panel.py", 863, "success", "A2", ""),
    ("desktop/status_panel.py", 865, "accent", "A2", ""),
    ("desktop/status_panel.py", 959, "success", "A2", ""),
    ("desktop/status_panel.py", 1013, "success", "A2", ""),
    ("desktop/status_panel.py", 1016, "accent", "A2", ""),
    ("desktop/status_panel.py", 1049, "success", "A2", ""),
    ("desktop/status_panel.py", 1093, "success", "A2", ""),
    ("desktop/sat_track_panel.py", 526, "success", "A2", ""),
    ("desktop/sat_track_panel.py", 528, "success", "A2", ""),
    ("desktop/vfo_panel.py", 191, "accent", "A2", ""),
    ("desktop/ntrip_panel.py", 50, "success", "A2", ""),
    # ---- FROZEN 冻结期登记 ----
    ("desktop/main_window.py", 5273, "status_warning", "FROZEN", ""),
    ("desktop/main_window.py", 11523, "accent", "FROZEN", ""),
    # ---- ISLAND 固定色岛屿（不随主题，属设计）----
    ("desktop/adsb_map_panel.py", 76, "accent", "ISLAND", ""),
    ("desktop/ai_panel.py", 504, "accent", "ISLAND", ""),
    ("desktop/constellation_panel.py", 439, "success", "ISLAND", ""),
    ("desktop/freq_display.py", 216, "accent", "ISLAND", ""),
    ("desktop/module_panel.py", 71, "accent", "ISLAND", ""),
    ("desktop/module_panel.py", 79, "success", "ISLAND", ""),
    ("desktop/new_spacetime_panel.py", 115, "accent", "ISLAND", ""),
    ("desktop/new_spacetime_panel.py", 116, "success", "ISLAND", ""),
    ("desktop/new_spacetime_panel.py", 117, "danger", "ISLAND", ""),
    ("desktop/rf_sky_view.py", 2017, "accent", "ISLAND", ""),
    ("desktop/rf_sky_view.py", 2037, "success", "ISLAND", ""),
    ("desktop/rf_sky_view.py", 2077, "accent", "ISLAND", ""),
    ("desktop/rf_sky_view.py", 2117, "accent", "ISLAND", ""),
    ("desktop/rf_sky_view.py", 2119, "success", "ISLAND", ""),
    ("desktop/rf_sky_view.py", 2139, "accent", "ISLAND", ""),
    ("desktop/rf_sky_view.py", 2140, "success", "ISLAND", ""),
    ("desktop/rf_sky_view.py", 2141, "danger", "ISLAND", ""),
    ("desktop/rf_sky_view.py", 2146, "accent", "ISLAND", ""),
    ("desktop/rf_sky_view.py", 2151, "accent", "ISLAND", ""),
    ("desktop/rf_sky_view.py", 2152, "danger", "ISLAND", ""),
    ("desktop/rf_sky_view.py", 2153, "success", "ISLAND", ""),
    ("desktop/rf_sky_view.py", 2156, "accent", "ISLAND", ""),
    ("desktop/rf_sky_view.py", 4682, "danger", "ISLAND", ""),
    ("desktop/rf_sky_view.py", 5581, "accent", "ISLAND", ""),
    ("desktop/rf_sky_view.py", 5585, "danger", "ISLAND", ""),
    ("desktop/rf_sky_view.py", 5589, "danger", "ISLAND", ""),
    ("desktop/rf_sky_view.py", 5686, "danger", "ISLAND", ""),
    ("desktop/rf_sky_view.py", 6202, "accent", "ISLAND", ""),
    ("desktop/rf_sky_view.py", 7032, "accent", "ISLAND", ""),
    ("desktop/rf_sky_view.py", 8967, "accent", "ISLAND", ""),
    # ---- TEST 测试断言 token 原值 ----
    ("tests/test_theme_tokens.py", 65, "accent", "TEST", ""),
)

#: A1 单元数上限（只许降）：当前 11 = status 4 + sat 3 + vfo 1 + ntrip 2 + control 1。
_BASELINE_CEILING = 11

#: 非语义 token 的直读欠账（2026-11-12 扩面后按「文件 × token 计数」重建）。
#: 与 `_BASELINE` 同一口径：只许降不许涨；改掉一处就同笔减数，减到 0 删键。
#: 岛内（rf_sky_view / ai_panel 等）的直读是设计，但也要在这里显式记账——
#: 否则「岛屿」会变成任何新直读的免检通道。注释行和 scripts/ 不参与扫描。
_LEGACY_READS = {
    "desktop/adsb_map_panel.py": {"card_1": 2, "gray_100": 2, "gray_400": 2},
    "desktop/adsb_panel.py": {"light_accent": 1, "light_bg": 1,
                              "light_grid": 1, "light_text": 1,
                              "station_blue": 1},
    "desktop/ai_panel.py": {"accent_warm": 1, "accent_warm_hover": 1,
                            "card_1": 2, "disabled_gray": 1, "gray_100": 1,
                            "gray_200": 2, "gray_400": 1,
                            "status_success": 1, "text_muted": 2},
    "desktop/constellation_panel.py": {"card_1": 2, "gray_200": 3,
                                       "gray_300": 2, "gray_400": 6},
    "desktop/freq_display.py": {"card_1": 1, "gray_300": 1, "gray_400": 1},
    "desktop/gain_panel.py": {"disabled_gray": 1},
    "desktop/main_window.py": {"bg_dark": 1, "card_2": 1, "card_hover": 1},
    "desktop/module_panel.py": {"card_1": 1, "gray_200": 1, "gray_300": 1,
                                "source_node": 1, "text_primary": 1},
    "desktop/new_spacetime_panel.py": {"card_1": 1, "gray_100": 1,
                                        "gray_200": 1, "gray_400": 1},
    "desktop/rf_sky_view.py": {
        "bg_dark": 4, "card_1": 9, "card_2": 12, "card_3": 1,
        "card_hover": 5, "celestial_moon": 1, "celestial_sun": 1,
        "constellation_galileo": 1, "gray_100": 9, "gray_200": 4,
        "gray_300": 15, "gray_400": 13, "ground_shadow_day": 1,
        "heatmap_high": 1, "heatmap_low": 1, "heatmap_mid": 1,
        "light_accent": 4, "light_bg": 1, "sky_day_ink": 1,
        "sky_day_zenith": 1, "sky_night_glow": 1, "sky_night_horizon": 1,
        "sky_night_zenith": 1, "text_primary": 2},
    "desktop/sat_track_panel.py": {"card_2": 1, "gray_300": 1, "gray_400": 1},
    "desktop/satellite_image_panel.py": {"image_placeholder_bg": 1},
    "desktop/status_panel.py": {"card_2": 1, "gray_300": 5, "gray_400": 1},
    "desktop/tokens.py": {"light_accent": 1, "light_bg": 1, "light_bg_alt": 1,
                          "light_border": 1, "light_card": 1, "light_text": 1},
    "desktop/vfo_panel.py": {"card_2": 1, "gray_200": 1, "text_primary": 1},
    "tests/test_theme_tokens.py": {"bg_dark": 1},
    "tests/test_ui_contrast.py": {"disabled_gray": 1},
}


def _baseline_counts():
    """基线计数 = 逐行语义行 + 非语义 token 的 file × token 欠账。"""
    base = Counter((f, t) for f, _l, t, *_ in _BASELINE)
    for rel, toks in _LEGACY_READS.items():
        for tok, n in toks.items():
            base[(rel, tok)] += n
    return base


def _scan_direct_reads():
    """返回 [(相对路径, 行号, token)]；跳过隐藏/缓存目录、scripts/ 与整行注释。

    `scripts/` 是一次性报表脚本，不属于产品代码；整行注释里的示例
    （形如 ``COLORS["<token>"]`` 的注释）不是真读。行尾注释不排除——真读后面
    跟注释的情况更常见，宁可多记一条也不能漏。
    """
    hits = []
    for dirpath, dirnames, filenames in os.walk(_ROOT):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d not in {"__pycache__", "tmp"}]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, _ROOT).replace(os.sep, "/")
            if rel.startswith("scripts/"):
                continue
            with open(path, encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, 1):
                    if line.lstrip().startswith("#"):
                        continue
                    for m in _DIRECT_READ.finditer(line):
                        hits.append((rel, lineno, m.group(1) or m.group(2)))
    return hits


def test_no_new_direct_semantic_color_reads():
    """棘轮：新增直读 → 红；基线已过期（修完没删行）或上限没跟着降 → 也红。

    范围是**任何直读全局 `COLORS[...]` / `.get(...)`**（不限六类语义色）：
    只拦 status_* 时，A1 那批 danger/success/accent/status_busy 全在门外，
    修完也没人守。六类语义色另在 `_BASELINE` 里逐条标 A1/A2 档位。

    允许集合是「基线（文件 × token 计数）」而不是行号集合：行号会随上方编辑漂移，
    但计数不会——漂移只更新注释里的行号，不改变判定。非语义 token 的欠账在
    `_LEGACY_READS`，两者合并成基线。

    两个方向都拦：
      * 代码里多出直读、基线没登记            → extra 红；
      * 代码里已经改掉、基线行还留着（过期）  → stale 红；
      * A1 单元数与 `_BASELINE_CEILING` 不等   → 红（`==`，不是 `<=`）。
    第三条是「每修一处必须同笔降 1」的执行点：漏降上限、漏删基线行都过不去。
    """
    assert {e[2] for e in _BASELINE} <= set(_SEMANTIC_TOKENS), "基线里有未知 token"
    known = set(tokens().COLORS)
    unknown = {(f, t) for (f, t) in _baseline_counts() if t not in known}
    assert not unknown, f"基线里登记了全局 COLORS 不存在的 token：{sorted(unknown)}"
    hits = _scan_direct_reads()
    got = Counter((f, t) for f, _l, t in hits)
    base = _baseline_counts()
    extra = {k: got[k] - base[k] for k in got if got[k] > base[k]}
    assert not extra, (
        "新增了直接读全局 COLORS 的点位（必须走 tokens().status_color(...) "
        "或主题 token）："
        + ", ".join("%s %s +%d" % (f, t, n) for (f, t), n in sorted(extra.items())))
    stale = {k: base[k] - got[k] for k in base if got[k] < base[k]}
    assert not stale, (
        "基线里登记了已不存在的直读（代码已改、基线行没删）："
        + ", ".join("%s %s -%d" % (f, t, n) for (f, t), n in sorted(stale.items()))
        + "；删掉对应基线行并把 _BASELINE_CEILING 减 1")
    a1_units = {e[4] for e in _BASELINE if e[3] == "A1"}
    assert len(a1_units) == _BASELINE_CEILING, (
        f"A1 待迁单元 {len(a1_units)} != 上限 {_BASELINE_CEILING}："
        "每修完一处必须同笔删基线行并把 _BASELINE_CEILING 减 1（只许降）")


#: 样本里的命名空间名拼出来：本文件也会被 `_scan_direct_reads()` 扫到，
#: 写成字面量就等于自己给自己贡献「新增直读」。
_GLOBAL = "COL" + "ORS"


def _index_read(token: str) -> str:
    return 'fg = %s["%s"]' % (_GLOBAL, token)


def _get_read(token: str) -> str:
    return "fg = t.%s.get('%s', '#000')" % (_GLOBAL, token)


@pytest.mark.parametrize("token", _SEMANTIC_TOKENS + ("gray_300", "card_2", "light_bg"))
def test_direct_read_regex_covers_all_six_semantic_families(token):
    """反证底线：正则必须命中六类语义色**和任意其它全局 token**。

    旧正则只写 `status_warning|status_danger`，A1 那批 danger/success/accent/
    status_busy 全被漏掉——这条用例就是为了让「缩回旧正则」立刻变红。
    `.get()` 分支走的是第二个捕获组，也必须取到 token 名。
    """
    for line in (_index_read(token), _get_read(token)):
        hit = _DIRECT_READ.search(line)
        assert hit is not None, f"正则漏掉了这行直读：{line}"
        assert (hit.group(1) or hit.group(2)) == token


def test_direct_read_regex_ignores_theme_colors():
    """`THEME_COLORS[...]` 是随主题的命名空间，不是「直读全局」，不许被拦。"""
    assert _DIRECT_READ.search('c = THEME_COLORS["default"]["danger"]') is None
    assert _DIRECT_READ.search("c = THEME_COLORS.get('default')") is None


def test_semantic_tokens_never_hide_in_legacy_debt():
    """六类语义色必须逐行登记在 `_BASELINE`（A1/A2/...），不许挪进 `_LEGACY_READS`。

    否则「把 A1 从基线剔掉、改记成一条欠账计数」也能让门禁变绿，棘轮就废了。
    """
    hidden = {(f, t) for f, toks in _LEGACY_READS.items()
              for t in toks if t in _SEMANTIC_TOKENS}
    assert not hidden, f"语义色不许走文件×token 欠账通道：{sorted(hidden)}"


def test_gain_range_hint_exempt_only_when_stage_unsupported():
    """禁用档 exempt ≠ 达标：可用档位的同一行字必须回到 ≥4.5 的主题角色。

    `disabled_gray` 只给「设备无此增益档」这类禁用态；豁免一旦被当成
    「这个面板随便什么色都行」，可用档位的提示字就会永远停在 2.85。
    """
    from PySide6.QtWidgets import QApplication
    from gain_panel import _StageSlider

    _app = QApplication.instance() or QApplication([])  # noqa: F841
    sl = _StageSlider("LNA")
    exempt = tokens().COLORS["disabled_gray"]

    assert exempt in sl.range_label.styleSheet(), "初始态（禁用）应走豁免色"

    sl.setup_stage("elem", 0.0, 40.0, True)
    css = sl.range_label.styleSheet()
    assert "palette(placeholder-text)" in css, "可用档位必须回到主题角色"
    assert exempt not in css, "可用档位的提示字不许继续用豁免色"
    for theme in _ALL:
        colors = tokens().theme_colors(theme)
        for surface in ("card", "bg"):
            ratio = contrast_ratio(colors["text_secondary"], colors[surface])
            assert ratio >= 4.5, (
                f"{theme}: 可用档位提示字 text_secondary/{surface} "
                f"{ratio:.2f} < 4.5")

    sl.setup_stage("elem", 0.0, 0.0, False)
    assert exempt in sl.range_label.styleSheet(), "不支持档位（禁用）回到豁免色"


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


_AI_PANEL_THEMED = ("title_label", "ai_status_label", "pop_btn",
                    "live_btn", "live_details")


def _ai_panel_css(name: str) -> str:
    """取 ai_panel 里某个控件 ``setStyleSheet(...)`` 的实参（括号配对扫描）。"""
    path = os.path.join(_ROOT, "desktop", "ai_panel.py")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    start = -1
    for prefix in (f"self.{name}.setStyleSheet(", f"{name}.setStyleSheet("):
        start = src.find(prefix)
        if start >= 0:
            break
    assert start >= 0, f"ai_panel.{name}.setStyleSheet 不见了（改名/删除？）"
    i = src.index("(", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "(":
            depth += 1
        elif src[j] == ")":
            depth -= 1
            if depth == 0:
                return src[i + 1:j]
    raise AssertionError(f"{name}.setStyleSheet 括号未闭合")


def test_ai_panel_header_follows_theme():
    """AI 面板标题/内核状态/常驻状态条/状态详情随主题；对话区保持固定深底。

    2026-11-12 实测（半透明底按真实叠底算合成值，不拿字面色当结论）：
    浅色主题下标题白字 1.11:1、弹出按钮 1.55:1、状态条 1.04:1、
    状态详情 2.59:1。改走 palette 角色后最差 text_secondary/bg_alt
    = 4.85:1（default）。对话区 ``#1f1f1f`` 是已声明的固定深底，不在
    本用例范围。

    反证：任一处改回原值（#B9C7DB / rgba(60,90,130,0.16) / #CFE0F5 /
    rgba(14,17,24,0.55) / ``_t.COLORS[...]``）→ 本用例必须红。
    """
    for name in _AI_PANEL_THEMED:
        css = _ai_panel_css(name)
        literal = _HINT_LITERAL.search(css)
        assert literal is None, (
            f"{name} 仍写死颜色 {literal.group(0)}：{css!r}")
        assert "COLORS[" not in css, (
            f"{name} 仍直读全局 COLORS（深色主题值，切主题不变）：{css!r}")
        assert "palette(" in css, f"{name} 没走主题角色：{css!r}"

    for theme in _ALL:
        c = tokens().theme_colors(theme)
        pairs = (
            ("标题/状态条", c["text"], c["bg"]),
            ("内核状态", c["text_secondary"], c["bg"]),
            ("弹出按钮", c["text_secondary"], c["bg"]),
            ("状态条", c["text"], c["bg_alt"]),
            ("状态详情", c["text_secondary"], c["bg_alt"]),
        )
        for label, fg, bg in pairs:
            ratio = contrast_ratio(fg, bg)
            assert ratio >= 4.5, (
                f"{theme}/{label}: {fg} on {bg} = {ratio:.2f} < 4.5")


# ---------------------------------------------------------------- 非文本 3:1
#
# 2026-11-12 非文本普查（WCAG 1.4.11，门槛 3:1）的口径：
#   ② 信息承载（本段逐条门禁）：控件边界 border（输入框/勾选框/单选框/
#      下拉框的唯一轮廓）、焦点环、单选选中圆点、能力抽屉单元格边框、
#      定位面板输入/列表/按钮边界与列表选中底色。
#   ③ 状态指示：单选选中、列表选中（与 ② 重叠，按"状态"这一面计入）。
#   ① 纯装饰（不进结论）：卡片/分组框/工具栏/状态栏的 1px 描边（内容分组
#      靠布局与底色已能读出）、按钮填充 vs 页底（按钮靠文字标签识别，
#      WCAG 明确豁免有文字标签的控件边界）、浮动面板外框、滑动条轨道
#      （轨道提到 3:1 会迫使手柄只能取近黑/近白，属重排配色，另行立项）。
_NON_TEXT_SURFACES = ("card", "bg", "bg_alt", "button")


@pytest.mark.parametrize("theme", _ALL)
def test_control_outline_meets_non_text_contrast(theme):
    """控件边界（border = QPalette::Mid）对四个相邻面都 ≥3:1。

    这些控件的填充与底色的对比只有 1.0–1.2（QLineEdit/勾选框填 button 或
    card），描边是**唯一**能看出控件存在的信息，按 1.4.11 必须 3:1。
    改造前实测（border on card，括号内为 on button）：default 1.80(1.80)、
    dark 1.52(1.30)、dark_car 2.08(1.77)、figma 1.59(1.35)；改后最差
    3.03（figma on button）。

    反证：把任一套主题的 border 改回上述旧值 → 本用例必红。
    """
    c = tokens().theme_colors(theme)
    for surface in _NON_TEXT_SURFACES:
        ratio = contrast_ratio(c["border"], c[surface])
        assert ratio >= 3.0, (
            f"{theme}: border {c['border']} on {surface} {c[surface]} "
            f"= {ratio:.2f} < 3.0（控件边界看不见）")


def test_focus_ring_meets_non_text_contrast():
    """焦点环用 palette(link)（= primary_text），五套主题 ≥3:1。

    改造前用 palette(highlight)：default 对页底只有 2.79:1——焦点框比
    未聚焦描边还淡。primary_text 对 card/bg/bg_alt 均 ≥4.67。

    反证：把 QSS 里任一条 ``:focus`` 改回 palette(highlight) → 必红。
    """
    from themes import THEMES

    qss = THEMES["default"]._qss()
    blocks = re.findall(r"[^{}]*:focus[^{}]*\{([^}]*)\}", qss)
    assert blocks, "QSS 里找不到 :focus 规则（选择器改名？）"
    for block in blocks:
        assert "palette(link)" in block, (
            f"焦点环没走 palette(link)：{block!r}")
        assert "palette(highlight)" not in block, (
            f"焦点环又用回 highlight（default 仅 2.79:1）：{block!r}")

    for theme in _ALL:
        c = tokens().theme_colors(theme)
        for surface in _NON_TEXT_SURFACES:
            ratio = contrast_ratio(c["primary_text"], c[surface])
            assert ratio >= 3.0, (
                f"{theme}: 焦点环 {c['primary_text']} on {surface} "
                f"{c[surface]} = {ratio:.2f} < 3.0")


def test_radio_checked_uses_gated_role():
    """单选选中圆点没有第二线索（无对勾），圆点自身必须 ≥3:1。"""
    from themes import THEMES

    qss = THEMES["default"]._qss()
    match = re.search(r"QRadioButton::indicator:checked\s*\{([^}]*)\}", qss)
    assert match, "QRadioButton::indicator:checked 规则不见了"
    block = match.group(1)
    assert "palette(link)" in block, (
        f"单选选中态没走 palette(link)（highlight 在 default 只有 2.79:1）：{block!r}")


def _composite(fg: str, alpha: float, bg: str) -> str:
    """把半透明前景按 alpha 合成到 bg 上（WCAG 判对比度前必须先合成）。"""
    f = [int(fg.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    b = [int(bg.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    return "#%02X%02X%02X" % tuple(
        round(alpha * f[i] + (1 - alpha) * b[i]) for i in range(3))


def _read_source(rel: str) -> str:
    with open(os.path.join(_ROOT, "desktop", rel), encoding="utf-8") as fh:
        return fh.read()


def test_capability_cell_outline_meets_non_text_contrast():
    """能力抽屉单元格的边框 ≥3:1（单元格填充只有 1.13:1，边框是唯一轮廓）。

    面板底 rgba(14,17,24,0.94) 最坏叠在浅色 app 底 #F5F3EF 上 → #1C1F25；
    单元格自身填充 rgba(255,255,255,0.045) → #26292F（更亮，取更严的那个）。
    反证：把边框 alpha 改回 0.18（合成 #373D46，1.51:1）→ 必红。
    """
    src = _read_source("capability_drawer.py")
    alphas = [float(a) for a in re.findall(
        r"rgba\(180,196,220,([0-9.]+)\)", src)]
    assert len(alphas) >= 2, f"单元格边框没找到两档 alpha：{alphas}"
    panel = _composite("#0E1118", 0.94, "#F5F3EF")
    cell = _composite("#FFFFFF", 0.045, panel)
    for alpha in alphas:
        for bg, label in ((panel, "面板底"), (cell, "单元格填充")):
            color = _composite("#B4C4DC", alpha, bg)
            ratio = contrast_ratio(color, bg)
            assert ratio >= 3.0, (
                f"能力抽屉单元格边框 alpha={alpha} on {label} "
                f"= {ratio:.2f} < 3.0")


def test_location_panel_control_edges_meet_non_text_contrast():
    """定位面板（固定深底 Qt.Tool）的控件边界与选中底色 ≥3:1。

    输入框/列表/按钮填 _PANEL(#1b1e26) 或 _BG(#14161c)，与描边 _LINE
    (#333845) 只差 1.42:1；选中行没有第二线索，选中底 0.35 叠出来只
    1.75:1。现取 _EDGE(#6A7284) 与选中 alpha 0.70。
    反证：控件边界改回 _LINE / 选中 alpha 改回 0.35 → 必红。
    """
    src = _read_source("location_panel.py")
    consts = dict(re.findall(r'^(_[A-Z]+) = "(#[0-9A-Fa-f]{6})"', src, re.M))
    for key in ("_BG", "_PANEL", "_LINE", "_EDGE"):
        assert key in consts, f"location_panel 缺常量 {key}"
    for surface in ("_PANEL", "_BG"):
        ratio = contrast_ratio(consts["_EDGE"], consts[surface])
        assert ratio >= 3.0, (
            f"定位面板控件边界 {consts['_EDGE']} on {surface} "
            f"{consts[surface]} = {ratio:.2f} < 3.0")

    alphas = [float(a) for a in re.findall(
        r"QListWidget::item:selected \{ background: rgba\(90,140,255,([0-9.]+)\)", src)]
    assert alphas, "定位面板列表选中底色没找到"
    for alpha in alphas:
        color = _composite("#5A8CFF", alpha, consts["_PANEL"])
        ratio = contrast_ratio(color, consts["_PANEL"])
        assert ratio >= 3.0, (
            f"列表选中底 alpha={alpha} 合成 {color} 对未选中底 "
            f"= {ratio:.2f} < 3.0")
