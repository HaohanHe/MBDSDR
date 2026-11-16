# SPDX-License-Identifier: MIT
"""ground_track.satellite_situation：多星态势纯计算契约（离线、注入 TLE）。

钉住三件事：
  1. 态势 = 覆盖圈 + 轨迹 + TLE 出处 + 新鲜度，注入 TLE 的出处是 "injected"
     （不冒充联网）；
  2. 时刻变了星下点真动（圈随时间走的算术基础）；
  3. 取不到真 TLE 就抛 TLEUnavailableError——**绝不回退合成根数**。
"""
import math
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai import ground_track as gt  # noqa: E402
from mbdsdr_ai.orbit import TLEUnavailableError  # noqa: E402

#: 确定性 TLE（与 tests/test_sat_ground_map.py 同一条真实格式 ISS 根数）。
ISS_TLE = (
    "1 25544U 98067A   26297.20915682  .00007800  00000+0  14938-3 0  9991",
    "2 25544  51.6335  21.6229 0006884 302.9098  57.1228 15.49030149591752",
)
T1 = 1763000000.0


def test_situation_with_injected_tle_is_complete():
    sit = gt.satellite_situation("ISS (ZARYA)", when_unix=T1,
                                 minutes=30.0, tle=ISS_TLE)
    assert sit["name"] == "ISS (ZARYA)"
    assert sit["tle_source"] == "injected", "注入 TLE 不许冒充联网"
    assert sit["at_unix"] == pytest.approx(T1)

    fp = sit["footprint"]
    assert len(fp["ring"]) >= 3
    assert fp["radius_km"] > 0
    assert fp["ellipsoid"] == "WGS-72"

    trk = sit["track"]
    assert trk["start_unix"] == pytest.approx(T1)
    assert len(trk["points"]) == int(round(30.0 * 60.0 / trk["step_s"])) + 1

    fresh = sit["tle_freshness"]
    assert fresh["status"] in ("fresh", "stale", "none")
    assert "age_days" in fresh


def test_footprint_follows_time():
    a = gt.satellite_situation("ISS (ZARYA)", when_unix=T1, tle=ISS_TLE)
    b = gt.satellite_situation("ISS (ZARYA)", when_unix=T1 + 600.0, tle=ISS_TLE)
    sa = a["footprint"]["subpoint"]
    sb = b["footprint"]["subpoint"]
    moved = (abs(sa.lat_deg - sb.lat_deg) > 0.01
             or abs(sa.lon_deg - sb.lon_deg) > 0.01)
    assert moved, f"10 分钟后星下点没动：{sa} vs {sb}"
    assert sb.unix_s == pytest.approx(T1 + 600.0)
    # 两次圈心不同 → 覆盖圈随时刻重算，而不是画一个静态圈
    ra = a["footprint"]["ring"][0]
    rb = b["footprint"]["ring"][0]
    assert ra != rb


def test_ring_vertices_are_on_the_central_angle():
    sit = gt.satellite_situation("ISS (ZARYA)", when_unix=T1, tle=ISS_TLE,
                                 points=24)
    fp = sit["footprint"]
    sub = fp["subpoint"]
    for lat, lon in fp["ring"][:-1]:
        ang = _angle(sub.lat_deg, sub.lon_deg, lat, lon)
        assert ang == pytest.approx(fp["central_angle_deg"], abs=0.01)


def _angle(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(((lon2 - lon1 + 180.0) % 360.0) - 180.0)
    v = (math.sin((p2 - p1) / 2.0) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2)
    return math.degrees(2.0 * math.asin(min(1.0, math.sqrt(v))))


def test_resolve_goes_through_the_real_fetch(monkeypatch):
    calls = []

    def fake_fetch(catnr, max_age_hours=24.0):
        calls.append(catnr)
        return ISS_TLE

    monkeypatch.setattr(gt, "fetch_tle", fake_fetch)
    (l1, l2), source = gt.resolve_tle_lines("ISS (ZARYA)")
    assert (l1, l2) == ISS_TLE
    assert source == "celestrak"
    assert calls == [25544], "取数必须走 gt.fetch_tle（既有测试注入点）"


def test_resolve_never_falls_back_to_synthetic_tle(monkeypatch):
    """取不到真 TLE 必须抛——回退合成根数画图就是造假。"""
    def boom(catnr, max_age_hours=24.0):
        raise TLEUnavailableError("离线且无缓存")

    monkeypatch.setattr(gt, "fetch_tle", boom)
    with pytest.raises(TLEUnavailableError):
        gt.resolve_tle_lines("ISS (ZARYA)")
    with pytest.raises(TLEUnavailableError):
        gt.satellite_situation("ISS (ZARYA)", when_unix=T1)


def test_unregistered_name_is_keyerror():
    with pytest.raises(KeyError):
        gt.resolve_tle_lines("NOT A SATELLITE")
