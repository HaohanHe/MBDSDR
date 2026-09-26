"""
MBDSDR 自动调制识别面板（desktop/modulation_panel.py）
=====================================================

把内核 ``mbdsdr_ai.analysis.modulation_classifier.ModulationClassifier``
接成结果卡片：点「识别信号」→ 从真实后端读 N 个样点 IQ → 规则决策树识别
调制类型/置信度/建议解调链/符号率提示 → 卡片展示。点「应用」自动设置模式+带宽。

红线：
  * 无后端时按钮置灰、显「未连接，无法识别」，绝不造假识别结果。
  * 只读真实 ``read_samples()``；识别失败显示原因，不编造调制类型。
"""
from __future__ import annotations

import time
from typing import Optional

import numpy as np

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QPushButton, QLabel,
    QProgressBar, QListWidget, QListWidgetItem, QFormLayout,
)

try:  # pragma: no cover
    from mbdsdr_ai.analysis.modulation_classifier import ModulationClassifier
    _KERNEL_AVAILABLE = True
    _KERNEL_IMPORT_ERR = ""
except Exception as _e:  # noqa: BLE001
    ModulationClassifier = None  # type: ignore
    _KERNEL_AVAILABLE = False
    _KERNEL_IMPORT_ERR = str(_e)


# 内核调制名 -> 桌面解调模式 + 默认带宽建议
_MOD_TO_MODE = {
    "FM": ("NFM", 12_500.0),
    "AM": ("AM", 8_000.0),
    "OOK": ("DIG", 20_000.0),
    "ASK": ("DIG", 20_000.0),
    "FSK": ("DIG", 20_000.0),
    "PSK": ("DIG", 20_000.0),
    "QAM": ("DIG", 50_000.0),
    "NOISE": ("NFM", 12_500.0),
    "UNKNOWN": ("NFM", 12_500.0),
}


class _IdentifyWorker(QThread):
    """后台读真 IQ + 识别；无后端直接 failed，不造假。"""

    done = Signal(object)   # ModulationResult 或 dict
    failed = Signal(str)

    def __init__(self, backend, n_samples: int = 16384, parent=None):
        super().__init__(parent)
        self._backend = backend
        self._n = int(n_samples)

    def run(self):
        if not _KERNEL_AVAILABLE:
            self.failed.emit(f"识别内核不可用: {_KERNEL_IMPORT_ERR}")
            return
        be = self._backend
        if be is None or not callable(getattr(be, "read_samples", None)):
            self.failed.emit("未连接 SDR，无法识别")
            return
        try:
            sr = float(be.get_sample_rate())
        except Exception:  # noqa: BLE001
            sr = 2_400_000.0
        try:
            iq = be.read_samples(self._n)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"读取 IQ 失败: {e}")
            return
        if iq is None or len(iq) < 64:
            self.failed.emit("读取到空 IQ")
            return
        try:
            iq = np.asarray(iq, dtype=np.complex128)
            clf = ModulationClassifier()
            result = clf.classify(iq, sr)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"识别失败: {e}")
            return
        self.done.emit(result)


class ModulationPanel(QWidget):
    """自动调制识别结果卡片。"""

    apply_demod_requested = Signal(str, float)  # (mode, bandwidth_hz)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._backend = None
        self._connected = False
        self._worker: Optional[_IdentifyWorker] = None
        self._last = None  # 最近一次识别结果
        self._build_ui()
        self._refresh_state()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        box = QGroupBox("自动调制识别 (AMR)")
        v = QVBoxLayout(box)
        row = QHBoxLayout()
        self.identify_btn = QPushButton("识别信号")
        self.identify_btn.clicked.connect(self._on_identify_clicked)
        row.addWidget(self.identify_btn)
        row.addStretch()
        v.addLayout(row)

        self.mod_label = QLabel("--")
        self.mod_label.setAlignment(Qt.AlignCenter)
        f = self.mod_label.font()
        f.setPointSize(22)
        f.setBold(True)
        self.mod_label.setFont(f)
        v.addWidget(self.mod_label)

        conf_row = QHBoxLayout()
        conf_row.addWidget(QLabel("置信度"))
        self.conf_bar = QProgressBar()
        self.conf_bar.setRange(0, 100)
        self.conf_bar.setValue(0)
        conf_row.addWidget(self.conf_bar, stretch=1)
        self.conf_val = QLabel("--")
        conf_row.addWidget(self.conf_val)
        v.addLayout(conf_row)

        form = QFormLayout()
        self.bw_label = QLabel("--")
        self.sr_hint_label = QLabel("--")
        form.addRow("建议带宽", self.bw_label)
        form.addRow("符号率提示", self.sr_hint_label)
        v.addLayout(form)

        v.addWidget(QLabel("建议解调链（点击应用）:"))
        self.suggest_list = QListWidget()
        self.suggest_list.itemDoubleClicked.connect(self._on_apply_clicked)
        v.addWidget(self.suggest_list, stretch=1)

        self.apply_btn = QPushButton("应用该解调链")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._on_apply_clicked)
        v.addWidget(self.apply_btn)

        self.status_label = QLabel("未连接")
        self.status_label.setObjectName("hintLabel")
        v.addWidget(self.status_label)
        root.addWidget(box, stretch=1)

    # ------------------------------------------------------------ 外部接线
    def set_backend(self, backend):
        self._backend = backend
        self._refresh_state()

    def set_sdr_connected(self, connected: bool):
        self._connected = bool(connected)
        self._refresh_state()

    def _refresh_state(self):
        ready = bool(self._connected and self._backend is not None
                     and _KERNEL_AVAILABLE)
        working = self._worker is not None and self._worker.isRunning()
        self.identify_btn.setEnabled(ready and not working)
        if not _KERNEL_AVAILABLE:
            self.status_label.setText("识别内核不可用")
        elif not ready:
            self.status_label.setText("未连接，无法识别")
        elif working:
            self.status_label.setText("识别中…")
        else:
            self.status_label.setText("就绪：对准信号后点「识别信号」")

    # ------------------------------------------------------------------ slots
    def _on_identify_clicked(self):
        if self._worker is not None and self._worker.isRunning():
            return
        self.status_label.setText("识别中…")
        self.identify_btn.setEnabled(False)
        self._worker = _IdentifyWorker(self._backend, parent=self)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._refresh_state)
        self._worker.start()

    def _on_done(self, result):
        self._last = result
        mod = getattr(result, "modulation", "?")
        conf = float(getattr(result, "confidence", 0.0))
        bw = float(getattr(result, "bandwidth", 0.0))
        sym = getattr(result, "symbol_rate_hint", None)
        suggestions = list(getattr(result, "suggestions", []) or [])

        self.mod_label.setText(str(mod))
        self.conf_bar.setValue(int(conf * 100))
        self.conf_val.setText(f"{conf*100:.0f}%")
        self.bw_label.setText(f"{bw/1e3:.1f} kHz" if bw > 0 else "--")
        self.sr_hint_label.setText(
            f"{sym/1e3:.1f} kBd" if sym else "--")
        self.suggest_list.clear()
        for s in suggestions:
            self.suggest_list.addItem(QListWidgetItem(str(s)))
        self.apply_btn.setEnabled(True)
        self.status_label.setText("识别完成")

    def _on_failed(self, msg: str):
        self.status_label.setText(msg)
        self.apply_btn.setEnabled(False)

    def _on_apply_clicked(self, *_):
        if self._last is None:
            return
        mod = getattr(self._last, "modulation", "UNKNOWN")
        bw = float(getattr(self._last, "bandwidth", 0.0))
        mode, default_bw = _MOD_TO_MODE.get(mod, ("NFM", 12_500.0))
        # 带宽建议：内核估出的占用带宽非空则用它，否则用模式默认值
        apply_bw = bw if bw and bw > 100 else default_bw
        # 强 FM 且带宽 > 100 kHz → 大概率 WFM 广播
        if mod == "FM" and bw > 100_000:
            mode = "WFM"
        self.apply_demod_requested.emit(mode, float(apply_bw))
        self.status_label.setText(f"已请求应用：{mode} / {apply_bw/1e3:.1f} kHz")
