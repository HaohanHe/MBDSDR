# SPDX-License-Identifier: MIT
"""卫星地面轨迹 / 覆盖圈地图（复用公用 ``OsmMapView.add_layer``）。

本控件只做「把 :mod:`mbdsdr_ai.ground_track` 算好的点画到公用地图上」，
不重复任何轨道/椭球计算。图层：``sat_footprint``（覆盖圈）、
``sat_ground_track``（轨迹 + 星下点）。轨迹跨日界线按段绘制，不横穿整图。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from tokens import tokens as _tok

try:  # 公用地图（缺 QtNetwork 时降级为提示，不影响面板其余功能）
    from osm_map import OsmMapView
except ImportError:  # pragma: no cover
    OsmMapView = None  # type: ignore

from mbdsdr_ai.ground_track import ground_track, satellite_footprint
from mbdsdr_ai.orbit import TLEUnavailableError

_t = _tok()


class SatelliteGroundMap(QWidget):
    """星下点 + 地面轨迹 + 覆盖圈的地图控件（默认 180px 高）。"""

    def __init__(self, parent: Optional[QWidget] = None,
                 online: bool = True):
        super().__init__(parent)
        self._track: Dict[str, Any] = {}
        self._footprint: Dict[str, Any] = {}
        self._error = ""
        self._summary = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if OsmMapView is None:  # pragma: no cover
            self.view = None
            layout.addWidget(QLabel("地图不可用（本机 Qt 缺 QtNetwork）", self))
            return
        self.view = OsmMapView(self, online=online)
        self.view.setMinimumHeight(180)
        self.view.add_layer("sat_footprint", self._paint_footprint)
        self.view.add_layer("sat_ground_track", self._paint_track)
        layout.addWidget(self.view)

    # ------------------------------------------------------------------ 数据
    def update_satellite(self, name: str,
                         when_unix: Optional[float] = None,
                         minutes: float = 90.0,
                         min_elevation_deg: float = 5.0,
                         recenter: bool = True) -> bool:
        """重算并重绘；返回是否成功（失败文本见 :attr:`error_text`）。"""
        try:
            track = ground_track(name, when_unix=when_unix, minutes=minutes)
            footprint = satellite_footprint(
                name, when_unix=track["start_unix"],
                min_elevation_deg=min_elevation_deg)
        except (TLEUnavailableError, KeyError, RuntimeError, ValueError) as exc:
            # 诚实空态：写明原因，不画假轨迹
            self._track = {}
            self._footprint = {}
            self._error = f"{type(exc).__name__}: {exc}"
            self._summary = ""
            if self.view is not None:
                self.view.update()
            return False

        self._track = track
        self._footprint = footprint
        self._error = ""
        sub = footprint["subpoint"]
        self._summary = (f"星下点 {sub.lat_deg:.3f}°, {sub.lon_deg:.3f}° · "
                         f"覆盖半径 {footprint['radius_km']:.0f} km"
                         f"（仰角 ≥ {min_elevation_deg:.0f}°）")
        if self.view is not None:
            if recenter:
                self.view.set_view(sub.lat_deg, sub.lon_deg, 2)
            self.view.update()
        return True

    @property
    def error_text(self) -> str:
        return self._error

    @property
    def summary_text(self) -> str:
        return self._summary

    # ------------------------------------------------------------------ 绘制
    def _paint_footprint(self, painter: QPainter, project) -> None:
        ring = self._footprint.get("ring") or []
        if len(ring) < 3:
            return
        base = QColor(_t.status_color("warning"))
        outline = QPen(base, 1.4, Qt.DashLine)
        fill = QColor(base)
        fill.setAlpha(38)
        painter.setPen(outline)
        painter.setBrush(QBrush(fill))
        for chunk in _split_lonlat(ring, closed=True):
            if len(chunk) < 3:
                continue
            poly = QPolygonF([project(lat, lon) for lat, lon in chunk])
            painter.drawPolygon(poly)
        painter.setBrush(Qt.NoBrush)

    def _paint_track(self, painter: QPainter, project) -> None:
        segments: List[List[Any]] = self._track.get("segments") or []
        if segments:
            pen = QPen(QColor(_t.status_color("success")), 1.8)
            painter.setPen(pen)
            for seg in segments:
                if len(seg) < 2:
                    continue
                poly = QPolygonF([project(p.lat_deg, p.lon_deg) for p in seg])
                painter.drawPolyline(poly)

        sub = self._footprint.get("subpoint")
        if sub is None:
            return
        pos = project(sub.lat_deg, sub.lon_deg)
        color = QColor(_t.status_color("danger"))
        painter.setPen(QPen(color, 1.5))
        painter.setBrush(QBrush(color))
        painter.drawEllipse(pos, 4.0, 4.0)
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(QPointF(pos.x() - 8.0, pos.y()),
                         QPointF(pos.x() + 8.0, pos.y()))
        painter.drawLine(QPointF(pos.x(), pos.y() - 8.0),
                         QPointF(pos.x(), pos.y() + 8.0))
        painter.setPen(QPen(color))
        painter.drawText(QPointF(pos.x() + 8.0, pos.y() - 8.0),
                         f"{sub.lat_deg:.2f}, {sub.lon_deg:.2f}")


def _split_lonlat(points: List[Tuple[float, float]],
                  closed: bool = False) -> List[List[Tuple[float, float]]]:
    """按经度跳变 >180° 切段；``closed`` 时去掉重复的闭合点再切。"""
    src = list(points)
    if closed and len(src) > 1 and src[0] == src[-1]:
        src = src[:-1]
    chunks: List[List[Tuple[float, float]]] = []
    current: List[Tuple[float, float]] = []
    for lat, lon in src:
        if current and abs(lon - current[-1][1]) > 180.0:
            chunks.append(current)
            current = []
        current.append((lat, lon))
    if current:
        current.append(current[0])
        chunks.append(current)
    return chunks
