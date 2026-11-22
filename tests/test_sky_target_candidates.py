# SPDX-License-Identifier: MIT
"""Pure-computation candidate adapter tests; fixed UTC and neutral example site."""
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from desktop.sky_target_candidates import build_target_results
from mbdsdr_ai.astronomy import az_to_compass, dec_to_dms, unix_to_jd
from mbdsdr_ai.astronomy import dso, planets
from mbdsdr_ai.celestial_geometry import (
    GroundStation, altaz_to_j2000, azalt_from_vec, j2000_to_altaz,
    radec_from_vec, vec_from_azalt, vec_from_radec,
)


UTC = datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc)
JD = unix_to_jd(UTC.timestamp())
CAVEAT = "所选时刻的计算候选；非整夜预报，天气、遮挡和器材条件未评估"


def test_real_backend_candidates_match_render_geometry():
    """Real recommendations and catalog, not a mocked candidate list."""
    result = build_target_results(JD, 0.0, 0.0)
    assert not result.errors
    assert result.caveat == CAVEAT
    station = GroundStation(longitude_deg=0.0, latitude_deg=0.0)
    expected = []
    sources = (
        ("planet", planets.recommend_targets(JD, 0.0, observer_lon_deg=0.0), 15.0),
        ("dso", dso.recommend_dso(JD, 0.0, observer_lon_deg=0.0), 20.0),
    )
    for kind, entries, threshold in sources:
        for entry in entries:
            az, alt = azalt_from_vec(j2000_to_altaz(
                vec_from_radec(entry.ra_deg, entry.dec_deg), station, JD))
            if alt >= threshold:
                expected.append((kind, entry, az, alt))
    assert {kind for kind, *_ in expected[:12]} == {"planet", "dso"}
    assert len(result.rows) == min(12, len(expected))
    for row, (kind, entry, az, alt) in zip(result.rows, expected[:12]):
        assert row["kind"] == kind
        assert row["key"] == entry.name
        assert row["ra"] == entry.ra_deg
        assert row["dec"] == entry.dec_deg
        assert row["magnitude"] == entry.magnitude
        assert row["az_deg"] == pytest.approx(az, abs=1e-10)
        assert row["alt_deg"] == pytest.approx(alt, abs=1e-10)
        assert row["sub"] == f"{az_to_compass(az)} · 高度{dec_to_dms(alt)}（几何）"
        assert row["key"] != "sun"
    print(f"UTC={UTC.isoformat()}, example site=(0, 0)")
    for row in result.rows:
        print(row)


@pytest.mark.parametrize(("jd", "lat", "lon"), [
    (None, 0.0, 0.0), (float("nan"), 0.0, 0.0),
    (float("inf"), 0.0, 0.0), (-float("inf"), 0.0, 0.0),
    (JD, None, 0.0), (JD, 0.0, None), (JD, None, None),
    (JD, float("nan"), 0.0), (JD, 0.0, float("nan")),
    (JD, float("inf"), 0.0), (JD, 0.0, -float("inf")),
    (JD, 90.01, 0.0), (JD, -90.01, 0.0),
    (JD, 0.0, 180.01), (JD, 0.0, -180.01),
    (True, 0.0, 0.0), (JD, False, 0.0), (JD, 0.0, "0"),
    (JD, [0.0], 0.0), (10**400, 0.0, 0.0),
])
def test_invalid_time_or_missing_site_reports_error_without_source_calls(
    monkeypatch, jd, lat, lon,
):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid input must not call astronomy sources")

    monkeypatch.setattr(planets, "recommend_targets", forbidden)
    monkeypatch.setattr(dso, "recommend_dso", forbidden)
    result = build_target_results(jd, lat, lon)
    assert result.rows == []
    assert result.errors
    assert result.caveat == CAVEAT


@pytest.mark.parametrize(("source", "method", "remaining", "error"), [
    (planets, "recommend_targets", "dso", RuntimeError("ephemeris unavailable")),
    (dso, "recommend_dso", "planet", OSError("catalog unavailable")),
    (dso, "recommend_dso", "planet", dso.DSOIntegrityError("catalog integrity")),
])
def test_source_failure_keeps_other_candidates_and_diagnostic(
    monkeypatch, source, method, remaining, error,
):
    healthy = build_target_results(JD, 0.0, 0.0, limit=1000)

    def unavailable(*args, **kwargs):
        raise error

    monkeypatch.setattr(source, method, unavailable)
    result = build_target_results(JD, 0.0, 0.0, limit=1000)
    assert result.rows == [r for r in healthy.rows if r["kind"] == remaining]
    assert result.rows
    assert len(result.errors) == 1
    assert type(error).__name__ in result.errors[0]
    assert str(error) in result.errors[0]
    assert result.caveat == CAVEAT


@pytest.mark.parametrize("limit", [-1, 1.5, True, None, "12", float("inf")])
def test_invalid_limit_is_an_error_not_silent_slicing(monkeypatch, limit):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid limit must not call astronomy sources")

    monkeypatch.setattr(planets, "recommend_targets", forbidden)
    monkeypatch.setattr(dso, "recommend_dso", forbidden)
    result = build_target_results(JD, 0.0, 0.0, limit=limit)
    assert result.rows == []
    assert result.errors
    assert "limit" in result.errors[0]


@pytest.mark.parametrize("lon", [-90.0, 90.0])
def test_longitude_reaches_both_real_backends_not_greenwich_default(monkeypatch, lon):
    # Synthetic longitude shifts, not a saved or personal observing location.
    station = GroundStation(longitude_deg=lon, latitude_deg=0.0)
    originals = (planets.recommend_targets, dso.recommend_dso)
    expected = []
    for kind, recommend, threshold in zip(("planet", "dso"), originals, (15.0, 20.0)):
        shifted = recommend(JD, 0.0, observer_lon_deg=lon)
        greenwich = recommend(JD, 0.0)
        assert {e.name for e in shifted} != {e.name for e in greenwich}
        for entry in shifted:
            az, alt = azalt_from_vec(j2000_to_altaz(
                vec_from_radec(entry.ra_deg, entry.dec_deg), station, JD))
            if alt >= threshold:
                expected.append((kind, entry.name, az, alt))
    calls = []

    def record(recommend):
        def call(jd, lat, **kwargs):
            calls.append((jd, lat, kwargs))
            return recommend(jd, lat, **kwargs)
        return call

    monkeypatch.setattr(planets, "recommend_targets", record(originals[0]))
    monkeypatch.setattr(dso, "recommend_dso", record(originals[1]))
    result = build_target_results(JD, 0.0, lon, limit=1000)
    assert not result.errors
    # No overriding backend altitude, brightness, or sun-distance defaults.
    assert calls == [(JD, 0.0, {"observer_lon_deg": lon})] * 2
    assert expected
    assert [(r["kind"], r["key"]) for r in result.rows] == [e[:2] for e in expected]
    for row, (_, _, az, alt) in zip(result.rows, expected):
        assert row["az_deg"] == pytest.approx(az, abs=1e-10)
        assert row["alt_deg"] == pytest.approx(alt, abs=1e-10)


@pytest.mark.parametrize("limit", [0, 1, 2, 3, 12, 1000])
def test_limit_is_total_and_does_not_fabricate_or_reorder_rows(limit):
    full = build_target_results(JD, 0.0, 0.0, limit=1000)
    result = build_target_results(JD, 0.0, 0.0, limit=limit)
    assert not result.errors
    assert result.rows == full.rows[:limit]
    kinds = [row["kind"] for row in result.rows]
    assert kinds == sorted(kinds, key=lambda kind: kind != "planet")


@pytest.mark.parametrize(("lat", "lon"), [(90.0, 180.0), (-90.0, -180.0)])
def test_inclusive_site_boundaries_are_legal(lat, lon):
    result = build_target_results(JD, lat, lon)
    assert not result.errors


def test_complete_source_failure_is_not_reported_as_no_matches(monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("source unavailable")

    monkeypatch.setattr(planets, "recommend_targets", unavailable)
    monkeypatch.setattr(dso, "recommend_dso", unavailable)
    failed = build_target_results(JD, 0.0, 0.0)
    assert failed.rows == []
    assert len(failed.errors) == 2
    assert "行星/月球" in failed.errors[0]
    assert "深空天体" in failed.errors[1]

    monkeypatch.setattr(planets, "recommend_targets", lambda *a, **k: [])
    monkeypatch.setattr(dso, "recommend_dso", lambda *a, **k: [])
    empty = build_target_results(JD, 0.0, 0.0)
    assert empty.rows == []
    assert empty.errors == ()
    assert empty.caveat == failed.caveat == CAVEAT


def test_limit_does_not_hide_failure_in_later_source(monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("catalog unavailable")

    monkeypatch.setattr(dso, "recommend_dso", unavailable)
    result = build_target_results(JD, 0.0, 0.0, limit=1)
    assert len(result.rows) == 1
    assert result.rows[0]["kind"] == "planet"
    assert result.errors


@pytest.mark.parametrize("error_type", [TypeError, AttributeError, KeyError, AssertionError])
def test_source_programming_errors_are_not_swallowed(monkeypatch, error_type):
    def broken(*args, **kwargs):
        raise error_type("programming error")

    monkeypatch.setattr(planets, "recommend_targets", broken)
    with pytest.raises(error_type, match="programming error"):
        build_target_results(JD, 0.0, 0.0)


def test_source_fields_labels_and_unknown_magnitude_are_preserved(monkeypatch):
    planet = planets.recommend_targets(JD, 0.0, observer_lon_deg=0.0)[0]
    entry = dso.recommend_dso(JD, 0.0, observer_lon_deg=0.0)[0]
    moon = replace(planet, name="moon", magnitude=None)
    unnamed = replace(entry, name="example-dso", zh_name="", magnitude=None,
                      name_en="Not the requested label", dso_type="nebula",
                      constellation="Ori")
    source_planets = [moon, planet]
    source_dsos = [unnamed, entry]
    labels = {planet.name: "调用方标签", "not-returned": "不得凭空补入"}
    before = dict(labels)
    monkeypatch.setattr(planets, "recommend_targets", lambda *a, **k: source_planets)
    monkeypatch.setattr(dso, "recommend_dso", lambda *a, **k: source_dsos)
    result = build_target_results(JD, 0.0, 0.0, planet_labels=labels)
    assert not result.errors
    assert [r["key"] for r in result.rows] == [moon.name, planet.name, unnamed.name, entry.name]
    for row, value in zip(result.rows, source_planets + source_dsos):
        assert row["ra"] == value.ra_deg
        assert row["dec"] == value.dec_deg
        assert row["magnitude"] == value.magnitude
    assert result.rows[0]["label"] == result.rows[0]["zh"] == "moon"
    assert result.rows[0]["magnitude"] is None
    assert result.rows[1]["label"] == result.rows[1]["zh"] == labels[planet.name]
    assert result.rows[1]["kind_label"] == "行星"
    assert result.rows[2]["label"] == unnamed.name
    assert result.rows[2]["magnitude"] is None
    assert result.rows[2]["dso_type"] == "nebula"
    assert result.rows[2]["con"] == "Ori"
    assert result.rows[3]["label"] == entry.zh_name
    assert result.rows[3]["kind_label"] == "深空天体"
    assert result.rows[3]["dso_type"] == entry.dso_type
    assert result.rows[3]["con"] == entry.constellation
    assert labels == before
    assert moon.magnitude is None and unnamed.magnitude is None


@pytest.mark.parametrize(("kind", "threshold"), [("planet", 15.0), ("dso", 20.0)])
def test_render_altitude_gate_filters_before_limit(monkeypatch, kind, threshold):
    # Controlled source candidates use the inverse of the same kernel, not
    # another spherical formula. Even a source-returned target is not trusted
    # to clear the renderer's altitude threshold (precession changes the frame).
    station = GroundStation(longitude_deg=0.0, latitude_deg=0.0)
    planet = planets.compute_planet("jupiter", JD)
    entry = dso.BUILTIN_CATALOG.get("M44")
    entries = []
    for index, altitude in enumerate((-10.0, threshold - 0.01, threshold + 0.01)):
        ra, dec = radec_from_vec(altaz_to_j2000(
            vec_from_azalt(45.0, altitude), station, JD))
        if kind == "planet":
            entries.append(replace(planet, name=f"test-{index}", ra_deg=ra, dec_deg=dec))
        else:
            entries.append(replace(entry, name=f"test-{index}", ra_hours=ra / 15.0, dec_deg=dec))
    monkeypatch.setattr(planets, "recommend_targets",
                        lambda *a, **k: entries if kind == "planet" else [])
    monkeypatch.setattr(dso, "recommend_dso",
                        lambda *a, **k: entries if kind == "dso" else [])
    result = build_target_results(JD, 0.0, 0.0, limit=1)
    assert not result.errors
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row["key"] == "test-2"
    assert row["alt_deg"] == pytest.approx(threshold + 0.01, abs=1e-10)
    assert row["az_deg"] == pytest.approx(45.0, abs=1e-10)
    assert row["sub"].startswith("东北 (45.0°) · 高度")


def test_row_programming_error_is_not_disguised_as_source_unavailable(monkeypatch):
    monkeypatch.setattr(planets, "recommend_targets", lambda *a, **k: [object()])
    with pytest.raises(AttributeError):
        build_target_results(JD, 0.0, 0.0)


def test_backend_initialization_failure_is_an_explicit_result(monkeypatch):
    # astronomy/__init__.py eagerly loads the DSO catalog. Its missing-file
    # error must not prevent importing this adapter or masquerade as no matches.
    import builtins
    import importlib
    from desktop import sky_target_candidates

    original = builtins.__import__

    def missing_catalog(name, *args, **kwargs):
        if name == "mbdsdr_ai.astronomy":
            raise FileNotFoundError("catalog unavailable at initialization")
        return original(name, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "__import__", missing_catalog)
        module = importlib.reload(sky_target_candidates)
        result = module.build_target_results(JD, 0.0, 0.0)
        assert result.rows == []
        assert result.errors
        assert "初始化失败" in result.errors[0]
        assert "FileNotFoundError" in result.errors[0]
        assert result.caveat == CAVEAT


@pytest.mark.parametrize(("field", "value"), [
    ("ra_deg", float("nan")), ("ra_deg", float("inf")),
    ("dec_deg", float("nan")), ("dec_deg", -float("inf")),
])
def test_invalid_source_coordinates_do_not_become_display_rows(monkeypatch, field, value):
    good = planets.recommend_targets(JD, 0.0, observer_lon_deg=0.0)[0]
    bad = replace(good, name="invalid-coordinates", **{field: value})
    monkeypatch.setattr(planets, "recommend_targets", lambda *a, **k: [bad, good])
    result = build_target_results(JD, 0.0, 0.0)
    assert result.rows[0]["key"] == good.name
    assert all(r["key"] != bad.name for r in result.rows)
    assert len(result.errors) == 1
    assert bad.name in result.errors[0]


def test_clean_process_uses_no_ui_network_or_file_writes():
    import subprocess
    import sys
    from pathlib import Path

    code = r'''
import builtins
import os
import sys

original = builtins.__import__
blocked = {"PySide6", "PyQt5", "PyQt6", "rf_sky_view", "main_window",
           "time_machine", "settings", "rtlsdr"}

def pure_import(name, *args, **kwargs):
    if blocked.intersection(name.split(".")):
        raise AssertionError("Unexpected UI/device import: " + name)
    return original(name, *args, **kwargs)

def pure_io(event, args):
    if event in {"socket.connect", "socket.bind", "socket.getaddrinfo"}:
        raise AssertionError("Unexpected network access: " + event)
    if event == "open":
        flags = args[2]
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            raise AssertionError("Unexpected file write")

builtins.__import__ = pure_import
sys.addaudithook(pure_io)
from desktop.sky_target_candidates import build_target_results
result = build_target_results(2461055.5, 0.0, 0.0)
assert result.rows and not result.errors, result.errors
assert not any(blocked.intersection(name.split(".")) for name in sys.modules)
print("Pure import and real calculation passed: no UI, network, or file writes")
'''
    completed = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        text=True, encoding="utf-8", capture_output=True, timeout=20,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    print(completed.stdout.strip())
