# SPDX-License-Identifier: MIT
"""导出脚本：把内置台址图层写成 QGIS 能直接打开的文件。

钉的是「真的有文件」这件此前不存在的事
--------------------------------------
三个 GeoJSON 导出函数一直只有内存里的 dict；没有任何一条路径落盘。
本文件用子进程跑真实脚本，再按 QGIS 的口径验收产物：

1. 文件存在且是严格 JSON（NaN/Infinity 直接判失败）；
2. 结构是 RFC 7946 FeatureCollection，坐标 ``[lon, lat]`` 在值域内；
3. 要素数与内置台址数一致（不写死 6/7，防止数据增长后测试过期）；
4. 官方页没给海拔的点写 ``null``，不用 0 冒充；
5. 覆盖写不留 ``.tmp`` 残片。
"""

import json
import math
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mbdsdr_ai.astronomy import observing_sites as sites_mod  # noqa: E402

SCRIPT = ROOT / "scripts" / "export_geojson.py"


def _strict_loads(text):
    """Python 的 json 默认接受 NaN；这里按 RFC 8259 严格解析。"""
    def _reject(value):
        raise AssertionError(f"不是合法 JSON 常量：{value}")
    return json.loads(text, parse_constant=_reject)


def _run(*args, cwd=ROOT):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(cwd), capture_output=True, text=True, timeout=120)


class TestExportObservingSites:
    def test_writes_openable_feature_collection(self, tmp_path):
        out = tmp_path / "sites.geojson"
        r = _run(str(out))
        assert r.returncode == 0, r.stderr
        assert out.exists()

        fc = _strict_loads(out.read_text(encoding="utf-8"))
        assert fc["type"] == "FeatureCollection"
        assert len(fc["features"]) == len(sites_mod.SITES)
        assert f"要素：{len(sites_mod.SITES)}" in r.stdout

        for feat in fc["features"]:
            assert feat["type"] == "Feature"
            geom = feat["geometry"]
            assert geom["type"] == "Point"
            lon, lat = geom["coordinates"]
            assert math.isfinite(lon) and math.isfinite(lat)
            assert -180.0 <= lon <= 180.0
            assert -90.0 <= lat <= 90.0
            props = feat["properties"]
            for key in ("site_id", "name_zh", "access", "source_url"):
                assert key in props
            assert props["source_url"].startswith("http")

    def test_missing_official_altitude_stays_null(self, tmp_path):
        out = tmp_path / "sites.geojson"
        assert _run(str(out)).returncode == 0
        fc = _strict_loads(out.read_text(encoding="utf-8"))
        mohe = [f for f in fc["features"]
                if f["properties"].get("site_id") == "mohe-beijicun"]
        assert mohe, "漠河点必须被导出"
        assert mohe[0]["properties"]["alt_m"] is None, \
            "官方页没给海拔时必须写 null，不能用 0 冒充"

    def test_overwrite_leaves_no_temp_files(self, tmp_path):
        out = tmp_path / "sites.geojson"
        assert _run(str(out)).returncode == 0
        first = out.read_text(encoding="utf-8")
        assert _run(str(out)).returncode == 0
        assert out.read_text(encoding="utf-8") == first, "两次导出必须一致"
        leftovers = [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"]
        assert leftovers == []

    def test_unknown_layer_is_rejected_by_argparse(self, tmp_path):
        r = _run(str(tmp_path / "x.geojson"), "--layer", "no-such-layer")
        assert r.returncode == 2
        assert "invalid choice" in r.stderr
        assert not (tmp_path / "x.geojson").exists()


class TestExportGroundTrack:
    """地面轨迹图层：TLE 由调用方给，轨迹按日界线切段，历元随属性落盘。"""

    TLE = "experiments/satellite_rx/data/iss.tle"
    START = "2026-11-17T00:00:00Z"

    def _export(self, out, *extra):
        return _run(str(out), "--layer", "ground-track", "--tle", self.TLE,
                    "--sat", "ISS", "--start", self.START, *extra)

    def test_writes_split_linestrings_with_tle_epoch(self, tmp_path):
        out = tmp_path / "iss.geojson"
        r = self._export(out)
        assert r.returncode == 0, r.stderr

        fc = _strict_loads(out.read_text(encoding="utf-8"))
        assert fc["type"] == "FeatureCollection"
        assert fc["features"], "90 分钟轨迹至少要有一段"
        assert f"要素：{len(fc['features'])}" in r.stdout

        for feat in fc["features"]:
            assert feat["type"] == "Feature"
            geom = feat["geometry"]
            assert geom["type"] == "LineString"
            assert len(geom["coordinates"]) >= 2
            for lon, lat in geom["coordinates"]:
                assert math.isfinite(lon) and math.isfinite(lat)
                assert -180.0 <= lon <= 180.0
                assert -90.0 <= lat <= 90.0
            props = feat["properties"]
            assert props["role"] == "ground_track"
            assert props["norad_id"] == 25544
            assert props["ellipsoid"] == "WGS-72"
            assert props["unit"] == "km"
            assert props["window_minutes"] == 90.0
            assert props["start_unix"] <= props["end_unix"]
            # 历元是「这条轨迹按哪份根数算的」的唯一凭据，不能省
            assert props["tle_epoch_utc"], "TLE 历元必须落盘"
            assert props["tle_epoch_utc"].endswith("UTC")
            assert str(props["satellite"]).upper().startswith("ISS")

    def test_dateline_jump_is_split_into_segments(self, tmp_path):
        out = tmp_path / "iss.geojson"
        assert self._export(out).returncode == 0
        fc = _strict_loads(out.read_text(encoding="utf-8"))
        for feat in fc["features"]:
            coords = feat["geometry"]["coordinates"]
            for (lon1, _), (lon2, _) in zip(coords, coords[1:]):
                assert abs(lon2 - lon1) <= 180.0, \
                    "同一段内不允许出现跨日界线的 360° 假连线"

    def test_same_start_is_byte_identical(self, tmp_path):
        out = tmp_path / "iss.geojson"
        assert self._export(out).returncode == 0
        first = out.read_text(encoding="utf-8")
        assert self._export(out).returncode == 0
        assert out.read_text(encoding="utf-8") == first, \
            "同一 TLE + 同一 start 必须给出同一份轨迹"

    def test_norad_id_lookup_matches_named_lookup(self, tmp_path):
        named = tmp_path / "by-name.geojson"
        by_id = tmp_path / "by-id.geojson"
        assert self._export(named).returncode == 0
        r = _run(str(by_id), "--layer", "ground-track", "--tle", self.TLE,
                 "--sat", "25544", "--start", self.START)
        assert r.returncode == 0, r.stderr
        assert by_id.read_text(encoding="utf-8") == named.read_text(
            encoding="utf-8")

    def test_missing_tle_arguments_are_rejected(self, tmp_path):
        out = tmp_path / "x.geojson"
        r = _run(str(out), "--layer", "ground-track", "--sat", "ISS")
        assert r.returncode == 2
        assert "--tle" in r.stderr
        assert not out.exists()

    def test_unknown_satellite_fails_without_writing(self, tmp_path):
        out = tmp_path / "x.geojson"
        r = _run(str(out), "--layer", "ground-track", "--tle", self.TLE,
                 "--sat", "NOT-A-SAT", "--start", self.START)
        assert r.returncode == 1
        assert "找不到卫星" in r.stderr
        assert not out.exists(), "失败时不能留下半截文件"


class TestExportFootprint:
    """覆盖圈图层：多边形或日界线切段边界 + 星下点，属性可复核。"""

    TLE = "experiments/satellite_rx/data/iss.tle"
    START = "2026-11-17T00:00:00Z"

    def _export(self, out, *extra):
        return _run(str(out), "--layer", "footprint", "--tle", self.TLE,
                    "--sat", "ISS", "--start", self.START, *extra)

    def test_writes_footprint_and_subpoint(self, tmp_path):
        out = tmp_path / "iss_fp.geojson"
        r = self._export(out)
        assert r.returncode == 0, r.stderr

        fc = _strict_loads(out.read_text(encoding="utf-8"))
        roles = [f["properties"]["role"] for f in fc["features"]]
        assert "footprint_subpoint" in roles

        fp = [f for f in fc["features"]
              if f["properties"]["role"] in ("footprint", "footprint_boundary")]
        assert fp, "覆盖圈不能缺失"
        for feat in fp:
            geom = feat["geometry"]
            assert geom["type"] in ("Polygon", "LineString")
            coords = geom["coordinates"]
            rings = coords if geom["type"] == "Polygon" else [coords]
            for ring in rings:
                assert len(ring) >= 3
                for lon, lat in ring:
                    assert math.isfinite(lon) and math.isfinite(lat)
                    assert -180.0 <= lon <= 180.0
                    assert -90.0 <= lat <= 90.0
            props = feat["properties"]
            assert props["norad_id"] == 25544
            assert props["ellipsoid"] == "WGS-72"
            assert props["unit"] == "km"
            assert 0.0 < props["radius_km"] < 10000.0
            assert 0.0 < props["central_angle_deg"] < 90.0
            assert props["tle_epoch_utc"], "TLE 历元必须落盘"
            assert -90.0 <= props["subpoint_lat_deg"] <= 90.0
            assert -180.0 <= props["subpoint_lon_deg"] <= 180.0

        subpoint = [f for f in fc["features"]
                    if f["properties"]["role"] == "footprint_subpoint"][0]
        assert subpoint["geometry"]["type"] == "Point"
        # 星下点必须落在覆盖圈中心：与属性里的 subpoint_* 一致
        lon, lat = subpoint["geometry"]["coordinates"]
        assert abs(lon - subpoint["properties"]["subpoint_lon_deg"]) < 1e-6
        assert abs(lat - subpoint["properties"]["subpoint_lat_deg"]) < 1e-6

    def test_min_elevation_changes_radius(self, tmp_path):
        wide = tmp_path / "wide.geojson"
        narrow = tmp_path / "narrow.geojson"
        assert self._export(wide, "--min-elevation", "0").returncode == 0
        assert self._export(narrow, "--min-elevation", "30").returncode == 0

        def radius(path):
            fc = _strict_loads(path.read_text(encoding="utf-8"))
            return fc["features"][0]["properties"]["radius_km"]

        assert radius(narrow) < radius(wide), \
            "仰角门限越高，覆盖圈越小"

    def test_same_start_is_byte_identical(self, tmp_path):
        out = tmp_path / "iss_fp.geojson"
        assert self._export(out).returncode == 0
        first = out.read_text(encoding="utf-8")
        assert self._export(out).returncode == 0
        assert out.read_text(encoding="utf-8") == first


class TestSplitRingAtDateline:
    """日界线切段是纯函数，单独钉住，不依赖某颗星的巧合位置。"""

    def _split(self, ring):
        sys.path.insert(0, str(ROOT / "scripts"))
        import export_geojson

        return export_geojson.split_ring_at_dateline(ring)

    def test_closed_ring_without_jump_stays_one_segment(self):
        ring = [(0.0, 0.0), (1.0, 1.0), (0.0, 2.0), (0.0, 0.0)]
        segs = self._split(ring)
        assert len(segs) == 1
        assert segs[0][0] == segs[0][-1]

    def test_jump_is_cut_at_both_edges(self):
        ring = [(0.0, 179.0), (0.0, -179.0), (0.0, 179.0), (0.0, 179.0)]
        segs = self._split(ring)
        assert len(segs) >= 2, "跨日界线的圈必须被切开"
        for seg in segs:
            for (lat1, lon1), (lat2, lon2) in zip(seg, seg[1:]):
                assert abs(lon2 - lon1) <= 180.0
        edges = {seg[0][1] for seg in segs} | {seg[-1][1] for seg in segs}
        assert edges & {180.0, -180.0}, "切点必须落在 ±180"
