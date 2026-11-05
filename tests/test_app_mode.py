#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
双形态壳层 offscreen 钉扎测试 (tests/test_app_mode.py)
=======================================================

顶层形态切换（IDE ↔ 星象仪）行为钉扎：
  * 形态栈存在且页0=IDE root（含三栏）、页1=星象仪页；
  * 进星象仪：sky_view 换父到星象仪页（对象身份不变，信号零重接）；
  * 退出：sky_view 归位 left_tab 原页签位，tabData "rf_sky" 不变；
  * 往返两轮后几何与页签数不漂移；
  * Esc 退出快捷键只在星象仪形态启用（IDE 形态不劫持 Esc）；
  * 视图菜单 checkable action 与形态状态同步。

诚实空态：无观测站/无 TLE 与本机制无关——形态切换不依赖观测站坐标。

运行:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_app_mode.py -v
"""
import logging
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

log = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication, QStackedWidget, QTabWidget  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _pump(app, ms=80):
    from PySide6.QtCore import QTimer
    QTimer.singleShot(ms, app.quit)
    app.exec()


@pytest.fixture(scope="module")
def mw(isolated_profile, qapp):
    # isolated_profile 见 tests/conftest.py：本夹具会 set_app_mode/写 settings，
    # 不做隔离就会把 app_mode 写进用户真实配置。
    from main_window import MainWindow
    w = MainWindow()
    # 测试全程从 IDE 形态出发，且结束后回 IDE，不污染真实用户配置。
    w.settings.set("app_mode", "ide")
    w.set_app_mode("ide")
    w.show()
    _pump(qapp, 150)
    yield w
    w.set_app_mode("ide")
    w.settings.set("app_mode", "ide")
    try:
        w._disconnect()
    except Exception as exc:  # noqa: BLE001 - 收尾失败必须留证，不静默
        log.warning("test_app_mode.py fixture 收尾失败（MainWindow._disconnect）：%s: %s",
                    type(exc).__name__, exc)
    w.close()


class TestModeShell:
    """顶层形态栈骨架。"""

    def test_mode_stack_is_central(self, mw):
        assert isinstance(mw.centralWidget(), QStackedWidget)
        assert mw.centralWidget() is mw._mode_stack

    def test_two_pages_ide_first(self, mw):
        assert mw._mode_stack.count() == 2
        # 页0 = IDE root，里面是三栏
        ide_root = mw._mode_stack.widget(0)
        assert mw._main_splitter.parent() is ide_root
        # 页1 = 星象仪页
        assert mw._mode_stack.widget(1) is mw._planetarium_page

    def test_default_mode_is_ide(self, mw):
        assert mw._app_mode == "ide"
        assert mw._mode_stack.currentIndex() == 0

    def test_sky_starts_in_left_tab(self, mw):
        assert mw.left_tab.indexOf(mw.sky_view) >= 0
        idx = mw.left_tab.indexOf(mw.sky_view)
        assert mw.left_tab.tabBar().tabData(idx) == "rf_sky"


class TestEnterPlanetarium:
    """进星象仪：sky_view 换父不换身份。"""

    def test_enter_moves_sky(self, mw):
        mw.set_app_mode("planetarium")
        try:
            assert mw._app_mode == "planetarium"
            assert mw._mode_stack.currentWidget() is mw._planetarium_page
            # sky_view 身份不变（同一对象），父链落在星象仪页内
            assert mw.sky_view.parentWidget() is mw._planetarium_sky_slot
            # left_tab 不再持有天空页签（页签数减一）
            assert mw.left_tab.indexOf(mw.sky_view) < 0
        finally:
            mw.set_app_mode("ide")

    def test_enter_persists_mode(self, mw):
        mw.set_app_mode("planetarium")
        try:
            assert str(mw.settings.get("app_mode", "ide")) == "planetarium"
        finally:
            mw.set_app_mode("ide")

    def test_esc_enabled_only_in_planetarium(self, mw):
        assert not mw._planetarium_esc.isEnabled()
        mw.set_app_mode("planetarium")
        try:
            assert mw._planetarium_esc.isEnabled()
        finally:
            mw.set_app_mode("ide")
        assert not mw._planetarium_esc.isEnabled()

    def test_menu_action_checked_sync(self, mw):
        mw._planetarium_action.setChecked(True)
        mw._planetarium_action.trigger()
        try:
            assert mw._app_mode == "planetarium"
            assert mw._planetarium_action.isChecked()
        finally:
            mw.set_app_mode("ide")
        assert not mw._planetarium_action.isChecked()


class TestExitRestore:
    """退出星象仪：sky_view 归位原容器。"""

    def test_exit_restores_tab(self, mw):
        orig_index = mw.left_tab.indexOf(mw.sky_view)
        assert orig_index >= 0
        mw.set_app_mode("planetarium")
        mw.set_app_mode("ide")
        assert mw._app_mode == "ide"
        assert mw._mode_stack.currentIndex() == 0
        new_index = mw.left_tab.indexOf(mw.sky_view)
        assert new_index == orig_index
        assert mw.left_tab.tabBar().tabData(new_index) == "rf_sky"
        assert str(mw.settings.get("app_mode", "ide")) == "ide"

    def test_roundtrip_twice_no_drift(self, mw):
        for _ in range(2):
            mw.set_app_mode("planetarium")
            assert mw.left_tab.indexOf(mw.sky_view) < 0
            mw.set_app_mode("ide")
        assert mw.left_tab.indexOf(mw.sky_view) >= 0
        texts = [mw.left_tab.tabText(i) for i in range(mw.left_tab.count())]
        assert "天空模式" in texts
        assert isinstance(mw.left_tab, QTabWidget)


class TestPlanetariumAssembly:
    """阶段1：星象仪页组装——瀑布/AI 命令栏 reparent，AI 回复覆盖卡。"""

    def test_waterfall_moves_and_restores(self, mw):
        assert mw._spectrum_page_layout.indexOf(mw.spectrum) >= 0
        mw.set_app_mode("planetarium")
        try:
            assert mw._spectrum_page_layout.indexOf(mw.spectrum) < 0
            assert mw._planetarium_waterfall_layout.indexOf(mw.spectrum) >= 0
            assert mw.spectrum.parentWidget() is mw._planetarium_waterfall_slot
            assert mw._planetarium_waterfall_slot.isVisibleTo(mw)
        finally:
            mw.set_app_mode("ide")
        assert mw._spectrum_page_layout.indexOf(mw.spectrum) >= 0
        assert mw._planetarium_waterfall_layout.indexOf(mw.spectrum) < 0
        assert not mw._planetarium_waterfall_slot.isVisibleTo(mw)

    def test_ai_command_bar_moves_and_restores(self, mw):
        ide_parent = mw._ai_cmd_bar.parentWidget()
        mw.set_app_mode("planetarium")
        try:
            assert mw._ai_cmd_bar.parentWidget() is mw._planetarium_sky_slot
            assert mw._ai_cmd_parent is mw._planetarium_sky_slot
        finally:
            mw.set_app_mode("ide")
        assert mw._ai_cmd_bar.parentWidget() is ide_parent
        assert mw._ai_cmd_parent is ide_parent

    def test_ai_command_reaches_handler_in_planetarium(self, mw, monkeypatch):
        from unittest.mock import MagicMock
        fake_call = MagicMock()
        monkeypatch.setattr(mw.ai_panel, "_call_ai", fake_call)
        mw.set_app_mode("planetarium")
        try:
            mw.cmd_input.setText("今晚 NOAA-19 过境吗")
            mw._on_cmd_submit()
            fake_call.assert_called_once_with("今晚 NOAA-19 过境吗")
        finally:
            mw.set_app_mode("ide")
            monkeypatch.undo()

    def test_ai_reply_card_shows_text(self, mw):
        mw.set_app_mode("planetarium")
        try:
            mw._on_planetarium_ai_reply("NOAA-19 22:14 升起")
            assert mw._planetarium_ai_card.isVisibleTo(mw)
            assert mw._planetarium_ai_card_text.text() == "NOAA-19 22:14 升起"
        finally:
            mw.set_app_mode("ide")

    def test_ai_reply_card_empty_honest_placeholder(self, mw):
        mw.set_app_mode("planetarium")
        try:
            mw._on_planetarium_ai_reply("")
            assert "未返回文本" in mw._planetarium_ai_card_text.text()
        finally:
            mw.set_app_mode("ide")

    def test_ai_reply_card_hides_on_exit(self, mw):
        mw.set_app_mode("planetarium")
        mw._on_planetarium_ai_reply("回复")
        assert mw._planetarium_ai_card.isVisibleTo(mw)
        mw.set_app_mode("ide")
        assert not mw._planetarium_ai_card.isVisibleTo(mw)


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
