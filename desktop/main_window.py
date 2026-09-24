"""
MBDSDR 桌面端主窗口
====================
整合频谱显示、控制面板、状态面板、AI 对话面板。
支持主题切换、MCP 连接管理、OpenGL/软件渲染自动选择。
"""

import os
import sys
from typing import Optional

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
        self._is_sim: bool = False  # 是否处于模拟模式（模拟时天空图标注“模拟数据”）
        self.sat_tracker: Optional[SatelliteTracker] = None
        # 真实 SDR 后端（SoapySDR/RTL-SDR/HackRF 等）。由设备选择对话框真实 connect 后填充；
        # 与 self._worker（ai-sdr Mini WebSocket）互斥。硬件失败绝不静默切 mock。
        self._active_sdr_backend: Optional[object] = None

        # 构建 UI
        self._build_menu_bar()
        self._build_tool_bar()
        self._build_central_widget()
        self._build_status_bar()

        # 应用默认主题
        self._apply_theme(DEFAULT_THEME)

        # 自动连接模拟模式（无硬件时演示）
        QTimer.singleShot(500, self._auto_connect_simulation)

        # 加载 GUI 配置（窗口大小、频率、主题等）
        QTimer.singleShot(100, self._load_gui_config)

        # 天空图实时更新定时器（每 5 秒刷新卫星位置 + 授时）
        self._sky_update_timer = QTimer(self)
        self._sky_update_timer.setInterval(5000)
        self._sky_update_timer.timeout.connect(self._update_sky_satellites)
        self._sky_update_timer.start()
        # 立即先刷一次
        QTimer.singleShot(1200, self._update_sky_satellites)

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

        sim_action = QAction("模拟模式（无硬件）", self)
        sim_action.setShortcut(QKeySequence("Ctrl+M"))
        sim_action.triggered.connect(lambda: self._connect_simulation())
        file_menu.addAction(sim_action)

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

        self.sim_btn = QPushButton("模拟模式")
        self.sim_btn.setFixedHeight(28)
        self.sim_btn.clicked.connect(lambda: self._connect_simulation())
        toolbar.addWidget(self.sim_btn)

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
        render_label.setStyleSheet("color: #6B6B6B; font-size: 9pt;")
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
        self.control_panel.tune_sdr_requested.connect(self._on_tune_sdr)
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

        status_bar.addPermanentWidget(QLabel("MBDSDR v0.1 | GPL-3.0 | 呼号 BI4MIB"))

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

    def _auto_connect_simulation(self):
        """自动连接模拟模式（启动时无硬件演示）。"""
        self._connect_simulation()

    def _connect_simulation(self):
        """连接模拟模式。"""
        self._disconnect()
        self._is_sim = True
        self._worker = self._worker_manager.start(use_simulation=True)
        self._connect_worker_signals()
        self.conn_label.setText("  状态: 模拟模式  ")
        self.conn_label.setStyleSheet("color: #C4845C; font-weight: 600;")
        self.status_conn.setText("模拟模式")
        self.connect_btn.setEnabled(False)
        self.sim_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)

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
        self._is_sim = False
        self._panels_set_sdr_connected(True)
        self.conn_label.setText(f"  状态: {backend.device.name}  ")
        self.conn_label.setStyleSheet("color: #6BA89A; font-weight: 600;")
        self.status_conn.setText(backend.device.name)
        self.connect_btn.setEnabled(False)
        self.sim_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)
        self.statusBar().showMessage(
            f"已连接 {backend.device.name}", 4000)

    def _connect_real(self, host: str, port: int):
        """连接真实硬件。"""
        self._disconnect()
        self._is_sim = False
        self._worker = self._worker_manager.start(host=host, port=port, use_simulation=False)
        self._connect_worker_signals()
        self._panels_set_sdr_connected(True)
        self.conn_label.setText(f"  状态: 连接中 {host}:{port}  ")
        self.conn_label.setStyleSheet("color: #C4B85C; font-weight: 600;")
        self.status_conn.setText(f"连接中 {host}:{port}")
        self.connect_btn.setEnabled(False)
        self.sim_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)

    def _connect_worker_signals(self):
        """连接 Worker 信号。"""
        if not self._worker:
            return
        self._worker.status_updated.connect(self.status_panel.on_status_updated)
        self._worker.status_updated.connect(self._on_status_for_ui)
        self._worker.gps_updated.connect(self.status_panel.on_gps_updated)
        self._worker.gps_updated.connect(self._on_gps_for_ui)
        self._worker.imu_updated.connect(self.status_panel.on_imu_updated)
        self._worker.connection_changed.connect(self.status_panel.on_connection_changed)
        self._worker.connection_changed.connect(self._on_connection_for_ui)
        self._worker.tool_result.connect(self.status_panel.on_tool_result)
        self._worker.tool_result.connect(self.ai_panel.on_tool_result)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.log_message.connect(self._on_log)

    def _panels_set_sdr_connected(self, connected: bool):
        """把真实 SDR 硬件连接状态同步到气象云图 / 多普勒定轨面板。
        未连接时这两个面板的「实时 SDR」模式按钮置灰并显示未连接。"""
        for panel in (getattr(self, "weather_panel", None),
                      getattr(self, "doppler_panel", None)):
            if panel is not None:
                try:
                    panel.set_sdr_connected(connected)
                except Exception:
                    pass

    def _disconnect(self):
        """断开连接。"""
        # 断开真实 SDR 后端（SoapySDR/RTL-SDR/HackRF）
        if self._active_sdr_backend is not None:
            try:
                self._active_sdr_backend.disconnect()
            except Exception:
                pass
            self._active_sdr_backend = None
        self._panels_set_sdr_connected(False)
        if self._worker_manager:
            self._worker_manager.stop()
        self._worker = None
        self.conn_label.setText("  状态: 未连接  ")
        self.conn_label.setStyleSheet("font-weight: 600;")
        self.status_conn.setText("未连接")
        self.connect_btn.setEnabled(True)
        self.sim_btn.setEnabled(True)
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

    @Slot(dict)
    def _on_gps_for_ui(self, gps: dict):
        if gps.get("fix"):
            lat = gps.get("lat")
            lon = gps.get("lon")
            self.status_gps.setText(f"GPS: {lat:.4f}, {lon:.4f}")
            # GNSS 有真实定位时，自动用其坐标更新观测站位置，驱动天空图卫星计算。
            # sim 模式下该坐标为合成值，_apply_observer_location 会据此标注“模拟数据”。
            if lat is not None and lon is not None:
                self._observer_lat = float(lat)
                self._observer_lon = float(lon)
                self._apply_observer_location()
        else:
            self.status_gps.setText("GPS: 未定位")

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

    @Slot(int)
    def _on_tune_am(self, freq: int):
        if self._worker:
            self._worker.request_tool.emit("tune_am", {"freq_khz": freq})

    @Slot(float, str)
    def _on_tune_sdr(self, freq_hz: float, mode: str):
        if self._worker:
            self._worker.request_tool.emit("tune_sdr",
                                   {"freq_hz": freq_hz, "mode": mode})
        self.spectrum.set_center_freq(freq_hz / 1e6)

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
    def _on_volume_changed(self, volume: int):
        if self._worker:
            self._worker.request_tool.emit("set_volume", {"volume": volume})

    @Slot(bool)
    def _on_record_toggled(self, recording: bool):
        if recording:
            if self._worker:
                self._worker.request_tool.emit("start_record", {})
            self._record_seconds = 0
            self._record_timer = QTimer(self)
            self._record_timer.timeout.connect(self._update_record_time)
            self._record_timer.start(1000)
        else:
            if self._worker:
                self._worker.request_tool.emit("stop_record", {})
            if self._record_timer:
                self._record_timer.stop()
                self._record_timer = None

    @Slot(str)
    def _on_mode_changed(self, mode: str):
        pass  # 模式切换在控制面板内部处理

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

    # ========================================================================
    # 其他操作
    # ========================================================================

    def _update_record_time(self):
        self._record_seconds += 1
        self.control_panel.update_record_time(self._record_seconds)

    def _toggle_record(self):
        # record_btn.toggled 已触发本槽，不要再 toggle() 否则无限递归。
        # 同步控制面板的录音按钮，由它的 toggled 走正常录音链路。
        checked = self.record_btn.isChecked()
        if self.control_panel.record_button.isChecked() != checked:
            self.control_panel.record_button.setChecked(checked)

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

    def _show_about(self):
        QMessageBox.about(
            self, "关于 MBDSDR",
            "<h3>MBDSDR - AI 定义无线电</h3>"
            "<p>全开源 GPL-3.0 软件定义无线电平台</p>"
            "<p>呼号 BI4MIB | 硬件板名 ai-sdr Mini</p>"
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

        - 坐标为 None：停止卫星计算，天空图显示“无数据”空状态；
        - 坐标已就位：交给 SatelliteTracker 做真实 sgp4 计算；
        - sim 模式下坐标为合成值，天空图标注橙色“模拟数据”角标。
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
        # sim 模式坐标为合成数据，标注“模拟数据”；真实 GNSS/手动配置为 real
        self.sky_view.set_data_source("sim" if self._is_sim else "real")

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
        event.accept()

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
            theme = config.get("theme", "japanese_light")
            if theme != self._current_theme:
                self._apply_theme(theme)
        except Exception:
            pass
