# SPDX-License-Identifier: MIT
"""
mbdsdr_ai/skyengine/satellites.py — 卫星元数据 / COSPAR / 升落预报
====================================================================

不造假卫星: 只存目录索引 (NORAD 编号, 类型, 别名), TLE 轨道元素仍由
orbit.fetch_tle() 从 Celestrak 或本地缓存提供。无 TLE 时不传播、不绘制。

字段对应 Stellarium 选中信息卡 (用户实测规格 10.4):
  NORAD 编号 / COSPAR / 别名(Also known as) / Distance(km) /
  Ra/Dec / Az/Alt (Alt 负=地平线下) / Magnitude (无值占位) / Visibility。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict


@dataclass(frozen=True)
class SatelliteMeta:
    name: str
    norad: int
    kind: str            # space_station / satellite / weather / amateur
    aliases: tuple = ()
    cospar_override: str = ""

    @property
    def kind_label(self) -> str:
        return {
            "space_station": "Space Station",
            "satellite": "Artificial Satellite",
            "weather": "Weather Satellite",
            "amateur": "Amateur Satellite",
        }.get(self.kind, "Satellite")


# 目录索引 (真实 NORAD 编号; 不含轨道元素)
SATELLITE_INDEX: Dict[int, SatelliteMeta] = {
    25544: SatelliteMeta(
        "ISS (ZARYA)", 25544, "space_station",
        aliases=("ISS", "International Space Station", "ISS (ZARYA)"),
        cospar_override="1998-067A"),
    48274: SatelliteMeta(
        "ISS (NAUKA)", 48274, "satellite",
        aliases=("Nauka", "ISS (NAUKA)"),
        cospar_override="2021-066A"),
    25338: SatelliteMeta(
        "NOAA 15", 25338, "weather",
        aliases=("NOAA 15",), cospar_override="1998-030A"),
    28654: SatelliteMeta(
        "NOAA 18", 28654, "weather",
        aliases=("NOAA 18",), cospar_override="2005-018A"),
    33591: SatelliteMeta(
        "NOAA 19", 33591, "weather",
        aliases=("NOAA 19",), cospar_override="2009-005A"),
    44016: SatelliteMeta(
        "METEOR M2", 44016, "weather",
        aliases=("METEOR M2",), cospar_override="2014-037A"),
    54234: SatelliteMeta(
        "FENGYUN 3D", 54234, "weather",
        aliases=("FENGYUN 3D",), cospar_override="2021-092A"),
}


def cospar_from_tle(line1: str, override: str = "") -> str:
    """从 TLE line1 解析国际 designator (COSPAR)。

    TLE line1 的 1-based 列 10-15 (0-based 9:15) 是国际编号, 如 "98067A";
    列 19-24 (0-based 18:24) 是历元年+年积日, 不要混淆。
    两位年: 00-56 -> 20xx, 57-99 -> 19xx (CelesTrak 惯例)。
    解析失败返回 override 或空串 (占位, 不编造)。
    """
    if override:
        return override
    try:
        raw = line1[9:15].strip()
        if len(raw) < 3:
            return ""
        yy = int(raw[:2])
        rest = raw[2:]
        century = 1900 if yy >= 57 else 2000
        return f"{century + yy}-{rest}"
    except Exception:
        return ""


def norad_from_tle(line1: str) -> Optional[int]:
    """从 line1 解析 NORAD 编号 (第 3-7 列)。"""
    try:
        return int(line1[2:7])
    except Exception:
        return None


def next_rise_set(satrec, lat: float, lon: float, alt_km: float,
                  jd_now: float, step_s: float = 30.0,
                  horizon_alt: float = 0.0,
                  horizon_hold_s: float = 86400.0) -> Dict[str, object]:
    """扫描未来 horizon_hold_s 秒, 找高度穿越地平线的时刻。

    返回 {"rise_jd": float|None, "set_jd": float|None, "max_alt": float}。
    传播失败/无过境时对应项为 None (占位, 不编造时刻)。
    """
    from mbdsdr_ai import orbit

    rise_jd: Optional[float] = None
    set_jd: Optional[float] = None
    max_alt = -999.0
    n = int(horizon_hold_s / step_s)
    prev_above: Optional[bool] = None
    for i in range(n + 1):
        jd = jd_now + (i * step_s) / 86400.0
        try:
            st = orbit._state_from_satrec(satrec, "x", jd, lat, lon, alt_km)
        except Exception:
            continue
        if st is None:
            continue
        alt = float(st["elevation"])
        above = alt > horizon_alt
        if alt > max_alt:
            max_alt = alt
        if prev_above is False and above and rise_jd is None:
            rise_jd = jd
        if prev_above is True and not above and set_jd is None:
            set_jd = jd
        prev_above = above
        if rise_jd is not None and set_jd is not None:
            break
    return {"rise_jd": rise_jd, "set_jd": set_jd, "max_alt": max_alt}
