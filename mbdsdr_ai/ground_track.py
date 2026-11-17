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
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from .orbit import (
    BUILTIN_SATS,
    WGS72_A,
    _engine_now_unix,
    _require_sgp4,
    fetch_tle,
    teme_to_ecef_km,
    wgs72_ecef_km_to_llh,
)
# 覆盖圈「最小仰角」就是全仓唯一可见门，不在本地再写一份 5.0。
# 改 sat_passes.VISIBLE_MIN_ALT_DEG 后，覆盖圈/地图/面板一起变。
from .sat_passes import VISIBLE_MIN_ALT_DEG

DEFAULT_TRACK_MINUTES = 90.0
DEFAULT_TRACK_STEP_S = 30.0
# 轨迹折线误差界（默认 30 s 步长）。取数口径：以 1 s 星下点为参照，把相邻
# 30 s 采样点按大圆短弧连线，算每个 1 s 真位置到所在弦的最大大圆距离
# （= 折线相对真轨迹的偏差）。实测 ISS（2026-10-24T06:00Z 起 24 h，每 2 h
# 取一段 90 min 轨迹的最大值）：20 s 0.0407 km / 30 s 0.0916 km /
# 60 s 0.3666 km，随步长平方增长。0.10 km 是 30 s 实测值的向上取整，
# 不是另设的物理阈值；判据见 tests/test_ground_track_step_bound.py。
DEFAULT_TRACK_STEP_MAX_CHORD_KM = 0.10
DEFAULT_TRACK_REF_STEP_S = 1.0
DEFAULT_MIN_ELEVATION_DEG = VISIBLE_MIN_ALT_DEG
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
    """当前（或指定时刻）星下点，WGS-72 + km。

    缺省时刻取 :func:`mbdsdr_ai.orbit._engine_now_unix`（全链路唯一时间源）；
    时间机器把引擎拨走之后，地图必须画那一刻的星下点，而不是墙钟时刻。
    """
    when = _engine_now_unix() if when_unix is None else float(when_unix)
    return _point_from_satrec(_satrec(name_or_catnr, tle=tle), when)


def split_at_dateline(points: Sequence[Subpoint]) -> List[List[Subpoint]]:
    """按经度跳变 >180° 切段，并在 ±180° 处插入边界点。

    只切段不插点会在日界线两侧各留最多半个步长的缺口（30 s ISS 约
    200 km），地图上表现为轨迹在地图边缘"断头"。插值只用于边界点：
    纬度/高度/时刻按线性插值，经度直接取 ±180。
    """
    segments: List[List[Subpoint]] = []
    current: List[Subpoint] = []
    for point in points:
        if current and abs(point.lon_deg - current[-1].lon_deg) > 180.0:
            prev = current[-1]
            # 先取最短路径的有符号经度差，再判断是往 +180 还是 -180 穿。
            # 不能只比大小：-179 → +179 是向西穿 -180（差 358°，实际 -2°）。
            delta = point.lon_deg - prev.lon_deg
            if delta > 180.0:
                delta -= 360.0
            elif delta < -180.0:
                delta += 360.0
            edge = 180.0 if delta > 0.0 else -180.0
            t = 0.0 if delta == 0.0 else (edge - prev.lon_deg) / delta
            t = min(max(t, 0.0), 1.0)
            lat_b = prev.lat_deg + t * (point.lat_deg - prev.lat_deg)
            alt_b = prev.sat_alt_km + t * (point.sat_alt_km - prev.sat_alt_km)
            when_b = prev.unix_s + t * (point.unix_s - prev.unix_s)
            current.append(Subpoint(lat_b, edge, alt_b, when_b))
            segments.append(current)
            current = [Subpoint(lat_b, -edge, alt_b, when_b)]
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
    start = _engine_now_unix() if when_unix is None else float(when_unix)
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


def to_geojson(track: Dict[str, Any],
               source: str = "ground_track") -> Dict[str, Any]:
    """把 :func:`ground_track` 的结果转成 GeoJSON FeatureCollection。

    坐标序固定 ``[lon, lat]``（RFC 7946），与
    :meth:`mbdsdr_ai.position_service.PositionService.geojson` 同一口径，
    QGIS 可直接打开。日界线切段后**每段一条 LineString**——不画一条
    横穿整个地图的假线（那是 180° 跳变，不是卫星真的绕回来）。
    """
    features: List[Dict[str, Any]] = []
    for seg in track.get("segments") or []:
        if len(seg) < 2:
            continue
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[float(p.lon_deg), float(p.lat_deg)]
                                for p in seg],
            },
            "properties": {
                "source": source,
                "role": "ground_track",
                "start_unix": float(seg[0].unix_s),
                "end_unix": float(seg[-1].unix_s),
                "ellipsoid": track.get("ellipsoid"),
                "unit": track.get("unit"),
                "window_minutes": track.get("window_minutes"),
                "step_s": track.get("step_s"),
            },
        })
    return {"type": "FeatureCollection", "features": features}


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


def resolve_tle_lines(name_or_catnr: Union[str, int]
                      ) -> Tuple[Tuple[str, str], str]:
    """真 TLE 取数，返回 ``((line1, line2), source)``。

    取数走本模块 :data:`fetch_tle`（测试注入点不变）。``fetch_tle`` 返回的
    都是 celestrak 真数据（联网或其磁盘/内存缓存），故 source 如实记
    "celestrak"；取不到就抛 :class:`TLEUnavailableError`——**绝不回退合成
    根数**，地图不画假轨迹（这是既有钉扎契约，不是新规矩）。
    """
    lines = fetch_tle(_catnr_for(name_or_catnr))
    return lines, "celestrak"


def satellite_situation(name_or_catnr: Union[str, int],
                        when_unix: Optional[float] = None,
                        minutes: float = DEFAULT_TRACK_MINUTES,
                        min_elevation_deg: float = DEFAULT_MIN_ELEVATION_DEG,
                        points: int = DEFAULT_FOOTPRINT_POINTS,
                        tle: Optional[Tuple[str, str]] = None,
                        tle_source: Optional[str] = None) -> Dict[str, Any]:
    """一颗星在指定时刻的地面态势：覆盖圈 + 轨迹 + TLE 出处与新鲜度。

    * ``tle`` 注入时（测试/钉扎）source 记 "injected"，不冒充联网；
    * 未注入时经 :func:`resolve_tle_lines` 取真 TLE；
    * 新鲜度走 :func:`mbdsdr_ai.new_spacetime_tle.tle_freshness`（年龄的
      唯一实现），解析不了就诚实返回 ``status: "none"``，不编历元。
    """
    if tle is None:
        tle, tle_source = resolve_tle_lines(name_or_catnr)
    elif tle_source is None:
        tle_source = "injected"
    footprint = satellite_footprint(name_or_catnr, when_unix=when_unix,
                                    min_elevation_deg=min_elevation_deg,
                                    points=points, tle=tle)
    track = ground_track(name_or_catnr, when_unix=when_unix,
                         minutes=minutes, tle=tle)
    try:
        from .new_spacetime_tle import tle_freshness
        fresh = tle_freshness(tle[0])
    except Exception:  # pragma: no cover - 新鲜度算不出不拖累态势
        fresh = {"status": "none", "epoch_utc": None, "age_days": None}
    return {
        "name": str(name_or_catnr),
        "footprint": footprint,
        "track": track,
        "tle_source": tle_source,
        "tle_freshness": fresh,
        "at_unix": footprint["subpoint"].unix_s,
    }
