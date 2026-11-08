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
