"""
tests/test_astronomy.py — Stellarium 天球引擎移植验证
======================================================

覆盖:
  1. 坐标往返 ICRF(J2000) <-> 地平, 误差 < 0.01° (celestial_geometry)
  2. GMST / 时间内核 (jtime) 正确性
  3. 透视投影正反投影往返 (StelProjector pinhole)
  4. TLE/SGP4 传播 (缓存真实 TLE, 已知时间位置可复现)
  5. 星等渲染 / COSPAR 解析
  6. RFSkyView 透视投影: 天顶->屏幕中心, 地平->屏幕下方

运行:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_astronomy.py -v
"""
import os
import sys
import math
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402


# ----------------------------------------------------------------------
class TestCoordinateChain(unittest.TestCase):
    """ICRF <-> 地平 坐标往返, 误差 < 0.01°。"""

    def setUp(self):
        from mbdsdr_ai.celestial_geometry import GroundStation
        # 长春 (真实城市站, 仅用于几何测试)
        self.gs = GroundStation(126.63, 45.75, 200.0, "Changchun")
        self.jd = 2461308.5  # 2026-09-26 前后

    def test_j2000_altaz_roundtrip(self):
        from mbdsdr_ai.celestial_geometry import (
            vec_from_radec, j2000_to_altaz, altaz_to_j2000, normalize)
        for ra, dec in [(100.0, 30.0), (279.234, 38.784), (101.287, -16.716),
                        (0.0, 0.0), (310.358, 45.28)]:
            v0 = vec_from_radec(ra, dec)
            v_altaz = j2000_to_altaz(v0, self.gs, self.jd)
            v_back = altaz_to_j2000(v_altaz, self.gs, self.jd)
            dot = float(np.dot(normalize(v0), normalize(v_back)))
            self.assertAlmostEqual(dot, 1.0, places=5,
                                   msg=f"ra={ra} dec={dec} dot={dot}")

    def test_altaz_inverse_recovers_direction(self):
        """地平 -> J2000 -> 地平 往返 (角度域)。"""
        from mbdsdr_ai.celestial_geometry import (
            vec_from_azalt, altaz_to_j2000, j2000_to_altaz,
            azalt_from_vec)
        for az, alt in [(45.0, 60.0), (180.0, 10.0), (270.0, 45.0)]:
            v = vec_from_azalt(az, alt)
            vj = altaz_to_j2000(v, self.gs, self.jd)
            v2 = j2000_to_altaz(vj, self.gs, self.jd)
            az2, alt2 = azalt_from_vec(v2)
            daz = abs((az2 - az + 180) % 360 - 180)
            self.assertLess(daz, 0.01, f"az {az}->{az2}")
            self.assertLess(abs(alt2 - alt), 0.01, f"alt {alt}->{alt2}")


class TestTimeKernel(unittest.TestCase):
    def test_gmst_known_value(self):
        from mbdsdr_ai.skyengine.jtime import gmst_deg
        # J2000.0 GMST ≈ 280.46° (IAU 1982, 18h41m50s)
        g = gmst_deg(2451545.0)
        self.assertAlmostEqual(g % 360.0, 280.4606, delta=0.5)

    def test_unix_jd_roundtrip(self):
        from mbdsdr_ai.skyengine.jtime import unix_to_jd, jd_to_unix
        u = 1700000000.0
        self.assertAlmostEqual(jd_to_unix(unix_to_jd(u)), u, places=3)

    def test_time_flow_per_navigation(self):
        from mbdsdr_ai.skyengine.jtime import TimeKernel
        k = TimeKernel(jd=2461308.5, speed=60.0)  # 60 秒/现实秒
        jd0 = k.jd
        k.advance(1.0)  # 现实 1s -> 内部 60s
        self.assertAlmostEqual((k.jd - jd0) * 86400.0, 60.0, delta=1e-4)


class TestPerspectiveProjection(unittest.TestCase):
    def test_roundtrip(self):
        from mbdsdr_ai.celestial_geometry import PerspectiveProjection
        p = PerspectiveProjection()
        for az, alt in [(45, 60), (0, 30), (180, 10), (90, 45), (270, 80)]:
            x, y = p.project_azalt(az, alt)
            az2, alt2 = p.unproject_azalt(x, y)
            self.assertAlmostEqual(az2, az, delta=0.01)
            self.assertAlmostEqual(alt2, alt, delta=0.01)

    def test_center_maps_to_origin(self):
        from mbdsdr_ai.celestial_geometry import PerspectiveProjection
        import numpy as np
        p = PerspectiveProjection()
        x, y = p.project_vec(np.array([0.0, 0.0, -1.0]))
        self.assertAlmostEqual(x, 0.0, places=6)
        self.assertAlmostEqual(y, 0.0, places=6)

    def test_behind_camera_inf(self):
        from mbdsdr_ai.celestial_geometry import PerspectiveProjection
        import numpy as np
        p = PerspectiveProjection()
        x, y = p.project_vec(np.array([1.0, 0.0, 0.0]))  # 90° off = 背面
        self.assertTrue(math.isinf(x))


class TestStarsAndSatMeta(unittest.TestCase):
    def test_star_draw_monotonic(self):
        from mbdsdr_ai.skyengine.stars import star_draw
        r_bright, b_bright = star_draw(-1.46)
        r_faint, b_faint = star_draw(4.0)
        self.assertGreater(r_bright, r_faint)
        self.assertGreater(b_bright, b_faint)
        self.assertEqual(star_draw(8.0), (0.0, 0.0))

    def test_cospar_parse(self):
        from mbdsdr_ai.skyengine.satellites import cospar_from_tle
        self.assertEqual(
            cospar_from_tle("1 25544U 98067A   26268.43198945"), "1998-067A")
        self.assertEqual(
            cospar_from_tle("1 25338U 98030A   26268.50000000"), "1998-030A")


class TestTLEPropagation(unittest.TestCase):
    """用本地缓存的真实 ISS TLE 传播, 已知时间位置可复现。"""

    def _iss_tle(self):
        import os
        cf = os.path.expanduser("~/.mbdsdr/tle_cache/25544.tle")
        if not os.path.exists(cf):
            self.skipTest("无本地 ISS TLE 缓存")
        lines = open(cf).read().splitlines()
        return lines[1], lines[2]

    def test_propagation_reproducible(self):
        from sgp4.api import Satrec
        l1, l2 = self._iss_tle()
        sat = Satrec.twoline2rv(l1, l2)
        jd = 2461308.5
        e1, r1, _ = sat.sgp4(int(jd), jd - int(jd))
        e2, r2, _ = sat.sgp4(int(jd), jd - int(jd))
        self.assertEqual(e1, 0)
        for a, b in zip(r1, r2):
            self.assertAlmostEqual(a, b, places=6)

    def test_state_azalt_in_range(self):
        from mbdsdr_ai import orbit
        from sgp4.api import Satrec
        l1, l2 = self._iss_tle()
        sat = Satrec.twoline2rv(l1, l2)
        st = orbit._state_from_satrec(sat, "ISS", 2461308.5,
                                      43.817, 125.323, 0.2)
        self.assertIsNotNone(st)
        self.assertTrue(0.0 <= st["azimuth"] <= 360.0)
        self.assertTrue(-90.0 <= st["elevation"] <= 90.0)
        self.assertGreater(st["range_km"], 100.0)


class TestRFSkyViewPerspective(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_zenith_screen_center(self):
        from desktop.rf_sky_view import RFSkyView
        v = RFSkyView(); v.resize(600, 600)
        cx, cy = v._sky_center()
        p = v._sky_to_screen(0.0, 90.0)
        self.assertAlmostEqual(p.x(), cx, places=3)
        self.assertAlmostEqual(p.y(), cy, places=3)

    def test_fov_range(self):
        from desktop.rf_sky_view import RFSkyView
        v = RFSkyView()
        self.assertEqual(v.view_state.min_fov, 5.0)
        self.assertEqual(v.view_state.max_fov, 120.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
