# SPDX-License-Identifier: MIT
"""星象仪收起时卫星跟踪器必须停表（用户口径：没展开的功能不许在后台烧 CPU）。

反证：把 ``RFSkyView`` 的 ``visibility_changed`` 发射删掉、或把
``SatelliteTracker._on_view_visibility`` 的 ``stop`` 删掉，本文件红。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_tracker_timer_follows_sky_view_visibility(qapp):
    from rf_sky_view import RFSkyView, SatelliteTracker

    view = RFSkyView()
    # 间隔拉到极长：本测试只验启停，不让它真的反复算 sgp4。
    tracker = SatelliteTracker(view, 39.9, 116.4, interval_ms=10 ** 9)
    try:
        # 构造后 refresh 会做一帧；之后本测试不再需要真实计算。
        tracker.refresh = lambda: None
        assert tracker._timer.isActive(), "创建时应按原行为开始刷新"

        view.show()
        qapp.processEvents()
        assert tracker._timer.isActive(), "页签展开时刷新应恢复"

        view.hide()
        qapp.processEvents()
        assert not tracker._timer.isActive(), \
            "星象仪收起后不得再跑 10s 一轮的 sgp4/过境粗扫"

        view.show()
        qapp.processEvents()
        assert tracker._timer.isActive(), "重新展开应立即恢复刷新"
    finally:
        tracker._timer.stop()
        view.deleteLater()
