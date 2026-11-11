# SPDX-License-Identifier: MIT
"""
主题令牌单一来源 / 可切换主题回归
=================================

锁三件事：
  1. 所有主题的 colors 必须逐键等于 tokens.THEME_COLORS（不许主题侧另写 hex）；
  2. 新增的 "figma" 主题可切换且默认不启用（DEFAULT_THEME 仍是 default）；
  3. 切过去颜色确实变（QPalette 变），切回来完全还原；且五套主题共用
     一份逐字相同的 QSS——切主题不重设样式表（A′，2026-11-11）；
  4. 每套主题的样式表都带禁用态规则（禁用控件颜色/底色可见）。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_theme_switch.py -q
"""
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from themes import THEMES, DEFAULT_THEME, get_theme  # noqa: E402
from tokens import tokens  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


def test_every_theme_color_comes_from_tokens():
    """主题色单一来源：themes.py 不得与 tokens.THEME_COLORS 分叉。"""
    t = tokens()
    for name, theme in THEMES.items():
        expected = t.theme_colors(name)
        assert theme.colors == expected, f"主题 {name} 与 tokens 分叉"
        assert set(theme.colors) >= {"bg", "card", "text", "text_secondary",
                                     "border", "primary", "primary_text"}


def test_schema_default_matches_themes():
    """设置表里的 theme 默认值必须等于 themes.DEFAULT_THEME。

    2026-11-09 踩过：改了 DEFAULT_THEME 却没改 config_schema 的 default，
    DEFAULTS 里仍然预填旧主题，于是 settings.get("theme", DEFAULT_THEME)
    永远拿到旧值——出厂默认形同虚设，且不报任何错。
    """
    from config_schema import SCHEMA, defaults

    # 真正会被 settings.get("theme", DEFAULT_THEME) 拿到的是 DEFAULTS 里预填的值，
    # 所以直接钉 DEFAULTS，比钉 SettingItem.default 更贴近失败路径。
    assert defaults("settings")["theme"] == DEFAULT_THEME, (
        f"config_schema 的 theme 默认值 "
        f"{defaults('settings')['theme']!r} 与 themes.DEFAULT_THEME="
        f"{DEFAULT_THEME!r} 不一致——DEFAULTS 会预填旧主题，"
        f"settings.get 永远拿不到新默认值")
    item = SCHEMA["theme"]
    # 旧主题必须仍可选（不是删掉了，只是不再是默认）
    assert any("default" in str(c) for c in item.choices), "浅色主题被删了"


def test_figma_theme_is_switchable_but_not_default():
    assert "figma" in THEMES, "figma 主题未注册"
    # 默认主题必须是登记在 THEMES 里的深色主题。2026-11-09 由 "default"（浅色）
    # 改为 "dark_car"：星象仪天空与频谱画布物理上必须是深底，外壳用浅色会让
    # 同一屏并存两套明度体系（截图里米白卡片直接贴着夜空）。浅色主题完整保留、
    # 可随时切回，只是不再是出厂默认。见 themes.DEFAULT_THEME 处的原因注释。
    assert DEFAULT_THEME == "dark_car", "默认主题又被改了，请同步更新这条断言与理由"
    assert get_theme(DEFAULT_THEME).name == DEFAULT_THEME
    assert get_theme("figma").name == "figma"
    # 未知主题名必须回退默认，不崩
    assert get_theme("no-such-theme").name == DEFAULT_THEME


@pytest.fixture()
def restore_theme():
    """测试后把应用主题还原成默认（避免污染同进程后续用例）。"""
    yield
    get_theme(DEFAULT_THEME).apply(app)


def test_switch_changes_colors_and_switch_back_restores(restore_theme):
    default_theme = get_theme("default")
    figma_theme = get_theme("figma")

    default_theme.apply(app)
    bg_before = app.palette().color(QPalette.Window).name()
    qss_before = app.styleSheet()

    figma_theme.apply(app)
    bg_after = app.palette().color(QPalette.Window).name()
    qss_after = app.styleSheet()
    assert bg_after != bg_before, "切主题后页底色没变"
    # A′（2026-11-11）：QSS 里不再有颜色字面量，改由 QPalette 供给。
    # 因此切主题**必须不重设样式表**——这正是 3.3 s → 0.5 s 的来源；
    # 若这里又变成「不同」，说明有人把颜色写回了 QSS。
    assert qss_after == qss_before, "切主题重设了 QSS（颜色必须只走 palette(role)）"
    assert "#000000" in bg_after.lower(), "figma 主题页底应为 #000000"

    default_theme.apply(app)
    assert app.palette().color(QPalette.Window).name() == bg_before
    assert app.styleSheet() == qss_before, "切回默认主题未完全还原"


def test_all_themes_share_one_identical_qss():
    """五套主题的样式表必须逐字相同，且不含颜色字面量。

    A′ 的硬前提：QSS 若随主题变化，切主题就得 setStyleSheet（实测 5.6 s，
    比不换 QSS 的重抛光路径慢一个数量级）。颜色全部写成 palette(<role>)，
    由 _build_palette 装进 QPalette。
    """
    qss = {name: theme._qss() for name, theme in THEMES.items()}
    assert len(set(qss.values())) == 1, "有主题的 QSS 与其它主题不同"

    text = next(iter(qss.values()))
    assert text.count("palette(") >= 100, "QSS 里的颜色没有走 palette(role)"
    # 去掉 QSS 注释后再查颜色字面量（注释里出现 #000 之类的说明文字是允许的）
    code = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    assert not re.search(r"#[0-9A-Fa-f]{3,8}\b", code), "QSS 仍有颜色字面量"


@pytest.mark.parametrize("name", sorted(THEMES))
def test_palette_carries_theme_colors(name):
    """palette(role) 的取值来源必须是本主题 colors（不是某一份写死的表）。"""
    from themes import _build_palette

    colors = THEMES[name].colors
    pal = _build_palette(colors)
    assert pal.color(QPalette.Window).name() == colors["bg"].lower()
    assert pal.color(QPalette.Base).name() == colors["card"].lower()
    assert pal.color(QPalette.Highlight).name() == colors["primary"].lower()
    assert pal.color(QPalette.WindowText).name() == colors["text"].lower()


@pytest.mark.parametrize("name", sorted(THEMES))
def test_disabled_state_is_visible_in_every_theme(name):
    """禁用态必须可见：按钮/下拉/输入/滑杆都有 :disabled 规则。"""
    qss = THEMES[name]._build_qss()
    # 注意：不给 QSlider 加 ::handle 子控件禁用规则——Qt 一旦看到子控件规则
    # 会改用 QSS 绘制，滑杆尺寸变化会挤动频谱区，破坏布局基线（已实测）。
    for selector in ("QPushButton:disabled", "QComboBox:disabled",
                     "QLineEdit:disabled", "QSpinBox:disabled"):
        assert selector in qss, f"{name} 主题缺少 {selector} 规则"


def test_theme_qss_has_no_undefined_placeholders():
    """QSS 生成不许残留未替换的占位/花括号错误（f-string 漏键的兜底）。"""
    for name in THEMES:
        qss = THEMES[name]._build_qss()
        # 颜色占位符不得以 c['xxx'] 文本形态残留（f-string 漏键的信号）
        assert "c['" not in qss, f"{name} 主题 QSS 有未替换的颜色键"
