"""星下点 / 地面轨迹 / 覆盖圈验收用例。

- 星下点与 skyfield 独立实现对拍（同一 TLE、同一时刻）；
- 覆盖圈半径用「几何解法 vs 绘制顶点」双向核对（含仰角门限）；
- 日界线断开有独立用例；
- 反证：单位/椭球/门限任一处改错，对应用例必须红。
"""
import math
import unittest
from datetime import datetime, timezone

from mbdsdr_ai.ground_track import (
    DEFAULT_MIN_ELEVATION_DEG,
    Subpoint,
    destination_point,
    footprint_central_angle_deg,
    footprint_ring,
    ground_track,
    satellite_footprint,
    satellite_subpoint,
    split_at_dateline,
)
from mbdsdr_ai.orbit import WGS72_A

# 仓库内离线 TLE（experiments/satellite_rx/data/iss.tle，历元 2026-297）
ISS_TLE = (
    "1 25544U 98067A   26297.20915682  .00007800  00000+0  14938-3 0  9991",
    "2 25544  51.6335  21.6229 0006884 302.9098  57.1228 15.49030149591752",
)
WHEN = datetime(2026, 10, 24, 6, 0, 0, tzinfo=timezone.utc).timestamp()

try:
    from skyfield.api import EarthSatellite, load
    _HAS_SKYFIELD = True
except Exception:                                    # pragma: no cover
    _HAS_SKYFIELD = False


def _central_angle_deg(lat1, lon1, lat2, lon2):
    """两点球面中心角（独立于 destination_point 的 haversine）。"""
    p1, l1 = math.radians(lat1), math.radians(lon1)
    p2, l2 = math.radians(lat2), math.radians(lon2)
    dlat, dlon = p2 - p1, l2 - l1
    a = (math.sin(dlat / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2)
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(a))))


class SubpointTest(unittest.TestCase):
    def test_matches_skyfield_independent_implementation(self):
        """同一 TLE、同一时刻，与 skyfield 的 subpoint() 比差。"""
        if not _HAS_SKYFIELD:                        # pragma: no cover
            self.skipTest("skyfield 未安装")
        ours = satellite_subpoint("ISS (ZARYA)", when_unix=WHEN, tle=ISS_TLE)
        ts = load.timescale()
        ref = EarthSatellite(ISS_TLE[0], ISS_TLE[1], ts=ts)
        geo = ref.at(ts.from_datetime(
            datetime.fromtimestamp(WHEN, tz=timezone.utc))).subpoint()
        dlat = abs(ours.lat_deg - geo.latitude.degrees)
        dlon = abs(ours.lon_deg - geo.longitude.degrees)
        # TEME->ECEF 用 GMST、skyfield 用完整 ITRF，差应远小于 0.05°。
        self.assertLess(dlat, 0.05, f"纬差 {dlat:.5f}° 超界（ours={ours}）")
        self.assertLess(dlon, 0.05, f"经差 {dlon:.5f}° 超界（ours={ours}）")
        # 反证：单位若从 km 变 m，高度会差 1000 倍，这里必须像 LEO。
        self.assertTrue(300.0 < ours.sat_alt_km < 600.0,
                        f"高度 {ours.sat_alt_km} km 不像 LEO，疑似单位错")

    def test_rejects_unknown_builtin_name(self):
        with self.assertRaises(KeyError):
            satellite_subpoint("NOT A SATELLITE", when_unix=WHEN)

    def test_subpoint_uses_wgs72_inverse_not_wgs84(self):
        """反证：星下点若改走 WGS-84 反解，高度会差 ~2 m，本用例必红。"""
        from sgp4.api import Satrec
        from mbdsdr_ai.geodesy import CoordinateConverter
        from mbdsdr_ai.orbit import teme_to_ecef_km, wgs72_ecef_km_to_llh

        jd = 2440587.5 + WHEN / 86400.0
        err, r_teme, _ = Satrec.twoline2rv(*ISS_TLE).sgp4(int(jd), jd - int(jd))
        self.assertEqual(err, 0)
        r_ecef = teme_to_ecef_km(r_teme, jd)
        _lat72, _lon72, h72 = wgs72_ecef_km_to_llh(*r_ecef)
        _lat84, _lon84, h84_m = CoordinateConverter.ecef_to_llh(
            *(v * 1000.0 for v in r_ecef))
        ours = satellite_subpoint("ISS (ZARYA)", when_unix=WHEN, tle=ISS_TLE)
        self.assertAlmostEqual(ours.sat_alt_km, h72, places=9)
        self.assertGreater(abs(h72 - h84_m / 1000.0), 0.001,
                           "两条椭球链高度差 <1 m，反证失去分辨力")


class GroundTrackTest(unittest.TestCase):
    def test_track_covers_window_and_splits_dateline(self):
        track = ground_track("ISS (ZARYA)", when_unix=WHEN,
                             minutes=90, step_s=30, tle=ISS_TLE)
        self.assertEqual(track["ellipsoid"], "WGS-72")
        self.assertEqual(len(track["points"]), 181)       # 0..90min / 30s
        self.assertGreaterEqual(len(track["segments"]), 2,
                                "90 min 轨迹应至少跨一次日界线")
        for seg in track["segments"]:
            for a, b in zip(seg, seg[1:]):
                self.assertLessEqual(abs(b.lon_deg - a.lon_deg), 180.0)
        # 所有点经度都在合法区间
        for p in track["points"]:
            self.assertTrue(-180.0 <= p.lon_deg <= 180.0)
            self.assertTrue(-90.0 <= p.lat_deg <= 90.0)

    def test_split_function_breaks_on_lon_jump(self):
        pts = [Subpoint(0.0, 179.0, 400.0, 0.0),
               Subpoint(0.0, -179.0, 400.0, 1.0)]
        segs = split_at_dateline(pts)
        self.assertEqual([len(s) for s in segs], [1, 1])

    def test_invalid_window_rejected(self):
        with self.assertRaises(ValueError):
            ground_track("ISS (ZARYA)", when_unix=WHEN, minutes=0, tle=ISS_TLE)


class FootprintTest(unittest.TestCase):
    def test_horizon_angle_matches_law_of_cosines(self):
        """ε=0 时中心角应等于地平线几何 acos(Re/(Re+h))。"""
        alt = 420.0
        got = footprint_central_angle_deg(alt, 0.0)
        want = math.degrees(math.acos(WGS72_A / (WGS72_A + alt)))
        self.assertAlmostEqual(got, want, places=9)

    def test_elevation_gate_changes_elevation_independently(self):
        """把解出的 λ 代回几何，地面点看到的仰角必须等于门限。"""
        alt = 420.0
        for eps_deg in (0.0, 5.0, 15.0):
            lam = math.radians(footprint_central_angle_deg(alt, eps_deg))
            r = WGS72_A + alt
            gx, sx = WGS72_A, r * math.cos(lam)
            sy = r * math.sin(lam)
            los = (sx - gx, sy, 0.0)
            norm = math.hypot(*los)
            elev = math.degrees(math.asin(los[0] / norm))
            self.assertAlmostEqual(elev, eps_deg, places=6)

    def test_ring_vertices_are_at_drawn_radius(self):
        """绘制顶点逐个回算中心角 = 几何解出的 λ（几何解 vs 绘制半径）。"""
        fp = satellite_footprint("ISS (ZARYA)", when_unix=WHEN,
                                 min_elevation_deg=5.0, tle=ISS_TLE)
        c = fp["subpoint"]
        self.assertAlmostEqual(fp["radius_km"],
                               math.radians(fp["central_angle_deg"]) * WGS72_A,
                               places=6)
        for lat, lon in fp["ring"]:
            ang = _central_angle_deg(c.lat_deg, c.lon_deg, lat, lon)
            self.assertAlmostEqual(ang, fp["central_angle_deg"], places=6)
        self.assertEqual(fp["ring"][0], fp["ring"][-1])

    def test_elevation_gate_shrinks_footprint(self):
        a0 = footprint_central_angle_deg(420.0, 0.0)
        a10 = footprint_central_angle_deg(420.0, 10.0)
        self.assertGreater(a0 - a10, 3.0,
                           "仰角门限 10° 若被忽略，覆盖圈不会缩小 3° 以上")
        ring = footprint_ring(0.0, 0.0, 10.0)
        self.assertEqual(len(ring), 73)
        self.assertEqual(ring[0], ring[-1])

    def test_default_min_elevation_is_applied(self):
        fp = satellite_footprint("ISS (ZARYA)", when_unix=WHEN, tle=ISS_TLE)
        self.assertEqual(fp["min_elevation_deg"], DEFAULT_MIN_ELEVATION_DEG)
        want = footprint_central_angle_deg(fp["subpoint"].sat_alt_km,
                                           DEFAULT_MIN_ELEVATION_DEG)
        self.assertAlmostEqual(fp["central_angle_deg"], want, places=9)


class DestinationPointTest(unittest.TestCase):
    def test_pole_seam_lon_wrap(self):
        lat, lon = destination_point(0.0, 179.5, 90.0, 2.0)
        self.assertTrue(-180.0 <= lon <= 180.0)
        self.assertGreater(lat, 0.0)


if __name__ == "__main__":
    unittest.main()
