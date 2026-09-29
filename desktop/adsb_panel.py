# SPDX-License-Identifier: MIT
"""
MBDSDR 桌面端 - ADS-B 航路图面板 (AdsbPanel)
================================================

1090 MHz Mode-S/ADS-B 实时面板：飞机列表表格 + 以接收站为中心的简易 QPainter 地图。

数据链路（只画真实解算出来的目标，绝不放演示飞机）：
  - 外部把一段 1090MHz 复基带 IQ 喂给 :meth:`AdsbPanel.feed_iq`；
    内部调用 ``mbdsdr_ai.adsb_lite.decode_adsb``（前导码检测→PPM→CRC-24 校验），
    通过 CRC 的帧交给 ``mbdsdr_ai.adsb_map.AircraftTracker`` 按 ICAO 字段级合并。
  - 表格列：ICAO / 呼号 / 纬度 / 经度 / 高度(ft) / 速度(kt) / 航向 / 最后更新。
  - 地图：以 ``~/.mbdsdr/config.json`` 的 ``ground_station_lat/lon`` 为中心的
    等距投影；飞机画成按航向旋转的三角形 + 呼号标签，历史轨迹用淡线。
  - 只有 lat/lon 同时有效的飞机才上图；位置未配对（needs_cpr）的飞机仍进表格，
    但地图上不画点（不臆造坐标）。

红线：
  - 无数据时表格与地图都显「等待 1090MHz 数据」，绝不预存任何飞机。
  - 地面站位置未配置时地图显「未设置地面站位置」。
  - 飞机 30 秒未更新即从列表移除（prune ttl=30）。
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from typing import List, Optional, Tuple

# desktop 包无 __init__.py，靠 repo root 在 sys.path 上完成导入。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt, QPointF, QRectF, Signal  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QBrush, QColor, QFont, QPainter, QPen, QPolygonF,
)
from PySide6.QtWidgets import (  # noqa: E402
    QHBoxLayout, QHeaderView, QLabel, QSizePolicy, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from mbdsdr_ai.adsb_map import AircraftTracker  # noqa: E402
from tokens import tokens as _tok  # noqa: E402

try:
    from mbdsdr_ai import adsb_lite as _adsb_lite
    _HAS_ADSB = True
except Exception:  # pragma: no cover - 内核解码器缺失时面板降级
    _adsb_lite = None
    _HAS_ADSB = False

# 与现有 adsb_map_panel 一致的米白三色系（从 tokens 取色，主题联动）。
_t = _tok()
_BG = QColor(_t.COLORS["light_bg"])
_TEXT = QColor(_t.COLORS["light_text"])
_ACCENT = QColor(_t.COLORS["light_accent"])
_GRID = QColor(_t.COLORS["light_grid"])
_STATION = QColor(_t.COLORS["station_blue"])

# 空态文案（测试据此断言面板进入“无数据”状态）。
ADSB_WAIT_TEXT = "等待 1090MHz 数据"
NO_STATION_TEXT = "未设置地面站位置（请在 ~/.mbdsdr/config.json 设置 ground_station_lat/lon）"

# 飞机超时 30 秒未更新即移除。
AIRCRAFT_TTL_SEC = 30.0


def _load_station() -> Tuple[Optional[float], Optional[float]]:
    """从 ~/.mbdsdr/config.json 读地面站 lat/lon；缺失或非法返回 (None, None)。"""
    path = os.path.expanduser("~/.mbdsdr/config.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        lat = cfg.get("ground_station_lat")
        lon = cfg.get("ground_station_lon")
        if lat is not None and lon is not None:
            return float(lat), float(lon)
    except Exception:
        pass
    return (None, None)


class _AdsbMapCanvas(QWidget):
    """以接收站为中心的等距投影地图画布（纯 QPainter）。"""

    def __init__(self, tracker: AircraftTracker, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._tracker = tracker
        self._station: Tuple[float, float] = _load_station()
        self._center_icao: Optional[str] = None
        self._tracks: dict = {}  # icao -> list[(lat,lon)]
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_station(self, lat: float, lon: float) -> None:
        self._station = (float(lat), float(lon))
        self.update()

    def center_on(self, icao: str) -> None:
        self._center_icao = icao
        self.update()

    def push_track(self, icao: str, lat: float, lon: float) -> None:
        tr = self._tracks.setdefault(icao, [])
        if not tr or (abs(tr[-1][0] - lat) > 1e-5 or abs(tr[-1][1] - lon) > 1e-5):
            tr.append((lat, lon))
            if len(tr) > 64:
                tr.pop(0)

    def prune_tracks(self, active: set) -> None:
        for k in [k for k in self._tracks if k not in active]:
            self._tracks.pop(k, None)

    # ------------------------------------------------------------------ #
    def _project(self, lat: float, lon: float, w: int, h: int
                 ) -> Tuple[float, float, float]:
        """等距投影：以本站为中心，返回屏幕 (x, y) 与半量程(km)。"""
        slat, slon = self._station
        km_per_deg_lat = 111.32
        km_per_deg_lon = 111.32 * math.cos(math.radians(slat))
        # 以本站为中心；若指定了居中飞机则以其为中心
        clat, clon = slat, slon
        ac = self._tracker._ac.get(self._center_icao) if self._center_icao else None
        if ac is not None and ac.lat is not None and ac.lon is not None:
            clat, clon = ac.lat, ac.lon
        dx_km = (lon - clon) * km_per_deg_lon
        dy_km = (lat - clat) * km_per_deg_lat
        # 量程：默认 80 km，按最远飞机自适应（不小于 20km）
        rng = 80.0
        for a in self._tracker.all():
            if a.lat is not None and a.lon is not None:
                d = math.hypot((a.lon - clon) * km_per_deg_lon,
                               (a.lat - clat) * km_per_deg_lat)
                rng = max(rng, d * 1.25)
        scale = (min(w, h) / 2.0 - 24.0) / rng
        x = w / 2.0 + dx_km * scale
        y = h / 2.0 - dy_km * scale  # 北 = -y
        return x, y, rng

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), _BG)

        # 径向距离圈（25/50/75% 半径）
        slat, slon = self._station
        if slat is None:
            p.setPen(QPen(_TEXT))
            f = QFont()
            f.setPointSize(12)
            p.setFont(f)
            p.drawText(QRectF(self.rect()), Qt.AlignCenter, NO_STATION_TEXT)
            p.end()
            return

        acs = [a for a in self._tracker.all()
               if a.lat is not None and a.lon is not None]
        if not acs:
            p.setPen(QPen(_TEXT))
            f = QFont()
            f.setPointSize(12)
            p.setFont(f)
            p.drawText(QRectF(self.rect()), Qt.AlignCenter, ADSB_WAIT_TEXT)
            p.end()
            return

        # 距离圈
        cx, cy = w / 2.0, h / 2.0
        _, _, rng = self._project(slat, slon, w, h)
        for frac in (0.25, 0.5, 0.75, 1.0):
            p.setPen(QPen(_GRID, 1))
            p.drawEllipse(QPointF(cx, cy),
                          (min(w, h) / 2.0 - 24.0) * frac,
                          (min(w, h) / 2.0 - 24.0) * frac)
        p.setPen(QPen(_GRID))
        p.drawText(QRectF(4, 4, 200, 18), Qt.AlignLeft, f"量程 ±{rng:.0f} km")

        # 本站标记（十字）
        p.setPen(QPen(_STATION, 2))
        p.drawLine(QPointF(cx - 6, cy), QPointF(cx + 6, cy))
        p.drawLine(QPointF(cx, cy - 6), QPointF(cx, cy + 6))
        p.drawText(QPointF(cx + 8, cy + 4), "本站")

        # 历史轨迹（淡线）
        for a in acs:
            tr = self._tracks.get(a.icao_hex)
            if not tr or len(tr) < 2:
                continue
            p.setPen(QPen(QColor(196, 132, 92, 90), 1))
            prev = None
            for (tlat, tlon) in tr:
                x, y, _ = self._project(tlat, tlon, w, h)
                if prev is not None:
                    p.drawLine(QPointF(*prev), QPointF(x, y))
                prev = (x, y)

        # 飞机：按航向旋转的三角形 + 呼号
        tri = QPolygonF([QPointF(0, -7), QPointF(-5, 6), QPointF(5, 6)])
        for a in acs:
            x, y, _ = self._project(float(a.lat), float(a.lon), w, h)
            p.save()
            p.translate(x, y)
            if a.track_deg is not None:
                p.rotate(float(a.track_deg))
            p.setPen(QPen(_ACCENT, 1))
            p.setBrush(QBrush(_ACCENT))
            p.drawPolygon(tri)
            p.restore()
            p.setPen(QPen(_TEXT))
            p.drawText(QPointF(x + 8, y - 6), a.callsign or a.icao_hex)

        p.end()


class AdsbPanel(QWidget):
    """ADS-B 航路图面板：上方飞机列表表格，下方/右侧简易地图。"""

    COLUMNS = ["ICAO", "呼号", "纬度", "经度", "高度(ft)",
               "速度(kt)", "航向(°)", "最后更新"]

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._tracker = AircraftTracker()
        self._sdr_connected = False
        self._station: Tuple[Optional[float], Optional[float]] = _load_station()
        self._build_ui()

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.header = QLabel(ADSB_WAIT_TEXT)
        self.header.setStyleSheet(f"color: {_TEXT.name()}; padding: 2px;")
        layout.addWidget(self.header)

        splitter = QSplitter(Qt.Vertical)
        layout.addWidget(splitter, 1)

        # 飞机列表表格
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        hdr = self.table.horizontalHeader()
        for i in range(len(self.COLUMNS)):
            hdr.setSectionResizeMode(i, QHeaderView.Stretch if i in (1, 7)
                                     else QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        splitter.addWidget(self.table)

        # 地图画布
        self.canvas = _AdsbMapCanvas(self._tracker)
        if self._station[0] is not None:
            self.canvas.set_station(*self._station)
        splitter.addWidget(self.canvas)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

    # ------------------------------------------------------------------ #
    def set_sdr_connected(self, connected: bool) -> None:
        """后端连接状态：未连接时保持等待文案，不清空已收数据。"""
        self._sdr_connected = bool(connected)
        self._refresh_header()

    def feed_iq(self, iq, sample_rate: float) -> None:
        """喂一段 1090MHz 复基带 IQ；内部解码并合并到跟踪表。"""
        if not _HAS_ADSB:
            return
        if iq is None or len(iq) == 0:
            return
        try:
            result = _adsb_lite.decode_adsb(iq, sample_rate)
        except Exception:
            return
        frames = result.get("frames") or []
        for fr in frames:
            ac = self._tracker.update(fr)
            if ac is not None and ac.lat is not None and ac.lon is not None:
                self.canvas.push_track(ac.icao_hex, ac.lat, ac.lon)
        self._after_update()

    def submit_decoded_frame(self, frame: dict) -> None:
        """直接喂一个已解码的 ADS-B 帧 dict（测试/外部后端用）。"""
        if not frame:
            return
        ac = self._tracker.update(frame)
        if ac is not None and ac.lat is not None and ac.lon is not None:
            self.canvas.push_track(ac.icao_hex, ac.lat, ac.lon)
        self._after_update()

    # ------------------------------------------------------------------ #
    def _after_update(self) -> None:
        self._tracker.prune(AIRCRAFT_TTL_SEC)
        active = {a.icao_hex for a in self._tracker.all()}
        self.canvas.prune_tracks(active)
        self._refresh_table()
        self._refresh_header()
        self.canvas.update()

    def _refresh_header(self) -> None:
        n = len(self._tracker)
        if n:
            self.header.setText(f"在视飞机 {n} 架"
                                + ("" if self._sdr_connected else "（未连接后端）"))
        else:
            self.header.setText(ADSB_WAIT_TEXT
                                if self._sdr_connected else "未连接（等待 1090MHz 数据）")

    def _refresh_table(self) -> None:
        acs = self._tracker.all()
        self.table.setRowCount(len(acs))
        now = time.time()
        for row, a in enumerate(acs):
            vals = [
                a.icao_hex,
                a.callsign or "--",
                "--" if a.lat is None else f"{a.lat:.4f}",
                "--" if a.lon is None else f"{a.lon:.4f}",
                "--" if a.altitude_ft is None else f"{a.altitude_ft}",
                "--" if a.groundspeed_kt is None else f"{a.groundspeed_kt:.0f}",
                "--" if a.track_deg is None else f"{a.track_deg:.0f}",
                f"{now - a.last_seen:.0f}s 前",
            ]
            for col, v in enumerate(vals):
                self.table.setItem(row, col, QTableWidgetItem(str(v)))

    def _on_row_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        item = self.table.item(rows[0].row(), 0)
        if item is not None:
            self.canvas.center_on(item.text())

    def aircraft_count(self) -> int:
        return len(self._tracker)

    def clear(self) -> None:
        self._tracker.clear()
        self.canvas._tracks.clear()
        self.table.setRowCount(0)
        self._refresh_header()
        self.canvas.update()
