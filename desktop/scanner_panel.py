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
    done = Signal(list)             # list[dict]
    failed = Signal(str)

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

    def cancel(self):
        self._cancel = True

    def run(self):
        if not _KERNEL_AVAILABLE:
            self.failed.emit(f"扫频内核不可用: {_KERNEL_IMPORT_ERR}")
            return
        be = self._backend
        if be is None or not callable(getattr(be, "read_samples", None)) \
                or not callable(getattr(be, "set_frequency", None)):
            self.failed.emit("未连接 SDR 硬件，无法扫频")
            return

        try:
            sr = float(be.get_sample_rate())
        except Exception:  # noqa: BLE001
            sr = 2_400_000.0

        n_steps = max(1, int(round((self._f1 - self._f0) / self._step)))
        done_steps = [0]

        def psd_provider(center_hz: float, span_hz: float):
            if self._cancel:
                return np.array([]), np.array([])
            try:
                be.set_frequency(float(center_hz))
            except Exception:  # noqa: BLE001
                return np.array([]), np.array([])
            time.sleep(0.03)  # 驻留让 AGC/滤波器稳定
            try:
                iq = be.read_samples(int(self._dwell))
            except Exception:  # noqa: BLE001
                return np.array([]), np.array([])
            if iq is None or len(iq) < 64:
                return np.array([]), np.array([])
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
            self.failed.emit(f"扫频失败: {e}")
            return
        if self._cancel:
            self.done.emit([])
            return
        out = []
        for seg in segments:
            try:
                d = seg.to_dict()
            except Exception:  # noqa: BLE001
                continue
            d["suggested_mode"] = _KIND_TO_MODE.get(d.get("kind", "UNKNOWN"), "NFM")
            out.append(d)
        self.done.emit(out)


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
        self.scan_btn = QPushButton("开始扫描")
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
        root.addWidget(self.result_list, stretch=1)

        tip = QLabel("点击活动段 → 自动调谐并建议解调模式")
        tip.setObjectName("hintLabel")
        root.addWidget(tip)

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
            self.status_label.setText("扫描中…")
        else:
            self.status_label.setText("就绪")

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
        self.result_list.clear()
        self._segments = []
        self.progress_bar.setValue(0)
        self.scan_began.emit()
        self._worker = _ScanWorker(
            self._backend, f0, f1, step, self.thr_sp.value(), parent=self)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_done)
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
        self.status_label.setText(f"扫描中… {center_hz / 1e6:.3f} MHz")
        self.progress.emit(float(frac), float(center_hz))

    def _on_done(self, segments: list):
        self._segments = segments or []
        self.result_list.clear()
        for seg in self._segments:
            icon = _KIND_ICON.get(seg.get("kind", "UNKNOWN"), "•")
            text = (f"{icon} {seg['start_freq']/1e6:.3f}–"
                    f"{seg['end_freq']/1e6:.3f} MHz   "
                    f"峰值 {seg.get('peak_db', 0):.1f} dB   "
                    f"{seg.get('kind', '?')} → {seg.get('suggested_mode', '?')}")
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, seg)
            self.result_list.addItem(item)
        self.status_label.setText(f"完成：{len(self._segments)} 个活动段")
        self.done.emit(self._segments)

    def _on_failed(self, msg: str):
        self.status_label.setText(msg)
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
