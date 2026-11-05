# SPDX-License-Identifier: MIT
"""P11 跟踪器站址守卫：0,0/海上不跟踪、不过境；可信站址正常。

运行：python -m pytest mbdsdr_ai/tests/test_tracker_station_guard.py -v
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai.sat_passes import GroundStation  # noqa: E402
from mbdsdr_ai.sat_tracker import SatelliteTracker  # noqa: E402


def test_null_island_station_refuses_tracking():
    t = SatelliteTracker(GroundStation(lat_deg=0.0, lon_deg=0.0, alt_m=0.0))
    assert t.select_satellite("NOAA 15") is True
    # 写/控制口径（老入口）语义不变：显式 (0,0) 仍严格拒绝
    st = t.station_status()
    assert st["trusted"] is False
    p = t.current_position()
    assert p["valid"] is False
    assert p["station_untrusted"] is True
    assert p["azimuth"] is None
    assert p["station_reasons"]
    # 只读/计算口径：显式声明放行并带 warning
    st_calc = t.station_status_for_calculation()
    assert st_calc["trusted"] is True
    assert "null_island_0_0" in st_calc["station_warning"]
    p_calc = t.current_position_for_calculation()
    assert p_calc["valid"] is True
    assert p_calc["station_warning"]
    assert t.next_pass() is not None


def test_ocean_station_refuses_tracking():
    t = SatelliteTracker(GroundStation(lat_deg=0.0, lon_deg=-150.0, alt_m=0.0))
    assert t.select_satellite("NOAA 15") is True
    assert t.station_status()["trusted"] is False
    assert t.current_position()["valid"] is False
    # 只读口径：显式海上坐标照常算，warning 标注海域
    assert t.station_status_for_calculation()["trusted"] is True
    assert t.current_position_for_calculation()["valid"] is True


def test_trusted_station_tracks_normally():
    t = SatelliteTracker(GroundStation(lat_deg=39.9, lon_deg=116.4, alt_m=50.0))
    assert t.select_satellite("NOAA 15") is True
    assert t.station_status()["trusted"] is True
    p = t.current_position()
    assert p["valid"] is True
    assert p["station_untrusted"] is False
    assert p["elevation"] == p["elevation"]  # 非 NaN
