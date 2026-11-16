# -*- coding: utf-8 -*-
"""new_spacetime.destination_point 经度归一化确定性符合测试（#104）。

缺陷：lon2 = lon1 + atan2(...) 未归一化，跨日界线产出 >180°/<-180° 非法经度
（实证：起点 (0,179.5) 向东 611km → 184.99°E）。真实消费方
sdr_tools._gis_destination → GeoPoint.to_string() 把非法经度直接显示给用户。

项目自有约定（事实源）：ground_track.destination_point 用 (deg+180)%360-180
归一化到 [-180,180)，且 test_ground_track.py::DestinationPointTest::
test_pole_seam_lon_wrap 已钉扎 -180<=lon<=180。本测试把 new_spacetime 对齐到
同一接缝，并跨实现对拍证明二者经度口径一致。

全部断言用固定起点/方位角/距离，不绑墙钟、不绑网络（测试确定性纪律）。
"""
import math
import re

import pytest

from mbdsdr_ai.new_spacetime import (
    destination_point, GeoPoint, EARTH_RADIUS_KM,
)
from mbdsdr_ai.ground_track import destination_point as gt_destination_point

# to_string 经度字段："{abs(lon):.6f}°{E|W}"（可选 ", {alt}m" 后缀）。
_LON_RE = re.compile(r"([\d.]+)°([EW])")

# 与 ground_track test_pole_seam_lon_wrap 同一边界口径。
_LON_MIN, _LON_MAX = -180.0, 180.0
_LAT_MIN, _LAT_MAX = -90.0, 90.0


def _in_range(lon: float, lat: float) -> bool:
    return (_LON_MIN <= lon <= _LON_MAX) and (_LAT_MIN <= lat <= _LAT_MAX)


# ---------------------------------------------------------------------------
# 1) 日界线跨越：经度必须归一化（缺陷的直接复现锚）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("dist_km,expect_lon", [
    (60.0, -179.960408),
    (111.0, -179.501754),
    (300.0, -177.802039),
    (611.0, -175.005153),
])
def test_dateline_crossing_normalizes_longitude(dist_km, expect_lon):
    """起点 (0,179.5) 正东跨越日界线：经度落 [-180,180] 且等于解析 wrap 值。

    去归一化突变（longitude=degrees(lon2)）在此必红：产出 184.99 等非法值。
    """
    dest = destination_point(GeoPoint(latitude=0.0, longitude=179.5), 90.0, dist_km)
    assert _LON_MIN <= dest.longitude <= _LON_MAX, (
        f"dist={dist_km} 经度越界: {dest.longitude}")
    # 解析期望：赤道正东，lon2 = 179.5 + degrees(dist/R)，再 wrap 到 [-180,180)。
    raw = 179.5 + math.degrees(dist_km / EARTH_RADIUS_KM)
    wrapped = (raw + 180.0) % 360.0 - 180.0
    assert abs(dest.longitude - wrapped) < 1e-9
    assert abs(dest.longitude - expect_lon) < 1e-5


@pytest.mark.parametrize("start_lon,bearing", [
    (179.5, 90.0),    # 东跨
    (-179.5, 270.0),  # 西跨
    (180.0, 90.0),    # 恰在日界线东跨
    (-180.0, 270.0),  # 恰在日界线西跨
    (170.0, 90.0),    # 近日界线东跨
    (-170.0, 270.0),  # 近日界线西跨
])
def test_dateline_both_directions_in_range(start_lon, bearing):
    """东西两向跨日界线，大距离下经度仍合法（覆盖 abs/符号翻转突变）。"""
    for dist_km in (100.0, 1000.0, 5000.0, 20000.0):
        dest = destination_point(GeoPoint(0.0, start_lon), bearing, dist_km)
        assert _in_range(dest.longitude, dest.latitude), (
            f"start_lon={start_lon} brg={bearing} dist={dist_km} "
            f"-> lon={dest.longitude} lat={dest.latitude}")


# ---------------------------------------------------------------------------
# 2) 网格不变量：任意起点×方位×距离，经纬度恒合法
# ---------------------------------------------------------------------------
def test_longitude_latitude_in_range_over_grid():
    """起点经度 -180..180（步长 5°）× 8 方位 × 5 距离，经纬度恒在合法域。

    这是缺陷类的广域不变量：去归一化突变在近日界线大距离格点必红。
    """
    offenders = []
    bearings = (0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0)
    distances = (10.0, 100.0, 1000.0, 5000.0, 20000.0)
    for i in range(-36, 37):           # -180 .. 180，步长 5°
        start_lon = i * 5.0
        for bearing in bearings:
            for dist_km in distances:
                dest = destination_point(GeoPoint(0.0, start_lon), bearing, dist_km)
                if not _in_range(dest.longitude, dest.latitude):
                    offenders.append((start_lon, bearing, dist_km,
                                      dest.longitude, dest.latitude))
    assert not offenders, f"非法经纬度 {len(offenders)} 例，前 5: {offenders[:5]}"


# ---------------------------------------------------------------------------
# 3) 跨实现对拍：与 ground_track.destination_point 同一经度口径
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ang_deg", [0.5, 1.0, 3.0, 5.5, 10.0, 30.0, 90.0])
def test_matches_ground_track_normalization_equator(ang_deg):
    """赤道正东：把 km 换算成与 ground_track 相同的中心角，二者经度必须一致。

    ground_track 是项目已钉扎的事实源（test_pole_seam_lon_wrap）。本对拍证明
    new_spacetime 修复后与其经度归一化口径逐点相符（差 < 1e-9）。
    """
    dist_km = math.radians(ang_deg) * EARTH_RADIUS_KM
    ns = destination_point(GeoPoint(0.0, 179.5), 90.0, dist_km)
    _, gt_lon = gt_destination_point(0.0, 179.5, 90.0, ang_deg)
    assert abs(ns.longitude - gt_lon) < 1e-9, (
        f"ang={ang_deg} ns={ns.longitude} gt={gt_lon}")
    assert _LON_MIN <= ns.longitude <= _LON_MAX


@pytest.mark.parametrize("lat,start_lon,bearing", [
    (40.0, 179.5, 90.0),
    (40.0, -179.5, 270.0),
    (-33.0, 170.0, 45.0),
    (0.0, -170.0, 225.0),
])
def test_matches_ground_track_normalization_off_equator(lat, start_lon, bearing):
    """非赤道/斜方位：同一中心角下二者经度仍逐点相符（球面公式同构）。"""
    ang_deg = 7.0
    dist_km = math.radians(ang_deg) * EARTH_RADIUS_KM
    ns = destination_point(GeoPoint(lat, start_lon), bearing, dist_km)
    _, gt_lon = gt_destination_point(lat, start_lon, bearing, ang_deg)
    assert abs(ns.longitude - gt_lon) < 1e-9, (
        f"lat={lat} lon={start_lon} brg={bearing} ns={ns.longitude} gt={gt_lon}")


# ---------------------------------------------------------------------------
# 4) 消费方可见不变量：to_string() 经度幅值恒 <= 180（GIS 终点工具路径）
# ---------------------------------------------------------------------------
def test_to_string_longitude_never_exceeds_180():
    """真实消费方 sdr_tools._gis_destination 用 GeoPoint.to_string() 显示终点。

    解析 to_string 的经度字段，幅值必须 <= 180（杜绝 "184.99°E" 用户可见非法值）。
    """
    offenders = []
    for i in range(-36, 37):
        start_lon = i * 5.0
        for bearing in (90.0, 270.0):
            for dist_km in (1000.0, 5000.0, 20000.0):
                dest = destination_point(GeoPoint(0.0, start_lon), bearing, dist_km)
                s = dest.to_string()
                m = _LON_RE.search(s)
                assert m is not None, f"to_string 无经度字段: {s}"
                lon_mag = float(m.group(1))
                if lon_mag > 180.0:
                    offenders.append((start_lon, bearing, dist_km, s))
    assert not offenders, f"to_string 非法经度 {len(offenders)} 例，前 5: {offenders[:5]}"


# ---------------------------------------------------------------------------
# 5) 回归锚：非日界线输入不被归一化扰动（修复对合法输入是恒等）
# ---------------------------------------------------------------------------
def test_non_dateline_unchanged():
    """常规起点 (40,116) 正东 100km：经度仍在范围内且等于未 wrap 的解析值。"""
    dest = destination_point(GeoPoint(40.0, 116.0), 90.0, 100.0)
    assert _in_range(dest.longitude, dest.latitude)
    assert abs(dest.longitude - 117.173911) < 1e-5
    assert abs(dest.latitude - 39.994078) < 1e-5


def test_due_north_keeps_longitude():
    """正北沿子午线：经度不变（0°），纬度增大约 1°/111.19km。"""
    dest = destination_point(GeoPoint(0.0, 0.0), 0.0, math.radians(1.0) * EARTH_RADIUS_KM)
    assert abs(dest.longitude) < 1e-9
    assert abs(dest.latitude - 1.0) < 1e-9


def test_zero_distance_is_identity():
    """零距离：终点 == 起点（经度归一化对合法起点是恒等）。"""
    start = GeoPoint(40.0, 116.0)
    dest = destination_point(start, 123.0, 0.0)
    assert abs(dest.longitude - start.longitude) < 1e-12
    assert abs(dest.latitude - start.latitude) < 1e-12