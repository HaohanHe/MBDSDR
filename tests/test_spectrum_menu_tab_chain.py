# SPDX-License-Identifier: MIT
"""
频谱工具条：菜单里的 ``QWidgetAction`` 开关不在 Tab 链（口径测试）
==================================================================

背景：频谱「更多 ⋯」把 7 个显示开关（瀑布平滑 / 峰值 / Max保持 / Min保持 /
书签 / 频段 / 自动量程）收进了 ``QMenu``，每个开关装在一个 ``QWidgetAction``
的 defaultWidget 里。菜单未弹出时，菜单子树整体 ``isVisible() == False``，
Qt 的 Tab 遍历会跳过不可见控件 —— 所以**键盘用户在没有打开菜单时摸不到这些
开关**，只能先聚焦「更多」按钮再按空格/回车弹出菜单。

本文件只钉住**能测的那一半**：
  * 菜单关闭时，7 个开关 ``isVisible() == False``，且不在面板的焦点链里；
  * 它们确实仍挂在 ``_more_menu`` 下（结构前提，若哪天改回工具条本测试失效）；
  * 对照组：「更多」「显示」两个按钮在焦点链里，证明遍历本身没走空。

**不测的那一半**（见文件末尾 skip 用例）：菜单弹出后这些开关到底能不能用
Tab / 方向键走到，offscreen 平台测不了（菜单弹层不是普通窗口，没有真实窗口
激活与键盘 grab），真机上也**未实测**。写了 skip 而不是假装断言通过。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_NO_HW", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import spectrum_widget as sw  # noqa: E402

#: 收在「更多」菜单里的显示开关（与 spectrum_widget._build_toolbar 一致）。
_MENU_SWITCHES = (
    "_wf_smooth_chk",    # 瀑布平滑
    "_peak_chk",         # 峰值
    "_maxhold_chk",      # Max保持
    "_minhold_chk",      # Min保持
    "_bookmark_chk",     # 书签
    "_bandplan_chk",     # 频段
    "_auto_db_chk",      # 自动量程
)


def _tab_chain(root: QWidget):
    """按 ``nextInFocusChain`` 环序遍历 ``root`` 子树内的控件。"""
    members = set(root.findChildren(QWidget))
    chain = []
    w = root.nextInFocusChain()
    for _ in range(len(members) + 1):
        if w is None or w not in members:
            break
        chain.append(w)
        w = w.nextInFocusChain()
    return chain


@pytest.fixture()
def panel():
    p = sw.SpectrumPanel()
    p.resize(900, 650)
    p.show()
    p.activateWindow()
    app.processEvents()
    yield p
    p.close()


def test_menu_switches_are_invisible_and_out_of_tab_chain_when_menu_closed(panel):
    """菜单关闭时，7 个开关不可达：不可见 + 不在焦点链里。"""
    assert not panel._more_menu.isVisible(), "前提：菜单此刻应处于关闭状态"
    chain = set(_tab_chain(panel))
    for name in _MENU_SWITCHES:
        chk = getattr(panel, name)
        assert panel._more_menu.isAncestorOf(chk), (
            f"{name} 不在「更多」菜单里了；若已挪回工具条，本测试前提失效，需重写")
        assert not chk.isVisible(), f"{name} 在菜单关闭时竟然是可见的"
        assert chk not in chain, f"{name} 在菜单关闭时仍留在了焦点链里"


def test_toolbar_entry_buttons_are_reachable_for_contrast(panel):
    """对照组：两个入口按钮在焦点链里，证明上面的遍历没有走空。"""
    chain = set(_tab_chain(panel))
    assert panel._more_btn in chain, "「更多」按钮不在焦点链，遍历口径有问题"
    assert panel._display_btn in chain, "「显示」按钮不在焦点链，遍历口径有问题"
    for btn in (panel._more_btn, panel._display_btn):
        assert btn.isVisible() and btn.focusPolicy() & Qt.TabFocus


@pytest.mark.skip(
    reason="offscreen 下菜单弹层没有真实键盘 grab，测不了「菜单打开后能否 Tab 到"
           "开关」；真机未实测。只用可见性/焦点链钉住能测的那一半，不许假装测过。")
def test_menu_switches_reachable_by_tab_after_menu_opens(panel):
    """待真机实测：菜单弹出后，Tab / 方向键能否在开关之间移动。"""
    panel._more_menu.popup(panel._more_btn.mapToGlobal(panel._more_btn.rect().bottomLeft()))
    app.processEvents()
    assert panel._more_menu.isVisible()
