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
import logging
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
                   pose_az=125.0, pose_el=22.0, pose_source="rotctld",
                   elapsed_s=0.5)
        stats = rec.stats()
        assert stats["pose_max_deg"] is not None
        assert stats["pose_max_deg"] > stats["cmd_max_deg"]
        assert stats["pose_source"] == "readback"

    def test_simulated_echo_never_counts_as_mechanical_error(self):
        """假后端「实际指向 = 指令」：误差必须进 simulated 桶，不进 pose_*。"""
        from mbdsdr_ai.gimbal_follow import PointingErrorRecorder
        rec = PointingErrorRecorder()
        rec.record(truth_az=120.0, truth_el=20.0, cmd_az=121.0, cmd_el=20.0,
                   pose_az=121.0, pose_el=20.0, pose_source="simulated")
        stats = rec.stats()
        assert stats["pose_max_deg"] is None, "模拟回声不得产出机械精度读数"
        assert stats["pose_samples"] == 0
        assert stats["pose_source"] == "simulated"
        assert stats["pose_error_by_source"]["simulated"]["samples"] == 1

    def test_no_pose_means_pose_stats_none(self):
        from mbdsdr_ai.gimbal_follow import PointingErrorRecorder
        rec = PointingErrorRecorder()
        rec.record(truth_az=1.0, truth_el=1.0, cmd_az=1.0, cmd_el=1.0)
        assert rec.stats()["pose_mean_deg"] is None

    def test_simulated_cannot_be_mixed_into_readback(self):
        """锁：simulated/别名一律不得落进 readback；混入则 pose_samples≠0 → 红。"""
        from mbdsdr_ai.gimbal_follow import PointingErrorRecorder
        for alias in ("simulated", "command_echo", "stub", "fake", "SIMULATED"):
            assert PointingErrorRecorder.pose_bucket(alias, True) == "simulated"
        rec = PointingErrorRecorder()
        for alias in ("simulated", "command_echo"):
            rec.record(truth_az=120.0, truth_el=20.0, cmd_az=121.0, cmd_el=20.0,
                       pose_az=121.0, pose_el=20.0, pose_source=alias)
        stats = rec.stats()
        assert stats["pose_samples"] == 0, "把 simulated 混进 readback 就会红"
        assert stats["pose_max_deg"] is None
        assert "readback" not in stats["pose_error_by_source"]
        # 没声明来源的姿态只能进 unknown，不能默认算回读
        rec.record(truth_az=120.0, truth_el=20.0, cmd_az=120.0, cmd_el=20.0,
                   pose_az=120.0, pose_el=20.0)
        assert rec.stats()["pose_error_by_source"]["unknown"]["samples"] == 1
        assert rec.stats()["pose_samples"] == 0


# ---------------------------------------------------------------------------
# 主窗口：模拟过境全程
# ---------------------------------------------------------------------------
class FakeGimbal:
    """**模拟**云台：记录每条绝对角指令；回读默认是「指令回声」（理想执行）。

    pose_source="simulated" 明确标注这不是机械回读；只有传 rotctld/imu 才代表
    真实回读，用于验证上报口径不会被模拟数据污染。
    """

    def __init__(self, reply: str = "ok", pose_source: str = "simulated",
                 pose_offset=(0.0, 0.0)):
        self.status = SimpleNamespace(
            mode=SimpleNamespace(value="board_pwm"), connected=True,
            backend_info="fake board")
        self.board = SimpleNamespace(_send=lambda m, p: {"ok": True})
        self.pointed = []
        self.stopped = 0
        self.reply = reply
        self.pose_source = pose_source
        self.pose_offset = pose_offset

    def point_to_satellite(self, az, el):
        if self.reply == "timeout":
            return {"dispatched": False, "pose_valid": False,
                    "error": "超时（2.0s 未收到应答）"}
        if self.reply == "offline":
            return {"dispatched": False, "pose_valid": False,
                    "error": "后端未连接"}
        if self.reply == "raise":
            raise RuntimeError("驱动串口崩了")
        self.pointed.append((az, el))
        return {"dispatched": True, "pose_valid": True,
                "current_az": az + self.pose_offset[0],
                "current_el": el + self.pose_offset[1],
                "pose_source": self.pose_source, "error": ""}

    def stop(self):
        self.stopped += 1
        return {"ok": True}

    def use_board(self):
        return self.status


@pytest.fixture(scope="module")
def mw(isolated_profile):
    from main_window import MainWindow
    from PySide6.QtCore import QTimer
    w = MainWindow()
    w.show()
    QTimer.singleShot(120, app.quit)
    app.exec()
    yield w
    try:
        w._disconnect()
    except Exception as e:  # 清理失败不掩盖主断言，但必须留证
        print(f"模块清理：断连失败 {e!r}")
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
    # mw 是 module 级夹具：每个用例前清空误差记录，避免上一用例的回读样本串味。
    if getattr(mw, "_gimbal_error", None) is not None:
        mw._gimbal_error.reset()
    mw._last_gimbal_cycle = None
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
        src = rep["pose_error_by_source"]
        assert src.get("simulated", {}).get("samples", 0) >= 5
        assert rep["pose_max_deg"] is None, "模拟执行不得产出机械精度读数"
        assert rep["pose_source_summary"]["mechanical_accuracy_claimable"] is False
        assert "指令侧" in rep["pose_source_summary"]["note"]
        # 限速 20°/s × 2s = 40° 上限；上界多给的 2.5° 只吸收真实墙钟开销
        # （每拍的派发/join 耗时会被 20°/s 放大成角度），仍远小于 60° 突跳，
        # 证明的是「限速真的生效」，不是放宽判定。
        assert 170.0 <= g.pointed[-1][0] <= 172.5, g.pointed[-1]

    def test_real_readback_stats_are_separated_from_simulation(
            self, mw, monkeypatch):
        """真实回读（rotctld/IMU）才进 pose_*；机械偏差 2° 能被测到。"""
        g = FakeGimbal(pose_source="rotctld", pose_offset=(2.0, 0.0))
        _active_tracking(mw, monkeypatch, _pos(123.0, 30.0), g)
        mw._on_sky_gimbal_follow_toggled(True)
        mw._poll_satellite_track()
        cyc = mw.last_gimbal_cycle()
        assert cyc["pose_source"] == "rotctld"
        rep = mw.gimbal_tracking_report()
        assert rep["pose_samples"] == 1
        # 30° 仰角下方位差 2° 的球面角距 = 2°·cos30°
        assert rep["pose_max_deg"] == pytest.approx(
            2.0 * math.cos(math.radians(30.0)), abs=0.08)
        assert rep["pose_error_by_source"]["readback"]["samples"] == 1
        assert rep["pose_source_summary"]["mechanical_accuracy_claimable"] is True
        assert "真实回读" in rep["pose_source_summary"]["note"]


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


class TestUserVisibleAccuracyAndStopReporting:
    """新增三字段必须落到用户可见处；不可报时写原因，绝不渲染成 0°。"""

    def test_simulated_pass_ui_says_unclaimable_with_reason(self, mw, monkeypatch):
        g = FakeGimbal()  # 指令回声 = 模拟执行
        _active_tracking(mw, monkeypatch, _pos(123.0, 30.0), g)
        mw._on_sky_gimbal_follow_toggled(True)
        mw._poll_satellite_track()
        text = mw.gimbal_accuracy_text()
        assert "不可报" in text and "模拟执行" in text
        assert "0.00°" not in text, "不可报不得伪装成精度 0"
        # 停机时同一句话既进状态栏，也进云台按钮 tooltip（用户可见处）
        mw._stop_gimbal_follow("测试停止")
        msg = mw.statusBar().currentMessage()
        assert "指向精度：不可报" in msg and "模拟执行" in msg
        assert "0.00°" not in msg
        tip = mw.sky_view._gimbal_btn.toolTip()
        assert "指向精度：不可报" in tip and "模拟执行" in tip

    def test_real_readback_ui_shows_measured_accuracy(self, mw, monkeypatch):
        g = FakeGimbal(pose_source="rotctld", pose_offset=(2.0, 0.0))
        _active_tracking(mw, monkeypatch, _pos(123.0, 30.0), g)
        mw._on_sky_gimbal_follow_toggled(True)
        mw._poll_satellite_track()
        text = mw.gimbal_accuracy_text()
        assert "机械回读 1 样本" in text and "不可报" not in text
        assert "max" in text and "mean" in text

    def test_stop_failure_reason_in_status_and_throttled_warning(
            self, mw, monkeypatch, caplog):
        """gc.stop() 抛异常：状态栏带原因；30s 内同 key 只落一条 warning。"""
        import main_window as mwmod
        mwmod._WARN_THROTTLE_LAST.clear()
        g = FakeGimbal()

        def _boom():
            raise RuntimeError("电机还在转")

        g.stop = _boom
        _active_tracking(mw, monkeypatch, _pos(123.0, 30.0), g)
        mw._on_sky_gimbal_follow_toggled(True)
        with caplog.at_level(logging.WARNING):
            mw._stop_gimbal_follow("测试停止", important=True)
            mw._stop_gimbal_follow("测试停止", important=True)
        msg = mw.statusBar().currentMessage()
        assert "云台停机指令未确认" in msg and "电机还在转" in msg
        hits = [r for r in caplog.records
                if "云台停机指令未确认" in r.getMessage()]
        assert len(hits) == 1, "30s 限流内同一 key 只能落一条 warning"

    def test_dispatch_exception_reaches_cycle_and_status(
            self, mw, monkeypatch, caplog):
        """云台指令抛异常：周期记录带 error，状态栏明确停止。"""
        g = FakeGimbal(reply="raise")
        _active_tracking(mw, monkeypatch, _pos(123.0, 30.0), g)
        mw._on_sky_gimbal_follow_toggled(True)
        with caplog.at_level(logging.WARNING):
            mw._poll_satellite_track()
        cyc = mw.last_gimbal_cycle()
        assert cyc["dispatched"] is False
        assert "驱动串口崩了" in (cyc.get("error") or "")
        msg = mw.statusBar().currentMessage()
        assert "云台指令未执行" in msg
        assert "驱动串口崩了" in msg
        assert "跟随已停止" in msg
        assert mw._gimbal_follow is False
        n = len(g.pointed)
        mw._poll_satellite_track()
        assert len(g.pointed) == n, "异常停机后不得再次下发"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
