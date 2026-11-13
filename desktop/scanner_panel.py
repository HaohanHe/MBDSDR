# SPDX-License-Identifier: MIT
"""
MBDSDR 扫频面板（desktop/scanner_panel.py）
============================================

把内核 ``mbdsdr_ai.scanner.SweepScanner`` 接成用户可用的停靠面板：
在频谱页旁展开，输入起止频率/步进 → 后台线程步进调谐**真实 SDR** →
拼接 PSD → 提取活动段 → 列出可点击结果。

红线：
  * 无后端（``set_backend(None)`` / 未连接）时「开始扫描」置灰并显「未连接」，
    绝不合成 IQ、绝不展示假活动段。
  * psd_provider 真读 ``backend.read_samples()``，调谐 ``set_frequency()``。
  * 扫描期间由主窗口停掉共享 IQ 轮询（``scan_began`` / ``scan_ended`` 信号），
    避免两个消费者抢同一环形缓冲。

点击活动段 → 发 ``segment_chosen(dict)``（含频率范围/峰值 dB/估计信号类型），
由主窗口弹确认并真正调谐（本面板不直接碰硬件频率）。
"""
from __future__ import annotations

import time
from typing import Optional

import numpy as np

from constants import DEFAULT_SAMPLE_RATE_HZ

# 频率读数唯一口径（6 位小数 MHz = 1 Hz），扫频状态与结果行不再就地拼小数位。
from readouts import format_frequency_hz, format_frequency_range_hz

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QDoubleSpinBox, QSpinBox, QPushButton, QProgressBar, QListWidget,
    QListWidgetItem, QLabel, QMessageBox,
)

# 内核：用 hasattr/try 守卫，导入失败不崩（面板仍可实例化，只是不能真扫）
try:  # pragma: no cover - 内核在仓库内，正常导入必成功
    from mbdsdr_ai.scanner import SweepScanner, ActiveSegment, classify_segment
    _KERNEL_AVAILABLE = True
    _KERNEL_IMPORT_ERR = ""
except Exception as _e:  # noqa: BLE001
    SweepScanner = None  # type: ignore
    ActiveSegment = None  # type: ignore
    classify_segment = None  # type: ignore
    _KERNEL_AVAILABLE = False
    _KERNEL_IMPORT_ERR = str(_e)

try:  # pragma: no cover - 失败消息整形（clean_exc 战役先例），缺失时退化原文
    from mbdsdr_ai.tool_registry import clean_exc as _clean_exc
except Exception:  # noqa: BLE001
    def _clean_exc(e):  # type: ignore[misc]
        return str(e)


# 估计信号类型 -> 解调模式建议（与 control_panel 模式枚举对齐）
_KIND_TO_MODE = {
    "WFM": "WFM", "NFM": "NFM", "AM": "AM",
    "CW": "CW", "DIG": "DIG", "UNKNOWN": "NFM",
}

_KIND_ICON = {
    "WFM": "📻", "NFM": "📞", "AM": "📡",
    "CW": "✦", "DIG": "🖧", "UNKNOWN": "•",
}


class _ScanWorker(QThread):
    """后台步进扫频：真调谐真实后端取 IQ → FFT PSD → SweepScanner 提取活动段。

    绝不合成 IQ；后端读失败的步直接返回 None，SweepScanner 拼接时跳过。
    """

    progress = Signal(float, float)   # (0..1 进度, 当前中心 Hz)
    done = Signal(list, int)        # (list[dict] 活动段, 读取失败步数)
    cancelled = Signal(list, int)   # (已扫到的活动段, 读取失败步数)
    failed = Signal(str, int)       # (失败说明, 读取失败步数)

    def __init__(self, backend, f_start_hz: float, f_stop_hz: float,
                 step_hz: float, threshold_db: float,
                 fft_size: int = 4096, dwell_samples: int = 16384,
                 parent=None):
        super().__init__(parent)
        self._backend = backend
        self._f0 = float(f_start_hz)
        self._f1 = float(f_stop_hz)
        self._step = float(step_hz)
        self._threshold = float(threshold_db)
        self._fft = int(fft_size)
        self._dwell = int(dwell_samples)
        self._cancel = False
        # 读取失败计数：全失败时不能落成「0 个活动段」的空态（S1）。
        self.read_attempts = 0
        self.read_failures = 0
        self.first_read_error = ""

    def cancel(self):
        self._cancel = True

    def run(self):
        if not _KERNEL_AVAILABLE:
            self.failed.emit(f"扫频内核不可用: {_KERNEL_IMPORT_ERR}", 0)
            return
        be = self._backend
        if be is None or not callable(getattr(be, "read_samples", None)) \
                or not callable(getattr(be, "set_frequency", None)):
            self.failed.emit("未连接 SDR 硬件，无法扫频", 0)
            return

        try:
            sr = float(be.get_sample_rate())
        except Exception:  # noqa: BLE001
            sr = float(DEFAULT_SAMPLE_RATE_HZ)

        n_steps = max(1, int(round((self._f1 - self._f0) / self._step)))
        done_steps = [0]

        def _read_failed(reason: str):
            """记一次读失败：失败要计数并上报，不能吞成空数组当成「没信号」。"""
            self.read_failures += 1
            if not self.first_read_error:
                self.first_read_error = reason
            return np.array([]), np.array([])

        def psd_provider(center_hz: float, span_hz: float):
            if self._cancel:
                return np.array([]), np.array([])
            self.read_attempts += 1
            try:
                be.set_frequency(float(center_hz))
            except Exception as e:  # noqa: BLE001
                return _read_failed("调谐失败: %s" % _clean_exc(e))
            time.sleep(0.03)  # 驻留让 AGC/滤波器稳定
            try:
                iq = be.read_samples(int(self._dwell))
            except Exception as e:  # noqa: BLE001
                return _read_failed("读取失败: %s" % _clean_exc(e))
            if iq is None:
                return _read_failed("设备返回空采样（read_samples 为 None）")
            if len(iq) < 64:
                return _read_failed("设备返回采样过少（%d < 64）" % len(iq))
            iq = np.asarray(iq, dtype=np.complex128)
            iq = iq - np.mean(iq)
            n = min(self._fft, len(iq))
            iq = iq[:n]
            win = np.hanning(n)
            spec = np.fft.fftshift(np.fft.fft(iq * win))
            psd = 20.0 * np.log10(np.abs(spec) + 1e-12)
            freqs = center_hz + np.fft.fftshift(np.fft.fftfreq(n, d=1.0 / sr))
            done_steps[0] += 1
            frac = done_steps[0] / n_steps
            self.progress.emit(float(min(1.0, frac)), float(center_hz))
            return freqs, psd

        try:
            scanner = SweepScanner(
                start_hz=self._f0, stop_hz=self._f1, step_hz=self._step,
                dwell_samples=self._dwell, threshold_db=self._threshold,
                min_sep_hz=self._step * 0.5,
            )
            scanner.psd_provider = psd_provider
            segments = scanner.sweep()
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"扫频失败: {_clean_exc(e)}", self.read_failures)
            return
        out = []
        for seg in segments:
            try:
                d = seg.to_dict()
            except Exception:  # noqa: BLE001
                continue
            d["suggested_mode"] = _KIND_TO_MODE.get(d.get("kind", "UNKNOWN"), "NFM")
            out.append(d)
        if self._cancel:
            # 取消不是「完成」：保留已扫到的段，交给面板按「已取消」呈现。
            self.cancelled.emit(out, self.read_failures)
            return
        if self.read_attempts and self.read_failures >= self.read_attempts:
            # 一次都没读回 IQ：这不是「频段干净」，是设备没给数据。
            # 两种结论对用户意味着相反的下一步（换门限 vs 检查设备），
            # 必须明确报读取失败，不许落进「0 个活动段」的空态。
            self.failed.emit(
                "设备未返回数据（%d 次读取失败）\n"
                "没有读到任何 IQ 采样，不能判断这个频段有没有信号。\n"
                "首个错误：%s\n"
                "请检查设备连接后重试——调门限或步进解决不了读失败。"
                % (self.read_failures, self.first_read_error or "未知"),
                self.read_failures)
            return
        self.done.emit(out, self.read_failures)


def empty_hint_text(scan_ran: bool, connected: bool,
                    kernel_ok: bool, outcome: str = "done",
                    read_failures: int = 0) -> str:
    """空态文案按真实状态分支：没开始扫 / 扫完但 0 段 / 读取失败 / 已取消。

    已连接且扫完了还说「连上设备后点开始扫频」是空态误导——用户会去
    找设备，而真正该做的是调门限或步进。反过来，设备一次都没读回数据时
    说「没有超过门限的活动段」同样误导：那是设备问题，不是门限问题。
    """
    if outcome == "failed":
        return ("上次扫频没有读到设备数据（%d 次读取失败）\n"
                "请检查设备连接后重新扫频（调门限/步进解决不了读失败）"
                % int(read_failures or 0))
    if outcome == "cancelled":
        return "扫频已取消，没有扫到活动段\n重新点「开始扫频」可继续"
    if scan_ran and connected and kernel_ok:
        return ("本次扫频没有发现超过门限的活动段\n"
                "调低「门限余量」或减小「步进」后重新扫频")
    return "还没有活动段\n连上设备后点「开始扫频」"


class ScannerPanel(QWidget):
    """扫频面板：参数输入 + 进度 + 活动段列表。"""

    segment_chosen = Signal(dict)   # 用户点击活动段
    scan_began = Signal()           # 主窗口据此停共享 IQ 轮询
    scan_ended = Signal()           # 主窗口据此恢复（若仍连接）
    # 向上层 re-emit 扫频进度/结果，供主窗口状态栏回显（面板内部自绘进度条/列表）。
    progress = Signal(float, float)   # (0..1 进度, 当前中心 Hz)
    done = Signal(list)               # list[dict] 活动段
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._backend = None
        self._connected = False
        self._worker: Optional[_ScanWorker] = None
        self._segments: list = []
        self._scan_ran = False
        # 上一次扫频的结局：idle / done / cancelled / failed。取消与读取失败
        # 都不能沿用「完成：0 个活动段」的说法（S1/S2）。
        self._scan_outcome = "idle"
        self._read_failures = 0
        # 终态文案：worker 结束后 _refresh_state 不得用「就绪」把它冲掉。
        self._terminal_status = ""
        self._build_ui()
        self._refresh_state()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        params = QGroupBox("步进扫频参数")
        form = QFormLayout(params)
        self.start_sp = QDoubleSpinBox()
        self.start_sp.setRange(0.01, 6000.0)
        self.start_sp.setDecimals(3)
        self.start_sp.setSuffix(" MHz")
        self.start_sp.setValue(87.0)
        self.stop_sp = QDoubleSpinBox()
        self.stop_sp.setRange(0.01, 6000.0)
        self.stop_sp.setDecimals(3)
        self.stop_sp.setSuffix(" MHz")
        self.stop_sp.setValue(108.0)
        self.step_sp = QDoubleSpinBox()
        self.step_sp.setRange(0.005, 20.0)
        self.step_sp.setDecimals(3)
        self.step_sp.setSuffix(" MHz")
        self.step_sp.setValue(0.1)
        self.thr_sp = QDoubleSpinBox()
        self.thr_sp.setRange(0.0, 30.0)
        self.thr_sp.setDecimals(1)
        self.thr_sp.setSuffix(" dB")
        self.thr_sp.setValue(6.0)
        form.addRow("起始", self.start_sp)
        form.addRow("结束", self.stop_sp)
        form.addRow("步进", self.step_sp)
        form.addRow("门限余量", self.thr_sp)
        root.addWidget(params)

        row = QHBoxLayout()
        self.scan_btn = QPushButton("开始扫频")
        self.scan_btn.clicked.connect(self._on_start_clicked)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        row.addWidget(self.scan_btn)
        row.addWidget(self.cancel_btn)
        root.addLayout(row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        root.addWidget(self.progress_bar)

        self.status_label = QLabel("未连接")
        self.status_label.setObjectName("hintLabel")
        root.addWidget(self.status_label)

        self.result_list = QListWidget()
        self.result_list.itemDoubleClicked.connect(self._on_item_activated)
        self.result_list.itemClicked.connect(self._on_item_clicked)
        # 空列表占位：整块 300px 的灰黑空框读起来像「扫完了但没结果」或
        # 「界面坏了」，两种都是误导。没有活动段时换成一句说明，占同一块
        # 空间；有结果立刻换回列表（不删控件，只切换可见性）。
        self.empty_hint = QLabel(
            "还没有活动段\n连上设备后点「开始扫频」")
        self.empty_hint.setObjectName("scanEmptyHint")
        self.empty_hint.setAlignment(Qt.AlignCenter)
        self.empty_hint.setWordWrap(True)
        root.addWidget(self.empty_hint, stretch=1)
        root.addWidget(self.result_list, stretch=1)
        self._sync_empty_hint()

        tip = QLabel("点击活动段 → 自动调谐并建议解调模式")
        tip.setObjectName("hintLabel")
        root.addWidget(tip)

    def _sync_empty_hint(self) -> None:
        """结果列表与空态提示二选一（两者都保留，只切可见性）。

        空态文案按真实状态分两种：没开始扫 / 扫完但 0 段。已连接却说
        「连上设备后点『开始扫频』」是空态误导，用户会去找设备。
        """
        empty = self.result_list.count() == 0
        if empty:
            self.empty_hint.setText(
                empty_hint_text(self._scan_ran, self._connected,
                                _KERNEL_AVAILABLE,
                                outcome=self._scan_outcome,
                                read_failures=self._read_failures))
        self.empty_hint.setVisible(empty)
        self.result_list.setVisible(not empty)

    # ------------------------------------------------------------ 外部接线
    def set_backend(self, backend):
        """注入真实 SDR 后端（None = 无硬件）。"""
        self._backend = backend
        self._refresh_state()

    def set_sdr_connected(self, connected: bool):
        self._connected = bool(connected)
        self._refresh_state()

    def _refresh_state(self):
        ready = bool(self._connected and self._backend is not None
                     and _KERNEL_AVAILABLE)
        scanning = self._worker is not None and self._worker.isRunning()
        self.scan_btn.setEnabled(ready and not scanning)
        self.cancel_btn.setEnabled(scanning)
        for sp in (self.start_sp, self.stop_sp, self.step_sp, self.thr_sp):
            sp.setEnabled(not scanning)
        if not _KERNEL_AVAILABLE:
            self.status_label.setText("扫频内核不可用")
        elif not ready:
            self.status_label.setText("未连接")
        elif scanning:
            self.status_label.setText("扫频中…")
        else:
            # 保留上一次的终态（完成/已取消/读取失败）；没有终态才是「就绪」。
            self.status_label.setText(self._terminal_status or "就绪")
        self._sync_empty_hint()

    # ------------------------------------------------------------------ slots
    def _on_start_clicked(self):
        if self._worker is not None and self._worker.isRunning():
            return
        f0 = self.start_sp.value() * 1e6
        f1 = self.stop_sp.value() * 1e6
        step = self.step_sp.value() * 1e6
        if f1 <= f0:
            QMessageBox.warning(self, "参数错误", "结束频率必须大于起始频率。")
            return
        # 防呆：开始新扫描会丢弃上一次的 N 条结果，必须先确认。
        # 结果虽可重扫，但用户可能正对着某条活动段在看（误点一下全没了）。
        if self.result_list.count() > 0:
            ans = QMessageBox.question(
                self, "开始新扫频？",
                f"当前有 {self.result_list.count()} 条扫频结果，"
                "开始新扫频会清空它们。是否继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans != QMessageBox.Yes:
                return
        self.result_list.clear()
        self._scan_ran = False
        self._scan_outcome = "idle"
        self._read_failures = 0
        self._terminal_status = ""
        self._sync_empty_hint()
        self._segments = []
        self.progress_bar.setValue(0)
        self.scan_began.emit()
        self._worker = _ScanWorker(
            self._backend, f0, f1, step, self.thr_sp.value(), parent=self)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_done)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_worker_finished)
        self._refresh_state()
        self._worker.start()

    def _on_cancel_clicked(self):
        if self._worker is not None:
            self._worker.cancel()
            self.status_label.setText("正在取消…")

    def _on_progress(self, frac: float, center_hz: float):
        self.progress_bar.setValue(int(frac * 100))
        self.status_label.setText(f"扫频中… {format_frequency_hz(center_hz)}")
        self.progress.emit(float(frac), float(center_hz))

    def _render_segments(self):
        """把当前活动段列表画进结果区（完成与取消共用）。"""
        self.result_list.clear()
        for seg in self._segments:
            icon = _KIND_ICON.get(seg.get("kind", "UNKNOWN"), "•")
            peak_db = seg.get("peak_db")
            peak_txt = "—" if peak_db is None else f"{float(peak_db):.1f} dB"
            text = (f"{icon} "
                    f"{format_frequency_range_hz(seg['start_freq'], seg['end_freq'])}   "
                    f"峰值 {peak_txt}   "
                    f"{seg.get('kind', '?')} → {seg.get('suggested_mode', '?')}")
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, seg)
            self.result_list.addItem(item)

    def _on_done(self, segments: list, failed_reads: int = 0):
        self._segments = segments or []
        self._scan_ran = True
        self._scan_outcome = "done"
        self._read_failures = int(failed_reads or 0)
        self._render_segments()
        note = (f"（另 {self._read_failures} 步读取失败，"
                "结果只覆盖成功读到的频点）" if self._read_failures else "")
        self._terminal_status = (
            f"完成：{len(self._segments)} 个活动段{note}")
        self.status_label.setText(self._terminal_status)
        self._sync_empty_hint()
        self.done.emit(self._segments)

    def _on_cancelled(self, segments: list, failed_reads: int = 0):
        """用户取消：保留已扫到的段，明确写「已取消」而不是「完成：0 个」。"""
        self._segments = segments or []
        self._scan_ran = True
        self._scan_outcome = "cancelled"
        self._read_failures = int(failed_reads or 0)
        self._render_segments()
        note = (f"（其中 {self._read_failures} 步读取失败）"
                if self._read_failures else "")
        self._terminal_status = (
            f"已取消：保留已扫到的 {len(self._segments)} 个活动段{note}")
        self.status_label.setText(self._terminal_status)
        self._sync_empty_hint()

    def _on_failed(self, msg: str, read_failures: int = 0):
        self._scan_outcome = "failed"
        self._scan_ran = False
        self._read_failures = int(read_failures or 0)
        # 状态条只放第一行（多行详情在弹窗里），且不被 finished 后的
        # _refresh_state 冲成「就绪」。
        self._terminal_status = msg.splitlines()[0] if msg else msg
        self.status_label.setText(self._terminal_status)
        self._sync_empty_hint()
        self.failed.emit(msg)
        QMessageBox.warning(self, "扫频", msg)

    def _on_worker_finished(self):
        self.scan_ended.emit()
        self._refresh_state()

    def _on_item_clicked(self, item: QListWidgetItem):
        seg = item.data(Qt.UserRole)
        if isinstance(seg, dict):
            self.segment_chosen.emit(dict(seg))

    def _on_item_activated(self, item: QListWidgetItem):
        seg = item.data(Qt.UserRole)
        if isinstance(seg, dict):
            self.segment_chosen.emit(dict(seg))
