# SPDX-License-Identifier: MIT
"""UX-08：AI 拒绝天空动作时，拒绝原因与下一步必须看得见。

反证：当前 `_request_sky_action` 在 `ok=False` 时只回弹按钮，状态栏为空、
星象仪覆盖卡不显示；本文件第一条断言即红。
"""
import logging
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

_LOGGER = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def window(qtapp):
    import main_window as mw
    from PySide6.QtTest import QTest
    w = mw.MainWindow()
    QTest.qWait(350)
    yield w
    try:
        w._teardown_connection()
    except Exception:
        _LOGGER.exception("MainWindow teardown 失败")


def _check_track_btn(w, checked: bool) -> None:
    """跟踪按钮在 sky_view 上，不是 MainWindow 属性。"""
    btn = w.sky_view._track_btn
    btn.blockSignals(True)
    btn.setChecked(checked)
    btn.blockSignals(False)


def test_ai_rejection_is_visible(window):
    w = window
    w._sat_track_timer = None
    _check_track_btn(w, True)
    w.ai_panel.request_sky_action = lambda ctx: False

    ok = w._request_sky_action("track", {"satellite": "ISS"})

    assert ok is False
    msg = w.statusBar().currentMessage()
    assert "跟踪未启动" in msg, f"拒绝必须看得见，实际: {msg}"
    assert "下一步" in msg, f"必须给下一步，实际: {msg}"
    assert w.sky_view._track_btn.isChecked() is False, "拒绝后按钮必须回弹"


def test_planetarium_overlay_shows_rejection(window):
    w = window
    w._sat_track_timer = None
    _check_track_btn(w, True)
    w.ai_panel.request_sky_action = lambda ctx: False
    w._app_mode = "planetarium"

    w._request_sky_action("track", {"satellite": "ISS"})

    assert w._planetarium_ai_card.isHidden() is False, "全屏时覆盖卡必须显示"
    assert "跟踪未启动" in w._planetarium_ai_card_text.text()
    assert "下一步" in w._planetarium_ai_card_text.text()
    w._app_mode = "ide"
