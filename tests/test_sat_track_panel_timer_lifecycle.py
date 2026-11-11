# SPDX-License-Identifier: MIT
"""SatTrackPanel 0 ms 单发定时器的生命周期回归（tests/test_sat_track_panel_timer_lifecycle.py）
==============================================================================================

缺陷（修复前）：``desktop/sat_track_panel.py`` 的
``QTimer.singleShot(0, self._apply_narrow_defaults)`` 只给了绑定方法，没把
receiver 绑到面板。面板**从未 show**（藏在非当前页签）且在事件循环首次空闲前
被父级销毁时，定时器仍会回调 ``self.window()``（``:218``），抛出
``RuntimeError: libshiboken: Internal C++ object (SatTrackPanel) already deleted``。

修复：三参重载 ``QTimer.singleShot(0, self, self._apply_narrow_defaults)``——
receiver 的 C++ 对象销毁时定时器自动取消。

验收口径（本文件钉死）：
  1. 隐藏面板在首次空闲前被销毁：10 轮 0 异常（修复前实测 10/10）；
  2. 原始目的不回退：从未 show 的面板仍按窗口宽度做窄屏收起（<1440 收起
     第 2 个起的分组，宽屏保持展开）；
  3. 夹具自检：旧两参写法必须仍被捕获（证明「0 异常」不是夹具失灵）。
"""
import os
import sys
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
import shiboken6  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402


@pytest.fixture(scope="module")
def qtapp(isolated_profile):
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


class _ExcCapture:
    """把槽里抛出的异常记下来（PySide6 用 sys.excepthook 报告）。"""

    def __init__(self):
        self.messages = []
        self._old = None

    def __enter__(self):
        self._old = sys.excepthook
        sys.excepthook = lambda t, v, tb: self.messages.append(
            "".join(traceback.format_exception(t, v, tb)))
        return self

    def __exit__(self, *exc_info):
        sys.excepthook = self._old
        return False


def _host_with_panel():
    from desktop.sat_track_panel import SatTrackPanel
    host = QWidget()
    lay = QVBoxLayout(host)
    panel = SatTrackPanel()
    lay.addWidget(panel)
    return host, panel


@pytest.mark.usefixtures("qtapp")
def test_hidden_panel_destroyed_before_idle_does_not_callback_deleted_object():
    """修复前 10/10 异常；修复后必须 0。"""
    with _ExcCapture() as cap:
        for _ in range(10):
            host, _panel = _host_with_panel()
            # 刻意不 show：面板藏在非当前页签，0 ms 定时器仍挂在队列里
            shiboken6.delete(host)
            QApplication.instance().processEvents()
    assert cap.messages == [], (
        "面板销毁后定时器仍回调到已删除对象：\n" + "\n".join(cap.messages))


@pytest.mark.usefixtures("qtapp")
def test_hidden_panel_narrow_window_still_collapses_on_first_idle():
    """原始目的：从未 show 的窄屏面板在首次空闲时收起第 2 个起的分组。

    例外：过境倒计时（机制 C）是「选完星就能看」的主信息，窄屏也保持展开。
    """
    host, panel = _host_with_panel()
    host.resize(1200, 800)
    QApplication.instance().processEvents()

    assert panel._collapse_applied, "首次空闲判定没有执行"
    assert len(panel._collapsible_boxes) >= 2
    assert panel._collapsible_boxes[0].isChecked(), "目标卫星分组应保持展开"
    for box in panel._collapsible_boxes[1:]:
        if box is panel._countdown_box:
            assert box.isChecked(), "过境倒计时在窄屏下也应保持展开"
            continue
        assert not box.isChecked(), "窄屏下非目标分组应默认收起"


@pytest.mark.usefixtures("qtapp")
def test_hidden_panel_wide_window_stays_expanded():
    host, panel = _host_with_panel()
    host.resize(1500, 800)
    QApplication.instance().processEvents()

    assert panel._collapse_applied
    assert all(box.isChecked() for box in panel._collapsible_boxes)


@pytest.mark.usefixtures("qtapp")
def test_countdown_repaint_timer_stops_when_not_visible():
    """1 s 倒计时重绘不能常驻：面板隐藏、分组收起都必须停心跳。

    性能口径：不是「重排很便宜」，而是「不显示时不该有心跳」。
    """
    host, panel = _host_with_panel()
    host.resize(1500, 800)
    host.show()
    QApplication.instance().processEvents()

    assert panel.isVisible()
    assert panel._countdown_timer.isActive(), "面板可见且分组展开时应有 1s 心跳"

    panel._countdown_box.setChecked(False)
    assert not panel._countdown_timer.isActive(), "倒计时分组收起后心跳应停止"

    panel._countdown_box.setChecked(True)
    assert panel._countdown_timer.isActive(), "重新展开后心跳应恢复"

    host.hide()
    QApplication.instance().processEvents()
    assert not panel.isVisible()
    assert not panel._countdown_timer.isActive(), "面板隐藏后心跳应停止"


@pytest.mark.usefixtures("qtapp")
def test_fixture_catches_unbound_single_shot():
    """夹具自检：旧两参写法必须仍被抓到，否则上面的 0 异常没有意义。"""
    with _ExcCapture() as cap:
        for _ in range(10):
            host = QWidget()
            lay = QVBoxLayout(host)

            class _Probe(QWidget):
                def touch(self):
                    _ = self.width()

            probe = _Probe()
            lay.addWidget(probe)
            QTimer.singleShot(0, probe.touch)   # 旧写法：无 receiver
            shiboken6.delete(host)
            QApplication.instance().processEvents()

    assert len(cap.messages) == 10
