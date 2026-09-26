"""确定性单测：行星位置与 JPL de440s 星历对比（误差 < 0.1°）。"""
import os
import pytest
from datetime import datetime, timezone

from mbdsdr_ai.astronomy import planets as P

BSP = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "repos", "SatDump", "resources", "spice", "de440s.bsp"))


def _jd_2026_09_27_12utc():
    dt = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)
    return 2440587.5 + (dt - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds() / 86400.0


@pytest.mark.skipif(not os.path.exists(BSP), reason="de440s.bsp not available")
def test_planets_vs_de440s():
    from skyfield.api import load
    ts = load.timescale()
    eph = load(BSP)
    t = ts.utc(2026, 9, 27, 12, 0, 0)
    jd = _jd_2026_09_27_12utc()
    naif = {"mercury": 199, "venus": 299, "mars": 4, "jupiter": 5,
            "saturn": 6, "uranus": 7, "neptune": 8, "moon": 301}
    for name, n in naif.items():
        p = P.compute_planet(name, jd) if name != "moon" else P.compute_moon(jd)
        ref = eph["earth"].at(t).observe(eph[n]).radec()
        sra, sdec = ref[0].hours * 15, ref[1].degrees
        dra = abs(p.ra_deg - sra)
        ddec = abs(p.dec_deg - sdec)
        assert dra < 0.1, f"{name} RA off by {dra:.3f}°"
        assert ddec < 0.1, f"{name} Dec off by {ddec:.3f}°"


def test_known_j2000_earth_position():
    """J2000 时地球日心黄经应 ≈ 100.47°（JPL p_elem_t1 L0）。"""
    xe, ye, ze = P._heliocentric_ecliptic("earth", 0.0)
    import math
    lon = math.degrees(math.atan2(ye, xe)) % 360
    assert abs(lon - 100.46) < 0.2


def test_compute_all_returns_eight_plus_moon():
    jd = _jd_2026_09_27_12utc()
    out = P.compute_all(jd)
    assert len(out) == 8  # 7 planets + moon (earth excluded)
    assert "moon" in out
    for name, p in out.items():
        assert -90 <= p.dec_deg <= 90
        assert 0 <= p.ra_deg < 360


def test_recommend_targets_returns_list():
    jd = _jd_2026_09_27_12utc()
    recs = P.recommend_targets(jd, observer_lat_deg=39.9, min_alt_deg=10.0)
    assert isinstance(recs, list)
    for p in recs:
        assert p.magnitude < 5.0  # 亮的优先


def test_unknown_planet_raises():
    with pytest.raises(ValueError):
        P.compute_planet("pluto", _jd_2026_09_27_12utc())
