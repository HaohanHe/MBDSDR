# SPDX-License-Identifier: MIT
"""Real Qt regressions for selected-body time/site context and hidden work.

Only the renderer's output and real calculation calls are observed; no ephemeris
or computation is replaced. All dates are UTC and sites are neutral fixtures.
"""
from datetime import datetime, timezone
from functools import wraps
import json
import math
import os
from pathlib import Path

import pytest


UTC0 = datetime(2026, 11, 19, 18, 0, tzinfo=timezone.utc).timestamp()
DAY_S = 86400.0


@pytest.fixture(scope="module")
def qapp(isolated_profile):
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    original_font = app.font()
    font_ids = []
    for filename in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/segoeui.ttf"):
        if Path(filename).is_file():
            font_ids.append(QFontDatabase.addApplicationFont(filename))
    app.setFont(QFont("Microsoft YaHei" if os.name == "nt" else "Sans Serif", 10))
    yield app
    app.setFont(original_font)
    for font_id in font_ids:
        if font_id >= 0:
            QFontDatabase.removeApplicationFont(font_id)


@pytest.fixture
def engine(isolated_profile):
    from mbdsdr_ai.new_spacetime import get_time_engine

    eng = get_time_engine()
    old_jd, old_rate = eng.now_jd(), eng.get_rate()
    following_system = eng.is_following_system()
    eng.set_unix(UTC0)
    eng.pause()
    yield eng
    if following_system:
        eng.set_time_now()
    else:
        eng.set_jd(old_jd)
    eng.set_rate(old_rate)


@pytest.fixture
def sky(qapp, engine, request):
    from PySide6.QtCore import QEvent
    from rf_sky_view import RFSkyView, SatelliteTracker

    view = RFSkyView()
    view.resize(1100, 800)
    view.set_observer(0.0, 0.0, 0.0)
    # An explicitly empty owner catalog is a supported public input, not fake TLE.
    # Sun/Moon computation remains real; no disk catalog or network is needed.
    tracker = SatelliteTracker(view, 0.0, 0.0,
                               interval_ms=getattr(request, "param", 10**9),
                               catalog_source=lambda: [])
    view.show()
    qapp.processEvents()
    try:
        yield view, tracker
    finally:
        view.hide()
        tracker._timer.stop()
        view.close()
        view.deleteLater()
        qapp.sendPostedEvents(None, QEvent.DeferredDelete)
        qapp.processEvents()


@pytest.fixture
def painted_text(monkeypatch):
    from PySide6.QtGui import QPainter

    texts = []
    real_draw_text = QPainter.drawText

    @wraps(real_draw_text)
    def draw_text(painter, *args, **kwargs):
        texts.extend(arg for arg in args if isinstance(arg, str))
        return real_draw_text(painter, *args, **kwargs)

    monkeypatch.setattr(QPainter, "drawText", draw_text)
    return texts


def _render(qapp, view, texts, evidence_name=None):
    from PySide6.QtGui import QPixmap

    assert view.isVisible(), "exercise the normally visible Qt paint path"
    view.update()
    qapp.processEvents()
    texts.clear()
    pixmap = QPixmap(view.size())
    view.render(pixmap)
    assert view._info_card_rect is not None, "selected body card was not painted"
    assert texts, "real QPainter text observation did not run"
    output = os.environ.get("SKY_BODY_EVIDENCE_DIR")
    if output and evidence_name:
        directory = Path(output)
        assert directory.is_dir(), "evidence directory must already exist"
        assert pixmap.save(str(directory / (evidence_name + ".png")))
    return tuple(texts)


def _card_snapshot(view):
    # Observe the same values consumed by the actual card/highlight painter.
    return {key: view._body_info.get(key)
            for key in ("ra", "dec", "mag", "dist_text", "desc", "rise", "set")}


def _planet_expected(pos):
    from rf_sky_view import (format_angle_deg, format_arcsec,
                             format_distance_au, format_percent)

    desc = (f"视直径 {format_arcsec(pos.angular_diameter_arcsec)} · "
            f"相位角 {format_angle_deg(pos.phase_angle_deg)} · ")
    if pos.name == "moon":
        illum = 0.5 * (1.0 + math.cos(math.radians(pos.phase_angle_deg)))
        desc = f"照度 {format_percent(illum)} · " + desc
    return {"ra": pos.ra_deg, "dec": pos.dec_deg, "mag": pos.magnitude,
            "dist_text": format_distance_au(pos.distance_au), "desc_prefix": desc}


def _assert_planet_card(view, expected, texts):
    from rf_sky_view import format_dms_deg, format_hms_ra

    actual = _card_snapshot(view)
    problems = []
    for field in ("ra", "dec", "mag"):
        if actual[field] != pytest.approx(expected[field], rel=0.0, abs=1e-9):
            problems.append(f"{field}: card={actual[field]!r}, backend={expected[field]!r}")
    if actual["dist_text"] != expected["dist_text"]:
        problems.append(f"distance: card={actual['dist_text']!r}, backend={expected['dist_text']!r}")
    if not actual["desc"].startswith(expected["desc_prefix"]):
        problems.append(f"phase/diameter: card={actual['desc']!r}, backend={expected['desc_prefix']!r}")
    radec_text = f"{format_hms_ra(expected['ra'])}  {format_dms_deg(expected['dec'])}"
    if radec_text not in texts:
        problems.append(f"current RA/Dec missing from real card paint: {radec_text!r}")
    assert not problems, "selected card did not follow current ephemeris:\n" + "\n".join(problems)


@pytest.mark.parametrize("key", ["moon", "jupiter"])
def test_selected_planet_card_follows_public_one_day_jump(sky, engine, qapp, painted_text, key):
    from mbdsdr_ai.astronomy import planets

    view, tracker = sky
    view.on_body_picked({"kind": "planet", "key": key})
    picked = {field: view._picked_body[field] for field in ("kind", "key")}
    before_texts = _render(qapp, view, painted_text, key + "_before")
    old = _card_snapshot(view)
    _assert_planet_card(view, _planet_expected(planets.compute_all(engine.now_jd())[key]),
                        before_texts)

    engine.set_unix(UTC0 + DAY_S)
    assert engine.get_rate() == 0.0
    tracker.refresh()
    view.set_time_info(engine.state())
    after_texts = _render(qapp, view, painted_text, key + "_after_day")
    expected = _planet_expected(planets.compute_all(engine.now_jd())[key])
    assert abs(expected["ra"] - old["ra"]) > 1e-4, "fixture must expose stale coordinates"
    assert view._picked_body and all(view._picked_body[field] == value for field, value in picked.items()), \
        "time jump must not discard the selected object"
    print(json.dumps({"body": key, "utc": engine.now_utc().isoformat(),
                      "before": old, "after": _card_snapshot(view), "expected": expected},
                     ensure_ascii=False))
    _assert_planet_card(view, expected, after_texts)


def _rise_set_at(pos, jd, longitude):
    """Independent context, real geometry, existing 24h/10min/0deg contract."""
    from mbdsdr_ai.celestial_geometry import (
        GroundStation, azalt_from_vec, j2000_to_altaz, vec_from_radec,
    )

    station = GroundStation(longitude, 0.0, 0.0)
    vector = vec_from_radec(pos.ra_deg, pos.dec_deg)
    result = {"rise": None, "set": None}
    previous = None
    for step in range(145):
        sample_jd = jd + step * 600.0 / DAY_S
        _, altitude = azalt_from_vec(j2000_to_altaz(vector, station, sample_jd))
        above = altitude > 0.0
        if previous is False and above and result["rise"] is None:
            result["rise"] = sample_jd
        if previous is True and not above and result["set"] is None:
            result["set"] = sample_jd
        previous = above
    assert all(value is not None for value in result.values()), "equatorial fixture must cross horizon"
    return result


def test_selected_body_rise_set_follows_observer_longitude(sky, engine, qapp, painted_text):
    from mbdsdr_ai.astronomy import planets
    from rf_sky_view import format_utc_time

    view, tracker = sky
    view.on_body_picked({"kind": "planet", "key": "jupiter"})
    picked = {field: view._picked_body[field] for field in ("kind", "key")}
    _render(qapp, view, painted_text)
    before = {key: view._body_info[key] for key in ("rise", "set")}
    pos = planets.compute_all(engine.now_jd())["jupiter"]
    assert before == pytest.approx(_rise_set_at(pos, engine.now_jd(), 0.0), rel=0, abs=1e-9)

    view.set_observer(0.0, 90.0, 0.0)
    tracker.set_location(0.0, 90.0, 0.0)
    texts = _render(qapp, view, painted_text, "jupiter_longitude_90")
    expected = _rise_set_at(pos, engine.now_jd(), 90.0)
    actual = {key: view._body_info[key] for key in ("rise", "set")}
    assert view._picked_body and all(view._picked_body[field] == value for field, value in picked.items()), \
        "valid site change must preserve selected body"
    assert abs(expected["rise"] - before["rise"]) > 0.2, "fixture must expose stale site context"
    print(json.dumps({"longitude_before": 0.0, "longitude_after": 90.0,
                      "before": before, "after": actual, "expected": expected}))
    assert actual == pytest.approx(expected, rel=0, abs=1e-9), "rise/set still uses the previous site"
    for value in expected.values():
        shown = format_utc_time((value - 2440587.5) * DAY_S)
        assert any(shown in text for text in texts), f"current site event {shown} not painted"


def test_sun_without_coordinates_reports_unknown_rise_set(sky, qapp, painted_text):
    view, _ = sky
    view.set_celestial_bodies(None, None, 0.0)
    view.on_body_picked({"kind": "sun", "key": "sun"})
    texts = _render(qapp, view, painted_text, "sun_missing_coordinates")
    assert view._body_info["ra"] is None and view._body_info["dec"] is None
    label_index = next(i for i, text in enumerate(texts) if text.startswith("升起/落下"))
    status = texts[label_index + 1]
    print(f"sun missing coordinates: painted rise/set status={status!r}")
    assert "无升降" not in status and any(
        word in status for word in ("未知", "缺测", "不可计算", "无法")
    ), f"missing ephemeris is unknown, not an observed non-crossing: {status!r}"


def _spy_real(monkeypatch, owner, name):
    calls = []
    real = getattr(owner, name)

    @wraps(real)
    def observed(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(owner, name, observed)
    return calls


@pytest.mark.parametrize("sky", [80], indirect=True)
def test_hidden_location_update_does_not_restart_periodic_refresh(sky, qapp, monkeypatch):
    from PySide6.QtTest import QTest

    view, tracker = sky
    view.hide()
    qapp.processEvents()
    assert not tracker._timer.isActive(), "precondition: hide must stop the existing timer"
    calls = _spy_real(monkeypatch, tracker, "_compute_sun_moon")
    view.set_observer(0.0, 90.0, 0.0)
    tracker.set_location(0.0, 90.0, 0.0)
    active_after_update = tracker._timer.isActive()
    immediate_calls = len(calls)
    QTest.qWait(3 * 80 + 80)
    periodic_calls = len(calls) - immediate_calls
    print(f"hidden location update: timer_active={active_after_update}, "
          f"immediate_refreshes={immediate_calls}, periodic_refreshes={periodic_calls}")
    assert not active_after_update and periodic_calls == 0, (
        f"hidden site update restarted work: timer_active={active_after_update}, "
        f"periodic_refreshes={periodic_calls} (expected False, 0)")


@pytest.mark.parametrize("sky", [80], indirect=True)
def test_show_after_hidden_location_update_resumes_refresh(sky, qapp, monkeypatch):
    from PySide6.QtTest import QTest

    view, tracker = sky
    view.hide()
    qapp.processEvents()
    view.set_observer(0.0, 90.0, 0.0)
    tracker.set_location(0.0, 90.0, 0.0)
    calls = _spy_real(monkeypatch, tracker, "_compute_sun_moon")
    view.show()
    qapp.processEvents()
    assert tracker._timer.isActive(), "show must resume the periodic timer"
    assert calls, "show must perform a real immediate refresh at the new site"
    after_show = len(calls)
    QTest.qWait(3 * 80 + 80)
    assert len(calls) > after_show, "visible tracker must resume real periodic refresh"


def test_explicit_refresh_remains_available_while_hidden(sky, engine, qapp, monkeypatch):
    from mbdsdr_ai import atmosphere

    view, tracker = sky
    view.hide()
    qapp.processEvents()
    view.set_observer(0.0, 90.0, 0.0)
    tracker.set_location(0.0, 90.0, 0.0)
    before_sun = dict(view._sun)
    engine.set_unix(UTC0 + DAY_S)
    calls = _spy_real(monkeypatch, tracker, "_compute_sun_moon")
    forwarded = _spy_real(monkeypatch, view, "set_celestial_bodies")
    tracker.refresh()
    assert len(calls) == 1, "explicit hidden refresh must still run the real calculation"
    assert calls[0][0] == (engine.now_unix(),), "manual refresh must use the current engine time"
    assert len(forwarded) == 1, "computed data must reach the real view setter"
    sun, moon, brightness = forwarded[0][0]
    assert view._sun == sun and view._moon == moon
    assert view._sun is not None and view._sun != before_sun, "manual refresh must replace stale solar data"
    expected_brightness = atmosphere.sky_brightness_factor(
        atmosphere.sun_altitude_deg(engine.now_jd(), 0.0, 90.0))
    assert view._sky_brightness == brightness == pytest.approx(expected_brightness, rel=0, abs=1e-9)
    # Optional JPL/astropy may be absent in an isolated offline profile. The
    # real solar fallback remains available; missing Moon data stays missing.
    print(f"explicit hidden refresh: calculations={len(calls)}, deliveries={len(forwarded)}, "
          f"moon_available={moon is not None}")


def test_no_selection_does_not_compute_body_rise_set(sky, engine, qapp, monkeypatch):
    from PySide6.QtGui import QPixmap

    view, tracker = sky
    calls = _spy_real(monkeypatch, view, "_body_rise_set")
    assert view._picked_body is None
    for day in (1, 2, 3):
        engine.set_unix(UTC0 + day * DAY_S)
        tracker.refresh()
        view.set_time_info(engine.state())
        view.update()
        qapp.processEvents()
        view.render(QPixmap(view.size()))
    print(f"unselected time jumps: rise/set calls={len(calls)}")
    assert not calls, "unselected repaint must not start body rise/set scans"


def test_hidden_selection_defers_rise_set_recomputation(sky, engine, qapp, painted_text, monkeypatch):
    from PySide6.QtTest import QTest

    view, tracker = sky
    view.on_body_picked({"kind": "planet", "key": "moon"})
    _render(qapp, view, painted_text)
    view.hide()
    qapp.processEvents()
    calls = _spy_real(monkeypatch, view, "_body_rise_set")
    for day in (1, 2, 3):
        engine.set_unix(UTC0 + day * DAY_S)
        view.set_observer(0.0, 90.0, 0.0)
        tracker.set_location(0.0, 90.0, 0.0)
        view.set_time_info(engine.state())
        view.update()
        qapp.processEvents()
    QTest.qWait(120)
    print(f"hidden selected context changes: rise/set calls={len(calls)}")
    assert not calls, "hidden selected card must defer expensive rise/set work"


def test_unchanged_selected_context_repaints_without_rescanning(sky, engine, qapp, painted_text, monkeypatch):
    view, tracker = sky
    calls = _spy_real(monkeypatch, view, "_body_rise_set")
    view.on_body_picked({"kind": "planet", "key": "moon"})
    _render(qapp, view, painted_text)
    assert calls, "precondition: initial selection must calculate real rise/set"
    baseline = len(calls)
    initial_jd = engine.now_jd()
    for _ in range(3):
        tracker.refresh()
        view.set_observer(0.0, 0.0, 0.0)
        view.set_time_info(engine.state())
        _render(qapp, view, painted_text)
    assert engine.now_jd() == initial_jd
    print(f"unchanged selected context: initial scans={baseline}, final scans={len(calls)}")
    assert len(calls) == baseline, "unchanged selected body must reuse its rise/set result"
