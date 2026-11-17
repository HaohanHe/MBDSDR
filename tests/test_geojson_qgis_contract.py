# SPDX-License-Identifier: MIT
"""QGIS 口径的 GeoJSON 契约：三个导出面用同一把尺子量。

背景：地面轨迹、台址点、实时位置各自有导出函数，字段与坐标序若各写各的，
QGIS 打开后就会出现「一张图在南极」「属性表缺列」「整层加载失败」。
本文件不测业务数值，只钉 QGIS 真正依赖的四件事：

1. 结构是 RFC 7946 ``FeatureCollection``，几何类型在 Point/LineString 内；
2. 坐标序是 ``[lon, lat]`` 且落在经纬度值域内、全是有限数；
3. 属性是**标准 JSON**（``allow_nan=False`` 能序列化）——NaN/Infinity
   不是 JSON，严格解析器会整层丢弃；
4. 每个导出面都带齐自己的口径字段（role/source/单位/时间等）。
"""

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.astronomy.observing_sites import sites_as_geojson  # noqa: E402
from mbdsdr_ai.ground_track import Subpoint, to_geojson as track_geojson  # noqa: E402
from mbdsdr_ai.position_service import PositionReport, PositionService  # noqa: E402

ISS_TLE = (
    "1 25544U 98067A   26297.20915682  .00007800  00000+0  14938-3 0  9991",
    "2 25544  51.6335  21.6229 0006884 302.9098  57.1228 15.49030149591752",
)
WHEN = 1792821600.0


def _assert_position(pos):
    assert isinstance(pos, list) and 2 <= len(pos) <= 3, pos
    lon, lat = pos[0], pos[1]
    for v in (lon, lat):
        assert isinstance(v, (int, float)) and not isinstance(v, bool), pos
        assert math.isfinite(v), pos
    assert -180.0 <= lon <= 180.0, pos
    assert -90.0 <= lat <= 90.0, pos


def _check_collection(fc, required):
    """结构 + 坐标 + 严格 JSON + 必填属性，一次全查。"""
    assert fc["type"] == "FeatureCollection"
    assert isinstance(fc["features"], list)
    # NaN / Infinity 不是合法 JSON，QGIS 的严格解析会整层失败
    json.dumps(fc, allow_nan=False)

    for f in fc["features"]:
        assert f["type"] == "Feature"
        geom = f["geometry"]
        assert geom["type"] in ("Point", "LineString"), geom["type"]
        if geom["type"] == "Point":
            _assert_position(geom["coordinates"])
        else:
            assert len(geom["coordinates"]) >= 2
            for pos in geom["coordinates"]:
                _assert_position(pos)

        props = f["properties"]
        assert isinstance(props, dict)
        json.dumps(props, allow_nan=False)
        for key in props:
            assert isinstance(key, str) and key, "属性名必须是非空字符串"
        for key in required:
            assert key in props, f"缺字段 {key}：{sorted(props)}"


class TestGroundTrackExport:
    def test_iss_track_opens_as_qgis_layer(self):
        from mbdsdr_ai.ground_track import ground_track

        track = ground_track("ISS", when_unix=WHEN, minutes=90.0,
                             step_s=30.0, tle=ISS_TLE)
        _check_collection(track_geojson(track), required=(
            "role", "source", "ellipsoid", "unit",
            "window_minutes", "step_s", "start_unix", "end_unix"))

    def test_dateline_split_stays_inside_lon_range(self):
        """切段后每段仍在 [-180,180]，不出现 181 或跨图假线。"""
        track = {"segments": [[
            Subpoint(10.0, 179.0, 400.0, WHEN),
            Subpoint(11.0, -179.0, 400.0, WHEN + 30),
        ]]}
        _check_collection(track_geojson(track), required=("role",))


class TestObservingSitesExport:
    def test_builtin_sites_open_as_qgis_layer(self):
        _check_collection(sites_as_geojson(), required=(
            "site_id", "name_zh", "name_en", "alt_m", "access",
            "source_url", "verified_on", "note", "role"))


class TestPositionServiceExport:
    def test_point_and_track_survive_roundtrip(self):
        svc = PositionService(clock=lambda: WHEN)
        for i, lon in enumerate((116.0, 116.2)):
            svc.report(PositionReport(
                source="ICAO123456", source_type="adsb",
                lat=39.9, lon=lon, altitude_m=1000.0,
                speed_mps=250.0, heading_deg=90.0,
                timestamp=WHEN + i, metadata={"callsign": "TEST123"}))
        fc = svc.geojson()
        _check_collection(fc, required=("source",))
        kinds = [f["geometry"]["type"] for f in fc["features"]]
        assert kinds == ["Point", "LineString"]
        point = fc["features"][0]
        assert point["geometry"]["coordinates"] == [116.2, 39.9]
        assert point["properties"]["altitude_m"] == 1000.0
        assert point["properties"]["callsign"] == "TEST123"
        assert fc["features"][1]["properties"]["role"] == "track"

    def test_empty_service_is_valid_empty_collection(self):
        fc = PositionService(clock=lambda: WHEN).geojson()
        assert fc == {"type": "FeatureCollection", "features": []}
        json.dumps(fc, allow_nan=False)
