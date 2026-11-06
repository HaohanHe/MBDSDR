# SPDX-License-Identifier: MIT
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
import threading
from typing import List, Optional

import numpy as np

from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, QSize, Signal
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

# 采样步长引用 sat_passes 的单一事实源（不在本面板另写 60/2 之类字面量）。
from mbdsdr_ai.sat_passes import PASS_COARSE_STEP_S  # noqa: E402


# 以包方式从仓库根 import（如 import desktop.new_spacetime_panel）时本目录不在
# sys.path 上，把 desktop/ 补进去，保证 tokens 等兄弟模块可解析（与 main.py 一致）。
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from tokens import tokens

# 过境筛选门只从 settings 读取（与天空图同一组入口，见 docs/settings-inventory.md）。
try:
    from settings import (
        PASS_LOW_ELEVATION_FLOOR_DEG,
        effective_pass_min_elevation_deg,
        effective_pass_visibility_gate_deg,
        effective_pass_window_hours,
        effective_show_low_elevation_passes,
    )
except ImportError:  # pragma: no cover - 兼容 desktop 目录直接上 sys.path
    from .settings import (  # type: ignore
        PASS_LOW_ELEVATION_FLOOR_DEG,
        effective_pass_min_elevation_deg,
        effective_pass_visibility_gate_deg,
        effective_pass_window_hours,
        effective_show_low_elevation_passes,
    )

# 站址只读口径：显式 (0,0)/海上坐标照常算，但必须把 warning 展示给用户。
from mbdsdr_ai.station import station_from_user, trust_for_calculation  # noqa: E402

# ---------------------------------------------------------------------------
# 默认主题色值（从 tokens.py 语义化取色，与 light 主题对齐）
# ---------------------------------------------------------------------------
_t = tokens()
COLOR_BG = _t.COLORS["light_bg"]           # 主背景
COLOR_CARD = _t.COLORS["light_card"]       # 卡片
COLOR_TEXT = _t.COLORS["light_text"]       # 文字主色
COLOR_TEXT_SEC = _t.COLORS["light_text_sub"]  # 文字次色
COLOR_BORDER = _t.COLORS["light_border"]   # 边框
COLOR_PRIMARY = _t.COLORS["light_accent"]  # 强调
COLOR_SUCCESS = _t.COLORS["success"]       # 同步 / 成功
COLOR_DANGER = _t.COLORS["danger"]         # 未同步 / 警示
COLOR_GRID = _t.COLORS["light_grid"]       # 网格
COLOR_HOVER = _t.COLORS["light_hover"]     # 按钮 hover
COLOR_BG_ALT = _t.COLORS["light_bg_alt"]   # 次级背景

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

    def plottable_points(self) -> List[FreqOrbitPoint]:
        """可绘制的点：频率未知（``None``）的不进入绘制循环。

        未知频率不是 0 MHz；若照画会落在 Y=0 的坐标 0 处，与真实频点混淆。
        """
        return [p for p in self._points if p.frequency_mhz is not None]

    def has_data(self) -> bool:
        return len(self.plottable_points()) > 0

    # ------------------------------------------------------------------
    def paintEvent(self, event):  # noqa: D401 - Qt 虚函数
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(COLOR_CARD))

        points = self.plottable_points()
        # 无数据：居中提示（区分「一颗都没算到」与「有星但频率未知」）
        if not points:
            painter.setPen(QColor(COLOR_TEXT_SEC))
            f = painter.font()
            f.setPointSize(10)
            painter.setFont(f)
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "无频率数据" if self._points else "无数据")
            painter.end()
            return

        # 绘图区边距
        left, top, right, bottom = 48, 18, 16, 30
        plot = self.rect().adjusted(left, top, -right, -bottom)
        if plot.width() < 20 or plot.height() < 20:
            painter.end()
            return

        xs = [p.altitude_km for p in points]
        ys = [p.frequency_mhz for p in points]
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
        for p in points:
            cx, cy = sx(p.altitude_km), sy(p.frequency_mhz)
            painter.drawEllipse(QPointF(cx, cy), r, r)

        # 点标注名称（#5B7B8C）
        painter.setPen(text_pen)
        for p in points:
            cx, cy = sx(p.altitude_km), sy(p.frequency_mhz)
            painter.drawText(QRectF(cx + 8, cy - 9, 110, 16),
                             Qt.AlignLeft | Qt.AlignVCenter, p.name)

        painter.end()


# ===========================================================================
# 主面板
# ===========================================================================
class NewSpacetimePanel(QWidget):
    """新时空差异化面板：AMR 调制识别 + 卫星过境时间线/授时 + 频率-轨道图。"""

    # 后台刷新结果回到 GUI 线程（显式排队：worker 是普通 Python 线程，直接
    # 调 GUI 会踩 Qt 的线程亲和性规则）。
    refresh_finished = Signal(object)

    def __init__(self, parent: Optional[QWidget] = None, settings=None):
        super().__init__(parent)
        self.setObjectName("newSpacetimePanel")

        # 过境筛选的读取入口（DesktopSettings / dict）；None 时用 schema 默认值。
        self._settings = settings
        # 最近一次刷新实际使用的门（_populate_passes 标注低仰角用）
        self._pass_min_elevation_deg = effective_pass_min_elevation_deg(settings)
        self._pass_visibility_gate_deg = effective_pass_visibility_gate_deg(settings)
        self._pass_show_low = effective_show_low_elevation_passes(settings)
        self._pass_window_hours = effective_pass_window_hours(settings)

        # 后端引擎（纯对象，构造不联网）
        self._tle_mgr = TLEManager()
        self._engine = TimelineEngine()
        self._analyzer = AMRStreamAnalyzer()

        self._analyzing = False
        self._gnss_nmea: Optional[str] = None
        # 过境刷新：网络在后台线程，重入保护 + 按钮显式「更新中…」。
        self._refreshing = False
        self._refresh_thread: Optional[threading.Thread] = None
        self.refresh_finished.connect(self._on_refresh_result,
                                      Qt.QueuedConnection)
        # 观测站「是否已配置」用显式状态位，不用坐标值当哨兵：
        # (0,0) 是几内亚湾的真实坐标，必须能正常算过境。None/False = 未配置。
        self._observer_configured = False
        self._setting_observer = False  # 程序写入 spin 时不误判为"用户已配置"

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

        # 观测站坐标输入。spin 只能显示数值（0.0），"是否配置"由
        # _observer_configured 显式记录：0.0/0.0 是合法站址，不是哨兵。
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
        self.lat_spin.valueChanged.connect(self._on_observer_edited)
        self.lon_spin.valueChanged.connect(self._on_observer_edited)
        obs_form.addRow("纬度 (°):", self.lat_spin)
        obs_form.addRow("经度 (°):", self.lon_spin)
        obs_form.addRow("海拔 (km):", self.alt_spin)
        lay.addLayout(obs_form)

        # 刷新过境按钮
        self.refresh_btn = QPushButton("刷新过境")
        self.refresh_btn.clicked.connect(self.refresh_passes_async)
        lay.addWidget(self.refresh_btn)

        # 筛选口径明示：低于峰值门的过境不会出现在表里（用户必须能看见这条规则）
        self.passes_filter_label = QLabel(
            self._pass_filter_text(self._pass_min_elevation_deg,
                                   self._pass_show_low,
                                   self._pass_window_hours))
        self.passes_filter_label.setObjectName("hintLabel")
        self.passes_filter_label.setWordWrap(True)
        lay.addWidget(self.passes_filter_label)

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
            QPushButton:hover {{ background: {COLOR_HOVER}; }}
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
                background: {COLOR_BG_ALT}; color: {COLOR_TEXT};
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
    def set_observer(self, lat: Optional[float], lon: Optional[float],
                     alt_km: float = 0.0) -> None:
        """由外部（GNSS / 手动）设置观测站坐标。

        ``lat``/``lon`` 为 ``None`` 表示**未配置**（真实缺省）；``0.0`` 是
        合法坐标（几内亚湾），必须照常计算，不得当占位哨兵。
        """
        self._setting_observer = True
        try:
            configured = lat is not None and lon is not None
            self._observer_configured = configured
            self.lat_spin.setValue(float(lat) if configured else 0.0)
            self.lon_spin.setValue(float(lon) if configured else 0.0)
            self.alt_spin.setValue(float(alt_km or 0.0))
        finally:
            self._setting_observer = False

    def _on_observer_edited(self, _value: float) -> None:
        """用户在 spin 里改经纬度即视为显式配置（含手输 0.0/0.0）。"""
        if not self._setting_observer:
            self._observer_configured = True

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

    def set_settings(self, settings) -> None:
        """注入/替换配置源；下一次 refresh_passes 起按新设置筛选。"""
        self._settings = settings

    def _resolved_settings(self):
        """显式注入优先；否则取宿主（主窗口）的 settings 实例。

        主窗口用 `self.settings = DesktopSettings.load()` 作为唯一实例，
        设置面板改的就是它（内存值立即生效，不依赖 500ms 防抖落盘）。
        """
        if self._settings is not None:
            return self._settings
        return getattr(self.window(), "settings", None)

    @staticmethod
    def _pass_filter_text(min_elevation_deg: float, show_low: bool,
                          window_hours: int) -> str:
        """列表筛选口径文案（用户必须能一眼看见「按峰值仰角」而非「当前仰角」）。"""
        floor = PASS_LOW_ELEVATION_FLOOR_DEG
        head = f"未来 {window_hours:g} 小时；列表按峰值仰角 ≥ {min_elevation_deg:g}° 筛选"
        if show_low and min_elevation_deg > floor:
            return (f"{head}；「显示低仰角过境」已开启：额外列出 {floor:g}–"
                    f"{min_elevation_deg:g}° 的过境并标注「低仰角」")
        if show_low:
            return (f"{head}；「显示低仰角过境」已开启（当前设置不低于 {floor:g}°，"
                    "没有额外低仰角区间）")
        return (f"{head}（峰值低于该值的过境不列出；要列 {floor:g}–"
                f"{min_elevation_deg:g}° 请打开「显示低仰角过境」）")

    def _begin_refresh(self) -> None:
        self._refreshing = True
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("更新中…")

    def _end_refresh(self) -> None:
        self._refreshing = False
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("刷新过境")

    def refresh_passes(self) -> None:
        """同步计算过境（直接调用方/测试用；按钮走异步入口）。

        峰值筛选门读 settings.effective_pass_*（与天空图过境列表同一组入口）。
        网络与计算都在本线程内完成——GUI 按钮不接这里，见
        :meth:`refresh_passes_async`。
        """
        if self._refreshing:
            return
        self._begin_refresh()
        try:
            inputs = self._snapshot_refresh_inputs()
            self._apply_refresh_result(self._compute_refresh(inputs))
        finally:
            self._end_refresh()

    def refresh_passes_async(self) -> None:
        """按钮入口：TLE 抓取与过境计算放后台线程，GUI 线程不联网。

        重入保护：刷新在跑时再次点击直接忽略；按钮显式「更新中…」。
        结果经 ``refresh_finished`` 排队回 GUI 线程后再更新控件。
        """
        if self._refreshing:
            return
        self._begin_refresh()
        inputs = self._snapshot_refresh_inputs()

        def _work() -> None:
            try:
                result = self._compute_refresh(inputs)
            except Exception as e:  # noqa: BLE001 - 必须带回 GUI 线程说明原因
                result = {"error": f"{type(e).__name__}: {e}"}
            self.refresh_finished.emit(result)

        self._refresh_thread = threading.Thread(
            target=_work, name="spacetime-pass-refresh", daemon=True)
        self._refresh_thread.start()

    def _on_refresh_result(self, result: dict) -> None:
        try:
            if result.get("error"):
                self.passes_hint.setText(f"刷新失败：{result['error']}")
                self.passes_hint.setVisible(True)
                self._populate_passes([])
                self.chart.set_data([])
            else:
                self._apply_refresh_result(result)
        finally:
            self._end_refresh()

    def _snapshot_refresh_inputs(self) -> dict:
        """GUI 线程里把设置/站址拍成纯数据：worker 不碰任何控件。"""
        settings = self._resolved_settings()
        return {
            "min_deg": effective_pass_min_elevation_deg(settings),
            "gate": effective_pass_visibility_gate_deg(settings),
            "show_low": effective_show_low_elevation_passes(settings),
            "window_hours": effective_pass_window_hours(settings),
            "lat": float(self.lat_spin.value()),
            "lon": float(self.lon_spin.value()),
            "alt_km": float(self.alt_spin.value()),
            "observer_configured": bool(self._observer_configured),
        }

    def _compute_refresh(self, inputs: dict) -> dict:
        """纯计算（可跑在后台线程）：抓 TLE、算过境、算频率-轨道散点。"""
        base_filter = self._pass_filter_text(
            inputs["min_deg"], inputs["show_low"], inputs["window_hours"])
        out = {
            "min_deg": inputs["min_deg"],
            "show_low": inputs["show_low"],
            "window_hours": inputs["window_hours"],
            "filter_text": base_filter,
            "passes": [],
            "freq_points": [],
            "passes_hint": None,
        }
        # 观测站是过境的必要条件。判定用显式状态位：None/未配置才提示，
        # 绝不能拿 0.0 当哨兵（0.0/0.0 是合法站址）。未配置时不白跑网络请求。
        if not inputs["observer_configured"]:
            out["passes_hint"] = (
                "未配置观测站位置：请在纬度/经度输入框填写地面站坐标")
            return out

        # 拉 TLE（失败的卫星跳过，绝不伪造）
        sats: List[tuple] = []
        for name, catnr in DEFAULT_SATELLITES:
            ent = self._tle_mgr.fetch(catnr, name)
            if ent is not None:
                sats.append((ent.name, ent.line1, ent.line2))

        if not sats:
            # 诚实说明"为什么没有"：把抓取链路的失败码/原因/建议原样带到界面，
            # 不再用与 code 无关的固定句子（离线/断网/限流/解析失败各有话术）。
            fails = []
            for name, catnr in DEFAULT_SATELLITES:
                rec = self._tle_mgr.last_failure(catnr)
                if rec:
                    fails.append((name, rec))
            if fails:
                names = "、".join(n for n, _ in fails[:3])
                more = f" 等 {len(fails)} 颗" if len(fails) > 3 else ""
                codes = []
                for _, rec in fails:
                    code = str(rec.get("code") or "tle_unknown")
                    if code not in codes:
                        codes.append(code)
                first = fails[0][1]
                text = (f"未获取 TLE：{names}{more}。"
                        f"{first.get('code')}：{first.get('label')} — "
                        f"{first.get('reason')}；建议：{first.get('action')}")
                if len(codes) > 1:
                    text += f"（本轮共 {len(codes)} 类失败码：{'、'.join(codes)}）"
            else:
                text = ("未获取 TLE：抓取链路未记录失败码/原因（请查 TLE 源健康度与日志）；"
                        "建议：连接网络后重试或导入离线星历。")
            out["passes_hint"] = text
            return out

        gate = inputs["gate"]
        passes = self._engine.compute_passes(
            observer_lat=inputs["lat"], observer_lon=inputs["lon"],
            observer_alt=inputs["alt_km"],
            satellites=sats, hours_ahead=float(inputs["window_hours"]),
            min_elevation=gate, step_sec=PASS_COARSE_STEP_S,
        )
        if not passes:
            # 有 TLE 但确实没有过境：别沿用上一轮"没网络"的旧文案
            out["passes_hint"] = (
                f"未来 {inputs['window_hours']:g} 小时内无过境"
                f"（门限 {gate:g}°"
                + ("，含低仰角" if inputs["show_low"] else "")
                + f"，窗口 {inputs['window_hours']:g} 小时）")
        out["passes"] = passes

        # 站址与 TLE 的疑问必须进提示区（表格与 AI 结果已各自留痕，但用户
        # 最先看的是这一行；只写日志/返回字段等于没提示）。
        station_ev = trust_for_calculation(
            station_from_user(inputs["lat"], inputs["lon"],
                              inputs["alt_km"] * 1000.0))
        lines = []
        if passes:
            lines.append(self._pass_time_basis_line(passes))
        lines.extend(self._pass_warning_lines(passes, station_ev))
        if lines:
            out["filter_text"] = base_filter + "\n" + "\n".join(lines)

        # 顺手刷新频率-轨道散点（无频率数据时频率为 0）
        out["freq_points"] = self._engine.compute_freq_orbit(sats)
        return out

    def _apply_refresh_result(self, result: dict) -> None:
        """GUI 线程：把计算结果落到控件（文案与表格都只在这里更新）。"""
        self._pass_min_elevation_deg = result["min_deg"]
        self._pass_show_low = result["show_low"]
        self._pass_window_hours = result["window_hours"]
        self.passes_filter_label.setText(result["filter_text"])
        if result.get("passes_hint"):
            self.passes_hint.setText(result["passes_hint"])
        self._populate_passes(result["passes"])
        self.chart.set_data(result["freq_points"])

    def _pass_time_basis_line(self, passes: List[SatellitePass]) -> str:
        """标明「这些时刻是怎么算出来的」——来源与精度。

        同一份界面里绝不能出现两种精度却不说明：本面板用 TimelineEngine
        的 60 秒网格（无细扫），AI/工具链走 sat_passes 的 60 秒粗扫 + 2 秒
        细扫 + 二分。实测偏差（60 s 网格 vs 2 s 细扫，24 小时 28 条过境）：
        峰值时刻最大 30 秒、升落最大 58 秒、峰值仰角最大差 7.6°。
        """
        steps = [getattr(p, "sampling_step_s", 0.0) or 0.0 for p in passes]
        step = max(steps) if steps else PASS_COARSE_STEP_S
        return (f"时刻来源：本面板 TimelineEngine（{step:g} 秒网格，未细扫）；"
                "峰值时刻偏差 ≤30 秒、升落 ≤58 秒、峰值仰角最大差 7.6°"
                "（不是卫星位置精度）。")

    def _pass_warning_lines(self, passes: List[SatellitePass],
                            station_ev: dict) -> List[str]:
        """把「用户必须知道但不会自己发现」的两类提醒压成文案行。

        - 站址可疑（显式 (0,0)/海上坐标）：照常计算，但先说清位置可疑；
        - TLE 过期/失效/年龄未知：必须给出年龄、分级和时刻精度口径，
          否则过境列表看起来和新鲜星历一模一样。

        文案不在这里另写：每条过境的 ``accuracy_note`` 已由
        ``tle_integrity.accuracy`` 按年龄判定表给出（≤7 天 / 8–30 /
        31–90 / >90 / 未知），这里只取最严重的一条原文贴上。
        """
        lines: List[str] = []
        sw = (station_ev or {}).get("station_warning")
        if sw:
            lines.append(f"站址提醒：{sw}")

        stale = {}
        for p in passes:
            tier = getattr(p, "tle_tier", "")
            if tier not in ("expired", "dead", "unknown"):
                continue
            prev = stale.get(p.name)
            if prev is None or ((p.tle_age_days or 0)
                                > (prev.tle_age_days or 0)):
                stale[p.name] = p
        if not stale:
            return lines

        _risk_order = {"low": 0, "moderate": 1, "high": 2, "unusable": 3}
        worst = max(
            stale.values(),
            key=lambda p: (_risk_order.get(getattr(p, "accuracy_risk", ""), -1),
                           p.tle_age_days or 0))
        note = getattr(worst, "accuracy_note", "") or ""
        if not note:
            # 数据层缺文案时宁可显式说缺，也不假装新鲜
            note = "TLE 年龄超过可用范围：轨道不可信，必须刷新星历。"
        names = "、".join(stale.keys())
        lines.append(
            f"TLE 提醒：{len(stale)} 颗（{names}，逐条年龄见卫星名后标注）。"
            f"{note}")
        return lines

    def _populate_passes(self, passes: List[SatellitePass]) -> None:
        self.passes_table.setRowCount(0)
        if not passes:
            self.passes_hint.setVisible(True)
            return
        self.passes_hint.setVisible(False)
        self.passes_table.setRowCount(len(passes))
        for row, p in enumerate(passes):
            # 低仰角 = 峰值低于用户设置门（只有开关打开时才会进列表）
            low = (self._pass_show_low
                   and p.max_elevation < self._pass_min_elevation_deg)
            name = f"{p.name}（低仰角）" if low else p.name
            tier = getattr(p, "tle_tier", "")
            if tier in ("expired", "dead"):
                age = (f" {p.tle_age_days:.0f} 天"
                       if p.tle_age_days is not None else "")
                name += f"（TLE {p.tle_tier_label or '过期'}{age}）"
            elif tier == "unknown":
                name += "（TLE 年龄未知）"
            self._set_item(row, 0, name)
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
