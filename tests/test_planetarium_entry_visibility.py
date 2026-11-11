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
from PySide6.QtWidgets import QLabel, QPushButton, QWidget  # noqa: E402


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def window(isolated_profile, qtapp):
    # 本模块 set_app_mode 会持久化 app_mode；不隔离就会重写用户真实
    # %APPDATA%/mbdsdr/desktop_settings.json（会话级守卫实测抓到 mtime 变化）。
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
    # 关窗必须发生在 isolated_profile 撤销之前：把窗口活到解释器退出，
    # closeEvent 会在真实 HOME 下重写用户 gui_config.json（会话守卫实测）。
    w.close()


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


def test_ai_bar_placed_in_sky_slot_not_collapsed(window, qtapp):
    """进星象仪命令栏必须真的被摆到天空顶部居中（不是 (0,0) 的残片）。

    回归 2026-11-11 实拍缺陷：命令栏在 IDE 里不入布局、只隐藏；reparent
    到天空槽后没有布局替它算几何，停在约 100px 宽，输入框被压成「…」，
    星象仪里外行唯一的 AI 入口等于不存在。上面的浮层测试都先手工
    ``bar.move()``，因此这条必须单独钉住「谁负责摆」。
    """
    w, mw = window
    w.set_app_mode("planetarium")
    qtapp.processEvents()
    w._planetarium_page.layout().activate()
    bar = w._ai_cmd_bar
    slot = w._planetarium_sky_slot
    # 前面的用例用过 setFixedWidth，先解除，验证真实布局路径。
    bar.setMinimumWidth(0)
    bar.setMaximumWidth(16777215)
    w._position_planetarium_overlay()
    qtapp.processEvents()

    assert bar.parentWidget() is slot, "命令栏必须挂在天空槽上"
    assert bar.isVisible() is True, "星象仪里命令栏必须可见"
    assert bar.width() >= 280, f"命令栏不得缩成残片，实际宽 {bar.width()}"
    assert bar.height() >= 34, f"命令栏高度不足以露出输入行：{bar.height()}"
    assert bar.x() == (slot.width() - bar.width()) // 2, (
        f"必须水平居中：bar.x={bar.x()} slot={slot.width()} w={bar.width()}")
    assert bar.y() == 12, f"必须贴上 12px，实际 y={bar.y()}"
    assert w.cmd_input.width() >= 120, (
        f"输入框必须能看清问题，实际宽 {w.cmd_input.width()}")
    assert w.cmd_mic_btn.isVisible() and w.cmd_mic_btn.isEnabled()


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


def test_planetarium_one_click_fullscreen(window):
    """一键全屏：星象仪里必须有一颗 ⛶，点一下真全屏、文案随状态走。

    用户原话（2026-11-07）："我希望这个星象仪模式我能点一个一键全屏"。
    只留菜单项 / F11 不算——那一层是给熟手的，产品面必须有一眼看得见的入口
    （demo 右上角那颗 ⛶ 就是这个位置）。
    """
    w, mw = window
    w.set_app_mode("planetarium")
    strip = w._planetarium_strip
    btn = strip.findChild(QPushButton, "planetariumFullscreen")
    assert btn is not None, "星象仪必须有一键全屏按钮"
    assert btn.isVisible(), "按钮必须在浮层里可见"
    assert btn.sizeHint().width() <= strip.width() - 8, (
        f"窄条里文字要放得下，sizeHint={btn.sizeHint().width()}")
    assert btn.toolTip(), "按钮必须有 tooltip 说明"

    # 进形态会自动全屏（独立天空窗真全屏，主窗口留在 IDE）→ 文案必须同步
    assert w._planetarium_window.isFullScreen() is True, "进星象仪应自动全屏"
    assert w.isFullScreen() is False, "主窗口不该跟着全屏（要能放另一块屏）"
    assert "退出全屏" in btn.text(), f"自动全屏后文案未同步: {btn.text()}"

    btn.click()                      # 一键回窗口
    assert w._planetarium_window.isFullScreen() is False, "点一下应回到窗口模式"
    assert "退出" not in btn.text() and "全屏" in btn.text(), \
        f"回窗口后文案应为「全屏」，实际: {btn.text()}"

    btn.click()                      # 再一键回全屏
    assert w._planetarium_window.isFullScreen() is True, "再点一下应回到全屏"
    assert "退出全屏" in btn.text()

    w.set_app_mode("ide")


def test_ai_card_and_search_never_share_the_bottom_band(window, qtapp):
    """AI 回复卡与「找星」搜索框都在底部居中，必须互斥。

    实拍缺陷：搜索输入框横在回复卡左缘上，两段文字互相穿透。两者都是
    临时浮层，最后发起的那个占位，另一个收起（对话全文仍在 AI 面板）。
    """
    w, mw = window
    w.set_app_mode("planetarium")
    qtapp.processEvents()

    w._open_planetarium_search()
    qtapp.processEvents()
    assert w.sky_view.search_visible() is True, "「找星」必须能唤出搜索框"

    w._on_planetarium_ai_reply("木星现在在东南方，高度约 32°。")
    qtapp.processEvents()
    assert w.sky_view.search_visible() is False, (
        "AI 回复卡出现后搜索框必须收起，否则两者在底部同一条带重叠")
    assert w._planetarium_ai_card.isVisible() is True

    w._open_planetarium_search()
    qtapp.processEvents()
    assert w._planetarium_ai_card.isVisible() is False, (
        "重新打开搜索时，上一句 AI 回复卡必须让位")
    assert w.sky_view.search_visible() is True
    w.sky_view.hide_search()


def test_ai_card_avoids_readout_card_on_narrow_window(window, qtapp):
    """窄窗里 AI 回复卡不许压住左下角读数卡（时间/位置/星等）。

    实拍缺陷：1000×600 下两卡都是底部一带，回复卡把「位置 35°N 110°E／
    星空 606 颗」盖掉半行。几何要按当前尺寸现算（读上一次 paint 的矩形
    会拿到旧尺寸，实测从 1600 缩到 1000 时避让失效）。
    """
    w, mw = window
    w.set_app_mode("planetarium")
    win = w._planetarium_window
    win.resize(1000, 600)
    qtapp.processEvents()
    w._on_planetarium_ai_reply("现在头顶最亮的是织女星（Vega，0.03 等）。")
    qtapp.processEvents()

    card = w._planetarium_ai_card
    slot = w._planetarium_sky_slot
    readout = w.sky_view.immersive_card_rect()
    assert not readout.isNull(), "沉浸态读数卡必须有可查询的几何"
    readout_rect = QRect(
        w.sky_view.mapTo(slot, readout.topLeft().toPoint()),
        readout.size().toSize())
    assert card.isVisible() is True
    assert card.geometry().intersects(readout_rect) is False, (
        f"AI 卡片 {card.geometry().getRect()} 压住了读数卡 "
        f"{readout_rect.getRect()}，底部两卡必须错开")
    assert card.geometry().intersects(w._planetarium_strip.geometry()) is False, (
        "AI 卡片压住了右侧按钮条")
    card.setVisible(False)
    # 复位：本模块 fixture 是 module 级，留在星象仪形态会让后面的
    # "退出星象仪"用例先撞上 set_app_mode 空转、窗口又不是全屏。
    w.set_app_mode("ide")
    win.resize(1280, 800)
    qtapp.processEvents()


def test_exit_planetarium_restores_windowed(window):
    """退出星象仪必须收掉天空窗（不留"全屏但没浮层"的困住状态）。"""
    w, mw = window
    w.set_app_mode("planetarium")
    win = w._planetarium_window
    assert win.isFullScreen() is True
    w.set_app_mode("ide")
    assert win.isFullScreen() is False, "退出后天空窗仍全屏，用户没有退路了"
    assert win.isVisible() is False, "退出后天空窗还在屏上"
    assert w.isVisible() is True and w.isFullScreen() is False, \
        "退出后主窗口必须回到窗口模式且可见"


def test_night_vision_signal_is_edge_triggered(window):
    """夜视两条入口（图层按钮 / set_night_vision）必须发同一条边沿信号。

    主窗口靠这条信号去压红浮层；值没变还发就等于每次重复挂特效。
    """
    w, mw = window
    seen = []
    w.sky_view.night_vision_changed.connect(seen.append)
    try:
        assert w.sky_view.set_layer("night", True) is True
        assert seen == [True], f"开夜视必须发一次 True，实际 {seen}"
        w.sky_view.set_layer("night", True)
        assert seen == [True], "重复开夜视不得重复发信号"
        w.sky_view.set_night_vision(False)
        assert seen == [True, False], f"关夜视必须发 False，实际 {seen}"
        assert w.sky_view.layer_state()["night"] is False
    finally:
        w.sky_view.night_vision_changed.disconnect(seen.append)
        w.sky_view.set_night_vision(False)


def test_night_vision_veil_covers_every_overlay(window, qtapp):
    """夜视必须压红全部浮层，而不只是天空自身（户外保暗适应）。

    实测缺陷：浮层是天空视图的子控件，Qt 在父 ``paintEvent`` 之后才画它们，
    天空末尾那道红光盖不到——夜里一点「图层」，白底按钮就在瞳孔上炸开。

    这里直接量渲染结果，而不是只看"有没有挂特效"：纱罩必须把按钮条的
    深色玻璃压成暗红（R 低于 120，不是被着色成 #EC5955 的亮红板子），
    关掉后必须回到原本的冷灰底（R < B）。
    """
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication
    w, mw = window
    w.set_app_mode("planetarium")
    qtapp.processEvents()
    w._planetarium_page.layout().activate()
    w._planetarium_layer_btn.click()   # 走真实开合路径：它会 raise_() 面板
    w._on_planetarium_ai_reply("夜视布局检查：这句话只用来撑出回复卡。")
    qtapp.processEvents()
    pop = w._layer_popover

    page = w._planetarium_page
    strip = w._planetarium_strip
    veil = w._night_veil
    probe = strip.rect().topLeft() + QPoint(10, 10)
    probe_page = strip.mapTo(page, probe)

    def strip_pixel():
        return page.grab().toImage().pixelColor(probe_page)

    day = strip_pixel()

    w._layer_btns["night"].setChecked(True)
    w._on_layer_btn("night")
    qtapp.processEvents()
    assert w.sky_view.layer_state()["night"] is True

    assert veil.isVisible() is True, "夜视下整页纱罩必须显示"
    assert veil.geometry().contains(page.rect()), (
        f"纱罩必须铺满整页，实际 {veil.geometry().getRect()} vs "
        f"{page.rect().getRect()}")
    # 覆盖面积：按钮条、命令栏、回复卡、瀑布槽都必须落在纱罩里。
    veil_global = veil.mapToGlobal(QPoint(0, 0))
    veil_rect = QRect(veil_global, veil.size())
    for name in ("_planetarium_strip", "_ai_cmd_bar",
                 "_planetarium_ai_card", "_planetarium_waterfall_slot"):
        wd = getattr(w, name, None)
        if wd is None or not wd.isVisible():
            continue
        r = QRect(wd.mapToGlobal(QPoint(0, 0)), wd.size())
        assert veil_rect.contains(r), f"{name} 不在夜视纱罩覆盖范围内"

    night = strip_pixel()
    assert night.red() > night.green() + 10 and night.red() > night.blue() + 10, (
        f"夜视下按钮条必须偏红，实际 {night.getRgb()}（白天 {day.getRgb()}）")
    assert night.red() <= 120, (
        f"夜视必须压暗而不是把面板冲成亮红板子：{night.getRgb()}")
    assert night.red() > day.red(), "纱罩应给深色面板补一点红底，不该更黑"

    # z 序：回复卡 / 图层面板都是天空视图的子控件，天生在页面级纱罩之下，
    # 因此下面两条像素断言在任何实现下都该成立——它们守的是"别把浮层
    # reparent 到页面级"这个未来改动，不是当前缺陷的反证。
    img = page.grab().toImage()
    card = w._planetarium_ai_card
    card_px = img.pixelColor(card.mapTo(page, QPoint(10, 10)))
    assert card_px.red() > card_px.blue() + 8, (
        f"AI 回复卡必须在纱罩之下被压红，实际 {card_px.getRgb()}")
    pop_px = img.pixelColor(pop.mapTo(page, QPoint(10, 10)))
    assert pop_px.red() > pop_px.blue() + 8, (
        f"图层面板必须在纱罩之下被压红，实际 {pop_px.getRgb()}")

    # 不变量：纱罩是页面里最后一个可见控件子对象（Qt 同级按 children 顺序
    # 堆叠）。浮层全部挂在天空视图内部，天生在纱罩之下；哪天有人把回复卡或
    # 图层面板 reparent 到页面级再 raise_，这条立刻红，别只靠肉眼截图。
    page_kids = [c for c in page.children()
                 if isinstance(c, QWidget) and c.isVisible()]
    assert page_kids and page_kids[-1] is veil, (
        "夜视纱罩必须是页面里最上层的可见控件："
        f"实际最上层是 {page_kids[-1]!r}")

    # 点击穿透：纱罩不能把按钮吃掉（WA_TransparentForMouseEvents）。
    btn = strip.findChild(QPushButton, "planetariumExit")
    hit = QApplication.widgetAt(btn.mapToGlobal(btn.rect().center()))
    assert hit is btn or (hit is not None and btn.isAncestorOf(hit)), (
        f"夜视纱罩挡住了按钮点击，命中的是 {hit!r}")

    # 关夜视必须摘干净：留着纱罩会让白天界面永远偏红。
    w._layer_btns["night"].setChecked(False)
    w._on_layer_btn("night")
    qtapp.processEvents()
    assert veil.isVisible() is False, "关闭夜视后纱罩必须收起"
    back = strip_pixel()
    assert back.red() <= back.blue(), (
        f"关闭夜视后按钮条应回到冷灰底，实际 {back.getRgb()}")
    w._planetarium_ai_card.setVisible(False)
    w._planetarium_layer_btn.setChecked(False)


def test_planetarium_spectrum_strip_is_compact_and_fits(window, qtapp):
    """星象仪底部 180px 频谱条：分区不得互相覆盖，提示必须可执行。

    实拍缺陷（2026-11-11）：面板最小高 347px（三根 80px 竖滑杆 + 标签），
    塞进 180px 槽后 QSplitter 按各自治最小高溢出——谱面 120px 与瀑布
    60px 重叠，频率轴被压进瀑布，「最小」标签整条出了面板；同时空态
    第二行写「点击工具栏「连接」选择设备」，而全屏独立窗没有工具栏。
    """
    w, mw = window
    w.set_app_mode("planetarium")
    qtapp.processEvents()
    w._planetarium_wf_btn.click()
    qtapp.processEvents()
    w._planetarium_page.layout().activate()

    sp = w.spectrum
    slot = w._planetarium_waterfall_slot
    assert slot.isVisible() is True
    assert slot.height() <= 180, f"底部条高度档位不能悄悄变大：{slot.height()}"
    assert sp.minimumSizeHint().height() <= slot.height(), (
        f"频谱面板最小高 {sp.minimumSizeHint().height()} 塞不进 "
        f"{slot.height()}px 槽，布局必然溢出")

    fft, wf = sp._fft_plot, sp._wf_plot
    assert fft.height() >= 70, f"谱面被压得太矮：{fft.height()}"
    assert wf.height() >= 36, f"瀑布被压得太矮：{wf.height()}"
    assert fft.geometry().bottom() < wf.geometry().top(), (
        f"谱面与瀑布必须首尾相接不重叠：fft={fft.geometry().getRect()} "
        f"wf={wf.geometry().getRect()}")

    # 三根竖滑杆的最小高 314px 在 180px 里无解 → 紧凑形态整体收起。
    assert sp._db_slider_col.isHidden() is True, "紧凑形态必须收起竖滑杆列"
    for sl in (sp._span_slider, sp._db_max_slider, sp._db_min_slider):
        assert sl.isVisible() is False

    hint = sp._state.offline_hint
    assert "Esc" in hint, f"全屏窗里必须给出可执行的出口，实际：{hint}"
    assert "工具栏" not in hint, (
        f"星象仪没有工具栏，不得让人去找不存在的按钮，实际：{hint}")

    # 收起瀑布条 → 频谱回 IDE 页，必须恢复常规形态与默认提示。
    w._planetarium_wf_btn.click()
    qtapp.processEvents()
    assert sp._db_slider_col.isHidden() is False, "回 IDE 必须恢复竖滑杆列"
    assert sp._fft_plot.minimumHeight() == 120
    assert "工具栏" in sp._state.offline_hint, "回 IDE 提示必须指回工具栏「连接」"

    # 模块级 fixture 共享窗口，收尾复位，别把形态留给后面的用例。
    w.set_app_mode("ide")
    w.resize(1280, 800)
    qtapp.processEvents()


def test_layer_popover_never_covers_ai_bar(window, qtapp):
    """图层面板不得盖住命令栏：麦克风/回车是星象仪里唯一的 AI 入口。

    实拍缺陷（2026-11-11，1000x600）：面板左缘 651、命令栏右缘 663，
    正好压住麦克风；面板定在 ``strip.y()``，而命令栏也贴上 12。
    """
    w, mw = window
    w.set_app_mode("planetarium")
    qtapp.processEvents()
    sky = w.sky_view
    pop = w._layer_popover
    for width in (800, 1000, 1280, 1440):
        sky.resize(width, sky.height())
        w._position_planetarium_overlay()
        w._planetarium_layer_btn.click()
        qtapp.processEvents()
        assert pop.isVisible() is True, f"宽 {width}：面板必须打开"
        tl = sky.mapFromGlobal(w._ai_cmd_bar.mapToGlobal(QPoint(0, 0)))
        bar_rect = QRect(tl, w._ai_cmd_bar.size())
        pop_rect = QRect(pop.x(), pop.y(), pop.width(), pop.height())
        assert not pop_rect.intersects(bar_rect), (
            f"宽 {width}：图层面板压住命令栏 "
            f"pop={pop_rect.getRect()} bar={bar_rect.getRect()}")
        mic = sky.mapFromGlobal(
            w.cmd_mic_btn.mapToGlobal(w.cmd_mic_btn.rect().center()))
        assert pop_rect.contains(mic) is False, (
            f"宽 {width}：图层面板盖住麦克风 {mic.x()},{mic.y()}")
        w._planetarium_layer_btn.setChecked(False)
    w.set_app_mode("ide")
    w.resize(1280, 800)
    qtapp.processEvents()


def test_planetarium_link_state_is_honest(window, qtapp):
    """全屏星象仪必须自己报 SDR 链路真相（主窗口状态栏在这里看不见）。

    背景：星空是离线星历算的，SDR 掉线后天空照转。没有这块读数，人戴在
    头上会以为还在收信号。三种状态各有唯一事实源，任何一条写错都算谎报。

    反证：把 `_sync_planetarium_link` 的未连接分支改成「SDR 已连接」，
    第一条断言立刻红；把降级分支删掉，第三条红。
    """
    from types import SimpleNamespace

    class _StubBackend:
        device = SimpleNamespace(name="FakeSDR")

        def get_status(self):
            return SimpleNamespace(connected=True)

    class _StubPipeline:
        def shutdown(self):
            pass

    w, mw = window
    w.set_app_mode("planetarium")
    qtapp.processEvents()
    label = w._planetarium_link_label
    strip = w._planetarium_strip

    # 1) 没接设备：必须说「未连接」，且给出可执行的下一步。
    assert label.isVisible() is True, "星象仪里链路读数必须可见"
    assert label.text() == "SDR 未连接", f"实际文案：{label.text()!r}"
    assert "Esc" in label.toolTip() and "连接" in label.toolTip(), (
        f"未连接时 tooltip 必须说清怎么回去连，实际：{label.toolTip()!r}")
    assert label.sizeHint().width() <= strip.width() - 14, (
        f"文案放不进 152px 浮层：need={label.sizeHint().width()} "
        f"avail={strip.width() - 14}")

    # 2) 已连接：只报事实，不把设备名塞进窄条（放 tooltip）。
    w._active_sdr_backend = _StubBackend()
    w._pipeline = _StubPipeline()
    w._sync_planetarium_link()
    assert label.text() == "SDR 已连接", f"实际文案：{label.text()!r}"
    assert "FakeSDR" in label.toolTip(), "设备名必须能在 tooltip 里查到"

    # 3) 连接仍在但 2s 无数据：降级可见，不能继续显示「已连接」。
    w._link_degraded = True
    w._sync_planetarium_link()
    assert label.text() == "数据流中断", f"实际文案：{label.text()!r}"
    assert label.sizeHint().width() <= strip.width() - 14

    # 4) 1Hz 自检只在星象仪里跑，退出必须停（别在 IDE 里空转）。
    assert w._planetarium_link_timer.isActive() is True
    w._link_degraded = False
    w._active_sdr_backend = None
    w._pipeline = None
    w._sync_planetarium_link()
    assert label.text() == "SDR 未连接"
    w.set_app_mode("ide")
    qtapp.processEvents()
    assert w._planetarium_link_timer.isActive() is False, (
        "退出星象仪后链路自检必须停表")
    w.resize(1280, 800)
    qtapp.processEvents()


def test_fov_control_never_hides_under_strip(window, qtapp):
    """窗口矮 + 底部频谱条时，右下 ⊖⊕ 必须让开浮层且点得到。

    实拍缺陷（2026-11-11）：天空只剩 420px 高时，浮层（高 413，贴右上 12）
    一路压到右下角，⊖⊕ 正好画在「退出星象仪」底下——既看不清也点不到；
    页签形态那颗常驻 ⛶ 也叠在浮层右缘（与浮层里的「⛶ 全屏」重复）。
    """
    from PySide6.QtCore import QRectF
    w, mw = window
    w.set_app_mode("planetarium")
    qtapp.processEvents()
    sky = w.sky_view
    strip = w._planetarium_strip

    sky.resize(1000, 420)          # 600px 高 - 180px 频谱条
    w._position_planetarium_overlay()
    qtapp.processEvents()
    sky.grab()                     # 触发一次绘制，拿到本次的 ⊖⊕ 矩形

    strip_rect = QRectF(strip.geometry())
    for rect, name in ((sky._fov_minus_rect, "⊖"), (sky._fov_plus_rect, "⊕")):
        assert rect is not None, f"{name} 必须画出来"
        assert strip_rect.intersects(rect) is False, (
            f"{name} 被浮层盖住：{rect} vs strip {strip_rect}")
        # 该点不得有子控件截走点击（⊖⊕ 是画在天空上的，任何子控件都会挡）。
        assert sky.childAt(rect.center().toPoint()) is None, (
            f"{name} 位置被浮层/子控件占据，点击会被截走")
    assert w._sky_fs_btn.isHidden() is True, (
        "浮层里已有「⛶ 全屏」，重复的常驻 ⛶ 必须收起（否则压住浮层右缘）")

    w.set_app_mode("ide")
    qtapp.processEvents()
    assert w._sky_fs_btn.isHidden() is False, "回 IDE 必须恢复页签形态的 ⛶"
    assert sky._hud_avoid_rect is None, "退出星象仪必须撤销让位声明"
    w.resize(1280, 800)
    qtapp.processEvents()


def test_escape_dismisses_innermost_overlay_before_leaving(window, qtapp):
    """Esc 逐层收：搜索框 → 图层面板 → AI 卡 → 退出星象仪。

    实拍缺陷（2026-11-11）：刚点开「🔍 找星」或「▦ 图层」时按 Esc，
    整座星象仪直接关掉——用户要的是收起刚打开的那一层，不是离场。
    """
    w, mw = window
    w.set_app_mode("planetarium")
    qtapp.processEvents()

    w._open_planetarium_search()
    qtapp.processEvents()
    assert w.sky_view.search_visible() is True
    w._on_escape()
    qtapp.processEvents()
    assert w.sky_view.search_visible() is False, "Esc 第一步必须收起搜索框"
    assert w._app_mode == "planetarium", "收搜索不许把星象仪一起关掉"

    w._planetarium_layer_btn.click()
    qtapp.processEvents()
    assert w._layer_popover.isVisible() is True
    w._on_escape()
    qtapp.processEvents()
    assert w._layer_popover.isHidden() is True
    assert w._planetarium_layer_btn.isChecked() is False, (
        "Esc 收图层必须把按钮也弹起，否则下次点击只是取消选中、面板点不开")
    assert w._app_mode == "planetarium"
    # 再点一次必须能重新展开（验证按钮没有停在按下态）
    w._planetarium_layer_btn.click()
    qtapp.processEvents()
    assert w._layer_popover.isVisible() is True
    w._planetarium_layer_btn.click()
    qtapp.processEvents()

    w._on_planetarium_ai_reply("布局检查：这句话只用来撑出回复卡。")
    qtapp.processEvents()
    assert w._planetarium_ai_card.isVisible() is True
    w._on_escape()
    qtapp.processEvents()
    assert w._planetarium_ai_card.isHidden() is True
    assert w._app_mode == "planetarium"

    w._on_escape()
    qtapp.processEvents()
    assert w._app_mode == "ide", "没有覆盖层时 Esc 才退出星象仪"
    w.resize(1280, 800)
    qtapp.processEvents()

