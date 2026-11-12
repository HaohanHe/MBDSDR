# SPDX-License-Identifier: MIT
"""
MBDSDR 桌面端 - ADS-B 实时地图面板 (AdsbMapPanel)
==================================================

底图走**公用地图服务** ``desktop/osm_map.py``（OpenStreetMap 瓦片），
本面板只负责把自己的东西叠上去：

  * 左侧 ``OsmMapView``：OSM 底图 + "adsb" 图层（飞机点 / 航向 / 呼号 /
    空态牌 / 解码诊断）。
  * 右侧 ``QTableWidget``：呼号 / 气压高度(ft) / 到观察者距离(km)。

为什么不自己画地图：2026-11-10 用户定调——自绘轮廓既有测绘合规风险
（没有地图编制资质却出具地图），又不如人家做得准。地图是公用能力
（地点选择 / APRS / 指向 / 模型展示都要用），不是 ADS-B 的私产。

数据链路（只画真实解算出来的位置，绝不臆造坐标）：
  - 外部把 ``ADSBDecoder.handle(raw)`` 返回的 dict 列表喂给
    :meth:`AdsbMapPanel.update_aircraft`；内部用 ``AircraftTracker`` 按 ICAO
    合并呼号 / 高度 / 速度 / 最新位置。
  - 只有 ``lat`` 和 ``lon`` 同时有效的飞机才会被画到图上、写进列表；
    没有任何位置帧时地图上叠一张"无数据"牌（底图照常显示）。
  - 距离由 ``set_observer(lat, lon)`` 设定本站坐标后，用 haversine 计算；
    未设观察者时距离列显示 ``--``。

红线：
  - 不内置任何飞机、不硬编码真实呼号；无数据就显空态。
  - 底图并不是"我们的地图"：署名按当前瓦片源如实显示（OSM / CARTO）。
"""

from __future__ import annotations

import math
import os
import sys
from typing import List, Optional, Tuple

# desktop 包无 __init__.py，靠 repo root 在 sys.path 上完成导入。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import Qt, QPointF, QRectF, QTimer  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QBrush, QColor, QFont, QFontMetrics, QPainter, QPen,
)
from PySide6.QtWidgets import (  # noqa: E402
    QHBoxLayout, QHeaderView, QTableWidget, QTableWidgetItem, QWidget,
)

from tokens import tokens as _tok  # noqa: E402

# 频率读数唯一口径（6 位小数 MHz = 1 Hz），面板不再就地拼小数位。
from readouts import format_frequency_hz  # noqa: E402

# 触屏：表格按像素滚动、地图捏合由 OsmMapView 自己处理
try:  # noqa: E402
    from touch_helpers import enable_touch_events
except Exception:  # pragma: no cover
    enable_touch_events = lambda w: None  # noqa: E731

try:  # OSM 公用地图（缺 QtNetwork 时面板降级为只有表格，不崩）
    from osm_map import OsmMapView, style_for_dark  # noqa: E402
except Exception:  # pragma: no cover
    OsmMapView = None  # type: ignore
    style_for_dark = lambda dark: "osm"  # noqa: E731

try:  # 1090MHz Mode S 解码器（无 numpy 时降级，不影响底图）
    from mbdsdr_ai import adsb_lite as _adsb_lite  # noqa: E402
    _HAS_ADSB = True
except Exception:  # pragma: no cover
    _adsb_lite = None  # type: ignore
    _HAS_ADSB = False

from mbdsdr_ai.adsb_map import AircraftTracker, haversine_km  # noqa: E402

_t = _tok()
_BG = QColor(_t.COLORS["card_1"])
_TEXT = QColor(_t.COLORS["gray_100"])
_ACCENT = QColor(_t.COLORS["accent"])
_GRID = QColor(_t.COLORS["gray_400"])

# 空态提示文案（测试据此断言画布进入“无数据”状态）。
ADSB_EMPTY_TEXT = "无 ADS-B 飞机（需 1090MHz）"


class AdsbMapPanel(QWidget):
    """左右布局：左侧公用地图（拉伸）+ "adsb" 图层，右侧飞机列表。"""

    def __init__(self, parent: Optional[QWidget] = None,
                 map_style: Optional[str] = None, map_online: bool = True):
        super().__init__(parent)
        self._tracker = AircraftTracker()
        self._observer: Optional[Tuple[float, float]] = None
        self._diag_text: str = ""

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # 公用地图：ADS-B 只是它的一个图层使用者
        if OsmMapView is not None:
            self.canvas = OsmMapView(
                self, online=map_online,
                style=map_style or _theme_map_style())
            self.canvas.set_view(35.0, 110.0, 5)
            self.canvas.add_layer("adsb", self._paint_adsb_layer)
        else:  # pragma: no cover - 缺 QtNetwork 的环境
            from PySide6.QtWidgets import QLabel
            self.canvas = QLabel("地图不可用（本机 Qt 缺 QtNetwork）", self)
        layout.addWidget(self.canvas, 1)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["呼号", "高度(ft)", "距离(km)"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        enable_touch_events(self.table)
        try:
            from PySide6.QtWidgets import QAbstractItemView
            self.table.setVerticalScrollMode(
                QAbstractItemView.ScrollMode.ScrollPerPixel)
        except Exception:
            pass
        # 右侧飞机列表：给弹性宽度范围，窗口缩放时跟随。
        # 最小宽 40 而非 160：中栏基线 300px 时「画布最小 240 + 列表 160 +
        # 间距 6 + 边距 8」= 414，布局会把列表挤出父容器（右侧被裁）。
        self.table.setMinimumWidth(40)
        self.table.setMaximumWidth(320)
        # 0 行时不该杵着一张空表格占掉中栏右侧：没数据就收起，地图铺满；
        # 有真实飞机时由 _refresh_table 自动放回来。
        self.table.setVisible(False)
        # 画布深色、表格默认白底 —— 中栏会一半黑一半白（用户原话「你是
        # 黑白无常啊」）。表格、表头、单元格全部跟随画布底色。
        _bg = _t.COLORS["card_1"]
        _fg = _t.COLORS["gray_100"]
        _line = _t.COLORS["gray_400"]
        self.table.setStyleSheet(
            "QTableWidget { background-color: %s; color: %s; border: none;"
            " gridline-color: %s; }"
            "QTableWidget::viewport { background-color: %s; }"
            "QTableWidget::item { background-color: %s; color: %s; }"
            "QHeaderView::section { background-color: %s; color: %s;"
            " border: none; border-bottom: 1px solid %s; padding: 4px; }"
            % (_bg, _fg, _line, _bg, _bg, _fg, _bg, _fg, _line))
        # QSS 对 QTableWidget 的底色常被 viewport 的调色板盖回去。把
        # viewport / 表格自身的 Base+Window 一起钉到画布底色。
        from PySide6.QtGui import QPalette
        from PySide6.QtGui import QColor as _QC
        pal = self.table.viewport().palette()
        pal.setColor(QPalette.ColorRole.Base, _QC(_bg))
        pal.setColor(QPalette.ColorRole.Window, _QC(_bg))
        pal.setColor(QPalette.ColorRole.Text, _QC(_fg))
        self.table.viewport().setPalette(pal)
        self.table.setPalette(pal)
        self.table.viewport().setAutoFillBackground(True)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        layout.addWidget(self.table, 0)

        # 面板整体最小宽锚定画布最小宽（240）：列表在窄档可被压窄。
        self.setMinimumSize(240, 180)

        # ---- IQ 自主订阅（主窗口只把 IQ 交给频谱面板，这里自己从总线取）----
        # 只在中心频率落在 1090MHz 附近、采样率够宽时才解码，其余零开销跳过。
        self._iq_seq = 0
        self._adsb_frames_total = 0
        self._bus_timer = QTimer(self)
        self._bus_timer.setInterval(200)
        self._bus_timer.timeout.connect(self._poll_iq_bus)
        self._bus_timer.start()

    # ------------------------------------------------------------------ #
    # 诊断文本（对外可读；原来挂在画布上，现在挂面板上——画布是公用组件，
    # 不该混进 ADS-B 概念）
    @property
    def diag_text(self) -> str:
        return self._diag_text

    def update_aircraft(self, frames: List[dict]) -> None:
        """喂入一帧/一批解码器输出 dict，归并后刷新画布与列表。"""
        for fr in frames:
            self._tracker.update(fr)
        self._tracker.prune()
        self._refresh_table()
        self.canvas.update()

    # -- IQ 总线消费 ------------------------------------------------------
    ADSB_CENTER_HZ = 1_090_000_000.0
    ADSB_TOLERANCE_HZ = 1_500_000.0
    ADSB_MIN_SAMPLE_RATE = 1_200_000.0

    def feed_iq(self, iq, sample_rate: float) -> None:
        """手动喂一段 1090MHz 复基带 IQ（测试/外部后端用，与订阅同一实现）。"""
        self._decode_and_update(iq, float(sample_rate))

    def _set_diag(self, text: str) -> None:
        """只在文本变化时重绘画布（200ms 轮询下同文案不重复排重绘）。"""
        text = str(text or "")[:200]
        if self._diag_text != text:
            self._diag_text = text
            self.canvas.update()

    def _decode_and_update(self, iq, sample_rate: float) -> int:
        if not _HAS_ADSB:
            self._set_diag("ADS-B 解码模块不可用（mbdsdr_ai.adsb_lite 缺失）")
            return 0
        if iq is None or len(iq) == 0:
            return 0
        try:
            result = _adsb_lite.decode_adsb(iq, sample_rate)
        except Exception as exc:
            # 旧实现 except: return 0 —— 解码器坏了和"没信号"在界面上
            # 完全一样。现在异常类型与摘要直接进入空态诊断行。
            self._set_diag(
                f"ADS-B 解码器异常：{type(exc).__name__}：{str(exc)[:100]}")
            return 0
        self._set_diag(result.get("note", ""))
        frames = result.get("frames") or []
        if not frames:
            return 0
        self._adsb_frames_total += len(frames)
        self.update_aircraft(frames)
        return len(frames)

    def _poll_iq_bus(self) -> None:
        """每 200ms 看一次总线；只在 1090MHz 且带宽够时解码。"""
        try:
            from desktop.iq_bus import get_iq_bus
            bus = get_iq_bus()
            frame = bus.latest_since(self._iq_seq)
            if frame is None:
                return
            self._iq_seq = frame.seq
            # 频段不符：不解码（不是每个频段都能解 ADS-B），零副作用返回
            if (abs(frame.center_hz - self.ADSB_CENTER_HZ) > self.ADSB_TOLERANCE_HZ
                    or frame.sample_rate < self.ADSB_MIN_SAMPLE_RATE):
                self._set_diag(
                    f"当前 {format_frequency_hz(frame.center_hz)} / "
                    f"{frame.sample_rate / 1e6:.3f} MS/s，"
                    "不在 1090±1.5 MHz 或采样率不足；未解码")
                return
            # 站址由主窗 push（_push_site_to_panels），本面板不自己读盘：
            # 以前这里每 5 s 直读 gui_config.json，自带一份 TTL，和主窗各判
            # 「什么时候站址变了」——两处真相 + 两套失效时机。
            self._decode_and_update(frame.iq, frame.sample_rate)
        except Exception as exc:
            # 总线异常同样不得静默：空态诊断行写明异常类型，下一拍再试。
            self._set_diag(f"IQ 总线读取异常：{type(exc).__name__}")

    def decoded_frames_total(self) -> int:
        """累计解码成功的帧数（测试与状态查询用，不参与绘制）。"""
        return int(self._adsb_frames_total)

    def set_observer(self, lat: float, lon: float) -> None:
        """设定本站坐标：距离列用它算，地图顺带居中到本站。"""
        self._observer = (float(lat), float(lon))
        self._refresh_table()
        try:
            self.canvas.set_marker(float(lat), float(lon))
            # 只在还没被用户拖走过时才跟着站点走（首次定位即居中）
            self.canvas.set_view(float(lat), float(lon),
                                 max(int(self.canvas.zoom()), 7))
        except (AttributeError, RuntimeError):
            pass
        self.canvas.update()

    def set_map_style(self, key: str) -> None:
        """换底图风格（浅色 / 暗色）。ADS-B、地点面板共用同一套缓存。"""
        try:
            self.canvas.set_style(str(key))
        except (AttributeError, RuntimeError):
            pass

    def map_style(self) -> str:
        try:
            return str(self.canvas.style_key())
        except (AttributeError, RuntimeError):
            return "osm"

    def clear(self) -> None:
        """清空跟踪表与列表，回到空态。"""
        self._tracker.clear()
        self._refresh_table()   # 同时收起空表格，与 update_aircraft 同口径
        self.canvas.update()

    def aircraft_count(self) -> int:
        """当前 lat/lon 均有效、可上图的飞机数量。"""
        return sum(1 for a in self._tracker.all()
                   if a.lat is not None and a.lon is not None)

    # ------------------------------------------------------------------ #
    def _displayed(self) -> list:
        return [a for a in self._tracker.all()
                if a.lat is not None and a.lon is not None]

    # ---- "adsb" 图层：画飞机 / 空态牌 / 诊断行 ------------------------- #
    def _paint_adsb_layer(self, p: QPainter, project) -> None:
        acs = self._displayed()
        if acs:
            dot_pen = QPen(_ACCENT, 1)
            dot_brush = QBrush(_ACCENT)
            for a in acs:
                pos = project(float(a.lat), float(a.lon))
                if a.track_deg is not None:
                    rad = math.radians(float(a.track_deg))
                    dx = math.sin(rad)
                    dy = -math.cos(rad)      # 屏幕 y 向下，北对应 -y
                    p.setPen(QPen(_ACCENT, 2))
                    p.drawLine(pos, QPointF(pos.x() + 14.0 * dx,
                                            pos.y() + 14.0 * dy))
                p.setPen(dot_pen)
                p.setBrush(dot_brush)
                p.drawEllipse(pos, 5.0, 5.0)
                p.setPen(QPen(_TEXT))
                p.drawText(QPointF(pos.x() + 8.0, pos.y() - 6.0),
                           a.callsign or a.icao_hex)
            p.setBrush(Qt.NoBrush)
            return

        # 空态：底图照常显示。地图是通用时空底图（今后还要承载星下点、
        # 测向线、过境轨迹），不再往中央压「无 ADS-B 飞机」的空话牌。
        diag = self._diag_text
        if diag:
            vp = p.window()
            w, h = float(vp.width()), float(vp.height())
            f2 = QFont()
            f2.setPointSize(8)
            p.setFont(f2)
            fm2 = QFontMetrics(f2)
            avail = max(40.0, w - 16.0)
            one = fm2.elidedText(diag, Qt.ElideRight, int(avail))
            ty = max(8.0, h - 22.0)
            p.setPen(QPen(_TEXT))
            p.drawText(QRectF(8.0, ty, avail, 14.0),
                       int(Qt.AlignLeft | Qt.AlignVCenter), one)

    # ------------------------------------------------------------------ #
    def _refresh_table(self) -> None:
        acs = self._displayed()
        self.table.setVisible(bool(acs))
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


def _theme_map_style() -> str:
    """按当前桌面主题选底图：深色主题用暗色瓦片，浅色主题用标准瓦片。"""
    try:
        from themes import DEFAULT_THEME, get_theme
        name = str(DEFAULT_THEME)
        try:
            dark = bool(get_theme().dark)
        except Exception:
            dark = any(k in name for k in ("dark", "car", "figma", "contrast"))
        return style_for_dark(dark)
    except Exception:  # noqa: BLE001
        return "osm"
