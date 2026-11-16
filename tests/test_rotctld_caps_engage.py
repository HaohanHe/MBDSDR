# SPDX-License-Identifier: MIT
"""W101 rotctld 残项：caps 限位钳位 + 连接≠启用闸 + park 归位契约钉扎。

判据来源：docs/research/2026-11-11-roadmap.md W101（逐条对应）：

* ``\\dump_caps`` 成功时按回报范围钳位；解析失败标 ``caps_unknown`` 并保持
  现状（不静默）——TestCapsClamp 的 garbage/error 两例钉「不静默」；
* ``park()`` 发 ``K``，仅 ``RPRT 0`` 算成功——TestPark 钉命令字与严格解析；
* ``gimbal_park_on_stop`` 打开时停止跟随→归位、关闭时不发——TestPark 的
  S→K 计数序列钉两个方向；
* **连接后默认不自动下发，须显式启用**：未启用时 ``set_position`` 返回
  「未启用」而不是悄悄发 ``P``——TestEngageGate 用服务器计数器钉零流量。

反证（外部插件 D:/mbdsdr/output/w101_draft/probe_w101_counter.py）：
  PROBE_W101_ARM=caps   → normalize 无视 caps → 越界钳位用例必须红；
  PROBE_W101_ARM=engage → set_position 自动启用 → 零流量用例必须红；
  PROBE_W101_ARM=park   → stop() 永不归位 → S→K 用例必须红；
  PROBE_W101_ARM=silent → 对照组，必须全绿。

模拟 ≠ 真机：本文件全部走 rotctld_sim（理想电机模型）。dump_caps 的报文
格式按 Hamlib master tests/dumpcaps_rot.c 逐字复刻；本机无 hamlib 二进制，
``rotctld -m 1`` 真机实测待补（诚实标注，不拿模拟数字冒充）。
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop"),
           os.path.join(_ROOT, "scripts", "hw_acceptance")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mbdsdr_ai import gimbal as gimbal_mod  # noqa: E402
from mbdsdr_ai.gimbal import GimbalController, RotctldClient  # noqa: E402
from rotctld_sim import FakeClock, SimRotctldServer, run_simulated_pass  # noqa: E402


def _client(server) -> RotctldClient:
    return RotctldClient("127.0.0.1", server.port, timeout=2.0)


def _controller(server) -> GimbalController:
    gc = GimbalController()
    gc.use_rotctld("127.0.0.1", server.port, simulated=True)
    return gc


# ---------------------------------------------------------------------------
# 1. 连接≠启用：未启用时 set_position/park 必须拒绝且零流量
# ---------------------------------------------------------------------------
class TestEngageGate:
    def test_fresh_client_set_position_refuses_with_zero_traffic(self):
        srv = SimRotctldServer().start()
        try:
            c = _client(srv)
            assert c.engaged is False, "连接成功不得默认允许下发"
            assert c.set_position(10.0, 20.0) is False
            assert "未启用" in c.last_error
            # 零流量证据：服务器一条 P 都没收到（不是发了被拒）
            assert srv.counters["P"] == 0
            assert srv.commands == []
        finally:
            srv.stop()

    def test_engage_then_dispatch_then_disengage_blocks_again(self):
        srv = SimRotctldServer().start()
        try:
            c = _client(srv)
            assert c.engage() is True
            assert c.set_position(10.0, 20.0) is True
            assert srv.counters["P"] == 1
            c.disengage()
            assert c.engaged is False
            assert c.set_position(30.0, 40.0) is False
            assert "未启用" in c.last_error
            assert srv.counters["P"] == 1, "disengage 后仍在下发"
        finally:
            srv.stop()

    def test_readonly_and_stop_are_not_gated(self):
        """读姿态/停机是安全动作，不需要启用；启用闸只拦运动指令。"""
        srv = SimRotctldServer().start()
        try:
            c = _client(srv)
            pose = c.get_position()          # 未启用也能读
            assert pose.azimuth >= 0.0
            assert srv.counters["p"] == 1
            assert c.stop() is True          # 未启用也能停
            assert srv.counters["S"] == 1
            assert c.ping() is True
        finally:
            srv.stop()

    def test_park_is_gated_too(self):
        srv = SimRotctldServer().start()
        try:
            c = _client(srv)
            assert c.park() is False
            assert "未启用" in c.last_error
            assert srv.counters["K"] == 0
        finally:
            srv.stop()

    def test_controller_connect_does_not_auto_engage(self):
        srv = SimRotctldServer().start()
        try:
            gc = _controller(srv)
            assert gc.status.connected is True
            assert gc.status.engaged is False
            # 连接时 best-effort 查 caps（只读），但绝不自动下发运动
            assert srv.counters["caps"] >= 1
            assert srv.counters["P"] == 0
            r = gc.point_to(10.0, 20.0)
            assert r["dispatched"] is False
            assert "未启用" in r["error"]
            assert srv.counters["P"] == 0
            assert gc.engage_rotctld() is True
            assert gc.status.engaged is True
            r = gc.point_to(10.0, 20.0)
            assert r["dispatched"] is True
            assert srv.counters["P"] == 1
            gc.disengage_rotctld()
            assert gc.status.engaged is False
            r = gc.point_to(10.0, 20.0)
            assert r["dispatched"] is False and "未启用" in r["error"]
        finally:
            srv.stop()

    def test_engage_requires_connected_rotctld(self):
        gc = GimbalController()              # OFFLINE
        assert gc.engage_rotctld() is False
        assert gc.status.engaged is False
        # 端口 1 无监听：连接失败 → 不得假装已启用
        gc.use_rotctld("127.0.0.1", 1)
        assert gc.status.connected is False
        assert gc.engage_rotctld() is False
        assert gc.status.engaged is False


# ---------------------------------------------------------------------------
# 2. caps 限位：成功按回报钳位；失败标 unknown 且不静默、保持默认钳位
# ---------------------------------------------------------------------------
class TestCapsClamp:
    def test_dump_caps_ok_clamps_to_reported_limits(self):
        srv = SimRotctldServer(min_az=0.0, max_az=180.0,
                               min_el=-10.0, max_el=90.0).start()
        try:
            c = _client(srv)
            assert c.dump_caps() is True
            assert c.caps_state == "ok"
            assert c.caps == {"min_az": 0.0, "max_az": 180.0,
                              "min_el": -10.0, "max_el": 90.0,
                              "can_park": True}
            assert c.can_park is True
            c.engage()
            # 方位 span=180<360：不环绕，直接夹到上限（270→180，不是 270%360）
            assert c.set_position(270.0, 50.0) is True
            assert srv.commands[-1]["az"] == pytest.approx(180.0)
            assert srv.commands[-1]["el"] == pytest.approx(50.0)
            assert (c.last_clamped.get("clamped") or {}).get("azimuth"), "越界必须显式记录，不静默"
            # 仰角下限 -10：-20 → -10
            assert c.set_position(100.0, -20.0) is True
            assert srv.commands[-1]["el"] == pytest.approx(-10.0)
            assert (c.last_clamped.get("clamped") or {}).get("elevation")
        finally:
            srv.stop()

    def test_normalize_returns_applied_and_clamped(self):
        srv = SimRotctldServer(min_az=0.0, max_az=180.0,
                               min_el=-10.0, max_el=90.0).start()
        try:
            c = _client(srv)
            c.dump_caps()
            naz, nel, clamped = c.normalize(270.0, -20.0)
            assert naz == pytest.approx(180.0)
            assert nel == pytest.approx(-10.0)
            assert "azimuth" in clamped and "elevation" in clamped
            # 范围内原样通过，clamped 为空
            naz, nel, clamped = c.normalize(90.0, 30.0)
            assert naz == pytest.approx(90.0) and nel == pytest.approx(30.0)
            assert clamped == {}
        finally:
            srv.stop()

    def test_full_span_caps_keep_wrap_behavior(self):
        """默认 0–360 全周：方位环绕归一（400→40），与 HEAD 行为一致。"""
        srv = SimRotctldServer().start()
        try:
            c = _client(srv)
            assert c.dump_caps() is True
            c.engage()
            assert c.set_position(400.0, 95.0) is True
            assert srv.commands[-1]["az"] == pytest.approx(40.0)
            assert srv.commands[-1]["el"] == pytest.approx(90.0)
        finally:
            srv.stop()

    @pytest.mark.parametrize("mode", ["garbage", "error"])
    def test_caps_failure_is_unknown_not_silent_and_keeps_default_clamp(self, mode):
        srv = SimRotctldServer(caps_mode=mode).start()
        try:
            c = _client(srv)
            assert c.dump_caps() is False
            assert c.caps_state == "unknown"
            assert c.last_error, "解析失败必须留原因（不静默）"
            assert c.can_park is None      # 未知 ≠ 不支持，也 ≠ 支持
            c.engage()
            # 保持现状：默认机械范围钳位（az%360 / el 0–90），不是放行越界
            assert c.set_position(400.0, 95.0) is True
            assert srv.commands[-1]["az"] == pytest.approx(40.0)
            assert srv.commands[-1]["el"] == pytest.approx(90.0)
        finally:
            srv.stop()

    def test_can_park_false_refuses_park_without_sending_K(self):
        srv = SimRotctldServer(can_park=False).start()
        try:
            c = _client(srv)
            assert c.dump_caps() is True
            assert c.can_park is False
            c.engage()
            assert c.park() is False
            assert "park" in c.last_error.lower()
            assert srv.counters["K"] == 0, "不支持 park 却发了 K"
        finally:
            srv.stop()

    def test_controller_point_to_uses_caps_and_reports_clamp(self):
        srv = SimRotctldServer(min_az=0.0, max_az=180.0,
                               min_el=-10.0, max_el=90.0).start()
        try:
            gc = _controller(srv)
            gc.engage_rotctld()
            r = gc.point_to(270.0, 50.0)
            assert r["dispatched"] is True
            assert r["target_az"] == pytest.approx(180.0)
            assert r["clamped"] and "azimuth" in r["clamped"]
            assert r["note"], "越界必须显式告知，不静默"
            assert srv.commands[-1]["az"] == pytest.approx(180.0)
        finally:
            srv.stop()


# ---------------------------------------------------------------------------
# 3. park：K + 严格 RPRT；park_on_stop 的 S→K 序列与配置读取
# ---------------------------------------------------------------------------
class TestPark:
    def test_park_sends_K_and_moves_to_park_pose(self):
        clk = FakeClock()
        srv = SimRotctldServer(park_az=45.0, park_el=10.0, rate_deg_s=360.0,
                               quantize_deg=0.0, clock=clk).start()
        try:
            c = _client(srv)
            c.dump_caps()
            c.engage()
            assert c.park() is True
            assert srv.counters["K"] == 1
            clk.advance(2.0)               # 电机模型按指令时间线收敛
            pose = c.get_position()
            assert pose.azimuth == pytest.approx(45.0, abs=0.05)
            assert pose.elevation == pytest.approx(10.0, abs=0.05)
        finally:
            srv.stop()

    def test_park_error_code_is_failure_not_silent_success(self):
        srv = SimRotctldServer(park_rprt=20).start()
        try:
            c = _client(srv)
            c.dump_caps()
            c.engage()
            assert c.park() is False, "RPRT 20 是失败码，不得报成功"
            assert srv.counters["K"] == 1  # 命令发了，但对端拒绝
        finally:
            srv.stop()

    def test_stop_with_park_on_stop_sends_S_then_K(self):
        srv = SimRotctldServer().start()
        try:
            gc = _controller(srv)
            gc.engage_rotctld()
            r = gc.stop(park_on_stop=True)
            assert r["ok"] is True
            assert r["parked"] is True
            assert srv.counters["S"] == 1
            assert srv.counters["K"] == 1
        finally:
            srv.stop()

    def test_stop_without_park_on_stop_never_sends_K(self):
        srv = SimRotctldServer().start()
        try:
            gc = _controller(srv)
            gc.engage_rotctld()
            r = gc.stop(park_on_stop=False)
            assert r["ok"] is True
            assert r.get("parked", False) is False
            assert srv.counters["S"] == 1
            assert srv.counters["K"] == 0
        finally:
            srv.stop()

    def test_park_on_stop_none_follows_config_reader(self, monkeypatch):
        srv = SimRotctldServer().start()
        try:
            gc = _controller(srv)
            gc.engage_rotctld()
            # park_on_stop 缺省(None) = 跟配置；消费方在 gimbal 模块内，
            # patch 打消费方自己的绑定
            monkeypatch.setattr(gimbal_mod, "_park_on_stop_from_config",
                                lambda: True)
            r = gc.stop()
            assert r["parked"] is True and srv.counters["K"] == 1
            monkeypatch.setattr(gimbal_mod, "_park_on_stop_from_config",
                                lambda: False)
            r = gc.stop()
            assert r.get("parked", False) is False
            assert srv.counters["K"] == 1, "配置关闭后仍在归位"
        finally:
            srv.stop()

    def test_park_failure_is_reported_not_swallowed(self):
        srv = SimRotctldServer(park_rprt=20).start()
        try:
            gc = _controller(srv)
            gc.engage_rotctld()
            r = gc.stop(park_on_stop=True)
            assert r["ok"] is True          # S 本身成功
            assert r["parked"] is False
            assert r.get("park_error"), "归位失败必须留原因（不静默）"
            assert srv.counters["K"] == 1
        finally:
            srv.stop()

    def test_controller_park_unsupported_backends_are_honest(self):
        gc = GimbalController()
        gc.use_manual()
        r = gc.park()
        assert r["ok"] is False
        assert r.get("error"), "manual 无归位能力，必须明说而不是假成功"


class TestParkOnStopConfig:
    """配置读取器：严格 bool、损坏降级 False、异常不抛。"""

    def test_reader_strict_bool_and_corrupt_degrades(self, tmp_path, monkeypatch):
        import mbdsdr_ai.config as cfg
        p = tmp_path / "config.json"
        monkeypatch.setattr(cfg, "CONFIG_PATH", str(p))
        read = gimbal_mod._park_on_stop_from_config
        assert read() is False              # 文件不存在 → 默认关
        p.write_text(json.dumps({"gimbal_park_on_stop": True}), encoding="utf-8")
        assert read() is True
        p.write_text(json.dumps({"gimbal_park_on_stop": "true"}), encoding="utf-8")
        assert read() is False, "字符串 'true' 不是 bool，不得当真"
        p.write_text(json.dumps({"gimbal_park_on_stop": 1}), encoding="utf-8")
        assert read() is False, "int 1 不是 bool，不得当真"
        p.write_text("{corrupt", encoding="utf-8")
        assert read() is False              # 损坏降级关，不抛异常

    def test_default_config_and_agent_config_roundtrip(self):
        from mbdsdr_ai.config import DEFAULT_CONFIG, AgentConfig
        assert DEFAULT_CONFIG["gimbal_park_on_stop"] is False
        assert AgentConfig().gimbal_park_on_stop is False
        c = AgentConfig.from_dict({"gimbal_park_on_stop": True})
        assert c.gimbal_park_on_stop is True
        assert c.to_dict()["gimbal_park_on_stop"] is True

    def test_schema_internal_key_not_exposed(self):
        from config_schema import ALL_ITEMS
        items = [i for i in ALL_ITEMS if i.key == "gimbal_park_on_stop"]
        assert len(items) == 1
        it = items[0]
        assert it.storage == "ai_config"
        assert it.exposed is False, "W101 不加可见控件（FZ-04）"
        assert it.kind == "bool" and it.default is False
        from settings_panel import SettingsPanel
        # 无前缀键原样落盘（_ai_file_key 只剥 ai_ 前缀）
        assert SettingsPanel._ai_file_key(it) == "gimbal_park_on_stop"


# ---------------------------------------------------------------------------
# 4. 模拟过境驱动：连接后必须显式 engage（否则一条 P 都发不出去）
# ---------------------------------------------------------------------------
class TestSimDriverEngages:
    def test_run_simulated_pass_dispatches_all_steps(self):
        out = run_simulated_pass(latency_s=0.05, steps=6)
        assert out["commands_sent"] == 6
        assert out["stopped"] is False
        assert out["error"] == ""

    def test_driver_without_engage_would_send_nothing(self):
        """反证基线：不 engage 的控制器在模拟服务器上零下发。

        这条钉住「驱动里的 engage 不是装饰」：去掉它，过境立刻停摆。
        """
        srv = SimRotctldServer().start()
        try:
            gc = GimbalController()
            gc.use_rotctld("127.0.0.1", srv.port, simulated=True)
            r = gc.point_to_satellite(60.0, 35.0)
            assert r["dispatched"] is False
            assert srv.counters["P"] == 0
        finally:
            srv.stop()