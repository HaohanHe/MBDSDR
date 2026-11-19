#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""冷启动默认形态配置项 app_mode_default 的行为钉扎（D7）。

用户拍板的是「加配置项，默认仍 IDE」：本文件只证明两件事——

1. 不配置（或配置坏值/读异常）时，冷启动与旧行为一致：`_app_mode == "ide"`；
2. 配置 `"planetarium"` 时，冷启动真的进星象仪形态（标志位 + 页面都换了），
   而不是只改了一个标志位、页面仍停在形态栈页 0。

既有三条回 IDE 的路径不动；这里只钉 `closeEvent` 仍回 IDE，防止默认形态
可配置后把退出路径带偏。

运行:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_app_mode_default.py -q
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_UNSET = object()


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def make_window(isolated_profile, qapp):
    """按需构造 MainWindow（默认形态配置在构造前写入隔离配置）。"""
    created = []

    def _make(default=_UNSET, settings_cls=None):
        import settings as settings_mod

        if default is not _UNSET:
            path = settings_mod.default_config_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            ds = settings_mod.DesktopSettings(path=path)
            # 显式写"上次形态=ide"，避免模块级配置被前序用例留成星象仪。
            ds.set("app_mode", "ide", explicit=False)
            # app_mode_default 是用户显式配置（非默认值），必须带显式标记，
            # 否则 load() 会按「没设过 → 跟随当前默认值」丢掉它。
            ds.set("app_mode_default", default)
            ds.flush()

        import main_window as mwmod

        old_cls = mwmod.DesktopSettings
        if settings_cls is not None:
            mwmod.DesktopSettings = settings_cls
        try:
            w = mwmod.MainWindow()
        finally:
            mwmod.DesktopSettings = old_cls
        # 启动延迟定时器会枚举/连接真实 SDR 与 GNSS：本文件只验形态，
        # 先停掉，免得测试顺带碰硬件（也避免与其它车道抢设备）。
        for t in getattr(w, "_startup_timers", []):
            try:
                t.stop()
            except Exception:
                pass
        w.setAttribute(Qt.WA_DontShowOnScreen, True)
        w.show()
        for _ in range(6):
            qapp.processEvents()
        created.append(w)
        return w

    yield _make

    for w in created:
        try:
            w.settings.set("app_mode", "ide", explicit=False)
            w.set_app_mode("ide")
        except Exception:
            pass
        try:
            w._disconnect()
        except Exception:
            pass
        try:
            w.close()
        except RuntimeError:
            pass
    qapp.processEvents()


def _ide_shape(w):
    """形态标志 + 可见页同时是 IDE 才算真的旧行为。"""
    return (w._app_mode == "ide"
            and w._mode_stack.currentIndex() == 0
            and w._mode_stack.widget(0) is not w._planetarium_page
            and w._planetarium_page.parentWidget() is w._mode_stack)


# ---------------------------------------------------------------------------
# 1. 反证：不配置时冷启动仍是 IDE（默认行为不变）
# ---------------------------------------------------------------------------
def test_unconfigured_startup_stays_ide(make_window):
    w = make_window()
    assert w.settings.get("app_mode_default") == "ide", (
        "未配置时 schema 默认值必须仍是 ide")
    assert _ide_shape(w)


# ---------------------------------------------------------------------------
# 2. 配置 planetarium：冷启动真的进星象仪形态
# ---------------------------------------------------------------------------
def test_configured_planetarium_enters_on_startup(make_window):
    w = make_window(default="planetarium")
    assert w._app_mode == "planetarium"
    # 不只是标志位：页面已从形态栈搬进独立天空窗并显示
    assert w._planetarium_window is not None
    assert w._planetarium_page.parentWidget() is w._planetarium_window
    assert w._mode_stack.indexOf(w._planetarium_page) < 0
    assert w._planetarium_page.isVisible()
    assert w._mode_stack.currentIndex() == 0


# ---------------------------------------------------------------------------
# 3. 非法值/空值归一：一律 ide，不抛异常
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw", ["sky", "", "   ", None, 42])
def test_invalid_values_normalize_to_ide(raw):
    from main_window import _normalize_app_mode_default

    assert _normalize_app_mode_default(raw) == "ide"


def test_invalid_value_startup_falls_back_without_crash(make_window):
    import settings as settings_mod

    class _SkySettings(settings_mod.DesktopSettings):
        def get(self, key, default=None):
            if key == "app_mode_default":
                return "sky"
            return super().get(key, default)

    w = make_window(settings_cls=_SkySettings)
    assert _ide_shape(w)


# ---------------------------------------------------------------------------
# 4. 配置读取抛异常：回落 ide，主窗口照常起来
# ---------------------------------------------------------------------------
def test_settings_read_exception_falls_back_to_ide(make_window):
    import settings as settings_mod

    class _RaisingSettings(settings_mod.DesktopSettings):
        def get(self, key, default=None):
            if key == "app_mode_default":
                raise RuntimeError("模拟配置读取失败")
            return super().get(key, default)

    w = make_window(settings_cls=_RaisingSettings)
    assert _ide_shape(w)


# ---------------------------------------------------------------------------
# 5. 既有回 IDE 路径不受影响：关掉天空窗（closeEvent）仍回 IDE
# ---------------------------------------------------------------------------
def test_planetarium_window_close_still_returns_to_ide(make_window):
    w = make_window(default="planetarium")
    assert w._app_mode == "planetarium"
    # 关掉独立天空窗 = 退出星象仪形态（_PlanetariumWindow.closeEvent 的既有契约）
    w._planetarium_window.closeEvent(QCloseEvent())
    assert w._app_mode == "ide"
    assert w._mode_stack.currentIndex() == 0


if __name__ == "__main__":
    import unittest

    unittest.main(verbosity=2)
