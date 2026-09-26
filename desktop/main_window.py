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

from PySide6.QtCore import Qt, QTimer, Slot, QDateTime, QTimeZone, QThread, Signal
from PySide6.QtGui import QAction, QKeySequence, QFont, QShortcut
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTabWidget, QStatusBar, QToolBar, QMenuBar, QMenu, QLabel,
    QFileDialog, QMessageBox, QInputDialog, QComboBox, QPushButton,
    QFrame, QSizePolicy, QDialog, QDialogButtonBox, QLineEdit,
    QGroupBox, QFormLayout, QSlider, QSpinBox, QCheckBox,
    QDoubleSpinBox, QProgressDialog,
)

# 确保能导入同目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 确保能导入仓库根下的 mbdsdr_ai 包（baseband_io / audio_out / sdr_backend）
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from themes import get_theme, THEMES, DEFAULT_THEME
from spectrum_widget import create_spectrum_widget, HAS_OPENGL
from control_panel import ControlPanel, MODE_VFO_BANDWIDTH
from status_panel import StatusPanel
from ai_panel import AIPanel
from mcp_worker import MCPWorkerManager
from rf_sky_view import RFSkyView, SkyObject, AntennaPointing, HeatmapCell, SatelliteTracker
from module_panel import ModulePanel
# 气象云图 / 多普勒定轨面板（懒加载：构造时只建 Qt 控件，后端 ToolRegistry
# 在用户首次点解码/定轨时才创建，避免拖慢启动）。
from weather_panel import WeatherPanel
from doppler_panel import DopplerPanel
from sat_track_panel import SatTrackPanel
from new_spacetime_panel import NewSpacetimePanel
from adsb_map_panel import AdsbMapPanel
# 卫星闭环跟踪器（mbdsdr_ai.sat_tracker）：选中卫星->实时 az/el/多普勒->自动调谐。
# 用别名避免与 rf_sky_view 里的展示型 SatelliteTracker 冲突。缺 skyfield 时降级。
try:
    from mbdsdr_ai.sat_tracker import SatelliteTracker as LoopSatTracker
except Exception:  # pragma: no cover
    LoopSatTracker = None  # type: ignore
# 运行参数持久化（频率/增益/模式/带宽/采样率/呼号/AGC/主题 落 JSON，退出不丢）
from settings import DesktopSettings

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
# 多 VFO 状态管理 + 多线程接收流水线（可缺依赖降级，不拖垮 GUI）
try:
    from mbdsdr_ai.vfo_manager import VfoManager
except Exception:  # pragma: no cover
    VfoManager = None  # type: ignore
try:
    from receive_pipeline import ReceivePipeline, SharedDemodConfig
except Exception:  # pragma: no cover
    ReceivePipeline = None  # type: ignore
    SharedDemodConfig = None  # type: ignore


class _SweepWorker(QThread):
    """后台宽带扫频线程：步进调谐真实 SDR → 逐段 PSD → 拼接 → 活动信号提取。

    对标 SDR++ Frequency Scanner 的交互模式：在独立线程里逐中心频率
    set_frequency → read_samples，主线程只负责进度/取消与结果回贴。
    acquire 回调直接驱动真实后端，绝不合成 IQ；取消通过 _cancel 标志在下一个
    调谐段边界生效（read_samples 自带 1s 超时，不会永久阻塞）。
    """

    progress = Signal(float)            # 当前正在调谐的中心频率 Hz（仅作进度提示）
    finished_ok = Signal(object)        # sweep.SweepResult
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, backend, f_start_hz: float, f_stop_hz: float,
                 sample_rate_hz: float, step_hz: float, overlap: float,
                 dwell_samples: int, parent=None):
        super().__init__(parent)
        self._backend = backend
        self._f0 = float(f_start_hz)
        self._f1 = float(f_stop_hz)
        self._sr = float(sample_rate_hz)
        self._step = float(step_hz)
        self._overlap = float(overlap)
        self._dwell = int(dwell_samples)
        self._cancel = False

    def cancel(self):
        """请求取消：在下一个调谐段边界停止。"""
        self._cancel = True

    def run(self):
        import time as _time
        try:
            from mbdsdr_ai.sweep import sweep_scan
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"sweep 模块导入失败: {e}")
            return

        backend = self._backend

        def acquire(center_hz, sample_rate_hz, n):
            if self._cancel:
                return None
            try:
                backend.set_frequency(float(center_hz))
            except Exception:
                return None
            # 调谐后短暂驻留，让 AGC/滤波器稳定（SDR++ scanner 换频后也会等）
            _time.sleep(0.04)
            try:
                iq = backend.read_samples(int(n))
            except Exception:
                return None
            self.progress.emit(float(center_hz))
            return iq

        try:
            result = sweep_scan(
                acquire, self._f0, self._f1, self._sr,
                step_hz=self._step, overlap=self._overlap,
                dwell_samples=self._dwell,
            )
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
            return
        if self._cancel:
            self.cancelled.emit()
        else:
            self.finished_ok.emit(result)


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
        # 运行参数持久化：启动即读回上次频率/增益/模式/带宽/采样率/呼号/AGC/主题。
        # _restoring_settings=True 期间 _apply_theme 不回写配置，避免启动默认主题覆盖
        # 已读回的主题；控件由专用 setters（内部 blockSignals）回填，不触发 save。
        self._restoring_settings = True
        self.settings = DesktopSettings.load()
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
        # ---- 卫星闭环自动跟踪（过境预测 -> 闭环调谐）----
        # loop_tracker 持有选中卫星与实时 az/el/多普勒；_sat_track_timer 500ms 轮询。
        # 与上面的展示型 sat_tracker（rf_sky_view 桥）互不相干。
        self._loop_sat_tracker = None      # mbdsdr_ai.sat_tracker.SatelliteTracker
        self._sat_track_timer: Optional[QTimer] = None
        self._sat_track_prev_freq_hz: Optional[float] = None
        self._sat_track_prev_mode: Optional[str] = None
        self._sat_track_mode: str = "FM"
        # 真实串口 GNSS 监测：插上 GNSS 模块后由用户在「工具 → 连接 GNSS」里选口/波特率
        # 启动，驱动状态面板 GPS 组与天空图观测站坐标。找不到设备/缺依赖时降级为 none，
        # 绝不崩、绝不造假坐标。不在启动时自动开串口（用户可能根本没接 GNSS）。
        self._gnss = RealGNSSMonitor() if RealGNSSMonitor is not None else None
        self._gnss_timer: Optional[QTimer] = None
        # GNSS 串口配置（持久化到 gui_config.json）：
        #   port="" → auto_detect；Windows CH340 接收器填 "COM10"
        self._gnss_port: str = ""
        self._gnss_baudrate: int = 9600
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
        # 默认 VFO 带宽 12.5 kHz（NBFM 语音档，与 control_panel.MODE_VFO_BANDWIDTH["FM"] 对齐）
        self._vfo_bw: float = 12_500.0
        # 调试/测试断言用：记录 _demod_and_play 本次实际使用的解调模式
        self._last_demod_mode: str = "FM"

        # ---- 多线程接收流水线（重构：IQ reader/splitter/FFT/demod 全部后台线程）----
        # VfoManager 统一管理多 VFO 的配置态与 DSP 绑定（push_offset/push_bandwidth
        # 真正调到 dsp.VFO.set_offset → DDC 搬频，不再硬编码 offset=0）。
        self._vfo_mgr = VfoManager() if VfoManager is not None else None
        # UI 线程写 / 解调 worker 线程读的共享解调参数（模式 + 静噪门限）
        self._demod_cfg = (SharedDemodConfig() if SharedDemodConfig is not None
                           else None)
        # 当前运行的流水线对象（连接后建，断开时拆）
        self._pipeline = None  # type: Optional[object]
        # VfoManager 里主/第二 VFO 的 id（None = 未建）
        self._main_vfo_id: Optional[str] = None
        self._vfo2_id: Optional[str] = None
        # vfo_id → DemodWorker 映射（主听切换时切换哪一路写声卡）
        self._vfo_workers: dict = {}
        # 主 VFO 收听频率 vs 硬件中心频率：offset = vfo_center - backend_center
        self._vfo_center_hz: float = 0.0
        self._backend_center_hz: float = 0.0

        # ---- 扫频找台（任务 3）：后台 QThread 跑 sweep_scan，真实后端取 IQ ----
        self._sweep_worker: Optional[_SweepWorker] = None
        self._sweep_progress: Optional[QProgressDialog] = None
        self._sweep_prev_center_hz: float = 0.0

        # ---- baseband 离线回放（任务 4）：QTimer 按采样率把文件 IQ 推给频谱 ----
        self._replay_timer: Optional[QTimer] = None
        self._replay_iq: Optional[np.ndarray] = None
        self._replay_pos: int = 0
        self._replay_sr: float = 0.0
        self._replay_center_hz: float = 0.0
        self._replay_total: int = 0
        self._replay_block: int = 0
        self._replay_resume_poll: bool = False

        # ---- 产品体验集成：状态栏信息密度 / 无设备引导 / 书签 ----
        # 书签内存表（freq_hz, name, mode）；持久化走 FrequencyManager（见下方）
        self._bookmarks: list = []
        # IQ 前端校正链（DC 去除 / IQ 平衡 / 抗混叠抽取）。
        # 懒加载：后端不可用时置 None，所有调用点 try/except 守卫，绝不崩。
        try:
            from mbdsdr_ai.iq_frontend import IQFrontend
            self._iq_frontend = IQFrontend(dc_removal=True, iq_balance=True,
                                           decimation=1)
        except Exception:
            self._iq_frontend = None
        # 频率管理器：内置标准频率库 + 用户自定义书签持久化到 ~/.mbdsdr/bookmarks.json
        try:
            from mbdsdr_ai.frequency_manager import FrequencyManager
            self._freq_mgr = FrequencyManager()
        except Exception:
            self._freq_mgr = None
        # status_panel.update_from_backend 节流计数（每 ~10 帧 ≈ 500ms 刷一次）
        self._backend_status_tick: int = 0
        # 最近一次 IQ 功率估计（dBFS），供状态栏/S-meter 复用
        self._last_dbfs: float = 0.0
        # 无设备时频谱上方引导提示标签（_build_central_widget 中创建）
        self._no_device_hint: Optional[QLabel] = None

        # 构建 UI
        self._build_menu_bar()
        self._build_tool_bar()
        self._build_central_widget()
        self._build_status_bar()

        # 启动即无硬件：所有 SDR 操作控件（调谐/音量/模式/录音/面板实时按钮）置灰
        self._panels_set_sdr_connected(False)

        # 应用默认主题
        self._apply_theme(DEFAULT_THEME)

        # 用读回的运行参数回填控件（专用 setter 内部 blockSignals，不触发 save/后端）。
        # 放在 _panels_set_sdr_connected(False) 之后，避免恢复出的频率显示被清成 "--"。
        self._apply_restored_settings()

        # 加载 GUI 配置（窗口大小、频率、主题等）
        QTimer.singleShot(100, self._load_gui_config)

        # 启动自动选源：枚举到 RTL-SDR 就直接连接，无需用户手选。
        # 延迟到事件循环就绪后执行；无设备时状态栏明确提示，绝不静默走假后端。
        QTimer.singleShot(200, self._auto_enumerate_and_connect)

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
        # 不在启动时自动开串口——用户可能没接 GNSS；等「工具 → 连接 GNSS」手动触发。
        # 定时器照常跑：未连接时 _poll_gnss 把状态显示为“GNSS: 未连接”。
        self._gnss_timer = QTimer(self)
        self._gnss_timer.setInterval(1000)
        self._gnss_timer.timeout.connect(self._poll_gnss)
        self._gnss_timer.start()

        # (E) UTC 时钟：底部状态栏每秒刷新（独立于 GNSS/IQ 定时器，无硬件也走）
        self._utc_timer = QTimer(self)
        self._utc_timer.setInterval(1000)
        self._utc_timer.timeout.connect(self._update_utc_clock)
        self._utc_timer.start()
        self._update_utc_clock()

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
        sweep_action.setEnabled(False)  # 无设备时置灰，连接后启用
        self._sweep_action = sweep_action
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

        # 真实串口 GNSS：手动选择串口号/波特率后打开 NMEA 流
        gnss_action = QAction("连接 GNSS...", self)
        gnss_action.triggered.connect(self._open_gnss_dialog)
        tools_menu.addAction(gnss_action)

        gnss_disconnect_action = QAction("断开 GNSS", self)
        gnss_disconnect_action.triggered.connect(self._disconnect_gnss)
        tools_menu.addAction(gnss_disconnect_action)

        tools_menu.addSeparator()

        # 卫星闭环跟踪：开始/停止/更新 TLE（与"卫星跟踪"页签按钮等价）
        sat_start_action = QAction("开始跟踪选中卫星", self)
        sat_start_action.triggered.connect(self._menu_start_sat_track)
        tools_menu.addAction(sat_start_action)

        sat_stop_action = QAction("停止卫星跟踪", self)
        sat_stop_action.triggered.connect(self._stop_satellite_tracking)
        tools_menu.addAction(sat_stop_action)

        sat_tle_action = QAction("更新卫星 TLE (Celestrak)", self)
        sat_tle_action.triggered.connect(self._on_update_sat_tle)
        tools_menu.addAction(sat_tle_action)

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

        # 连接按钮（未连接时高亮引导用户点击；连接成功后取消高亮）
        self.connect_btn = QPushButton("连接")
        self.connect_btn.setFixedHeight(28)
        self.connect_btn.setStyleSheet(
            "QPushButton { background-color:#C4845C; color:#FFFFFF;"
            " font-weight:600; padding:0 14px; }"
            "QPushButton:hover { background-color:#D4946C; }")
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

        # 多 VFO：新建 VFO 按钮（在当前频谱中心建一个次听 VFO）
        self.new_vfo_btn = QPushButton("+ VFO")
        self.new_vfo_btn.setFixedHeight(28)
        self.new_vfo_btn.setToolTip("在当前频谱中心新建一个 VFO（次听）")
        self.new_vfo_btn.clicked.connect(self._on_new_vfo_button)
        toolbar.addWidget(self.new_vfo_btn)

        # 主听/次听循环切换按钮（Ctrl+Tab）
        self.cycle_vfo_btn = QPushButton("切换主听")
        self.cycle_vfo_btn.setFixedHeight(28)
        self.cycle_vfo_btn.setToolTip("在 VFO 之间循环切换主听（Ctrl+Tab）")
        self.cycle_vfo_btn.clicked.connect(self._on_cycle_vfo)
        toolbar.addWidget(self.cycle_vfo_btn)

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

        # 回放按钮（占位：选择 .iq 录音文件，baseband_io 支持时才真正回放）
        self.replay_btn = QPushButton("回放")
        self.replay_btn.setFixedHeight(28)
        self.replay_btn.setToolTip("选择已录制的 .iq 文件进行离线回放")
        self.replay_btn.clicked.connect(self._replay_recording)
        toolbar.addWidget(self.replay_btn)

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

        # 无设备引导提示横幅（未连接时显示；连接后隐藏；自动枚举失败时由
        # _show_no_rtl_hint 覆写为具体排查信息）
        self._no_device_hint = QLabel(
            "  正在枚举 SDR 设备…若未自动连接，请点击工具栏「连接」或按 Ctrl+C  ")
        self._no_device_hint.setObjectName("hintLabel")
        self._no_device_hint.setAlignment(Qt.AlignCenter)
        self._no_device_hint.setStyleSheet(
            "QLabel { background-color:#F0E8DC; color:#8A6D4A;"
            " padding:4px; border-radius:3px; font-size:9pt; }")
        spectrum_layout.addWidget(self._no_device_hint)

        # 频谱组件
        self.spectrum = create_spectrum_widget(prefer_opengl=False)  # QOpenGLWidget fails to composite on some Windows GPUs; QPainter is equivalent here
        _is_gl = self.spectrum.__class__.__name__ == "SpectrumGLWidget"
        self._render_label.setText("  渲染: " + ("OpenGL" if _is_gl else "软件渲染 (QPainter)") + "  ")
        self.spectrum.freq_changed.connect(self._on_spectrum_freq_changed)
        # 共享同一个 VfoManager：频谱绘制/拖拽 与 DSP 绑定/主听切换 操作同一份数据
        if self._vfo_mgr is not None:
            self.spectrum.vfo_manager = self._vfo_mgr
        # 多 VFO 交互信号接线
        self.spectrum.vfo_created.connect(self._on_vfo_created)
        self.spectrum.vfo_moved.connect(self._on_vfo_moved)
        self.spectrum.vfo_bw_changed.connect(self._on_vfo_bw_changed_ui)
        self.spectrum.vfo_selected.connect(self._on_vfo_selected)
        self.spectrum.vfo_removed.connect(self._on_vfo_removed)
        # Ctrl+Tab 循环切换主听 VFO
        QShortcut(QKeySequence("Ctrl+Tab"), self, self._on_cycle_vfo)
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

        # Tab 6: 卫星闭环自动跟踪（选星->实时 az/el/多普勒->自动调谐 SDR）
        self.sat_track_panel = SatTrackPanel()
        left_tab.addTab(self.sat_track_panel, "卫星跟踪")
        self.sat_track_panel.track_requested.connect(self._start_satellite_tracking)
        self.sat_track_panel.stop_requested.connect(self._stop_satellite_tracking)
        self.sat_track_panel.update_tle_requested.connect(self._on_update_sat_tle)
        self.sat_track_panel.satellite_changed.connect(self._on_sat_combo_changed)
        # 初始化卫星目录（无观测者位置也能列出 TLE 供选择）
        self._init_loop_sat_catalog()

        # Tab 7: 新时空（AMR 实时识别 + 卫星过境时间线 + 授时 + 频率轨道图）
        self.new_spacetime_panel = NewSpacetimePanel()
        left_tab.addTab(self.new_spacetime_panel, "新时空")

        # Tab 8: ADS-B 航路图（1090MHz 飞机实时位置）
        self.adsb_map_panel = AdsbMapPanel()
        left_tab.addTab(self.adsb_map_panel, "ADS-B 航路")

        # 初始化天空视图演示数据
        self._init_sky_view()

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
        # —— Agent A 新增控件信号（用 hasattr 守卫，并行开发时不崩）——
        if hasattr(self.control_panel, "agc_changed"):
            self.control_panel.agc_changed.connect(self._on_agc_changed)
        if hasattr(self.control_panel, "ppm_changed"):
            self.control_panel.ppm_changed.connect(self._on_ppm_changed)
        if hasattr(self.control_panel, "offset_tuning_changed"):
            self.control_panel.offset_tuning_changed.connect(
                self._on_offset_tuning_changed)
        if hasattr(self.control_panel, "vfo_bandwidth_changed"):
            self.control_panel.vfo_bandwidth_changed.connect(
                self._on_vfo_bandwidth_changed)
        if hasattr(self.control_panel, "step_changed"):
            self.control_panel.step_changed.connect(self._on_step_changed)
        if hasattr(self.control_panel, "bookmark_added"):
            self.control_panel.bookmark_added.connect(self._on_bookmark_added)
        if hasattr(self.control_panel, "dc_removal_changed"):
            self.control_panel.dc_removal_changed.connect(
                self._on_dc_removal_changed)
        if hasattr(self.control_panel, "iq_balance_changed"):
            self.control_panel.iq_balance_changed.connect(
                self._on_iq_balance_changed)
        # 从 FrequencyManager 加载书签（内置标准频率库 + 用户自定义持久化书签）
        try:
            if self._freq_mgr is not None and hasattr(
                    self.control_panel, "load_bookmarks_from_list"):
                bm_list = [(b.freq_hz, b.name, b.mode)
                           for b in self._freq_mgr.list() if b.freq_hz > 0]
                self.control_panel.load_bookmarks_from_list(bm_list)
        except Exception:
            pass
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
        """构建状态栏。

        信息密度对标 SDR++ 底部状态栏：连接状态 / 中心频率 / 采样率 / VFO 带宽 /
        增益 / 设备名 / 信号电平(dBFS) / GPS / UTC 时间。
        无后端时所有射频字段显 "--"，绝不展示假数据。
        """
        status_bar = QStatusBar()
        self.setStatusBar(status_bar)

        self.status_conn = QLabel("未连接")
        status_bar.addWidget(self.status_conn)

        status_bar.addWidget(QLabel(" | "))

        self.status_freq = QLabel("频率: --")
        status_bar.addWidget(self.status_freq)

        status_bar.addWidget(QLabel(" | "))

        self.status_sr = QLabel("采样率: --")
        status_bar.addWidget(self.status_sr)

        status_bar.addWidget(QLabel(" | "))

        self.status_bw = QLabel("带宽: --")
        status_bar.addWidget(self.status_bw)

        status_bar.addWidget(QLabel(" | "))

        self.status_gain = QLabel("增益: --")
        status_bar.addWidget(self.status_gain)

        status_bar.addWidget(QLabel(" | "))

        self.status_dev = QLabel("设备: --")
        status_bar.addWidget(self.status_dev)

        status_bar.addWidget(QLabel(" | "))

        self.status_rssi = QLabel("信号: --")
        status_bar.addWidget(self.status_rssi)

        status_bar.addWidget(QLabel(" | "))

        self.status_gps = QLabel("GNSS: 未连接")
        status_bar.addWidget(self.status_gps)

        status_bar.addWidget(QLabel(" | "))

        # 呼号：全局可编辑（placeholder 提示），值持久化到 desktop_settings.json，
        # 同时驱动射频天空图角标显示。空串 = 未设置。
        status_bar.addWidget(QLabel("呼号:"))
        self.callsign_edit = QLineEdit()
        self.callsign_edit.setPlaceholderText("输入你的呼号")
        self.callsign_edit.setMaxLength(16)
        self.callsign_edit.setFixedWidth(130)
        self.callsign_edit.setToolTip("业余无线电呼号（落盘保存，显示在射频天空图）")
        self.callsign_edit.editingFinished.connect(self._on_callsign_edited)
        status_bar.addWidget(self.callsign_edit)

        # 永久右侧：UTC 时钟 + 版本
        self.status_utc = QLabel("UTC: --:--:--")
        self.status_utc.setStyleSheet("color:#5B7B8C;")
        status_bar.addPermanentWidget(self.status_utc)
        status_bar.addPermanentWidget(QLabel(" | "))
        status_bar.addPermanentWidget(QLabel("MBDSDR v0.1 | GPL-3.0"))

    def _update_utc_clock(self):
        """每秒刷新底部状态栏 UTC 时间。"""
        try:
            utc = QDateTime.currentDateTimeUtc()
            self.status_utc.setText(
                "UTC: " + utc.toString("yyyy-MM-dd HH:mm:ss"))
        except Exception:
            pass

    def _update_status_bar(self):
        """统一从后端状态刷新底部状态栏射频字段。

        无后端 / 后端异常时全部显 "--"；绝不保留旧值或编造数值。
        由 _poll_sdr_iq 节流调用（约每 500ms），也可在连接/断开时手动触发。
        """
        backend = self._active_sdr_backend
        if backend is None:
            for lbl, txt in (
                (self.status_freq, "频率: --"),
                (self.status_sr, "采样率: --"),
                (self.status_bw, "带宽: --"),
                (self.status_gain, "增益: --"),
                (self.status_dev, "设备: --"),
                (self.status_rssi, "信号: --"),
            ):
                try:
                    lbl.setText(txt)
                except Exception:
                    pass
            return
        try:
            st = backend.get_status()
        except Exception:
            st = None
        try:
            f_hz = float(backend.get_frequency())
        except Exception:
            f_hz = 0.0
        try:
            sr = float(backend.get_sample_rate())
        except Exception:
            sr = 0.0
        gain = getattr(st, "gain_db", 0.0) if st is not None else 0.0
        dev_name = getattr(getattr(backend, "device", None), "name", "") \
            or backend.__class__.__name__
        try:
            self.status_freq.setText(f"频率: {f_hz / 1e6:.3f} MHz")
        except Exception:
            pass
        try:
            self.status_sr.setText(f"采样率: {sr / 1e6:.3f} MS/s")
        except Exception:
            pass
        try:
            bw_hz = self._vfo_bw
            if bw_hz >= 1000:
                self.status_bw.setText(f"带宽: {bw_hz / 1000:.1f} kHz")
            else:
                self.status_bw.setText(f"带宽: {bw_hz:.0f} Hz")
        except Exception:
            pass
        try:
            self.status_gain.setText(f"增益: {gain:.1f} dB")
        except Exception:
            pass
        try:
            self.status_dev.setText(f"设备: {dev_name}")
        except Exception:
            pass
        # 信号电平：用最近一次 IQ 功率估计（_poll_sdr_iq 写入 _last_dbfs）
        try:
            self.status_rssi.setText(f"信号: {self._last_dbfs:.1f} dBFS")
        except Exception:
            pass

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

        # 用户手动切主题时持久化；启动恢复阶段不回写（避免默认主题覆盖已读回值）
        if not getattr(self, "_restoring_settings", False):
            try:
                self.settings.set("theme", theme_name)
            except Exception:
                pass

    def _on_theme_combo_changed(self, index: int):
        theme_name = self.theme_combo.itemData(index)
        if theme_name:
            self._apply_theme(theme_name)

    # ========================================================================
    # 运行参数持久化（desktop_settings.json）
    # ========================================================================

    def _apply_restored_settings(self):
        """启动时把上次持久化的运行参数回填到控件。

        全部走 control_panel 的专用恢复 setter（内部 blockSignals），不发射变更信号，
        因此不会触发后端下发、也不会立刻写配置。呼号框与天空图角标一并恢复。
        结束后清掉 _restoring_settings 守卫，之后用户改动才会真正落盘。
        """
        s = self.settings
        # 频率 / 模式 / 增益 / 采样率 / AGC
        try:
            self.control_panel.set_frequency_hz(float(s.get("frequency_hz", 98e6)))
        except Exception:
            pass
        try:
            self.control_panel.set_mode(str(s.get("demod_mode", "FM") or "FM"))
        except Exception:
            pass
        try:
            self.control_panel.set_gain(float(s.get("gain_db", 20.0)))
        except Exception:
            pass
        try:
            self.control_panel.set_sample_rate(
                float(s.get("sample_rate_hz", 2_048_000.0)))
        except Exception:
            pass
        try:
            self.control_panel.set_agc(bool(s.get("agc_enabled", True)))
        except Exception:
            pass
        # 带宽：0/缺省 = 随模式自动（set_mode 已套好该模式默认带宽），非 0 才显式覆盖
        bw = float(s.get("bandwidth_hz", 0.0) or 0.0)
        if bw > 0:
            try:
                self.control_panel.set_vfo_bandwidth(bw)
            except Exception:
                pass
        # 呼号：状态栏编辑框 + 天空图角标（blockSignals 避免 editingFinished 回写）
        cs = str(s.get("callsign", "") or "")
        try:
            self.callsign_edit.blockSignals(True)
            self.callsign_edit.setText(cs)
            self.callsign_edit.blockSignals(False)
        except Exception:
            pass
        try:
            self.sky_view.set_callsign(cs)
        except Exception:
            pass
        # 主题（恢复到上次退出时的主题；_restoring_settings 守卫下不回写）
        try:
            self._apply_theme(str(s.get("theme", "default") or "default"))
        except Exception:
            pass
        self._restoring_settings = False

    @Slot()
    def _on_callsign_edited(self):
        """呼号编辑框失焦/回车 → 持久化 + 刷新天空图角标。"""
        cs = self.callsign_edit.text().strip()
        try:
            self.settings.set("callsign", cs)
        except Exception:
            pass
        try:
            self.sky_view.set_callsign(cs)
        except Exception:
            pass

    # ========================================================================
    # MCP 连接管理
    # ========================================================================

    # 设备选择对话框中"远程 rtl_tcp 源"特殊条目的 data 标记
    _RTL_TCP_SPECIAL = "__rtl_tcp_remote__"

    # ------------------------------------------------------------------
    # 启动自动选源：枚举到 RTL 就默认选中连接，无需用户手选。
    # 对标 SDR++ 开箱体验：插上棒启动即收，不弹设备选择对话框。
    # 枚举失败（缺 pyrtlsdr / 缺驱动 / 无棒）时状态栏明确提示，绝不静默走假后端。
    # ------------------------------------------------------------------
    def _auto_enumerate_and_connect(self):
        """启动后自动枚举 SDR 设备；找到 RTL-SDR 则直接连接，否则提示排查。

        只在启动时调用一次（QTimer.singleShot 延迟到事件循环就绪）。
        用户已手动连接 / 已有后端时直接返回，不重复连接。
        任何异常都不崩溃，降级为状态栏提示 + 保持未连接状态。
        """
        if self._active_sdr_backend is not None:
            return
        try:
            from mbdsdr_ai.sdr_backend import (
                enumerate_all_sdr_devices, build_backend_for_device)
            devices = enumerate_all_sdr_devices()
        except Exception as e:
            # 枚举本身抛异常（典型：pyrtlsdr 未安装 / librtlsdr 缺失）
            self._show_no_rtl_hint(f"设备枚举失败：{e}")
            return

        # 优先选 RTL-SDR 原生棒（driver == "rtlsdr"），其次任意真实设备
        rtl_dev = None
        other_dev = None
        for dev in devices:
            if not isinstance(dev, dict):
                continue
            drv = (dev.get("driver") or "").lower()
            if drv == "rtlsdr":
                rtl_dev = dev
                break
            if other_dev is None:
                other_dev = dev

        chosen = rtl_dev or other_dev
        if chosen is None:
            # 真的没设备：明确提示，不弹对话框、不造假
            self._show_no_rtl_hint("未找到 RTL-SDR，检查 Zadig WinUSB 驱动 / pip install pyrtlsdr")
            return

        # 构造后端并连接（默认参数：2.048MS/s、AGC 开、PPM 0、offset 关）
        try:
            backend = build_backend_for_device(chosen)
        except Exception as e:
            self._show_no_rtl_hint(f"构造后端失败：{e}")
            return
        if backend is None:
            self._show_no_rtl_hint("未找到 RTL-SDR，检查 Zadig WinUSB 驱动 / pip install pyrtlsdr")
            return

        ok = self._connect_backend(
            backend,
            sample_rate=2_048_000.0,
            gain=20.0,
            ppm=0,
            agc=True,
            offset_tuning=False,
        )
        if not ok:
            # 连接失败：状态栏已由 _connect_backend 写了错误；补充无设备引导
            self._show_no_rtl_hint(
                "RTL-SDR 连接失败，检查 Zadig WinUSB 驱动 / pip install pyrtlsdr")

    def _show_no_rtl_hint(self, message: str):
        """无设备 / 枚举失败时：状态栏 + 频谱上方横幅同时显示明确提示。

        绝不静默走假后端、绝不显示"已连接"。横幅文字与状态栏一致，
        用户一眼看到排查方向（Zadig 驱动 / pyrtlsdr 库）。
        """
        try:
            self.statusBar().showMessage(message, 8000)
        except Exception:
            pass
        try:
            if self._no_device_hint is not None:
                self._no_device_hint.setText(f"  {message}  ")
                self._no_device_hint.setVisible(True)
        except Exception:
            pass
        try:
            self.status_conn.setText("未连接")
        except Exception:
            pass

    def _connect_backend(self, backend, sample_rate: float = 2_048_000.0,
                         gain: float = 20.0, ppm: int = 0,
                         agc: bool = True, offset_tuning: bool = False) -> bool:
        """通用后端连接流程：connect → 成功则切状态 + 下发参数 + 启动 IQ 流。

        被 _connect_dialog（用户手动选设备）和 _auto_enumerate_and_connect
        （启动自动选 RTL）共用。连接失败返回 False 并写状态栏，不抛异常。
        红线：硬件失败绝不静默切 mock 报 success。
        """
        try:
            ok = backend.connect()
        except Exception as e:
            ok = False
            try:
                backend.status.error = f"connect 异常: {e}"
            except Exception:
                pass

        if not ok:
            raw_err = ""
            try:
                raw_err = backend.get_status().error or ""
            except Exception:
                pass
            friendly = self._humanize_error(raw_err) if raw_err else \
                "设备无响应：请检查 USB 棒、librtlsdr 驱动、以及设备是否被 SDR#/GQRX 占用。"
            self.statusBar().showMessage(f"连接失败：{friendly}", 6000)
            try:
                backend.disconnect()
            except Exception:
                pass
            return False

        # 连接成功：停掉旧 worker，切换到真实后端
        self._worker_manager.stop()
        self._worker = None
        self._active_sdr_backend = backend
        self._panels_set_sdr_connected(True)
        try:
            self.conn_label.setText(f"  状态: {backend.device.name}  ")
            self.conn_label.setStyleSheet("color: #6BA89A; font-weight: 600;")
            self.status_conn.setText(backend.device.name)
        except Exception:
            pass
        self.connect_btn.setEnabled(False)
        self.connect_btn.setStyleSheet("")
        self.disconnect_btn.setEnabled(True)
        try:
            self.spectrum.set_connected(True)
        except Exception:
            pass
        try:
            if self._no_device_hint is not None:
                self._no_device_hint.setVisible(False)
        except Exception:
            pass
        self.statusBar().showMessage(f"已连接 {backend.device.name}", 4000)

        # 下发设备参数（失败仅 warning，不阻断连接）
        try:
            backend.set_sample_rate(float(sample_rate))
        except Exception as e:
            self.statusBar().showMessage(f"采样率应用失败: {e}", 4000)
        # 连接后立即下发默认中心频率（从控制面板读回，否则 98.5MHz）
        default_freq = 98_500_000.0
        try:
            default_freq = float(self.control_panel.get_frequency_hz())
        except Exception:
            default_freq = 98_500_000.0
        try:
            backend.set_frequency(default_freq)
        except Exception as e:
            self.statusBar().showMessage(f"初始频率下发失败: {e}", 4000)
        try:
            backend.set_ppm(int(ppm))
        except Exception:
            pass
        try:
            backend.set_offset_tuning(bool(offset_tuning))
        except Exception:
            pass
        try:
            backend.set_agc(bool(agc))
        except Exception:
            pass
        if not agc:
            try:
                backend.set_gain(float(gain))
            except Exception:
                pass
        self._update_status_bar()
        # 启动真实 IQ 流：频谱/录制/声卡全部接通
        self._start_iq_streams()
        return True

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

        # 远程 rtl_tcp 源：连局域网/公网已跑 rtl_tcp 服务的机器，复用参数区
        combo.addItem("远程 rtl_tcp 源 (host:port)", self._RTL_TCP_SPECIAL)

        def _dev_label(dev):
            """设备下拉框显示名：在枚举 label 后追加来源/驱动标签，
            让 RTL 原生棒与 SoapySDR 总线设备（HackRF/Pluto/Airspy...）一眼可分。"""
            base = dev.get("label") or dev.get("driver", "SDR 设备")
            source = (dev.get("source") or "").lower()
            driver = (dev.get("driver") or "").lower()
            if source == "soapy":
                tag = "SoapySDR"
            elif source == "rtl_native":
                tag = "RTL-SDR 原生"
            elif driver:
                tag = driver
            else:
                tag = ""
            return f"{base} ({tag})" if tag else base

        if devices:
            for dev in devices:
                combo.addItem(_dev_label(dev), dev)
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

        # ---- 设备参数区（对标 SDR++ source.cpp：采样率/增益/PPM/AGC/offset tuning）----
        # 选中真实设备后才可配置；连接成功后立即 apply 到后端。
        param_group = QGroupBox("设备参数（连接后应用）")
        param_form = QFormLayout(param_group)

        sr_combo = QComboBox()
        try:
            from mbdsdr_ai.sdr_backend import RTLSDRBackend
            for _r in RTLSDRBackend.SAMPLE_RATES:
                sr_combo.addItem(f"{_r / 1e6:.3f} MS/s", _r)
            # 默认 2.048 MS/s（与后端 DEFAULT_SAMPLE_RATE 对齐）
            _idx = sr_combo.findData(2_048_000)
            if _idx >= 0:
                sr_combo.setCurrentIndex(_idx)
        except Exception:
            sr_combo.addItem("2.048 MS/s", 2_048_000)
        param_form.addRow("采样率", sr_combo)

        gain_spin = QSpinBox()
        gain_spin.setRange(0, 49)
        gain_spin.setSuffix(" dB")
        gain_spin.setValue(20)
        gain_spin.setToolTip("手动增益（0-49 dB）；开启 AGC 时忽略")
        param_form.addRow("增益", gain_spin)

        ppm_spin = QSpinBox()
        ppm_spin.setRange(-1000, 1000)
        ppm_spin.setSuffix(" ppm")
        ppm_spin.setValue(0)
        ppm_spin.setToolTip("晶振频偏校正（廉价 RTL-SDR 棒典型 20~50 ppm）")
        param_form.addRow("PPM 校正", ppm_spin)

        agc_chk = QCheckBox("启用自动增益 (AGC)")
        agc_chk.setChecked(True)
        param_form.addRow("", agc_chk)

        offset_chk = QCheckBox("Offset Tuning（直流抵消）")
        offset_chk.setChecked(False)
        param_form.addRow("", offset_chk)

        layout.addWidget(param_group)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        def _update_param_state():
            """根据当前选中项启用/置灰参数区（WebSocket/无设备时不可调）。"""
            d = combo.currentData()
            enabled = d is not None
            # 远程 rtl_tcp 源也支持采样率/增益/PPM 参数，参数区保持启用。
            param_group.setEnabled(enabled)
            # PPM 晶振频偏校正只对 RTL-SDR 类设备有意义（廉价棒典型 20~50ppm）；
            # HackRF/Pluto/Airspy/USRP 等 Soapy 前端无 ppm 概念，置灰该输入。
            # 远程 rtl_tcp 源后端本质也是 RTL-SDR 棒，PPM 同样有效，一并启用。
            is_rtl = bool(enabled) and (
                (isinstance(d, dict) and (d.get("driver") or "").lower() == "rtlsdr")
                or d == self._RTL_TCP_SPECIAL
            )
            ppm_spin.setEnabled(is_rtl)

        combo.currentIndexChanged.connect(lambda _i: _update_param_state())
        _update_param_state()

        def _reload_devices():
            """点刷新：重新枚举，保留已选。"""
            try:
                new_devices = enumerate_all_sdr_devices()
            except Exception:
                new_devices = []
            prev = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("远程 rtl_tcp 源 (host:port)", self._RTL_TCP_SPECIAL)
            if new_devices:
                for dev in new_devices:
                    combo.addItem(_dev_label(dev), dev)
            else:
                combo.addItem("未发现 SDR 设备（请接好 USB/安装 SoapySDR 驱动后点刷新）", None)
                combo.model().item(combo.count() - 1).setEnabled(False)
            # 尽量恢复之前的选择
            match = combo.findData(prev)
            combo.setCurrentIndex(match if match >= 0 else 0)
            combo.blockSignals(False)
            _update_param_state()

        refresh_btn.clicked.connect(_reload_devices)

        if dlg.exec() != QDialog.Accepted:
            return

        data = combo.currentData()
        if data is None:
            return  # "未发现设备"项不可选；兜底

        # 收集对话框参数（WebSocket 路径不用）
        chosen_sr = float(sr_combo.currentData() or 2_048_000)
        chosen_gain = float(gain_spin.value())
        chosen_ppm = int(ppm_spin.value())
        chosen_agc = bool(agc_chk.isChecked())
        chosen_offset = bool(offset_chk.isChecked())

        # 远程 rtl_tcp 源：问 host:port，构造 RtlTcpBackend 后走通用连接流程
        if data == self._RTL_TCP_SPECIAL:
            host_port, ok = QInputDialog.getText(
                self, "连接远程 rtl_tcp 源",
                "请输入 host:port（默认端口 1234）:",
                text="127.0.0.1:1234")
            if not ok or not host_port.strip():
                return
            host, port = self._parse_host_port(host_port.strip(), default_port=1234)
            if host is None:
                QMessageBox.critical(
                    self, "连接失败",
                    f"无法解析地址「{host_port.strip()}」，格式应为 host:port"
                    "（如 192.168.1.100:1234）")
                return
            try:
                from mbdsdr_ai.rtltcp_client import RtlTcpBackend
            except ImportError:
                QMessageBox.critical(
                    self, "连接失败",
                    "rtl_tcp 客户端模块未就绪（mbdsdr_ai.rtltcp_client 导入失败）")
                return
            backend = RtlTcpBackend(host, port, ppm=chosen_ppm)
            ok = self._connect_backend(
                backend,
                sample_rate=chosen_sr,
                gain=chosen_gain,
                ppm=chosen_ppm,
                agc=chosen_agc,
                offset_tuning=chosen_offset,
            )
            if not ok:
                QMessageBox.warning(
                    self, "连接失败",
                    f"无法连接远程 rtl_tcp 源 {host}:{port}\n"
                    "请检查地址/端口、网络连通性，以及远端 rtl_tcp 服务是否在运行。")
            return

        # 真实 SDR 设备：构造后端并走通用连接流程（_connect_backend，
        # 与启动自动选源共用同一段 connect + 下发参数 + 启动 IQ 流逻辑）。
        backend = build_backend_for_device(data)
        if backend is None:
            QMessageBox.critical(
                self, "连接失败",
                f"无法为设备「{data.get('label', '?')}」构造后端。")
            return

        ok = self._connect_backend(
            backend,
            sample_rate=chosen_sr,
            gain=chosen_gain,
            ppm=chosen_ppm,
            agc=chosen_agc,
            offset_tuning=chosen_offset,
        )
        if not ok:
            # _connect_backend 已在状态栏写了详细错误；对话框额外弹一次提醒
            QMessageBox.warning(
                self, "连接失败",
                "设备连接失败，请查看状态栏错误详情。\n"
                "常见原因：USB 棒未插 / 驱动未装（Zadig WinUSB）/ 被其他软件占用。")

    @staticmethod
    def _parse_host_port(s: str, default_port: int = 1234):
        """解析 'host:port' 字符串，返回 (host, port)；解析失败返回 (None, None)。
        支持 IPv6 [::1]:1234 格式。"""
        s = s.strip()
        if not s:
            return None, None
        # IPv6 带方括号
        if s.startswith("["):
            end = s.find("]")
            if end < 0:
                return None, None
            host = s[1:end]
            rest = s[end + 1:]
            if rest.startswith(":"):
                try:
                    port = int(rest[1:])
                except ValueError:
                    return None, None
            else:
                port = default_port
            return host, port
        # 普通 host:port（rsplit 一次，兼容 host 不含端口时默认端口）
        if ":" in s:
            parts = s.rsplit(":", 1)
            host = parts[0]
            try:
                port = int(parts[1])
            except ValueError:
                return None, None
        else:
            host = s
            port = default_port
        if not host or port < 1 or port > 65535:
            return None, None
        return host, port

    @staticmethod
    def _humanize_error(raw_err: str) -> str:
        """把后端原始错误信息翻译成用户可理解的人话提示。

        通过错误消息关键词匹配常见故障类别；未命中时回退显示原始信息。
        绝不因为翻译而吞掉错误——原始信息作为附注保留。
        """
        if not raw_err:
            return "设备无响应（请检查 USB 棒、驱动与设备占用）"
        low = raw_err.lower()
        # Python 库缺失（pyrtlsdr 未安装）
        if any(k in low for k in ("no module named", "importerror", "no module")):
            return "未安装 Python 库：pip install pyrtlsdr"
        # 网络连接失败（远程 rtl_tcp 源连不上）。
        # 注意必须排在"设备被占用"分支之前：socket 错误常带 "[Errno 111] Connection refused"，
        # 里面的 "errno" 会被占用分支的裸词误命中。
        if any(k in low for k in ("connection refused", "timeout", "timed out",
                                  "network", "unreachable", "连接失败", "refused")):
            return ("无法连接远程 rtl_tcp 源：请检查 host:port 是否正确、"
                    "网络是否连通、远端 rtl_tcp 服务是否在运行（默认端口 1234）。")
        # 权限不足（udev 规则 / 需要 root / WinUSB 驱动）
        if any(k in low for k in ("permission", "access denied", "errno 13",
                                  "uid", "root", "权限")):
            return ("USB 权限不足：Linux 执行 sudo cp rtl-sdr.rules "
                    "/etc/udev/rules.d/ 并重插；Windows 用 zadig 安装 WinUSB 驱动。")
        # 设备被占用（另一 SDR 软件/rtl_tcp 仍开着）
        if any(k in low for k in ("busy", "resource", "already in use",
                                  "could not open", "errno", "device is used",
                                  "占用")):
            return "设备被其他软件占用，请关闭 SDR#/GQRX/HDSDR 后重试。"
        # 未检测到 RTL-SDR 硬件（USB VID/PID 找不到）
        if any(k in low for k in ("0bda", "2838", "no device", "no devices",
                                  "找不到", "cannot find device")):
            return ("未检测到 RTL-SDR 硬件，请确认 USB 棒已插入并识别"
                    "（设备管理器中应为 RTL2838UHIDIR）。")
        # 驱动缺失（librtlsdr/SoapySDR 未安装）
        if any(k in low for k in ("no such file", "not found", "soapy",
                                  "librtlsdr", "dll", "driver", "驱动",
                                  "未找到", "no backend")):
            return ("未找到设备驱动，请安装 librtlsdr / SoapySDR 驱动"
                    "（Linux: sudo apt install librtlsdr0 soapy-sdk）。")
        # 设备拔插 / USB 断开
        if any(k in low for k in ("disconnect", "lost", "usb", "stall",
                                  "epipe", "no such device", "断开")):
            return "设备已断开，请检查 USB 连接（可换个 USB 口）后点刷新。"
        # 采样率不被支持
        if any(k in low for k in ("sample rate", "samplerate", "invalid rate",
                                  "不支持", "采样率")):
            return "该采样率不被设备支持，已自动切换到最近合法档位。"
        # 其他：保留原始信息
        return f"{raw_err}"

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
        self.connect_btn.setStyleSheet("")
        self.disconnect_btn.setEnabled(True)
        try:
            if self._no_device_hint is not None:
                self._no_device_hint.setVisible(False)
        except Exception:
            pass

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
        控制面板的调谐/音量/模式/预设控件与录音按钮一并禁用，绝不暴露假可控状态；
        频率显示显 "-- MHz"（避免误导用户以为正在接收 98.5）；
        频谱上方显示引导横幅，连接按钮恢复高亮。"""
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
        # 控制面板频率显示：未连接时显 "-- MHz"，连接后由实际调谐更新
        try:
            cp = getattr(self, "control_panel", None)
            if cp is not None:
                if not connected:
                    if hasattr(cp, "freq_display"):
                        cp.freq_display.setText("-- MHz")
                    if hasattr(cp, "freq_input"):
                        cp.freq_input.setText("")
                else:
                    # 连接后若频率显示还是 "--"，填一个占位（后续由真实调谐覆盖）
                    if hasattr(cp, "freq_display") and \
                            cp.freq_display.text().startswith("--"):
                        cp.freq_display.setText("0.000 MHz")
        except Exception:
            pass
        # 工具栏录音按钮（无 SDR 不可录音）
        try:
            if hasattr(self, "record_btn"):
                self.record_btn.setEnabled(bool(connected))
        except Exception:
            pass
        # 扫频菜单 action（无设备置灰）
        try:
            if hasattr(self, "_sweep_action"):
                self._sweep_action.setEnabled(bool(connected))
        except Exception:
            pass
        # 频谱标题栏频率/RSSI：未连接时显 "--"
        try:
            if not connected:
                self.freq_label.setText("-- MHz")
                self.rssi_label.setText("RSSI: --")
        except Exception:
            pass
        # 无设备引导横幅：未连接时显示
        try:
            if self._no_device_hint is not None:
                self._no_device_hint.setVisible(not connected)
        except Exception:
            pass
        # status_panel：连接/断开状态（Agent C 新增接口，hasattr 守卫）
        try:
            sp = getattr(self, "status_panel", None)
            if sp is not None and hasattr(sp, "set_sdr_connected"):
                sp.set_sdr_connected(bool(connected))
        except Exception:
            pass
        # 连接按钮：未连接时恢复橙色高亮引导
        try:
            if not connected:
                self.connect_btn.setStyleSheet(
                    "QPushButton { background-color:#C4845C; color:#FFFFFF;"
                    " font-weight:600; padding:0 14px; }"
                    "QPushButton:hover { background-color:#D4946C; }")
            else:
                self.connect_btn.setStyleSheet("")
        except Exception:
            pass
        # 断连后刷新底部状态栏（全部回落到 "--"）
        if not connected:
            self._last_dbfs = 0.0
            self._update_status_bar()

    def _disconnect(self):
        """断开连接。"""
        # 断开真实 SDR 后端（SoapySDR/RTL-SDR/HackRF）
        # 先停掉回放 / 扫频 / 录制 / IQ 轮询 / 声卡，避免断开后还在读空句柄
        try:
            self._stop_replay()
        except Exception:
            pass
        if self._sweep_worker is not None and self._sweep_worker.isRunning():
            try:
                self._sweep_worker.cancel()
                self._sweep_worker.wait(1500)
            except Exception:
                pass
            self._sweep_worker = None
            if self._sweep_progress is not None:
                try:
                    self._sweep_progress.close()
                except Exception:
                    pass
                self._sweep_progress = None
        self._stop_iq_streams()
        # 断开时重置 IQ 前端校正链状态，避免换源/换频后旧直流残留
        if self._iq_frontend is not None:
            try:
                self._iq_frontend.reset()
            except Exception:
                pass
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
        # VFO 收听频率跟随 → DDC 重算 offset
        self._vfo_center_hz = freq * 1e6
        self._backend_center_hz = freq * 1e6
        self._update_main_vfo_offset()

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
        # VFO 收听频率跟随 → DDC 重算 offset
        self._vfo_center_hz = freq_hz
        self._backend_center_hz = freq_hz
        if self._demod_cfg is not None:
            self._demod_cfg.mode = str(mode).upper()
        self._update_main_vfo_offset()
        # 持久化：频率 + 解调模式（防抖写盘）
        try:
            self.settings.set("frequency_hz", float(freq_hz))
            self.settings.set("demod_mode", str(mode))
        except Exception:
            pass

    @Slot(float)
    def _on_module_tune(self, freq_hz: float):
        """模块面板里改了频率 → 同步到频谱中心频。"""
        self.spectrum.set_center_freq(freq_hz / 1e6)
        self.freq_label.setText(f"{freq_hz / 1e6:.3f} MHz")
        self.status_freq.setText(f"频率: {freq_hz / 1e6:.3f} MHz")
        # VFO 收听频率跟随 → DDC 重算 offset
        self._vfo_center_hz = freq_hz
        self._backend_center_hz = freq_hz
        self._update_main_vfo_offset()

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
        # 持久化：LNA 增益
        try:
            self.settings.set("gain_db", float(db))
        except Exception:
            pass

    def _on_squelch_changed(self, dbfs: float):
        # 本地声卡门控：信号低于此 dBFS 时静音（不下发硬件）
        self._squelch_db = float(dbfs)
        if self._demod_cfg is not None:
            self._demod_cfg.squelch_db = float(dbfs)

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
        """模式切换（WFM/NFM/AM/USB/LSB/CW）：立刻切解调 + 改 VFO 带宽，音频不断。

        - backend.set_demod(mode) 同步下发硬件解调模式；
        - _demod_cfg.mode 更新后，后台解调 worker 下一帧即按新模式解调，
          声卡流不重建、不中断（对标 SDR++ 切模式音频无缝）；
        - VFO 带宽按 MODE_VFO_BANDWIDTH 逐模式套用（CW 500Hz / SSB 2.4k /
          AM 6k / NBFM 12.5k / WFM 180k），推到已绑定的 DDC。
        """
        # 真实 SDR 后端：下发解调模式
        if self._active_sdr_backend is not None:
            try:
                self._active_sdr_backend.set_demod(mode)
            except Exception:
                pass
        # 同步到解调 worker 共享配置（后台线程下一帧生效，音频不中断）
        if self._demod_cfg is not None:
            self._demod_cfg.mode = str(mode).upper()
        # 根据模式套用典型 VFO 带宽（MODE_VFO_BANDWIDTH，逐模式精确值）
        try:
            m = (mode or "FM").upper()
            bw = float(MODE_VFO_BANDWIDTH.get(m, MODE_VFO_BANDWIDTH["FM"]))
            self._vfo_bw = bw
            # 推到已绑定的主/次 VFO DSP（VfoManager.push_bandwidth → set_bandwidth）
            if self._vfo_mgr is not None:
                for vid in (self._main_vfo_id, self._vfo2_id):
                    if vid is not None:
                        try:
                            self._vfo_mgr.push_bandwidth(vid, bw)
                        except Exception:
                            pass
            elif self._vfo is not None and hasattr(self._vfo, "set_bandwidth"):
                self._vfo.set_bandwidth(bw)
            # 频谱 VFO 带宽高亮同步
            try:
                if hasattr(self.spectrum, "set_vfo_bandwidth"):
                    self.spectrum.set_vfo_bandwidth(bw)
            except Exception:
                pass
        except Exception:
            pass
        # 持久化：解调模式
        try:
            self.settings.set("demod_mode", str(mode))
        except Exception:
            pass

    @Slot(float)
    def _on_sample_rate_changed(self, rate_hz: float):
        """控制面板采样率下拉切换 → 下发后端 + 重建流水线（VFO 重采样系数变了）。"""
        # 真实 SDR 后端：真正下发采样率
        if self._active_sdr_backend is not None:
            try:
                self._active_sdr_backend.set_sample_rate(float(rate_hz))
            except Exception:
                pass
        # 更新录制采样率（下一次开始录制时生效）
        self._record_sr = float(rate_hz)
        # VFO 输入采样率变了 → 拆旧流水线重建（新 VFO 的 in_sr/重采样系数正确）
        self._stop_iq_streams()
        self._start_iq_streams()
        # 刷新底部状态栏采样率显示
        self._update_status_bar()
        # 持久化：采样率
        try:
            self.settings.set("sample_rate_hz", float(rate_hz))
        except Exception:
            pass

    # ---- Agent A 新增控件信号槽（无后端时 no-op；所有后端调用 try/except）----

    @Slot(bool)
    def _on_agc_changed(self, enabled: bool):
        """控制面板 AGC 开关 → 后端 set_agc。"""
        # 持久化 AGC 开关（无后端也要存，连接后生效）
        try:
            self.settings.set("agc_enabled", bool(enabled))
        except Exception:
            pass
        if self._active_sdr_backend is None:
            return
        try:
            self._active_sdr_backend.set_agc(bool(enabled))
        except Exception as e:
            self.statusBar().showMessage(f"AGC 设置失败: {e}", 4000)

    @Slot(int)
    def _on_ppm_changed(self, ppm: int):
        """控制面板 PPM 校正 → 后端 set_ppm（RTL-SDR 晶振频偏）。"""
        if self._active_sdr_backend is None:
            return
        try:
            if hasattr(self._active_sdr_backend, "set_ppm"):
                self._active_sdr_backend.set_ppm(int(ppm))
        except Exception as e:
            self.statusBar().showMessage(f"PPM 设置失败: {e}", 4000)

    @Slot(bool)
    def _on_offset_tuning_changed(self, enabled: bool):
        """控制面板 Offset Tuning 开关 → 后端 set_offset_tuning（直流抵消）。"""
        if self._active_sdr_backend is None:
            return
        try:
            if hasattr(self._active_sdr_backend, "set_offset_tuning"):
                self._active_sdr_backend.set_offset_tuning(bool(enabled))
        except Exception as e:
            self.statusBar().showMessage(f"Offset tuning 设置失败: {e}", 4000)

    @Slot(float)
    def _on_vfo_bandwidth_changed(self, bw_hz: float):
        """控制面板 VFO 带宽 → 更新本地 _vfo_bw + 频谱 VFO 带宽（Agent B 接口）。"""
        try:
            self._vfo_bw = float(bw_hz)
        except Exception:
            return
        # VFO 已创建则更新其带宽（经 VfoManager.push_bandwidth → set_bandwidth）
        try:
            if self._vfo_mgr is not None:
                for vid in (self._main_vfo_id, self._vfo2_id):
                    if vid is not None:
                        try:
                            self._vfo_mgr.push_bandwidth(vid, self._vfo_bw)
                        except Exception:
                            pass
            elif self._vfo is not None and hasattr(self._vfo, "set_bandwidth"):
                self._vfo.set_bandwidth(self._vfo_bw)
        except Exception:
            pass
        # 频谱组件 VFO 带宽高亮（Agent B 新增 set_vfo_bandwidth，hasattr 守卫）
        try:
            if hasattr(self.spectrum, "set_vfo_bandwidth"):
                self.spectrum.set_vfo_bandwidth(self._vfo_bw)
        except Exception:
            pass
        # 后端 RF 带宽
        if self._active_sdr_backend is not None:
            try:
                self._active_sdr_backend.set_bandwidth(self._vfo_bw)
            except Exception:
                pass
        self._update_status_bar()
        # 持久化：VFO 带宽
        try:
            self.settings.set("bandwidth_hz", float(bw_hz))
        except Exception:
            pass

    @Slot(float)
    def _on_step_changed(self, step_hz: float):
        """控制面板步进值 → 频谱吸附间隔（snap_interval）。"""
        try:
            if hasattr(self.spectrum, "set_snap_interval"):
                self.spectrum.set_snap_interval(float(step_hz))
        except Exception:
            pass

    @Slot(float, str, str)
    def _on_bookmark_added(self, freq_hz: float, name: str, mode: str):
        """控制面板书签按钮 → 内存保存 + FrequencyManager 持久化。"""
        try:
            self._bookmarks.append((float(freq_hz), str(name), str(mode)))
            if self._freq_mgr is not None:
                try:
                    self._freq_mgr.add(
                        str(name), float(freq_hz), mode=str(mode),
                        category="自定义")
                except Exception:
                    pass
            self.statusBar().showMessage(
                f"已收藏 {float(freq_hz) / 1e6:.3f} MHz {name}", 3000)
        except Exception:
            pass

    @Slot(bool)
    def _on_dc_removal_changed(self, enabled: bool):
        """控制面板 DC 去除开关 → IQFrontend.set_dc_removal。"""
        if self._iq_frontend is not None:
            try:
                self._iq_frontend.set_dc_removal(bool(enabled))
            except Exception:
                pass

    @Slot(bool)
    def _on_iq_balance_changed(self, enabled: bool):
        """控制面板 IQ 平衡开关 → IQFrontend.set_iq_balance。"""
        if self._iq_frontend is not None:
            try:
                self._iq_frontend.set_iq_balance(bool(enabled))
            except Exception:
                pass

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
        # VFO 收听频率跟随 → DDC 重算 offset
        self._vfo_center_hz = freq * 1e6
        self._backend_center_hz = freq * 1e6
        self._update_main_vfo_offset()

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
        """真实 SDR 连接成功后：打开声卡 + 启动多线程接收流水线。

        无后端 / 无 read_samples / 无声卡设备时全部安全降级，绝不崩溃。
        旧的 UI 线程 QTimer 轮询路径不再启动——FFT 与解调全部移到后台线程。
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
        # (A) 启动多线程流水线：IQ reader → splitter → FFT/demod/record 各 worker
        self._build_pipeline()

    def _build_pipeline(self):
        """组装并启动接收流水线（见 receive_pipeline.ReceivePipeline）。

        - 无后端 / 无 read_samples / 流水线模块不可用：直接返回，不启动任何线程，
          频谱保持"未连接"，状态栏显 "--"。
        - 创建主 VFO + 第二 VFO 的 dsp.VFO，经 VfoManager.bind_dsp 绑定，
          offset 由 _update_main_vfo_offset 真搬频。
        """
        read, sr = self._active_read_samples()
        if read is None:
            return
        # IQ 前端校正链（DC 去除 / IQ 平衡 / 抗混叠抽取）：在 reader 线程中逐块处理
        if self._iq_frontend is not None and read is not None:
            _raw_read = read
            _fe = self._iq_frontend

            def _iq_frontend_read(n, _raw=_raw_read, _f=_fe):
                iq = _raw(n)
                if iq is not None:
                    try:
                        iq = _f.process(iq)
                    except Exception:
                        pass
                return iq
            read = _iq_frontend_read
        if self._pipeline is not None:
            return
        if ReceivePipeline is None or self._demod_cfg is None:
            return
        be = self._active_sdr_backend
        try:
            self._backend_center_hz = float(be.get_frequency())
        except Exception:
            self._backend_center_hz = 0.0
        if self._vfo_center_hz <= 0.0:
            self._vfo_center_hz = self._backend_center_hz

        # 建两个 dsp.VFO（DDC：NCO 搬频 → 有理重采样 → Nuttall LPF）
        from mbdsdr_ai.dsp import VFO
        try:
            main_vfo = VFO(in_samplerate=sr, out_samplerate=self._vfo_out_sr,
                           bandwidth=self._vfo_bw, offset=0.0)
            vfo2 = VFO(in_samplerate=sr, out_samplerate=self._vfo_out_sr,
                       bandwidth=self._vfo_bw, offset=0.0)
        except Exception:
            main_vfo, vfo2 = None, None
        self._vfo = main_vfo          # 兼容旧引用（_on_mode_changed 等仍可触达）
        self._vfo_in_sr = float(sr)

        # VfoManager 登记 + bind_dsp（对照 SDR++ vfo_manager.cpp:16,30-49）
        # vfo_id → DemodWorker 映射：主听切换时按 id 切换哪一路写声卡。
        self._vfo_workers = {}
        if self._vfo_mgr is not None:
            s1 = self._vfo_mgr.add(self._vfo_center_hz, self._vfo_bw,
                                   self._demod_cfg.mode)
            self._vfo_mgr.bind_dsp(s1.vfo_id, main_vfo,
                                   output_stream=self._audio_player)
            self._main_vfo_id = s1.vfo_id
            s2 = self._vfo_mgr.add(self._vfo_center_hz, self._vfo_bw,
                                   self._demod_cfg.mode)
            self._vfo_mgr.bind_dsp(s2.vfo_id, vfo2, output_stream=None)
            self._vfo2_id = s2.vfo_id
            # 首建：s1（主 VFO）为默认主听
            self._vfo_mgr.set_active_context(s1, temporary=False)

        # 同步当前解调模式 / 静噪到 worker 共享配置
        try:
            self._demod_cfg.squelch_db = self._squelch_db
            be_mode = getattr(getattr(be, "status", None), "demod_mode", None)
            if be_mode:
                self._demod_cfg.mode = str(be_mode).upper()
        except Exception:
            pass

        pipe = ReceivePipeline(
            read_fn=read, sr=sr,
            gen=self.spectrum.generator,
            center_getter=self._safe_center_hz,
            main_vfo_dsp=main_vfo, vfo2_dsp=vfo2,
            player=self._audio_player, config=self._demod_cfg,
            on_record_iq=self._on_pipeline_record_iq,
        )
        pipe.frame_ready.connect(self._on_pipeline_frame_ready)
        pipe.start()
        self._pipeline = pipe
        # 登记 vfo_id → demod worker（主听切换时切换哪一路写声卡）
        self._vfo_workers = {
            self._main_vfo_id: pipe._demod1,
            self._vfo2_id: pipe._demod2,
        }
        # 首帧：把 offset（= vfo_center - backend_center）推给 DDC
        self._update_main_vfo_offset()

    def _safe_center_hz(self) -> float:
        """后台 FFT worker 回读硬件中心频率（失败回落到缓存值，绝不崩）。"""
        try:
            return float(self._active_sdr_backend.get_frequency())
        except Exception:
            return self._backend_center_hz

    def _update_main_vfo_offset(self):
        """把每个已绑定 DSP 的 VFO 收听频率搬到各自 DDC：offset = vfo_center - backend_center。

        调用链：UI 调谐事件 → _on_tune_* → 本方法 →
        VfoManager.push_offset(vfo_id, offset) → dsp_vfo.set_offset(offset)
        → VFO._rebuild_xlator() 重算 _d_theta（dsp.py:980），process() 真搬频。
        多 VFO 时每个 DDC 各自搬频到自己的中心频率。
        """
        if self._vfo_mgr is None:
            return
        for v in self._vfo_mgr.list_all():
            try:
                offset = float(v.center_hz) - self._backend_center_hz
                self._vfo_mgr.push_offset(v.vfo_id, offset)
            except Exception:
                pass

    # ------------------------------------------------------------------ 多 VFO 接线
    def _on_new_vfo_button(self):
        """工具栏「+ VFO」：在当前频谱中心新建一个次听 VFO。

        带宽取当前主 VFO 带宽（_vfo_bw，默认 12.5 kHz）。新建后设为主听。
        若流水线已运行且还有空闲 DDC 通道（当前共 2 路 demod worker），
        则把新 VFO 绑到对应 worker；超出 2 路的 VFO 只在频谱上显示，
        次听 VFO 解调待后续流水线扩展为动态通道。
        """
        mgr = self._vfo_mgr
        if mgr is None:
            return
        center = self.spectrum.generator.center_freq_hz
        mode = self._demod_cfg.mode if self._demod_cfg else "FM"
        v = mgr.add(center, self._vfo_bw, mode, inherit_last=False)
        mgr.set_active_context(v, temporary=False)
        self.statusBar().showMessage(
            f"新建 VFO {v.vfo_id} @ {center/1e6:.3f} MHz", 3000)
        # 新建后立即把 offset 推给已绑定 DSP（若有）
        self._update_main_vfo_offset()
        self.spectrum._plot.update()

    def _on_cycle_vfo(self):
        """Ctrl+Tab / 「切换主听」按钮：在 VFO 之间循环切到下一个主听。"""
        mgr = self._vfo_mgr
        if mgr is None or not mgr.list_all():
            return
        nxt = mgr.next()
        if nxt is None:
            return
        self._apply_primary_vfo(nxt.vfo_id)

    def _on_vfo_created(self, vfo_id: str, center_hz: float, bw_hz: float):
        """频谱 Shift 拖拽新建 VFO：尝试绑 DSP 通道（超出 2 路则仅显示）。"""
        # Shift 拖拽新建的 VFO 已由频谱组件 add() 进 mgr；这里补 offset 推送
        self._update_main_vfo_offset()
        self.statusBar().showMessage(
            f"新建 VFO {vfo_id} @ {center_hz/1e6:.3f} MHz", 3000)

    def _on_vfo_moved(self, vfo_id: str, center_hz: float):
        """VFO 被拖拽移动：更新该 VFO 的 DDC offset。"""
        mgr = self._vfo_mgr
        if mgr is None:
            return
        v = mgr.get(vfo_id)
        if v is None:
            return
        try:
            offset = float(center_hz) - self._backend_center_hz
            mgr.push_offset(vfo_id, offset)
        except Exception:
            pass

    def _on_vfo_bw_changed_ui(self, vfo_id: str, bw_hz: float):
        """VFO 边缘拖拽改带宽：推到该 VFO 的 DDC。"""
        mgr = self._vfo_mgr
        if mgr is None:
            return
        try:
            mgr.push_bandwidth(vfo_id, float(bw_hz))
        except Exception:
            pass

    def _on_vfo_selected(self, vfo_id: str):
        """点击 VFO 矩形 → 设为主听，切换声卡输出到该 VFO 的解调 worker。"""
        self._apply_primary_vfo(vfo_id)

    def _apply_primary_vfo(self, vfo_id: str):
        """把 vfo_id 设为主听：该路 demod 写声卡，其余静音。更新控制面板。"""
        mgr = self._vfo_mgr
        if mgr is not None:
            mgr.set_primary(vfo_id)
        # 切换声卡输出：在 vfo_id → worker 映射里，主听那路 audio_enabled=True
        workers = getattr(self, "_vfo_workers", {}) or {}
        for vid, worker in workers.items():
            try:
                worker.set_audio_enabled(vid == vfo_id)
            except Exception:
                pass
        # 更新控制面板频率/模式显示为主听 VFO 的参数
        v = mgr.get(vfo_id) if mgr is not None else None
        if v is not None:
            try:
                self.control_panel.set_freq_fm(v.center_hz / 1e6)
                self._vfo_center_hz = float(v.center_hz)
                self._update_main_vfo_offset()
            except Exception:
                pass
        tag = "主听" if vfo_id in workers else "次听-未解调"
        self.statusBar().showMessage(
            f"主听切换 → {vfo_id}（{tag}）", 3000)

    def _on_vfo_removed(self, vfo_id: str):
        """频谱右键删除 VFO：清掉 worker 映射并切主听到剩余 VFO。"""
        workers = getattr(self, "_vfo_workers", {}) or {}
        workers.pop(vfo_id, None)
        # 被删的若是主听，_apply_primary_vfo 会切到 mgr.current（频谱已处理）
        if self._vfo_mgr is not None:
            cur = self._vfo_mgr.active_vfo_id
            if cur is not None:
                self._apply_primary_vfo(cur)
        self.statusBar().showMessage(f"已删除 VFO {vfo_id}", 3000)

    @Slot(object, float)
    def _on_pipeline_frame_ready(self, iq: np.ndarray, sr: float):
        """FFT worker 算完一帧后跨线程回到 UI：只做轻量 repaint + tap。

        重活（窗×FFT×fftshift×dBFS×IIR）已在 FftWorker 线程完成，这里绝不做 FFT。
        """
        # 频谱 repaint（generator 内部状态已被 worker 更新）
        try:
            self.spectrum._refresh_status_labels()
            self.spectrum._plot.update()
        except Exception:
            pass

        # 多普勒定轨面板 tap：feed_iq 碰 Qt 控件（定时器/刷新门），必须在 UI 线程
        try:
            dp = getattr(self, "doppler_panel", None)
            if dp is not None and hasattr(dp, "feed_iq"):
                dp.feed_iq(iq, sr)
        except Exception:
            pass

        # RSSI（从 IQ 功率估计，16384 样本开销极小）
        self._update_rssi_from_iq(iq)

        # 节流：约每 10 帧（500ms）回读后端状态刷新底部状态栏 + status_panel
        self._backend_status_tick += 1
        if self._backend_status_tick >= 10:
            self._backend_status_tick = 0
            self._update_status_bar()
            try:
                sp = getattr(self, "status_panel", None)
                if sp is not None and hasattr(sp, "update_from_backend"):
                    sp.update_from_backend(self._active_sdr_backend)
            except Exception:
                pass

    def _on_pipeline_record_iq(self, iq: np.ndarray):
        """录制 tap：RecordTap 线程把每帧 IQ（已是独立副本）交给 UI 累积。"""
        if self._recording and self._record_iq_buffer is not None:
            try:
                self._record_iq_buffer.append(iq)
            except Exception:
                pass

    def _stop_iq_streams(self):
        """断开时：停流水线全部线程 + 清 VfoManager 绑定 + 关声卡 + 停 IQ 轮询。

        干净关闭对标 SDR++ main.cpp stop()：先停生产者线程再关设备，
        确保不残留读线程 / 不占着 USB 棒。所有操作 try/except，绝不崩。
        """
        # 停 UI 线程 IQ 轮询定时器（流水线模式下未启动，但防御性停止）
        try:
            if self._iq_poll_timer is not None:
                self._iq_poll_timer.stop()
        except Exception:
            pass
        # 停流水线（reader + splitter + FFT/demod/record workers），join 回收
        if self._pipeline is not None:
            try:
                self._pipeline.shutdown()
            except Exception:
                pass
            self._pipeline = None
        # 清 vfo_id → worker 映射
        self._vfo_workers = {}
        # 清 VfoManager 里的运行时绑定（全部 VFO，含用户新建的）
        if self._vfo_mgr is not None:
            for v in list(self._vfo_mgr.list_all()):
                try:
                    self._vfo_mgr.remove(v.vfo_id)
                except Exception:
                    pass
        self._main_vfo_id = None
        self._vfo2_id = None
        self._vfo = None
        self._vfo_in_sr = 0.0
        # 关声卡
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

        # 多普勒定轨面板：tap 同一块 IQ（不另起 read_samples，避免抢环形缓冲）。
        # 面板内部自行缓冲 ~2s 并按 1s 周期做窄带 FFT 提取频偏观测；未选实时
        # 模式/未连接时 feed_iq 内部直接丢弃，零开销。
        try:
            dp = getattr(self, "doppler_panel", None)
            if dp is not None and hasattr(dp, "feed_iq"):
                dp.feed_iq(iq, sr)
        except Exception:
            pass

        # 更新 RSSI 显示（从后端状态或 IQ 功率估计）
        self._update_rssi_from_iq(iq)

        # 节流：约每 10 帧（500ms）从后端回读一次状态刷新底部状态栏 + status_panel
        self._backend_status_tick += 1
        if self._backend_status_tick >= 10:
            self._backend_status_tick = 0
            self._update_status_bar()
            # Agent C 新增接口：从后端读取所有字段更新状态面板（hasattr 守卫）
            try:
                sp = getattr(self, "status_panel", None)
                if sp is not None and hasattr(sp, "update_from_backend"):
                    sp.update_from_backend(self._active_sdr_backend)
            except Exception:
                pass

        # (B) 录制中：累积到 buffer
        if self._recording and self._record_iq_buffer is not None:
            try:
                self._record_iq_buffer.append(iq.copy())
            except Exception:
                pass

        # (C) FM 鉴频解调 → 声卡输出（带静噪门控）
        self._demod_and_play(iq, sr)

    def _compute_vfo_rssi_dbfs(self) -> Optional[float]:
        """从频谱 FFT 数据计算当前 VFO 带宽内的信号功率 (dBFS)。

        RSSI 真从 VFO 带宽内 FFT bin 功率求和：取 generator.spectrum（dBFS/bin）中
        [vfo_center - bw/2, vfo_center + bw/2] 对应的 bin 区间，逐 bin dBFS→线性功率
        求和→再转 dBFS，得到"当前 VFO 收听带宽内"的总功率。这不是全带宽平均，
        也不是峰值，而是 VFO 信道带宽内的积分功率。

        无 FFT 数据 / FFT 全 NaN / VFO 中心超出当前 FFT 带宽时返回 None，
        由调用方 _update_rssi_from_iq 回退到全带宽 IQ 平均功率。绝不造假值。
        """
        try:
            gen = getattr(self.spectrum, "generator", None)
            if gen is None:
                return None
            spec = getattr(gen, "spectrum", None)
            if spec is None or not isinstance(spec, np.ndarray) or spec.size == 0:
                return None
            # 全部 NaN = 无真实数据
            if np.all(np.isnan(spec)):
                return None
            num_bins = int(gen.num_bins)
            sr = float(gen.sample_rate_hz)
            cf = float(gen.center_freq_hz)
            if sr <= 0 or num_bins <= 0:
                return None
            # VFO 中心频率与带宽
            vfo_center = float(self._vfo_center_hz) if self._vfo_center_hz > 0 else cf
            bw = float(self._vfo_bw) if self._vfo_bw > 0 else 12_500.0
            # VFO 完全落在当前 FFT 带宽外？回退全带宽
            f_lo = cf - sr / 2.0
            f_hi = cf + sr / 2.0
            v_lo = vfo_center - bw / 2.0
            v_hi = vfo_center + bw / 2.0
            if v_hi < f_lo or v_lo > f_hi:
                return None
            # bin 索引：f = f_lo + i * (sr / num_bins)
            bin_width = sr / num_bins
            i_lo = int(max(0, (v_lo - f_lo) / bin_width))
            i_hi = int(min(num_bins - 1, (v_hi - f_lo) / bin_width))
            if i_hi < i_lo:
                return None
            band = spec[i_lo:i_hi + 1]
            band = band[~np.isnan(band)]
            if band.size == 0:
                return None
            # dBFS/bin → 线性功率求和 → dBFS（带内总功率）
            linear = np.power(10.0, band / 10.0)
            total_p = float(np.sum(linear))
            if total_p <= 0:
                return None
            return 10.0 * math.log10(total_p + 1e-12)
        except Exception:
            return None

    def _update_rssi_from_iq(self, iq: np.ndarray):
        """RSSI/信号强度：状态栏实时显示当前 VFO 处信号强度 dBFS。

        计算优先级：
          1. VFO 带宽内 FFT 功率（_compute_vfo_rssi_dbfs，最贴近"当前 VFO 处"）；
          2. 全带宽真 IQ 平均功率（np.mean(|iq|^2)，兜底）。
        两者均从真实 IQ / 真实 FFT 算出，绝不造假。
        写入 self._last_dbfs 供 _update_status_bar 复用；推送 S-meter。
        """
        dbfs = None
        # 优先：VFO 带宽内 FFT 功率
        try:
            dbfs = self._compute_vfo_rssi_dbfs()
        except Exception:
            dbfs = None
        # 兜底：全带宽真 IQ 平均功率
        if dbfs is None:
            try:
                p = float(np.mean(np.abs(iq) ** 2))
                dbfs = 10.0 * math.log10(p + 1e-12)
            except Exception:
                return
        try:
            self._last_dbfs = float(dbfs)
            txt = f"RSSI: {dbfs:.1f} dBFS"
            self.rssi_label.setText(txt)
            self.status_rssi.setText(f"信号: {dbfs:.1f} dBFS")
            # 推送 S-meter（hasattr 守卫）
            try:
                cp = getattr(self, "control_panel", None)
                if cp is not None and hasattr(cp, "update_signal_level"):
                    cp.update_signal_level(dbfs)
            except Exception:
                pass
            try:
                sp = getattr(self, "status_panel", None)
                if sp is not None and hasattr(sp, "update_signal_level"):
                    sp.update_signal_level(dbfs)
            except Exception:
                pass
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
        """按当前解调模式真正解调 → 静噪门控 → 写声卡。

        接收链对标 SDR++/gqrx：
          - VFO 路径（路径 A）：先 VFO 信道变频+重采样+低通（repos/sdrpp/core/src/dsp/
            channel/rx_vfo.h:89），VFO 输出已是 48k complex IQ，再按模式解调，结果直接写
            48k 声卡，不再重采样。
          - WFM 广播例外：最大频偏 75kHz，需 ≥200kHz 带宽，VFO bw=12k 太窄，故跳过 VFO，
            直接对全带宽 IQ 跑 wfm_broadcast_demod（内部已鉴频→重采样到48k→去加重→低通→归一化，
            对标 repos/sdrpp/core/src/module_demod/analog_module.cpp 的 WFM 链）。
          - 回退路径（路径 B）：VFO 不可用时对原始全带宽 IQ 按模式解调，再用
            dsp.audio_to_playback 按模式音频带宽低通 + 抗混叠重采样到 48k。
        无 AudioPlayer / 无设备 / 被静噪门住 / RAW/DIG 模式时全部安全 no-op。
        """
        player = self._audio_player
        if player is None or not getattr(player, "available", False):
            return
        if iq.size < 16:
            return

        # 1) 读取当前解调模式：优先 UI 共享配置 _demod_cfg.mode（用户在控制面板选的模式，
        #    回放期间后端可能仍连着，但回放应按用户选的模式解调，而不是后端 status 里的
        #    硬件解调模式）；回退到 backend.status.demod_mode；再回退到 FM。绝不崩。
        try:
            mode = None
            if self._demod_cfg is not None:
                m = getattr(self._demod_cfg, "mode", None)
                if m:
                    mode = str(m).upper()
            if not mode:
                be = self._active_sdr_backend
                if be is not None and getattr(be, "status", None) is not None:
                    m = getattr(be.status, "demod_mode", None)
                    if m:
                        mode = str(m).upper()
            if not mode:
                mode = "FM"
        except Exception:
            mode = "FM"
        # 调试/测试断言用：记录本次实际使用的解调模式
        self._last_demod_mode = mode

        # RAW/DIG：数字模式不解调，不输出音频
        if mode in ("RAW", "DIG"):
            return

        # 静噪门控：信号太弱时渐出静音（不再硬 return 导致咔哒）。
        # 对照 SDR++ power_squelch.h 门在 IF 域 + 后级 LPF 平滑；我们在
        # 音频域用 AudioPlayer 的 5ms ramp 等效实现（audio_out.py _callback）。
        try:
            p = float(np.mean(np.abs(iq) ** 2))
            dbfs = 10.0 * math.log10(p + 1e-12)
            if dbfs < self._squelch_db:
                player.set_muted(True)
                return
            else:
                player.set_muted(False)
        except Exception:
            return

        from mbdsdr_ai import dsp as _dsp

        # 2) WFM 广播特殊处理：跳过 VFO，直接对原始全带宽 IQ 鉴频（内部已重采样到 48k）
        if mode == "WFM":
            try:
                audio = _dsp.wfm_broadcast_demod(iq, sample_rate=sr, audio_sr=48000)
                if audio is not None and audio.size > 0:
                    player.write(audio)
            except Exception:
                pass
            return

        # ---- 路径 A：VFO 信道滤波后按模式解调（VFO 输出已是 48k complex IQ，直接写声卡）----
        vfo_ok = False
        try:
            if self._ensure_vfo(sr):
                vfo_out = self._vfo.process(iq)
                if vfo_out is not None and len(vfo_out) > 16:
                    audio = self._demod_at_48k(_dsp, mode, vfo_out)
                    if audio is not None and audio.size > 0:
                        player.write(audio)
                        vfo_ok = True
        except Exception:
            vfo_ok = False

        if vfo_ok:
            return

        # ---- 路径 B（回退）：直接对全带宽 IQ 按模式解调 + audio_to_playback 重采样到 48k ----
        try:
            audio = self._demod_at_native_sr(_dsp, mode, iq, sr)
            if audio is None or audio.size == 0:
                return
            player.write(audio)
        except Exception:
            pass

    @staticmethod
    def _demod_at_48k(dsp, mode: str, vfo_out: np.ndarray) -> np.ndarray:
        """VFO 输出已是 48k complex IQ，按模式解调后直接可写声卡（不再重采样）。

        模式→函数映射：
          NFM  -> dsp.fm_demod(vfo_out, deviation=5000.0,  sample_rate=48000)
          FM   -> dsp.fm_demod(vfo_out, deviation=75000.0, sample_rate=48000)
          AM   -> dsp.am_demod(vfo_out)
          USB  -> dsp.ssb_demod(vfo_out, mode="USB", sample_rate=48000)
          LSB  -> dsp.ssb_demod(vfo_out, mode="LSB", sample_rate=48000)
          CW   -> dsp.cw_demod(vfo_out, tone_freq=700.0, sample_rate=48000)
        """
        if mode == "NFM":
            return dsp.fm_demod(vfo_out, deviation=5000.0, sample_rate=48000)
        if mode == "FM":
            return dsp.fm_demod(vfo_out, deviation=75000.0, sample_rate=48000)
        if mode == "AM":
            return dsp.am_demod(vfo_out)
        if mode == "USB":
            return dsp.ssb_demod(vfo_out, mode="USB", sample_rate=48000)
        if mode == "LSB":
            return dsp.ssb_demod(vfo_out, mode="LSB", sample_rate=48000)
        if mode == "CW":
            return dsp.cw_demod(vfo_out, tone_freq=700.0, sample_rate=48000)
        # 兜底（RAW/DIG/WFM 不应走到这里）
        return dsp.fm_demod(vfo_out, deviation=75000.0, sample_rate=48000)

    @staticmethod
    def _demod_at_native_sr(dsp, mode: str, iq: np.ndarray, sr: float) -> np.ndarray:
        """回退路径：对原始全带宽 IQ（采样率 sr）按模式解调，再按模式音频带宽重采样到 48k。

        各模式音频带宽 cutoff_hz（对应 audio_to_playback 的低通截止）：
          WFM=15000, NFM=3000, FM=15000, AM=4000, USB=3000, LSB=3000, CW=1000
        """
        cutoff = {
            "WFM": 15000.0,
            "NFM": 3000.0,
            "FM": 15000.0,
            "AM": 4000.0,
            "USB": 3000.0,
            "LSB": 3000.0,
            "CW": 1000.0,
        }.get(mode, 4000.0)
        if mode == "NFM":
            audio = dsp.fm_demod(iq, deviation=5000.0, sample_rate=sr)
        elif mode == "FM":
            audio = dsp.fm_demod(iq, deviation=75000.0, sample_rate=sr)
        elif mode == "AM":
            audio = dsp.am_demod(iq)
        elif mode == "USB":
            audio = dsp.ssb_demod(iq, mode="USB", sample_rate=sr)
        elif mode == "LSB":
            audio = dsp.ssb_demod(iq, mode="LSB", sample_rate=sr)
        elif mode == "CW":
            audio = dsp.cw_demod(iq, tone_freq=700.0, sample_rate=sr)
        else:
            audio = dsp.fm_demod(iq, deviation=75000.0, sample_rate=sr)
        return dsp.audio_to_playback(audio, in_sr=sr, cutoff_hz=cutoff, out_sr=48000)

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
        """启动宽带扫频找台（真实后端步进调谐 → 拼接 PSD → 提取活动信号）。

        对标 SDR++ Frequency Scanner：弹出参数对话框（起止频率/步进/驻留），
        在 QThread 里用 sweep_scan() 步进调谐真实 SDR 并逐段取 IQ；完成后在
        频谱上标注活动频点，并把峰值列表（频率/带宽/峰值 dB）输出到 AI 面板。
        无本地 SDR 后端（仅有 WebSocket worker）或取消时绝不造假结果。
        """
        backend = self._active_sdr_backend
        # 扫频需要本地后端的 set_frequency + read_samples；WebSocket worker
        # 没有本地 IQ 流，无法步进取数。
        if backend is None or not callable(getattr(backend, "read_samples", None)) \
                or not callable(getattr(backend, "set_frequency", None)):
            self.statusBar().showMessage(
                "扫频需要连接本地 SDR 硬件（请先用工具栏「连接」选 USB 设备）", 5000)
            QMessageBox.information(
                self, "无法扫频",
                "扫频找台需要真实 SDR 硬件步进调谐并逐段接收 IQ。\n"
                "ai-sdr Mini WebSocket 模式不暴露本地 IQ 流，暂不支持扫频。\n"
                "请连接 RTL-SDR/HackRF/SoapySDR 等 USB 设备后再试。")
            return
        if self._sweep_worker is not None and self._sweep_worker.isRunning():
            return  # 已经在扫

        # ---- 扫频参数对话框（对标 SDR++ scanner 的 start/end/step/dwell）----
        try:
            sr = float(backend.get_sample_rate())
        except Exception:
            sr = 2_400_000.0
        dlg = QDialog(self)
        dlg.setWindowTitle("扫频找台")
        dlg.setMinimumWidth(380)
        form = QFormLayout(dlg)

        start_sp = QDoubleSpinBox(); start_sp.setRange(10.0, 6000.0)
        start_sp.setDecimals(3); start_sp.setSuffix(" MHz"); start_sp.setValue(87.0)
        stop_sp = QDoubleSpinBox(); stop_sp.setRange(10.0, 6000.0)
        stop_sp.setDecimals(3); stop_sp.setSuffix(" MHz"); stop_sp.setValue(108.0)
        step_sp = QDoubleSpinBox(); step_sp.setRange(0.01, 20.0)
        step_sp.setDecimals(3); step_sp.setSuffix(" MHz")
        step_sp.setValue(round(sr * 0.5 / 1e6, 3))  # overlap 0.5 对应的步进
        dwell_sp = QSpinBox(); dwell_sp.setRange(4096, 65536)
        dwell_sp.setSingleStep(4096); dwell_sp.setValue(16384)
        dwell_sp.setToolTip("每个调谐段采集的样本数（驻留时间 = dwell/sr）")

        form.addRow("起始频率", start_sp)
        form.addRow("终止频率", stop_sp)
        form.addRow("步进宽度", step_sp)
        form.addRow("驻留样本数", dwell_sp)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec() != QDialog.Accepted:
            return
        f0 = start_sp.value() * 1e6
        f1 = stop_sp.value() * 1e6
        step = step_sp.value() * 1e6
        dwell = dwell_sp.value()
        if f1 <= f0:
            QMessageBox.warning(self, "参数错误", "终止频率必须大于起始频率。")
            return

        # ---- 暂停统一 IQ 轮询：扫频线程独占 set_frequency/read_samples，
        # 否则两个消费者抢同一环形缓冲，且主循环改频会打乱步进。
        try:
            self._sweep_prev_center_hz = float(backend.get_frequency())
        except Exception:
            self._sweep_prev_center_hz = f0
        self._stop_iq_streams()

        worker = _SweepWorker(backend, f0, f1, sr, step, 0.5, dwell, parent=self)
        self._sweep_worker = worker
        prog = QProgressDialog("扫频中...", "取消", 0, 0, self)
        prog.setWindowTitle("扫频找台")
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.setValue(0)
        self._sweep_progress = prog

        worker.progress.connect(
            lambda c_hz: prog.setLabelText(
                f"扫频中...  当前调谐 {c_hz/1e6:.3f} MHz"))
        worker.finished_ok.connect(self._on_sweep_done)
        worker.failed.connect(self._on_sweep_failed)
        worker.cancelled.connect(self._on_sweep_cancelled)
        prog.canceled.connect(worker.cancel)

        worker.start()

    def _sweep_cleanup(self):
        """扫频结束（成功/失败/取消）后的公共清理：关进度框、恢复 IQ 轮询。"""
        if self._sweep_progress is not None:
            try:
                self._sweep_progress.close()
            except Exception:
                pass
            self._sweep_progress = None
        # 等后台线程真正退出，避免 "QThread destroyed while running" 告警
        wk = self._sweep_worker
        if wk is not None:
            try:
                wk.wait(2000)
            except Exception:
                pass
        self._sweep_worker = None
        # 恢复实时 IQ 轮询（后端仍连接时）
        if self._active_sdr_backend is not None:
            self._start_iq_streams()

    def _on_sweep_done(self, result):
        """扫频完成：标注活动频点、切到最强台、AI 面板输出峰值列表。"""
        backend = self._active_sdr_backend
        activities = list(getattr(result, "activities", []) or [])
        # 在频谱上标注所有实测活动频点（marker 仅在当前 span 内可见）
        try:
            self.spectrum.set_markers([a.center_hz for a in activities])
        except Exception:
            pass
        # 调谐到最强活动台，让第一个 marker 立即可见
        if activities and backend is not None:
            try:
                backend.set_frequency(float(activities[0].center_hz))
                self.spectrum.generator.center_freq_hz = float(activities[0].center_hz)
            except Exception:
                pass
        self._sweep_cleanup()

        # 状态栏摘要
        n = len(activities)
        try:
            nf = getattr(result, "noise_floor_db", 0.0)
            f0 = float(result.freqs_hz[0]) / 1e6
            f1 = float(result.freqs_hz[-1]) / 1e6
        except Exception:
            nf = 0.0; f0 = 0.0; f1 = 0.0
        self.statusBar().showMessage(
            f"扫频完成：{n} 个活动频点（噪声底 {nf:.1f} dB）", 10000)
        # AI 面板输出完整峰值列表
        try:
            rows = ["<b>扫频找台结果（实测）</b><br>"
                    f"范围 {f0:.1f}–{f1:.1f} MHz，"
                    f"门限 {getattr(result,'threshold_db',0.0):.1f} dB<br>"]
            if activities:
                rows.append(
                    "<table width='100%' cellspacing='2' cellpadding='2' "
                    "style='font-size:8pt;'>"
                    "<tr style='color:#5B7B8C;'><td><b>中心MHz</b></td>"
                    "<td><b>带宽kHz</b></td><td><b>峰值dB</b></td></tr>")
                for a in activities:
                    rows.append(
                        f"<tr><td>{a.center_hz/1e6:.3f}</td>"
                        f"<td>{a.bandwidth_hz/1e3:.1f}</td>"
                        f"<td>{a.peak_db:.1f}</td></tr>")
                rows.append("</table>")
            else:
                rows.append("该频段未检测到超过门限的活动信号（可能无电台/增益过低）。")
            self.ai_panel._add_system_message("".join(rows))
        except Exception:
            pass

    def _on_sweep_failed(self, msg: str):
        self._sweep_cleanup()
        self.statusBar().showMessage(f"扫频失败：{msg}", 8000)
        QMessageBox.warning(self, "扫频失败", f"扫频过程出错：\n{msg}")

    def _on_sweep_cancelled(self):
        self._sweep_cleanup()
        self.statusBar().showMessage("扫频已取消", 4000)

    # ------------------------------------------------------------------
    # baseband 离线回放（任务 4）
    # ------------------------------------------------------------------
    def _replay_recording(self):
        """回放已录制的 .iq/.cf32/.wav baseband 文件。

        用 QFileDialog 选文件 → baseband_io.load_iq() 读回真实 IQ →
        QTimer 按原始采样率把样本分块推给 spectrum.update_iq() 做离线回放。
        文件里有什么就播什么，绝不造假。回放期间暂停实时 SDR 轮询，结束后恢复。
        """
        if self._replay_timer is not None:
            self._stop_replay()
        path, _ = QFileDialog.getOpenFileName(
            self, "选择录音文件",
            os.path.expanduser("~/mbdsdr_recordings"),
            "Baseband IQ (*.iq *.cf32 *.cs16 *.wav);;All Files (*)")
        if not path:
            self.statusBar().showMessage("未选择录音文件", 3000)
            return
        try:
            from mbdsdr_ai import baseband_io
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "回放失败", f"baseband_io 不可用：\n{e}")
            return
        try:
            rec = baseband_io.load_iq(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "回放失败", f"读取文件失败：\n{e}")
            return
        if rec.get("error"):
            QMessageBox.warning(self, "回放失败", str(rec["error"]))
            return
        iq = np.asarray(rec["iq"], dtype=np.complex64)
        if iq.size < 64:
            QMessageBox.information(self, "回放", "文件中有效样本太少，无法回放。")
            return
        sr = float(rec.get("sample_rate", 2.4e6))
        center_hz = float(rec.get("center_freq_hz", 0.0))

        # 停止实时轮询（回放期间独占频谱）；结束后按当时连接状态恢复
        self._replay_resume_poll = self._iq_poll_timer is not None and \
            self._iq_poll_timer.isActive()
        self._stop_iq_streams()
        # _stop_iq_streams 会关掉声卡；回放本身要出声，这里重新拉起声卡
        # （AudioPlayer.start 幂等：已启动直接返回 True）。回放结束 _stop_replay
        # 不关声卡：实时后端仍连着时由 _start_iq_streams 继续管理。
        if self._audio_player is not None:
            try:
                self._audio_player.start()
            except Exception:
                pass
        try:
            self.spectrum.set_connected(True)
        except Exception:
            pass

        self._replay_iq = iq
        self._replay_pos = 0
        self._replay_sr = sr
        self._replay_center_hz = center_hz
        self._replay_total = int(iq.size)
        # 每 50ms 推一块（按采样率换算样本数），与实时 20fps 观感一致
        self._replay_block = max(1024, int(sr * 0.05))
        self._replay_timer = QTimer(self)
        self._replay_timer.setInterval(50)
        self._replay_timer.timeout.connect(self._replay_tick)
        self._replay_timer.start()
        self.statusBar().showMessage(
            f"回放中: {os.path.basename(path)}  "
            f"0.0/{self._replay_total/sr:.1f}s", 0)

    def _replay_tick(self):
        """回放定时器：按采样率把下一块真实 IQ 推给频谱 + 解调链出声。

        每块回放 IQ 同时做两件事：
          1. spectrum.update_iq() 做离线 FFT 频谱；
          2. self._demod_and_play(seg, sr) 走 VFO DDC → 按当前模式解调 → 声卡输出。
        解调模式优先取 self._demod_cfg.mode（用户在控制面板选的模式），
        回放期间后端可能仍连着，但不应被后端 status.demod_mode 覆盖。
        """
        if self._replay_iq is None or self._replay_timer is None:
            self._stop_replay()
            return
        n = self._replay_block
        seg = self._replay_iq[self._replay_pos:self._replay_pos + n]
        if seg.size >= 64:
            try:
                self.spectrum.update_iq(seg, self._replay_center_hz, self._replay_sr)
            except Exception:
                pass
            # 真送解调链：VFO DDC → 按 _demod_cfg.mode 解调 → 声卡（带静噪门控）
            try:
                self._demod_and_play(seg, self._replay_sr)
            except Exception:
                pass
        self._replay_pos += n
        cur_s = self._replay_pos / self._replay_sr
        tot_s = self._replay_total / self._replay_sr
        self.statusBar().showMessage(
            f"回放中: {min(cur_s, tot_s):.1f}/{tot_s:.1f}s", 0)
        if self._replay_pos >= self._replay_total:
            self._stop_replay()
            self.statusBar().showMessage(
                f"回放完成（{tot_s:.1f}s）", 5000)

    def _stop_replay(self):
        """停止回放并恢复实时状态。

        注意：这里不主动关声卡。回放期间声卡是回放启动时拉起的；若实时后端仍连着，
        下面 _start_iq_streams() 会继续复用同一条 OutputStream（start 幂等）；
        若后端未连接，声卡留空跑也无害（无数据入队即静音）。
        """
        if self._replay_timer is not None:
            try:
                self._replay_timer.stop()
            except Exception:
                pass
            self._replay_timer = None
        self._replay_iq = None
        self._replay_pos = 0
        # 回放结束：若实时后端仍连着，恢复统一 IQ 轮询；否则保持未连接空白
        if self._replay_resume_poll and self._active_sdr_backend is not None:
            self._start_iq_streams()
        elif self._active_sdr_backend is None:
            try:
                self.spectrum.set_connected(False)
            except Exception:
                pass
        self._replay_resume_poll = False

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
            "<p>呼号：在「设置」中配置</p>"
            "<p>集成 GNU Radio / SDR++ / SatDump 等开源项目功能</p>"
            "<p>支持 MCP 工具调用，任意 AI IDE 可直接控制硬件</p>"
            "<p>射频天空视图借鉴 Stellarium 设计理念</p>"
        )

    # ========================================================================
    # 射频天空视图
    # ========================================================================

    def _init_sky_view(self):
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

    # ========================================================================
    # 卫星闭环自动跟踪（过境预测 -> 实时 az/el/多普勒 -> 自动调谐 SDR）
    # ========================================================================
    def _init_loop_sat_catalog(self):
        """创建闭环跟踪器并填充卫星下拉框（无观测者位置也能列出 TLE）。"""
        if LoopSatTracker is None:
            return
        try:
            self._loop_sat_tracker = LoopSatTracker(None)
        except Exception:
            self._loop_sat_tracker = None
            return
        names = [s["name"] for s in self._loop_sat_tracker.list_satellites()]
        self.sat_track_panel.populate_satellites(names)
        self.sat_track_panel.set_tle_date(self._loop_sat_tracker.tle_date)
        if names:
            self.sat_track_panel.set_downlink_mhz(
                self._loop_sat_tracker.downlink_freq_for(names[0]) / 1e6)

    def _on_sat_combo_changed(self, name: str):
        """下拉框切换卫星：回填该星的标准下行频率。"""
        if self._loop_sat_tracker is not None and name:
            f = self._loop_sat_tracker.downlink_freq_for(name)
            if f > 0:
                self.sat_track_panel.set_downlink_mhz(f / 1e6)

    def _on_update_sat_tle(self):
        """从 Celestrak 在线刷新内置 TLE；失败保留内置，不崩。"""
        if self._loop_sat_tracker is None:
            self._init_loop_sat_catalog()
        if self._loop_sat_tracker is None:
            return
        n = self._loop_sat_tracker.update_tle_from_celestrak()
        names = [s["name"] for s in self._loop_sat_tracker.list_satellites()]
        self.sat_track_panel.populate_satellites(names)
        self.sat_track_panel.set_tle_date(self._loop_sat_tracker.tle_date)
        if n > 0:
            self.statusBar().showMessage(f"TLE 已更新（{n} 颗刷新）", 4000)
        else:
            self.statusBar().showMessage("TLE 在线更新失败，使用内置 TLE", 4000)

    def _menu_start_sat_track(self):
        """菜单项触发：等价于点面板上的开始按钮。"""
        name = self.sat_track_panel.sat_combo.currentText().strip()
        if not name:
            return
        self._start_satellite_tracking(name, self.sat_track_panel.current_downlink_hz())

    def _start_satellite_tracking(self, sat_name: str, downlink_freq_hz: float):
        """进入闭环自动跟踪：选星 -> 500ms 轮询 az/el/多普勒 -> 自动调谐 SDR。

        硬约束：
          - 调谐频率 = 下行频率 + 多普勒偏移（含多普勒校正）；
          - 仰角 < 5° 时停止并提示过境结束；
          - 无观测者位置时拒绝跟踪并提示。
        """
        if LoopSatTracker is None:
            self.statusBar().showMessage("卫星跟踪不可用（缺 skyfield 依赖）", 4000)
            return
        if self._observer_lat is None or self._observer_lon is None:
            self.sat_track_panel.set_tracking_state(False)
            self.statusBar().showMessage(
                "需要观测者位置：连接 GNSS 或在 gui_config.json 配置 observer_lat/lon",
                6000)
            return

        from mbdsdr_ai.sat_passes import GroundStation
        gs = GroundStation(lat_deg=self._observer_lat,
                           lon_deg=self._observer_lon)
        if self._loop_sat_tracker is None:
            self._loop_sat_tracker = LoopSatTracker(gs)
        else:
            # 切换观测站坐标后必须在 select_satellite 之前注入，
            # 否则 _build_satellite 仍用旧 Topos。
            self._loop_sat_tracker.ground_station = gs

        if not self._loop_sat_tracker.select_satellite(sat_name):
            self.statusBar().showMessage(f"无法选中卫星 {sat_name}", 4000)
            return
        self._loop_sat_tracker.set_downlink_freq(downlink_freq_hz)

        # 记录调谐前频率/模式，过境结束后恢复
        self._sat_track_prev_freq_hz = self._vfo_center_hz
        self._sat_track_prev_mode = getattr(self.control_panel, "_current_mode", "FM")
        # 气象卫星 APT 用 WFM 接收；业余中继沿用当前模式
        if sat_name.upper().startswith(("NOAA", "METEOR", "FENGYUN")):
            self._sat_track_mode = "WFM"
        else:
            self._sat_track_mode = self._sat_track_prev_mode or "FM"

        if self._sat_track_timer is None:
            self._sat_track_timer = QTimer(self)
            self._sat_track_timer.setInterval(500)
            self._sat_track_timer.timeout.connect(self._poll_satellite_track)
        self._sat_track_timer.start()
        self.sat_track_panel.set_tracking_state(True)
        self.statusBar().showMessage(
            f"开始跟踪 {sat_name}（下行 {downlink_freq_hz/1e6:.4f} MHz）", 4000)
        self._poll_satellite_track()  # 立即先调谐一次

    def _poll_satellite_track(self):
        """500ms 轮询：取实时 az/el/多普勒 -> 自动调谐 SDR -> 刷新面板。"""
        if self._loop_sat_tracker is None:
            return
        pos = self._loop_sat_tracker.current_position()
        self.sat_track_panel.update_position(pos)
        if not pos.get("valid"):
            return
        el = float(pos["elevation"])
        # 仰角 < 5°：过境结束，停止闭环
        if el < 5.0:
            self._stop_satellite_tracking()
            self.statusBar().showMessage(
                f"卫星过境结束（仰角 {el:.1f}° < 5°）", 6000)
            return
        # 核心：调谐频率 = 下行频率 + 多普勒偏移（含多普勒校正）
        corrected = float(pos["corrected_freq_hz"])
        try:
            self._on_tune_sdr(corrected, self._sat_track_mode)
        except Exception:
            pass

    def _stop_satellite_tracking(self):
        """停止闭环跟踪，恢复用户之前的接收频率。"""
        if self._sat_track_timer is not None:
            self._sat_track_timer.stop()
        self.sat_track_panel.set_tracking_state(False)
        if self._sat_track_prev_freq_hz is not None:
            mode = self._sat_track_prev_mode or "FM"
            try:
                self._on_tune_sdr(self._sat_track_prev_freq_hz, mode)
            except Exception:
                pass
        self._sat_track_prev_freq_hz = None
        self.statusBar().showMessage("卫星跟踪已停止", 3000)

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

    def _open_gnss_dialog(self):
        """弹出 GNSS 串口连接对话框：选串口号（如 COM10）+ 波特率。

        串口号留空 → auto_detect 自动扫描所有候选口。确定后先断开旧连接再按新参数
        start()；打开失败不抛异常，仅在状态栏提示。成功/失败均持久化到 gui_config.json。
        """
        if self._gnss is None:
            QMessageBox.warning(self, "GNSS", "串口 GNSS 依赖不可用（pyserial 未安装）。")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("连接 GNSS")
        form = QFormLayout(dlg)
        port_edit = QLineEdit(self._gnss_port)
        port_edit.setPlaceholderText("COM10（留空自动检测）")
        form.addRow("串口号:", port_edit)
        baud_combo = QComboBox()
        for b in (9600, 19200, 38400, 57600, 115200):
            baud_combo.addItem(str(b), b)
        idx = baud_combo.findData(self._gnss_baudrate)
        if idx >= 0:
            baud_combo.setCurrentIndex(idx)
        form.addRow("波特率:", baud_combo)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() != QDialog.Accepted:
            return
        port = port_edit.text().strip()
        baud = int(baud_combo.currentData())
        self._gnss_port = port
        self._gnss_baudrate = baud
        # 若已连着旧口，先干净断开再按新参数重开（reader 内部自带热插拔重连）
        try:
            self._gnss.stop()
        except Exception:
            pass
        ok = False
        try:
            ok = bool(self._gnss.start(port or None, baud))
        except Exception:
            ok = False
        if ok:
            self.statusBar().showMessage(
                f"GNSS 已连接: {port or '自动检测'} @ {baud}，正在搜星…", 5000)
        else:
            self.statusBar().showMessage(
                f"GNSS 连接失败: {port or '自动检测'} @ {baud}"
                f"（检查串口/波特率/接线，端口是否被占用）", 10000)
        try:
            self._save_gui_config()
        except Exception:
            pass

    def _disconnect_gnss(self):
        """手动断开 GNSS 串口（保留轮询定时器，状态栏回到“未连接”）。"""
        if self._gnss is None:
            return
        try:
            self._gnss.stop()
        except Exception:
            pass
        self.statusBar().showMessage("GNSS 已断开", 3000)

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
        # 底部状态栏 GNSS 标签：三态文案（同一串口数据源，不走 worker 双写）
        try:
            connected = bool(self._gnss.is_connected)
        except Exception:
            connected = False
        try:
            if not connected:
                self.status_gps.setText("GNSS: 未连接")
            else:
                sats = pos.sats if isinstance(pos.sats, int) and pos.sats >= 0 else 0
                if pos.lat is not None and pos.lon is not None:
                    ns = "N" if float(pos.lat) >= 0 else "S"
                    ew = "E" if float(pos.lon) >= 0 else "W"
                    hdop_s = (f" HDOP={float(pos.hdop):.1f}"
                              if isinstance(pos.hdop, (int, float)) else "")
                    self.status_gps.setText(
                        f"GNSS: {abs(float(pos.lat)):.4f}°{ns} "
                        f"{abs(float(pos.lon)):.4f}°{ew} {sats}星{hdop_s}")
                else:
                    self.status_gps.setText(f"GNSS: 搜索中/{sats} 星")
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
        # 运行参数（频率/增益/模式/带宽/采样率/呼号/AGC/主题）立即落盘，退出不丢
        try:
            self.settings.flush()
        except Exception:
            pass
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
                "gnss_port": self._gnss_port,
                "gnss_baudrate": self._gnss_baudrate,
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
            # 恢复 GNSS 串口配置（port="" 留空 → 连接时 auto_detect）
            try:
                gp = config.get("gnss_port", "")
                self._gnss_port = str(gp) if gp else ""
                gb = config.get("gnss_baudrate", 9600)
                self._gnss_baudrate = int(gb)
            except Exception:
                pass
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
