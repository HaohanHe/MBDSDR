# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""导入身份守卫：desktop.* 包名白名单 + themes 全局复位生效。

两条都便宜：

1. `MainWindow()` 起完之后，`sys.modules` 里 `desktop.*` 的键集合必须等于
   白名单。多一个就是新增了一个包式导入点 —— 先确认它有没有可变全局状态，
   有就是两个真值源（`themes` 就是这么裂的，见 docs/pending-patches/
   2026-11-11-themes-dual-instance.md）。
2. `themes` 的三个进程级全局在每个用例起点必须是干净的。复位由
   `tests/conftest.py::_restore_font_scale_globals`（autouse）负责；
   这里断言它真的生效，顺带把"谁会把状态留在进程里"钉成可执行事实。
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
    """QWidget 必须先有 QApplication，否则 Qt 直接 abort（0xC0000409）。"""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


#: 允许出现在 sys.modules 里的 desktop 包名键。
#:   desktop           —— 包垫片本身（config_schema / shortcuts 的硬包导入会拉起）
#:   desktop.constants —— config_schema 优先从包名取常量（无状态，两份无害）
#:   desktop.themes    —— themes 的 sys.modules 别名（commit 6c1064b1）
ALLOWED_DESKTOP_PACKAGE_KEYS = frozenset({
    "desktop",
    "desktop.constants",
    "desktop.themes",
})


class _Screen:
    """只实现 fit_scale_to_screen 用得到的接口，避免依赖真实显示器。"""

    def availableGeometry(self):
        from PySide6.QtCore import QRect
        return QRect(0, 0, 800, 800)


class _FakeWindow:
    right_tab = None

    def screen(self):
        return _Screen()

    def ensurePolished(self):
        pass

    def layout(self):
        return None

    def minimumSizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(1200, 800)


def _desktop_package_keys():
    return {k for k in sys.modules if k == "desktop" or k.startswith("desktop.")}


def test_desktop_package_import_whitelist(qtapp, isolated_profile):
    """构造一次 MainWindow 新增的 desktop.* 包名键，必须全部落在白名单里。

    只看「本次构造新增」而不是「当前全量键集合」：sys.modules 是进程级的，
    全量跑时别的测试文件先 ``import desktop.main_window`` 就会留下
    ``desktop.main_window``（实测与 ``test_desktop_panels`` 同跑即命中），
    严格等于白名单会把别人的导入记到这次构造头上，单跑绿、全量红。
    干净进程（构造前一个 desktop.* 都没有）仍按「等于白名单」严格校验，
    这样白名单本身漂了也会红。
    """
    from PySide6.QtCore import Qt

    import main_window as mw

    before = _desktop_package_keys()
    win = mw.MainWindow()
    win.setAttribute(Qt.WA_DontShowOnScreen, True)
    try:
        after = _desktop_package_keys()
    finally:
        try:
            win._teardown_connection()
        except Exception:  # noqa: BLE001 - 拆线失败不影响断言
            pass

    extra = sorted((after - before) - ALLOWED_DESKTOP_PACKAGE_KEYS)
    assert not extra, (
        "新增了 desktop 包式导入点，先确认是否会造成双实例：\n"
        f"  多出：{extra}\n"
        f"  白名单：{sorted(ALLOWED_DESKTOP_PACKAGE_KEYS)}\n"
        "判据：该模块有没有字典/集合/缓存这类可变全局；有，两份副本就是两个真值源。"
    )
    if not before:
        missing = sorted(ALLOWED_DESKTOP_PACKAGE_KEYS - after)
        assert not missing, (
            f"干净进程里构造 MainWindow 后，包名键比白名单少：{missing}\n"
            "若是有意改导入方式，请同步更新白名单与本说明。"
        )


def test_font_scale_globals_start_pristine():
    """每个用例起点，themes 的三个全局必须是平台默认（无降档、无提示）。"""
    import themes

    assert themes._FONT_SCALE_OVERRIDE is None, (
        "上一个用例把降档倍率留在进程里了；conftest 的 autouse 复位没生效。")
    assert themes._FONT_SCALE_NOTE is None, (
        "上一个用例把降档提示留在进程里了；conftest 的 autouse 复位没生效。")
    assert themes._PLATFORM_FONT_PT is None, (
        "平台字号被前序用例缓存成固定值了；conftest 的 autouse 复位没生效。")


def test_fit_scale_writes_process_wide_state(monkeypatch):
    """fit_scale_to_screen 改的是模块级全局（这正是需要复位的原因）。

    故意不复位：下一个用例（无论顺序）必须仍看到干净状态。这一条是
    conftest 复位的证据来源，不是产品行为断言。
    """
    import themes

    monkeypatch.setattr(themes, "_platform_font_pt", lambda: 15.0)
    chosen = themes.fit_scale_to_screen(_FakeWindow())

    assert chosen < 1.5, "800px 屏宽放不下 150%，应降档"
    assert themes.font_scale_note(), "降档必须写出说明"
