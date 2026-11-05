# SPDX-License-Identifier: MIT
"""
MBDSDR 卫星闭环跟踪面板
=========================
在"天空模式"图之外提供一个独立控制面板：

  - QComboBox 选择已加载 TLE 卫星；
  - 下行频率输入框（MHz，按卫星名自动套用标准下行频率）；
  - "开始跟踪 / 停止跟踪"按钮（触发 main_window 的 500ms 自动调谐闭环）；
  - "更新 TLE"按钮（从 Celestrak 在线刷新）；
  - 实时显示：方位角 / 仰角 / 多普勒偏移 / 校正后接收频率 / 斜距；
  - 仰角指示器（0-90° 进度条）：仰角低于跟踪门限时变红并提示"跟踪已停止"。

本面板只负责 UI 展示与发信号，真正的轨道计算与 SDR 调谐闭环在
main_window（持有 mbdsdr_ai.sat_tracker.SatelliteTracker）。
"""
from __future__ import annotations

from typing import Optional, Dict

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QComboBox,
    QDoubleSpinBox, QPushButton, QProgressBar, QGroupBox, QMessageBox,
)

from tokens import tokens

# 两个**语义不同**的仰角量，别合并：
#   VISIBLE_MIN_ALT_DEG（5°）= 当前可见/跟踪门：仰角低于它就不再把状态当作
#     「跟踪中」；它决定「现在能不能收」，与用户排班偏好无关。
#   effective_pass_min_elevation_deg()（用户设置，默认 10°）= 过境列表筛选门：
#     按**峰值**仰角筛「这次过境值不值得列出来」，不是当前仰角门。
from mbdsdr_ai.sat_passes import VISIBLE_MIN_ALT_DEG
try:
    from settings import (
        effective_pass_min_elevation_deg,
        effective_pass_visibility_gate_deg,
        effective_show_low_elevation_passes,
    )
except ImportError:  # pragma: no cover - 兼容 desktop 目录直接上 sys.path
    from .settings import (  # type: ignore
        effective_pass_min_elevation_deg,
        effective_pass_visibility_gate_deg,
        effective_show_low_elevation_passes,
    )


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

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self.setObjectName("satTrackPanel")
        self.setFrameShape(QFrame.StyledPanel)
        self._tracking = False
        # 窄屏「可折叠面板」：收起的是分组框正文，标题行与指示符始终可见。
        self._collapsible_boxes: list[QGroupBox] = []
        self._collapse_applied = False
        # 过境列表筛选门从这里读（不是当前可见门，见模块顶部注释）。
        self._settings = settings
        self._build_ui()
        # 面板可能一直藏在非当前页签里（永不触发 showEvent），所以在事件循环
        # 第一次空闲时也判定一次；此时主窗口几何已就绪。
        QTimer.singleShot(0, self._apply_narrow_defaults)
        # 面板级 setStyleSheet 已删除，由全局 dark_car QSS 接管按钮/分组框/标签样式。

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        title = QLabel("卫星闭环自动跟踪")
        title.setStyleSheet("font-size: 11pt; font-weight: 600; color: "
                            + tokens().text("secondary") + ";")
        root.addWidget(title)

        self.tle_date_label = QLabel("TLE 日期: --")
        self.tle_date_label.setStyleSheet("color: " + tokens().text("tertiary")
                                         + "; font-size: 8.5pt;")
        root.addWidget(self.tle_date_label)

        # ---- 卫星选择 + 下行频率 ----
        sel_box = QGroupBox("目标卫星")
        self._make_collapsible(sel_box, "目标卫星", 2, "satSelBox")
        form = QFormLayout(sel_box)
        self.sat_combo = QComboBox()
        self.sat_combo.setMinimumWidth(220)
        self.sat_combo.currentTextChanged.connect(self._on_sat_selected)
        form.addRow("卫星:", self.sat_combo)

        self.freq_spin = QDoubleSpinBox()
        # 范围与 scanner_panel 一致（0.01–6000 MHz）：合法下行频率全段覆盖，
        # S 波段（如 2271/2401 MHz）也可输入，不被旧 100–2000 MHz 限卡住。
        self.freq_spin.setRange(0.01, 6000.0)      # MHz
        self.freq_spin.setDecimals(4)
        self.freq_spin.setSingleStep(0.0025)
        self.freq_spin.setSuffix(" MHz")
        self.freq_spin.setValue(137.620)
        self.freq_spin.setToolTip(
            "跟踪时调谐到的下行频率（MHz）；实际可调范围以当前 SDR 设备为准；"
            "先用所选制式的默认值，通联确认后再微调")
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
        self._make_collapsible(data_box, "实时状态", 6, "satDataBox")
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
        self._make_collapsible(el_box, "仰角指示器", 2, "satElBox")
        el_layout = QVBoxLayout(el_box)
        self.el_bar = QProgressBar()
        self.el_bar.setRange(0, 90)
        self.el_bar.setValue(0)
        self.el_bar.setTextVisible(True)
        self.el_bar.setFormat("仰角 %v° / 90°")
        el_layout.addWidget(self.el_bar)
        # 口径分开展示：跟踪可见门（5°）≠ 过境列表峰值筛选门（用户设置）
        self.pass_gate_label = QLabel()
        self.pass_gate_label.setObjectName("passGateLabel")
        self.pass_gate_label.setWordWrap(True)
        self.pass_gate_label.setStyleSheet(
            "color: " + tokens().text("tertiary") + "; font-size: 8.5pt;")
        el_layout.addWidget(self.pass_gate_label)
        self._refresh_pass_gate_label()
        root.addWidget(el_box)

        root.addStretch(1)

    # ------------------------------------------------------- 窄屏可折叠
    def _make_collapsible(self, box: QGroupBox, label: str, count: int,
                          object_name: str) -> None:
        """把分组框改成「可折叠」：收起只留标题行 + 指示符，正文隐藏。

        可达性口径（docs/narrow-screen-plan.md §2.6）：
        * 标题行始终可见，且写出「里面有什么」与条目数；
        * 指示符 ▼=已展开 / ▶=已收起，tooltip 写清点击后的动作；
        * 控件一个不删——收起只是 ``setChecked(False)``，子控件仍可按属性名取到。
        """
        box.setObjectName(object_name)
        box.setCheckable(True)
        box.setChecked(True)
        self._collapsible_boxes.append(box)

        def _sync(checked: bool) -> None:
            box.setTitle(f"{'▼' if checked else '▶'} {label} · {count} 项")
            box.setToolTip("点击标题收起" if checked
                           else "已收起，点击标题展开")
            # 收起必须真的隐藏正文：Qt 的可勾选 QGroupBox 只置灰不隐藏，
            # 高度不会降。控件本身保留（不删），只是 setVisible(False)。
            lay = box.layout()
            if lay is not None:
                for i in range(lay.count()):
                    it = lay.itemAt(i)
                    w = it.widget() if it is not None else None
                    if w is not None:
                        w.setVisible(checked)

        box.toggled.connect(_sync)
        _sync(True)

    def _apply_narrow_defaults(self) -> None:
        """窄屏（<1440）首次判定：默认收起「实时状态」「仰角指示器」。

        目标卫星分组保持展开（开始跟踪要先选卫星）；只判定一次，避免窗口
        拖动来回抖动。宽屏保持全展开。默认态**不持久化**（见 plan §2.6）。
        """
        if self._collapse_applied:
            return
        self._collapse_applied = True
        if self.window().width() < 1440:
            for box in self._collapsible_boxes[1:]:
                box.setChecked(False)

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_narrow_defaults()

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
            self.state_label.setStyleSheet("color: " + tokens().text("tertiary")
                                           + "; font-weight:600;")

    def set_observer_ready(self, ready: bool):
        """观测者位置是否就绪；未就绪时开始按钮置灰并提示。"""
        if not ready and not self._tracking:
            self.start_btn.setEnabled(False)
        else:
            self.start_btn.setEnabled(not self._tracking)

    def update_position(self, pos: Dict[str, object]):
        """用 tracker.current_position() 的结果刷新显示。"""
        # 设置可能被用户改过：每次刷新重读两个门的显示（只拼一行文本）。
        self._refresh_pass_gate_label()
        if not pos.get("valid"):
            self.az_label.setText("--")
            self.el_label.setText("--")
            self.dop_label.setText("--")
            self.corr_label.setText("--")
            self.range_label.setText("--")
            self.el_bar.setValue(0)
            self._set_el_color(tokens().COLORS["gray_300"])
            return

        az = float(pos["azimuth"])
        el = float(pos["elevation"])
        dop = float(pos["doppler_hz"])
        corr = float(pos["corrected_freq_hz"])
        rng = float(pos["range_km"])

        self.az_label.setText(f"{az:6.1f}°")
        self.el_label.setText(f"{el:6.1f}°")
        if abs(dop) < 0.5:      # N-02：负零归零，避免「-0 Hz」误读成反向偏移
            dop = 0.0
        self.dop_label.setText(f"{dop:+8.1f} Hz")
        self.corr_label.setText(f"{corr/1e6:9.6f} MHz")
        self.range_label.setText(f"{rng:7.0f} km")

        # 仰角条：钳到 0..90
        el_bar_val = int(max(0.0, min(90.0, el)))
        self.el_bar.setValue(el_bar_val)

        if el < VISIBLE_MIN_ALT_DEG:
            # 当前可见/跟踪门（5°）：低于它标红并提示过境结束。
            # 用户设置的「过境最低仰角」是峰值筛选门，**不用在这里**——
            # 25° 时卫星仍可跟踪，不能因为用户想少列过境就假装跟踪结束。
            _t = tokens()
            self._set_el_color(_t.COLORS["danger"])
            self.state_label.setText(
                f"跟踪已停止（仰角低于设定门限 {VISIBLE_MIN_ALT_DEG:g}°；"
                "卫星尚未落下）")
            self.state_label.setStyleSheet("color:" + _t.COLORS["danger"]
                                           + "; font-weight:600;")
        else:
            _t = tokens()
            self._set_el_color(_t.COLORS["success"])
            self.state_label.setText("跟踪中")
            self.state_label.setStyleSheet("color:" + _t.COLORS["success"]
                                           + "; font-weight:600;")

    def _resolved_settings(self):
        """显式注入优先；否则取宿主（主窗口）的 settings 实例（内存值即时生效）。"""
        if self._settings is not None:
            return self._settings
        return getattr(self.window(), "settings", None)

    def _refresh_pass_gate_label(self) -> None:
        """显示两个门的分工：可见/跟踪门 5° vs 过境列表峰值门（用户设置）。"""
        settings = self._resolved_settings()
        a1 = effective_pass_min_elevation_deg(settings)
        gate = effective_pass_visibility_gate_deg(settings)
        show_low = effective_show_low_elevation_passes(settings)
        text = (f"跟踪/可见门 {VISIBLE_MIN_ALT_DEG:g}°（只决定当前能否跟踪）；"
                f"过境列表筛选门：峰值仰角 ≥ {gate:g}°")
        if show_low and a1 > gate:
            text += f"（已含 {gate:g}–{a1:g}° 低仰角）"
        self.pass_gate_label.setText(text)

    def _set_el_color(self, hex_color: str):
        _t = tokens()
        self.el_bar.setStyleSheet(
            f"QProgressBar {{ border:1px solid {_t.COLORS['gray_400']}; "
            f"border-radius:3px; text-align:center; "
            f"background:{_t.COLORS['card_2']}; "
            f"color:{_t.text('secondary')}; }}"
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
        if self._tracking:
            # 已在跟踪：重复触发（连点、菜单/外部调用）一律幂等，
            # 绝不再发一次 track_requested——主窗口若重入会覆盖
            # 「跟踪前频率/模式」，停止时就回不到用户原来的调谐。
            # 但必须留下可见反馈：静默 return 等于点了没反应。
            self.state_label.setText("跟踪中（忽略重复开始）")
            return
        name = self.sat_combo.currentText().strip()
        if not name:
            QMessageBox.warning(self, "卫星跟踪", "请先选择一颗卫星。")
            return
        self.track_requested.emit(name, self.current_downlink_hz())

    def _on_stop_clicked(self):
        self.stop_requested.emit()
