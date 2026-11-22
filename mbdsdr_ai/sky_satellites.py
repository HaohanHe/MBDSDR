# SPDX-License-Identifier: MIT
"""卫星目标识别 —— 给定地平指向，回答视场里有哪些可见卫星。

与 :func:`mbdsdr_ai.sky_bodies.identify_bodies`（行星/月球）同一玩法：
输入是**地平坐标**指向（az 北=0 东=90，alt 地平=0，单位度）与视场直径，
输出落在视锥内、且在地平线以上的卫星，按与指向的角距从近到远排序。

位置计算全部复用既有实现，本模块不另造轨道：

* :func:`mbdsdr_ai.orbit.visible_satellites`（真 SGP4 + 现有 TLE 缓存）；
* 角距：``sky_interaction.angular_distance_deg``。

单位口径：站址高度对调用方是**米**，传给 ``visible_satellites`` 前
``/1000`` 换成**公里**（``orbit`` 的 ``observer_alt`` 是公里，传米会把站址
抬高 1000 倍）。

诚实语义：输入非法或视场内没有目标时返回 ``matched=False`` 与具体
``reason``，不拿最近邻硬凑；位置计算失败时保留失败原因，不吞成「没有
卫星」。纯函数（只调用注入的 ``visible_fn``），不 import Qt、不读盘。
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional

__all__ = ["identify_satellites", "visible_satellites"]

_NO_SATELLITE_REASON = "视场内没有可见卫星"


def visible_satellites(observer_lat: float, observer_lon: float,
                       observer_alt: float = 0.0,
                       min_elevation: float = 0.0,
                       satellite_type: str = "all",
                       when: Optional[float] = None) -> List[Dict[str, Any]]:
    """延迟转发到 :func:`mbdsdr_ai.orbit.visible_satellites`。

    作为模块级名字暴露，:func:`identify_satellites` 缺省就用它；测试 /
    嵌入方可按同名注入替身，不必改 ``orbit`` 或联网。
    """
    from . import orbit
    return orbit.visible_satellites(
        observer_lat, observer_lon, observer_alt=observer_alt,
        min_elevation=min_elevation, satellite_type=satellite_type,
        when=when)


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
        "satellites": [],
        "total_matched": 0,
        "truncated": False,
        "query": {"az_deg": az_deg, "alt_deg": alt_deg, "fov_deg": fov_deg},
        "reason": reason,
    }


def identify_satellites(
    az_deg: float,
    alt_deg: float,
    fov_deg: float,
    when_unix: float,
    lat_deg: float,
    lon_deg: float,
    limit: int = 3,
    *,
    observer_alt_m: float = 0.0,
    satellite_type: str = "all",
    visible_fn: Optional[Callable[..., List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """识别某个地平指向附近、地平线以上的可见卫星。

    Args:
        az_deg / alt_deg: 指向（度，北=0 东=90，alt 地平以上为正）。
        fov_deg: 视场直径（度）；只返回与指向角距 ``<= fov/2`` 的卫星。
        when_unix: 计算时刻（Unix UTC 秒），仿真/快转时必须传真实时刻。
        lat_deg / lon_deg: 观测站纬度/经度（度，北纬/东经为正）。
        limit: 最多返回几颗（按角距从近到远，默认 3）。
        observer_alt_m: 观测站海拔（**米**）；内部 ``/1000`` 换成公里。
        satellite_type: 传给 ``visible_satellites`` 的类别筛选。
        visible_fn: 位置查询函数，缺省用模块级 :func:`visible_satellites`；
            测试注入替身时不会联网/读盘。

    Returns:
        命中：``matched=True``、``satellites``（每项含 ``name`` / ``kind``
        / ``az_deg`` / ``alt_deg`` / ``angular_distance_deg`` / ``range_km``
        / ``range_rate_kms`` / ``altitude_km`` / ``sunlit``）、
        ``total_matched``、``truncated``、``query``。
        没有目标：``matched=False`` + ``reason``，不返回最近邻凑数。
        位置计算抛异常：``matched=False``，``reason`` 含「卫星位置计算失败」。
    """
    az = _as_finite(az_deg)
    alt = _as_finite(alt_deg)
    fov = _as_finite(fov_deg)
    when = _as_finite(when_unix)
    lat = _as_finite(lat_deg)
    lon = _as_finite(lon_deg)
    alt_m = _as_finite(observer_alt_m)

    if az is None or alt is None:
        return _invalid(az_deg, alt_deg, fov_deg,
                        "输入非法：az_deg / alt_deg 必须是有限数值")
    if not (-90.0 <= alt <= 90.0):
        return _invalid(az, alt, fov_deg,
                        f"输入非法：alt_deg 越界（{alt}°，合法 -90..90）")
    if fov is None or not (0.0 < fov <= 180.0):
        return _invalid(az, alt, fov_deg,
                        f"输入非法：fov_deg 必须在 (0, 180] 度内，收到 {fov_deg!r}")
    if when is None:
        return _invalid(az, alt, fov,
                        "输入非法：when_unix 必须是有限数值")
    if lat is None:
        return _invalid(az, alt, fov, "输入非法：lat_deg 必须是有限数值")
    if lon is None:
        return _invalid(az, alt, fov, "输入非法：lon_deg 必须是有限数值")
    if alt_m is None:
        return _invalid(az, alt, fov,
                        "输入非法：observer_alt_m 必须是有限数值")
    if isinstance(limit, bool):
        return _invalid(az, alt, fov,
                        f"输入非法：limit 必须是正整数，收到 {limit!r}")
    try:
        limit_n = int(limit)
    except (TypeError, ValueError):
        return _invalid(az, alt, fov,
                        f"输入非法：limit 必须是正整数，收到 {limit!r}")
    if limit_n <= 0 or limit_n != limit:
        return _invalid(az, alt, fov,
                        f"输入非法：limit 必须 > 0，收到 {limit!r}")

    from .sky_interaction import angular_distance_deg

    fn = visible_fn if visible_fn is not None else visible_satellites
    try:
        raw = fn(
            lat,
            lon,
            observer_alt=alt_m / 1000.0,
            min_elevation=0.0,
            satellite_type=satellite_type,
            when=when,
        )
        states = [] if raw is None else list(raw)
    except Exception as exc:  # noqa: BLE001 - 失败原因原样交给调用方
        return {
            "matched": False,
            "satellites": [],
            "total_matched": 0,
            "truncated": False,
            "query": {"az_deg": az, "alt_deg": alt, "fov_deg": fov,
                      "when_unix": when, "lat_deg": lat, "lon_deg": lon},
            "reason": f"卫星位置计算失败：{type(exc).__name__}: {exc}",
        }

    radius = 0.5 * fov
    hits: List[Dict[str, Any]] = []
    for state in states:
        if not isinstance(state, dict):
            continue
        elev = _as_finite(state.get("elevation"))
        sat_az = _as_finite(state.get("azimuth"))
        if elev is None or elev <= 0.0 or sat_az is None:
            continue
        name = state.get("satellite")
        if not isinstance(name, str) or not name.strip():
            continue
        dist = float(angular_distance_deg(sat_az, elev, az, alt))
        if dist > radius:
            continue
        hits.append({
            "name": name.strip(),
            "kind": "satellite",
            "az_deg": sat_az,
            "alt_deg": elev,
            "angular_distance_deg": dist,
            "range_km": _as_finite(state.get("range_km")),
            "range_rate_kms": _as_finite(state.get("range_rate_kms")),
            "altitude_km": _as_finite(state.get("altitude_km")),
            "sunlit": state.get("sunlit"),
        })

    hits.sort(key=lambda s: (s["angular_distance_deg"], s["name"]))
    total = len(hits)
    satellites = hits[:limit_n]

    return {
        "matched": bool(satellites),
        "satellites": satellites,
        "total_matched": total,
        "truncated": total > limit_n,
        "query": {"az_deg": az, "alt_deg": alt, "fov_deg": fov,
                  "when_unix": when, "lat_deg": lat, "lon_deg": lon},
        "reason": "" if satellites else _NO_SATELLITE_REASON,
    }
