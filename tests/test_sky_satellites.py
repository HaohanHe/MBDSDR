# SPDX-License-Identifier: MIT
"""确定性单测：卫星目标识别（``mbdsdr_ai.sky_satellites.identify_satellites``）。

全部用例注入 fake ``visible_fn``，不联网、不读 TLE 缓存、不依赖 sgp4。
固定指向与时刻只用于让断言可复现。

运行:
    python -m pytest tests/test_sky_satellites.py -q
"""

import math

from mbdsdr_ai.sky_satellites import identify_satellites

WHEN = 1790000000.0
LAT, LON = 40.0, 116.0
AZ, ALT = 120.0, 30.0
FOV = 10.0

_FIELDS = {
    "name", "kind", "az_deg", "alt_deg", "angular_distance_deg",
    "range_km", "range_rate_kms", "altitude_km", "sunlit",
}


def _sat(name: str, az: float, alt: float, **extra):
    state = {
        "satellite": name,
        "azimuth": az,
        "elevation": alt,
        "range_km": 550.0,
        "range_rate_kms": -1.25,
        "altitude_km": 420.0,
        "sunlit": True,
    }
    state.update(extra)
    return state


def _run(states, **kwargs):
    calls = []

    def fake(*args, **call_kwargs):
        calls.append((args, call_kwargs))
        return states

    out = identify_satellites(AZ, ALT, FOV, WHEN, LAT, LON,
                              visible_fn=fake, **kwargs)
    return out, calls


def test_satellite_at_pointing_matches_with_complete_fields():
    out, _calls = _run([_sat("ISS (ZARYA)", AZ, ALT)])

    assert out["matched"] is True
    assert out["total_matched"] == 1
    assert out["truncated"] is False
    sat = out["satellites"][0]
    assert set(sat) == _FIELDS
    assert sat["name"] == "ISS (ZARYA)"
    assert sat["kind"] == "satellite"
    assert sat["angular_distance_deg"] < 0.01
    assert sat["az_deg"] == AZ
    assert sat["alt_deg"] == ALT
    assert math.isfinite(sat["range_km"])
    assert math.isfinite(sat["range_rate_kms"])
    assert math.isfinite(sat["altitude_km"])
    assert sat["sunlit"] is True
    assert out["reason"] == ""


def test_satellite_outside_fov_is_not_matched():
    out, _calls = _run([_sat("FAR", (AZ + 180.0) % 360.0, ALT)])

    assert out["matched"] is False
    assert out["satellites"] == []
    assert out["total_matched"] == 0
    assert out["truncated"] is False
    assert out["reason"].strip()


def test_multiple_satellites_sorted_and_truncated():
    states = [
        _sat("C", AZ + 3.0, ALT),
        _sat("A", AZ, ALT),
        _sat("B", AZ + 1.0, ALT),
    ]
    out, _calls = _run(states, limit=2)

    assert out["matched"] is True
    assert out["total_matched"] == 3
    assert out["truncated"] is True
    assert [s["name"] for s in out["satellites"]] == ["A", "B"]
    dists = [s["angular_distance_deg"] for s in out["satellites"]]
    assert dists == sorted(dists)

    full, _calls = _run(states, limit=9)
    assert full["truncated"] is False
    assert [s["name"] for s in full["satellites"]] == ["A", "B", "C"]


def test_observer_alt_is_converted_from_meters_to_km():
    _out, calls = _run([], observer_alt_m=200.0)

    assert calls, "fake visible_fn 应被调用"
    _args, kwargs = calls[0]
    assert kwargs["observer_alt"] == 0.2
    assert kwargs["min_elevation"] == 0.0
    assert kwargs["when"] == WHEN

    _out, calls = _run([])
    assert calls[0][1]["observer_alt"] == 0.0


def test_below_horizon_satellite_is_excluded():
    out, _calls = _run([_sat("SET", AZ, 0.0), _sat("DOWN", AZ, -5.0)])

    assert out["matched"] is False
    assert out["satellites"] == []
    assert out["reason"] == "视场内没有可见卫星"


def test_invalid_inputs_rejected_without_calling_visible_fn():
    def boom(*_args, **_kwargs):
        raise AssertionError("非法输入不应触发位置计算")

    cases = [
        (float("nan"), ALT, FOV, WHEN, 3),
        (AZ, float("inf"), FOV, WHEN, 3),
        (AZ, ALT, 0.0, WHEN, 3),
        (AZ, ALT, 181.0, WHEN, 3),
        (AZ, ALT, FOV, float("nan"), 3),
        (AZ, ALT, FOV, WHEN, 0),
        (AZ, ALT, FOV, WHEN, -2),
    ]
    for az, alt, fov, when, limit in cases:
        out = identify_satellites(az, alt, fov, when, LAT, LON,
                                  limit=limit, visible_fn=boom)
        assert out["matched"] is False
        assert out["satellites"] == []
        assert out["reason"].strip()


def test_visible_fn_exception_is_reported_not_swallowed():
    def boom(*_args, **_kwargs):
        raise RuntimeError("TLE cache exploded")

    out = identify_satellites(AZ, ALT, FOV, WHEN, LAT, LON, visible_fn=boom)

    assert out["matched"] is False
    assert out["satellites"] == []
    assert out["total_matched"] == 0
    assert "卫星位置计算失败" in out["reason"]
    assert "RuntimeError" in out["reason"]
    assert "TLE cache exploded" in out["reason"]


def test_empty_visible_list_reports_honest_reason():
    out, calls = _run([])

    assert out["matched"] is False
    assert out["satellites"] == []
    assert out["truncated"] is False
    assert out["reason"] == "视场内没有可见卫星"
    assert calls, "即使结果为空也应真的查询过位置"
