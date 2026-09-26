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

from mbdsdr_ai.adsb_map import (  # noqa: E402
    WORLD_LAND_POLYGONS,
    AircraftTracker,
    haversine_km,
    project_equirectangular,
)

# 直接硬编码三色系（与 themes.DEFAULT_LIGHT.colors 取值一致）。
_BG = QColor("#F5F3EF")
_TEXT = QColor("#5B7B8C")
_ACCENT = QColor("#C4845C")
_GRID = QColor("#D8D2C8")
_LAND_FILL = QColor("#E8E4DD")

# 空态提示文案（测试据此断言画布进入“无数据”状态）。
ADSB_EMPTY_TEXT = "无 ADS-B 飞机（需 1090MHz）"


class _MapCanvas(QWidget):
    """纯 QPainter 绘制的等距圆柱投影地图画布。"""

    def __init__(self, tracker: AircraftTracker, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._tracker = tracker
        self.setMinimumSize(360, 240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

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

        acs = self._displayed()
        if not acs:
            # 空态：居中提示，不画任何假点。
            p.setPen(QPen(_TEXT))
            f = QFont()
            f.setPointSize(12)
            p.setFont(f)
            p.drawText(QRectF(self.rect()), Qt.AlignCenter, ADSB_EMPTY_TEXT)
            p.end()
            return

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
        self.table.setFixedWidth(220)
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
