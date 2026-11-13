# SPDX-License-Identifier: MIT
"""过境极坐标图（机制 D）：方位/仰角轨迹的投影、跨北连续性与数据注入。

验收口径：
  1. 天顶投影：el=90 在圆心、el=0 在外圈，北=上/东=右；
  2. 过境跨 0°/360° 时方位角展开，轨迹连续且不横穿圆盘；
  3. 轨迹点只来自宿主 provider 的 ``track``；空/非法点不补、不猜；
  4. 轨迹与当前方位/仰角标记能随数据刷新，invalidate 后清空。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

#: 固定时基（与星表测试同一钉死时刻），不依赖跑测时的真实时钟。
BASE = 1_790_510_400.0

#: 与 TLE epoch 26297 对齐的 ISS 星历（只用于离线传播测试）。
ISS_TLE = (
    "1 25544U 98067A   26297.20915682  .00007800  00000+0  14938-3 0  9991",
    "2 25544  51.6335  21.6229 0006884 302.9098  57.1228 15.49030149591752",
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _panel(names=("ISS",)):
    from desktop.sat_track_panel import SatTrackPanel
    p = SatTrackPanel()
    p.populate_satellites(list(names))
    return p


def test_polar_xy_cardinals_and_zenith():
    from desktop.sat_track_panel import polar_xy

    cx, cy, r = 100.0, 100.0, 80.0
    assert polar_xy(0, 0, cx, cy, r) == pytest.approx((100.0, 20.0))
    assert polar_xy(90, 0, cx, cy, r) == pytest.approx((180.0, 100.0))
    assert polar_xy(180, 0, cx, cy, r) == pytest.approx((100.0, 180.0))
    assert polar_xy(270, 0, cx, cy, r) == pytest.approx((20.0, 100.0))
    # 天顶与方位无关；仰角超界钳到 90°
    assert polar_xy(123, 90, cx, cy, r) == pytest.approx((100.0, 100.0))
    assert polar_xy(0, 120, cx, cy, r) == pytest.approx((100.0, 100.0))


def test_unwrap_keeps_north_crossing_continuous():
    from desktop.sat_track_panel import unwrap_azimuths

    out = unwrap_azimuths([(350, 10), (355, 20), (1, 30), (6, 40)])
    azs = [a for a, _ in out]
    assert azs == [350.0, 355.0, 361.0, 366.0]
    assert max(abs(b - a) for a, b in zip(azs, azs[1:])) < 180.0


def test_unwrap_south_crossing_and_below_horizon():
    from desktop.sat_track_panel import unwrap_azimuths

    out = unwrap_azimuths([(5, 10), (358, 20), (30, -1), (40, 6)])
    assert out == [(5.0, 10), (-2.0, 20), (40.0, 6)]


def test_sample_pass_track_endpoints_and_order():
    from mbdsdr_ai.sat_passes import (GroundStation, predict_passes,
                                      sample_pass_track)

    gs = GroundStation(lat_deg=39.9, lon_deg=116.4, alt_m=50.0)
    passes = predict_passes(ISS_TLE, gs, start_unix=BASE, hours=24,
                            min_alt=10.0)
    assert passes, "24h 内 ISS 应有可见过境"
    pts = sample_pass_track(ISS_TLE, gs, passes[0], num_points=32)
    assert len(pts) == 32
    unix = [p["unix"] for p in pts]
    assert unix == sorted(unix)
    # 端点就是 AOS/LOS：仰角回到筛选门 10°（采样误差 1° 内）
    assert pts[0]["el"] == pytest.approx(10.0, abs=1.0)
    assert pts[-1]["el"] == pytest.approx(10.0, abs=1.0)
    assert max(p["el"] for p in pts) == pytest.approx(passes[0].max_alt, abs=1.0)
    assert all(0.0 <= p["az"] < 360.0 for p in pts)
    assert all(0.0 <= p["el"] <= 90.0 for p in pts)


def test_sample_pass_track_refuses_bad_input():
    from mbdsdr_ai.sat_passes import (GroundStation, predict_passes,
                                      sample_pass_track)

    gs = GroundStation(lat_deg=39.9, lon_deg=116.4)
    p = predict_passes(ISS_TLE, gs, start_unix=BASE, hours=24, min_alt=10.0)[0]
    assert sample_pass_track([], gs, p) == []
    assert sample_pass_track(["not a tle"], gs, p) == []


def test_plot_filters_malformed_points(qapp, isolated_profile):
    from desktop.sat_track_panel import PolarPassPlot

    w = PolarPassPlot()
    w.set_track([
        {"az": 10, "el": 20},
        {"az": "x", "el": 5},
        {"el": 3},
        {"az": float("nan"), "el": 10},
        {"az": 370, "el": 30},
    ])
    assert w.track_points() == [(10.0, 20.0), (10.0, 30.0)]


def test_panel_draws_provider_track_and_current_point(qapp, isolated_profile):
    p = _panel()
    track = [
        {"unix": BASE + 100, "az": 350, "el": 10},
        {"unix": BASE + 250, "az": 5, "el": 40},
        {"unix": BASE + 400, "az": 20, "el": 12},
    ]
    p.set_pass_provider(lambda name: [{
        "aos_unix": BASE + 100, "los_unix": BASE + 400,
        "max_alt": 40.0, "track": track,
    }])
    p.update_position({
        "valid": True, "azimuth": 356.0, "elevation": 30.0,
        "doppler_hz": 0.0, "corrected_freq_hz": 137.62e6, "range_km": 1000.0,
    }, now_unix=BASE)
    assert len(p.polar_plot.track_points()) == 3
    assert p.polar_plot.current_point() == (356.0, 30.0)
    # 真画一遍：绘制路径不得抛异常，且图非空
    img = p.polar_plot.grab().toImage()
    assert img.width() > 0 and img.height() > 0


def test_panel_clears_track_when_no_pass_or_invalidated(qapp, isolated_profile):
    p = _panel()
    p.set_pass_provider(lambda name: [])
    p.update_position({"valid": False}, now_unix=BASE)
    assert p.polar_plot.track_points() == []
    assert p.polar_plot.current_point() is None

    p.set_pass_provider(lambda name: [{
        "aos_unix": BASE + 100, "los_unix": BASE + 400,
        "track": [{"az": 10, "el": 10}, {"az": 20, "el": 20}],
    }])
    p.update_position({"valid": False}, now_unix=BASE)
    assert len(p.polar_plot.track_points()) == 2
    p.invalidate_passes()
    assert p.polar_plot.track_points() == []
