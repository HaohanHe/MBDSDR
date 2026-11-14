# SPDX-License-Identifier: MIT
"""物理常量口径登记表 + 常态对拍（2026-11-14，QGIS 复核式学习第三轮落地）。

借的是 QGIS 的**治理手段**，不是它的坐标栈：

- ``QgsEllipsoidUtils::EllipsoidParameters`` 把 ``semiMajor`` 的单位直接写进
  结构体注释（"in meters"），``QgsDistanceArea::lengthUnits()`` 让调用方
  能查出当前长度单位；``Qgis::DistanceUnit`` 把 Meters / Kilometers 列为
  **不同枚举值**，``QgsUnitTypes::fromUnitToUnitFactor`` 才做显式换算。
- 对应到本项目：几何常量必须「值 + 单位 + 口径族 + 消费链」一起登记，
  同名不算同口径。``orbit.WGS72_A``(km) 与 ``orbit_dynamics.WGS72_A``(m)
  同名差 1000 倍；``orbit.OMEGA_E`` 与 ``orbit_dynamics.OMEGA_E`` 同名
  不同规范值。二者都**不许统一**，但必须可被机器查出来。

独立 oracle：下表数值是规范文本硬编码，**不 import 产品常量生成期望值**。
本文件同时是「椭球唯一真值」的结构守卫：把字面量改回去必须变红。
"""
from __future__ import annotations

import ast
import importlib
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Tuple

import pytest

_REPO = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ConstantSpec:
    """一条口径登记：符号、规范值、单位、口径族、消费链。"""

    module: str
    symbol: str
    value: float
    unit: str
    family: str
    consumers: str


_F72 = 1.0 / 298.26
_E2_72 = _F72 * (2.0 - _F72)

#: 登记表。新增/改动任何一条都必须同步改这里 —— 这就是「常态对拍」的锚。
SPECS: Tuple[ConstantSpec, ...] = (
    # --- 椭球：唯一定义在 geodesy（米制） ---
    ConstantSpec("mbdsdr_ai.geodesy", "WGS72.a_m", 6378135.0, "m",
                 "WGS-72/SGP4", "orbit(km 链)、orbit_dynamics(m 链)"),
    ConstantSpec("mbdsdr_ai.geodesy", "WGS72.f", _F72, "dimensionless",
                 "WGS-72/SGP4", "orbit、orbit_dynamics、astronomy"),
    ConstantSpec("mbdsdr_ai.geodesy", "WGS72.e2", _E2_72, "dimensionless",
                 "WGS-72/SGP4", "orbit、astronomy"),
    ConstantSpec("mbdsdr_ai.geodesy", "WGS84.a_m", 6378137.0, "m",
                 "WGS-84/RTK", "CoordinateConverter、GNSS 链"),
    # --- SGP4 / TEME 的 km 链 ---
    ConstantSpec("mbdsdr_ai.orbit", "WGS72_A", 6378.135, "km",
                 "WGS-72/SGP4", "ground_track、decoders、sat_passes"),
    ConstantSpec("mbdsdr_ai.orbit", "WGS72_F", _F72, "dimensionless",
                 "WGS-72/SGP4", "wgs72_* 正反解"),
    ConstantSpec("mbdsdr_ai.orbit", "WGS72_B", 6378.135 * (1.0 - _F72), "km",
                 "WGS-72/SGP4", "wgs72_ecef_km_to_llh 极点分支"),
    ConstantSpec("mbdsdr_ai.orbit", "WGS72_E2", _E2_72, "dimensionless",
                 "WGS-72/SGP4", "wgs72_* 正反解"),
    ConstantSpec("mbdsdr_ai.orbit", "OMEGA_E", 7.292115e-5, "rad/s",
                 "WGS-72/SGP4", "TEME→ECEF 速度修正"),
    ConstantSpec("mbdsdr_ai.orbit", "C_LIGHT_KMPS", 299792.458, "km/s",
                 "SI/真空光速", "decoders 多普勒"),
    # ---动力学 m 链：同名不同单位 ---
    ConstantSpec("mbdsdr_ai.orbit_dynamics", "WGS72_A", 6378135.0, "m",
                 "WGS-72/SGP4", "RLS/STM、geodetic_to_ecef(m)"),
    ConstantSpec("mbdsdr_ai.orbit_dynamics", "OMEGA_E", 7.2921151467e-5, "rad/s",
                 "IS-GPS/RTKLIB", "ecef_to_eci/eci_to_ecef 旋转"),
    ConstantSpec("mbdsdr_ai.orbit_dynamics", "MU_EARTH_KM3_S2", 398600.4418,
                 "km3/s2", "IGS/EGM", "two_body_dynamics_stm"),
    # --- 其余同名 / 同族实现，登记而不合并 ---
    ConstantSpec("mbdsdr_ai.orbit_determination", "OMEGA_E", 7.292115e-5, "rad/s",
                 "WGS-72/SGP4", "定轨参考实现"),
    ConstantSpec("mbdsdr_ai.orbit_determination", "GM_EARTH_KM3_S2", 398600.4418,
                 "km3/s2", "IGS/EGM", "轨道积分"),
    ConstantSpec("mbdsdr_ai.gps_nav", "OMEGA_EARTH", 7.2921151467e-5, "rad/s",
                 "IS-GPS/RTKLIB", "GPS 星历 ECEF 旋转"),
    ConstantSpec("mbdsdr_ai.gps_nav", "MU_EARTH_M3_S2", 3.986005e14, "m3/s2",
                 "GPS ICD", "GPS 轨道根数"),
    ConstantSpec("mbdsdr_ai.gpredict_adapter", "GE_KM3_S2", 3.986008e5, "km3/s2",
                 "WGS-72/Spacetrack", "gpredict 镜像 SGP4"),
    ConstantSpec("mbdsdr_ai.gpredict_adapter", "MFACTOR", 7.292115e-5, "rad/s",
                 "WGS-72/Spacetrack", "gpredict 镜像 SGP4"),
    ConstantSpec("mbdsdr_ai.geodesy", "OMGE", 7.2921151467e-5, "rad/s",
                 "IS-GPS/RTKLIB", "RTKLIB 常量叶子"),
)


def _resolve(module_name: str, dotted: str) -> Any:
    obj: Any = importlib.import_module(module_name)
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


def _numeric_literals(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    }


def _imports_wgs72_from_geodesy(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "geodesy"
        and any(alias.name == "WGS72" for alias in node.names)
        for node in ast.walk(tree)
    )


class FakeSat:
    """只提供 sgp4(jd, fr) 的假传播器，避免测试依赖真实 TLE / sgp4 库。"""

    def sgp4(self, jd: float, fr: float):
        return 0, (7000.0, 1000.0, 500.0), (0.0, 7.5, 0.0)


_LAT, _LON, _T_UNIX = 39.9, 116.4, 1763000000.0
_GOLDEN_ECEF_KM = (-2178.673427036012, 4388.9091589526015, 4069.536747041215)
_GOLDEN_EL_AZ = (-65.71984320053457, 19.67257527933333)


def test_every_registered_constant_matches_runtime():
    """常态对拍：登记表每条都必须在运行时逐位对得上，不许出现影子副本。"""
    for spec in SPECS:
        actual = _resolve(spec.module, spec.symbol)
        assert actual == spec.value, (
            f"{spec.module}:{spec.symbol} 实测 {actual!r} != 登记 {spec.value!r}"
            f"（unit={spec.unit}, family={spec.family}）")


def test_same_name_different_units_stay_distinct():
    """orbit.WGS72_A(km) 与 orbit_dynamics.WGS72_A(m)：同名差 1000 倍。"""
    km = _resolve("mbdsdr_ai.orbit", "WGS72_A")
    m = _resolve("mbdsdr_ai.orbit_dynamics", "WGS72_A")
    assert m / km == 1000.0
    rows = [s for s in SPECS if s.symbol == "WGS72_A"]
    assert len(rows) == 2
    assert {s.unit for s in rows} == {"km", "m"}
    assert {s.family for s in rows} == {"WGS-72/SGP4"}
    # 登记表必须点名两链，且明确写了「禁止直接互传」的边界。
    assert any("ground_track" in s.consumers for s in rows)
    assert any("RLS" in s.consumers for s in rows)


def test_same_name_different_spec_values_are_not_unified():
    """OMEGA_E 同名但两套规范值：SGP4 的 7.292115e-5 与 IS-GPS 的 ...1467e-5。"""
    sgp4 = _resolve("mbdsdr_ai.orbit", "OMEGA_E")
    isgps = _resolve("mbdsdr_ai.orbit_dynamics", "OMEGA_E")
    assert sgp4 != isgps
    assert math.isclose(isgps - sgp4, 1.467e-12, rel_tol=0.0, abs_tol=1e-18)
    omega_rows = [s for s in SPECS if s.symbol == "OMEGA_E"]
    assert {s.family for s in omega_rows} == {"WGS-72/SGP4", "IS-GPS/RTKLIB"}


def test_three_gm_spec_values_are_not_unified():
    """GPS ICD / IGS / WGS-72 三套 GM 必须都在，不许「顺手统一」。"""
    gps_icd_km = _resolve("mbdsdr_ai.gps_nav", "MU_EARTH_M3_S2") / 1e9
    igs = _resolve("mbdsdr_ai.orbit_dynamics", "MU_EARTH_KM3_S2")
    wgs72 = _resolve("mbdsdr_ai.gpredict_adapter", "GE_KM3_S2")
    assert gps_icd_km == pytest.approx(398600.5, abs=1e-6)
    assert wgs72 - igs == pytest.approx(0.3582, abs=1e-9)
    assert gps_icd_km != wgs72


def test_ellipsoid_definition_has_single_source():
    """结构守卫：椭球数值只许在 geodesy.WGS72 出现，orbit/astronomy 必须委托。

    反证：把 ``a = 6378.135`` 或 ``WGS72_A = 6378.135`` 改回字面量，本例必红。
    """
    paths = (_REPO / "mbdsdr_ai" / "orbit.py",
             _REPO / "mbdsdr_ai" / "astronomy.py")
    forbidden = {6378.135, 6378135.0, 298.26}
    for path in paths:
        assert _numeric_literals(path).isdisjoint(forbidden), (
            f"{path.name} 又写回了 WGS-72 椭球字面量，单一真值被破坏")
        assert _imports_wgs72_from_geodesy(path), (
            f"{path.name} 没有从 geodesy 取 WGS72")


def test_only_two_module_level_wgs72_a_definitions():
    """全仓实测：模块级 WGS72_A 只允许两条链各一份，禁止出现第三个同名常量。"""
    found = []
    for path in sorted((_REPO / "mbdsdr_ai").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:  # 只看模块级，函数局部不算
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "WGS72_A":
                        found.append(path.name)
    assert sorted(found) == ["orbit.py", "orbit_dynamics.py"]


def test_wgs72_km_constants_are_bitwise_derived_from_geodesy():
    """km 链四个常量与 geodesy 米制定义逐位一致（不是近似相等）。"""
    from mbdsdr_ai import orbit
    from mbdsdr_ai.geodesy import WGS72

    a_m = WGS72.a_m
    assert orbit.WGS72_A == a_m / 1000.0
    assert orbit.WGS72_F == WGS72.f
    assert orbit.WGS72_B == (a_m / 1000.0) * (1.0 - WGS72.f)
    assert orbit.WGS72_E2 == WGS72.e2
    assert orbit.WGS72_E2 == WGS72.f * (2.0 - WGS72.f)


def test_golden_vectors_unchanged_after_single_source_refactor():
    """收敛前实测的黄金向量：站心 ECEF / 反解 / 天文 ENU 角度都不许漂。"""
    from mbdsdr_ai import orbit

    ecef = orbit.wgs72_geodetic_to_ecef_km(_LAT, _LON, 0.1)
    for got, want in zip(ecef, _GOLDEN_ECEF_KM):
        assert got == pytest.approx(want, abs=1e-9)
    lat, lon, alt = orbit.wgs72_ecef_km_to_llh(*ecef)
    assert (lat, lon, alt) == pytest.approx((_LAT, _LON, 0.1), abs=1e-9)

    import mbdsdr_ai.astronomy  # noqa: F401 - 触发 __init__ 的 legacy 桥接
    legacy = sys.modules["mbdsdr_ai._astronomy_legacy"]
    el_az = legacy._sat_elaz_at_unix(FakeSat(), _T_UNIX, _LAT, _LON, 0.0)
    assert list(el_az) == pytest.approx(list(_GOLDEN_EL_AZ), abs=1e-9)


def test_astronomy_follows_the_single_source_at_runtime():
    """动态反证：分别只改 a_m 与 f，站心几何都必须跟着变（不是硬编码）。

    第一版只把整个椭球换成 WGS-84，结果「a 写死、e2 仍跟随」也能通过 ——
    反证实测抓到过这个假绿，故拆成两个只动一个字段的变体。
    """
    from dataclasses import replace

    import mbdsdr_ai.astronomy  # noqa: F401
    from mbdsdr_ai.geodesy import WGS72

    legacy = sys.modules["mbdsdr_ai._astronomy_legacy"]

    def elaz():
        return legacy._sat_elaz_at_unix(FakeSat(), _T_UNIX, _LAT, _LON, 0.0)

    before = elaz()
    assert list(before) == pytest.approx(list(_GOLDEN_EL_AZ), abs=1e-9)

    variants = (
        ("a_m", replace(WGS72, a_m=WGS72.a_m + 2000.0)),   # 只动长半轴
        ("f/e2", replace(WGS72, f=WGS72.f * 1.01)),         # 只动扁率/偏心率的平方
    )
    for label, variant in variants:
        original = legacy._WGS72_ELLIPSOID
        try:
            legacy._WGS72_ELLIPSOID = variant
            after = elaz()
        finally:
            legacy._WGS72_ELLIPSOID = original
        assert after != before, (
            f"只改 {label} 后站心几何没变，说明该椭球参数仍是硬编码")
    assert list(elaz()) == pytest.approx(list(_GOLDEN_EL_AZ), abs=1e-9)


def test_registry_units_are_explicit():
    """登记表的单位字段不许含糊：每个符号必须有明确单位与口径族。"""
    valid_units = {"m", "km", "rad/s", "km/s", "km3/s2", "m3/s2",
                   "dimensionless"}
    for spec in SPECS:
        assert spec.unit in valid_units
        assert spec.family
        assert spec.consumers
    assert len({(s.module, s.symbol) for s in SPECS}) == len(SPECS), (
        "登记表出现重复键")


# ---------------------------------------------------------------------------
# 附带修复的钉扎：回归中暴露的既有红（HEAD 基线反证确认，非本轮改动引入）
# equatorial_to_altaz 曾另写一条「地平线下 = known」分支，把「折射不适用」
# 冒充「气象已知」。修法同样是把判定收回唯一入口，故一并钉在这里。
# ---------------------------------------------------------------------------

def _measured_observer():
    from datetime import datetime, timezone

    from mbdsdr_ai.astronomy import Observer

    at = (datetime.now(timezone.utc).replace(microsecond=0)
          .isoformat().replace("+00:00", "Z"))
    return Observer(
        latitude_deg=31.2, longitude_deg=121.5, height_m=50.0,
        pressure_hpa=1012.0, temperature_c=18.0, humidity=60.0,
        weather_at_utc=at, weather_source="测试气象站")


def test_refraction_single_entry_for_below_horizon():
    """低仰角 + 气象未知必须是 None，不能是无折射路径的 0.0。"""
    from mbdsdr_ai.astronomy import Observer, compute_refraction

    assert compute_refraction(-23.0, Observer()) is None
    assert compute_refraction(-23.0, _measured_observer()) == 0.0
    assert compute_refraction(10.0, Observer()) is None


def test_altaz_below_horizon_does_not_fake_known_weather():
    """地平线下的天体：无实测气象标 unknown，有实测气象才是 known/0.0。"""
    from mbdsdr_ai.astronomy import EquatorialCoord, Observer, unix_to_jd

    jd = unix_to_jd()
    eq = EquatorialCoord(ra_deg=150.0, dec_deg=20.0)
    unknown = eq.to_altaz(Observer(latitude_deg=31.2, longitude_deg=121.5), jd)
    assert unknown.alt_deg < -2.0
    assert unknown.refraction_deg is None
    assert unknown.refraction_status == "unknown"

    known = eq.to_altaz(_measured_observer(), jd)
    assert known.refraction_deg == 0.0
    assert known.refraction_status == "known"


def test_refraction_weather_check_is_the_first_branch():
    """结构守卫：把低仰角早退挪回气象检查之前，本例必红。"""
    src = (_REPO / "mbdsdr_ai" / "astronomy.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "compute_refraction")
    first_if = next(s for s in fn.body if isinstance(s, ast.If))
    segment = ast.get_source_segment(src, first_if.test) or ""
    assert "refraction_weather_status" in segment, (
        "低仰角早退又跑到气象检查前面了，unknown 会被冒充成 known")


def test_altaz_has_no_second_refraction_branch():
    """结构守卫：equatorial_to_altaz 不许再按 alt_deg 分叉出第二套折射口径。"""
    src = (_REPO / "mbdsdr_ai" / "astronomy.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "equatorial_to_altaz")
    conditions = [ast.get_source_segment(src, s.test) or ""
                  for s in ast.walk(fn) if isinstance(s, ast.If)]
    assert not any("alt_deg > -2.0" in cond for cond in conditions), (
        "又出现了地平线上下分叉，折射状态判定不再唯一")

