# SPDX-License-Identifier: MIT
"""UX-07/12：星象仪退出入口可读，录音指示只读状态不自起计时。"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QLabel, QPushButton  # noqa: E402


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def window(qtapp):
    import main_window as mw
    w = mw.MainWindow()
    w.resize(1280, 800)
    w.show()
    yield w, mw
    try:
        w._teardown_connection()
    except Exception:
        import logging
        logging.getLogger(__name__).exception("MainWindow teardown 失败")


def test_planetarium_exit_entry_is_readable(window):
    w, mw = window
    strip = w._planetarium_strip
    assert strip.width() >= 120, f"窄条必须够宽，实际: {strip.width()}"
    btn = strip.findChild(QPushButton, "planetariumExit")
    assert btn is not None, "退出按钮必须有稳定 objectName"
    text = btn.text()
    assert "退出星象仪" in text, f"按钮文案必须说人话，实际: {text}"
    assert "Esc" in text
    assert btn.minimumHeight() >= 44, "触控目标必须够高"
    assert btn.sizeHint().width() <= strip.width() - 8, (
        f"最小宽度下文本要完整显示，sizeHint={btn.sizeHint().width()}")


def test_planetarium_recording_indicator_reads_state(window):
    w, mw = window
    strip = w._planetarium_strip
    rec = strip.findChild(QLabel, "planetariumRecIndicator")
    assert rec is not None, "星象仪必须有独立录音指示"
    assert rec.isHidden() is True, "未录音时指示必须隐藏"

    w._recording = True
    w._record_seconds = 65
    w._sync_planetarium_recording()
    assert rec.isHidden() is False
    assert "01:05" in rec.text()

    w._record_seconds = 66
    w._sync_planetarium_recording()
    assert "01:06" in rec.text(), "文本必须跟随 _record_seconds 变化"

    before = rec.text()
    time.sleep(0.3)
    assert rec.text() == before, "指示不得自起计时（必须只读状态）"

    w._recording = False
    w._sync_planetarium_recording()
    assert rec.isHidden() is True, "停止录音后必须隐藏"


def test_overlay_is_not_in_layout_and_sky_keeps_full_width(window, qtapp):
    w, mw = window
    w.set_app_mode("planetarium")
    qtapp.processEvents()
    w._planetarium_page.layout().activate()

    strip = w._planetarium_strip
    sky = w.sky_view
    page = w._planetarium_page
    assert strip.parent() is sky, "浮层必须挂在天空视图上，才不占布局"
    assert page.layout().indexOf(strip) == -1, "浮层不得进入页面布局"
    # 满宽 = 天空宽度等于页面宽度（浮层不扣任何一列像素）。
    assert sky.width() == page.width(), (
        f"天空视图必须满宽：sky={sky.width()} page={page.width()}")
    assert sky.height() == w._planetarium_sky_slot.height(), (
        f"天空视图必须填满天空槽：sky={sky.height()} "
        f"slot={w._planetarium_sky_slot.height()}")
    # 相对窄条版（764 宽 / 534 高）不得更小；浮层版实际是 800×536。
    assert sky.width() >= 764 and sky.height() >= 534, (
        f"天空视图不得比窄条版更小，实际: {sky.width()}x{sky.height()}")
    assert strip.x() + strip.width() == sky.width() - 12, (
        f"浮层须贴右边距 12px，实际 x={strip.x()} w={strip.width()} "
        f"sky={sky.width()}")
    assert strip.y() == 12, "浮层须贴上边距 12px"


def test_overlay_exit_button_is_clickable(window):
    w, mw = window
    w.set_app_mode("planetarium")
    strip = w._planetarium_strip
    btn = strip.findChild(QPushButton, "planetariumExit")
    assert strip.isVisible() is True
    assert btn.isVisible() is True and btn.isEnabled() is True
    btn.click()
    assert w._app_mode == "ide", "浮层按钮点击必须能退出星象仪"
    assert strip.isHidden() is True, "退出后浮层必须隐藏"
