"""P0：WGS-72(km) 与 WGS-84(m) 两个椭球入口的边界对照。

同一组经纬高在两套椭球下**不应相等**；差值必须落在米级预期区间。
另外故意把 WGS-72 的 ECEF 喂给 WGS-84 反解，必须被测试抓住（高度差 ~2 m）。
"""
import math
import unittest

from mbdsdr_ai import orbit
from mbdsdr_ai.geodesy import CoordinateConverter

LAT_DEG, LON_DEG, ALT_KM = 39.9, 116.4, 0.0


class EllipsoidBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.ecef72_km = orbit.wgs72_geodetic_to_ecef_km(LAT_DEG, LON_DEG, ALT_KM)
        self.ecef84_m = CoordinateConverter.llh_to_ecef_m(
            math.radians(LAT_DEG), math.radians(LON_DEG), ALT_KM * 1000.0)

    def test_same_llh_differs_at_meter_level(self):
        """两套椭球各算一次：差值应为米级，既不相等也不应大到公里级。"""
        d = math.dist([v * 1000.0 for v in self.ecef72_km], self.ecef84_m)
        # 实测约 1.93 m（a 差 2 m，f 差 ~2.6e-6）。区间固定为可解释的量级。
        self.assertGreater(d, 1.0, f"WGS-72/WGS-84 ECEF 差 {d:.3f} m 过小，疑似被统一")
        self.assertLess(d, 5.0, f"WGS-72/WGS-84 ECEF 差 {d:.3f} m 过大，量级不对")

    def test_feeding_wgs72_ecef_to_wgs84_inverse_is_caught(self):
        """故意喂错：WGS-72 的 ECEF(km) 当成 WGS-84 的米，高度差必须 >1 m。"""
        _lat, _lon, h84_m = CoordinateConverter.ecef_to_llh(
            *(v * 1000.0 for v in self.ecef72_km))
        self.assertGreater(abs(h84_m), 1.0,
                           f"混用两套椭球只产生 {h84_m:.3f} m 高度差，测试抓不住")

    def test_wgs72_inverse_recovers_input(self):
        lat, lon, alt = orbit.wgs72_ecef_km_to_llh(*self.ecef72_km)
        self.assertAlmostEqual(lat, LAT_DEG, places=9)
        self.assertAlmostEqual(lon, LON_DEG, places=9)
        self.assertAlmostEqual(alt, ALT_KM, places=6)

    def test_wgs72_alt_not_produced_by_wgs84_inverse(self):
        """反证：星下点高度若改用 WGS-84 反解，会与 WGS-72 结果差 ~2 m。"""
        alt72_km = orbit.wgs72_ecef_km_to_llh(*self.ecef72_km)[2]
        alt84_km = CoordinateConverter.ecef_to_llh(
            *(v * 1000.0 for v in self.ecef72_km))[2] / 1000.0
        self.assertAlmostEqual(alt72_km, 0.0, places=6)
        self.assertGreater(abs(alt72_km - alt84_km) * 1000.0, 1.0)

    def test_ambiguous_public_names_are_gone(self):
        """边界显式化：裸名字不能再被调用，只能走带椭球/单位的名字。"""
        self.assertFalse(hasattr(orbit, "geodetic_to_ecef"))
        self.assertFalse(hasattr(CoordinateConverter, "llh_to_ecef"))


if __name__ == "__main__":
    unittest.main()
