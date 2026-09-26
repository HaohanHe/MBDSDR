"""
MBDSDR 分段增益面板（desktop/gain_panel.py）
==============================================

对照 GQRX dockinputctl 的 LNA/Mixer/VGA 三档独立增益 + SDR++ gain 控件：
  * 三档增益独立滑块：LNA / Mixer / VGA（设备支持才显示）。
  * 每档显示当前值和设备范围（从 GainStager.getGainRange() 查询，不硬编码）。
  * 总增益显示（dB）。
  * AGC 开关（设备支持硬件 AGC 时可用）。
  * 不支持的档位滑块置灰显「不支持」。
  * 无后端时全部置灰。

红线：
  * 增益范围从设备查询（GainStager），绝不硬编码 0..40。
  * 无设备时显「未连接」，不造假范围。
"""
from __future__ import annotations

from typing import Optional, Dict

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSlider,
    QCheckBox, QGroupBox, QFormLayout,
)

try:  # pragma: no cover
    from mbdsdr_ai.gain_staging import GainStager
    _GAIN_OK = True
except Exception:  # noqa: BLE001
    GainStager = None  # type: ignore
    _GAIN_OK = False


class _StageSlider(QWidget):
    """单档增益滑块 + 数值 + 范围标签。"""

    value_changed = Signal(str, float)  # (logical_name, db)

    def __init__(self, logical: str, parent=None):
        super().__init__(parent)
        self.logical = logical
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        row = QHBoxLayout()
        self.name_label = QLabel(f"{logical}:")
        self.value_label = QLabel("-- dB")
        row.addWidget(self.name_label)
        row.addStretch()
        row.addWidget(self.value_label)
        lay.addLayout(row)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setEnabled(False)
        self.slider.valueChanged.connect(self._on_slider)
        lay.addWidget(self.slider)

        self.range_label = QLabel("范围: --")
        self.range_label.setStyleSheet("color:#888; font-size:8pt;")
        lay.addWidget(self.range_label)

        self._min = 0.0
        self._max = 0.0
        self._supported = False

    def setup_stage(self, elem_name: str, min_db: float, max_db: float,
                    supported: bool) -> None:
        self._supported = bool(supported)
        self._min = float(min_db)
        self._max = float(max_db)
        if supported:
            self.name_label.setText(f"{self.logical} ({elem_name}):")
            self.range_label.setText(f"范围: {min_db:.0f}..{max_db:.0f} dB")
            self.slider.setEnabled(True)
            span = max(1.0, max_db - min_db)
            self.slider.blockSignals(True)
            self.slider.setRange(0, max(1, int(span)))
            self.slider.blockSignals(False)
        else:
            self.name_label.setText(f"{self.logical}: 不支持")
            self.range_label.setText("设备无此增益档")
            self.slider.setEnabled(False)
            self.value_label.setText("-- dB")

    def set_value(self, db: float) -> None:
        if not self._supported:
            return
        self.value_label.setText(f"{db:.1f} dB")
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(db - self._min)))
        self.slider.blockSignals(False)

    def _on_slider(self, v: int):
        if not self._supported:
            return
        db = self._min + v
        self.value_label.setText(f"{db:.1f} dB")
        self.value_changed.emit(self.logical, float(db))


class GainPanel(QWidget):
    """分段增益控制面板。

    Signals
    -------
    stage_gain_changed(str, float)
        某档增益改变 (logical, db)。
    agc_changed(bool)
        AGC 开关。
    """

    stage_gain_changed = Signal(str, float)
    agc_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connected = False
        self._stager = None
        self._build_ui()
        self._apply_connected_state()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        box = QGroupBox("分段增益")
        form = QVBoxLayout(box)

        self.lna = _StageSlider("LNA")
        self.mixer = _StageSlider("Mixer")
        self.vga = _StageSlider("VGA")
        for s in (self.lna, self.mixer, self.vga):
            s.value_changed.connect(self._on_stage_changed)
            form.addWidget(s)

        # 总增益
        self.total_label = QLabel("总增益: -- dB")
        self.total_label.setObjectName("statusValue")
        form.addWidget(self.total_label)

        # AGC
        self.agc_chk = QCheckBox("硬件 AGC")
        self.agc_chk.toggled.connect(self.agc_changed.emit)
        form.addWidget(self.agc_chk)

        root.addWidget(box)
        root.addStretch()

    # ------------------------------------------------------------------ 后端接线
    def set_backend(self, backend) -> None:
        """主窗口注入已连接后端；用 GainStager 查询设备支持的增益档。"""
        self._connected = backend is not None
        self._stager = None
        if backend is None:
            self._apply_connected_state()
            return
        # 尝试从后端设备构造 GainStager（duck-type：listGains/getGainRange）
        dev = getattr(backend, "device", None) or backend
        if _GAIN_OK and GainStager is not None:
            try:
                self._stager = GainStager(dev, direction=0, channel=0)
                desc = self._stager.describe()
                for logical, sl in (("LNA", self.lna),
                                    ("Mixer", self.mixer),
                                    ("VGA", self.vga)):
                    info = desc.get(logical, {})
                    sl.setup_stage(
                        info.get("element", ""),
                        float(info.get("min_db", 0.0)),
                        float(info.get("max_db", 0.0)),
                        bool(info.get("supported", False)),
                    )
                    sl.set_value(float(info.get("current_db", 0.0)))
            except Exception:  # noqa: BLE001
                self._stager = None
                for sl in (self.lna, self.mixer, self.vga):
                    sl.setup_stage("", 0.0, 0.0, False)
        else:
            for sl in (self.lna, self.mixer, self.vga):
                sl.setup_stage("", 0.0, 0.0, False)
        self._update_total()
        self._apply_connected_state()

    def _apply_connected_state(self):
        ready = self._connected
        for sl in (self.lna, self.mixer, self.vga):
            # 滑块本身已按 supported 置灰；这里只控制 AGC
            pass
        self.agc_chk.setEnabled(ready)
        if not ready:
            self.total_label.setText("总增益: 未连接")

    # ------------------------------------------------------------------ 槽
    def _on_stage_changed(self, logical: str, db: float):
        if self._stager is not None:
            try:
                if logical == "LNA":
                    self._stager.set_lna(db)
                elif logical == "Mixer":
                    self._stager.set_mixer(db)
                elif logical == "VGA":
                    self._stager.set_vga(db)
            except Exception:  # noqa: BLE001
                pass
        self.stage_gain_changed.emit(logical, db)
        self._update_total()

    def _update_total(self):
        if not self._connected:
            return
        total = 0.0
        for sl in (self.lna, self.mixer, self.vga):
            if sl._supported:
                try:
                    total += float(sl.slider.value()) + sl._min
                except Exception:
                    pass
        self.total_label.setText(f"总增益: {total:.1f} dB")
