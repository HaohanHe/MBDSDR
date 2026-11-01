# SPDX-License-Identifier: MIT
"""
tests/test_sky_track_wiring.py
==============================
天空图「跟踪」开关 → 主窗口卫星闭环 的接线钉扎（阶段3）：

  rf_sky_view 侧：
  1. 选中目录内卫星 → 跟踪按钮可见且可用；未选中 → 隐藏；
  2. 按下跟踪 → track_toggled(True, 星名, 目录下行频点)；
  3. 再按 → track_toggled(False, ...)；
  4. 目录外卫星（无下行频点）→ 诚实拒绝，按钮弹回不残留假状态；
  5. 跟踪中换星/取消选中 → 自动发停止信号。

  main_window 侧（offscreen）：
  6. track_toggled(True) → _start_satellite_tracking 被调（mock 断言参数）；
  7. 无观测站启动被拒 → 天空图开关被复位（不残留假状态）；
  8. 自动停（_stop_satellite_tracking）→ 天空图开关复位。

运行：
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_sky_track_wiring.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

TLE_L1 = ("1 33591U 09005A   24001.50000000  .00000300  00000-0  16087-3 0  9990")
TLE_L2 = ("2 33591  99.1900 312.3500 0014200  80.0000 280.0000 14.11715000458920")


def _make_view_with(name: str):
    from desktop.rf_sky_view import RFSkyView, SkyObject
    view = RFSkyView()
    obj = SkyObject(name=name, azimuth_deg=180.0, elevation_deg=45.0,
                    obj_type="satellite")
    view.set_objects([obj])
    view.set_satellite_catalog([{
        "name": name, "line1": TLE_L1, "line2": TLE_L2, "freq_mhz": 0.0,
        "kind": "weather",
    }])
    view.set_observer(40.0, 116.0, 0.0)
    view.on_object_picked(obj)
    return view


class TestTrackButtonStates:
    def test_hidden_before_selection(self):
        from desktop.rf_sky_view import RFSkyView
        view = RFSkyView()
        assert not view._track_btn.isVisibleTo(view)
        view.close()

    def test_visible_enabled_for_catalog_sat(self):
        view = _make_view_with("NOAA 19")
        assert view._track_btn.isVisibleTo(view)
        assert view._track_btn.isEnabled()

    def test_disabled_for_unknown_sat(self):
        view = _make_view_with("SOME UNKNOWN SAT")
        assert view._track_btn.isVisibleTo(view)
        assert not view._track_btn.isEnabled()


class TestTrackToggleSignals:
    def test_toggle_on_emits_start(self):
        view = _make_view_with("NOAA 19")
        events = []
        view.track_toggled.connect(
            lambda on, name, hz: events.append((on, name, hz)))
        view._track_btn.setChecked(True)
        assert events == [(True, "NOAA 19", pytest.approx(137.1e6))]

    def test_toggle_off_emits_stop(self):
        view = _make_view_with("NOAA 19")
        events = []
        view.track_toggled.connect(
            lambda on, name, hz: events.append((on, name, hz)))
        view._track_btn.setChecked(True)
        view._track_btn.setChecked(False)
        assert events[0][0] is True
        assert events[1][0] is False

    def test_unknown_sat_refuses_check(self):
        view = _make_view_with("SOME UNKNOWN SAT")
        events = []
        view.track_toggled.connect(
            lambda on, name, hz: events.append((on, name, hz)))
        view._track_btn.setChecked(True)  # 应被诚实拒绝并弹回
        assert not view._track_btn.isChecked()
        assert events == []

    def test_reselect_stops_tracking(self):
        view = _make_view_with("NOAA 19")
        events = []
        view.track_toggled.connect(
            lambda on, name, hz: events.append((on, name, hz)))
        view._track_btn.setChecked(True)
        # 换选另一颗目录内卫星（不同名）→ 自动停旧跟踪
        from desktop.rf_sky_view import SkyObject
        obj2 = SkyObject(name="NOAA 18", azimuth_deg=90.0, elevation_deg=30.0,
                         obj_type="satellite")
        view.set_objects([obj2] + list(view._objects))
        view.set_satellite_catalog([{
            "name": "NOAA 18", "line1": TLE_L1, "line2": TLE_L2,
            "freq_mhz": 0.0, "kind": "weather",
        }])
        view.on_object_picked(obj2)
        assert events[-1][0] is False
        assert not view._track_btn.isChecked()

    def test_set_tracking_checked_no_emit(self):
        view = _make_view_with("NOAA 19")
        events = []
        view.track_toggled.connect(
            lambda on, name, hz: events.append((on, name, hz)))
        view.set_tracking_checked(True)
        view.set_tracking_checked(False)
        assert events == []  # 主窗口反向同步不发射信号


@pytest.fixture(scope="module")
def mw():
    from main_window import MainWindow
    w = MainWindow()
    w.show()
    from PySide6.QtCore import QTimer
    QTimer.singleShot(120, app.quit)
    app.exec()
    yield w
    try:
        w._disconnect()
    except Exception:
        pass
    w.close()


class TestMainWindowWiring:
    def test_track_on_calls_start(self, mw, monkeypatch):
        calls = []
        monkeypatch.setattr(
            mw, "_start_satellite_tracking",
            lambda name, hz: calls.append((name, hz)))
        mw._on_sky_track_toggled(True, "NOAA 19", 137.1e6)
        assert calls == [("NOAA 19", 137.1e6)]
        monkeypatch.undo()

    def test_track_off_calls_stop(self, mw, monkeypatch):
        stops = []
        monkeypatch.setattr(
            mw, "_stop_satellite_tracking", lambda: stops.append(1))
        mw._on_sky_track_toggled(False, "NOAA 19", 137.1e6)
        assert stops == [1]
        monkeypatch.undo()

    def test_refused_start_resets_sky_button(self, mw, monkeypatch):
        # 无观测站 + 启动被拒（_start_satellite_tracking 不动 timer）→ 开关复位
        monkeypatch.setattr(mw, "_observer_lat", None)
        monkeypatch.setattr(mw, "_observer_lon", None)
        mw.sky_view.set_tracking_checked(True)
        mw._on_sky_track_toggled(True, "NOAA 19", 137.1e6)
        assert not mw.sky_view._track_btn.isChecked()
        monkeypatch.undo()

    def test_auto_stop_resets_sky_button(self, mw):
        mw.sky_view.set_tracking_checked(True)
        assert mw.sky_view._track_btn.isChecked()
        mw._stop_satellite_tracking()
        assert not mw.sky_view._track_btn.isChecked()
