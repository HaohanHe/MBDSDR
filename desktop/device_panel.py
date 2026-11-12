# SPDX-License-Identifier: MIT
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

import logging
from pathlib import Path
from typing import Optional, List, Dict

from PySide6.QtCore import Qt, Signal, QTimer, QObject, QRunnable, QThreadPool
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QLineEdit, QGroupBox, QFormLayout, QMessageBox,
)

try:  # pragma: no cover
    from mbdsdr_ai.sdr_backend import (
        OWNER_STATE_UNKNOWN as _OWNER_STATE_UNKNOWN,
        enumerate_all_sdr_devices,
        get_owner_state as _get_owner_state,
        owner_registry_path as _owner_registry_path,
        owner_state_text as _owner_state_text,
    )
    from mbdsdr_ai import usb_hotplug
    _ENUM_OK = True
except Exception:  # noqa: BLE001
    enumerate_all_sdr_devices = None  # type: ignore
    usb_hotplug = None  # type: ignore
    _ENUM_OK = False
    _OWNER_STATE_UNKNOWN = "unknown"
    _get_owner_state = None  # type: ignore
    _owner_registry_path = None  # type: ignore
    _owner_state_text = None  # type: ignore

# 跨进程设备占用**不在这里登记**：判据只有一个地方——后端 connect()（真正
# 开硬件的那一处）。面板 / 连接对话框 / 自动枚举 / 模块面板最终都走它，
# 在这里各自 claim 的结果是漏掉另外三条路（实测双开）。本面板只发信号。

from constants import DEFAULT_SAMPLE_RATE_HZ, format_sample_rate
# 连接请求的唯一持有者：本面板只渲染/写回，不再自持 _sample_rate/_gain 副本。
import connect_request_store as _req_store

logger = logging.getLogger(__name__)

#: 无设备空态：下拉框说事实，状态行说下一步——两处都写「未检测到 SDR 设备」
#: 等于同一句话念两遍，用户看不出该干什么。
_NO_DEVICE_COMBO = "未检测到 SDR 设备"
_NO_DEVICE_STATUS = "未检测到设备 · 插上后点「刷新」"
_NO_DEVICE_TIP = ("可能原因：USB 没插好、缺驱动，或设备被其他程序占用。"
                  "RTL-SDR 需要先用 Zadig 安装 WinUSB 驱动。")

try:
    from perf_profile import sample_rate_for as _profile_rate
    from config_schema import SCHEMA as _SCHEMA
    _DEFAULT_PROFILE = str(_SCHEMA["perf_profile"].default)
    PERF_DEFAULT_SR = int(_profile_rate(_DEFAULT_PROFILE))
except Exception:  # noqa: BLE001 - 单独打包时回内核基准
    PERF_DEFAULT_SR = int(DEFAULT_SAMPLE_RATE_HZ)


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


#: 单次后台枚举的最长等待（毫秒）。USB open 没有超时，设备/驱动卡死时
#: ``enumerate_all_sdr_devices()`` 会永不返回；没有这个上限，inflight 会
#: 永久卡 True，热插拔轮询从此全部跳过，而且日志里一个字都没有。
#: 5 s 覆盖本机实测最慢 2.4 s，只在真的卡住时触发。
ENUM_TIMEOUT_MS = 5000


class _DeviceEnumSignals(QObject):
    """跨线程枚举结果信号（不挂父对象，生命周期由运行中的任务持有）。"""

    #: (devices | None, error_text, token)。token 用于丢弃超时后迟到的结果，
    #: 避免它把新一轮枚举的 inflight 清掉（见 DevicePanel._on_enum_timeout）。
    finished = Signal(object, str, int)


class _DeviceEnumTask(QRunnable):
    """后台枚举 SDR 设备。

    本机实测：``rtlsdr_open`` 探测调谐器在设备被占用时要 0.2–2.4 s，Pluto 网络
    探测固定 0.5 s 超时——2 s 一次的热插拔轮询若在 GUI 线程同步跑，界面会被
    吃满（实测 10 s 内 63% 的 GUI 线程时间耗在轮询里）。这里把整段枚举丢到
    QThreadPool：存在性判断结果不变，只是不再阻塞事件循环。
    """

    def __init__(self, signals: _DeviceEnumSignals, token: int = 0):
        super().__init__()
        self._signals = signals
        self._token = int(token)

    def run(self):  # noqa: D102 - Qt 接口
        try:
            devices = list(enumerate_all_sdr_devices()) if (
                _ENUM_OK and enumerate_all_sdr_devices is not None) else []
            err = ""
        except Exception as exc:  # noqa: BLE001
            devices, err = None, f"{type(exc).__name__}: {exc}"
        try:
            self._signals.finished.emit(devices, err, self._token)
        except RuntimeError:
            # 面板已销毁、接收端已自动断开：结果无人接收，安静丢弃。
            pass


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
    #: USB 物理在位探针；None = 按平台默认（Windows SetupAPI / Linux sysfs）。
    #: 测试可换成假探针，热插拔判定因此不依赖真机。
    _hotplug_probe = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self._devices: List[Dict] = []
        #: 与 _devices 同序的身份结论（key/quality/warning）。
        self._identities: List = []
        self._connected = False
        self._current_dev_key: Optional[str] = None
        self._current_dev_locator: Optional[tuple] = None
        self._last_capability_report: Dict = {}
        self._last_verdict: tuple = (None, "尚未连接")
        #: 最近一次占用三态结论（诊断弹窗复用）：不在弹窗时再读一遍，
        #: 否则「损坏已备份移走」会让两次结论前后不一致。
        self._last_owner_state = None
        #: 本次连接中「设备未申报量程、未做能力校验」的字段与请求值。
        #: 连上之后仍要能在面板上看到，不能随 set_connected(True) 消失。
        self._unverified_capabilities: list = []
        #: 面板最后一次发起的连接（用户点「连接」时的设备字典）。它是
        #: 「实际连上的那台设备」的第一手证据——不能用下拉框当前选中项推断：
        #: 下拉选 B、实际连 A 时，按选中项会把身份记成 B，A 拔掉永远检测不到。
        self._pending_connect_device: Optional[Dict] = None
        #: 已确认身份的那台连接设备（主窗口可能连调两次 set_connected(True)，
        #: 第二次不能再丢掉第一次确认好的身份）。
        self._connected_device: Optional[Dict] = None
        #: 本次连接确认不了身份时的告警标记（只对本次连接告警一次）。
        self._unplug_identity_warned = False
        # 热插拔判定基准：上一拍枚举的稳定定位符集合。
        # 用轮询历史而不是下拉框快照，避免 serial/tuner 抖动被误判成插拔。
        self._last_poll_locs: set = set()
        self._build_ui()
        _req_store.subscribe(self._on_request_changed)
        # 首次枚举延后到首帧之后：enumerate_all_sdr_devices 本机实测 1.0–1.8 s
        # （rtlsdr_open + Pluto 网络探测 0.5 s），压在构造函数里等于让首帧等它。
        # 期间界面如实显示「正在枚举」，不假装「未检测到设备」。
        # 刷新（含启动首次）也走后台：枚举 1.0–1.8 s，延后到首帧之后仍会在
        # 首帧后立刻冻住 GUI；这里只派发，结果回主线程再更新下拉框。
        self._refresh_signals = _DeviceEnumSignals()
        self._refresh_signals.finished.connect(self._on_devices_refreshed)
        self._refresh_inflight = False
        #: 刷新枚举的 token 与看门狗：枚举卡死时也要能复位 inflight，
        #: 否则 _refresh_inflight 会永久挡住热插拔轮询（见 _on_hotplug_poll）。
        self._refresh_token = 0
        self._refresh_watchdog = QTimer(self)
        self._refresh_watchdog.setSingleShot(True)
        self._refresh_watchdog.timeout.connect(self._on_refresh_timeout)
        # 枚举完成前 dropdown 如实写「正在枚举」、连接按钮置灰：
        # 不假装「未检测到设备」，也不允许对着占位项点连接。
        self.dev_combo.addItem("正在枚举设备…")
        self.connect_btn.setEnabled(False)
        QTimer.singleShot(400, self.refresh_devices_async)

        # 热插拔轮询：每 2 秒派发一次后台枚举（枚举本身不在这条 GUI 线程里跑）
        self._enum_signals = _DeviceEnumSignals()
        self._enum_signals.finished.connect(self._on_hotplug_devices)
        self._enum_inflight = False
        #: 本轮枚举的 token；超时/完成后自增，迟到结果按 token 丢弃。
        self._enum_token = 0
        self._enum_timeout_count = 0
        self._enum_watchdog = QTimer(self)
        self._enum_watchdog.setSingleShot(True)
        self._enum_watchdog.timeout.connect(self._on_enum_timeout)
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
        self.dev_combo.currentIndexChanged.connect(self._on_device_selected)
        dev_row.addWidget(self.dev_combo, stretch=1)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(self.refresh_devices_async)
        dev_row.addWidget(self.refresh_btn)
        form.addRow("设备", dev_row)

        # 参数
        # 采样率输入保留 Hz：帧格式与后端接口都以 Hz 计，输入框量纲合法，
        # 且 2048000 这类整数值不该被 3 位小数截断。右侧给等价值
        # （format_sample_rate，与状态栏/内核同一读数口径），用户不必自己换算。
        # 桌面默认按性能档（省电 1.024 MS/s），不是内核 2.4 MS/s 基准。
        # 内核常量仍是设备能力/验收基准，两者不是一回事。
        self.sr_edit = QLineEdit(str(PERF_DEFAULT_SR))
        self.sr_edit.setToolTip(
            "每秒采集的样本数（配置意图，不是设备读数）：未连接时按性能档"
            f"预填 {format_sample_rate(PERF_DEFAULT_SR)}（默认 · 未确认），"
            "连接时按此值下发")
        self.sr_equiv = QLabel("")
        self.sr_equiv.setObjectName("statusValue")
        self.sr_equiv.setToolTip("等价值：按项目统一读数口径（MS/s，3 位小数）显示")
        sr_row = QHBoxLayout()
        sr_row.setContentsMargins(0, 0, 0, 0)
        sr_row.addWidget(self.sr_edit, 1)
        sr_row.addWidget(self.sr_equiv)
        form.addRow("采样率(Hz)", sr_row)
        self.sr_edit.textChanged.connect(self._update_sr_equiv)
        self._update_sr_equiv()

        self.gain_edit = QLineEdit("30")
        self.gain_edit.setToolTip(
            "接收放大倍数；信号弱就调大，过强出现削顶就调小，开启硬件 AGC 时此项不生效")
        form.addRow("增益(dB)", self.gain_edit)

        self.ppm_edit = QLineEdit("0")
        self.ppm_edit.setToolTip(
            "频率校正（PPM = 百万分率）：晶振频偏校正，典型 20~50，可为负")
        form.addRow("PPM", self.ppm_edit)

        # 用户改完任一参数就写回唯一请求存储：另一面板选中同一台设备时
        # 看到的是同一份请求，而不是各自的旧副本。
        for _w in (self.sr_edit, self.gain_edit, self.ppm_edit):
            _w.editingFinished.connect(self._push_request_from_editors)

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

        self.status_label = QLabel(_NO_DEVICE_STATUS)
        self.status_label.setObjectName("statusValue")
        self.status_label.setToolTip(_NO_DEVICE_TIP)
        form.addRow("", self.status_label)

        # 跨进程占用三态：空闲时不占地方；「被占用 / 状态未知」必须显式说
        # 出来。第三态尤其不能显示成空闲——点连接才发现被拒，界面此前一直
        # 在说假话（与「被占用却提示 USB 权限不足」是同一个病）。
        self.owner_state_label = QLabel("")
        self.owner_state_label.setObjectName("statusValue")
        self.owner_state_label.setWordWrap(True)
        self.owner_state_label.setVisible(False)
        owner_row = QHBoxLayout()
        owner_row.setContentsMargins(0, 0, 0, 0)
        owner_row.addWidget(self.owner_state_label, 1)
        self.owner_retry_btn = QPushButton("重试")
        self.owner_retry_btn.setToolTip(
            "重新读取设备占用登记：确认这台设备是否被其它程序占用")
        self.owner_retry_btn.clicked.connect(
            lambda: self._refresh_owner_state())
        self.owner_retry_btn.setVisible(False)
        owner_row.addWidget(self.owner_retry_btn)
        self.owner_diag_btn = QPushButton("查看诊断")
        self.owner_diag_btn.setToolTip(
            "查看占用登记文件、损坏证据与修复办法")
        self.owner_diag_btn.clicked.connect(
            lambda: self._show_owner_diagnostics())
        self.owner_diag_btn.setVisible(False)
        owner_row.addWidget(self.owner_diag_btn)
        form.addRow("", owner_row)

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

    def _update_sr_equiv(self) -> None:
        """采样率等价值：2048000 → 「= 2.048 MS/s」；空/非数字/非正 → 「—」。

        Hz 输入不截断精度（后端按 Hz 下发），等价值只按项目读数口径展示，
        不改写输入框，也不参与连接载荷。
        """
        text = self.sr_edit.text().strip()
        try:
            hz = float(text)
        except (TypeError, ValueError):
            self.sr_equiv.setText("—")
            return
        shown = format_sample_rate(hz)
        if not self._connected and abs(hz - float(PERF_DEFAULT_SR)) < 1e-6:
            # 未连接时这一档只是性能档预填值，不是设备读数：与控制面板/模块
            # 面板共用同一句「默认 · 未确认」写法（数字随性能档，语义不同）。
            self.sr_equiv.setText(f"= {shown}（性能档默认 · 未确认）")
            return
        self.sr_equiv.setText(f"= {shown}" if hz > 0 else "—")

    # ------------------------------------------------------------------ 设备枚举
    def refresh_devices(self) -> None:
        """同步枚举 SDR 设备（同步调用入口，测试与显式请求用）。"""
        if not _ENUM_OK or enumerate_all_sdr_devices is None:
            self._apply_devices([], enum_err="设备枚举不可用（枚举依赖缺失）")
            return
        try:
            devices = list(enumerate_all_sdr_devices())
        except Exception as e:  # noqa: BLE001 - 保留原因，不压成「没插设备」
            self._apply_devices([], enum_err=f"{type(e).__name__}: {e}")
            return
        self._apply_devices(devices)

    def refresh_devices_async(self) -> None:
        """派发一次后台设备枚举（只派发，不等结果）。

        枚举里有同步 USB open 与 0.5 s 网络超时，实测 1.0–1.8 s；在 GUI 线程
        同步跑等于冻住界面。真实结果在 ``_on_devices_refreshed`` 里落到控件。
        """
        if not _ENUM_OK or enumerate_all_sdr_devices is None:
            self._apply_devices([], enum_err="设备枚举不可用（枚举依赖缺失）")
            return
        if self._refresh_inflight or self._enum_inflight:
            return  # 上一拍还没回来，不堆积任务
        self._refresh_inflight = True
        self._refresh_token += 1
        # 枚举卡死时不能永远等：看门狗到点复位 inflight（见 _on_refresh_timeout）。
        self._refresh_watchdog.start(ENUM_TIMEOUT_MS)
        # 每次手动刷新都给「正在枚举」这一拍：设备集合没变时，否则界面
        # 从点击到结果零变化，用户无法判断按钮到底有没有被响应（审计
        # 白名单里这条「幂等刷新」即由此而来）。结果回来照旧写实际台数。
        self.status_label.setText("正在枚举设备…")
        self.status_label.setToolTip("")
        QThreadPool.globalInstance().start(
            _DeviceEnumTask(self._refresh_signals, self._refresh_token))

    def _on_devices_refreshed(self, devices, err: str, token: int = 0) -> None:
        """后台刷新回来了：GUI 线程里更新下拉框（失败保留原因，不造假）。"""
        if token != self._refresh_token:
            return  # 超时后迟到的旧结果：不得清掉新一轮的 inflight
        self._refresh_inflight = False
        self._refresh_watchdog.stop()
        if err or devices is None:
            self._apply_devices([], enum_err=err or "枚举任务未返回设备列表")
            return
        self._apply_devices(devices)

    def _on_refresh_timeout(self) -> None:
        """刷新枚举超过上限仍未返回：复位 inflight，让后续刷新/轮询能重试。"""
        if not self._refresh_inflight:
            return
        self._refresh_inflight = False
        self._refresh_token += 1  # 迟到结果作废
        logger.warning(
            "设备枚举超时（刷新，>%.1fs）：enumerate_all_sdr_devices 未返回，"
            "已复位 inflight，下一次刷新会重新发起枚举",
            ENUM_TIMEOUT_MS / 1000.0)
        self.status_label.setText("设备枚举超时 · 稍后自动重试")
        self.status_label.setToolTip(
            "枚举设备超过 %.0f 秒没有返回（这是本程序设置的等待上限，不是设备读数），"
            "通常是设备/驱动无响应（USB open 无超时）。"
            "面板会自动重试；也可拔插设备后点「刷新」。"
            % (ENUM_TIMEOUT_MS / 1000.0))

    def _apply_devices(self, devices, enum_err: Optional[str] = None) -> None:
        """把一份已枚举到的设备列表落到控件上（不触发重新枚举）。

        ``enum_err`` 与空列表是**两件事**：枚举失败时设备可能插得好好的，
        说成「未检测到 SDR 设备」会把用户赶去拔插 USB，而真实原因可能是
        依赖缺失、USB open 卡死或驱动报错（module_panel 早已分开，两个面板
        不能对同一次失败给出相反结论）。
        """
        self._devices = list(devices or [])
        # 身份键与设备列表同序：撞键/无序列号时 resolve 会显式告警。
        self._identities = _req_store.resolve(self._devices)
        self.dev_combo.clear()
        if not self._devices:
            self.dev_combo.addItem(_NO_DEVICE_COMBO)
            if enum_err:
                # 枚举失败 != 没插设备：不要把用户赶去拔插 USB
                self.status_label.setText("设备枚举失败 · 这不等于没插设备")
                self.status_label.setToolTip(
                    f"枚举设备时出错：{enum_err}\n"
                    "设备可能已插好，只是本次枚举失败。请点「刷新」重试，"
                    "或拔插设备后重试。")
            else:
                self.status_label.setText(_NO_DEVICE_STATUS)
                self.status_label.setToolTip(_NO_DEVICE_TIP)
            self.connect_btn.setEnabled(False)
            self._last_poll_locs = set()
            self._set_owner_state(None)
            return
        for d in self._devices:
            label = d.get("label") or d.get("driver") or "未知设备"
            self.dev_combo.addItem(label)
        self.status_label.setText(f"检测到 {len(self._devices)} 台设备")
        self.status_label.setToolTip("")
        self.connect_btn.setEnabled(not self._connected)
        # 手动刷新/初始刷新后，轮询基准同步到最新列表：刷新本身不算“新插入”。
        self._last_poll_locs = {self._locator_of(d) for d in self._devices}
        self._render_request(self.dev_combo.currentIndex())
        # 设备列表变化后占用状态也可能变（别的进程连上/释放）：重读一次。
        self._refresh_owner_state()

    def _refresh_audio_devices(self) -> None:
        self.audio_combo.blockSignals(True)
        self.audio_combo.clear()
        self.audio_combo.addItem("系统默认", -1)
        for d in _list_audio_output_devices():
            self.audio_combo.addItem(d["name"], d["index"])
        self.audio_combo.blockSignals(False)

    # ------------------------------------------------------------------ 热插拔
    def _on_hotplug_poll(self):
        """每 2 秒派发一次后台设备枚举。

        枚举里有同步 USB open 与 0.5 s 网络超时，必须在 GUI 线程之外跑：
        上一拍还没回来就跳过本拍，避免任务堆积。
        """
        if not _ENUM_OK or enumerate_all_sdr_devices is None:
            return
        if self._enum_inflight or self._refresh_inflight:
            return
        self._enum_inflight = True
        self._enum_token += 1
        # 枚举卡死（USB open 无超时）时 finished 永不发射：看门狗到点复位
        # inflight，否则热插拔检测整体死亡且日志里看不出来。
        self._enum_watchdog.start(ENUM_TIMEOUT_MS)
        QThreadPool.globalInstance().start(
            _DeviceEnumTask(self._enum_signals, self._enum_token))

    def _hotplug_monitor(self):
        """懒建热插拔监听（``mbdsdr_ai/usb_hotplug``）。

        不放进 ``__init__``：面板可能在没有 mbdsdr_ai 的打包形态里单独导入，
        而且监听第一拍才需要探针（Windows SetupAPI 枚举一次约几毫秒）。
        """
        if usb_hotplug is None:
            return None
        monitor = getattr(self, "_hotplug_mon", None)
        if monitor is None:
            monitor = usb_hotplug.HotplugMonitor(probe=self._hotplug_probe)
            self._hotplug_mon = monitor
        return monitor

    def _on_hotplug_devices(self, now, err: str, token: int = 0):
        """后台枚举回来了：按稳定身份判定插拔，再把结论落到界面。

        拔出判定**不再用 (driver, index)**：枚举序号会随同类设备插拔前移，
        「拔掉 A」会被记成「序号 1 消失」= 误判成 B 被拔。身份分级、物理
        在位探针与失败归因都在 ``mbdsdr_ai/usb_hotplug`` 里，这里只做接线：

        * 只处理与「当前连接那台」匹配的事件，别的设备拔掉不打断数据流；
        * 拔出后不产生任何连接动作，重连仍由主窗口/用户决定；
        * 枚举失败、探针不可用都如实显示，不当成「设备全都没了」。
        """
        if token != self._enum_token:
            # 超时那拍已经复位并重派了新任务：迟到的旧结果既不能当现状，
            # 也不能把新任务的 inflight 清掉（那会让下一拍重复派发）。
            return
        self._enum_inflight = False
        self._enum_watchdog.stop()
        monitor = self._hotplug_monitor()
        if monitor is None:
            return
        if now is None and not err:
            err = "枚举任务没有返回设备列表"
        events = monitor.observe(now, err or "")
        if not events:
            return

        now_locs = {self._locator_of(d) for d in (now or [])}
        plugged_label = ""
        for event in events:
            if event.kind == usb_hotplug.EVENT_PLUGGED:
                if not self._connected and not plugged_label:
                    plugged_label = event.label or "新设备"
                continue
            if event.kind in (usb_hotplug.EVENT_POLL_PROBLEM,
                              usb_hotplug.EVENT_PROBE_PROBLEM):
                # 监听本身出了问题也要可见：三种失败各有自己的文案，
                # 不静默降级成「什么都没有发生」。
                self.status_label.setText(event.user_text)
                self.status_label.setToolTip(event.detail)
                continue
            if event.kind == usb_hotplug.EVENT_RECOVERED:
                if self._connected:
                    dev = self._connected_device or {}
                    self.status_label.setText(
                        f"已连接: {dev.get('label') or '设备'}")
                elif self._devices:
                    self.status_label.setText(
                        f"检测到 {len(self._devices)} 台设备")
                self.status_label.setToolTip("")
                continue
            # 以下事件只对「当前连接的那一台」有意义。别的设备拔出不许
            # 触发断开，否则就是被删 watchdog 的老毛病（拔一台停一排）。
            if not (self._connected and event.key == self._current_dev_key):
                continue
            if event.kind == usb_hotplug.EVENT_UNPLUGGED:
                self.status_label.setText(event.user_text or "设备已拔出")
                self.status_label.setToolTip(event.detail)
                self.device_unplugged.emit()
                self.set_connected(False)
                # set_connected(False) 会改写提示并清掉 key；拔出的结论要
                # 留在界面上，所以这里最后再写一次（含「按枚举判定」等限定）。
                self.status_label.setText(event.user_text or "设备已拔出")
                self.status_label.setToolTip(event.detail)
            elif event.kind == usb_hotplug.EVENT_PRESENT_UNOPENABLE:
                # 物理探针确认还在，只是这一拍枚举读不到。locator 仍在
                # 通常只是「本程序正占用，读不到 serial」，属正常现象，
                # 不打扰用户；locator 也没了才提示。
                if self._current_dev_locator not in now_locs:
                    self.status_label.setText(event.user_text)
                    self.status_label.setToolTip(event.detail)
            else:
                # ambiguous / disappeared_unverified：只提示，不断开。
                self.status_label.setText(event.user_text)
                self.status_label.setToolTip(event.detail)

        if plugged_label and not self._connected:
            self.device_plugged.emit(plugged_label)
            # 本轮枚举刚拿到的列表直接落控件，不再触发第二次枚举。
            self._apply_devices(now)

    def _on_enum_timeout(self) -> None:
        """热插拔枚举超过上限仍未返回：复位 inflight 并留下可见证据。

        这一步是整个看门狗的关键：不复位 ``_enum_inflight``，之后每一拍轮询
        都会在 ``_on_hotplug_poll`` 开头被跳过，热插拔检测从此静默死亡。
        复位后下一拍会重新派发枚举（本次失败可重试）；旧任务即使随后返回，
        也因 token 不匹配被丢弃。
        """
        if not self._enum_inflight:
            return
        self._enum_inflight = False
        self._enum_token += 1  # 迟到结果作废
        self._enum_timeout_count += 1
        logger.warning(
            "设备枚举超时（第 %d 次，>%.1fs）：enumerate_all_sdr_devices 未返回，"
            "已复位 inflight；下一拍轮询会重新发起枚举",
            self._enum_timeout_count, ENUM_TIMEOUT_MS / 1000.0)
        self.status_label.setText(
            f"设备枚举超时 · 已自动重试 {self._enum_timeout_count} 次")
        self.status_label.setToolTip(
            "枚举设备超过 %.0f 秒没有返回（这是本程序设置的等待上限，不是设备读数），"
            "通常是设备/驱动无响应（USB open 无超时）。"
            "热插拔检测会在下一拍自动重试；也可拔插设备后点「刷新」。"
            % (ENUM_TIMEOUT_MS / 1000.0))

    @staticmethod
    def _locator_of(d: Dict) -> tuple:
        args = d.get("device_args") or {}
        try:
            idx = int(args.get("index", -1))
        except Exception:
            idx = -1
        return (d.get("driver") or "", idx)

    # ------------------------------------------------------------------ 状态
    def last_capability_verdict(self):
        """最近一次连接校验的 (verdict, reason)。

        verdict 为 True/False/None；None = 能力未知、无法校验（不是通过）。
        """
        return self._last_verdict

    def last_capability_report(self) -> Dict:
        return self._last_capability_report

    def _on_device_selected(self, idx: int) -> None:
        if 0 <= idx < len(self._devices):
            from mbdsdr_ai.device_capabilities import summarize_device
            try:
                tip = summarize_device(self._devices[idx])
            except Exception as e:
                # 摘要失败不阻断选择，但 tooltip 要如实说「不可用」而不是留空。
                tip = f"能力摘要不可用：{e}"
                logger.warning("设备能力摘要生成失败：%s", e)
            self.status_label.setToolTip(tip)
            self._render_request(idx)
        self._refresh_owner_state()

    # ------------------------------------------------------ 跨进程占用三态
    def _refresh_owner_state(self) -> None:
        """重新读取当前设备的跨进程占用状态（空闲/被占用/状态未知）。

        第三态**不显示成空闲**：登记读不出来时如实说「无法判断」，并把
        「重试」「查看诊断」摆在同一条状态行上。用户不知道能不能连，比
        直接说不能连更糟；界面先宣称空闲、点连接才被拒，就是在说假话。
        """
        if self._connected:
            # 已连上时登记里的持有者就是本进程，显示「被占用」只会误导。
            self._set_owner_state(None)
            return
        ident = self._current_identity()
        if ident is None or _get_owner_state is None:
            self._set_owner_state(None)
            return
        try:
            state = _get_owner_state(ident.key)
        except Exception as e:  # noqa: BLE001 - 查询失败同样不能说成空闲
            self._last_owner_state = None
            self._show_owner_unknown(
                f"查询设备占用状态失败：{type(e).__name__}: {e}")
            return
        self._last_owner_state = state
        self._set_owner_state(state)

    def _set_owner_state(self, state) -> None:
        """渲染三态；``None``/``free`` 隐藏整行（空闲不必占地方）。"""
        if state is None or not bool(state):
            self.owner_state_label.setText("")
            self.owner_state_label.setToolTip("")
            self.owner_state_label.setVisible(False)
            self.owner_retry_btn.setVisible(False)
            self.owner_diag_btn.setVisible(False)
            return
        if getattr(state, "state", "") == _OWNER_STATE_UNKNOWN:
            self._show_owner_unknown(self._owner_state_text_value(state))
            return
        text = self._owner_state_text_value(state)
        self.owner_state_label.setText(text)
        self.owner_state_label.setVisible(True)
        # 被占用也可能随时被对方释放：同样给「重试」，但不引到诊断。
        self.owner_retry_btn.setVisible(True)
        self.owner_diag_btn.setVisible(False)

    @staticmethod
    def _owner_state_text_value(state) -> str:
        if _owner_state_text is None:
            return str(getattr(state, "state", "") or "")
        return _owner_state_text(state)

    def _show_owner_unknown(self, text: str) -> None:
        """第三态：明确写「无法判断」，并把重试/诊断入口一起摆出来。"""
        self.owner_state_label.setText(f"⚠ {text}")
        self.owner_state_label.setToolTip(
            f"{text}\n点「重试」重新读取登记；点「查看诊断」查看登记文件、"
            "损坏证据与修复办法。")
        self.owner_state_label.setVisible(True)
        self.owner_retry_btn.setVisible(True)
        self.owner_diag_btn.setVisible(True)

    def _show_owner_diagnostics(self) -> None:
        """「查看诊断」：把登记路径、本次结论/原因、损坏证据摊开。"""
        path = _owner_registry_path() if _owner_registry_path else ""
        lines = [f"占用登记文件：{path or '（不可用）'}"]
        ident = self._current_identity()
        if ident is not None:
            lines.append(f"当前设备键：{ident.key}")
        state = self._last_owner_state
        if state is not None:
            lines.append(f"本次结论：{self._owner_state_text_value(state)}")
            reason = getattr(state, "reason", "")
            if reason:
                lines.append(f"原因：{reason}")
        evidence: List[str] = []
        if path:
            try:
                evidence = sorted(
                    str(p) for p in Path(path).parent.glob(
                        Path(path).name + ".corrupt-*.reason.txt"))
            except Exception:  # noqa: BLE001 - 诊断信息不全也不能崩
                evidence = []
        lines.append("损坏证据：" + ("无" if not evidence else ""))
        lines.extend(evidence)
        lines.append("")
        lines.append("修复办法：把该文件修成合法的 JSON 对象（含 owners 对象），"
                     "或确认没有别的程序在用这台设备后移除/改名该文件，"
                     "再点「重试」。")
        QMessageBox.information(self, "设备占用登记诊断", "\n".join(lines))

    # ------------------------------------------------------ 连接请求（唯一存储）
    def _resolve_connected_device(self, dev_label: str, device: Optional[Dict]):
        """确认「实际连上的那台设备」及其身份键；拿不到就返回 ``(None, None)``。

        身份一律走 ``connect_request_store.resolve`` 的同一套规则，
        面板不再自己造 key。优先级里的前三项都是**实际连接证据**，
        没有一项是「下拉框现在选着什么」。
        """
        if device is not None:
            return device, self._identity_of_device(device)
        pending, self._pending_connect_device = self._pending_connect_device, None
        if pending is not None:
            return pending, self._identity_of_device(pending)
        if self._connected_device is not None:
            return (self._connected_device,
                    self._identity_of_device(self._connected_device))
        if dev_label:
            hits = [i for i, d in enumerate(self._devices)
                    if (d.get("label") or d.get("driver") or "") == dev_label]
            if len(hits) == 1:
                idx = hits[0]
                if len(self._identities) != len(self._devices):
                    self._identities = _req_store.resolve(self._devices)
                if idx < len(self._identities):
                    return self._devices[idx], self._identities[idx]
        return None, None

    @staticmethod
    def _identity_of_device(device: Dict):
        """用连接请求存储的身份规则给单台设备定身份（拿不到时返回 None）。"""
        try:
            idents = _req_store.resolve([device])
        except Exception:  # noqa: BLE001 - 身份解析失败不该拖垮连接状态
            logger.exception("设备身份解析失败")
            return None
        return idents[0] if idents else None

    def _current_identity(self):
        """当前选中设备的身份结论；无设备时返回 None。"""
        # 设备列表被直接替换（测试/异步枚举竞态）时补一次身份解析，
        # 避免把“还没跑 resolve”误判成“没有身份”。
        if len(self._identities) != len(self._devices):
            self._identities = _req_store.resolve(self._devices)
        idx = self.dev_combo.currentIndex()
        if 0 <= idx < len(self._identities):
            return self._identities[idx]
        return None

    def _render_request(self, idx: int) -> None:
        """按唯一请求存储渲染当前设备的参数（不再使用面板自持副本）。"""
        if not (0 <= idx < len(self._identities)):
            return
        ident = self._identities[idx]
        req = _req_store.request_for(ident.key)
        # 正在输入的控件不抢：用户还在改的时候被另一侧的旧值覆盖是更坏的行为。
        if req.sample_rate_hz is not None and not self.sr_edit.hasFocus():
            self.sr_edit.setText(f"{req.sample_rate_hz:g}")
        if req.gain_db is not None and not self.gain_edit.hasFocus():
            self.gain_edit.setText(f"{req.gain_db:g}")
        if req.ppm is not None and not self.ppm_edit.hasFocus():
            self.ppm_edit.setText(f"{int(req.ppm)}")

        if ident.warning:
            # 身份不可靠时必须在状态行说出来，不许静默共用/错位。
            self.status_label.setText(f"⚠ {ident.warning}")
        elif self.status_label.text().startswith("⚠"):
            # 只有上一台设备的身份告警才需要回退成计数；不覆盖连接校验等提示。
            self.status_label.setText(f"检测到 {len(self._devices)} 台设备")

    def _push_request_from_editors(self) -> None:
        """把输入框里的合法值写回唯一请求存储（非法值不写，保持原值）。"""
        ident = self._current_identity()
        if ident is None:
            return
        values = {}
        try:
            # 这里只认纯 Hz；带 M/k 后缀的写法由连接时的统一解析负责，
            # 本方法不复制一份解析器（避免第三条解析口径）。
            hz = float(self.sr_edit.text().strip())
        except (TypeError, ValueError):
            hz = None
        if hz is not None and hz > 0:
            values["sample_rate_hz"] = hz
        try:
            values["gain_db"] = float(self.gain_edit.text())
        except (TypeError, ValueError):
            pass
        try:
            values["ppm"] = int(self.ppm_edit.text())
        except (TypeError, ValueError):
            pass
        if values:
            _req_store.update(ident.key, **values)

    def _on_request_changed(self, key: str) -> None:
        """另一面板改了同一台设备的请求 → 这边同步渲染。"""
        ident = self._current_identity()
        if ident is None or ident.key != key:
            return
        self._render_request(self.dev_combo.currentIndex())

    def set_connected(self, connected: bool, dev_label: str = "",
                      device: Optional[Dict] = None) -> None:
        """主窗口连接成功/断开后调用。

        ``device`` 是**实际连上的那台设备**（主窗口已知时应传入）。缺省时按
        「面板最后一次发起的连接 → 本次已确认的设备 → 枚举里唯一同名设备」
        依次确认身份；都确认不了就显式拒绝检测拔出（日志 + tooltip 说明），
        绝不按下拉框当前选中项猜——选中项可以和实际连接设备不是同一台，
        据此推断会让拔出永远检测不到。
        """
        self._connected = bool(connected)
        if connected:
            # 已连上：登记里的持有者就是本进程，占用行不再显示。
            self._set_owner_state(None)
            dev, ident = self._resolve_connected_device(dev_label, device)
            if dev is not None and ident is not None:
                self._connected_device = dev
                self._current_dev_key = ident.key
                self._current_dev_locator = self._locator_of(dev)
                self._unplug_identity_warned = False
                self.status_label.setToolTip("")
                # 预登记到热插拔监听：连接发生在首拍枚举之前时，这台设备
                # 从未进过基准，拔掉也判不出来。
                monitor = self._hotplug_monitor()
                if monitor is not None:
                    monitor.expect(ident.key, dev)
                if usb_hotplug is not None:
                    identity_note = usb_hotplug.hotplug_grade(dev)[1]
                    if identity_note:
                        # 「按口路径/按序号识别」的限制要在连接时就说清；
                        # 换口会被当成另一台设备。
                        self.status_label.setToolTip(identity_note)
                # 登记 store 侧会话占用（第三档）：数据层靠它冻结本次生效值，
                # 重复调用不覆盖首次快照，所以两次 set_connected(True) 无副作用。
                _req_store.begin_session(self._current_dev_key)
                # 跨进程占用不在这里补登记：后端 connect() 已按同一身份键 claim，
                # UI 入口再补一层只会让「改一层、另一层悄悄不一致」。
            else:
                self._connected_device = None
                self._current_dev_key = None
                self._current_dev_locator = None
                self.status_label.setToolTip(
                    "本次连接无法确认设备身份，拔出不会被自动检测。"
                    "从本面板选择设备重新连接可恢复检测。")
                if not self._unplug_identity_warned:
                    self._unplug_identity_warned = True
                    logger.warning(
                        "设备面板无法确认已连接设备的身份（label=%r）："
                        "本次不检测拔出，也不按下拉框选中项猜设备。", dev_label)
            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(True)
            text = f"已连接: {dev_label or '设备'}"
            if self._unverified_capabilities:
                # 连上之后仍留在面板上：不是一闪而过的状态栏提示。
                # RTL 这类「接受高采样率但可能丢样」不会有失败事件，
                # 只有持续可见的「未校验」才能覆盖到。
                text += (" · 本次能力未校验（"
                         + "、".join(self._unverified_capabilities) + "）")
            self.status_label.setText(text)
        else:
            # 先注销会话占用，再清 key——清完就不知道是哪台要解锁。
            if self._current_dev_key:
                # 跨进程占用由后端 disconnect() 归还（与 claim 同一层）。
                _req_store.end_session(self._current_dev_key)
            self._connected_device = None
            self._pending_connect_device = None
            self._unplug_identity_warned = False
            self._unverified_capabilities = []
            self._current_dev_key = None
            self._current_dev_locator = None
            self.connect_btn.setEnabled(bool(self._devices))
            self.disconnect_btn.setEnabled(False)
            self.status_label.setToolTip("")
            if self.status_label.text() != "设备已拔出":
                self.status_label.setText("已断开")
            # 断开后占用登记已归还：重读一次，别让上一轮的占用结论留在界面上。
            self._refresh_owner_state()
        # 「默认 · 未确认」标记随连接态切换：断开后重新标，连上后去掉。
        self._update_sr_equiv()

    # ------------------------------------------------------------------ 槽
    def _on_connect_clicked(self):
        idx = self.dev_combo.currentIndex()
        if idx < 0 or idx >= len(self._devices):
            QMessageBox.information(self, "提示", "未选择 SDR 设备")
            return
        dev = dict(self._devices[idx])
        # 新的连接请求重新计「未校验」清单，避免上一次连接的标记残留。
        self._unverified_capabilities = []
        # 把面板上的参数塞进 dev，供主窗口连接时读取。
        # 不合法输入一律拒绝并提示，绝不静默改写成默认值（用户以为连的是
        # 自己填的参数，实际拿到默认值 = 撒谎）。
        text = self.sr_edit.text().strip()
        try:
            sr = float(text)
        except (TypeError, ValueError):
            QMessageBox.warning(
                self, "采样率无效",
                f"“{text}”不是数字。请填写采样率（Hz），如 2048000。")
            return
        if sr <= 0:
            QMessageBox.warning(
                self, "采样率无效", f"采样率必须为正数（Hz），收到 {sr}。")
            return
        from mbdsdr_ai.device_capabilities import build_capability_report, check_range
        report = build_capability_report(dev)
        self._last_capability_report = report
        verdict, reason = check_range(report, "sample_rate_range", sr)
        self._last_verdict = (verdict, reason)
        if verdict is False:
            lo, hi = report["sample_rate_range"].value
            QMessageBox.warning(
                self, "采样率超出设备能力",
                f"采样率 {sr:.0f} Hz 超出该设备范围 "
                f"{lo:.0f}–{hi:.0f} Hz。请改值后重试。")
            return
        if verdict is None:
            # 未知范围：不能假装校验通过，也不能凭空填默认范围；如实说明后放行。
            self.status_label.setText(f"采样率范围未知，无法校验；将以 {sr:.0f} Hz 下发")
            self._unverified_capabilities.append(f"采样率 {sr:.0f} Hz")
        # 采样率留在局部变量里，连接前与增益/PPM 一起写入唯一请求存储；
        # 连接载荷由 fill_payload 统一落键，本面板不再直接写载荷副本。
        text = self.gain_edit.text().strip()
        try:
            gain = float(text)
        except (TypeError, ValueError):
            QMessageBox.warning(
                self, "增益无效",
                f"“{text}”不是数字。请填写总增益（dB），如 30。")
            return
        gverdict, greason = check_range(report, "gain_range", gain)
        if gverdict is False:
            lo, hi = report["gain_range"].value
            QMessageBox.warning(
                self, "增益超出设备能力",
                f"增益 {gain:g} dB 超出该设备范围 "
                f"{lo:g}–{hi:g} dB。请改值后重试。")
            return
        if gverdict is None:
            self.status_label.setText(
                f"增益范围未知，无法校验；将以 {gain:g} dB 下发")
            self._unverified_capabilities.append(f"增益 {gain:g} dB")

        text = self.ppm_edit.text().strip()
        try:
            ppm = int(text)
        except (TypeError, ValueError):
            QMessageBox.warning(
                self, "PPM 无效",
                f"“{text}”不是整数。PPM 是晶振频偏校正（百万分率），"
                "典型值 20~50，可为负。")
            return

        ident = self._current_identity()
        if ident is None:
            # 身份键拿不到就无法保证参数归属：拒绝连接并说明原因，
            # 绝不退回“各写一份副本”的老路（那正是本次要消灭的静默错值）。
            self.status_label.setText("无法确定设备身份，未发起连接")
            logger.warning("设备身份键不可用，拒绝连接：%r",
                           dev.get("label") or dev.get("driver"))
            return
        # 三个参数一次性写回唯一请求存储，载荷由 fill_payload 统一生成：
        # 另一面板选同一台设备时读到的就是这份，不再各持旧副本。
        _req_store.update(ident.key, sample_rate_hz=sr,
                          gain_db=gain, ppm=ppm)
        _req_store.fill_payload(dev, ident.key)
        # 跨进程互斥不在这里做：后端 connect() 按同一身份键先查占用，拿不到
        # 就拒绝开硬件并给出「谁占着、该怎么办」。UI 入口只负责发信号。
        # 记下「这次真要连的是哪台」：连接成功后 set_connected 用它确认身份，
        # 而不是等回调时再看下拉框（那时选中项可能已经不是这台了）。
        self._pending_connect_device = dev
        self.connect_requested.emit(dev)
        # 失败时不用面板补释放：占用由后端 connect() 统一管理。
        # 信号是同一线程的直连：emit 返回时连接流程已经走完。若 pending 还在，
        # 说明这次连接失败、没人调 set_connected(True)——必须立刻作废，
        # 否则下一次无关的连接会误用这台设备的身份，把别人的拔出算到它头上。
        # 占用登记由后端 connect 的失败路径自行归还，面板不再补一手。
        self._pending_connect_device = None

    def _on_audio_changed(self, _idx: int):
        dev_index = self.audio_combo.currentData()
        self.audio_output_changed.emit(int(dev_index if dev_index is not None else -1))
