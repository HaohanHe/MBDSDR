# SPDX-License-Identifier: MIT
"""
卫星受晒/地影判定行为测试
=========================

钉住 ``mbdsdr_ai.sat_illumination`` 的物理判据与渲染契约：

  1. 显式输入：位置 + 太阳方向；时间只走 ``at_unix`` / TimeEngine，
     本模块不读墙钟（无 `time`/`datetime.now` 引用）。
  2. 三种模型：圆锥影（gpredict 同构，三态）与圆柱影（二态，无半影）。
  3. 边界：刚进影 / 刚出影 / 半影擦边 / 极区夏季（晨昏轨道）不落影 /
     太阳方向零向量退化。
  4. 与 gpredict ``Sat_Eclipsed`` 独立实现逐点对拍（本影二值）。
  5. 渲染契约：``status``/``sunlit``/``basis``/``model``/``boundary_uncertainty_s``
     /``at_unix``/``geometry`` 字段稳定，``to_dict`` 可 JSON 序列化。

运行: python -m pytest mbdsdr_ai/tests/test_sat_illumination.py -v
"""
import json
import math

import pytest

from .. import sat_illumination as si


# ---------------------------------------------------------------------------
# 独立几何（用于对拍，不复用被测实现）
# ---------------------------------------------------------------------------
def _angle_deg(a, b):
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    dot = sum(x * y for x, y in zip(a, b)) / (na * nb)
    return math.degrees(math.acos(max(-1.0, min(1.0, dot))))


def _pos_on_plane(theta_deg, r_km=6378.137 + 500.0):
    """在 x-y 平面内、与 +x（太阳方向）夹角 theta 的位置。"""
    t = math.radians(theta_deg)
    return (r_km * math.cos(t), r_km * math.sin(t), 0.0)


SUN_UNIT = (1.0, 0.0, 0.0)
R_KM = 6378.137 + 500.0
SD_EARTH_DEG = math.degrees(math.asin(6378.137 / R_KM))       # ≈ 68.0187°
SD_SUN_DEG = math.degrees(math.asin(696000.0 / 149597870.700))  # ≈ 0.2666°
# 本影边界：δ = sd_e - sd_s；δ = 180° - theta（theta 为地心处 pos 与太阳夹角）
UMBRA_THETA_DEG = 180.0 - (SD_EARTH_DEG - SD_SUN_DEG)   # ≈ 112.2479°
OUTER_THETA_DEG = 180.0 - (SD_EARTH_DEG + SD_SUN_DEG)   # ≈ 111.7148°


def _gpredict_eclipsed(pos_km, sun_unit=SUN_UNIT):
    """gpredict ``sgpsdp/solar.c:46-64`` Sat_Eclipsed 的直译（二值本影）。"""
    r_sat = math.sqrt(sum(x * x for x in pos_km))
    sun_vec = tuple(s * si.AU_KM for s in sun_unit)
    rho = tuple(s - p for s, p in zip(sun_vec, pos_km))
    sd_earth = math.asin(6378.137 / r_sat)
    sd_sun = math.asin(696000.0 / math.sqrt(sum(x * x for x in rho)))
    earth = tuple(-p for p in pos_km)   # 卫星→地球方向
    delta = math.radians(_angle_deg(sun_vec, earth))
    depth = sd_earth - sd_sun - delta
    if sd_earth < sd_sun:
        return False
    return depth >= 0.0


# ---------------------------------------------------------------------------
# 1. 显式输入纪律
# ---------------------------------------------------------------------------
def test_position_and_sun_are_required_inputs():
    r = si.evaluate_illumination(_pos_on_plane(150.0), SUN_UNIT)
    assert r.status in (si.STATUS_UMBRA, si.STATUS_PENUMBRA, si.STATUS_SUNLIT)
    assert r.sunlit is False          # 150° 远离太阳，必然在本影
    assert r.basis and "圆锥影" in r.basis


def test_no_sun_data_is_unknown_not_guessed():
    r = si.evaluate_illumination(_pos_on_plane(150.0), None)
    assert r.status == si.STATUS_UNKNOWN
    assert r.sunlit is None
    assert "不猜测" in r.basis


def test_zero_sun_vector_raises_explicitly():
    with pytest.raises(ValueError, match="长度为零"):
        si.evaluate_illumination(_pos_on_plane(150.0), (0.0, 0.0, 0.0))


def test_position_inside_earth_raises():
    with pytest.raises(ValueError, match="未高于地球半径"):
        si.evaluate_illumination((1000.0, 0.0, 0.0), SUN_UNIT)


def test_time_comes_from_explicit_source_or_time_engine(monkeypatch):
    """不给 at_unix 时走全局 TimeEngine；给了就原样记录，不读墙钟。"""
    r = si.evaluate_from_position(_pos_on_plane(150.0), at_unix=1700000000.0)
    assert r.at_unix == 1700000000.0
    assert r.basis and "圆柱影" not in r.basis

    class _Eng:
        def now_unix(self):
            return 1700000100.0

    r2 = si.evaluate_from_position(_pos_on_plane(150.0), time_engine=_Eng())
    assert r2.at_unix == 1700000100.0
    assert r2.model == si.MODEL_CONICAL


def test_sun_direction_helper_is_unit_and_finite():
    for t in (0.0, 1700000000.0, 1700000000.0 + 365 * 86400.0):
        v = si.sun_direction_eci(t)
        assert abs(math.sqrt(sum(x * x for x in v)) - 1.0) < 1e-12
        assert all(math.isfinite(x) for x in v)


# ---------------------------------------------------------------------------
# 2. 边界：刚进影 / 刚出影 / 半影擦边（圆锥影）
# ---------------------------------------------------------------------------
def test_just_entered_umbra():
    """theta 刚越过本影边界（+0.05°）→ umbra。"""
    r = si.evaluate_illumination(_pos_on_plane(UMBRA_THETA_DEG + 0.05), SUN_UNIT)
    assert r.status == si.STATUS_UMBRA
    assert r.sunlit is False
    assert r.geometry["sun_earth_separation_deg"] < r.geometry["umbra_half_angle_deg"]


def test_just_exited_shadow_is_sunlit():
    """theta 刚低于半影外边界（-0.05°）→ sunlit。"""
    r = si.evaluate_illumination(_pos_on_plane(OUTER_THETA_DEG - 0.05), SUN_UNIT)
    assert r.status == si.STATUS_SUNLIT
    assert r.sunlit is True


def test_penumbra_grazing_is_three_state():
    """落在半影带正中 → penumbra，且 sunlit 是 None（部分受照不可二值化）。"""
    mid = 0.5 * (UMBRA_THETA_DEG + OUTER_THETA_DEG)
    r = si.evaluate_illumination(_pos_on_plane(mid), SUN_UNIT)
    assert r.status == si.STATUS_PENUMBRA
    assert r.sunlit is None
    assert "半影" in r.basis


def test_boundary_uncertainty_is_reported_per_model():
    r = si.evaluate_illumination(_pos_on_plane(UMBRA_THETA_DEG + 0.05), SUN_UNIT)
    assert r.boundary_uncertainty_s == si.BOUNDARY_UNCERTAINTY_S[si.MODEL_CONICAL]
    rc = si.evaluate_illumination(_pos_on_plane(UMBRA_THETA_DEG + 0.05), SUN_UNIT,
                                  model=si.MODEL_CYLINDRICAL)
    assert rc.boundary_uncertainty_s == si.BOUNDARY_UNCERTAINTY_S[si.MODEL_CYLINDRICAL]
    assert rc.model == si.MODEL_CYLINDRICAL


# ---------------------------------------------------------------------------
# 3. 圆柱影模型：无半影带（模型自身近似，不是漏算）
# ---------------------------------------------------------------------------
def test_cylindrical_has_no_penumbra_status():
    mid = 0.5 * (UMBRA_THETA_DEG + OUTER_THETA_DEG)
    r = si.evaluate_illumination(_pos_on_plane(mid), SUN_UNIT,
                                 model=si.MODEL_CYLINDRICAL)
    assert r.status in (si.STATUS_UMBRA, si.STATUS_SUNLIT)
    assert r.status != si.STATUS_PENUMBRA
    assert r.sunlit is not None


def test_cylindrical_boundary_at_earth_radius():
    """圆柱边界：垂距 < R_e → 本影；≥ R_e → 受晒。"""
    near = si.evaluate_illumination(
        _pos_on_plane(math.degrees(math.acos(-0.5))), SUN_UNIT,
        model=si.MODEL_CYLINDRICAL)  # 120°：垂距明显 < R_e
    assert near.status == si.STATUS_UMBRA
    far = si.evaluate_illumination(_pos_on_plane(95.0), SUN_UNIT,
                                   model=si.MODEL_CYLINDRICAL)
    assert far.status == si.STATUS_SUNLIT  # 垂距 ≈ 6851 km > R_e


# ---------------------------------------------------------------------------
# 4. 极区夏季：晨昏/极昼轨道整圈不落影
# ---------------------------------------------------------------------------
def test_polar_summer_dawn_dusk_orbit_never_eclipses():
    """太阳赤纬 +23.44°，轨道面取垂直日向的晨昏面：整圈 theta=90° → 受晒。

    取 72 个相位（5° 步进），断言没有一个进入 umbra/penumbra。
    """
    dec = math.radians(23.44)
    sun = (math.cos(dec), 0.0, math.sin(dec))
    # 构造与 sun 垂直的正交基 u/v
    u = (0.0, 1.0, 0.0)
    v = (sun[2], 0.0, -sun[0])   # sun × u 方向，已归一（sun 是单位向量）
    r_km = 6378.137 + 800.0
    statuses = []
    for i in range(72):
        phi = math.radians(5.0 * i)
        p = tuple(r_km * (u[k] * math.cos(phi) + v[k] * math.sin(phi))
                  for k in range(3))
        statuses.append(
            si.evaluate_illumination(p, sun, model=si.MODEL_CONICAL).status)
    assert si.STATUS_UMBRA not in statuses
    assert si.STATUS_PENUMBRA not in statuses
    assert statuses.count(si.STATUS_SUNLIT) == 72


# ---------------------------------------------------------------------------
# 5. 与 gpredict Sat_Eclipsed 对拍（本影二值）
# ---------------------------------------------------------------------------
def test_matches_gpredict_sat_eclipsed_over_grid():
    """沿 theta 扫 100°→120°（含本影/半影/受晒三段），本影判定逐点一致。"""
    checked = 0
    for i in range(101):
        theta = 100.0 + 0.2 * i
        pos = _pos_on_plane(theta)
        ours = si.evaluate_illumination(pos, SUN_UNIT)
        reference = _gpredict_eclipsed(pos)
        assert (ours.status == si.STATUS_UMBRA) == reference, \
            f"theta={theta:.1f}° 本影判定不一致：ours={ours.status}, gpredict={reference}"
        checked += 1
    assert checked == 101


# ---------------------------------------------------------------------------
# 6. 渲染契约（天空视图另一条线直接消费）
# ---------------------------------------------------------------------------
CONTRACT_KEYS = {"status", "model", "basis", "boundary_uncertainty_s",
                 "sunlit", "at_unix", "geometry"}


def test_render_contract_keys_and_enums():
    r = si.evaluate_illumination(_pos_on_plane(115.0), SUN_UNIT,
                                 at_unix=1700000000.0)
    assert set(r.to_dict()) == CONTRACT_KEYS
    assert r.status in (si.STATUS_SUNLIT, si.STATUS_UMBRA,
                        si.STATUS_PENUMBRA, si.STATUS_UNKNOWN)
    assert r.model in si.MODELS
    assert r.basis.strip()
    assert r.boundary_uncertainty_s >= 0.0
    assert r.at_unix == 1700000000.0
    json.dumps(r.to_dict(), ensure_ascii=False)  # 必须可序列化


def test_unknown_state_still_satisfies_contract():
    r = si.evaluate_illumination(_pos_on_plane(115.0), None)
    d = r.to_dict()
    assert set(d) == CONTRACT_KEYS
    assert d["status"] == si.STATUS_UNKNOWN and d["sunlit"] is None


# ---------------------------------------------------------------------------
# 7. TLE + 显式时刻入口
# ---------------------------------------------------------------------------
TLE1 = "1 42960U 17061F   25117.46489195  .00000241  00000+0  78821-4 0  9994"
TLE2 = "2 42960  86.4033 198.9703 0001974 107.2578 252.8834 14.34218356395236"


def test_from_tle_uses_explicit_time_and_reports_state():
    pytest.importorskip("skyfield")
    at = 1700000000.0
    r = si.evaluate_from_tle(TLE1, TLE2, at_unix=at)
    assert r.at_unix == at
    assert r.status in (si.STATUS_SUNLIT, si.STATUS_UMBRA, si.STATUS_PENUMBRA)
    assert r.geometry["r_sat_km"] > si.R_EARTH_KM
    assert r.basis.strip()
