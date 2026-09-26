"""
MBDSDR 卫星闭环跟踪面板
=========================
在"射频天空"图之外提供一个独立控制面板：

  - QComboBox 选择已加载 TLE 卫星；
  - 下行频率输入框（MHz，按卫星名自动套用标准下行频率）；
  - "开始跟踪 / 停止跟踪"按钮（触发 main_window 的 500ms 自动调谐闭环）；
  - "更新 TLE"按钮（从 Celestrak 在线刷新）；
  - 实时显示：方位角 / 仰角 / 多普勒偏移 / 校正后接收频率 / 斜距；
  - 仰角指示器（0-90° 进度条）：仰角 < 5° 变红并提示"过境结束"。

本面板只负责 UI 展示与发信号，真正的轨道计算与 SDR 调谐闭环在
main_window（持有 mbdsdr_ai.sat_tracker.SatelliteTracker）。
"""
from __future__ import annotations

from typing import Optional, Dict

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QComboBox,
    QDoubleSpinBox, QPushButton, QProgressBar, QGroupBox, QMessageBox,
)

# 过境结束阈值（与 sat_tracker.DEFAULT_MIN_ELEVATION_DEG 一致）。
_MIN_ELEVATION_DEG = 5.0


class SatTrackPanel(QFrame):
    """卫星闭环自动跟踪控制面板。

    Signals
    -------
    track_requested(str, float):
        用户点"开始跟踪"，携带 (卫星名, 下行频率 Hz)。
    stop_requested():
        用户点"停止跟踪"。
    update_tle_requested():
        用户点"更新 TLE"。
    """

    track_requested = Signal(str, float)
    stop_requested = Signal()
    update_tle_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("satTrackPanel")
        self.setFrameShape(QFrame.StyledPanel)
        self._tracking = False
        self._build_ui()
        self._apply_styles()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        title = QLabel("卫星闭环自动跟踪")
        title.setStyleSheet("font-size: 11pt; font-weight: 600; color: #5B7B8C;")
        root.addWidget(title)

        self.tle_date_label = QLabel("TLE 日期: --")
        self.tle_date_label.setStyleSheet("color: #8a9aa5; font-size: 8.5pt;")
        root.addWidget(self.tle_date_label)

        # ---- 卫星选择 + 下行频率 ----
        sel_box = QGroupBox("目标卫星")
        form = QFormLayout(sel_box)
        self.sat_combo = QComboBox()
        self.sat_combo.setMinimumWidth(220)
        self.sat_combo.currentTextChanged.connect(self._on_sat_selected)
        form.addRow("卫星:", self.sat_combo)

        self.freq_spin = QDoubleSpinBox()
        self.freq_spin.setRange(100.0, 2000.0)      # MHz
        self.freq_spin.setDecimals(4)
        self.freq_spin.setSingleStep(0.0025)
        self.freq_spin.setSuffix(" MHz")
        self.freq_spin.setValue(137.620)
        form.addRow("下行频率:", self.freq_spin)
        root.addWidget(sel_box)

        # ---- 跟踪按钮 ----
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("开始跟踪")
        self.stop_btn = QPushButton("停止跟踪")
        self.stop_btn.setEnabled(False)
        self.update_tle_btn = QPushButton("更新 TLE")
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addWidget(self.update_tle_btn)
        root.addLayout(btn_row)

        self.start_btn.clicked.connect(self._on_start_clicked)
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        self.update_tle_btn.clicked.connect(self.update_tle_requested.emit)

        # ---- 实时数据 ----
        data_box = QGroupBox("实时状态")
        dform = QFormLayout(data_box)
        self.az_label = QLabel("--")
        self.el_label = QLabel("--")
        self.dop_label = QLabel("--")
        self.corr_label = QLabel("--")
        self.range_label = QLabel("--")
        self.state_label = QLabel("空闲")
        self.state_label.setStyleSheet("font-weight: 600;")
        dform.addRow("方位角:", self.az_label)
        dform.addRow("仰角:", self.el_label)
        dform.addRow("多普勒:", self.dop_label)
        dform.addRow("校正后频率:", self.corr_label)
        dform.addRow("距离:", self.range_label)
        dform.addRow("状态:", self.state_label)
        root.addWidget(data_box)

        # ---- 仰角指示器（0-90° 进度条）----
        el_box = QGroupBox("仰角指示器")
        el_layout = QVBoxLayout(el_box)
        self.el_bar = QProgressBar()
        self.el_bar.setRange(0, 90)
        self.el_bar.setValue(0)
        self.el_bar.setTextVisible(True)
        self.el_bar.setFormat("仰角 %v° / 90°")
        el_layout.addWidget(self.el_bar)
        root.addWidget(el_box)

        root.addStretch(1)

    def _apply_styles(self):
        self.setStyleSheet("""
            QFrame#satTrackPanel { background: #F5F3EF; border: 1px solid #d8d2c8; }
            QGroupBox { color: #5B7B8C; font-weight: 600; border: 1px solid #d8d2c8;
                border-radius: 4px; margin-top: 8px; padding-top: 6px; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
            QPushButton { background: #5B7B8C; color: #F5F3EF; border: none;
                border-radius: 3px; padding: 4px 10px; font-size: 9pt; }
            QPushButton:hover { background: #C4845C; }
            QPushButton:disabled { background: #b9c2c7; color: #e6e6e6; }
            QLabel { color: #3d4a52; }
        """)

    # ------------------------------------------------------------ 对外接口
    def populate_satellites(self, names):
        """填充卫星下拉框；保持当前选中项尽量不变。"""
        prev = self.sat_combo.currentText()
        self.sat_combo.blockSignals(True)
        self.sat_combo.clear()
        for n in names:
            self.sat_combo.addItem(n)
        if prev:
            idx = self.sat_combo.findText(prev)
            if idx >= 0:
                self.sat_combo.setCurrentIndex(idx)
        self.sat_combo.blockSignals(False)

    def set_tle_date(self, date_str: str):
        self.tle_date_label.setText(f"TLE 日期: {date_str}")

    def set_tracking_state(self, tracking: bool):
        self._tracking = tracking
        self.start_btn.setEnabled(not tracking)
        self.stop_btn.setEnabled(tracking)
        self.sat_combo.setEnabled(not tracking)
        self.freq_spin.setEnabled(not tracking)
        if not tracking:
            self.state_label.setText("已停止")
            self.state_label.setStyleSheet("color: #8a9aa5; font-weight:600;")

    def set_observer_ready(self, ready: bool):
        """观测者位置是否就绪；未就绪时开始按钮置灰并提示。"""
        if not ready and not self._tracking:
            self.start_btn.setEnabled(False)
        else:
            self.start_btn.setEnabled(not self._tracking)

    def update_position(self, pos: Dict[str, object]):
        """用 tracker.current_position() 的结果刷新显示。"""
        if not pos.get("valid"):
            self.az_label.setText("--")
            self.el_label.setText("--")
            self.dop_label.setText("--")
            self.corr_label.setText("--")
            self.range_label.setText("--")
            self.el_bar.setValue(0)
            self._set_el_color("#b9c2c7")
            return

        az = float(pos["azimuth"])
        el = float(pos["elevation"])
        dop = float(pos["doppler_hz"])
        corr = float(pos["corrected_freq_hz"])
        rng = float(pos["range_km"])

        self.az_label.setText(f"{az:6.1f}°")
        self.el_label.setText(f"{el:6.1f}°")
        self.dop_label.setText(f"{dop:+8.0f} Hz")
        self.corr_label.setText(f"{corr/1e6:9.4f} MHz")
        self.range_label.setText(f"{rng:7.0f} km")

        # 仰角条：钳到 0..90
        el_bar_val = int(max(0.0, min(90.0, el)))
        self.el_bar.setValue(el_bar_val)

        if el < _MIN_ELEVATION_DEG:
            # 仰角 < 5°：变红 + 提示过境结束
            self._set_el_color("#B85C5C")
            self.state_label.setText("过境结束 (仰角<5°)")
            self.state_label.setStyleSheet("color:#B85C5C; font-weight:600;")
        else:
            self._set_el_color("#6BA89A")
            self.state_label.setText("跟踪中")
            self.state_label.setStyleSheet("color:#6BA89A; font-weight:600;")

    def _set_el_color(self, hex_color: str):
        self.el_bar.setStyleSheet(
            f"QProgressBar {{ border:1px solid #d8d2c8; border-radius:3px; "
            f"text-align:center; background:#eceae4; color:#3d4a52; }}"
            f"QProgressBar::chunk {{ background:{hex_color}; }}"
        )

    # ------------------------------------------------------------ 内部槽
    def _on_sat_selected(self, name: str):
        """用户在下拉框选了卫星：由 main_window 注入下行频率。这里只发出查询请求，
        实际频率回填由 main_window 通过 set_downlink_mhz 完成。"""
        self.satellite_changed.emit(name)

    # 额外信号：卫星切换时通知 main_window 刷新下行频率
    satellite_changed = Signal(str)

    def set_downlink_mhz(self, mhz: float):
        if mhz > 0:
            self.freq_spin.blockSignals(True)
            self.freq_spin.setValue(mhz)
            self.freq_spin.blockSignals(False)

    def current_downlink_hz(self) -> float:
        return self.freq_spin.value() * 1e6

    def _on_start_clicked(self):
        name = self.sat_combo.currentText().strip()
        if not name:
            QMessageBox.warning(self, "卫星跟踪", "请先选择一颗卫星。")
            return
        self.track_requested.emit(name, self.current_downlink_hz())

    def _on_stop_clicked(self):
        self.stop_requested.emit()
