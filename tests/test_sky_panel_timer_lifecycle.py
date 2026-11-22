# SPDX-License-Identifier: MIT
"""星象仪面板「不展开就不跑」：页签收起时停掉 500ms 时间标签刷新（FR-06）。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from PySide6.QtGui import QHideEvent, QShowEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from rf_sky_view import RFSkyViewPanel  # noqa: E402


def test_time_label_timer_stops_when_panel_hidden():
    panel = RFSkyViewPanel()
    panel.showEvent(QShowEvent())
    assert panel._time_ui_timer.isActive(), "面板可见时应刷新时间标签"

    panel.hideEvent(QHideEvent())
    assert not panel._time_ui_timer.isActive(), "页签收起后 500ms 定时器必须停"

    panel.showEvent(QShowEvent())
    assert panel._time_ui_timer.isActive(), "重新显示后应恢复"
    panel.close()
