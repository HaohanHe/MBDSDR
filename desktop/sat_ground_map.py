# SPDX-License-Identifier: MIT
"""卫星地面轨迹 / 覆盖圈 + 时空要素地图（复用公用 ``OsmMapView.add_layer``）。

本控件只做「把 :mod:`mbdsdr_ai.ground_track` 算好的点画到公用地图上」，
不重复任何轨道/椭球计算。图层：``sat_footprint``（覆盖圈）、
``sat_ground_track``（轨迹 + 星下点）、``spacetime_features``（地面对象 +
此刻圈内高亮 + 时间角标，见 :mod:`spacetime_map_layer`）。
轨迹跨日界线按段绘制，不横穿整图。

活的地面态势（2026-11-16）：
  * 多颗真实 TLE 卫星（:meth:`update_constellation`），覆盖圈/轨迹按
    :class:`~mbdsdr_ai.spacetime_features.TemporalBus` 广播时刻整体重算——
    时间一拖，圈跟着走；
  * 位置中枢（:mod:`mbdsdr_ai.position_service`）的 ADS-B 等真实移动目标
    自动进要素集合，没有数据源就一个点不画；
  * TLE 出处与新鲜度如实进摘要（celestrak / injected + 历元年龄），
    取不到真 TLE 就诚实空态，**不画合成根数**。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

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

from mbdsdr_ai.ground_track import (
    ground_track, satellite_footprint, satellite_situation)
from mbdsdr_ai.orbit import TLEUnavailableError
# 可见门只有一个来源（sat_passes）；不在地图控件里再写一份 5.0。
from mbdsdr_ai.sat_passes import VISIBLE_MIN_ALT_DEG

_t = _tok()

#: 单星摘要的既有格式（tests/test_sat_ground_map.py 钉住「覆盖半径」）。
_SINGLE_SUMMARY = ("星下点 {lat:.3f}°, {lon:.3f}° · "
                   "覆盖半径 {radius:.0f} km（仰角 ≥ {elev:.0f}°）")


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
        # 多星态势：每个元素是 ground_track.satellite_situation 的返回值。
        self._sats: List[Dict[str, Any]] = []
        self._tle_errors: List[str] = []
        self._last_params: Optional[Dict[str, Any]] = None
        self._busy = False
        self._unsubscribe_time = None
        self._unsubscribe_pos = None

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
        self._attach_buses()
        # Qt 对象销毁时自动退订（Python 侧 __del__ 不保证时机）。
        self.destroyed.connect(lambda *_: self.shutdown())

    # ---------------------------------------------------------------- 接线
    def _attach_buses(self) -> None:
        """订阅时间广播（圈随时刻重算）与位置中枢（真实移动目标进图）。"""
        try:
            from mbdsdr_ai.spacetime_features import get_temporal_bus
            self._unsubscribe_time = get_temporal_bus().subscribe(
                self._on_temporal_context)
        except Exception:  # pragma: no cover
            self._unsubscribe_time = None
        try:
            from mbdsdr_ai.position_service import get_position_service
            self._unsubscribe_pos = get_position_service().subscribe(
                self._on_position_report)
        except Exception:  # pragma: no cover
            self._unsubscribe_pos = None

    def shutdown(self) -> None:
        """退订所有总线（可重入；面板销毁时经 destroyed 自动调用）。"""
        for name in ("_unsubscribe_time", "_unsubscribe_pos"):
            fn = getattr(self, name, None)
            setattr(self, name, None)
            if fn is not None:
                try:
                    fn()
                except Exception:
                    pass
        layer = self.spacetime_layer
        self.spacetime_layer = None
        if layer is not None:
            try:
                layer.close()
            except Exception:
                pass

    def _on_temporal_context(self, ctx) -> None:
        """时间广播：按广播时刻整体重算覆盖圈/轨迹（不移动视图）。"""
        if self._busy or not self._last_params:
            return
        at = float(getattr(ctx, "at_unix", 0.0) or 0.0)
        if at <= 0.0:
            return  # 诚实空态广播（unknown）：不取「好看的时刻」重算
        params = dict(self._last_params)
        self._update_many(when_unix=at, recenter=False, **params)

    def _on_position_report(self, report) -> None:
        """位置中枢 → 要素集合：真实移动目标上图；没位置的报告不收。"""
        try:
            from mbdsdr_ai.position_service import get_position_service
            from mbdsdr_ai.spacetime_features import (
                get_ground_features, upsert_position_report)
            svc = get_position_service()
            upsert_position_report(
                report, track=tuple(svc.track(report.source)),
                ttl_s=svc.ttl_s, collection=get_ground_features())
            if self.spacetime_layer is not None:
                self.spacetime_layer.refresh()
            elif self.view is not None:
                self.view.update()
        except Exception:  # pragma: no cover - 上报异常不拖累地图
            pass

    def _emit_feature_detail(self, text: str, suggestion: str,
                             level: str) -> None:
        self.feature_detail.emit(text, suggestion, level)

    # ---------------------------------------------------------------- 数据
    def update_satellite(self, name: str,
                         when_unix: Optional[float] = None,
                         minutes: float = 90.0,
                         min_elevation_deg: float = VISIBLE_MIN_ALT_DEG,
                         recenter: bool = True) -> bool:
        """重算并重绘单星（多星入口是 :meth:`update_constellation`）。"""
        return self.update_constellation(
            [name], when_unix=when_unix, minutes=minutes,
            min_elevation_deg=min_elevation_deg, primary=name,
            recenter=recenter)

    def update_constellation(self, names: Sequence[str],
                             when_unix: Optional[float] = None,
                             minutes: float = 90.0,
                             min_elevation_deg: float = VISIBLE_MIN_ALT_DEG,
                             primary: Optional[str] = None,
                             tle_map: Optional[Dict[str, Tuple[str, str]]] = None,
                             recenter: bool = True) -> bool:
        """多颗真实 TLE 卫星的覆盖圈 + 轨迹一次算清；返回是否有任何一颗成功。

        * ``primary``：摘要与「圈内查询」用的主星（缺省第一颗成功的）；
        * ``tle_map``：测试/离线钉扎用的显式 TLE 注入（source 记 injected）；
        * 部分失败：画成功的，失败名单进 :attr:`error_text`，不吞不编；
        * 全部失败：诚实空态（单星旧契约：error 含异常名、summary 为空）。
        """
        return self._update_many(
            names=list(names), when_unix=when_unix, minutes=minutes,
            min_elevation_deg=min_elevation_deg, primary=primary,
            tle_map=dict(tle_map) if tle_map else None, recenter=recenter)

    def _update_many(self, names: List[str],
                     when_unix: Optional[float] = None,
                     minutes: float = 90.0,
                     min_elevation_deg: float = VISIBLE_MIN_ALT_DEG,
                     primary: Optional[str] = None,
                     tle_map: Optional[Dict[str, Tuple[str, str]]] = None,
                     recenter: bool = True) -> bool:
        self._busy = True
        try:
            return self._update_many_inner(
                names, when_unix, minutes, min_elevation_deg,
                primary, tle_map, recenter)
        finally:
            self._busy = False

    def _update_many_inner(self, names: List[str],
                           when_unix: Optional[float],
                           minutes: float,
                           min_elevation_deg: float,
                           primary: Optional[str],
                           tle_map: Optional[Dict[str, Tuple[str, str]]],
                           recenter: bool) -> bool:
        sats: List[Dict[str, Any]] = []
        errors: List[str] = []
        for name in names:
            try:
                sats.append(satellite_situation(
                    name, when_unix=when_unix, minutes=minutes,
                    min_elevation_deg=min_elevation_deg,
                    tle=(tle_map or {}).get(name)))
            except (TLEUnavailableError, KeyError, RuntimeError,
                    ValueError) as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}")

        self._sats = sats
        self._tle_errors = errors
        if not sats:
            # 诚实空态：写明原因，不画假轨迹（单星旧契约逐字保留）。
            self._track = {}
            self._footprint = {}
            self._error = "; ".join(errors) if errors else "未提供卫星名单"
            self._summary = ""
            self._publish_footprint(None, None)
            if self.view is not None:
                self.view.update()
            return False

        chosen = None
        if primary:
            for sit in sats:
                if sit["name"] == primary:
                    chosen = sit
                    break
        if chosen is None:
            chosen = sats[0]
        self._track = chosen["track"]
        self._footprint = chosen["footprint"]
        self._error = "; ".join(errors)
        self._publish_footprint(self._footprint, chosen["at_unix"])

        sub = self._footprint["subpoint"]
        summary = _SINGLE_SUMMARY.format(
            lat=sub.lat_deg, lon=sub.lon_deg,
            radius=self._footprint["radius_km"], elev=min_elevation_deg)
        if len(sats) > 1:
            summary += f" · {len(sats)} 星"
        summary += f" · TLE {_format_tle_state(sats)}"
        if errors:
            summary += f" · {len(errors)} 星取不到（{self._error[:80]}）"
        self._summary = summary

        self._last_params = {
            "names": list(names), "minutes": float(minutes),
            "min_elevation_deg": float(min_elevation_deg),
            "primary": primary or chosen["name"],
            "tle_map": dict(tle_map) if tle_map else None,
        }
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

    @property
    def constellation(self) -> Tuple[Dict[str, Any], ...]:
        """当前各星态势（只读快照），顺序与成功计算的名单一致。"""
        return tuple(self._sats)

    # ---------------------------------------------------------------- 绘制
    def _paint_footprint(self, painter: QPainter, project) -> None:
        base = QColor(_t.status_color("warning"))
        outline = QPen(base, 1.4, Qt.DashLine)
        fill = QColor(base)
        fill.setAlpha(38)
        painter.setPen(outline)
        painter.setBrush(QBrush(fill))
        for sit in self._sats:
            ring = sit["footprint"].get("ring") or []
            for chunk in _split_lonlat(ring, closed=True):
                if len(chunk) < 3:
                    continue
                poly = QPolygonF([project(lat, lon) for lat, lon in chunk])
                painter.drawPolygon(poly)
        painter.setBrush(Qt.NoBrush)

    def _paint_track(self, painter: QPainter, project) -> None:
        pen = QPen(QColor(_t.status_color("success")), 1.8)
        for sit in self._sats:
            segments: List[List[Any]] = sit["track"].get("segments") or []
            if segments:
                painter.setPen(pen)
                for seg in segments:
                    if len(seg) < 2:
                        continue
                    poly = QPolygonF([project(p.lat_deg, p.lon_deg)
                                      for p in seg])
                    painter.drawPolyline(poly)

        for sit in self._sats:
            sub = sit["footprint"].get("subpoint")
            if sub is None:
                continue
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


def _format_tle_state(sats: List[Dict[str, Any]]) -> str:
    """TLE 出处与新鲜度的人话摘要：只陈述事实，不美化。"""
    sources: Dict[str, int] = {}
    ages: List[str] = []
    for sit in sats:
        src = str(sit.get("tle_source") or "unknown")
        sources[src] = sources.get(src, 0) + 1
        fresh = sit.get("tle_freshness") or {}
        age = fresh.get("age_days")
        status = fresh.get("status") or "none"
        ages.append(f"{status}" if age is None else f"{status} {age:.1f}天")
    src_text = ", ".join(f"{k}×{v}" for k, v in sorted(sources.items()))
    return f"{src_text}（{'/'.join(ages)}）" if ages else src_text


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
