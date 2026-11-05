# SPDX-License-Identifier: MIT
"""
tests/test_gimbal_follow_wiring.py
==================================
云台跟随闭环接线钉扎（阶段4）：

  rf_sky_view 侧：
  1. 云台按钮默认隐藏且不可用（诚实空态：无后端不画饼）；
  2. set_gimbal_available(True) 后选中卫星 → 按钮可用，toggle 发信号；
  3. set_gimbal_available(False) 时跟随中自动弹回并发停止信号；
  4. set_gimbal_follow_checked 不发射信号（主窗口反向同步专用）。

  main_window 侧（offscreen，fake 云台）：
  5. 未跟踪时开跟随 → 诚实拒绝并复位按钮；
  6. 跟踪中开跟随 → _gimbal_follow=True，500ms 拍内下发卫星 az/el；
  7. 停跟踪 → fake 云台收到 stop()，跟随与按钮复位；
  8. 拍内后端掉线 → 跟随诚实关闭。

运行：
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gimbal_follow_wiring.py -v
"""
import os
import sys
from types import SimpleNamespace

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


class TestGimbalButtonStates:
    def test_hidden_disabled_by_default(self):
        from desktop.rf_sky_view import RFSkyView
        view = RFSkyView()
        assert not view._gimbal_btn.isVisibleTo(view)
        assert not view._gimbal_btn.isEnabled()
        view.close()

    def test_enabled_when_available_and_sat_selected(self):
        view = _make_view_with("NOAA 19")
        view.set_gimbal_available(True)
        assert view._gimbal_btn.isVisibleTo(view)
        assert view._gimbal_btn.isEnabled()

    def test_disabled_when_unavailable_even_with_sat(self):
        view = _make_view_with("NOAA 19")
        assert view._gimbal_btn.isVisibleTo(view)
        assert not view._gimbal_btn.isEnabled()


class TestGimbalToggleSignals:
    def test_toggle_on_off_emits(self):
        view = _make_view_with("NOAA 19")
        view.set_gimbal_available(True)
        events = []
        view.gimbal_follow_toggled.connect(lambda on: events.append(on))
        view._gimbal_btn.setChecked(True)
        view._gimbal_btn.setChecked(False)
        assert events == [True, False]

    def test_unavailable_bounces_check(self):
        view = _make_view_with("NOAA 19")  # 不可用（未注入可用性）
        events = []
        view.gimbal_follow_toggled.connect(lambda on: events.append(on))
        view._gimbal_btn.setChecked(True)
        assert not view._gimbal_btn.isChecked()
        assert events == []

    def test_lost_availability_stops_follow(self):
        view = _make_view_with("NOAA 19")
        view.set_gimbal_available(True)
        events = []
        view.gimbal_follow_toggled.connect(lambda on: events.append(on))
        view._gimbal_btn.setChecked(True)
        view.set_gimbal_available(False)  # 后端掉线
        assert not view._gimbal_btn.isChecked()
        assert events == [True, False]

    def test_set_follow_checked_no_emit(self):
        view = _make_view_with("NOAA 19")
        events = []
        view.gimbal_follow_toggled.connect(lambda on: events.append(on))
        view.set_gimbal_follow_checked(True)
        view.set_gimbal_follow_checked(False)
        assert events == []


class FakeGimbal:
    """复刻 GimbalController 连接/握手语义的替身（monkeypatch 纪律）。"""

    def __init__(self):
        self.status = SimpleNamespace(
            mode=SimpleNamespace(value="board_pwm"), connected=True,
            backend_info="fake board")
        self.board = SimpleNamespace(_send=lambda m, p: {"ok": True})
        self.pointed: list = []
        self.stopped = 0

    def point_to_satellite(self, az, el):
        self.pointed.append((az, el))
        return {"dispatched": True}

    def stop(self):
        self.stopped += 1
        return {"ok": True}

    def use_board(self):
        return self.status


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


class TestMainWindowGimbalLoop:
    def _fake_active_tracking(self, mw, monkeypatch):
        from PySide6.QtCore import QTimer
        t = QTimer(mw)
        t.setInterval(500)
        t.start()
        monkeypatch.setattr(mw, "_sat_track_timer", t)
        fake = FakeGimbal()
        monkeypatch.setattr(mw, "_gimbal_controller", lambda: fake)
        monkeypatch.setattr(mw, "_on_tune_sdr", lambda *a, **k: None)
        # 云台指向走 trust_for_tx 严格口：给一个真实陆地站址（非 (0,0) 占位）
        mw._observer_lat, mw._observer_lon = 39.9, 116.4
        mw._observer_from_gnss = False
        monkeypatch.setattr(mw, "_loop_sat_tracker", SimpleNamespace(
            current_position=lambda: {
                "valid": True, "elevation": 30.0, "azimuth": 123.0,
                "doppler_hz": 3500.0, "corrected_freq_hz": 137.1e6,
                "range_km": 1050.0,
            },
            # 多普勒补偿关闭时主循环回退到标称下行（monkeypatch 纪律：
            # 替身必须复刻真 tracker 的这一接口，97683ab 之后主循环会调）。
            downlink_freq_hz=lambda: 137.1e6 - 3500.0))
        return fake

    def test_follow_refused_without_tracking(self, mw, monkeypatch):
        monkeypatch.setattr(mw, "_gimbal_controller", lambda: FakeGimbal())
        mw._on_sky_gimbal_follow_toggled(True)
        assert mw._gimbal_follow is False
        assert not mw.sky_view._gimbal_btn.isChecked()
        monkeypatch.undo()

    def test_follow_drives_gimbal_each_poll(self, mw, monkeypatch):
        fake = self._fake_active_tracking(mw, monkeypatch)
        mw._on_sky_gimbal_follow_toggled(True)
        assert mw._gimbal_follow is True
        mw._poll_satellite_track()
        assert fake.pointed == [(123.0, 30.0)]
        # 同一目标 + 间隔未到（整形器最小间隔/死区）→ 不再下发，
        # 避免 500ms 环变成云台指令风暴；到位即停。
        mw._poll_satellite_track()
        assert fake.pointed == [(123.0, 30.0)]
        monkeypatch.undo()

    def test_target_lost_unlocks_gimbal(self, mw, monkeypatch):
        """目标位置失效 → 云台跟随诚实解锁并停机，不向过期方位下发。"""
        fake = self._fake_active_tracking(mw, monkeypatch)
        mw._loop_sat_tracker = SimpleNamespace(
            current_position=lambda: {"valid": False, "elevation": 0.0,
                                      "azimuth": 0.0},
            downlink_freq_hz=lambda: 137.1e6)
        mw._on_sky_gimbal_follow_toggled(True)
        mw._poll_satellite_track()
        assert mw._gimbal_follow is False
        assert fake.pointed == []
        assert fake.stopped >= 1
        assert not mw.sky_view._gimbal_btn.isChecked()
        monkeypatch.undo()

    def test_stop_tracking_stops_gimbal(self, mw, monkeypatch):
        fake = self._fake_active_tracking(mw, monkeypatch)
        mw._on_sky_gimbal_follow_toggled(True)
        mw.sky_view.set_gimbal_follow_checked(True)
        mw._stop_satellite_tracking()
        assert fake.stopped == 1
        assert mw._gimbal_follow is False
        assert not mw.sky_view._gimbal_btn.isChecked()
        monkeypatch.undo()

    def test_backend_loss_mid_loop_honest_off(self, mw, monkeypatch):
        self._fake_active_tracking(mw, monkeypatch)
        mw._on_sky_gimbal_follow_toggled(True)
        # 拍内后端消失
        monkeypatch.setattr(mw, "_gimbal_available", lambda: False)
        mw._poll_satellite_track()
        assert mw._gimbal_follow is False
        assert not mw.sky_view._gimbal_btn.isChecked()
        monkeypatch.undo()
