# SPDX-License-Identifier: MIT
"""
主题令牌单一来源 / 可切换主题回归
=================================

锁三件事：
  1. 所有主题的 colors 必须逐键等于 tokens.THEME_COLORS（不许主题侧另写 hex）；
  2. 新增的 "figma" 主题可切换且默认不启用（DEFAULT_THEME 仍是 default）；
  3. 切过去颜色确实变（QPalette + QSS 都变），切回来完全还原；
  4. 每套主题的样式表都带禁用态规则（禁用控件颜色/底色可见）。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_theme_switch.py -q
"""
import os
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


def test_figma_theme_is_switchable_but_not_default():
    assert "figma" in THEMES, "figma 主题未注册"
    assert DEFAULT_THEME == "default", "默认主题被强制替换了"
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
    assert qss_after != qss_before, "切主题后 QSS 没变"
    assert "#000000" in bg_after.lower(), "figma 主题页底应为 #000000"

    default_theme.apply(app)
    assert app.palette().color(QPalette.Window).name() == bg_before
    assert app.styleSheet() == qss_before, "切回默认主题未完全还原"


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
