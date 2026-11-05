# SPDX-License-Identifier: MIT
"""
tests/test_astronomy.py — 天文坐标变换验证
======================================================

覆盖:
  1. 坐标往返 ICRF(J2000) <-> 地平, 误差 < 0.01° (celestial_geometry)
  2. GMST / 时间内核 (jtime) 正确性
  3. 透视投影正反投影往返 (pinhole)
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
        # 示例城市 (真实城市站, 仅用于几何测试)
        self.gs = GroundStation(126.63, 45.75, 200.0, "ExampleSite")
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
        r_bright, b_bright, _ = star_draw(-1.46)
        r_faint, b_faint, _ = star_draw(4.0)
        self.assertGreater(r_bright, r_faint)
        self.assertGreater(b_bright, b_faint)
        self.assertEqual(star_draw(8.0), (0.0, 0.0, 0.0))

    def test_star_draw_small_star_r3_compensation(self):
        # StelSkyDrawer.cpp:420：r<1.2 的小星亮度按 r³/1.728 补偿。
        # 6 等星 r≈0.6px，无补偿时亮度 ≈0.06，补偿后仍可见（>0.01），
        # 且严格小于 1 等星亮度（单调性不被补偿打破）。
        from mbdsdr_ai.skyengine.stars import star_draw
        _r6, b6, _ = star_draw(6.0)
        _r1, b1, _ = star_draw(1.0)
        self.assertGreater(b6, 0.005)
        self.assertGreater(b1, b6)

    def test_star_draw_sqrt_compression(self):
        # 大星平方根压缩：未压缩映射 2.6px → 压缩后 1.2+√1.4 ≈ 2.38，
        # 必须严格小于未压缩值（不会糊屏），且仍大于 3 等星。
        from mbdsdr_ai.skyengine.stars import star_draw
        r_bright, _, _ = star_draw(-1.46)
        r_mid, _, _ = star_draw(3.0)
        self.assertLess(r_bright, 2.45)
        self.assertGreater(r_bright, r_mid)

    def test_star_draw_bloom_only_bright(self):
        from mbdsdr_ai.skyengine.stars import star_draw
        _r, _b, bloom_sirius = star_draw(-1.46)
        _r, _b, bloom_faint = star_draw(4.0)
        self.assertGreater(bloom_sirius, 0.0)
        self.assertEqual(bloom_faint, 0.0)

    def test_star_draw_bortle_clamps_limit(self):
        # 光害极限星等 clamp：Bortle 9 下 5 等星（>4.4 极限）不可见。
        from mbdsdr_ai.skyengine.stars import star_draw
        self.assertEqual(star_draw(5.0, bortle=9), (0.0, 0.0, 0.0))
        self.assertGreater(star_draw(4.0, bortle=9)[0], 0.0)

    def test_star_draw_monotonic_across_planet_magnitudes(self):
        # 行星接入同一模型后必须继续单调：旧 frac 的 min(1.0,...) 上限 clamp
        # 会把所有 vmag<-1.5 的天体压成同大同亮（金星 -4.70 与木星 -1.94
        # 画出来一模一样）。半径要一路单调；亮度到 1.0 饱和是对的（alpha
        # 不可能超过满值），差异由半径与辉光承担。
        from mbdsdr_ai.skyengine.stars import star_draw
        r_venus, b_venus, bl_venus = star_draw(-4.70)
        r_jup, b_jup, bl_jup = star_draw(-1.94)
        r_sat, _b_sat, _bl_sat = star_draw(0.91)
        self.assertGreater(r_venus, r_jup)
        self.assertGreater(r_jup, r_sat)
        self.assertEqual(b_venus, 1.0)
        self.assertEqual(b_jup, 1.0)               # 亮度到顶饱和（alpha 无余量）
        self.assertGreaterEqual(bl_venus, bl_jup)  # 差异由半径与辉光承担
        self.assertGreater(bl_venus, 0.0)

    def test_star_draw_unchanged_inside_star_magnitude_range(self):
        # 反证/非回归：去掉 frac 上限 clamp 对恒星必须**逐点无影响**——
        # 两张星表最亮才 -1.44/-1.46，从不触发 clamp。这里把旧公式内联，
        # 在恒星整个星等域（-1.5..8.0）× 四档 Bortle 上逐点对拍。
        from mbdsdr_ai.skyengine.stars import star_draw

        def old(vmag, bortle):
            limit = 6.5 - 0.35 * max(0, int(bortle) - 3)
            if vmag > limit:
                return (0.0, 0.0, 0.0)
            dim = 1.0 - 0.06 * (bortle - 3)
            frac = max(0.0, min(1.0, (6.5 - vmag) / 8.0))
            r = (0.6 + 2.0 * frac ** (1.1 / 2.0)) * dim
            if r > 1.2:
                r = 1.2 + (r - 1.2) ** 0.5
            b = max(0.0, min(1.0, frac))
            if r < 1.2:
                b = min(1.0, b * (r ** 3) / 1.728)
            bl = 0.0 if r < 2.0 else min(1.0, 0.4 + (r - 2.0) / 6.0)
            return (max(0.5, r), b, bl)

        checked = 0
        for bortle in (1, 3, 5, 9):
            vmag = -1.5
            while vmag <= 8.0:
                for got, exp in zip(star_draw(vmag, bortle), old(vmag, bortle)):
                    self.assertAlmostEqual(got, exp, places=12,
                                           msg=f"vmag={vmag} bortle={bortle}")
                checked += 1
                vmag = round(vmag + 0.05, 10)
        self.assertGreater(checked, 700)   # 真跑了扫描，不是空转

    def test_extinction_delta_mag_law(self):
        # 等厚大气 Δm = k/sin(alt)：天顶 = k，alt=30° = 2k，单调随高度角减小。
        from mbdsdr_ai.skyengine.stars import extinction_delta_mag, EXTINCTION_K
        self.assertAlmostEqual(extinction_delta_mag(90.0), EXTINCTION_K,
                               places=6)
        self.assertAlmostEqual(extinction_delta_mag(30.0), 2 * EXTINCTION_K,
                               places=6)
        self.assertGreater(extinction_delta_mag(10.0),
                           extinction_delta_mag(30.0))
        self.assertGreater(extinction_delta_mag(5.0),
                           extinction_delta_mag(10.0))

    def test_extinction_delta_mag_clamped(self):
        # 奇异防护：alt<=0 不炸、Δm 上限 3.5。
        from mbdsdr_ai.skyengine.stars import (
            extinction_delta_mag, _EXTINCTION_MAX_DM)
        self.assertLessEqual(extinction_delta_mag(0.0), _EXTINCTION_MAX_DM)
        self.assertLessEqual(extinction_delta_mag(-10.0), _EXTINCTION_MAX_DM)
        self.assertGreater(extinction_delta_mag(0.0),
                           extinction_delta_mag(30.0))

    def test_extinction_makes_horizon_star_dimmer(self):
        # 端到端语义：同一颗星，贴地平的有效渲染必须比天顶暗/小。
        from mbdsdr_ai.skyengine.stars import (
            star_draw, extinction_delta_mag)
        vmag = 1.0
        r_top, b_top, _ = star_draw(vmag + extinction_delta_mag(80.0))
        r_hor, b_hor, _ = star_draw(vmag + extinction_delta_mag(5.0))
        self.assertGreater(r_top, r_hor)
        self.assertGreater(b_top, b_hor)

    def test_cospar_parse(self):
        from mbdsdr_ai.skyengine.satellites import cospar_from_tle
        self.assertEqual(
            cospar_from_tle("1 25544U 98067A   26268.43198945"), "1998-067A")
        self.assertEqual(
            cospar_from_tle("1 25338U 98030A   26268.50000000"), "1998-030A")


class TestVectorizedStarMirrors(unittest.TestCase):
    """批量绘制路径的「向量化镜像」必须与标量事实源逐点一致。

    星空层每帧把 1515 颗星整批算完再落笔（`star_draw_many` /
    `bv_to_rgb_many`）；单一事实源纪律要求批量版只是标量版的镜像，
    不允许在批量路径里悄悄改公式（改了就成第二套视觉模型）。
    """

    def test_star_draw_many_matches_scalar(self):
        import numpy as np
        from mbdsdr_ai.skyengine.stars import star_draw, star_draw_many

        for bortle in (1, 3, 5, 9):
            mags = np.arange(-1.5, 8.0001, 0.01)
            r, b, bl = star_draw_many(mags, bortle)
            checked = 0
            for i, m in enumerate(mags):
                exp = star_draw(float(m), bortle)
                for got, want in zip((r[i], b[i], bl[i]), exp):
                    self.assertAlmostEqual(
                        float(got), want, places=12,
                        msg=f"vmag={m:.2f} bortle={bortle}")
                checked += 1
            self.assertGreater(checked, 900, "扫描必须真跑满星等域")
        # 空输入与标量不可见极限（Bortle 9 下 5 等星）
        r, b, bl = star_draw_many([], 9)
        self.assertEqual((len(r), len(b), len(bl)), (0, 0, 0))
        r, b, bl = star_draw_many([5.0], 9)
        self.assertEqual((r[0], b[0], bl[0]), (0.0, 0.0, 0.0))

    def test_star_draw_many_no_nan_or_warning(self):
        # 半径 <1.2px 的星在 np.where 里也会走 sqrt 分支；负底数若未夹住
        # 会发 RuntimeWarning 并可能在某些平台变成 nan。这里直接咬死。
        import warnings
        import numpy as np
        from mbdsdr_ai.skyengine.stars import star_draw_many

        mags = np.concatenate([
            np.arange(-2.0, 9.0, 0.05),
            np.array([np.nan, np.inf, -np.inf]),
        ])
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            r, b, bl = star_draw_many(mags, 3)
        self.assertFalse(
            [w for w in caught if "sqrt" in str(w.message)],
            f"sqrt 分支仍在报警: {[str(w.message) for w in caught]}")
        for arr in (r, b, bl):
            self.assertTrue(np.all(np.isfinite(arr)), "批量模型不得产出 NaN/Inf")
        self.assertTrue(np.all(np.asarray(r) >= 0.0))

    def test_bv_to_rgb_many_matches_scalar(self):
        import numpy as np
        from mbdsdr_ai.skyengine.stars import bv_to_rgb, bv_to_rgb_many

        bvs = np.concatenate([
            np.arange(-1.0, 2.0001, 0.005),
            np.array([np.nan, np.inf, -np.inf]),
        ])
        r, g, b = bv_to_rgb_many(bvs)
        for i, x in enumerate(bvs):
            want = bv_to_rgb(float(x))
            self.assertEqual((int(r[i]), int(g[i]), int(b[i]),), want,
                             f"bv={x}")
        # 采样确实覆盖了全部 6 个色标（不是空转）
        self.assertGreater(len(bvs), 590)


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
                                      45.0, 120.0, 0.2)
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
        # 0.5° 下限：让内行星（金星最亮 ~60″）在 700px 窗口能分辨（角半径 ~11.7px）
        self.assertEqual(v.view_state.min_fov, 0.5)
        self.assertEqual(v.view_state.max_fov, 120.0)


class TestStarCulture(unittest.TestCase):
    """中国星名/星官对照（文化对位层，"读懂"冲刺）。"""

    def test_known_star_zh_name(self):
        from mbdsdr_ai.skyengine import stars as _stars
        c = _stars.star_culture("Vega")
        self.assertIsNotNone(c)
        self.assertEqual(c["zh"], "织女一")
        self.assertIn("note", c)

    def test_unknown_star_returns_none(self):
        from mbdsdr_ai.skyengine import stars as _stars
        self.assertIsNone(_stars.star_culture("NoSuchStarXYZ"))

    def test_culture_label_fallback(self):
        from mbdsdr_ai.skyengine import stars as _stars
        self.assertEqual(_stars.culture_label("Vega"), "织女一")
        self.assertEqual(_stars.culture_label("NoSuchStarXYZ"), "NoSuchStarXYZ")

    def test_all_labeled_stars_have_culture_entry(self):
        """亮星标签层（V<=2.0）每颗都必须有中文星官名，防文化层漏星。"""
        from mbdsdr_ai.skyengine import stars as _stars
        missing = [s.name for s in _stars.BRIGHT_STARS
                   if s.vmag <= 2.0 and _stars.star_culture(s.name) is None]
        self.assertEqual(missing, [])

    def test_culture_file_keys_are_real_stars(self):
        """对照表不许收编表外星（防皮配置漂进假名）。

        「真星」宇宙 = merged_star_index（BRIGHT_STARS 亮星 + 星座扩员
        EXTENSION_STARS）。2026-11-02 修口径：此前只查 BRIGHT_STARS，
        星座连线层的扩员星（Merak/Phecda/Megrez 等 70 颗）被误判成"假名"
        ——文化表是为连线层扩的，星表却没并进测试真值，集成缝。
        """
        from mbdsdr_ai.skyengine import stars as _stars
        from mbdsdr_ai.skyengine.constellations import merged_star_index
        table = _stars._load_culture()
        names = set(merged_star_index())
        stray = [k for k in table if k not in names]
        self.assertEqual(stray, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
