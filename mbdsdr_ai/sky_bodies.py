# SPDX-License-Identifier: MIT
"""行星/月球目标识别 —— 给定地平指向，回答视场里有哪些太阳系天体。

与 :func:`mbdsdr_ai.sky_pointing.identify_direction`（恒星/星官）同一玩法：
输入是**地平坐标**指向（az 北=0 东=90，alt 地平=0，单位度）与视场直径，
输出落在视锥内、且位于地平线以上的行星与月球，按星等从亮到暗排序。

坐标层全部复用既有实现，本模块不另造历元变换：

* 太阳系位置：``mbdsdr_ai.astronomy.planets.compute_all``（J2000 RA/Dec）；
* J2000 赤道 -> 地平：``celestial_geometry`` 的
  ``GroundStation`` + ``j2000_to_altaz`` + ``azalt_from_vec``；
* 角距：``sky_interaction.angular_distance_deg``。

诚实语义：输入非法或视场内没有目标时返回 ``matched=False`` 与具体
``reason``，不拿最近邻硬凑；地平线以下的暗天体不会被误报成可见。

纯函数、无 Qt、不读盘。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

__all__ = ["identify_bodies", "NAME_ZH"]

# 八大行星 + 月球的中文名
NAME_ZH: Dict[str, str] = {
    "mercury": "水星",
    "venus": "金星",
    "mars": "火星",
    "jupiter": "木星",
    "saturn": "土星",
    "uranus": "天王星",
    "neptune": "海王星",
    "moon": "月球",
}

_NO_BODY_REASON = "视场内没有可见的行星或月球"


def _as_finite(value: Any) -> Optional[float]:
    """转成有限 float；非数值 / NaN / Inf 返回 None。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _invalid(az_deg: Any, alt_deg: Any, fov_deg: Any,
             reason: str) -> Dict[str, Any]:
    return {
        "matched": False,
        "bodies": [],
        "total_matched": 0,
        "truncated": False,
        "query": {"az_deg": az_deg, "alt_deg": alt_deg, "fov_deg": fov_deg},
        "reason": reason,
    }


def identify_bodies(az_deg: float, alt_deg: float, fov_deg: float,
                    jd: float, lat_deg: float, lon_deg: float,
                    limit: int = 3) -> Dict[str, Any]:
    """识别某个地平指向附近、地平线以上的行星与月球。

    Args:
        az_deg / alt_deg: 指向（度，北=0 东=90，alt 地平以上为正）。
        fov_deg: 视场直径（度）；只返回与指向角距 ``<= fov/2`` 的天体。
        jd: UT1 儒略日（与工具面同一口径）。
        lat_deg / lon_deg: 观测站纬度/经度（度，北纬/东经为正）。
        limit: 最多返回几个（按星等从亮到暗，默认 3）。

    Returns:
        命中：``matched=True``、``bodies``（每项含 ``name`` / ``name_zh`` /
        ``kind`` / ``ra_deg`` / ``dec_deg`` / ``az_deg`` / ``alt_deg`` /
        ``magnitude`` / ``angular_diameter_arcsec`` / ``distance_au`` /
        ``angular_distance_deg``）、``total_matched``、``truncated``、
        ``query``。
        没有目标：``matched=False`` + ``reason``，不返回最近邻凑数。
    """
    az = _as_finite(az_deg)
    alt = _as_finite(alt_deg)
    fov = _as_finite(fov_deg)
    jd_v = _as_finite(jd)
    lat = _as_finite(lat_deg)
    lon = _as_finite(lon_deg)

    if az is None or alt is None:
        return _invalid(az_deg, alt_deg, fov_deg,
                        "输入非法：az_deg / alt_deg 必须是有限数值")
    if not (-90.0 <= alt <= 90.0):
        return _invalid(az, alt, fov_deg,
                        f"输入非法：alt_deg 越界（{alt}°，合法 -90..90）")
    if fov is None or not (0.0 < fov <= 180.0):
        return _invalid(az, alt, fov_deg,
                        f"输入非法：fov_deg 必须在 (0, 180] 度内，收到 {fov_deg!r}")
    if jd_v is None:
        return _invalid(az, alt, fov,
                        "输入非法：jd 必须是有限的 UT1 儒略日")
    if lat is None:
        return _invalid(az, alt, fov, "输入非法：lat_deg 必须是有限数值")
    if lon is None:
        return _invalid(az, alt, fov, "输入非法：lon_deg 必须是有限数值")
    try:
        limit_n = int(limit)
    except (TypeError, ValueError):
        return _invalid(az, alt, fov,
                        f"输入非法：limit 必须是正整数，收到 {limit!r}")
    if limit_n <= 0:
        return _invalid(az, alt, fov,
                        f"输入非法：limit 必须 > 0，收到 {limit!r}")

    from .astronomy.planets import compute_all
    from .celestial_geometry import (
        GroundStation, azalt_from_vec, j2000_to_altaz, vec_from_radec)
    from .sky_interaction import angular_distance_deg

    station = GroundStation(lon, lat, 0.0, "sky_bodies")
    radius = 0.5 * fov

    hits: List[Dict[str, Any]] = []
    for name, pos in compute_all(jd_v).items():
        vec = j2000_to_altaz(
            vec_from_radec(pos.ra_deg, pos.dec_deg), station, jd_v)
        body_az, body_alt = azalt_from_vec(vec)
        body_az = float(body_az)
        body_alt = float(body_alt)
        # 地平线以下不可见，不做目标
        if body_alt <= 0.0:
            continue
        dist = angular_distance_deg(body_az, body_alt, az, alt)
        if dist > radius:
            continue
        hits.append({
            "name": name,
            "name_zh": NAME_ZH[name],
            "kind": "moon" if name == "moon" else "planet",
            "ra_deg": float(pos.ra_deg),
            "dec_deg": float(pos.dec_deg),
            "az_deg": body_az,
            "alt_deg": body_alt,
            "magnitude": float(pos.magnitude),
            "angular_diameter_arcsec": float(pos.angular_diameter_arcsec),
            "distance_au": float(pos.distance_au),
            "angular_distance_deg": float(dist),
        })

    # 星等越小越亮 -> 升序即从亮到暗
    hits.sort(key=lambda b: b["magnitude"])
    total = len(hits)
    bodies = hits[:limit_n]

    return {
        "matched": bool(bodies),
        "bodies": bodies,
        "total_matched": total,
        "truncated": total > limit_n,
        "query": {"az_deg": az, "alt_deg": alt, "fov_deg": fov},
        "reason": "" if bodies else _NO_BODY_REASON,
    }
