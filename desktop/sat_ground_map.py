# SPDX-License-Identifier: MIT
"""卫星地面轨迹 / 覆盖圈 + 时空要素地图（复用公用 ``OsmMapView.add_layer``）。

本控件只做「把 :mod:`mbdsdr_ai.ground_track` 算好的点画到公用地图上」，
不重复任何轨道/椭球计算。图层：``sat_footprint``（覆盖圈）、
``sat_ground_track``（轨迹 + 星下点）、``spacetime_features``（地面对象 +
此刻圈内高亮 + 时间角标，见 :mod:`spacetime_map_layer`）。
轨迹跨日界线按段绘制，不横穿整图。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from tokens import tokens as _tok

try:  # 公用地图（缺 QtNetwork 时降级为提示，不影响面板其余功能）
    from osm_map import OsmMapView
except ImportError:  # pragma: no cover
    OsmMapView = None  # type: ignore

try:  # 时空要素层：PySide6 不全时静默降级，原地图功能不受影响
    from spacetime_map_layer import (
        SpacetimeFeatureLayer, footprint_from_satellite)
except ImportError:  # pragma: no cover
    try:
        from desktop.spacetime_map_layer import (  # type: ignore
            SpacetimeFeatureLayer, footprint_from_satellite)
    except ImportError:
        SpacetimeFeatureLayer = None  # type: ignore
        footprint_from_satellite = None  # type: ignore

from mbdsdr_ai.ground_track import ground_track, satellite_footprint
from mbdsdr_ai.orbit import TLEUnavailableError
# 可见门只有一个来源（sat_passes）；不在地图控件里再写一份 5.0。
from mbdsdr_ai.sat_passes import VISIBLE_MIN_ALT_DEG

_t = _tok()


class SatelliteGroundMap(QWidget):
    """星下点 + 地面轨迹 + 覆盖圈 + 时空要素的地图控件（默认 180px 高）。"""

    #: 点中地面对象时把详情交给上层（不自己塞控件）；参数 text/suggestion/level
    feature_detail = Signal(str, str, str)

    def __init__(self, parent: Optional[QWidget] = None,
                 online: bool = True):
        super().__init__(parent)
        self._track: Dict[str, Any] = {}
        self._footprint: Dict[str, Any] = {}
        self._error = ""
        self._summary = ""
        self.spacetime_layer = None

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
        if SpacetimeFeatureLayer is not None:
            try:
                from mbdsdr_ai.spacetime_features import get_ground_features
                self.spacetime_layer = SpacetimeFeatureLayer(
                    self.view, get_ground_features(),
                    detail_cb=self._emit_feature_detail)
            except Exception:  # pragma: no cover - 要素层缺失不拖累原地图
                self.spacetime_layer = None

    def _emit_feature_detail(self, text: str, suggestion: str,
                             level: str) -> None:
        self.feature_detail.emit(text, suggestion, level)

    # ------------------------------------------------------------------ 数据
    def update_satellite(self, name: str,
                         when_unix: Optional[float] = None,
                         minutes: float = 90.0,
                         min_elevation_deg: float = VISIBLE_MIN_ALT_DEG,
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
            self._publish_footprint(None, None)
            if self.view is not None:
                self.view.update()
            return False

        self._track = track
        self._footprint = footprint
        self._error = ""
        self._publish_footprint(footprint, track.get("start_unix"))
        sub = footprint["subpoint"]
        self._summary = (f"星下点 {sub.lat_deg:.3f}°, {sub.lon_deg:.3f}° · "
                         f"覆盖半径 {footprint['radius_km']:.0f} km"
                         f"（仰角 ≥ {min_elevation_deg:.0f}°）")
        if self.view is not None:
            if recenter:
                self.view.set_view(sub.lat_deg, sub.lon_deg, 2)
            self.view.update()
        return True

    def _publish_footprint(self, footprint: Optional[Dict[str, Any]],
                           when_unix: Optional[float]) -> None:
        """覆盖圈交给时空要素层做「此刻谁在圈内」的查询并画到画布上。

        面板可能先于主窗时间广播出现，故在 bus 尚无历史时按当前时间引擎
        发布一次（唯一时间事实源仍是 ``get_time_engine()``）。
        """
        layer = self.spacetime_layer
        if layer is None or footprint_from_satellite is None:
            return
        try:
            from mbdsdr_ai.new_spacetime import get_time_engine
            from mbdsdr_ai.spacetime_features import get_temporal_bus
            bus = get_temporal_bus()
            if bus.last is None:
                bus.publish_from_engine(get_time_engine())
            layer.set_footprint(
                footprint_from_satellite(footprint, when_unix=when_unix))
        except Exception:  # pragma: no cover - 要素层异常不拖累原地图
            pass

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
