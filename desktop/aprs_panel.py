# SPDX-License-Identifier: MIT
"""
MBDSDR 桌面端 - APRS / AX.25 数据包面板 (AprsPanel)
=====================================================

144.390 MHz APRS（1200 baud AFSK Bell-202）实时面板：数据包列表 + 详情区。

数据链路（只显示真实解调出的包，绝不放演示包）：
  - 外部把 FM 鉴频后的 AFSK 音频喂给 :meth:`AprsPanel.feed_audio`；
    内部调用 ``mbdsdr_ai.ax25.AFSKModem.demodulate``（AFSK→NRZI→HDLC 帧→CRC-16），
    通过 FCS 校验的 UI 帧交给 ``mbdsdr_ai.aprs_parser.parse_aprs_frame`` 结构化解析。
  - 包列表列：时间 / 源呼号 / 目的呼号 / 路径 / 数据类型 / 内容摘要。
  - 详情区：选中包后显示完整内容（位置→经纬度/符号/评论；消息→收发方/文本；
    天气→温度/湿度/风）。
  - 支持按数据类型过滤（全部/位置/消息/天气/其他）；最多保留 500 条。

红线：
  - 无数据时列表区显「等待 144.390MHz 数据包」，绝不预存任何包。
"""

from __future__ import annotations

import os
import sys
import time
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QComboBox, QHBoxLayout, QHeaderView, QLabel, QSplitter,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

try:
    from mbdsdr_ai.ax25 import AFSKModem, AX25Frame
    _HAS_AX25 = True
except Exception:  # pragma: no cover
    AFSKModem = None
    AX25Frame = None
    _HAS_AX25 = False

try:
    from mbdsdr_ai.aprs_parser import parse_aprs_frame
    _HAS_APRS = True
except Exception:  # pragma: no cover
    parse_aprs_frame = None
    _HAS_APRS = False

# 空态文案（测试据此断言面板进入“无数据”状态）。
APRS_WAIT_TEXT = "等待 144.390MHz 数据包"

MAX_PACKETS = 500

_TYPE_LABELS = {
    "position": "位置",
    "mic_e": "位置(MIC-E)",
    "message": "消息",
    "weather": "天气",
    "status": "状态",
    "telemetry": "遥测",
    "object": "对象",
    "item": "条目",
    "query": "查询",
    "third_party": "第三方",
    "unknown": "其他",
}

_FILTERS = ["全部", "位置", "消息", "天气", "其他"]


class AprsPanel(QWidget):
    """APRS 包面板：上方过滤栏 + 包列表，下方详情区。"""

    COLUMNS = ["时间", "源呼号", "目的呼号", "路径", "类型", "内容摘要"]

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._sdr_connected = False
        self._packets: List[dict] = []   # 全部包（未过滤）
        self._filter = "全部"
        self._modem = AFSKModem() if _HAS_AX25 else None
        self._build_ui()

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # 过滤栏
        bar = QHBoxLayout()
        self.header = QLabel(APRS_WAIT_TEXT)
        bar.addWidget(self.header, 1)
        bar.addWidget(QLabel("过滤:"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(_FILTERS)
        self.filter_combo.currentTextChanged.connect(self._on_filter)
        bar.addWidget(self.filter_combo)
        layout.addLayout(bar)

        splitter = QSplitter(Qt.Vertical)
        layout.addWidget(splitter, 1)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        splitter.addWidget(self.table)

        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText("选中一个包查看完整内容…")
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

    # ------------------------------------------------------------------ #
    def set_sdr_connected(self, connected: bool) -> None:
        self._sdr_connected = bool(connected)
        self._refresh_header()

    def feed_audio(self, audio, sample_rate: float) -> None:
        """喂入 FM 鉴频后的 AFSK 音频；解调 + APRS 解析。"""
        if not (_HAS_AX25 and _HAS_APRS and self._modem is not None):
            return
        if audio is None or len(audio) == 0:
            return
        try:
            frames = self._modem.demodulate(audio)
        except Exception:
            return
        for fr in frames:
            self._consume_frame(fr)

    def submit_frame(self, frame) -> None:
        """直接喂一个 AX25Frame（测试/外部后端用）。"""
        self._consume_frame(frame)

    def submit_aprs_dict(self, pkt: dict) -> None:
        """直接喂一个已解析的 APRS dict（测试用）。"""
        if not pkt:
            return
        self._add_packet(pkt)

    # ------------------------------------------------------------------ #
    def _consume_frame(self, frame) -> None:
        if frame is None or not getattr(frame, "fcs_valid", False):
            return
        if not _HAS_APRS:
            return
        try:
            pkt = parse_aprs_frame(frame)
        except Exception:
            return
        self._add_packet(pkt)

    def _add_packet(self, pkt: dict) -> None:
        pkt = dict(pkt)
        pkt["_ts"] = time.time()
        self._packets.append(pkt)
        if len(self._packets) > MAX_PACKETS:
            self._packets = self._packets[-MAX_PACKETS:]
        self._refresh_table()
        self._refresh_header()

    def _on_filter(self, text: str) -> None:
        self._filter = text
        self._refresh_table()

    def _matches(self, pkt: dict) -> bool:
        if self._filter == "全部":
            return True
        t = pkt.get("type", "unknown")
        if self._filter == "位置":
            return t in ("position", "mic_e")
        if self._filter == "消息":
            return t == "message"
        if self._filter == "天气":
            return t == "weather"
        if self._filter == "其他":
            return t not in ("position", "mic_e", "message", "weather")
        return True

    def _summary(self, pkt: dict) -> str:
        t = pkt.get("type", "unknown")
        if t in ("position", "mic_e"):
            la, lo = pkt.get("latitude"), pkt.get("longitude")
            tail = pkt.get("comment") or ""
            if la is not None and lo is not None:
                return f"{la:.4f},{lo:.4f} {tail}".strip()
            return tail or "(位置)"
        if t == "message":
            m = pkt.get("message") or {}
            return f"->{m.get('addressee','')}: {m.get('message','')}"[:60]
        if t == "weather":
            w = pkt.get("weather") or {}
            parts = []
            if w.get("temperature_f") is not None:
                parts.append(f"{(w['temperature_f']-32)*5/9:.0f}°C")
            if w.get("humidity") is not None:
                parts.append(f"湿{w['humidity']}%")
            if w.get("wind_speed") is not None:
                parts.append(f"风{w['wind_speed']}kt")
            return " ".join(parts) or "天气"
        return (pkt.get("comment") or pkt.get("raw") or "")[:60]

    def _refresh_table(self) -> None:
        shown = [p for p in self._packets if self._matches(p)]
        self.table.setRowCount(len(shown))
        for row, p in enumerate(shown):
            ts = time.strftime("%H:%M:%S", time.localtime(p.get("_ts", 0)))
            src = f"{p.get('source','')}-{p.get('source_ssid',0)}"
            dst = f"{p.get('destination','')}-{p.get('destination_ssid',0)}"
            digi = ",".join(p.get("digipeaters", []))
            vals = [ts, src, dst, digi,
                    _TYPE_LABELS.get(p.get("type", "unknown"), "其他"),
                    self._summary(p)]
            for col, v in enumerate(vals):
                self.table.setItem(row, col, QTableWidgetItem(str(v)))

    def _refresh_header(self) -> None:
        n = len(self._packets)
        if n:
            self.header.setText(f"已收 {n} 包"
                                + ("" if self._sdr_connected else "（未连接后端）"))
        else:
            self.header.setText(APRS_WAIT_TEXT
                                if self._sdr_connected else "未连接（等待 144.390MHz 数据包）")

    def _on_row_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        shown = [p for p in self._packets if self._matches(p)]
        idx = rows[0].row()
        if idx >= len(shown):
            return
        p = shown[idx]
        lines = [f"类型: {_TYPE_LABELS.get(p.get('type','unknown'),'其他')}",
                 f"源: {p.get('source','')}-{p.get('source_ssid',0)}",
                 f"目的: {p.get('destination','')}-{p.get('destination_ssid',0)}",
                 f"路径: {','.join(p.get('digipeaters', [])) or '无'}"]
        if p.get("latitude") is not None:
            lines.append(f"位置: {p['latitude']:.5f}, {p['longitude']:.5f}")
        if p.get("symbol"):
            lines.append(f"符号: {p['symbol']}")
        if p.get("altitude") is not None:
            lines.append(f"高度: {p['altitude']} ft")
        if p.get("speed") is not None:
            lines.append(f"速度: {p['speed']} kt")
        if p.get("course") is not None:
            lines.append(f"航向: {p['course']}°")
        if p.get("weather"):
            w = p["weather"]
            lines.append("天气:")
            for k, label in (("wind_direction", "风向"), ("wind_speed", "风速(kt)"),
                             ("wind_gust", "阵风(kt)"), ("temperature_f", "温度(°F)"),
                             ("humidity", "湿度(%)"), ("pressure_mbar", "气压(hPa)")):
                if w.get(k) is not None:
                    lines.append(f"  {label}: {w[k]}")
        if p.get("message"):
            m = p["message"]
            lines.append(f"消息 -> {m.get('addressee','')}: {m.get('message','')}")
        if p.get("comment"):
            lines.append(f"评论: {p['comment']}")
        if p.get("raw"):
            lines.append(f"原始: {p['raw']}")
        self.detail.setPlainText("\n".join(lines))

    def packet_count(self) -> int:
        return len(self._packets)

    def clear(self) -> None:
        self._packets.clear()
        self.table.setRowCount(0)
        self.detail.clear()
        self._refresh_header()
