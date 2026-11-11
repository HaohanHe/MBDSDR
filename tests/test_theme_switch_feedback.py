# SPDX-License-Identifier: MIT
"""W25 / P0-16：切主题必须有反馈，且结束后状态栏回到切换前的文案。

为什么单独测这三件事：切主题的 2.6 s 花在 Qt 对 ~2450 个控件逐个重抛光上
（`app.setStyleSheet` 即使传同一份字符串也要重抛光，实测 3.8 s；palette 0.3 ms、
font 0.9 ms 都已无油水），这段时间无法缩短，只能给反馈。实测改前：切换期间
50 ms 定时器触发 0 次、状态栏无任何变化 —— 用户看到的就是「点了没反应」，
然后重复点击。

本用例只断言行为，不碰耗时：
1. 应用主题那一刻，状态栏是「正在应用主题…」且光标是忙碌态；
2. 切换结束后恢复切换前的原文案；
3. 切换前状态栏本来是空的，结束后必须仍是空的（不留永久提示）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

_BUSY_TEXT = "正在应用主题…"


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def window(isolated_profile, qtapp):
    import main_window as mw
    w = mw.MainWindow()
    yield w
    try:
        w.close()
    except Exception:  # noqa: BLE001 - 收尾失败不影响断言结论
        pass


@pytest.fixture()
def other_theme(window):
    """挑一个与当前不同的主题，并在用例结束后切回去。"""
    names = ("dark", "default", "high_contrast", "dark_car")
    cur = getattr(window, "_current_theme", names[0])
    target = next(n for n in names if n != cur)
    yield target
    try:
        window._apply_theme(cur)
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture()
def seen_apply(window, monkeypatch, qtapp):
    """在真正套主题的那一刻取现场：状态栏文案 + 覆盖光标形状。

    只观察、不替换：底层仍调原 apply，避免把被测行为换成假实现。
    """
    import themes
    from PySide6.QtWidgets import QApplication

    seen = {}
    orig = themes.Theme.apply

    def _probe(self, app):
        msg = window.statusBar().currentMessage()
        cur = QApplication.overrideCursor()
        seen["msg"] = msg
        seen["cursor"] = cur.shape() if cur is not None else None
        return orig(self, app)

    monkeypatch.setattr(themes.Theme, "apply", _probe)
    return seen


def _set_status(window, text):
    if text:
        window.statusBar().showMessage(text)
    else:
        window.statusBar().clearMessage()


def test_switch_shows_busy_feedback_at_apply_time(window, other_theme,
                                                  seen_apply):
    from PySide6.QtCore import Qt
    _set_status(window, "上一动作的提示")
    window._apply_theme(other_theme)
    assert seen_apply.get("msg") == _BUSY_TEXT, (
        "套主题时状态栏没有给出反馈，实际：" + repr(seen_apply.get("msg")))
    assert seen_apply.get("cursor") == Qt.WaitCursor, (
        "套主题时光标不是忙碌态，实际：" + repr(seen_apply.get("cursor")))


def test_switch_restores_previous_status_text(window, other_theme, seen_apply):
    from PySide6.QtWidgets import QApplication
    prev = "采样率 2.4 MS/s（切换前应有）"
    _set_status(window, prev)
    window._apply_theme(other_theme)
    assert seen_apply.get("msg") == _BUSY_TEXT, (
        "套主题时状态栏没有给出反馈，实际：" + repr(seen_apply.get("msg")))
    assert window.statusBar().currentMessage() == prev, (
        "切换结束后没有恢复原文案，实际："
        + repr(window.statusBar().currentMessage()))
    assert QApplication.overrideCursor() is None, "切换结束后忙等待光标没有撤销"


def test_switch_clears_status_when_it_was_empty(window, other_theme,
                                                seen_apply):
    _set_status(window, "")
    assert window.statusBar().currentMessage() == ""
    window._apply_theme(other_theme)
    assert seen_apply.get("msg") == _BUSY_TEXT, (
        "套主题时状态栏没有给出反馈，实际：" + repr(seen_apply.get("msg")))
    assert window.statusBar().currentMessage() == "", (
        "切换前状态栏是空的，结束后却留下了提示："
        + repr(window.statusBar().currentMessage()))
