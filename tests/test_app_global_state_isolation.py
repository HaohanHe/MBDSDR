# SPDX-License-Identifier: MIT
"""
应用级全局态的跨用例隔离（护栏钉扎）
====================================

像素断言是果，状态才是因：``134 px`` 落在 ``110..135`` 容差里能蒙过去，
状态断言蒙不过去。本文件用相邻用例直接钉两件事：

1. ``QApplication`` 的字体（家族+磅值）与样式表（长度+首尾片段）在用例
   结束后回到会话基线——前一个用例走真实 ``_apply_theme`` 路径污染，
   后一个用例读状态；
2. ``touch_manager()._mode_override`` 与 ``layout_store()`` 的
   ``_default_store`` 单例在用例结束后回到基线。

全程不请求 ``isolated_profile`` 等隔离 fixture，验证的是 conftest 的
autouse 兜底本身（而不是某个用例自己写的 try/finally）。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_app_global_state_isolation.py -q
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

import layout_store as _ls  # noqa: E402
from touch_manager import touch_manager as _touch_manager  # noqa: E402

# 全程基线：在收集阶段（任何 fixture 之前）取，等价于会话基线。
# app 必须在 import 期就存在，否则会话基线会拖到第一个用例之后才拍，
# 拍到的可能已经是被污染的现场。
_APP = QApplication.instance() or QApplication(sys.argv)
_BASELINE = {
    "touch_override": _touch_manager()._mode_override,
    "layout_store": _ls.layout_store(),
}


@pytest.fixture(scope="module")
def qapp():
    yield _APP


def test_1_pollute_app_style(qapp):
    """走真实路径污染：MainWindow._apply_theme 就是这么改 app 的。"""
    import themes

    themes.get_theme(themes.DEFAULT_THEME).apply(qapp)
    assert qapp.styleSheet(), "主题应用后 QSS 应为非空，否则本用例没污染到"


def test_2_app_style_restored_before_next_case(app_style_baseline):
    """上一用例结束后的状态必须等于会话基线（字体家族+磅值、QSS 长度+首尾）。"""
    app = QApplication.instance()
    font = app.font()
    assert (font.family(), font.pointSizeF()) == (
        app_style_baseline["font_family"],
        app_style_baseline["font_point_size"],
    ), "上一用例的 app 字体泄漏出来了"

    qss = app.styleSheet()
    assert len(qss) == app_style_baseline["qss_len"], "上一用例的 QSS 长度泄漏出来了"
    assert qss[:64] == app_style_baseline["qss_head"], "QSS 首段与基线不一致"
    assert qss[-64:] == app_style_baseline["qss_tail"], "QSS 尾段与基线不一致"


def test_3_pollute_app_singletons(qapp):
    """不请求任何隔离 fixture，直接改两个进程级单例。"""
    _touch_manager().set_override(True)
    _ls._default_store = _ls.LayoutStore(  # noqa: SLF001
        os.path.join(tempfile.gettempdir(), "mbdsdr_guard_probe_layouts.json"))

    assert _touch_manager()._mode_override is True
    assert _ls.layout_store() is not _BASELINE["layout_store"]


def test_4_singletons_restored_before_next_case(qapp):
    """下一个用例读到的必须是基线：触屏模式与布局库单例双双复原。"""
    assert _touch_manager()._mode_override == _BASELINE["touch_override"], (
        "上一用例的触屏模式泄漏出来了")
    assert _ls.layout_store() is _BASELINE["layout_store"], (
        "上一用例替换的 layout_store 单例泄漏出来了")
