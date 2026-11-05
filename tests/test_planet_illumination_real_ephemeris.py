# SPDX-License-Identifier: MIT
"""
tests/test_planet_illumination_real_ephemeris.py
=================================================
行星照度（日-行星-地球真实三角形）真历表回归。

背景缺陷（2026-11-05 修）：``solar_system.get_planet_position`` 旧实现把
「行星日心距」与「地心距」都填成 ``pos.distance_au``，三角形退化成
``cos χ = R/(2Δ)``：
  - 内行星（Δ < R/2）余弦被夹到 1 → 下合时给「满月」（应为细牙）；
  - 外行星（Δ > R/2）给 0.53–0.87 的假「半亮」（应接近满相）。
修法：三边分别取日心距 / 地心距 / 日地距，唯一换算入口
``planet_illum_from_geometry``，三后端共用。

时刻来源：DE421 覆盖 2026-01-01→2027-12-31，逐日扫描地心距角极值
（``skyfield`` 直接算 ``separation_from``），再按日心距/地心距区分上合
（远）与下合（近）。全部为真实历表时刻，不插值、不硬编码照度。
"""
import importlib.util
import math
import os
import sys
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from mbdsdr_ai import solar_system as sol  # noqa: E402

SITE = sol.GroundStation(latitude_deg=40.0, longitude_deg=116.0, height_m=0.0)

# 内行星两个极端（距角≈0°，靠距离区分：下合近、上合远）
MERCURY_INFERIOR = datetime(2026, 11, 5, tzinfo=timezone.utc)
MERCURY_SUPERIOR = datetime(2027, 4, 29, tzinfo=timezone.utc)
VENUS_INFERIOR = datetime(2026, 10, 25, tzinfo=timezone.utc)
VENUS_SUPERIOR = datetime(2026, 1, 6, tzinfo=timezone.utc)
# 外行星：冲（距角≈180°）、合（距角≈0°）、方照（距角≈90°，相位角最大）
MARS_OPPOSITION = datetime(2027, 2, 20, tzinfo=timezone.utc)
MARS_CONJUNCTION = datetime(2026, 1, 9, tzinfo=timezone.utc)
MARS_QUADRATURE = datetime(2027, 5, 26, tzinfo=timezone.utc)
JUPITER_OPPOSITION = datetime(2026, 1, 10, tzinfo=timezone.utc)
JUPITER_QUADRATURE = datetime(2026, 4, 6, tzinfo=timezone.utc)
SATURN_OPPOSITION = datetime(2026, 10, 5, tzinfo=timezone.utc)
SATURN_QUADRATURE = datetime(2026, 12, 30, tzinfo=timezone.utc)


def _planet(name: str, when: datetime):
    return sol.get_planet_position(name, when.timestamp(), SITE)


def _legacy_illum(name: str, when: datetime) -> float:
    """复原旧实现（r_p 被填成地心距）的照度，只用于「旧实现必红」反证。"""
    pos = _planet(name, when)
    sun = sol.get_sun_position(when.timestamp(), SITE)
    cos_chi = max(-1.0, min(1.0, float(sun.distance_au) / (2.0 * float(pos.distance_au))))
    return 0.5 * (1.0 + cos_chi)


# --------------------------------------------------------------------------
# 1. 纯几何：三边余弦定理与边界
# --------------------------------------------------------------------------
def test_geometry_hits_both_ends_of_the_range():
    # 行星在日地之间（下合几何）：三边共线，χ=180° → 全暗
    assert sol.planet_illum_from_geometry(1.0, 0.5, 1.5) == pytest.approx(0.0)
    assert sol.planet_phase_angle_from_geometry(1.0, 0.5, 1.5) == pytest.approx(180.0)
    # 行星在地球外侧、日地同侧（外行星冲/合几何）：χ=0 → 全亮
    assert sol.planet_illum_from_geometry(1.0, 2.0, 1.0) == pytest.approx(1.0)
    assert sol.planet_phase_angle_from_geometry(1.0, 2.0, 1.0) == pytest.approx(0.0)


def test_geometry_rejects_degenerate_triangle():
    # 旧实现正是靠「r_helio 与 delta 填同一个数」蒙混过关；退化必须报错。
    for bad in ((0.0, 1.0, 1.0), (1.0, -0.5, 1.0), (1.0, 1.0, 0.0)):
        with pytest.raises(ValueError):
            sol.planet_illum_from_geometry(*bad)


def test_geometry_matches_independent_quadrature_triangle():
    # 火星方照的独立手算：r_helio=1.5, Δ=sqrt(1.5²+1²)=1.8028, R=1
    cos_chi = (1.5 ** 2 + 1.8028 ** 2 - 1.0) / (2.0 * 1.5 * 1.8028)
    assert sol.planet_illum_from_geometry(1.5, 1.8028, 1.0) == \
        pytest.approx(0.5 * (1.0 + cos_chi))


# --------------------------------------------------------------------------
# 2. 内行星：下合细牙、上合满相
# --------------------------------------------------------------------------
def test_venus_inferior_conjunction_is_crescent_not_full():
    pos = _planet("venus", VENUS_INFERIOR)
    assert pos.phase_angle_deg >= 165.0
    assert pos.illumination <= 0.02, f"下合应近新月，实测 {pos.illumination:.5f}"
    # 旧实现此处把余弦夹到 1，给「满月」——反证这条必红
    assert _legacy_illum("venus", VENUS_INFERIOR) >= 0.99


def test_venus_superior_conjunction_is_full_not_half():
    pos = _planet("venus", VENUS_SUPERIOR)
    assert pos.phase_angle_deg <= 5.0
    assert 0.99 <= pos.illumination < 1.0, \
        f"上合应近满相（且不能恰好 1.0），实测 {pos.illumination!r}"
    # 旧实现给 ~0.64 的假「半亮」
    assert _legacy_illum("venus", VENUS_SUPERIOR) <= 0.70


def test_mercury_shows_both_extremes():
    inferior = _planet("mercury", MERCURY_INFERIOR)
    superior = _planet("mercury", MERCURY_SUPERIOR)
    assert inferior.illumination <= 0.02, f"水星下合实测 {inferior.illumination:.5f}"
    assert 0.99 <= superior.illumination < 1.0, \
        f"水星上合实测 {superior.illumination!r}"
    assert inferior.phase_angle_deg >= 165.0
    assert superior.phase_angle_deg <= 5.0


# --------------------------------------------------------------------------
# 3. 火星：冲日满相、方照最暗；合日在外行星几何上也接近满相
# --------------------------------------------------------------------------
def test_mars_opposition_is_full_and_quadrature_is_clearly_not():
    opp = _planet("mars", MARS_OPPOSITION)
    quad = _planet("mars", MARS_QUADRATURE)
    assert opp.illumination >= 0.99
    assert quad.illumination < 0.95, f"方照应明显 <1，实测 {quad.illumination:.5f}"
    assert quad.illumination > 0.80, "方照不该被压成半亮（旧实现 0.70 的假象）"


def test_mars_conjunction_is_full_by_outer_planet_physics():
    """外行星「合日」是行星躲在太阳背后：被照半球仍朝向地球 → 接近满相。

    这是物理，不是「新月」。内行星的下合才是细牙（见上面金星/水星两条）。
    旧实现把合日算成 0.60，是把外行星当月亮处理的同一类错误。
    """
    conj = _planet("mars", MARS_CONJUNCTION)
    assert conj.phase_angle_deg <= 5.0
    assert conj.illumination >= 0.99, f"火星合日实测 {conj.illumination:.5f}"
    assert _legacy_illum("mars", MARS_CONJUNCTION) <= 0.70


# --------------------------------------------------------------------------
# 4. 外行星：接近 1 是物理，不是被夹出来的
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name,quad_time,opp_time,min_illum", [
    ("jupiter", JUPITER_QUADRATURE, JUPITER_OPPOSITION, 0.985),
    ("saturn", SATURN_QUADRATURE, SATURN_OPPOSITION, 0.995),
])
def test_outer_planets_near_full_but_strictly_less_than_one(
        name, quad_time, opp_time, min_illum):
    quad = _planet(name, quad_time)
    opp = _planet(name, opp_time)
    # 方照是外行星相位角的极大处，照度最低——必须严格 <1 且可测地小于 1，
    # 证明没有「夹到 1」；外行星接近 1 是 r_helio ≫ R 的真实几何结果。
    assert min_illum <= quad.illumination < 1.0, \
        f"{name} 方照实测 {quad.illumination!r}"
    assert 1.0 - quad.illumination >= 0.002, \
        f"{name} 方照与 1 的差不可为 0（疑似夹值）：{quad.illumination!r}"
    assert opp.illumination > quad.illumination
    assert opp.illumination - quad.illumination < 0.02, "外行星照度应随距角缓慢变化"
    # 旧实现把外行星压成 0.52–0.55 的假「半亮」
    assert _legacy_illum(name, quad_time) <= 0.56


# --------------------------------------------------------------------------
# 5. 三后端一致性（skyfield / jplephem / astropy）
# --------------------------------------------------------------------------
def _backend(kind):
    """构造指定引擎的后端实例；不可用的返回 (None, 原因)。"""
    be = sol._EphemerisBackend(auto_download=False)
    if kind == "skyfield":
        if be._engine != "skyfield":
            return None, "无 de421.bsp（skyfield 引擎不可用）"
        return be, ""
    if kind == "jplephem":
        if importlib.util.find_spec("jplephem") is None:
            return None, "jplephem 未安装"
        if not sol._file_valid(sol.DE421_PATH):
            return None, "无 de421.bsp（jplephem 引擎不可用）"
        from jplephem.spk import SPK
        try:
            spk = SPK.open(sol.DE421_PATH)
        except OSError as exc:
            return None, f"de421.bsp 无法打开（{type(exc).__name__}）"
        handle = sol._EphemerisHandle(engine="jplephem", spk=spk)
        be._apply_handle(handle)
        return be, ""
    if kind == "astropy":
        if importlib.util.find_spec("astropy") is None:
            return None, "astropy 未安装"
        be._init_astropy()
        if be._astropy is None:
            return None, "astropy 回退初始化失败"
        return be, ""
    raise AssertionError(kind)


def _planet_with_backend(be, name: str, when: datetime):
    old = sol._backend
    sol._backend = be
    try:
        return sol.get_planet_position(name, when.timestamp(), SITE)
    finally:
        sol._backend = old


def test_three_backends_agree_on_planet_illumination():
    used, skipped = [], []
    for kind in ("skyfield", "jplephem", "astropy"):
        be, why = _backend(kind)
        if be is None:
            skipped.append(f"{kind}: {why}")
            continue
        pos = _planet_with_backend(be, "mars", MARS_QUADRATURE)
        used.append((kind, float(pos.illumination)))

    if len(used) < 2:
        pytest.skip("可用后端不足 2 个：" + "；".join(skipped))

    values = [v for _k, v in used]
    assert max(values) - min(values) <= 0.01, f"三后端照度不一致：{used}"
    for kind, value in used:
        assert 0.88 <= value <= 0.91, f"{kind} 火星方照照度越界：{value:.5f}"
    if skipped:
        print("跳过的后端：" + "；".join(skipped))
