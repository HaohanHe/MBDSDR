# SPDX-License-Identifier: MIT
"""球面大圆距离唯一真值源守卫（QGIS-fed 治理：单一真值源 + 独立 oracle + 结构守卫）。

背景
----
历史上 adsb_map / location.places / new_spacetime / xastir_adapter / acquisition
各写一份 haversine，且平均地球半径取值不一（6371.0 / 6371.0088 / 6371000.0 km/m
混用）。本套守卫钉住收敛后的事实：

  1. 唯一实现：五处再导出/薄包装的函数与常量，与 geo_distance 是**同一对象**
     （object-identity 结构守卫，`is`）——任何一处偷偷改回本地副本都会红。
  2. 唯一半径：平均半径取 IUGG R1 = (2a + b)/3（WGS-84）≈ 6371.0088 km，
     与 geodesy 的椭球长半轴 a = 6378137 m 是**不同物理量**，刻意不统一
     （口径分离守卫：b < R1 < a）。
  3. 数值正确：用**独立推导**的弦长 oracle（3D 单位向量欧氏弦 → 中心角）
     对拍金标准向量，不复用 haversine 半角公式，避免"自己验自己"。

诚实红线
--------
- 全部坐标为抽象几何测试点，参考原点 (0.0, 0.0) 仅作"示例站点"，不含任何
  真实城市/地点/呼号（identity redline）。
- 结构守卫用 `is`（对象同一性），金标准用数值近似断言（计算值），**不读源码
  文本**，故不触发 source-pin ratchet，无需登记文本 pin。
- 反证：把任一消费方改回本地副本 → identity 守卫红；把半径改错 → 口径/金标准红。
"""
from __future__ import annotations

import math
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai import geo_distance as G  # noqa: E402
from mbdsdr_ai import adsb_map, acquisition, new_spacetime, xastir_adapter  # noqa: E402
from mbdsdr_ai.location import places  # noqa: E402


# --------------------------------------------------------------------------- #
# 独立 oracle：弦长路线（3D 单位向量 → 欧氏弦 → 中心角），与 haversine 半角公式
# 完全不同的推导，且对小距离/近对跖点都良态。半径用同一口径 R1 以隔离公式差异。
# --------------------------------------------------------------------------- #
def _oracle_chord_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, l1, p2, l2 = map(math.radians, (lat1, lon1, lat2, lon2))
    v1 = (math.cos(p1) * math.cos(l1), math.cos(p1) * math.sin(l1), math.sin(p1))
    v2 = (math.cos(p2) * math.cos(l2), math.cos(p2) * math.sin(l2), math.sin(p2))
    chord = math.sqrt(sum((a - b) ** 2 for a, b in zip(v1, v2)))
    central = 2.0 * math.asin(max(-1.0, min(1.0, chord / 2.0)))
    return G.MEAN_EARTH_RADIUS_KM * central


# 金标准绝对值（km）：在 canonical R1=6371.0088 km 下一次性算出并冻结为字面量。
# 刻意**不**写成 G.MEAN_EARTH_RADIUS_KM 的表达式——否则半径漂移时期望值跟着移动，
# 金标准就失去钉住绝对值的意义。冻结后，半径或公式任何漂移都会令这些断言红。
_GOLDEN_EQUATOR_1DEG_KM = 111.1950802335329   # R1·rad(1°)
_GOLDEN_QUARTER_KM = 10007.557221017962       # R1·π/2（四分之一大圆）
_GOLDEN_HALF_KM = 20015.114442035923          # R1·π（对跖半周长）

# 金标准向量：全部为抽象几何点；原点 (0,0) 仅作示例站点参考。
# (lat1, lon1, lat2, lon2, 期望 km 或 None=仅用 oracle 对拍)
_GOLDEN = [
    (0.0, 0.0, 0.0, 0.0, 0.0),                          # 同点
    (0.0, 0.0, 0.0, 1.0, _GOLDEN_EQUATOR_1DEG_KM),       # 赤道 1°
    (0.0, 0.0, 1.0, 0.0, _GOLDEN_EQUATOR_1DEG_KM),       # 子午线 1°（对称）
    (0.0, 0.0, 0.0, 90.0, _GOLDEN_QUARTER_KM),           # 四分之一周长
    (0.0, 0.0, 0.0, 180.0, _GOLDEN_HALF_KM),             # 对跖：半周长
    (90.0, 0.0, 0.0, 0.0, _GOLDEN_QUARTER_KM),           # 极点到赤道
    (10.0, 20.0, 30.0, 40.0, None),                      # 一般斜向（oracle 对拍）
    (-10.0, -20.0, -30.0, -40.0, None),                  # 南半球/负经度
    (45.0, 0.0, -45.0, 180.0, _GOLDEN_HALF_KM),          # 对跖（斜）
]


# --------------------------------------------------------------------------- #
# 1. 唯一半径口径
# --------------------------------------------------------------------------- #
def test_mean_radius_is_iugg_r1():
    """平均半径 = IUGG R1 = (2a + b)/3（WGS-84），而非随手取的 6371.0。"""
    a = 6378137.0                 # WGS-84 椭球长半轴（m）
    f = 1.0 / 298.257223563
    b = a * (1.0 - f)             # 短半轴（m）
    r1_km = (2.0 * a + b) / 3.0 / 1000.0
    # 6371.0088 是 R1 的四舍五入；容差 1e-4 km 足以拒绝 6371.0（差 0.0088 km）。
    assert abs(G.MEAN_EARTH_RADIUS_KM - r1_km) < 1e-4
    assert G.MEAN_EARTH_RADIUS_M == pytest.approx(G.MEAN_EARTH_RADIUS_KM * 1000.0, abs=1e-6)


def test_mean_radius_kou_jing_separated_from_ellipsoid():
    """口径分离：平均半径严格介于短半轴 b 与长半轴 a 之间，绝不等于椭球 a。

    geodesy 的 ECEF/RTK 用椭球长半轴 a=6378137 m；本模块用平均半径 R1。
    两者是不同物理量，刻意不统一——此守卫防止有人把 R1 误"统一"成 a。
    """
    a_km = 6378.137
    b_km = 6356.752314245
    assert b_km < G.MEAN_EARTH_RADIUS_KM < a_km
    assert G.MEAN_EARTH_RADIUS_M != 6378137.0


# --------------------------------------------------------------------------- #
# 2. 数值正确：独立弦长 oracle 对拍金标准向量
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lat1,lon1,lat2,lon2,expect", _GOLDEN)
def test_haversine_km_matches_independent_oracle(lat1, lon1, lat2, lon2, expect):
    got = G.haversine_km(lat1, lon1, lat2, lon2)
    oracle = _oracle_chord_km(lat1, lon1, lat2, lon2)
    # 两条独立推导路线应在 1e-9 相对量级一致（同半径、同球面几何）。
    assert got == pytest.approx(oracle, rel=1e-9, abs=1e-9)
    if expect is not None:
        assert got == pytest.approx(expect, rel=1e-9, abs=1e-9)


def test_haversine_m_is_km_times_1000():
    for lat1, lon1, lat2, lon2, _ in _GOLDEN:
        km = G.haversine_km(lat1, lon1, lat2, lon2)
        m = G.haversine_m(lat1, lon1, lat2, lon2)
        assert m == pytest.approx(km * 1000.0, rel=1e-12, abs=1e-6)


def test_symmetry_and_zero():
    assert G.haversine_km(0.0, 0.0, 0.0, 0.0) == 0.0
    a = G.haversine_km(10.0, 20.0, 30.0, 40.0)
    b = G.haversine_km(30.0, 40.0, 10.0, 20.0)
    assert a == pytest.approx(b, rel=1e-12, abs=1e-12)


def test_equator_one_degree_closed_form():
    """赤道 1° 的解析闭式 R1·rad(1)，冻结为绝对金标准字面量（不随常量移动）。"""
    assert G.haversine_km(0.0, 0.0, 0.0, 1.0) == pytest.approx(
        _GOLDEN_EQUATOR_1DEG_KM, rel=1e-9, abs=1e-9
    )


# --------------------------------------------------------------------------- #
# 3. 唯一实现：object-identity 结构守卫（`is`，非源码文本 pin）
# --------------------------------------------------------------------------- #
def test_consumers_reexport_same_function_object():
    """五处消费方的 haversine 必须与 geo_distance 是同一函数对象。

    任何一处改回本地 def（哪怕公式/半径完全一样）都会令 `is` 红——
    这正是"唯一真值源"的结构保证，行为对拍无法替代。
    """
    assert adsb_map.haversine_km is G.haversine_km
    assert places.haversine_km is G.haversine_km
    assert xastir_adapter.haversine_m is G.haversine_m
    assert new_spacetime.haversine_km is G.haversine_km


def test_consumers_share_same_radius_object():
    """消费方的半径常量必须与 geo_distance 是同一 float 对象（同一口径）。"""
    assert new_spacetime.EARTH_RADIUS_KM is G.MEAN_EARTH_RADIUS_KM
    assert xastir_adapter.EARTH_RADIUS_M is G.MEAN_EARTH_RADIUS_M
    assert acquisition.EARTH_RADIUS_M is G.MEAN_EARTH_RADIUS_M


def test_new_spacetime_wrapper_delegates():
    """new_spacetime.haversine_distance(GeoPoint, GeoPoint) 薄包装委托到唯一真值源。"""
    p1 = new_spacetime.GeoPoint(latitude=10.0, longitude=20.0)
    p2 = new_spacetime.GeoPoint(latitude=30.0, longitude=40.0)
    wrapped = new_spacetime.haversine_distance(p1, p2)
    canonical = G.haversine_km(10.0, 20.0, 30.0, 40.0)
    assert wrapped == pytest.approx(canonical, rel=1e-12, abs=1e-9)


def test_acquisition_geo_radius_uses_canonical_mean_radius():
    """acquisition 同步轨道半径 = 唯一平均半径 + 35786 km（口径收敛后仍自洽）。"""
    assert acquisition.GEO_RADIUS_M == pytest.approx(
        G.MEAN_EARTH_RADIUS_M + 35_786_000.0, rel=0.0, abs=1e-6
    )


def test_xastir_geofence_uses_canonical_distance():
    """地理围栏 contains 判定走唯一真值源：半径略大于/略小于真实距离时翻转。"""
    fence = xastir_adapter.GeoFence(
        name="示例围栏", center_lat=0.0, center_lon=0.0, radius_m=1.0
    )
    # 赤道上 1e-5° ≈ 1.11 m > 1 m 半径 → 围栏外；0.5e-5° ≈ 0.56 m < 1 m → 内。
    assert fence.contains(0.0, 0.5e-5) is True
    assert fence.contains(0.0, 2e-5) is False