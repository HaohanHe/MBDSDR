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
  - 地面站位置未配置时地图显「未设置观测地点」。
  - 飞机 30 秒未更新即从列表移除（prune ttl=30）。
"""

from __future__ import annotations

import csv
import json
import logging
import math
import os
import sys
import time
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# desktop 包无 __init__.py，靠 repo root 在 sys.path 上完成导入。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt, QPointF, QRectF, Signal  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QBrush, QColor, QFont, QPainter, QPen, QPolygonF,
)
from PySide6.QtWidgets import (  # noqa: E402
    QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMenu, QSizePolicy,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from mbdsdr_ai.adsb_map import AircraftTracker  # noqa: E402
from tokens import tokens as _tok  # noqa: E402

try:
    from mbdsdr_ai import adsb_lite as _adsb_lite
    _HAS_ADSB = True
except Exception:  # pragma: no cover - 内核解码器缺失时面板降级
    _adsb_lite = None
    _HAS_ADSB = False

# 与现有 adsb_map_panel 一致的米白三色系。**固定取全局 COLORS，不随主题变**：
# 面板自绘一张浅色「地图纸」，换主题不会刷新这里（模块导入时求值一次）。
_t = _tok()
_BG = QColor(_t.COLORS["light_bg"])
_TEXT = QColor(_t.COLORS["light_text"])
_ACCENT = QColor(_t.COLORS["light_accent"])
_GRID = QColor(_t.COLORS["light_grid"])
_STATION = QColor(_t.COLORS["station_blue"])

# 空态文案（测试据此断言面板进入“无数据”状态）。
ADSB_WAIT_TEXT = "等待 1090MHz 数据"
ADSB_CENTER_HZ = 1_090_000_000
#: 当前设备真实可调上界低于 1090 MHz 时的空态：不许让用户对着
#: 「等待 1090MHz 数据」空等一台物理上收不到的设备。
ADSB_UNREACHABLE_TEXT = (
    "当前设备上限 {max_mhz:.1f} MHz，无法接收 1090 MHz；"
    "需覆盖 1090 MHz 的前端（如 R820T/R828D）"
)
#: 频率/模式门控：ADS-B 在 1090 MHz，AM 包络检波。
#: 频点允差取 ±2.5 MHz（1090ES 占用带宽 <2 MHz，留一点调谐误差余量）；
#: 解调必须是 AM，NFM/WFM/USB 等解出来的基带形状不对，解不出前导码。
ADSB_BAND_TOL_HZ = 2_500_000
ADSB_OK_DEMODS = ("am", "am-env", "raw", "auto")
ADSB_FREQ_GATE_TEXT = (
    "当前中心频率 {cur_mhz:.3f} MHz，不是 1090 MHz —— 收不到 ADS-B；"
    "请把中心频率调到 1090.000 MHz"
)
ADSB_DEMOD_GATE_TEXT = (
    "当前解调 {demod}，ADS-B 需要 AM —— 请切到 AM 再解"
)
NO_STATION_TEXT = ("未设置观测地点\n"
                   "请在「设置 → 天文观测 → 观测地点纬度/经度」填写")

# 飞机超时 30 秒未更新即移除。
AIRCRAFT_TTL_SEC = 30.0

#: 列表一次最多渲染的飞机行数。``_refresh_table`` 在每批解码帧后整表重建，
#: 成本跟随在视飞机数：同机实测 100 架 3.7 ms、500 架 26 ms、2000 架 66 ms
#: （每档预热 1 + 采样 5 取中位）。取 300（约 10 ms/批，低于一帧），只显示
#: 最近更新的前 300 架；表头明说还有多少架未显示。
#: 复跑入口：``scripts/repro/2026-11-14-list-render-baseline.py --only adsb``。
MAX_RENDERED_AIRCRAFT = 300


def _load_station() -> Tuple[Optional[float], Optional[float]]:
    """从 ~/.mbdsdr/config.json 读地面站 lat/lon；缺失或非法返回 (None, None)。

    两种「取不到」必须可分：文件不存在＝本来就没配（正常空态，不记日志）；
    文件在但读不动/字段非法＝降级（地图会显示「未设置观测地点」，但原因
    要留一条可查记录，否则和「没配」混成同一件事）。
    """
    path = os.path.expanduser("~/.mbdsdr/config.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        lat = cfg.get("ground_station_lat")
        lon = cfg.get("ground_station_lon")
        if lat is not None and lon is not None:
            return float(lat), float(lon)
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        logger.warning("地面站配置不可用，地图按未定位降级：%s | %s: %s",
                       path, type(exc).__name__, exc)
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

    def _draw_empty_text(self, p: QPainter, text: str) -> None:
        """在画布中央画空态文案（自动换行）。

        必须换行：长句在 320~560px 宽的画布上会被左右同时切掉——2026-11-11
        实拍「未设置地面站位置（请在 ~/.mbdsdr/config.json 设置
        ground_station_lat/lon）」，两端各缺一截，一个字都读不全。
        """
        p.setPen(QPen(_TEXT))
        f = QFont()
        f.setPointSize(12)
        p.setFont(f)
        p.drawText(self.rect().adjusted(14, 14, -14, -14),
                   int(Qt.AlignCenter | Qt.TextWordWrap), text)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), _BG)

        # 径向距离圈（25/50/75% 半径）
        slat, slon = self._station
        if slat is None:
            self._draw_empty_text(p, NO_STATION_TEXT)
            p.end()
            return

        acs = [a for a in self._tracker.all()
               if a.lat is not None and a.lon is not None]
        if not acs:
            self._draw_empty_text(p, ADSB_WAIT_TEXT)
            p.end()
            return

        # 距离圈
        cx, cy = w / 2.0, h / 2.0
        _, _, rng = self._project(slat, slon, w, h)
        for frac in (0.25, 0.5, 0.75, 1.0):
            # 距圈是非文本刻度线：light_grid 对纸面只有 1.36:1，看不清；
            # 换岛内既有的站色（对纸面 3.94:1），且距圈本就以本站为圆心。
            p.setPen(QPen(_STATION, 1))
            p.drawEllipse(QPointF(cx, cy),
                          (min(w, h) / 2.0 - 24.0) * frac,
                          (min(w, h) / 2.0 - 24.0) * frac)
        # 量程是文字，必须按正文门槛取色：light_grid 对纸面 1.36:1 看不出来，
        # 用岛内既有的文字色（5.11:1）。
        p.setPen(QPen(_TEXT))
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
        # 当前设备真实可调上界（Hz）；None = 未知，不替设备下结论。
        self._tuner_max_hz: Optional[float] = None
        self._station: Tuple[Optional[float], Optional[float]] = _load_station()
        # 当前接收上下文（由主窗口同步）：中心频率与解调模式；None = 未知，
        # 未知时不做门控（不替设备下结论）。
        self._rx_center_hz: Optional[float] = None
        self._rx_demod: Optional[str] = None
        self._gate_reason: Optional[str] = None
        self._build_ui()

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.header = QLabel(ADSB_WAIT_TEXT)
        # 不给内联 color（也不给内联样式表）：_TEXT 是给那张浅色「地图纸」用的
        # 墨色，画在深色面板上就是深灰压深灰；留白用布局边距做，颜色交给主题。
        self.header.setContentsMargins(4, 2, 4, 2)
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
        # 导出入口只挂在表格右键菜单：不改布局、不新增控件。
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_table_menu)
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

    def set_tuner_max_hz(self, max_hz: Optional[float]) -> None:
        """当前设备真实可调上界；低于 1090 MHz 时如实说明收不到。"""
        self._tuner_max_hz = float(max_hz) if max_hz else None
        self._refresh_header()

    def set_rx_context(self, center_hz: Optional[float],
                       demod: Optional[str]) -> None:
        """同步主窗口的中心频率与解调模式，用于频率/模式门控。

        传 None 表示未知：未知时不做门控，只按连接/上界/数据说话。
        """
        self._rx_center_hz = float(center_hz) if center_hz else None
        self._rx_demod = (str(demod).strip().lower() if demod else None) or None
        self._gate_reason = self._compute_gate_reason()
        self._refresh_header()

    def gate_reason(self) -> Optional[str]:
        """当前门控原因；None 表示放行（或频率/模式未知）。"""
        return self._gate_reason

    def _compute_gate_reason(self) -> Optional[str]:
        if (self._rx_center_hz is not None
                and abs(self._rx_center_hz - ADSB_CENTER_HZ) > ADSB_BAND_TOL_HZ):
            return ADSB_FREQ_GATE_TEXT.format(
                cur_mhz=self._rx_center_hz / 1e6)
        if self._rx_demod is not None and self._rx_demod not in ADSB_OK_DEMODS:
            return ADSB_DEMOD_GATE_TEXT.format(demod=self._rx_demod.upper())
        return None

    def feed_iq(self, iq, sample_rate: float) -> None:
        """喂一段 1090MHz 复基带 IQ；内部解码并合并到跟踪表。

        门控不通过（频点/模式不对）时不解码：解了也只会白白烧 CPU 并让
        用户以为「有设备却没飞机」。
        """
        if not _HAS_ADSB:
            return
        if iq is None or len(iq) == 0:
            return
        if self._gate_reason is not None:
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
            suffix = "（未连接后端）" if not self._sdr_connected else ""
            if self._gate_reason:
                suffix = "（当前不在 1090 MHz/AM，新数据不会进来）"
            hidden = max(0, n - MAX_RENDERED_AIRCRAFT)
            tail = (f"；列表只显示最近 {MAX_RENDERED_AIRCRAFT} 架，"
                    f"另 {hidden} 架未显示") if hidden else ""
            self.header.setText(f"在视飞机 {n} 架{suffix}{tail}")
        elif not self._sdr_connected:
            self.header.setText("未连接（等待 1090MHz 数据）")
        elif (self._tuner_max_hz is not None
              and self._tuner_max_hz < ADSB_CENTER_HZ):
            # 设备上界 < 1090：物理上收不到，别让用户对着「等待数据」空等。
            self.header.setText(
                ADSB_UNREACHABLE_TEXT.format(max_mhz=self._tuner_max_hz / 1e6))
        elif self._gate_reason:
            # 频点/模式不对：说出来，别显示「等待 1090MHz 数据」让人以为在收。
            self.header.setText(self._gate_reason)
        else:
            self.header.setText(ADSB_WAIT_TEXT)

    def _refresh_table(self) -> None:
        acs = self._tracker.all()
        shown = acs[:MAX_RENDERED_AIRCRAFT]
        self.table.setRowCount(len(shown))
        now = time.time()
        for row, a in enumerate(shown):
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

    def export_csv(self, path: str) -> bool:
        """把当前在视飞机导出为 CSV（UTF-8 BOM，Excel 直开）。

        0 架时返回 False 且不落空文件：空表导出会让人以为「导出成功、
        只是没数据」，与「本次没有可导出的目标」是两件事。
        """
        acs = self._tracker.all()
        if not acs:
            return False
        now = time.time()
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(self.COLUMNS[:-1] + ["最后更新(s)"])
            for a in acs:
                w.writerow([
                    a.icao_hex,
                    a.callsign or "",
                    "" if a.lat is None else f"{a.lat:.4f}",
                    "" if a.lon is None else f"{a.lon:.4f}",
                    "" if a.altitude_ft is None else a.altitude_ft,
                    "" if a.groundspeed_kt is None else f"{a.groundspeed_kt:.0f}",
                    "" if a.track_deg is None else f"{a.track_deg:.0f}",
                    f"{now - a.last_seen:.0f}",
                ])
        return True

    def _show_table_menu(self, pos) -> None:
        menu = QMenu(self)
        act = menu.addAction("导出为 CSV…")
        act.setEnabled(self.aircraft_count() > 0)
        act.triggered.connect(self._on_export_clicked)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _on_export_clicked(self) -> None:
        if self.aircraft_count() == 0:
            return
        default = os.path.join(
            os.path.expanduser("~"), f"adsb_{time.strftime('%Y%m%d_%H%M%S')}.csv")
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 ADS-B 列表", default, "CSV (*.csv)")
        if not path:
            return
        if not self.export_csv(path):
            self.header.setText("无可导出数据（当前没有在视飞机）")

    def clear(self) -> None:
        self._tracker.clear()
        self.canvas._tracks.clear()
        self.table.setRowCount(0)
        self._refresh_header()
        self.canvas.update()
