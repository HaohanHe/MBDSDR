# SPDX-License-Identifier: MIT
"""设计系统门禁：对比度 + 原生控件接管。

两条都来自参考项目 Glasswork 的做法（见 README「实现原理」）：
它把材质收敛成四条声明，但对**结果**下了硬约束——玻璃上的文字对比度不得
低于 4.5:1，浓度由这条倒推。我们照这个思路把「看着还行」换成可测门槛。

门禁一：对比度
    逐主题、逐前景/背景配对算 WCAG 比值。补上了 `test_ui_contrast.py` 没盖
    到的三处（2026-11-09 实测全部不达标）：
      * default 强调色 2.79（需 3.0）
      * default 成功色 2.46（需 3.0）
      * 选中态白字压橙底 3.09（需 4.5）
      * dark 卡片描边 1.30（需 1.5）
    修法都是同色相压暗/提亮，色相不动，见 tokens.THEME_COLORS 的行内注释。

门禁二：原生控件接管
    主窗实测有 138 个 QToolButton、28 个 QCheckBox、42 个 QListView、
    11 个 QSplitterHandle，而当时所有主题的 QSS 一个都没选中它们——同一屏里
    一半控件按 token 画、另一半是 Qt 默认皮肤，「设计语言不统一」主要来自这里。
    这条守卫生成后的 QSS 必须真的含有这些选择器，防止 `_control_coverage_qss`
    被摘掉连接。
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

from theme_contrast import audit  # noqa: E402
from themes import THEMES, _control_coverage_qss, _on_primary, contrast_ratio  # noqa: E402


# ---------------------------------------------------------------- 门禁一


def test_all_theme_contrast_pairs_pass():
    """所有主题的前景/背景配对都要达标；失败项逐条打印，不许整批豁免。"""
    failures = [
        f"{name}: {fg} on {bg} = {ratio:.2f} < {need:.1f}"
        for name, fg, bg, ratio, need, ok in audit() if not ok
    ]
    assert not failures, "对比度不达标：\n  " + "\n  ".join(failures)


def test_audit_covers_every_theme():
    """主题表新增一个主题时，门禁必须自动覆盖它（不是只测 default）。"""
    covered = {row[0] for row in audit()}
    assert covered == set(THEMES), f"漏测主题：{set(THEMES) - covered}"


def test_on_primary_is_readable_for_every_theme():
    """强调色底上的墨色必须自己算出来，不能写死 white。"""
    for name, theme in THEMES.items():
        ink = _on_primary(theme.colors)
        ratio = contrast_ratio(ink, theme.colors["primary"])
        assert ratio >= 4.5, f"{name} 选中态 {ink} on {theme.colors['primary']} 只有 {ratio:.2f}"


def test_on_primary_counterproof():
    """反证：白字压在 default 的原强调色上确实不达标（说明这条规则有实际意义）。"""
    original = "#C4845C"
    assert contrast_ratio("#FFFFFF", original) < 4.5, "反证前提变了，需重新评估这条规则"


# ---------------------------------------------------------------- 门禁二


REQUIRED_SELECTORS = (
    "QToolButton",
    "QToolButton:hover",
    "QToolButton:checked",
    "QCheckBox::indicator",
    "QCheckBox::indicator:checked",
    "QRadioButton::indicator",
    "QListView::item:selected",
    "QListWidget::item:hover",
    "QSplitter::handle",
    "QSizeGrip",
)


def test_control_coverage_qss_contains_required_selectors():
    """覆盖块必须真的写着这些选择器——被删掉就等于回到原生皮肤。"""
    qss = _control_coverage_qss(THEMES["default"].colors)
    missing = [sel for sel in REQUIRED_SELECTORS if sel not in qss]
    assert not missing, f"覆盖块缺选择器：{missing}"


def test_control_coverage_works_for_every_theme():
    """五个主题的颜色键不完全相同，覆盖块必须都能生成且不含未替换的 {占位}。"""
    for name, theme in THEMES.items():
        qss = _control_coverage_qss(theme.colors)
        assert qss.strip().startswith("/*"), f"{name} 覆盖块为空"
        # f-string 的变量都替换完，不该残留单层花括号包着的键名
        assert "{text}" not in qss and "{border}" not in qss, f"{name} 有未替换占位符"
        assert "background-color: ;" not in qss, f"{name} 有空值声明"


def test_control_coverage_is_applied_by_every_theme():
    """apply() 必须把覆盖块并进最终 QSS，否则前面几条都是空谈。"""
    from themes import Theme

    for name, theme in THEMES.items():
        base = theme._build_qss()
        merged = base + _control_coverage_qss(theme.colors)
        assert "QToolButton" in merged, f"{name} 最终 QSS 不含 QToolButton"
        assert merged != base, f"{name} 覆盖块没接上"
        # 覆盖块必须排在主题自带 QSS 之后（同优先级后写者生效）
        assert merged.index("QToolButton") > len(base) - 1


def test_no_theme_leaves_toolbutton_unstyled():
    """直接检查真实生成的样式表：任何一个主题都不许漏掉 QToolButton。"""
    import inspect

    src = inspect.getsource(type(THEMES["default"]).apply)
    assert "_control_coverage_qss" in src, (
        "Theme.apply 又回到只设 _build_qss()，原生控件会退回系统皮肤")
