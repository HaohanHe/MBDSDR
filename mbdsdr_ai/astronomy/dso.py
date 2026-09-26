"""
mbdsdr_ai/astronomy/dso.py — 深空天体 (DSO) 目录
==================================================

对照 Stellarium NGC/IC 目录（``nebulae/default/catalog.dat``）。
内置真实数据精简子集：Messier 110 + 著名 NGC，J2000 坐标（RA 小时、Dec 度），
V 星等，角大小角分。数据来源：Messier Catalog / NGC 公开星表值。

DSO 类型：galaxy / nebula / cluster / supernova_remnant。
我们的增强：:func:`recommend_dso` 按当前时刻/观测者位置推荐亮 DSO。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Tuple

__all__ = ["DSOEntry", "DSOCatalog", "BUILTIN_CATALOG", "recommend_dso"]


@dataclass(frozen=True)
class DSOEntry:
    name: str
    dso_type: str
    ra_hours: float
    dec_deg: float
    magnitude: float
    size_arcmin: float
    constellation: str = ""

    @property
    def ra_deg(self) -> float:
        return self.ra_hours * 15.0


_DSO_ROWS: Tuple[Tuple[str, str, float, float, float, float, str], ...] = (
    ("M1",  "supernova_remnant", 5.5755,  +22.0145, 8.4, 6.0,  "Tau"),
    ("M2",  "cluster", 21.5505,  -0.8233, 6.5, 16.0,  "Aqr"),
    ("M3",  "cluster", 13.7280,  +28.3704, 6.2, 18.0,  "CVn"),
    ("M4",  "cluster", 16.3910,  -26.5218, 5.6, 26.0,  "Sco"),
    ("M5",  "cluster", 15.1846,  +2.0815, 5.6, 23.0,  "Ser"),
    ("M6",  "cluster", 17.5728,  -32.2060, 4.2, 20.0,  "Sco"),
    ("M7",  "cluster", 17.8975,  -34.7967, 3.3, 80.0,  "Sco"),
    ("M8",  "nebula", 18.0520,  -24.3867, 6.0, 90.0,  "Sgr"),
    ("M9",  "cluster", 17.5581,  -18.5250, 7.7, 9.3,  "Oph"),
    ("M10", "cluster", 16.9573,  -4.0900, 6.6, 20.0,  "Oph"),
    ("M11", "cluster", 18.8663,  -6.2633, 6.3, 14.0,  "Sct"),
    ("M12", "cluster", 16.6928,  -1.9483, 6.7, 22.0,  "Oph"),
    ("M13", "cluster", 16.6948,  +36.4613, 5.8, 20.0,  "Her"),
    ("M14", "cluster", 17.6495,  -3.2472, 7.6, 11.0,  "Oph"),
    ("M15", "cluster", 21.5075,  +12.1674, 6.2, 18.0,  "Peg"),
    ("M16", "nebula", 18.3385,  -13.7852, 6.0, 35.0,  "Ser"),
    ("M17", "nebula", 18.3398,  -16.1710, 6.0, 46.0,  "Sgr"),
    ("M18", "cluster", 18.3305,  -17.0780, 7.5, 9.8,  "Sgr"),
    ("M19", "cluster", 17.6789,  -26.2657, 6.8, 17.0,  "Oph"),
    ("M20", "nebula", 18.0248,  -23.0288, 6.3, 28.0,  "Sgr"),
    ("M21", "cluster", 18.0314,  -22.5150, 5.9, 13.0,  "Sgr"),
    ("M22", "cluster", 18.0978,  -23.9047, 5.1, 32.0,  "Sgr"),
    ("M23", "cluster", 17.9620,  -19.0033, 5.5, 27.0,  "Sgr"),
    ("M24", "cluster", 18.3270,  -18.4167, 4.6, 90.0,  "Sgr"),
    ("M25", "cluster", 18.5408,  -19.1500, 4.6, 32.0,  "Sgr"),
    ("M26", "cluster", 18.7021,  -9.4060, 8.0, 15.0,  "Sct"),
    ("M27", "nebula", 19.9942,  +22.7210, 7.4, 8.0,  "Vul"),
    ("M28", "cluster", 18.3961,  -24.8704, 6.9, 11.0,  "Sgr"),
    ("M29", "cluster", 20.2233,  +38.5217, 6.6, 7.0,  "Cyg"),
    ("M30", "cluster", 21.5421,  -23.1798, 7.2, 12.0,  "Cap"),
    ("M31", "galaxy", 0.7123,  +41.2692, 3.4, 178.0, "And"),
    ("M32", "galaxy", 0.7148,  +40.8652, 8.1, 8.0,  "And"),
    ("M33", "galaxy", 1.5644,  +30.6602, 5.7, 73.0,  "Tri"),
    ("M34", "cluster", 2.7960,  +42.7780, 5.5, 35.0,  "Per"),
    ("M35", "cluster", 6.1680,  +24.3317, 5.1, 28.0,  "Gem"),
    ("M36", "cluster", 5.5847,  +34.1358, 6.0, 12.0,  "Aur"),
    ("M37", "cluster", 5.8837,  +32.5483, 6.2, 24.0,  "Aur"),
    ("M38", "cluster", 5.8367,  +35.8467, 6.4, 20.0,  "Aur"),
    ("M39", "cluster", 21.5228,  +48.4400, 4.6, 32.0,  "Cyg"),
    ("M40", "cluster", 12.5475,  +58.0600, 8.4, 0.5,  "UMa"),
    ("M41", "cluster", 6.7816,  -20.7317, 4.5, 38.0,  "CMa"),
    ("M42", "nebula", 5.5833,  -5.3911, 4.0, 66.0,  "Ori"),
    ("M43", "nebula", 5.5596,  -5.2703, 9.0, 20.0,  "Ori"),
    ("M44", "cluster", 8.0275,  +19.6725, 3.1, 95.0,  "Cnc"),
    ("M45", "cluster", 3.7992,  +24.1051, 1.6, 110.0, "Tau"),
    ("M46", "cluster", 7.4883,  -14.8028, 6.0, 30.0,  "Pup"),
    ("M47", "cluster", 7.6113,  -14.4817, 4.4, 30.0,  "Pup"),
    ("M48", "cluster", 8.2342,  -5.7433, 5.8, 30.0,  "Hya"),
    ("M49", "galaxy", 12.8018,  +8.0000, 8.4, 9.0,  "Vir"),
    ("M50", "cluster", 7.0213,  -8.3361, 5.9, 16.0,  "Mon"),
    ("M51", "galaxy", 13.4989,  +47.1952, 8.4, 11.0, "CVn"),
    ("M52", "cluster", 23.4024,  +61.5922, 7.3, 13.0, "Cas"),
    ("M53", "cluster", 13.2645,  +18.1684, 7.6, 13.0, "Com"),
    ("M54", "cluster", 18.9158,  -30.4702, 7.6, 9.0,  "Sgr"),
    ("M55", "cluster", 19.6985,  -30.9625, 6.3, 19.0, "Sgr"),
    ("M56", "cluster", 19.9931,  +30.9742, 8.3, 8.0,  "Lyr"),
    ("M57", "nebula", 18.8980,  +33.0289, 8.8, 1.4,  "Lyr"),
    ("M58", "galaxy", 12.6685,  +11.8236, 9.7, 5.0,  "Vir"),
    ("M59", "galaxy", 12.7044,  +11.8847, 9.8, 5.0,  "Vir"),
    ("M60", "galaxy", 12.7092,  +11.5528, 8.8, 7.0,  "Vir"),
    ("M61", "galaxy", 12.7038,  +4.4012, 9.7, 6.0,  "Vir"),
    ("M62", "cluster", 17.0111,  -30.1138, 6.5, 15.0, "Oph"),
    ("M63", "galaxy", 13.0950,  +42.0222, 8.6, 10.0, "CVn"),
    ("M64", "galaxy", 12.9907,  +21.9835, 8.5, 10.0, "Com"),
    ("M65", "galaxy", 11.1834,  +13.0922, 9.3, 8.0,  "Leo"),
    ("M66", "galaxy", 11.2054,  +12.9917, 8.9, 8.0,  "Leo"),
    ("M67", "cluster", 8.8975,  +11.8069, 6.1, 30.0, "Cnc"),
    ("M68", "cluster", 12.3985,  -26.7400, 7.8, 10.0, "Hya"),
    ("M69", "cluster", 18.3103,  -32.2656, 7.6, 7.0,  "Sgr"),
    ("M70", "cluster", 18.4580,  -32.2914, 7.9, 8.0,  "Sgr"),
    ("M71", "cluster", 19.7676,  +18.1078, 8.2, 7.0,  "Sge"),
    ("M72", "cluster", 20.2306,  -12.5372, 9.3, 6.6,  "Aqr"),
    ("M73", "cluster", 20.7442,  -12.6136, 9.0, 2.8,  "Aqr"),
    ("M74", "galaxy", 1.5350,  +29.0185, 9.4, 10.0, "Psc"),
    ("M75", "cluster", 20.0782,  -21.9214, 8.5, 6.0,  "Sgr"),
    ("M76", "nebula", 1.3225,  +51.5756, 10.1, 2.7,  "Per"),
    ("M77", "galaxy", 2.7898,  -0.0133, 8.9, 7.0,  "Cet"),
    ("M78", "nebula", 5.7893,  +0.7967, 8.3, 8.0,  "Ori"),
    ("M79", "cluster", 5.9487,  -24.2075, 7.7, 9.0,  "Lep"),
    ("M80", "cluster", 16.6900,  -22.9783, 7.3, 10.0, "Sco"),
    ("M81", "galaxy", 9.9263,  +69.1042, 6.9, 24.0, "UMa"),
    ("M82", "galaxy", 9.9330,  +69.6817, 8.4, 11.0, "UMa"),
    ("M83", "galaxy", 13.6897,  -29.6248, 7.5, 11.0, "Hya"),
    ("M84", "galaxy", 12.6373,  +12.8867, 9.1, 5.0,  "Vir"),
    ("M85", "galaxy", 12.6703,  +18.1175, 9.1, 7.0,  "Com"),
    ("M86", "galaxy", 12.6450,  +12.9567, 8.9, 7.0,  "Vir"),
    ("M87", "galaxy", 12.6690,  +12.9112, 8.6, 8.0,  "Vir"),
    ("M88", "galaxy", 12.9825,  +14.4217, 9.6, 7.0,  "Com"),
    ("M89", "galaxy", 12.5672,  +12.5578, 9.8, 5.0,  "Vir"),
    ("M90", "galaxy", 12.5725,  +13.1633, 9.5, 7.0,  "Vir"),
    ("M91", "galaxy", 12.5050,  +14.4900, 10.2, 5.0, "Com"),
    ("M92", "cluster", 17.1708,  +43.1310, 6.4, 14.0, "Her"),
    ("M93", "cluster", 7.7490,  -23.8500, 6.0, 22.0, "Pup"),
    ("M94", "galaxy", 12.8403,  +41.0733, 8.2, 11.0, "CVn"),
    ("M95", "galaxy", 10.0593,  +11.7017, 9.7, 7.0,  "Leo"),
    ("M96", "galaxy", 10.1215,  +11.8214, 9.2, 7.0,  "Leo"),
    ("M97", "nebula", 11.1483,  +55.0183, 9.9, 3.4,  "UMa"),
    ("M98", "galaxy", 12.1433,  +14.9072, 10.1, 8.0, "Com"),
    ("M99", "galaxy", 12.3260,  +14.4200, 9.8, 5.0,  "Com"),
    ("M100", "galaxy", 12.4375, +15.8225, 9.3, 7.0,  "Com"),
    ("M101", "galaxy", 14.0428, +54.3489, 7.9, 29.0, "UMa"),
    ("M102", "galaxy", 16.0142, +55.7617, 9.9, 7.0,  "UMa"),
    ("M103", "cluster", 1.5603, +60.6572, 7.4, 6.0,  "Cas"),
    ("M104", "galaxy", 12.3995, -11.6231, 8.0, 9.0,  "Vir"),
    ("M105", "galaxy", 11.3143, +12.5881, 9.3, 6.0,  "Leo"),
    ("M106", "galaxy", 12.1800, +47.3036, 8.4, 18.0, "CVn"),
    ("M107", "cluster", 16.7315, -13.0569, 7.9, 10.0, "Oph"),
    ("M108", "galaxy", 11.9677, +55.9028, 10.0, 8.0, "UMa"),
    ("M109", "galaxy", 11.9655, +53.3811, 9.8, 8.0,  "UMa"),
    ("M110", "galaxy", 0.6755, +41.6858, 8.0, 17.0, "And"),
    ("NGC253",  "galaxy", 0.7683,  -25.2889, 8.0, 18.0, "Scl"),
    ("NGC869",  "cluster", 2.3383,  +56.5575, 4.3, 18.0, "Cas"),
    ("NGC884",  "cluster", 2.3600,  +57.1417, 4.4, 18.0, "Cas"),
    ("NGC2070", "nebula", 5.6283,  -69.1000, 5.1, 36.0, "Dor"),
    ("NGC5139", "cluster", 13.3948, -47.4783, 3.7, 36.0, "Cen"),
    ("NGC7000", "nebula", 20.9800, +44.5500, 4.0, 120.0, "Cyg"),
    ("NGC7293", "nebula", 22.2968, -20.8344, 7.3, 16.0, "Aqr"),
)


class DSOCatalog:
    def __init__(self, entries: Optional[List[DSOEntry]] = None):
        self._entries: List[DSOEntry] = entries or []
        self._by_name: Dict[str, DSOEntry] = {e.name: e for e in self._entries}

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[DSOEntry]:
        return iter(self._entries)

    def get(self, name: str) -> Optional[DSOEntry]:
        return self._by_name.get(name.upper())

    def filter_type(self, dso_type: str) -> List[DSOEntry]:
        return [e for e in self._entries if e.dso_type == dso_type]

    def near(self, ra_deg: float, dec_deg: float, radius_deg: float) -> List[DSOEntry]:
        out = []
        for e in self._entries:
            dra = math.radians((e.ra_deg - ra_deg) * math.cos(math.radians(dec_deg)))
            ddec = math.radians(e.dec_deg - dec_deg)
            if math.degrees(math.sqrt(dra * dra + ddec * ddec)) <= radius_deg:
                out.append(e)
        return out

    @classmethod
    def builtin(cls) -> "DSOCatalog":
        entries = [DSOEntry(name=n, dso_type=t, ra_hours=ra, dec_deg=dec,
                            magnitude=mag, size_arcmin=sz, constellation=c)
                   for (n, t, ra, dec, mag, sz, c) in _DSO_ROWS]
        return cls(entries)


BUILTIN_CATALOG = DSOCatalog.builtin()


def recommend_dso(jd: float, observer_lat_deg: float,
                  catalog: Optional[DSOCatalog] = None,
                  min_alt_deg: float = 20.0,
                  max_magnitude: float = 9.0,
                  max_sun_dist_deg: float = 90.0) -> List[DSOEntry]:
    cat = catalog or BUILTIN_CATALOG
    T = (jd - 2451545.0) / 36525.0
    sun_lon = 280.46646 + 36000.76983 * T
    from .planets import _ecliptic_to_equatorial
    sun_ra, sun_dec = _ecliptic_to_equatorial(sun_lon, 0.0)
    gmst = (280.46061837 + 360.98564736629 * (jd - 2451545.0)) % 360.0

    out: List[DSOEntry] = []
    for e in cat:
        if e.magnitude > max_magnitude:
            continue
        ha = math.radians((gmst - e.ra_deg) % 360.0)
        lat = math.radians(observer_lat_deg)
        dec = math.radians(e.dec_deg)
        sin_alt = math.sin(lat) * math.sin(dec) + math.cos(lat) * math.cos(dec) * math.cos(ha)
        alt = math.degrees(math.asin(max(-1.0, min(1.0, sin_alt))))
        if alt < min_alt_deg:
            continue
        cos_sep = (math.sin(dec) * math.sin(math.radians(sun_dec))
                   + math.cos(dec) * math.cos(math.radians(sun_dec))
                   * math.cos(math.radians(e.ra_deg - sun_ra)))
        sep = math.degrees(math.acos(max(-1.0, min(1.0, cos_sep))))
        if sep < max_sun_dist_deg:
            continue
        out.append(e)
    out.sort(key=lambda x: x.magnitude)
    return out
