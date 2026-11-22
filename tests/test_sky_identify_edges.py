# SPDX-License-Identifier: MIT
"""``identify_direction`` 的边界测试（离线、确定性、不依赖 Qt）。

覆盖口径
--------
* 方位跨 0°/360°：用随仓星表真实算出一颗 az 落在 355°–5° 的亮星，
  以它自己的 az/alt 反查必须命中，证明视锥判定不是线性截断；
* ``az=0`` 与 ``az=360`` 是同一方向，返回星名集合一致；
* 极端纬度 ±89.9°：不抛异常、信封结构齐全；
* 地平线以下：``matched=False`` 且原因是人话（不含 NaN/Inf 字样）；
* NaN/Inf 输入：以实际实现为准，返回 ``matched=False`` + 原因；
* FOV 0.5°/180° 边界：不抛异常，窄视场内角距按 fov/2 收口；
* ``limit`` 生效；命中星不伪造 ``HIP -1`` 编号。

全部使用固定 jd/lat/lon，不读写用户数据、不起 Qt。
"""

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.sky_pointing import identify_direction  # noqa: E402

_JD = 2461359.5  # 2026-11-16 00:00 UT1，与 test_sky_pointing.py 同一固定历元
_LAT = 45.0
_LON = 120.0

_STAR_KEYS = {
    "name", "hip", "con", "vmag", "angular_distance_deg",
    "az_deg", "alt_deg", "asterisms",
}


def _catalog_azalt(index):
    """星表第 index 行的 J2000 RA/Dec → 固定时刻/站点的地平 az/alt。"""
    from mbdsdr_ai.celestial_geometry import (
        GroundStation, azalt_from_vec, j2000_to_altaz, vec_from_radec)
    from mbdsdr_ai.skyengine.star_catalog import get_catalog

    cat = get_catalog()
    v = vec_from_radec(float(cat.ra_deg[index]), float(cat.dec_deg[index]))
    az, alt = azalt_from_vec(j2000_to_altaz(
        v, GroundStation(_LON, _LAT, 0.0, "test"), _JD))
    return float(az), float(alt)


def _cross_zero_named_star():
    """找一颗 az 在 355°–5°、alt>10° 且有专名的亮星（按离 0° 最近取）。"""
    from mbdsdr_ai.skyengine.star_catalog import get_catalog

    cat = get_catalog()
    best = None
    for i in range(min(cat.count, 2000)):
        name = str(cat.name[i]).strip()
        if not name or float(cat.vmag[i]) > 5.0:
            continue
        az, alt = _catalog_azalt(i)
        gap = min(az, 360.0 - az)
        if gap <= 5.0 and alt > 10.0 and (best is None or gap < best[0]):
            best = (gap, name, az, alt, int(cat.hip[i]))
    return best


def _bright_named_star():
    """按星等从亮到暗找一颗 alt>30° 且有专名的星（窄视场测试锚点）。"""
    from mbdsdr_ai.skyengine.star_catalog import get_catalog

    cat = get_catalog()
    for i in np.argsort(cat.vmag)[:400]:
        name = str(cat.name[i]).strip()
        if not name:
            continue
        az, alt = _catalog_azalt(int(i))
        if alt > 30.0:
            return name, az, alt, int(cat.hip[i])
    pytest.skip("固定时刻无可见亮星，环境星表异常")


def test_identify_direction_hits_star_across_azimuth_zero():
    found = _cross_zero_named_star()
    if found is None:
        pytest.skip("固定时刻无 az 跨 0° 的亮星，无法构造该边界")
    _gap, name, az, alt, _hip = found

    out = identify_direction(az, alt, 2.0, _JD, _LAT, _LON, limit=10)
    assert out["matched"] is True
    names = [star["name"] for star in out["stars"]]
    assert name in names, (
        f"方位 {az:.3f}° 附近未命中星表星 {name}，"
        "疑似视锥判定做了线性截断")
    hit = next(star for star in out["stars"] if star["name"] == name)
    assert hit["angular_distance_deg"] < 0.01


def test_azimuth_zero_and_360_are_equivalent():
    at_zero = identify_direction(0.0, 45.0, 30.0, _JD, _LAT, _LON, limit=5)
    at_360 = identify_direction(360.0, 45.0, 30.0, _JD, _LAT, _LON, limit=5)

    assert at_zero["matched"] is True
    assert at_360["matched"] is True
    assert [(s["name"], s["hip"], s["vmag"]) for s in at_zero["stars"]] == \
        [(s["name"], s["hip"], s["vmag"]) for s in at_360["stars"]]
    assert at_zero["total_matched"] == at_360["total_matched"]


@pytest.mark.parametrize("lat_deg", [89.9, -89.9])
def test_extreme_latitude_returns_well_formed_result(lat_deg):
    out = identify_direction(0.0, 45.0, 30.0, _JD, lat_deg, _LON, limit=3)

    assert isinstance(out["matched"], bool)
    assert isinstance(out["stars"], list)
    if out["matched"]:
        assert out["stars"], "命中分支必须给出至少一颗星"
        for star in out["stars"]:
            assert _STAR_KEYS.issubset(star.keys())
            assert math.isfinite(star["az_deg"])
            assert math.isfinite(star["alt_deg"])
    else:
        # 该天区确实无星可以接受，但不能只给一个空原因。
        assert out["stars"] == []
        assert isinstance(out["reason"], str) and out["reason"].strip()


def test_below_horizon_is_no_match_with_human_reason():
    out = identify_direction(180.0, -30.0, 5.0, _JD, _LAT, _LON)

    assert out["matched"] is False
    assert out["stars"] == []
    assert isinstance(out["reason"], str) and out["reason"].strip()
    lowered = out["reason"].lower()
    assert "nan" not in lowered
    assert "inf" not in lowered


@pytest.mark.parametrize("az_deg,alt_deg,fov_deg", [
    (float("nan"), 20.0, 5.0),
    (10.0, float("inf"), 5.0),
    (10.0, 20.0, float("nan")),
])
def test_non_finite_inputs_do_not_raise(az_deg, alt_deg, fov_deg):
    # 实际实现不抛异常，而是返回 matched=False + 具体原因；按真实行为钉住。
    out = identify_direction(az_deg, alt_deg, fov_deg, _JD, _LAT, _LON)
    assert out["matched"] is False
    assert out["stars"] == []
    assert isinstance(out["reason"], str) and out["reason"].strip()


def test_narrow_fov_keeps_hits_inside_half_fov_radius():
    # 实现口径：fov_deg 是视场直径，命中半径 = fov/2 = 0.25°。
    name, az, alt, hip = _bright_named_star()
    out = identify_direction(az, alt, 0.5, _JD, _LAT, _LON, limit=5)

    assert out["matched"] is True
    names = [star["name"] for star in out["stars"]]
    assert name in names

    from mbdsdr_ai.celestial_geometry import (
        GroundStation, azalt_from_vec, j2000_to_altaz, vec_from_radec)
    from mbdsdr_ai.sky_interaction import angular_distance_deg
    from mbdsdr_ai.skyengine.star_catalog import get_catalog

    cat = get_catalog()
    hit_row = int(np.nonzero(cat.hip == hip)[0][0])
    star_az, star_alt = _catalog_azalt(hit_row)
    dist = angular_distance_deg(az, alt, star_az, star_alt)
    assert dist <= 0.25 + 1e-6, (
        f"{name} 角距 {dist}° 超出 0.5° 视场的半角 0.25°")


def test_full_sky_fov_accepted_without_error():
    out = identify_direction(180.0, 45.0, 180.0, _JD, _LAT, _LON, limit=3)

    assert isinstance(out["matched"], bool)
    assert isinstance(out["stars"], list)
    if out["matched"]:
        for star in out["stars"]:
            assert _STAR_KEYS.issubset(star.keys())
    else:
        assert out["reason"].strip()


def test_limit_one_truncates_star_list():
    out = identify_direction(180.0, 60.0, 120.0, _JD, _LAT, _LON, limit=1)

    assert out["matched"] is True
    assert len(out["stars"]) <= 1
    assert len(out["stars"]) == 1
    assert out["total_matched"] >= 1
    assert out["truncated"] is True


def test_identify_direction_never_fabricates_hip_id():
    out = identify_direction(180.0, 60.0, 120.0, _JD, _LAT, _LON, limit=200)

    assert out["matched"] is True
    for star in out["stars"]:
        assert star["name"].strip()
        assert star["name"] != "HIP -1"
        if star["name"].startswith("HIP "):
            assert star["hip"] > 0
