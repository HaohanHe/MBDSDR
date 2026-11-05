# SPDX-License-Identifier: MIT
"""
A 键归属反证：焦点在哪，局部优先
================================

规则（见 desktop/shortcuts.py 顶部注释）：
  * 频谱控件自身获焦 → ``A`` 归频谱的 AM 解调，主窗「全天模式」不得抢键；
  * 频谱没有焦点 → ``A`` 归主窗，真的进入全天模式。

为什么这里用 ``QShortcut.activated.emit()`` 而不是 QTest.keyClick：
offscreen 平台下 QTest / ``QApplication.sendEvent`` 投递的按键不会进入
Qt 的快捷键映射（只有窗口系统自发的按键才会），实测两者都不触发
QShortcut/QAction。因此行为断言直接驱动生产接缝：
  * 频谱侧：``plot._local_mode_shortcuts`` 里登记的那条 QShortcut；
  * 主窗侧：``_all_sky_action.trigger()``（等价于快捷键命中后 Qt 发 triggered）。
解调模式用独立频谱面板断言，避免把模式写回真实用户配置影响其它测试用例。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402


def test_main_window_imports_without_shim(qapp):
    """HEAD 自洽：main_window 依赖的 spectrum_widget 符号必须真实存在。

    不做 hasattr 垫片、不 try/except 兜底——直接 import 并断言关键符号，
    防止两侧接口分先后提交时把 HEAD 变成 ImportError。
    """
    import importlib
    mw = importlib.import_module("desktop.main_window")
    sw = importlib.import_module("desktop.spectrum_widget")
    assert hasattr(sw, "owns_local_shortcuts"), (
        "spectrum_widget 缺少 owns_local_shortcuts，main_window 会 ImportError")
    assert callable(mw.owns_local_shortcuts)
    assert mw.owns_local_shortcuts.__module__.endswith("spectrum_widget"), (
        "main_window 的 owns_local_shortcuts 不是来自 spectrum_widget")
    assert callable(sw.create_spectrum_widget)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def spectrum_plot(qapp):
    from spectrum_widget import create_spectrum_widget
    panel = create_spectrum_widget(prefer_opengl=False)
    panel.resize(900, 500)
    yield panel, panel._plot
    panel.deleteLater()


@pytest.fixture(scope="module")
def main_window(qapp):
    from main_window import MainWindow
    w = MainWindow()
    w.show()
    w.activateWindow()
    qapp.processEvents()
    yield w
    try:
        w._disconnect()
    except Exception:
        pass
    w.close()


def _focus_spectrum_plot(main_window, qapp):
    """把频谱页带到前台并让谱面控件真正拿到焦点。"""
    plot = main_window.spectrum._plot
    idx = main_window.left_tab.indexOf(plot.parentWidget())
    if idx >= 0:
        main_window.left_tab.setCurrentIndex(idx)
    main_window.activateWindow()
    plot.setFocus()
    qapp.processEvents()
    from main_window import _focus_widget
    assert _focus_widget() is plot, "测试前置失败：频谱控件没拿到焦点"
    return plot


def test_global_a_does_not_hijack_spectrum_am(spectrum_plot, qapp):
    """频谱获焦 → 按 A → 解调模式变 AM（且不是全天模式）。"""
    panel, plot = spectrum_plot
    panel.show()
    plot.setFocus()
    qapp.processEvents()

    a_shortcuts = [sc for sc in plot._local_mode_shortcuts
                   if sc.key().toString() == "A"]
    assert len(a_shortcuts) == 1, (
        "频谱侧没有登记 A 的局部 QShortcut（焦点内局部键必须由 Qt 派发）")
    assert a_shortcuts[0].context().name == "WidgetShortcut", (
        "频谱局部键必须是 WidgetShortcut 上下文，否则抢不过主窗")

    got = []
    panel.mode_hotkey_requested.connect(got.append)
    a_shortcuts[0].activated.emit()
    assert got == ["AM"], f"A 没切到 AM，实际 {got}"
    panel.close()


def test_all_sky_ignores_a_while_spectrum_has_focus(main_window, qapp):
    """兜底：频谱获焦时，即使全局 A 动作被触发也不得进入全天模式。"""
    plot = _focus_spectrum_plot(main_window, qapp)
    main_window.sky_view.set_all_sky_mode(False)
    assert plot is not None

    main_window._all_sky_action.trigger()
    qapp.processEvents()
    assert main_window.sky_view.all_sky_active is False, (
        "全局 A 动作在频谱获焦时不该进入全天模式")


def test_a_still_enters_all_sky_without_spectrum_focus(main_window, qapp):
    """频谱无焦点 → 按 A → 真的进入全天模式（保护不倒退）。"""
    main_window.sky_view.set_all_sky_mode(False)
    # 焦点放到非频谱控件（左栏页签本身），确保不属于频谱局部键作用域。
    main_window.left_tab.setFocus()
    qapp.processEvents()

    main_window._all_sky_action.trigger()
    qapp.processEvents()
    assert main_window.sky_view.all_sky_active is True, (
        "没有频谱焦点时 A 没能进入全天模式（行为倒退）")

    main_window.sky_view.set_all_sky_mode(False)
