# SPDX-License-Identifier: MIT
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

import math
from typing import Optional, Dict

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSlider,
    QCheckBox, QGroupBox, QFormLayout,
)

from tokens import tokens

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
        lay.addWidget(self.range_label)
        self._set_range_color(exempt=True)

        self._min = 0.0
        self._max = 0.0
        self._supported = False

    def _set_range_color(self, exempt: bool) -> None:
        """禁用档豁免 ≠ 达标：档位可用时，同一行字必须回到 ≥4.5 的主题角色。

        ``disabled_gray`` 只给「设备无此增益档 / 未连接」这类禁用态用；
        WCAG 对这种状态豁免对比度，但豁免不是「这个面板随便什么色都行」。
        """
        color = ("palette(placeholder-text)" if not exempt
                 else tokens().COLORS["disabled_gray"])
        self.range_label.setStyleSheet(f"color:{color}; font-size:8pt;")

    def setup_stage(self, elem_name: str, min_db: float, max_db: float,
                    supported: bool) -> None:
        self._supported = bool(supported)
        self._min = float(min_db)
        self._max = float(max_db)
        self._set_range_color(exempt=not supported)
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
    #: 分档增益没落到硬件时发出人话原因（上层必须把它变成可见失败，
    #: 绝不能把「滑杆动了」当成「增益已生效」）。
    stage_gain_failed = Signal(str)
    agc_changed = Signal(bool)
    # 向上层 re-emit 原始滑杆值（logical, db），供主窗口同步后端/状态栏。
    value_changed = Signal(str, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connected = False
        self._stager = None
        #: 每档最后一次**确认已下发**的值（clamp 后）。失败回读时兜底用，
        #: 与 ControlPanel 的「滑杆=配置意图，标签=实际读数」同口径。
        self._applied: Dict[str, float] = {}
        self._build_ui()
        self._apply_connected_state()

    def _slider_for(self, logical: str):
        return {"LNA": self.lna, "Mixer": self.mixer,
                "VGA": self.vga}.get(logical)

    def _read_back_stage(self, logical: str) -> Optional[float]:
        """失败时向设备**真回读**当前增益；做不到就返回 None。

        ``GainStager.stages[logical].current_db`` 初始化成 0.0，并不是设备
        真值（``_set_stage`` 只在下发成功后才改它）。拿它冒充回读值等于
        编造一个 0 dB，比显示「未知」更糟。
        """
        stager = self._stager
        try:
            info = stager.stage_info(logical)
            dev = getattr(stager, "device", None)
            getter = getattr(dev, "getGain", None)
            if info and dev is not None and callable(getter):
                value = getter(getattr(stager, "direction", 0),
                               getattr(stager, "channel", 0), info.elem_name)
                if value is not None and math.isfinite(float(value)):
                    return float(value)
        except Exception:  # noqa: BLE001 - 无回读能力的设备一律视为「未知」
            pass
        return None

    def _mark_stage_failed(self, logical: str, requested: float,
                           reason: str) -> None:
        """把该档标签改成设备实际状态 + ⚠，滑杆保持用户意图。"""
        sl = self._slider_for(logical)
        if sl is None:
            return
        actual = self._read_back_stage(logical)
        if actual is None:
            sl.value_label.setText("-- dB ⚠ 未生效")
            state = "设备当前值未知（无回读能力）"
        else:
            sl.value_label.setText(f"{actual:.1f} dB ⚠ 未生效")
            state = f"设备回读当前值 {actual:.1f} dB"
        sl.value_label.setToolTip(
            f"请求 {requested:.1f} dB 未下发；{state}。"
            f"滑杆仍是配置意图，不代表硬件状态。原因：{reason}")

    def _mark_stage_applied(self, logical: str, requested: float) -> None:
        """成功：标签写设备实际生效值（stager 会 clamp），清掉失败标注。"""
        sl = self._slider_for(logical)
        actual = requested
        try:
            info = self._stager.stage_info(logical)
            if info is not None and info.current_db is not None:
                actual = float(info.current_db)
        except Exception:  # noqa: BLE001 - 无 stage_info 的测试桩用请求值
            pass
        self._applied[logical] = actual
        if sl is None:
            return
        sl.value_label.setText(f"{actual:.1f} dB")
        if abs(actual - requested) > 0.05:
            sl.value_label.setToolTip(
                f"⚠ 设备夹到 {actual:.1f} dB；滑杆 {requested:.1f} dB 是配置意图")
        else:
            sl.value_label.setToolTip("")

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
        # 换设备 = 旧的「已下发值/未生效」标注全部失效，不许带过去冒充新设备状态
        self._applied.clear()
        for sl in (self.lna, self.mixer, self.vga):
            sl.value_label.setToolTip("")
        if backend is None:
            self._apply_connected_state()
            return
        # 尝试从后端设备构造 GainStager（duck-type：listGains/getGainRange）。
        # 优先原生设备句柄（_sdr）：backend.device 是 SDRDevice 信息类，
        # 没有 listGains/getGainRange，传它会让三档增益被误判为全不支持。
        dev = (getattr(backend, "_sdr", None)
               or getattr(backend, "device", None) or backend)
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
        if self._stager is None:
            reason = "设备未提供分档增益"
            self.stage_gain_failed.emit(
                f"分档增益未生效：{reason}（{logical} {db:.1f} dB 未下发）")
            self._mark_stage_failed(logical, db, reason)
            return
        setter = {"LNA": "set_lna", "Mixer": "set_mixer",
                  "VGA": "set_vga"}.get(logical)
        if setter is None:
            return
        try:
            ok = getattr(self._stager, setter)(db)
        except Exception as e:  # noqa: BLE001 - 失败必须上报，不许当成功
            self.stage_gain_failed.emit(
                f"分档增益未生效（{logical} {db:.1f} dB）：{e}")
            self._mark_stage_failed(logical, db, str(e))
            return
        # 关键：GainStager._set_stage 内部 catch 住 device.setGain 异常后
        # **返回 False**（不抛），这才是硬件拒绝的主要形态。旧码完全不看
        # 返回值，于是 LNA 显示已改、总增益已变，硬件其实一动没动。
        if ok is False:
            reason = "设备拒绝或未下发该档（后端返回 False）"
            self.stage_gain_failed.emit(
                f"分档增益未生效（{logical} {db:.1f} dB）：{reason}")
            self._mark_stage_failed(logical, db, reason)
            return
        self._mark_stage_applied(logical, db)
        self.stage_gain_changed.emit(logical, db)
        self.value_changed.emit(logical, db)
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
