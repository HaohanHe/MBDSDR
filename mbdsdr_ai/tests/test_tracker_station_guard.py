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
    st = t.station_status()
    assert st["trusted"] is False
    p = t.current_position()
    assert p["valid"] is False
    assert p["station_untrusted"] is True
    assert p["azimuth"] is None
    assert p["station_reasons"]
    assert t.next_pass() is None


def test_ocean_station_refuses_tracking():
    t = SatelliteTracker(GroundStation(lat_deg=0.0, lon_deg=-150.0, alt_m=0.0))
    assert t.select_satellite("NOAA 15") is True
    assert t.station_status()["trusted"] is False
    assert t.current_position()["valid"] is False


def test_trusted_station_tracks_normally():
    t = SatelliteTracker(GroundStation(lat_deg=39.9, lon_deg=116.4, alt_m=50.0))
    assert t.select_satellite("NOAA 15") is True
    assert t.station_status()["trusted"] is True
    p = t.current_position()
    assert p["valid"] is True
    assert p["station_untrusted"] is False
    assert p["elevation"] == p["elevation"]  # 非 NaN
