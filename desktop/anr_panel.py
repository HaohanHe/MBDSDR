"""
MBDSDR ANR / 自动降噪面板（desktop/anr_panel.py）
==================================================

对照 SDR++ audio_filter / GQRX dockaudio 的降噪开关：
  * 噪声门（squelch，已有，不动）
  * ANR（自适应噪声抑制，谱减法 mbdsdr_ai.anr.SpectralSubtractionANR）
  * 降噪强度滑块（过减因子 α）
  * 实时显示降噪前后 SNR 改善（dB）

红线：
  * 默认关闭；开启才处理音频。
  * 无后端时全部置灰，不造假 SNR 数据。
  * 本面板只暴露控件与信号；真正的音频处理由主窗口在音频回调里调用 ANR。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QSlider, QComboBox, QGroupBox, QFormLayout,
)

try:  # pragma: no cover
    from mbdsdr_ai.anr import SpectralSubtractionANR, ANRConfig
    _ANR_OK = True
except Exception:  # noqa: BLE001
    SpectralSubtractionANR = None  # type: ignore
    ANRConfig = None  # type: ignore
    _ANR_OK = False


class AnrPanel(QWidget):
    """降噪控制面板。

    Signals
    -------
    enabled_changed(bool)
        ANR 总开关。
    strength_changed(float)
        降噪强度（过减因子 α，0..10）。
    learn_noise_requested()
        用户点「采样噪声」→ 主窗口用当前音频块更新噪声底。
    mode_changed(str)
        降噪类型："squelch" / "anr"。
    """

    enabled_changed = Signal(bool)
    strength_changed = Signal(float)
    learn_noise_requested = Signal()
    mode_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connected = False
        self._anr = None
        if _ANR_OK and SpectralSubtractionANR is not None:
            try:
                self._anr = SpectralSubtractionANR(ANRConfig())
            except Exception:  # noqa: BLE001
                self._anr = None
        self._build_ui()
        self._apply_connected_state()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        box = QGroupBox("自动降噪 (ANR)")
        form = QFormLayout(box)

        # 类型
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("噪声门 (Squelch)", "squelch")
        self.mode_combo.addItem("自适应谱减 (ANR)", "anr")
        self.mode_combo.currentIndexChanged.connect(
            lambda _i: self.mode_changed.emit(self.mode_combo.currentData()))
        form.addRow("降噪类型", self.mode_combo)

        # 总开关
        self.enable_chk = QCheckBox("启用 ANR")
        self.enable_chk.toggled.connect(self._on_enabled_toggled)
        form.addRow("", self.enable_chk)

        # 强度滑块
        strength_row = QHBoxLayout()
        self.strength_slider = QSlider(Qt.Horizontal)
        self.strength_slider.setRange(0, 100)
        self.strength_slider.setValue(20)  # 默认 α=2.0
        self.strength_slider.valueChanged.connect(self._on_strength_changed)
        self.strength_label = QLabel("2.0")
        strength_row.addWidget(self.strength_slider, stretch=1)
        strength_row.addWidget(self.strength_label)
        form.addRow("强度", strength_row)

        # 采样噪声按钮
        self.learn_btn = QPushButton("采样噪声底")
        self.learn_btn.setToolTip("在无信号/静音段按此按钮，学习当前噪声谱")
        self.learn_btn.clicked.connect(self.learn_noise_requested.emit)
        form.addRow("", self.learn_btn)

        # SNR 改善显示
        self.snr_label = QLabel("SNR 改善: -- dB")
        self.snr_label.setObjectName("statusValue")
        form.addRow("", self.snr_label)

        root.addWidget(box)
        root.addStretch()

    # ------------------------------------------------------------------ 状态
    def set_sdr_connected(self, connected: bool) -> None:
        self._connected = bool(connected)
        self._apply_connected_state()

    def _apply_connected_state(self):
        ready = self._connected
        self.enable_chk.setEnabled(ready)
        self.strength_slider.setEnabled(ready and self.enable_chk.isChecked())
        self.strength_label.setEnabled(ready)
        self.learn_btn.setEnabled(ready and self.enable_chk.isChecked())
        self.mode_combo.setEnabled(ready)
        if not ready:
            self.snr_label.setText("SNR 改善: 未连接")
        else:
            self.snr_label.setText("SNR 改善: -- dB")

    # ------------------------------------------------------------------ 槽
    def _on_enabled_toggled(self, on: bool):
        self.enabled_changed.emit(on)
        self.strength_slider.setEnabled(on)
        self.learn_btn.setEnabled(on)
        if self._anr is not None:
            self._anr.enabled = on

    def _on_strength_changed(self, val: int):
        # 滑块 0..100 → α 0..10
        alpha = val / 10.0
        self.strength_label.setText(f"{alpha:.1f}")
        self.strength_changed.emit(alpha)
        if self._anr is not None:
            self._anr.cfg.alpha = alpha

    # ------------------------------------------------------------------ 主窗口调用
    @property
    def anr(self):
        """主窗口音频回调里直接用这个 ANR 实例处理音频。"""
        return self._anr

    def is_anr_enabled(self) -> bool:
        return bool(self.enable_chk.isChecked())

    def set_snr_improvement(self, db: float) -> None:
        """主窗口每 N 帧回贴一次 SNR 改善（dB）。"""
        if self._connected:
            self.snr_label.setText(f"SNR 改善: {db:+.1f} dB")
