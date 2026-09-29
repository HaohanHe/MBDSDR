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
from typing import Callable, Dict, List, Optional, Tuple

__all__ = ["PositionService", "PositionReport"]


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

    def subscribe(self, cb: Callable[[PositionReport], None]) -> None:
        self._subscribers.append(cb)

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
