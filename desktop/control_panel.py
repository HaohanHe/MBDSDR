"""
MBDSDR 调谐控制面板
====================
频率显示/调节、模式选择、音量、预设电台、录音控制。

控件布局参考：
- SDR++ rtl_sdr_source/main.cpp（设备配置菜单：PPM 整数输入、Offset Tuning /
  RTL AGC / Tuner AGC 垂直复选框列表、Gain 滑杆）。
- GQRX dockrxopt/dockinputctl（带宽档位下拉、AGC 预设、PPM 频偏校正）。
"""

from PySide6.QtCore import Qt, Signal, Slot, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSlider, QComboBox, QLineEdit, QFrame, QGroupBox, QSizePolicy,
    QCheckBox, QSpinBox, QProgressBar, QInputDialog,
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

# 模式 → 默认 VFO 带宽（Hz）。对标 GQRX/SDR++ 各解调方式的典型中频带宽。
MODE_VFO_BANDWIDTH = {
    "FM": 12_000,    # NBFM 语音
    "WFM": 180_000,  # 广播调频立体声
    "AM": 6_000,
    "USB": 3_000,
    "LSB": 3_000,
    "CW": 500,
}

# 合法频率范围：10 kHz ~ 6 GHz（SDR 全频段，不再硬编码 FM/AM 广播段）
FREQ_MIN_HZ = 10_000.0
FREQ_MAX_HZ = 6.0e9


class ControlPanel(QWidget):
    """调谐控制面板。"""

    # ===== 既有信号（保持不变，仅新增不删除）=====
    tune_fm_requested = Signal(float)    # 请求调谐 FM (MHz)
    tune_am_requested = Signal(int)      # 请求调谐 AM (kHz)
    tune_sdr_requested = Signal(float, str)  # 请求调谐 SDR (Hz, 模式)
    volume_changed = Signal(int)          # 音量改变 (0-63)
    record_toggled = Signal(bool)         # 录音开关
    mode_changed = Signal(str)            # 模式改变
    gain_changed = Signal(int)             # 硬件增益 (dB, RTL-SDR LNA 0~49)
    squelch_changed = Signal(float)        # 静噪门限 (dBFS, -120~0)
    sample_rate_changed = Signal(float)    # 采样率改变 (Hz)

    # ===== 新增信号 =====
    step_changed = Signal(float)               # 频率步进改变 (Hz)
    vfo_bandwidth_changed = Signal(float)      # VFO 带宽改变 (Hz)
    agc_changed = Signal(bool)                 # AGC 自动增益开关
    ppm_changed = Signal(int)                  # PPM 频偏校正
    offset_tuning_changed = Signal(bool)       # Offset 调谐开关
    bookmark_added = Signal(float, str, str)   # 收藏新增 (freq_hz, name, mode)，供主窗口持久化

    # 采样率档位：与 RTLSDRBackend.SAMPLE_RATES 对齐（11 档离散表）。
    # 动态导入失败时回退到硬编码表，保证 GUI 不崩。
    try:
        from mbdsdr_ai.sdr_backend import RTLSDRBackend
        SAMPLE_RATES = list(RTLSDRBackend.SAMPLE_RATES)
    except Exception:
        SAMPLE_RATES = [
            250_000, 1_024_000, 1_536_000, 1_792_000, 1_920_000,
            2_048_000, 2_160_000, 2_400_000, 2_560_000, 2_880_000, 3_200_000,
        ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_mode = "FM"
        # 统一以 Hz 为内部频率真值（兼容 SDR 全频段），默认 98.5 MHz
        self._freq_hz = 98.5e6
        self._step_hz = 10_000.0   # 默认步进 10 kHz
        self._volume = 30
        self._recording = False
        self._record_seconds = 0
        # 收藏列表：内存中，初始为空，不预存任何地区性电台
        self._bookmarks: list[tuple[float, str, str]] = []

        self._build_ui()
        self._update_freq_display()
        # 默认未连接 SDR：调谐/音量/模式/录音控件全部置灰，绝不暴露假可控状态
        self.set_sdr_connected(False)

    def set_sdr_connected(self, connected: bool):
        """无真实 SDR 时禁用调谐/音量/模式/录音等操作控件。

        连接成功后由 main_window._panels_set_sdr_connected(True) 统一启用；
        断开时再置灰。频率只读显示(freq_display)保持可用展示，不置灰。
        收藏查看/删除始终可用，仅“收藏当前频率”按钮随连接状态禁用。
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
            getattr(self, "sample_rate_combo", None),
            # 新增控件
            getattr(self, "step_combo", None),
            getattr(self, "vfo_bw_combo", None),
            getattr(self, "agc_check", None),
            getattr(self, "ppm_spin", None),
            getattr(self, "offset_check", None),
            getattr(self, "bookmark_add_btn", None),
            # 增益 / 静噪滑杆：无设备时禁用，不暴露假可控状态
            getattr(self, "gain_slider", None),
            getattr(self, "squelch_slider", None),
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
        self.freq_display = QLabel("98.500 MHz")
        self.freq_display.setObjectName("freqDisplay")
        self.freq_display.setAlignment(Qt.AlignCenter)
        self.freq_display.setMinimumHeight(60)
        freq_layout.addWidget(self.freq_display)

        # 模式选择 + 频率输入
        mode_freq_row = QHBoxLayout()

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["FM", "WFM", "AM", "USB", "LSB", "CW"])
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

        # 频率步进选择 + ±1 步按钮
        step_row = QHBoxLayout()
        step_row.addWidget(QLabel("步进:"))
        self.step_combo = QComboBox()
        for txt, hz in [("10Hz", 10.0), ("100Hz", 100.0), ("1kHz", 1_000.0),
                        ("10kHz", 10_000.0), ("100kHz", 100_000.0), ("1MHz", 1_000_000.0)]:
            self.step_combo.addItem(txt, hz)
        # 默认选中 10kHz
        _def_step = self.step_combo.findData(10_000.0)
        self.step_combo.setCurrentIndex(_def_step if _def_step >= 0 else 3)
        self.step_combo.currentIndexChanged.connect(self._on_step_combo_changed)
        step_row.addWidget(self.step_combo, 1)

        self._step_buttons = []
        for text, direction in [("-", -1), ("+", +1)]:
            btn = QPushButton(text)
            btn.setFixedHeight(28)
            btn.setFixedWidth(44)
            btn.clicked.connect(lambda checked, d=direction: self._apply_step(d))
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

        # ---- 收藏（书签）----
        bookmark_group = QGroupBox("收藏")
        bookmark_layout = QVBoxLayout(bookmark_group)

        self.bookmark_combo = QComboBox()
        self.bookmark_combo.setFixedHeight(28)
        self._refresh_bookmarks()
        self.bookmark_combo.currentIndexChanged.connect(self._on_bookmark_selected)
        bookmark_layout.addWidget(self.bookmark_combo)

        bm_btn_row = QHBoxLayout()
        self.bookmark_add_btn = QPushButton("收藏当前频率")
        self.bookmark_add_btn.setFixedHeight(28)
        self.bookmark_add_btn.clicked.connect(self._on_bookmark_add)
        bm_btn_row.addWidget(self.bookmark_add_btn)

        self.bookmark_del_btn = QPushButton("删除选中")
        self.bookmark_del_btn.setFixedHeight(28)
        self.bookmark_del_btn.clicked.connect(self._on_bookmark_del)
        bm_btn_row.addWidget(self.bookmark_del_btn)
        bookmark_layout.addLayout(bm_btn_row)

        layout.addWidget(bookmark_group)

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

        # ---- 接收：S-meter + 采样率 + 带宽 + 增益 + AGC + PPM + Offset + 静噪 ----
        rx_group = QGroupBox("接收")
        rx_layout = QVBoxLayout(rx_group)

        # 信号电平表（S-meter）：真实数据驱动，由 update_signal_level() 喂入
        smeter_row = QHBoxLayout()
        self.smeter_label = QLabel("--")
        self.smeter_label.setObjectName("statusValue")
        self.smeter_label.setFixedWidth(70)
        smeter_row.addWidget(self.smeter_label)
        self.smeter_bar = QProgressBar()
        self.smeter_bar.setRange(-120, 0)
        self.smeter_bar.setValue(-120)
        self.smeter_bar.setTextVisible(False)
        smeter_row.addWidget(self.smeter_bar, 1)
        rx_layout.addLayout(smeter_row)

        # 采样率下拉框（11 档离散表，默认 2.048 MHz = 索引 5）
        sr_row = QHBoxLayout()
        self.sr_label = QLabel("采样率")
        self.sr_label.setObjectName("statusValue")
        self.sr_label.setFixedWidth(40)
        sr_row.addWidget(self.sr_label)
        self.sample_rate_combo = QComboBox()
        for _rate in self.SAMPLE_RATES:
            if _rate >= 1_000_000:
                _txt = f"{_rate / 1e6:.3f} MHz"
            else:
                _txt = f"{_rate / 1e3:.0f} kHz"
            self.sample_rate_combo.addItem(_txt, float(_rate))
        # 默认选中 2.048 MHz（索引 5）
        try:
            _default_idx = self.SAMPLE_RATES.index(2_048_000)
        except ValueError:
            _default_idx = 0
        self.sample_rate_combo.setCurrentIndex(_default_idx)
        self.sample_rate_combo.currentIndexChanged.connect(self._on_sample_rate_changed)
        sr_row.addWidget(self.sample_rate_combo)
        rx_layout.addLayout(sr_row)

        # VFO 带宽选择（采样率下方一行）
        bw_row = QHBoxLayout()
        self.vfo_bw_label = QLabel("带宽")
        self.vfo_bw_label.setObjectName("statusValue")
        self.vfo_bw_label.setFixedWidth(40)
        bw_row.addWidget(self.vfo_bw_label)
        self.vfo_bw_combo = QComboBox()
        for txt, hz in [("500Hz (CW)", 500.0), ("3kHz (SSB)", 3_000.0),
                        ("6kHz (AM)", 6_000.0), ("12kHz (NBFM)", 12_000.0),
                        ("180kHz (WFM)", 180_000.0)]:
            self.vfo_bw_combo.addItem(txt, hz)
        # FM 默认 12kHz
        _def_bw = self.vfo_bw_combo.findData(MODE_VFO_BANDWIDTH["FM"])
        self.vfo_bw_combo.setCurrentIndex(_def_bw if _def_bw >= 0 else 3)
        self.vfo_bw_combo.currentIndexChanged.connect(self._on_vfo_bw_changed)
        bw_row.addWidget(self.vfo_bw_combo, 1)
        rx_layout.addLayout(bw_row)

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

        # AGC 自动增益开关（增益滑杆下方）
        self.agc_check = QCheckBox("AGC 自动增益")
        self.agc_check.toggled.connect(self._on_agc_changed)
        rx_layout.addWidget(self.agc_check)

        # PPM 频偏校正（AGC 下方）
        ppm_row = QHBoxLayout()
        self.ppm_label = QLabel("PPM")
        self.ppm_label.setObjectName("statusValue")
        self.ppm_label.setFixedWidth(40)
        ppm_row.addWidget(self.ppm_label)
        self.ppm_spin = QSpinBox()
        self.ppm_spin.setRange(-1000, 1000)
        self.ppm_spin.setSingleStep(1)
        self.ppm_spin.setValue(0)
        self.ppm_spin.valueChanged.connect(self._on_ppm_changed)
        ppm_row.addWidget(self.ppm_spin)
        ppm_row.addStretch(1)
        rx_layout.addLayout(ppm_row)

        # Offset 调谐开关（PPM 下方）
        self.offset_check = QCheckBox("Offset 调谐")
        self.offset_check.toggled.connect(self._on_offset_tuning_changed)
        rx_layout.addWidget(self.offset_check)

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

    # ========================================================================
    # 频率解析 / 统一频率真值
    # ========================================================================

    def _parse_freq_text(self, text: str) -> float:
        """把用户输入解析为 Hz。

        支持：
        - 带 M/MHz/k/kHz/G 后缀，如 "98.5M"=98.5MHz, "144500k"=144.5MHz
        - 纯数字：FM/WFM 按 MHz，AM 按 kHz；绝对值 >10000 视为 Hz（如 144500000）
        解析失败抛 ValueError。
        """
        t = text.strip().replace(",", "").replace(" ", "")
        if not t:
            raise ValueError("empty")
        low = t.lower()
        suffix = None
        for suf in ("mhz", "khz", "hz", "ghz", "g", "m", "k"):
            if low.endswith(suf):
                suffix = suf
                num = t[: -len(suf)]
                break
        else:
            num = t
        if num in ("", "-", "+", "."):
            raise ValueError("no number")
        val = float(num)
        if suffix is None:
            if abs(val) > 10000:
                suffix = "hz"
            elif self._current_mode in ("FM", "WFM"):
                suffix = "m"
            else:
                suffix = "k"
        if suffix in ("m", "mhz"):
            return val * 1e6
        if suffix in ("g", "ghz"):
            return val * 1e9
        if suffix in ("k", "khz"):
            return val * 1e3
        return val  # hz

    def _set_freq_hz(self, freq_hz: float):
        """统一设置频率真值并更新显示、发射调谐信号。"""
        freq_hz = max(FREQ_MIN_HZ, min(FREQ_MAX_HZ, float(freq_hz)))
        self._freq_hz = freq_hz
        self._update_freq_display()
        self._emit_tune(freq_hz)

    def _emit_tune(self, freq_hz: float):
        mode = self._current_mode
        # 兼容旧信号
        if mode in ("FM", "WFM"):
            self.tune_fm_requested.emit(freq_hz / 1e6)
        elif mode == "AM":
            self.tune_am_requested.emit(int(round(freq_hz / 1e3)))
        # SDR 主路径（Hz, 模式）
        self.tune_sdr_requested.emit(freq_hz, mode)

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
        """更新频率显示（按数量级自适应 MHz/kHz/Hz）。"""
        hz = self._freq_hz
        if hz >= 1e6:
            self.freq_display.setText(f"{hz / 1e6:.3f} MHz")
            self.freq_input.setText(f"{hz / 1e6:g}")
        elif hz >= 1e3:
            self.freq_display.setText(f"{hz / 1e3:.1f} kHz")
            self.freq_input.setText(f"{hz / 1e3:g}")
        else:
            self.freq_display.setText(f"{hz:.0f} Hz")
            self.freq_input.setText(f"{hz:g}")

    # ========================================================================
    # 槽函数
    # ========================================================================

    @Slot(str)
    def _on_mode_changed(self, mode: str):
        self._current_mode = mode
        self.mode_changed.emit(mode)
        self._populate_presets(mode)
        self._update_freq_display()
        # 切换模式时自动套用该模式的典型 VFO 带宽，并 emit vfo_bandwidth_changed
        bw = MODE_VFO_BANDWIDTH.get(mode, MODE_VFO_BANDWIDTH["FM"])
        self.vfo_bw_combo.blockSignals(True)
        idx = self.vfo_bw_combo.findData(float(bw))
        if idx >= 0:
            self.vfo_bw_combo.setCurrentIndex(idx)
        self.vfo_bw_combo.blockSignals(False)
        self.vfo_bandwidth_changed.emit(float(bw))

    @Slot()
    def _on_freq_input(self):
        text = self.freq_input.text()
        try:
            freq_hz = self._parse_freq_text(text)
        except (ValueError, OSError):
            # 解析失败：输入框变红，稍后恢复原值，绝不崩溃
            self.freq_input.setStyleSheet("background-color:#d98a8a;")
            QTimer.singleShot(700, self._restore_freq_input)
            return
        self.freq_input.setStyleSheet("")
        self._set_freq_hz(freq_hz)

    def _restore_freq_input(self):
        self.freq_input.setStyleSheet("")
        self._update_freq_display()

    @Slot(int)
    def _on_step_combo_changed(self, index: int):
        hz = float(self.step_combo.itemData(index) or 10_000.0)
        self._step_hz = hz
        self.step_changed.emit(hz)

    def _apply_step(self, direction: int):
        """按当前选中的步进 ±1 步。"""
        self._set_freq_hz(self._freq_hz + direction * self._step_hz)

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
            self._current_mode = mode
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentText(mode)
            self.mode_combo.blockSignals(False)
            self._set_freq_hz(freq_mhz * 1e6)
        else:
            # 旧格式兼容
            if self._current_mode in ("FM", "WFM"):
                self._set_freq_hz(float(data) * 1e6)
            else:
                self._set_freq_hz(float(data) * 1e3)

    @Slot(int)
    def _on_volume_changed(self, value: int):
        self._volume = value
        self.volume_label.setText(str(value))
        self.volume_changed.emit(value)

    def _on_gain_changed(self, db: int):
        self.gain_val.setText(f"{db}dB")
        self.gain_changed.emit(db)

    @Slot(int)
    def _on_sample_rate_changed(self, index: int):
        """采样率档位切换 → emit sample_rate_changed(rate_hz)。"""
        rate = float(self.sample_rate_combo.itemData(index) or 0.0)
        if rate > 0:
            self.sample_rate_changed.emit(rate)

    @Slot(int)
    def _on_vfo_bw_changed(self, index: int):
        """VFO 带宽档位切换 → emit vfo_bandwidth_changed(bw_hz)。"""
        bw = float(self.vfo_bw_combo.itemData(index) or 0.0)
        if bw > 0:
            self.vfo_bandwidth_changed.emit(bw)

    def _on_squelch_changed(self, dbfs: int):
        self.squelch_val.setText(str(dbfs))
        self.squelch_changed.emit(float(dbfs))

    @Slot(bool)
    def _on_agc_changed(self, checked: bool):
        self.agc_changed.emit(checked)

    @Slot(int)
    def _on_ppm_changed(self, ppm: int):
        self.ppm_changed.emit(int(ppm))

    @Slot(bool)
    def _on_offset_tuning_changed(self, checked: bool):
        self.offset_tuning_changed.emit(checked)

    # ---- 收藏 ----
    def _refresh_bookmarks(self):
        self.bookmark_combo.blockSignals(True)
        self.bookmark_combo.clear()
        if not self._bookmarks:
            self.bookmark_combo.addItem("（暂无收藏）", None)
        else:
            for i, (hz, name, mode) in enumerate(self._bookmarks):
                self.bookmark_combo.addItem(
                    f"{name} ({hz / 1e6:.3f}MHz {mode})", i)
        self.bookmark_combo.blockSignals(False)

    @Slot()
    def _on_bookmark_add(self):
        default_name = f"{self._freq_hz / 1e6:.3f}MHz {self._current_mode}"
        name, ok = QInputDialog.getText(
            self, "收藏频率", "名称:", QLineEdit.Normal, default_name)
        if not ok:
            return
        name = name.strip() or default_name
        self._bookmarks.append((self._freq_hz, name, self._current_mode))
        self._refresh_bookmarks()
        self.bookmark_combo.setCurrentIndex(self.bookmark_combo.count() - 1)
        self.bookmark_added.emit(self._freq_hz, name, self._current_mode)

    @Slot()
    def _on_bookmark_del(self):
        idx = self.bookmark_combo.currentData()
        if idx is None or not isinstance(idx, int):
            return
        if 0 <= idx < len(self._bookmarks):
            del self._bookmarks[idx]
            self._refresh_bookmarks()

    @Slot(int)
    def _on_bookmark_selected(self, index: int):
        idx = self.bookmark_combo.itemData(index)
        if idx is None or not isinstance(idx, int):
            return
        if not (0 <= idx < len(self._bookmarks)):
            return
        hz, _name, mode = self._bookmarks[idx]
        self._current_mode = mode
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentText(mode)
        self.mode_combo.blockSignals(False)
        self._set_freq_hz(hz)

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

    def get_frequency_hz(self) -> float:
        """返回当前内部频率真值 (Hz)。供主窗口连接后下发初始中心频率。"""
        return float(self._freq_hz)

    def set_freq_fm(self, freq: float):
        """从外部设置 FM 频率（如频谱组件双击）。"""
        self._current_mode = "FM"
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentText("FM")
        self.mode_combo.blockSignals(False)
        self._freq_hz = freq * 1e6
        self._update_freq_display()

    def set_freq_am(self, freq: int):
        """从外部设置 AM 频率。"""
        self._current_mode = "AM"
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentText("AM")
        self.mode_combo.blockSignals(False)
        self._freq_hz = freq * 1e3
        self._update_freq_display()

    def update_signal_level(self, dbfs):
        """由主窗口从 IQ 功率估计后调用，更新 S-meter。真实数据驱动，绝不造假。

        传入 None 或不可解析值时显示 "--" 并把进度条归零（空）。
        """
        if dbfs is None:
            self.smeter_label.setText("--")
            self.smeter_bar.setValue(self.smeter_bar.minimum())
            return
        try:
            v = float(dbfs)
        except (TypeError, ValueError):
            self.smeter_label.setText("--")
            self.smeter_bar.setValue(self.smeter_bar.minimum())
            return
        v = max(-120.0, min(0.0, v))
        self.smeter_bar.setValue(int(v))
        self.smeter_label.setText(f"{v:.0f} dBFS")

    def update_record_time(self, seconds: int):
        """更新录音时长显示。"""
        self._record_seconds = seconds
        if self._recording:
            m, s = divmod(seconds, 60)
            self.record_status.setText(f"录音中... {m:02d}:{s:02d}")

    def set_volume(self, volume: int):
        """从外部设置音量。"""
        self.volume_slider.setValue(volume)

    # ========================================================================
    # 启动恢复用 setter（持久化参数回填）
    # ------------------------------------------------------------------------
    # 这些 setter 只在程序启动、从 desktop_settings.json 读回上次参数时调用。
    # 一律 blockSignals：只更新控件显示与内部真值，不向 main_window 发射变更信号，
    # 从而不触发后端下发 / 不重复写配置。用户手动操作仍走原有 _on_* 信号路径。
    # ========================================================================

    def set_frequency_hz(self, freq_hz: float):
        """从外部恢复中心频率 (Hz)，不发射调谐信号。"""
        freq_hz = max(FREQ_MIN_HZ, min(FREQ_MAX_HZ, float(freq_hz)))
        self._freq_hz = freq_hz
        self._update_freq_display()

    def set_mode(self, mode: str):
        """从外部恢复解调模式，不发射 mode_changed / vfo_bandwidth_changed。"""
        mode = (mode or "FM").upper()
        self._current_mode = mode
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentText(mode)
        self.mode_combo.blockSignals(False)
        self._populate_presets(mode)
        self._update_freq_display()
        # 同步带宽下拉到该模式典型带宽（blockSignals，不 emit）
        bw = MODE_VFO_BANDWIDTH.get(mode, MODE_VFO_BANDWIDTH["FM"])
        self.vfo_bw_combo.blockSignals(True)
        idx = self.vfo_bw_combo.findData(float(bw))
        if idx >= 0:
            self.vfo_bw_combo.setCurrentIndex(idx)
        self.vfo_bw_combo.blockSignals(False)

    def set_gain(self, db: float):
        """从外部恢复 LNA 增益 (dB)，不发射 gain_changed。"""
        db = int(max(0, min(49, float(db))))
        self.gain_slider.blockSignals(True)
        self.gain_slider.setValue(db)
        self.gain_slider.blockSignals(False)
        self.gain_val.setText(f"{db}dB")

    def set_sample_rate(self, rate_hz: float):
        """从外部恢复采样率档位；无精确匹配时选最近档，不发射 sample_rate_changed。"""
        rate = float(rate_hz)
        idx = self.sample_rate_combo.findData(rate)
        if idx < 0 and self.sample_rate_combo.count():
            best, best_diff = 0, None
            for i in range(self.sample_rate_combo.count()):
                d = abs(float(self.sample_rate_combo.itemData(i)) - rate)
                if best_diff is None or d < best_diff:
                    best_diff, best = d, i
            idx = best
        self.sample_rate_combo.blockSignals(True)
        self.sample_rate_combo.setCurrentIndex(idx)
        self.sample_rate_combo.blockSignals(False)

    def set_vfo_bandwidth(self, bw_hz: float):
        """从外部恢复 VFO 带宽；无精确匹配选最近档，不发射 vfo_bandwidth_changed。"""
        bw = float(bw_hz)
        idx = self.vfo_bw_combo.findData(bw)
        if idx < 0 and self.vfo_bw_combo.count():
            best, best_diff = 3, None
            for i in range(self.vfo_bw_combo.count()):
                d = abs(float(self.vfo_bw_combo.itemData(i)) - bw)
                if best_diff is None or d < best_diff:
                    best_diff, best = d, i
            idx = best
        self.vfo_bw_combo.blockSignals(True)
        self.vfo_bw_combo.setCurrentIndex(idx)
        self.vfo_bw_combo.blockSignals(False)

    def set_agc(self, enabled: bool):
        """从外部恢复 AGC 开关，不发射 agc_changed。"""
        self.agc_check.blockSignals(True)
        self.agc_check.setChecked(bool(enabled))
        self.agc_check.blockSignals(False)
