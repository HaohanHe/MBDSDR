# SPDX-License-Identifier: MIT
"""把时空要素**画在地图上**（复用公用 ``OsmMapView.add_layer``）。

交付物在画布上，不在抽屉里：地面对象点、历史轨迹、此刻在覆盖圈内的高亮、
以及带模拟态标注的时间角标。时间一跳，:class:`TemporalBus` 广播新
``TemporalContext``，本图层重算圈内集合并 ``update()``，地图跟着变。

红线：

- 只走 ``add_layer(name, cb(painter, project))``，不新增 QWidget、
  不改主界面布局、不碰天空投影。
- 不内置任何默认站点/信标/干扰源；没要素就显示诚实空态。
- 没拿到覆盖圈或没时间数据时显示「未设定时间范围 · 不推断」，
  **绝不画一个看似合理的圈或时刻**。
- ODbL 署名仍由 ``OsmMapView`` 统一负责，本图层不重复画。
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF

from mbdsdr_ai.spacetime_features import (
    FeatureCollection, FeatureState, Footprint, QueryResult, SpatialFeature,
    TemporalBus, TemporalContext, TemporalMode, get_temporal_bus,
    great_circle_angle_deg,
)

__all__ = ["SpacetimeFeatureLayer", "LAYER_NAME", "footprint_from_satellite"]

LAYER_NAME = "spacetime_features"

#: 状态 -> (边线色, 半径, 是否实心)。只用几何图形，不用会回退的符号字体。
_STYLE: Dict[str, Tuple[str, float, bool]] = {
    "inside": ("#ff5f56", 5.5, True),
    "shown": ("#ffb347", 3.5, True),
    "outside": ("#8b94a5", 3.0, False),
    "expired": ("#6b7280", 2.5, False),
    "not_yet": ("#6b7280", 2.5, False),
    "unknown": ("#6b7280", 3.0, False),
}


def footprint_from_satellite(footprint: Dict[str, Any],
                             when_unix: Optional[float] = None) -> Optional[Footprint]:
    """把 ``ground_track.satellite_footprint`` 的结果转成查询用的覆盖圈。

    只取真值字段：星下点经纬高、中心角、时刻。ring 是绘制用的顶点序列，
    查询走球面中心角，避免跨日界线切段后多边形漏判。
    """
    if not footprint:
        return None
    sub = footprint.get("subpoint")
    if sub is None:
        return None
    angle = footprint.get("central_angle_deg")
    if angle is None:
        return None
    return Footprint(
        lat_deg=float(sub.lat_deg),
        lon_deg=float(sub.lon_deg),
        central_angle_deg=float(angle),
        at_unix=when_unix)


def _split_track(points: Sequence[Tuple[float, float]]
                 ) -> List[List[Tuple[float, float]]]:
    """按经度跳变 >180° 切段，避免轨迹横穿整张地图。"""
    chunks: List[List[Tuple[float, float]]] = []
    current: List[Tuple[float, float]] = []
    for lat, lon in points:
        if current and abs(lon - current[-1][1]) > 180.0:
            chunks.append(current)
            current = []
        current.append((lat, lon))
    if current:
        chunks.append(current)
    return [c for c in chunks if len(c) >= 2]


class SpacetimeFeatureLayer:
    """地图上的时空要素层。构造即向公用地图注册一个绘制层。"""

    def __init__(self, view: Any,
                 collection: Optional[FeatureCollection] = None,
                 *, bus: Optional[TemporalBus] = None,
                 detail_cb: Optional[Callable[[str, str, str], None]] = None,
                 layer_name: str = LAYER_NAME) -> None:
        self.view = view
        self.collection = collection or FeatureCollection()
        self.bus = bus or get_temporal_bus()
        self._detail_cb = detail_cb
        self._layer_name = layer_name
        self._footprint: Optional[Footprint] = None
        self._result: Optional[QueryResult] = None
        self._ctx: Optional[TemporalContext] = None
        self._picked_id: Optional[str] = None

        self._colors = self._resolve_colors()
        view.add_layer(layer_name, self._paint)
        self._unsubscribe = self.bus.subscribe(self._on_context)
        if self._ctx is None:
            # 订阅时没有历史广播：不造时刻，保持诚实空态。
            self._result = self.collection.query_at(
                0.0, None, mode=TemporalMode.RANGE_FILTER,
                has_time_data=False)
        if hasattr(view, "picked"):
            try:
                view.picked.connect(self.on_picked)
            except Exception:
                pass

    # ---------------------------------------------------------------- 配色
    @staticmethod
    def _resolve_colors() -> Dict[str, QColor]:
        out = {k: QColor(v[0]) for k, v in _STYLE.items()}
        try:  # 与全仓主题 token 对齐；拿不到时用上面的兜底色
            from tokens import tokens as _tok
            t = _tok()
            mapping = {
                "inside": "danger", "shown": "warning",
                "outside": "info", "expired": "info",
                "not_yet": "info", "unknown": "info",
            }
            for state, kind in mapping.items():
                c = QColor(t.status_color(kind))
                if c.isValid():
                    out[state] = c
        except Exception:
            pass
        return out

    # ---------------------------------------------------------------- 数据
    @property
    def result(self) -> Optional[QueryResult]:
        return self._result

    @property
    def context(self) -> Optional[TemporalContext]:
        return self._ctx

    def set_collection(self, collection: FeatureCollection) -> None:
        self.collection = collection
        self.refresh()

    def add_feature(self, feature: SpatialFeature) -> None:
        self.collection.upsert(feature)
        self.refresh()

    def set_footprint(self, footprint: Optional[Footprint]) -> None:
        """由卫星地图在 ``update_satellite`` 成功后调用。"""
        self._footprint = footprint
        self.refresh()

    def _on_context(self, ctx: TemporalContext) -> None:
        self._ctx = ctx
        self.refresh()

    def refresh(self) -> QueryResult:
        """按当前广播量重算查询结果并请求重绘。"""
        ctx = self._ctx or self.bus.last
        if ctx is None or not ctx.has_time_data:
            self._ctx = ctx
            self._result = self.collection.query_at(
                0.0, self._footprint, mode=TemporalMode.RANGE_FILTER,
                basis="unknown", has_time_data=False)
        else:
            self._result = self.collection.query_at(
                ctx.at_unix, self._footprint, mode=ctx.mode,
                basis=ctx.basis, has_time_data=True)
        if hasattr(self.view, "update"):
            self.view.update()
        return self._result

    def close(self) -> None:
        """解绑（面板销毁时调用），避免信号挂着已释放的对象。"""
        try:
            self._unsubscribe()
        except Exception:
            pass
        try:
            if hasattr(self.view, "picked"):
                self.view.picked.disconnect(self.on_picked)
        except Exception:
            pass
        if hasattr(self.view, "remove_layer"):
            self.view.remove_layer(self._layer_name)

    # ---------------------------------------------------------------- 交互
    def pick(self, lat: float, lon: float,
             max_angle_deg: float = 2.0) -> Optional[FeatureState]:
        """返回离点击处最近的要素（超过阈值返回 None）。"""
        if self._result is None:
            return None
        best: Optional[FeatureState] = None
        best_angle = max_angle_deg
        for item in self._result.items:
            d = great_circle_angle_deg(lat, lon,
                                       item.feature.lat, item.feature.lon)
            if d <= best_angle:
                best, best_angle = item, d
        return best

    def on_picked(self, lat: float, lon: float) -> Optional[FeatureState]:
        """接 ``OsmMapView.picked``：点中要素就报详情，没点中不装作有。"""
        item = self.pick(lat, lon)
        if item is None:
            return None
        self._picked_id = item.feature.feature_id
        if self._detail_cb is not None:
            f = item.feature
            text = (f"{f.label or f.feature_id} · {f.kind} · "
                    f"{item.state} · {item.reason}")
            level = "info" if item.is_highlighted else "warning"
            try:
                self._detail_cb(text, self._detail_suggestion(item), level)
            except Exception:
                pass
        if hasattr(self.view, "update"):
            self.view.update()
        return item

    @staticmethod
    def _detail_suggestion(item: FeatureState) -> str:
        f = item.feature
        bits = [f"位置 {f.lat:.4f}°, {f.lon:.4f}°",
                f"有效期 {f.valid.describe()}"]
        if f.properties:
            bits.append("；".join(f"{k}={v}" for k, v in f.properties.items()))
        if item.state == "unknown":
            bits.append("原因未明，不做推断")
        return " · ".join(bits)

    # ---------------------------------------------------------------- 绘制
    def _paint(self, painter: QPainter, project: Callable) -> None:
        result = self._result
        if result is None:
            return
        painter.save()
        try:
            self._draw_tracks(painter, project, result)
            self._draw_points(painter, project, result)
            self._draw_badge(painter, result)
        finally:
            painter.restore()

    def _draw_tracks(self, painter: QPainter, project: Callable,
                     result: QueryResult) -> None:
        for item in result.items:
            chunks = _split_track(item.feature.track)
            if not chunks:
                continue
            c = QColor(self._colors.get(item.state, QColor("#8b94a5")))
            c.setAlpha(150 if item.is_highlighted else 80)
            painter.setPen(QPen(c, 1.2, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            for chunk in chunks:
                poly = QPolygonF([project(lat, lon) for lat, lon in chunk])
                painter.drawPolyline(poly)

    def _draw_points(self, painter: QPainter, project: Callable,
                     result: QueryResult) -> None:
        for item in result.items:
            f = item.feature
            pos = project(f.lat, f.lon)
            color = QColor(self._colors.get(item.state, QColor("#8b94a5")))
            _key, radius, filled = _STYLE.get(
                item.state, ("#8b94a5", 3.0, False))
            if item.is_highlighted and item.state == "inside":
                halo = QColor(color)
                halo.setAlpha(70)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(halo))
                painter.drawEllipse(pos, radius + 6.0, radius + 6.0)
            painter.setPen(QPen(color, 1.4))
            painter.setBrush(QBrush(color) if filled else Qt.NoBrush)
            painter.drawEllipse(pos, radius, radius)
            if item.state in ("unknown", "expired", "not_yet"):
                # 叉号用几何线，不依赖会回退的符号字体。
                d = radius * 0.7
                painter.drawLine(QPointF(pos.x() - d, pos.y() - d),
                                 QPointF(pos.x() + d, pos.y() + d))
                painter.drawLine(QPointF(pos.x() - d, pos.y() + d),
                                 QPointF(pos.x() + d, pos.y() - d))
            if (item.feature.feature_id == self._picked_id
                    or item.state == "inside"):
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(color, 1.0, Qt.DotLine))
                painter.drawEllipse(pos, radius + 3.0, radius + 3.0)
            # 逐点文字标签在低缩放级别会叠成一团；名称改在角标里列，
            # 点选后的完整详情走上层提示行。

    def _draw_badge(self, painter: QPainter, result: QueryResult) -> None:
        """左上角状态角标：时间基准 + 计数 + 诚实空态。画在画布上，不加控件。"""
        if not hasattr(self.view, "width"):
            return
        lines = [result.summary()]
        if result.has_time_data:
            import datetime
            try:
                stamp = datetime.datetime.fromtimestamp(
                    result.at_unix, datetime.timezone.utc
                ).strftime("%m-%d %H:%M UTC")
            except (OverflowError, OSError, ValueError):
                stamp = "时刻不可表示"
            tag = {"real": "实时", "simulated": "模拟时刻",
                   "replay": "回放", "unknown": "时间基准未知"}.get(
                       result.basis, "时间基准未知")
            lines.insert(0, f"{tag} · {stamp}")
        else:
            lines.insert(0, "未设定时间范围 · 不推断")
        if result.gaps:
            lines.append(f"数据空洞 {len(result.gaps)} 段 · 未插值")
        age = result.footprint_age_s
        if age is not None and abs(age) > 60.0:
            # 覆盖圈是面板按某个时刻算好的；时间跳了不等于圈重算过。
            # 明说，别让「模拟时刻 +20 天」配一个旧的圈冒充新结果。
            lines.append(
                f"覆盖圈早于当前 {abs(age) / 60.0:.0f} 分钟 · 未随时重算")
        names = [i.feature.label or i.feature.feature_id
                 for i in result.inside]
        if names:
            joined = " / ".join(names)
            if len(joined) > 42:
                joined = joined[:39] + "..."
            lines.append("圈内：" + joined)

        # 左上角 y=8..30 是 OsmMapView 自己的状态 HUD（暗色 zN），
        # 角标从 40 起画，避免两层文字互相压住。
        top = 40.0
        font = QFont(painter.font())
        font.setPointSizeF(max(8.0, font.pointSizeF() - 1.0))
        painter.setFont(font)
        fm = painter.fontMetrics()
        w = max(fm.horizontalAdvance(s) for s in lines) + 16
        h = fm.height() * len(lines) + 12
        rect = QRectF(8.0, top, w, h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(12, 16, 24, 190)))
        painter.drawRoundedRect(rect, 4.0, 4.0)
        painter.setPen(QPen(QColor("#d6dbe4")))
        for i, line in enumerate(lines):
            painter.drawText(
                QPointF(16.0, top + fm.ascent() + i * fm.height() + 6), line)
