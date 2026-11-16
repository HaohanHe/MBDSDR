# SPDX-License-Identifier: MIT
"""
mbdsdr_ai/position_service.py — 位置数据聚合 / 轨迹 / GeoJSON
=============================================================

对照 OpenWebRX ``owrx/map.py``：
  - ``updateLocation(source, loc, mode)``：按 source key 聚合最新位置；
  - ``removeOldPositions``：按 TTL 清理过期位置；
  - ``broadcast``：把增量推送给订阅者。

本模块额外提供：
  * 轨迹记录（同一 callsign/ICAO 的历史点串）；
  * GeoJSON FeatureCollection 输出（直接喂给前端地图）；
  * 不绑定具体解码器，ADS-B/APRS 都通过 :meth:`report_position` 写入。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

__all__ = ["PositionService", "PositionReport",
           "get_position_service", "reset_position_service",
           "report_from_aircraft", "ingest_aircraft"]


@dataclass
class PositionReport:
    source: str           # e.g. "ICAO123456" or "KJ6ABC-7"
    source_type: str      # "adsb" | "aprs"
    lat: float
    lon: float
    altitude_m: Optional[float] = None
    speed_mps: Optional[float] = None
    heading_deg: Optional[float] = None
    timestamp: float = 0.0
    metadata: Dict[str, str] = field(default_factory=dict)


class PositionService:
    """位置聚合 + 轨迹 + GeoJSON。"""

    def __init__(self, ttl_s: float = 120.0,
                 clock: Callable[[], float] = time.time,
                 max_track_points: int = 200):
        self._lock = threading.RLock()
        self._latest: Dict[str, PositionReport] = {}
        self._tracks: Dict[str, List[Tuple[float, float]]] = {}
        self._ttl_s = ttl_s
        self._clock = clock
        self._max_track = max_track_points
        self._subscribers: List[Callable[[PositionReport], None]] = []

    def subscribe(self, cb: Callable[[PositionReport], None]
                  ) -> Callable[[], None]:
        """订阅增量上报；返回退订函数（与 TemporalBus.subscribe 同约定）。"""
        self._subscribers.append(cb)

        def _unsubscribe() -> None:
            try:
                self._subscribers.remove(cb)
            except ValueError:
                pass

        return _unsubscribe

    @property
    def ttl_s(self) -> float:
        """位置有效期（秒）：过期未更新的源由 :meth:`remove_expired` 清除。"""
        return self._ttl_s

    def report(self, report: PositionReport) -> None:
        if report.timestamp == 0.0:
            report.timestamp = self._clock()
        with self._lock:
            self._latest[report.source] = report
            track = self._tracks.setdefault(report.source, [])
            track.append((report.lat, report.lon))
            if len(track) > self._max_track:
                del track[: len(track) - self._max_track]
        for cb in self._subscribers:
            try:
                cb(report)
            except Exception:
                pass

    def get(self, source: str) -> Optional[PositionReport]:
        with self._lock:
            return self._latest.get(source)

    def all_latest(self) -> List[PositionReport]:
        with self._lock:
            return list(self._latest.values())

    def track(self, source: str) -> List[Tuple[float, float]]:
        with self._lock:
            return list(self._tracks.get(source, []))

    def remove_expired(self) -> List[str]:
        now = self._clock()
        expired = []
        with self._lock:
            for k in list(self._latest):
                if now - self._latest[k].timestamp > self._ttl_s:
                    del self._latest[k]
                    expired.append(k)
        return expired

    def geojson(self) -> Dict:
        """导出 GeoJSON FeatureCollection。"""
        features = []
        with self._lock:
            for k, r in self._latest.items():
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [r.lon, r.lat]},
                    "properties": {
                        "source": r.source, "type": r.source_type,
                        "altitude_m": r.altitude_m, "speed_mps": r.speed_mps,
                        "heading_deg": r.heading_deg, "timestamp": r.timestamp,
                        **r.metadata,
                    },
                })
            # 轨迹 LineString
            for k, pts in self._tracks.items():
                if len(pts) >= 2:
                    features.append({
                        "type": "Feature",
                        "geometry": {"type": "LineString",
                                     "coordinates": [[lon, lat] for lat, lon in pts]},
                        "properties": {"source": k, "role": "track"},
                    })
        return {"type": "FeatureCollection", "features": features}

    def count(self) -> int:
        with self._lock:
            return len(self._latest)


# ---------------------------------------------------------------------------
# 进程级单例：与 get_time_engine / get_ground_features 同约定。
# ---------------------------------------------------------------------------
_global_service: Optional[PositionService] = None


def get_position_service() -> PositionService:
    global _global_service
    if _global_service is None:
        _global_service = PositionService()
    return _global_service


def reset_position_service() -> PositionService:
    """测试隔离：换一个干净单例并返回它（仿 desktop.iq_bus.reset_iq_bus）。"""
    global _global_service
    _global_service = PositionService()
    return _global_service


_FT_TO_M = 0.3048
_KT_TO_MPS = 0.514444


def report_from_aircraft(ac: Any) -> Optional[PositionReport]:
    """``adsb_map.Aircraft`` → :class:`PositionReport`（纯换算，不带副作用）。

    没有配对出位置（lat/lon 为 None）时返回 ``None``——不造坐标。
    单位口径：ft→m、kt→m/s、航向 = 地速航向 track_deg。
    """
    lat = getattr(ac, "lat", None)
    lon = getattr(ac, "lon", None)
    if lat is None or lon is None:
        return None
    alt_ft = getattr(ac, "altitude_ft", None)
    gs_kt = getattr(ac, "groundspeed_kt", None)
    meta: Dict[str, str] = {}
    cs = (getattr(ac, "callsign", "") or "").strip()
    if cs:
        meta["callsign"] = cs
    return PositionReport(
        source=str(getattr(ac, "icao_hex", "")).strip().upper() or f"{lat},{lon}",
        source_type="adsb",
        lat=float(lat),
        lon=float(lon),
        altitude_m=None if alt_ft is None else float(alt_ft) * _FT_TO_M,
        speed_mps=None if gs_kt is None else float(gs_kt) * _KT_TO_MPS,
        heading_deg=getattr(ac, "track_deg", None),
        timestamp=float(getattr(ac, "last_seen", 0.0) or 0.0),
        metadata=meta,
    )


def ingest_aircraft(ac: Any,
                    service: Optional[PositionService] = None
                    ) -> Optional[PositionReport]:
    """跟踪器出口：合并出位置就上报到位置中枢，返回本次报告（没位置返 None）。

    **同位置去重**：位置没动就不重复上报——否则每个识别帧都会往轨迹里
    塞同一个点，轨迹被刷屏（航向/高度更新等下一次位置解算再带）。任何
    异常就地吞掉，绝不拖累解码合并主链。
    """
    report = report_from_aircraft(ac)
    if report is None:
        return None
    svc = service or get_position_service()
    prev = svc.get(report.source)
    if prev is not None and prev.lat == report.lat and prev.lon == report.lon:
        return None
    svc.report(report)
    return report


def report_from_aprs(parsed: Dict[str, Any],
                     now: Optional[float] = None) -> Optional[PositionReport]:
    """``aprs_parser.parse_aprs_frame`` 的输出 dict → :class:`PositionReport`。

    没有解出位置（latitude/longitude 为 None）返回 ``None``——不造坐标。
    单位口径（APRS 事实）：速度=节→m/s、高度=英尺→m、course=度。
    源标识 = 呼号[-SSID]（如 ``KJ6ABC-7``，与 PositionReport 文档一致）。
    """
    lat = parsed.get("latitude")
    lon = parsed.get("longitude")
    if lat is None or lon is None:
        return None
    call = str(parsed.get("source") or "").strip()
    if not call:
        call = f"{lat},{lon}"
    ssid = parsed.get("source_ssid")
    if ssid:
        call = f"{call}-{int(ssid)}"
    speed = parsed.get("speed")
    alt = parsed.get("altitude")
    course = parsed.get("course")
    meta: Dict[str, str] = {"protocol": "aprs"}
    if parsed.get("symbol"):
        meta["symbol"] = str(parsed["symbol"])
    return PositionReport(
        source=call,
        source_type="aprs",
        lat=float(lat),
        lon=float(lon),
        altitude_m=None if alt is None else float(alt) * _FT_TO_M,
        speed_mps=None if speed is None else float(speed) * _KT_TO_MPS,
        heading_deg=None if course is None else float(course),
        timestamp=float(time.time() if now is None else now),
        metadata=meta,
    )


def ingest_aprs_position(parsed: Dict[str, Any],
                         service: Optional[PositionService] = None,
                         now: Optional[float] = None
                         ) -> Optional[PositionReport]:
    """APRS 解析出口：解出位置就上报到位置中枢（同位置去重）。

    与 ``ingest_aircraft`` 同一条口径：位置没动不重复刷轨迹；异常就地
    吞掉，绝不拖累解码主链。
    """
    report = report_from_aprs(parsed, now=now)
    if report is None:
        return None
    svc = service or get_position_service()
    prev = svc.get(report.source)
    if prev is not None and prev.lat == report.lat and prev.lon == report.lon:
        return None
    svc.report(report)
    return report
