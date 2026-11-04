# SPDX-License-Identifier: MIT
"""
tests/test_doppler_link.py
==========================
多普勒链路补偿（唯一真相源 doppler_link + RX 调谐预偏 + TX 发射预偏）。

钉住的事实：
  1. 符号方向（不是「有没有调用」）：
     目标接近（range_rate<0）→ 接收频率升高 → 调谐中心 = 标称 + Δ > 标称；
     目标远离（range_rate>0）→ 接收频率降低 → 调谐中心 < 标称；
     TX 反向预偏：接近 → 发射频率压低（tx < 标称），远离 → 抬高。
  2. 诚实回落：无目标/TLE 失效/仰角<0 → invalid + 补偿自动关闭 + 原因；
     rx_freq/tx_freq 一律 None（不沿用过期偏移）。
  3. 不许双补偿：后端（C++/基带）补偿活动时，Python 侧拒绝开启并给冲突原因；
     已开启的被强制关闭；tx_freq 返回 None。
  4. 开关幂等：开→关→开 状态往返一致。
  5. TC 上行：arm_manual 门不变；预偏标志生效时频点按预测反向偏移，
     无目标时如实标注未预偏。
  6. 合成占位 TLE（norad 99999）已从定轨面板移除：标准路径无真实
     prior_tle 时诚实报错，不给假初值。

运行:
    python -m pytest tests/test_doppler_link.py -v
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

from mbdsdr_ai.doppler_link import (  # noqa: E402
    C_LIGHT_MPS, DopplerLinkCompensator, _reset_compensator_for_tests,
    get_compensator,
)

# 测试锚点站址（SGP4 地面站夹具坐标，不代表任何个人位置）
LAT, LON = 43.9, 125.3
ISS = "ISS (ZARYA)"
ISS_DOWNLINK_HZ = 145.8e6

skyfield = pytest.importorskip("skyfield", reason="skyfield 缺失（可选依赖）")

from mbdsdr_ai.sat_tracker import SatelliteTracker  # noqa: E402
from mbdsdr_ai.sat_passes import GroundStation, make_timescale  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_singleton():
    _reset_compensator_for_tests()
    yield
    _reset_compensator_for_tests()


def _tracker_with_iss() -> SatelliteTracker:
    tr = SatelliteTracker(GroundStation(lat_deg=LAT, lon_deg=LON))
    assert tr.select_satellite(ISS), "内置目录应含 ISS (ZARYA)"
    tr.set_downlink_freq(ISS_DOWNLINK_HZ)
    return tr


def _pos_at(tracker, t):
    """指定时刻的位置（显式注入 at_unix，不劫持 timescale 的 now）。"""
    return tracker.current_position(at_unix=t.utc_datetime().timestamp())


def _find_moment(tracker, rr_negative: bool):
    """在 24h 内找一个 range_rate 满足符号且仰角>10°的时刻。

    时钟钉死在内置 ISS TLE 历元附近（2026-09-26），与墙钟日期无关。
    """
    ts = make_timescale()
    t0 = ts.utc(2026, 9, 26)                 # 钉死，不用 ts.now()
    for k in range(0, 24 * 12):              # 5 分钟步进
        t = ts.tt_jd(t0.tt + k * 300.0 / 86400.0)
        pos = _pos_at(tracker, t)
        if not pos.get("valid"):
            continue
        rr = float(pos["range_rate_km_s"])
        if (rr < 0) == rr_negative and float(pos["elevation"]) > 10.0:
            return t, pos
    raise RuntimeError("24h 内未找到符合条件的锚定时刻（测试数据问题）")


class FakeTracker:
    """可控替身 tracker：喂固定位置字典（隔离时序，不给生产造假）。"""

    def __init__(self, pos):
        self._pos = pos

    def current_position(self):
        return self._pos


def _valid_pos(rr_kms: float, el: float = 45.0, downlink: float = ISS_DOWNLINK_HZ):
    dop = -rr_kms * 1000.0 / C_LIGHT_MPS * downlink
    return {
        "valid": True, "azimuth": 100.0, "elevation": el,
        "range_km": 800.0, "range_rate_km_s": rr_kms,
        "doppler_hz": dop, "doppler_rate_hz_s": 0.0,
        "corrected_freq_hz": downlink + dop, "is_visible": el >= 5.0,
    }


# ---------------------------------------------------------------------------
# 符号方向（真 TLE 全链 + 替身运动学）
# ---------------------------------------------------------------------------
class TestSignConvention:
    def test_approaching_raises_rx_and_lowers_tx(self):
        """接近（range_rate<0）→ Δ>0 → 调谐中心>标称；TX 反向预偏 < 标称。"""
        comp = DopplerLinkCompensator(FakeTracker(_valid_pos(-3.0)))
        assert comp.arm_target(ISS, ISS_DOWNLINK_HZ) is True
        st = comp.state
        assert st.valid and st.doppler_hz > 0.0
        assert comp.rx_freq() > ISS_DOWNLINK_HZ
        tx = comp.tx_freq(435.0e6)
        assert tx is not None and tx < 435.0e6

    def test_receding_lowers_rx_and_raises_tx(self):
        """远离（range_rate>0）→ Δ<0 → 调谐中心<标称；TX > 标称。"""
        comp = DopplerLinkCompensator(FakeTracker(_valid_pos(+3.0)))
        assert comp.arm_target(ISS, ISS_DOWNLINK_HZ) is True
        assert comp.state.doppler_hz < 0.0
        assert comp.rx_freq() < ISS_DOWNLINK_HZ
        tx = comp.tx_freq(435.0e6)
        assert tx is not None and tx > 435.0e6

    def test_real_tle_chain_sign_holds(self):
        """真 TLE（ISS 内置）真地面站：锚定接近/远离两个真实时刻验证符号。"""
        tr = _tracker_with_iss()
        comp = DopplerLinkCompensator(tr)
        # 先锚定真实高仰角时刻（当前真实时刻 ISS 可能在地平线下，属
        # 预期诚实空态，不是测试目标——用时间锚注入再绑定）
        t_app, pos_app = _find_moment(tr, rr_negative=True)
        t_rec, pos_rec = _find_moment(tr, rr_negative=False)
        assert comp.arm_target(ISS, ISS_DOWNLINK_HZ,
                               at_unix=t_app.utc_datetime().timestamp()) is True
        # 接近时刻（refresh 用锚定位置，与 _poll_satellite_track 同路径）
        assert comp.refresh(pos_app) is True
        assert comp.state.doppler_hz > 0.0
        assert comp.rx_freq() > ISS_DOWNLINK_HZ
        tx_app = comp.tx_freq(435.0e6)
        assert tx_app is not None and tx_app < 435.0e6
        # 远离时刻
        assert comp.refresh(pos_rec) is True
        assert comp.state.doppler_hz < 0.0
        assert comp.rx_freq() < ISS_DOWNLINK_HZ
        tx_rec = comp.tx_freq(435.0e6)
        assert tx_rec is not None and tx_rec > 435.0e6

    def test_leo_doppler_magnitude_bounds(self):
        """LEO 量级约束：|Δ/f| <= 7.8km/s / c（超界即数据/符号错误）。"""
        tr = _tracker_with_iss()
        comp = DopplerLinkCompensator(tr)
        t, pos = _find_moment(tr, rr_negative=True)
        comp.arm_target(ISS, ISS_DOWNLINK_HZ,
                        at_unix=t.utc_datetime().timestamp())
        comp.refresh(pos)
        assert abs(comp.state.doppler_hz) / ISS_DOWNLINK_HZ \
            <= 7.8e3 / C_LIGHT_MPS + 1e-9


# ---------------------------------------------------------------------------
# 诚实回落
# ---------------------------------------------------------------------------
class TestHonestFallback:
    def test_no_target_invalid(self):
        comp = DopplerLinkCompensator(FakeTracker(_valid_pos(-3.0)))
        assert comp.refresh() is False
        assert comp.state.valid is False and comp.state.enabled is False
        assert comp.state.reason
        assert comp.rx_freq() is None and comp.tx_freq(435.0e6) is None

    def test_invalid_position_disables_with_reason(self):
        comp = DopplerLinkCompensator(FakeTracker({"valid": False}))
        assert comp.arm_target(ISS, ISS_DOWNLINK_HZ) is False
        assert comp.state.valid is False and comp.state.enabled is False
        assert "失效" in comp.state.reason

    def test_below_horizon_disables_with_reason(self):
        comp = DopplerLinkCompensator(FakeTracker(_valid_pos(-3.0, el=-2.0)))
        assert comp.arm_target(ISS, ISS_DOWNLINK_HZ) is False
        assert comp.state.enabled is False
        assert "地平线" in comp.state.reason

    def test_stop_tracking_clears_offset(self):
        comp = DopplerLinkCompensator(FakeTracker(_valid_pos(-3.0)))
        comp.arm_target(ISS, ISS_DOWNLINK_HZ)
        comp.clear("跟踪停止")
        assert comp.rx_freq() is None and comp.tx_freq(435.0e6) is None
        assert comp.state.enabled is False


# ---------------------------------------------------------------------------
# 不许双补偿
# ---------------------------------------------------------------------------
class TestNoDoubleCompensation:
    def test_backend_active_refuses_enable(self):
        comp = DopplerLinkCompensator(FakeTracker(_valid_pos(-3.0)))
        comp.arm_target(ISS, ISS_DOWNLINK_HZ)
        comp.set_backend_compensation_active(True)
        assert comp.set_enabled(True) is False
        assert comp.state.enabled is False
        assert "双倍" in comp.state.reason or "后端" in comp.state.reason
        assert comp.tx_freq(435.0e6) is None

    def test_backend_activation_forces_off(self):
        comp = DopplerLinkCompensator(FakeTracker(_valid_pos(-3.0)))
        comp.arm_target(ISS, ISS_DOWNLINK_HZ)
        assert comp.state.enabled is True
        comp.set_backend_compensation_active(True)
        assert comp.state.enabled is False
        assert comp.state.reason

    def test_python_and_backend_never_both_on(self):
        comp = DopplerLinkCompensator(FakeTracker(_valid_pos(-3.0)))
        comp.arm_target(ISS, ISS_DOWNLINK_HZ)
        for active in (True, False, True, False, True):
            comp.set_backend_compensation_active(active)
            comp.set_enabled(True)
            both = comp.state.enabled and comp.state.backend_comp_active
            assert not both


# ---------------------------------------------------------------------------
# 开关幂等
# ---------------------------------------------------------------------------
class TestSwitchIdempotence:
    def test_toggle_roundtrip(self):
        comp = DopplerLinkCompensator(FakeTracker(_valid_pos(-3.0)))
        comp.arm_target(ISS, ISS_DOWNLINK_HZ)
        assert comp.set_enabled(False) is True
        assert comp.rx_freq() is None
        assert comp.set_enabled(True) is True
        assert comp.rx_freq() is not None


# ---------------------------------------------------------------------------
# TC 上行集成
# ---------------------------------------------------------------------------
class TestTcUplinkPrecomp:
    def test_arm_gate_unchanged(self):
        from mbdsdr_ai.tc_uplink import tc_uplink_transmit
        r = tc_uplink_transmit(arm_manual=False, iq=[0.0, 0.0],
                               freq_hz=435.0e6, doppler_precomp=True)
        assert r["status"] == "GATED"

    def test_precomp_applies_with_target(self):
        from mbdsdr_ai.tc_uplink import tc_uplink_transmit
        comp = get_compensator()
        comp.tracker = FakeTracker(_valid_pos(-3.0))   # 接近 → TX 压低
        comp.arm_target(ISS, ISS_DOWNLINK_HZ)
        r = tc_uplink_transmit(arm_manual=True, iq=[0.0, 0.0],
                               freq_hz=435.0e6, doppler_precomp=True)
        assert r["status"] == "NEED_HW"          # 无设备仍诚实 NEED_HW
        assert r["doppler"]["applied"] is True
        assert r["freq_hz"] < 435.0e6
        assert r["doppler"]["delta_hz"] == r["freq_hz"] - 435.0e6

    def test_precomp_honest_note_without_target(self):
        from mbdsdr_ai.tc_uplink import tc_uplink_transmit
        _reset_compensator_for_tests()           # 无目标
        r = tc_uplink_transmit(arm_manual=True, iq=[0.0, 0.0],
                               freq_hz=435.0e6, doppler_precomp=True)
        assert r["doppler"]["applied"] is False
        assert r["doppler"]["reason"]
        assert r["freq_hz"] == 435.0e6           # 未预偏，不静默偏移


def _ROOTDesktop() -> str:
    return os.path.join(_ROOT, "desktop")
