# SPDX-License-Identifier: MIT
"""
tests/test_proper_motion.py
===========================
恒星自行侧车（``hyg_pm_v41.npz``）与 J2000 -> 日期传播的行为契约。

覆盖：
  1. 纯几何：1000 mas/yr、100 年 -> 0.0277778°；传播后向量仍是单位向量；
     近极点 + 大自行不产生 NaN/Inf；
  2. 缺值安全：任一 PM 为 NaN / JD 非有限 / 侧车缺失 -> 原样返回；
  3. 真实侧车对拍：Sirius PM 取自 Simbad 口径（-546.01, -1223.08 mas/yr，
     与 HYG 一致），100 年总位移 ≈ hypot(...)×100/3.6e6，方向在西南象限；
  4. ``select_for_view`` 集成：启用侧车时 Sirius 的 alt/az 相对静态基线
     位移在 0.001°–0.1°；禁用时逐位等于静态矩阵基线，counts 如实。
"""

import math
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.celestial_geometry import GroundStation, vec_from_radec  # noqa: E402
from mbdsdr_ai.skyengine import proper_motion as pm  # noqa: E402
from mbdsdr_ai.skyengine import star_catalog as sc  # noqa: E402

SIRIUS_HYG_ID = 32263
SIRIUS_PMRA_MAS = -546.01      # Simbad μα*（= HYG pmra，已含 cosδ）
SIRIUS_PMDEC_MAS = -1223.08    # Simbad μδ
YEAR_JD = pm.JULIAN_YEAR_DAYS

# 固定站址/时刻：Sirius 在该时点高度约 33°，避免地平门/折射细节干扰。
STATION = GroundStation(116.0, 40.0, 0.0, "test")
JD = 2461043.1666667


def _unit_vector(ra_deg: float, dec_deg: float) -> np.ndarray:
    ra = math.radians(ra_deg)
    dec = math.radians(dec_deg)
    cd = math.cos(dec)
    return np.array([cd * math.sin(ra), math.sin(dec), -cd * math.cos(ra)])


# ---------------------------------------------------------------------------
# 1. 纯几何
# ---------------------------------------------------------------------------
def test_1000_mas_over_100_years_moves_00277778_deg():
    jd = pm.EPOCH_J2000_JD + 100.0 * YEAR_JD
    ra, dec = pm.propagate_radec_many(0.0, 0.0, 1000.0, 0.0, jd)
    assert ra == pytest.approx(1000.0 * 100.0 / 3.6e6, abs=1e-6)
    assert dec == pytest.approx(0.0, abs=1e-12)


def test_vector_propagation_stays_unit_and_moves_northeast():
    jd = pm.EPOCH_J2000_JD + 100.0 * YEAR_JD
    v = _unit_vector(0.0, 0.0).reshape(1, 3)
    out = pm.propagate_vectors_many(v, 0.0, 0.0, 1000.0, 1000.0, jd)
    assert np.linalg.norm(out[0]) == pytest.approx(1.0, abs=1e-12)
    # 赤经/赤纬都增加 -> 东/北分量都为正（本仓 J2000 左手系）。
    assert out[0, 0] > 0.0 and out[0, 1] > 0.0


def test_near_pole_large_pm_is_finite():
    jd = pm.EPOCH_J2000_JD + 1000.0 * YEAR_JD
    ra, dec = pm.propagate_radec_many(10.0, 89.9999, 10000.0, 10000.0, jd)
    assert np.isfinite(ra) and np.isfinite(dec)
    assert -90.0 <= dec <= 90.0
    v = _unit_vector(10.0, 89.9999).reshape(1, 3)
    out = pm.propagate_vectors_many(v, 10.0, 89.9999, 10000.0, 10000.0, jd)
    assert np.all(np.isfinite(out))
    assert np.linalg.norm(out[0]) == pytest.approx(1.0, abs=1e-12)


# ---------------------------------------------------------------------------
# 2. 缺值安全
# ---------------------------------------------------------------------------
def test_nan_pm_or_nonfinite_jd_returns_original():
    jd = pm.EPOCH_J2000_JD + 10.0 * YEAR_JD
    for pmra, pmdec, jd_in in ((np.nan, 5.0, jd),
                               (5.0, np.nan, jd),
                               (5.0, 5.0, np.nan)):
        ra, dec = pm.propagate_radec_many(10.0, 20.0, pmra, pmdec, jd_in)
        assert ra == 10.0 and dec == 20.0

    ra, dec = pm.propagate_radec_many(
        np.array([10.0, 30.0]), np.array([20.0, -5.0]),
        np.array([np.nan, 1000.0]), np.array([5.0, 5.0]), jd)
    assert ra[0] == 10.0 and dec[0] == 20.0
    assert ra[1] != 30.0


def test_missing_table_yields_nan(monkeypatch):
    monkeypatch.setattr(pm, "load_pm_table", lambda path=None: None)
    pmra, pmdec = pm.pm_for_hyg_ids(np.array([SIRIUS_HYG_ID]))
    assert np.all(np.isnan(pmra))
    assert np.all(np.isnan(pmdec))


def test_pm_lookup_unknown_id_is_nan():
    table = pm.load_pm_table()
    if table is None:
        pytest.skip("hyg_pm_v41.npz 未生成")
    pmra, pmdec = pm.pm_for_hyg_ids(
        np.array([SIRIUS_HYG_ID, 2_000_000_000]), table)
    assert np.isfinite(pmra[0]) and np.isfinite(pmdec[0])
    assert np.isnan(pmra[1]) and np.isnan(pmdec[1])


# ---------------------------------------------------------------------------
# 3. 真实侧车对拍
# ---------------------------------------------------------------------------
def test_real_sidecar_sirius_100yr_displacement():
    table = pm.load_pm_table()
    if table is None:
        pytest.skip("hyg_pm_v41.npz 未生成")
    pmra, pmdec = pm.pm_for_hyg_ids(np.array([SIRIUS_HYG_ID]), table)
    # 独立参考值（Simbad），不是从实现输出反推。
    assert float(pmra[0]) == pytest.approx(SIRIUS_PMRA_MAS, abs=0.05)
    assert float(pmdec[0]) == pytest.approx(SIRIUS_PMDEC_MAS, abs=0.05)

    cat = sc.load_catalog()
    idx = int(np.where(cat.hyg_id == SIRIUS_HYG_ID)[0][0])
    ra0 = float(cat.ra_deg[idx])
    dec0 = float(cat.dec_deg[idx])
    jd = pm.EPOCH_J2000_JD + 100.0 * YEAR_JD
    ra1, dec1 = pm.propagate_radec_many(
        ra0, dec0, float(pmra[0]), float(pmdec[0]), jd)

    dra = ((ra1 - ra0 + 180.0) % 360.0) - 180.0
    sep = math.hypot(dra * math.cos(math.radians(dec0)), dec1 - dec0)
    expected = math.hypot(SIRIUS_PMRA_MAS, SIRIUS_PMDEC_MAS) * 100.0 / 3.6e6
    assert sep == pytest.approx(expected, abs=2e-4)
    # pmra/pmdec 均为负 -> 赤经（西）与赤纬（南）都减小。
    assert dra < 0.0 and dec1 < dec0


# ---------------------------------------------------------------------------
# 4. select_for_view 集成
# ---------------------------------------------------------------------------
def _sirius_center() -> tuple:
    cat = sc.load_catalog()
    idx = int(np.where(cat.hyg_id == SIRIUS_HYG_ID)[0][0])
    v = vec_from_radec(float(cat.ra_deg[idx]), float(cat.dec_deg[idx]))
    w = sc._j2000_to_altaz_matrix(STATION, JD) @ v
    alt = math.degrees(math.asin(max(-1.0, min(1.0, -w[2]))))
    az = math.degrees(math.atan2(w[1], w[0])) % 360.0
    return cat, az, alt


def _static_selection():
    cat, az, alt = _sirius_center()
    return cat, cat.select_for_view(
        station=STATION, jd_ut1=JD, center_az=az, center_alt=alt,
        fov_deg=6.0, limit_mag=2.0, max_count=20000), az, alt


def test_select_for_view_pm_disabled_matches_static_baseline(monkeypatch):
    cat, sel_old = None, None
    with monkeypatch.context() as m:
        m.setattr(sc, "load_pm_table", lambda path=None: None)
        cat, sel_old, _, _ = _static_selection()
    assert sel_old.pm_applied_count == 0
    assert sel_old.pm_missing_count == len(sel_old)
    assert int(np.count_nonzero(sel_old.hyg_id == SIRIUS_HYG_ID)) == 1

    # 独立用 J2000 矩阵重算一遍静态基线（等价于改动前的代码路径）。
    M = sc._j2000_to_altaz_matrix(STATION, JD)
    ra = np.radians(sel_old.ra_deg)
    dec = np.radians(sel_old.dec_deg)
    cd = np.cos(dec)
    v = np.column_stack((cd * np.sin(ra), np.sin(dec), -cd * np.cos(ra)))
    v_alt = v @ M.T
    alt = np.degrees(np.arcsin(np.clip(-v_alt[:, 2], -1.0, 1.0)))
    az = np.degrees(np.arctan2(v_alt[:, 1], v_alt[:, 0])) % 360.0
    assert np.array_equal(sel_old.alt_deg, alt)
    assert np.array_equal(sel_old.az_deg, az)


def test_select_for_view_applies_sirius_pm(monkeypatch):
    if pm.load_pm_table() is None:
        pytest.skip("hyg_pm_v41.npz 未生成")
    cat, az, alt = _sirius_center()
    with monkeypatch.context() as m:
        m.setattr(sc, "load_pm_table", lambda path=None: None)
        sel_static = cat.select_for_view(
            station=STATION, jd_ut1=JD, center_az=az, center_alt=alt,
            fov_deg=6.0, limit_mag=2.0, max_count=20000)
    sel_pm = cat.select_for_view(
        station=STATION, jd_ut1=JD, center_az=az, center_alt=alt,
        fov_deg=6.0, limit_mag=2.0, max_count=20000)

    assert sel_pm.pm_applied_count > 0
    assert sel_pm.pm_applied_count + sel_pm.pm_missing_count == len(sel_pm)

    i_s = int(np.where(sel_static.hyg_id == SIRIUS_HYG_ID)[0][0])
    i_p = int(np.where(sel_pm.hyg_id == SIRIUS_HYG_ID)[0][0])
    # 输出 ra/dec 仍是 J2000：自行只改 alt/az。
    assert sel_pm.ra_deg[i_p] == sel_static.ra_deg[i_s]
    assert sel_pm.dec_deg[i_p] == sel_static.dec_deg[i_s]

    d_alt = float(sel_pm.alt_deg[i_p] - sel_static.alt_deg[i_s])
    d_az = ((float(sel_pm.az_deg[i_p] - sel_static.az_deg[i_s]) + 180.0)
            % 360.0) - 180.0
    d_az *= math.cos(math.radians(float(sel_static.alt_deg[i_s])))
    sep = math.hypot(d_alt, d_az)
    # 2026 年距 J2000 约 26 年，Sirius 位移约 0.01°；远大于矩阵噪声、
    # 远小于视场/容差口径。
    assert 0.001 < sep < 0.1


def test_select_for_view_without_pm_table_is_static(monkeypatch):
    cat, az, alt = _sirius_center()
    with monkeypatch.context() as m:
        m.setattr(sc, "load_pm_table", lambda path=None: None)
        sel = cat.select_for_view(
            station=STATION, jd_ut1=JD, center_az=az, center_alt=alt,
            fov_deg=120.0, max_count=20000)
    assert len(sel) > 0
    assert sel.pm_applied_count == 0
    assert sel.pm_missing_count == len(sel)
