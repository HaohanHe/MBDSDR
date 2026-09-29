# SPDX-License-Identifier: MIT
"""mbdsdr_ai.satellite.tracker 确定性单测。（合成向量，非硬件 / NOT HARDWARE）

对照 SatDump tracking/ + passes/（docs/learn/satdump.md §8-§9）：
  1. SGP4 传播：用固定 TLE + 固定时刻，卫星位置与 skyfield 参考解误差 <1km；
  2. 站心量：仰角/方位/距离落在物理合理区间；
  3. AOS/LOS 预测：在 TLE 有效窗口内预测过境，aos<los<max_el_t 在其间，max_el>0；
  4. 多普勒：AOS（接近）doppler>0、LOS（远离）doppler<0 的符号规律；
  5. next_pass_countdown 结构正确。

运行：pytest tests/test_satellite_tracker.py -v
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.satellite import (  # noqa: E402
    GroundStation, SatelliteTracker, PassPredictor,
    auto_detect_satellite, tune_command, DOWNLINK_FREQUENCIES,
)

# 固定历史 TLE（NOAA-19，epoch 2024-01-01 12:00 UTC）。测试时间取 epoch 后 ~12h，
# TLE 有效，传播结果确定。
TLE_L1 = ("1 33591U 09005A   24001.50000000  .00000300  00000-0  16087-3 0  9990")
TLE_L2 = ("2 33591  99.1900 312.3500 0014200  80.0000 280.0000 14.11715000458920")
# 2024-01-02 00:00:00 UTC
T_TEST = 1704153600.0
BEIJING = GroundStation(39.9042, 116.4074, 0.0, "Beijing")


@pytest.fixture(scope="module")
def tracker():
    return SatelliteTracker("NOAA-19", TLE_L1, TLE_L2, norad=33591)


def test_sgp4_position_regression(tracker):
    """SGP4 传播：硬编码回归值（与 skyfield 参考解一致），容差 1km。"""
    st = tracker.position_at(T_TEST, BEIJING)
    assert st is not None
    # 回归值：skyfield 同 TLE/时刻 得 range=8712.725km, el=-35.325°, az=73.637°
    assert st["range_km"] == pytest.approx(8712.73, abs=1.0)     # <1 km
    assert st["elevation"] == pytest.approx(-35.32, abs=0.1)
    assert st["azimuth"] == pytest.approx(73.64, abs=0.1)
    # NOAA-19 轨道高度 ~850km
    assert 800 < st["altitude_km"] < 900


def test_sgp4_crosscheck_skyfield(tracker):
    """若环境有 skyfield，独立参考解对比 <1km。无则跳过。"""
    sf = pytest.importorskip("skyfield")
    from skyfield.api import Loader, EarthSatellite, wgs84
    import datetime
    import tempfile
    try:
        loader = Loader(tempfile.mkdtemp(prefix="sf_"))
        ts = loader.timescale()
        sat = EarthSatellite(TLE_L1, TLE_L2, "N19", ts)
        dt = datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=T_TEST)
        t = ts.utc(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
        gs = wgs84.latlon(BEIJING.lat, BEIJING.lon, BEIJING.alt_km * 1000)
        obs = (sat - gs).at(t)
        el, az, _ = obs.altaz()
    except Exception as e:  # 无网/缺星历文件则跳过
        pytest.skip(f"skyfield 参考解不可用: {e}")
    st = tracker.position_at(T_TEST, BEIJING)
    assert st["range_km"] == pytest.approx(obs.distance().km, abs=1.0)
    assert st["elevation"] == pytest.approx(el.degrees, abs=0.1)
    assert st["azimuth"] == pytest.approx(az.degrees, abs=0.2)


def test_doppler_sign_and_magnitude(tracker):
    """多普勒公式：shift = -v_los/c * f；量级 ~±2kHz（LEO）。"""
    d = tracker.doppler_at(T_TEST, BEIJING, 137.1e6)
    assert d is not None
    assert "doppler_shift_hz" in d
    assert abs(d["doppler_shift_hz"]) < 5000  # LEO 多普勒典型 ±几 kHz
    # 符号：range_rate>0（远离）→ shift<0；这里 range_rate≈-4.28 km/s（接近）→ shift>0
    v = d["range_rate_kms"]
    expected_sign = -np.sign(v) if abs(v) > 1e-6 else 0
    if expected_sign != 0:
        assert np.sign(d["doppler_shift_hz"]) == expected_sign


def test_aos_los_prediction(tracker):
    """PassPredictor：在 TLE 有效窗口内找过境，结构正确。"""
    pp = PassPredictor(tracker, BEIJING)
    # 从 2024-01-02 00:00 UTC 起预测 6 小时
    passes = pp.predict(hours=6.0, min_el_deg=0.0, t_start=T_TEST)
    assert len(passes) >= 1, "6h 内至少应有一次过境"
    for p in passes:
        assert p["aos"] < p["los"]
        assert p["max_el"] > 0
        # 中天时间落在 AOS/LOS 之间
        assert p["aos"] <= p["max_el_t"] <= p["los"]
        assert p["duration_s"] > 0
        assert 0 <= p["aos_az"] < 360
        assert 0 <= p["los_az"] < 360


def test_predictor_min_elevation_filter(tracker):
    """min_el 过滤：max_el 应 >= 阈值。"""
    pp = PassPredictor(tracker, BEIJING)
    passes = pp.predict(hours=12.0, min_el_deg=30.0, t_start=T_TEST)
    for p in passes:
        assert p["max_el"] >= 30.0


def test_next_pass_countdown(tracker):
    pp = PassPredictor(tracker, BEIJING)
    cd = pp.next_pass_countdown(hours=6.0, min_el_deg=0.0)
    # 从真实 now 起；可能无过境（返回 None）也可能有
    if cd is not None:
        assert cd["seconds_to_aos"] >= 0
        assert "pass" in cd


def test_auto_detect_satellite():
    """按下行频率识别卫星（对照 decoder.cpp:115-153）。"""
    assert auto_detect_satellite(137.1e6)["name"] == "NOAA-19"
    assert auto_detect_satellite(137.9125e6)["name"] == "NOAA-18"
    assert auto_detect_satellite(137.62e6)["name"] == "NOAA-15"
    assert auto_detect_satellite(100.0e6) is None


def test_tune_command(tracker):
    """与 rf_sky_view 联动：调谐频率 = 下行 + 多普勒。"""
    out = tune_command(tracker, BEIJING, t_unix=T_TEST)
    assert out["base_freq_hz"] == DOWNLINK_FREQUENCIES["NOAA-19"]
    assert abs(out["tuned_freq_hz"] - out["base_freq_hz"]) < 5000
