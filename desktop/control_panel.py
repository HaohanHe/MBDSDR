"""
MBDSDR 调谐控制面板
====================
频率显示/调节、模式选择、音量、预设电台、录音控制。
"""

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSlider, QComboBox, QLineEdit, QFrame, QGroupBox, QSizePolicy
)
from PySide6.QtGui import QFont, QIntValidator, QDoubleValidator


# 预设电台（FM，MHz）
# 不预存地区性广播台：FM 频率随城市/地区不同，硬编码其他城市的电台没有意义。
# 本地电台请用“自动扫台”（sdr_fm_scan）动态发现，或在频率框手动输入后自行保存。
PRESET_FM_STATIONS: list[tuple[str, str]] = []

# 预设 AM 电台（kHz）
# 同样不预存地区性中波台：频率随地区不同。请自动扫台或手动输入。
PRESET_AM_STATIONS: list[tuple[str, str]] = []

# SDR 常用频段预设（频率MHz, 名称, 模式）
SDR_BAND_PRESETS = [
    # 航空
    ("118.000", "航空塔台", "AM"),
    ("121.500", "航空应急", "AM"),
    ("123.450", "航空通联", "AM"),
    ("132.000", "航空进近", "AM"),
    # 业余无线电 2m
    ("144.390", "APRS 144", "AFSK"),
    ("145.800", "ISS APRS", "AFSK"),
    ("145.500", "2m 呼叫", "FM"),
    ("146.520", "2m 全美呼叫", "FM"),
    # 业余无线电 70cm
    ("432.100", "70cm CW", "CW"),
    ("433.000", "70cm 呼叫", "FM"),
    ("435.000", "70cm 卫星", "FM"),
    # 气象卫星
    ("137.100", "NOAA 19 APT", "FM"),
    ("137.620", "NOAA 15 APT", "FM"),
    ("137.9125", "NOAA 18 APT", "FM"),
    ("137.100", "METEOR M2", "FM"),
    # 导航
    ("1575.42", "GPS L1", "PSK"),
    ("1561.098", "北斗 B1", "PSK"),
    ("1227.60", "GPS L2", "PSK"),
    # 广播
    ("87.500", "FM 广播起点", "WFM"),
    ("108.000", "FM 广播终点", "WFM"),
    # 工业/物联网
    ("433.920", "ISM 433", "ASK"),
    ("868.000", "ISM 868", "FSK"),
    ("915.000", "ISM 915", "FSK"),
    ("2400.00", "WiFi/BT 2.4G", "QAM"),
    # 短波
    ("3.500", "80m 业余", "LSB"),
    ("7.000", "40m 业余", "LSB"),
    ("14.000", "20m 业余", "USB"),
    ("21.000", "15m 业余", "USB"),
    ("28.000", "10m 业余", "USB"),
]


class ControlPanel(QWidget):
    """调谐控制面板。"""

    # 信号
    tune_fm_requested = Signal(float)    # 请求调谐 FM (MHz)
    tune_am_requested = Signal(int)      # 请求调谐 AM (kHz)
    tune_sdr_requested = Signal(float, str)  # 请求调谐 SDR (Hz, 模式)
    volume_changed = Signal(int)          # 音量改变 (0-63)
    record_toggled = Signal(bool)         # 录音开关
    mode_changed = Signal(str)            # 模式改变 ("FM"/"AM")
    gain_changed = Signal(int)             # 硬件增益 (dB, RTL-SDR LNA 0~49)
    squelch_changed = Signal(float)        # 静噪门限 (dBFS, -120~0)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_mode = "FM"
        self._current_freq_fm = 98.5
        self._current_freq_am = 980
        self._volume = 30
        self._recording = False
        self._record_seconds = 0

        self._build_ui()
        self._update_freq_display()
        # 默认未连接 SDR：调谐/音量/模式/录音控件全部置灰，绝不暴露假可控状态
        self.set_sdr_connected(False)

    def set_sdr_connected(self, connected: bool):
        """无真实 SDR 时禁用调谐/音量/模式/录音等操作控件。

        连接成功后由 main_window._panels_set_sdr_connected(True) 统一启用；
        断开时再置灰。频率只读显示(freq_display)保持可用展示，不置灰。
        """
        self._sdr_connected = bool(connected)
        widgets = [
            getattr(self, "mode_combo", None),
            getattr(self, "freq_input", None),
            getattr(self, "tune_button", None),
            getattr(self, "band_combo", None),
            getattr(self, "preset_combo", None),
            getattr(self, "volume_slider", None),
            getattr(self, "record_button", None),
        ]
        widgets += self._step_buttons
        for w in widgets:
            if w is not None:
                w.setEnabled(self._sdr_connected)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # ---- 频率显示区 ----
        freq_group = QGroupBox("频率")
        freq_layout = QVBoxLayout(freq_group)

        # 大字体频率显示
        self.freq_display = QLabel("98.50 MHz")
        self.freq_display.setObjectName("freqDisplay")
        self.freq_display.setAlignment(Qt.AlignCenter)
        self.freq_display.setMinimumHeight(60)
        freq_layout.addWidget(self.freq_display)

        # 模式选择 + 频率输入
        mode_freq_row = QHBoxLayout()

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["FM", "AM"])
        self.mode_combo.setFixedWidth(80)
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        mode_freq_row.addWidget(self.mode_combo)

        self.freq_input = QLineEdit("98.5")
        self.freq_input.setFixedHeight(32)
        self.freq_input.returnPressed.connect(self._on_freq_input)
        mode_freq_row.addWidget(self.freq_input)

        self.tune_button = QPushButton("调谐")
        self.tune_button.setFixedHeight(32)
        self.tune_button.clicked.connect(self._on_freq_input)
        mode_freq_row.addWidget(self.tune_button)

        freq_layout.addLayout(mode_freq_row)

        # 频率步进按钮
        self._step_buttons = []
        step_row = QHBoxLayout()
        for step, label in [(-1.0, "-1.0"), (-0.1, "-0.1"), (0.1, "+0.1"), (1.0, "+1.0")]:
            btn = QPushButton(label)
            btn.setFixedHeight(28)
            btn.clicked.connect(lambda checked, s=step: self._step_freq(s))
            self._step_buttons.append(btn)
            step_row.addWidget(btn)
        freq_layout.addLayout(step_row)

        layout.addWidget(freq_group)

        # ---- 预设电台 ----
        preset_group = QGroupBox("频率预设")
        preset_layout = QVBoxLayout(preset_group)

        # 频段分类选择
        band_row = QHBoxLayout()
        self.band_combo = QComboBox()
        self.band_combo.setFixedHeight(28)
        self.band_combo.addItems(["FM 广播", "AM 广播", "航空", "业余 2m", "业余 70cm", "气象卫星", "导航", "ISM/物联网", "短波"])
        self.band_combo.currentIndexChanged.connect(self._on_band_changed)
        band_row.addWidget(QLabel("频段:"))
        band_row.addWidget(self.band_combo)
        preset_layout.addLayout(band_row)

        self.preset_combo = QComboBox()
        self.preset_combo.setFixedHeight(32)
        self._populate_presets("FM 广播")
        self.preset_combo.currentIndexChanged.connect(self._on_preset_selected)
        preset_layout.addWidget(self.preset_combo)

        layout.addWidget(preset_group)

        # ---- 音量 ----
        volume_group = QGroupBox("音量")
        volume_layout = QVBoxLayout(volume_group)

        vol_row = QHBoxLayout()
        self.volume_label = QLabel("30")
        self.volume_label.setObjectName("statusValue")
        self.volume_label.setFixedWidth(40)
        vol_row.addWidget(self.volume_label)

        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 63)
        self.volume_slider.setValue(30)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        vol_row.addWidget(self.volume_slider)

        volume_layout.addLayout(vol_row)
        layout.addWidget(volume_group)

        # ---- 接收：硬件增益 + 静噪（对标 SDR++ 右侧控制条）----
        rx_group = QGroupBox("接收")
        rx_layout = QVBoxLayout(rx_group)

        gain_row = QHBoxLayout()
        self.gain_label = QLabel("LNA")
        self.gain_label.setObjectName("statusValue")
        self.gain_label.setFixedWidth(40)
        gain_row.addWidget(self.gain_label)
        self.gain_slider = QSlider(Qt.Horizontal)
        self.gain_slider.setRange(0, 49)      # RTL-SDR 典型 LNA 增益上限 ~49dB
        self.gain_slider.setValue(0)
        self.gain_slider.valueChanged.connect(self._on_gain_changed)
        gain_row.addWidget(self.gain_slider)
        self.gain_val = QLabel("0dB")
        self.gain_val.setFixedWidth(44)
        gain_row.addWidget(self.gain_val)
        rx_layout.addLayout(gain_row)

        sq_row = QHBoxLayout()
        self.squelch_label = QLabel("静噪")
        self.squelch_label.setObjectName("statusValue")
        self.squelch_label.setFixedWidth(40)
        sq_row.addWidget(self.squelch_label)
        self.squelch_slider = QSlider(Qt.Horizontal)
        self.squelch_slider.setRange(-120, 0)
        self.squelch_slider.setValue(-80)
        self.squelch_slider.valueChanged.connect(self._on_squelch_changed)
        sq_row.addWidget(self.squelch_slider)
        self.squelch_val = QLabel("-80")
        self.squelch_val.setFixedWidth(44)
        sq_row.addWidget(self.squelch_val)
        rx_layout.addLayout(sq_row)

        layout.addWidget(rx_group)

        # ---- 录音 ----
        record_group = QGroupBox("录音")
        record_layout = QVBoxLayout(record_group)

        self.record_button = QPushButton("开始录音")
        self.record_button.setObjectName("recordButton")
        self.record_button.setCheckable(True)
        self.record_button.setFixedHeight(40)
        self.record_button.toggled.connect(self._on_record_toggled)
        record_layout.addWidget(self.record_button)

        self.record_status = QLabel("未录音")
        self.record_status.setAlignment(Qt.AlignCenter)
        record_layout.addWidget(self.record_status)

        layout.addWidget(record_group)

        # 弹性空间
        layout.addStretch()

    def _populate_presets(self, band: str):
        """填充预设电台列表。"""
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        if band == "FM 广播":
            if PRESET_FM_STATIONS:
                for freq, name in PRESET_FM_STATIONS:
                    self.preset_combo.addItem(f"{freq} MHz - {name}", (float(freq), "FM"))
            else:
                self.preset_combo.addItem("（无预设：请用“自动扫台”或手动输入频率）", None)
        elif band == "AM 广播":
            if PRESET_AM_STATIONS:
                for freq, name in PRESET_AM_STATIONS:
                    self.preset_combo.addItem(f"{freq} kHz - {name}", (float(freq) / 1000.0, "AM"))
            else:
                self.preset_combo.addItem("（无预设：请用“自动扫台”或手动输入频率）", None)
        else:
            # SDR 频段预设，按频段过滤
            band_keywords = {
                "航空": ["航空"],
                "业余 2m": ["2m", "APRS 144", "ISS"],
                "业余 70cm": ["70cm"],
                "气象卫星": ["NOAA", "METEOR"],
                "导航": ["GPS", "北斗"],
                "ISM/物联网": ["ISM", "WiFi"],
                "短波": ["80m", "40m", "20m", "15m", "10m"],
            }
            keywords = band_keywords.get(band, [])
            for freq, name, mode in SDR_BAND_PRESETS:
                if any(k in name for k in keywords):
                    self.preset_combo.addItem(f"{freq} MHz - {name} ({mode})", (float(freq), mode))
        self.preset_combo.blockSignals(False)

    @Slot(int)
    def _on_band_changed(self, index: int):
        """频段分类切换。"""
        band = self.band_combo.itemText(index)
        self._populate_presets(band)

    def _update_freq_display(self):
        """更新频率显示。"""
        if self._current_mode == "FM":
            self.freq_display.setText(f"{self._current_freq_fm:.2f} MHz")
            self.freq_input.setText(f"{self._current_freq_fm:.1f}")
        else:
            self.freq_display.setText(f"{self._current_freq_am} kHz")
            self.freq_input.setText(str(self._current_freq_am))

    # ========================================================================
    # 槽函数
    # ========================================================================

    @Slot(str)
    def _on_mode_changed(self, mode: str):
        self._current_mode = mode
        self.mode_changed.emit(mode)
        self._populate_presets(mode)
        self._update_freq_display()

    @Slot()
    def _on_freq_input(self):
        text = self.freq_input.text().strip()
        try:
            if self._current_mode == "FM":
                freq = float(text)
                if 64 <= freq <= 108:
                    self._current_freq_fm = freq
                    self.tune_fm_requested.emit(freq)
            else:
                freq = int(float(text))
                if 531 <= freq <= 1710:
                    self._current_freq_am = freq
                    self.tune_am_requested.emit(freq)
            self._update_freq_display()
        except ValueError:
            pass

    @Slot(float)
    def _step_freq(self, step: float):
        if self._current_mode == "FM":
            new_freq = max(64.0, min(108.0, self._current_freq_fm + step))
            self._current_freq_fm = new_freq
            self.tune_fm_requested.emit(new_freq)
        else:
            step_khz = int(step * 100) if abs(step) >= 1 else int(step * 10)
            new_freq = max(531, min(1710, self._current_freq_am + step_khz))
            self._current_freq_am = new_freq
            self.tune_am_requested.emit(new_freq)
        self._update_freq_display()

    @Slot(int)
    def _on_preset_selected(self, index: int):
        if index < 0:
            return
        data = self.preset_combo.itemData(index)
        if data is None:
            return
        # 新格式: (freq_mhz, mode)
        if isinstance(data, tuple):
            freq_mhz, mode = data
            self._current_freq_fm = freq_mhz
            self.freq_input.setText(f"{freq_mhz:.3f}")
            self.tune_fm_requested.emit(freq_mhz)
            # 发射 SDR 调谐请求（包含模式）
            if hasattr(self, 'tune_sdr_requested'):
                self.tune_sdr_requested.emit(freq_mhz * 1e6, mode)
        else:
            # 旧格式兼容
            if self._current_mode == "FM":
                freq = float(data)
                self._current_freq_fm = freq
                self.tune_fm_requested.emit(freq)
            else:
                freq = int(data)
                self._current_freq_am = freq
                self.tune_am_requested.emit(freq)
        self._update_freq_display()

    @Slot(int)
    def _on_volume_changed(self, value: int):
        self._volume = value
        self.volume_label.setText(str(value))
        self.volume_changed.emit(value)

    def _on_gain_changed(self, db: int):
        self.gain_val.setText(f"{db}dB")
        self.gain_changed.emit(db)

    def _on_squelch_changed(self, dbfs: int):
        self.squelch_val.setText(str(dbfs))
        self.squelch_changed.emit(float(dbfs))

    @Slot(bool)
    def _on_record_toggled(self, checked: bool):
        self._recording = checked
        if checked:
            self.record_button.setText("停止录音")
            self.record_status.setText("录音中... 00:00")
            self._record_seconds = 0
        else:
            self.record_button.setText("开始录音")
            self.record_status.setText(f"录音完成 ({self._record_seconds} 秒)")
        self.record_toggled.emit(checked)

    # ========================================================================
    # 外部接口
    # ========================================================================

    def set_freq_fm(self, freq: float):
        """从外部设置 FM 频率（如频谱组件双击）。"""
        self._current_mode = "FM"
        self.mode_combo.setCurrentText("FM")
        self._current_freq_fm = freq
        self._update_freq_display()

    def set_freq_am(self, freq: int):
        """从外部设置 AM 频率。"""
        self._current_mode = "AM"
        self.mode_combo.setCurrentText("AM")
        self._current_freq_am = freq
        self._update_freq_display()

    def update_record_time(self, seconds: int):
        """更新录音时长显示。"""
        self._record_seconds = seconds
        if self._recording:
            m, s = divmod(seconds, 60)
            self.record_status.setText(f"录音中... {m:02d}:{s:02d}")

    def set_volume(self, volume: int):
        """从外部设置音量。"""
        self.volume_slider.setValue(volume)
