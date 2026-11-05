# SPDX-License-Identifier: MIT
"""
tests/test_gimbal_track_chain.py
================================
云台接卫星的端到端追踪链（**全部为模拟后端**，不接真机）：
  卫星位置 → 方位/俯仰 → 云台指令 → 实际指向 → 误差统计。

钉住：
  1. 指令为**绝对角**（az/el），不是增量角；死区 2°、最小间隔 1s、速率上限 20°/s；
  2. 每拍记录 卫星真角 / 指令角 / 姿态回读角 与耗时，能给出过境全过程
     最大/平均追踪误差；
  3. 四条失败路径必须「明确停止并给原因」，且停止后不再下发任何角度：
       卫星不可见 / 站址不可信 / 云台离线 / 指令超时。
"""
from __future__ import annotations

import os
import sys
import math
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


# ---------------------------------------------------------------------------
# 纯逻辑：角距与误差统计
# ---------------------------------------------------------------------------
class TestAngularSeparation:
    def test_same_point_zero(self):
        from mbdsdr_ai.gimbal_follow import angular_separation_deg
        assert angular_separation_deg(123.0, 30.0, 123.0, 30.0) == pytest.approx(0.0)

    def test_wrap_across_north(self):
        from mbdsdr_ai.gimbal_follow import angular_separation_deg
        # 359° 与 1° 相差 2°，不能算成 358°
        assert angular_separation_deg(359.0, 0.0, 1.0, 0.0) == pytest.approx(2.0, abs=0.05)

    def test_elevation_difference(self):
        from mbdsdr_ai.gimbal_follow import angular_separation_deg
        assert angular_separation_deg(180.0, 10.0, 180.0, 40.0) == pytest.approx(30.0, abs=0.2)


class TestPointingErrorRecorder:

    def test_max_and_mean_over_synthetic_pass(self):
        """模拟过境：方位滞后 3°；30° 仰角时真实球面角距 = 3°·cos30°。

        按 az/el 分量差会算成 3°（高估）；这里钉住球面角距口径。
        """
        from mbdsdr_ai.gimbal_follow import PointingErrorRecorder
        rec = PointingErrorRecorder()
        for i in range(5):
            truth_az = 100.0 + 10.0 * i
            rec.record(truth_az=truth_az, truth_el=30.0,
                      cmd_az=truth_az - 3.0, cmd_el=30.0, elapsed_s=1.0)
        stats = rec.stats()
        expected = 3.0 * math.cos(math.radians(30.0))
        assert stats["samples"] == 5
        assert stats["cmd_max_deg"] == pytest.approx(expected, abs=0.05)
        assert stats["cmd_mean_deg"] == pytest.approx(expected, abs=0.05)
        assert stats["elapsed_total_s"] == pytest.approx(5.0)

    def test_pose_error_when_pose_available(self):
        from mbdsdr_ai.gimbal_follow import PointingErrorRecorder
        rec = PointingErrorRecorder()
        rec.record(truth_az=120.0, truth_el=20.0, cmd_az=121.0, cmd_el=20.0,
                   pose_az=125.0, pose_el=22.0, elapsed_s=0.5)
        stats = rec.stats()
        assert stats["pose_max_deg"] is not None
        assert stats["pose_max_deg"] > stats["cmd_max_deg"]

    def test_no_pose_means_pose_stats_none(self):
        from mbdsdr_ai.gimbal_follow import PointingErrorRecorder
        rec = PointingErrorRecorder()
        rec.record(truth_az=1.0, truth_el=1.0, cmd_az=1.0, cmd_el=1.0)
        assert rec.stats()["pose_mean_deg"] is None


# ---------------------------------------------------------------------------
# 主窗口：模拟过境全程
# ---------------------------------------------------------------------------
class FakeGimbal:
    """模拟云台：记录每条**绝对角**指令，姿态回读 = 指令角（理想执行）。"""

    def __init__(self, reply: str = "ok"):
        self.status = SimpleNamespace(
            mode=SimpleNamespace(value="board_pwm"), connected=True,
            backend_info="fake board")
        self.board = SimpleNamespace(_send=lambda m, p: {"ok": True})
        self.pointed = []
        self.stopped = 0
        self.reply = reply

    def point_to_satellite(self, az, el):
        if self.reply == "timeout":
            return {"dispatched": False, "pose_valid": False,
                    "error": "超时（2.0s 未收到应答）"}
        if self.reply == "offline":
            return {"dispatched": False, "pose_valid": False,
                    "error": "后端未连接"}
        self.pointed.append((az, el))
        return {"dispatched": True, "pose_valid": True,
                "current_az": az, "current_el": el, "error": ""}

    def stop(self):
        self.stopped += 1
        return {"ok": True}

    def use_board(self):
        return self.status


@pytest.fixture(autouse=True)
def _isolated(isolated_profile):
    """MainWindow 会读写 DesktopSettings/QSettings，必须隔离到 tmp_path。"""
    yield


@pytest.fixture(scope="module")
def mw():
    from main_window import MainWindow
    from PySide6.QtCore import QTimer
    w = MainWindow()
    w.show()
    QTimer.singleShot(120, app.quit)
    app.exec()
    yield w
    try:
        w._disconnect()
    except Exception:
        pass
    w.close()


def _active_tracking(mw, monkeypatch, pos, gimbal):
    from PySide6.QtCore import QTimer
    t = QTimer(mw)
    t.setInterval(500)
    t.start()
    monkeypatch.setattr(mw, "_sat_track_timer", t)
    monkeypatch.setattr(mw, "_gimbal_controller", lambda: gimbal)
    monkeypatch.setattr(mw, "_on_tune_sdr", lambda *a, **k: None)
    monkeypatch.setattr(mw, "_loop_sat_tracker", SimpleNamespace(
        current_position=lambda: dict(pos),
        downlink_freq_hz=lambda: 137.1e6,
        selected_name="NOAA 19"))
    mw._observer_lat, mw._observer_lon = 39.9, 116.4
    mw._observer_from_gnss = False
    return t


def _pos(az=123.0, el=30.0):
    return {"valid": True, "azimuth": az, "elevation": el,
            "doppler_hz": 0.0, "corrected_freq_hz": 137.1e6,
            "range_km": 1050.0}


class TestTrackChainData:
    def test_chain_reports_each_stage_and_elapsed(self, mw, monkeypatch):
        g = FakeGimbal()
        _active_tracking(mw, monkeypatch, _pos(), g)
        mw._on_sky_gimbal_follow_toggled(True)
        mw._poll_satellite_track()
        cyc = mw.last_gimbal_cycle()
        assert cyc["satellite_az"] == 123.0
        assert cyc["satellite_el"] == 30.0
        assert cyc["command_az"] == 123.0, "首拍必须给完整绝对角"
        assert cyc["command_el"] == 30.0
        assert cyc["dispatched"] is True
        assert cyc["pose_valid"] is True
        for key in ("pos_ms", "dispatch_ms", "total_ms"):
            assert isinstance(cyc[key], (int, float)) and cyc[key] >= 0.0
        # 实际指向 = 指令（模拟理想执行）
        assert cyc["pose_az"] == 123.0
        assert cyc["cmd_error_deg"] == pytest.approx(0.0, abs=0.05)

    def test_commands_are_absolute_not_incremental(self, mw, monkeypatch):
        g = FakeGimbal()
        _active_tracking(mw, monkeypatch, _pos(123.0, 30.0), g)
        mw._on_sky_gimbal_follow_toggled(True)
        mw._poll_satellite_track()
        assert g.pointed == [(123.0, 30.0)]
        # 目标前移 10°；把整形器时间基线回拨 2s，让本拍允许下发
        mw._loop_sat_tracker.current_position = lambda: _pos(133.0, 30.0)
        mw._gimbal_limiter._last_time -= 2.0
        mw._poll_satellite_track()
        assert g.pointed[-1] == (133.0, 30.0), "第二拍应为绝对角 133°，不是增量 +10"
        mw._gimbal_limiter._last_time -= 2.0
        mw._loop_sat_tracker.current_position = lambda: _pos(143.0, 30.0)
        mw._poll_satellite_track()
        assert g.pointed[-1] == (143.0, 30.0)

    def test_deadband_suppresses_small_motion(self, mw, monkeypatch):
        g = FakeGimbal()
        _active_tracking(mw, monkeypatch, _pos(123.0, 30.0), g)
        mw._on_sky_gimbal_follow_toggled(True)
        mw._poll_satellite_track()
        assert g.pointed == [(123.0, 30.0)]
        mw._gimbal_limiter._last_time -= 2.0
        mw._loop_sat_tracker.current_position = lambda: _pos(124.0, 30.5)
        mw._poll_satellite_track()
        assert g.pointed == [(123.0, 30.0)], "1° 移动小于 2° 死区，不得重发"

    def test_error_report_covers_whole_pass(self, mw, monkeypatch):
        g = FakeGimbal()
        _active_tracking(mw, monkeypatch, _pos(100.0, 30.0), g)
        mw._on_sky_gimbal_follow_toggled(True)
        for i in range(4):
            mw._loop_sat_tracker.current_position = lambda i=i: _pos(
                100.0 + 10.0 * i, 30.0)
            mw._gimbal_limiter._last_time -= 2.0
            mw._poll_satellite_track()
        # 末段目标突跳 60°：限速 20°/s × 2s 只允许走 40°，残差会被记入误差
        mw._loop_sat_tracker.current_position = lambda: _pos(190.0, 30.0)
        mw._gimbal_limiter._last_time -= 2.0
        mw._poll_satellite_track()
        rep = mw.gimbal_tracking_report()
        assert rep["samples"] >= 5
        assert rep["cmd_max_deg"] > 10.0, "高动态过境必须出现可测的滞后误差"
        assert rep["cmd_mean_deg"] > 0.0
        assert rep["command_mode"] == "absolute_az_el"
        assert rep["deadband_deg"] == 2.0
        assert g.pointed[-1][0] == pytest.approx(170.0, abs=0.2), \
            "限速器每拍最多走 20°/s × 2s = 40°"


class TestFailurePaths:
    def _stop_msg(self, mw):
        return mw.statusBar().currentMessage()

    def test_satellite_invisible_stops_and_no_more_commands(self, mw, monkeypatch):
        g = FakeGimbal()
        _active_tracking(mw, monkeypatch, _pos(123.0, 2.0), g)
        mw._on_sky_gimbal_follow_toggled(True)
        mw._poll_satellite_track()
        assert mw._gimbal_follow is False
        assert g.pointed == [], "卫星不可见时不得下发角度"
        assert g.stopped >= 1
        assert not mw.sky_view._gimbal_btn.isChecked()
        assert not mw._sat_track_timer.isActive()
        assert "不可见" in self._stop_msg(mw) and "停止" in self._stop_msg(mw)

    def test_untrusted_station_refuses_gimbal(self, mw, monkeypatch):
        g = FakeGimbal()
        _active_tracking(mw, monkeypatch, _pos(), g)
        mw._observer_lat, mw._observer_lon = 0.0, 0.0  # 占位坐标
        mw._on_sky_gimbal_follow_toggled(True)
        assert mw._gimbal_follow is False
        assert not mw.sky_view._gimbal_btn.isChecked()
        assert g.pointed == []
        msg = self._stop_msg(mw)
        assert "站址不可信" in msg and "0_0" in msg

    def test_untrusted_station_mid_pass_stops_follow(self, mw, monkeypatch):
        g = FakeGimbal()
        _active_tracking(mw, monkeypatch, _pos(), g)
        mw._on_sky_gimbal_follow_toggled(True)
        mw._poll_satellite_track()
        mw._observer_lat, mw._observer_lon = 0.0, 0.0
        mw._poll_satellite_track()
        assert mw._gimbal_follow is False
        assert "站址不可信" in self._stop_msg(mw)
        n = len(g.pointed)
        mw._poll_satellite_track()
        assert len(g.pointed) == n, "停止后不得继续下发上一个角"

    def test_gimbal_offline_reports_reason(self, mw, monkeypatch):
        g = FakeGimbal()
        _active_tracking(mw, monkeypatch, _pos(), g)
        mw._on_sky_gimbal_follow_toggled(True)
        monkeypatch.setattr(mw, "_gimbal_available", lambda: False)
        mw._poll_satellite_track()
        assert mw._gimbal_follow is False
        assert g.pointed == []
        assert "离线" in self._stop_msg(mw)

    def test_command_timeout_stops_and_reports(self, mw, monkeypatch):
        g = FakeGimbal(reply="timeout")
        _active_tracking(mw, monkeypatch, _pos(), g)
        mw._on_sky_gimbal_follow_toggled(True)
        mw._poll_satellite_track()
        assert mw._gimbal_follow is False
        assert g.stopped >= 1
        assert "超时" in self._stop_msg(mw)
        assert "停止" in self._stop_msg(mw)
        n = len(g.pointed)
        mw._poll_satellite_track()
        assert len(g.pointed) == n

    def test_gimbal_not_in_tx_prebias_path(self):
        """反证 4：云台不参与 TX 预偏。TX 预偏代码块不得出现云台调用。"""
        import inspect
        from main_window import MainWindow
        for fn in (MainWindow._start_tx, MainWindow._refresh_tx_readout):
            src = inspect.getsource(fn)
            assert "gimbal" not in src.lower(), f"{fn.__name__} 竟与云台耦合"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
