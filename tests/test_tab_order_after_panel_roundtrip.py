# SPDX-License-Identifier: MIT
"""D7：面板弹出再收回后必须重跑 tab order，且顺序与弹出前完全一致。

判据：
  * ``_pop_out_tab`` 与 ``_return_panel`` 各触发一次 ``_setup_tab_order``
    （用实例属性打桩计数，不空改）；
  * 弹出→收回后，从 ``right_nav`` 走到 ``home_btn`` 的实际焦点链
    与弹出前逐项相同（setTabOrder 真的把那段接回去了）；
  * ``_tab_order`` 与弹出前一致。

反证：删掉两处 ``QTimer.singleShot(0, self._setup_tab_order)`` 后，
调用次数断言与实际焦点链断言同时红。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qtapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def window(qtapp, isolated_profile):
    import main_window as mw
    w = mw.MainWindow()
    w._recording = False
    yield w, mw
    try:
        w._recording = False
        w._return_all_panes(persist=False)
        w._teardown_connection()
    except Exception:  # noqa: BLE001
        pass


def _walk(start, stop):
    out = [start]
    cur = start.nextInFocusChain()
    for _ in range(20000):
        if cur is None:
            break
        out.append(cur)
        if cur is stop:
            break
        cur = cur.nextInFocusChain()
    return out


def _keys(widgets):
    """焦点链的稳定指纹：类型 + 对象名 + 可见文案。

    面板弹出/收回可能触发右栏重建，控件对象会换新；D7 的契约是
    「Tab 经过的顺序不变」，用身份比会把重建误判成顺序变化。
    """
    out = []
    for w in widgets:
        text = ""
        for attr in ("text", "title"):
            fn = getattr(w, attr, None)
            if callable(fn):
                try:
                    text = str(fn())
                    break
                except Exception:  # noqa: BLE001
                    pass
        out.append((type(w).__name__, w.objectName(), text))
    return out


def test_tab_order_unchanged_after_pop_out_and_return(window):
    w, _mw = window
    assert w.right_tab.count() > 0
    key = w._float_key("right", w.right_tab, 0)

    baseline_order = tuple(w._tab_order)
    baseline_chain = _walk(w.right_nav, w.home_btn)
    assert w.home_btn in baseline_chain

    calls = []
    orig = w._setup_tab_order

    def spy():
        calls.append(True)
        orig()

    w._setup_tab_order = spy

    w._pop_out_tab("right", 0)
    QTest.qWait(30)
    assert key in w._float_windows
    popped = len(calls)
    assert popped >= 1, "弹出页签后必须重跑 tab order"

    w._return_panel(key)
    QTest.qWait(30)
    assert key not in w._float_windows
    assert tuple(w._tab_order) == baseline_order
    assert _keys(_walk(w.right_nav, w.home_btn)) == _keys(baseline_chain), \
        "弹出再收回后，右栏到 home 的 Tab 顺序必须与弹出前一致"
    assert len(calls) == popped + 1, "收回页签后必须再重跑一次 tab order"
