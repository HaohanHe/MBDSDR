"""
MBDSDR 桌面端主窗口
====================
整合频谱显示、控制面板、状态面板、AI 对话面板。
支持主题切换、MCP 连接管理、OpenGL/软件渲染自动选择。
"""

import os
import sys
import math
import tempfile
from datetime import datetime
from typing import Optional

import numpy as np

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QAction, QKeySequence, QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTabWidget, QStatusBar, QToolBar, QMenuBar, QMenu, QLabel,
    QFileDialog, QMessageBox, QInputDialog, QComboBox, QPushButton,
    QFrame, QSizePolicy, QDialog, QDialogButtonBox
)

# 确保能导入同目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 确保能导入仓库根下的 mbdsdr_ai 包（baseband_io / audio_out / sdr_backend）
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from themes import get_theme, THEMES, DEFAULT_THEME
from spectrum_widget import create_spectrum_widget, HAS_OPENGL
from control_panel import ControlPanel
from status_panel import StatusPanel
from ai_panel import AIPanel
from mcp_worker import MCPWorkerManager
from rf_sky_view import RFSkyView, SkyObject, AntennaPointing, HeatmapCell, SatelliteTracker
from module_panel import ModulePanel
# 气象云图 / 多普勒定轨面板（懒加载：构造时只建 Qt 控件，后端 ToolRegistry
# 在用户首次点解码/定轨时才创建，避免拖慢启动）。
from weather_panel import WeatherPanel
from doppler_panel import DopplerPanel

# 真实 baseband 存盘 + 声卡实时输出（可选依赖，导入失败也不拖垮 GUI）
try:
    from mbdsdr_ai.baseband_io import save_iq as _save_iq
except Exception:  # pragma: no cover
    _save_iq = None
try:
    from mbdsdr_ai.audio_out import AudioPlayer
except Exception:  # pragma: no cover
    AudioPlayer = None  # type: ignore
# 真实串口 GNSS（NMEA）：插上模块后 auto_detect 定位；缺 pyserial 等依赖时降级为 none
try:
    from mbdsdr_ai.gnss_monitor import RealGNSSMonitor
except Exception:  # pragma: no cover
    RealGNSSMonitor = None  # type: ignore


class MainWindow(QMainWindow):
    """MBDSDR 主窗口。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("MBDSDR - AI 定义无线电")
        # 应用图标（直接实例化主窗口时也能显示标题栏图标；打包后由 main.py 统一设置）
        _icon_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "assets", "icon.png"
        )
        if os.path.exists(_icon_path):
            from PySide6.QtGui import QIcon
            self.setWindowIcon(QIcon(_icon_path))
        self.setMinimumSize(1200, 800)
        self.resize(1400, 900)

        # 状态
        self._current_theme = DEFAULT_THEME
        self._worker_manager = MCPWorkerManager()
        self._worker: Optional[object] = None
        self._record_timer: Optional[QTimer] = None
        self._record_seconds = 0
        self._sky_update_timer: Optional[QTimer] = None
        # 观测站坐标：默认 None 表示“未配置观测站位置”（不再硬编码长春坐标）。
        # 由 GNSS 真实定位或 gui_config.json 手动配置后填充；None 时天空图不计算卫星。
        self._observer_lat: Optional[float] = None
        self._observer_lon: Optional[float] = None
        self.sat_tracker: Optional[SatelliteTracker] = None
        # 真实串口 GNSS 监测：插上 GNSS 模块后 auto_detect 定位，驱动状态面板 GPS 组
        # 与天空图观测站坐标。找不到设备/缺依赖时降级为 none，绝不崩、绝不造假坐标。
        self._gnss = RealGNSSMonitor() if RealGNSSMonitor is not None else None
        self._gnss_timer: Optional[QTimer] = None
        # 地面站坐标是否由真实 GNSS 自动设定（True 时才允许 GNSS 写入；
        # 用户在 gui_config.json 手动配置的坐标优先，不被 GNSS 覆盖）。
        self._observer_from_gnss: bool = False
        # 真实 SDR 后端（SoapySDR/RTL-SDR/HackRF 等）。由设备选择对话框真实 connect 后填充；
        # 与 self._worker（ai-sdr Mini WebSocket）互斥。硬件失败绝不静默切 mock。
        self._active_sdr_backend: Optional[object] = None
        # NTRIP 配置对话框（非模态）。首次从菜单打开时才创建；保持引用防 GC。
        self._ntrip_dialog: Optional[object] = None

        # ---- 真实数据链路状态（A 频谱 / B baseband 录制 / C 声卡输出）----
        # 统一的 IQ 轮询定时器（50ms / 20fps）：一次 read_samples 同时喂给
        # 频谱、录制缓冲、FM 解调声卡输出，避免多个定时器抢读同一个流。
        self._iq_poll_timer: Optional[QTimer] = None
        # baseband 录制状态
        self._recording: bool = False
        self._record_file_path: Optional[str] = None
        self._record_iq_buffer: list = []
        self._record_sr: float = 2_400_000.0
        self._record_center_hz: float = 0.0
        self._record_guard: bool = False  # 同步两个录音按钮时防重入
        # sidecar 元数据（开始录制时从真实后端捕获，停止落盘时写入 .json）
        self._record_start_time: str = ""
        self._record_gain_db: float = 0.0
        self._record_device: str = ""
        self._record_driver: str = ""
        # 声卡实时输出（sounddevice 可选；无设备时 available=False 安全降级）
        self._audio_player = None
        if AudioPlayer is not None:
            try:
                self._audio_player = AudioPlayer(sample_rate=48000, channels=1, gain=0.5)
            except Exception:
                self._audio_player = None
        # 静噪门限（dBFS / dBm 估计）：信号低于此值时声卡静音不输出
        self._squelch_db: float = -80.0
        # VFO（SDR++ 风格接收链：变频→重采样→低通）。懒加载：连接后根据后端采样率创建。
        # VFO 失败/scipy 不可用时回退到直接鉴频，绝不崩。
        self._vfo = None  # type: Optional[object]
        self._vfo_in_sr: float = 0.0
        self._vfo_out_sr: float = 48000.0
        self._vfo_bw: float = 12000.0

        # 构建 UI
        self._build_menu_bar()
        self._build_tool_bar()
        self._build_central_widget()
        self._build_status_bar()

        # 启动即无硬件：所有 SDR 操作控件（调谐/音量/模式/录音/面板实时按钮）置灰
        self._panels_set_sdr_connected(False)

        # 应用默认主题
        self._apply_theme(DEFAULT_THEME)

        # 加载 GUI 配置（窗口大小、频率、主题等）
        QTimer.singleShot(100, self._load_gui_config)

        # 天空图实时更新定时器（每 5 秒刷新卫星位置 + 授时）
        self._sky_update_timer = QTimer(self)
        self._sky_update_timer.setInterval(5000)
        self._sky_update_timer.timeout.connect(self._update_sky_satellites)
        self._sky_update_timer.start()
        # 立即先刷一次
        QTimer.singleShot(1200, self._update_sky_satellites)

        # (A) 真实 IQ 轮询定时器：连接 SDR 后才 start；无 SDR 时保持"未连接"。
        # 一次 tick 读一块 IQ，分发给频谱 / baseband 录制 / FM 解调声卡。
        self._iq_poll_timer = QTimer(self)
        self._iq_poll_timer.setInterval(50)  # 20 fps
        self._iq_poll_timer.timeout.connect(self._poll_sdr_iq)

        # (D) 真实串口 GNSS 轮询定时器：每 1s 读一次 NMEA fix。
        # UI 初始化完成后再延迟 auto_detect 启动，避免阻塞首屏；找不到设备也不崩。
        self._gnss_timer = QTimer(self)
        self._gnss_timer.setInterval(1000)
        self._gnss_timer.timeout.connect(self._poll_gnss)
        QTimer.singleShot(800, self._start_real_gnss)

    # ========================================================================
    # UI 构建
    # ========================================================================

    def _build_menu_bar(self):
        """构建菜单栏。"""
        menubar = self.menuBar()

        # 文件菜单
        file_menu = menubar.addMenu("文件(&F)")

        connect_action = QAction("连接设备...", self)
        connect_action.setShortcut(QKeySequence("Ctrl+C"))
        connect_action.triggered.connect(self._connect_dialog)
        file_menu.addAction(connect_action)

        disconnect_action = QAction("断开连接", self)
        disconnect_action.setShortcut(QKeySequence("Ctrl+D"))
        disconnect_action.triggered.connect(self._disconnect)
        file_menu.addAction(disconnect_action)

        file_menu.addSeparator()

        exit_action = QAction("退出", self)
        exit_action.setShortcut(QKeySequence("Ctrl+Q"))
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # 视图菜单
        view_menu = menubar.addMenu("视图(&V)")

        theme_menu = view_menu.addMenu("主题")
        for theme_name, theme in THEMES.items():
            action = QAction(theme.display_name, self)
            action.setCheckable(True)
            if theme_name == self._current_theme:
                action.setChecked(True)
            action.triggered.connect(lambda checked, name=theme_name: self._apply_theme(name))
            theme_menu.addAction(action)

        view_menu.addSeparator()

        waterfall_action = QAction("显示瀑布图", self)
        waterfall_action.setCheckable(True)
        waterfall_action.setChecked(True)
        waterfall_action.triggered.connect(self._toggle_waterfall)
        view_menu.addAction(waterfall_action)

        fullscreen_action = QAction("全屏", self)
        fullscreen_action.setShortcut(QKeySequence("F11"))
        fullscreen_action.triggered.connect(self._toggle_fullscreen)
        view_menu.addAction(fullscreen_action)

        # 工具菜单
        tools_menu = menubar.addMenu("工具(&T)")

        sweep_action = QAction("FM 扫频找台", self)
        sweep_action.triggered.connect(self._start_sweep)
        tools_menu.addAction(sweep_action)

        record_action = QAction("开始/停止录音", self)
        record_action.setShortcut(QKeySequence("Ctrl+R"))
        record_action.triggered.connect(self._toggle_record)
        tools_menu.addAction(record_action)

        tools_menu.addSeparator()

        ntrip_action = QAction("NTRIP 设置", self)
        ntrip_action.triggered.connect(self._open_ntrip_dialog)
        tools_menu.addAction(ntrip_action)

        tools_menu.addSeparator()

        about_action = QAction("关于 MBDSDR", self)
        about_action.triggered.connect(self._show_about)
        tools_menu.addAction(about_action)

    def _build_tool_bar(self):
        """构建工具栏。"""
        toolbar = QToolBar("主工具栏")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        # 连接状态
        self.conn_label = QLabel("  状态: 未连接  ")
        self.conn_label.setStyleSheet("font-weight: 600;")
        toolbar.addWidget(self.conn_label)

        toolbar.addSeparator()

        # 连接按钮
        self.connect_btn = QPushButton("连接")
        self.connect_btn.setFixedHeight(28)
        self.connect_btn.clicked.connect(self._connect_dialog)
        toolbar.addWidget(self.connect_btn)

        self.disconnect_btn = QPushButton("断开")
        self.disconnect_btn.setFixedHeight(28)
        self.disconnect_btn.clicked.connect(self._disconnect)
        self.disconnect_btn.setEnabled(False)
        toolbar.addWidget(self.disconnect_btn)

        toolbar.addSeparator()

        # 主题切换
        toolbar.addWidget(QLabel(" 主题: "))
        self.theme_combo = QComboBox()
        for name, theme in THEMES.items():
            self.theme_combo.addItem(theme.display_name, name)
        self.theme_combo.setFixedHeight(28)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_combo_changed)
        toolbar.addWidget(self.theme_combo)

        toolbar.addSeparator()

        # 瀑布图开关
        self.waterfall_btn = QPushButton("瀑布图")
        self.waterfall_btn.setFixedHeight(28)
        self.waterfall_btn.setCheckable(True)
        self.waterfall_btn.setChecked(True)
        self.waterfall_btn.clicked.connect(self._toggle_waterfall)
        toolbar.addWidget(self.waterfall_btn)

        toolbar.addSeparator()

        # 渲染模式指示
        render_text = "OpenGL" if HAS_OPENGL else "软件渲染"
        self._render_label = QLabel(f"  渲染: {render_text}  ")
        render_label = self._render_label
        render_label.setStyleSheet("color: #5B7B8C; font-size: 9pt;")
        toolbar.addWidget(render_label)

        # 拉伸空白（QToolBar 没有 addStretch，用空白 QWidget 替代）
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)

        # 录音按钮
        self.record_btn = QPushButton("录音")
        self.record_btn.setObjectName("recordButton")
        self.record_btn.setFixedHeight(28)
        self.record_btn.setCheckable(True)
        self.record_btn.toggled.connect(self._toggle_record)
        toolbar.addWidget(self.record_btn)

    def _build_central_widget(self):
        """构建中央组件。"""
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # 主分割器：左侧频谱 + 右侧面板
        main_splitter = QSplitter(Qt.Horizontal)

        # 左侧：频谱显示 + 射频天空视图（Tab 切换）
        left_tab = QTabWidget()
        left_tab.setTabPosition(QTabWidget.North)

        # Tab 1: 频谱显示
        spectrum_widget = QWidget()
        spectrum_layout = QVBoxLayout(spectrum_widget)
        spectrum_layout.setContentsMargins(0, 0, 0, 0)
        spectrum_layout.setSpacing(4)

        # 频谱标题栏
        spectrum_header = QFrame()
        spectrum_header.setObjectName("card")
        spectrum_header.setFixedHeight(36)
        header_layout = QHBoxLayout(spectrum_header)
        header_layout.setContentsMargins(12, 4, 12, 4)

        self.spectrum_title = QLabel("频谱显示")
        self.spectrum_title.setObjectName("sectionTitle")
        header_layout.addWidget(self.spectrum_title)

        header_layout.addStretch()

        self.freq_label = QLabel("-- MHz")
        self.freq_label.setObjectName("statusValue")
        header_layout.addWidget(self.freq_label)

        self.rssi_label = QLabel("RSSI: --")
        self.rssi_label.setObjectName("statusValue")
        header_layout.addWidget(self.rssi_label)

        spectrum_layout.addWidget(spectrum_header)

        # 频谱组件
        self.spectrum = create_spectrum_widget(prefer_opengl=False)  # QOpenGLWidget fails to composite on some Windows GPUs; QPainter is equivalent here
        _is_gl = self.spectrum.__class__.__name__ == "SpectrumGLWidget"
        self._render_label.setText("  渲染: " + ("OpenGL" if _is_gl else "软件渲染 (QPainter)") + "  ")
        self.spectrum.freq_changed.connect(self._on_spectrum_freq_changed)
        spectrum_layout.addWidget(self.spectrum, stretch=1)

        left_tab.addTab(spectrum_widget, "频谱")

        # Tab 2: 射频天空视图（借鉴 Stellarium）
        self.sky_view = RFSkyView()
        self.sky_view.object_clicked.connect(self._on_sky_object_clicked)
        left_tab.addTab(self.sky_view, "射频天空")

        # Tab 3: SDR++ 式模块面板（源设备选择 + 信号流图 + 参数 + sink）
        # 来源: SDR++ core/src/core.cpp:138-157 的 Source/Radio/Sinks 菜单结构
        self.module_panel = ModulePanel()
        self.module_panel.tune_requested.connect(self._on_module_tune)
        left_tab.addTab(self.module_panel, "模块 / 信号流")

        # Tab 4: 气象卫星云图面板（GK-2A/FY-4/FY-3/GOES/NOAA）
        self.weather_panel = WeatherPanel()
        left_tab.addTab(self.weather_panel, "气象云图")

        # Tab 5: 多普勒定轨面板（LRO / Iridium / 自定义 TLE，EKF/RLS）
        self.doppler_panel = DopplerPanel()
        left_tab.addTab(self.doppler_panel, "多普勒定轨")

        # 初始化天空视图演示数据
        self._init_sky_view_demo()

        main_splitter.addWidget(left_tab)

        # 右侧：控制面板 + 状态面板 + AI 面板（Tab）
        right_tab = QTabWidget()
        right_tab.setTabPosition(QTabWidget.East)

        # Tab 1: 控制
        self.control_panel = ControlPanel()
        self.control_panel.tune_fm_requested.connect(self._on_tune_fm)
        self.control_panel.tune_am_requested.connect(self._on_tune_am)
        self.control_panel.volume_changed.connect(self._on_volume_changed)
        self.control_panel.record_toggled.connect(self._on_record_toggled)
        self.control_panel.mode_changed.connect(self._on_mode_changed)
        self.control_panel.gain_changed.connect(self._on_gain_changed)
        self.control_panel.squelch_changed.connect(self._on_squelch_changed)
        self.control_panel.tune_sdr_requested.connect(self._on_tune_sdr)
        self.control_panel.sample_rate_changed.connect(self._on_sample_rate_changed)
        right_tab.addTab(self.control_panel, "控制")

        # Tab 2: 状态
        self.status_panel = StatusPanel()
        right_tab.addTab(self.status_panel, "状态")

        # Tab 3: AI
        self.ai_panel = AIPanel()
        self.ai_panel.tool_call_requested.connect(self._on_ai_tool_call)
        self.ai_panel.command_submitted.connect(self._on_ai_command)
        right_tab.addTab(self.ai_panel, "AI 助手")
        # 启动即从 ~/.mbdsdr/config.json 初始化 agent，否则永远走规则降级
        self._init_ai_agent_from_config()

        right_tab.setCurrentIndex(0)
        main_splitter.addWidget(right_tab)

        # 设置分割比例
        main_splitter.setStretchFactor(0, 3)
        main_splitter.setStretchFactor(1, 2)
        main_splitter.setSizes([800, 500])

        main_layout.addWidget(main_splitter)

    def _build_status_bar(self):
        """构建状态栏。"""
        status_bar = QStatusBar()
        self.setStatusBar(status_bar)

        self.status_conn = QLabel("未连接")
        status_bar.addWidget(self.status_conn)

        status_bar.addWidget(QLabel(" | "))

        self.status_freq = QLabel("频率: --")
        status_bar.addWidget(self.status_freq)

        status_bar.addWidget(QLabel(" | "))

        self.status_rssi = QLabel("RSSI: --")
        status_bar.addWidget(self.status_rssi)

        status_bar.addWidget(QLabel(" | "))

        self.status_gps = QLabel("GPS: --")
        status_bar.addWidget(self.status_gps)

        status_bar.addPermanentWidget(QLabel("MBDSDR v0.1 | GPL-3.0"))

    # ========================================================================
    # 主题
    # ========================================================================

    def _apply_theme(self, theme_name: str):
        """应用主题。"""
        self._current_theme = theme_name
        theme = get_theme(theme_name)
        theme.apply(self.app if hasattr(self, 'app') else QApplication.instance())

        # 更新频谱组件颜色
        self.spectrum.set_theme_colors(
            theme.colors["bg"],
            theme.colors["border"],
            theme.colors["text"],
            theme.colors["primary"],
            theme.colors["accent"],
            theme.spectrum_colors,
        )

        # 更新主题下拉框
        idx = self.theme_combo.findData(theme_name)
        if idx >= 0:
            self.theme_combo.blockSignals(True)
            self.theme_combo.setCurrentIndex(idx)
            self.theme_combo.blockSignals(False)

    def _on_theme_combo_changed(self, index: int):
        theme_name = self.theme_combo.itemData(index)
        if theme_name:
            self._apply_theme(theme_name)

    # ========================================================================
    # MCP 连接管理
    # ========================================================================

    # 设备选择对话框中"ai-sdr Mini WebSocket"特殊条目的 data 标记
    _WS_SPECIAL = "__ai_sdr_mini_ws__"

    def _connect_dialog(self):
        """弹出设备选择对话框：动态枚举真实 SDR 设备，选中后真实 connect。

        来源: mbdsdr_ai/sdr_backend.py enumerate_all_sdr_devices() ——
        合并 SoapySDR 总线 + 原生 RTL-SDR/HackRF 枚举并去重。
        无设备时下拉框显示"未发现 SDR 设备"，不再写死设备列表。
        """
        # 动态枚举真实设备（任何异常都不应让 GUI 崩溃）
        try:
            from mbdsdr_ai.sdr_backend import (
                enumerate_all_sdr_devices, build_backend_for_device)
            devices = enumerate_all_sdr_devices()
        except Exception as e:
            devices = []
            QMessageBox.warning(self, "设备枚举失败", f"枚举 SDR 设备时出错：\n{e}")

        dlg = QDialog(self)
        dlg.setWindowTitle("选择 SDR 设备")
        dlg.setMinimumWidth(420)
        layout = QVBoxLayout(dlg)

        layout.addWidget(QLabel("检测到的 SDR 设备："))
        combo = QComboBox()

        # 保留原 ai-sdr Mini WebSocket 入口（自研板走 MCP/WebSocket）
        combo.addItem("ai-sdr Mini (WebSocket 192.168.4.1:81)", self._WS_SPECIAL)

        if devices:
            for dev in devices:
                label = dev.get("label") or dev.get("driver", "SDR 设备")
                combo.addItem(label, dev)
        else:
            # 无真实设备：显式提示，且不可选（data=None）
            none_item = "未发现 SDR 设备（请接好 USB/安装 SoapySDR 驱动后点刷新）"
            combo.addItem(none_item, None)
            idx = combo.count() - 1
            combo.model().item(idx).setEnabled(False)
        layout.addWidget(combo)

        # 刷新按钮：重新枚举并重建下拉框内容
        refresh_btn = QPushButton("刷新设备列表")
        layout.addWidget(refresh_btn)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        def _reload_devices():
            """点刷新：重新枚举，保留已选。"""
            try:
                new_devices = enumerate_all_sdr_devices()
            except Exception:
                new_devices = []
            prev = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("ai-sdr Mini (WebSocket 192.168.4.1:81)", self._WS_SPECIAL)
            if new_devices:
                for dev in new_devices:
                    combo.addItem(dev.get("label") or dev.get("driver", "SDR 设备"), dev)
            else:
                combo.addItem("未发现 SDR 设备（请接好 USB/安装 SoapySDR 驱动后点刷新）", None)
                combo.model().item(combo.count() - 1).setEnabled(False)
            # 尽量恢复之前的选择
            match = combo.findData(prev)
            combo.setCurrentIndex(match if match >= 0 else 0)
            combo.blockSignals(False)

        refresh_btn.clicked.connect(_reload_devices)

        if dlg.exec() != QDialog.Accepted:
            return

        data = combo.currentData()
        if data is None:
            return  # "未发现设备"项不可选；兜底

        # ai-sdr Mini WebSocket：走原 host/port 流程
        if data == self._WS_SPECIAL:
            host, ok = QInputDialog.getText(
                self, "连接 ai-sdr Mini", "设备 IP 地址:", text="192.168.4.1")
            if ok and host.strip():
                port, ok2 = QInputDialog.getInt(
                    self, "连接 ai-sdr Mini", "端口:",
                    value=81, min=1, max=65535)
                if ok2:
                    self._connect_real(host.strip(), port)
            return

        # 真实 SDR 设备：构造对应后端并真实 connect。
        # 红线：硬件失败绝不静默切 mock 报 success，弹错误提示。
        backend = build_backend_for_device(data)
        if backend is None:
            QMessageBox.critical(
                self, "连接失败",
                f"无法为设备「{data.get('label', '?')}」构造后端。")
            return

        try:
            ok = backend.connect()
        except Exception as e:
            ok = False
            backend.status.error = f"connect 异常: {e}"

        if not ok:
            err = backend.get_status().error or "未知错误（设备被占用/无权限/驱动缺失）"
            QMessageBox.critical(
                self, "连接失败",
                f"无法连接到 {backend.device.name}：\n{err}\n\n"
                f"（未切换到模拟模式，请检查硬件后重试）")
            try:
                backend.disconnect()
            except Exception:
                pass
            return

        # 连接成功：停掉旧 worker，切换到真实后端
        self._worker_manager.stop()
        self._worker = None
        self._active_sdr_backend = backend
        self._panels_set_sdr_connected(True)
        self.conn_label.setText(f"  状态: {backend.device.name}  ")
        self.conn_label.setStyleSheet("color: #6BA89A; font-weight: 600;")
        self.status_conn.setText(backend.device.name)
        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)
        self.statusBar().showMessage(
            f"已连接 {backend.device.name}", 4000)
        # 默认采样率对齐 SDR++ 风格的 2.048 MS/s（与 control_panel 默认档一致）
        try:
            backend.set_sample_rate(2_048_000.0)
        except Exception:
            pass
        # (A/B/C) 启动真实 IQ 流：频谱/录制/声卡全部接通
        self._start_iq_streams()

    def _connect_real(self, host: str, port: int):
        """连接真实硬件（ai-sdr Mini WebSocket）。"""
        self._disconnect()
        self._worker = self._worker_manager.start(host=host, port=port)
        self._connect_worker_signals()
        self._panels_set_sdr_connected(True)
        self.conn_label.setText(f"  状态: 连接中 {host}:{port}  ")
        self.conn_label.setStyleSheet("color: #C4845C; font-weight: 600;")
        self.status_conn.setText(f"连接中 {host}:{port}")
        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)

    def _connect_worker_signals(self):
        """连接 Worker 信号。"""
        if not self._worker:
            return
        self._worker.status_updated.connect(self.status_panel.on_status_updated)
        self._worker.status_updated.connect(self._on_status_for_ui)
        # GNSS 唯一数据源：真实串口 RealGNSSMonitor._poll_gnss → status_panel.update_gnss。
        # 不再连接 worker.gps_updated（避免 WebSocket 旧 sim 路径与串口真实值双写横跳）。
        self._worker.imu_updated.connect(self.status_panel.on_imu_updated)
        self._worker.connection_changed.connect(self.status_panel.on_connection_changed)
        self._worker.connection_changed.connect(self._on_connection_for_ui)
        self._worker.tool_result.connect(self.status_panel.on_tool_result)
        self._worker.tool_result.connect(self.ai_panel.on_tool_result)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.log_message.connect(self._on_log)

    def _panels_set_sdr_connected(self, connected: bool):
        """把真实 SDR 连接状态同步到各操作控件。
        未连接时：气象云图 / 多普勒定轨面板的实时 SDR 按钮置灰；
        控制面板的调谐/音量/模式/预设控件与录音按钮一并禁用，绝不暴露假可控状态。"""
        for panel in (getattr(self, "weather_panel", None),
                      getattr(self, "doppler_panel", None)):
            if panel is not None:
                try:
                    panel.set_sdr_connected(connected)
                except Exception:
                    pass
        # 控制面板：调谐/音量/模式/录音
        try:
            cp = getattr(self, "control_panel", None)
            if cp is not None and hasattr(cp, "set_sdr_connected"):
                cp.set_sdr_connected(connected)
        except Exception:
            pass
        # 工具栏录音按钮（无 SDR 不可录音）
        try:
            if hasattr(self, "record_btn"):
                self.record_btn.setEnabled(bool(connected))
        except Exception:
            pass

    def _disconnect(self):
        """断开连接。"""
        # 断开真实 SDR 后端（SoapySDR/RTL-SDR/HackRF）
        # 先停掉录制 / IQ 轮询 / 声卡，避免断开后还在读空句柄
        self._stop_iq_streams()
        if self._recording:
            # 断开时若还在录制，先落盘存盘
            try:
                self._apply_recording(False)
            except Exception:
                self._recording = False
        if self._active_sdr_backend is not None:
            try:
                self._active_sdr_backend.disconnect()
            except Exception:
                pass
            self._active_sdr_backend = None
        # 频谱清空为"未连接/无 IQ 数据"，绝不保留旧假谱
        try:
            self.spectrum.set_connected(False)
        except Exception:
            pass
        self._panels_set_sdr_connected(False)
        if self._worker_manager:
            self._worker_manager.stop()
        self._worker = None
        self.conn_label.setText("  状态: 未连接  ")
        self.conn_label.setStyleSheet("font-weight: 600;")
        self.status_conn.setText("未连接")
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)

    # ========================================================================
    # 信号槽：从 Worker 接收数据更新 UI
    # ========================================================================

    @Slot(bool, str)
    def _on_connection_for_ui(self, connected: bool, message: str):
        if connected:
            self.conn_label.setText(f"  状态: {message}  ")
            self.conn_label.setStyleSheet("color: #6BA89A; font-weight: 600;")
            self.status_conn.setText(message)
        else:
            self.conn_label.setText(f"  状态: {message}  ")
            self.status_conn.setText(message)

    @Slot(dict)
    def _on_status_for_ui(self, status: dict):
        freq = status.get("freq_display", "--")
        rssi = status.get("rssi", 0)
        self.freq_label.setText(freq)
        self.rssi_label.setText(f"RSSI: {rssi}")
        self.status_freq.setText(f"频率: {freq}")
        self.status_rssi.setText(f"RSSI: {rssi} dBm")

    @Slot(str)
    def _on_error(self, error: str):
        self.statusBar().showMessage(f"错误: {error}", 5000)

    @Slot(str)
    def _on_log(self, msg: str):
        # 写入状态栏
        try:
            self.statusBar().showMessage(str(msg), 5000)
        except Exception:
            pass

    # ========================================================================
    # 信号槽：从控制面板/AI 面板接收指令
    # ========================================================================

    @Slot(float)
    def _on_tune_fm(self, freq: float):
        if self._worker:
            self._worker.request_tool.emit("tune_fm", {"freq_mhz": freq})
        self.spectrum.set_center_freq(freq)
        # 真实 SDR 后端：真正下发到硬件
        if self._active_sdr_backend is not None:
            try:
                self._active_sdr_backend.set_frequency(freq * 1e6)
            except Exception:
                pass
        # 更新频率显示
        self.freq_label.setText(f"{freq:.3f} MHz")
        self.status_freq.setText(f"频率: {freq:.3f} MHz")

    @Slot(int)
    def _on_tune_am(self, freq: int):
        if self._worker:
            self._worker.request_tool.emit("tune_am", {"freq_khz": freq})
        # 真实 SDR 后端：真正下发到硬件
        if self._active_sdr_backend is not None:
            try:
                self._active_sdr_backend.set_frequency(freq * 1e3)
            except Exception:
                pass

    @Slot(float, str)
    def _on_tune_sdr(self, freq_hz: float, mode: str):
        if self._worker:
            self._worker.request_tool.emit("tune_sdr",
                                   {"freq_hz": freq_hz, "mode": mode})
        self.spectrum.set_center_freq(freq_hz / 1e6)
        # 真实 SDR 后端：真正下发频率 + 解调模式
        if self._active_sdr_backend is not None:
            try:
                self._active_sdr_backend.set_frequency(freq_hz)
            except Exception:
                pass
            try:
                self._active_sdr_backend.set_demod(mode)
            except Exception:
                pass
        # 更新频率显示
        self.freq_label.setText(f"{freq_hz / 1e6:.3f} MHz")
        self.status_freq.setText(f"频率: {freq_hz / 1e6:.3f} MHz")

    @Slot(float)
    def _on_module_tune(self, freq_hz: float):
        """模块面板里改了频率 → 同步到频谱中心频。"""
        self.spectrum.set_center_freq(freq_hz / 1e6)
        self.freq_label.setText(f"{freq_hz / 1e6:.3f} MHz")

    @Slot(str)
    def _on_ai_command(self, text: str):
        # AI 面板提交的自然语言指令 -> 转发给 AI worker
        try:
            self.ai_panel._call_ai(text)
        except Exception:
            pass

    @Slot(int)
    def _on_gain_changed(self, db: int):
        if self._worker:
            try:
                self._worker.request_tool.emit("sdr_set_gain", {"gain_db": float(db)})
            except Exception:
                pass
        # 真实 SDR 后端：真正下发 LNA 增益
        if self._active_sdr_backend is not None:
            try:
                self._active_sdr_backend.set_gain(float(db))
            except Exception:
                pass

    def _on_squelch_changed(self, dbfs: float):
        # 本地声卡门控：信号低于此 dBFS 时静音（不下发硬件）
        self._squelch_db = float(dbfs)

    def _on_volume_changed(self, volume: int):
        if self._worker:
            self._worker.request_tool.emit("set_volume", {"volume": volume})
        # (C) 真实声卡音量：0-63 → 0.0-2.0 增益
        if self._audio_player is not None:
            try:
                self._audio_player.set_gain(volume / 63.0 * 2.0)
            except Exception:
                pass

    @Slot(bool)
    def _on_record_toggled(self, recording: bool):
        """控制面板录音按钮 toggled → 走与工具栏/菜单一致的本地 baseband 录制链路。"""
        # 同步工具栏录音按钮（防重入 guard）
        if self.record_btn.isChecked() != recording:
            self._record_guard = True
            try:
                self.record_btn.setChecked(recording)
            finally:
                self._record_guard = False
        # 转发给旧 worker（WebSocket 模式）保持兼容；真实 SDR 模式下 worker 为 None
        if self._worker:
            try:
                self._worker.request_tool.emit(
                    "start_record" if recording else "stop_record", {})
            except Exception:
                pass
        # 真正的本地 baseband 录制状态机（幂等：已是该状态则 no-op）
        self._apply_recording(recording)

    @Slot(str)
    def _on_mode_changed(self, mode: str):
        # 真实 SDR 后端：下发解调模式
        if self._active_sdr_backend is not None:
            try:
                self._active_sdr_backend.set_demod(mode)
            except Exception:
                pass
        # 根据模式调整 VFO 带宽（WFM 广播 ~180k，窄带 FM/AM ~12k）
        try:
            m = (mode or "FM").upper()
            if m == "WFM" or m == "FM":
                bw = 180000.0 if m == "WFM" else 12000.0
            else:
                bw = 12000.0
            if self._vfo is not None:
                self._vfo_bw = bw
                self._vfo.set_bandwidth(bw)
        except Exception:
            pass

    @Slot(float)
    def _on_sample_rate_changed(self, rate_hz: float):
        """控制面板采样率下拉切换 → 下发后端 + 重建 VFO + 更新录制采样率。"""
        # 真实 SDR 后端：真正下发采样率
        if self._active_sdr_backend is not None:
            try:
                self._active_sdr_backend.set_sample_rate(float(rate_hz))
            except Exception:
                pass
        # 更新录制采样率（下一次开始录制时生效）
        self._record_sr = float(rate_hz)
        # VFO 输入采样率变了 → 下次解调时懒加载重建
        self._vfo = None

    def _init_ai_agent_from_config(self):
        """从 ~/.mbdsdr/config.json 读 API 配置并初始化 AI agent。"""
        import json as _json, os
        cfg_path = os.path.expanduser("~/.mbdsdr/config.json")
        try:
            if os.path.exists(cfg_path):
                with open(cfg_path) as f:
                    cfg = _json.load(f)
                if cfg.get("api_key"):
                    self.ai_panel.init_agent(
                        api_key=cfg.get("api_key", ""),
                        base_url=cfg.get("base_url", ""),
                        model=cfg.get("model", ""),
                    )
        except Exception:
            pass  # 无配置则保持规则降级，不崩溃

    @Slot(str, dict)
    def _on_ai_tool_call(self, tool_name: str, params: dict):
        if self._worker:
            self._worker.request_tool.emit(tool_name, params)
        # 同步更新 UI
        if tool_name == "tune_fm":
            self.spectrum.set_center_freq(params.get("freq_mhz", 98.5))
            self.control_panel.set_freq_fm(params.get("freq_mhz", 98.5))
        elif tool_name == "set_volume":
            self.control_panel.set_volume(params.get("volume", 30))

    @Slot(float)
    def _on_spectrum_freq_changed(self, freq: float):
        self.control_panel.set_freq_fm(freq)
        if self._worker:
            self._worker.request_tool.emit("tune_fm", {"freq_mhz": freq})
        # 真实 SDR 后端：频谱拖动/双击 → 真正下发中心频率
        if self._active_sdr_backend is not None:
            try:
                self._active_sdr_backend.set_frequency(freq * 1e6)
            except Exception:
                pass
        # 更新状态栏频率显示
        self.freq_label.setText(f"{freq:.3f} MHz")
        self.status_freq.setText(f"频率: {freq:.3f} MHz")

    # ========================================================================
    # 其他操作
    # ========================================================================

    def _update_record_time(self):
        self._record_seconds += 1
        self.control_panel.update_record_time(self._record_seconds)

    def _toggle_record(self):
        """菜单 / 工具栏录音按钮统一入口。

        工具栏 record_btn.toggled 会触发本槽；菜单/快捷键触发时按钮尚未变化。
        用 _record_guard 防止两个按钮互相 setChecked 引发无限递归。
        真正的录制动作统一交给 _apply_recording（幂等）。
        """
        if self._record_guard:
            return
        target = not self._recording
        self._record_guard = True
        try:
            # 两边按钮都对齐到 target（值相同则 Qt 不再发 toggled，不会死循环）
            if self.record_btn.isChecked() != target:
                self.record_btn.setChecked(target)
            if self.control_panel.record_button.isChecked() != target:
                self.control_panel.record_button.setChecked(target)
        finally:
            self._record_guard = False
        # control_panel.record_button.setChecked 会触发 record_toggled
        # → _on_record_toggled → _apply_recording；这里兜底再调一次（幂等）。
        self._apply_recording(target)

    # ========================================================================
    # (A/B/C) 真实 IQ 数据流：频谱 / baseband 录制 / 声卡解调输出
    # ========================================================================

    def _start_iq_streams(self):
        """真实 SDR 连接成功后：启动 IQ 轮询定时器 + 打开声卡输出。

        无后端 / 无 read_samples / 无声卡设备时全部安全降级，绝不崩溃。
        """
        # (C) 尝试打开声卡；无 sounddevice / 无设备时 available=False 安全降级
        if self._audio_player is not None:
            try:
                ok = self._audio_player.start()
                if not ok:
                    self.statusBar().showMessage(
                        "无音频输出设备（声卡已禁用，不影响频谱/录制）", 5000)
            except Exception:
                pass
        # (A) 启动 IQ 轮询定时器
        if self._iq_poll_timer is not None and not self._iq_poll_timer.isActive():
            self._iq_poll_timer.start()

    def _stop_iq_streams(self):
        """断开时：停 IQ 轮询 + 关声卡。"""
        if self._iq_poll_timer is not None:
            try:
                self._iq_poll_timer.stop()
            except Exception:
                pass
        if self._audio_player is not None:
            try:
                self._audio_player.stop()
            except Exception:
                pass

    def _active_read_samples(self):
        """防御性拿到 (read_samples_fn, sample_rate)。无后端/无方法返 (None, None)。"""
        backend = self._active_sdr_backend
        if backend is None:
            return None, None
        # 已连接？
        try:
            st = backend.get_status()
            if st is not None and not getattr(st, "connected", True):
                return None, None
        except Exception:
            pass
        read = getattr(backend, "read_samples", None)
        if not callable(read):
            return None, None
        try:
            sr = float(getattr(backend, "get_sample_rate", lambda: 2_400_000.0)())
        except Exception:
            sr = 2_400_000.0
        return read, sr

    def _poll_sdr_iq(self):
        """每 50ms 从真实后端读一块 IQ，分发给频谱 / 录制 / FM 解调声卡。

        - 无后端/无 read_samples/返回 None：不崩溃，频谱保持"未连接"。
        - worker(WebSocket) 模式无本地 IQ 流，本定时器不启动，保持"未连接"，不造假峰。
        """
        read, sr = self._active_read_samples()
        if read is None:
            return
        try:
            # 16384 样点：与 spectrum_widget FFT 16384 档位对齐；
            # RTL-SDR 后端已有环形缓冲（生产者线程持续读），消费者可一次读大块。
            iq = read(16384)
        except Exception:
            return
        if iq is None:
            return
        iq = np.asarray(iq, dtype=np.complex64)
        if iq.size < 64:
            return

        # (A) 喂频谱（真 FFT：Nuttall 窗 + 复 FFT + fftshift + dBFS + IIR）
        try:
            self.spectrum.set_iq_data(iq, sr)
        except Exception:
            pass

        # 更新 RSSI 显示（从后端状态或 IQ 功率估计）
        self._update_rssi_from_iq(iq)

        # (B) 录制中：累积到 buffer
        if self._recording and self._record_iq_buffer is not None:
            try:
                self._record_iq_buffer.append(iq.copy())
            except Exception:
                pass

        # (C) FM 鉴频解调 → 声卡输出（带静噪门控）
        self._demod_and_play(iq, sr)

    def _update_rssi_from_iq(self, iq: np.ndarray):
        """用 IQ 功率估计 RSSI(dBFS) 更新状态栏；失败静默。"""
        try:
            p = float(np.mean(np.abs(iq) ** 2))
            dbfs = 10.0 * math.log10(p + 1e-12)
            txt = f"RSSI: {dbfs:.1f} dBFS"
            self.rssi_label.setText(txt)
            self.status_rssi.setText(txt)
        except Exception:
            pass

    def _ensure_vfo(self, in_sr: float) -> bool:
        """懒加载创建/重建 VFO。成功返回 True；VFO 不可用返回 False（走回退路径）。"""
        if in_sr <= 0:
            return False
        if self._vfo is not None and abs(self._vfo_in_sr - in_sr) < 1.0:
            return True  # 已有且采样率一致
        try:
            from mbdsdr_ai.dsp import VFO
            self._vfo = VFO(
                in_samplerate=float(in_sr),
                out_samplerate=self._vfo_out_sr,
                bandwidth=self._vfo_bw,
                offset=0.0,  # RTL-SDR 零中频：VFO 中心 = 中心频率
            )
            self._vfo_in_sr = float(in_sr)
            return True
        except Exception:
            self._vfo = None
            self._vfo_in_sr = 0.0
            return False

    def _demod_and_play(self, iq: np.ndarray, sr: float):
        """FM 复数鉴频 → 静噪门控 → 写声卡。

        优先走 SDR++ 风格 VFO 链（变频→重采样→低通），VFO 输出已是 48k；
        VFO 不可用/失败时回退到直接对全带宽 IQ 鉴频 + _decimate_to_48k。
        无 AudioPlayer / 无设备 / 被静噪门住时全部安全 no-op。
        """
        player = self._audio_player
        if player is None or not getattr(player, "available", False):
            return
        if iq.size < 16:
            return
        # 静噪门控：信号太弱不输出（避免底噪刺耳）。基于全带宽 IQ 功率。
        try:
            p = float(np.mean(np.abs(iq) ** 2))
            dbfs = 10.0 * math.log10(p + 1e-12)
            if dbfs < self._squelch_db:
                return
        except Exception:
            return

        # ---- 路径 A：VFO 链（SDR++ 风格：先信道滤波再鉴频）----
        vfo_ok = False
        try:
            if self._ensure_vfo(sr):
                vfo_out = self._vfo.process(iq)
                if vfo_out is not None and len(vfo_out) > 16:
                    # VFO 输出已是 out_sr(48k)，鉴频后直接写声卡，不再 decimate
                    phase = np.angle(
                        vfo_out[1:] * np.conj(vfo_out[:-1])
                    ).astype(np.float32)
                    if phase.size > 0:
                        player.write(phase)
                        vfo_ok = True
        except Exception:
            vfo_ok = False

        if vfo_ok:
            return

        # ---- 路径 B（回退）：直接对全带宽 IQ 鉴频 + 整数抽取到 48k ----
        try:
            # 标准复数鉴频：相邻采样相位差
            phase = np.angle(iq[1:] * np.conj(iq[:-1])).astype(np.float32)
            audio = self._decimate_to_48k(phase, sr)
            if audio is None or audio.size == 0:
                return
            player.write(audio)
        except Exception:
            pass

    @staticmethod
    def _decimate_to_48k(audio: np.ndarray, sr: float) -> Optional[np.ndarray]:
        """把鉴频音频（采样率 sr）降到 48k。简单移动平均低通 + 整数抽取。"""
        target = 48000
        if sr <= 0:
            return audio
        if sr > target * 1.5:
            d = int(round(sr / target))
            if d >= 2 and audio.size >= d:
                n = (audio.size // d) * d
                # 移动平均（低通）后抽取
                return audio[:n].reshape(-1, d).mean(axis=1).astype(np.float32)
            return audio
        if sr < target * 0.8:
            # 上采样：线性插值（scipy 不可用时也能跑）
            try:
                import numpy as _np
                n_src = audio.size
                n_dst = max(1, int(round(n_src * target / sr)))
                x_old = _np.linspace(0.0, 1.0, n_src, endpoint=False)
                x_new = _np.linspace(0.0, 1.0, n_dst, endpoint=False)
                return _np.interp(x_new, x_old, audio).astype(_np.float32)
            except Exception:
                return audio
        return audio

    # ------------------------------------------------------------------
    # (B) baseband 录制状态机
    # ------------------------------------------------------------------

    def _apply_recording(self, on: bool):
        """统一录制状态切换（幂等）。on=True 开始，on=False 停止并存盘。"""
        if on == self._recording:
            return
        if on:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self):
        """开始录制：检查后端 → 建文件名 → 清 buffer → 状态栏提示。
        无 SDR 后端时弹提示且不造假文件（按钮会被调用方弹回）。"""
        backend = self._active_sdr_backend
        if backend is None or not callable(getattr(backend, "read_samples", None)):
            QMessageBox.warning(
                self, "无法录音",
                "未连接 SDR，无法录制 baseband。\n请先连接真实 SDR 设备。")
            # 复位状态/按钮
            self._recording = False
            self._record_guard = True
            try:
                self.record_btn.setChecked(False)
                self.control_panel.record_button.setChecked(False)
            finally:
                self._record_guard = False
            return

        if _save_iq is None:
            QMessageBox.warning(self, "无法录音", "baseband_io 不可用，无法存盘。")
            self._recording = False
            return

        try:
            sr = float(backend.get_sample_rate())
        except Exception:
            sr = 2_400_000.0
        try:
            center_hz = float(backend.get_frequency())
        except Exception:
            center_hz = 0.0

        # sidecar 元数据：从真实后端捕获开始时间 / 射频增益 / 设备名 / 驱动
        self._record_start_time = datetime.now().isoformat(timespec="seconds")
        try:
            self._record_gain_db = float(getattr(backend.status, "gain_db", 0.0))
        except Exception:
            self._record_gain_db = 0.0
        _dev_obj = getattr(backend, "device", None)
        self._record_device = str(
            getattr(_dev_obj, "name", "") or backend.__class__.__name__)
        self._record_driver = backend.__class__.__name__

        rec_dir = os.path.expanduser("~/mbdsdr_recordings")
        try:
            os.makedirs(rec_dir, exist_ok=True)
        except Exception:
            rec_dir = tempfile.gettempdir()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(rec_dir, f"baseband_{ts}.iq")

        self._record_file_path = path
        self._record_iq_buffer = []
        self._record_sr = sr
        self._record_center_hz = center_hz
        self._recording = True

        # 1s 计时 UI（沿用原有 _record_timer）
        self._record_seconds = 0
        if self._record_timer is None:
            self._record_timer = QTimer(self)
            self._record_timer.timeout.connect(self._update_record_time)
        self._record_timer.start(1000)

        # UI：录音中按钮变红显示"停止中"
        self.record_btn.setText("停止中")
        self.record_btn.setStyleSheet(
            "background-color:#B85C5C; color:#FFFFFF; font-weight:600;")
        try:
            self.control_panel.record_button.setText("停止录音")
            self.control_panel.record_status.setText("录音中... 00:00")
        except Exception:
            pass
        self.statusBar().showMessage(f"开始录制 baseband: {path}")

    def _stop_recording(self):
        """停止录制：拼接 buffer → save_iq 落盘（.iq + .json sidecar）→ 状态栏汇报。"""
        self._recording = False
        if self._record_timer is not None:
            try:
                self._record_timer.stop()
            except Exception:
                pass
            self._record_timer = None

        info = None
        try:
            if self._record_iq_buffer:
                iq = np.concatenate(self._record_iq_buffer)
            else:
                iq = np.zeros(0, dtype=np.complex64)
            if iq.size > 0 and self._record_file_path and _save_iq is not None:
                info = _save_iq(
                    iq, self._record_file_path,
                    sample_rate=self._record_sr,
                    center_freq_hz=self._record_center_hz,
                    start_time=self._record_start_time or None,
                    gain_db=self._record_gain_db,
                    device=self._record_device,
                    driver=self._record_driver,
                    note="MBDSDR baseband recording (float32 interleaved I/Q)")
        except Exception as e:
            self.statusBar().showMessage(f"录制存盘失败: {e}", 5000)
            info = None
        finally:
            self._record_iq_buffer = []

        # UI 恢复
        self.record_btn.setText("录音")
        self.record_btn.setStyleSheet("")
        secs = getattr(self, "_record_seconds", 0)
        try:
            self.control_panel.record_button.setText("开始录音")
        except Exception:
            pass
        if info:
            kb = info.get("size_bytes", 0) / 1024.0
            msg = (f"录制完成: {info['path']}  "
                   f"({info['samples']} 样本, {kb:.1f} KB, {secs}s)")
            try:
                self.control_panel.record_status.setText(
                    f"已存 {info['samples']} 样本 ({kb:.0f} KB)")
            except Exception:
                pass
        else:
            msg = "录制结束（无数据落盘）"
            try:
                self.control_panel.record_status.setText("未录音")
            except Exception:
                pass
        self.statusBar().showMessage(msg, 8000)

    def _toggle_waterfall(self):
        self.spectrum.toggle_waterfall()

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _start_sweep(self):
        """启动扫频（切换到 AI 面板并填入指令）。"""
        # 找到 right_tab 的索引
        for i in range(self.ai_panel.parent().count()):
            if self.ai_panel.parent().widget(i) == self.ai_panel:
                self.ai_panel.parent().setCurrentIndex(i)
                break
        self.ai_panel.input_field.setText("扫频 87-108 MHz 找所有电台")

    def _open_ntrip_dialog(self):
        """打开 NTRIP 配置对话框（非模态）。

        首次打开时创建 NtripConfigDialog 并保持引用 self._ntrip_dialog 防 GC；
        之后重复打开只是 show()/raise_activate()，不重复构造。
        """
        if self._ntrip_dialog is None:
            try:
                from ntrip_panel import NtripConfigDialog
            except Exception:
                from desktop.ntrip_panel import NtripConfigDialog  # type: ignore
            dlg = NtripConfigDialog(parent=self)
            # 回填上次保存的配置（若有）
            try:
                import json
                cfg_file = os.path.expanduser("~/.mbdsdr/gui_config.json")
                if os.path.exists(cfg_file):
                    with open(cfg_file, "r") as f:
                        saved = json.load(f)
                    dlg.apply_config(saved.get("ntrip"))
            except Exception:
                pass
            self._ntrip_dialog = dlg
        self._ntrip_dialog.show()
        self._ntrip_dialog.raise_()
        self._ntrip_dialog.activateWindow()

    def _show_about(self):
        QMessageBox.about(
            self, "关于 MBDSDR",
            "<h3>MBDSDR - AI 定义无线电</h3>"
            "<p>全开源 GPL-3.0 软件定义无线电平台</p>"
            "<p>呼号：在「设置」中配置 | 硬件板名 ai-sdr Mini</p>"
            "<p>集成 GNU Radio / SDR++ / SatDump 等开源项目功能</p>"
            "<p>支持 MCP 工具调用，任意 AI IDE 可直接控制硬件</p>"
            "<p>射频天空视图借鉴 Stellarium 设计理念</p>"
            "<p>版本: v0.2 (2026-09-17)</p>"
        )

    # ========================================================================
    # 射频天空视图
    # ========================================================================

    def _init_sky_view_demo(self):
        """初始化天空视图：天线指向默认值 + 真实 sgp4 卫星跟踪。

        观测站坐标为 None（未配置/GNSS 未定位）时，SatelliteTracker 不启动计算，
        天空图显示空状态提示；坐标由 GNSS 或配置文件就位后再启动。
        """
        # 卫星位置统一由 SatelliteTracker 驱动（mbdsdr_ai.orbit 真实 celestrak TLE）。
        self.sat_tracker = SatelliteTracker(
            self.sky_view, self._observer_lat, self._observer_lon,
        )

        # 天线指向（默认正北水平，is_tracking=False；这是合理的默认状态而非假数据）
        antenna = AntennaPointing(
            azimuth_deg=0,
            elevation_deg=0,
            beamwidth_deg=30,
            gain_dbi=5.0,
            is_tracking=False,
            target_name="",
        )
        self.sky_view.set_antenna(antenna)

        # 不再生成假信号热力图（原 -70+20*sin... 伪热力已移除）：
        # 无真实射频扫描数据时不显示热力图。
        self._apply_observer_location()

    def _apply_observer_location(self):
        """根据当前观测站坐标同步 SatelliteTracker 与天空图数据来源标注。

        - 坐标为 None：停止卫星计算，天空图显示“地面站未设置”空状态；
        - 坐标已就位：交给 SatelliteTracker 做真实 sgp4 计算，数据源恒为 real。
        """
        lat = self._observer_lat
        lon = self._observer_lon
        if lat is None or lon is None:
            # 未设置观测站位置（GNSS 未连接或未手动配置）
            if self.sat_tracker is not None:
                self.sat_tracker.set_location(None, None)
            self.sky_view.set_data_source("none")
            return

        if self.sat_tracker is None:
            self.sat_tracker = SatelliteTracker(self.sky_view, lat, lon)
        else:
            self.sat_tracker.set_location(lat, lon)
        # 坐标来源只能是真实 GNSS 或 gui_config.json 手动配置，恒为 real
        self.sky_view.set_data_source("real")

    def _update_sky_satellites(self):
        """天空图周期任务：仅刷新新时空授时信息。

        卫星位置统一由 SatelliteTracker（mbdsdr_ai.orbit 真实 celestrak TLE）
        以自身定时器驱动；原 decoders.BUILTIN_TLE 硬编码 TLE 计算路径已移除，
        避免两套卫星数据冲突。
        """
        # 新时空：更新授时信息（每30秒NTP同步一次，其余用系统时间）
        self._update_time_info()

    def _update_time_info(self):
        """更新新时空授时信息到天空图。"""
        try:
            from mbdsdr_ai.new_spacetime import get_time_info
            # 每30次更新（约150秒）做一次NTP同步，其余用系统时间
            if not hasattr(self, '_ntp_sync_counter'):
                self._ntp_sync_counter = 0
            self._ntp_sync_counter += 1
            do_ntp = (self._ntp_sync_counter % 30 == 1)

            info = get_time_info(prefer_ntp=do_ntp)
            time_dict = {
                "utc_time": info.utc_time,
                "gps_week": int(info.gps_time // 604800) if info.gps_time else 0,
                "gps_tow": info.gps_time % 604800 if info.gps_time else 0,
                "ntp_server": info.ntp_server,
                "ntp_status": info.source,
                "clock_offset_ms": info.clock_offset_ms,
            }
            self.sky_view.set_time_info(time_dict)
        except Exception:
            pass

    # ========================================================================
    # 真实串口 GNSS（NMEA）轮询
    # ========================================================================

    def _start_real_gnss(self):
        """UI 初始化完成后启动真实串口 GNSS auto_detect。

        找不到设备 / 缺 pyserial 时 start() 返回 False，不抛异常、不阻塞 UI。
        无论成败都开启 1s 轮询定时器：无设备时 get_position() 返回 source="none"，
        状态面板与天空图停留在“未连接/地面站未设置”空状态。
        """
        if self._gnss is None:
            return
        try:
            self._gnss.start()
        except Exception:
            pass
        if self._gnss_timer is not None:
            self._gnss_timer.start()

    @Slot()
    def _poll_gnss(self):
        """每 1s 读一次真实串口 GNSS fix，刷新状态面板与天空图。

        无串口/无 fix 时 pos.source=="none"、坐标全 None：状态面板显示“未连接”，
        天空图回到“地面站未设置”空状态，绝不保留旧坐标或编造坐标。
        """
        if self._gnss is None:
            return
        try:
            pos = self._gnss.get_position()
        except Exception:
            return

        fix_dict = {
            "source": pos.source,
            "latitude": pos.lat,
            "longitude": pos.lon,
            "altitude_m": pos.alt,
            "satellites": pos.sats,
            "hdop": pos.hdop,
            "speed_kmh": pos.speed,
            "course_deg": pos.course,
            "utc_time": pos.utc_time,
            "fix_quality": 0,
            "timestamp": pos.timestamp,
        }
        # 状态面板 GPS 组（real 绿 / none 灰）
        try:
            self.status_panel.update_gnss(fix_dict)
        except Exception:
            pass
        # 底部状态栏 GPS 标签（同一串口数据源，避免再走 worker 双写）
        try:
            if pos.source == "real" and pos.lat is not None and pos.lon is not None:
                self.status_gps.setText(
                    f"GPS: {float(pos.lat):.4f}, {float(pos.lon):.4f}")
            else:
                self.status_gps.setText("GPS: 未连接")
        except Exception:
            pass
        # 天空图观测站坐标与数据来源角标
        try:
            self.sky_view.set_gnss_position(fix_dict)
        except Exception:
            pass
        # 真实 GNSS 卫星天空图：各星座可见卫星位置/信噪比 + GSA 定位卫星标记
        # （1s 轮询即 GSV 典型刷新率；无数据时天空图自行显示“GNSS 未连接”）
        try:
            gsv_frames = self._gnss.get_gsv_frames()
            gsa_frame = self._gnss.get_gsa()
            self.sky_view.update_gnss_satellites(gsv_frames, gsa_frame)
        except Exception:
            pass
        # 真实 GNSS 自动设定地面站位置：仅当用户尚未手动配置
        # （_observer_lat/lon 均为 None，即 gui_config.json 无坐标）时才写入，
        # 避免覆盖手动配置坐标。
        if pos.source == "real" and pos.lat is not None and pos.lon is not None:
            if self._observer_lat is None and self._observer_lon is None:
                self._observer_lat = float(pos.lat)
                self._observer_lon = float(pos.lon)
                self._observer_from_gnss = True
                self._apply_observer_location()

    def _on_sky_object_clicked(self, obj):
        """天空对象点击处理：显示详情，可选跟踪，并调用 MCP 工具调谐频率。"""
        if obj.obj_type == "satellite":
            # 切换天线指向该卫星
            antenna = AntennaPointing(
                azimuth_deg=obj.azimuth_deg,
                elevation_deg=obj.elevation_deg,
                beamwidth_deg=30,
                gain_dbi=5.0,
                is_tracking=True,
                target_name=obj.name,
            )
            self.sky_view.set_antenna(antenna)

            # 如果是气象卫星，真正调用 MCP 工具调谐到其频率
            if obj.frequency_hz > 0:
                freq_mhz = obj.frequency_hz / 1e6
                self.freq_label.setText(f"{freq_mhz:.1f} MHz")
                self.status_freq.setText(f"频率: {freq_mhz:.1f} MHz")

                # 调用 MCP 工具设置频率（频谱-天空联动）
                if self._worker:
                    try:
                        self._worker.request_tool.emit("sdr_set_frequency", {"frequency_hz": int(obj.frequency_hz)})
                    except Exception:
                        pass

                # 同时更新控制面板的频率显示
                if hasattr(self, 'control_panel'):
                    try:
                        self.control_panel.freq_spin.setValue(freq_mhz)
                    except Exception:
                        pass

            # 状态栏提示
            self.statusBar().showMessage(
                f"已跟踪 {obj.name} (AZ {obj.azimuth_deg:.1f}°, EL {obj.elevation_deg:.1f}°) - 已自动调谐",
                5000,
            )
        elif obj.obj_type == "interferer":
            self.statusBar().showMessage(
                f"干扰源 {obj.name}: AZ {obj.azimuth_deg:.1f}°, EL {obj.elevation_deg:.1f}°, "
                f"信号 {obj.signal_strength_db:.1f} dB",
                5000,
            )

    def closeEvent(self, event):
        """关闭时断开连接并保存配置。"""
        self._save_gui_config()
        self._disconnect()
        # 停止真实串口 GNSS 轮询定时器与后台串口读线程
        try:
            if self._gnss_timer is not None:
                self._gnss_timer.stop()
        except Exception:
            pass
        try:
            if self._gnss is not None:
                self._gnss.stop()
        except Exception:
            pass
        event.accept()

    def _read_ntrip_from_disk(self) -> Optional[dict]:
        """读取 gui_config.json 里的 ntrip 段（不存在/损坏返回 None）。
        仅用于「对话框未打开」时保存配置不丢旧值；绝不打印密码。"""
        import json
        cfg_file = os.path.expanduser("~/.mbdsdr/gui_config.json")
        try:
            if os.path.exists(cfg_file):
                with open(cfg_file, "r") as f:
                    cfg = json.load(f)
                seg = cfg.get("ntrip")
                if isinstance(seg, dict):
                    return seg
        except Exception:
            pass
        return None

    def _save_gui_config(self):
        """保存 GUI 配置到 ~/.mbdsdr/gui_config.json。"""
        import json
        config_dir = os.path.expanduser("~/.mbdsdr")
        config_file = os.path.join(config_dir, "gui_config.json")
        try:
            os.makedirs(config_dir, exist_ok=True)
            config = {
                "window": {
                    "width": self.width(),
                    "height": self.height(),
                    "x": self.x(),
                    "y": self.y(),
                    "maximized": self.isMaximized(),
                },
                "current_freq_mhz": getattr(self.control_panel, '_current_freq_fm', 98.5),
                "current_mode": getattr(self.control_panel, '_current_mode', 'FM'),
                "volume": getattr(self.control_panel, 'volume_slider', None).value() if hasattr(self.control_panel, 'volume_slider') else 30,
                "theme": self._current_theme,
                "show_waterfall": getattr(self.spectrum, '_show_waterfall', True),
                "observer_lat": self._observer_lat,
                "observer_lon": self._observer_lon,
            }
            # NTRIP 配置：对话框已打开则取当前表单值；否则保留磁盘上的旧值。
            # 密码本地明文保存（不打印日志），未配置时写空串。
            ntrip_dlg = getattr(self, "_ntrip_dialog", None)
            if ntrip_dlg is not None:
                try:
                    config["ntrip"] = ntrip_dlg.get_config()
                except Exception:
                    pass
            else:
                # 对话框未打开：尽量保留已存在的 ntrip 段，避免 _load 后丢配置
                old = self._read_ntrip_from_disk()
                config["ntrip"] = old or {
                    "host": "", "port": 2101,
                    "mountpoint": "", "username": "", "password": "",
                }
            with open(config_file, 'w') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _load_gui_config(self):
        """从 ~/.mbdsdr/gui_config.json 加载 GUI 配置。"""
        import json
        config_file = os.path.expanduser("~/.mbdsdr/gui_config.json")
        if not os.path.exists(config_file):
            return
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
            # 恢复窗口大小和位置
            win = config.get("window", {})
            if not win.get("maximized", False):
                w = win.get("width", 1400)
                h = win.get("height", 900)
                x = win.get("x", 100)
                y = win.get("y", 100)
                self.resize(w, h)
                self.move(x, y)
            # 恢复观测站坐标：缺失时默认 None（未配置），不再回退到长春硬编码坐标
            self._observer_lat = config.get("observer_lat")
            self._observer_lon = config.get("observer_lon")
            # 坐标就位后同步 SatelliteTracker 与天空图空状态/角标
            self._apply_observer_location()
            # 恢复主题
            theme = config.get("theme", "default")
            if theme != self._current_theme:
                self._apply_theme(theme)
            # NTRIP 配置：对话框已创建则回填；未创建则等 _open_ntrip_dialog 时再读盘。
            ntrip_dlg = getattr(self, "_ntrip_dialog", None)
            if ntrip_dlg is not None:
                try:
                    ntrip_dlg.apply_config(config.get("ntrip"))
                except Exception:
                    pass
        except Exception:
            pass
