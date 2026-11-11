# SPDX-License-Identifier: MIT
"""
tests/test_gimbal_rotctld_sim.py
================================
用「可注入的 rotctld 模拟服务器」把 ``command_vs_truth`` 从「有字段」变成「有数字」，
并锁死三件事：

1. 模拟后端下的姿态误差**永不**被算成机械精度（``pose_*`` 为 None、只进
   ``pose_error_by_source.simulated``），UI 不得出现任何度数；
2. 延迟越大、指令角到回读角的滞后越大（50/200/500ms 三档，单调）；
3. 丢指令 = 明确停止：状态栏带原因、本拍记 ``error``、之后不再下发。

模拟 ≠ 真机：服务端只有「理想电机 + 传输延迟 + 量化 + 抖动」，没有齿隙、
风力、电机加减速曲线；这些数字只能证明指令链，不能证明机械跟随精度。
"""
from __future__ import annotations

import math
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))
sys.path.insert(0, os.path.join(_ROOT, "scripts", "hw_acceptance"))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from rotctld_sim import SimRotctldServer, run_simulated_pass  # noqa: E402

QUANTIZE_DEG = 0.1          # 模拟服务器量化步进；也是滞后角允许的 ±1 档抖动


# ---------------------------------------------------------------------------
# 数据面：误差分布按来源分栏
# ---------------------------------------------------------------------------
class TestSimulatedPassMetrics:
    def test_numbers_exist_per_source_and_never_claim_mechanical_accuracy(self):
        out = run_simulated_pass(latency_s=0.05, steps=12)
        assert out["backend"] == "rotctld_sim"
        assert out["commands_sent"] == 12 and out["dropped"] == 0
        summ = out["pose_source_summary"]
        assert summ["readback_samples"] == 0
        assert summ["simulated_samples"] == 12
        assert summ["mechanical_accuracy_claimable"] is False
        # 分栏：只出现 simulated 桶，readback 桶不凭空造数
        assert "readback" not in out["pose_error_by_source"]
        sim = out["pose_error_by_source"]["simulated"]
        for key in ("max_deg", "mean_deg", "rms_deg"):
            assert isinstance(sim[key], float) and sim[key] >= 0.0
        cmd = out["cmd_vs_truth"]
        assert cmd["samples"] == 12
        assert isinstance(cmd["max_deg"], float) and isinstance(cmd["rms_deg"], float)
        assert out["lag_cmd_vs_pose"]["mean_deg"] > 0.0

    def test_pose_stats_are_none_not_zero_under_simulation(self):
        """模拟过境：``pose_*`` 必须 None（不可报），不是 0（看起来完美）。"""
        out = run_simulated_pass(latency_s=0.05, steps=8)
        assert out["pose_stats"]["samples"] == 0
        assert out["pose_stats"]["max_deg"] is None
        assert out["pose_stats"]["mean_deg"] is None
        assert out["pose_stats"]["rms_deg"] is None
        # 同时 simulated 桶确实有数——证明「有数字」与「能报精度」是两件事
        assert out["pose_error_by_source"]["simulated"]["samples"] == 8

    def test_jitter_widens_distribution_but_stays_out_of_readback(self):
        base = run_simulated_pass(latency_s=0.05, steps=12, jitter_deg=0.0)
        noisy = run_simulated_pass(latency_s=0.05, steps=12, jitter_deg=0.6)
        assert noisy["pose_error_by_source"]["simulated"]["max_deg"] \
            > base["pose_error_by_source"]["simulated"]["max_deg"]
        assert noisy["pose_source_summary"]["mechanical_accuracy_claimable"] is False


class TestLatencyLag:
    """延迟 → 滞后角单调。±1 个量化步（0.1°）内允许抖动，实测无抖动。"""

    def test_500ms_lags_more_than_50ms(self):
        lags = {}
        for d in (0.05, 0.2, 0.5):
            out = run_simulated_pass(latency_s=d, steps=20)
            lags[d] = out["lag_cmd_vs_pose"]["mean_deg"]
        assert lags[0.5] > lags[0.05] + QUANTIZE_DEG, (
            f"500ms 滞后 {lags[0.5]:.3f}° 应显著大于 50ms 的 {lags[0.05]:.3f}°")
        assert lags[0.2] > lags[0.05] - QUANTIZE_DEG
        assert lags[0.5] > lags[0.2] - QUANTIZE_DEG
        for d, v in lags.items():
            assert math.isfinite(v) and v > 0.0
        # 记录实测，便于回归时对比（不是断言）
        print(f"模拟滞后角：50ms={lags[0.05]:.3f}° 200ms={lags[0.2]:.3f}° "
              f"500ms={lags[0.5]:.3f}°")


# ---------------------------------------------------------------------------
# 主窗口：丢指令停机 + UI 永不出现度数
# ---------------------------------------------------------------------------
def _pos(az=123.0, el=30.0):
    return {"valid": True, "azimuth": az, "elevation": el,
            "doppler_hz": 0.0, "corrected_freq_hz": 137.1e6,
            "range_km": 1050.0}


def _prep_tracking(mw, monkeypatch, pos):
    from PySide6.QtCore import QTimer
    t = QTimer(mw)
    t.setInterval(500)
    t.start()
    monkeypatch.setattr(mw, "_sat_track_timer", t)
    monkeypatch.setattr(mw, "_on_tune_sdr", lambda *a, **k: None)
    monkeypatch.setattr(mw, "_loop_sat_tracker", SimpleNamespace(
        current_position=lambda: dict(pos),
        downlink_freq_hz=lambda: 137.1e6,
        selected_name="NOAA 19"))
    mw._observer_lat, mw._observer_lon = 39.9, 116.4
    mw._observer_from_gnss = False
    if getattr(mw, "_gimbal_error", None) is not None:
        mw._gimbal_error.reset()      # module 级夹具，避免上一用例样本串味
    mw._last_gimbal_cycle = None


def _sim_controller(server):
    from mbdsdr_ai.gimbal import GimbalController
    gc = GimbalController()
    gc.use_rotctld("127.0.0.1", server.port, simulated=True)
    return gc


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


class TestUserVisibleUnderSimulation:
    def test_ui_never_shows_degrees(self, mw, monkeypatch):
        server = SimRotctldServer(latency_s=0.05, quantize_deg=QUANTIZE_DEG,
                                  initial_az=123.0, initial_el=30.0).start()
        try:
            gc = _sim_controller(server)
            monkeypatch.setattr(mw, "_gimbal_controller", lambda: gc)
            _prep_tracking(mw, monkeypatch, _pos())
            mw._on_sky_gimbal_follow_toggled(True)
            mw._poll_satellite_track()
            mw._gimbal_limiter._last_time -= 2.0
            mw._poll_satellite_track()
            text = mw.gimbal_accuracy_text()
            assert "不可报" in text
            assert "°" not in text, f"模拟下不得出现任何度数：{text!r}"
            status = mw.statusBar().currentMessage()
            assert "不可报" in status and "°" not in status.split("指向精度", 1)[1]
            sv = getattr(mw, "sky_view", None)
            tip = sv._gimbal_btn.toolTip() if sv is not None else ""
            assert "不可报" in tip and "°" not in tip.split("指向精度", 1)[1]
        finally:
            server.stop()
            mw._on_sky_gimbal_follow_toggled(False)

    def test_dropped_command_stops_with_reason_and_never_redispatch(
            self, mw, monkeypatch):
        # 每第 2 条运动指令返回 RPRT 20（Hamlib 未执行）
        server = SimRotctldServer(latency_s=0.0, quantize_deg=QUANTIZE_DEG,
                                  drop_every=2, initial_az=123.0,
                                  initial_el=30.0).start()
        try:
            gc = _sim_controller(server)
            monkeypatch.setattr(mw, "_gimbal_controller", lambda: gc)
            _prep_tracking(mw, monkeypatch, _pos())
            mw._on_sky_gimbal_follow_toggled(True)
            mw._poll_satellite_track()          # 第 1 条：正常下发
            assert server.counters["P"] == 1
            # 目标前移 2°（越过死区）+ 回拨整形器时间基线 → 第 2 拍必须下发
            mw._loop_sat_tracker.current_position = lambda: _pos(125.0, 30.0)
            mw._gimbal_limiter._last_time -= 2.0
            mw._poll_satellite_track()          # 第 2 条：被丢弃 → 立即停机
            assert server.counters["P"] == 2
            cycle = mw.last_gimbal_cycle()
            assert cycle["dispatched"] is False
            assert "RPRT" in (cycle["error"] or "")
            assert mw._gimbal_follow is False
            msg = mw.statusBar().currentMessage()
            assert "云台指令未执行" in msg
            assert "跟随已停止" in msg
            assert "RPRT" in msg, f"状态栏必须带原因：{msg!r}"
            # 停止后不再下发任何角度
            mw._gimbal_limiter._last_time -= 2.0
            mw._poll_satellite_track()
            assert server.counters["P"] == 2, "停机后仍在向云台下发指令"
        finally:
            server.stop()
            mw._on_sky_gimbal_follow_toggled(False)
