"""
MBDSDR 设备选择 / 热插拔面板（desktop/device_panel.py）
=======================================================

对照 SDR++ source 选择菜单 + GQRX device 面板：
  * 枚举已连接 SDR 设备（RTL-SDR/HackRF/BladeRF/PlutoSDR/SoapySDR）。
  * 刷新按钮重新枚举。
  * 设备参数：采样率 / 增益 / PPM 频偏校正。
  * 连接 / 断开按钮。
  * 热插拔检测：QTimer 每 2 秒轮询设备列表；拔出自动断开并显「设备已拔出」，
    插入提示「发现新设备」。
  * sounddevice 输出设备下拉枚举音频输出设备。

红线：
  * 无设备时列表显「未检测到 SDR 设备」，不造假设备。
  * 连接动作只发信号，真正 connect 由主窗口走 _connect_backend。
"""
from __future__ import annotations

from typing import Optional, List, Dict

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QLineEdit, QGroupBox, QFormLayout, QMessageBox,
)

try:  # pragma: no cover
    from mbdsdr_ai.sdr_backend import enumerate_all_sdr_devices
    _ENUM_OK = True
except Exception:  # noqa: BLE001
    enumerate_all_sdr_devices = None  # type: ignore
    _ENUM_OK = False


def _list_audio_output_devices() -> List[Dict]:
    """枚举 sounddevice 输出设备；无 sounddevice 时返回空列表（不造假）。"""
    try:
        import sounddevice as sd  # type: ignore
        out = []
        for i, d in enumerate(sd.query_devices()):
            if int(d.get("max_output_channels", 0)) > 0:
                out.append({"index": i, "name": str(d.get("name", f"设备{i}"))})
        return out
    except Exception:
        return []


class DevicePanel(QWidget):
    """设备选择 + 热插拔面板。

    Signals
    -------
    connect_requested(dict)
        用户点连接 → 主窗口用该设备 dict 构造后端并连接。
    disconnect_requested()
        用户点断开。
    audio_output_changed(int)
        音频输出设备 index 改变（-1 = 默认）。
    device_unplugged()
        热插拔检测到当前设备被拔出。
    device_plugged(str)
        检测到新设备插入（label）。
    """

    connect_requested = Signal(dict)
    disconnect_requested = Signal()
    audio_output_changed = Signal(int)
    device_unplugged = Signal()
    device_plugged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._devices: List[Dict] = []
        self._connected = False
        self._current_dev_key: Optional[str] = None
        self._current_dev_locator: Optional[tuple] = None
        self._build_ui()
        self.refresh_devices()

        # 热插拔轮询：每 2 秒
        self._hotplug_timer = QTimer(self)
        self._hotplug_timer.setInterval(2000)
        self._hotplug_timer.timeout.connect(self._on_hotplug_poll)
        self._hotplug_timer.start()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        # SDR 设备组
        sdr_box = QGroupBox("SDR 设备")
        form = QFormLayout(sdr_box)

        dev_row = QHBoxLayout()
        self.dev_combo = QComboBox()
        self.dev_combo.setMinimumWidth(180)
        dev_row.addWidget(self.dev_combo, stretch=1)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(self.refresh_devices)
        dev_row.addWidget(self.refresh_btn)
        form.addRow("设备", dev_row)

        # 参数
        self.sr_edit = QLineEdit("2048000")
        self.sr_edit.setToolTip("采样率 Hz")
        form.addRow("采样率(Hz)", self.sr_edit)

        self.gain_edit = QLineEdit("30")
        self.gain_edit.setToolTip("总增益 dB（AGC 关闭时生效）")
        form.addRow("增益(dB)", self.gain_edit)

        self.ppm_edit = QLineEdit("0")
        self.ppm_edit.setToolTip("频率校正 PPM")
        form.addRow("PPM", self.ppm_edit)

        # 连接按钮
        btn_row = QHBoxLayout()
        self.connect_btn = QPushButton("连接")
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        self.disconnect_btn = QPushButton("断开")
        self.disconnect_btn.clicked.connect(self.disconnect_requested.emit)
        self.disconnect_btn.setEnabled(False)
        btn_row.addWidget(self.connect_btn)
        btn_row.addWidget(self.disconnect_btn)
        form.addRow("", btn_row)

        self.status_label = QLabel("未检测到 SDR 设备")
        self.status_label.setObjectName("statusValue")
        form.addRow("", self.status_label)

        root.addWidget(sdr_box)

        # 音频输出组
        aud_box = QGroupBox("音频输出")
        aud_form = QFormLayout(aud_box)
        self.audio_combo = QComboBox()
        self.audio_combo.currentIndexChanged.connect(self._on_audio_changed)
        aud_form.addRow("输出设备", self.audio_combo)
        root.addWidget(aud_box)

        root.addStretch()
        self._refresh_audio_devices()

    # ------------------------------------------------------------------ 设备枚举
    def refresh_devices(self) -> None:
        """重新枚举 SDR 设备并刷新下拉框。"""
        self.dev_combo.clear()
        self._devices = []
        if _ENUM_OK and enumerate_all_sdr_devices is not None:
            try:
                self._devices = list(enumerate_all_sdr_devices())
            except Exception:  # noqa: BLE001
                self._devices = []
        if not self._devices:
            self.dev_combo.addItem("未检测到 SDR 设备")
            self.status_label.setText("未检测到 SDR 设备")
            self.connect_btn.setEnabled(False)
            return
        for d in self._devices:
            label = d.get("label") or d.get("driver") or "未知设备"
            self.dev_combo.addItem(label)
        self.status_label.setText(f"检测到 {len(self._devices)} 台设备")
        self.connect_btn.setEnabled(not self._connected)

    def _refresh_audio_devices(self) -> None:
        self.audio_combo.blockSignals(True)
        self.audio_combo.clear()
        self.audio_combo.addItem("系统默认", -1)
        for d in _list_audio_output_devices():
            self.audio_combo.addItem(d["name"], d["index"])
        self.audio_combo.blockSignals(False)

    # ------------------------------------------------------------------ 热插拔
    def _on_hotplug_poll(self):
        """每 2 秒轮询设备列表变化。"""
        if not _ENUM_OK or enumerate_all_sdr_devices is None:
            return
        try:
            now = enumerate_all_sdr_devices()
        except Exception:  # noqa: BLE001
            return
        now_keys = {self._key_of(d) for d in now}
        old_keys = {self._key_of(d) for d in self._devices}
        now_locs = {self._locator_of(d) for d in now}

        # 拔出：设备打开（占用）后再次枚举读不到 USB 字符串，serial 会变空、
        # tuner 变 Unknown，key 随之改变，但这并不代表拔出。因此连接中的设备
        # 同时用稳定的 (driver, index) 判在位：key 与 locator 都消失才算真拔出。
        if self._connected and self._current_dev_key and \
                self._current_dev_key not in now_keys and \
                self._current_dev_locator not in now_locs:
            self.status_label.setText("设备已拔出")
            self.device_unplugged.emit()
            self.set_connected(False)

        # 插入
        new_keys = now_keys - old_keys
        if new_keys and not self._connected:
            for d in now:
                if self._key_of(d) in new_keys:
                    label = d.get("label", "新设备")
                    self.device_plugged.emit(label)
                    break
            self.refresh_devices()

    @staticmethod
    def _key_of(d: Dict) -> str:
        return d.get("serial") or d.get("label") or d.get("driver", "?")

    @staticmethod
    def _locator_of(d: Dict) -> tuple:
        args = d.get("device_args") or {}
        try:
            idx = int(args.get("index", -1))
        except Exception:
            idx = -1
        return (d.get("driver") or "", idx)

    # ------------------------------------------------------------------ 状态
    def set_connected(self, connected: bool, dev_label: str = "") -> None:
        """主窗口连接成功/断开后调用。"""
        self._connected = bool(connected)
        if connected:
            self._current_dev_key = None
            self._current_dev_locator = None
            idx = self.dev_combo.currentIndex()
            if 0 <= idx < len(self._devices):
                self._current_dev_key = self._key_of(self._devices[idx])
                self._current_dev_locator = self._locator_of(self._devices[idx])
            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(True)
            self.status_label.setText(f"已连接: {dev_label or '设备'}")
        else:
            self._current_dev_key = None
            self._current_dev_locator = None
            self.connect_btn.setEnabled(bool(self._devices))
            self.disconnect_btn.setEnabled(False)
            if self.status_label.text() != "设备已拔出":
                self.status_label.setText("已断开")

    # ------------------------------------------------------------------ 槽
    def _on_connect_clicked(self):
        idx = self.dev_combo.currentIndex()
        if idx < 0 or idx >= len(self._devices):
            QMessageBox.information(self, "提示", "未选择 SDR 设备")
            return
        dev = dict(self._devices[idx])
        # 把面板上的参数塞进 dev，供主窗口连接时读取
        try:
            dev["_sample_rate"] = float(self.sr_edit.text())
        except Exception:
            dev["_sample_rate"] = 2_048_000.0
        try:
            dev["_gain"] = float(self.gain_edit.text())
        except Exception:
            dev["_gain"] = 30.0
        try:
            dev["_ppm"] = int(self.ppm_edit.text())
        except Exception:
            dev["_ppm"] = 0
        self.connect_requested.emit(dev)

    def _on_audio_changed(self, _idx: int):
        dev_index = self.audio_combo.currentData()
        self.audio_output_changed.emit(int(dev_index if dev_index is not None else -1))
