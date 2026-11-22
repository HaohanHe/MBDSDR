# SPDX-License-Identifier: MIT
"""新时空面板「不展开就不跑」：隐藏停表、显示恢复（FR-06）。"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from PySide6.QtGui import QHideEvent, QShowEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from desktop.new_spacetime_panel import NewSpacetimePanel  # noqa: E402


def _pump(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def test_timers_stop_on_hide_and_resume_on_show():
    panel = NewSpacetimePanel()
    panel.showEvent(QShowEvent())
    assert panel._sync_timer.isActive(), "可见时应有 60s 授时刷新"
    assert panel._countdown_timer.isActive(), "可见时应有 1Hz 倒计时"

    panel.hideEvent(QHideEvent())
    assert not panel._sync_timer.isActive(), "隐藏后授时刷新必须停表"
    assert not panel._countdown_timer.isActive(), "隐藏后倒计时必须停表"

    panel.showEvent(QShowEvent())
    assert panel._sync_timer.isActive(), "重新显示后授时刷新应恢复"
    assert panel._countdown_timer.isActive(), "重新显示后倒计时应恢复"
    panel.close()


def test_hidden_timer_counter_does_not_grow():
    """7.4：隐藏后后台计数器不再增长，重新显示后恢复。"""
    panel = NewSpacetimePanel()
    counts = {"n": 0}
    panel._countdown_timer.timeout.connect(lambda: counts.__setitem__("n", counts["n"] + 1))
    panel._countdown_timer.setInterval(50)

    panel.showEvent(QShowEvent())
    _pump(0.3)
    assert counts["n"] > 0, "可见时倒计时心跳应在增长"

    panel.hideEvent(QHideEvent())
    counts["n"] = 0
    _pump(0.3)
    assert counts["n"] == 0, "隐藏后计数器不得继续增长"

    panel.showEvent(QShowEvent())
    _pump(0.3)
    assert counts["n"] > 0, "重新显示后计数应恢复"
    panel.close()
