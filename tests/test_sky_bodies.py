# SPDX-License-Identifier: MIT
"""确定性单测：行星/月球目标识别（``mbdsdr_ai.sky_bodies.identify_bodies``）。

固定历元 2026-09-27 22:00 UTC（与 ``tests/test_planets.py`` 同一日期，
取木星在地平以上的时刻，保证断言可复现），观测站 lat 40 / lon 116。

运行:
    python -m pytest tests/test_sky_bodies.py -q
"""

import math
from datetime import datetime, timezone

import pytest

from mbdsdr_ai.astronomy import planets as P
from mbdsdr_ai.celestial_geometry import (
    GroundStation, azalt_from_vec, j2000_to_altaz, vec_from_radec)
from mbdsdr_ai.sky_bodies import NAME_ZH, identify_bodies

# 固定历元: 2026-09-27 22:00 UTC（该时刻木星 alt≈41°，金星在地平以下）
FIXED_UNIX = datetime(2026, 9, 27, 22, 0, tzinfo=timezone.utc).timestamp()
FIXED_JD = 2440587.5 + FIXED_UNIX / 86400.0
LAT, LON = 40.0, 116.0

_FIELDS = {
    "name", "name_zh", "kind", "ra_deg", "dec_deg", "az_deg", "alt_deg",
    "magnitude", "angular_diameter_arcsec", "distance_au",
    "angular_distance_deg",
}


def _altaz(ra_deg: float, dec_deg: float):
    gs = GroundStation(LON, LAT, 0.0, "test")
    az, alt = azalt_from_vec(
        j2000_to_altaz(vec_from_radec(ra_deg, dec_deg), gs, FIXED_JD))
    return float(az), float(alt)


def _visible(names, min_alt: float = 2.0):
    """按给定顺序取第一颗地平高度足够的星，返回 (name, pos, az, alt)。"""
    pos_all = P.compute_all(FIXED_JD)
    for name in names:
        pos = pos_all[name]
        az, alt = _altaz(pos.ra_deg, pos.dec_deg)
        if alt > min_alt:
            return name, pos, az, alt
    return None


def test_pointing_hits_jupiter_or_venus():
    got = _visible(("jupiter", "venus"))
    if got is None:
        pytest.skip("固定历元下木星与金星均在地平以下")
    name, _pos, az, alt = got
    out = identify_bodies(az, alt, 5.0, FIXED_JD, LAT, LON)

    assert out["matched"] is True
    bodies = {b["name"]: b for b in out["bodies"]}
    assert name in bodies
    body = bodies[name]
    assert body["name_zh"] == NAME_ZH[name]
    assert body["kind"] == "planet"
    assert body["angular_distance_deg"] < 0.01


def test_pointing_hits_moon_and_labels_kind():
    moon = P.compute_all(FIXED_JD)["moon"]
    az, alt = _altaz(moon.ra_deg, moon.dec_deg)
    if alt <= 0.0:
        pytest.skip("固定历元下月球在地平以下")
    out = identify_bodies(az, alt, 5.0, FIXED_JD, LAT, LON)

    bodies = {b["name"]: b for b in out["bodies"]}
    assert "moon" in bodies
    assert bodies["moon"]["name_zh"] == "月球"
    assert bodies["moon"]["kind"] == "moon"


def test_opposite_direction_no_match():
    got = _visible(("jupiter", "venus", "saturn", "moon"))
    if got is None:
        pytest.skip("固定历元下没有地平以上的目标")
    _name, _pos, az, alt = got
    out = identify_bodies((az + 180.0) % 360.0, alt, 2.0,
                          FIXED_JD, LAT, LON)

    assert out["matched"] is False
    assert out["bodies"] == []
    assert out["reason"].strip()


def test_body_below_horizon_is_excluded():
    pos_all = P.compute_all(FIXED_JD)
    target = None
    for name, pos in pos_all.items():
        az, alt = _altaz(pos.ra_deg, pos.dec_deg)
        if alt < 0.0:
            target = (name, az, alt)
            break
    if target is None:
        pytest.skip("固定历元下所有天体都在地平以上，无法验证地平线过滤")
    name, az, alt = target
    out = identify_bodies(az, alt, 5.0, FIXED_JD, LAT, LON)

    assert name not in [b["name"] for b in out["bodies"]]


def test_invalid_non_finite_inputs_rejected():
    for out in (
        identify_bodies(float("nan"), 20.0, 5.0, FIXED_JD, LAT, LON),
        identify_bodies(10.0, float("inf"), 5.0, FIXED_JD, LAT, LON),
        identify_bodies(10.0, 20.0, float("inf"), FIXED_JD, LAT, LON),
        identify_bodies(10.0, 20.0, 5.0, float("nan"), LAT, LON),
        identify_bodies(10.0, 20.0, 5.0, FIXED_JD, float("nan"), LON),
        identify_bodies(10.0, 20.0, 5.0, FIXED_JD, LAT, float("inf")),
    ):
        assert out["matched"] is False
        assert out["bodies"] == []
        assert out["reason"].strip()


def test_invalid_alt_out_of_range():
    for alt in (-90.1, 90.1, 120.0):
        out = identify_bodies(10.0, alt, 5.0, FIXED_JD, LAT, LON)
        assert out["matched"] is False
        assert "alt_deg" in out["reason"]


def test_invalid_fov_out_of_range():
    for fov in (0.0, -1.0, 181.0):
        out = identify_bodies(10.0, 20.0, fov, FIXED_JD, LAT, LON)
        assert out["matched"] is False
        assert "fov_deg" in out["reason"]


def test_invalid_limit():
    for limit in (0, -3):
        out = identify_bodies(10.0, 20.0, 5.0, FIXED_JD, LAT, LON,
                              limit=limit)
        assert out["matched"] is False
        assert "limit" in out["reason"]


def test_wide_fov_sorted_and_truncation_consistent():
    # 指向天顶 + fov=180 -> 覆盖整个地平以上半球
    out = identify_bodies(0.0, 90.0, 180.0, FIXED_JD, LAT, LON, limit=2)

    assert out["matched"] is True
    mags = [b["magnitude"] for b in out["bodies"]]
    assert mags == sorted(mags)
    assert out["total_matched"] >= len(out["bodies"])
    assert out["truncated"] is (out["total_matched"] > 2)

    full = identify_bodies(0.0, 90.0, 180.0, FIXED_JD, LAT, LON, limit=99)
    assert full["total_matched"] == out["total_matched"]
    assert full["truncated"] is False
    assert len(full["bodies"]) == full["total_matched"]


def test_body_fields_complete():
    out = identify_bodies(0.0, 90.0, 180.0, FIXED_JD, LAT, LON, limit=99)
    assert out["bodies"], "固定历元下天顶半球应至少有一个目标"
    for body in out["bodies"]:
        assert set(body) == _FIELDS
        assert isinstance(body["name"], str) and body["name"]
        assert isinstance(body["name_zh"], str) and body["name_zh"]
        assert body["kind"] in ("planet", "moon")
        assert math.isfinite(body["angular_distance_deg"])
        assert 0.0 <= body["angular_distance_deg"] <= 90.0


def test_name_zh_mapping_covers_all_bodies():
    expected = {
        "mercury": "水星", "venus": "金星", "mars": "火星",
        "jupiter": "木星", "saturn": "土星", "uranus": "天王星",
        "neptune": "海王星", "moon": "月球",
    }
    for name in P.compute_all(FIXED_JD):
        assert name in NAME_ZH, f"缺少 {name} 的中文名映射"
    assert NAME_ZH == expected
