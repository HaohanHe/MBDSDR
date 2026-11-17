# SPDX-License-Identifier: MIT
"""卫星地面轨迹 GeoJSON 契约测试（QGIS 方向收口）。

钉三件事：
1. **坐标序**是 ``[lon, lat]``（RFC 7946），不是 ``[lat, lon]``——
   反了在 QGIS 里会把北京画到南极附近；
2. **日界线切段**：每段一条 LineString，不出现 |Δlon| > 180 的假线；
3. **字段齐全**：`role/source/ellipsoid/unit/window_minutes/step_s`
   都在 properties 里，QGIS 属性表能直接核对口径。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.ground_track import Subpoint, to_geojson  # noqa: E402

ISS_TLE = (
    "1 25544U 98067A   26297.20915682  .00007800  00000+0  14938-3 0  9991",
    "2 25544  51.6335  21.6229 0006884 302.9098  57.1228 15.49030149591752",
)
WHEN = 1792821600.0  # 2026-10-24T06:00:00Z


def _track():
    from mbdsdr_ai.ground_track import ground_track

    return ground_track("ISS", when_unix=WHEN, minutes=90.0,
                        step_s=30.0, tle=ISS_TLE)


class TestRealTrack:
    def test_feature_collection_shape(self):
        fc = to_geojson(_track())
        assert fc["type"] == "FeatureCollection"
        assert len(fc["features"]) >= 1
        for f in fc["features"]:
            assert f["type"] == "Feature"
            assert f["geometry"]["type"] == "LineString"
            assert len(f["geometry"]["coordinates"]) >= 2

    def test_coordinate_order_is_lon_lat(self):
        """第一段首点必须等于 (lon, lat)，不是 (lat, lon)。"""
        track = _track()
        fc = to_geojson(track)
        p0 = track["points"][0]
        first = fc["features"][0]["geometry"]["coordinates"][0]
        assert first == [p0.lon_deg, p0.lat_deg]
        assert -180.0 <= first[0] <= 180.0
        assert -90.0 <= first[1] <= 90.0

    def test_properties_carry_units_and_window(self):
        f = to_geojson(_track())["features"][0]
        props = f["properties"]
        assert props["role"] == "ground_track"
        assert props["source"] == "ground_track"
        assert props["ellipsoid"] == "WGS-72"
        assert props["unit"] == "km"
        assert props["window_minutes"] == 90.0
        assert props["step_s"] == 30.0
        assert props["end_unix"] > props["start_unix"]

    def test_no_fake_cross_dateline_segment(self):
        """任何相邻两点经度差都不许超过 180°（否则是画到地图另一边）。"""
        for f in to_geojson(_track())["features"]:
            coords = f["geometry"]["coordinates"]
            for a, b in zip(coords, coords[1:]):
                assert abs(b[0] - a[0]) <= 180.0 + 1e-9


class TestDatelineSplit:
    def test_two_segments_become_two_features(self):
        track = {"segments": [
            [Subpoint(10.0, 179.0, 400.0, WHEN),
             Subpoint(11.0, 179.5, 400.0, WHEN + 30)],
            [Subpoint(11.0, -179.5, 400.0, WHEN + 60),
             Subpoint(12.0, -179.0, 400.0, WHEN + 90)],
        ], "ellipsoid": "WGS-72", "unit": "km",
            "window_minutes": 1.0, "step_s": 30.0}
        fc = to_geojson(track)
        assert len(fc["features"]) == 2
        assert fc["features"][0]["geometry"]["coordinates"] == \
            [[179.0, 10.0], [179.5, 11.0]]
        assert fc["features"][1]["geometry"]["coordinates"] == \
            [[-179.5, 11.0], [-179.0, 12.0]]

    def test_empty_track_is_empty_collection(self):
        fc = to_geojson({"segments": []})
        assert fc == {"type": "FeatureCollection", "features": []}

    def test_single_point_segment_is_skipped(self):
        """一个点画不出线：不生成退化 LineString。"""
        track = {"segments": [[Subpoint(10.0, 10.0, 400.0, WHEN)]]}
        assert to_geojson(track)["features"] == []
