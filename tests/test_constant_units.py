# SPDX-License-Identifier: MIT
"""第5件：光速/引力常数的单位后缀与规范值（值不动，只改名）。

约定：
  - 光速 ``_MPS`` = m/s，``_KMPS`` = km/s；
  - 引力常数 ``_M3_S2`` = m³/s²，``_KM3_S2`` = km³/s²。

两个规范值必须原样保留（只允许改名，不允许改数值、不允许统一）：
  - GPS ICD（IS-GPS-200）: 3.986005e14 m³/s²；
  - WGS-72（Spacetrack Report #3）: 3.986008e5 km³/s²。
"""
import unittest

from mbdsdr_ai import astronomy, acquisition, gpredict_adapter as gp, gps_nav, orbit
from mbdsdr_ai import orbit_determination as odet
from mbdsdr_ai import orbit_dynamics as od
from mbdsdr_ai import vna_cal


class SpeedOfLightTest(unittest.TestCase):
    def test_values_are_the_same_physical_constant(self):
        self.assertEqual(od.C_LIGHT_MPS, 299792458.0)
        self.assertEqual(gps_nav.C_LIGHT_MPS, 299792458.0)
        self.assertEqual(acquisition.C_LIGHT_MPS, 299792458.0)
        self.assertEqual(vna_cal.C_LIGHT_MPS, 299792458.0)
        self.assertAlmostEqual(od.C_LIGHT_KMPS, 299792.458, places=9)
        self.assertAlmostEqual(orbit.C_LIGHT_KMPS, 299792.458, places=9)
        self.assertAlmostEqual(gp.C_LIGHT_KMPS, 299792.458, places=9)
        self.assertAlmostEqual(odet.C_LIGHT_KMPS, 299792.458, places=9)

    def test_mps_and_kmps_differ_by_exactly_1000(self):
        self.assertAlmostEqual(od.C_LIGHT_MPS / od.C_LIGHT_KMPS, 1000.0, places=9)
        self.assertAlmostEqual(odet.C_LIGHT_MPS / odet.C_LIGHT_KMPS, 1000.0, places=9)

    def test_old_ambiguous_names_are_gone(self):
        for mod in (orbit, od, odet, gps_nav, gp, acquisition, vna_cal):
            for old in ("C_LIGHT", "C_LIGHT_MS", "C_LIGHT_KMS"):
                self.assertFalse(hasattr(mod, old),
                                 f"{mod.__name__} 仍保留无单位/旧后缀的 {old}")


class GravityConstantTest(unittest.TestCase):
    def test_gps_icd_value_unchanged(self):
        """GPS ICD 规范值：3.986005e14 m³/s²。"""
        self.assertEqual(gps_nav.MU_EARTH_M3_S2, 3.986005e14)

    def test_wgs72_value_unchanged(self):
        """WGS-72 规范值：3.986008e5 km³/s²（与 GPS ICD 不是同一椭球口径）。"""
        self.assertEqual(gp.GE_KM3_S2, 3.986008e5)

    def test_km_scale_constants_keep_their_values(self):
        self.assertEqual(od.MU_EARTH_KM3_S2, 398600.4418)
        self.assertEqual(odet.GM_EARTH_KM3_S2, 398600.4418)
        self.assertEqual(odet.GM_MOON_KM3_S2, 4902.8005)

    def test_three_spec_values_are_not_unified(self):
        """3.986005e14(m) / 398600.4418(km) / 3.986008e5(km) 三套口径必须都在。"""
        gps_icd_km = gps_nav.MU_EARTH_M3_S2 / 1e9          # 398600.5
        igs_km = od.MU_EARTH_KM3_S2                         # 398600.4418
        wgs72_km = gp.GE_KM3_S2                             # 398600.8
        self.assertAlmostEqual(gps_icd_km, 398600.5, places=6)
        self.assertAlmostEqual(gps_icd_km - igs_km, 0.0582, places=9)
        self.assertAlmostEqual(wgs72_km - igs_km, 0.3582, places=9)
        self.assertNotEqual(gps_icd_km, wgs72_km)

    def test_old_ambiguous_names_are_gone(self):
        for mod in (od, odet):
            self.assertFalse(hasattr(mod, "MU_EARTH"))
            self.assertFalse(hasattr(mod, "GM_EARTH"))
        self.assertFalse(hasattr(odet, "GM_MOON"))
        self.assertFalse(hasattr(gp, "GE"))


class AstronomyDeadConstantTest(unittest.TestCase):
    def test_zero_use_radius_is_deleted(self):
        """EARTH_RADIUS_KM=6378.137 零引用且与 new_spacetime 的平均半径同名不同义。"""
        self.assertFalse(hasattr(astronomy, "EARTH_RADIUS_KM"))
        # 删除要外科手术：同段的地球扁率仍在。
        self.assertAlmostEqual(astronomy.EARTH_FLATTENING, 1.0 / 298.257223563,
                               places=15)


if __name__ == "__main__":
    unittest.main()
