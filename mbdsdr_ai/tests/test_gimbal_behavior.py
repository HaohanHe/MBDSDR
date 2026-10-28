# SPDX-License-Identifier: MIT
"""
gimbal.py（云台/指向控制）行为测试
====================================

零硬件：board 走**设计内的可注入 send_callable**（模块自带注入缝，非 mock
硬件结果）；rotctld 用本机真实 TCP 回环 + 脚本化应答（真实 socket 真实协议
字节，测的是客户端解析，不是伪造硬件成功）。

重点钉死错误诚实语义：
  - RPRT 错误码含 "0"（如 RPRT 20）不得假成功（旧 `any("0" in ln)` 之坑）；
  - 无姿态回读时 (0,0) 只是占位，不得报"已对准"（假成功）；
  - stop() 的负值哨兵不得被 %360/clamp 改写成移动指令；
  - sweep_azimuth(step<=0) 必须报错而不是死循环/空转。

运行：python -m pytest mbdsdr_ai/tests/test_gimbal_behavior.py -v
"""
from __future__ import annotations

import socket
import threading

import pytest

from mbdsdr_ai.gimbal import (
    BoardGimbalClient,
    GimbalController,
    GimbalMode,
    GimbalPose,
    GimbalStatus,
    RotctldClient,
    angle_to_servo_us,
    az_el_to_rotator_angles,
    clamp_angle,
)


# ── 1. 纯几何：角度误差环绕 + 对准容差 ─────────────────────────
def test_angular_error_wraps_shortest_way():
    cur = GimbalPose(azimuth=350.0, elevation=10.0)
    az_err, el_err = cur.angular_error(GimbalPose(azimuth=10.0, elevation=25.0))
    assert az_err == pytest.approx(20.0)   # 350→10 走 +20 而不是 -340
    assert el_err == pytest.approx(15.0)

    az_err2, _ = GimbalPose(azimuth=10.0).angular_error(GimbalPose(azimuth=350.0))
    assert az_err2 == pytest.approx(-20.0)


def test_aligned_is_tolerance_inclusive():
    cur = GimbalPose(azimuth=100.0, elevation=30.0)
    assert cur.aligned(GimbalPose(azimuth=105.0, elevation=35.0),
                       az_tolerance=5.0, el_tolerance=5.0) is True
    assert cur.aligned(GimbalPose(azimuth=105.1, elevation=35.0),
                       az_tolerance=5.0, el_tolerance=5.0) is False


# ── 2. 舵机换算：线性 + 钳位 ───────────────────────────────────
def test_angle_to_servo_us_linear_and_clamped():
    assert angle_to_servo_us(0.0) == pytest.approx(500.0)
    assert angle_to_servo_us(90.0) == pytest.approx(1500.0)
    assert angle_to_servo_us(180.0) == pytest.approx(2500.0)
    # 超限钳位，不得越界输出脉宽
    assert angle_to_servo_us(-30.0) == pytest.approx(500.0)
    assert angle_to_servo_us(270.0) == pytest.approx(2500.0)


def test_clamp_angle_and_rotator_passthrough():
    assert clamp_angle(-5.0, 0.0, 90.0) == 0.0
    assert clamp_angle(120.0, 0.0, 90.0) == 90.0
    az, el = az_el_to_rotator_angles(370.0, -5.0)  # 普通座架：透传+钳位
    assert az == pytest.approx(10.0)
    assert el == pytest.approx(0.0)
    az2, el2 = az_el_to_rotator_angles(180.0, 45.0, mount_tilt_deg=30.0)
    assert 0.0 <= az2 < 360.0
    assert 0.0 <= el2 <= 90.0


# ── 3. board 客户端：注入缝协议 + 未连接诚实空态 ────────────────
def test_board_without_channel_is_honest_failure():
    b = BoardGimbalClient()
    r = b.set_position(10.0, 20.0)
    assert r["ok"] is False
    assert "error" in r
    assert b.get_position() is None, "无通道不得伪造姿态"


def test_board_set_position_normalizes_and_stop_sentinel_passes_through():
    sent = []

    def fake_send(method, params):
        sent.append((method, params))
        return {"ok": True}

    b = BoardGimbalClient(send_callable=fake_send)
    r = b.set_position(370.0, 120.0)
    assert r == {"ok": True}
    method, params = sent[-1]
    assert method == "gimbal_set"
    assert params["az"] == pytest.approx(10.0)   # 370 % 360
    assert params["el"] == pytest.approx(90.0)   # 钳位到 90

    # 停机哨兵（固件约定负值=停）：不得被 %360/clamp 改写成 (359, 0)
    b.set_position(-1, -1)
    _, stop_params = sent[-1]
    assert stop_params["az"] == -1.0
    assert stop_params["el"] == -1.0


def test_board_get_position_passes_through_failure():
    b = BoardGimbalClient(send_callable=lambda m, p: {"ok": False, "error": "x"})
    assert b.get_position() is None
    ok_b = BoardGimbalClient(
        send_callable=lambda m, p: {"ok": True, "az": 12.5, "el": 34.5})
    pose = ok_b.get_position()
    assert pose == GimbalPose(azimuth=12.5, elevation=34.5)


# ── 4. rotctld：RPRT 错误码必须严格解析（含 "0" 的错误码不假成功）─
def _serve_once(reply_bytes: bytes):
    """真实本机 TCP 回环：接受一个连接、回一段脚本化应答后关闭。"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def _run():
        try:
            conn, _ = srv.accept()
            with conn:
                conn.recv(256)
                conn.sendall(reply_bytes)
        finally:
            srv.close()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return port, t


def test_rotctld_strict_rprt_parsing():
    # 纯解析：错误码 20 / -11 含字符 "0" 或不含都必须判失败
    assert RotctldClient._reply_ok(["RPRT 0"]) is True
    assert RotctldClient._reply_ok(["RPRT 20"]) is False
    assert RotctldClient._reply_ok(["RPRT -11"]) is False
    assert RotctldClient._reply_ok(["RPRT 10"]) is False
    assert RotctldClient._reply_ok([]) is False
    assert RotctldClient._reply_ok(["garbage"]) is False


def test_rotctld_set_position_false_success_on_error_code():
    """RPRT 20（错误码含 '0'）在旧实现下假成功，必须如实失败。"""
    port, t = _serve_once(b"RPRT 20\n")
    c = RotctldClient(host="127.0.0.1", port=port, timeout=2.0)
    ok = c.set_position(100.0, 30.0)
    t.join(timeout=2.0)
    assert ok is False, "RPRT 20 是失败码，不得报成功"


def test_rotctld_set_position_success_and_get_position():
    port, t = _serve_once(b"RPRT 0\n")
    c = RotctldClient(host="127.0.0.1", port=port, timeout=2.0)
    assert c.set_position(100.0, 30.0) is True
    t.join(timeout=2.0)

    port2, t2 = _serve_once(b"123.50\n45.25\n")
    c2 = RotctldClient(host="127.0.0.1", port=port2, timeout=2.0)
    pose = c2.get_position()
    t2.join(timeout=2.0)
    assert pose.azimuth == pytest.approx(123.5)
    assert pose.elevation == pytest.approx(45.25)


def test_rotctld_ping_false_when_no_server():
    # 端口 1 无监听：ping 必须如实 False（不得假 True）
    c = RotctldClient(host="127.0.0.1", port=1, timeout=0.3)
    assert c.ping() is False


# ── 5. 控制器：模式诚实 + 无回读不得假"已对准" ─────────────────
def test_point_to_offline_is_honest_not_dispatched():
    gc = GimbalController()
    r = gc.point_to(90.0, 30.0)
    assert r["backend"] == "offline"
    assert r["dispatched"] is False, "离线模式不得假装已下发"
    assert r["aligned"] is False


def test_point_to_without_pose_feedback_cannot_claim_aligned():
    """目标就是 (0,0) 且无任何回读：占位姿态恰好与目标重合，仍不得报已对准。"""
    gc = GimbalController()
    r = gc.point_to(0.0, 0.0)
    assert r["aligned"] is False
    assert r["pose_valid"] is False
    assert "无法判定" in r["guidance"]


def test_point_to_with_pose_provider_is_closed_loop():
    gc = GimbalController()
    gc.set_pose_provider(lambda: GimbalPose(azimuth=88.0, elevation=29.0))
    r = gc.point_to(90.0, 30.0)
    assert r["pose_valid"] is True
    assert r["aligned"] is True           # 偏差 2°/1° 在 5° 容差内
    assert r["az_error_deg"] == pytest.approx(2.0, abs=0.1)
    assert "对准" in r["guidance"]


def test_point_to_manual_backend_is_manual_not_dispatched():
    gc = GimbalController()
    gc.use_manual()
    gc.set_pose_provider(lambda: GimbalPose(azimuth=0.0, elevation=0.0))
    r = gc.point_to(90.0, 0.0)
    assert r["backend"] == "manual"
    assert r["dispatched"] is False
    assert "顺时针" in r["guidance"], r["guidance"]


def test_board_stop_uses_negative_sentinel():
    sent = []
    gc = GimbalController()
    gc.use_board(send_callable=lambda m, p: sent.append((m, p)) or {"ok": True})
    r = gc.stop()
    assert r == {"ok": True, "backend": "board_pwm"}
    _, params = sent[-1]
    assert params["az"] == -1.0 and params["el"] == -1.0


# ── 6. sweep_azimuth：坏输入报错、诚实空态 ─────────────────────
def test_sweep_rejects_nonpositive_step():
    gc = GimbalController()
    with pytest.raises(ValueError):
        gc.sweep_azimuth(az_start=0.0, az_end=360.0, step=0.0)
    with pytest.raises(ValueError):
        gc.sweep_azimuth(az_start=0.0, az_end=360.0, step=-30.0)


def test_sweep_manual_mode_yields_guidance_without_rssi():
    gc = GimbalController()
    gc.use_manual()
    gc.set_pose_provider(lambda: GimbalPose(azimuth=0.0, elevation=20.0))
    r = gc.sweep_azimuth(az_start=0.0, az_end=90.0, step=30.0,
                         elevation=20.0, settle_sec=0.0)
    assert r["manual"] is True
    assert [s["az"] for s in r["samples"]] == [0.0, 30.0, 60.0]
    assert all(s["rssi"] is None for s in r["samples"])
    assert all(s["guidance"] for s in r["samples"])
    assert r["peak_az"] is None, "无 RSSI 数据不得编造峰值方位"


def test_sweep_auto_mode_peak_az_from_rssi_provider():
    gc = GimbalController()
    # 模拟在 az=60 处信号最强
    rssi_by_az = {0.0: -95.0, 30.0: -80.0, 60.0: -45.0, 90.0: -70.0}
    gc.set_pose_provider(lambda: GimbalPose(azimuth=0.0, elevation=20.0))
    r = gc.sweep_azimuth(az_start=0.0, az_end=120.0, step=30.0,
                         elevation=20.0, settle_sec=0.0,
                         rssi_provider=lambda: -50.0)
    assert r["manual"] is False
    assert r["peak_az"] is not None
    assert len(r["samples"]) == 4

    gc2 = GimbalController()
    gc2.set_pose_provider(lambda: GimbalPose(azimuth=0.0, elevation=20.0))
    idx = {"i": 0}
    vals = [-95.0, -80.0, -45.0, -70.0]

    def provider():
        v = vals[idx["i"]]
        idx["i"] += 1
        return v

    r2 = gc2.sweep_azimuth(az_start=0.0, az_end=120.0, step=30.0,
                           elevation=20.0, settle_sec=0.0,
                           rssi_provider=provider)
    assert r2["peak_az"] == pytest.approx(60.0)
    assert r2["samples"][2]["rssi"] == pytest.approx(-45.0)


def test_sweep_empty_range_is_honest_empty():
    gc = GimbalController()
    r = gc.sweep_azimuth(az_start=10.0, az_end=10.0, step=30.0,
                         settle_sec=0.0)
    assert r["samples"] == []
    assert r["peak_az"] is None
