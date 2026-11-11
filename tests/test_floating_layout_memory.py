# SPDX-License-Identifier: MIT
"""自由窗口摆位跨重启记忆的契约测试。

用户 2026-11-08：「像真实桌面，电脑可以放左边也可以挪右边」「用户想看啥
就让他看啥，别老给他决定」。这里钉的是：摆过一次，下次打开还是那样；
关窗不丢；主动归位才清；已有几何不被启动时级联摆放覆盖。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _ai_tab_index(w):
    for i in range(w.right_tab.count()):
        if "AI" in w.right_tab.tabText(i):
            return i
    pytest.skip("AI 助手页签不在右栏")


def test_popout_layout_survives_restart_and_clears_on_explicit_return(
        isolated_profile, qapp, tmp_path):
    from PySide6.QtCore import QSettings
    from main_window import MainWindow

    w = MainWindow()
    s = QSettings("MBDSDR", "Desktop")
    try:
        s.remove(w._FLOAT_OPEN_KEY)
        idx = _ai_tab_index(w)
        key = w._float_key("right", w.right_tab, idx)
        right_count = w.right_tab.count()

        # 1) 弹出 → 记住「哪些面板是弹出的」
        w._pop_out_tab("right", idx)
        assert key in w._float_windows
        assert key in [str(k) for k in
                       (s.value(w._FLOAT_OPEN_KEY, []) or [])]
        assert w.right_tab.count() == right_count, "占位页签必须保住页签数"

        # 2) 用户把面板拖到某处，几何由 FloatingPanel 自己落盘
        panel = w._float_windows[key]["panel"]
        panel.move(140, 180)
        panel.save_geometry()
        saved_pos = QSettings("MBDSDR", "Desktop").value(
            "floating_panel/%s/pos" % key)
        assert saved_pos is not None

        # 3) 关窗：收起浮层但不清列表
        w._return_all_panes(persist=False)
        assert key not in w._float_windows
        assert key in [str(k) for k in
                       (s.value(w._FLOAT_OPEN_KEY, []) or [])]

        # 4) 下次启动：按稳定 key 还原，且不覆盖用户存的位置
        w._restore_float_panels()
        assert key in w._float_windows, "上次弹出的面板应自动回到桌面"
        assert QSettings("MBDSDR", "Desktop").value(
            "floating_panel/%s/pos" % key) == saved_pos

        # 5) 用户主动归位 → 这次才从列表里清掉
        w._return_panel(key)
        assert key not in [str(k) for k in
                           (s.value(w._FLOAT_OPEN_KEY, []) or [])]
        assert w.right_tab.count() == right_count
    finally:
        try:
            w._return_all_panes()
        except Exception:
            pass
        w.close()


def test_capability_panel_never_restores_detached(isolated_profile, qapp):
    """上次把能力矩阵 ⤢ 弹出并折叠，重启不许再还原成桌面上的孤儿黑条。

    用户 2026-11-10：「这小框咋的？干哈的？单独拿出来卖单的啊」——弹出态的
    顶层窗口没有主窗口当宿主，主窗口最小化后它仍留在桌面上。
    """
    from PySide6.QtCore import QSettings
    from main_window import MainWindow

    s = QSettings("MBDSDR", "Desktop")
    s.setValue("floating_panel/capability_matrix/detached", True)
    s.setValue("floating_panel/capability_matrix/collapsed", True)
    s.sync()

    w = MainWindow()
    try:
        panel = w._cap_panel
        assert panel is not None, "能力矩阵必须真实创建"
        assert panel._detached is False, "启动不得恢复成独立窗口"
        assert panel.isWindow() is False, "收回后必须是主窗口内的子控件"
        assert w.isAncestorOf(panel) is True, "回收后必须挂在主窗口控件树里"
        assert str(s.value("floating_panel/capability_matrix/detached")
                   ).lower() in ("false", "0", "none"), "落盘的弹出态必须被改写"
    finally:
        w.close()


def test_capability_drawer_reopens_from_panel_menu(isolated_profile, qapp):
    """能力矩阵默认不占工作面；从「面板」菜单能开、⤶ 能收、还能再开。"""
    from main_window import MainWindow

    w = MainWindow()
    w.show()
    qapp.processEvents()
    try:
        act = w._cap_action
        assert act.isCheckable() is True
        # 默认收起不摆出来：它若默认可见就会浮在频谱左上角盖住工具栏
        assert w._cap_panel.isVisible() is False
        assert act.isChecked() is False

        act.trigger()
        qapp.processEvents()
        assert act.isChecked() is True
        assert w._cap_panel.isVisible() is True, "菜单必须能打开能力矩阵"

        w._hide_capability_drawer()
        assert w._cap_panel.isVisible() is False
        assert act.isChecked() is False, "收起后菜单勾选必须同步取消"
    finally:
        w.close()
