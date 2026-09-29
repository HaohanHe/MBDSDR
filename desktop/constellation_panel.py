# SPDX-License-Identifier: MIT
"""
MBDSDR 实时星座图面板（desktop/constellation_panel.py）
=======================================================

用 QPainter 绘制 I/Q 复基带散点图（不用 matplotlib）：
  * I 轴水平、Q 轴垂直、网格。
  * 半透明散点叠加（时间积分，类似 SDR++ scope）。
  * 可选叠加 BPSK/QPSK/8PSK/16QAM 判定边界模板（虚线圆/网格）。
  * ~10 fps 刷新，默认 512 点/帧。

红线：
  * 无信号/无后端时显「等待数据」，不造假散点。
  * 本面板只接收 ``feed_iq(iq_complex)``；数据来源由主窗口从 ReceiveChain
    信道输出喂入。
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import (
    QPainter, QPen, QBrush, QColor, QPolygonF, QPainterPath,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLabel, QPushButton,
    QCheckBox, QGroupBox, QFormLayout, QSpinBox, QSizePolicy,
)

from tokens import tokens


# 星座模板：{name: [(i, q), ...]} 单位圆归一化坐标
_TEMPLATES = {
    "None": [],
    "BPSK": [(-1.0, 0.0), (1.0, 0.0)],
    "QPSK": [(-1, -1), (-1, 1), (1, -1), (1, 1)],
    "8PSK": [(math.cos(2*math.pi*k/8), math.sin(2*math.pi*k/8))
             for k in range(8)],
    "16QAM": [(i, q) for i in (-3, -1, 1, 3) for q in (-3, -1, 1, 3)],
}


class _ConstellationView(QWidget):
    """QPainter 散点画布。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(280, 280)
        sp = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setSizePolicy(sp)
        self._points: list = []   # 累积散点 [(i, q), ...]
        self._max_points = 512
        self._template = "None"
        self._show_grid = True
        self._wait_text = "等待数据"
        self._has_data = False

    def feed(self, iq: np.ndarray) -> None:
        """喂入一段复基带 IQ，取尾部 N 点。"""
        iq = np.asarray(iq).ravel()
        if iq.size == 0:
            return
        self._has_data = True
        tail = iq[-self._max_points:]
        for v in tail:
            self._points.append((float(np.real(v)), float(np.imag(v))))
        # 限长，防止内存无限涨
        if len(self._points) > self._max_points * 4:
            self._points = self._points[-self._max_points * 2:]
        self.update()

    def clear_points(self) -> None:
        self._points.clear()
        self._has_data = False
        self.update()

    def set_template(self, name: str) -> None:
        self._template = name if name in _TEMPLATES else "None"
        self.update()

    def set_show_grid(self, on: bool) -> None:
        self._show_grid = bool(on)
        self.update()

    def set_max_points(self, n: int) -> None:
        self._max_points = max(64, int(n))

    # ------------------------------------------------------------------
    def paintEvent(self, event):  # noqa: N802 - Qt 命名
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        r = min(w, h) / 2.0 - 16.0

        # 背景
        _t = tokens()
        p.fillRect(0, 0, w, h, QColor(_t.COLORS["card_1"]))

        if not self._has_data:
            p.setPen(QPen(QColor(_t.COLORS["gray_200"])))
            p.drawText(self.rect(), Qt.AlignCenter, self._wait_text)
            p.end()
            return

        # 网格 + 坐标轴
        if self._show_grid:
            p.setPen(QPen(QColor(_t.COLORS["gray_400"]), 1, Qt.DashLine))
            for f in (-0.5, 0.5):
                p.drawLine(cx + f * r * 2, cy - r, cx + f * r * 2, cy + r)
                p.drawLine(cx - r, cy + f * r * 2, cx + r, cy + f * r * 2)
        p.setPen(QPen(QColor(_t.COLORS["gray_300"]), 1))
        p.drawLine(cx - r, cy, cx + r, cy)
        p.drawLine(cx, cy - r, cx, cy + r)
        # 单位圆
        p.setPen(QPen(QColor(_t.COLORS["gray_400"]), 1, Qt.DashLine))
        p.drawEllipse(int(cx - r), int(cy - r), int(r * 2), int(r * 2))

        # 归一化系数：把散点包络压到半径 r 内
        if self._points:
            max_abs = 1e-9
            for (i, q) in self._points:
                max_abs = max(max_abs, abs(i), abs(q))
            scale = (r * 0.9) / max_abs
        else:
            scale = 1.0

        # 散点（半透明叠加）
        p.setPen(Qt.NoPen)
        _scatter = QColor(_t.COLORS["success"])
        _scatter.setAlpha(140)
        for (i, q) in self._points:
            x = cx + i * scale
            y = cy - q * scale
            p.setBrush(QBrush(_scatter))
            p.drawEllipse(int(x - 1.5), int(y - 1.5), 3, 3)

        # 模板叠加（虚线叉）
        if self._template != "None":
            p.setPen(QPen(QColor(220, 200, 100), 1, Qt.DashLine))
            norm = 3.0 if self._template == "16QAM" else 1.0
            for (i, q) in _TEMPLATES[self._template]:
                x = cx + (i / norm) * r * 0.85
                y = cy - (q / norm) * r * 0.85
                p.drawLine(int(x - 5), int(y), int(x + 5), int(y))
                p.drawLine(int(x), int(y - 5), int(x), int(y + 5))
        p.end()


class ConstellationPanel(QWidget):
    """实时星座图面板。

    Signals
    -------
    cleared()
        用户点「清空」。
    """

    cleared = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connected = False
        self._build_ui()
        self._apply_connected_state()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        # 控制行
        ctl = QHBoxLayout()
        ctl.addWidget(QLabel("模板:"))
        self.tmpl_combo = QComboBox()
        for name in _TEMPLATES:
            self.tmpl_combo.addItem(name)
        self.tmpl_combo.currentTextChanged.connect(self._on_template_changed)
        ctl.addWidget(self.tmpl_combo)

        self.grid_chk = QCheckBox("网格")
        self.grid_chk.setChecked(True)
        self.grid_chk.toggled.connect(self._on_grid_changed)
        ctl.addWidget(self.grid_chk)

        ctl.addWidget(QLabel("点数:"))
        self.points_spin = QSpinBox()
        self.points_spin.setRange(64, 4096)
        self.points_spin.setValue(512)
        self.points_spin.setSingleStep(128)
        self.points_spin.valueChanged.connect(self._on_points_changed)
        ctl.addWidget(self.points_spin)

        self.clear_btn = QPushButton("清空")
        self.clear_btn.clicked.connect(self._on_clear)
        ctl.addWidget(self.clear_btn)
        ctl.addStretch()
        root.addLayout(ctl)

        # 画布
        self.view = _ConstellationView()
        root.addWidget(self.view, stretch=1)

        # 状态
        self.status_label = QLabel("等待数据")
        self.status_label.setObjectName("statusValue")
        root.addWidget(self.status_label)

        # 10fps 刷新定时器（空转；feed_iq 已直接 update，这里只做状态节流）
        self._tick = QTimer(self)
        self._tick.setInterval(100)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()

    # ------------------------------------------------------------------ 状态
    def set_sdr_connected(self, connected: bool) -> None:
        self._connected = bool(connected)
        self._apply_connected_state()

    def _apply_connected_state(self):
        if not self._connected:
            self.status_label.setText("等待数据")
            self.view._has_data = False
            self.view.update()

    # ------------------------------------------------------------------ 槽
    def _on_template_changed(self, name: str):
        self.view.set_template(name)

    def _on_grid_changed(self, on: bool):
        self.view.set_show_grid(on)

    def _on_points_changed(self, n: int):
        self.view.set_max_points(n)

    def _on_clear(self):
        self.view.clear_points()
        self.cleared.emit()

    def _on_tick(self):
        if not self._connected:
            return
        # 无新数据时 canvas 自绘；有数据时 feed_iq 已 update
        if not self.view._has_data:
            self.status_label.setText("等待数据")

    # ------------------------------------------------------------------ 主窗口调用
    def feed_iq(self, iq: np.ndarray) -> None:
        """主窗口从 ReceiveChain 信道输出喂入复基带 IQ。"""
        if not self._connected:
            return
        self.view.feed(iq)
        if self.view._has_data:
            self.status_label.setText("接收中")
