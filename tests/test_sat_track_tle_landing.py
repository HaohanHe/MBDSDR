# SPDX-License-Identifier: MIT
"""卫星跟踪面板「更新 TLE」必须落地：进行中禁用，结束（成功/失败）复位。

真值只有一份——主窗口的 ``_tle_update_result`` 回执；面板只当视图。
这里用一个只有该信号的假宿主验证三条出口：成功、失败、宿主拒绝启动。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402
from PySide6.QtCore import Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


class _FakeHost(QWidget):
    """只保留主窗口契约里面板真正依赖的两样：回执信号 + 星历目录是否可用。"""

    _tle_update_result = Signal(object)

    def __init__(self):
        super().__init__()
        self._loop_sat_tracker = object()
        self.requests = 0


def _make_panel(host):
    from desktop.sat_track_panel import SatTrackPanel

    panel = SatTrackPanel(host)
    panel.update_tle_requested.connect(lambda: setattr(host, "requests", host.requests + 1))
    return panel


def test_click_enters_busy_and_receipt_leaves_it(qapp):
    host = _FakeHost()
    panel = _make_panel(host)
    try:
        panel.update_tle_btn.click()

        assert host.requests == 1
        assert panel.update_tle_btn.isEnabled() is False, "进行中必须禁用，避免重复点"
        assert panel.tle_refresh_btn.isEnabled() is False
        assert "更新中" in panel.update_tle_btn.text()

        host._tle_update_result.emit((42, None))

        assert panel.update_tle_btn.isEnabled() is True, "回执到了必须复位"
        assert panel.update_tle_btn.text() == "更新 TLE"
        text = panel.tle_status_label.text()
        assert "42" in text and "已更新" in text
        assert panel.tle_status_label.isVisibleTo(panel)
    finally:
        panel.deleteLater()


def test_failure_receipt_lands_visible_reason(qapp):
    host = _FakeHost()
    panel = _make_panel(host)
    try:
        panel.update_tle_btn.click()
        host._tle_update_result.emit(
            (0, {"code": "network_timeout", "reason": "connect timeout"}))

        assert panel.update_tle_btn.isEnabled() is True, "失败后必须能重试"
        text = panel.tle_status_label.text()
        assert "失败" in text, "失败必须写清没做成什么"
        assert "建议" in text, "失败必须给出现在能做什么"
    finally:
        panel.deleteLater()


def test_host_refusal_does_not_leave_panel_busy(qapp):
    """宿主没有可更新的目录 → 不会发回执，面板必须自己立刻落地。"""
    host = _FakeHost()
    host._loop_sat_tracker = None
    panel = _make_panel(host)
    try:
        panel._on_update_tle_clicked()

        assert panel.update_tle_btn.isEnabled() is True
        text = panel.tle_status_label.text()
        assert "未开始" in text and "重试" in text
    finally:
        panel.deleteLater()


def test_standalone_panel_does_not_fake_busy(qapp):
    """没有完成回执通道时不进入「进行中」，也不假装知道结果。"""
    from desktop.sat_track_panel import SatTrackPanel

    panel = SatTrackPanel()
    try:
        panel._on_update_tle_clicked()
        assert panel.update_tle_btn.isEnabled() is True
        assert "状态栏" in panel.tle_status_label.text()
    finally:
        panel.deleteLater()
