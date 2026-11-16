#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""设置右栏横溢修复的结构钉扎（2026-11-15 几何审计收口）。

真缺陷回顾：右栏视口 ~310px，但若干行的固有最小宽把面板顶到 317~392，
右侧永久横滚、最右输入被裁。根因：
  1. 枚举下拉 / 带后缀 spinbox 的 minimumSizeHint 过大（主题 147、静噪 222），
     把整行→面板最小宽撑爆；
  2. 「数据与操作」导出/导入/打开配置目录三按钮横排最小宽 ~300。
修复：_build_row 给控件一个「正的」小布局下限解耦固有 hint（Qt qSmartMinSize
仅在 min>0 时覆盖 minimumSizeHint，设 0 无效）；三按钮拆成两行。

为什么钉结构而不钉 offscreen 绝对几何：offscreen 用回退字体（中文□），
minimumSizeHint 与任何真实用户字体都不同（实测 offscreen 321 vs 原生 269），
钉绝对宽会把字体伪影当回归。真实字体下的「逐节/全展开/五档窗宽均无横滚」
已由仓库外探针 settings_matrix.py / settings_sizes.py 实证（原生 Windows）。
这里钉住两个与字体无关的根因不变量，防止修复被回退。
不删/放宽任何既有断言；offscreen，不碰真硬件真网络。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QHBoxLayout, QPushButton  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from main_window import MainWindow  # noqa: E402
import right_column as rc  # noqa: E402


@pytest.fixture(scope="module")
def window(isolated_profile):
    w = MainWindow()
    w.resize(1440, 900)
    w.show()
    for _ in range(8):
        app.processEvents()
    yield w
    w._active_sdr_backend = None


def _panel(w):
    panel = w.settings_panel
    panel._ensure_ui_built()   # 懒构建：offscreen 不 show 透时也要真建出分节
    w._show_right_tab_of(panel)
    for _ in range(8):
        app.processEvents()
    return panel


def test_row_control_min_width_decoupled(window):
    """控件布局下限应为正的小值（解耦固有 hint），而非 0/未设（0 会被 Qt 回退）。"""
    w = window
    panel = _panel(w)
    from config_schema import SCHEMA
    assert panel._rows, "设置面板应渲染出 schema 行控件"
    checked = 0
    for key, control in panel._rows.items():
        if SCHEMA[key].kind in ("enum", "int", "float", "text", "bool"):
            mw = control.minimumWidth()
            assert 0 < mw <= 120, (
                f"{key}: 控件最小宽 {mw} 应为正的小布局下限（0 无效、过大撑爆右栏）")
            checked += 1
    assert checked > 0, "应至少检查到一个 schema 行控件"


def test_config_action_buttons_split_not_one_wide_row(window):
    """导出/导入/打开配置目录不得同处一个横排（三按钮横排 ~300 撑爆右栏）。"""
    w = window
    panel = _panel(w)
    secs = panel.findChildren(rc.RightSection)
    assert secs, "设置面板应有右栏分节"
    buttons = [b for b in panel.findChildren(QPushButton)
               if b.text() in ("导出配置", "导入配置", "打开配置目录")]
    assert len(buttons) == 3, f"应找到三个配置操作按钮，实得 {len(buttons)}"
    worst = 0
    for hbox in panel.findChildren(QHBoxLayout):
        n = sum(1 for b in buttons if hbox.indexOf(b) >= 0)
        worst = max(worst, n)
    assert worst <= 2, (
        f"有 {worst} 个配置按钮同处一个横排；三按钮横排最小宽 ~300 > 右栏可用宽，"
        "应拆成两行（导出/导入 一行、打开目录 一行）")