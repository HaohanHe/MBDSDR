"""手机端天空算法的对拍与静态校验（M1.2 / M1.3 的本机可验证部分）。

背景
----
本机**没有 Flutter 工具链**（`docs/parameter-unification.md:453` 已记录同一事实），
`mobile/test/*.dart` 无法用 `flutter test` 执行。但"没法跑 Dart"不等于
"没法验证算法"。本文件做三件事：

1. **算法对拍**：把 `mobile/lib/astro/sky_transform.dart` 与
   `sky_projection.dart` 里的算式在 Python 侧**逐行对应地复现**，
   再与桌面唯一真值（`celestial_geometry` / `sky_interaction`）对拍。
   算法一致 + Dart 是同一算式的翻译 ⇒ Dart 的数学是对的。
2. **黄金向量自洽**：校验 `mobile/test/fixtures/*.json` 与当前桌面实现一致
   （防止有人手改期望值让 Dart 测试假绿）。
3. **常量字面量校验**：从 Dart 源码里查关键系数是否抄错。
   这一条能抓到"Dart 公式结构对、某个系数少一位"这类最阴的错误。

**不覆盖**：Dart 能否编译、类型是否正确、Flutter 运行时行为。
那三项必须在有工具链的机器上跑 `flutter analyze` / `flutter test`，
本文件不代劳、也不假装代劳。
"""

from __future__ import annotations

import json
import math
import os
import re

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MOBILE = os.path.join(_ROOT, "mobile")
_FIXTURES = os.path.join(_MOBILE, "test", "fixtures")

# ---------------------------------------------------------------------------
# 一、Dart 算法的 Python 逐行复现
#
# 下面两个函数不是"另一种实现"，它们刻意与 Dart 源码逐行对应：
# 变量名、运算顺序、短路条件都照抄。任何一处与 Dart 不同，这个测试就失去意义。
# ---------------------------------------------------------------------------

J2000_JD = 2451545.0


def dart_normalize_deg(deg: float) -> float:
    """对应 Dart `normalizeDeg`。"""
    v = math.fmod(deg, 360.0)
    return v + 360.0 if v < 0 else v


def dart_gmst_deg(jd_ut1: float) -> float:
    """对应 Dart `gmstDegCapitaine`（Capitaine 2003）。"""
    jd_tt = jd_ut1
    t = (jd_tt - J2000_JD) / 36525.0
    tu = (jd_ut1 - J2000_JD) / 36525.0
    ut1_sec = (jd_ut1 - math.floor(jd_ut1) + 0.5) * 86400.0
    s = (((-0.000000002454 * t - 0.00000199708) * t - 0.0000002926) * t
         + 0.092772110) * t * t
    s += (t - tu) * 307.4771013
    s += 8640184.79447825 * tu + 24110.5493771
    s += ut1_sec
    return dart_normalize_deg(s / 240.0)


def dart_precession_angles_arcsec(jd_tt: float):
    """对应 Dart `precessionAnglesArcsec`（IAU 2006 P03）。"""
    t = (jd_tt - J2000_JD) / 36525.0
    t2, t3, t4, t5 = t * t, t**3, t**4, t**5
    zeta = (2.650545 + 2306.083227 * t + 0.2988499 * t2 + 0.01801828 * t3
            - 0.000005971 * t4 - 0.0000003173 * t5)
    z = (-2.650545 + 2306.077181 * t + 1.0927348 * t2 + 0.01826898 * t3
         - 0.000028596 * t4 - 0.0000002904 * t5)
    theta = (2004.191903 * t - 0.4269350 * t2 - 0.04183110 * t3
             - 0.000006059 * t4 - 0.00000014543 * t5)
    return zeta, z, theta


def dart_precess_j2000_to_date(ra_deg: float, dec_deg: float, jd_tt: float):
    """对应 Dart `precessJ2000ToDate`。"""
    zeta_as, z_as, theta_as = dart_precession_angles_arcsec(jd_tt)
    zeta = math.radians(zeta_as / 3600.0)
    z = math.radians(z_as / 3600.0)
    theta = math.radians(theta_as / 3600.0)

    ra = math.radians(ra_deg)
    dec = math.radians(dec_deg)
    cos_dec = math.cos(dec)
    sin_dec = math.sin(dec)
    ra_zeta = ra + zeta
    cos_ra_zeta = math.cos(ra_zeta)
    sin_ra_zeta = math.sin(ra_zeta)

    big_a = cos_dec * sin_ra_zeta
    big_b = math.cos(theta) * cos_dec * cos_ra_zeta - math.sin(theta) * sin_dec
    big_c = math.sin(theta) * cos_dec * cos_ra_zeta + math.cos(theta) * sin_dec

    ra_out = math.degrees(math.atan2(big_a, big_b)) + math.degrees(z)
    dec_out = math.degrees(math.asin(max(-1.0, min(1.0, big_c))))
    return dart_normalize_deg(ra_out), dec_out


def dart_altaz_from_j2000(ra_deg, dec_deg, lat_deg, lon_deg, jd_ut1):
    """对应 Dart `altAzFromJ2000` -> `altAzFromDate`。"""
    ra2, dec2 = dart_precess_j2000_to_date(ra_deg, dec_deg, jd_ut1)
    lst = math.radians(dart_normalize_deg(dart_gmst_deg(jd_ut1) + lon_deg))
    ra = math.radians(ra2)
    dec = math.radians(dec2)
    lat = math.radians(lat_deg)

    hour_angle = lst - ra
    sin_alt = (math.sin(dec) * math.sin(lat)
               + math.cos(dec) * math.cos(lat) * math.cos(hour_angle))
    alt = math.asin(max(-1.0, min(1.0, sin_alt)))

    y = -math.cos(dec) * math.sin(hour_angle)
    x = (math.sin(dec) * math.cos(lat)
         - math.cos(dec) * math.sin(lat) * math.cos(hour_angle))
    az = math.atan2(y, x)

    return dart_normalize_deg(math.degrees(az)), math.degrees(alt)


class DartSkyView:
    """对应 Dart `SkyView`。"""

    def __init__(self, center_az, center_alt, fov, width, height, rotation=0.0):
        self.center_az = center_az
        self.center_alt = center_alt
        self.fov = fov
        self.width = width
        self.height = height
        self.rotation = rotation

    @property
    def min_dim(self):
        return min(self.width, self.height)

    @property
    def center_x(self):
        return self.width / 2.0

    @property
    def center_y(self):
        return self.height / 2.0

    @property
    def pixel_per_rad(self):
        if self.min_dim <= 0:
            return 1.0
        half = self.fov * math.pi / 180.0 * 0.5
        t = math.tan(half)
        if abs(t) < 1e-12:
            return 1.0
        return (self.min_dim * 0.5) / t


def _dart_view_rotation(center_az_deg, center_alt_deg):
    """对应 Dart `_viewRotation`（含天顶邻域那一支）。"""
    caz = math.radians(center_az_deg)
    calt = math.radians(center_alt_deg)
    if abs(math.cos(calt)) < 1e-3:
        return (
            (math.cos(caz), math.sin(caz), 0.0),
            (-math.sin(caz), math.cos(caz), 0.0),
            (0.0, 0.0, 1.0),
        )
    s_alt, c_alt = math.sin(calt), math.cos(calt)
    xv = (-s_alt * math.cos(caz), -s_alt * math.sin(caz), -c_alt)
    zv = (-c_alt * math.cos(caz), -c_alt * math.sin(caz), s_alt)
    yv = (
        xv[1] * zv[2] - xv[2] * zv[1],
        xv[2] * zv[0] - xv[0] * zv[2],
        xv[0] * zv[1] - xv[1] * zv[0],
    )
    return xv, yv, zv


def dart_project_azalt(az_deg, alt_deg, view: DartSkyView):
    """对应 Dart `projectAzAlt`。不可见返回 None（对应 Dart 的 null）。"""
    az = math.radians(az_deg)
    alt = math.radians(alt_deg)
    ca = math.cos(alt)
    v = (ca * math.cos(az), ca * math.sin(az), -math.sin(alt))

    rot = _dart_view_rotation(view.center_az, view.center_alt)
    vx = sum(rot[0][i] * v[i] for i in range(3))
    vy = sum(rot[1][i] * v[i] for i in range(3))
    vz = sum(rot[2][i] * v[i] for i in range(3))

    denom = -vz
    if denom <= 1e-12:
        return None

    xn = vx / denom
    yn = vy / denom
    ppr = view.pixel_per_rad
    px = view.center_x + yn * ppr
    py = view.center_y - xn * ppr

    if abs(view.rotation) > 1e-9:
        ang = math.radians(view.rotation)
        cs, sn = math.cos(ang), math.sin(ang)
        dx, dy = px - view.center_x, py - view.center_y
        px = view.center_x + dx * cs - dy * sn
        py = view.center_y + dx * sn + dy * cs
    return px, py


def dart_clip_segment(p1, p2, width, height):
    """对应 Dart `clipSegmentToRect`（Liang–Barsky）。"""
    x0, y0 = p1
    x1, y1 = p2
    if not all(map(math.isfinite, (x0, y0, x1, y1))):
        return None
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0

    def clip(p, q):
        nonlocal t0, t1
        if abs(p) < 1e-12:
            return q >= 0
        r = q / p
        if p < 0:
            if r > t1:
                return False
            if r > t0:
                t0 = r
        else:
            if r < t0:
                return False
            if r < t1:
                t1 = r
        return True

    if not clip(-dx, x0):
        return None
    if not clip(dx, width - x0):
        return None
    if not clip(-dy, y0):
        return None
    if not clip(dy, height - y0):
        return None
    if t0 > t1:
        return None
    return ((x0 + t0 * dx, y0 + t0 * dy), (x0 + t1 * dx, y0 + t1 * dy))


# ---------------------------------------------------------------------------
# 二、对拍：Dart 算法 vs 桌面真值
# ---------------------------------------------------------------------------

def _angular_diff_deg(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


class TestTransformMatchesDesktop:
    """Dart 坐标链算式 vs 桌面 `j2000_to_altaz`。"""

    @pytest.mark.parametrize("jd", [2451545.0, 2460000.5, 2461355.25, 2480000.0])
    @pytest.mark.parametrize("site", [(45.0, 120.0), (-33.9, 18.4), (0.0, 0.0)])
    @pytest.mark.parametrize("star", [
        (101.28722, -16.716116),   # 天狼
        (279.234734, 38.783689),   # 织女
        (37.954560, 89.264109),    # 北极星
        (165.93196, 61.75103),     # 天枢
    ])
    def test_altaz_within_nutation_budget(self, jd, site, star):
        """差必须落在章动量级（<0.02°）——Dart 不做章动，这是已知且写明的口径差。"""
        from mbdsdr_ai.celestial_geometry import (
            GroundStation, azalt_from_vec, j2000_to_altaz, vec_from_radec)

        lat, lon = site
        ra, dec = star
        st = GroundStation(lon, lat, 0.0, "test")
        az_ref, alt_ref = azalt_from_vec(j2000_to_altaz(
            vec_from_radec(ra, dec), st, jd))

        az_dart, alt_dart = dart_altaz_from_j2000(ra, dec, lat, lon, jd)
        assert _angular_diff_deg(az_dart, float(az_ref)) < 0.02
        assert abs(alt_dart - float(alt_ref)) < 0.02

    def test_gmst_matches_desktop_capitaine(self):
        """Dart 的 GMST 与桌面星象仪同式，应到浮点舍入级一致。"""
        from mbdsdr_ai.celestial_geometry import gmst

        for jd in (2451545.0, 2460000.5, 2461355.25, 2480000.0):
            assert _angular_diff_deg(dart_gmst_deg(jd), gmst(jd)) < 1e-9

    def test_polaris_altitude_equals_latitude(self):
        """北极星高度≈纬度：这一条不依赖任何实现，是天文事实。"""
        for lat in (0.0, 30.0, 45.0, 60.0):
            _, alt = dart_altaz_from_j2000(37.954560, 89.264109, lat, 120.0,
                                          2461355.25)
            assert abs(alt - lat) < 0.8  # 北极星偏天极约 0.7°


class TestProjectionMatchesDesktop:
    """Dart 透视投影算式 vs 桌面 `sky_to_screen` + `PerspectiveProjection`。"""

    @pytest.mark.parametrize("view_args", [
        dict(center_az=180.0, center_alt=45.0, fov=60.0, w=1080, h=1920, rot=0.0),
        dict(center_az=90.0, center_alt=89.0, fov=50.0, w=1080, h=1920, rot=0.0),
        dict(center_az=270.0, center_alt=30.0, fov=25.0, w=1920, h=1080, rot=17.0),
        dict(center_az=0.0, center_alt=20.0, fov=5.0, w=800, h=800, rot=0.0),
        dict(center_az=135.0, center_alt=60.0, fov=110.0, w=800, h=800, rot=0.0),
    ])
    @pytest.mark.parametrize("offset", [(0.0, 0.0), (0.0, 20.0), (20.0, 0.0),
                                        (10.0, 10.0), (0.0, -20.0)])
    def test_pixel_within_half_pixel(self, view_args, offset):
        from mbdsdr_ai.celestial_geometry import PerspectiveProjection
        from mbdsdr_ai.sky_interaction import ViewState, sky_to_screen

        view = ViewState(center_az=view_args["center_az"],
                         center_alt=view_args["center_alt"],
                         fov_deg=view_args["fov"],
                         rotation=view_args["rot"],
                         # ViewState 默认 min_fov=30° 会把 25°/5° 夹成 30°；
                         # 桌面视图自己跑的是 min_fov=0.5（rf_sky_view:1855），
                         # Dart 的 SkyView 也不夹取，这里对齐真实运行值。
                         min_fov=0.5, max_fov=180.0)
        proj = PerspectiveProjection()
        w, h = view_args["w"], view_args["h"]

        az = (view_args["center_az"] + offset[0]) % 360.0
        alt = max(-89.0, min(89.0, view_args["center_alt"] + offset[1]))

        px_ref, py_ref = sky_to_screen(az, alt, view, proj, (w, h))
        got = dart_project_azalt(
            az, alt,
            DartSkyView(view_args["center_az"], view_args["center_alt"],
                        view_args["fov"], w, h, view_args["rot"]))

        if not (math.isfinite(px_ref) and math.isfinite(py_ref)):
            assert got is None, "桌面判不可见，Dart 却给了坐标"
            return

        # 桌面把画布外有限点钳到 ±4×短边（防极端 FOV 下坐标爆炸），
        # 手机端故意不钳、用真实坐标让段裁剪算对方向。两者都对但数值不同。
        # 这里不做"迁就"，而是把这条差异验出来：钳过的点，Dart 必须更远。
        lim = 4.0 * float(min(w, h))
        clamped = (abs(px_ref - w / 2.0) >= lim) or (abs(py_ref - h / 2.0) >= lim)
        assert got is not None, "桌面可见，Dart 却返回 null"
        if clamped:
            assert (abs(got[0] - w / 2.0) >= lim - 0.5
                    or abs(got[1] - h / 2.0) >= lim - 0.5), \
                "桌面被钳到边界，Dart 却给出界内坐标——符号或缩放有误"
            return

        assert abs(got[0] - float(px_ref)) < 0.5
        assert abs(got[1] - float(py_ref)) < 0.5

    def test_center_maps_to_canvas_center(self):
        v = DartSkyView(200.0, 35.0, 55.0, 1080, 1920)
        x, y = dart_project_azalt(200.0, 35.0, v) or (float("nan"),) * 2
        assert abs(x - 540.0) < 1e-6
        assert abs(y - 960.0) < 1e-6

    def test_behind_camera_returns_none(self):
        v = DartSkyView(180.0, 45.0, 60.0, 800, 800)
        assert dart_project_azalt(0.0, -45.0, v) is None

    def test_pixel_per_rad_uses_short_side(self):
        a = DartSkyView(0, 45, 60, 1080, 1920)
        b = DartSkyView(0, 45, 60, 1920, 1080)
        assert abs(a.pixel_per_rad - b.pixel_per_rad) < 1e-9
        assert abs(a.pixel_per_rad - (540.0 / math.tan(math.pi / 6))) < 1e-9


# ---------------------------------------------------------------------------
# 三、黄金向量自洽（防止手改期望值让 Dart 测试假绿）
# ---------------------------------------------------------------------------

class TestGoldenFixturesNotTampered:
    def test_transform_fixture_matches_desktop(self):
        path = os.path.join(_FIXTURES, "sky_transform_golden.json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)

        from mbdsdr_ai.celestial_geometry import (
            GroundStation, azalt_from_vec, j2000_to_altaz, vec_from_radec)

        assert data["meta"]["count"] == len(data["cases"])
        tol = float(data["meta"]["tolerance_deg"])
        assert tol > 0
        for c in data["cases"]:
            st = GroundStation(c["lon_deg"], c["lat_deg"], 0.0, c["site"])
            az, alt = azalt_from_vec(j2000_to_altaz(
                vec_from_radec(c["ra_deg"], c["dec_deg"]), st, c["jd_ut1"]))
            # 期望值本身就是桌面算的，这里必须逐位复现（只允许浮点舍入）。
            assert _angular_diff_deg(float(az), c["az_deg"]) < 1e-9, \
                f"{c['star']}/{c['site']}/{c['jd_ut1']}: az 期望值被改过"
            assert abs(float(alt) - c["alt_deg"]) < 1e-9, \
                f"{c['star']}/{c['site']}/{c['jd_ut1']}: alt 期望值被改过"

    def test_projection_fixture_matches_desktop(self):
        path = os.path.join(_FIXTURES, "sky_projection_golden.json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)

        from mbdsdr_ai.celestial_geometry import PerspectiveProjection
        from mbdsdr_ai.sky_interaction import ViewState, sky_to_screen

        proj = PerspectiveProjection()
        assert data["meta"]["case_count"] == len(data["cases"])
        for case in data["cases"]:
            v = case["view"]
            view = ViewState(center_az=v["center_az_deg"],
                             center_alt=v["center_alt_deg"],
                             fov_deg=v["fov_deg"],
                             rotation=v["rotation_deg"],
                             min_fov=v["min_fov_deg"],
                             max_fov=v["max_fov_deg"])
            size = (v["width"], v["height"])
            for p in case["points"]:
                px, py = sky_to_screen(p["az_deg"], p["alt_deg"], view, proj, size)
                visible = math.isfinite(px) and math.isfinite(py)
                assert visible == p["visible"], \
                    f"{case['name']} az={p['az_deg']}：可见性期望被改过"
                if visible:
                    assert abs(float(px) - p["px"]) < 1e-9
                    assert abs(float(py) - p["py"]) < 1e-9

    def test_fixtures_are_regenerable(self):
        """黄金向量必须带生成器路径，且脚本存在——否则无法复现就是死数据。"""
        for name in ("sky_transform_golden.json", "sky_projection_golden.json"):
            with open(os.path.join(_FIXTURES, name), encoding="utf-8") as fh:
                meta = json.load(fh)["meta"]
            gen = meta["generator"]
            assert os.path.exists(os.path.join(_ROOT, gen)), \
                f"{name} 的生成器 {gen} 不存在"


# ---------------------------------------------------------------------------
# 四、Dart 源码静态校验（常量抄错是这类移植最阴的错误）
# ---------------------------------------------------------------------------

def _read_dart(name: str) -> str:
    with open(os.path.join(_MOBILE, "lib", "astro", name), encoding="utf-8") as fh:
        return fh.read()


class TestDartSourceConstants:
    def test_transform_has_iau2006_precession_coefficients(self):
        src = _read_dart("sky_transform.dart")
        for coeff in ("2.650545", "2306.083227", "2306.077181",
                      "2004.191903", "0.2988499", "1.0927348", "0.4269350"):
            assert coeff in src, f"岁差系数 {coeff} 缺失"

    def test_transform_has_capitaine_gmst_coefficients(self):
        src = _read_dart("sky_transform.dart")
        for coeff in ("8640184.79447825", "24110.5493771", "307.4771013",
                      "0.092772110"):
            assert coeff in src, f"GMST 系数 {coeff} 缺失"

    def test_transform_ignores_delta_t_by_design(self):
        """ΔT 忽略是**有意的口径**，源码里必须留痕，否则后人会当 bug 改掉。"""
        src = _read_dart("sky_transform.dart")
        assert "ΔT" in src or "delta T" in src.lower()

    def test_projection_uses_short_side_scaling(self):
        src = _read_dart("sky_projection.dart")
        assert "minDim" in src
        # 去掉空格后查字面量：允许格式化差异，不允许换成长边。
        compact = re.sub(r"\s+", "", src)
        assert "(minDim*0.5)/t" in compact, \
            "像素缩放必须是 (短边/2) / tan(fov/2)，用长边会让宽屏视场竖向变窄"
        assert "math.tan" in src

    def test_projection_rejects_backside_explicitly(self):
        """透视必须显式判 v[2]>=0 并返回 null，不能返回 inf 让下游当坐标用。"""
        src = _read_dart("sky_projection.dart")
        assert "denom <= 1e-12" in src
        assert "return null;" in src

    def test_projection_screen_signs_match_desktop(self):
        """落屏符号：px 加 y_n（东在右）、py 减 x_n（北在上）。符号反了画面会镜像。"""
        src = _read_dart("sky_projection.dart")
        assert "centerX + yn * ppr" in src, "px 应为 cx + y_n*ppr"
        assert "centerY - xn * ppr" in src, "py 应为 cy - x_n*ppr"

    def test_clip_handles_all_four_edges(self):
        src = _read_dart("sky_projection.dart")
        for edge in ("clip(-dx, x0)", "clip(dx, width - x0)",
                     "clip(-dy, y0)", "clip(dy, height - y0)"):
            assert edge in src, f"Liang–Barsky 缺边：{edge}"

    def test_dart_files_record_verification(self):
        """2026-11-18 起本机有 Flutter 工具链，Dart 实测通过；
        文件头必须记录验证事实（版本 + 命令 + 结果），不许再挂 `未验证` 假标记。"""
        for name in ("sky_transform.dart", "sky_projection.dart"):
            src = _read_dart(name)
            assert "未验证" not in src, f"{name} 仍挂着过期的未验证声明"
            assert "验证记录" in src, f"{name} 缺验证记录段"
            assert "Flutter 3.50.0" in src, f"{name} 未写明工具链版本"
            assert "flutter test" in src, f"{name} 未写明验证命令"


class TestClipBehaviour:
    """裁剪四态：这是"装不下也要画到边界"的根，必须在算法层钉死。"""

    def test_both_inside(self):
        r = dart_clip_segment((100, 100), (200, 200), 400, 400)
        assert r is not None
        assert abs(r[0][0] - 100) < 1e-9 and abs(r[1][1] - 200) < 1e-9

    def test_one_end_outside(self):
        r = dart_clip_segment((200, 200), (900, 200), 400, 400)
        assert r is not None
        assert abs(r[1][0] - 400) < 1e-9
        assert abs(r[1][1] - 200) < 1e-9

    def test_both_outside_but_crossing(self):
        r = dart_clip_segment((-500, 200), (900, 200), 400, 400)
        assert r is not None, "穿过画面的段被丢弃 = 用户看不到线往哪边走"
        assert abs(r[0][0] - 0) < 1e-9
        assert abs(r[1][0] - 400) < 1e-9

    def test_fully_outside(self):
        assert dart_clip_segment((-100, -100), (-50, -50), 400, 400) is None

    def test_non_finite(self):
        assert dart_clip_segment((float("inf"), 0), (10, 10), 400, 400) is None

    def test_horizontal_no_div_zero(self):
        r = dart_clip_segment((-10, 50), (500, 50), 400, 400)
        assert r is not None
        assert abs(r[0][0]) < 1e-9 and abs(r[1][0] - 400) < 1e-9

    def test_vertical_no_div_zero(self):
        r = dart_clip_segment((50, -10), (50, 500), 400, 400)
        assert r is not None
        assert abs(r[0][1]) < 1e-9 and abs(r[1][1] - 400) < 1e-9
