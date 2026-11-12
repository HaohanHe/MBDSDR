# SPDX-License-Identifier: MIT
"""
MBDSDR 实时星座图面板（desktop/constellation_panel.py）
=======================================================

两层数据，默认只看 GNSS：

1. **GNSS 星群图（主视图）**：画 GNSS 模块通过 NMEA GSV/GSA 真报上来的卫星，
   按星座分组，每颗一根信噪比柱（实心 = GSA 报参与定位，半透明 = 仅可见）。
   GNSS 模块只给方位/仰角/信噪比，不给 I/Q，所以这里画的是卫星星群，不是
   符号散点。没有 GSV 就只出空态文字，绝不生成占位卫星。
2. **SDR 叠加层（可选）**：原有 I/Q 散点图，用 QPainter 绘制复基带散点
   （I 轴水平、Q 轴垂直、网格、半透明叠加、可选 BPSK/QPSK/8PSK/16QAM 模板）。
   它画的是 SDR 当前信道的符号，**不是卫星信号**；全向天线收不到卫星，
   收卫星要等定向天线/锅子。无载波时不画噪声云（复高斯噪声的
   ``p95(|x|)/p50(|x|)`` ≈2.08，恒包络≈1.0、16QAM≈1.34，超过阈值即判无载波）。

红线：
  * 无信号/无后端时显「等待数据」「未连接」，不造假散点、不造假卫星。
  * ``feed_iq(iq_complex)`` 由主窗口从 ReceiveChain 信道输出喂入；
    ``update_gnss(gsv_frames, gsa_frame, connected)`` 由主窗口 1s 轮询喂入。
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from PySide6.QtCore import Qt, QTimer, Signal, QRectF, QPointF
from PySide6.QtGui import (
    QPainter, QPen, QBrush, QColor, QPolygonF, QPainterPath,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLabel, QPushButton,
    QCheckBox, QGroupBox, QFormLayout, QSpinBox, QSizePolicy, QButtonGroup,
)

from constants import cons_of_talker as _cons_of_talker
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

#: GNSS 星座显示顺序 / 配色 / 短名（短名只用于图头，不改变 NMEA 口径）
_CONS_ORDER = ("GPS", "GLONASS", "Galileo", "BeiDou", "未知")
_CONS_COLORS = {
    "GPS": "#4C9AFF",
    "GLONASS": "#E8604C",
    "Galileo": "#E8B84B",
    "BeiDou": "#A97CF0",
    "未知": "#8892A6",
}
_CONS_SHORT = {"BeiDou": "北斗"}


def _sat_used(sat: dict, used) -> bool:
    """是否参与定位：优先 (星座, PRN) 精确匹配，退联合并 PRN 匹配。"""
    prn = sat.get("prn")
    return ((sat.get("constellation"), prn) in used) or (("*", prn) in used)


def _used_keys(gsa_frame, connected: bool) -> set:
    """GSA → 参与定位的卫星键集合，按 talker 分别映射星座。

    ``get_gsa()`` 同时给「按 talker 分组」和「合并 satellites_used」两种口径；
    分开用才能避免 GPS PRN 5 和北斗 PRN 5 被同一份合并列表互相点亮。
    没有分组数据时才退联合并 PRN（任意星座按 PRN 匹配）。
    """
    if not connected or not gsa_frame:
        return set()
    used = set()
    per_talker = {k: v for k, v in gsa_frame.items() if isinstance(v, dict)}
    for talker, rec in per_talker.items():
        for prn in rec.get("satellites_used") or []:
            try:
                p = int(prn)
            except (TypeError, ValueError):
                continue
            used.add((_cons_of_talker(talker, p), p))
    if not per_talker:
        for prn in gsa_frame.get("satellites_used") or []:
            try:
                used.add(("*", int(prn)))
            except (TypeError, ValueError):
                continue
    return used


def _prn_key(sat: dict):
    prn = sat.get("prn")
    return (0, prn) if isinstance(prn, int) else (1, str(prn))


def _gnss_status_text(sats, used, connected: bool) -> str:
    if not connected:
        return "GNSS 未连接 · 无可见卫星"
    if not sats:
        return "GNSS 已连接 · 暂无可见卫星"
    n_used = sum(1 for s in sats if _sat_used(s, used))
    return f"GNSS · 可见 {len(sats)} 颗 · 参与定位 {n_used} 颗"


def _gnss_status_detail(sats, used) -> str:
    """状态行的完整版（按星座分组 + 口径说明），放 tooltip 防止窄列折行。"""
    if not sats:
        return ""
    counts = {}
    for s in sats:
        name = s.get("constellation")
        counts[name] = counts.get(name, 0) + 1
    parts = [f"{_CONS_SHORT.get(n, n)} {counts[n]}"
             for n in _CONS_ORDER if n in counts]
    n_used = sum(1 for s in sats if _sat_used(s, used))
    return (f"可见 {len(sats)} 颗（{' / '.join(parts)}）· 参与定位 {n_used} 颗"
            " · 柱高=信噪比，实心=参与定位，半透明=仅可见")


class _GnssSkyView(QWidget):
    """GNSS 卫星信噪比星群图：只画 NMEA GSV 真报上来的卫星。

    横轴按星座分组（GPS / GLONASS / Galileo / 北斗 / 未知），每颗卫星一根柱：
    柱高 = 该星 snr_db，柱顶是信噪比数值，柱下是 PRN 与仰角；实心 = GSA
    报「参与定位」，半透明 = 仅可见、未参与解算。鼠标悬停出仰角/方位详情。
    没有 GSV 就只显示空态文字，绝不生成占位卫星。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(170)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self._sats: list = []
        self._used: set = set()
        self._connected = False
        self._empty_text = "GNSS 未连接 · 无可见卫星"
        self._hits: list = []     # 上一次绘制的 (x, w, sat)，给悬停命中用

    def update_sats(self, sats, used, connected: bool) -> None:
        self._sats = list(sats or [])
        self._used = set(used or ())
        self._connected = bool(connected)
        if self._sats:
            self._empty_text = ""
        elif self._connected:
            self._empty_text = "GNSS 已连接 · 暂无可见卫星"
        else:
            self._empty_text = "GNSS 未连接 · 无可见卫星"
        self.update()

    def is_used(self, sat: dict) -> bool:
        return _sat_used(sat, self._used)

    # ------------------------------------------------------------------
    def mouseMoveEvent(self, event):  # noqa: N802 - Qt 命名
        x = event.position().x()
        sat = None
        for hx, hw, item in self._hits:
            if hx <= x < hx + hw:
                sat = item
                break
        if sat is None:
            self.setToolTip("")
        else:
            parts = [f"{sat.get('constellation')} PRN {sat.get('prn')}"]
            if sat.get("elevation") is not None:
                parts.append(f"仰角 {float(sat['elevation']):.0f}°")
            if sat.get("azimuth") is not None:
                parts.append(f"方位 {float(sat['azimuth']):.0f}°")
            if sat.get("snr_db") is None:
                parts.append("信噪比未上报")
            else:
                parts.append(f"信噪比 {float(sat['snr_db']):.0f} dB-Hz")
            parts.append("参与定位" if self.is_used(sat) else "仅可见")
            self.setToolTip(" · ".join(parts))
        super().mouseMoveEvent(event)

    def paintEvent(self, event):  # noqa: N802 - Qt 命名
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        _t = tokens()
        p.fillRect(0, 0, w, h, QColor(_t.COLORS["card_1"]))

        if not self._sats:
            p.setPen(QPen(QColor(_t.COLORS["gray_200"])))
            p.drawText(self.rect(), Qt.AlignCenter, self._empty_text)
            p.end()
            return

        groups = []
        for name in _CONS_ORDER:
            items = sorted((s for s in self._sats
                            if s.get("constellation") == name), key=_prn_key)
            if items:
                groups.append((name, items))

        left, right, top, bottom = 8.0, 8.0, 16.0, 30.0
        base = h - bottom
        gap = 12.0
        n_slots = sum(len(items) for _, items in groups) or 1
        avail = max(24.0, w - left - right - gap * (len(groups) - 1))
        slot = avail / n_slots
        bar_w = max(3.0, min(16.0, slot * 0.62))
        max_snr = max([float(s["snr_db"]) for s in self._sats
                       if s.get("snr_db") is not None] or [0.0])
        scale_max = max(50.0, max_snr)
        bar_area = max(20.0, base - top - 16.0)

        base_font = p.font()
        small = p.font()
        small.setPointSizeF(max(6.5, base_font.pointSizeF() - 1.5))

        # 基线 + 量程
        p.setPen(QPen(QColor(_t.COLORS["gray_400"]), 1))
        p.drawLine(int(left), int(base), int(w - right), int(base))
        p.setFont(small)
        fm = p.fontMetrics()
        p.drawText(QPointF(left, top - 5),
                   f"0–{int(round(scale_max))} dB-Hz")

        self._hits = []
        x = left
        for name, items in groups:
            group_w = slot * len(items)
            head = f"{_CONS_SHORT.get(name, name)} {len(items)}"
            # 不按组宽裁剪：单星小组（如 GLONASS 1）的标题比组宽还宽，
            # 用 QRectF 居中会被截成「LONASS」。
            head_w = fm.horizontalAdvance(head)
            cx = x + group_w / 2.0
            p.setPen(QPen(QColor(_CONS_COLORS.get(name, "#8892A6"))))
            p.drawText(QPointF(cx - head_w / 2.0, top - 5), head)
            for sat in items:
                used = self.is_used(sat)
                col = QColor(_CONS_COLORS.get(name, "#8892A6"))
                col.setAlpha(235 if used else 95)
                snr = sat.get("snr_db")
                bx = x + (slot - bar_w) / 2.0
                if snr is None:
                    p.setPen(QPen(QColor(_t.COLORS["gray_400"]), 1, Qt.DashLine))
                    p.drawLine(int(bx), int(base - 2),
                               int(bx + bar_w), int(base - 2))
                    bh = 0.0
                else:
                    bh = bar_area * (min(max(0.0, float(snr)), scale_max)
                                     / scale_max)
                    p.setPen(Qt.NoPen)
                    p.setBrush(QBrush(col))
                    p.drawRect(QRectF(bx, base - bh, bar_w, bh))
                if slot >= 18 and snr is not None:
                    p.setPen(QPen(QColor(_t.COLORS["gray_200"])))
                    p.drawText(QRectF(x, base - bh - 16, slot, 14),
                               Qt.AlignHCenter | Qt.AlignBottom,
                               f"{float(snr):.0f}")
                p.setPen(QPen(QColor(_t.COLORS["gray_300"] if used
                                     else _t.COLORS["gray_400"])))
                p.drawText(QRectF(x, base + 2, slot, 13),
                           Qt.AlignHCenter, str(sat.get("prn")))
                el = sat.get("elevation")
                if slot >= 12 and el is not None:
                    p.setPen(QPen(QColor(_t.COLORS["gray_400"])))
                    p.drawText(QRectF(x, base + 15, slot, 12),
                               Qt.AlignHCenter, f"{float(el):.0f}°")
                self._hits.append((x, slot, sat))
                x += slot
            x += gap
        p.setFont(base_font)
        p.end()


class _ConstellationView(QWidget):
    """QPainter 散点画布。"""

    #: 「无载波」判定：p95(|x|)/p50(|x|)。复高斯噪声 ≈2.08（瑞利），
    #: 恒包络 ≈1.00，16QAM ≈1.34。上锁/解锁两阈值不同，防止边界抖动。
    _LOCK_RATIO = 1.55
    _UNLOCK_RATIO = 1.75
    #: 一批样本中最少有效点数（幅度非 0），太少统计不可信。
    _MIN_VALID = 32

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(280, 280)
        sp = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setSizePolicy(sp)
        self._points: list = []   # 累积散点 [(i, q), ...]
        self._max_points = 512
        self._template = "None"
        self._show_grid = True
        # 画布空态与下方状态行各说一件事：画布说"为什么没有图"，
        # 状态行说"当前链路状态"，两处都写「等待数据」只是把同一句念两遍。
        self._wait_text = "SDR 未连接"
        self._has_data = False
        #: 载波锁定状态：无载波时不画散点，只出提示。
        self._carrier_locked = False
        self._no_carrier_reason = ""
        #: 归一化基准：一批样本的 p99 幅度（不是 max，单个离群点压不扁整幅）。
        self._ref_amp = 0.0

    def feed(self, iq: np.ndarray) -> None:
        """喂入一段复基带 IQ，取尾部 N 点。

        先判有没有载波：纯噪声的散点也是一团点，画出来只会骗人。
        """
        iq = np.asarray(iq).ravel()
        if iq.size == 0:
            return
        tail = iq[-self._max_points:]
        mag = np.abs(tail)
        valid = mag[mag > 1e-12]
        if valid.size < self._MIN_VALID:
            self._set_no_carrier("无载波（幅度为 0）")
            return
        p50, p95, p99 = np.percentile(valid, (50.0, 95.0, 99.0))
        ratio = float(p95 / p50) if p50 > 0 else float("inf")
        limit = self._UNLOCK_RATIO if self._carrier_locked else self._LOCK_RATIO
        if not math.isfinite(ratio) or ratio > limit:
            self._set_no_carrier("无载波 · 未锁定")
            return

        self._carrier_locked = True
        self._no_carrier_reason = ""
        self._ref_amp = float(p99)
        self._has_data = True
        # 2026-11-06：批量转 Python 浮点。逐点 `for v in tail` 在 146 帧/s 下
        # 是 7.5 万个 tuple/s 的纯 Python 开销（面板在别的页签也照跑）。
        # 这里一次 tolist 拆成两个列表再 zip，避免逐元素建 np 标量。
        re_l = np.real(tail).tolist()
        im_l = np.imag(tail).tolist()
        self._points.extend(zip(re_l, im_l))
        # 限长，防止内存无限涨
        if len(self._points) > self._max_points * 4:
            self._points = self._points[-self._max_points * 2:]
        if self.isVisible():
            # 不可见时不排重绘（数据仍累积，切回来立刻有图）
            self.update()

    def _set_no_carrier(self, reason: str) -> None:
        """判为无载波：清掉旧散点（上一个载波的残留不能当当前图）。"""
        self._carrier_locked = False
        self._no_carrier_reason = reason
        self._has_data = False
        self._ref_amp = 0.0
        if self._points:
            self._points.clear()
        if self.isVisible():
            self.update()

    def clear_points(self) -> None:
        self._points.clear()
        self._has_data = False
        self._carrier_locked = False
        self._no_carrier_reason = ""
        self._ref_amp = 0.0
        self.update()

    def set_template(self, name: str) -> None:
        self._template = name if name in _TEMPLATES else "None"
        self.update()

    def set_show_grid(self, on: bool) -> None:
        self._show_grid = bool(on)
        self.update()

    def set_idle_text(self, text: str) -> None:
        """空画布提示（未连接 / 等待载波），由面板按链路状态切换。"""
        self._wait_text = str(text)
        if self.isVisible():
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
            p.drawText(self.rect(), Qt.AlignCenter,
                       self._no_carrier_reason or self._wait_text)
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

        # 归一化系数：p99 幅度对齐半径（旧实现用 max，一个离群点就把整幅压扁）
        scale = (r * 0.9) / self._ref_amp if self._ref_amp > 0 else 1.0

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
    """实时星座图面板：GNSS 星群图为主，SDR 散点为可选叠加层。

    Signals
    -------
    cleared()
        用户点「清空」。
    """

    cleared = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connected = False
        self._sdr_overlay = False
        self._gnss_sats: list = []
        self._gnss_used: set = set()
        self._gnss_connected = False
        self._build_ui()
        self._apply_connected_state()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        # 数据层切换：GNSS 是主视图，SDR 是叠加层（不是二选一的替代品）
        layer = QHBoxLayout()
        self.gnss_only_btn = QPushButton("只看 GNSS")
        self.gnss_only_btn.setObjectName("constellationGnssOnly")
        self.gnss_only_btn.setCheckable(True)
        self.gnss_only_btn.setChecked(True)
        self.gnss_only_btn.setToolTip("只画 GNSS 模块通过 NMEA GSV/GSA 报上来的卫星")
        self.gnss_sdr_btn = QPushButton("＋ 叠加上 SDR")
        self.gnss_sdr_btn.setObjectName("constellationGnssSdr")
        self.gnss_sdr_btn.setCheckable(True)
        self.gnss_sdr_btn.setToolTip(
            "在 GNSS 星群图下方叠加 SDR 当前信道的 I/Q 散点；"
            "全向天线收不到卫星，收卫星要等定向天线/锅子")
        self._layer_group = QButtonGroup(self)
        self._layer_group.setExclusive(True)
        self._layer_group.addButton(self.gnss_only_btn)
        self._layer_group.addButton(self.gnss_sdr_btn)
        self.gnss_only_btn.clicked.connect(
            lambda _checked=False: self.set_sdr_overlay(False))
        self.gnss_sdr_btn.clicked.connect(
            lambda _checked=False: self.set_sdr_overlay(True))
        layer.addWidget(self.gnss_only_btn)
        layer.addWidget(self.gnss_sdr_btn)
        layer.addStretch()
        root.addLayout(layer)

        # GNSS 星群图（主视图）
        self.gnss_view = _GnssSkyView()
        self.gnss_view.setObjectName("gnssSkyView")
        root.addWidget(self.gnss_view, stretch=3)

        self.gnss_status = QLabel("GNSS 未连接 · 无可见卫星")
        self.gnss_status.setObjectName("statusValue")
        self.gnss_status.setWordWrap(True)
        root.addWidget(self.gnss_status)

        # ---- SDR 叠加层：原有控制行 / 散点画布 / 状态整体收进 sdr_box ----
        self.sdr_box = QWidget()
        sdr = QVBoxLayout(self.sdr_box)
        sdr.setContentsMargins(0, 0, 0, 0)
        sdr.setSpacing(6)

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
        sdr.addLayout(ctl)

        # 画布
        self.view = _ConstellationView()
        sdr.addWidget(self.view, stretch=1)

        # 状态
        self.status_label = QLabel("等待数据")
        self.status_label.setObjectName("statusValue")
        sdr.addWidget(self.status_label)

        # 说清这一层是什么、不是什么（避免又被当成卫星信号）
        self.sdr_hint = QLabel(
            "SDR 这一层画的是当前信道 I/Q，不是卫星信号；"
            "全向天线收不到卫星，收卫星要等定向天线/锅子。")
        self.sdr_hint.setWordWrap(True)
        # 说明字不是状态色，走主题的 text_secondary（= palette placeholder-text）：
        # 写死 #8A93A6 时浅色 bg 上只有 2.79:1，且切主题不会跟着变。
        self.sdr_hint.setStyleSheet("color: palette(placeholder-text);")
        sdr.addWidget(self.sdr_hint)

        self.sdr_box.setVisible(False)
        root.addWidget(self.sdr_box, stretch=2)

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
            self.view.set_idle_text("SDR 未连接")
            self.view._has_data = False
            self.view._carrier_locked = False
            self.view._no_carrier_reason = ""
            self.view._ref_amp = 0.0
            self.view.update()
        else:
            # 连上了但还没喂到 IQ：画布说明在等什么，不是没连上。
            self.view.set_idle_text("等待载波锁定")

    # ------------------------------------------------------------------ 数据层
    def set_sdr_overlay(self, on: bool) -> None:
        """切换「只看 GNSS」/「叠加上 SDR」；SDR 只是叠加层，不替代 GNSS。"""
        self._sdr_overlay = bool(on)
        self.sdr_box.setVisible(self._sdr_overlay)
        self.gnss_sdr_btn.setChecked(self._sdr_overlay)
        self.gnss_only_btn.setChecked(not self._sdr_overlay)

    def update_gnss(self, gsv_frames, gsa_frame=None,
                    connected: bool = False) -> None:
        """主窗口 1s 轮询喂入 NMEA GSV/GSA：只画真上报的卫星。

        未连接时整层清空（``get_gsv_frames()`` 在串口停掉后仍会返回上一帧，
        不清就会把断线前的卫星一直挂在图上）。
        """
        sats = []
        if connected:
            merged = {}
            for frame in (gsv_frames or []):
                talker = str(frame.get("talker") or "")
                for sat in frame.get("sats") or []:
                    prn = sat.get("id")
                    if not isinstance(prn, int):
                        continue
                    cons = _cons_of_talker(talker, prn)
                    merged[(cons, prn)] = {
                        "prn": prn,
                        "constellation": cons,
                        "talker": talker,
                        "elevation": sat.get("elevation"),
                        "azimuth": sat.get("azimuth"),
                        "snr_db": sat.get("snr_db"),
                    }
            sats = list(merged.values())
        used = _used_keys(gsa_frame, connected)
        self._gnss_sats = sats
        self._gnss_used = used
        self._gnss_connected = bool(connected)
        self.gnss_view.update_sats(sats, used, connected)
        self.gnss_status.setText(_gnss_status_text(sats, used, connected))
        # 窄列里长文案会折行被裁掉，细节放 tooltip，状态行只留短句
        self.gnss_status.setToolTip(_gnss_status_detail(sats, used))

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
            text = "未锁定" if self.view._no_carrier_reason else "等待数据"
            if self.status_label.text() != text:
                self.status_label.setText(text)

    # ------------------------------------------------------------------ 状态查询
    @property
    def carrier_locked(self) -> bool:
        """是否判定到载波（无载波时画布不画散点，只出提示）。"""
        return self.view._carrier_locked

    @property
    def no_carrier_reason(self) -> str:
        """无载波时的提示文案；已锁定时为空串。"""
        return self.view._no_carrier_reason

    @property
    def sdr_overlay(self) -> bool:
        """SDR 散点层是否叠加显示（默认 False = 只看 GNSS）。"""
        return self._sdr_overlay

    @property
    def gnss_satellites(self) -> list:
        """当前星群图上的真实卫星（空列表 = 未连接或无可见卫星）。"""
        return list(self._gnss_sats)

    @property
    def gnss_status_text(self) -> str:
        return self.gnss_status.text()

    # ------------------------------------------------------------------ 主窗口调用
    def feed_iq(self, iq: np.ndarray) -> None:
        """主窗口从 ReceiveChain 信道输出喂入复基带 IQ。

        2026-11-06：状态文字只在**真的变化**时才写（旧实现每帧 setText，146 次/s
        的 QLabel 布局开销；面板在别的页签也照跑）。重绘由 view.feed 内部按
        可见性判断，数据照样累积，切回来立即有图。
        """
        if not self._connected:
            return
        self.view.feed(iq)
        if self.view._has_data:
            text = "接收中"
        elif self.view._no_carrier_reason:
            text = "未锁定"
        else:
            text = "等待数据"
        if self.status_label.text() != text:
            self.status_label.setText(text)
