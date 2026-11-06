# SPDX-License-Identifier: MIT
"""过境预测：内核窗口硬上界 + 60s 粗扫的擦边补点。

两条都是「用户看不出来但结果错」：
1. `hours` 只在桌面 settings 侧钳到 1–72，MCP/agent/CLI 直接调
   `controller.get_satellite_pass` 时内核无上界 → hours=10000 = 60 万次
   SGP4 传播，服务长时间无响应。
2. 60s 粗扫会整段吞掉「峰值刚越门限、时长 <60s」的过境（两端采样点都
   低于门限，峰在中间）。二分/2s 细扫都只对**已发现的区间**生效，救不回来。

用 monkeypatch `_alt_az` 注入解析仰角曲线，精确复现第二条并做反证。
"""
import math
import os
import sys
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mbdsdr_ai import sat_passes as sp  # noqa: E402
from mbdsdr_ai.sat_passes import (  # noqa: E402
    GroundStation, clamp_pass_window_hours, predict_passes,
    PASS_COARSE_STEP_S, PASS_WINDOW_MIN_HOURS, PASS_WINDOW_MAX_HOURS,
)

TLE = ["ISS (ZARYA)",
       "1 25544U 98067A   08264.51780074  .00016553  00000-0  10270-3 0  2897",
       "2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563530"]
STATION = GroundStation(lat_deg=43.9, lon_deg=125.3, alt_m=50.0)
START_UNIX = datetime(2008, 9, 20, 6, 0, 0, tzinfo=timezone.utc).timestamp()


def _inject_curve(monkeypatch, curve):
    """把 `_alt_az` 换成解析曲线；返回调用计数列表。"""
    calls = []
    state = {"tt0": None}

    def _fake(_diff, t):
        tt = float(t.tt)
        if state["tt0"] is None:
            state["tt0"] = tt
        sec = (tt - state["tt0"]) * 86400.0
        calls.append(sec)
        return (curve(sec), 0.0)

    monkeypatch.setattr(sp, "_alt_az", _fake)
    return calls


# -------------------------------------------------------------- 窗口钳位
class TestWindowClamp:
    def test_bounds_and_bad_values(self):
        assert clamp_pass_window_hours(0.5) == PASS_WINDOW_MIN_HOURS
        assert clamp_pass_window_hours(-3) == PASS_WINDOW_MIN_HOURS
        assert clamp_pass_window_hours(10000) == PASS_WINDOW_MAX_HOURS
        assert clamp_pass_window_hours(24) == 24.0
        for bad in (None, "abc", float("nan"), float("inf"), float("-inf")):
            assert clamp_pass_window_hours(bad) == sp.PASS_WINDOW_HOURS

    def test_kernel_bounds_match_desktop_schema(self):
        """内核 DoS 上界与 UI 可选范围必须同一口径，改一边就红。"""
        import settings
        low, high = settings.PASS_WINDOW_RANGE_HOURS
        assert (PASS_WINDOW_MIN_HOURS, PASS_WINDOW_MAX_HOURS) == (low, high)

    def test_absurd_hours_cannot_trigger_massive_propagations(self, monkeypatch):
        calls = _inject_curve(monkeypatch, lambda _s: -10.0)
        result = predict_passes(TLE, STATION, start_unix=START_UNIX,
                                hours=10000, min_alt=10.0)
        assert result == []
        # 72h / 60s ≈ 4321 个粗扫点；未钳位会是 600001 个，测试会超时。
        assert len(calls) < 10000, f"窗口没钳住：{len(calls)} 次传播"
        assert PASS_COARSE_STEP_S == 60.0, "粗扫步长仍是全仓唯一事实源"


# ------------------------------------------------------------ 擦边补点
def _grazing_curve(sec):
    """峰值 10.4° @30s、门限 10°、越门限时长 50s 的抛物线过境。"""
    return 10.4 - 0.00064 * (sec - 30.0) ** 2


class TestGrazingPass:
    def test_short_grazing_pass_is_found(self, monkeypatch):
        calls = _inject_curve(monkeypatch, _grazing_curve)
        passes = predict_passes(TLE, STATION, start_unix=START_UNIX,
                                hours=1.0, min_alt=10.0)
        assert len(passes) == 1, "60s 网格整段吞掉的擦边过境必须被补点救回"
        p = passes[0]
        assert p.max_alt >= 10.0
        assert math.isfinite(p.max_alt) and p.max_alt < 11.0
        assert p.set_time.tt > p.rise_time.tt
        assert any(abs(s - 30.0) < 0.01 for s in calls), \
            "必须真的探测过区间中点（skyfield Time.tt 有 ~µs 浮点误差）"

    def test_counterproof_disabling_probe_loses_the_pass(self, monkeypatch):
        """反证：把补点判据关掉，同一条曲线立刻漏检——证明补点真的在咬。"""
        _inject_curve(monkeypatch, _grazing_curve)
        monkeypatch.setattr(sp, "PASS_EDGE_PROBE_MARGIN_DEG", -1e9)
        assert predict_passes(TLE, STATION, start_unix=START_UNIX,
                              hours=1.0, min_alt=10.0) == []

    def test_no_duplicate_for_a_normal_wide_pass(self, monkeypatch):
        """宽过境（多个采样点越门限）仍只出一条，补点不许制造重复。"""
        def _wide(sec):
            return 30.0 - 0.0005 * (sec - 600.0) ** 2

        _inject_curve(monkeypatch, _wide)
        passes = predict_passes(TLE, STATION, start_unix=START_UNIX,
                                hours=1.0, min_alt=10.0)
        assert len(passes) == 1
        assert passes[0].max_alt >= 29.9

    def test_all_below_horizon_stays_empty(self, monkeypatch):
        calls = _inject_curve(monkeypatch, lambda _s: -20.0)
        assert predict_passes(TLE, STATION, start_unix=START_UNIX,
                              hours=1.0, min_alt=10.0) == []
        # 地平以下的区间不该触发中点探测（成本不能翻倍）
        assert len(calls) < 70, f"多余探测：{len(calls)} 次"
