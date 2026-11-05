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


# --------------------------------------------------------------------------
# 6. 与 Meeus 独立模型（mbdsdr_ai/astronomy/planets.py）交叉校验
# --------------------------------------------------------------------------
def _illum_from_phase_deg(phase_deg: float) -> float:
    """参考定义：illum = 0.5(1+cos χ)，与渲染层 planet_phase_illumination 同式。"""
    return 0.5 * (1.0 + math.cos(math.radians(phase_deg)))


def _meeus_planet(name: str, when: datetime):
    from mbdsdr_ai.astronomy import planets as _p
    jd = when.timestamp() / 86400.0 + 2440587.5
    return _p.compute_planet(name, jd)


def test_planets_module_delegates_phase_geometry():
    """独立模型只算星历三边，余弦定理必须调 solar_system 的唯一入口。

    这条是结构守卫：谁想在 planets.py 里再写一份 ``(r²+Δ²-R²)/(2rΔ)``，
    这里立刻红（数值交叉校验在两边都正确时抓不到「悄悄新增第二份」）。
    """
    import inspect
    from mbdsdr_ai.astronomy import planets as _p
    src = inspect.getsource(_p.compute_planet)
    assert "planet_phase_angle_from_geometry(" in src, \
        "planets.py 必须委托 solar_system.planet_phase_angle_from_geometry"
    assert "r_e * r_e" not in src, "旧余弦定理片段仍在（第二份实现）"
    assert "math.acos" not in src, "acos 也应归一到唯一入口"


CROSS_CHECK_EVENTS = [
    ("venus", VENUS_INFERIOR),
    ("venus", VENUS_SUPERIOR),
    ("mercury", MERCURY_INFERIOR),
    ("mars", MARS_OPPOSITION),
    ("mars", MARS_QUADRATURE),
    ("jupiter", JUPITER_OPPOSITION),
    ("jupiter", JUPITER_QUADRATURE),
    ("saturn", SATURN_OPPOSITION),
    ("saturn", SATURN_QUADRATURE),
]


def test_meeus_model_and_ephemeris_model_agree_on_illumination():
    """两份独立实现（Meeus 低精度 vs DE421）在同一时刻的照度差 ≤1e-4。

    意义：任一侧以后偷偷改公式，数值立刻分叉、这条当场红。
    """
    worst = (0.0, "")
    for name, when in CROSS_CHECK_EVENTS:
        eph_illum = float(_planet(name, when).illumination)
        meeus_illum = _illum_from_phase_deg(
            float(_meeus_planet(name, when).phase_angle_deg))
        diff = abs(eph_illum - meeus_illum)
        if diff > worst[0]:
            worst = (diff, f"{name} {when.date()}")
        assert diff <= 1e-4, \
            f"{name} {when.date()} 两模型照度差 {diff:.2e}：{eph_illum:.5f} vs {meeus_illum:.5f}"
    print(f"两模型最大照度差 {worst[0]:.2e}（{worst[1]}）")


# 4 时刻 × 4 行星：跨模型最坏网格（含水星借金星下合时刻，Meeus 低精度误差最大）
COS_GRID_EVENTS = (
    MERCURY_INFERIOR,
    VENUS_INFERIOR,
    MARS_QUADRATURE,
    SATURN_QUADRATURE,
)
COS_GRID_PLANETS = ("mercury", "venus", "mars", "saturn")

# 跨模型带宽：Meeus ch.31 低精度 vs DE421 的星历差上限（实测 2.900e-4 / 1.450e-4）。
# 这是**模型差**的封顶，不是公式错误的容差——公式错误量级 1e-2~1e0（见下方变异测试）。
MODEL_BAND_COS = 5e-4
MODEL_BAND_ILLUM = 3e-4
# 同一星历、同一三边的精确网：调用点传错边长顺序时，输出与直接重算的差 ~1e-5~1e0，
MODEL_SAME_SOURCE_TOL = 1e-12


def test_meeus_and_ephemeris_cos_phase_agree_within_model_tolerance():
    """4 时刻 × 4 行星：两条路径的 cos χ 与 illum 差必须落在「模型精度带」内。

    公式已经单源（两边都走 ``planet_phase_angle_from_geometry``），剩余差
    全部来自星历模型：Meeus ch.31 低精度 vs DE421。实测最坏值
    ``|Δcos χ| = 2.900e-4``、``|Δillum| = 1.450e-4``（均出现在
    mercury @ 2026-10-25，即「水星 + 金星下合时刻」这组最不利几何）。
    因此 1e-6 不可达；这里是**显式约束**，谁把任一侧悄悄改出模型精度就红。

    为什么保持硬阈值（2.900e-4 对 5e-4 只有 1.72 倍，看起来窄）：
    两条路径在固定时刻、固定星历下是**确定性**的——2.900e-4 是常量，
    不是抽样，1.72 倍不是统计裕度。带宽要防的是「传错边长/写错公式」
    这类 1e-2~1e0 量级的错（见下方变异测试，最小也有 8.48e-3）；
    真发生「有意外层升级」（换 DE440、换高精度级数）时，带宽理应报警，
    处理方式是重测后同时改阈值与文档，**不许为了绿放宽**。
    另外本测试不是唯一网：``test_ephemeris_illum_matches_same_source_triangle``
    用同一星历同一三边做 1e-12 精确对拍，那条对「边长顺序错误」零容忍。
    """
    worst_cos = (0.0, "")
    worst_illum = (0.0, "")
    for when in COS_GRID_EVENTS:
        for name in COS_GRID_PLANETS:
            eph = _planet(name, when)
            meeus = _meeus_planet(name, when)
            cos_eph = math.cos(math.radians(float(eph.phase_angle_deg)))
            cos_meeus = math.cos(math.radians(float(meeus.phase_angle_deg)))
            illum_meeus = 0.5 * (1.0 + cos_meeus)
            d_cos = abs(cos_eph - cos_meeus)
            d_illum = abs(float(eph.illumination) - illum_meeus)
            tag = f"{name} {when.date()}"
            if d_cos > worst_cos[0]:
                worst_cos = (d_cos, tag)
            if d_illum > worst_illum[0]:
                worst_illum = (d_illum, tag)
            assert d_cos <= MODEL_BAND_COS, \
                f"{tag} |Δcos χ|={d_cos:.3e} 超出模型精度带 {MODEL_BAND_COS:.0e}（公式或星历被改？"\
                "若为有意外层升级，重测后同步改阈值与本测试 docstring / limitations-register）"
            assert d_illum <= MODEL_BAND_ILLUM, \
                f"{tag} |Δillum|={d_illum:.3e} 超出模型精度带 {MODEL_BAND_ILLUM:.0e}（公式或星历被改？"\
                "若为有意外层升级，重测后同步改阈值与本测试 docstring / limitations-register）"
    print(f"4×4 最大 |Δcos χ|={worst_cos[0]:.3e}（{worst_cos[1]}）；"
          f"最大 |Δillum|={worst_illum[0]:.3e}（{worst_illum[1]}）")


def test_ephemeris_illum_matches_same_source_triangle():
    """同一星历、同一三边：输出必须与「按正确入参顺序直接重算」一致（≤1e-12）。

    这条是**精确网**，专抓调用点传错边长顺序。跨模型带宽对这类错的灵敏度
    取决于行星距离（外行星 r_helio≈delta，互换几乎是零扰动），抓不住；
    这里从 ``_planet_distances_skyfield`` 取同一组三边重算，差应精确为 0。
    """
    checked = 0
    for when in COS_GRID_EVENTS:
        for name in COS_GRID_PLANETS:
            dist = sol._planet_distances_skyfield(name, when.timestamp())
            if dist is None:
                pytest.skip("skyfield 后端不可用，无法取真实三边")
            pos = _planet(name, when)
            expected = sol.planet_illum_from_geometry(*dist)
            diff = abs(float(pos.illumination) - expected)
            assert diff <= MODEL_SAME_SOURCE_TOL, \
                f"{name} {when.date()} 输出与同源三边重算差 {diff:.3e}（边长顺序被改？）"
            checked += 1
    print(f"同源精确网 {checked} 例，容差 {MODEL_SAME_SOURCE_TOL:.0e}")


def test_model_band_and_exact_net_catch_geometry_arg_mutations():
    """反向变异：传错三边必须被约束抓住，而不是「宽到能过」。

    变异 A：``r_geo`` 填进 ``r_helio`` 位（旧缺陷的真实形态）；
    变异 B：``r_sun`` 与 ``delta`` 互换。
    实测（4×4 网格，skyfield 三边）：

    - 变异 B 全部 16 例都远超带宽（最小 8.48e-3，约带宽的 28 倍）；
    - 变异 A 对水星/金星/火星远超带宽，但 **saturn @ 2026-12-30 只有
      2.24e-5**——外行星 ``r_helio ≈ delta``，把 delta 填进 r_helio 位几乎
      是零扰动，带宽在几何上就看不见它（不是阈值宽，是三边本身近似相等）。
      这类误差由上面 1e-12 的精确网兜住。

    所以本测试断言：每个变异都改变结果 >1e-9（精确网必抓），且变异 B
    全部 > 带宽；变异 A 只在三边可分离时要求 > 带宽。
    """
    worst_b = (float("inf"), "")
    worst_a_exact = (float("inf"), "")
    band_misses = []
    for when in COS_GRID_EVENTS:
        for name in COS_GRID_PLANETS:
            r_helio, delta, r_sun = sol._planet_distances_skyfield(name, when.timestamp())
            correct = sol.planet_illum_from_geometry(r_helio, delta, r_sun)
            mutated_a = sol.planet_illum_from_geometry(delta, delta, r_sun)
            mutated_b = sol.planet_illum_from_geometry(r_helio, r_sun, delta)
            d_a = abs(correct - mutated_a)
            d_b = abs(correct - mutated_b)
            tag = f"{name} {when.date()}"

            # 精确网（1e-12）对两种变异都必抓，这里用 1e-9 留 3 个数量级余量
            assert d_a > 1e-9, f"{tag} 变异 A 只改变 {d_a:.3e}，精确网看不见"
            assert d_b > 1e-9, f"{tag} 变异 B 只改变 {d_b:.3e}，精确网看不见"
            worst_a_exact = min(worst_a_exact, (d_a, tag))
            worst_b = min(worst_b, (d_b, tag))

            assert d_b > MODEL_BAND_ILLUM, \
                f"{tag} 变异 B 只差 {d_b:.3e}，带宽盖住了错误（阈值已失效？）"
            if d_a <= MODEL_BAND_ILLUM:
                band_misses.append((tag, d_a, abs(r_helio - delta)))
    print(f"变异 B 最小差 {worst_b[0]:.3e}（{worst_b[1]}）；"
          f"变异 A 最小差 {worst_a_exact[0]:.3e}（{worst_a_exact[1]}）")
    for tag, d_a, gap in band_misses:
        print(f"带宽未覆盖变异 A：{tag} 差 {d_a:.2e}，|r_helio-delta|={gap:.3f} AU（精确网兜住）")


LEGACY_WRONG_ILLUM = (
    ("mercury", MERCURY_INFERIOR, 0.86697),
    ("venus", VENUS_INFERIOR, 1.00000),
    ("mars", MARS_CONJUNCTION, 0.60227),
)


def test_model_band_rejects_historical_wrong_illumination_values():
    """历史缺陷的实际输出（0.86697 / 1.00000 / 0.60227）必须被带宽抓住。

    ``_legacy_illum`` 复原旧实现，先断言它复现这三个历史值，再断言当前
    正确值与历史值之差远超带宽——把「旧缺陷会被抓住」钉在可复跑的数字上，
    不是推理。
    """
    for name, when, historical in LEGACY_WRONG_ILLUM:
        legacy_now = _legacy_illum(name, when)
        assert abs(legacy_now - historical) <= 1e-5, \
            f"{name} {when.date()} 旧公式复现 {legacy_now:.5f} ≠ 历史值 {historical:.5f}"
        true_illum = float(_planet(name, when).illumination)
        diff = abs(true_illum - historical)
        print(f"{name} {when.date()}：历史错误 {historical:.5f} / 当前 {true_illum:.6f} / "
              f"差 {diff:.3e}（带宽 {MODEL_BAND_ILLUM:.0e}）")
        assert diff > MODEL_BAND_ILLUM, \
            f"{name} {when.date()} 历史错误只差 {diff:.3e}，带宽盖住了"


def test_phase_angle_zero_basis_is_identical_between_models():
    """零度基准一致：两边都是「日-行星-地球」夹角，0°=全亮。

    对照：月球函数的入参是「日月距角」，0°=新月，且月相照度用 1-cos——
    语义相反，别把月球的换算套到行星上。
    """
    for name, when in CROSS_CHECK_EVENTS:
        eph = float(_planet(name, when).phase_angle_deg)
        meeus = float(_meeus_planet(name, when).phase_angle_deg)
        # Meeus ch.31 低精度（外行星 ~0.1°）与 DE421 的允许差，不是基准差
        assert abs(eph - meeus) <= 0.5, f"{name} {when.date()} 基准不一致：{eph} vs {meeus}"
        assert 0.0 <= eph <= 180.0 and 0.0 <= meeus <= 180.0

