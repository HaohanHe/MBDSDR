# SPDX-License-Identifier: MIT
"""岁差矩阵的**绝对位置**真值回归（不是往返一致性）。

背景（2026-11-06）
------------------
``celestial_geometry.precession_matrix`` 旧实现写作
``rot_z(z_A) @ rot_x(-theta_A) @ rot_z(-zeta_A)``，两处错：

1. θ_A（赤道与黄道夹角）在标准 IAU 2006 三角度式里绕 **y** 轴，旧码用了 ``rot_x``；
2. 本模块赤道基 (x=RA0h, y=NCP, z=-RA6h) 行列式为 -1，是**左手系**，标准右手
   矩阵不能直接左乘本模块向量，还缺一次基变换 ``Sᵀ·P_std·S``。

旧码在 J2000 恰好等于单位阵，所以「J2000 → date → J2000 往返」这类自洽测试
一条都查不出来（往返两边用同一个错矩阵）。实测偏差：2026 年 Sirius 的 of-date
赤经多 1112"、赤纬少 628"，整张恒星系相对日月/卫星/地平线转过约 0.35°。

本测试用 skyfield（外部真值）预先算出的 of-date 位置金样
``tests/golden/celestial_ofdate_golden.json`` 做绝对比较；金样由
``precessionlib.compute_precession``（IAU 2006 四角度式）+ 纯岁差（无章动）生成，
与本函数的职责严格对齐。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from mbdsdr_ai import celestial_geometry as cg

GOLDEN = Path(__file__).parent / "golden" / "celestial_ofdate_golden.json"

# 标准右手基 → 本模块左手基 的基变换（与本模块 celestial_geometry 内的常量同义）
S = np.array([[0.0, 0.0, -1.0],
              [1.0, 0.0, 0.0],
              [0.0, 1.0, 0.0]])

ARCSEC = 1.0 / 3600.0


@pytest.fixture(scope="module")
def golden():
    if not GOLDEN.exists():  # pragma: no cover
        pytest.skip(f"金样缺失: {GOLDEN}")
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def _radec_no_nutation(ra_deg: float, dec_deg: float, jd_tt: float):
    v = cg.precession_matrix(jd_tt) @ cg.vec_from_radec(ra_deg, dec_deg)
    return cg.radec_from_vec(v)


def _ang_sep_arcsec(ra1, dec1, ra2, dec2):
    """两点球面角距（角秒），避免 RA 过 0/360 时的假偏差。"""
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    cos_sep = (math.sin(d1) * math.sin(d2)
               + math.cos(d1) * math.cos(d2) * math.cos(r2 - r1))
    return math.degrees(math.acos(max(-1.0, min(1.0, cos_sep)))) * 3600.0


# ---------------------------------------------------------------------------
# 1. 矩阵级：与 skyfield 的 IAU 2006 四角度式逐元素对拍
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("epoch", ["1850-01-01", "2000-01-01", "2026-11-06", "2100-01-01"])
def test_precession_matrix_matches_skyfield(golden, epoch):
    jd = golden["matrix"][epoch]["jd_tt"]
    P_ref = np.array(golden["matrix"][epoch]["P"], dtype=float)
    P_mod = cg.precession_matrix(jd)
    # 本模块基 → 标准基，再与外部真值比
    err_arcsec = np.abs(S @ P_mod @ S.T - P_ref).max() / ARCSEC
    assert err_arcsec < 0.05, f"{epoch}: 岁差矩阵与 skyfield 差 {err_arcsec:.3f}\""


# ---------------------------------------------------------------------------
# 2. 位置级：恒星 of-date 赤经/赤纬（纯岁差，无章动）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("epoch", ["1850-01-01", "2000-01-01", "2026-11-06", "2100-01-01"])
def test_ofdate_positions_match_golden(golden, epoch):
    jd = golden["matrix"][epoch]["jd_tt"]
    j2000 = json.loads(GOLDEN.read_text(encoding="utf-8"))  # 只为读原始 J2000 输入
    for name, (ra_truth, dec_truth) in golden["radec"][epoch].items():
        # 金样生成脚本里恒星输入的 J2000 坐标
        ra0, dec0 = _J2000_INPUT[name]
        ra_mod, dec_mod = _radec_no_nutation(ra0, dec0, jd)
        sep = _ang_sep_arcsec(ra_truth, dec_truth, ra_mod, dec_mod)
        assert sep < 0.05, f"{epoch} {name}: 位置差 {sep:.2f}\""


_J2000_INPUT = {
    "Sirius": (101.28716, -16.71611),
    "Vega": (279.23473, 38.78369),
    "Deneb": (310.35798, 45.28034),
    "Antares": (247.35192, -26.43200),
    "Polaris": (37.95456, 89.26411),
}


# ---------------------------------------------------------------------------
# 3. 物理量级锚（不依赖外部文件，咬住"岁差量级必须对"）
# ---------------------------------------------------------------------------
def test_precession_amplitude_is_physically_right():
    """2000→2026 的岁差位移必须约 0.30°（不是 0.6°，也不是 0）。

    旧码在 J2000 是单位阵、在 2026 把 Sirius 的 RA 多推 1112"，合起来位移
    约 0.61° —— 这条断言就是给那种"只改了转轴一半"的回归准备的。
    """
    ra0, dec0 = _J2000_INPUT["Sirius"]
    ra1, dec1 = _radec_no_nutation(ra0, dec0, 2461350.5)   # 2026-11-06
    dra_deg = ((ra1 - ra0 + 180.0) % 360.0) - 180.0
    ra_move_arcsec = abs(dra_deg) * 3600.0
    # 天狼星 RA 年变化率约 41"/yr → 26 年约 1070"（0.297°）
    assert 1000.0 < ra_move_arcsec < 1160.0, (
        f"天狼星 2000→2026 赤经位移 {ra_move_arcsec:.1f}\"，物理量级应为 ~1070\"")
    assert abs(dec1 - dec0) < 400.0 / 3600.0


def test_nutation_stays_second_scale():
    """章动仍在角秒量级（~17"），不要因为修岁差把章动带成 0 或 1 度。"""
    dpsi, deps = cg.nutation_angles_2000b(2461350.5)
    # Δψ 全幅约 ±17"，Δε 约 ±9"；2026-11-06 实测 Δψ≈9.8"、Δε≈?
    assert 2.0 / 3600.0 < abs(math.degrees(dpsi)) < 25.0 / 3600.0
    assert 0.5 / 3600.0 < abs(math.degrees(deps)) < 15.0 / 3600.0


# ---------------------------------------------------------------------------
# 4. 有 skyfield 时直接在线对拍（金样之外的额外抽样，防止金样本身写错）
# ---------------------------------------------------------------------------
def test_cross_check_live_skyfield():
    precessionlib = pytest.importorskip("skyfield.precessionlib")
    for jd in (2415020.5, 2440000.5, 2470000.5, 2490000.5):
        P_ref = precessionlib.compute_precession(jd)
        P_mod = cg.precession_matrix(jd)
        err = np.abs(S @ P_mod @ S.T - P_ref).max() / ARCSEC
        assert err < 0.05, f"jd={jd}: 与 skyfield 差 {err:.3f}\""
