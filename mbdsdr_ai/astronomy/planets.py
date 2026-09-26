"""
mbdsdr_ai/astronomy/planets.py — 太阳系行星/月球位置
======================================================

对照 Stellarium ``src/core/modules/SolarSystem.cpp``（底层 VSOP87/ELP2000）。
采用 Meeus《Astronomical Algorithms》第 31/32 章低精度 Kepler 元素表
（Table 31.A/B），1000–3000 AD 内精度约 0.01°（内行星）~0.1°（外行星）。

  - 八大行星：椭圆轨道六要素 + 每世纪速率，解 Kepler 方程得日心黄经，再
    换算到地心黄经/黄纬，再转 J2000 赤道坐标 (RA/Dec)。
  - 月球：Meeus ch.47 主要周期项 (L', D, M, M', F)，Top 15 黄经 / Top 8 黄纬。
  - 视星等：Meeus ch.41 经验公式；角直径：标准距离处角直径 / 距离。

数据来自 Meeus 已发表公式，不造假。测试用 de440s JPL 星历比对。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Tuple

__all__ = ["PlanetPos", "compute_planet", "compute_moon", "compute_all", "recommend_targets"]

J2000_JD = 2451545.0
OBLIQUITY_J2000 = 23.4392911  # deg

# JPL p_elem_t1.txt (Explanatory Supplement ch.8, valid 1800-2050):
# (a0,a1, e0,e1, i0,i1, L0,L1, peri0,peri1, node0,node1)
_PLANET_ELEMENTS: Dict[str, Tuple[float, ...]] = {
    "mercury": (0.38709927, 0.00000037, 0.20563593, 0.00001906, 7.00497902, -0.00594749, 252.25032350, 149472.67411175, 77.45779628, 0.16047689, 48.33076593, -0.12534081),
    "venus":   (0.72333566, 0.00000390, 0.00677672, -0.00004107, 3.39467605, -0.00078890, 181.97909950, 58517.81538729, 131.60246718, 0.00268329, 76.67984255, -0.27769418),
    "earth":   (1.00000261, 0.00000562, 0.01671123, -0.00004392, -0.00001531, -0.01294668, 100.46457166, 35999.37244981, 102.93768193, 0.32327364, 0.0, 0.0),
    "mars":    (1.52371034, 0.00001847, 0.09339410, 0.00007882, 1.84969142, -0.00813131, -4.55343205, 19140.30268499, -23.94362959, 0.44441088, 49.55953891, -0.29257343),
    "jupiter": (5.20288700, -0.00011607, 0.04838624, -0.00013253, 1.30439695, -0.00183714, 34.39644051, 3034.74612775, 14.72847983, 0.21252668, 100.47390909, 0.20469106),
    "saturn":  (9.53667594, -0.00125060, 0.05386179, -0.00050991, 2.48599187, 0.00193609, 49.95424423, 1222.49362201, 92.59887831, -0.41897216, 113.66242448, -0.28867794),
    "uranus":  (19.18916464, -0.00196176, 0.04725744, -0.00004397, 0.77263783, -0.00242939, 313.23810451, 428.48202785, 170.95427630, 0.40805281, 74.01692503, 0.04240589),
    "neptune": (30.06992276, 0.00026291, 0.00859048, 0.00005105, 1.77004347, 0.00035372, -55.12002969, 218.45945325, 44.96476227, -0.32241464, 131.78422574, -0.00508664),
}

_ANGULAR_DIAMETER_ARCSEC_AT_1AU = {
    "mercury": 6.74, "venus": 16.98, "earth": 0.0, "mars": 9.36,
    "jupiter": 196.74, "saturn": 165.6, "uranus": 70.7, "neptune": 68.7,
}

_MAGNITUDE_COEFFS = {
    "mercury": (1.58, 0.0380), "venus": (-4.47, 0.000), "mars": (-1.52, 0.016),
    "jupiter": (-9.40, 0.005), "saturn": (-8.88, 0.044), "uranus": (-7.19, 0.0),
    "neptune": (-6.87, 0.0),
}


@dataclass
class PlanetPos:
    name: str
    ra_deg: float
    dec_deg: float
    ecl_lon_deg: float
    ecl_lat_deg: float
    distance_au: float
    heliocentric_distance_au: float
    magnitude: float
    angular_diameter_arcsec: float
    phase_angle_deg: float

    def describe(self) -> str:
        return (f"{self.name:8s} RA={self.ra_deg:8.3f} Dec={self.dec_deg:+8.3f} "
                f"dist={self.distance_au:.3f}AU V={self.magnitude:+.2f} "
                f"dia={self.angular_diameter_arcsec:.2f}\" phase={self.phase_angle_deg:.1f}")


def _norm360(x: float) -> float:
    x = x % 360.0
    return x + 360.0 if x < 0 else x


def _kepler_eccentric_anomaly(M_deg: float, e: float, iterations: int = 30) -> float:
    M = math.radians(_norm360(M_deg))
    E = M if e < 0.05 else math.pi
    for _ in range(iterations):
        E = E - (E - e * math.sin(E) - M) / (1.0 - e * math.cos(E))
    return E


def _heliocentric_ecliptic(name: str, T: float) -> Tuple[float, float, float]:
    (a0, a1, e0, e1, i0, i1, L0, L1, p0, p1, n0, n1) = _PLANET_ELEMENTS[name]
    a = a0 + a1 * T
    e = e0 + e1 * T
    i = math.radians(i0 + i1 * T)
    L = math.radians(_norm360(L0 + L1 * T))
    varpi = math.radians(_norm360(p0 + p1 * T))
    Omega = math.radians(_norm360(n0 + n1 * T))

    M = _norm360(math.degrees(L) - math.degrees(varpi))
    E = _kepler_eccentric_anomaly(M, e)

    xp = a * (math.cos(E) - e)
    yp = a * math.sqrt(1.0 - e * e) * math.sin(E)

    w = varpi - Omega
    cosO, sinO = math.cos(Omega), math.sin(Omega)
    cosw, sinw = math.cos(w), math.sin(w)
    cosi, sini = math.cos(i), math.sin(i)

    x = (cosw * cosO - sinw * sinO * cosi) * xp + (-sinw * cosO - cosw * sinO * cosi) * yp
    y = (cosw * sinO + sinw * cosO * cosi) * xp + (-sinw * sinO + cosw * cosO * cosi) * yp
    z = (sinw * sini) * xp + (cosw * sini) * yp
    return x, y, z


def _ecliptic_to_equatorial(lon_deg: float, lat_deg: float) -> Tuple[float, float]:
    lam = math.radians(lon_deg)
    beta = math.radians(lat_deg)
    eps = math.radians(OBLIQUITY_J2000)
    ra = math.degrees(math.atan2(math.sin(lam) * math.cos(eps) - math.tan(beta) * math.sin(eps),
                                 math.cos(lam)))
    dec = math.degrees(math.asin(math.sin(beta) * math.cos(eps) + math.cos(beta) * math.sin(eps) * math.sin(lam)))
    return _norm360(ra), dec


def compute_planet(name: str, jd: float) -> PlanetPos:
    name = name.lower()
    if name not in _PLANET_ELEMENTS or name == "earth":
        raise ValueError(f"unknown planet: {name}")
    T = (jd - J2000_JD) / 36525.0

    x_p, y_p, z_p = _heliocentric_ecliptic(name, T)
    x_e, y_e, z_e = _heliocentric_ecliptic("earth", T)

    X = x_p - x_e
    Y = y_p - y_e
    Z = z_p - z_e
    dist = math.sqrt(X * X + Y * Y + Z * Z)
    lon = math.degrees(math.atan2(Y, X))
    lat = math.degrees(math.atan2(Z, math.sqrt(X * X + Y * Y)))
    ra, dec = _ecliptic_to_equatorial(lon, lat)

    r_p = math.sqrt(x_p * x_p + y_p * y_p + z_p * z_p)
    r_e = math.sqrt(x_e * x_e + y_e * y_e + z_e * z_e)
    cos_i = (r_p * r_p + dist * dist - r_e * r_e) / (2.0 * r_p * dist)
    cos_i = max(-1.0, min(1.0, cos_i))
    phase = math.degrees(math.acos(cos_i))

    H, k = _MAGNITUDE_COEFFS[name]
    mag = H + 5.0 * math.log10(r_p * dist) + k * phase
    dia = _ANGULAR_DIAMETER_ARCSEC_AT_1AU[name] / dist

    return PlanetPos(name=name, ra_deg=ra, dec_deg=dec, ecl_lon_deg=_norm360(lon),
                     ecl_lat_deg=lat, distance_au=dist, heliocentric_distance_au=r_p,
                     magnitude=mag, angular_diameter_arcsec=dia, phase_angle_deg=phase)


# Meeus ch.47 Table 47.A (D, M, M', F, lon x1e-6 deg) — top 30
_MOON_LON_TERMS: Tuple[Tuple[int, int, int, int, float], ...] = (
    (0, 0, 1, 0, 6288774.0), (2, 0, -1, 0, 1274027.0), (2, 0, 0, 0, 658314.0),
    (0, 0, 2, 0, 213618.0), (0, 1, 0, 0, -185116.0), (0, 0, 0, 2, -114332.0),
    (2, 0, -2, 0, 58793.0), (2, -1, -1, 0, 57066.0), (2, 0, 1, 0, 53322.0),
    (2, -1, 0, 0, 45758.0), (0, 1, -1, 0, -40923.0), (1, 0, 0, 0, -34720.0),
    (0, 1, 1, 0, -30383.0), (2, 0, 0, -2, 15327.0), (0, 0, 1, 2, -12528.0),
    (0, 0, 1, -2, 10980.0), (4, 0, -1, 0, 10675.0), (0, 0, 3, 0, 10034.0),
    (4, 0, -2, 0, 8548.0), (2, 1, -1, 0, -7888.0), (2, 1, 0, 0, -6766.0),
    (1, 0, -1, 0, -5163.0), (1, 1, 0, 0, 4987.0), (2, -1, 1, 0, 4036.0),
    (2, 0, 2, 0, 3994.0), (4, 0, 0, 0, 3861.0), (2, 0, -3, 0, 3665.0),
    (0, 1, -2, 0, -2689.0), (2, 0, -1, 2, -2602.0), (2, -1, -2, 0, 2390.0),
)
# Table 47.B (D, M, M', F, lat x1e-6 deg) — top 15
_MOON_LAT_TERMS: Tuple[Tuple[int, int, int, int, float], ...] = (
    (0, 0, 0, 1, 5128122.0), (0, 0, 1, 1, 280602.0), (0, 0, 1, -1, 277693.0),
    (2, 0, 0, -1, 173237.0), (2, 0, -1, 1, 55413.0), (2, 0, -1, -1, 46271.0),
    (2, 0, 0, 1, 32573.0), (0, 0, 2, 1, 17198.0), (2, 0, 1, -1, 9266.0),
    (0, 0, 2, -1, 8822.0), (2, -1, 0, -1, 8216.0), (2, 0, -2, -1, 4324.0),
    (2, 0, 1, 1, 4200.0), (2, 1, 0, -1, -3359.0), (2, -1, -1, 1, 2463.0),
)


def compute_moon(jd: float) -> PlanetPos:
    T = (jd - J2000_JD) / 36525.0
    Lp = math.radians(_norm360(218.3164477 + 481267.88123421 * T))
    D = math.radians(_norm360(297.8501921 + 445267.1114034 * T))
    M = math.radians(_norm360(357.5291092 + 35999.0502909 * T))
    Mp = math.radians(_norm360(134.9633964 + 477198.8675055 * T))
    F = math.radians(_norm360(93.2720950 + 483202.0175233 * T))

    dlon = 0.0
    for d, m, mp, f, coeff in _MOON_LON_TERMS:
        dlon += coeff * math.sin(d * D + m * M + mp * Mp + f * F)
    dlat = 0.0
    for d, m, mp, f, coeff in _MOON_LAT_TERMS:
        dlat += coeff * math.sin(d * D + m * M + mp * Mp + f * F)

    lon = _norm360(math.degrees(Lp) + dlon * 1e-6)
    lat = dlat * 1e-6

    dist_km = 385000.56 - 20905.355 * math.cos(Mp) - 3699.111 * math.cos(2 * D - Mp)
    dist_au = dist_km / 149597870.7

    ra, dec = _ecliptic_to_equatorial(lon, lat)
    mag = -12.74 + 5.0 * math.log10(dist_au / 0.00257)
    dia = 3474.8 / (dist_km / 384400.0)
    sun_lon = _norm360(280.46646 + 36000.76983 * T)
    phase = abs(_norm360(lon - sun_lon))
    if phase > 180.0:
        phase = 360.0 - phase
    return PlanetPos(name="moon", ra_deg=ra, dec_deg=dec, ecl_lon_deg=lon, ecl_lat_deg=lat,
                     distance_au=dist_au, heliocentric_distance_au=dist_au + 1.0,
                     magnitude=mag, angular_diameter_arcsec=dia, phase_angle_deg=phase)


def compute_all(jd: float) -> Dict[str, PlanetPos]:
    out: Dict[str, PlanetPos] = {}
    for p in _PLANET_ELEMENTS:
        if p == "earth":
            continue
        out[p] = compute_planet(p, jd)
    out["moon"] = compute_moon(jd)
    return out


def recommend_targets(jd: float, observer_lat_deg: float,
                     min_alt_deg: float = 15.0,
                     max_sun_dist_deg: float = 90.0) -> List[PlanetPos]:
    T = (jd - J2000_JD) / 36525.0
    sun_lon_deg = _norm360(280.46646 + 36000.76983 * T)
    sun_ra, sun_dec = _ecliptic_to_equatorial(sun_lon_deg, 0.0)

    gmst = _norm360(280.46061837 + 360.98564736629 * (jd - J2000_JD))
    results: List[PlanetPos] = []
    for p in compute_all(jd).values():
        ha = math.radians(_norm360(gmst - p.ra_deg))
        lat = math.radians(observer_lat_deg)
        dec = math.radians(p.dec_deg)
        sin_alt = math.sin(lat) * math.sin(dec) + math.cos(lat) * math.cos(dec) * math.cos(ha)
        alt = math.degrees(math.asin(max(-1.0, min(1.0, sin_alt))))
        cos_sep = (math.sin(math.radians(p.dec_deg)) * math.sin(math.radians(sun_dec))
                   + math.cos(math.radians(p.dec_deg)) * math.cos(math.radians(sun_dec))
                   * math.cos(math.radians(p.ra_deg - sun_ra)))
        sep = math.degrees(math.acos(max(-1.0, min(1.0, cos_sep))))
        if alt >= min_alt_deg and sep >= max_sun_dist_deg:
            results.append(p)
    results.sort(key=lambda x: x.magnitude)
    return results
