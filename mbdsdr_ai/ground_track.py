"""星下点 / 地面轨迹 / 覆盖圈（SGP4 -> TEME -> WGS-72）。

口径硬边界（与 :mod:`mbdsdr_ai.orbit` 一致，禁止接 WGS-84 米制链）：

- 传播：``sgp4`` 的 TEME，长度单位 **km**；
- 地固系：TEME 绕 z 轴转 -GMST；
- 星下点/覆盖圈：**WGS-72 椭球**，高度 km；
- 覆盖圈半径用 WGS-72 赤道半径做球面几何，返回值同时给出中心角与地面弧长。

覆盖圈几何（最小仰角 ε）::

    λ = acos(Re / (Re + h) * cos ε) - ε      # 星下点到覆盖边界的中心角
    r = λ * Re                                # 地表大圆弧长

``λ`` 是「绘制半径」的真值：地图上画的多边形顶点全部落在距星下点 ``λ``
的大圆上，因此几何解与绘制半径可逐点对照，而不是另设一个像素半径。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from .orbit import (
    BUILTIN_SATS,
    WGS72_A,
    _require_sgp4,
    fetch_tle,
    teme_to_ecef_km,
    wgs72_ecef_km_to_llh,
)

DEFAULT_TRACK_MINUTES = 90.0
DEFAULT_TRACK_STEP_S = 30.0
DEFAULT_MIN_ELEVATION_DEG = 5.0
DEFAULT_FOOTPRINT_POINTS = 72


@dataclass(frozen=True)
class Subpoint:
    """卫星星下点：WGS-72 经纬度(°) 与卫星相对椭球的高度(km)。"""

    lat_deg: float
    lon_deg: float
    sat_alt_km: float
    unix_s: float


def _unix_to_jd(unix_s: float) -> float:
    """Unix 秒 -> Julian Date (UTC)。"""
    return 2440587.5 + unix_s / 86400.0


def _catnr_for(name_or_catnr: Union[str, int]) -> int:
    if isinstance(name_or_catnr, int):
        return name_or_catnr
    key = str(name_or_catnr).strip().upper()
    for name, catnr in BUILTIN_SATS.items():
        if name.upper() == key:
            return catnr
    raise KeyError(f"未登记的内置卫星: {name_or_catnr!r}")


def _satrec(name_or_catnr: Union[str, int],
            tle: Optional[Tuple[str, str]] = None):
    """取 TLE 建 Satrec。TLE 可取内置目录或调用方直接注入（测试/离线）。"""
    _require_sgp4()
    from sgp4.api import Satrec  # 可选依赖，缺 sgp4 时由 _require_sgp4 先报
    if tle is None:
        line1, line2 = fetch_tle(_catnr_for(name_or_catnr))
    else:
        line1, line2 = tle
    sat = Satrec.twoline2rv(line1, line2)
    return sat


def _point_from_satrec(sat, unix_s: float) -> Subpoint:
    """单个时刻：Satrec -> TEME(km) -> ECEF(km) -> WGS-72 星下点。"""
    jd = _unix_to_jd(unix_s)
    err, r_teme, _v_teme = sat.sgp4(int(jd), jd - int(jd))
    if err != 0:
        raise RuntimeError(f"SGP4 传播失败 (error={err})")
    lat_deg, lon_deg, alt_km = wgs72_ecef_km_to_llh(
        *teme_to_ecef_km(r_teme, jd))
    return Subpoint(lat_deg, lon_deg, alt_km, unix_s)


def satellite_subpoint(name_or_catnr: Union[str, int],
                       when_unix: Optional[float] = None,
                       tle: Optional[Tuple[str, str]] = None) -> Subpoint:
    """当前（或指定时刻）星下点，WGS-72 + km。"""
    when = time.time() if when_unix is None else float(when_unix)
    return _point_from_satrec(_satrec(name_or_catnr, tle=tle), when)


def split_at_dateline(points: Sequence[Subpoint]) -> List[List[Subpoint]]:
    """按经度跳变 >180° 切段，避免轨迹在地图上横穿日界线。"""
    segments: List[List[Subpoint]] = []
    current: List[Subpoint] = []
    for point in points:
        if current and abs(point.lon_deg - current[-1].lon_deg) > 180.0:
            segments.append(current)
            current = []
        current.append(point)
    if current:
        segments.append(current)
    return segments


def ground_track(name_or_catnr: Union[str, int],
                 when_unix: Optional[float] = None,
                 minutes: float = DEFAULT_TRACK_MINUTES,
                 step_s: float = DEFAULT_TRACK_STEP_S,
                 tle: Optional[Tuple[str, str]] = None) -> Dict[str, Any]:
    """未来 ``minutes`` 分钟的地面轨迹（含日界线切段）。

    返回 ``points``（原始时序）与 ``segments``（可直接逐段画线）。
    默认 90 min 覆盖一圈 LEO 轨道，30 s 一个点。
    """
    if minutes <= 0 or step_s <= 0:
        raise ValueError("minutes 与 step_s 必须为正")
    start = time.time() if when_unix is None else float(when_unix)
    sat = _satrec(name_or_catnr, tle=tle)
    count = int(round(minutes * 60.0 / step_s))
    points = [_point_from_satrec(sat, start + i * step_s)
              for i in range(count + 1)]
    return {
        "points": points,
        "segments": split_at_dateline(points),
        "start_unix": start,
        "window_minutes": float(minutes),
        "step_s": float(step_s),
        "ellipsoid": "WGS-72",
        "unit": "km",
    }


def footprint_central_angle_deg(alt_km: float,
                                min_elevation_deg: float) -> float:
    """星下点到覆盖边界的中心角（度）。几何解，球面近似用 WGS-72 a。"""
    if alt_km < 0:
        raise ValueError("alt_km 不能为负")
    if not -90.0 < min_elevation_deg < 90.0:
        raise ValueError("min_elevation_deg 必须在 (-90, 90) 内")
    eps = math.radians(min_elevation_deg)
    ratio = WGS72_A / (WGS72_A + alt_km)
    cos_lam_plus_eps = max(-1.0, min(1.0, ratio * math.cos(eps)))
    lam = math.acos(cos_lam_plus_eps) - eps
    return math.degrees(max(0.0, lam))


def destination_point(lat_deg: float, lon_deg: float,
                      bearing_deg: float, angular_dist_deg: float
                      ) -> Tuple[float, float]:
    """球面正算：从一点沿方位角走给定中心角，返回 (lat, lon)。"""
    lat1 = math.radians(lat_deg)
    lon1 = math.radians(lon_deg)
    brg = math.radians(bearing_deg)
    ang = math.radians(angular_dist_deg)
    sin_lat2 = (math.sin(lat1) * math.cos(ang)
                + math.cos(lat1) * math.sin(ang) * math.cos(brg))
    lat2 = math.asin(max(-1.0, min(1.0, sin_lat2)))
    lon2 = lon1 + math.atan2(
        math.sin(brg) * math.sin(ang) * math.cos(lat1),
        math.cos(ang) - math.sin(lat1) * sin_lat2)
    return math.degrees(lat2), (math.degrees(lon2) + 180.0) % 360.0 - 180.0


def footprint_ring(lat_deg: float, lon_deg: float,
                   central_angle_deg: float,
                   points: int = DEFAULT_FOOTPRINT_POINTS
                   ) -> List[Tuple[float, float]]:
    """覆盖圈多边形顶点（闭合），每个顶点距中心恰好 ``central_angle_deg``。"""
    if points < 3:
        raise ValueError("覆盖圈至少需要 3 个顶点")
    ring = [destination_point(lat_deg, lon_deg, 360.0 * i / points,
                              central_angle_deg)
            for i in range(points)]
    ring.append(ring[0])
    return ring


def satellite_footprint(name_or_catnr: Union[str, int],
                        when_unix: Optional[float] = None,
                        min_elevation_deg: float = DEFAULT_MIN_ELEVATION_DEG,
                        points: int = DEFAULT_FOOTPRINT_POINTS,
                        tle: Optional[Tuple[str, str]] = None
                        ) -> Dict[str, Any]:
    """星下点 + 覆盖圈：按最小仰角门限算中心角、地面半径与多边形。"""
    sub = satellite_subpoint(name_or_catnr, when_unix=when_unix, tle=tle)
    angle_deg = footprint_central_angle_deg(sub.sat_alt_km, min_elevation_deg)
    return {
        "subpoint": sub,
        "ring": footprint_ring(sub.lat_deg, sub.lon_deg, angle_deg, points),
        "central_angle_deg": angle_deg,
        "radius_km": math.radians(angle_deg) * WGS72_A,
        "min_elevation_deg": float(min_elevation_deg),
        "ellipsoid": "WGS-72",
        "unit": "km",
    }
