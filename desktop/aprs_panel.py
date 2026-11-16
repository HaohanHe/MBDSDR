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

from PySide6.QtCore import Qt, Signal  # noqa: E402
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
    #: 解码结果统一经 Signal 上屏。Qt 的 AutoConnection 让同线程调用保持
    #: 同步（测试/外部后端），DemodWorker 线程调用则自动排队回 GUI 线程，
    #: 绝不在 worker 线程里 setItem / setText。
    packet_ready = Signal(object)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._sdr_connected = False
        self._packets: List[dict] = []   # 全部包（未过滤）
        self._filter = "全部"
        self._modem = AFSKModem() if _HAS_AX25 else None
        # 流式解调用的滑动窗口（见 feed_audio 的注释）
        self._audio_buf = None           # np.ndarray | None
        self._recent_frames: dict = {}   # 帧签名 -> 上次上报时刻
        self._feed_count = 0
        self._decode_ms_total = 0.0      # 累计解调耗时，供诊断
        self._decode_calls = 0
        self._build_ui()
        self.packet_ready.connect(self._add_packet)  # Auto: 同线程同步/跨线程排队

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
        # 空表说明：只有表头的空表格既不告诉用户"这里以后会有东西"，也不告诉
        # 他"现在没内容"是怎么回事。挂在 viewport 上（不占行列、不参与选择），
        # 有包后随表格一起隐藏。
        # 数据 tap 只在面板展开时喂音频（main_window.py 中 aprs_dock.isVisible()
        # 的可见性门），收起期间不缓存；空态必须写出这个前提，否则用户会以为
        # 面板一直后台在收。
        self.empty_hint = QLabel(
            "还没有收到数据包\n调到 144.390 MHz（FM）并保持本面板展开；"
            "收起时不接收，展开后附近台站发报才会列出",
            self.table.viewport())
        self.empty_hint.setObjectName("hintLabel")
        self.empty_hint.setAlignment(Qt.AlignCenter)
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        _hint_lay = QVBoxLayout(self.table.viewport())
        _hint_lay.setContentsMargins(12, 8, 12, 8)
        _hint_lay.addWidget(self.empty_hint)
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

    #: 流式解调窗口（秒）。一个 AX.25 帧最长约 2 s（256 字节 @1200bd + 前导），
    #: 窗口必须能完整装下一帧，否则永远解不出来。
    STREAM_WINDOW_S = 2.0

    #: 静默门限。整窗峰值低于它就不跑解调——没有信号时白跑 2 s 的相关运算
    #: 是纯浪费（每块几十毫秒，够把 UI 拖顿）。
    STREAM_ENERGY_FLOOR = 1e-3

    #: 同一帧在这个时间内重复出现，只报一次。滑动窗口会让同一帧在连续几次
    #: 解调里反复出现，不去重的话一条报文会刷十几行。
    STREAM_DEDUP_S = 3.0

    #: 单次最多上报多少帧。避免启动/突发时一屏灌满。
    STREAM_MAX_FRAMES = 8

    def feed_audio(self, audio, sample_rate: float) -> None:
        """喂入 FM 鉴频后的 AFSK 音频；**滑动窗口解调**后交给 APRS 解析。

        为什么不能"每块独立解调"
        ------------------------
        `AFSKModem.demodulate()` 内部的位定时（PLL）是**从零起相位**的，
        它假设输入是一个完整独立的录音。实时喂进来的音频是一块一块的
        （典型 1024–8192 样本 @48k ≈ 21–170 ms），而一个 AX.25 帧约
        300–500 ms（含前导）——**几乎每个帧都跨块**。逐块独立解调的结果是
        "看着在实时跑、实际解不出长帧"，比明确不支持更隐蔽。

        所以这里维护一个 2 秒滑动窗口：每来一块就追加、裁掉最旧，然后对
        **整个窗口**解调。窗口能装下完整帧，跨块问题就消失了；代价是每块
        重解 2 秒音频，用静默门限与去重把浪费压下去。

        去重：窗口滑动会让同一帧在连续几次解调里反复出现，按帧字节签名
        在 `STREAM_DEDUP_S` 内只报一次。
        """
        if not (_HAS_AX25 and _HAS_APRS and self._modem is not None):
            return
        if audio is None or len(audio) == 0:
            return

        import numpy as np

        try:
            arr = np.asarray(audio, dtype=np.float64).reshape(-1)
        except (TypeError, ValueError):
            return
        if arr.size == 0:
            return

        target_fs = float(getattr(self._modem, "sample_rate", 48000.0) or 48000.0)
        fs = float(sample_rate or target_fs)
        if abs(fs - target_fs) > 1.0:
            # 采样率不一致时做线性插值对齐。1200 bd 的 AFSK 只占 1–2.4 kHz，
            # 线性插值足够；换成"直接丢样本/复制样本"会把音调整体搬移，
            # 解出来全是错的。
            n_out = int(round(arr.size * target_fs / fs))
            if n_out <= 1:
                return
            arr = np.interp(np.linspace(0.0, arr.size - 1.0, n_out),
                            np.arange(arr.size, dtype=np.float64), arr)
            fs = target_fs

        # --- 滑动窗口 ---
        if self._audio_buf is None or self._audio_buf.size == 0:
            self._audio_buf = arr
        else:
            self._audio_buf = np.concatenate([self._audio_buf, arr])
        cap = int(round(self.STREAM_WINDOW_S * fs))
        if cap > 0 and self._audio_buf.size > cap:
            self._audio_buf = self._audio_buf[-cap:]
        if self._audio_buf.size == 0:
            return

        # 静默不白跑（整窗都没有能量，解调必然空转）
        if float(np.max(np.abs(self._audio_buf))) < self.STREAM_ENERGY_FLOOR:
            return

        self._feed_count += 1
        t0 = time.perf_counter()
        try:
            frames = self._modem.demodulate(self._audio_buf)
        except Exception:
            return
        dt_ms = (time.perf_counter() - t0) * 1000.0
        self._decode_ms_total += dt_ms
        self._decode_calls += 1

        # 去重窗口用单调时钟：墙钟被 NTP 回拨时，按 wall clock 算间隔会让
        # 同一帧在「时间倒流」后重复上报。
        now = time.monotonic()
        reported = 0
        for fr in frames or []:
            if reported >= self.STREAM_MAX_FRAMES:
                break
            if not getattr(fr, "fcs_valid", False):
                continue
            try:
                signature = bytes(fr.to_bytes())
            except Exception:
                signature = b""
            if signature:
                last = self._recent_frames.get(signature)
                if last is not None and (now - last) < self.STREAM_DEDUP_S:
                    continue
                self._recent_frames[signature] = now
            reported += 1
            self._consume_frame(fr)

        # 去重表清理：只留还在窗口期内的签名，避免长跑无限增长
        if self._recent_frames:
            stale = [k for k, ts in self._recent_frames.items()
                     if (now - ts) >= self.STREAM_DEDUP_S * 4]
            for k in stale:
                self._recent_frames.pop(k, None)

    def stream_diagnostics(self) -> dict:
        """流式解调的自检快照（平均块耗时等），供诊断面板/测试读取。"""
        calls = max(1, self._decode_calls)
        return {
            "feed_count": self._feed_count,
            "decode_calls": self._decode_calls,
            "avg_decode_ms": round(self._decode_ms_total / calls, 2),
            "window_s": self.STREAM_WINDOW_S,
            "buffered_samples": int(0 if self._audio_buf is None
                                    else self._audio_buf.size),
            "dedup_entries": len(self._recent_frames),
        }

    def submit_frame(self, frame) -> None:
        """直接喂一个 AX25Frame（测试/外部后端用）。"""
        self._consume_frame(frame)

    def submit_aprs_dict(self, pkt: dict) -> None:
        """直接喂一个已解析的 APRS dict（测试用）。"""
        if not pkt:
            return
        self.packet_ready.emit(pkt)

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
        self.packet_ready.emit(pkt)

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
        self.empty_hint.setVisible(not shown)
        self.table.setRowCount(len(shown))
        for row, p in enumerate(shown):
            # 缺收报时刻时显示「—」：etime 0 会渲染成 1970 年，那是假读数。
            _ts = p.get("_ts")
            ts = (time.strftime("%H:%M:%S", time.localtime(_ts))
                  if isinstance(_ts, (int, float)) and _ts > 0 else "—")
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
        self._refresh_table()
        self.detail.clear()
        self._refresh_header()
