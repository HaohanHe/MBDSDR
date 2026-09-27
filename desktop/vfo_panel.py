"""
MBDSDR 多 VFO 面板（desktop/vfo_panel.py）
============================================

把内核 ``mbdsdr_ai.vfo_manager.VfoManager`` + ``vfo_enhanced.VfoAudioRouter``
接成可停靠面板：列出全部 VFO、增删、切主听（主听出声、其他静音）、循环切主听。

交互对照 SDR++ VFO 面板 / GQRX Channels 面板：
  * 每行一个 VFO：频率 / 模式 / 带宽 / 主听按钮 / 静音按钮 / 删除按钮。
  * 点行 → 发 ``vfo_selected(vfo_id)``，主窗口把该 VFO 设为主听并在频谱上
    高亮其滤波器边沿。
  * 主听行高亮，其他行半透明（通过样式表 alpha）。
  * 「+ VFO」按钮：在当前中心频率 ±偏移 新建一个 VFO。
  * 「循环主听」按钮 / Ctrl+Tab：在 VFO 间循环切主听。

红线：
  * 无后端时面板显「未连接」，按钮置灰，不造假 VFO 数据。
  * 本面板**不直接持有 DSP 绑定**：只操作 VfoManager 配置态，真正的 offset/
    带宽推 DSP 由主窗口在信号槽里完成。
"""
from __future__ import annotations

from typing import Optional, Callable

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QGroupBox,
)
from PySide6.QtGui import QBrush, QColor

from tokens import tokens

try:  # pragma: no cover - 内核缺失时面板降级
    from mbdsdr_ai.vfo_manager import VfoManager
    _VFO_MGR_OK = True
except Exception:  # noqa: BLE001
    VfoManager = None  # type: ignore
    _VFO_MGR_OK = False


def _fmt_freq_hz(hz: float) -> str:
    """Hz → 可读字符串。"""
    if abs(hz) >= 1e6:
        return f"{hz/1e6:.3f} MHz"
    if abs(hz) >= 1e3:
        return f"{hz/1e3:.1f} kHz"
    return f"{hz:.0f} Hz"


def _fmt_bw_hz(hz: float) -> str:
    if abs(hz) >= 1e3:
        return f"{hz/1e3:.1f}k"
    return f"{hz:.0f}"


class VfoPanel(QWidget):
    """多 VFO 列表面板。

    Signals
    -------
    vfo_selected(str)
        用户点选某 VFO 行 → 主窗口设为主听。
    vfo_added(float, float, str)
        用户点「+ VFO」→ 主窗口新建 VFO (center_hz, bw_hz, mode)。
    vfo_removed(str)
        用户点删除 → 主窗口移除 VFO。
    primary_changed(str)
        主听切换 → 主窗口同步声卡路由。
    cycle_primary()
        「循环主听」按钮 / 快捷键 → 主窗口切到下一个 VFO。
    """

    vfo_selected = Signal(str)
    vfo_added = Signal(float, float, str)
    vfo_removed = Signal(str)
    primary_changed = Signal(str)
    cycle_primary = Signal()

    def __init__(self, vfo_manager=None, parent=None):
        super().__init__(parent)
        self._mgr = vfo_manager  # 可注入；None 时用模块自带
        if self._mgr is None and _VFO_MGR_OK and VfoManager is not None:
            try:
                self._mgr = VfoManager()
            except Exception:  # noqa: BLE001
                self._mgr = None
        self._connected = False
        # 提供给面板的"当前中心频率"回调（主窗口注入，新建 VFO 时取中心频）
        self._center_freq_provider: Optional[Callable[[], float]] = None
        self._default_mode = "FM"
        self._default_bw = 12_500.0
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        # 顶部：状态 + 添加按钮
        top = QHBoxLayout()
        self.status_label = QLabel("未连接")
        self.status_label.setObjectName("statusValue")
        top.addWidget(self.status_label)
        top.addStretch()
        self.add_btn = QPushButton("+ VFO")
        self.add_btn.setToolTip("在当前中心频率附近新建一个 VFO")
        self.add_btn.clicked.connect(self._on_add_vfo)
        top.addWidget(self.add_btn)
        self.cycle_btn = QPushButton("循环主听")
        self.cycle_btn.setToolTip("在 VFO 间循环切主听（Ctrl+Tab）")
        self.cycle_btn.clicked.connect(self.cycle_primary.emit)
        top.addWidget(self.cycle_btn)
        root.addLayout(top)

        # VFO 列表
        self.table = QTableWidget(0, 5, self)
        self.table.setHorizontalHeaderLabels(["VFO", "频率", "模式", "带宽", "操作"])
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        root.addWidget(self.table, stretch=1)

        # 底部提示
        self.hint = QLabel("点行设为主听；主听高亮，其他 VFO 静音")
        self.hint.setObjectName("hintLabel")
        root.addWidget(self.hint)

        self._apply_connected_state()

    # ------------------------------------------------------------------ 状态
    def set_sdr_connected(self, connected: bool) -> None:
        """主窗口连接/断开后端时调用：切换按钮可用态与状态文字。"""
        self._connected = bool(connected)
        self._apply_connected_state()
        self.refresh()

    def set_center_freq_provider(self, fn: Callable[[], float]) -> None:
        self._center_freq_provider = fn

    def set_default_params(self, mode: str, bw_hz: float) -> None:
        self._default_mode = mode or "FM"
        self._default_bw = float(bw_hz)

    def _apply_connected_state(self):
        if not self._connected:
            self.status_label.setText("未连接")
            self.add_btn.setEnabled(False)
            self.cycle_btn.setEnabled(False)
            self.table.setEnabled(False)
        else:
            self.status_label.setText("已连接")
            self.add_btn.setEnabled(self._mgr is not None)
            self.cycle_btn.setEnabled(self._mgr is not None)
            self.table.setEnabled(True)

    # ------------------------------------------------------------------ 数据
    def refresh(self) -> None:
        """从 VfoManager 重读 VFO 列表并刷新表格（主窗口在 VFO 变化后调用）。"""
        mgr = self._mgr
        self.table.setRowCount(0)
        if mgr is None:
            return
        primary_id = getattr(mgr, "active_vfo_id", None)
        for v in mgr.list_all():
            row = self.table.rowCount()
            self.table.insertRow(row)
            # VFO id
            id_item = QTableWidgetItem(v.vfo_id)
            _t = tokens()
            # 主听行高亮（accent 半透明底），其他半透明
            is_primary = (v.vfo_id == primary_id)
            if is_primary:
                _hl = QColor(_t.COLORS["accent"])
                _hl.setAlpha(60)
                brush = QBrush(_hl)
                id_item.setForeground(QBrush(QColor(_t.COLORS["text_primary"])))
                f = id_item.font()
                f.setBold(True)
                id_item.setFont(f)
            else:
                _dim = QColor(_t.COLORS["card_2"])
                _dim.setAlpha(40)
                brush = QBrush(_dim)
            for col in range(5):
                self.table.setItem(row, col, QTableWidgetItem(""))
            for col in range(5):
                it = self.table.item(row, col)
                it.setBackground(brush)
                if not is_primary:
                    it.setForeground(QBrush(QColor(_t.COLORS["gray_200"])))
            self.table.setItem(row, 0, id_item)
            self.table.item(row, 1).setText(_fmt_freq_hz(v.center_hz))
            self.table.item(row, 2).setText(v.mode)
            self.table.item(row, 3).setText(_fmt_bw_hz(v.bw_hz))
            # 操作列：主听/静音/删除 三按钮
            ops = QWidget()
            ol = QHBoxLayout(ops)
            ol.setContentsMargins(2, 0, 2, 0)
            ol.setSpacing(2)
            primary_btn = QPushButton("主听" if not is_primary else "✓")
            primary_btn.setCheckable(False)
            primary_btn.setMinimumHeight(20)
            primary_btn.setMaximumHeight(28)
            primary_btn.clicked.connect(
                lambda _=False, vid=v.vfo_id: self.vfo_selected.emit(vid))
            mute_btn = QPushButton("静音" if not v.muted else "取消静音")
            mute_btn.setMinimumHeight(20)
            mute_btn.setMaximumHeight(28)
            mute_btn.clicked.connect(
                lambda _=False, vid=v.vfo_id: self._on_toggle_mute(vid))
            del_btn = QPushButton("删")
            del_btn.setMinimumHeight(20)
            del_btn.setMaximumHeight(28)
            del_btn.setStyleSheet("color:" + tokens().COLORS["danger"] + ";")
            del_btn.clicked.connect(
                lambda _=False, vid=v.vfo_id: self.vfo_removed.emit(vid))
            ol.addWidget(primary_btn)
            ol.addWidget(mute_btn)
            ol.addWidget(del_btn)
            self.table.setCellWidget(row, 4, ops)
            self.table.setRowHeight(row, 28)

    # ------------------------------------------------------------------ 槽
    def _on_add_vfo(self):
        """在当前中心频率 ±偏移 新建 VFO。"""
        center = 0.0
        if self._center_freq_provider is not None:
            try:
                center = float(self._center_freq_provider())
            except Exception:  # noqa: BLE001
                center = 0.0
        if center <= 0:
            center = 100_000_000.0
        # 默认偏移 ±一个信道带宽（次听落在旁边）
        offset = self._default_bw * 1.5
        new_center = center + offset
        self.vfo_added.emit(float(new_center), float(self._default_bw),
                            str(self._default_mode))

    def _on_toggle_mute(self, vfo_id: str):
        mgr = self._mgr
        if mgr is None:
            return
        v = mgr.get(vfo_id)
        if v is None:
            return
        v.muted = not v.muted
        self.refresh()

    def _on_row_selected(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return
        row = rows[0].row()
        id_item = self.table.item(row, 0)
        if id_item is None:
            return
        vid = id_item.text()
        self.vfo_selected.emit(vid)
