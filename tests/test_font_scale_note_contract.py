# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""契约：直接调 themes.fit_scale_to_screen() → 设置页提示必须出现。

这条用例**不经过 `desktop/main.py`**，也不在测试里给任何函数套 wrapper：
`3a04ee8b` 已把入口定成「SettingsPanel 构造先刷一次 + main.py 唯一入口」，
再包一层就是第二套入口。这里只钉一件事：

    字号降档是 themes 的模块级状态变化，状态一变，已建好的设置页就该看到。

它红，说明缺的是「状态变更广播」——去产品侧补广播，不要在测试里绕。
（请求 150% + 800px 屏宽：`fit_scale_to_screen` 必然降档并写出说明。）
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


class _Screen:
    def availableGeometry(self):
        from PySide6.QtCore import QRect
        return QRect(0, 0, 800, 800)


@pytest.mark.xfail(
    strict=True,
    reason=("缺「状态变更广播」：themes 降档后已建好的设置页不刷新。"
            "单据见 docs/pending-patches/2026-11-11-themes-dual-instance.md；"
            "产品侧补上广播后本用例会 XPASS，strict=True 让那天必须来摘标。"))
def test_fit_scale_to_screen_shows_note_in_settings_panel(
        qtapp, isolated_profile, monkeypatch):
    from PySide6.QtCore import Qt

    import main_window as mw
    import themes

    win = mw.MainWindow()
    win.setAttribute(Qt.WA_DontShowOnScreen, True)
    try:
        # 固定屏幕与平台字号，结果不随跑测机器的真实显示器变化。
        win.screen = lambda: _Screen()
        themes.set_font_scale_override(None)
        themes._FONT_SCALE_NOTE = None
        monkeypatch.setattr(themes, "_platform_font_pt", lambda: 15.0)

        chosen = themes.fit_scale_to_screen(win)

        note = themes.font_scale_note()
        label = win.settings_panel._font_scale_note
        assert chosen < 1.5, "800px 屏宽放不下 150%，应降档"
        assert note, "降档必须写出说明"
        assert label.text() == note, (
            "状态变了但设置页还是旧文案：缺「状态变更广播」。\n"
            f"  themes 里是：{note!r}\n"
            f"  设置页里是：{label.text()!r}")
        assert not label.isHidden(), "提示有内容但控件仍隐藏"
    finally:
        try:
            win._teardown_connection()
        except Exception:  # noqa: BLE001 - 拆线失败不影响断言
            pass
