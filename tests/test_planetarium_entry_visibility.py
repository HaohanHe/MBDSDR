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
from PySide6.QtCore import QPoint, QRect  # noqa: E402
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
    # 相对窄条版（764 宽）不得更窄；高度随瀑布槽显隐变化，由上面的
    # “填满天空槽”等式负责，不再钉死 534。
    assert sky.width() >= 764, (
        f"天空视图不得比窄条版更窄，实际: {sky.width()}x{sky.height()}")
    assert strip.x() + strip.width() == sky.width() - 12, (
        f"浮层须贴右边距 12px，实际 x={strip.x()} w={strip.width()} "
        f"sky={sky.width()}")
    bar = w._ai_cmd_bar
    bar_rect = QRect(
        sky.mapFromGlobal(bar.mapToGlobal(QPoint(0, 0))), bar.size())
    ex, ey = mw._planetarium_overlay_pos(
        sky.width(), sky.height(), strip.width(), strip.height(), bar_rect)
    assert (strip.x(), strip.y()) == (ex, ey), (
        f"浮层位置必须与几何判定一致：实际 {(strip.x(), strip.y())} "
        f"期望 {(ex, ey)}")


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


def test_overlay_hidden_in_ide_mode(window, qtapp):
    """IDE 形态不许留一块挡住星图的浮层。"""
    w, mw = window
    w.set_app_mode("ide")
    qtapp.processEvents()
    strip = w._planetarium_strip
    assert strip.isHidden() is True, "IDE 形态浮层必须隐藏"
    assert strip.isVisible() is False
    assert w._app_mode == "ide"


def test_overlay_avoids_ai_bar_only_on_intersection(window, qtapp):
    """相交才下移，回到宽屏必须自动回到上 12；命令栏麦克风按钮不得被盖。"""
    w, mw = window
    w.set_app_mode("planetarium")
    qtapp.processEvents()
    sky = w.sky_view
    strip = w._planetarium_strip
    bar = w._ai_cmd_bar

    def bar_rect():
        tl = sky.mapFromGlobal(bar.mapToGlobal(QPoint(0, 0)))
        return QRect(tl, bar.size())

    def strip_rect():
        return QRect(strip.x(), strip.y(), strip.width(), strip.height())

    # 阶段 1：宽 1280 → 默认贴右上 12，不压命令栏。
    # 窗口最小 1000 宽且 offscreen 屏幕尺寸会漂，这里显式按 1280 布局，
    # 保证三条断言在任何机器上都是同一组几何。
    sky.resize(1280, sky.height())
    bar.setFixedWidth(min(1280 - 40, 560))
    bar.move((1280 - bar.width()) // 2, 16)
    w._position_planetarium_overlay()
    assert strip.x() + strip.width() == 1280 - 12
    assert strip.y() == 12, f"宽屏浮层必须贴上 12，实际 y={strip.y()}"
    assert not strip_rect().intersects(bar_rect())

    # 阶段 2：压到 800 宽（<888）→ 浮层与命令栏真的相交，整体下移。
    sky.resize(800, sky.height())
    bar.setFixedWidth(min(800 - 40, 560))
    bar.move((800 - bar.width()) // 2, 16)
    w._position_planetarium_overlay()
    r = bar_rect()
    assert strip_rect().intersects(r) is False, (
        f"避让后不得再压命令栏：strip={strip.geometry().getRect()} "
        f"bar={r.getRect()}")
    assert strip.y() == r.bottom() + 1 + 8, (
        f"相交时必须下移到命令栏底部 +8，实际 y={strip.y()} "
        f"bar.bottom={r.bottom()}")
    mic_center = sky.mapFromGlobal(
        w.cmd_mic_btn.mapToGlobal(w.cmd_mic_btn.rect().center()))
    assert strip_rect().contains(mic_center) is False, (
        "浮层不得盖住命令栏麦克风/发送按钮中心点")
    assert w.cmd_mic_btn.isVisible() and w.cmd_mic_btn.isEnabled()

    # 阶段 3：拉回宽屏 → 下一次重排自动回到上 12（不是永久下移）。
    sky.resize(1280, sky.height())
    bar.setFixedWidth(min(1280 - 40, 560))
    bar.move((1280 - bar.width()) // 2, 16)
    w._position_planetarium_overlay()
    assert strip.y() == 12, f"回到宽屏必须回上 12，实际 y={strip.y()}"
    assert not strip_rect().intersects(bar_rect())


def test_send_controls_clickable_at_every_width(window, qtapp):
    """任何窗口宽度下发送路径都可点：命令栏输入框/麦克风不被浮层遮挡。

    命令栏本身没有独立「发送」按钮——回车即发送；可点的发送控件是
    ``cmd_input``（回车）与 ``cmd_mic_btn``。AI 面板另有 ``send_btn``，
    这里同时钉住它的 enabled（页签可见性由 Dock 布局另测）。
    """
    w, mw = window
    w.set_app_mode("planetarium")
    qtapp.processEvents()
    sky = w.sky_view
    strip = w._planetarium_strip
    bar = w._ai_cmd_bar

    def strip_rect():
        return QRect(strip.x(), strip.y(), strip.width(), strip.height())

    for width in (640, 720, 800, 888, 960, 1000, 1120, 1280, 1440, 1600):
        sky.resize(width, sky.height())
        bar.setFixedWidth(min(width - 40, 560))
        bar.move((width - bar.width()) // 2, 16)
        w._position_planetarium_overlay()
        qtapp.processEvents()

        assert w.cmd_input.isVisible() and w.cmd_input.isEnabled(), (
            f"宽 {width}：命令栏输入框（回车发送）必须可点")
        assert w.cmd_mic_btn.isVisible() and w.cmd_mic_btn.isEnabled(), (
            f"宽 {width}：命令栏麦克风按钮必须可点")
        # 浮层不得盖住输入框中心/右端（否则点击会被浮层截走）。
        for pt in (w.cmd_input.rect().center(),
                   w.cmd_input.rect().topRight()):
            sky_pt = sky.mapFromGlobal(w.cmd_input.mapToGlobal(pt))
            assert strip_rect().contains(sky_pt) is False, (
                f"宽 {width}：浮层压住了输入框可点区域 {pt.x()},{pt.y()}")
        btn = getattr(w.ai_panel, "send_btn", None)
        assert btn is not None and btn.isEnabled(), (
            f"宽 {width}：AI 助手发送按钮必须可用")
