# SPDX-License-Identifier: MIT
"""
主题切换不得让 app 级样式表变长（A′ 回归）
==========================================

2026-11-11 实测缺陷：触屏模式下每次切主题 ``app.styleSheet()`` 都长 528 字节
（20832 → 22944）。根因是 ``MainWindow._apply_theme`` 把**已含触控后缀**的
``app.styleSheet()`` 记成了 ``_theme_qss`` 基串，``_apply_touch_stylesheet``
再叠一遍——切一百次就是 50 KB 的样式表，每次重抛光都要重新解析。

本文件钉两件事：
  1. 触屏模式下连切 20 次主题，``len(app.styleSheet())`` 恒定（失败时打印
     切前长度，能直接看出是不是又叠了）；
  2. 切主题期间 app 级 ``setStyleSheet`` 调用为 0；切换一次触控开关后恰好
     +1，且后缀长度真的增减——不能靠「永不重设」蒙混过关。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_theme_qss_growth.py -q
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

THEME_CYCLE = ["default", "dark", "high_contrast", "dark_car", "figma"]


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def mw(isolated_profile, qapp):
    # isolated_profile：MainWindow 会读写真实 %APPDATA% 配置，不隔离会污染用户设置。
    from main_window import MainWindow
    w = MainWindow()
    yield w
    try:
        w.close()
    except Exception:
        pass


class _CountAppStyleSheet:
    """只统计 **app 级** setStyleSheet（控件级不算，那是各面板自己的样式）。"""

    def __init__(self, app):
        self.app = app
        self.lengths = []

    def __enter__(self):
        self._orig = QApplication.setStyleSheet

        def _counting(widget, sheet):
            if widget is self.app:
                self.lengths.append(len(sheet))
            return self._orig(widget, sheet)

        QApplication.setStyleSheet = _counting
        return self

    def __exit__(self, *exc):
        QApplication.setStyleSheet = self._orig
        return False

    def clear(self):
        self.lengths.clear()

    @property
    def calls(self):
        return len(self.lengths)


def test_touch_mode_theme_switches_do_not_grow_app_stylesheet(mw, qapp):
    """连切 20 次主题，样式表长度必须恒定。"""
    from touch_manager import touch_manager

    tm = touch_manager()
    old_override = tm._mode_override
    try:
        with _CountAppStyleSheet(qapp) as counter:
            tm.set_override(True)          # 触屏模式：把后缀挂上去
            mw._apply_theme(THEME_CYCLE[0])
            before = len(qapp.styleSheet())
            assert before > 0, "触屏模式首次应用主题后样式表不应为空"

            lengths = []
            calls_per_switch = []
            for i in range(20):
                counter.clear()
                mw._apply_theme(THEME_CYCLE[(i + 1) % len(THEME_CYCLE)])
                lengths.append(len(qapp.styleSheet()))
                calls_per_switch.append(counter.calls)

            assert len(set(lengths)) == 1, (
                f"切主题让 app 级样式表变长：切前 {before} 字节，"
                f"第 20 次后 {lengths[-1]} 字节（差 {lengths[-1] - before}）；"
                f"逐次长度 {lengths}")
            assert lengths[0] == before, (
                f"第一次切换就改变了长度：切前 {before} → {lengths[0]}")
            assert sum(calls_per_switch) == 0, (
                f"切主题仍在重设 app 级样式表（20 次共 {sum(calls_per_switch)} 次，"
                f"逐次 {calls_per_switch}）；A′ 只换 QPalette + 重抛光")
    finally:
        tm.set_override(old_override)


def test_touch_mode_toggle_appends_and_removes_suffix_once(mw, qapp):
    """触控开关每切换一次只重设一次样式表，并真的增/减后缀。"""
    from touch_manager import touch_manager

    tm = touch_manager()
    old_override = tm._mode_override
    try:
        with _CountAppStyleSheet(qapp) as counter:
            tm.set_override(False)         # 先回到鼠标模式（基串）
            mw._apply_theme(THEME_CYCLE[0])
            mouse_len = len(qapp.styleSheet())

            counter.clear()
            tm.set_override(True)          # 鼠标 → 触屏：恰好一次
            assert counter.calls == 1, (
                f"开启触屏应恰好 setStyleSheet 1 次，实际 {counter.calls} 次")
            touch_len = len(qapp.styleSheet())
            assert touch_len > mouse_len, (
                f"触屏后缀没挂上：鼠标 {mouse_len} → 触屏 {touch_len}")

            counter.clear()
            tm.set_override(False)         # 触屏 → 鼠标：再恰好一次
            assert counter.calls == 1, (
                f"关闭触屏应恰好 setStyleSheet 1 次，实际 {counter.calls} 次")
            assert len(qapp.styleSheet()) == mouse_len, (
                f"关掉触屏后样式表未回到基串：{len(qapp.styleSheet())} != {mouse_len}")
    finally:
        tm.set_override(old_override)
