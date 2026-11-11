# SPDX-License-Identifier: MIT
"""
tests/test_gimbal_control_robustness.py
=======================================
云台控制面健壮性与防呆（全部用假后端，不接真机）。

钉住四组行为：
  1. 指令排队与超时：后端永不返回时 GUI 线程不得被阻塞（每拍 < 0.5s）；
     超过 GIMBAL_DISPATCH_TIMEOUT_S 必须判超时、停机，状态栏带原因。
     停机指令同样有界等待，慢/挂死后端不得冻住「停止跟随」按钮。
  2. 限位与越界：越界夹取必须**显式**（clamped + `已夹到 X°`/`已归一到 X°`），
     不许静默夹；main_window 必须把这条 note 呈现给用户。
  3. 连点与重复指令：连开两次不得起两路跟踪；在途指令期间本拍不叠加新指令；
     关一次即停，旧目标不再收到角度。
  4. 掉线恢复：跟踪中断连 → 明确「跟随已停止」且不再下发；恢复后**不自动**
     重新对准（必须用户显式重开）。
"""
from __future__ import annotations

import os
import sys
import time
import threading
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


# ---------------------------------------------------------------------------
# 假后端
# ---------------------------------------------------------------------------
class HangGimbal:
    """点指令/停机都挂住的假后端：用于验证「GUI 线程不许被云台阻塞」。

    ``release()`` 放行，让后台线程尽快收尾（测试不留下永活的等待线程）。
    """

    def __init__(self, timeout_s: float = 5.0):
        self.status = SimpleNamespace(
            mode=SimpleNamespace(value="board_pwm"), connected=True,
            backend_info="hang board")
        self.board = SimpleNamespace(_send=lambda m, p: {"ok": True})
        self.gate = threading.Event()
        self.wait_s = timeout_s
        self.calls = 0
        self.stops = 0

    def point_to_satellite(self, az, el):
        self.calls += 1
        self.gate.wait(self.wait_s)
        return {"dispatched": True, "pose_valid": False}

    def stop(self):
        self.stops += 1
        self.gate.wait(self.wait_s)
        return {"ok": True}

    def use_board(self):
        return self.status

    def release(self):
        self.gate.set()


class FakeGimbal:
    """即时返回的理想后端（模拟执行 = 指令回声）。"""

    def __init__(self, note: str = ""):
        self.status = SimpleNamespace(
            mode=SimpleNamespace(value="board_pwm"), connected=True,
            backend_info="fake board")
        self.board = SimpleNamespace(_send=lambda m, p: {"ok": True})
        self.pointed = []
        self.stopped = 0
        self.note = note

    def point_to_satellite(self, az, el):
        self.pointed.append((az, el))
        return {"dispatched": True, "pose_valid": True,
                "current_az": az, "current_el": el,
                "pose_source": "simulated", "note": self.note}

    def stop(self):
        self.stopped += 1
        return {"ok": True}

    def use_board(self):
        return self.status


# ---------------------------------------------------------------------------
# 主窗口夹具（与 test_gimbal_track_chain.py 同口径）
# ---------------------------------------------------------------------------
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
    except Exception as exc:  # 收尾失败不掩盖主断言，但必须留证
        print(f"模块清理：断连失败 {exc!r}")
    w.close()


@pytest.fixture(autouse=True)
def _clean_gimbal_state(mw):
    """每个用例前后清空在途任务/跟随开关：module 级窗口跨用例复用。"""
    mw._gimbal_follow = False
    mw._drop_gimbal_task()
    if getattr(mw, "_gimbal_error", None) is not None:
        mw._gimbal_error.reset()
    mw._last_gimbal_cycle = None
    yield
    mw._gimbal_follow = False
    mw._drop_gimbal_task()


def _pos(az=123.0, el=30.0):
    # 真实 current_position() 必带 policy；替身漏键会被执行层闸门判为缺数据。
    return {"valid": True, "azimuth": az, "elevation": el,
            "doppler_hz": 0.0, "corrected_freq_hz": 137.1e6,
            "range_km": 1050.0,
            "policy": {"allow_tune": True, "allow_gimbal": True,
                       "label": "星历新鲜", "reason": "TLE 年龄 0.3 天",
                       "next_action": "可直接跟踪"}}


def _active_tracking(mw, monkeypatch, pos, gimbal):
    from PySide6.QtCore import QTimer
    t = QTimer(mw)
    t.setInterval(500)
    t.start()
    monkeypatch.setattr(mw, "_sat_track_timer", t)
    monkeypatch.setattr(mw, "_gimbal_controller", lambda: gimbal)
    monkeypatch.setattr(mw, "_on_tune_sdr", lambda *a, **k: None)
    mw._loop_sat_tracker = SimpleNamespace(
        current_position=lambda: dict(pos),
        downlink_freq_hz=lambda: 137.1e6,
        selected_name="NOAA 19",
        # 云台闸门（P0-01）按「当前跟踪目标」判：替身必须复刻目录接口，
        # 否则被判「星历状态无法判定」而禁用云台（与超时/排队无关）。
        list_satellites=lambda: [{"name": "NOAA 19"}],
        tracking_policy_for=lambda *_a, **_k: {
            "allow_gimbal": True, "label": "星历新鲜",
            "next_action": "可直接跟踪"})
    mw._observer_lat, mw._observer_lon = 39.9, 116.4
    mw._observer_from_gnss = False
    mw._gimbal_error.reset()
    mw._last_gimbal_cycle = None
    return t


# ---------------------------------------------------------------------------
# 1. 指令排队与超时
# ---------------------------------------------------------------------------
class TestDispatchTimeout:
    def test_hang_backend_does_not_block_gui(self, mw, monkeypatch):
        hang = HangGimbal()
        _active_tracking(mw, monkeypatch, _pos(), hang)
        mw._on_sky_gimbal_follow_toggled(True)
        t0 = time.monotonic()
        mw._poll_satellite_track()
        dt = time.monotonic() - t0
        assert dt < 0.5, f"GUI 线程被云台阻塞 {dt:.2f}s（应 < 0.5s）"
        assert mw._gimbal_follow is True, "指令在途不等于失败：仍在跟随"
        assert mw.last_gimbal_cycle() is None, "本拍不应伪造已完成记录"
        msg = mw.statusBar().currentMessage()
        assert "等待应答" in msg and "未阻塞" in msg, msg
        hang.release()

    def test_inflight_command_is_not_stacked(self, mw, monkeypatch):
        """上一条还没回来，本拍不得再下发一条（否则就是两路并发指令）。"""
        hang = HangGimbal()
        _active_tracking(mw, monkeypatch, _pos(), hang)
        mw._on_sky_gimbal_follow_toggled(True)
        mw._poll_satellite_track()
        mw._gimbal_limiter._last_time -= 2.0
        mw._poll_satellite_track()
        assert hang.calls == 1, f"在途期间叠加了指令（实际调用 {hang.calls} 次）"
        hang.release()

    def test_timeout_stops_follow_with_reason(self, mw, monkeypatch):
        import main_window as mwmod
        monkeypatch.setattr(mwmod, "GIMBAL_DISPATCH_GRACE_S", 0.05)
        monkeypatch.setattr(mwmod, "GIMBAL_DISPATCH_TIMEOUT_S", 0.30)
        hang = HangGimbal()
        _active_tracking(mw, monkeypatch, _pos(), hang)
        mw._on_sky_gimbal_follow_toggled(True)
        mw._poll_satellite_track()          # 投递，在途
        time.sleep(0.40)
        mw._poll_satellite_track()          # 收：已超时 → 停机
        assert mw._gimbal_follow is False
        msg = mw.statusBar().currentMessage()
        assert "云台指令未执行" in msg and "超时" in msg and "跟随已停止" in msg, msg
        assert hang.stops >= 1, "超时必须下发停机"
        n = hang.calls
        mw._poll_satellite_track()
        assert hang.calls == n, "停后不得再下发"
        hang.release()

    def test_bounded_stop_with_hanging_backend(self, mw, monkeypatch):
        hang = HangGimbal()
        _active_tracking(mw, monkeypatch, _pos(), hang)
        mw._on_sky_gimbal_follow_toggled(True)
        mw._poll_satellite_track()
        t0 = time.monotonic()
        mw._stop_gimbal_follow("测试停止", important=True)
        dt = time.monotonic() - t0
        assert dt < 0.5, f"停机路径阻塞 {dt:.2f}s（应 < 0.5s）"
        assert mw._gimbal_follow is False
        msg = mw.statusBar().currentMessage()
        assert "测试停止" in msg
        assert "50ms" in msg and "未确认" in msg, "未确认的停机必须如实说明"
        hang.release()


# ---------------------------------------------------------------------------
# 2. 限位与越界
# ---------------------------------------------------------------------------
class TestOutOfRange:
    def test_point_to_clamps_explicitly(self):
        """仰角夹到机械上限、方位环形归一，两者都要有 note（不许静默）。"""
        from mbdsdr_ai.gimbal import GimbalController
        gc = GimbalController()
        gc.use_board(lambda m, p: {"ok": True})
        r = gc.point_to(400.0, 100.0)
        assert r["requested_az"] == 400.0 and r["requested_el"] == 100.0
        assert r["target_el"] == 90.0
        assert r["target_az"] == pytest.approx(40.0)
        assert r["clamped"]["elevation"]["applied"] == 90.0
        assert r["clamped"]["azimuth"]["applied"] == pytest.approx(40.0)
        assert "已夹到 90.0°" in r["note"]
        assert "已归一到 40.0°" in r["note"]

    def test_in_range_command_has_no_clamp_note(self):
        from mbdsdr_ai.gimbal import GimbalController
        gc = GimbalController()
        gc.use_board(lambda m, p: {"ok": True})
        r = gc.point_to(123.4, 30.0)
        assert r["clamped"] is None
        assert r["note"] == ""

    def test_main_window_surfaces_clamp_note(self, mw, monkeypatch):
        note = "仰角 100.0° 超出机械范围 0–90°，已夹到 90.0°"
        g = FakeGimbal(note=note)
        _active_tracking(mw, monkeypatch, _pos(), g)
        mw._on_sky_gimbal_follow_toggled(True)
        mw._poll_satellite_track()
        assert note in mw.statusBar().currentMessage(), "夹取原因必须让用户看见"
        cyc = mw.last_gimbal_cycle()
        assert cyc["note"] == note


# ---------------------------------------------------------------------------
# 3. 连点与重复指令
# ---------------------------------------------------------------------------
class TestRepeatCommands:
    def test_double_toggle_single_path(self, mw, monkeypatch):
        g = FakeGimbal()
        _active_tracking(mw, monkeypatch, _pos(), g)
        mw._on_sky_gimbal_follow_toggled(True)
        mw._on_sky_gimbal_follow_toggled(True)
        assert mw._gimbal_follow is True
        mw._poll_satellite_track()
        assert len(g.pointed) == 1, "连开两次不得产生两路下发"
        # 同目标：死区 + 最小间隔，不得重复下发
        mw._poll_satellite_track()
        assert len(g.pointed) == 1

    def test_toggle_off_really_stops_old_target(self, mw, monkeypatch):
        g = FakeGimbal()
        _active_tracking(mw, monkeypatch, _pos(), g)
        mw._on_sky_gimbal_follow_toggled(True)
        mw._poll_satellite_track()
        n = len(g.pointed)
        mw._on_sky_gimbal_follow_toggled(False)
        assert mw._gimbal_follow is False
        assert g.stopped == 1
        mw._poll_satellite_track()
        assert len(g.pointed) == n, "关掉后旧目标不得再收到角度"


# ---------------------------------------------------------------------------
# 4. 掉线与恢复
# ---------------------------------------------------------------------------
class TestDisconnectRecovery:
    def test_backend_lost_stops_and_reports(self, mw, monkeypatch):
        g = FakeGimbal()
        _active_tracking(mw, monkeypatch, _pos(), g)
        mw._on_sky_gimbal_follow_toggled(True)
        mw._poll_satellite_track()
        n = len(g.pointed)
        monkeypatch.setattr(mw, "_gimbal_available", lambda: False)
        mw._poll_satellite_track()
        assert mw._gimbal_follow is False
        msg = mw.statusBar().currentMessage()
        assert "离线" in msg and "跟随已停止" in msg, msg
        assert len(g.pointed) == n, "断连后不得继续假装跟随"
        assert g.stopped >= 1

    def test_recovery_does_not_auto_realign(self, mw, monkeypatch):
        g = FakeGimbal()
        _active_tracking(mw, monkeypatch, _pos(), g)
        mw._on_sky_gimbal_follow_toggled(True)
        mw._poll_satellite_track()
        monkeypatch.setattr(mw, "_gimbal_available", lambda: False)
        mw._poll_satellite_track()
        n = len(g.pointed)
        monkeypatch.setattr(mw, "_gimbal_available", lambda: True)
        mw._poll_satellite_track()
        assert mw._gimbal_follow is False, "恢复连接不得自动重开跟随"
        assert len(g.pointed) == n, "恢复后不得自动重新下发（要用户显式重开）"

    def test_no_task_thread_leaks_after_stop(self, mw, monkeypatch):
        g = FakeGimbal()
        _active_tracking(mw, monkeypatch, _pos(), g)
        mw._on_sky_gimbal_follow_toggled(True)
        mw._poll_satellite_track()
        mw._stop_gimbal_follow("清理")
        assert mw._gimbal_task_thread is None
        assert mw._gimbal_task is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
