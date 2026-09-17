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
PRESET_FM_STATIONS = [
    ("87.6", "北京文艺"),
    ("88.7", "Hit FM"),
    ("90.0", "中央音乐"),
    ("91.5", "轻松调频"),
    ("95.5", "北京交通"),
    ("97.4", "北京音乐"),
    ("98.5", "音乐之声"),
    ("101.8", "都市之声"),
    ("103.9", "北京交通"),
    ("106.1", "中国之声"),
]

# 预设 AM 电台（kHz）
PRESET_AM_STATIONS = [
    ("540", "中央人民"),
    ("639", "中国之声"),
    ("720", "乡村"),
    ("828", "新闻"),
    ("900", "经济"),
    ("1008", "文艺"),
    ("1134", "交通"),
    ("1251", "生活"),
]


class ControlPanel(QWidget):
    """调谐控制面板。"""

    # 信号
    tune_fm_requested = Signal(float)    # 请求调谐 FM (MHz)
    tune_am_requested = Signal(int)      # 请求调谐 AM (kHz)
    volume_changed = Signal(int)          # 音量改变 (0-63)
    record_toggled = Signal(bool)         # 录音开关
    mode_changed = Signal(str)            # 模式改变 ("FM"/"AM")

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
        step_row = QHBoxLayout()
        for step, label in [(-1.0, "-1.0"), (-0.1, "-0.1"), (0.1, "+0.1"), (1.0, "+1.0")]:
            btn = QPushButton(label)
            btn.setFixedHeight(28)
            btn.clicked.connect(lambda checked, s=step: self._step_freq(s))
            step_row.addWidget(btn)
        freq_layout.addLayout(step_row)

        layout.addWidget(freq_group)

        # ---- 预设电台 ----
        preset_group = QGroupBox("预设电台")
        preset_layout = QVBoxLayout(preset_group)

        self.preset_combo = QComboBox()
        self.preset_combo.setFixedHeight(32)
        self._populate_presets("FM")
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

    def _populate_presets(self, mode: str):
        """填充预设电台列表。"""
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        if mode == "FM":
            for freq, name in PRESET_FM_STATIONS:
                self.preset_combo.addItem(f"{freq} MHz - {name}", freq)
        else:
            for freq, name in PRESET_AM_STATIONS:
                self.preset_combo.addItem(f"{freq} kHz - {name}", freq)
        self.preset_combo.blockSignals(False)

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
        freq_str = self.preset_combo.itemData(index)
        if freq_str is None:
            return
        if self._current_mode == "FM":
            freq = float(freq_str)
            self._current_freq_fm = freq
            self.tune_fm_requested.emit(freq)
        else:
            freq = int(freq_str)
            self._current_freq_am = freq
            self.tune_am_requested.emit(freq)
        self._update_freq_display()

    @Slot(int)
    def _on_volume_changed(self, value: int):
        self._volume = value
        self.volume_label.setText(str(value))
        self.volume_changed.emit(value)

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
