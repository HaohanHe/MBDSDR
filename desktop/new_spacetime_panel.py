"""
MBDSDR 新时空差异化面板
==========================

把三块能力整合进一个 QWidget：

  1. AMR 实时调制识别 —— 大字号显示当前调制方式、置信度进度条、
     带宽 / 峰数 / SNR / 中心偏移一行指标；外部通过
     :meth:`NewSpacetimePanel.update_iq` 喂 IQ，或
     :meth:`NewSpacetimePanel.update_amr_result` 直接给结果。
  2. 卫星过境时间线 + 授时 —— 授时状态行（GNSS/NTP/未同步，颜色区分）、
     观测站经纬度/海拔输入框（不预设任何城市坐标）、"刷新过境"按钮同步拉
     TLE 并计算未来过境；过境表格列出升起/中天/落下/最大仰角/持续/多普勒。
  3. 频率-轨道关系图 —— 自绘 QWidget 散点图：X 轨道高度(km)，
     Y 下行频率(MHz)，每颗卫星一个点并标注名称。

设计约束（硬红线）：
- 原生 PySide6，图表全部自绘，不引入 matplotlib / pyqtgraph。
- 构造函数不联网、不连硬件、不做任何阻塞调用，offscreen 可秒级实例化。
- 观测站坐标由用户 / GNSS 填入，绝不硬编码任何城市。
- 面板本身不主动拉 IQ；"开始分析"只是一个状态标记。
"""
from __future__ import annotations

import os
import sys
from typing import List, Optional

import numpy as np

from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, QSize
from PySide6.QtGui import (
    QPainter, QPen, QBrush, QColor, QPalette, QFont,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLabel,
    QDoubleSpinBox, QPushButton, QProgressBar, QTableWidget,
    QTableWidgetItem, QHeaderView, QSizePolicy,
)

# ---------------------------------------------------------------------------
# 后端模块导入（兼容从仓库根 / desktop/ 两种方式运行）
# ---------------------------------------------------------------------------
try:
    from mbdsdr_ai.new_spacetime_tle import (
        TLEManager, TLEEntry, DEFAULT_SATELLITES,
    )
    from mbdsdr_ai.new_spacetime_amr import (
        AMRStreamAnalyzer, AMRStreamResult,
    )
    from mbdsdr_ai.new_spacetime_timeline import (
        TimelineEngine, SatellitePass, PassPoint,
        TimeSyncStatus, FreqOrbitPoint,
    )
except ImportError:  # pragma: no cover - 直接以脚本方式运行时
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from mbdsdr_ai.new_spacetime_tle import (  # type: ignore
        TLEManager, TLEEntry, DEFAULT_SATELLITES,
    )
    from mbdsdr_ai.new_spacetime_amr import (  # type: ignore
        AMRStreamAnalyzer, AMRStreamResult,
    )
    from mbdsdr_ai.new_spacetime_timeline import (  # type: ignore
        TimelineEngine, SatellitePass, PassPoint,
        TimeSyncStatus, FreqOrbitPoint,
    )


# ---------------------------------------------------------------------------
# 默认主题色值（与 themes.py DEFAULT_LIGHT 对齐）
# ---------------------------------------------------------------------------
COLOR_BG = "#F5F3EF"           # 主背景
COLOR_CARD = "#FFFFFF"         # 卡片
COLOR_TEXT = "#5B7B8C"         # 文字主色
COLOR_TEXT_SEC = "#8A9BA8"     # 文字次色
COLOR_BORDER = "#C8C0B4"       # 边框
COLOR_PRIMARY = "#C4845C"      # 强调
COLOR_SUCCESS = "#6BA89A"      # 同步 / 成功
COLOR_DANGER = "#B85C5C"       # 未同步 / 警示
COLOR_GRID = "#D8D2C8"         # 网格

_NO_SIGNAL_TEXT = "未检测到信号"
_NA = "--"


# ===========================================================================
# 区域 3：频率-轨道关系自绘散点图
# ===========================================================================
class FreqOrbitChart(QWidget):
    """X=轨道高度(km)，Y=下行频率(MHz) 的自绘散点图。

    纯 QPainter 绘制，不依赖任何第三方绘图库。
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("freqOrbitChart")
        self._points: List[FreqOrbitPoint] = []
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # 卡片白底，保证独立实例化时背景正确
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor(COLOR_CARD))
        self.setPalette(pal)

    def sizeHint(self) -> QSize:  # noqa: D401 - Qt 虚函数
        return QSize(360, 220)

    def set_data(self, points: Optional[List[FreqOrbitPoint]]) -> None:
        """设置散点数据并触发重绘。None 视作空。"""
        self._points = list(points or [])
        self.update()

    def has_data(self) -> bool:
        return len(self._points) > 0

    # ------------------------------------------------------------------
    def paintEvent(self, event):  # noqa: D401 - Qt 虚函数
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(COLOR_CARD))

        # 无数据：居中提示
        if not self._points:
            painter.setPen(QColor(COLOR_TEXT_SEC))
            f = painter.font()
            f.setPointSize(10)
            painter.setFont(f)
            painter.drawText(self.rect(), Qt.AlignCenter, "无数据")
            painter.end()
            return

        # 绘图区边距
        left, top, right, bottom = 48, 18, 16, 30
        plot = self.rect().adjusted(left, top, -right, -bottom)
        if plot.width() < 20 or plot.height() < 20:
            painter.end()
            return

        xs = [p.altitude_km for p in self._points]
        ys = [p.frequency_mhz for p in self._points]
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        # 防止零范围除零
        if xmax - xmin < 1e-6:
            xmax = xmin + 1.0
        if ymax - ymin < 1e-6:
            ymax = ymin + 1.0
        xpad = (xmax - xmin) * 0.08
        ypad = (ymax - ymin) * 0.12
        xmin -= xpad
        xmax += xpad
        ymin -= ypad
        ymax += ypad

        def sx(x: float) -> float:
            return plot.left() + (x - xmin) / (xmax - xmin) * plot.width()

        def sy(y: float) -> float:
            return plot.bottom() - (y - ymin) / (ymax - ymin) * plot.height()

        # 网格 + 边框（#C8C0B4）
        grid_pen = QPen(QColor(COLOR_BORDER))
        grid_pen.setWidth(1)
        painter.setPen(grid_pen)
        painter.drawRect(plot)
        for i in range(1, 5):
            gx = plot.left() + plot.width() * i / 5.0
            painter.drawLine(QPointF(gx, plot.top()), QPointF(gx, plot.bottom()))
            gy = plot.top() + plot.height() * i / 5.0
            painter.drawLine(QPointF(plot.left(), gy), QPointF(plot.right(), gy))

        # 坐标轴刻度文字（#5B7B8C）
        text_pen = QPen(QColor(COLOR_TEXT))
        painter.setPen(text_pen)
        tick_font = painter.font()
        tick_font.setPointSize(8)
        painter.setFont(tick_font)
        painter.drawText(QRectF(plot.left() - 44, plot.top() - 2, 40, 16),
                         Qt.AlignRight | Qt.AlignVCenter, f"{ymax:.0f}")
        painter.drawText(QRectF(plot.left() - 44, plot.bottom() - 14, 40, 16),
                         Qt.AlignRight | Qt.AlignVCenter, f"{ymin:.0f}")
        painter.drawText(QRectF(plot.left(), plot.bottom() + 3, 70, 16),
                         Qt.AlignLeft | Qt.AlignTop, f"{xmin:.0f} km")
        painter.drawText(QRectF(plot.right() - 70, plot.bottom() + 3, 70, 16),
                         Qt.AlignRight | Qt.AlignTop, f"{xmax:.0f} km")
        painter.drawText(QRectF(plot.left(), plot.bottom() + 18, plot.width(), 16),
                         Qt.AlignHCenter | Qt.AlignTop, "轨道高度 (km)")

        # 数据点（#C4845C）
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(COLOR_PRIMARY)))
        r = 5.0
        for p in self._points:
            cx, cy = sx(p.altitude_km), sy(p.frequency_mhz)
            painter.drawEllipse(QPointF(cx, cy), r, r)

        # 点标注名称（#5B7B8C）
        painter.setPen(text_pen)
        for p in self._points:
            cx, cy = sx(p.altitude_km), sy(p.frequency_mhz)
            painter.drawText(QRectF(cx + 8, cy - 9, 110, 16),
                             Qt.AlignLeft | Qt.AlignVCenter, p.name)

        painter.end()


# ===========================================================================
# 主面板
# ===========================================================================
class NewSpacetimePanel(QWidget):
    """新时空差异化面板：AMR 调制识别 + 卫星过境时间线/授时 + 频率-轨道图。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("newSpacetimePanel")

        # 后端引擎（纯对象，构造不联网）
        self._tle_mgr = TLEManager()
        self._engine = TimelineEngine()
        self._analyzer = AMRStreamAnalyzer()

        self._analyzing = False
        self._gnss_nmea: Optional[str] = None

        self._build_ui()
        self._apply_styles()

        # 可选：每 60s 本地刷新授时状态（try_ntp=False，不阻塞网络）。
        # 定时器首次触发在 60s 后，构造函数本身不做任何网络调用。
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(60_000)
        self._sync_timer.timeout.connect(self._refresh_time_sync_local)
        self._sync_timer.start()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        root.addWidget(self._build_amr_section())
        root.addWidget(self._build_timeline_section())
        root.addWidget(self._build_freq_orbit_section())
        root.addStretch(1)

    # ---- 区域 1：AMR 实时调制识别 ----
    def _build_amr_section(self) -> QGroupBox:
        box = QGroupBox("AMR 实时调制识别")
        box.setObjectName("amrGroup")
        lay = QVBoxLayout(box)
        lay.setSpacing(6)

        # 大字号当前调制方式
        self.amr_mod_label = QLabel(_NO_SIGNAL_TEXT)
        self.amr_mod_label.setObjectName("amrModLabel")
        self.amr_mod_label.setAlignment(Qt.AlignCenter)
        self.amr_mod_label.setStyleSheet(
            f"font-size: 20pt; font-weight: 700; color: {COLOR_TEXT_SEC};"
            f"padding: 6px;")
        lay.addWidget(self.amr_mod_label)

        # 置信度进度条
        self.amr_conf_bar = QProgressBar()
        self.amr_conf_bar.setRange(0, 100)
        self.amr_conf_bar.setValue(0)
        self.amr_conf_bar.setFormat("置信度 %p%")
        lay.addWidget(self.amr_conf_bar)

        # 一行指标：带宽 / 峰数 / SNR / 中心偏移
        metric_row = QHBoxLayout()
        self.bw_label = self._make_metric("带宽", _NA, metric_row)
        self.peak_label = self._make_metric("峰数", _NA, metric_row)
        self.snr_label = self._make_metric("SNR", _NA, metric_row)
        self.offset_label = self._make_metric("中心偏移", _NA, metric_row)
        lay.addLayout(metric_row)

        # 开始分析按钮（仅置标记，不拉 IQ）
        self.amr_start_btn = QPushButton("开始分析")
        self.amr_start_btn.setCheckable(True)
        self.amr_start_btn.toggled.connect(self._on_amr_toggled)
        lay.addWidget(self.amr_start_btn)

        return box

    @staticmethod
    def _make_metric(caption: str, value: str, row: QHBoxLayout) -> QLabel:
        cap = QLabel(f"{caption}:")
        cap.setStyleSheet(f"color: {COLOR_TEXT_SEC};")
        val = QLabel(value)
        val.setStyleSheet(f"color: {COLOR_TEXT}; font-weight: 600;")
        wrap = QWidget()
        wl = QHBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(cap)
        wl.addWidget(val)
        row.addWidget(wrap)
        return val

    # ---- 区域 2：卫星过境时间线 + 授时 ----
    def _build_timeline_section(self) -> QGroupBox:
        box = QGroupBox("卫星过境时间线 + 授时")
        box.setObjectName("timelineGroup")
        lay = QVBoxLayout(box)
        lay.setSpacing(6)

        # 授时状态行
        self.time_sync_label = QLabel("未同步")
        self.time_sync_label.setObjectName("timeSyncLabel")
        self.time_sync_label.setStyleSheet(
            f"color: {COLOR_DANGER}; font-weight: 600;")
        lay.addWidget(self.time_sync_label)

        # 观测站坐标输入（初始 0.0，不预设任何城市）
        obs_form = QFormLayout()
        self.lat_spin = QDoubleSpinBox()
        self.lat_spin.setRange(-90.0, 90.0)
        self.lat_spin.setDecimals(6)
        self.lat_spin.setSingleStep(0.0001)
        self.lat_spin.setValue(0.0)
        self.lon_spin = QDoubleSpinBox()
        self.lon_spin.setRange(-180.0, 180.0)
        self.lon_spin.setDecimals(6)
        self.lon_spin.setSingleStep(0.0001)
        self.lon_spin.setValue(0.0)
        self.alt_spin = QDoubleSpinBox()
        self.alt_spin.setRange(0.0, 10000.0)
        self.alt_spin.setDecimals(3)
        self.alt_spin.setSingleStep(1.0)
        self.alt_spin.setSuffix(" km")
        self.alt_spin.setValue(0.0)
        obs_form.addRow("纬度 (°):", self.lat_spin)
        obs_form.addRow("经度 (°):", self.lon_spin)
        obs_form.addRow("海拔 (km):", self.alt_spin)
        lay.addLayout(obs_form)

        # 刷新过境按钮
        self.refresh_btn = QPushButton("刷新过境")
        self.refresh_btn.clicked.connect(self.refresh_passes)
        lay.addWidget(self.refresh_btn)

        # 过境表格
        self.passes_table = QTableWidget(0, 7)
        self.passes_table.setHorizontalHeaderLabels(
            ["卫星", "升起(UTC)", "中天(UTC)", "落下(UTC)",
             "最大仰角", "持续(分)", "多普勒(Hz)"])
        self.passes_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch)
        self.passes_table.verticalHeader().setVisible(False)
        self.passes_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.passes_table.setSelectionBehavior(QTableWidget.SelectRows)
        lay.addWidget(self.passes_table)

        # 空数据提示
        self.passes_hint = QLabel("无数据")
        self.passes_hint.setStyleSheet(f"color: {COLOR_TEXT_SEC};")
        self.passes_hint.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.passes_hint)

        return box

    # ---- 区域 3：频率-轨道关系图 ----
    def _build_freq_orbit_section(self) -> QGroupBox:
        box = QGroupBox("频率-轨道关系")
        box.setObjectName("freqOrbitGroup")
        lay = QVBoxLayout(box)
        self.chart = FreqOrbitChart()
        lay.addWidget(self.chart)
        return box

    # ---- 独立实例化时的兜底 QSS（嵌入主窗口后会被 themes.py 覆盖）----
    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
            QWidget#newSpacetimePanel {{ background: {COLOR_BG}; }}
            QGroupBox {{
                background: {COLOR_CARD};
                border: 1px solid {COLOR_BORDER};
                border-radius: 8px;
                margin-top: 12px; padding-top: 8px;
                color: {COLOR_TEXT}; font-weight: 600;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin; left: 10px; padding: 0 4px;
            }}
            QPushButton {{
                background: {COLOR_CARD}; color: {COLOR_TEXT};
                border: 1px solid {COLOR_BORDER}; border-radius: 6px;
                padding: 6px 14px;
            }}
            QPushButton:hover {{ background: #F0EDE8; }}
            QPushButton:checked {{
                background: {COLOR_PRIMARY}; color: {COLOR_CARD};
                border-color: {COLOR_PRIMARY};
            }}
            QDoubleSpinBox {{
                background: {COLOR_CARD}; color: {COLOR_TEXT};
                border: 1px solid {COLOR_BORDER}; border-radius: 6px;
                padding: 3px 6px;
            }}
            QProgressBar {{
                background: {COLOR_GRID}; border: 1px solid {COLOR_BORDER};
                border-radius: 6px; text-align: center; color: {COLOR_TEXT};
            }}
            QProgressBar::chunk {{ background: {COLOR_PRIMARY}; border-radius: 5px; }}
            QTableWidget {{
                background: {COLOR_CARD}; color: {COLOR_TEXT};
                border: 1px solid {COLOR_BORDER}; border-radius: 6px;
                gridline-color: {COLOR_GRID};
            }}
            QHeaderView::section {{
                background: #FAF8F5; color: {COLOR_TEXT};
                border: none; border-bottom: 1px solid {COLOR_BORDER};
                padding: 4px 6px; font-weight: 600;
            }}
        """)

    # ==================================================================
    # 区域 1 对外接口
    # ==================================================================
    def update_iq(self, iq: np.ndarray, sample_rate: float,
                  center_freq: float = 0.0) -> None:
        """外部喂一段复基带 IQ，触发一次 AMR 分析并刷新显示。"""
        result = self._analyzer.analyze_iq(iq, sample_rate, center_freq)
        self.update_amr_result(result)

    def update_amr_result(self, result: AMRStreamResult) -> None:
        """直接用一个分析结果刷新 AMR 区域显示。"""
        if result.signal_detected:
            self.amr_mod_label.setText(result.modulation)
            self.amr_mod_label.setStyleSheet(
                f"font-size: 20pt; font-weight: 700; color: {COLOR_PRIMARY};"
                f"padding: 6px;")
            self.amr_conf_bar.setValue(int(round(max(0.0, min(1.0, result.confidence)) * 100)))
            self.bw_label.setText(f"{result.bandwidth_hz / 1e3:.1f} kHz")
            self.peak_label.setText(str(result.peak_count))
            self.snr_label.setText(f"{result.snr_db:.1f} dB")
            self.offset_label.setText(f"{result.center_offset_hz:+.0f} Hz")
        else:
            self.amr_mod_label.setText(_NO_SIGNAL_TEXT)
            self.amr_mod_label.setStyleSheet(
                f"font-size: 20pt; font-weight: 700; color: {COLOR_TEXT_SEC};"
                f"padding: 6px;")
            self.amr_conf_bar.setValue(0)
            self.bw_label.setText(_NA)
            self.peak_label.setText(_NA)
            self.snr_label.setText(_NA)
            self.offset_label.setText(_NA)

    def _on_amr_toggled(self, checked: bool) -> None:
        """仅切换面板内的分析状态标记；面板本身不主动拉 IQ。"""
        self._analyzing = checked
        self.amr_start_btn.setText("停止分析" if checked else "开始分析")

    # ==================================================================
    # 区域 2 对外接口
    # ==================================================================
    def set_observer(self, lat: float, lon: float, alt_km: float) -> None:
        """由外部（GNSS / 手动）设置观测站坐标。"""
        self.lat_spin.setValue(float(lat))
        self.lon_spin.setValue(float(lon))
        self.alt_spin.setValue(float(alt_km))

    def set_gnss_nmea(self, nmea: str) -> None:
        """注入一段 GNSS RMC NMEA，本地计算授时状态（不尝试 NTP）。"""
        self._gnss_nmea = nmea
        self._refresh_time_sync_local()

    def _refresh_time_sync_local(self) -> None:
        """本地授时刷新：优先已注入的 GNSS，否则显式"未同步"（不联网）。"""
        status = self._engine.compute_time_sync(
            gnss_nmea=self._gnss_nmea, try_ntp=False)
        self._apply_time_sync(status)

    def _apply_time_sync(self, status: TimeSyncStatus) -> None:
        if status.synchronized:
            src = (status.source or "").upper()
            self.time_sync_label.setText(f"{src} · {status.detail}")
            self.time_sync_label.setStyleSheet(
                f"color: {COLOR_SUCCESS}; font-weight: 600;")
        else:
            self.time_sync_label.setText(status.detail or "未同步")
            self.time_sync_label.setStyleSheet(
                f"color: {COLOR_DANGER}; font-weight: 600;")

    def refresh_passes(self) -> None:
        """同步拉取 DEFAULT_SATELLITES 的 TLE 并计算过境 / 频率-轨道关系。

        会阻塞若干秒（TLE fetch 带超时），仅由按钮点击触发。
        """
        lat = self.lat_spin.value()
        lon = self.lon_spin.value()
        alt_km = self.alt_spin.value()

        # 拉 TLE（失败的卫星跳过，绝不伪造）
        sats: List[tuple] = []
        for name, catnr in DEFAULT_SATELLITES:
            ent = self._tle_mgr.fetch(catnr, name)
            if ent is not None:
                sats.append((ent.name, ent.line1, ent.line2))

        if not sats:
            self._populate_passes([])
            self.chart.set_data([])
            return

        passes = self._engine.compute_passes(
            observer_lat=lat, observer_lon=lon, observer_alt=alt_km,
            satellites=sats, hours_ahead=24.0, min_elevation=5.0, step_sec=60.0,
        )
        self._populate_passes(passes)

        # 顺手刷新频率-轨道散点（无频率数据时频率为 0）
        fo_points = self._engine.compute_freq_orbit(sats)
        self.chart.set_data(fo_points)

    def _populate_passes(self, passes: List[SatellitePass]) -> None:
        self.passes_table.setRowCount(0)
        if not passes:
            self.passes_hint.setVisible(True)
            return
        self.passes_hint.setVisible(False)
        self.passes_table.setRowCount(len(passes))
        for row, p in enumerate(passes):
            self._set_item(row, 0, p.name)
            self._set_item(row, 1, self._fmt_dt(p.rise_time))
            self._set_item(row, 2, self._fmt_dt(p.max_time))
            self._set_item(row, 3, self._fmt_dt(p.set_time))
            self._set_item(row, 4, f"{p.max_elevation:.1f}°")
            self._set_item(row, 5, f"{p.duration_sec / 60.0:.1f}")
            self._set_item(row, 6, f"{p.max_doppler_hz:+.0f}")

    def _set_item(self, row: int, col: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)
        self.passes_table.setItem(row, col, item)

    @staticmethod
    def _fmt_dt(dt) -> str:
        if dt is None:
            return _NA
        try:
            return dt.strftime("%m-%d %H:%M")
        except (AttributeError, ValueError):
            return _NA
