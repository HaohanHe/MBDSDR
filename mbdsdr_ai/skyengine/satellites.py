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
from typing import Optional, Dict, List

#: 升落穿越点二分的时间容差（秒）。``next_rise_set`` 用它作为停止条件，
#: 也是「AOS/LOS 时刻误差上界」的唯一声明值——调用方与断言都引用这里，
#: 不许再内联字面量。
RISE_SET_BISECT_TOL_S = 1.0


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

    返回 ``{"rise_jd", "set_jd", "max_alt", "already_above", "event_order"}``。
    传播失败/无过境时时刻为 None (占位, 不编造时刻)。

    2026-11-06 修：旧式把 **30s 采样点**直接当 AOS/LOS（误差上限 30s，
    实测 13s），现在对每个穿越区间做二分，收敛到 1s 以内。扫描起点已在
    天上时不再把「下一次 set」和「再下一次 rise」混成一对：显式给
    ``already_above=True`` + ``event_order=["set","rise"]``，让上层能如实
    说明「当前可见，下一次事件是落下」。
    """
    from mbdsdr_ai import orbit

    def _alt_at(jd: float) -> Optional[float]:
        try:
            st = orbit._state_from_satrec(satrec, "x", jd, lat, lon, alt_km)
        except Exception:
            return None
        return None if st is None else float(st["elevation"])

    def _crossing(jd_lo: float, el_lo: float,
                  jd_hi: float, el_hi: float) -> float:
        """在 [jd_lo, jd_hi] 二分求仰角 = horizon_alt 的时刻（RISE_SET_BISECT_TOL_S 容差）。"""
        lo_above = el_lo > horizon_alt
        for _ in range(32):
            if (jd_hi - jd_lo) * 86400.0 <= RISE_SET_BISECT_TOL_S:
                break
            mid = 0.5 * (jd_lo + jd_hi)
            e = _alt_at(mid)
            if e is None:
                break
            if (e > horizon_alt) == lo_above:
                jd_lo, el_lo = mid, e
            else:
                jd_hi, el_hi = mid, e
        return 0.5 * (jd_lo + jd_hi)

    rise_jd: Optional[float] = None
    set_jd: Optional[float] = None
    max_alt = -999.0
    n = int(horizon_hold_s / step_s)
    prev_jd: Optional[float] = None
    prev_el: Optional[float] = None
    prev_above: Optional[bool] = None
    first_above: Optional[bool] = None
    order: List[str] = []
    for i in range(n + 1):
        jd = jd_now + (i * step_s) / 86400.0
        alt = _alt_at(jd)
        if alt is None:
            continue
        above = alt > horizon_alt
        if first_above is None:
            first_above = above
        if alt > max_alt:
            max_alt = alt
        if prev_above is False and above and rise_jd is None:
            rise_jd = _crossing(prev_jd, prev_el, jd, alt)
            order.append("rise")
        if prev_above is True and not above and set_jd is None:
            set_jd = _crossing(prev_jd, prev_el, jd, alt)
            order.append("set")
        prev_jd, prev_el, prev_above = jd, alt, above
        if rise_jd is not None and set_jd is not None:
            break
    already_above = bool(first_above) if first_above is not None else False
    return {"rise_jd": rise_jd, "set_jd": set_jd, "max_alt": max_alt,
            "already_above": already_above, "event_order": order}
