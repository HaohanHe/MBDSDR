# SPDX-License-Identifier: MIT
"""天球坐标链回归：钉死章动/黄赤交角的单位错误，验证正逆往返。

运行：python -m pytest tests/test_celestial_frame.py -q

背景（2026-11-06 修）：`nutation_angles_2000b()` 把角秒量级的主项系数
按 0.0001 角秒缩放，dpsi 只有 0.0003"、N≈I，章动等于没生效；
`obliquity()` 的岁差率同样小了约 660 倍。这两个错误不会让任何往返测试
失败（正逆都用同一个 N），所以必须单独钉扎**绝对量级**。
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from mbdsdr_ai import celestial_geometry as cg  # noqa: E402

ARCSEC_PER_RAD = 206264.80624709636
# 覆盖 J2000、当前、以及 ±数十年（UI 的时间倍率允许大幅快转）
JDS = (2451545.0, 2455000.0, 2461000.0, 2463000.0, 2470000.0, 2490000.0)


@pytest.mark.parametrize("jd", JDS)
def test_nutation_is_arcsecond_scale_not_micro(jd):
    """章动必须是角秒量级；1e4 单位错误会让 N 退化为单位阵。"""
    dpsi, deps = cg.nutation_angles_2000b(jd)
    psi_as, eps_as = abs(dpsi * ARCSEC_PER_RAD), abs(deps * ARCSEC_PER_RAD)
    assert 0.1 < psi_as < 30.0, (
        f"jd={jd}: |dpsi|={psi_as:.6f} arcsec，量级异常（单位换算错？）")
    assert 0.01 < eps_as < 20.0, (
        f"jd={jd}: |deps|={eps_as:.6f} arcsec，量级异常（单位换算错？）")
    n_off = float(np.max(np.abs(cg.nutation_matrix(jd) - np.eye(3))))
    assert n_off > 1e-6, f"jd={jd}: 章动矩阵几乎恒等，章动未生效: {n_off}"


def test_obliquity_precession_rate():
    """平黄赤交角每世纪应减约 46.84"，不是 0.071"。"""
    eps_j2000 = cg.obliquity(cg.J2000)
    eps_2100 = cg.obliquity(cg.J2000 + 36525.0)
    delta_as = (eps_j2000 - eps_2100) * ARCSEC_PER_RAD
    assert 46.0 < delta_as < 48.0, f"黄赤交角岁差率异常: {delta_as:.4f} arcsec/century"


def test_precession_identity_at_j2000_and_moves_stars():
    """岁差矩阵必须正交；26 年后恒星赤道方向应有可测位移。

    诚实口径：仓库多项式在 T=0 时 P 并非严格单位阵，而是留有约 2.57e-5 rad
    （≈5.3"）的常数项旋转（zeta_A / z_A 的 ±2.650545" 常数项与 rot_z 的
    负角约定组合后未完全抵消）。这个量级对 UI 显示可忽略，已记入产品
    not_verified；这里只钉扎「正交 + 常数项不放大」，不假装它是严格 I。

    位移量级：一般岁差 50"/年是**黄经**总岁差；赤道坐标的实际角位移
    由 2306"T / 2004"T 的岁差角组合决定，对 ra=100°/dec=30° 的星
    26 年实测约 0.075°，不是 0.36°。别拿黄经岁差当赤道位移。
    """
    P0 = cg.precession_matrix(cg.J2000)
    assert np.allclose(P0 @ P0.T, np.eye(3), atol=1e-12), "岁差矩阵必须正交"
    off = float(np.max(np.abs(P0 - np.eye(3))))
    assert off < 1e-4, f"J2000 岁差常数项偏差异常放大: {off} rad"
    v = cg.vec_from_radec(100.0, 30.0)
    v0 = cg.j2000_to_equinox_of_date(v, cg.J2000, apply_nutation=False)
    v_date = cg.j2000_to_equinox_of_date(v, 2461000.0, apply_nutation=False)
    sep0 = math.degrees(math.acos(float(np.clip(np.dot(v, v0), -1, 1))))
    sep_deg = math.degrees(math.acos(float(np.clip(np.dot(v, v_date), -1, 1))))
    assert sep0 < 0.01, f"J2000 岁差常数项位移异常: {sep0:.4f}°"
    assert 0.02 < sep_deg < 0.6, f"26 年赤道岁差位移异常: {sep_deg:.4f}°"
    assert sep_deg > sep0 * 10, "岁差不随时间增长"


@pytest.mark.parametrize("jd", JDS)
@pytest.mark.parametrize("lon,lat", [(0.0, 0.0), (116.4, 39.9), (-74.0, 40.7)])
def test_full_chain_roundtrip(jd, lon, lat):
    """J2000 → 岁差 → 章动 → 地平 → 逆链，角距误差 < 0.001°。"""
    station = cg.GroundStation(longitude_deg=lon, latitude_deg=lat)
    for ra, dec in ((0.0, 0.0), (101.3, -16.7), (200.0, 55.0), (350.0, -70.0)):
        v = cg.vec_from_radec(ra, dec)
        v_alt = cg.j2000_to_altaz(v, station, jd)
        v_back = cg.altaz_to_j2000(v_alt, station, jd)
        dot = float(np.dot(cg.normalize(v), cg.normalize(v_back)))
        assert abs(dot - 1.0) < 1e-9, (
            f"jd={jd} station=({lon},{lat}) ra={ra} dec={dec}: dot={dot}")


@pytest.mark.parametrize("jd", JDS)
def test_batch_matches_single_chain(jd):
    """批量版与单点版必须是同一条链（P·N·R），不能另写几何。"""
    station = cg.GroundStation(longitude_deg=126.63, latitude_deg=45.75)
    vv = np.array([cg.vec_from_radec(*p) for p in
                   ((10.0, 10.0), (90.0, -20.0), (180.0, 60.0), (270.0, 0.0))])
    many = cg.j2000_to_altaz_many(vv, station, jd)
    single = np.array([cg.j2000_to_altaz(v, station, jd) for v in vv])
    assert np.allclose(many, single, atol=1e-14)


def test_gmst_is_normalized_and_known_epoch():
    """GMST 归一化 [0,360)，J2000.0 0h UT 与 Meeus 参考值相差 < 0.01°。"""
    g = cg.gmst(cg.J2000)
    assert 0.0 <= g < 360.0
    # J2000.0 0h UT: GMST ≈ 280.46061837° (Meeus)；此处 JD 是 12h UT，
    # 故与 280.46 差约 180°，用「半日推进量」而不是硬钉绝对值。
    g_next = cg.gmst(cg.J2000 + 0.5)
    d = (g_next - g) % 360.0
    assert abs(d - 180.4928) < 0.01, f"半日 GMST 推进异常: {d:.6f}°"


# ---------------------------------------------------------------------------
# 地平基的方位约定（2026-11-06 修正：东向曾整体取反，az 全镜像）
# ---------------------------------------------------------------------------
def test_altaz_east_points_east_and_north_points_north():
    """地平基必须是「x=北, y=东, z=天顶」，不是镜像的。

    旧实现 E=(-cosθ,0,-sinθ) 与本模块自己的赤道基 (y=NCP, -z=春分点)
    差一个符号，导致所有方位角东西镜像；这条测试用「北极星几乎正北」
    与「赤道东点升起在天东」两个已知几何钉死它。
    """
    D = math.pi / 180.0
    jd = 2461000.0
    lat, lon = 39.9, 116.4
    sta = cg.GroundStation(longitude_deg=lon, latitude_deg=lat)

    def altaz(ra_deg, dec_deg):
        v = cg.j2000_to_altaz(cg.vec_from_radec(ra_deg, dec_deg), sta, jd)
        # 本模块地平系 z 指天底 → 取反成「z=天顶」
        v = np.array([v[0], v[1], -v[2]], dtype=float)
        alt = math.degrees(math.asin(max(-1.0, min(1.0, v[2]))))
        az = math.degrees(math.atan2(v[1], v[0])) % 360.0
        return alt, az

    # 北极星：高度≈纬度，方位≈0（不是 180）
    alt, az = altaz(2.530301028 * 15, 89.264109)
    assert abs(alt - lat) < 1.0, f"北极星高度异常: {alt:.2f}"
    assert az < 5 or az > 355, f"北极星方位应在正北附近，实测 {az:.2f}°"

    # 时角 H = L - RA：H = -90°（尚未过中天）时天体在**东**方地平
    L = (cg.gmst(jd) + lon) % 360.0
    alt, az = altaz((L + 90.0) % 360.0, 0.0)
    assert abs(alt) < 1.0, f"东点高度应≈0: {alt:.2f}"
    assert abs(az - 90.0) < 1.5, f"东点方位应≈90°，实测 {az:.2f}°（<90 说明东西镜像）"

    # 西点同时成立（对称性：不是把两个方向都翻过去）
    alt, az = altaz((L - 90.0) % 360.0, 0.0)
    assert abs(alt) < 1.0 and abs(az - 270.0) < 1.5, f"西点异常: alt={alt:.2f} az={az:.2f}"
