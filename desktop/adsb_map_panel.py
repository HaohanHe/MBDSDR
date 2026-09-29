# SPDX-License-Identifier: MIT
"""
MBDSDR 桌面端 - ADS-B 实时地图面板 (AdsbMapPanel)
==================================================

原生 QPainter 绘制，离线等距圆柱投影底图：

  * 左侧 ``_MapCanvas``：米白背景 + 30° 经纬网格 + 简化大陆轮廓
    （来自 ``mbdsdr_ai.adsb_map.WORLD_LAND_POLYGONS``）+ 实时飞机点。
  * 右侧 ``QTableWidget``：呼号 / 气压高度(ft) / 到观察者距离(km)。

数据链路（只画真实解算出来的位置，绝不臆造坐标）：
  - 外部把 ``ADSBDecoder.handle(raw)`` 返回的 dict 列表喂给
    :meth:`AdsbMapPanel.update_aircraft`；内部用 ``AircraftTracker`` 按 ICAO
    合并呼号 / 高度 / 速度 / 最新位置。
  - 只有 ``lat`` 和 ``lon`` 同时有效的飞机才会被画到图上、写进列表；
    没有任何位置帧时画布中央显示空态提示。
  - 距离由 ``set_observer(lat, lon)`` 设定本站坐标后，用 haversine 计算；
    未设观察者时距离列显示 ``--``。

红线：
  - 不联网取瓦片、不内置任何默认飞机、不硬编码真实呼号。
  - 无数据时画布显空态文字，不画假点、不造假距离。

配色（与 themes.DEFAULT_LIGHT 一致）：
  米白 #F5F3EF / 蓝灰 #5B7B8C / 橙 #C4845C / 网格浅米灰 #D8D2C8。
"""

from __future__ import annotations

import math
import os
import sys
from typing import List, Optional, Tuple

# desktop 包无 __init__.py，靠 repo root 在 sys.path 上完成导入。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt, QPointF, QRectF  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QBrush, QColor, QFont, QPainter, QPen, QPolygonF,
)
from PySide6.QtWidgets import (  # noqa: E402
    QHBoxLayout, QHeaderView, QSizePolicy, QTableWidget, QTableWidgetItem,
    QWidget,
)

from themes import get_theme, DEFAULT_THEME  # noqa: E402

from tokens import tokens as _tok  # noqa: E402

# 触屏手势：双指捏合缩放 + 单指拖动平移地图（Surface/平板）。
try:  # noqa: E402
    from touch_helpers import PinchZoomMixin, enable_touch_events
except Exception:  # pragma: no cover
    PinchZoomMixin = object  # type: ignore
    enable_touch_events = lambda w: None  # noqa: E731

from mbdsdr_ai.adsb_map import (  # noqa: E402
    WORLD_LAND_POLYGONS,
    AircraftTracker,
    haversine_km,
    project_equirectangular,
)

_t = _tok()
# CarWith dark_car 画布配色（地图底/文字/强调/网格/陆地填充）
_BG = QColor(_t.COLORS["card_1"])
_TEXT = QColor(_t.COLORS["gray_100"])
_ACCENT = QColor(_t.COLORS["accent"])
_GRID = QColor(_t.COLORS["gray_400"])
_LAND_FILL = QColor(_t.COLORS["card_2"])

# 空态提示文案（测试据此断言画布进入“无数据”状态）。
ADSB_EMPTY_TEXT = "无 ADS-B 飞机（需 1090MHz）"


class _MapCanvas(PinchZoomMixin, QWidget):
    """纯 QPainter 绘制的等距圆柱投影地图画布。

    触屏（Surface/平板）：PinchZoomMixin 双指捏合缩放地图、单指拖动平移。
    缩放/平移通过 QPainter 变换矩阵实现，不改动 project_equirectangular 投影；
    鼠标模式（无手势）完全不变。
    """

    def __init__(self, tracker: AircraftTracker, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._tracker = tracker
        self.setMinimumSize(360, 240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # 触屏缩放/平移状态（QPainter 变换：以画布中心为锚点 scale + translate）
        self._map_zoom: float = 1.0
        self._map_pan_x: float = 0.0
        self._map_pan_y: float = 0.0
        if PinchZoomMixin is not object and hasattr(self, "init_pinch_zoom"):
            self.init_pinch_zoom()
            try:
                self.pinch_scale_changed.connect(self._on_touch_pinch)
                self.pan_changed.connect(self._on_touch_pan)
            except Exception:
                pass

    # ---- 触屏手势：缩放/平移（仅改变换矩阵，不造假飞机点）----
    def _on_touch_pinch(self, scale_factor: float, center_pos: QPointF) -> None:
        try:
            self._map_zoom = max(1.0, min(12.0, self._map_zoom * scale_factor))
            self.update()
        except Exception:
            pass

    def _on_touch_pan(self, dx: float, dy: float) -> None:
        try:
            self._map_pan_x += dx
            self._map_pan_y += dy
            self.update()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    def _displayed(self) -> list:
        """只保留 lat/lon 同时有效的飞机，绝不画无坐标目标。"""
        return [a for a in self._tracker.all()
                if a.lat is not None and a.lon is not None]

    def paintEvent(self, event) -> None:  # noqa: D401 (Qt 回调)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), _BG)

        acs = self._displayed()
        if not acs:
            # 空态：居中提示，不画任何假点（不施加缩放变换）。
            p.setPen(QPen(_TEXT))
            f = QFont()
            f.setPointSize(12)
            p.setFont(f)
            p.drawText(QRectF(self.rect()), Qt.AlignCenter, ADSB_EMPTY_TEXT)
            p.end()
            return

        # 触屏缩放/平移：以画布中心为锚点做 scale+translate 变换。
        # 经纬网/大陆轮廓/飞机点都在变换后的坐标系里绘制，整体一起缩放。
        if self._map_zoom != 1.0 or self._map_pan_x or self._map_pan_y:
            cx, cy = w / 2.0, h / 2.0
            p.translate(cx + self._map_pan_x, cy + self._map_pan_y)
            p.scale(self._map_zoom, self._map_zoom)
            p.translate(-cx, -cy)

        # 30° 间隔经纬网格（浅米灰细线）。
        p.setPen(QPen(_GRID, 1))
        for lon in range(-180, 181, 30):
            x_top, _ = project_equirectangular(90.0, float(lon), w, h)
            x_bot, _ = project_equirectangular(-90.0, float(lon), w, h)
            p.drawLine(QPointF(x_top, 0.0), QPointF(x_bot, float(h)))
        for lat in range(-90, 91, 30):
            _, y_left = project_equirectangular(float(lat), -180.0, w, h)
            _, y_right = project_equirectangular(float(lat), 180.0, w, h)
            p.drawLine(QPointF(0.0, y_left), QPointF(float(w), y_right))

        # 简化大陆轮廓：浅填充 + 蓝灰粗描边。
        outline_pen = QPen(_TEXT, 2)
        for poly in WORLD_LAND_POLYGONS:
            qp = QPolygonF()
            for (lon, lat) in poly:
                x, y = project_equirectangular(float(lat), float(lon), w, h)
                qp << QPointF(x, y)
            p.setPen(outline_pen)
            p.setBrush(QBrush(_LAND_FILL))
            p.drawPolygon(qp)

        # 飞机点：橙色实心圆 + 呼号文字；有航向时画一小段航向线。
        dot_pen = QPen(_ACCENT, 1)
        dot_brush = QBrush(_ACCENT)
        for a in acs:
            x, y = project_equirectangular(float(a.lat), float(a.lon), w, h)
            if a.track_deg is not None:
                rad = math.radians(float(a.track_deg))
                dx = math.sin(rad)
                dy = -math.cos(rad)  # 屏幕 y 向下，北对应 -y
                p.setPen(QPen(_ACCENT, 2))
                p.drawLine(QPointF(x, y),
                           QPointF(x + 14.0 * dx, y + 14.0 * dy))
            p.setPen(dot_pen)
            p.setBrush(dot_brush)
            p.drawEllipse(QPointF(x, y), 5.0, 5.0)
            p.setPen(QPen(_TEXT))
            p.drawText(QPointF(x + 8.0, y - 6.0), a.callsign or a.icao_hex)

        p.end()


class AdsbMapPanel(QWidget):
    """左右布局：左侧地图画布（拉伸），右侧飞机列表（固定宽）。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._tracker = AircraftTracker()
        self._observer: Optional[Tuple[float, float]] = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        self.canvas = _MapCanvas(self._tracker)
        layout.addWidget(self.canvas, 1)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["呼号", "高度(ft)", "距离(km)"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        # 触屏友好：开启触屏事件 + 按像素滚动（手指拖动列表行）
        enable_touch_events(self.table)
        try:
            from PySide6.QtWidgets import QAbstractItemView
            self.table.setVerticalScrollMode(
                QAbstractItemView.ScrollMode.ScrollPerPixel)
        except Exception:
            pass
        # 右侧飞机列表：给弹性宽度范围，窗口缩放时跟随
        self.table.setMinimumWidth(160)
        self.table.setMaximumWidth(320)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        layout.addWidget(self.table, 0)

    # ------------------------------------------------------------------ #
    def update_aircraft(self, frames: List[dict]) -> None:
        """喂入一帧/一批解码器输出 dict，归并后刷新画布与列表。"""
        for fr in frames:
            self._tracker.update(fr)
        self._tracker.prune()
        self._refresh_table()
        self.canvas.update()

    def set_observer(self, lat: float, lon: float) -> None:
        """设定本站（观察者）坐标，用于列表距离列；未设时距离显示 --。"""
        self._observer = (float(lat), float(lon))
        self._refresh_table()
        self.canvas.update()

    def clear(self) -> None:
        """清空跟踪表与列表，回到空态。"""
        self._tracker.clear()
        self.table.setRowCount(0)
        self.canvas.update()

    def aircraft_count(self) -> int:
        """当前 lat/lon 均有效、可上图的飞机数量。"""
        return sum(1 for a in self._tracker.all()
                   if a.lat is not None and a.lon is not None)

    # ------------------------------------------------------------------ #
    def _displayed(self) -> list:
        return [a for a in self._tracker.all()
                if a.lat is not None and a.lon is not None]

    def _refresh_table(self) -> None:
        acs = self._displayed()
        self.table.setRowCount(len(acs))
        for row, a in enumerate(acs):
            self.table.setItem(row, 0,
                               QTableWidgetItem(a.callsign or a.icao_hex))
            alt = "--" if a.altitude_ft is None else f"{a.altitude_ft}"
            self.table.setItem(row, 1, QTableWidgetItem(alt))
            if self._observer is None:
                dist = "--"
            else:
                d = haversine_km(self._observer[0], self._observer[1],
                                 float(a.lat), float(a.lon))
                dist = f"{d:.0f}"
            self.table.setItem(row, 2, QTableWidgetItem(dist))
