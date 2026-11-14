# SPDX-License-Identifier: MIT
"""P0：LLA<->ECEF 唯一内核的已知向量回归（WGS-72 与 WGS-84 都必须能表达）。

背景：仓里曾有六份实现（geodesy / orbit_dynamics / orbit_determination /
satdump_adapter / orbit.wgs72_* / decoders 内联）。其中 ``orbit_dynamics``
的反解返回 (经度, 纬度, 高度)、其余返回 (纬度, 经度, 高度)，接错就是经纬
对调且不报错；单位也有米与公里两套。本用例把「椭球可表达、单位后缀、
返回顺序」三件事钉在已知向量上，谁的实现漂了都会红。

基准值来源：RTKLIB ``pos2ecef``/``ecef2pos`` 公式
（a=6378137, f=1/298.257223563）与 WGS-72（a=6378135, f=1/298.26）
在同一组经纬高上独立手算到 1e-6 m。
"""
import math
import unittest

from mbdsdr_ai import geodesy
from mbdsdr_ai import orbit_determination as odet
from mbdsdr_ai import orbit_dynamics as od
from mbdsdr_ai import satdump_adapter as sa
from mbdsdr_ai.geodesy import WGS72, WGS84

LAT, LON, ALT_M = 39.9, 116.4, 50.0

# 39.9N / 116.4E / 50m 的 ECEF(m) 基准
GOLDEN_84_M = (-2178657.082725, 4388876.233551, 4069505.747982)
GOLDEN_72_M = (-2178656.371605, 4388874.801010, 4069504.674560)


def _dist(a, b):
    return math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b)))


class KnownVectorTest(unittest.TestCase):
    def test_wgs84_anchors(self):
        """赤道/本初子午线/北极三个定义式锚点（米）。"""
        self.assertEqual(geodesy.geodetic_to_ecef_m(0.0, 0.0, 0.0),
                         (WGS84.a_m, 0.0, 0.0))
        x, y, z = geodesy.geodetic_to_ecef_m(0.0, 90.0, 0.0)
        self.assertAlmostEqual(x, 0.0, places=6)
        self.assertAlmostEqual(y, WGS84.a_m, places=6)
        x, y, z = geodesy.geodetic_to_ecef_m(90.0, 0.0, 0.0)
        self.assertAlmostEqual(x, 0.0, places=6)
        self.assertAlmostEqual(y, 0.0, places=6)
        self.assertAlmostEqual(z, 6356752.314245, places=6)

    def test_wgs72_anchors_and_two_meter_gap(self):
        """WGS-72 必须能独立表达：赤道半径差恰 2.0 m，极半径同步不同。"""
        self.assertEqual(geodesy.geodetic_to_ecef_m(0.0, 0.0, 0.0, WGS72),
                         (6378135.0, 0.0, 0.0))
        self.assertAlmostEqual(
            geodesy.geodetic_to_ecef_m(90.0, 0.0, 0.0, WGS72)[2],
            6356750.520016, places=6)
        self.assertAlmostEqual(WGS84.a_m - WGS72.a_m, 2.0, places=12)

    def test_golden_vector_wgs84_m_and_order(self):
        """米制前向==基准；反解返回 (lat, lon, alt) 而不是 (lon, lat, alt)。"""
        got = geodesy.geodetic_to_ecef_m(LAT, LON, ALT_M)
        self.assertLess(_dist(got, GOLDEN_84_M), 1e-6, got)

        lat, lon, alt = geodesy.ecef_to_geodetic_m(*GOLDEN_84_M)
        self.assertAlmostEqual(lat, LAT, places=9)     # 第一项必须是纬度
        self.assertAlmostEqual(lon, LON, places=9)     # 第二项必须是经度
        self.assertAlmostEqual(alt, ALT_M, places=6)

    def test_golden_vector_wgs72_km(self):
        """公里制 + WGS-72：同一组经纬高落在另一套基准上。"""
        got_km = geodesy.geodetic_to_ecef_km(LAT, LON, ALT_M / 1000.0, WGS72)
        self.assertLess(_dist([v * 1000.0 for v in got_km], GOLDEN_72_M), 1e-6)

        lat, lon, alt_km = geodesy.ecef_to_geodetic_km(*got_km, WGS72)
        self.assertAlmostEqual(lat, LAT, places=9)
        self.assertAlmostEqual(lon, LON, places=9)
        self.assertAlmostEqual(alt_km, ALT_M / 1000.0, places=9)

    def test_units_are_not_interchangeable(self):
        """m 与 km 两个入口不能互相顶替：差 1000 倍必须被量出来。"""
        m = geodesy.geodetic_to_ecef_m(LAT, LON, ALT_M)
        km = geodesy.geodetic_to_ecef_km(LAT, LON, ALT_M / 1000.0)
        self.assertLess(_dist([v / 1000.0 for v in m], km), 1e-9)

    def test_pole_inverse_has_no_zero_division(self):
        """x=y=0 的极点：直接 ±90°，不触发 p/cos(lat) 除零。"""
        b = WGS84.b_m
        lat, lon, alt = geodesy.ecef_to_geodetic_m(0.0, 0.0, b)
        self.assertAlmostEqual(lat, 90.0, places=9)
        self.assertEqual(lon, 0.0)
        self.assertAlmostEqual(alt, 0.0, places=6)
        lat_s, _lon_s, alt_s = geodesy.ecef_to_geodetic_m(0.0, 0.0, -b)
        self.assertAlmostEqual(lat_s, -90.0, places=9)
        self.assertAlmostEqual(alt_s, 0.0, places=6)

    def test_coordinate_converter_boundary_is_radians(self):
        """CoordinateConverter 保持 RTKLIB 的弧度/米边界，内核是度。"""
        lat_r, lon_r, h_m = geodesy.CoordinateConverter.ecef_to_llh(*GOLDEN_84_M)
        self.assertAlmostEqual(math.degrees(lat_r), LAT, places=9)
        self.assertAlmostEqual(math.degrees(lon_r), LON, places=9)
        self.assertAlmostEqual(h_m, ALT_M, places=6)
        back = geodesy.CoordinateConverter.llh_to_ecef_m(lat_r, lon_r, h_m)
        self.assertLess(_dist(back, GOLDEN_84_M), 1e-6)


class DelegationTest(unittest.TestCase):
    """四条历史入口必须与内核给出同一个答案（不能只是名字像）。"""

    def test_wgs72_entry_matches_kernel(self):
        self.assertLess(
            _dist(od.geodetic_to_ecef(LAT, LON, ALT_M), GOLDEN_72_M), 1e-6)
        self.assertEqual(od.WGS72_A, WGS72.a_m)
        lat, lon, alt = od.ecef_to_geodetic(GOLDEN_72_M)
        self.assertAlmostEqual(lat, LAT, places=9)
        self.assertAlmostEqual(lon, LON, places=9)
        self.assertAlmostEqual(alt, ALT_M, places=6)

    def test_wgs84_km_entries_match_kernel(self):
        ref_km = geodesy.geodetic_to_ecef_km(LAT, LON, ALT_M / 1000.0, WGS84)
        self.assertLess(_dist(odet.geodetic_to_ecef(LAT, LON, ALT_M / 1000.0),
                              ref_km), 1e-12)
        self.assertLess(_dist(sa.lla_to_ecef(LAT, LON, ALT_M / 1000.0),
                              ref_km), 1e-12)

    def test_wgs84_inverse_entries_agree_on_order(self):
        # 各喂各自椭球的 ECEF：WGS-72 链喂 GOLDEN_72，WGS-84 链喂 GOLDEN_84。
        for tag, out, alt_scale in (
            ("orbit_dynamics", od.ecef_to_geodetic(GOLDEN_72_M), 1.0),
            ("satdump_adapter", sa.ecef_to_lla(
                tuple(v / 1000.0 for v in GOLDEN_84_M)), 1000.0),
        ):
            lat, lon, alt = out
            self.assertAlmostEqual(lat, LAT, places=7, msg=tag)
            self.assertAlmostEqual(lon, LON, places=7, msg=tag)
            self.assertAlmostEqual(alt * alt_scale, ALT_M, places=3, msg=tag)

    def test_cross_ellipsoid_feed_is_visible(self):
        """把 WGS-84 的 ECEF 喂进 WGS-72 反解：纬度偏 ~1.8e-6°、高度偏 ~1.9 m。"""
        lat72, _lon72, alt72 = od.ecef_to_geodetic(GOLDEN_84_M)
        self.assertGreater(abs(lat72 - LAT), 1e-6)
        self.assertGreater(abs(alt72 - ALT_M), 1.0)

    def test_ellipsoids_are_not_unified(self):
        """同一组经纬高在两套椭球下的 ECEF 差必须保持米级（~1.93 m）。"""
        d = _dist(GOLDEN_84_M, GOLDEN_72_M)
        self.assertGreater(d, 1.0, f"WGS-72/WGS-84 只差 {d:.4f} m，疑似被统一")
        self.assertLess(d, 5.0, f"WGS-72/WGS-84 差 {d:.4f} m，量级不对")
        # 反证：把 WGS-72 的 ECEF 喂给 WGS-84 反解，高度必须偏 >1 m
        _lat, _lon, h84 = geodesy.ecef_to_geodetic_m(*GOLDEN_72_M, WGS84)
        self.assertGreater(abs(h84 - ALT_M), 1.0,
                           f"混用椭球只差 {abs(h84 - ALT_M):.3f} m，抓不住")


if __name__ == "__main__":
    unittest.main()
