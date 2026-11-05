# SPDX-License-Identifier: MIT
"""
MBDSDR 桌面端主窗口
====================
整合频谱显示、控制面板、状态面板、AI 对话面板。
支持主题切换、MCP 连接管理、OpenGL/软件渲染自动选择。
"""

import os
import sys
import json
import logging
import math
import threading
import time
from datetime import datetime
from typing import Optional, Dict

logger = logging.getLogger(__name__)

# 状态栏提示时长登记档（唯一口径，见 tests/test_status_message_durations.py）：
# QUICK   一眼读完的瞬时反馈（模式切换/已清空/扫频进度）
# NORMAL  普通结果（已保存/已应用/一般失败）
# IMPORTANT 需要用户注意（失败/断开/注意事项）
MSG_QUICK = 2000
MSG_NORMAL = 4000
MSG_IMPORTANT = 8000

# S8 断源感知：读线程卡死时 read_failed 可能永远发不出来（read_samples 阻塞在
# 驱动/套接字里），所以 UI 侧还要独立按数据新鲜度轮询后端 health。
# 轮询间隔 1s；超过 DATA_STALE_S 无真实数据即判掉线（AC-06.7「断源 2s 内可见」）；
# 刚建流水线的 HEALTH_GRACE_S 内不判，避免首帧延迟被误报成掉线。
HEALTH_POLL_MS = 1000
DATA_STALE_S = 2.0
#: 后端已报 stale 但迟迟不升级时，UI 侧最后的硬兜底（2 × 5s 心跳）。
#: 优先信后端 link_state=disconnected；这个值只用于「后端不报 link_state」
#: 的旧实现，避免界面永远停在「数据流中断」。
DATA_LOST_S = 10.0
HEALTH_GRACE_S = 3.0

#: 云台指令在后台线程执行。GUI 线程每拍最多等这么久收结果（快速后端仍在
#: 同一拍落地）；超过就本拍先放过，下一拍再收——绝不让云台把界面冻住。
GIMBAL_DISPATCH_GRACE_S = 0.05
#: 指令在途多久判超时并停止跟随。取略大于 rotctld 套接字超时（2.0s），
#: 让正常超时仍报它自己的原因（「超时（2.0s 未收齐应答）」）。
GIMBAL_DISPATCH_TIMEOUT_S = 2.5


def _focus_widget():
    """当前焦点控件（便于单测打桩）。"""
    from PySide6.QtWidgets import QApplication
    return QApplication.focusWidget()


#: 同 key 告警的限流时刻（monotonic 秒）。跟踪环 2 Hz，故障不限期会刷屏。
_WARN_THROTTLE_LAST: Dict[str, float] = {}


def _warn_throttled(key: str, msg: str, *args, interval_s: float = 30.0) -> None:
    """同一 key 的 warning 在 interval_s 内只落一条。

    限流只压**日志频次**，不压上报：调用方仍要把原因写进状态栏/周期记录，
    否则就成了另一种静默。
    """
    now = time.monotonic()
    if now - _WARN_THROTTLE_LAST.get(key, -math.inf) < interval_s:
        return
    _WARN_THROTTLE_LAST[key] = now
    logger.warning(msg, *args)


def _snr_signal_present(snr_db, was_present: bool,
                        enter_db: float = 8.0, exit_db: float = 5.0) -> bool:
    """星点亮 SNR 回差判定：>=8 dB 点亮，<5 dB 熄灭，中间保持原态不抖动。

    无数据（None）不点亮（was_present 不延续——跟踪环里无频谱即无信号）。
    """
    if snr_db is None:
        return False
    if snr_db >= enter_db:
        return True
    if snr_db < exit_db:
        return False
    return bool(was_present)


def _fmt_dbfs(value) -> str:
    """引擎/IQ 路径电平：dBFS、1 位小数；缺失/NaN 显「—」（U-02/N-03）。

    绝不把缺值渲染成 0——0 dBFS 是满量程，会被读成「信号很强」。
    """
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(v):
        return "—"
    if abs(v) < 0.05:          # 负零归零（N-02）
        v = 0.0
    return f"{v:.1f} dBFS"


def _fmt_rssi_mcp(value) -> str:
    """MCP 固件状态里的 RSSI：SI4732 的 dBµV（0–127，整数）。

    数据源：ai-sdr-mini-kicad/ai_sdr_mini_firmware_v0.5_WebOTA.ino:336
    ``state.si4732_rssi = resp[4];  // RSSI (0-127 dBuV)``。它与引擎 IQ 的
    dBFS 不是同一物理量，不得共用「dBFS」标签；缺值一律「—」。
    """
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(v):
        return "—"
    return f"{v:.0f} dBµV"


def format_doppler_status(st) -> str:
    """把 get_doppler_compensation_status()/doppler_status() 的读数渲染成一行。

    红线：applied_hz=None 一律显示 "—"（"不是我在施加/我不知道具体值"），
    绝不渲染成 0——0 的语义是"算出来确实是 0"，两者混了就是假读数。
    规则先命中先显示：
      error → 后端施加 → Python 基带 → 调谐器施加 → 其余（未补偿）
    """
    st = dict(st or {})
    if str(st.get("error") or "").strip():
        return "补偿失败"

    def _hz(value, digits=0):
        try:
            v = float(value)
        except (TypeError, ValueError):
            return "—"
        if abs(v) < 0.5 * 10 ** (-digits):
            v = 0.0          # N-02：不显示「-0 Hz」
        return f"{v:+.{digits}f} Hz"

    applied_by = str(st.get("applied_by") or "none").lower()
    applied = st.get("applied_hz")
    predicted = st.get("predicted_hz")
    note = f"预测 {_hz(predicted)}" if predicted is not None else ""

    if applied_by == "backend":
        return "—（由后端施加）" + (f" · {note}" if note else "")
    if applied_by == "python":
        txt = ("—" if applied is None else _hz(applied, 1)) + "（Python 基带）"
        if st.get("settling"):
            txt += "（切换中）"
        return txt
    if (applied_by == "none"
            and str(st.get("application") or "") == "tuner"
            and bool(st.get("enabled")) and predicted is not None):
        return "—（由调谐器施加）"
    if applied is None:
        txt = "—（未补偿）"  # 红线：null 绝不显示 0
    elif float(applied) == 0.0:
        txt = "0 Hz（未补偿）"
    else:
        txt = f"{_hz(applied, 1)}（未补偿）"
    reason = str(st.get("reason") or "").strip()
    return txt + (f" · {reason}" if reason else "")


def _record_segment_path(base_path: str, index: int) -> str:
    """分段落盘路径：第 1 段用原路径，其后 ``<stem>_part<N><ext>``。"""
    if index <= 1:
        return base_path
    stem, ext = os.path.splitext(base_path)
    return f"{stem}_part{index}{ext}"


def _unique_record_path(path: str) -> str:
    """同名录音（.iq 或其 .json sidecar）已存在时换名，绝不覆盖旧录音。

    文件名只精确到秒；停止后同一秒内再次开始会命中同一路径，旧实现的
    ``open(path, "wb")`` 会把上一段直接截断。这里只换名、不弹窗——
    弹窗会打断正在进行的录音，静默换名既不丢数据也不阻塞。
    """
    def _taken(p: str) -> bool:
        return os.path.exists(p) or os.path.exists(p + ".json")

    if not _taken(path):
        return path
    stem, ext = os.path.splitext(path)
    idx = 2
    while True:
        cand = f"{stem}_{idx}{ext}"
        if not _taken(cand):
            return cand
        idx += 1


def _planetarium_overlay_pos(sky_w: int, sky_h: int, strip_w: int,
                             strip_h: int, bar_rect: QRect,
                             margin: int = 12, gap: int = 8) -> tuple[int, int]:
    """浮层坐标：默认贴右上角 ``margin``；仅当真的压到 AI 命令栏时才下移。

    下移后再也不会自动回弹，所以判定必须基于当前几何而不是一次性开关：
    窗口变宽、浮层与命令栏不再相交时，下一次重排（resize 事件）回到上 12。
    """
    x = max(margin, sky_w - strip_w - margin)
    y = margin
    if bar_rect.isValid() and QRect(x, y, strip_w, strip_h).intersects(bar_rect):
        y = bar_rect.bottom() + 1 + gap
    return x, y


def _tx_unapplied_reason(note) -> str:
    """把 resolve_tx_doppler 的 reason 归成读数里的短标签（只做措辞，不算 Δ）。"""
    reason = str((note or {}).get("reason") or "").strip()
    if "已关闭" in reason:
        return "已关闭"
    if "无目标" in reason:
        return "无目标"
    if "后端补偿已接管" in reason:
        return "后端补偿已接管"
    if "未开启" in reason:
        return "补偿未开启"
    return reason or "原因未知"


#: 无 SDR 设备时的统一引导（横幅/状态栏同一份：发生了什么 + 可能原因 + 下一步）。
_NO_SDR_HINT = (
    "没找到 SDR 设备。可能原因：USB 没插好、缺驱动，或被其他程序占用。"
    "下一步：接好设备后点工具栏「连接」重试；"
    "RTL-SDR 需先用 Zadig 安装 WinUSB 驱动，远端设备可用 rtl_tcp。")


def format_tx_doppler(note, *, failed_reason=None, replay: bool = False) -> str:
    """把 resolve_tx_doppler 的 note 渲染成发射频点读数（纯展示，零 Δ 计算）。

    契约：``resolve_tx_doppler(nominal_hz, None) -> (freq_hz, note)``；
    note 里的 nominal_hz / freq_hz / delta_hz / target 是唯一真相源，
    本函数只排版，绝不用 UI 侧再算一遍偏移（否则两套数学必然分叉）。

    红线：null（未知）一律显示「—」，绝不渲染成 0；发射失败只出
    「未发射（原因）」，不得残留「已预偏」。
    MHz 用 6 位小数（1 kHz 级预偏否则看不见），Δ 保留 0.1 Hz。
    """
    if replay:
        return "未预偏（回放，保持原频率）"
    failed = str(failed_reason or "").strip()
    if failed:
        return f"未发射（{failed}）"
    if not isinstance(note, dict) or not note:
        return "—"

    def _mhz(value) -> str:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return "—"
        if not math.isfinite(v):
            return "—"
        if abs(v) < 0.5:      # N-02：负零/亚 Hz 残差不写成「-0.000000」
            v = 0.0
        return f"{v / 1e6:.6f} MHz"

    nominal = note.get("nominal_hz")
    freq = note.get("freq_hz")
    if nominal is None or freq is None:
        return "—"

    if bool(note.get("applied")):
        delta = note.get("delta_hz")
        try:
            dv = float(delta)
            if not math.isfinite(dv):
                raise ValueError
            if abs(dv) < 0.05:
                dv = 0.0       # N-02：不显示「-0.0 Hz」
            delta_txt = f"{dv:+.1f} Hz"
        except (TypeError, ValueError):
            delta_txt = "—"  # 未知偏移不编造成 0
        mode_txt = "显式" if str(note.get("mode") or "") == "on" else "自动"
        head = f"发波 {_mhz(freq)}（已预偏 {delta_txt}，{mode_txt}）"
        tail = f"标称 {_mhz(nominal)}"
        target = str(note.get("target") or "").strip()
        if target:
            tail += f"，目标 {target}"
        return f"{head} · {tail}"

    return (f"发波 {_mhz(freq)}（未预偏：{_tx_unapplied_reason(note)}）"
            f" · 标称 {_mhz(nominal)}")


def ptt_state(backend) -> tuple[str, bool, bool]:
    """PTT 常驻能力文案（docs/doppler-compensation.md §3.8.2）。

    返回 ``(文案, 是否可点, 是否带常驻警示)``。唯一文案事实源是
    ``backend.tx_capability()``；前两步（类上有写流实现 / 设备声明）只用于
    cap 缺字段时兜底，**绝不据此下「没有 TX 通道」的结论**——「驱动级可发、
    本路径未接入」与「硬件仅接收」必须分开显示。``verified`` 只读不推。
    """
    if backend is None:
        return "本后端发射：未连接设备（能力未知）", False, False

    impl = callable(getattr(type(backend), "write_tx", None))
    supports = getattr(backend, "supports_tx", None)
    declared = bool(supports()) if callable(supports) else False
    cap: dict = {}
    get_cap = getattr(backend, "tx_capability", None)
    if callable(get_cap):
        try:
            raw = get_cap()
        except Exception as e:  # noqa: BLE001 - 能力查询失败按「未知」降级，但要留证
            logger.warning("读取发射能力矩阵失败: %s", e)
            raw = None
        if isinstance(raw, dict):
            cap = raw
        elif raw is not None:
            logger.warning("发射能力矩阵格式异常（应为 dict）: %r", raw)

    impl = bool(cap.get("implemented", impl))
    declared = bool(cap.get("declared", declared))
    driver = cap.get("driver_capable")
    ready = bool(cap.get("ready"))
    verified = bool(cap.get("verified"))
    reason = str(cap.get("blocked_reason") or "")
    if "connected" in cap:
        connected = cap.get("connected")
    else:
        st = getattr(backend, "status", None)
        connected = getattr(st, "connected", None)

    # 判定顺序是显式的（F2）：先按「实现」分叉，再看「就绪」；
    # 不允许「声明 / 驱动可发」这些旁证把已实现的路径说成未实现。
    if not impl:
        if declared:
            return "本后端发射：仅有声明 / 未实现（不可发射）", False, False
        if driver is True:
            return "本后端发射：驱动级可发 / 本路径未接入（不可发射）", False, False
        if driver is False:
            # F3：硬件真不能发（换设备）与软件没实现（等版本）是两条行动建议
            return "本后端发射：本设备硬件不支持发射（驱动报告 0 个 TX 通道）", False, False
        if reason == "driver_capability_unknown":
            return "本后端发射：能力未知（未连接/驱动未报告）", False, False
        return "本后端发射：本后端尚未实现发射（该驱动没有 TX 代码路径）", False, False

    if not ready:
        # F1：HackRF/USRP/Pluto 有 write_tx，只是没连设备——插上就能发，
        # 不能显示成「未实现」，否则用户会以为这台机器根本不能发射。
        if connected is False:
            return "本后端发射：已实现，但设备未连接（连上即可发射）", False, False
        return "本后端发射：已实现，但当前不可发射（未就绪）", False, False

    if verified:
        return "本后端发射：驱动级已实现 / 真机已验证", True, False
    return "本后端发射：驱动级已实现 / 真机未验证", True, True


def _is_text_entry_widget(w) -> bool:
    """是否为文本输入类控件（Ctrl+C 应走标准复制语义）。"""
    if w is None:
        return False
    from PySide6.QtWidgets import (
        QAbstractSpinBox, QComboBox, QLineEdit, QPlainTextEdit, QTextEdit,
    )
    if isinstance(w, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
        return True
    if isinstance(w, QComboBox) and w.isEditable():
        return True
    return False

import l10n   # i18n 加载层（Qt Linguist）；同级裸导入，双上下文由 desktop/__init__ 垫片保证

import numpy as np

from PySide6.QtCore import (
    Qt, QTimer, Slot, QDateTime, QTimeZone, QThread, Signal, QSettings, QRect,
    QPoint,
)
from PySide6.QtGui import (
    QAction, QActionGroup, QKeySequence, QFont, QShortcut, QGuiApplication,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTabWidget, QStatusBar, QToolBar, QMenuBar, QMenu, QLabel,
    QFileDialog, QMessageBox, QInputDialog, QComboBox, QPushButton,
    QFrame, QSizePolicy, QDialog, QDialogButtonBox, QLineEdit,
    QGroupBox, QFormLayout, QSlider, QSpinBox, QCheckBox,
    QDoubleSpinBox, QProgressDialog, QDockWidget, QScrollArea,
    QStackedWidget, QToolButton, QStyle, QToolTip,
)

# 确保能导入同目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 确保能导入仓库根下的 mbdsdr_ai 包（baseband_io / audio_out / sdr_backend）
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from themes import get_theme, THEMES, DEFAULT_THEME
from shortcuts import bind as _key
from spectrum_widget import (
    create_spectrum_widget, HAS_OPENGL, owns_local_shortcuts)
from control_panel import ControlPanel
# 错误码 → 帮助「怎么办」入口（无对应条目的 code 不出链接，见 error_remedy）
from error_remedy import (
    RemedyLinkLabel, attach_remedy_link, classify_connect_error,
    classify_record_dir_error,
)
from mode_registry import (
    default_bandwidth,
    audio_cutoff,
    demodulate,
    get as get_mode,
)
from status_panel import StatusPanel
from ai_panel import AIPanel
from mcp_worker import MCPWorkerManager
from mcp_protocol import GATE_PARAM, is_write_tool
from rf_sky_view import RFSkyView, SkyObject, AntennaPointing, HeatmapCell, SatelliteTracker
from module_panel import ModulePanel
# CarWith 设计体系：token 单例 / 面板注册表 / Dock 布局管理器
from tokens import tokens
from panel_registry import registry
from dock_layout_manager import DockLayoutManager
from constants import DEFAULT_SAMPLE_RATE_HZ, format_sample_rate
# 气象云图面板（懒加载：构造时只建 Qt 控件，后端 ToolRegistry
# 在用户首次点解码时才创建，避免拖慢启动）。
from weather_panel import WeatherPanel
from sat_track_panel import SatTrackPanel
from new_spacetime_panel import NewSpacetimePanel
from adsb_map_panel import AdsbMapPanel
# 内核能力面板（扫频 / 自动调制识别 / 书签管理 / 远程服务设置）。
# 用 try/except 守卫：内核模块缺失时面板降级为不可用，绝不拖垮启动。
try:
    from scanner_panel import ScannerPanel
except Exception:  # pragma: no cover
    ScannerPanel = None  # type: ignore
try:
    from modulation_panel import ModulationPanel
except Exception:  # pragma: no cover
    ModulationPanel = None  # type: ignore
try:
    from bookmark_panel import BookmarkPanel
except Exception:  # pragma: no cover
    BookmarkPanel = None  # type: ignore
try:
    from settings_panel import ServiceSettingsPanel, SettingsPanel
except Exception:  # pragma: no cover
    ServiceSettingsPanel = None  # type: ignore
    SettingsPanel = None  # type: ignore
# 新增内核能力面板（多 VFO / ANR / 星座图 / 设备选择 / 分段增益）。
# try/except 守卫：面板文件缺失时主窗口不崩，对应 dock 不建。
try:
    from vfo_panel import VfoPanel
except Exception:  # pragma: no cover
    VfoPanel = None  # type: ignore
try:
    from anr_panel import AnrPanel
except Exception:  # pragma: no cover
    AnrPanel = None  # type: ignore
try:
    from constellation_panel import ConstellationPanel
except Exception:  # pragma: no cover
    ConstellationPanel = None  # type: ignore
try:
    from device_panel import DevicePanel
except Exception:  # pragma: no cover
    DevicePanel = None  # type: ignore
try:
    from gain_panel import GainPanel
except Exception:  # pragma: no cover
    GainPanel = None  # type: ignore
# 数字信号解码面板：ADS-B 航路图 / APRS 包 / 卫星云图。
# try/except 守卫：面板文件缺失时主窗口不崩，对应 dock 不建。
try:
    from adsb_panel import AdsbPanel
except Exception:  # pragma: no cover
    AdsbPanel = None  # type: ignore
try:
    from aprs_panel import AprsPanel
except Exception:  # pragma: no cover
    AprsPanel = None  # type: ignore
try:
    from satellite_image_panel import SatelliteImagePanel
except Exception:  # pragma: no cover
    SatelliteImagePanel = None  # type: ignore
# 卫星闭环跟踪器（mbdsdr_ai.sat_tracker）：选中卫星->实时 az/el/多普勒->自动调谐。
# 用别名避免与 rf_sky_view 里的展示型 SatelliteTracker 冲突。缺 skyfield 时降级。
try:
    from mbdsdr_ai.sat_tracker import SatelliteTracker as LoopSatTracker
except Exception:  # pragma: no cover
    LoopSatTracker = None  # type: ignore
# 运行参数持久化（频率/增益/模式/带宽/采样率/呼号/AGC/主题 落 JSON，退出不丢）
from settings import DesktopSettings, volume_pct_to_hw, volume_hw_to_pct
# 触屏/Surface/平板二合一适配：单例管理器 + 触控目标/手势辅助。
from touch_manager import touch_manager
from touch_helpers import (enable_touch_events, apply_touch_target,
                           apply_touch_targets)

# 声卡实时输出（可选依赖，导入失败也不拖垮 GUI）
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
try:
    # 静噪滑杆初值的唯一权威来源（滑杆刻度 -100..-20，-50 ≈ 6 dB 余量）
    from mbdsdr_ai.squelch import DEFAULT_SQUELCH_DBFS
except Exception:  # pragma: no cover
    DEFAULT_SQUELCH_DBFS = None  # type: ignore


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

    # AI 工具环内执行完硬件调用后的 UI 同步（MCP handler 跑在 AI 后台线程，
    # 经 Qt 信号排队回 UI 线程改控件，跨线程安全）
    _ai_tool_done = Signal(str, dict)
    # AI 后台线程 → UI 线程的人工确认请求（payload dict，含 event/approved）。
    # 用 queued 信号而不是直接调 QMessageBox：MCP handler 在 AI 线程里跑。
    _human_approval_requested = Signal(object)
    # 发射线程 → UI 线程的收尾（TxSession.status() 字典），跨线程改按钮必须走信号
    _tx_finished = Signal(object)

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
        # 最小窗口 1000×600：用户明确要求该档可用（不叠字、无死黑）；
        # splitter minSizeHint ≈ 982 < 1000，可容纳。
        self.setMinimumSize(1000, 600)
        self.resize(1400, 900)

        # 状态
        self._current_theme = DEFAULT_THEME
        # 运行参数持久化：启动即读回上次频率/增益/模式/带宽/采样率/呼号/AGC/主题。
        # _restoring_settings=True 期间 _apply_theme 不回写配置，避免启动默认主题覆盖
        # 已读回的主题；控件由专用 setters（内部 blockSignals）回填，不触发 save。
        self._restoring_settings = True
        self.settings = DesktopSettings.load()
        # ---- 触屏/Surface/平板适配：从设置读 touch_mode(auto/on/off) 并应用 ----
        # 在构建 UI 之前完成：控件构造期 apply_touch_target 即可按当前模式决定
        # 最小高度；之后模式切换由 _on_touch_mode_changed 重新适配。
        touch_manager().set_from_settings(
            str(self.settings.get("touch_mode", "auto")))
        touch_manager().touch_mode_changed.connect(self._on_touch_mode_changed)
        self._worker_manager = MCPWorkerManager()
        self._worker: Optional[object] = None
        # AI 工具环内完成的硬件调用 → 回 UI 线程同步控件
        self._ai_tool_done.connect(self._sync_ui_for_tool)
        # 人工确认门：AI 后台线程发请求，UI 线程弹窗/显式确认后回填结果。
        # 模型自带的 arm_manual 一律作废——确认只能来自真人（S13）。
        self._human_approval_requested.connect(self._on_human_approval_requested)
        # 发射线程收尾（恢复接收/复位按钮）必须排队回 UI 线程
        self._tx_finished.connect(self._on_tx_finished)
        self._approval_handler = None      # 测试注入：可替换弹窗，签名 (tool, args) -> bool
        # S11：转发到硬件的工具参数先记账，等 tool_result 确认成功后才同步 UI
        self._pending_ui_sync: dict = {}
        self._approval_timeout_s = 180.0   # 无人应答超时＝拒绝
        self._approval_audit_path = os.path.join(
            os.path.expanduser("~"), ".mbdsdr", "approval_audit.jsonl")
        self._record_timer: Optional[QTimer] = None
        self._record_seconds = 0
        self._sky_update_timer: Optional[QTimer] = None
        # 后端掉线处置（读线程空读 + status 核实/持续空读升级）
        self._backend_lost_handled = False
        self._read_fail_signals = 0
        # S8 轮询只在"本段连接确实来过数据"之后才判掉线：首帧前的空窗交给
        # read_failed 信号与宽限期处理，避免把"还没开始出数"误报成掉线。
        self._health_has_data = False
        # 链路降级态（stale：连接仍在但无数据）在界面上的呈现状态。
        # 不能把它当掉线拆连接，也不能继续显示「已连接」。
        self._link_degraded = False
        self._link_degraded_announced = False
        self._link_degraded_code = ""
        self._link_normal_text = ""
        # 损坏配置的处置状态：已备份才允许写回；备份失败则拒绝覆盖原文件
        self._gui_config_corrupt = False
        self._gui_config_corrupt_backup = None

        # 观测站坐标：默认 None 表示“未配置观测站位置”（不再硬编码示例坐标）。
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
        self._sat_signal_present: bool = False    # 星点亮 SNR 回差状态
        # ---- 云台跟随（阶段4）：与跟踪环同拍驱动天线指向 ----
        # 复用 sdr_tools 全局 GimbalController 单例（ai-sdr-mini WebSocket
        # 连接成功时 sdr_backend 自动注入板载通道；rotctld/manual 由 AI
        # 工具配置）。_gimbal_follow=True 时 _poll_satellite_track 每拍
        # 下发卫星 az/el；停跟踪单点停止。
        self._gimbal_follow = False
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
        self._record_sr: float = float(DEFAULT_SAMPLE_RATE_HZ)
        self._record_center_hz: float = 0.0
        self._record_guard: bool = False  # 同步两个录音按钮时防重入
        # sidecar 元数据（开始录制时从真实后端捕获，停止落盘时写入 .json）
        self._record_start_time: str = ""
        self._record_gain_db: float = 0.0
        self._record_device: str = ""
        self._record_driver: str = ""
        # 分段落盘（S4）：录音中改采样率 → 关旧段、开新段，绝不混速率写同一文件。
        self._record_session_path: Optional[str] = None
        self._record_segments: list = []
        self._record_segment_index: int = 1
        self._record_segment_start_time: str = ""
        # 声卡实时输出（sounddevice 可选；无设备时 available=False 安全降级）
        self._audio_player = None
        if AudioPlayer is not None:
            try:
                self._audio_player = AudioPlayer(sample_rate=48000, channels=1, gain=0.5)
            except Exception:
                self._audio_player = None
        # 静噪门限：滑杆刻度 -100..-20，值是「相对该模式噪声基线的余量」，
        # 不是绝对 dBFS（初值只认 mbdsdr_ai.squelch 的权威常量）。
        self._squelch_db: float = (
            float(DEFAULT_SQUELCH_DBFS) if DEFAULT_SQUELCH_DBFS is not None else 0.0)
        # 回放静噪基线（懒建；每次 _replay_recording 起点重置）
        self._replay_squelch = None
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
        # S8：数据流健康轮询（只在流水线运行期间启动，拆流水线即停）
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(HEALTH_POLL_MS)
        self._health_timer.timeout.connect(self._on_health_tick)
        self._health_grace_until = 0.0
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

        # ---- 最小发射面（PTT）：真人确认 + 一次性 token + TxSession 状态机 ----
        # 只有「设备有 write_tx 通道 + 人工确认通过」才允许发波；无硬件时按钮置灰。
        self._tx_session = None          # 正在发射的 TxSession（None = 空闲）
        self._tx_thread: Optional[threading.Thread] = None
        self._tx_button_guard: bool = False  # 程序改按钮选中态时防重入

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
        # 最近一次 IQ 功率估计（dBFS），供状态栏/S-meter 复用；None=还没测到
        self._last_dbfs: Optional[float] = None
        # 无设备时频谱上方引导提示标签（_build_central_widget 中创建）
        self._no_device_hint: Optional[QLabel] = None

        # 构建 UI
        self._build_menu_bar()
        # 应用级事件过滤：Ctrl+C 焦点感知（文本框内放行复制，见 eventFilter）
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self._build_tool_bar()
        self._build_central_widget()
        self._build_status_bar()

        # 恢复上次退出时的窗口几何与右侧坞布局（QDockWidget 由 QMainWindow 管理）
        self._restore_window_state()

        # 启动即无硬件：所有 SDR 操作控件（调谐/音量/模式/录音/面板实时按钮）置灰
        self._panels_set_sdr_connected(False)

        # 应用默认主题
        self._apply_theme(DEFAULT_THEME)

        # 用读回的运行参数回填控件（专用 setter 内部 blockSignals，不触发 save/后端）。
        # 放在 _panels_set_sdr_connected(False) 之后，避免恢复出的频率显示被清成 "--"。
        self._apply_restored_settings()

        # 延迟启动项一律挂成 self 的子定时器（不用 QTimer.singleShot 静态版）：
        # 静态 singleShot 造出的定时器无 QObject parent，关窗时扫不到、停不掉，
        # 会在窗口关闭之后才打进来（_auto_enumerate_and_connect 甚至会去连真实
        # 硬件、往已关窗口的状态栏写提示）。挂成子对象后由
        # _release_runtime_resources 统一停掉。
        self._startup_timers: list = []
        for _ms, _fn in ((100, self._load_gui_config),
                         (200, self._auto_enumerate_and_connect),
                         (1200, self._update_sky_satellites)):
            _t = QTimer(self)
            _t.setSingleShot(True)
            _t.setInterval(_ms)
            _t.timeout.connect(_fn)
            _t.start()
            self._startup_timers.append(_t)

        # 天空图实时更新定时器（每 5 秒刷新卫星位置 + 授时）
        self._sky_update_timer = QTimer(self)
        self._sky_update_timer.setInterval(5000)
        self._sky_update_timer.timeout.connect(self._update_sky_satellites)
        self._sky_update_timer.start()

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

        # ---- 触屏适配：UI 全部构建完成后，统一开启触控事件 / 放大触控目标 /
        # 加宽 splitter 手柄与滚动条。鼠标模式下这些操作零副作用（helper 内部判断）。
        self._apply_touch_adaptation()

    # ========================================================================
    # 触屏 / Surface / 平板适配
    # ========================================================================
    def _apply_touch_adaptation(self) -> None:
        """按当前 touch_mode 重新适配整窗触控尺寸与手势热区。

        鼠标模式下：apply_touch_target 直接 return、splitter 恢复 tokens 默认 4px、
        不加触控 QSS——桌面手感与之前完全一致。触屏模式下：
          * 主窗口 / 中央 Tab / DockWidget 开启 WA_AcceptTouchEvents；
          * 递归把所有 QPushButton/QComboBox/QLineEdit/QSpinBox 最小高度提到 ≥44px；
          * 三栏 splitter 与频谱内 splitter 手柄加宽到 10px；
          * 追加触控 QSS（滚动条 14px / slider handle ≥20px / :pressed 反馈）。
        """
        tm = touch_manager()
        # 1) 全局触屏事件接受
        enable_touch_events(self)
        for tab in (getattr(self, "left_tab", None),
                    getattr(self, "right_tab", None)):
            if tab is not None:
                enable_touch_events(tab)
        try:
            for dock in self.findChildren(QDockWidget):
                enable_touch_events(dock)
        except Exception:
            pass

        # 2) 递归放大所有可交互控件的触控目标（内部按 is_touch_mode 旁路）
        apply_touch_targets(self)

        # 3) Splitter 手柄加宽（触屏 10px / 鼠标 4px = tokens 默认）
        handle_w = tm.splitter_handle()
        for splitter in self.findChildren(QSplitter):
            try:
                splitter.setHandleWidth(handle_w)
            except Exception:
                pass

        # 4) 长列表触屏滚动：QComboBox 下拉视图按像素滚动 + 触屏行高
        try:
            from PySide6.QtWidgets import QListView, QAbstractItemView
            row_h = tm.list_row_height()
            for combo in self.findChildren(QComboBox):
                view = combo.view()
                if view is not None:
                    view.setVerticalScrollMode(
                        QAbstractItemView.ScrollMode.ScrollPerPixel)
                    if row_h > 0:
                        view.setStyleSheet(
                            f"QListView::item {{ height: {row_h}px; }}")
        except Exception:
            pass

        # 5) 触控 QSS（滚动条加宽 / slider handle 加大 / pressed 反馈）
        self._apply_touch_stylesheet()

    def _apply_touch_stylesheet(self) -> None:
        """追加/移除触屏模式专属 QSS。鼠标模式下追加空串（恢复主题默认）。"""
        tm = touch_manager()
        extra = ""
        if tm.is_touch_mode():
            sb_w = tm.scrollbar_width()       # 触屏 14 / 鼠标 10
            extra = (
                f"QScrollBar:vertical {{ width: {sb_w}px; }}"
                f"QScrollBar:horizontal {{ height: {sb_w}px; }}"
                "QScrollBar::handle:vertical { min-height: 44px; }"
                "QScrollBar::handle:horizontal { min-width: 44px; }"
                "QSlider::handle:horizontal {"
                " width: 24px; height: 24px; margin: -8px 0; }"
                "QSlider::handle:vertical {"
                " height: 24px; width: 24px; margin: 0 -8px; }"
                "QPushButton#iconButton:pressed {"
                " background-color: rgba(255,255,255,0.25); }"
                "QPushButton#iconButton:checked {"
                " background-color: rgba(145,156,172,0.5); }"
                "QPushButton:pressed {"
                " background-color: rgba(0,0,0,0.18); }"
            )
        # 追加到当前 app 级 QSS 之上；_apply_theme 设置的基础 QSS 保持不变。
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                base = getattr(self, "_theme_qss", "")
                target = base + extra
                # 幂等：app 级 setStyleSheet 重抛光全部控件（成本 O(全部控件)，
                # 见 Theme.apply 的说明）。触屏模式切换时每个存亡窗口的槽都会
                # 调到这里，不跳过就是 O(N²)——tests/ 根 90% 处挂死的真因。
                # 内容相同则跳过：重复应用不改变任何视觉表现。
                if app.styleSheet() != target:
                    app.setStyleSheet(target)
        except Exception:
            pass

    def _on_touch_mode_changed(self, on: bool) -> None:
        """touch_manager.touch_mode_changed 槽：模式切换后重新适配全窗触控尺寸。

        注意：这里不回写 settings——auto 模式下检测结果变化不应把用户的
        "auto" 偏好覆盖成 "on"/"off"；持久化由设置面板的显式选择负责。
        """
        self._apply_touch_adaptation()

    def _set_touch_mode(self, mode: str) -> None:
        """触屏模式显式切换入口（视图→触屏模式）：落 settings 并重新适配。

        这是"设置面板/菜单的显式选择"那个此前缺失的入口；自动检测结果
        变化不走这里，不会覆盖用户的 auto 偏好。
        """
        if mode not in ("auto", "on", "off"):
            return
        self.settings.set("touch_mode", mode)
        touch_manager().set_from_settings(mode)
        label = {"auto": "自动检测", "on": "强制触屏", "off": "强制鼠标"}[mode]
        self.statusBar().showMessage(f"触屏模式：{label}", MSG_QUICK)

    # ========================================================================
    # UI 构建
    # ========================================================================

    def _build_menu_bar(self):
        """构建菜单栏。"""
        menubar = self.menuBar()

        # 文件菜单
        file_menu = menubar.addMenu("文件(&F)")

        connect_action = QAction("连接设备...", self)
        connect_action.setShortcut(QKeySequence(_key("file.connect")))
        connect_action.triggered.connect(self._connect_dialog)
        file_menu.addAction(connect_action)

        disconnect_action = QAction("断开连接", self)
        disconnect_action.setShortcut(QKeySequence(_key("file.disconnect")))
        disconnect_action.triggered.connect(self._disconnect)
        file_menu.addAction(disconnect_action)

        file_menu.addSeparator()

        exit_action = QAction("退出", self)
        exit_action.setShortcut(QKeySequence(_key("file.quit")))
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

        # 触屏模式（auto/on/off）：补上此前缺失的切换入口。设计上 _on_touch_mode_
        # changed 早已预留重新适配路径，但一直没有 UI，用户只能手改 JSON 文件。
        touch_menu = view_menu.addMenu("触屏模式")
        touch_group = QActionGroup(self)
        touch_group.setExclusive(True)
        current_tm = str(self.settings.get("touch_mode", "auto") or "auto")
        for mode_key, label in (("auto", "自动检测"), ("on", "强制触屏"),
                                ("off", "强制鼠标")):
            act = QAction(label, self)
            act.setCheckable(True)
            act.setChecked(current_tm == mode_key)
            act.triggered.connect(
                lambda _c, m=mode_key: self._set_touch_mode(m))
            touch_group.addAction(act)
            touch_menu.addAction(act)

        view_menu.addSeparator()

        waterfall_action = QAction("显示瀑布图", self)
        waterfall_action.setShortcut(QKeySequence(_key("view.waterfall")))
        waterfall_action.setCheckable(True)
        waterfall_action.setChecked(True)
        waterfall_action.triggered.connect(self._toggle_waterfall)
        view_menu.addAction(waterfall_action)

        fullscreen_action = QAction("全屏", self)
        fullscreen_action.setShortcut(QKeySequence(_key("view.fullscreen")))
        fullscreen_action.triggered.connect(self._toggle_fullscreen)
        view_menu.addAction(fullscreen_action)

        # B2：外接屏拔掉后窗口可能落在屏幕外，这里给一个应用内复位入口
        reset_pos_action = QAction("重置窗口位置", self)
        reset_pos_action.setToolTip("把主窗口移回主屏中央（窗口跑到屏幕外时用这个）")
        reset_pos_action.triggered.connect(self._on_reset_window_position)
        view_menu.addAction(reset_pos_action)

        # 全天模式（天空视图自带独立 ViewState + 立体投影 160°）。校验态与视图
        # 状态双向同步；A 键作用域限定在天空视图控件内（与视图内 A 键同源），
        # 避免在输入框里按 A 也切图。
        all_sky_action = QAction("全天模式（160°）", self)
        all_sky_action.setCheckable(True)
        all_sky_action.setShortcut(QKeySequence(_key("view.all_sky")))
        all_sky_action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        all_sky_action.setToolTip(
            "天空视图立体投影全天模式（视场 160°）；视图内按 A 切换，Esc 先退全天")
        all_sky_action.triggered.connect(self._on_all_sky_action_triggered)
        view_menu.addAction(all_sky_action)
        self._all_sky_action = all_sky_action

        # 星象仪模式：顶层第二形态（全屏天空）。checkable 与当前形态同步。
        planetarium_action = QAction("星象仪模式", self)
        planetarium_action.setShortcut(QKeySequence(_key("view.planetarium")))
        planetarium_action.setCheckable(True)
        planetarium_action.setToolTip(
            "切换到星象仪形态：整个天空铺满屏幕，点星即看/即听")
        planetarium_action.triggered.connect(self._toggle_planetarium_mode)
        view_menu.addAction(planetarium_action)
        self._planetarium_action = planetarium_action
        # Esc 仅星象仪形态下生效：先关 AI 回复卡（弹层），再退出回 IDE；
        # IDE 形态不劫持 Esc（留给对话框/输入框的默认取消语义）。
        self._planetarium_esc = QShortcut(
            QKeySequence(_key("view.planetarium_exit")), self)
        self._planetarium_esc.activated.connect(self._on_escape)
        self._planetarium_esc.setEnabled(False)

        # 工具菜单
        tools_menu = menubar.addMenu("工具(&T)")

        sweep_action = QAction("FM 扫频找台", self)
        sweep_action.setShortcut(QKeySequence(_key("tools.sweep")))
        sweep_action.triggered.connect(self._start_sweep)
        sweep_action.setEnabled(False)  # 无设备时置灰，连接后启用
        self._sweep_action = sweep_action
        tools_menu.addAction(sweep_action)

        record_action = QAction("开始/停止录音", self)
        record_action.setShortcut(QKeySequence(_key("tools.record")))
        record_action.triggered.connect(self._toggle_record)
        tools_menu.addAction(record_action)

        tools_menu.addSeparator()

        ntrip_action = QAction("NTRIP 设置", self)
        ntrip_action.setShortcut(QKeySequence(_key("tools.ntrip")))
        ntrip_action.triggered.connect(self._open_ntrip_dialog)
        tools_menu.addAction(ntrip_action)

        tools_menu.addSeparator()

        # 真实串口 GNSS：手动选择串口号/波特率后打开 NMEA 流
        gnss_action = QAction("连接 GNSS...", self)
        gnss_action.setShortcut(QKeySequence(_key("tools.gnss_connect")))
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
        # 跟踪未运行时置灰：点下去走空闲早退、界面毫无变化＝假活。
        # 状态统一由 _sync_tracking_ui 出口刷新（不另存第二份布尔）。
        sat_stop_action.setEnabled(False)
        sat_stop_action.setToolTip(
            "现在没有正在跟踪的卫星，所以此项目前不可用；"
            "先到「卫星跟踪」页签选一颗卫星再开始跟踪")
        tools_menu.addAction(sat_stop_action)
        self._sat_stop_action = sat_stop_action

        sat_tle_action = QAction("更新卫星 TLE (Celestrak)", self)
        sat_tle_action.triggered.connect(self._on_update_sat_tle)
        tools_menu.addAction(sat_tle_action)

        tools_menu.addSeparator()

        about_action = QAction("关于 MBDSDR", self)
        about_action.setShortcut(QKeySequence(_key("tools.about")))
        about_action.triggered.connect(self._show_about)
        tools_menu.addAction(about_action)

        # 帮助菜单：可发现性入口。内容全部从 shortcuts.py / 注册表实时生成，
        # 不在这里手抄键位或能力清单。
        help_menu = menubar.addMenu("帮助(&H)")

        quick_start_action = QAction("三步上手", self)
        quick_start_action.triggered.connect(lambda: self._show_help_panel(0))
        help_menu.addAction(quick_start_action)

        catalog_action = QAction("能力目录", self)
        catalog_action.triggered.connect(lambda: self._show_help_panel(1))
        help_menu.addAction(catalog_action)

        shortcuts_action = QAction("快捷键一览", self)
        shortcuts_action.triggered.connect(self._show_shortcuts_panel)
        help_menu.addAction(shortcuts_action)

        # 全局命令面板：一个搜索框定位菜单命令/页签/能力/快捷键。
        palette_action = QAction("命令面板", self)
        palette_action.setShortcut(QKeySequence(_key("help.command_palette")))
        palette_action.setToolTip(
            "用一个搜索框找到菜单命令、面板页签、能力和快捷键；"
            "想不起入口在哪时按 Ctrl+K")
        palette_action.triggered.connect(self._show_command_palette)
        help_menu.addAction(palette_action)

    def _build_tool_bar(self):
        """构建工具栏（SDR++ 风格：大触控目标，前置频率/模式/音量，右侧折叠坞菜单）。"""
        toolbar = QToolBar("主工具栏")
        toolbar.setObjectName("mainToolBar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        # 坞面板折叠按钮（汉堡菜单）：一键显示/隐藏右侧控制/状态/AI 坞
        self.toggle_docks_btn = QPushButton("☰")
        self.toggle_docks_btn.setMinimumWidth(28)
        self.toggle_docks_btn.setMaximumWidth(48)
        self.toggle_docks_btn.setMinimumHeight(28)
        self.toggle_docks_btn.setToolTip("显示 / 隐藏右侧面板坞")
        self.toggle_docks_btn.clicked.connect(self._toggle_right_docks)
        toolbar.addWidget(self.toggle_docks_btn)

        # 连接状态
        self.conn_label = QLabel("  状态: 未连接  ")
        self.conn_label.setStyleSheet("font-weight: 600;")
        toolbar.addWidget(self.conn_label)

        toolbar.addSeparator()

        # 连接按钮（未连接时高亮引导用户点击；连接成功后取消高亮）
        self.connect_btn = QPushButton(self.tr("连接"))
        self.connect_btn.setMinimumHeight(28)
        self.connect_btn.setStyleSheet(self._connect_button_qss())
        self.connect_btn.clicked.connect(self._connect_dialog)
        toolbar.addWidget(self.connect_btn)

        self.disconnect_btn = QPushButton(self.tr("断开"))
        self.disconnect_btn.setMinimumHeight(28)
        self.disconnect_btn.clicked.connect(self._on_disconnect_clicked)
        self.disconnect_btn.setEnabled(False)
        toolbar.addWidget(self.disconnect_btn)

        toolbar.addSeparator()

        # 大字号频率显示（等宽字体 bold；由 _update_status_bar 从真实后端同步）
        self.toolbar_freq_label = QLabel("—")
        self.toolbar_freq_label.setStyleSheet(
            "font-family: 'Consolas','Menlo',monospace; font-size:14pt;"
            " font-weight:700; padding:0 6px;")
        self.toolbar_freq_label.setToolTip("当前中心频率（实时从后端读取）")
        toolbar.addWidget(self.toolbar_freq_label)

        # 解调模式下拉（与控制面板 mode_combo 同源：改它走既有信号链切解调）
        self.toolbar_mode_combo = QComboBox()
        self.toolbar_mode_combo.addItems(
            [m for m in ["FM", "WFM", "NFM", "AM", "USB", "LSB", "CW"] if get_mode(m)])
        self.toolbar_mode_combo.setMinimumHeight(28)
        self.toolbar_mode_combo.setToolTip(
            "切换当前信号的解调方式；听模拟语音用 FM/NFM，听短波/航空用 AM")
        self.toolbar_mode_combo.currentTextChanged.connect(
            self._on_toolbar_mode_selected)
        toolbar.addWidget(self.toolbar_mode_combo)

        toolbar.addSeparator()

        # 主题切换
        toolbar.addWidget(QLabel(self.tr(" 主题: ")))
        self.theme_combo = QComboBox()
        for name, theme in THEMES.items():
            self.theme_combo.addItem(theme.display_name, name)
        self.theme_combo.setMinimumHeight(28)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_combo_changed)
        toolbar.addWidget(self.theme_combo)

        # UI 语言（i18n）：语言自称不翻译；持久化 ui_language，诚实提示重启生效
        # （翻译器只在启动时装载——与 C++ 桌面端同一口径，不做假实时切换）。
        toolbar.addWidget(QLabel(self.tr(" 语言: ")))
        self.lang_combo = QComboBox()
        for tag in l10n.SUPPORTED_UI_LANGUAGES:
            self.lang_combo.addItem(l10n.LANGUAGE_SELF_NAMES[tag], tag)
        self.lang_combo.setMinimumHeight(28)
        self.lang_combo.setToolTip(self.tr("更改语言后重启应用生效"))
        self.lang_combo.currentIndexChanged.connect(self._on_lang_combo_changed)
        toolbar.addWidget(self.lang_combo)

        toolbar.addSeparator()

        # 布局预设切换（动态：内置 专注/分析/网格 + 用户自定义，启动时填充）
        toolbar.addWidget(QLabel(" 布局: "))
        self.layout_combo = QComboBox()
        self.layout_combo.setMinimumHeight(28)
        self.layout_combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.layout_combo.setToolTip("切换布局预设（可自由拖动，自动持久化）")
        self.layout_combo.currentIndexChanged.connect(self._on_layout_preset_changed)
        toolbar.addWidget(self.layout_combo)

        # 保存当前布局为自定义预设
        self.layout_save_btn = QPushButton("保存")
        self.layout_save_btn.setMinimumHeight(28)
        self.layout_save_btn.setShortcut(QKeySequence(_key("layout.save")))
        self.layout_save_btn.setToolTip("把当前布局（页签顺序/三栏比例/面板可见性）存为自定义预设")
        self.layout_save_btn.clicked.connect(self._on_save_layout)
        toolbar.addWidget(self.layout_save_btn)

        # 删除当前用户预设（内置预设置灰）
        self.layout_delete_btn = QPushButton("删除")
        self.layout_delete_btn.setMinimumHeight(28)
        self.layout_delete_btn.setToolTip("删除当前选中的自定义布局（内置布局不可删）")
        self.layout_delete_btn.clicked.connect(self._on_delete_layout)
        toolbar.addWidget(self.layout_delete_btn)

        toolbar.addSeparator()

        # 多 VFO：新建 VFO 按钮（在当前频谱中心建一个次听 VFO）
        self.new_vfo_btn = QPushButton("+ VFO")
        self.new_vfo_btn.setMinimumHeight(28)
        self.new_vfo_btn.setShortcut(QKeySequence(_key("vfo.new")))
        self.new_vfo_btn.setToolTip("在当前频谱中心新建一个 VFO（次听）")
        self.new_vfo_btn.clicked.connect(self._on_new_vfo_button)
        toolbar.addWidget(self.new_vfo_btn)

        # 主听/次听循环切换按钮（Ctrl+Tab）
        self.cycle_vfo_btn = QPushButton("切换主听")
        self.cycle_vfo_btn.setMinimumHeight(28)
        self.cycle_vfo_btn.setToolTip("在 VFO 之间循环切换主听（Ctrl+Tab）")
        self.cycle_vfo_btn.clicked.connect(self._on_cycle_vfo)
        toolbar.addWidget(self.cycle_vfo_btn)

        toolbar.addSeparator()

        # 音量滑块（0-100 映射到控制面板 volume_slider 0-63，走既有音量链）
        self.toolbar_volume = QSlider(Qt.Horizontal)
        self.toolbar_volume.setRange(0, 100)
        self.toolbar_volume.setMinimumWidth(60)
        self.toolbar_volume.setMaximumWidth(200)
        self.toolbar_volume.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.toolbar_volume.setMinimumHeight(28)
        self.toolbar_volume.setToolTip(
            "拖动调节收听音量；与控制面板的音量是同一个值，需要静音可按 Ctrl+M")
        self._toolbar_vol_prev = 48
        self.toolbar_volume.setValue(48)  # 先定值再接线，避免构造期误触发
        self.toolbar_volume.valueChanged.connect(self._on_toolbar_volume_changed)
        toolbar.addWidget(self.toolbar_volume)

        # 静音按钮（checkable：按下静音到 0，再按恢复到上次音量）
        self.toolbar_mute_btn = QPushButton("静音")
        self.toolbar_mute_btn.setMinimumHeight(28)
        self.toolbar_mute_btn.setCheckable(True)
        self.toolbar_mute_btn.setShortcut(QKeySequence(_key("vfo.mute")))
        self.toolbar_mute_btn.toggled.connect(self._on_toolbar_mute_toggled)
        toolbar.addWidget(self.toolbar_mute_btn)

        # 拉伸空白（QToolBar 没有 addStretch，用空白 QWidget 替代）
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)

        # 录音按钮
        self.record_btn = QPushButton("录音")
        self.record_btn.setObjectName("recordButton")
        self.record_btn.setMinimumHeight(28)
        self.record_btn.setCheckable(True)
        self.record_btn.toggled.connect(self._toggle_record)
        toolbar.addWidget(self.record_btn)

        # 回放按钮：选 .iq/.cf32/.cs16/.wav 录音文件做离线回放。
        # 已完整实现（见 _replay_recording）：真实读文件 IQ、按原始采样率分块
        # 推给频谱；baseband_io 不可用 / 读取失败 / 样本太少均由处理函数如实
        # 弹窗，绝不静默、绝不造数据。旧注释写的"占位"已与实现不符，按文档
        # 漂移处理（AGENTS.md §2：文档与实现掰腕子算 bug）。
        self.replay_btn = QPushButton("回放")
        self.replay_btn.setMinimumHeight(28)
        self.replay_btn.setShortcut(QKeySequence(_key("vfo.replay")))
        self.replay_btn.setToolTip("选择已录制的 .iq 文件进行离线回放")
        self.replay_btn.clicked.connect(self._replay_recording)
        toolbar.addWidget(self.replay_btn)

        self._setup_tab_order()

    def _setup_tab_order(self) -> None:
        """顶栏 Tab 顺序按视觉从左到右显式串联。

        Qt 的默认焦点链按控件创建次序，中间夹了 QLabel/分隔符后会跳序；
        显式 setTabOrder 保证键盘用户按屏幕顺序走。链尾进入中央三栏。
        """
        chain = [
            self.toolbar_mode_combo, self.theme_combo, self.lang_combo,
            self.layout_combo, self.layout_save_btn, self.layout_delete_btn,
            self.new_vfo_btn, self.cycle_vfo_btn, self.toolbar_volume,
            self.toolbar_mute_btn, self.record_btn, self.replay_btn,
        ]
        self._tab_order = tuple(chain)
        for first, second in zip(chain, chain[1:]):
            QWidget.setTabOrder(first, second)

    def _build_central_widget(self):
        """构建「平行视界」三栏固定 QSplitter 布局。

        布局（行：顶栏 / 三栏 / 底坞）：
          * 顶栏：细状态栏（连接/频率/采样率/GPS/UTC），见 _build_top_bar；
          * 左栏（~300px）：沉浸天空/轨道极坐标——QTabWidget 页签
            （星象仪模式 / 卫星跟踪 / 频谱）；
          * 中栏（flex）：暗色地图世界铺满（AdsbMapPanel 全出血），上方悬浮
            胶囊形 AI 命令栏（半透明毛玻璃，输入框 + 麦克风，路由到 ai_panel）；
          * 右栏（~360px）：当前 App——QTabWidget 页签
            （控制/状态/AI/模块/气象/新时空/扫频/调制识别/...），原 QDockWidget
            里的面板全部搬进来；
          * 底坞（~74px）：Home + 模式块 + Now Bar，见 _build_bottom_dock。
        三栏用 QSplitter(Horizontal) 连接，handle 厚度 4px（tokens），可拖宽。
        现有所有 panel 实例与信号连接保持原样，只换容器。
        """
        # ------------------------------------------------------------------
        # 左栏：天空 / 轨道 / 频谱 页签
        # ------------------------------------------------------------------
        left_tab = QTabWidget()
        left_tab.setTabPosition(QTabWidget.North)
        left_tab.setMinimumWidth(260)  # 左栏沉浸天空最小宽（tokens 无此键，弹性下限）

        # Tab: 星象仪模式（主）
        self.sky_view = self._create_sky_widget()
        if hasattr(self.sky_view, "object_clicked"):
            self.sky_view.object_clicked.connect(self._on_sky_object_clicked)
        left_tab.addTab(self.sky_view, "星象仪模式")
        left_tab.tabBar().setTabData(left_tab.count() - 1, "rf_sky")
        # 天空页左下角（那一排 sky 按钮上方）的全天模式开关
        self._build_all_sky_toggle()

        # Tab: 卫星闭环自动跟踪
        self.sat_track_panel = SatTrackPanel()
        left_tab.addTab(self.sat_track_panel, "卫星跟踪")
        left_tab.tabBar().setTabData(left_tab.count() - 1, "sat_track")
        self.sat_track_panel.track_requested.connect(self._start_satellite_tracking)
        self.sat_track_panel.stop_requested.connect(self._stop_satellite_tracking)
        self.sat_track_panel.update_tle_requested.connect(self._on_update_sat_tle)
        self.sat_track_panel.satellite_changed.connect(self._on_sat_combo_changed)
        self._init_loop_sat_catalog()

        # Tab: 频谱显示（左栏页签之一；拖宽 splitter 获得更宽 FFT/瀑布）
        spectrum_widget = QWidget()
        spectrum_layout = QVBoxLayout(spectrum_widget)
        spectrum_layout.setContentsMargins(0, 0, 0, 0)
        spectrum_layout.setSpacing(4)
        spectrum_header = QFrame()
        spectrum_header.setObjectName("card")
        spectrum_header.setMinimumHeight(tokens().SIZE["topbar_h"] - 20)
        header_layout = QHBoxLayout(spectrum_header)
        header_layout.setContentsMargins(12, 4, 12, 4)
        self.spectrum_title = QLabel("频谱显示")
        self.spectrum_title.setObjectName("sectionTitle")
        header_layout.addWidget(self.spectrum_title)
        header_layout.addStretch()
        self.freq_label = QLabel("-- MHz")
        self.freq_label.setObjectName("statusValue")
        header_layout.addWidget(self.freq_label)
        self.rssi_label = QLabel("RSSI: —")
        self.rssi_label.setObjectName("statusValue")
        header_layout.addWidget(self.rssi_label)
        spectrum_layout.addWidget(spectrum_header)

        self._no_device_hint = QLabel(
            "正在查找 SDR 设备…没有设备也可以先看星象仪模式和 AI 对话。")
        self._no_device_hint.setObjectName("hintLabel")
        # 单行不换行：wordWrap 会随窗口宽度变成 2~3 行，把下方频谱压到
        # 320x228（基线 320x240，见 test_ui_layout_locks 频谱基线）。
        # 窄窗省略与完整文案进 tooltip 的做法另行提交，不与本笔混改。
        self._no_device_hint.setAlignment(Qt.AlignCenter)
        _hint_bg = self._tok("light_hint_bg", "#F0E8DC")
        _hint_fg = self._tok("light_hint_text", "#8A6D4A")
        self._no_device_hint.setStyleSheet(
            f"QLabel {{ background-color:{_hint_bg}; color:{_hint_fg};"
            f" padding:4px; border-radius:3px; font-size:9pt; }}")
        spectrum_layout.addWidget(self._no_device_hint)

        self.spectrum = create_spectrum_widget(prefer_opengl=False)
        self.spectrum.freq_changed.connect(self._on_spectrum_freq_changed)
        if self._vfo_mgr is not None:
            self.spectrum.vfo_manager = self._vfo_mgr
        self.spectrum.vfo_created.connect(self._on_vfo_created)
        self.spectrum.vfo_moved.connect(self._on_vfo_moved)
        self.spectrum.vfo_bw_changed.connect(self._on_vfo_bw_changed_ui)
        self.spectrum.vfo_selected.connect(self._on_vfo_selected)
        self.spectrum.vfo_removed.connect(self._on_vfo_removed)
        # 谱面单键切解调（gqrx 键盘流）：走面板 combo 全链（mode_changed +
        # 带宽预设 + 后端 set_demod）
        self.spectrum.mode_hotkey_requested.connect(self._on_mode_hotkey)
        if hasattr(self.spectrum, "bookmark_activated"):
            self.spectrum.bookmark_activated.connect(
                self._on_bookmark_activated)
        self._cycle_vfo_shortcut = QShortcut(
            QKeySequence(_key("vfo.cycle")), self, self._on_cycle_vfo)
        spectrum_layout.addWidget(self.spectrum, stretch=1)
        # 星象仪形态退出时瀑布归位用的布局句柄（reparent 往返，对象身份不变）。
        self._spectrum_page_layout = spectrum_layout
        left_tab.addTab(spectrum_widget, "频谱")
        left_tab.tabBar().setTabData(left_tab.count() - 1, "spectrum")
        # 无设备引导横幅就在这一页；首启提示要能把用户带到它面前（S1）。
        self._spectrum_page = spectrum_widget

        self.left_tab = left_tab

        # ------------------------------------------------------------------
        # 中栏：暗色地图铺满 + 悬浮 AI 命令栏
        # ------------------------------------------------------------------
        center_widget = QWidget()
        center_widget.setObjectName("centerPane")
        center_lay = QVBoxLayout(center_widget)
        center_lay.setContentsMargins(0, 0, 0, 0)
        self.adsb_map_panel = AdsbMapPanel()
        center_lay.addWidget(self.adsb_map_panel)
        self._build_ai_command_bar(center_widget)

        # ------------------------------------------------------------------
        # 右栏：当前 App 页签（原 QDockWidget 面板全部搬进来）
        # ------------------------------------------------------------------
        right_tab = QTabWidget()
        right_tab.setTabPosition(QTabWidget.North)
        right_tab.setMinimumWidth(tokens().SIZE.get("min_dock_w", 320))
        self.right_tab = right_tab

        # 控制面板
        self.control_panel = ControlPanel()
        self.control_panel.tune_fm_requested.connect(self._on_tune_fm)
        self.control_panel.tune_am_requested.connect(self._on_tune_am)
        self.control_panel.volume_changed.connect(self._on_volume_changed)
        self.control_panel.record_toggled.connect(self._on_record_toggled)
        self.control_panel.mode_changed.connect(self._on_mode_changed)
        self.control_panel.gain_changed.connect(self._on_gain_changed)
        self.control_panel.squelch_changed.connect(self._on_squelch_changed)
        self.control_panel.auto_squelch_toggled.connect(
            self._on_auto_squelch_toggled)
        self.control_panel.tune_sdr_requested.connect(self._on_tune_sdr)
        self.control_panel.sample_rate_changed.connect(self._on_sample_rate_changed)
        if hasattr(self.control_panel, "agc_changed"):
            self.control_panel.agc_changed.connect(self._on_agc_changed)
        if hasattr(self.control_panel, "agc_speed_changed"):
            self.control_panel.agc_speed_changed.connect(
                self._on_agc_speed_changed)
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
        try:
            if self._freq_mgr is not None and hasattr(
                    self.control_panel, "load_bookmarks_from_list"):
                bm_list = [(b.freq_hz, b.name, b.mode, bool(b.builtin))
                           for b in self._freq_mgr.list() if b.freq_hz > 0]
                self.control_panel.load_bookmarks_from_list(bm_list)
                # 同一批收藏同时灌进内存表（频谱书签标签的数据源）
                self._bookmarks.extend(bm_list)
                self._sync_spectrum_bookmarks()
        except Exception:
            pass
        self._add_right_tab("控制", self.control_panel, "control_dock")

        # 状态面板
        self.status_panel = StatusPanel()
        self._add_right_tab("状态", self.status_panel, "status_dock")

        # AI 助手面板
        self.ai_panel = AIPanel()
        self.ai_panel.tool_call_requested.connect(self._on_ai_tool_call)
        # 面板自身的提交已在 _on_submit 里直接调 _call_ai；这里再接
        # command_submitted 会造成一次提交起两个 AIWorker（S10 的重复派发）。
        # 星象仪命令栏仍显式走 _on_ai_command（见 _build_ai_command_bar）。
        # 星象仪覆盖卡：AI 回复跨形态镜像（只读，历史以面板为准）
        self.ai_panel.reply_received.connect(self._on_planetarium_ai_reply)
        self._init_ai_agent_from_config()
        self._add_right_tab("AI 助手", self.ai_panel, "ai_dock")

        # 模块 / 信号流（原中央页签 → 右栏 App）
        self.module_panel = ModulePanel()
        self.module_panel.tune_requested.connect(self._on_module_tune)
        # —— 孤儿接通：选设备 / 解调模式 / sink / 节点 ——
        self.module_panel.device_selected.connect(self._on_module_device_selected)
        self.module_panel.demod_mode_changed.connect(
            self._on_module_demod_mode_changed)
        self.module_panel.sink_changed.connect(self._on_module_sink_changed)
        self.module_panel.node_selected.connect(self._on_module_node_selected)
        self._add_right_tab("模块", self.module_panel, "module_dock")

        # 气象云图（原中央页签 → 右栏 App）
        self.weather_panel = WeatherPanel()
        self._add_right_tab("气象云图", self.weather_panel, "weather_dock")

        # 新时空（原中央页签 → 右栏 App）
        self.new_spacetime_panel = NewSpacetimePanel()
        self._add_right_tab("新时空", self.new_spacetime_panel, "new_spacetime_dock")

        # ------------------------------------------------------------------
        # 三栏 QSplitter（handle 4px，可拖宽）
        # ------------------------------------------------------------------
        self._main_splitter = QSplitter(Qt.Horizontal)
        self._main_splitter.setHandleWidth(tokens().SIZE["splitter_handle"])
        self._main_splitter.setChildrenCollapsible(False)
        self._main_splitter.addWidget(left_tab)
        self._main_splitter.addWidget(center_widget)
        self._main_splitter.addWidget(right_tab)
        # 权重来源（截图三栏均分死黑事故）：弹性给左栏（天空/频谱工作面），
        # 中栏 ADS-B 空态时不许吞主区——stretch=0 固定基线，最小宽见面板；
        # 右栏控制台同样固定。窗口变宽时全部增量进左栏。
        self._main_splitter.setStretchFactor(0, 1)
        self._main_splitter.setStretchFactor(1, 0)
        self._main_splitter.setStretchFactor(2, 0)
        # 初始比例：左 392 / 中 300 / 右 300（= 最小窗口 1000 - 2×4px 手柄，
        # 恰好放得下不触发压缩缩放；窗口更宽时全部增量由 stretch 进左栏，
        # 用户拖动后持久化）
        self._main_splitter.setSizes([392, 300, 300])

        # ------------------------------------------------------------------
        # root：顶栏 + 三栏 + 底坞
        # ------------------------------------------------------------------
        root = QWidget()
        root_lay = QVBoxLayout(root)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)
        self._build_top_bar(root_lay)
        root_lay.addWidget(self._main_splitter, stretch=1)
        self._build_bottom_dock(root_lay)
        self.setCentralWidget(root)

        # ---- 内核能力面板：扫频 / 自动调制识别 / 书签 / 服务设置（右栏页签）----
        self.scanner_panel = None
        self.modulation_panel = None
        self.bookmark_panel = None
        self.service_settings_panel = None
        self._build_kernel_panels()
        # 多 VFO / ANR / 星座图 / 设备 / 分段增益（右栏页签）
        self.vfo_panel = None
        self.anr_panel = None
        self.constellation_panel = None
        self.device_panel = None
        self.gain_panel = None
        self._anr_enabled = False
        self._audio_output_device = -1
        self._build_extended_panels()
        # 数字信号解码面板：ADS-B 列表 / APRS / 卫星云图（右栏页签）
        self.digital_adsb_panel = None
        self.digital_aprs_panel = None
        self.digital_sat_image_panel = None
        self._build_digital_decode_panels()

        # ---- CarWith 面板注册表挂载（记账挂载表；关窗时
        # _release_runtime_resources 对称摘除，见 PanelRegistry.detach_widget）----
        reg = registry()
        self._panel_attachments = [
            ("spectrum", self.spectrum), ("rf_sky", self.sky_view),
            ("weather", self.weather_panel), ("module", self.module_panel),
            ("sat_track", self.sat_track_panel),
            ("new_spacetime", self.new_spacetime_panel),
            ("adsb", self.adsb_map_panel), ("control", self.control_panel),
            ("status", self.status_panel), ("ai", self.ai_panel),
        ]
        for pid, wgt in self._panel_attachments:
            reg.attach_widget(pid, wgt)

        # 自由浮窗管理器已弃用（用户否决）；保留类实例避免 import / 预设 combo 崩。
        self.dock_layout = DockLayoutManager(self)

        # ---- 用户自定义布局：面板菜单 + 动态下拉 + 启动恢复上次 active 布局 ----
        self._build_panel_menu()
        self._restore_user_layout()

        # ---- 双形态壳层：IDE / 星象仪 顶层模式栈（页0=IDE root，页1=星象仪页）----
        self._build_mode_shell()
        self._restore_app_mode()

        # ---- 动态 UI 动作总线挂载（模型 → 界面）：本窗口为执行器，
        # UI 线程 150ms 排水（AIWorker 后台线程不直接碰控件）----
        self._init_ui_action_bus()
        # 天空动作执行总线（模型协商后 → 主窗口 UI 线程落地，150ms 排水）
        self._init_sky_action_bus()

    # ------------------------------------------------------------------
    # 动态 UI（模型 → 界面动作执行器 + 排水）
    # ------------------------------------------------------------------
    def _init_ui_action_bus(self):
        from mbdsdr_ai.ui_actions import get_ui_bus
        bus = get_ui_bus()
        bus.executor = self._execute_ui_action
        self._ui_bus = bus
        self._ui_bus_timer = QTimer(self)
        self._ui_bus_timer.setInterval(150)
        self._ui_bus_timer.timeout.connect(bus.drain)
        self._ui_bus_timer.start()

    def _execute_ui_action(self, action: dict) -> dict:
        """执行一条界面动作并返回结果（含逆操作，供撤销栈）。

        动词：show_panel/hide_panel（面板 id → 面板菜单勾选）、
        select_tab/focus_panel（面板 id → 左栏页签切前）、
        set_layout_ratio（"左:中:右" → 三栏 splitter 按比分配）。
        未知目标 → 诚实 ERROR（不假装界面变了）。
        """
        verb = str(action.get("verb", ""))
        target = str(action.get("target", ""))
        if verb in ("show_panel", "hide_panel"):
            act = (self._panel_actions or {}).get(target)
            if act is None:
                return {"ok": False, "status": "ERROR",
                        "reason": f"未知面板 id {target!r}（见 panel_registry）"}
            want = verb == "show_panel"
            if act.isChecked() == want:
                return {"ok": True, "status": "NOOP",
                        "inverse": {"verb": "hide_panel" if want else "show_panel",
                                    "target": target}}
            act.setChecked(want)   # 经 _toggle_panel 同路径（不删控件）
            return {"ok": True, "status": "DONE",
                    "inverse": {"verb": "hide_panel" if want else "show_panel",
                                "target": target}}
        if verb in ("select_tab", "focus_panel"):
            tab = getattr(self, "left_tab", None)
            if tab is None:
                return {"ok": False, "status": "ERROR", "reason": "无左栏页签"}
            prev = tab.currentIndex()
            for i in range(tab.count()):
                if tab.tabBar().tabData(i) == target:
                    tab.setCurrentIndex(i)
                    return {"ok": True, "status": "DONE",
                            "inverse": {"verb": "select_tab",
                                        "target": tab.tabBar().tabData(prev)
                                        if prev >= 0 else target}}
            return {"ok": False, "status": "ERROR",
                    "reason": f"页签不含面板 id {target!r}"}
        if verb == "set_layout_ratio":
            sp = getattr(self, "_main_splitter", None)
            if sp is None:
                return {"ok": False, "status": "ERROR",
                        "reason": "三栏 splitter 未就绪"}
            parts = [int(p) for p in str(action.get("value", "")).split(":")]
            total_parts = sum(parts)
            old_sizes = list(sp.sizes())
            total = max(1, sum(old_sizes))
            sp.setSizes([total * p // total_parts for p in parts])
            inv_total = max(1, sum(old_sizes))
            return {"ok": True, "status": "DONE",
                    "inverse": {"verb": "set_layout_ratio",
                                "value": ":".join(str(s) for s in old_sizes),
                                "_abs_sizes": True}}
        return {"ok": False, "status": "ERROR",
                "reason": f"执行器不支持动词 {verb!r}"}

    # ------------------------------------------------------------------
    # 天空动作总线：模型协商结束后 → UI 线程落地
    # ------------------------------------------------------------------
    def _init_sky_action_bus(self):
        from mbdsdr_ai.gimbal_follow import GimbalFollowLimiter, PointingErrorRecorder
        from mbdsdr_ai.sky_actions import get_sky_bus
        bus = get_sky_bus()
        bus.executor = self._execute_sky_action
        self._sky_bus = bus
        self._sky_bus_timer = QTimer(self)
        self._sky_bus_timer.setInterval(150)
        self._sky_bus_timer.timeout.connect(bus.drain)
        self._sky_bus_timer.start()
        # 云台跟随整形器：死区 + 最小间隔 + 速率上限，避免指令风暴
        self._gimbal_limiter = GimbalFollowLimiter()
        # 过境全过程指向误差：指令角 vs 卫星真角（可选姿态回读角）
        self._gimbal_error = PointingErrorRecorder()
        self._gimbal_cycle_seq = 0
        self._last_gimbal_cycle: Optional[Dict] = None
        # 云台指令后台任务：GUI 线程只投递/收结果，不阻塞等回包
        self._gimbal_task_thread: Optional[threading.Thread] = None
        self._gimbal_task: Optional[Dict] = None
        self._gimbal_pending_notified = False

    def _sky_selected_name(self) -> str:
        sv = getattr(self, "sky_view", None)
        info = getattr(sv, "_selected_info", None) if sv is not None else None
        return str((info or {}).get("title") or "")

    def _sky_action_context(self, action: str):
        """取天空视图的真实目标上下文（拿不到的字段如实留空）。"""
        sv = getattr(self, "sky_view", None)
        if sv is None or not hasattr(sv, "sky_action_context"):
            return None
        try:
            return sv.sky_action_context(action)
        except Exception:
            return None

    def _execute_sky_action(self, action: dict) -> dict:
        """执行模型协商后提交的天空动作（目标必须与当前选中一致）。"""
        kind = str(action.get("action", ""))
        satellite = str(action.get("satellite", "")).strip()
        current = self._sky_selected_name()
        if not current:
            return {"ok": False, "status": "UNAVAILABLE",
                    "reason": "天空视图当前没有选中卫星，动作未执行"}
        if satellite and satellite != current:
            return {"ok": False, "status": "TARGET_MISMATCH",
                    "reason": f"当前选中的是 {current}，不是 {satellite}；"
                              "请重新选择目标后再发起"}
        sv = getattr(self, "sky_view", None)
        res = None
        if sv is not None and hasattr(sv, "_compute_tune"):
            try:
                res = sv._compute_tune()
            except Exception:
                res = None

        if kind == "tune":
            if res is None:
                return {"ok": False, "status": "UNAVAILABLE",
                        "reason": "缺少实时轨道/链路数据，无法计算多普勒调谐"}
            self._on_sky_tune_satellite({
                "name": str(res.get("satellite") or current),
                "tuned_freq_hz": float(res.get("tuned_freq_hz", 0.0)),
                "doppler_shift_hz": float(res.get("doppler_shift_hz", 0.0)),
                "mode": res.get("mode", "NFM"),
            })
            return {"ok": True, "status": "DONE", "satellite": current}

        if kind == "track":
            on = bool(action.get("on", True))
            if on:
                base_hz = float(action.get("frequency_hz") or 0.0)
                if base_hz <= 0 and res is not None:
                    base_hz = float(res.get("base_freq_hz", 0.0))
                if base_hz <= 0:
                    return {"ok": False, "status": "UNAVAILABLE",
                            "reason": "未知下行频点，无法进入闭环跟踪"}
            else:
                base_hz = 0.0
            self._on_sky_track_toggled(on, current, base_hz)
            active = (self._sat_track_timer is not None
                      and self._sat_track_timer.isActive())
            if on and not active:
                return {"ok": False, "status": "REFUSED",
                        "reason": "跟踪未能启动（缺观测站/选星失败/缺依赖），"
                                  "按钮已复位"}
            return {"ok": True, "status": "DONE", "satellite": current}

        if kind == "gimbal_follow":
            on = bool(action.get("on", True))
            self._on_sky_gimbal_follow_toggled(on)
            if on and not self._gimbal_follow:
                return {"ok": False, "status": "REFUSED",
                        "reason": "云台跟随未启动（无可用后端或未先开始跟踪）"}
            return {"ok": True, "status": "DONE", "satellite": current}

        return {"ok": False, "status": "ERROR",
                "reason": f"执行器不支持天空动作 {kind!r}"}

    # ------------------------------------------------------------------
    # 天空按钮 → agent 能力协商（按钮不再直连硬件）
    # ------------------------------------------------------------------
    def _on_sky_tune_requested(self, info: dict):
        """「调到该星」→ 交给 agent 协商后执行，AI 不可用则诚实拒绝。"""
        ctx = self._sky_action_context("tune")
        if ctx is None:
            self.statusBar().showMessage(
                "无法发起调谐：缺少卫星/观测站/链路数据", MSG_NORMAL)
            return
        self._request_sky_action("tune", ctx)

    def _on_sky_track_requested(self, on: bool, sat_name: str,
                                downlink_hz: float):
        """「跟踪」→ 开始先协商；停止直接停环（停止不需要问硬件）。"""
        if not on:
            self._on_sky_track_toggled(False, sat_name, 0.0)
            return
        ctx = self._sky_action_context("track")
        if ctx is None:
            self.statusBar().showMessage(
                "无法发起跟踪：缺少卫星/观测站/链路数据", MSG_NORMAL)
            self._sync_sky_track_btn(False)
            return
        self._request_sky_action("track", ctx)

    def _on_sky_gimbal_follow_requested(self, on: bool):
        """「云台跟随」→ 开始先协商；解锁直接停（无需问硬件）。"""
        if not on:
            self._on_sky_gimbal_follow_toggled(False)
            return
        ctx = self._sky_action_context("gimbal_follow")
        if ctx is None:
            self.statusBar().showMessage(
                "无法发起云台跟随：缺少卫星/观测站数据", MSG_NORMAL)
            self._sync_sky_gimbal_btn(False)
            return
        self._request_sky_action("gimbal_follow", ctx)

    def _request_sky_action(self, action: str, context: dict) -> bool:
        """把动作上下文交给 AI 会话（模型据此决定探测/询问/执行）。"""
        panel = getattr(self, "ai_panel", None)
        if panel is None or not hasattr(panel, "request_sky_action"):
            self.statusBar().showMessage(
                "AI 对话面板不可用，天空动作未执行（不退回直连硬件）", MSG_NORMAL)
            self._reset_sky_pending_ui(action)
            return False
        ok = bool(panel.request_sky_action(context))
        if not ok:
            self._reset_sky_pending_ui(action)
            name = "跟踪" if action == "track" else "云台跟随"
            text = (
                f"{name}未启动：AI 没有接受这次请求"
                "（可能未配置 API Key、正忙或需要先确认硬件）。"
                "下一步：打开右侧「AI」面板查看原因后重试，"
                "或在天空页手动操作。")
            self.statusBar().showMessage(text, MSG_IMPORTANT)
            if getattr(self, "_app_mode", "ide") == "planetarium":
                self._on_planetarium_ai_reply(text)
        return ok

    def _reset_sky_pending_ui(self, action: str) -> None:
        """协商未执行时把按钮回弹到真实状态（不残留假勾选）。"""
        if action == "track":
            if not (self._sat_track_timer is not None
                    and self._sat_track_timer.isActive()):
                self._sync_sky_track_btn(False)
        elif action == "gimbal_follow":
            if not self._gimbal_follow:
                self._sync_sky_gimbal_btn(False)

    def _on_sky_action_resolved(self, action: str, satellite: str,
                                requested: bool) -> None:
        """一轮协商结束：按钮勾选与真实硬件状态对齐。"""
        if action == "track":
            active = (self._sat_track_timer is not None
                      and self._sat_track_timer.isActive())
            self._sync_sky_track_btn(active)
            if not active:
                self.statusBar().showMessage(
                    "AI 本轮尚未执行跟踪（可能在确认硬件，详见对话）", MSG_IMPORTANT)
        elif action == "gimbal_follow":
            self._sync_sky_gimbal_btn(self._gimbal_follow)
            if not self._gimbal_follow:
                self.statusBar().showMessage(
                    "AI 本轮尚未启动云台跟随（可能在确认硬件，详见对话）",
                    MSG_IMPORTANT)

    # ------------------------------------------------------------------
    # 双形态壳层：IDE 形态 / 星象仪形态
    #
    # 切换 = QStackedWidget 换页 + sky_view 换父。对象身份不变、信号零重接。
    # 不用第二窗口（状态双份、焦点分裂）；不用自由浮窗（用户已否决，见
    # dock_layout_manager 弃用史）。与 DockLayoutManager 的"布局预设"正交：
    # 预设管栏内面板编排，形态管顶层 IDE↔星象仪。
    # ------------------------------------------------------------------
    def _build_mode_shell(self) -> None:
        """把已构建的 IDE root 包进顶层形态栈，并建空星象仪容器页。"""
        root = self.centralWidget()
        self._mode_stack = QStackedWidget()
        self._mode_stack.addWidget(root)
        self._planetarium_page = self._build_planetarium_page()
        self._mode_stack.addWidget(self._planetarium_page)
        self.setCentralWidget(self._mode_stack)
        self._app_mode = "ide"
        self._sky_restore_info: Optional[dict] = None

    def _build_planetarium_page(self) -> QWidget:
        """星象仪容器页：天空铺满 + 底部瀑布 + 天空右上角形态浮层。

        AI 命令栏（IDE 中栏已有那颗胶囊）进形态时 reparent 到天空槽上，
        回复经 ai_panel.reply_received 打到 _planetarium_ai_card 覆盖卡。
        全程复用既有部件与同一 agent 实例——对话历史跨形态连续。
        """
        page = QWidget()
        page.setObjectName("planetariumPage")
        hbox = QHBoxLayout(page)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(0)

        # 左列：天空（铺满）+ 瀑布（底部，进形态后 reparent self.spectrum）
        left_col = QWidget()
        col_lay = QVBoxLayout(left_col)
        col_lay.setContentsMargins(0, 0, 0, 0)
        col_lay.setSpacing(0)
        self._planetarium_sky_slot = QWidget()
        self._planetarium_sky_slot.setObjectName("planetariumSkySlot")
        slot_lay = QVBoxLayout(self._planetarium_sky_slot)
        slot_lay.setContentsMargins(0, 0, 0, 0)
        slot_lay.setSpacing(0)
        self._planetarium_sky_layout = slot_lay
        col_lay.addWidget(self._planetarium_sky_slot, stretch=1)

        self._planetarium_waterfall_slot = QWidget()
        self._planetarium_waterfall_slot.setObjectName(
            "planetariumWaterfallSlot")
        wf_lay = QVBoxLayout(self._planetarium_waterfall_slot)
        wf_lay.setContentsMargins(0, 0, 0, 0)
        wf_lay.setSpacing(0)
        self._planetarium_waterfall_layout = wf_lay
        self._planetarium_waterfall_slot.setFixedHeight(180)
        self._planetarium_waterfall_slot.hide()
        col_lay.addWidget(self._planetarium_waterfall_slot)

        hbox.addWidget(left_col, stretch=1)

        # 形态浮层：浮在天空视图右上角，不参与布局。占一条 132px 的窄条会
        # 永久吃掉 96px 天空宽度（764→668，面积 -12.6%），浮层只遮右上角一隅。
        strip = QFrame(self.sky_view)
        strip.setObjectName("planetariumStrip")
        # 宽度走令牌 152px（内容外边距 6×2 + 按钮 sizeHint 136 + 边框 2，
        # 留 2px 余量）；collapse_strip(36) 会把「退出星象仪」裁掉右缘。
        strip.setFixedWidth(tokens().SIZE["planetarium_strip_w"])
        strip.setAttribute(Qt.WA_StyledBackground, True)
        strip.setStyleSheet(
            "QFrame#planetariumStrip {"
            " background-color: rgba(18,19,22,0.55);"
            f" border: 1px solid {self._tok('gray_400', '#585c63')};"
            " border-radius: 10px; }")
        strip_lay = QVBoxLayout(strip)
        strip_lay.setContentsMargins(6, 6, 6, 6)
        strip_lay.setSpacing(4)
        # 录音指示：只读 _recording/_record_seconds，不自起计时
        rec_indicator = QLabel("")
        rec_indicator.setObjectName("planetariumRecIndicator")
        rec_indicator.setAlignment(Qt.AlignCenter)
        rec_indicator.setWordWrap(True)
        rec_indicator.setStyleSheet(
            f"QLabel#planetariumRecIndicator {{ color: "
            f"{self._tok('danger', '#E5484D')}; font-size: 9pt; }}")
        rec_indicator.setVisible(False)
        strip_lay.addWidget(rec_indicator, 0, Qt.AlignHCenter)
        exit_btn = QPushButton("退出星象仪\n(Esc)")
        exit_btn.setObjectName("planetariumExit")
        exit_btn.setToolTip("切回 IDE 形态；按 Esc 同样退出")
        exit_btn.setMinimumHeight(44)
        exit_btn.clicked.connect(lambda: self.set_app_mode("ide"))
        strip_lay.addWidget(exit_btn, 0, Qt.AlignHCenter)
        strip_lay.addStretch(1)
        self._planetarium_strip = strip
        self._planetarium_rec_indicator = rec_indicator
        strip.installEventFilter(self)
        strip.hide()  # 只在星象仪形态显示；不占布局，天空视图保持满宽

        # AI 回复覆盖卡（天空槽上的半透明卡片；隐藏=无回复，诚实空态不编造）
        card = QFrame(self._planetarium_sky_slot)
        card.setObjectName("planetariumAiCard")
        card.setStyleSheet(
            "QFrame#planetariumAiCard {"
            " background-color: rgba(18,19,22,0.82);"
            f" border: 1px solid {self._tok('gray_400', '#585c63')};"
            f" border-radius: {tokens().RADIUS['card'] if 'card' in tokens().RADIUS else 8}px;"
            " }"
            "QLabel#planetariumAiCardText {"
            " background: transparent; color: rgba(255,255,255,0.92);"
            " padding: 8px 12px; }"
        )
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(4, 4, 4, 4)
        card_text = QLabel(card)
        card_text.setObjectName("planetariumAiCardText")
        card_text.setWordWrap(True)
        card_text.setTextFormat(Qt.PlainText)
        card_text.setMaximumWidth(520)
        card_lay.addWidget(card_text)
        card.setVisible(False)
        self._planetarium_ai_card = card
        self._planetarium_ai_card_text = card_text
        self._planetarium_sky_slot.installEventFilter(self)
        return page

    def _position_planetarium_ai_card(self) -> None:
        """AI 回复卡定位：天空槽底部居中，叠在瀑布上方。

        窄槽（天空被布局预设搬进右 Dock 时视口可窄到 288px）必须收窄：
        卡片的 520px 文字上限大于槽宽时，`x = max(margin, ...)` 会把卡片
        右缘顶出槽外，父容器直接裁掉半句话。所以先按槽宽收口再量尺寸。
        """
        card = getattr(self, "_planetarium_ai_card", None)
        slot = getattr(self, "_planetarium_sky_slot", None)
        if card is None or slot is None:
            return
        margin = 16
        text = getattr(self, "_planetarium_ai_card_text", None)
        avail = max(120, slot.width() - 2 * margin)
        if text is not None:
            # 卡片宽 = 文字宽 + layout 边距 8 + 样式 padding 左右 24
            text.setMaximumWidth(max(60, min(520, avail - 32)))
        card.setMaximumWidth(avail)
        card.adjustSize()
        x = max(margin, (slot.width() - card.width()) // 2)
        wf = self._planetarium_waterfall_slot
        bottom = wf.y() if wf.isVisibleTo(slot) else slot.height()
        y = max(margin, bottom - card.height() - 12)
        card.move(x, y)
        card.raise_()

    @Slot(str)
    def _on_planetarium_ai_reply(self, text: str):
        """AI 回复 → 星象仪覆盖卡。空内容诚实显示占位，绝不编造。"""
        card = getattr(self, "_planetarium_ai_card", None)
        if card is None:
            return
        shown = text.strip() if text else ""
        if not shown:
            shown = "（AI 未返回文本内容，可能只执行了工具调用）"
        self._planetarium_ai_card_text.setText(shown)
        card.setVisible(True)
        self._position_planetarium_ai_card()

    def _capture_sky_location(self) -> dict:
        """记录 sky_view 当前所在容器，供退出星象仪归位。

        布局预设可能把天空搬到右侧 Dock（DockLayoutManager._move_sky_to_right），
        所以归位目标不能写死 left_tab——进形态前在哪里，出来回哪里。
        """
        sky = self.sky_view
        lt = getattr(self, "left_tab", None)
        if lt is not None and lt.indexOf(sky) >= 0:
            return {"kind": "tab", "index": lt.indexOf(sky)}
        p = sky.parentWidget()
        while p is not None:
            if isinstance(p, QDockWidget):
                return {"kind": "dock", "dock": p}
            p = p.parentWidget()
        return {"kind": "widget", "parent": sky.parentWidget()}

    def _restore_sky_location(self, info: Optional[dict]) -> None:
        sky = self.sky_view
        info = info or {}
        kind = info.get("kind")
        lt = getattr(self, "left_tab", None)
        if kind == "tab" and lt is not None:
            idx = max(0, min(int(info.get("index", 0)), lt.count()))
            lt.insertTab(idx, sky, "星象仪模式")
            lt.tabBar().setTabData(idx, "rf_sky")
        elif kind == "dock" and info.get("dock") is not None:
            info["dock"].setWidget(sky)
        elif info.get("parent") is not None:
            sky.setParent(info["parent"])
            sky.show()

    def _enter_planetarium(self) -> None:
        if getattr(self, "_app_mode", "ide") == "planetarium":
            return
        self._sky_restore_info = self._capture_sky_location()
        self._planetarium_sky_layout.addWidget(self.sky_view)
        # 天空可能从"非当前页签/隐藏 Dock"进来，自带 hidden 标记；reparent 不清它，
        # 不显式 show 则星象仪整片天黑（只有槽底色）。show 只清自身标记，尺寸归布局。
        self.sky_view.show()
        # 底部瀑布 reparent（对象身份不变；频谱页签里只剩头部/提示，退出归位）
        wf_slot = self._planetarium_waterfall_slot
        if getattr(self, "spectrum", None) is not None:
            self._planetarium_waterfall_layout.addWidget(self.spectrum)
            wf_slot.setVisible(True)
        # AI 胶囊命令栏 reparent 到天空槽（同一颗胶囊、同一路由 _on_ai_command，
        # 对话历史跨形态连续）；记下原父，退出归位。
        bar = getattr(self, "_ai_cmd_bar", None)
        if bar is not None:
            self._ai_bar_restore_parent = bar.parentWidget()
            bar.setParent(self._planetarium_sky_slot)
            self._ai_cmd_parent = self._planetarium_sky_slot
            self._position_ai_cmd_bar()
        self._mode_stack.setCurrentWidget(self._planetarium_page)
        self._app_mode = "planetarium"
        # 星象仪构图：朝地平看（地景剪影+辉光带入画），天顶俯视留给 IDE 形态
        if hasattr(self.sky_view, "enter_horizon_view"):
            self.sky_view.enter_horizon_view()
        self.settings.set("app_mode", "planetarium")
        self._sync_mode_action()
        self._sync_planetarium_recording()
        self._position_planetarium_overlay()
        self._planetarium_strip.show()
        # 窗口已显示才联动全屏；启动恢复阶段窗口未 show，交给正常 show 流程。
        if self.isVisible() and not self.isFullScreen():
            self.showFullScreen()

    def _exit_planetarium(self) -> None:
        if getattr(self, "_app_mode", "ide") != "planetarium":
            return
        self._planetarium_sky_layout.removeWidget(self.sky_view)
        self._restore_sky_location(self._sky_restore_info)
        self._sky_restore_info = None
        # 瀑布归位频谱页签
        if getattr(self, "spectrum", None) is not None:
            self._planetarium_waterfall_layout.removeWidget(self.spectrum)
            lay = getattr(self, "_spectrum_page_layout", None)
            if lay is not None:
                lay.addWidget(self.spectrum, stretch=1)
        self._planetarium_waterfall_slot.setVisible(False)
        # AI 命令栏归位 IDE 中栏
        bar = getattr(self, "_ai_cmd_bar", None)
        if bar is not None:
            old_parent = getattr(self, "_ai_bar_restore_parent", None)
            if old_parent is not None:
                bar.setParent(old_parent)
                self._ai_cmd_parent = old_parent
                self._position_ai_cmd_bar()
        # 回复覆盖卡随形态退出隐藏（不销毁文本；历史在 AI 面板里）
        card = getattr(self, "_planetarium_ai_card", None)
        if card is not None:
            card.setVisible(False)
        self._mode_stack.setCurrentIndex(0)
        self._app_mode = "ide"
        self.settings.set("app_mode", "ide")
        self._planetarium_strip.hide()
        self._sync_mode_action()
        if self.isFullScreen():
            self.showNormal()

    def set_app_mode(self, mode: str) -> None:
        """顶层形态切换入口（IDE / 星象仪）。"""
        if mode == "planetarium":
            self._enter_planetarium()
        else:
            self._exit_planetarium()

    def _toggle_planetarium_mode(self, checked: bool = False) -> None:
        self.set_app_mode(
            "ide" if self._app_mode == "planetarium" else "planetarium")

    def _sync_mode_action(self) -> None:
        act = getattr(self, "_planetarium_action", None)
        if act is not None:
            act.setChecked(self._app_mode == "planetarium")
        esc = getattr(self, "_planetarium_esc", None)
        if esc is not None:
            esc.setEnabled(self._app_mode == "planetarium")

    def _on_escape(self) -> None:
        """Esc 逐层退出当前状态：先关星象仪 AI 回复卡，再退出星象仪形态。

        只处理"看得见却点不到"的覆盖层；QDialog 弹窗由 Qt 自身 Esc→reject。
        """
        card = getattr(self, "_planetarium_ai_card", None)
        if card is not None and card.isVisible():
            card.setVisible(False)
            return
        if getattr(self, "_app_mode", "ide") == "planetarium":
            self.set_app_mode("ide")

    def _restore_app_mode(self) -> None:
        """启动恢复上次顶层形态（settings 权威源；读不到/异常回退 IDE）。"""
        try:
            mode = str(self.settings.get("app_mode", "ide") or "ide")
        except Exception:
            mode = "ide"
        if mode == "planetarium":
            self._enter_planetarium()

    def _restore_user_layout(self) -> None:
        """启动时：填充布局下拉、恢复上次 active 布局的页签顺序/比例/可见性。"""
        try:
            self.dock_layout.restore_state()
        except Exception:
            pass
        try:
            self._refresh_layout_combo()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 右栏页签辅助：面板包进无框 QScrollArea 加入 right_tab，并记录
    # <attr_name>_dock = scroll（数据 tap 用 isVisible() 守卫：非当前页签时
    # QStackedWidget 自动隐藏，天然零开销）。
    # ------------------------------------------------------------------
    def _add_right_tab(self, title: str, panel: QWidget,
                       attr_name: Optional[str] = None) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(panel)
        self.right_tab.addTab(scroll, title)
        if attr_name:
            setattr(self, attr_name, scroll)
            # 稳定身份：从 attr_name 推导 panel id（"control_dock" -> "control"）。
            # 落盘/恢复一律按 id 走，绝不按译文 —— 语言切换后译文会变，
            # 用译文当身份会让布局静默丢失（见 DockLayoutManager._tab_key）。
            pid = attr_name[:-5] if attr_name.endswith("_dock") else attr_name
            self.right_tab.tabBar().setTabData(self.right_tab.count() - 1, pid)
        return scroll

    # ------------------------------------------------------------------
    # 中栏悬浮 AI 命令栏（胶囊形半透明毛玻璃）
    # ------------------------------------------------------------------
    def _build_ai_command_bar(self, parent: QWidget) -> None:
        bar = QFrame(parent)
        bar.setObjectName("aiCommandBar")
        bar.setMinimumHeight(36)
        bar.setMaximumHeight(64)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(18, 0, 12, 0)
        lay.setSpacing(8)
        self.cmd_input = QLineEdit()
        self.cmd_input.setPlaceholderText("问 AI… 频率 / 模式 / 解调 / 搜索")
        self.cmd_input.returnPressed.connect(self._on_cmd_submit)
        lay.addWidget(self.cmd_input, stretch=1)
        self.cmd_mic_btn = QPushButton("🎤")
        self.cmd_mic_btn.setObjectName("iconButton")
        self.cmd_mic_btn.setMinimumSize(28, 28)
        self.cmd_mic_btn.setMaximumSize(48, 48)
        self.cmd_mic_btn.setToolTip("语音输入（未连接麦克风时无操作）")
        self.cmd_mic_btn.clicked.connect(self._on_cmd_mic)
        lay.addWidget(self.cmd_mic_btn)
        # 胶囊形半透明深色底（毛玻璃用半透明模拟；QSS 不支持 backdrop-filter）
        bar.setStyleSheet(
            "QFrame#aiCommandBar {"
            " background-color: rgba(18,19,22,0.82);"
            f" border: 1px solid {self._tok('gray_400', '#585c63')};"
            f" border-radius: {tokens().RADIUS['circle_sm']}px;"
            "}"
            "QFrame#aiCommandBar QLineEdit {"
            " background: transparent; border: none;"
            " color: rgba(255,255,255,0.9);"
            "}"
        )
        bar.adjustSize()
        self._ai_cmd_bar = bar
        self._ai_cmd_parent = parent
        parent.installEventFilter(self)
        self._position_ai_cmd_bar()

    def _position_ai_cmd_bar(self) -> None:
        """把胶囊命令栏定位到中栏顶部居中。"""
        bar = getattr(self, "_ai_cmd_bar", None)
        parent = getattr(self, "_ai_cmd_parent", None)
        if bar is None or parent is None:
            return
        w = min(parent.width() - 40, 560)
        bar.setFixedWidth(max(w, 280))
        bar.move((parent.width() - bar.width()) // 2, 16)
        bar.raise_()
        self._position_planetarium_overlay()

    def _handle_shortcut_override(self, event) -> bool:
        """Ctrl+C 焦点感知：文本输入控件上放行标准复制，返回 True 表示已接管。

        操作逻辑红线：全局快捷键不得劫持系统惯例。
        """
        if event.modifiers() == Qt.ControlModifier and event.key() == Qt.Key_C:
            if _is_text_entry_widget(_focus_widget()):
                event.accept()
                return True
        return False

    def eventFilter(self, obj, event):  # noqa: N802 (Qt 命名)
        from PySide6.QtCore import QEvent
        if obj is getattr(self, "_ai_cmd_parent", None) \
                and event.type() == QEvent.Resize:
            self._position_ai_cmd_bar()
        if obj is getattr(self, "_planetarium_sky_slot", None) \
                and event.type() == QEvent.Resize:
            self._position_planetarium_ai_card()
        if obj is getattr(self, "sky_view", None) \
                and event.type() == QEvent.Resize:
            self._position_all_sky_btn()
            self._position_planetarium_overlay()
        if obj is getattr(self, "_planetarium_strip", None):
            if event.type() == QEvent.Enter:
                self._set_planetarium_strip_hover(True)
            elif event.type() == QEvent.Leave:
                self._set_planetarium_strip_hover(False)
        if event.type() == QEvent.ShortcutOverride:
            if self._handle_shortcut_override(event):
                return True
        return super().eventFilter(obj, event)

    def _on_cmd_submit(self) -> None:
        text = self.cmd_input.text().strip()
        if not text:
            return
        self._on_ai_command(text)
        self.cmd_input.clear()

    def _on_cmd_mic(self) -> None:
        """麦克风按钮：无语音识别后端时仅提示，不造假。"""
        self.statusBar().showMessage("语音输入未配置（接麦克风后可用）", MSG_NORMAL)

    # ------------------------------------------------------------------
    # 顶栏（细状态栏）/ 底坞（Home + 模式块 + Now Bar）
    # ------------------------------------------------------------------
    def _build_top_bar(self, parent_layout: QVBoxLayout) -> None:
        """顶部细状态栏：连接 / 频率 / 采样率 / GPS / UTC（精简自原 QStatusBar）。"""
        bar = QWidget()
        bar.setObjectName("topBar")
        bar.setFixedHeight(tokens().SIZE["topbar_h"])
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(12)

        self.status_conn = QLabel("未连接")
        lay.addWidget(self.status_conn)
        lay.addWidget(self._vsep())
        self.status_freq = QLabel("频率: —")
        self.status_freq.setObjectName("statusValue")
        lay.addWidget(self.status_freq)
        lay.addWidget(self._vsep())
        self.status_sr = QLabel("采样率: —")
        self.status_sr.setObjectName("statusValue")
        lay.addWidget(self.status_sr)
        lay.addWidget(self._vsep())
        self.status_gps = QLabel("GNSS: 未连接")
        self.status_gps.setObjectName("statusValue")
        lay.addWidget(self.status_gps)
        lay.addWidget(self._vsep())
        # 多普勒链路补偿芯片（标称/实际/Δ + 开关；不新增页面——链路指示而非功能页）
        self.doppler_chip = QToolButton()
        self.doppler_chip.setObjectName("statusValue")
        self.doppler_chip.setCheckable(True)
        self.doppler_chip.setChecked(False)
        self.doppler_chip.setText("多普勒 --")
        self.doppler_chip.setToolTip(
            "多普勒链路补偿：存在卫星目标时调谐中心 = 标称频率 + 预测 Δ，"
            "发射上行反向预偏；点击开关补偿（目标失效自动关闭并提示）")
        self.doppler_chip.toggled.connect(self._on_doppler_chip_toggled)
        lay.addWidget(self.doppler_chip)
        # 补偿量常驻读数（与上面的开关分开：开关只映射 enabled，读数只说施加量）
        self.doppler_readout = QLabel("多普勒: —")
        self.doppler_readout.setObjectName("statusValue")
        self.doppler_readout.setToolTip(
            "多普勒补偿读数：— 表示不是本机施加或施加量未知；"
            "0 Hz 才表示确实算出 0")
        lay.addWidget(self.doppler_readout)
        # 发射频点常驻读数（与接收多普勒读数列）：实际发波/标称/预偏状态。
        # 只在频率变化/目标切换/设备就绪时刷新，不做每帧轮询。
        self.tx_readout = QLabel("发射: —")
        self.tx_readout.setObjectName("statusValue")
        self.tx_readout.setToolTip(
            "发射频点读数：实际发波频率 / 标称频率 / 预偏状态。"
            "— 表示未连接设备或读数未知，绝不表示 0")
        lay.addWidget(self.tx_readout)
        # 发射能力常驻声明（docs/doppler-compensation.md §3.8.2）：必须在
        # PTT 可按下之前就能看见，不许只放 tooltip；「驱动级已实现 / 真机
        # 未验证」时按钮可点但这行常驻黄色警示。
        # 窄屏（DPR 1.0 / 1344×700）下改为图标化：灰色信息图标 / 黄色警示
        # 图标本身仍是常驻可见信号；完整文案进 tooltip + 可访问名，
        # 按钮可用 Tab 聚焦（见 docs/narrow-screen-plan.md §2.3.1）。
        self.tx_capability_label = QToolButton()
        self.tx_capability_label.setObjectName("txCapability")
        self.tx_capability_label.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.tx_capability_label.setFocusPolicy(Qt.StrongFocus)
        self.tx_capability_label.setText("")
        self.tx_capability_label.setAccessibleName("本后端发射能力")
        self.tx_capability_label.setToolTip(
            "说明本设备能不能发射；显示「驱动级已实现」只代表代码支持，"
            "未经真机验证前请谨慎发射")
        self.tx_capability_label.clicked.connect(
            self._show_tx_capability_details)
        lay.addWidget(self.tx_capability_label)
        self._refresh_tx_capability_label()
        # 最小发射面：PTT 按钮（按下=发射，再次按下=停止）。默认置灰；
        # 无设备/无 TX 通道/回放中都会在 tooltip 里写明原因（不出现"点了没反应"）。
        self.tx_button = QToolButton()
        self.tx_button.setObjectName("statusValue")
        self.tx_button.setCheckable(True)
        self.tx_button.setText("发射")
        self.tx_button.setToolTip("发射/停止（PTT）：需经人工确认")
        self.tx_button.toggled.connect(self._on_tx_button_toggled)
        self.tx_button.setEnabled(False)
        lay.addWidget(self.tx_button)
        lay.addStretch(1)

        # 呼号（全局可编辑，持久化）
        lay.addWidget(QLabel("呼号:"))
        self.callsign_edit = QLineEdit()
        self.callsign_edit.setPlaceholderText("输入你的呼号")
        self.callsign_edit.setMaxLength(16)
        self.callsign_edit.setMinimumWidth(80)
        self.callsign_edit.setMaximumWidth(200)
        self.callsign_edit.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.callsign_edit.setToolTip("业余无线电呼号（落盘保存，显示在星象仪模式图）")
        self.callsign_edit.editingFinished.connect(self._on_callsign_edited)
        lay.addWidget(self.callsign_edit)
        lay.addWidget(self._vsep())
        # 窄屏下 UTC 只留时分秒（约 180px）；完整日期时间进 tooltip，信息不丢。
        self.status_utc = QLabel("UTC --:--:--")
        self.status_utc.setObjectName("statusValue")
        self.status_utc.setToolTip("协调世界时（完整日期时间）")
        lay.addWidget(self.status_utc)
        parent_layout.addWidget(bar)

    def _build_bottom_dock(self, parent_layout: QVBoxLayout) -> None:
        """底部坞（48px）：Home 按钮 + 当前解调模式大字号块 + Now Bar。

        窄屏下由 74 收到 48（省 26px 高）：控件一个不删、只收紧纵向留白。
        """
        dock = QWidget()
        dock.setObjectName("bottomDock")
        dock.setFixedHeight(48)
        lay = QHBoxLayout(dock)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(16)

        # Home 按钮（回频谱页）
        self.home_btn = QPushButton("⌂")
        self.home_btn.setObjectName("iconButton")
        self.home_btn.setMinimumSize(28, 28)
        self.home_btn.setMaximumSize(48, 48)
        self.home_btn.setToolTip("回到频谱主页")
        self.home_btn.clicked.connect(self._go_home)
        lay.addWidget(self.home_btn)

        # 模式块：当前解调模式大字号
        self.dock_mode_label = QLabel("FM")
        self.dock_mode_label.setObjectName("dockMode")
        self.dock_mode_label.setToolTip(
            "现在使用的解调方式；要切换可用顶栏下拉，"
            "频谱获焦时按 A/N/F/W/C 更直接")
        lay.addWidget(self.dock_mode_label)

        lay.addWidget(self._vsep())

        # Now Bar：频率 / 增益 / 带宽 / 录音状态 / 处理指示
        self.dock_freq_label = QLabel("—")
        self.dock_freq_label.setObjectName("dockNow")
        lay.addWidget(self.dock_freq_label)
        lay.addWidget(self._vsep())
        self.status_gain = QLabel("增益: —")
        self.status_gain.setObjectName("statusValue")
        lay.addWidget(self.status_gain)
        lay.addWidget(self._vsep())
        self.status_bw = QLabel("带宽: —")
        self.status_bw.setObjectName("statusValue")
        lay.addWidget(self.status_bw)
        lay.addWidget(self._vsep())
        self.status_dev = QLabel("设备: —")
        self.status_dev.setObjectName("statusValue")
        lay.addWidget(self.status_dev)
        lay.addWidget(self._vsep())
        self.status_rssi = QLabel("信号: —")
        self.status_rssi.setObjectName("statusValue")
        lay.addWidget(self.status_rssi)
        lay.addStretch(1)
        self.status_processing = QLabel("待机")
        self.status_processing.setObjectName("dockHint")
        lay.addWidget(self.status_processing)
        parent_layout.addWidget(dock)

    def _vsep(self) -> QLabel:
        sep = QLabel("|")
        sep.setObjectName("dockHint")
        return sep

    def _build_status_bar(self):
        """构建底部 QStatusBar——仅用于瞬态消息（showMessage）。

        常驻射频字段（连接/频率/采样率/带宽/增益/设备/信号/GPS/UTC/呼号/
        Home/模式块/Now Bar）已搬到顶栏 _build_top_bar 与底坞 _build_bottom_dock。
        这里只保留一个极薄的 QStatusBar 供各处 self.statusBar().showMessage() 使用，
        不展示假数据。
        """
        status_bar = QStatusBar()
        self.setStatusBar(status_bar)
        # 「查看怎么办 →」：只在带上 error_catalog code 的错误消息里出现；
        # 普通瞬态消息（messageChanged）自动清掉旧链接，绝不指向过期错误。
        self._remedy_link = RemedyLinkLabel(status_bar)
        status_bar.addPermanentWidget(self._remedy_link)
        status_bar.messageChanged.connect(
            lambda _text: self._remedy_link.clear_code())

    def _show_error_status(self, text: str, code=None, timeout: int = MSG_IMPORTANT):
        """状态栏错误消息 + 可点的「查看怎么办」入口（无条目则不出链接）。"""
        self.statusBar().showMessage(text, timeout)  # 先触发 messageChanged 清旧链接
        if code:
            self._remedy_link.set_code(code)

    def _show_error_dialog(self, icon, title: str, text: str, code=None):
        """有正文的错误弹窗：底部追加「查看怎么办」，不新增按钮/不动默认焦点。"""
        box = QMessageBox(icon, title, text, QMessageBox.Ok, self)
        box.setDefaultButton(QMessageBox.Ok)
        if code:
            attach_remedy_link(box, code)
        box.exec()

    def _go_home(self):
        """主页按钮：切回频谱 Tab。"""
        tab = getattr(self, "left_tab", None)
        if tab is None:
            return
        for i in range(tab.count()):
            if "频谱" in tab.tabText(i):
                tab.setCurrentIndex(i)
                return

    # ------------------------------------------------------------------
    # 视觉常量：全部从 tokens 单例取色，不在业务代码散落 magic hex。
    # ------------------------------------------------------------------
    @staticmethod
    def _tok(name: str, default: str = "") -> str:
        """从 tokens.COLORS 取一个语义色（取不到回退 default，绝不崩）。"""
        try:
            return tokens().COLORS.get(name, default)
        except Exception:
            return default

    def _connect_button_qss(self) -> str:
        """主操作（连接）按钮高亮 QSS——强调色取自 tokens，不硬编码 hex。"""
        accent = self._tok("light_accent", "#C4845C")
        hover = self._tok("light_accent_hover", "#D4946C")
        fg = self._tok("text_primary", "#FFFFFF")
        return (
            f"QPushButton {{ background-color:{accent}; color:{fg};"
            f" font-weight:600; padding:0 14px; }}"
            f"QPushButton:hover {{ background-color:{hover}; }}")

    def _conn_label_qss(self, connected: bool, highlighted: bool = False) -> str:
        """工具栏连接状态文字色：已连=成功绿，未连=主题字色。"""
        if connected:
            return f"color: {self._tok('success', '#6BA89A')}; font-weight: 600;"
        if highlighted:
            return f"color: {self._tok('light_accent', '#C4845C')}; font-weight: 600;"
        return "font-weight: 600;"

    def _create_sky_widget(self):
        """创建星象仪模式 widget：路B工厂 create_sky_widget() 优先，回退 RFSkyView。

        路B（星空组，负责 rf_sky_view.py）会产出自包含天空 widget 并在模块里
        暴露 ``create_sky_widget()`` 工厂。这里用 hasattr/try 守卫：
          * 工厂存在 → 调用它拿到路B产物；
          * 工厂缺失（路B尚未交付）→ 回退本模块已 import 的 RFSkyView。
        无论哪条路，返回的 widget 都要支持 addTab / set_callsign / get_observer /
        object_clicked 等既有接口（缺哪个由调用方 hasattr 守卫，绝不崩）。
        """
        try:
            from rf_sky_view import create_sky_widget as _factory  # type: ignore
            w = _factory()
            if w is not None:
                return w
        except Exception:
            pass
        return RFSkyView()

    def _set_processing_status(self, text: str):
        """更新底 Dock 的"正在处理"指示（录制/回放/流水线）。无设备由调用方置灰。"""
        try:
            self.status_processing.setText(text)
        except Exception:
            pass

    def _update_utc_clock(self):
        """每秒刷新顶部 UTC 时间（窄屏只显示时分秒，完整日期在 tooltip）。"""
        try:
            utc = QDateTime.currentDateTimeUtc()
            self.status_utc.setText("UTC " + utc.toString("HH:mm:ss"))
            self.status_utc.setToolTip(
                "协调世界时 " + utc.toString("yyyy-MM-dd HH:mm:ss"))
        except Exception:
            pass

    def _update_status_bar(self):
        """统一从后端状态刷新底部状态栏射频字段。

        无后端 / 后端异常时全部显「—」；绝不保留旧值或编造数值。
        由 _poll_sdr_iq 节流调用（约每 500ms），也可在连接/断开时手动触发。
        """
        backend = self._active_sdr_backend
        if backend is None:
            for lbl, txt in (
                (self.status_freq, "频率: —"),
                (self.status_sr, "采样率: —"),
                (self.status_bw, "带宽: —"),
                (self.status_gain, "增益: —"),
                (self.status_dev, "设备: —"),
                (self.status_rssi, "信号: —"),
            ):
                try:
                    lbl.setText(txt)
                except Exception:
                    pass
            # 顶栏大字号频率显示同步为「—」（无后端绝不展示旧值/假值）
            try:
                self.toolbar_freq_label.setText("—")
            except Exception:
                pass
            try:
                self.doppler_readout.setText("多普勒: —")
                self.tx_readout.setText("发射: —")
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
        # 0 Hz 的语义是“设备尚未上报真实频点”（重连后常见），不是 0.000 MHz；
        # 三处读数同源，统一用占位，绝不写假数值。
        try:
            if f_hz > 0:
                self.status_freq.setText(f"频率: {f_hz / 1e6:.3f} MHz")
                self.toolbar_freq_label.setText(f"{f_hz / 1e6:.3f} MHz")
                self.dock_freq_label.setText(f"{f_hz / 1e6:.3f} MHz")
            else:
                self.status_freq.setText("频率: —")
                self.toolbar_freq_label.setText("—")
                self.dock_freq_label.setText("—")
        except Exception:
            pass
        # 顶栏解调模式下拉同步（blockSignals 防回环；后端未上报则不动，不编造）
        try:
            mode = getattr(st, "demod_mode", None) or getattr(st, "mode", None)
            if mode:
                self.toolbar_mode_combo.blockSignals(True)
                self.toolbar_mode_combo.setCurrentText(str(mode).upper())
                self.toolbar_mode_combo.blockSignals(False)
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
        # 信号电平：用最近一次 IQ 功率估计（_poll_sdr_iq 写入 _last_dbfs）；
        # 还没测到就显「—」，绝不把 0 dBFS（满量程）画成真实读数。
        try:
            self.status_rssi.setText(
                "信号: —" if self._last_dbfs is None
                else f"信号: {self._last_dbfs:.1f} dBFS")
        except Exception:
            pass
        # 多普勒补偿读数：直接照后端/接收链的真值渲染，没有就不编
        try:
            self.doppler_readout.setText(
                "多普勒: " + format_doppler_status(
                    backend.get_doppler_compensation_status()))
        except Exception:
            try:
                self.doppler_readout.setText("多普勒: 补偿失败")
            except Exception:
                pass

    # ---------------------------------------------------------------- 发射读数
    def _schedule_tx_readout(self) -> None:
        """发射频点读数防抖刷新：连续调谐/拖动只算最后一次。

        契约要求只在频率变化/面板打开/目标切换时算，不做每帧轮询；
        resolve_tx_doppler 在跟踪中会跑一次 SGP4，拖动时不能每个事件都算。
        """
        timer = getattr(self, "_tx_readout_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(250)
            timer.timeout.connect(self._refresh_tx_readout)
            self._tx_readout_timer = timer
        timer.start()

    def _refresh_tx_readout(self) -> None:
        """重算发射频点读数。Δ 只由 resolve_tx_doppler 裁决，UI 不自己算。"""
        lbl = getattr(self, "tx_readout", None)
        if lbl is None:
            return
        if self._replay_timer is not None:
            lbl.setText("发射: " + format_tx_doppler(None, replay=True))
            return
        if getattr(self, "_active_sdr_backend", None) is None:
            lbl.setText("发射: —")  # 无设备 = 无发射频点，不展示旧值
            return
        nominal_hz = float(getattr(self, "_vfo_center_hz", 0.0) or 0.0)
        if nominal_hz <= 0.0:
            lbl.setText("发射: —")
            return
        try:
            from mbdsdr_ai.sdr_tools import resolve_tx_doppler
            _freq_hz, note = resolve_tx_doppler(nominal_hz, None)
        except Exception as e:  # 后端契约不可用：读数退回「—」，不编造
            logger.debug("发射频点读数不可用: %s", e)
            lbl.setText("发射: —")
            return
        lbl.setText("发射: " + format_tx_doppler(note))

    # ---------------------------------------------------------------- 最小发射面
    def _tx_block_reason(self) -> str:
        """不能发射的原因（空串=可以发）。置灰与 tooltip 共用这一个出口。

        能力判定统一交给 :func:`ptt_state`（唯一事实源是 tx_capability()）：
        不再按 `write_tx` / `supports_tx()` 的布尔直接说「没有 TX 通道」——
        Soapy 通用路径没有写流，但驱动能发，把能说成不能同样是假信息。
        """
        if self._replay_timer is not None:
            return "回放中：保持原频率，不发射"
        backend = getattr(self, "_active_sdr_backend", None)
        if backend is None:
            return "未连接设备：请先连接支持发射的 SDR"
        text, clickable, _warn = ptt_state(backend)
        if clickable:
            return ""
        return text.split("：", 1)[-1]

    def _show_tx_capability_details(self) -> None:
        """点按图标：弹出完整能力文案（键盘 Space/Enter 也可触发）。"""
        lbl = getattr(self, "tx_capability_label", None)
        if lbl is None or not lbl.toolTip():
            return
        QToolTip.showText(lbl.mapToGlobal(lbl.rect().bottomLeft()),
                          lbl.toolTip(), lbl)

    def _refresh_tx_capability_label(self) -> None:
        """PTT 常驻能力声明：文案/黄色警示随当前后端刷新（§3.8.2 七行表）。

        图标化后完整文案进 tooltip + 可访问名；警示态仍是常驻黄色信号
        （图标与底色同时变黄），不是「只有悬停才看得到」。
        """
        lbl = getattr(self, "tx_capability_label", None)
        if lbl is None:
            return
        backend = getattr(self, "_active_sdr_backend", None)
        text, _clickable, warn = ptt_state(backend)
        # 视觉上只画图标（ToolButtonIconOnly 不绘制文字），但 text() 保留全文：
        # 既有「常驻文案原文」断言不放松，读屏/程序侧仍能取到完整状态。
        lbl.setText(text)
        lbl.setToolTip(text)
        lbl.setAccessibleDescription(text)
        if warn:
            lbl.setIcon(self.style().standardIcon(QStyle.SP_MessageBoxWarning))
            amber = tokens().COLORS["status_warning"]
            lbl.setStyleSheet(
                f"background-color: rgba(200, 160, 64, 0.18);"
                f" border: 1px solid {amber}; border-radius: 3px;"
                f" color: {amber}; padding: 0 6px;")
            # 完整状态原文留在 tooltip（测试按原文钉住）；附加操作提示进
            # 可访问描述，不改变 tooltip 原文。
            lbl.setAccessibleDescription(
                text + "；按钮可按下，失败时状态栏会显示真实错误原因")
        else:
            lbl.setIcon(self.style().standardIcon(QStyle.SP_MessageBoxInformation))
            lbl.setStyleSheet("")
            lbl.setToolTip(text)

    def _set_tx_button_checked(self, checked: bool) -> None:
        """程序改选中态（防重入，避免触发 toggled 里的发/停逻辑）。"""
        btn = getattr(self, "tx_button", None)
        if btn is None or btn.isChecked() == bool(checked):
            return
        self._tx_button_guard = True
        try:
            btn.setChecked(bool(checked))
        finally:
            self._tx_button_guard = False

    def _refresh_tx_button_state(self) -> None:
        """按钮可用性/文案/tooltip 单一出口：置灰必须说明原因，不出现"点了没反应"。"""
        btn = getattr(self, "tx_button", None)
        if btn is None:
            return
        self._refresh_tx_capability_label()
        if self._tx_session is not None:
            self._set_tx_button_checked(True)
            btn.setText("停止发射")
            btn.setEnabled(True)
            btn.setToolTip("正在发射：再次按下停止（PTT）")
            return
        reason = self._tx_block_reason()
        self._set_tx_button_checked(False)
        btn.setText("发射")
        btn.setEnabled(not reason)
        btn.setToolTip("发射/停止（PTT）：需经人工确认" if not reason
                       else f"无法发射：{reason}")

    @Slot(bool)
    def _on_tx_button_toggled(self, checked: bool) -> None:
        """PTT：按下 → 能力检查 → 预偏裁决 → 真人确认 → 发波；再按 → 停止。"""
        if self._tx_button_guard:
            return
        if not checked:
            self._stop_tx()
            return
        reason = self._tx_block_reason()
        if reason:
            self._set_tx_button_checked(False)
            self.statusBar().showMessage(f"无法发射：{reason}", MSG_IMPORTANT)
            return
        nominal_hz = float(getattr(self, "_vfo_center_hz", 0.0) or 0.0)
        if nominal_hz <= 0.0:
            self._set_tx_button_checked(False)
            self.statusBar().showMessage("无法发射：当前没有标称频率", MSG_IMPORTANT)
            return
        # 契约唯一真相源：发波频率与读数用同一次裁决结果，UI 不算 Δ
        try:
            from mbdsdr_ai.sdr_tools import resolve_tx_doppler
            freq_hz, note = resolve_tx_doppler(nominal_hz, None)
        except Exception as e:  # noqa: BLE001
            logger.warning("发射预偏裁决失败: %s", e)
            self._set_tx_button_checked(False)
            self.tx_readout.setText("发射: " + format_tx_doppler(
                None, failed_reason=f"预偏裁决失败：{e}"))
            self.statusBar().showMessage(f"未发射（预偏裁决失败：{e}）", MSG_IMPORTANT)
            return
        self.tx_readout.setText("发射: " + format_tx_doppler(note))
        args = {"nominal_hz": round(nominal_hz, 3),
                "freq_hz": round(float(freq_hz), 3)}
        approved = self._confirm_write_action(
            "desktop_transmit", args,
            prompt=(f"确认发射？\n\n"
                    f"{format_tx_doppler(note)}\n\n"
                    "发射会占用设备并可能影响接收；确认后立即发波，是否继续？"))
        self._audit_approval("desktop_transmit", args, approved, note="桌面 PTT")
        if not approved:
            self._set_tx_button_checked(False)
            self.tx_readout.setText("发射: " + format_tx_doppler(
                None, failed_reason="人工确认未通过"))
            self.statusBar().showMessage("未发射（人工确认未通过）", MSG_IMPORTANT)
            return
        self._start_tx(self._active_sdr_backend, freq_hz, args)

    def _start_tx(self, backend, freq_hz: float, args: dict) -> None:
        """人工确认通过后：签发一次性 token → 后台 TxSession 连续发波。"""
        try:
            from mbdsdr_ai.tx_guard import (
                TxSession, action_digest, capabilities_from_device,
                capabilities_from_hal_backend, issue_confirmation,
                policy_from_env)
        except Exception as e:  # noqa: BLE001
            logger.warning("发射安全网关不可用: %s", e)
            self._set_tx_button_checked(False)
            self.statusBar().showMessage(f"未发射（安全网关不可用：{e}）", MSG_IMPORTANT)
            return
        # desktop 后端的能力源是 SDRDevice；hal 后端才用 hal 版推导
        caps = (capabilities_from_device(getattr(backend, "device", None))
                if getattr(backend, "device", None) is not None
                else capabilities_from_hal_backend(backend))
        if not caps.known:
            self._set_tx_button_checked(False)
            self.tx_readout.setText("发射: " + format_tx_doppler(
                None, failed_reason=f"设备发射能力未知（{caps.source}）"))
            self.statusBar().showMessage(
                f"未发射（设备能力未知：{caps.source}）", MSG_IMPORTANT)
            return
        try:
            token = issue_confirmation("desktop_transmit", args,
                                       note="桌面发射按钮（人工确认）")
            action = action_digest("desktop_transmit", args)
        except Exception as e:  # noqa: BLE001
            logger.warning("签发发射 token 失败: %s", e)
            self._set_tx_button_checked(False)
            self.statusBar().showMessage(f"未发射（签发授权失败：{e}）", MSG_IMPORTANT)
            return
        session = TxSession(backend)
        self._tx_session = session
        # 半双工：先在 UI 线程暂停接收（TxSession 运行在后台线程，不能跨线程碰 Qt 定时器）
        try:
            self._stop_iq_streams()
        except Exception as e:  # noqa: BLE001
            logger.warning("发射前暂停接收失败: %s", e)
        self._refresh_tx_button_state()
        carrier = np.ones(4096, dtype=np.complex64)

        def _blocks():
            while not session.cancel_event.is_set():
                yield carrier

        def _run():
            try:
                info = session.transmit(
                    _blocks(), freq_hz=float(freq_hz),
                    tx_token=token, action=action, caps=caps,
                    regulatory=policy_from_env())
            except Exception as e:  # noqa: BLE001
                logger.error("发射线程异常: %s", e)
                info = {"state": "failed", "reason": f"发射线程异常: {e}"}
            self._tx_finished.emit(info)

        self._tx_thread = threading.Thread(target=_run, name="desktop-tx",
                                           daemon=True)
        self._tx_thread.start()

    def _stop_tx(self, reason: str = "用户停止发射", *, wait: bool = False) -> None:
        """停止当前发射（若空闲则无动作）。``wait`` 用于断开/回放前确认已停波。"""
        session = self._tx_session
        if session is None:
            self._refresh_tx_button_state()
            return
        session.cancel(reason)
        self.statusBar().showMessage("正在停止发射…", MSG_NORMAL)
        if wait and self._tx_thread is not None:
            self._tx_thread.join(timeout=2.0)

    @Slot(object)
    def _on_tx_finished(self, info) -> None:
        """发射线程收尾（UI 线程）：恢复接收、复位按钮、按真实结果报文案。"""
        info = dict(info or {})
        self._tx_session = None
        self._tx_thread = None
        self._set_tx_button_checked(False)
        # 恢复接收（发波前在 UI 线程暂停过）
        try:
            if self._active_sdr_backend is not None and self._replay_timer is None:
                self._start_iq_streams()
        except Exception as e:  # noqa: BLE001
            logger.warning("发射结束后恢复接收失败: %s", e)
            self.statusBar().showMessage(f"恢复接收失败：{e}", MSG_IMPORTANT)
        state = str(info.get("state") or "")
        reason = str(info.get("reason") or "")
        if state == "cancelled":
            self.statusBar().showMessage("发射已停止", MSG_NORMAL)
            self._refresh_tx_readout()
        elif state == "completed":
            self.statusBar().showMessage("发射完成", MSG_NORMAL)
            self._refresh_tx_readout()
        elif state in ("rejected", "failed"):
            self.tx_readout.setText("发射: " + format_tx_doppler(
                None, failed_reason=reason or "发射被拒"))
            self.statusBar().showMessage(f"未发射（{reason}）", MSG_IMPORTANT)
        else:  # aborted / stopping：发过波但被中止，不谎报「未发射」
            self.statusBar().showMessage(f"发射中止（{reason}）", MSG_IMPORTANT)
            self._refresh_tx_readout()
        self._refresh_tx_button_state()

    # ========================================================================
    # 主题
    # ========================================================================

    def _apply_theme(self, theme_name: str):
        """应用主题。"""
        self._current_theme = theme_name
        theme = get_theme(theme_name)
        _app = self.app if hasattr(self, 'app') else QApplication.instance()
        theme.apply(_app)
        # 记录主题基础 QSS，触屏模式下 _apply_touch_stylesheet 在其后追加触控规则
        # （theme.apply 会整体替换 stylesheet，故这里必须重新捕获并叠加）。
        try:
            self._theme_qss = _app.styleSheet() if _app is not None else ""
        except Exception:
            self._theme_qss = ""
        self._apply_touch_stylesheet()

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

    def _on_lang_combo_changed(self, index: int):
        """UI 语言切换：持久化 ui_language；诚实提示重启生效。"""
        tag = self.lang_combo.itemData(index)
        if not tag or getattr(self, "_restoring_settings", False):
            return
        self.settings.set("ui_language", l10n.normalize_ui_language(tag))
        # 翻译器只在启动时装载（与 C++ 桌面端同口径）——不做假实时切换。
        try:
            self.statusBar().showMessage(self.tr("更改语言后重启应用生效"), MSG_NORMAL)
        except Exception:
            pass

    def _on_layout_preset_changed(self, index: int):
        """顶栏布局下拉：切换内置 / 用户自定义布局。"""
        name = self.layout_combo.itemData(index)
        if not name:
            return
        dlm = getattr(self, "dock_layout", None)
        if dlm is not None:
            dlm.apply_preset(name)
            dlm.save_state()
        self._update_layout_delete_btn()

    # ------------------------------------------------------------------
    # 布局下拉 / 保存 / 删除 / 恢复出厂
    # ------------------------------------------------------------------
    def _refresh_layout_combo(self) -> None:
        """从 dock_layout.list_layouts() 动态填充下拉，并选中当前 active 布局。"""
        dlm = getattr(self, "dock_layout", None)
        combo = getattr(self, "layout_combo", None)
        if dlm is None or combo is None:
            return
        names = dlm.list_layouts()
        active = dlm.current_preset
        combo.blockSignals(True)
        combo.clear()
        for n in names:
            combo.addItem(n, n)
        idx = combo.findData(active)
        if idx < 0:
            idx = 0
        combo.setCurrentIndex(idx)
        combo.blockSignals(False)
        self._update_layout_delete_btn()

    def _update_layout_delete_btn(self) -> None:
        """内置布局不可删：删除按钮仅对用户自定义布局可用。"""
        dlm = getattr(self, "dock_layout", None)
        btn = getattr(self, "layout_delete_btn", None)
        combo = getattr(self, "layout_combo", None)
        if dlm is None or btn is None or combo is None:
            return
        name = combo.currentData()
        btn.setEnabled(bool(name) and not dlm.is_builtin(name))

    def _on_save_layout(self) -> None:
        """弹出输入框，把当前布局存为自定义预设。"""
        dlm = getattr(self, "dock_layout", None)
        if dlm is None:
            return
        name, ok = QInputDialog.getText(self, "保存布局", "布局名称:")
        if not ok:
            return
        name = (name or "").strip()
        if not name:
            return
        if dlm.save_user_layout(name):
            self._refresh_layout_combo()
            idx = self.layout_combo.findData(name)
            if idx >= 0:
                self.layout_combo.blockSignals(True)
                self.layout_combo.setCurrentIndex(idx)
                self.layout_combo.blockSignals(False)
            self._update_layout_delete_btn()
            self.statusBar().showMessage(f"已保存布局「{name}」", MSG_NORMAL)
        else:
            QMessageBox.warning(self, "保存失败", "无法保存布局，请重试。")

    def _on_delete_layout(self) -> None:
        """删除当前选中的用户布局（内置不可删）。"""
        dlm = getattr(self, "dock_layout", None)
        if dlm is None:
            return
        name = self.layout_combo.currentData()
        if not name or dlm.is_builtin(name):
            return
        ret = QMessageBox.question(
            self, "删除布局", f"删除自定义布局「{name}」？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        if dlm.delete_user_layout(name):
            self._refresh_layout_combo()
            # 删除后应用当前下拉选中项
            cur = self.layout_combo.currentData()
            if cur:
                dlm.apply_preset(cur)
                dlm.save_state()

    def _on_reset_layout(self) -> None:
        """恢复出厂布局：清除所有用户预设，回到首个内置。"""
        dlm = getattr(self, "dock_layout", None)
        if dlm is None:
            return
        ret = QMessageBox.question(
            self, "恢复出厂布局",
            "清除所有自定义布局并恢复到默认「专注」布局？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        dlm.reset_to_factory()
        self._refresh_layout_combo()
        self._rebuild_panel_menu_checks()

    # ------------------------------------------------------------------
    # 窗口坐标：恢复时校验屏幕可见性 + 应用内复位入口（B2）
    # ------------------------------------------------------------------
    @staticmethod
    def _sanitize_window_pos(x: int, y: int, w: int, h: int) -> tuple:
        """把恢复的窗口坐标夹到实际存在的屏幕上。

        外接显示器拔掉后，上次保存的 (5000, 400) 会被原样应用，窗口落在
        屏幕外且标题栏够不着。判定口径：窗口与任一屏幕可用区至少有
        96×96 的可见交集才认为可达；否则回落到主屏居中。
        """
        try:
            screens = QGuiApplication.screens()
            primary = QGuiApplication.primaryScreen()
        except Exception:
            return int(x), int(y)
        if not screens or primary is None:
            return int(x), int(y)
        target = QRect(int(x), int(y), max(1, int(w)), max(1, int(h)))
        for scr in screens:
            inter = scr.availableGeometry().intersected(target)
            if inter.width() >= 96 and inter.height() >= 96:
                return int(x), int(y)
        g = primary.availableGeometry()
        return (g.x() + max(0, (g.width() - int(w)) // 2),
                g.y() + max(0, (g.height() - int(h)) // 2))

    def _on_reset_window_position(self) -> None:
        """把主窗口移回主屏中央（B2 的应用内复位入口）。"""
        scr = QGuiApplication.primaryScreen()
        if scr is None:
            return
        if self.isMaximized() and getattr(self, "_app_mode", "ide") != "planetarium":
            self.showNormal()
        g = scr.availableGeometry()
        self.move(g.x() + max(0, (g.width() - self.width()) // 2),
                  g.y() + max(0, (g.height() - self.height()) // 2))
        self.statusBar().showMessage("窗口已重置到主屏中央", MSG_NORMAL)

    # ------------------------------------------------------------------
    # 「面板」菜单：列出所有注册面板，checkable 控制页签显示/隐藏
    # ------------------------------------------------------------------
    def select_tab(self, tab_key: str) -> dict:
        """按页签身份（tabBar tabData，如 ``settings_dock`` / ``spectrum``）切页。

        命令面板的「页签定位」走这里：先确保页签可见（曾从「面板」菜单隐藏
        的也能救回来），再切前并聚焦。找不到就诚实返回 ERROR，不假装切了。
        """
        for area, tab in (("左栏", getattr(self, "left_tab", None)),
                          ("右栏", getattr(self, "right_tab", None))):
            if tab is None:
                continue
            for i in range(tab.count()):
                if tab.tabBar().tabData(i) != tab_key:
                    continue
                tab.setTabVisible(i, True)
                tab.setCurrentIndex(i)
                tab.setFocus()
                return {"ok": True, "status": "DONE",
                        "reason": f"已切到{area}「{tab.tabText(i)}」页签"}
        return {"ok": False, "status": "ERROR",
                "reason": f"没有身份为 {tab_key!r} 的页签"}

    def _find_panel_tab(self, spec):
        """按注册表 spec.name 在前/右栏 QTabWidget 里找对应页签。返回 (tab, idx)。"""
        for tab in (getattr(self, "left_tab", None),
                    getattr(self, "right_tab", None)):
            if tab is None:
                continue
            for i in range(tab.count()):
                t = tab.tabText(i)
                if t == spec.name or spec.name in t or t in spec.name:
                    return tab, i
        return None

    def _build_panel_menu(self) -> None:
        """在菜单栏加「面板」菜单：checkable 勾选控制各面板页签显示/隐藏。"""
        menubar = self.menuBar()
        panel_menu = menubar.addMenu("面板(&P)")
        self._panel_menu = panel_menu
        self._panel_actions: dict = {}
        for spec in registry().list():
            if not spec.closable:
                continue
            act = QAction(spec.name, self)
            act.setCheckable(True)
            loc = self._find_panel_tab(spec)
            checked = True
            if loc is not None:
                tab, idx = loc
                try:
                    checked = bool(tab.isTabVisible(idx))
                except Exception:
                    checked = True
            act.setChecked(checked)
            act.triggered.connect(lambda checked, s=spec: self._toggle_panel(s, checked))
            panel_menu.addAction(act)
            self._panel_actions[spec.id] = act
        panel_menu.addSeparator()
        reset_act = QAction("恢复出厂布局", self)
        reset_act.setToolTip("清除所有自定义布局并回到默认布局")
        reset_act.triggered.connect(self._on_reset_layout)
        panel_menu.addAction(reset_act)

    def _toggle_panel(self, spec, checked: bool) -> None:
        """面板菜单项勾选/取消：显示/隐藏对应页签（不删除控件，可随时再开）。"""
        loc = self._find_panel_tab(spec)
        if loc is None:
            return
        tab, idx = loc
        try:
            tab.setTabVisible(idx, bool(checked))
        except Exception:
            pass

    def _rebuild_panel_menu_checks(self) -> None:
        """重建面板菜单勾选态（恢复出厂 / 切换布局后调用）。"""
        if not getattr(self, "_panel_actions", None):
            return
        for pid, act in self._panel_actions.items():
            spec = registry().get(pid)
            if spec is None:
                continue
            loc = self._find_panel_tab(spec)
            checked = True
            if loc is not None:
                tab, idx = loc
                try:
                    checked = bool(tab.isTabVisible(idx))
                except Exception:
                    checked = True
            act.blockSignals(True)
            act.setChecked(checked)
            act.blockSignals(False)

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
                float(s.get("sample_rate_hz", DEFAULT_SAMPLE_RATE_HZ)))
        except Exception:
            pass
        try:
            self.control_panel.set_agc(bool(s.get("agc_enabled", True)))
        except Exception:
            pass
        try:
            self.control_panel.set_agc_speed(str(s.get("agc_speed", "medium")))
        except Exception:
            pass
        try:
            if self._demod_cfg is not None:
                speed = str(s.get("agc_speed", "medium")).lower()
                self._demod_cfg.agc_speed = (
                    speed if speed in ("off", "fast", "medium", "slow")
                    else "medium")
        except Exception:
            pass
        # UI 语言下拉回填（blockSignals，不触发落盘回写）
        try:
            idx = self.lang_combo.findData(
                l10n.normalize_ui_language(s.get("ui_language", "zh")))
            if idx >= 0:
                self.lang_combo.blockSignals(True)
                self.lang_combo.setCurrentIndex(idx)
                self.lang_combo.blockSignals(False)
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
        # 离线模式（conftest/CI 默认 MBDSDR_OFFLINE=1）：不碰真实硬件。
        # 否则启动 200ms 后就去枚举 USB —— 在测试进程里会同步阻塞（本机
        # RTL-SDR 处于异常态时阻塞发生在原生层、不释放 GIL，整个会话挂死，
        # 连 faulthandler 都转储不出栈）。离线就不该有硬件副作用。
        if os.environ.get("MBDSDR_OFFLINE") == "1":
            # 不枚举硬件，但状态栏提示必须与"无设备"口径一致（含排查关键词）：
            # 离线态同样没有连上后端，用户需要知道怎么排查，不能只说"跳过"。
            self._show_no_rtl_hint("离线模式（未枚举硬件）。" + _NO_SDR_HINT)
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
            # 真的没设备：明确提示，不弹对话框、不造假。
            # 默认 USB 走 SoapySDR（无 GPL）；pyrtlsdr 仅可选自装。
            self._show_no_rtl_hint(_NO_SDR_HINT, code="device_not_found")
            return

        # 构造后端并连接（默认参数：2.048MS/s、AGC 开、PPM 0、offset 关）
        try:
            backend = build_backend_for_device(chosen)
        except Exception as e:
            self._show_no_rtl_hint(f"构造后端失败：{e}",
                                   code=classify_connect_error(str(e)))
            return
        if backend is None:
            self._show_no_rtl_hint(_NO_SDR_HINT, code="device_not_found")
            return

        ok = self._connect_backend(
            backend,
            sample_rate=float(DEFAULT_SAMPLE_RATE_HZ),
            gain=20.0,
            ppm=0,
            agc=True,
            offset_tuning=False,
        )
        if not ok:
            # 连接失败：状态栏已由 _connect_backend 写了错误；补充无设备引导。
            # 链接沿用 _connect_backend 分类出的同一个 code，避免两条提示
            # 对同一次失败给出两个不同的「怎么办」。
            self._show_no_rtl_hint(
                "RTL-SDR 连接失败：检查 Zadig WinUSB 驱动 / SoapySDR 驱动 / 设备是否被占用",
                code=getattr(self, "_last_connect_code", None))

    def _show_no_rtl_hint(self, message: str, code=None):
        """无设备 / 枚举失败时：状态栏 + 频谱上方横幅同时显示明确提示。

        绝不静默走假后端、绝不显示"已连接"。横幅文字与状态栏一致，
        用户一眼看到排查方向（Zadig 驱动 / pyrtlsdr 库）。
        """
        try:
            self._show_error_status(message, code, 8000)
        except Exception:
            pass
        try:
            if self._no_device_hint is not None:
                self._no_device_hint.setText(f"  {message}  ")
                self._no_device_hint.setVisible(True)
                # S1：横幅在「频谱」页里，而首启默认页是「星象仪模式」——不切页
                # 用户根本看不到（状态栏 8 秒后也消失）。提示出现即把该页带到
                # 前台；这是"没有设备"的唯一引导，优先级高于保留上次页签。
                page = getattr(self, "_spectrum_page", None)
                lt = getattr(self, "left_tab", None)
                if page is not None and lt is not None:
                    lt.setCurrentWidget(page)
        except Exception:
            pass
        try:
            self.status_conn.setText("未连接")
        except Exception:
            pass

    def _connect_backend(self, backend, sample_rate: float = float(DEFAULT_SAMPLE_RATE_HZ),
                         gain: float = 20.0, ppm: int = 0,
                         agc: bool = True, offset_tuning: bool = False,
                         bandwidth: float = 0.0) -> bool:
        """通用后端连接流程：connect → 成功则切状态 + 下发参数 + 启动 IQ 流。

        被 _connect_dialog（用户手动选设备）和 _auto_enumerate_and_connect
        （启动自动选 RTL）共用。连接失败返回 False 并写状态栏，不抛异常。
        红线：硬件失败绝不静默切 mock 报 success。
        """
        # B1：切换设备必须先幂等拆除旧连接（停读线程/释放硬件/清 UI 连接态）。
        # 不拆旧后端会出现两种坏状态：旧后端继续供数（界面换了、数据还是旧的）；
        # 同一根棒子被自己占着，新 connect 直接 ACCESS(-3) 失败。
        # 切换设备前先抓住当前调谐：teardown 会 set_connected(False) 清空
        # 频率真值，不先存就会退回落 98.5 MHz 默认——用户切个设备就被换台。
        carried_freq = None
        if self._active_sdr_backend is not None:
            try:
                carried_freq = float(self.control_panel.get_frequency_hz())
            except Exception as exc:  # noqa: BLE001 - 读不到就退回默认，但要留证
                logger.warning("切换设备前读取当前频率失败: %s: %s",
                               type(exc).__name__, exc)
        previous = self._active_sdr_backend
        if previous is not None:
            # 防呆：发射中切换设备会立即停波并释放设备，先确认；
            # 取消则整条切换都不做，保持当前设备与发射。
            if (self._tx_session is not None
                    and not self._confirm_stop_tx("切换设备")):
                self.statusBar().showMessage("已取消切换（发射继续）", MSG_NORMAL)
                return False
            self._teardown_connection()
        # 连接是同步阻塞调用（远端后端默认 5s 超时）：先把「连接中」写上并
        # 冲一次事件循环，否则用户在最长 5s 内看到的仍是旧标签（假状态）。
        try:
            name = getattr(getattr(backend, "device", None), "name", "") or "设备"
            self.conn_label.setText(f"  状态: 连接中 {name}  ")
            self.status_conn.setText(f"连接中 {name}")
            self.statusBar().showMessage(f"正在连接 {name} ...", MSG_QUICK)
            QApplication.processEvents()
        except Exception:
            pass
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
            # 原始错误 → error_catalog code：认不出就不出「查看怎么办」，
            # 绝不硬套一个码把用户带到不相关的条目。
            self._last_connect_code = classify_connect_error(raw_err)
            self._show_error_status(f"连接失败：{friendly}",
                                    self._last_connect_code, 6000)
            try:
                backend.disconnect()
            except Exception:
                pass
            # 切换失败：回滚到拆掉的旧后端，绝不留"未连接"却以为还连着
            if previous is not None:
                self._restore_backend_after_failed_switch(previous, friendly)
            else:
                # 没有可回滚的旧设备：必须把「连接中」收回「未连接」，
                # 不许让一次失败的连接把标签永久停在连接中。
                self.conn_label.setText("  状态: 未连接  ")
                self.status_conn.setText("未连接")
            return False

        # 连接成功：停掉旧 worker，切换到真实后端
        self._last_connect_code = None
        # 新连接从「正常」开始，清掉上一条链路留下的降级态标记
        self._link_degraded = False
        self._link_degraded_announced = False
        self._link_degraded_code = ""
        self._worker_manager.stop()
        self._worker = None
        self._active_sdr_backend = backend
        self._panels_set_sdr_connected(True)
        try:
            self.conn_label.setText(f"  状态: {backend.device.name}  ")
            self.conn_label.setStyleSheet(self._conn_label_qss(True))
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
        self.statusBar().showMessage(f"已连接 {backend.device.name}", MSG_NORMAL)

        # 增益动态范围：按所连设备能力收放面板滑杆（此前 0-49/0-50 硬编码
        # 是"RTL-SDR 典型"占位，接到别的设备也不动）。失败仅忽略——滑杆
        # 退回硬编码兜底范围，不影响连接。
        try:
            max_gain = float(getattr(backend.device, "max_gain", 0.0) or 0.0)
            if max_gain > 0:
                for _pname in ("control_panel", "module_panel"):
                    _panel = getattr(self, _pname, None)
                    _fn = getattr(_panel, "set_gain_range", None)
                    if callable(_fn):
                        _fn(0.0, max_gain)
        except Exception as e:  # noqa: BLE001
            logger.warning("增益范围动态收放失败（已忽略，滑杆保持兜底范围）: %s", e)

        # 下发设备参数（失败仅 warning，不阻断连接）
        try:
            backend.set_sample_rate(float(sample_rate))
        except Exception as e:
            self.statusBar().showMessage(f"采样率应用失败: {e}", MSG_NORMAL)
        # 带宽：0=自动（与后端 bandwidth_hz 语义一致）。此前面板设的带宽
        # 被静默丢弃——只下发了采样率/增益，用户调了也没用。
        try:
            backend.set_bandwidth(float(bandwidth))
        except Exception as e:
            self.statusBar().showMessage(f"带宽应用失败: {e}", MSG_NORMAL)
        # 连接后立即下发默认中心频率（从控制面板读回，否则 98.5MHz）
        default_freq = 98_500_000.0
        try:
            default_freq = float(self.control_panel.get_frequency_hz())
        except Exception:
            default_freq = 98_500_000.0
        try:
            backend.set_frequency(default_freq)
        except Exception as e:
            self.statusBar().showMessage(f"初始频率下发失败: {e}", MSG_NORMAL)
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
        # 设备就绪 → 发射频点读数就位（此后只在频率/目标变化时重算）
        self._refresh_tx_readout()
        self._refresh_tx_button_state()
        # 启动真实 IQ 流：频谱/录制/声卡全部接通
        self._start_iq_streams()
        return True

    def _restore_backend_after_failed_switch(self, previous, reason: str) -> bool:
        """设备切换失败后的回滚：重新连回旧后端，连不上就如实停在未连接。

        画面/数据必须和"真连着的设备"一致：能回滚就连回旧设备并重启流水线；
        回滚也失败时保留明确的"未连接"与失败原因，不做任何假展示。
        """
        try:
            ok = bool(previous.connect())
        except Exception:
            ok = False
        if not ok:
            self.statusBar().showMessage(
                f"切换失败（{reason}），原设备也无法重新连接；请检查后手动连接。",
                MSG_IMPORTANT)
            return False
        self._active_sdr_backend = previous
        self._panels_set_sdr_connected(True)
        try:
            name = previous.device.name
        except Exception:
            name = "原设备"
        try:
            self.conn_label.setText(f"  状态: {name}  ")
            self.conn_label.setStyleSheet(self._conn_label_qss(True))
            self.status_conn.setText(name)
        except Exception:
            pass
        try:
            self.spectrum.set_connected(True)
        except Exception:
            pass
        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)
        self._start_iq_streams()
        self.statusBar().showMessage(
            f"切换失败（{reason}），已恢复原设备 {name}", MSG_IMPORTANT)
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
        # 就地显示枚举结果/进行中，失败不再只写在主窗口状态栏
        result_label = QLabel("")
        result_label.setObjectName("deviceResultLabel")

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

        layout.addWidget(combo)
        layout.addWidget(result_label)

        # 刷新按钮：重新枚举并重建下拉框内容
        refresh_btn = QPushButton("刷新设备列表")
        layout.addWidget(refresh_btn)

        # ---- 设备参数区（对标 SDR++ source.cpp：采样率/增益/PPM/AGC/offset tuning）----
        # 选中真实设备后才可配置；连接成功后立即 apply 到后端。
        param_group = QGroupBox("设备参数（连接后应用）")
        param_form = QFormLayout(param_group)

        sr_combo = QComboBox()

        def _fill_sr_combo(device):
            """采样率档位来自该设备的能力，不再写死前端档位表。

            - 设备报告了量程且后端有离散表 → 用后端表 ∩ 量程；
            - 有量程但后端无离散表 → 只给量程两端（不编中间档）；
            - 量程未知（含尚未连接的 rtl_tcp）→ 单项显式说明且 data=None，
              连接后按默认档；绝不把假档位摆进下拉。
            """
            try:
                from mbdsdr_ai.device_capabilities import sample_rate_options
                options = sample_rate_options(device) if isinstance(device, dict) else None
            except Exception:
                options = None
            sr_combo.blockSignals(True)
            sr_combo.clear()
            if not options:
                sr_combo.addItem(
                    "未知（设备未报告采样率范围，将用默认 "
                    f"{format_sample_rate(DEFAULT_SAMPLE_RATE_HZ)}）", None)
            else:
                for _r in options:
                    sr_combo.addItem(format_sample_rate(_r), float(_r))
                # 默认档与后端 DEFAULT_SAMPLE_RATE 对齐（U-03：唯一 MS/s 口径）
                _idx = sr_combo.findData(float(DEFAULT_SAMPLE_RATE_HZ))
                sr_combo.setCurrentIndex(_idx if _idx >= 0 else 0)
            sr_combo.blockSignals(False)

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

        ok_btn = buttons.button(QDialogButtonBox.Ok)
        busy = {"on": False}

        def _update_param_state():
            """根据当前选中项启用/置灰参数区与 OK（无设备时 OK 不可点）。"""
            d = combo.currentData()
            enabled = d is not None
            # 远程 rtl_tcp 源也支持采样率/增益/PPM 参数，参数区保持启用。
            param_group.setEnabled(enabled)
            # OK 只认真实数据：占位项 data=None，连“确认”都不该可点。
            ok_btn.setEnabled(enabled and not busy["on"])
            # PPM 晶振频偏校正只对 RTL-SDR 类设备有意义（廉价棒典型 20~50ppm）；
            # HackRF/Pluto/Airspy/USRP 等 Soapy 前端无 ppm 概念，置灰该输入。
            # 远程 rtl_tcp 源后端本质也是 RTL-SDR 棒，PPM 同样有效，一并启用。
            is_rtl = bool(enabled) and (
                (isinstance(d, dict) and (d.get("driver") or "").lower() == "rtlsdr")
                or d == self._RTL_TCP_SPECIAL
            )
            ppm_spin.setEnabled(is_rtl)
            # 采样率档位随选中设备能力重建（换设备即换档位集合）
            _fill_sr_combo(d)

        def _fill_combo(device_list, select_prev=None):
            """真实设备优先、rtl_tcp 殿后；无设备时不可选占位项置顶且默认选中。"""
            combo.blockSignals(True)
            combo.clear()
            if device_list:
                for dev in device_list:
                    combo.addItem(_dev_label(dev), dev)
                combo.addItem("远程 rtl_tcp 源 (host:port)", self._RTL_TCP_SPECIAL)
                result_label.setText(f"检测到 {len(device_list)} 台 SDR 设备")
            else:
                combo.addItem(
                    "未发现 SDR 设备（请接好 USB/安装驱动后点「刷新设备列表」）", None)
                combo.model().item(0).setEnabled(False)
                combo.addItem("远程 rtl_tcp 源 (host:port)", self._RTL_TCP_SPECIAL)
                result_label.setText("未发现 SDR 设备（检查 USB/驱动后点刷新）")
            idx = 0
            if select_prev is not None:
                match = combo.findData(select_prev)
                if match >= 0:
                    idx = match
            combo.setCurrentIndex(idx)
            combo.blockSignals(False)
            _update_param_state()

        def _set_busy(on):
            busy["on"] = on
            refresh_btn.setEnabled(not on)
            if on:
                result_label.setText("正在查找设备…")
            ok_btn.setEnabled((not on) and combo.currentData() is not None)

        _fill_combo(devices)
        combo.currentIndexChanged.connect(lambda _i: _update_param_state())

        def _reload_devices():
            """点刷新：重新枚举；期间禁用刷新/OK 并就地显示进行中。"""
            _set_busy(True)
            try:
                try:
                    new_devices = enumerate_all_sdr_devices()
                except Exception:
                    new_devices = []
                _fill_combo(new_devices, select_prev=combo.currentData())
            finally:
                _set_busy(False)

        refresh_btn.clicked.connect(_reload_devices)

        if dlg.exec() != QDialog.Accepted:
            return

        data = combo.currentData()
        if data is None:
            # 占位项不可选；万一被键盘路径确认了，也要说清为什么没继续。
            self._show_error_dialog(
                QMessageBox.Information, "未选择设备",
                "当前没有可连接的 SDR 设备。"
                "下一步：接好 USB/安装驱动后重新打开此窗口，"
                "或选择「远程 rtl_tcp 源」。", code="device_not_found")
            return

        # 收集对话框参数（WebSocket 路径不用）
        chosen_sr = float(sr_combo.currentData() or DEFAULT_SAMPLE_RATE_HZ)
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
                self._show_error_dialog(
                    QMessageBox.Critical, "连接失败",
                    "rtl_tcp 客户端模块未就绪（mbdsdr_ai.rtltcp_client 导入失败）",
                    code="missing_dependency")
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
                self._show_error_dialog(
                    QMessageBox.Warning, "连接失败",
                    f"无法连接远程 rtl_tcp 源 {host}:{port}\n"
                    "请检查地址/端口、网络连通性，以及远端 rtl_tcp 服务是否在运行。",
                    code="network_unreachable")
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
            # _connect_backend 已在状态栏写了详细错误；对话框额外弹一次提醒。
            # code 沿用同一次失败分类结果；认不出（None）就不出链接。
            self._show_error_dialog(
                QMessageBox.Warning, "连接失败",
                "设备连接失败，请查看状态栏错误详情。\n"
                "常见原因：USB 棒未插 / 驱动未装（Zadig WinUSB）/ 被其他软件占用。",
                code=getattr(self, "_last_connect_code", None))

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
        # Python 库缺失（SoapySDR 未装；pyrtlsdr 为可选 GPL-3.0）
        if any(k in low for k in ("no module named", "importerror", "no module")):
            return ("未安装 SDR Python 库：本机 USB 默认装 SoapySDR(SoapyRTLSDR)；"
                    "pyrtlsdr 为可选 GPL-3.0，按需自装")
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
        # S9：连接只是"进行中"，硬件还没就绪。控件保持置灰（_disconnect 已置灰），
        # 等 connection_changed(True) 才启用——否则用户对着未连上的设备调谐/录音
        # 全是假动作。
        self.conn_label.setText(f"  状态: 连接中 {host}:{port}  ")
        self.conn_label.setStyleSheet(self._conn_label_qss(False, highlighted=True))
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
        self._worker.tool_result.connect(self._on_worker_tool_result)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.log_message.connect(self._on_log)

    def _panels_set_sdr_connected(self, connected: bool):
        """把真实 SDR 连接状态同步到各操作控件。
        未连接时：气象云图面板的实时 SDR 按钮置灰；
        控制面板的调谐/音量/模式/预设控件与录音按钮一并禁用，绝不暴露假可控状态；
        频率显示显「—」（避免误导用户以为正在接收 98.5）；
        频谱上方显示引导横幅，连接按钮恢复高亮。"""
        for panel in (getattr(self, "weather_panel", None),):
            if panel is not None:
                try:
                    panel.set_sdr_connected(connected)
                except Exception:
                    pass
        # 数字解码面板（ADS-B / APRS / 卫星云图）随连接状态切换空态文案
        for pname in ("digital_adsb_panel", "digital_aprs_panel",
                      "digital_sat_image_panel"):
            p = getattr(self, pname, None)
            if p is not None:
                try:
                    p.set_sdr_connected(connected)
                except Exception:
                    pass
        # 控制面板：调谐/音量/模式/录音
        try:
            cp = getattr(self, "control_panel", None)
            if cp is not None:
                # 断连必须清真值/模式下拉/录音状态，只置灰会留下“还在工作”的假象。
                if hasattr(cp, "set_connected"):
                    cp.set_connected(connected)
                elif hasattr(cp, "set_sdr_connected"):
                    cp.set_sdr_connected(connected)
        except Exception:
            pass
        # 控制面板频率显示：未连接时显占位；连接后若无真实频率也保持占位，
        # 绝不用 0 Hz 顶替“未知”，真实频率由实际调谐/恢复 setter 回填。
        try:
            cp = getattr(self, "control_panel", None)
            if cp is not None:
                fd = getattr(cp, "freq_display", None)
                if fd is not None and hasattr(fd, "set_frequency"):
                    if not connected:
                        fd.set_frequency(None)
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
        # 内核能力面板：扫频 / 信号识别 随连接状态启停；服务设置注入后端
        try:
            be = getattr(self, "_active_sdr_backend", None)
            for pname in ("scanner_panel", "modulation_panel"):
                p = getattr(self, pname, None)
                if p is not None:
                    p.set_backend(be)
                    p.set_sdr_connected(bool(connected))
            sp = getattr(self, "service_settings_panel", None)
            if sp is not None and hasattr(sp, "set_backend"):
                sp.set_backend(be)
        except Exception:
            pass
        # 频谱标题栏频率/RSSI：未连接时显「—」
        try:
            if not connected:
                self.freq_label.setText("— MHz")
                self.rssi_label.setText("RSSI: —")
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
        # 新增面板：多 VFO / ANR / 星座图 / 设备选择 / 分段增益
        try:
            vp = getattr(self, "vfo_panel", None)
            if vp is not None and hasattr(vp, "set_sdr_connected"):
                vp.set_sdr_connected(bool(connected))
            ap = getattr(self, "anr_panel", None)
            if ap is not None and hasattr(ap, "set_sdr_connected"):
                ap.set_sdr_connected(bool(connected))
            cp = getattr(self, "constellation_panel", None)
            if cp is not None and hasattr(cp, "set_sdr_connected"):
                cp.set_sdr_connected(bool(connected))
            dp = getattr(self, "device_panel", None)
            if dp is not None and hasattr(dp, "set_connected"):
                dp.set_connected(bool(connected))
            gp = getattr(self, "gain_panel", None)
            if gp is not None and hasattr(gp, "set_backend"):
                gp.set_backend(self._active_sdr_backend if connected else None)
        except Exception:
            pass
        # VFO 列表刷新
        try:
            self._refresh_vfo_panel()
        except Exception:
            pass
        # 连接按钮：未连接时恢复橙色高亮引导（色值取自 tokens，与工具栏同源）
        try:
            if not connected:
                self.connect_btn.setStyleSheet(self._connect_button_qss())
            else:
                self.connect_btn.setStyleSheet("")
        except Exception:
            pass
        # 断连后刷新底部状态栏（全部回落到 "--"）
        if not connected:
            self._last_dbfs = None
            self._update_status_bar()

    def _teardown_connection(self, *, stop_tracking: bool = True) -> None:
        """幂等拆除当前连接：停回放/扫频/录制/流水线/跟踪 → 断开硬件 → UI 置未连接。

        设备切换必须在建新后端前走这里：旧后端不释放，同一根棒子再 connect
        会失败；旧流水线不拆，"已连接新设备"就只是界面标签（B1）。
        `_disconnect()` 与 `_connect_backend()` 共用这一条拆除路径，
        重复调用安全（全程 try/except）。
        """
        # 发射中先停波（先停硬件动作，再拆其余资源）
        self._stop_tx("断开连接", wait=True)
        # 回放：先清 resume 标志，避免 _stop_replay 在拆连接过程中又拉起流水线
        self._replay_resume_poll = False
        try:
            self._stop_replay()
        except Exception:
            pass
        # 断开真实 SDR 后端（SoapySDR/RTL-SDR/HackRF）
        # 先停掉回放 / 扫频 / 录制 / IQ 轮询 / 声卡，避免断开后还在读空句柄
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
        if stop_tracking:
            # 跟踪环会继续 _on_tune_sdr 写频率；断开设备必须同步停掉（S15）。
            # stop 内部会把旧接收频率写回设置，仍在旧后端连接期完成，顺序正确。
            try:
                self._stop_satellite_tracking()
            except Exception:
                pass
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
        self._link_degraded = False
        self._link_degraded_announced = False
        self._link_degraded_code = ""
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        self._refresh_tx_readout()  # 无设备 → 发射读数回 "—"，不留旧频点
        self._refresh_tx_button_state()  # 无硬件 → 发射按钮置灰并说明原因

    def _confirm_stop_tx(self, action: str) -> bool:
        """动作会停波时先确认；返回 True 表示用户选择继续。

        发射是唯一「点错一下就断了别人通联」的操作，断开/切设备/关窗
        三条路径都必须先问一句；默认按钮是「否」，误触不会直接停波。
        """
        ans = QMessageBox.question(
            self, f"正在发射，仍要{action}？",
            f"{action}会立即停止当前发射并释放设备。\n是否继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return ans == QMessageBox.Yes

    def _on_disconnect_clicked(self):
        """工具栏「断开」按钮：发射中先确认，误点不直接断波。"""
        if self._tx_session is not None \
                and not self._confirm_stop_tx("断开连接"):
            self.statusBar().showMessage("已取消断开（发射继续）", MSG_NORMAL)
            return
        self._disconnect()

    def _disconnect(self):
        """断开连接（用户点"断开"）：幂等拆除 + 未连接时如实说明。"""
        if self._active_sdr_backend is None and not self._recording:
            # 点了没反应是最糟的体验：没连上也说清楚
            self.statusBar().showMessage("当前未连接 SDR，没有可断开的设备", MSG_NORMAL)
        self._teardown_connection()

    # ========================================================================
    # 信号槽：从 Worker 接收数据更新 UI
    # ========================================================================

    @Slot(bool, str)
    def _on_connection_for_ui(self, connected: bool, message: str):
        if connected:
            self.conn_label.setText(f"  状态: {message}  ")
            self.conn_label.setStyleSheet(self._conn_label_qss(True))
            self.status_conn.setText(message)
            self._link_degraded = False
            self._link_degraded_announced = False
            self._link_degraded_code = ""
            # S9：硬件真正就绪才启用调谐/录音等控件（连接中一直保持置灰）
            self._panels_set_sdr_connected(True)
            # 硬件就绪：把设备报告的工具 schema 注册进 AI agent
            self._sync_ai_mcp_tools()
        else:
            self.conn_label.setText(f"  状态: {message}  ")
            self.conn_label.setStyleSheet(self._conn_label_qss(False))
            self.status_conn.setText(message)
            # S9：连接失败/掉线必须把控件收回到"未连接"并清空频谱。
            # 只改标签会让用户继续调谐/录音（全是假动作），旧谱也一直定格。
            try:
                self.spectrum.set_connected(False)
            except Exception:
                pass
            self._panels_set_sdr_connected(False)
            try:
                self.disconnect_btn.setEnabled(False)
                self.connect_btn.setEnabled(True)
            except Exception:
                pass
            self._refresh_tx_readout()
            self._refresh_tx_button_state()

    @Slot(dict)
    def _on_status_for_ui(self, status: dict):
        freq = status.get("freq_display") or "—"
        # U-02：MCP 固件 RSSI 是 SI4732 dBµV（0–127），不是 dBFS；缺值显「—」。
        rssi = _fmt_rssi_mcp(status.get("rssi"))
        self.freq_label.setText(freq)
        self.rssi_label.setText(f"RSSI: {rssi}")
        self.status_freq.setText(f"频率: {freq}")
        self.status_rssi.setText(f"RSSI: {rssi}")

    @Slot(str)
    def _on_error(self, error: str):
        self.statusBar().showMessage(f"错误: {error}", MSG_NORMAL)

    @Slot(str)
    def _on_log(self, msg: str):
        # 写入状态栏
        try:
            self.statusBar().showMessage(str(msg), MSG_NORMAL)
        except Exception:
            pass

    # ========================================================================
    # 信号槽：从控制面板/AI 面板接收指令
    # ========================================================================

    def _report_hw_failure(self, action: str, exc: Exception) -> None:
        """硬件写失败的三段式提示：发生了什么 / 可能原因 / 下一步。"""
        self.statusBar().showMessage(
            f"{action}未生效：{type(exc).__name__}: {exc}。"
            "可能原因：SDR 已断开、被其他程序占用或驱动异常。"
            "下一步：点工具栏「连接」重连后重试；"
            "界面与已保存配置保持上次成功值。",
            MSG_IMPORTANT,
        )

    def _apply_hw_action(self, action: str, fn) -> bool:
        """执行硬件写入；失败只提示并返回 False，不抛、不提交 UI。"""
        try:
            fn()
        except Exception as e:
            self._report_hw_failure(action, e)
            return False
        return True

    def _revert_tune_ui(self) -> None:
        """把频率/模式显示拉回最后一次真实成功值（失败后不许留在新值）。"""
        last_hz = float(getattr(self, "_backend_center_hz", 0.0) or 0.0)
        if last_hz > 0:
            self.freq_label.setText(f"{last_hz / 1e6:.3f} MHz")
            self.status_freq.setText(f"频率: {last_hz / 1e6:.3f} MHz")
        else:
            self.freq_label.setText("-- MHz")
            self.status_freq.setText("频率: —")
        cp = getattr(self, "control_panel", None)
        if cp is not None:
            if last_hz > 0:
                cp.set_frequency_hz(last_hz)
            prev_mode = (getattr(self._demod_cfg, "mode", None)
                         if self._demod_cfg is not None else None)
            if prev_mode:
                cp.set_mode(prev_mode)

    @Slot(float)
    def _on_tune_fm(self, freq: float):
        backend = self._active_sdr_backend
        if backend is not None:
            if not self._apply_hw_action(
                    "调谐", lambda: backend.set_frequency(freq * 1e6)):
                self._revert_tune_ui()
                return
        if self._worker:
            self._worker.request_tool.emit("tune_fm", {"freq_mhz": freq})
        self.spectrum.set_center_freq(freq)
        # 更新频率显示
        self.freq_label.setText(f"{freq:.3f} MHz")
        self.status_freq.setText(f"频率: {freq:.3f} MHz")
        # VFO 收听频率跟随 → DDC 重算 offset
        self._vfo_center_hz = freq * 1e6
        self._backend_center_hz = freq * 1e6
        self._update_main_vfo_offset()

    @Slot(int)
    def _on_tune_am(self, freq: int):
        backend = self._active_sdr_backend
        if backend is not None:
            if not self._apply_hw_action(
                    "调谐", lambda: backend.set_frequency(freq * 1e3)):
                self._revert_tune_ui()
                return
        if self._worker:
            self._worker.request_tool.emit("tune_am", {"freq_khz": freq})

    @Slot(float, str)
    def _on_tune_sdr(self, freq_hz: float, mode: str):
        backend = self._active_sdr_backend
        if backend is not None:
            if not self._apply_hw_action(
                    "调谐", lambda: backend.set_frequency(freq_hz)):
                self._revert_tune_ui()
                return
            if not self._apply_hw_action(
                    "切换解调模式", lambda: backend.set_demod(mode)):
                self._revert_tune_ui()
                return
        if self._worker:
            self._worker.request_tool.emit("tune_sdr",
                                   {"freq_hz": freq_hz, "mode": mode})
        self.spectrum.set_center_freq(freq_hz / 1e6)
        # 更新频率显示
        self.freq_label.setText(f"{freq_hz / 1e6:.3f} MHz")
        self.status_freq.setText(f"频率: {freq_hz / 1e6:.3f} MHz")
        # VFO 收听频率跟随 → DDC 重算 offset
        self._vfo_center_hz = freq_hz
        self._backend_center_hz = freq_hz
        if self._demod_cfg is not None:
            self._demod_cfg.mode = str(mode).upper()
        self._update_main_vfo_offset()
        # 持久化：频率 + 解调模式（防抖写盘；仅在真实成功后落盘）
        try:
            self.settings.set("frequency_hz", float(freq_hz))
            self.settings.set("demod_mode", str(mode))
        except Exception:
            pass
        # 频率变化 → 发射预偏读数重算（契约唯一真相源，UI 不算 Δ）
        self._schedule_tx_readout()

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
        self._schedule_tx_readout()

    @Slot(str)
    def _on_ai_command(self, text: str):
        # AI 面板提交的自然语言指令 -> 转发给 AI worker
        try:
            # S10：忙锁由 _call_ai 统一把关（命令栏不能绕过）
            if self.ai_panel._call_ai(text) is False:
                self.statusBar().showMessage(
                    "上一条 AI 指令还在执行，本次指令未提交", MSG_NORMAL)
        except Exception:
            pass

    @Slot(int)
    def _on_gain_changed(self, db: int):
        backend = self._active_sdr_backend
        if backend is not None:
            if not self._apply_hw_action(
                    "设置增益", lambda: backend.set_gain(float(db))):
                return
        if self._worker:
            try:
                self._worker.request_tool.emit("sdr_set_gain", {"gain_db": float(db)})
            except Exception:
                pass
        # 持久化：LNA 增益（仅真实成功后落盘）
        try:
            self.settings.set("gain_db", float(db))
        except Exception:
            pass

    def _on_squelch_changed(self, dbfs: float):
        # 滑杆刻度是「相对噪声基线的余量」（-50≈6 dB），不是绝对 dBFS；
        # 门控在 BaselineSquelch / 解调链内部换算，这里只传刻度。
        # 用户手动拖动门限即切到固定门限，关闭自动静噪。
        self._squelch_db = float(dbfs)
        if self._demod_cfg is not None:
            self._demod_cfg.squelch_db = float(dbfs)
            self._demod_cfg.auto_squelch = False
        # 写回后端状态：状态栏据此显示「桌面余量 / C++ 自动 / 手动」；
        # 不写回时界面会一直挂着旧来源（或 none），用户分不清谁在生效。
        backend = getattr(self, "_backend", None)
        set_squelch = getattr(backend, "set_squelch", None)
        if callable(set_squelch):
            try:
                set_squelch(float(dbfs))
            except Exception as exc:  # noqa: BLE001
                logger.warning("同步静噪到后端失败: %s", exc)

    def _on_auto_squelch_toggled(self, on: bool):
        """切换自动/固定静噪。"""
        if self._demod_cfg is not None:
            self._demod_cfg.auto_squelch = bool(on)

    def _on_volume_changed(self, volume: int):
        # volume 是 0–100 人类量纲（控制面板/工具栏统一）；0–63 硬件寄存器
        # 量纲只在下发边界换算，不再倒灌 UI（换算式收敛在 settings 两个纯函数）。
        if self._worker:
            self._worker.request_tool.emit(
                "set_volume", {"volume": volume_pct_to_hw(volume)})
        # (C) 真实声卡音量：0-100 → 0.0-2.0 增益
        if self._audio_player is not None:
            try:
                self._audio_player.set_gain(volume / 100.0 * 2.0)
            except Exception:
                pass
        # 音量持久化（权威源 settings，0-100 人类量纲）。
        # 启动恢复期不回写，避免默认值覆盖已存值。
        if not getattr(self, "_restoring_settings", False):
            try:
                self.settings.set("volume", int(volume))
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
    def _on_mode_hotkey(self, mode: str):
        """谱面单键切解调（gqrx 键盘流 A/N/F/W/S/C）：走面板 combo 全链。

        setCurrentText 触发 control_panel._on_mode_changed → mode_changed
        → 本类 _on_mode_changed（后端 set_demod + 带宽预设），与下拉切换
        完全同路径；已是当前模式时为 no-op。
        """
        cp = getattr(self, "control_panel", None)
        if cp is None or not hasattr(cp, "mode_combo"):
            return
        try:
            cp.mode_combo.setCurrentText(str(mode))
        except Exception:
            pass

    def _on_mode_changed(self, mode: str):
        """模式切换（WFM/NFM/AM/USB/LSB/CW）：立刻切解调 + 改 VFO 带宽，音频不断。

        - backend.set_demod(mode) 同步下发硬件解调模式；
        - _demod_cfg.mode 更新后，后台解调 worker 下一帧即按新模式解调，
          声卡流不重建、不中断（对标 SDR++ 切模式音频无缝）；
        - VFO 带宽按 MODE_VFO_BANDWIDTH 逐模式套用（CW 500Hz / SSB 2.4k /
          AM 6k / NBFM 12.5k / WFM 180k），推到已绑定的 DDC。
        """
        # 真实 SDR 后端：下发解调模式；失败则不提交 UI/不落配置
        backend = self._active_sdr_backend
        if backend is not None:
            if not self._apply_hw_action(
                    "切换解调模式", lambda: backend.set_demod(mode)):
                self._revert_tune_ui()
                return
        # 同步到解调 worker 共享配置（后台线程下一帧生效，音频不中断）
        if self._demod_cfg is not None:
            self._demod_cfg.mode = str(mode).upper()
        # 根据模式套用典型 VFO 带宽（MODE_VFO_BANDWIDTH，逐模式精确值）
        try:
            m = (mode or "FM").upper()
            bw = float(default_bandwidth(m))
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
        # 底坞模式块大字号同步
        try:
            self.dock_mode_label.setText(str(mode).upper())
        except Exception:
            pass

    @Slot(float)
    def _on_sample_rate_changed(self, rate_hz: float):
        """控制面板采样率下拉切换 → 下发后端 + 重建流水线（VFO 重采样系数变了）。

        录音中改率（S4）：先停旧流水线等录制 tap join，收尾当前文件段并写
        sidecar，再用新速率开下一段——同一个 .iq 绝不混两种采样率。各段速率
        全程精确，读取端按 ``segments`` / ``sample_rate_changes`` 分段。
        """
        rate = float(rate_hz)
        if self._recording:
            if rate <= 0.0 or abs(rate - float(self._record_sr)) < 1.0:
                return
            old_sr = float(self._record_sr)
            # 1) 停旧流水线：shutdown join record tap，确保旧速率的块已落盘
            self._stop_iq_streams()
            # 2) 关当前段（sidecar 固定旧速率），得到该段信息
            info = self._finalize_record_segment()
            # 3) 下发新速率；中心频率可能随设备/档位变化，重新读取
            applied_sr = rate
            if self._active_sdr_backend is not None:
                try:
                    ok = self._active_sdr_backend.set_sample_rate(rate)
                    if ok is False:
                        raise RuntimeError("设备拒绝该采样率（后端已回滚原速率）")
                    # 回读真正生效值：RTL 会吸附到 11 档，段头必须写实际速率
                    applied_sr = float(self._active_sdr_backend.get_sample_rate())
                except Exception as e:
                    logger.warning("设备切换采样率失败: %s", e)
                    # 后端速率没变：用旧速率续录新段，绝不让 _recording=True
                    # 却没有任何段可写（否则停止时会覆盖已收尾的旧文件）。
                    next_index = len(self._record_segments) + 1
                    new_path = _record_segment_path(
                        self._record_session_path or self._record_file_path or "",
                        next_index)
                    if not self._open_record_segment(
                            new_path, old_sr, float(self._record_center_hz),
                            datetime.now().isoformat(timespec="seconds"),
                            next_index):
                        # 新段打不开：录音已被 S7 收口停止，接收链仍需继续
                        self._start_iq_streams()
                        return
                    self._start_iq_streams()
                    self.statusBar().showMessage(
                        f"采样率切换失败，继续按 {format_sample_rate(old_sr)} 录制: {e}", MSG_IMPORTANT)
                    return
                if abs(applied_sr - rate) > 1.0:
                    logger.warning("采样率 %s 被设备吸附到 %s", rate, applied_sr)
                    self.statusBar().showMessage(
                        f"设备只接受 {format_sample_rate(applied_sr)}，"
                        f"已按该速率续录（请求 {format_sample_rate(rate)}）",
                        MSG_IMPORTANT)
                    rate = applied_sr
            try:
                center_hz = float(self._active_sdr_backend.get_frequency())
            except Exception as e:
                logger.warning("切换采样率后读取中心频率失败: %s", e)
                center_hz = float(self._record_center_hz)
            # 4) 开下一段（有空段被丢弃时沿用未占用的序号）
            next_index = len(self._record_segments) + 1
            new_path = _record_segment_path(
                self._record_session_path or self._record_file_path or "", next_index)
            if not self._open_record_segment(
                    new_path, rate, center_hz,
                    datetime.now().isoformat(timespec="seconds"), next_index):
                # 新段打不开：录音已被 S7 收口停止，接收链仍需继续
                self._start_iq_streams()
                return
            # 5) 变更表保留精确边界（供只读 sidecar 的读者按段还原）
            if info is not None:
                self._record_rate_changes.append({
                    "time_s": getattr(self, "_record_seconds", 0),
                    "from_sample_rate": old_sr,
                    "sample_rate": rate,
                    "start_sample": sum(int(s["samples"])
                                        for s in self._record_segments),
                })
            self._start_iq_streams()
            self._update_status_bar()
            self.statusBar().showMessage(
                f"采样率 {format_sample_rate(old_sr)} → "
                f"{format_sample_rate(rate)}，录音已分到新文件段: "
                f"{os.path.basename(new_path)}", MSG_IMPORTANT)
        else:
            # 非录音：下发后端 → 回读实际值。三种结果分别说清楚，不改 UI 读数：
            # ① 后端返回 False → 显式拒绝，保持原速率；② 回读≠请求（被吸附）
            # → 报实际生效值并把下拉回填；③ 相等 → 真生效。
            if self._active_sdr_backend is not None:
                try:
                    ok = self._active_sdr_backend.set_sample_rate(rate)
                    applied = float(self._active_sdr_backend.get_sample_rate())
                except Exception as e:
                    logger.warning("设备切换采样率失败（非录音态）: %s", e)
                    self.statusBar().showMessage(
                        f"采样率切换失败（保持原速率）: {e}", MSG_IMPORTANT)
                    return
                if ok is False:
                    logger.warning("设备拒绝采样率 %s，保持 %s", rate, applied)
                    self.statusBar().showMessage(
                        f"该采样率未被设备接受（保持 {format_sample_rate(applied)}）",
                        MSG_IMPORTANT)
                    self.control_panel.set_sample_rate(applied)
                    return
                if abs(applied - rate) > 1.0:
                    logger.warning("采样率 %s 被设备吸附到 %s", rate, applied)
                    self.statusBar().showMessage(
                        f"设备按 {format_sample_rate(applied)} 生效"
                        f"（请求 {format_sample_rate(rate)}）", MSG_IMPORTANT)
                    self.control_panel.set_sample_rate(applied)
                rate = applied
            self._record_sr = rate
            self._stop_iq_streams()
            self._start_iq_streams()
            self._update_status_bar()
        # 持久化：采样率
        try:
            self.settings.set("sample_rate_hz", rate)
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
            self.statusBar().showMessage(f"AGC 设置失败: {e}", MSG_NORMAL)

    @Slot(str)
    def _on_agc_speed_changed(self, speed: str):
        """音频 AGC 档位 → 解调 worker 共享配置（gqrx Fast/Medium/Slow）。"""
        key = str(speed or "medium").lower()
        if key not in ("off", "fast", "medium", "slow"):
            key = "medium"
        try:
            self.settings.set("agc_speed", key)
        except Exception:
            pass
        if self._demod_cfg is not None:
            self._demod_cfg.agc_speed = key

    @Slot(int)
    def _on_ppm_changed(self, ppm: int):
        """控制面板 PPM 校正 → 后端 set_ppm（RTL-SDR 晶振频偏）。"""
        if self._active_sdr_backend is None:
            return
        try:
            if hasattr(self._active_sdr_backend, "set_ppm"):
                self._active_sdr_backend.set_ppm(int(ppm))
        except Exception as e:
            self.statusBar().showMessage(f"PPM 设置失败: {e}", MSG_NORMAL)

    @Slot(bool)
    def _on_offset_tuning_changed(self, enabled: bool):
        """控制面板 Offset Tuning 开关 → 后端 set_offset_tuning（直流抵消）。"""
        if self._active_sdr_backend is None:
            return
        try:
            if hasattr(self._active_sdr_backend, "set_offset_tuning"):
                self._active_sdr_backend.set_offset_tuning(bool(enabled))
        except Exception as e:
            self.statusBar().showMessage(f"Offset tuning 设置失败: {e}", MSG_NORMAL)

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
        """控制面板书签按钮 → 内存保存 + FrequencyManager 持久化 + 频谱标签。"""
        try:
            self._bookmarks.append(
                (float(freq_hz), str(name), str(mode), False))
            if self._freq_mgr is not None:
                try:
                    self._freq_mgr.add(
                        str(name), float(freq_hz), mode=str(mode),
                        category="自定义")
                except Exception:
                    pass
            self._sync_spectrum_bookmarks()
            self.statusBar().showMessage(
                f"已收藏 {float(freq_hz) / 1e6:.3f} MHz {name}", MSG_NORMAL)
        except Exception:
            pass

    def _sync_spectrum_bookmarks(self):
        """内存书签表 → 频谱书签竖旗（[(freq_hz, name), ...]）。"""
        try:
            if hasattr(self.spectrum, "set_bookmarks"):
                self.spectrum.set_bookmarks(
                    [(float(hz), str(name))
                     for hz, name, _m, _b in self._bookmarks])
        except Exception:
            pass

    @Slot(float)
    def _on_bookmark_activated(self, freq_hz: float):
        """点频谱书签竖旗 → 调到该书签频率（gqrx 书签点击调谐）。

        书签带模式则一并切过去；否则保持当前解调模式。
        """
        mode = None
        for hz, _name, m, _b in self._bookmarks:
            if abs(float(hz) - float(freq_hz)) < 1.0:
                mode = str(m) if m else None
                break
        if not mode:
            mode = (self._demod_cfg.mode if self._demod_cfg is not None
                    else "FM")
        self._on_tune_sdr(float(freq_hz), mode)

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
        """从 ~/.mbdsdr/config.json 读 API 配置并初始化 AI agent。

        配置读取经 normalize_config_keys（旧键 api_base → base_url 一次性迁移），
        与 ai_panel 的写入键统一；随后接线 MCP 硬件工具注册。
        """
        import json as _json, os
        cfg_path = os.path.expanduser("~/.mbdsdr/config.json")
        try:
            if os.path.exists(cfg_path):
                with open(cfg_path) as f:
                    cfg = _json.load(f)
                from mbdsdr_ai.config import normalize_config_keys
                cfg = normalize_config_keys(cfg)
                if cfg.get("api_key"):
                    self.ai_panel.init_agent(
                        api_key=cfg.get("api_key", ""),
                        base_url=cfg.get("base_url", ""),
                        model=cfg.get("model", ""),
                    )
        except Exception:
            pass  # 无配置则不启用 AI，不崩溃
        # 接线「桌面 AI → 硬件工具」编排：取设备工具 schema 注册进 agent
        self._sync_ai_mcp_tools()

    def _sync_ai_mcp_tools(self):
        """把 MCP 硬件工具 schema 注册进 AI agent（编排链路接线点）。

        时机：AI agent 初始化后、硬件连接成功后各调一次。工具 schema 只来自
        真实设备 list_tools——未连接时如实空态（不注册、不臆造 schema）；
        agent 尚未创建时 ai_panel 挂起缓存，配好 API Key 自动补注册。
        """
        try:
            worker = self._worker
            tools = worker.fetch_mcp_tools() if (
                worker is not None and worker.connected) else []
            self.ai_panel.register_mcp_tools(tools, self._mcp_call_handler)
        except Exception:
            pass  # 接线失败不崩主窗口；下次连接成功时会重试

    # ── 人工确认门（S13 红线）──────────────────────────────────────────
    # 模型驱动的写/发射/改站址，确认只能来自真人：模型传 arm_manual=true
    # 一律作废（参数在入口处剥离），必须经过桌面端弹窗或注入的确认控件。
    @staticmethod
    def _summarize_args(args: dict) -> str:
        parts = []
        for k, v in (args or {}).items():
            s = repr(v)
            if len(s) > 60:
                s = s[:57] + "..."
            parts.append(f"{k}={s}")
        return ", ".join(parts) or "（无参数）"

    def _audit_approval(self, tool_name: str, args: dict, approved: bool,
                        note: str = "") -> None:
        """把人工确认结果落到 JSONL，便于事后审计（时间戳+动作摘要）。"""
        rec = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "tool": tool_name,
            "args": self._summarize_args(args),
            "approved": bool(approved),
            "note": note,
        }
        try:
            os.makedirs(os.path.dirname(self._approval_audit_path), exist_ok=True)
            with open(self._approval_audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:  # noqa: BLE001
            logger.warning("人工确认审计写入失败: %s", e)

    def _confirm_write_action(self, tool_name: str, args: dict,
                              prompt: Optional[str] = None) -> bool:
        """桌面真人确认（默认弹窗；测试可注入 _approval_handler 替代）。

        ``prompt`` 仅供桌面自发操作（如 PTT 发射）改写弹窗正文；判定逻辑不变。
        """
        handler = self._approval_handler
        if handler is not None:
            try:
                return bool(handler(tool_name, dict(args or {})))
            except Exception:
                return False
        if prompt:
            text = prompt
        else:
            text = (f"AI 请求执行写操作：{tool_name}\n\n"
                    f"{self._summarize_args(args)}\n\n"
                    "该操作会改变设备状态且不可自动撤销，是否允许执行？")
        ret = QMessageBox.warning(
            self, "确认硬件操作", text,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return ret == QMessageBox.Yes

    def _request_human_approval(self, tool_name: str, args: dict) -> bool:
        """写操作放行判定：只认真人确认结果，模型/参数不参与。"""
        if threading.current_thread() is threading.main_thread():
            approved = bool(self._confirm_write_action(tool_name, args))
            self._audit_approval(tool_name, args, approved)
            return approved
        # AI 后台线程：投递到 UI 线程弹窗，阻塞等待真人选择（超时＝拒绝）
        req = {"tool": tool_name, "args": dict(args or {}),
               "event": threading.Event(), "approved": False}
        self._human_approval_requested.emit(req)
        if not req["event"].wait(self._approval_timeout_s):
            self._audit_approval(tool_name, args, False, note="timeout")
            return False
        return bool(req["approved"])

    @Slot(object)
    def _on_human_approval_requested(self, req) -> None:
        tool = str((req or {}).get("tool", ""))
        args = dict((req or {}).get("args") or {})
        try:
            approved = bool(self._confirm_write_action(tool, args))
        except Exception:
            approved = False
        req["approved"] = approved
        self._audit_approval(tool, args, approved)
        req["event"].set()

    def _mcp_call_handler(self, tool_name: str, args: dict) -> dict:
        """AI 工具环内执行硬件调用（register_mcp_tools 注册的 MCP handler）。

        在 AI 后台线程被调用：经 worker.call_tool_sync 走通用 client.call
        直接落到硬件（不再"先执行软件工具再事后转发"）；完成后 emit
        _ai_tool_done 排队回 UI 线程同步频谱/音量控件。返回 dict 由
        ToolRegistry 包装成 ToolResult，success/content 如实反映硬件结果。

        红线：模型驱动的硬件写操作必走真人确认门（S13）；只读工具不受限。
        模型传 arm_manual=true 不构成确认，入口处直接剥离。
        UI 直操（_on_tune_* 等）是用户自己的手，不走此门。
        """
        args = dict(args or {})
        args.pop(GATE_PARAM, None)   # 模型自报"已确认"一律作废
        if tool_name == "tune_fm" and args.get("freq_mhz") in (None, ""):
            # S12：缺 freq_mhz 直接拒绝，绝不让演示频率（98.5MHz）进入界面/硬件
            return {
                "success": False,
                "error": "missing_argument",
                "content": "缺少 freq_mhz：拒绝执行 tune_fm（不接受演示频率）。",
                "next_step": "请让模型给出真实目标频率后重试。",
            }
        if is_write_tool(tool_name) and not self._request_human_approval(tool_name, args):
            return {
                "success": False,
                "error": "awaiting_human_approval",
                "content": f"等待人工确认：未执行 {tool_name}（模型不能自行放行写操作）。",
                "next_step": "请在桌面端确认该操作后再重试；拒绝或超时都不会下发硬件。",
            }
        worker = self._worker
        if worker is None:
            return {"success": False, "error": "not_connected",
                    "content": "硬件未连接：MCP 硬件工具需先连接 ai-sdr Mini"}
        raw = worker.call_tool_sync(tool_name, args)
        ok = "error" not in raw and not raw.get("skipped")
        if ok:
            # 只有硬件真执行成功才同步 UI：失败时先跳频的假状态必须避免（S11）
            self._ai_tool_done.emit(tool_name, dict(args or {}))
        result = {"success": ok, "content": json.dumps(raw, ensure_ascii=False)}
        if not ok:
            result["error"] = (str(raw.get("error", "") or "")
                               or str(raw.get("reason", "") or "hardware_error"))
        return result

    def _sync_ui_for_tool(self, tool_name: str, params: dict):
        """硬件调用完成后的 UI 同步（频谱中心频 / 音量控件跟随真实调用）。"""
        if tool_name == "tune_fm":
            # 没有真实 freq_mhz 就不同步：绝不拿 demo 频率假装"已调谐"（S11）
            if "freq_mhz" in params:
                freq = params["freq_mhz"]
                self.spectrum.set_center_freq(freq)
                self.control_panel.set_freq_fm(freq)
        elif tool_name == "set_volume":
            # 硬件面 set_volume 是 0–63 寄存器量纲，UI 是 0–100 人类量纲。
            try:
                v63 = int(params.get("volume", 30))
            except (TypeError, ValueError):
                v63 = 30
            self.control_panel.set_volume(volume_hw_to_pct(v63))

    @Slot(str, dict)
    def _on_ai_tool_call(self, tool_name: str, params: dict):
        """AI 面板的工具请求（只含"工具环未执行"的 MCP 硬件名）。

        只转发 MCP 注册名（硬件面）；软件工具不过二次转发、不产生 FAIL 噪声。
        AI 来源的写请求同样过真人确认门（S13）；UI 直操不走此链。
        先确认、后转发；UI 只在硬件真实执行成功后同步（S11），缺参数直接拒绝（S12）。
        """
        params = dict(params or {})
        params.pop(GATE_PARAM, None)   # 模型自报"已确认"一律作废
        if not (self._worker and self._worker.is_hardware_tool(tool_name)):
            return
        if tool_name == "tune_fm" and params.get("freq_mhz") in (None, ""):
            self.ai_panel._add_system_message(
                "缺少 freq_mhz：未转发 tune_fm（不接受演示频率）")
            return
        if is_write_tool(tool_name) and not self._request_human_approval(tool_name, params):
            self.ai_panel._add_system_message(
                f"等待人工确认：未转发 {tool_name}（模型不能自行放行写操作）")
            return
        # S11：执行前不改 UI，只记参数；等 tool_result 回来说明成功后再同步
        self._pending_ui_sync[tool_name] = params
        self._worker.request_tool.emit(tool_name, params)

    @Slot(str, dict)
    def _on_worker_tool_result(self, tool_name: str, result: dict):
        """硬件调用返回后同步 UI：仅成功才改，失败不留“已调谐/已改音量”的假状态。"""
        params = self._pending_ui_sync.pop(tool_name, None)
        if params is None:
            return
        if isinstance(result, dict) and ("error" in result or result.get("skipped")):
            return
        self._sync_ui_for_tool(tool_name, params)

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
        self._schedule_tx_readout()

    # ========================================================================
    # 其他操作
    # ========================================================================

    def _update_record_time(self):
        self._record_seconds += 1
        self.control_panel.update_record_time(self._record_seconds)
        self._sync_planetarium_recording()

    def _sync_planetarium_recording(self) -> None:
        """星象仪录音指示：只读 ``_recording`` / ``_record_seconds``，不自起计时。"""
        rec = getattr(self, "_planetarium_rec_indicator", None)
        if rec is None:
            return
        if not getattr(self, "_recording", False):
            rec.setVisible(False)
            self._position_planetarium_overlay()
            return
        secs = int(getattr(self, "_record_seconds", 0) or 0)
        rec.setText(f"● 录音中 {secs // 60:02d}:{secs % 60:02d}")
        rec.setVisible(True)
        # 录音指示显隐会改变浮层高度：必须重新 adjustSize，否则底部按钮被裁。
        self._position_planetarium_overlay()

    def _position_planetarium_overlay(self) -> None:
        """浮层贴天空视图右上角 12px；与 AI 命令栏相交时下移避让。

        必须用当前几何现算：窗口变宽后自然回到上 12，不存在“下移一次就永久
        下移”的状态位。
        """
        strip = getattr(self, "_planetarium_strip", None)
        sky = getattr(self, "sky_view", None)
        if strip is None or sky is None:
            return
        strip.adjustSize()
        bar = getattr(self, "_ai_cmd_bar", None)
        bar_rect = QRect()
        if bar is not None and bar.isVisible():
            # 命令栏与天空同属一个槽但不同父链，用全局坐标换算到天空坐标系。
            top_left = sky.mapFromGlobal(bar.mapToGlobal(QPoint(0, 0)))
            bar_rect = QRect(top_left, bar.size())
        x, y = _planetarium_overlay_pos(
            sky.width(), sky.height(), strip.width(), strip.height(), bar_rect)
        strip.move(x, y)
        strip.raise_()

    def _set_planetarium_strip_hover(self, on: bool) -> None:
        """鼠标移入变实、移开变淡（浮层不挡视线）。"""
        strip = getattr(self, "_planetarium_strip", None)
        if strip is None:
            return
        alpha = "0.92" if on else "0.55"
        strip.setStyleSheet(
            "QFrame#planetariumStrip {"
            f" background-color: rgba(18,19,22,{alpha});"
            f" border: 1px solid {self._tok('gray_400', '#585c63')};"
            " border-radius: 10px; }")

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
                        "无音频输出设备（声卡已禁用，不影响频谱/录制）", MSG_NORMAL)
            except Exception:
                pass
        # 注意：桌面端不在此调用 backend.start_audio()。后端自带的 ReceiveChain +
        # 独立声卡是 headless/CLI/MCP 的出声路径；桌面端由下面的 ReceivePipeline
        # 独占解调并写入上面这一个 AudioPlayer。两套同时启动会导致三路解调、
        # 两个声卡同时播放（混音成噪声）且 CPU 被烧满。
        # (A) 启动多线程流水线：IQ reader → splitter → FFT/demod/record 各 worker
        self._build_pipeline()

    def _backend_audio_hook(self, method: str) -> None:
        """路C 后端音频钩子：backend 暴露 start_audio()/stop_audio() 时调用。

        路C 会在 backend 上提供 ``start_audio()`` / ``stop_audio()``（声卡/DSP
        音频通路）。这里用 hasattr 守卫：方法不存在就静默跳过，绝不崩；
        存在则真实调用一次。本端本地 AudioPlayer 仍按既有逻辑独立启停。
        """
        be = getattr(self, "_active_sdr_backend", None)
        if be is None:
            return
        fn = getattr(be, method, None)
        if not callable(fn):
            return
        try:
            fn()
        except Exception:
            pass

    def _build_pipeline(self):
        """组装并启动接收流水线（见 receive_pipeline.ReceivePipeline）。

        - 无后端 / 无 read_samples / 流水线模块不可用：直接返回，不启动任何线程，
          频谱保持"未连接"，状态栏显 "--"。
        - 创建主 VFO + 第二 VFO 的 dsp.VFO，经 VfoManager.bind_dsp 绑定，
          offset 由 _update_main_vfo_offset 真搬频。
        """
        read, sr = self._active_read_samples("pipeline")
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
            # 已存在流水线不能直接 return：旧 reader 仍指向旧后端/旧采样率，
            # 新连接会"界面已切换、数据还是旧的"。先干净关停再按当前后端重建。
            try:
                self._pipeline.shutdown()
            except Exception:
                pass
            self._pipeline = None
        if ReceivePipeline is None or self._demod_cfg is None:
            return
        be = self._active_sdr_backend
        try:
            self._backend_center_hz = float(be.get_frequency())
        except Exception:
            self._backend_center_hz = 0.0
        if self._vfo_center_hz <= 0.0:
            self._vfo_center_hz = self._backend_center_hz

        # 复用已有主 VFO（含用户自建/选为主听的那个），没有才新建：
        # 重建流水线不得删除用户 VFO 目录（S3）。
        s1 = None
        if self._vfo_mgr is not None and self._main_vfo_id is not None:
            s1 = self._vfo_mgr.get(self._main_vfo_id)
        if s1 is not None:
            if self._vfo_center_hz > 0.0:
                s1.center_hz = float(self._vfo_center_hz)
            else:
                self._vfo_center_hz = float(s1.center_hz)
            self._vfo_bw = float(s1.bw_hz)
        # 建两个 dsp.VFO（DDC：NCO 搬频 → 有理重采样 → Nuttall LPF）
        from mbdsdr_ai.dsp import VFO
        try:
            main_vfo = VFO(in_samplerate=sr, out_samplerate=self._vfo_out_sr,
                           bandwidth=self._vfo_bw, offset=0.0)
        except Exception:
            main_vfo = None
        # 第二 VFO 尚未在 UI 开放：默认不创建，避免重复 DDC 空耗 CPU。
        vfo2 = None
        self._vfo = main_vfo          # 兼容旧引用（_on_mode_changed 等仍可触达）
        self._vfo_in_sr = float(sr)

        # VfoManager 登记 + bind_dsp（对照 SDR++ vfo_manager.cpp:16,30-49）
        # vfo_id → DemodWorker 映射：主听切换时按 id 切换哪一路写声卡。
        self._vfo_workers = {}
        if self._vfo_mgr is not None:
            if s1 is None:
                s1 = self._vfo_mgr.add(self._vfo_center_hz, self._vfo_bw,
                                       self._demod_cfg.mode)
            self._vfo_mgr.bind_dsp(s1.vfo_id, main_vfo,
                                   output_stream=self._audio_player)
            self._main_vfo_id = s1.vfo_id
            # 第二 VFO 未开放：只登记/绑定主 VFO。
            self._vfo2_id = None
            # 主 VFO 恢复为默认主听（重建后焦点指针不能悬空）
            self._vfo_mgr.set_active_context(s1, temporary=False)

        # 同步静噪到 worker 共享配置；解调模式以用户设置为准——
        # 后端 status.demod_mode 只读，不覆盖用户选择（S3）。
        try:
            self._demod_cfg.squelch_db = self._squelch_db
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
        # 读线程连续空读 ≈1s：查询后端真实 status，设备真掉线就如实断开（S8/S9）
        pipe.read_failed.connect(self._on_pipeline_read_failed)
        pipe.start()
        self._pipeline = pipe
        # 首帧到来前给设备一段宽限，之后再按数据新鲜度判掉线（S8）
        self._health_grace_until = time.monotonic() + HEALTH_GRACE_S
        self._health_has_data = False
        self._health_timer.start()
        # 主听路走统一音频后处理（ANR 降噪 + tap 分发），此前绕过 _tap_demod_audio
        try:
            pipe._demod1.audio_tap = self._tap_demod_audio
        except Exception:
            pass
        # 登记 vfo_id → demod worker（主听切换时切换哪一路写声卡）
        self._vfo_workers = {
            self._main_vfo_id: pipe._demod1,
        }
        # 首帧：把 offset（= vfo_center - backend_center）推给 DDC
        self._update_main_vfo_offset()

    def _safe_center_hz(self) -> float:
        """后台 FFT worker 回读硬件中心频率（失败回落到缓存值，绝不崩）。"""
        try:
            return float(self._active_sdr_backend.get_frequency())
        except Exception:
            return self._backend_center_hz

    @Slot(str)
    def _on_pipeline_read_failed(self, reason: str) -> None:
        """读线程持续空读：核实后端 status，真掉线就如实断开并提示（S8/S9）。

        不信任"界面还显示已连接"：status 报错/未连接，或连续三个阈值周期
        （≈3s）都拿不到数据，就按掉线收尾；短暂空读只做状态栏告警。
        """
        backend = self._active_sdr_backend
        if backend is None or self._pipeline is None:
            return
        error, connected, age, link, code, _buf = self._backend_status_facts(backend)
        self._read_fail_signals += 1
        if link == "stale":
            # 后端明确说「连接仍在、只是没数据」：这是等待态，不是掉线
            self._show_link_degraded(age, error or reason, code)
            return
        if not error and connected and self._read_fail_signals < 3:
            self.statusBar().showMessage(f"数据流中断，正在重试：{reason}", MSG_IMPORTANT)
            return
        self._handle_backend_lost(error or reason or "设备已断开", code)

    def _backend_status_facts(self, backend) -> tuple[
            str, bool, "float | None", str, str, str]:
        """读后端 status 的六个事实：错误说明、是否连接、数据年龄、链路状态、错误码、缓冲水位。

        读不到 status（异常/无该方法）时按"仍连接、无年龄/无链路状态"处理，
        绝不据此断连。
        """
        error = ""
        connected = True
        age = None
        link = ""
        code = ""
        buffer_level = ""
        try:
            st = backend.get_status()
            error = str(getattr(st, "error", "") or "")
            connected = bool(getattr(st, "connected", True))
            age = getattr(st, "last_data_age_s", None)
            age = None if age is None else float(age)
            link = str(getattr(st, "link_state", "") or "")
            code = str(getattr(st, "error_code", "") or "")
            buffer_level = str(getattr(st, "buffer_level", "") or "")
        except Exception:
            pass
        return error, connected, age, link, code, buffer_level

    def _show_link_degraded(self, age, error: str, code: str) -> None:
        """连接仍在但数据流中断：界面显式降级，不拆连接、也不冒充已连接。

        标签每秒更新数据年龄（用户能看见「等了多久」）；状态栏只在第一次
        进入降级或错误码变化时写，避免 1Hz 重置 8s 显示计时。
        """
        try:
            if not self._link_degraded:
                self._link_normal_text = self.status_conn.text()
            self._link_degraded = True
            age_txt = f"{float(age):.1f}s" if age is not None else "超过阈值"
            self.status_conn.setText(f"数据流中断 {age_txt}（连接仍在）")
            self.conn_label.setText("  状态: 数据流中断（连接仍在）  ")
            if (not self._link_degraded_announced) or (
                    code and code != self._link_degraded_code):
                self._link_degraded_announced = True
                self._link_degraded_code = code
                msg = f"数据流中断：{age_txt} 未收到 IQ 数据，连接仍在，等待恢复"
                if code:
                    msg += f"（错误码 {code}）"
                self.statusBar().showMessage(msg, MSG_IMPORTANT)
        except Exception:
            pass

    def _restore_link_display(self) -> None:
        """数据恢复：把降级文案还原成连接态；只恢复一次，不每拍刷屏。"""
        if not self._link_degraded:
            return
        self._link_degraded = False
        self._link_degraded_announced = False
        self._link_degraded_code = ""
        name = self._link_normal_text or "未连接"
        try:
            self.status_conn.setText(name)
            self.conn_label.setText(f"  状态: {name}  ")
            self.statusBar().showMessage("数据流已恢复", MSG_NORMAL)
        except Exception:
            pass

    def _handle_backend_lost(self, msg: str, code: str = "") -> None:
        """统一掉线收尾：拆连接（禁用依赖控件 + 清频谱 + 停流水线）+ 状态栏说明。"""
        if self._backend_lost_handled:
            return
        self._backend_lost_handled = True
        logger.warning("后端掉线，按未连接收尾: %s", msg)
        try:
            self._teardown_connection()
        finally:
            self._backend_lost_handled = False
        final = f"{msg}（错误码 {code}）" if code else msg
        # 桌面端没有自动重连（ReconnectionManager 未接线），必须明说，
        # 否则用户会以为界面正在后台重试。
        self.statusBar().showMessage(
            f"设备已断开：{final}（不会自动重连，请点工具栏「连接」重试）",
            MSG_IMPORTANT)

    def _on_health_tick(self) -> None:
        """S8：每秒按后端真实 health 判链路状态（读线程卡死时的兜底）。

        状态阶梯：connected → stale（5s 无数据，连接仍在，界面降级可见）
        → disconnected/failed（10s 硬判，拆连接并上浮错误码）。UI 侧在 2s
        就先降级可见（AC-06.7），但只到 10s 仍无 link_state 才硬兜底断开。
        年龄为 None（后端不报新鲜度）时不动手，交给 read_failed 那条路径。
        """
        backend = self._active_sdr_backend
        if backend is None or self._pipeline is None:
            return
        if not self._health_has_data:
            # 本段连接还没出过一帧：空窗由 read_failed + 宽限期负责
            return
        if time.monotonic() < self._health_grace_until:
            return
        error, connected, age, link, code, buffer_level = \
            self._backend_status_facts(backend)
        if link in ("failed", "disconnected") or not connected:
            self._handle_backend_lost(error or "后端报告未连接", code)
            return
        if link == "stale":
            self._show_link_degraded(age, error, code)
            return
        if not link:
            # 旧后端不报 link_state：保留 error 优先的旧判据，但把 2s..10s
            # 这段从「已断开」改成可见的数据流中断（仍不拆连接）。
            if error:
                self._handle_backend_lost(error)
                return
            if age is not None and age > DATA_LOST_S:
                self._handle_backend_lost(
                    f"超过 {age:.1f}s 未收到 IQ 数据（数据流中断）")
                return
        if age is not None and age > DATA_STALE_S:
            # 后端还标 connected 但真实数据已断 2s：先降级可见（AC-06.7），
            # 等后端 5s stale / 10s disconnected 再升级，不提前拆连接。
            self._show_link_degraded(age, error, code)
            return
        self._restore_link_display()
        if buffer_level == "high":
            # 缓冲积压不是链路故障（数据还在流），但会覆盖旧样本导致丢样：
            # 独立提示 + buffer_overflow 码，限流 30s，绝不拆连接。
            self._warn_buffer_overflow()

    def _warn_buffer_overflow(self) -> None:
        """环形缓冲积压到 high：限流提示，避免每秒刷屏。"""
        now = time.monotonic()
        if now - getattr(self, "_buffer_warn_last", -math.inf) < 30.0:
            return
        self._buffer_warn_last = now
        self.statusBar().showMessage(
            "环形缓冲积压，旧样本被覆盖（错误码 buffer_overflow）："
            "降低采样率或减少订阅者，让读侧跟上", MSG_IMPORTANT)

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
            f"新建 VFO {v.vfo_id} @ {center/1e6:.3f} MHz", MSG_NORMAL)
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
            f"新建 VFO {vfo_id} @ {center_hz/1e6:.3f} MHz", MSG_NORMAL)

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
            # 主听即"重建流水线时要复用的那个 VFO"，id 必须跟随，否则重建后
            # 声卡又回到旧的 VFO（用户新建/切换的主听丢失）。
            self._main_vfo_id = vfo_id
            try:
                self.control_panel.set_freq_fm(v.center_hz / 1e6)
                self._vfo_center_hz = float(v.center_hz)
                self._update_main_vfo_offset()
                self._schedule_tx_readout()
            except Exception:
                pass
        tag = "主听" if vfo_id in workers else "次听-未解调"
        self.statusBar().showMessage(
            f"主听切换 → {vfo_id}（{tag}）", MSG_NORMAL)

    def _on_vfo_removed(self, vfo_id: str):
        """频谱右键删除 VFO：清掉 worker 映射并切主听到剩余 VFO。"""
        workers = getattr(self, "_vfo_workers", {}) or {}
        workers.pop(vfo_id, None)
        # 被删的若是主听，_apply_primary_vfo 会切到 mgr.current（频谱已处理）
        if self._vfo_mgr is not None:
            cur = self._vfo_mgr.active_vfo_id
            if cur is not None:
                self._apply_primary_vfo(cur)
        self.statusBar().showMessage(f"已删除 VFO {vfo_id}", MSG_NORMAL)

    @Slot(object, float)
    def _on_pipeline_frame_ready(self, iq: np.ndarray, sr: float):
        """FFT worker 算完一帧后跨线程回到 UI：只做轻量 repaint + tap。

        重活（窗×FFT×fftshift×dBFS×IIR）已在 FftWorker 线程完成，这里绝不做 FFT。
        """
        # 收到真实数据 = 链路恢复，清零掉线计数
        self._read_fail_signals = 0
        self._health_has_data = True
        # 频谱状态文字（重绘由 30fps 定时器统一驱动，避免逐帧重画）
        try:
            self.spectrum._refresh_status_labels()
        except Exception:
            pass
        self._ensure_repaint_timer()

        # 星座图面板 tap：喂入复基带 IQ（节流 ~10fps，取尾部 512 点）
        try:
            cp = getattr(self, "constellation_panel", None)
            if cp is not None and hasattr(cp, "feed_iq"):
                cp.feed_iq(iq)
        except Exception:
            pass

        # 新时空面板 tap：可见时喂 IQ 做 AMR 实时识别（节流 ~5Hz；不可见零开销）
        try:
            self._nst_tick = getattr(self, "_nst_tick", 0) + 1
            if self._nst_tick >= 4:
                self._nst_tick = 0
                self._tap_new_spacetime_iq(iq, sr)
        except Exception:
            pass

        # VFO 面板列表刷新（节流：约每 10 帧）
        try:
            self._vfo_refresh_tick = getattr(self, "_vfo_refresh_tick", 0) + 1
            if self._vfo_refresh_tick >= 10:
                self._vfo_refresh_tick = 0
                self._refresh_vfo_panel()
        except Exception:
            pass

        # RSSI（节流 ~10Hz；S-meter 无需逐帧刷新）
        now = time.perf_counter()
        if now - getattr(self, '_rssi_last_t', 0.0) >= 0.1:
            self._rssi_last_t = now
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

    def _ensure_repaint_timer(self):
        """30fps 统一重绘定时器（随流水线数据启动），替代每帧 update()。"""
        t = getattr(self, '_repaint_timer', None)
        if t is None:
            from PySide6.QtCore import QTimer
            t = QTimer(self)
            t.setInterval(33)
            t.timeout.connect(self._repaint_plot)
            self._repaint_timer = t
        if not t.isActive():
            t.start()

    def _repaint_plot(self):
        try:
            self.spectrum._plot.update()
        except Exception:
            pass

    def _record_write_chunk(self, iq: np.ndarray) -> None:
        """录制写一块：直写磁盘；写失败立即停录并报错（S7）。

        绝不退回内存缓冲：攒在 RAM 里的样本既不保证写得出去，又会让停止时
        的 samples/字节数与磁盘实际内容对不上。失败时已写部分原样保留。
        """
        if not self._recording:
            return
        fh = getattr(self, "_record_stream_fh", None)
        if fh is None:
            self._report_record_abort("录音流句柄丢失（本段未成功打开）")
            return
        try:
            # 用 write(bytes) 而不是 ndarray.tofile：后者只认真实 fd/文件名，
            # 写失败（磁盘满/句柄失效）要能被这一层捕获并收口。
            fh.write(np.asarray(iq, dtype=np.complex64).tobytes())
        except (OSError, ValueError) as exc:
            self._report_record_abort(f"写入录音文件失败: {exc}")
            return
        self._record_samples_total = \
            getattr(self, "_record_samples_total", 0) + int(len(iq))

    def _on_pipeline_record_iq(self, iq: np.ndarray):
        """录制 tap：RecordTap 线程把每帧 IQ（已是独立副本）交给 UI 累积。"""
        if self._recording and self._record_iq_buffer is not None:
            try:
                self._record_write_chunk(iq)
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
        # 停 30fps 重绘定时器
        try:
            t = getattr(self, '_repaint_timer', None)
            if t is not None:
                t.stop()
        except Exception:
            pass
        # 停流水线（reader + splitter + FFT/demod/record workers），join 回收
        # 同时停掉数据流健康轮询：没有流水线就没有"数据新鲜度"可判（S8）
        self._health_timer.stop()
        self._health_grace_until = 0.0
        self._health_has_data = False
        if self._pipeline is not None:
            try:
                self._pipeline.shutdown()
            except Exception:
                pass
            self._pipeline = None
        # 清 vfo_id → worker 映射
        self._vfo_workers = {}
        # 只解绑运行时 DSP，保留用户 VFO 目录（重建流水线时复用，不删用户自建项）
        if self._vfo_mgr is not None:
            for v in self._vfo_mgr.list_all():
                v.dsp_vfo = None
                v.output_stream = None
        self._vfo2_id = None
        self._vfo = None
        self._vfo_in_sr = 0.0
        # 关声卡
        if self._audio_player is not None:
            try:
                self._audio_player.stop()
            except Exception:
                pass
        # 路C 后端若自带 stop_audio()，一并调用（hasattr 守卫，缺失不崩）
        self._backend_audio_hook("stop_audio")

    def _active_read_samples(self, sub_name: str = "spectrum"):
        """防御性拿到 (read_fn, sample_rate)。无后端/无方法返 (None, None)。

        sub_name 指定 fan-out 命名订阅者（频谱/流水线各自独立游标，互不抢样）；
        后端不支持 fan-out 时回退到 read_samples。"""
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
        read = None
        if sub_name and hasattr(backend, "subscribe_iq"):
            try:
                sub = backend.subscribe_iq(sub_name)
                if sub is not None:
                    read = getattr(sub, "read", None)
            except Exception:
                read = None
        if not callable(read):
            read = getattr(backend, "read_samples", None)
        if not callable(read):
            return None, None
        try:
            sr = float(getattr(backend, "get_sample_rate",
                               lambda: float(DEFAULT_SAMPLE_RATE_HZ))())
        except Exception:
            sr = float(DEFAULT_SAMPLE_RATE_HZ)
        return read, sr

    def _tap_new_spacetime_iq(self, iq: np.ndarray, sr: float) -> None:
        """新时空面板 tap：面板可见时喂一段复基带 IQ 做 AMR 识别。

        不可见（非当前 Tab）时零开销。中心频率优先取后端 get_frequency()，
        回退 self._backend_center_hz。全程 try/except，绝不崩。无信号时面板
        内部 analyzer 自会显示“未检测到信号”，绝不伪造数值。
        """
        p = getattr(self, "new_spacetime_panel", None)
        if p is None or not hasattr(p, "update_iq"):
            return
        try:
            if not p.isVisible():
                return
        except Exception:
            return
        try:
            cf = float(self._backend_center_hz)
        except Exception:
            cf = 0.0
        be = getattr(self, "_active_sdr_backend", None)
        if be is not None and hasattr(be, "get_frequency"):
            try:
                cf = float(be.get_frequency())
            except Exception:
                pass
        try:
            p.update_iq(iq, float(sr), cf)
        except Exception:
            pass

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

        # ADS-B 航路图面板：tap 同一块 IQ（1090MHz 时面板内部做前导码/PPM/CRC）。
        # 面板不可见时跳过解码，零开销；无数据时面板自显“等待 1090MHz 数据”。
        try:
            ap = getattr(self, "digital_adsb_panel", None)
            if (ap is not None and hasattr(ap, "feed_iq")
                    and getattr(self, "adsb_dock", None) is not None
                    and self.adsb_dock.isVisible()):
                ap.feed_iq(iq, sr)
        except Exception:
            pass

        # 新时空面板 tap：可见时喂 IQ 做 AMR 实时识别（节流 ~5Hz；不可见零开销）
        try:
            self._nst_poll_tick = getattr(self, "_nst_poll_tick", 0) + 1
            if self._nst_poll_tick >= 4:
                self._nst_poll_tick = 0
                self._tap_new_spacetime_iq(iq, sr)
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

        # (B) 录制中：流式直写磁盘（不再攒数组）
        if self._recording and self._record_iq_buffer is not None:
            try:
                self._record_write_chunk(iq.copy())
            except Exception:
                pass

        # (C) 音频：后端若已用 ReceiveChain 出声，不在 GUI 重复播放（避免双音频）；
        # 仅对不自带音频的后端走 GUI 鉴频声卡通路。
        _be = self._active_sdr_backend
        _be_audio = (_be is not None
                     and getattr(_be, "_audio_thread", None) is not None
                     and _be._audio_thread.is_alive())
        if not _be_audio:
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

        from mbdsdr_ai import dsp as _dsp

        def _emit(audio_in) -> None:
            """统一出口：解调后先过后处理，再按「相对噪声基线的余量」门控写声卡。

            滑杆值是余量刻度（-50 ≈ 6 dB），旧代码拿 IQ 绝对 dBFS 与它比较，
            会把 -40 dBFS 的噪声判成信号。静噪状态只由音频存在度决定。
            """
            if audio_in is None or len(audio_in) == 0:
                return
            processed = self._tap_demod_audio(audio_in)
            opened = self._replay_squelch_gate(processed, mode)
            player.set_muted(not opened)
            if opened:
                player.write(processed)

        # 2) WFM 广播特殊处理：跳过 VFO，直接对原始全带宽 IQ 鉴频（内部已重采样到 48k）
        if mode == "WFM":
            try:
                audio = _dsp.wfm_broadcast_demod(iq, sample_rate=sr, audio_sr=48000)
                _emit(audio)
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
                        _emit(audio)
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
            _emit(audio)
        except Exception:
            pass

    def _replay_squelch_gate(self, audio: np.ndarray, mode: str) -> bool:
        """回放静噪判定：与实时同口径（相对该模式噪声基线的余量）。

        回放不经过 ReceivePipeline 的 DemodWorker，这里自建一个
        ``BaselineSquelch``；滑杆值由 ``margin_threshold_db()`` 归一化，
        绝不再拿 IQ 绝对 dBFS 与滑杆刻度直接比较。
        """
        if audio is None or len(audio) == 0:
            return False
        sq = getattr(self, "_replay_squelch", None)
        if sq is None:
            from mbdsdr_ai.squelch import BaselineSquelch
            sq = BaselineSquelch(squelch_db=self._squelch_db, mode=mode,
                                 sample_rate=48000.0)
            self._replay_squelch = sq
        else:
            sq.set_mode(mode)
            sq.set_threshold(self._squelch_db)
        return bool(sq.update(audio))

    def _tap_demod_audio(self, audio: np.ndarray) -> np.ndarray:
        """解调后音频的统一后处理 / 分发：ANR 降噪 → tap 给 APRS / 卫星云图面板。

        所有分支 try/except，绝不因 tap 失败影响声卡输出：
          * 先把本块音频存为 ``self._last_demod_audio``，供「采样噪声底」按钮学习；
          * ANR 开启时用 ``anr_panel.anr`` 谱减降噪（失败旁路原音频），节流回贴 SNR 改善；
          * APRS 包 dock 可见时喂 48k 音频做 AFSK 解码（不可见零开销）；
          * 卫星云图 dock 可见时喂 48k 音频做 APT 累积（面板内部点「开始解码」后才累积）。
        返回（可能被 ANR 处理过的）音频，供 ``player.write``。
        """
        if audio is None or len(audio) == 0:
            return audio
        # 存最近一块音频，供「采样噪声底」按钮在无信号段学习
        try:
            self._last_demod_audio = np.asarray(audio, dtype=np.float32)
        except Exception:
            self._last_demod_audio = audio

        out = audio
        # 1) ANR 谱减降噪（开启才处理；失败旁路原音频）
        try:
            if getattr(self, "_anr_enabled", False):
                anr_p = getattr(self, "anr_panel", None)
                anr = getattr(anr_p, "anr", None) if anr_p is not None else None
                if anr is not None and getattr(anr, "enabled", False):
                    processed = anr.process(audio)
                    if processed is not None and len(processed) == len(audio):
                        # 节流回贴 SNR 改善（~2Hz，按 50ms/帧 ≈ 40 帧一次）
                        self._anr_snr_tick = getattr(self, "_anr_snr_tick", 0) + 1
                        if self._anr_snr_tick >= 40:
                            self._anr_snr_tick = 0
                            try:
                                db = anr.snr_improvement_db(audio, processed)
                                anr_p.set_snr_improvement(db)
                            except Exception:
                                pass
                        out = processed
        except Exception:
            out = audio

        # 2) APRS 包面板 tap（dock 可见才喂，避免不可见时白跑解码）
        try:
            ap = getattr(self, "digital_aprs_panel", None)
            if (ap is not None and hasattr(ap, "feed_audio")
                    and getattr(self, "aprs_dock", None) is not None
                    and self.aprs_dock.isVisible()):
                ap.feed_audio(out, 48000)
        except Exception:
            pass

        # 3) 卫星云图面板 tap（dock 可见才喂；面板内部 start 后才累积）
        try:
            sp = getattr(self, "digital_sat_image_panel", None)
            if (sp is not None and hasattr(sp, "feed_audio")
                    and getattr(self, "sat_image_dock", None) is not None
                    and self.sat_image_dock.isVisible()):
                sp.feed_audio(out, 48000)
        except Exception:
            pass

        return out

    @staticmethod
    def _demod_at_48k(dsp, mode: str, vfo_out: np.ndarray) -> np.ndarray:
        """VFO 输出已是 48k complex IQ，按模式解调后直接可写声卡（不再重采样）。

        模式→函数映射已收口到 mode_registry：DemodMode.demod_func / demod_kwargs
        逐模式声明（NFM 频偏 5k / FM 75k / AM 包络 / USB/LSB Weaver / CW 700Hz）。
        WFM 在调用方已特殊处理并 return，不会走到这里。
        """
        desc = get_mode(mode)
        if desc is not None and desc.outputs_audio:
            result = demodulate(mode, vfo_out, sample_rate=48000)
            if result is not None:
                return result
        # 兜底（RAW/DIG/未知模式不应走到这里）
        return dsp.fm_demod(vfo_out, deviation=75000.0, sample_rate=48000)

    @staticmethod
    def _demod_at_native_sr(dsp, mode: str, iq: np.ndarray, sr: float) -> np.ndarray:
        """回退路径：对原始全带宽 IQ（采样率 sr）按模式解调，再按模式音频带宽重采样到 48k。

        音频截止 cutoff_hz 与模式→函数映射均来自 mode_registry（DemodMode）：
          WFM=15000, NFM=3000, FM=15000, AM=4000, USB=3000, LSB=3000, CW=1000
        """
        cutoff = audio_cutoff(mode)
        audio = demodulate(mode, iq, sample_rate=sr)
        if audio is None:
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

    def _open_record_segment(self, path: str, sr: float, center_hz: float,
                             start_time: str, index: int) -> bool:
        """打开一个录制文件段（每段速率恒定），初始化该段计数。

        打不开就**不录**（S7）：绝不退回内存缓冲——内存里的样本不是落盘
        数据，停止时再写出去会让用户以为整场都录在盘上。返回是否成功；
        失败时已经 ``_report_record_abort`` 报错并停止录音。
        """
        self._record_file_path = path
        self._record_segment_index = int(index)
        self._record_segment_start_time = start_time or ""
        self._record_iq_buffer = []
        self._record_sr = float(sr)
        self._record_center_hz = float(center_hz)
        self._record_samples_total = 0
        self._record_stream_fh = None
        try:
            self._record_stream_fh = open(path, "wb")
        except OSError as exc:
            self._report_record_abort(f"录音文件打不开（{path}）: {exc}")
            return False
        return True

    def _report_record_abort(self, reason: str) -> None:
        """写盘失败/文件打不开的统一收口：报错 + 停止录音 + 保留已写部分（S7）。

        sidecar 只按**已成功写入**的样本数收尾（其 samples 就是磁盘上真实的
        样本数）；内存缓冲里的任何东西都不参与落盘，也不冒充已录数据。
        """
        written = int(getattr(self, "_record_samples_total", 0)) * 8
        path = self._record_file_path or ""
        logger.error("录音中止：%s（已写 %d 字节 → %s）", reason, written, path)
        self._stop_recording(
            error=f"{reason}（已写 {written} 字节"
                  f"{('，文件 ' + os.path.basename(path)) if path else ''}）")

    def _record_segment_meta(self) -> dict:
        """当前段的 sidecar 元数据（速率/样本/时长严格自洽）。"""
        n = int(getattr(self, "_record_samples_total", 0))
        secs = (n / self._record_sr) if self._record_sr else 0.0
        return {
            "sample_rate": float(self._record_sr),
            "center_freq_hz": float(self._record_center_hz),
            "start_time": (self._record_segment_start_time
                           or self._record_start_time),
            "samples": n,
            "duration_s": round(secs, 3),
            "gain_db": self._record_gain_db,
            "device": self._record_device,
            "driver": self._record_driver,
            "format": "cf32",
            "segment_index": int(self._record_segment_index),
            "session_path": self._record_session_path or self._record_file_path,
            "note": "MBDSDR baseband recording (float32 interleaved I/Q, streamed)",
        }

    def _finalize_record_segment(self) -> Optional[dict]:
        """收尾当前段：关流 + 写 sidecar；零样本段删除不留空文件。

        返回段信息（写入 ``_record_segments``）；无数据可落盘时返回 None。
        """
        path = self._record_file_path
        n = int(getattr(self, "_record_samples_total", 0))
        fh = getattr(self, "_record_stream_fh", None)
        self._record_stream_fh = None
        info = None
        if fh is not None:
            try:
                fh.close()
            except Exception as e:
                logger.warning("关闭录制流失败: %s", e)
            if n <= 0:
                # 改率发生在任何样本之前：删掉空壳，段序号也不占位
                try:
                    os.remove(path)
                except OSError:
                    pass
            else:
                info = None
                try:
                    from mbdsdr_ai.baseband_io import write_sidecar
                    meta = self._record_segment_meta()
                    sidecar = write_sidecar(path, meta)
                    info = {"path": path, "samples": n,
                            "sample_rate": float(self._record_sr),
                            "duration_s": meta["duration_s"],
                            "start_time": meta["start_time"],
                            "segment_index": meta["segment_index"],
                            "size_bytes": n * 8, "sidecar": sidecar}
                except Exception as e:
                    logger.warning("录制 sidecar 写盘失败: %s", e)
                    self.statusBar().showMessage(
                        f"录制 sidecar 写盘失败: {e}", MSG_NORMAL)
                    info = {"path": path, "samples": n,
                            "sample_rate": float(self._record_sr),
                            "duration_s": round(n / (self._record_sr or 1), 3),
                            "start_time": self._record_segment_start_time,
                            "segment_index": int(self._record_segment_index),
                            "size_bytes": n * 8, "sidecar": None}
        else:
            # 流从未成功打开（S7）：没有落盘数据可记，不得拿内存缓冲冒充存盘
            info = None
        self._record_iq_buffer = []
        if info is not None:
            self._record_segments.append(info)
        return info

    def _start_recording(self):
        """开始录制：检查后端 → 建文件名 → 清 buffer → 状态栏提示。
        无 SDR 后端时弹提示且不造假文件（按钮会被调用方弹回）。"""
        # 回放与录音互斥：回放中按录音＝先停回放再录，语义确定且用户可见
        if self._replay_timer is not None:
            self._stop_replay()
            self.statusBar().showMessage("已停止回放，开始录音", MSG_IMPORTANT)
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

        try:
            sr = float(backend.get_sample_rate())
        except Exception:
            sr = float(DEFAULT_SAMPLE_RATE_HZ)
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

        # 录音目录唯一来源是设置（settings.record_dir）；目录不可用时绝不
        # 静默退 tempfile/默认目录——那会让用户以为录到了自己选的盘上。
        rec_dir, rec_error = self.settings.ensure_record_dir()
        if rec_error:
            rec_code = classify_record_dir_error(rec_error)
            self._show_error_dialog(
                QMessageBox.Warning, "录音目录不可用", rec_error, code=rec_code)
            self._show_error_status(rec_error, rec_code, 8000)
            self._recording = False
            self._record_guard = True
            try:
                self.record_btn.setChecked(False)
                self.control_panel.record_button.setChecked(False)
            finally:
                self._record_guard = False
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        # 防呆：同名 .iq/.json 已存在则换名，绝不覆盖上一段录音（L3）
        path = _unique_record_path(os.path.join(rec_dir, f"baseband_{ts}.iq"))

        self._record_session_path = path
        self._record_segments = []
        self._record_segment_index = 1
        self._record_rate_changes = []
        self._recording = True
        self._sync_planetarium_recording()
        # 流式落盘：边收边写，不再全程攒数组（长录制内存安全）。
        # S7：文件打不开＝直接不录（_report_record_abort 已复位 UI 并报错）。
        if not self._open_record_segment(
                path, sr, center_hz, self._record_start_time, 1):
            return

        # 1s 计时 UI（沿用原有 _record_timer）
        self._record_seconds = 0
        if self._record_timer is None:
            self._record_timer = QTimer(self)
            self._record_timer.timeout.connect(self._update_record_time)
        self._record_timer.start(1000)

        # UI：录音中按钮变红显示"停止中"（danger 色取自 tokens）
        self.record_btn.setText("停止中")
        self.record_btn.setStyleSheet(
            f"background-color:{self._tok('danger', '#B85C5C')};"
            f" color:{self._tok('text_primary', '#FFFFFF')}; font-weight:600;")
        try:
            self.control_panel.record_button.setText("停止录音")
            self.control_panel.record_status.setText("录音中... 00:00")
        except Exception:
            pass
        self.statusBar().showMessage(
            f"开始录制 baseband: {path}"
            "（录音目录设置只影响新录音，已有录音不迁移）", MSG_IMPORTANT)

    def _write_session_sidecars(self, segments: list, manifest: list,
                                total_samples: int, session_duration: float):
        """把整场录音的 segments 清单写回每一段的 sidecar。

        各段自己的 ``sample_rate``/``samples``/``duration_s`` 保持不变；
        追加 ``segments`` 与会话汇总，读取端据此按段还原，不再靠猜。
        """
        try:
            from mbdsdr_ai.baseband_io import write_sidecar
        except Exception as e:
            logger.warning("会话 sidecar 写入不可用: %s", e)
            return
        changes = getattr(self, "_record_rate_changes", None)
        for seg in segments:
            spath = seg["path"] + ".json"
            try:
                with open(spath, encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception as e:
                logger.warning("读取分段 sidecar 失败，按段信息重建: %s", e)
                meta = {
                    "sample_rate": float(seg["sample_rate"]),
                    "samples": int(seg["samples"]),
                    "duration_s": round(int(seg["samples"])
                                        / float(seg["sample_rate"]), 3),
                    "start_time": seg.get("start_time", ""),
                    "segment_index": int(seg.get("segment_index", 1)),
                }
            meta["segments"] = manifest
            meta["session_path"] = segments[0]["path"]
            meta["session_samples"] = int(total_samples)
            meta["session_duration_s"] = session_duration
            meta["session_start_time"] = segments[0].get("start_time", "")
            if changes:
                meta["sample_rate_changes"] = changes
            try:
                write_sidecar(seg["path"], meta)
            except Exception as e:
                logger.warning("会话 sidecar 写盘失败（%s）: %s",
                               os.path.basename(seg["path"]), e)
                self.statusBar().showMessage(
                    f"会话 sidecar 写盘失败（{os.path.basename(seg['path'])}）: {e}",
                    MSG_NORMAL)

    def _stop_recording(self, error: Optional[str] = None):
        """停止录制：收尾最后一段并写 sidecar；多段时汇总 segments 清单。

        ``error`` 非空表示因写盘失败而中止（S7）：已写部分照常收尾，最终
        提示以错误开头，绝不把中止说成「录制完成」。
        """
        self._recording = False
        self._sync_planetarium_recording()
        if self._record_timer is not None:
            try:
                self._record_timer.stop()
            except Exception:
                pass
            self._record_timer = None

        info = None
        self._finalize_record_segment()
        segments = list(getattr(self, "_record_segments", None) or [])
        if segments:
            total_samples = sum(int(s["samples"]) for s in segments)
            total_bytes = sum(int(s.get("size_bytes") or 0) for s in segments)
            session_duration = round(sum(
                int(s["samples"]) / float(s["sample_rate"]) for s in segments), 3)
            if len(segments) > 1:
                manifest = []
                offset = 0
                for i, seg in enumerate(segments):
                    manifest.append({
                        "path": seg["path"],
                        "segment_index": int(seg.get("segment_index", i + 1)),
                        "start_sample": offset,
                        "samples": int(seg["samples"]),
                        "sample_rate": float(seg["sample_rate"]),
                        "start_time": seg.get("start_time", ""),
                    })
                    offset += int(seg["samples"])
                self._write_session_sidecars(
                    segments, manifest, total_samples, session_duration)
                info = {"path": segments[0]["path"],
                        "samples": total_samples,
                        "size_bytes": total_bytes,
                        "segments": len(segments),
                        "session_duration_s": session_duration,
                        "sidecar": segments[0].get("sidecar")}
            else:
                info = dict(segments[0])
                info["segments"] = 1
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
            if int(info.get("segments") or 1) > 1:
                msg = (f"录制完成: {info['segments']} 段, 首段 {info['path']}  "
                       f"(共 {info['samples']} 样本, {kb:.1f} KB, {secs}s)")
            else:
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
        if error:
            msg = f"{error}；{msg}"
        self.statusBar().showMessage(msg, MSG_IMPORTANT)

    def _toggle_waterfall(self):
        self.spectrum.toggle_waterfall()
        # 瀑布可见性持久化（权威源 settings；此前只有 gui_config 的键）。
        # 启动恢复期不回写。
        if not getattr(self, "_restoring_settings", False):
            try:
                st = getattr(self.spectrum, "_state", None)
                if st is not None and hasattr(st, "show_waterfall"):
                    self.settings.set("show_waterfall", bool(st.show_waterfall))
            except Exception:
                pass

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # ------------------------------------------------------------------
    # 右侧 QDockWidget 停靠体系（控制 / 状态 / AI 助手）
    # ------------------------------------------------------------------
    def _wrap_panel_in_dock(self, title: str, panel: QWidget) -> QScrollArea:
        """兼容旧接口：把面板包进无框 QScrollArea（不再用 QDockWidget）。

        三栏重构后所有面板都进 right_tab 页签；此方法仅为兼容保留，
        新代码请直接用 _add_right_tab。
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(panel)
        return scroll

    def _toggle_right_docks(self):
        """☰ 按钮：一键显示 / 隐藏右栏 App 区。"""
        rt = getattr(self, "right_tab", None)
        if rt is None:
            return
        rt.setVisible(not rt.isVisible())

    # ------------------------------------------------------------------ #
    # 内核能力面板：扫频 / 自动调制识别 / 书签 / 远程服务设置
    # ------------------------------------------------------------------ #
    def _build_kernel_panels(self):
        """把内核能力面板接入右栏页签（缺面板类时 hasattr 守卫，不崩）。"""
        if ScannerPanel is not None:
            try:
                self.scanner_panel = ScannerPanel()
                self.scanner_panel.segment_chosen.connect(
                    self._on_scanner_segment_chosen)
                self.scanner_panel.scan_began.connect(self._on_panel_scan_began)
                self.scanner_panel.scan_ended.connect(self._on_panel_scan_ended)
                # —— 孤儿接通：扫频进度/结果/失败回显到状态栏 ——
                self.scanner_panel.progress.connect(self._on_scanner_progress)
                self.scanner_panel.done.connect(self._on_scanner_done)
                self.scanner_panel.failed.connect(self._on_scanner_failed)
                self.scanner_dock = self._add_right_tab(
                    "扫频", self.scanner_panel, "scanner_dock")
            except Exception:  # noqa: BLE001
                self.scanner_panel = None
        if ModulationPanel is not None:
            try:
                self.modulation_panel = ModulationPanel()
                self.modulation_panel.apply_demod_requested.connect(
                    self._on_apply_demod_requested)
                # —— 孤儿接通：识别完成/失败回显，完成后可选自动切模式 ——
                self.modulation_panel.done.connect(self._on_modulation_done)
                self.modulation_panel.failed.connect(self._on_modulation_failed)
                self.modulation_dock = self._add_right_tab(
                    "调制识别", self.modulation_panel, "modulation_dock")
            except Exception:  # noqa: BLE001
                self.modulation_panel = None

        if BookmarkPanel is not None:
            try:
                self.bookmark_panel = BookmarkPanel()
                self.bookmark_panel.tune_requested.connect(
                    self._on_bookmark_tune_requested)
                self.bookmark_panel.set_current_freq_provider(
                    self._safe_center_hz)
                self.bookmark_dock = self._add_right_tab(
                    "书签", self.bookmark_panel, "bookmark_dock")
            except Exception:  # noqa: BLE001
                self.bookmark_panel = None
        if ServiceSettingsPanel is not None:
            try:
                self.service_settings_panel = ServiceSettingsPanel(
                    settings=self.settings)
                self.service_settings_dock = self._add_right_tab(
                    "服务设置", self.service_settings_panel,
                    "service_settings_dock")
                # UI 就绪后再按上次持久化的显式意图恢复服务（默认全关不变；
                # 只恢复用户上次自己勾过的，避免"勾了重启又关"反人类行为）。
                self.service_settings_panel.restore_enabled_services()
            except Exception:  # noqa: BLE001
                self.service_settings_panel = None

        # 全量设置面板（按 config_schema 渲染；本段只是停靠接线）。
        if SettingsPanel is not None:
            self.settings_panel = SettingsPanel(
                settings=self.settings, host=self)
            self.settings_dock = self._add_right_tab(
                "设置", self.settings_panel, "settings_dock")

        # 天空视图「调到该星 / 跟踪 / 云台跟随」→ 先走 agent 能力协商，
        # 不再由按钮直连硬件（按钮只表达意图，执行由模型决定后经动作总线落地）
        try:
            sv = getattr(self, "sky_view", None)
            if sv is not None and hasattr(sv, "tune_satellite_requested"):
                sv.tune_satellite_requested.connect(
                    self._on_sky_tune_requested)
            if sv is not None and hasattr(sv, "track_toggled"):
                sv.track_toggled.connect(self._on_sky_track_requested)
            if sv is not None and hasattr(sv, "gimbal_follow_toggled"):
                sv.gimbal_follow_toggled.connect(
                    self._on_sky_gimbal_follow_requested)
                # 初始可用性（后端可能 200ms 后才连上，5s 周期任务会再刷）
                if hasattr(sv, "set_gimbal_available"):
                    sv.set_gimbal_available(self._gimbal_available())
            if self.ai_panel is not None and hasattr(
                    self.ai_panel, "sky_action_resolved"):
                self.ai_panel.sky_action_resolved.connect(
                    self._on_sky_action_resolved)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 新增面板：多 VFO / ANR / 星座图 / 设备选择 / 分段增益
    # ------------------------------------------------------------------ #
    def _build_extended_panels(self):
        """把 SDR++/GQRX 对标面板接入右栏页签；全部 hasattr/try 守卫，缺类不崩。"""
        # ---- 多 VFO 面板 ----
        if VfoPanel is not None:
            try:
                self.vfo_panel = VfoPanel(vfo_manager=self._vfo_mgr)
                self.vfo_panel.set_center_freq_provider(self._safe_center_hz)
                self.vfo_panel.vfo_selected.connect(self._on_vfo_selected)
                self.vfo_panel.vfo_added.connect(self._on_panel_vfo_added)
                self.vfo_panel.vfo_removed.connect(self._on_vfo_removed)
                self.vfo_panel.primary_changed.connect(self._on_vfo_selected)
                self.vfo_panel.cycle_primary.connect(self._on_cycle_vfo)
                self.vfo_dock = self._add_right_tab(
                    "VFO", self.vfo_panel, "vfo_dock")
            except Exception:  # noqa: BLE001
                self.vfo_panel = None

        # ---- ANR 降噪面板 ----
        if AnrPanel is not None:
            try:
                self.anr_panel = AnrPanel()
                self.anr_panel.enabled_changed.connect(self._on_anr_enabled)
                self.anr_panel.strength_changed.connect(self._on_anr_strength)
                self.anr_panel.learn_noise_requested.connect(
                    self._on_anr_learn_noise)
                # —— 孤儿接通：ANR 降噪模式切换（谱减/门限等）——
                self.anr_panel.mode_changed.connect(self._on_anr_mode_changed)
                self.anr_dock = self._add_right_tab(
                    "ANR", self.anr_panel, "anr_dock")
            except Exception:  # noqa: BLE001
                self.anr_panel = None

        # ---- 星座图面板 ----
        if ConstellationPanel is not None:
            try:
                self.constellation_panel = ConstellationPanel()
                # —— 孤儿接通：清空星座图时重置主窗口统计 ——
                self.constellation_panel.cleared.connect(
                    self._on_constellation_cleared)
                self.constellation_dock = self._add_right_tab(
                    "星座图", self.constellation_panel, "constellation_dock")
            except Exception:  # noqa: BLE001
                self.constellation_panel = None

        # ---- 设备选择 / 热插拔面板 ----
        if DevicePanel is not None:
            try:
                self.device_panel = DevicePanel()
                self.device_panel.connect_requested.connect(
                    self._on_panel_device_connect)
                self.device_panel.disconnect_requested.connect(self._disconnect)
                self.device_panel.audio_output_changed.connect(
                    self._on_audio_output_changed)
                self.device_panel.device_unplugged.connect(self._disconnect)
                # —— 孤儿接通：设备热插入 → 状态栏提示 + 刷新列表 ——
                self.device_panel.device_plugged.connect(
                    self._on_device_plugged)
                self.device_dock = self._add_right_tab(
                    "设备", self.device_panel, "device_dock")
            except Exception:  # noqa: BLE001
                self.device_panel = None

        # ---- 分段增益面板 ----
        if GainPanel is not None:
            try:
                self.gain_panel = GainPanel()
                self.gain_panel.stage_gain_changed.connect(
                    self._on_stage_gain_changed)
                self.gain_panel.agc_changed.connect(self._on_agc_changed)
                # —— 孤儿接通：增益滑杆实时值 → 同步后端/状态栏 ——
                self.gain_panel.value_changed.connect(self._on_gain_value_changed)
                self.gain_dock = self._add_right_tab(
                    "增益", self.gain_panel, "gain_dock")
            except Exception:  # noqa: BLE001
                self.gain_panel = None

    # ------------------------------------------------------------------ #
    # 数字信号解码面板：ADS-B 列表 / APRS 包 / 卫星云图
    # ------------------------------------------------------------------ #
    def _build_digital_decode_panels(self):
        """把数字解码面板接入右栏页签；全部 hasattr/try 守卫，缺类不崩。

        中栏全出血地图 = self.adsb_map_panel（AdsbMapPanel）；这里的 ADS-B 文本
        列表面板（AdsbPanel）作为右栏页签。面板数据由 _poll_sdr_iq 喂入；
        无后端时各面板自带“等待数据/未连接”空态。
        """
        # ---- ADS-B 文本列表（右栏页签）----
        if AdsbPanel is not None:
            try:
                self.digital_adsb_panel = AdsbPanel()
                self.adsb_dock = self._add_right_tab(
                    "ADS-B", self.digital_adsb_panel, "adsb_dock")
            except Exception:  # noqa: BLE001
                self.digital_adsb_panel = None

        # ---- APRS 数据包（右栏页签）----
        if AprsPanel is not None:
            try:
                self.digital_aprs_panel = AprsPanel()
                self.aprs_dock = self._add_right_tab(
                    "APRS", self.digital_aprs_panel, "aprs_dock")
            except Exception:  # noqa: BLE001
                self.digital_aprs_panel = None

        # ---- 卫星云图（右栏页签）----
        if SatelliteImagePanel is not None:
            try:
                self.digital_sat_image_panel = SatelliteImagePanel()
                self.sat_image_dock = self._add_right_tab(
                    "卫星云图", self.digital_sat_image_panel, "sat_image_dock")
            except Exception:  # noqa: BLE001
                self.digital_sat_image_panel = None

    # ------------------------------------------------------------------ #
    # 孤儿信号接通：handler（真吃数据；面板不可见时零开销）
    # ------------------------------------------------------------------ #
    def _panel_active(self, panel: QWidget) -> bool:
        """面板当前是否可见（在右栏/左栏的当前页签里）。offscreen 单测用
        monkeypatch 覆盖面板 isVisible，这里直接读 panel.isVisible()。"""
        try:
            return bool(panel.isVisible())
        except Exception:
            return False

    # —— 2. 自动调制识别 done/failed ——
    def _on_modulation_done(self, result):
        """识别完成：状态栏回显调制方式 + 置信度（不自动切模式，等用户点应用）。"""
        try:
            mod = getattr(result, "modulation", None) or (
                result.get("modulation") if isinstance(result, dict) else None)
            conf = float(getattr(result, "confidence", 0.0) or (
                result.get("confidence", 0.0) if isinstance(result, dict) else 0.0))
            self.statusBar().showMessage(
                f"识别完成：{mod}（置信度 {conf*100:.0f}%）", MSG_NORMAL)
        except Exception:
            pass

    def _on_modulation_failed(self, msg: str):
        try:
            self.statusBar().showMessage(f"识别失败: {msg}", MSG_NORMAL)
        except Exception:
            pass

    # —— 3. 扫频 progress/done/failed ——
    def _on_scanner_progress(self, frac: float, center_hz: float):
        if not self._panel_active(getattr(self, "scanner_panel", None)):
            return
        try:
            self.statusBar().showMessage(
                f"扫频中 {int(frac*100)}% @ {center_hz/1e6:.3f} MHz", MSG_QUICK)
        except Exception:
            pass

    def _on_scanner_done(self, segments: list):
        try:
            self.statusBar().showMessage(
                f"扫频完成：{len(segments or [])} 个活动段", MSG_NORMAL)
        except Exception:
            pass

    def _on_scanner_failed(self, msg: str):
        try:
            self.statusBar().showMessage(f"扫频失败: {msg}", MSG_IMPORTANT)
        except Exception:
            pass

    # —— 4. 模块/信号流 node_selected/device_selected/demod_mode_changed/sink_changed ——
    def _on_module_device_selected(self, dev: dict):
        """模块面板选设备 → 走通用连接流程（与设备面板连接按钮同 handler）。"""
        try:
            self._on_panel_device_connect(dev)
        except Exception:
            pass

    def _on_module_demod_mode_changed(self, mode: str):
        """模块面板改解调模式 → 同步后端（与控制面板模式下拉同链）。"""
        try:
            self._on_mode_changed(mode)
        except Exception:
            pass

    def _on_module_sink_changed(self, sink: str):
        try:
            self.statusBar().showMessage(f"输出 sink: {sink}", MSG_NORMAL)
        except Exception:
            pass

    def _on_module_node_selected(self, name: str, ntype: str):
        try:
            self.statusBar().showMessage(f"模块节点: {name} ({ntype})", MSG_QUICK)
        except Exception:
            pass

    # —— 5. 分段增益 value_changed → 同步后端/状态栏 ——
    def _on_gain_value_changed(self, logical: str, db: float):
        try:
            be = getattr(self, "_active_sdr_backend", None)
            if be is not None and hasattr(be, "set_gain"):
                # GainPanel 已通过 GainStager 真实下发分档；这里仅回贴状态栏总增益。
                pass
            self.status_gain.setText(f"增益: {logical} {db:.1f} dB")
        except Exception:
            pass

    # —— 6. ANR mode_changed → 切换降噪模式 ——
    def _on_anr_mode_changed(self, mode: str):
        try:
            anr_p = getattr(self, "anr_panel", None)
            anr = getattr(anr_p, "anr", None) if anr_p is not None else None
            if anr is not None and hasattr(anr, "set_mode"):
                anr.set_mode(mode)
            self.statusBar().showMessage(f"降噪模式: {mode}", MSG_NORMAL)
        except Exception:
            pass

    # —— 7. 星座图 cleared → 重置统计 ——
    def _on_constellation_cleared(self):
        try:
            self._last_dbfs = None
            self.status_rssi.setText("信号: —")
            self.statusBar().showMessage("星座图已清空，统计已重置", MSG_QUICK)
        except Exception:
            pass

    # —— 8. 设备 device_plugged → 热插拔刷新列表 ——
    def _on_device_plugged(self, label: str):
        try:
            dp = getattr(self, "device_panel", None)
            if dp is not None and hasattr(dp, "refresh_devices"):
                dp.refresh_devices()
            self.statusBar().showMessage(f"检测到新设备: {label}", MSG_NORMAL)
        except Exception:
            pass

    def _on_panel_vfo_added(self, center_hz: float, bw_hz: float,
                            mode: str):
        """多 VFO 面板「+ VFO」按钮：在指定频率新建 VFO 并设为主听。"""
        mgr = self._vfo_mgr
        if mgr is None:
            return
        v = mgr.add(float(center_hz), float(bw_hz), mode, inherit_last=False)
        mgr.set_active_context(v, temporary=False)
        self._apply_primary_vfo(v.vfo_id)
        self._refresh_vfo_panel()

    def _refresh_vfo_panel(self):
        """VfoManager 变化后刷新多 VFO 面板列表。"""
        vp = getattr(self, "vfo_panel", None)
        if vp is not None and hasattr(vp, "refresh"):
            try:
                vp.refresh()
            except Exception:
                pass

    def _on_anr_enabled(self, on: bool):
        """ANR 总开关：主窗口音频回调里据此决定是否过 ANR。"""
        self._anr_enabled = bool(on)

    def _on_anr_strength(self, alpha: float):
        pass  # AnrPanel 内部已改 anr.cfg.alpha

    def _on_anr_learn_noise(self):
        """用最近一块解调音频刷新噪声底。无音频 / 无 ANR 实例时无操作。"""
        try:
            audio = getattr(self, "_last_demod_audio", None)
            if audio is None or len(audio) == 0:
                return
            anr_p = getattr(self, "anr_panel", None)
            anr = getattr(anr_p, "anr", None) if anr_p is not None else None
            if anr is not None and hasattr(anr, "learn_noise"):
                anr.learn_noise(np.asarray(audio, dtype=np.float64))
        except Exception:
            pass

    def _on_audio_output_changed(self, dev_index: int):
        """设备面板选音频输出设备 → 真传 sounddevice device 并热切换重启流。"""
        try:
            if hasattr(self, "_audio_player") and self._audio_player is not None:
                self._audio_output_device = dev_index
                player = self._audio_player
                player.preferred_device = dev_index
                # 已在播放：重启流让新设备生效（start 幂等，stop 后重开）
                if getattr(player, "_stream", None) is not None:
                    player.stop()
                    player.start()
        except Exception:
            pass

    def _on_stage_gain_changed(self, logical: str, db: float):
        """分段增益面板改某档 → 兜底走后端 set_gain（总增益）。"""
        be = self._active_sdr_backend
        if be is None:
            return
        try:
            # GainPanel 已直接调 GainStager；这里兜底同步总增益显示
            if hasattr(be, "set_gain"):
                pass  # 分档模式下总增益由 GainStager 管
        except Exception:
            pass

    def _on_panel_device_connect(self, dev: dict):
        """设备面板「连接」按钮：构造后端并走通用连接流程。"""
        try:
            from mbdsdr_ai.sdr_backend import build_backend_for_device
            backend = build_backend_for_device(dev)
        except Exception as e:  # noqa: BLE001
            self.statusBar().showMessage(f"构造后端失败: {e}", MSG_NORMAL)
            return
        if backend is None:
            self.statusBar().showMessage("无法构造该设备后端", MSG_NORMAL)
            return
        ok = self._connect_backend(
            backend,
            sample_rate=float(dev.get("_sample_rate", DEFAULT_SAMPLE_RATE_HZ)),
            gain=float(dev.get("_gain", 20.0)),
            ppm=int(dev.get("_ppm", 0)),
            agc=False,
            bandwidth=float(dev.get("_bandwidth", 0.0)),
        )
        if ok:
            try:
                label = dev.get("label") or dev.get("driver", "设备")
                dp = getattr(self, "device_panel", None)
                if dp is not None and hasattr(dp, "set_connected"):
                    dp.set_connected(True, label)
                gp = getattr(self, "gain_panel", None)
                if gp is not None and hasattr(gp, "set_backend"):
                    gp.set_backend(backend)
            except Exception:
                pass

    def _on_scanner_segment_chosen(self, seg: dict):
        """扫频活动段点击 → 确认后调谐到峰值频点并建议解调模式。

        峰值频点/带宽缺值一律按「无读数」处理：显示 — 并跳过调谐，
        绝不拿 0 Hz 兜底去指挥设备（U-02）。
        """
        try:
            peak = float(seg.get("peak_freq"))
        except (TypeError, ValueError):
            peak = None
        if peak is None or not math.isfinite(peak) or peak <= 0:
            self.statusBar().showMessage(
                "活动段峰值频点无读数（—），已跳过调谐", MSG_NORMAL)
            return
        kind = seg.get("kind", "?")
        mode = seg.get("suggested_mode", "NFM")
        try:
            bw = float(seg.get("bandwidth"))
        except (TypeError, ValueError):
            bw = None
        peak_db = seg.get("peak_db")
        peak_txt = "—" if peak_db is None else f"{float(peak_db):.1f} dB"
        reply = QMessageBox.question(
            self, "检测到信号",
            f"检测到 {kind} 信号（{peak/1e6:.3f} MHz，"
            f"峰值 {peak_txt}），\n"
            f"是否切换到 {mode} 模式调谐到该频率？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if reply != QMessageBox.Yes:
            return
        self._on_tune_sdr(peak, mode)
        if bw is not None and math.isfinite(bw) and bw > 0 \
                and hasattr(self, "_on_vfo_bandwidth_changed_ui"):
            try:
                self._on_vfo_bandwidth_changed_ui("", bw)
            except Exception:
                pass

    def _on_panel_scan_began(self):
        """扫频开始：暂停共享 IQ 轮询，避免抢读同一环形缓冲。"""
        try:
            if self._active_sdr_backend is not None:
                try:
                    self._panel_scan_prev_center = float(
                        self._active_sdr_backend.get_frequency())
                except Exception:
                    self._panel_scan_prev_center = None
            self._stop_iq_streams()
        except Exception:
            pass

    def _on_panel_scan_ended(self):
        """扫频结束：若仍连接则恢复 IQ 流并调回扫频前频点。"""
        try:
            if self._active_sdr_backend is not None:
                prev = getattr(self, "_panel_scan_prev_center", None)
                if prev:
                    try:
                        self._active_sdr_backend.set_frequency(prev)
                    except Exception:
                        pass
                self._start_iq_streams()
        except Exception:
            pass

    def _on_apply_demod_requested(self, mode: str, bandwidth_hz: float):
        """自动识别结果「应用」→ 设置解调模式 + 带宽。"""
        try:
            if hasattr(self.control_panel, "set_mode"):
                self.control_panel.set_mode(mode)
        except Exception:
            pass
        try:
            self._on_mode_changed(mode)
        except Exception:
            pass
        if bandwidth_hz > 0:
            try:
                self._on_vfo_bandwidth_changed_ui("", float(bandwidth_hz))
            except Exception:
                pass
        self.statusBar().showMessage(f"已应用解调：{mode}", MSG_NORMAL)

    def _on_bookmark_tune_requested(self, freq_hz: float, mode: str,
                                    bandwidth_hz: float):
        """书签点击 → 调谐 + 设模式 + 带宽。"""
        self._on_tune_sdr(float(freq_hz), mode or "NFM")
        if bandwidth_hz and bandwidth_hz > 0:
            try:
                self._on_vfo_bandwidth_changed_ui("", float(bandwidth_hz))
            except Exception:
                pass

    def _on_sky_tune_satellite(self, info: dict):
        """天空视图「调到该星」→ 用实时多普勒校正后的频率真正调谐。"""
        freq = float(info.get("tuned_freq_hz", 0.0))
        if freq <= 0:
            return
        mode = info.get("mode", "NFM")
        dop = float(info.get("doppler_shift_hz", 0.0))
        self._on_tune_sdr(freq, mode)
        self.statusBar().showMessage(
            f"已调谐 {info.get('name','卫星')}：{freq/1e6:.4f} MHz "
            f"(多普勒 {dop:+.0f} Hz)", MSG_NORMAL)

    def _on_sky_track_toggled(self, on: bool, sat_name: str,
                              downlink_hz: float):
        """天空图「跟踪」开关 → 接线既有卫星闭环（_sat_track_timer 500ms 环）。

        on=True  进入闭环：每 500ms 按实时多普勒自动调谐，仰角<5° 自停；
        on=False 停环并恢复用户之前的接收频率。
        启动失败（无观测站/选星失败）由 _start_satellite_tracking 状态栏
        诚实提示，并复位天空图开关（按钮不残留假状态）。
        """
        if on:
            if not sat_name or downlink_hz <= 0.0:
                self.statusBar().showMessage("跟踪失败：缺少卫星名或下行频点", MSG_NORMAL)
                self._sync_sky_track_btn(False)
                return
            before = self._sat_track_timer is not None and \
                self._sat_track_timer.isActive()
            self._start_satellite_tracking(sat_name, downlink_hz)
            after = self._sat_track_timer is not None and \
                self._sat_track_timer.isActive()
            if not after:
                # 启动被拒（无观测站/选星失败/缺依赖）：复位开关
                self._sync_sky_track_btn(False)
            elif not before:
                # 模型协商后真正启动：把按钮勾选与真实跟踪状态对齐
                self._sync_sky_track_btn(True)
        else:
            self._stop_satellite_tracking()

    def _sync_sky_track_btn(self, on: bool) -> None:
        sv = getattr(self, "sky_view", None)
        if sv is not None and hasattr(sv, "set_tracking_checked"):
            sv.set_tracking_checked(on)

    # ------------------------------------------------------------------ #
    # 云台跟随（阶段4）：与卫星跟踪环同拍驱动天线
    # ------------------------------------------------------------------ #
    def _gimbal_controller(self):
        """sdr_tools 全局云台控制器单例；缺依赖/异常返回 None（诚实）。"""
        try:
            from mbdsdr_ai.sdr_tools import _get_gimbal_controller
            return _get_gimbal_controller()
        except Exception:
            return None

    def _gimbal_available(self) -> bool:
        """云台后端可用性：board 通道已注入 / rotctld 连通 / manual。"""
        gc = self._gimbal_controller()
        if gc is None:
            return False
        mode = gc.status.mode.value
        if mode == "board_pwm":
            return gc.status.connected or \
                getattr(gc.board, "_send", None) is not None
        if mode == "rotctld":
            return bool(gc.status.connected)
        if mode == "manual":
            return True
        # OFFLINE 但 ai-sdr-mini WebSocket 已把板载通道注入进来 → 可激活
        return getattr(gc.board, "_send", None) is not None

    def _activate_gimbal_if_needed(self):
        """确保有可下发后端；OFFLINE 且板载通道在 → 激活 board_pwm。

        返回 None 表示无可用后端（调用方应诚实关闭跟随，不造假动作）。
        """
        gc = self._gimbal_controller()
        if gc is None or not self._gimbal_available():
            return None
        if gc.status.mode.value == "offline":
            try:
                gc.use_board()  # send_callable 已由 sdr_backend 注入
            except Exception:
                return None
        return gc

    def _sync_sky_gimbal_btn(self, on: bool) -> None:
        sv = getattr(self, "sky_view", None)
        if sv is not None and hasattr(sv, "set_gimbal_follow_checked"):
            sv.set_gimbal_follow_checked(on)

    def _refresh_gimbal_availability(self) -> None:
        sv = getattr(self, "sky_view", None)
        if sv is not None and hasattr(sv, "set_gimbal_available"):
            sv.set_gimbal_available(self._gimbal_available())

    def _pointing_station_trust(self) -> Dict:
        """云台指向用的站址可信度：**走 TX 同级严格口** ``trust_for_tx``。

        站址 (0,0)、显式海上坐标、缺坐标一律拒绝；只读的计算口径
        （``trust_for_calculation``）不能用于会动机械/会发射的动作。
        """
        from mbdsdr_ai.station import StationFix, trust_for_tx
        lat = getattr(self, "_observer_lat", None)
        lon = getattr(self, "_observer_lon", None)
        if lat is None or lon is None:
            return {"trusted": False,
                    "reasons": ["missing_coordinates（未设置站址，拒绝指向）"],
                    "warnings": []}
        source = "gnss" if getattr(self, "_observer_from_gnss", False) else "user"
        fix = StationFix(lat=float(lat), lon=float(lon), alt_m=0.0,
                         source=source, origin="main_window")
        return trust_for_tx(fix)

    # ---- 云台指令后台派发：GUI 线程绝不等云台应答 ------------------------
    def _gimbal_task_running(self) -> bool:
        """是否还有云台指令在后台执行（应答未回）。"""
        th = getattr(self, "_gimbal_task_thread", None)
        return th is not None and th.is_alive()

    def _drop_gimbal_task(self) -> None:
        """解除对在途任务的引用（线程是 daemon，不等也不杀）。"""
        self._gimbal_task = None
        self._gimbal_task_thread = None
        self._gimbal_pending_notified = False

    def _gimbal_dispatch_async(self, gc, cmd, ctx: Dict) -> bool:
        """后台线程执行 ``point_to_satellite``；GUI 线程只等 GRACE 收结果。

        ``ctx`` 是本拍的真相快照（真角/指令角/各阶段时刻），结果落地时
        仍用它记账，避免跨拍数据串台。返回 True 表示本拍已拿到结论并落地。
        """
        box: Dict = {}
        t0 = time.monotonic()

        def _run():
            try:
                box["res"] = gc.point_to_satellite(cmd[0], cmd[1])
            except Exception as e:  # 后端异常 = 指令未执行（原因原样上报）
                box["err"] = f"{e.__class__.__name__}: {e}"
                box["err_type"] = e.__class__.__name__

        th = threading.Thread(target=_run, name="gimbal-dispatch", daemon=True)
        self._gimbal_task = {"thread": th, "cmd": (float(cmd[0]), float(cmd[1])),
                             "ctx": ctx, "t0": t0, "box": box}
        self._gimbal_task_thread = th
        self._gimbal_pending_notified = False
        th.start()
        th.join(GIMBAL_DISPATCH_GRACE_S)
        return self._collect_gimbal_task(0.0)

    def _collect_gimbal_task(self, wait_s: float = GIMBAL_DISPATCH_GRACE_S) -> bool:
        """收取在途云台指令。最多等 ``wait_s``；超过 TIMEOUT 则合成超时并停机。

        返回 True 表示本拍已出结论（成功/失败/超时都已落地），False 表示
        指令还在途，调用方本拍放过（界面继续响应，不叠加新指令）。
        """
        task = getattr(self, "_gimbal_task", None)
        if task is None:
            return False
        th = task["thread"]
        if wait_s > 0 and th.is_alive():
            th.join(wait_s)
        elapsed_s = time.monotonic() - float(task["t0"])
        if th.is_alive():
            if elapsed_s < GIMBAL_DISPATCH_TIMEOUT_S:
                if not self._gimbal_pending_notified:
                    self._gimbal_pending_notified = True
                    self.statusBar().showMessage(
                        "云台指令等待应答中（后台执行，界面未阻塞）"
                        f" · {self.gimbal_accuracy_text()}", MSG_QUICK)
                return False
            self._drop_gimbal_task()
            self._apply_gimbal_result(
                task,
                {"dispatched": False, "pose_valid": False,
                 "error": f"超时（{elapsed_s:.1f}s 未返回应答）"},
                elapsed_s * 1000.0)
            return True
        box = task["box"]
        err = str(box.get("err") or "")
        if err:
            # 2 Hz 跟踪环里同一类异常会刷屏：按异常类型限流记日志，
            # 但原因原样进 res/周期记录/状态栏，不吞。
            _warn_throttled(f"gimbal_dispatch:{box.get('err_type') or 'unknown'}",
                            "云台指令异常（az=%.1f el=%.1f）：%s",
                            task["cmd"][0], task["cmd"][1], err)
        res = {"dispatched": False, "error": err} if err else box.get("res")
        res = res if isinstance(res, dict) else {}
        self._drop_gimbal_task()
        self._apply_gimbal_result(task, res, elapsed_s * 1000.0)
        return True

    def _apply_gimbal_result(self, task: Dict, res: Dict,
                             dispatch_ms: float) -> None:
        """把一条云台指令的结论落地：周期记录 / 误差统计 / 失败即停机。

        失败（未执行、异常、超时）必须明确停止并给原因；成功按姿态来源
        分桶记账，模拟回声永不计入机械精度。
        """
        from mbdsdr_ai.gimbal_follow import angular_separation_deg
        ctx = task.get("ctx") or {}
        az = float(ctx.get("truth_az") or 0.0)
        el = float(ctx.get("truth_el") or 0.0)
        cmd = ctx.get("cmd")
        effective = ctx.get("effective")
        t_cycle = float(ctx.get("t_cycle") or 0.0)
        t_pos = float(ctx.get("t_pos") or 0.0)
        t_plan = float(ctx.get("t_plan") or 0.0)
        t_cmd = float(ctx.get("t_cmd") or 0.0)
        rec = getattr(self, "_gimbal_error", None)
        res = res if isinstance(res, dict) else {}
        dispatched = bool(res.get("dispatched", False))
        note = str(res.get("note") or "")
        dispatch_ms = max(0.0, float(dispatch_ms))

        if cmd is not None and not dispatched:
            err_text = str(res.get("error") or "无应答/超时")
            self._gimbal_cycle_seq = getattr(self, "_gimbal_cycle_seq", 0) + 1
            self._last_gimbal_cycle = {
                "seq": self._gimbal_cycle_seq,
                "satellite_az": az, "satellite_el": el,
                "command_az": cmd[0], "command_el": cmd[1],
                "dispatched": False, "pose_valid": False,
                "pose_az": None, "pose_el": None, "pose_source": "",
                "error": err_text, "note": note,
                "cmd_error_deg": angular_separation_deg(az, el, cmd[0], cmd[1]),
                "pose_error_deg": None,
                "pos_ms": (t_pos - t_cycle) * 1000.0,
                "dispatch_ms": dispatch_ms,
                "total_ms": (time.monotonic() - t_cycle) * 1000.0,
            }
            if rec is not None:
                rec.record(az, el, cmd[0], cmd[1], elapsed_s=0.5)
            self._stop_gimbal_follow(
                f"云台指令未执行（{err_text}），跟随已停止", important=True)
            return

        if effective is None:
            return
        pose_valid = bool(res.get("pose_valid"))
        pose_az = res.get("current_az") if pose_valid else None
        pose_el = res.get("current_el") if pose_valid else None
        # 姿态来源必须记账：真实回读（imu/rotctld）与假后端的「指令回声」
        # 是两种口径，后者的 0° 误差不代表机械精度。
        pose_source = str(res.get("pose_source") or (
            "unknown" if pose_valid else ""))
        errs = (rec.record(az, el, effective[0], effective[1],
                           pose_az=pose_az, pose_el=pose_el,
                           pose_source=pose_source,
                           elapsed_s=0.5)
                if rec is not None else {})
        self._gimbal_cycle_seq = getattr(self, "_gimbal_cycle_seq", 0) + 1
        self._last_gimbal_cycle = {
            "seq": self._gimbal_cycle_seq,
            "satellite_az": az, "satellite_el": el,
            "command_az": effective[0], "command_el": effective[1],
            "dispatched": dispatched,
            "pose_valid": pose_valid,
            "pose_az": pose_az, "pose_el": pose_el,
            "pose_source": pose_source,
            "note": note,
            "cmd_error_deg": errs.get("cmd_error_deg"),
            "pose_error_deg": errs.get("pose_error_deg"),
            "pos_ms": (t_pos - t_cycle) * 1000.0,
            "cmd_ms": (t_cmd - t_plan) * 1000.0,
            "dispatch_ms": dispatch_ms,
            "total_ms": (time.monotonic() - t_cycle) * 1000.0,
        }
        if note:
            # 越界夹取/归一必须让用户看见「已夹到 X°」，不许静默执行
            self.statusBar().showMessage(note, MSG_NORMAL)

    def _stop_gimbal_backend_bounded(self) -> str:
        """下发停机但最多等 GRACE；返回未确认原因（空串=已确认或无后端）。

        挂死的后端不得冻住 GUI：线程置 daemon，超时后由后台继续等。
        """
        try:
            gc = self._gimbal_controller()
        except Exception as e:
            err = str(e) or e.__class__.__name__
            _warn_throttled("gimbal_stop", "云台停机指令未确认：%s", err)
            return err
        if gc is None:
            return ""
        box: Dict = {}

        def _run():
            try:
                box["ok"] = gc.stop()
            except Exception as e:  # 停机失败必须留证，不能静默
                box["err"] = str(e) or e.__class__.__name__

        th = threading.Thread(target=_run, name="gimbal-stop", daemon=True)
        th.start()
        th.join(GIMBAL_DISPATCH_GRACE_S)
        if th.is_alive():
            return (f"停机指令已发出，{GIMBAL_DISPATCH_GRACE_S * 1000:.0f}ms "
                    "内未确认（后台继续等待）")
        err = str(box.get("err") or "")
        if err:
            _warn_throttled("gimbal_stop", "云台停机指令未确认：%s", err)
        return err

    def _stop_gimbal_follow(self, reason: str,
                            important: bool = False) -> None:
        """明确停止云台跟随：复位开关、整形器、下发停机，并把原因说清楚。"""
        self._gimbal_follow = False
        # 在途指令立即解除引用：停机后到达的应答不得再驱动任何动作
        self._drop_gimbal_task()
        limiter = getattr(self, "_gimbal_limiter", None)
        if limiter is not None:
            limiter.reset()
        self._sync_sky_gimbal_btn(False)
        # 停机失败不能让开关卡死（状态已在上方复位），但原因必须留证并进上报，
        # 否则用户只看到「已停止」而不知道电机可能还在动。
        stop_err = self._stop_gimbal_backend_bounded()
        if stop_err:
            reason = f"{reason}（云台停机指令未确认：{stop_err}）"
        text = self.gimbal_accuracy_text()
        self._sync_gimbal_accuracy_ui(text)
        self.statusBar().showMessage(
            f"{reason} · {text}", MSG_IMPORTANT if important else MSG_NORMAL)

    def gimbal_accuracy_text(self) -> str:
        """用户可见的指向精度口径：不可报时必须写明原因，绝不渲染成 0°。

        口径唯一来源是 ``gimbal_tracking_report()``：``pose_*`` 只含真实回读；
        模拟执行/无回读时显示「不可报（原因）」，不显示「精度 0」。
        """
        rep = self.gimbal_tracking_report()
        summ = rep.get("pose_source_summary") or {}
        if summ.get("mechanical_accuracy_claimable"):
            return (f"指向精度：机械回读 {int(rep.get('pose_samples') or 0)} 样本，"
                    f"max {float(rep.get('pose_max_deg') or 0.0):.2f}° / "
                    f"mean {float(rep.get('pose_mean_deg') or 0.0):.2f}°")
        return f"指向精度：不可报（{summ.get('note') or '原因未知'}）"

    def _sync_gimbal_accuracy_ui(self, text: str) -> None:
        """把精度口径落到用户可见处（云台按钮 tooltip；不改布局/像素）。"""
        sv = getattr(self, "sky_view", None)
        if sv is not None and hasattr(sv, "set_gimbal_accuracy_text"):
            sv.set_gimbal_accuracy_text(text)

    def last_gimbal_cycle(self) -> Optional[Dict]:
        """最近一拍「卫星真角 → 指令角 → 姿态 → 耗时」原始数据。"""
        return dict(self._last_gimbal_cycle) if self._last_gimbal_cycle else None

    def gimbal_tracking_report(self) -> Dict:
        """过境全过程指向误差统计。

        来源**必须分栏**，不得用一个字段混报：
        - ``cmd_*``：指令角 vs 卫星真角（只证明死区/限速造成的指令滞后）；
        - ``pose_*``：**仅真实姿态回读**（IMU / rotctld 回读）vs 真角，
          这才是机械跟随精度的可用口径；
        - ``pose_error_by_source``：按来源分桶（readback / simulated / unknown /
          none）。假后端把「实际指向 = 指令」当回读时归 simulated，不进
          ``pose_*``，避免把 0.2° 指令残差当成云台机械精度。
        """
        rec = getattr(self, "_gimbal_error", None)
        rep = rec.stats() if rec is not None else {"samples": 0}
        rep["last_cycle"] = self.last_gimbal_cycle()
        rep["command_mode"] = "absolute_az_el"
        rep["command_error_source"] = "command_vs_truth"
        limiter = getattr(self, "_gimbal_limiter", None)
        if limiter is not None:
            rep["deadband_deg"] = limiter.deadband_deg
            rep["min_interval_s"] = limiter.min_interval_s
            rep["max_rate_deg_s"] = limiter.max_rate_deg_s
        by_src = rep.get("pose_error_by_source") or {}
        readback_n = int((by_src.get("readback") or {}).get("samples", 0))
        simulated_n = int((by_src.get("simulated") or {}).get("samples", 0))
        if readback_n > 0:
            note = "姿态样本来自真实回读，pose_* 才可作为机械跟随精度"
        elif simulated_n > 0:
            note = ("姿态样本来自模拟执行（实际指向=指令回声），pose_* 不构成"
                    "机械精度；本轮只有指令侧滞后可报")
        else:
            note = "无姿态回读样本，机械跟随精度未知"
        rep["pose_source_summary"] = {
            "readback_samples": readback_n,
            "simulated_samples": simulated_n,
            "mechanical_accuracy_claimable": readback_n > 0,
            "note": note,
        }
        return rep

    def _on_sky_gimbal_follow_toggled(self, on: bool):
        """天空图「云台跟随」开关：确认后端可用后随跟踪环同拍驱动。"""
        if not on:
            was_on = self._gimbal_follow
            self._gimbal_follow = False
            self._drop_gimbal_task()
            limiter = getattr(self, "_gimbal_limiter", None)
            if limiter is not None:
                limiter.reset()
            if was_on:
                # 用户主动解锁：下发停机（固件负值=释放），不留持续指令。
                # 有界等待：慢/挂死后端不得让「关开关」这个点击卡住界面。
                stop_err = self._stop_gimbal_backend_bounded()
                if stop_err:
                    self.statusBar().showMessage(
                        f"云台跟随已关闭，但停机未确认：{stop_err}", MSG_NORMAL)
            return
        if not (self._sat_track_timer is not None
                and self._sat_track_timer.isActive()):
            self.statusBar().showMessage(
                "先按「🎯 跟踪」开始闭环跟踪，再开云台跟随", MSG_NORMAL)
            self._sync_sky_gimbal_btn(False)
            return
        gc = self._activate_gimbal_if_needed()
        if gc is None:
            self.statusBar().showMessage(
                "无可用云台后端：连接 ai-sdr-mini 或在 AI 对话里配置 rotctld",
                MSG_NORMAL)
            self._sync_sky_gimbal_btn(False)
            return
        trust = self._pointing_station_trust()
        if not trust.get("trusted"):
            reasons = "；".join(trust.get("reasons") or []) or "站址不可信"
            self._stop_gimbal_follow(
                f"站址不可信，拒绝云台指向：{reasons}", important=True)
            return
        self._gimbal_follow = True
        # 新会话：丢弃上一轮可能残留的在途任务引用（线程为 daemon，不等不杀），
        # 避免旧目标的应答在新会话里被误记账。
        self._drop_gimbal_task()
        limiter = getattr(self, "_gimbal_limiter", None)
        if limiter is not None:
            limiter.reset()  # 新会话首拍给完整指向，不做插值
        self._sync_sky_gimbal_btn(True)
        text = self.gimbal_accuracy_text()
        self._sync_gimbal_accuracy_ui(text)
        self.statusBar().showMessage(
            f"云台跟随已开启（{gc.status.backend_info}） · {text}", MSG_NORMAL)

    # ------------------------------------------------------------------
    # 全天模式（天空视图独立 ViewState / 立体投影 160°）
    # ------------------------------------------------------------------
    def _build_all_sky_toggle(self) -> None:
        """在天空页左下角 sky 按钮排上方放「全天」开关。

        状态一律从 sky_view 读（all_sky_active / all_sky_mode_changed），
        按钮不自己留一份布尔；A 键的动作也挂到天空视图作用域内，与视图内
        按 A 完全同源。
        """
        sv = getattr(self, "sky_view", None)
        if sv is None or not hasattr(sv, "set_all_sky_mode"):
            return
        btn = QPushButton("🌐 全天 160°", sv)
        btn.setCheckable(True)
        btn.setToolTip(
            "全天模式（立体投影，视场 160°）；视图内按 A 同效，Esc 先退全天")
        t = tokens()
        btn.setStyleSheet(
            "QPushButton { background: " + t.COLORS["card_2"] + "; color: "
            + t.text("primary") + ";"
            " border:none; padding:4px 10px; border-radius:3px; }"
            "QPushButton:hover { background: " + t.COLORS["card_hover"] + "; }"
            "QPushButton:checked { background: " + t.COLORS["accent"]
            + "; color: " + t.COLORS["bg_dark"] + "; }")
        btn.clicked.connect(self._on_all_sky_btn_clicked)
        self._all_sky_btn = btn
        if hasattr(sv, "all_sky_mode_changed"):
            sv.all_sky_mode_changed.connect(self._on_all_sky_mode_changed)
        act = getattr(self, "_all_sky_action", None)
        if act is not None:
            # WidgetWithChildrenShortcut + 挂到天空视图：只在天空图上按 A 生效
            sv.addAction(act)
        self._position_all_sky_btn()
        self._sync_all_sky_ui(bool(getattr(sv, "all_sky_active", False)))

    def _position_all_sky_btn(self) -> None:
        """贴在那排 sky 按钮正上方（左下角），随天空视图尺寸变化。"""
        btn = getattr(self, "_all_sky_btn", None)
        sv = getattr(self, "sky_view", None)
        if btn is None or sv is None:
            return
        try:
            h = 26
            w = max(btn.sizeHint().width(), 118)
            y = max(12, sv.height() - 26 - 12 - h - 6)
            btn.setGeometry(12, y, w, h)
            btn.raise_()
        except Exception:
            pass

    def _sync_all_sky_ui(self, active: bool) -> None:
        """视图状态 → 按钮/菜单（单向真相源，禁回环）。"""
        btn = getattr(self, "_all_sky_btn", None)
        if btn is not None:
            btn.blockSignals(True)
            btn.setChecked(bool(active))
            btn.setText("🌐 全天 160°（已进入）" if active
                        else "🌐 全天 160°")
            btn.blockSignals(False)
            self._position_all_sky_btn()
        act = getattr(self, "_all_sky_action", None)
        if act is not None:
            act.blockSignals(True)
            act.setChecked(bool(active))
            act.blockSignals(False)

    @Slot(bool)
    def _on_all_sky_mode_changed(self, active: bool) -> None:
        self._sync_all_sky_ui(active)

    def _on_all_sky_btn_clicked(self, checked: bool) -> None:
        sv = getattr(self, "sky_view", None)
        if sv is not None:
            sv.set_all_sky_mode(bool(checked))

    def _on_all_sky_action_triggered(self, checked: bool) -> None:
        # 焦点在哪，局部优先：频谱获焦时 A 归频谱的局部 AM（Qt 已按
        # WidgetShortcut 派发），全局动作不得再改天空状态。这里兜底
        # 处理快捷键歧义/菜单调用，并恢复菜单勾选为真实状态。
        if owns_local_shortcuts(_focus_widget()):
            sv = getattr(self, "sky_view", None)
            self._sync_all_sky_ui(
                bool(getattr(sv, "all_sky_active", False)) if sv else False)
            return
        sv = getattr(self, "sky_view", None)
        if sv is None:
            return
        if checked and getattr(self, "_app_mode", "ide") != "planetarium":
            # 从菜单进入时把天空页带到前台，否则状态改了却看不见效果
            try:
                idx = self.left_tab.indexOf(sv)
                if idx >= 0:
                    self.left_tab.setCurrentIndex(idx)
            except Exception:
                pass
        sv.set_all_sky_mode(bool(checked))

    def _handle_escape_layer(self) -> bool:
        """Esc 分层：先退全天（仍在星象仪模式），再退星象仪模式。"""
        sv = getattr(self, "sky_view", None)
        if sv is not None and bool(getattr(sv, "all_sky_active", False)):
            sv.set_all_sky_mode(False)
            self.statusBar().showMessage(
                "已退出全天模式（Esc 再按一次退出星象仪模式）", MSG_NORMAL)
            return True
        if getattr(self, "_app_mode", "ide") == "planetarium":
            self._exit_planetarium()
            self.statusBar().showMessage("已退出星象仪模式", MSG_NORMAL)
            return True
        return False

    def keyPressEvent(self, event):  # noqa: N802 (Qt 命名)
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            if self._handle_escape_layer():
                event.accept()
                return
        super().keyPressEvent(event)


    def _on_toolbar_mode_selected(self, mode: str):
        """工具栏模式下拉 → 走控制面板既有信号链（真实切解调 + VFO 带宽）。

        直接改 control_panel.mode_combo 文本（不 blockSignals），让其内部
        currentTextChanged → ControlPanel._on_mode_changed → mode_changed →
        MainWindow._on_mode_changed 整条链按原样触发，不绕过后端下发。
        """
        cp = getattr(self, "control_panel", None)
        if cp is None or not hasattr(cp, "mode_combo"):
            return
        target = (mode or "FM").upper()
        if cp.mode_combo.currentText().upper() != target:
            cp.mode_combo.setCurrentText(target)

    def _on_toolbar_volume_changed(self, value: int):
        """工具栏音量(0-100) → 控制面板 volume_slider(0-100)，同一人类量纲 1:1。"""
        cp = getattr(self, "control_panel", None)
        if cp is None or not hasattr(cp, "volume_slider"):
            return
        target = int(max(0, min(100, value)))
        if cp.volume_slider.value() != target:
            cp.volume_slider.setValue(target)

    def _on_toolbar_mute_toggled(self, checked: bool):
        """静音按钮：按下音量归零，再按恢复到上次音量（复用既有音量链）。"""
        if checked:
            self._toolbar_vol_prev = self.toolbar_volume.value()
            self.toolbar_volume.setValue(0)
        else:
            self.toolbar_volume.setValue(
                int(getattr(self, "_toolbar_vol_prev", 48)))

    # ------------------------------------------------------------------
    # 窗口几何 + 坞布局持久化（QSettings）
    # ------------------------------------------------------------------
    def _restore_window_state(self):
        s = QSettings("MBDSDR", "Desktop")
        geom = s.value("window_geometry")
        if geom is not None:
            self.restoreGeometry(geom)
        # 三栏固定 QSplitter 布局后，QDockWidget 停靠体系与布局预设已弃用：
        # 不再 restoreState / apply_preset（否则旧 preset 会把天空页签搬进已
        # 弃用的浮窗 dock）。仅恢复窗口几何。
        # splitter 比例也持久化一份（供下次启动复位初始比例）。
        # 版本门（均分死黑事故）：旧权重时代（stretch 0/1/0，中栏吃弹性）
        # 持久化的比例会把新默认盖回去——非当前版本标记的存量一律弃用，
        # 回退到新权重默认 [392,300,300]；用户在本版本下拖动后才再持久化。
        sizes = s.value("three_column_sizes")
        try:
            ver = int(s.value("three_column_layout_version", 0) or 0)
        except (TypeError, ValueError):
            ver = 0
        if sizes is not None and ver >= self._THREE_COLUMN_LAYOUT_VERSION \
                and getattr(self, "_main_splitter", None) is not None:
            try:
                self._main_splitter.restoreState(sizes)
            except Exception:
                pass

    _THREE_COLUMN_LAYOUT_VERSION = 3

    def _save_window_state(self):
        s = QSettings("MBDSDR", "Desktop")
        s.setValue("window_geometry", self.saveGeometry())
        # 三栏 splitter 比例持久化（替代旧 dock_state）
        if getattr(self, "_main_splitter", None) is not None:
            try:
                s.setValue("three_column_sizes",
                           self._main_splitter.saveState())
                s.setValue("three_column_layout_version",
                           self._THREE_COLUMN_LAYOUT_VERSION)
            except Exception:
                pass

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
                "扫频需要连接本地 SDR 硬件（请先用工具栏「连接」选 USB 设备）", MSG_NORMAL)
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
            sr = float(DEFAULT_SAMPLE_RATE_HZ)
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
            f"扫频完成：{n} 个活动频点（噪声底 {nf:.1f} dB）", MSG_IMPORTANT)
        # AI 面板输出完整峰值列表
        try:
            rows = ["<b>扫频找台结果（实测）</b><br>"
                    f"范围 {f0:.1f}–{f1:.1f} MHz，"
                    f"门限 {getattr(result,'threshold_db',0.0):.1f} dB<br>"]
            if activities:
                _tbl_color = self._tok('light_text', '#5B7B8C')
                rows.append(
                    "<table width='100%' cellspacing='2' cellpadding='2' "
                    "style='font-size:8pt;'>"
                    f"<tr style='color:{_tbl_color};'><td><b>中心MHz</b></td>"
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
        self.statusBar().showMessage(f"扫频失败：{msg}", MSG_IMPORTANT)
        QMessageBox.warning(self, "扫频失败", f"扫频过程出错：\n{msg}")

    def _on_sweep_cancelled(self):
        self._sweep_cleanup()
        self.statusBar().showMessage("扫频已取消", MSG_NORMAL)

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
            # 回放中再点「回放」= 停止当前回放，**不再弹文件框**。
            # 旧行为是先 _stop_replay 再 getOpenFileName：用户取消选择就等于
            # 误触停播，且没有「停止回放」的显式入口。这里把二次点击定义成
            # 幂等的停止动作，状态栏给出可逆说明。
            self._stop_replay()
            self.statusBar().showMessage(
                "已停止回放（再点「回放」可重新选择文件）", MSG_NORMAL)
            return
        self._stop_tx("回放开始", wait=True)  # 安全：回放前必须停波
        path, _ = QFileDialog.getOpenFileName(
            self, "选择录音文件",
            self.settings.record_dir,
            "Baseband IQ (*.iq *.cf32 *.cs16 *.wav);;All Files (*)")
        if not path:
            self.statusBar().showMessage("未选择录音文件", MSG_NORMAL)
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
        sr = float(rec.get("sample_rate", DEFAULT_SAMPLE_RATE_HZ))
        center_hz = float(rec.get("center_freq_hz", 0.0))
        # S4：分段录音/混率录音的读数不能装成单一速率
        segments = rec.get("segments") or []
        self._replay_unknown_duration = rec.get("duration_s") is None
        if segments and len(segments) > 1:
            self.statusBar().showMessage(
                f"该录音共 {len(segments)} 段（录制中改过采样率）；"
                f"本次回放第 {rec.get('segment_index', 1)} 段", MSG_IMPORTANT)
        elif self._replay_unknown_duration:
            QMessageBox.warning(
                self, "回放说明",
                "该录音在录制中改过采样率，sidecar 缺少分段边界；"
                "无法确定真实时长，本次按 sidecar 末次采样率回放。")

        # 停止实时轮询（回放期间独占频谱）；结束后按当时连接状态恢复。
        # 恢复条件 = 实时后端是否连着，而不是旧 UI 线程 _iq_poll_timer
        # 的活动态（后台流水线时代该定时器不再启动，用它会永远不恢复）。
        # 录音与回放互斥：先落盘停录（用户数据不丢），再进入回放，
        # 避免"录音中... 00:00"假状态（S5）。只在文件确认可播后才做，
        # 用户取消选择时录照常继续。
        if self._recording:
            self._apply_recording(False)
            self.statusBar().showMessage(
                "已停止当前录音（文件已保存），开始回放", MSG_IMPORTANT)
        # 每段回放都从零标定噪声基线（不把上一段/上一档增益的底带进来）
        self._replay_squelch = None
        self._replay_resume_poll = self._active_sdr_backend is not None
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
            f"0.0/{self._replay_total/sr:.1f}s", 0)  # 常驻：回放进度每帧覆盖，直到用户停止
        self._refresh_tx_readout()  # 回放不预偏：读数显式说明保持原频率
        # 回放保持原频率：自动预偏开关置灰 + PTT 置灰（tooltip 说明原因）
        self.doppler_chip.setEnabled(False)
        self.doppler_chip.setToolTip("回放中：保持原频率，自动预偏不可用")
        self._refresh_tx_button_state()

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
        if getattr(self, "_replay_unknown_duration", False):
            self.statusBar().showMessage(
                f"回放中: {cur_s:.1f}s（总时长未知：录制中改过采样率）", 0)  # 常驻：回放进度每帧覆盖，直到用户停止
        else:
            self.statusBar().showMessage(
                f"回放中: {min(cur_s, tot_s):.1f}/{tot_s:.1f}s", 0)  # 常驻：回放进度每帧覆盖，直到用户停止
        if self._replay_pos >= self._replay_total:
            done = ("回放完成（时长未知：录制中改过采样率）"
                    if getattr(self, "_replay_unknown_duration", False)
                    else f"回放完成（{tot_s:.1f}s）")
            self._stop_replay()
            self.statusBar().showMessage(done, MSG_NORMAL)

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
        self.doppler_chip.setEnabled(True)
        self.doppler_chip.setToolTip(
            "多普勒链路补偿：存在卫星目标时调谐中心 = 标称频率 + 预测 Δ，"
            "发射上行反向预偏；点击开关补偿（目标失效自动关闭并提示）")
        self._refresh_tx_readout()
        self._refresh_tx_button_state()

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

    def _show_help_panel(self, tab: int = 0) -> None:
        """打开帮助/能力目录（非模态，不挡住主界面操作）。"""
        from help_panel import HelpPanel
        dlg = HelpPanel(self, initial_tab=tab)
        dlg.setAttribute(Qt.WA_DeleteOnClose, True)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        self._help_dialog = dlg

    def _show_shortcuts_panel(self) -> None:
        """打开快捷键一览（键位实时来自 desktop/shortcuts.py）。"""
        from shortcuts_panel import ShortcutsPanel
        dlg = ShortcutsPanel(self)
        dlg.setAttribute(Qt.WA_DeleteOnClose, True)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        self._shortcuts_dialog = dlg

    def _show_command_palette(self) -> None:
        """打开全局命令面板（非模态；索引由 command_palette 实时生成）。"""
        from command_palette import CommandPalette
        dlg = CommandPalette(window=self, parent=self)
        dlg.setAttribute(Qt.WA_DeleteOnClose, True)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        self._command_palette_dialog = dlg

    def _show_about(self):
        QMessageBox.about(
            self, "关于 MBDSDR",
            "<h3>MBDSDR - AI 定义无线电</h3>"
            "<p>开源 MIT 许可的软件定义无线电平台（独立实现）</p>"
            "<p>呼号：在「设置」中配置</p>"
            "<p>对标 GNU Radio / SDR++ / SatDump 等开源项目能力，独立实现（未包含其源代码）</p>"
            "<p>支持 MCP 工具调用，任意 AI IDE 可直接控制硬件</p>"
            "<p>星象仪模式视图借鉴 Stellarium 设计理念</p>"
            "<hr>"
            "<p>深空数据来自 OpenNGC（CC BY-SA 4.0）；中文名来自 Wikidata（CC0）。"
            "许可说明见 docs/dso-catalog.md。</p>"
            "<p>OpenNGC: <a href=\"https://github.com/mattiaverga/OpenNGC\">"
            "github.com/mattiaverga/OpenNGC</a><br>"
            "CC BY-SA 4.0: <a href=\"https://creativecommons.org/licenses/by-sa/4.0/\">"
            "creativecommons.org/licenses/by-sa/4.0/</a></p>"
        )

    # ========================================================================
    # 星象仪模式视图
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
            self.sky_view.set_observer(None, None)
            self.sky_view.set_data_source("none")
            return

        if self.sat_tracker is None:
            self.sat_tracker = SatelliteTracker(self.sky_view, lat, lon)
        else:
            self.sat_tracker.set_location(lat, lon)
        # 观测站坐标进天空视图本体：恒星/行星/星座层、信息卡升降时刻、
        # 搜索选中、拾取与调谐按钮都依赖它（此前只标 real 未真正注入，
        # 手动站址下整条"点星即听"链被静默阻断）。
        self.sky_view.set_observer(lat, lon)
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
        # 状态栏只渲染抓取链路给出的 code/label/reason/action：
        # 失败时必须有码和原因，绝不回落到与 code 无关的固定句子。
        from mbdsdr_ai.tle_fetch import tle_refresh_status_text
        failure = self._loop_sat_tracker.last_refresh_failure
        msg = tle_refresh_status_text(n, failure)
        ok = n > 0 and not failure
        self.statusBar().showMessage(msg, MSG_NORMAL if ok else MSG_IMPORTANT)

    def _menu_start_sat_track(self):
        """菜单项触发：等价于点面板上的开始按钮。"""
        name = self.sat_track_panel.sat_combo.currentText().strip()
        if not name:
            return
        self._start_satellite_tracking(name, self.sat_track_panel.current_downlink_hz())

    def _sync_tracking_ui(self, tracking: bool) -> None:
        """跟踪状态 → 面板按钮 + 「停止卫星跟踪」动作（单一真相源出口）。

        启动/手动停止/过境结束自动停/断开清理都走这里：入口和退出必须同步，
        否则无目标时动作仍可点，点下去只会静默早退（假活）。
        """
        self.sat_track_panel.set_tracking_state(bool(tracking))
        act = getattr(self, "_sat_stop_action", None)
        if act is not None:
            act.setEnabled(bool(tracking))
            act.setToolTip("停止当前卫星跟踪并恢复原频率/模式" if tracking
                           else "当前无跟踪目标")

    def _start_satellite_tracking(self, sat_name: str, downlink_freq_hz: float):
        """进入闭环自动跟踪：选星 -> 500ms 轮询 az/el/多普勒 -> 自动调谐 SDR。

        硬约束：
          - 调谐频率 = 下行频率 + 多普勒偏移（含多普勒校正）；
          - 仰角 < 5° 时停止并提示过境结束；
          - 无观测者位置时拒绝跟踪并提示。
        """
        if LoopSatTracker is None:
            self.statusBar().showMessage("卫星跟踪不可用（缺 skyfield 依赖）", MSG_NORMAL)
            return
        if self._observer_lat is None or self._observer_lon is None:
            self._sync_tracking_ui(False)
            self.statusBar().showMessage(
                "需要观测者位置：连接 GNSS 或在 gui_config.json 配置 observer_lat/lon",
                MSG_IMPORTANT)
            return

        # 重复开始守卫：跟踪中再次点「开始」（天空图开关/菜单/外部调用）
        # 会重走本函数。若直接继续，下面记录的「跟踪前频率」已是上一次
        # 多普勒后的频点，停跟踪时就回不到用户原来的调谐；同一颗星
        # 重复开始还会重建定时器。同一目标按幂等忽略，切换目标先停旧跟踪
        # （_stop 会把频率/模式写回，再开始才能记对 prev）。
        active = (self._sat_track_timer is not None
                  and self._sat_track_timer.isActive())
        if active:
            current = str(getattr(self._loop_sat_tracker,
                                  "selected_name", "") or "")
            if not current or sat_name == current:
                self.statusBar().showMessage(
                    f"已在跟踪 {sat_name}，忽略重复开始", MSG_NORMAL)
                self._sync_tracking_ui(True)
                return
            self._stop_satellite_tracking()

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
            self.statusBar().showMessage(f"无法选中卫星 {sat_name}", MSG_NORMAL)
            return
        self._loop_sat_tracker.set_downlink_freq(downlink_freq_hz)

        # 多普勒链路补偿：与跟踪环同一 tracker/目标，唯一真相源
        # （RX 调谐预偏 + TX 发射预偏共用；不锁定则补偿关闭）。
        from mbdsdr_ai.doppler_link import get_compensator
        comp = get_compensator()
        comp.tracker = self._loop_sat_tracker
        comp.arm_target(sat_name, downlink_freq_hz)

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
        # 新一次过境：误差统计从零开始，不混入上一次的样本
        rec = getattr(self, "_gimbal_error", None)
        if rec is not None:
            rec.reset()
        self._gimbal_cycle_seq = 0
        self._last_gimbal_cycle = None
        self._sat_track_timer.start()
        self._sync_tracking_ui(True)
        self.statusBar().showMessage(
            f"开始跟踪 {sat_name}（下行 {downlink_freq_hz/1e6:.4f} MHz）", MSG_NORMAL)
        self._poll_satellite_track()  # 立即先调谐一次
        self._refresh_tx_readout()    # 目标切换 → 发射预偏读数立即重算

    def _poll_satellite_track(self):
        """500ms 轮询：取实时 az/el/多普勒 -> 自动调谐 SDR -> 刷新面板。"""
        if self._loop_sat_tracker is None:
            return
        t_cycle = time.monotonic()
        pos = self._loop_sat_tracker.current_position()
        t_pos = time.monotonic()
        self.sat_track_panel.update_position(pos)
        # 链路补偿状态（唯一真相源；失效自动关闭 + 原因上芯片）
        from mbdsdr_ai.doppler_link import get_compensator
        comp = get_compensator()
        comp.refresh(pos)
        self._update_doppler_chip(comp)
        if not pos.get("valid"):
            # 目标位置失效：云台跟随不能对着过期方位继续下发，诚实解锁并提示
            if self._gimbal_follow:
                self._stop_gimbal_follow(
                    "目标位置失效，云台跟随已停止并解锁（保持最后指向）")
            return
        el = float(pos["elevation"])
        # 仰角 < 5°：卫星不可见 → 跟踪与云台一起明确停止，不向地平线下发角
        if el < 5.0:
            self._stop_satellite_tracking()
            self.statusBar().showMessage(
                f"卫星不可见（仰角 {el:.1f}° < 5°），跟踪与云台跟随已停止",
                MSG_IMPORTANT)
            return
        # 核心：调谐频率 = 下行频率 + 多普勒偏移（补偿开启时）；
        # 用户关闭补偿 → 调谐标称下行（诚实不补偿，不沿用过期偏移）。
        tune_hz = comp.rx_freq()
        if tune_hz is None:
            tune_hz = float(self._loop_sat_tracker.downlink_freq_hz())
        try:
            self._on_tune_sdr(float(tune_hz), self._sat_track_mode)
        except Exception:
            pass
        # 星点亮：主听 VFO 带内 SNR 过门限 -> 天空图该星加辉光环
        # （点星即听闭环下半场："信号出现星点亮"）。
        try:
            sat_name = str(getattr(self._loop_sat_tracker,
                                   "selected_name", "") or "")
            snr = self.spectrum.compute_snr_db() \
                if hasattr(self, "spectrum") else None
            now_present = _snr_signal_present(snr, self._sat_signal_present)
            if now_present != self._sat_signal_present and sat_name:
                self._sat_signal_present = now_present
                sv = getattr(self, "sky_view", None)
                if sv is not None and hasattr(sv, "set_sat_signal"):
                    sv.set_sat_signal(sat_name, now_present)
        except Exception:
            pass
        # 云台跟随：同一拍驱动天线指向卫星（调谐与云台天然同步、单点停止）。
        # 经整形器：死区 + 最小间隔 + 速率上限，目标到位后不再重复下发。
        # 指令为**绝对角** (az, el)，不是增量；误差按球面角距统计。
        # 下发在后台线程（GUI 线程每拍最多等 GIMBAL_DISPATCH_GRACE_S）：
        # 慢响应/永不应答的后端不得冻住界面。
        if self._gimbal_follow:
            trust = self._pointing_station_trust()
            if not trust.get("trusted"):
                reasons = "；".join(trust.get("reasons") or []) or "站址不可信"
                self._stop_gimbal_follow(
                    f"站址不可信，云台跟随已停止：{reasons}", important=True)
                return
            if not self._gimbal_available():
                self._stop_gimbal_follow(
                    "云台后端已离线，跟随已停止（不再下发角度）", important=True)
                return
            # ① 先收上一拍的在途指令（可能已完成、异常或已超时）。
            self._collect_gimbal_task()
            if not self._gimbal_follow:
                return
            # ② 指令仍在途：本拍不叠加新指令；整形器也不得先走
            #    （否则指令没发却已按「已下发」记账，下一拍会被死区吃掉）。
            if self._gimbal_task_running():
                self.statusBar().showMessage(
                    "云台指令等待应答中（后台执行，界面未阻塞）"
                    f" · {self.gimbal_accuracy_text()}", MSG_QUICK)
                return
            try:
                gc = self._activate_gimbal_if_needed()
                if gc is None:
                    self._stop_gimbal_follow(
                        "云台后端已离线，跟随已停止（不再下发角度）",
                        important=True)
                    return
                az = float(pos.get("azimuth") or 0.0)
                limiter = getattr(self, "_gimbal_limiter", None)
                t_plan = time.monotonic()
                cmd = (limiter.plan(az, el) if limiter is not None
                       else (az, el))
                t_cmd = time.monotonic()
                # 指令没触发整形器下发时，仍用「当前生效的绝对角」与真角比，
                # 这样死区/限速造成的滞后也会进过境误差统计。
                effective = cmd
                if effective is None and limiter is not None:
                    effective = limiter.last_command
                if effective is None:
                    return
                ctx = {"cmd": cmd, "effective": effective,
                       "truth_az": az, "truth_el": el,
                       "t_cycle": t_cycle, "t_pos": t_pos,
                       "t_plan": t_plan, "t_cmd": t_cmd}
                if cmd is None:
                    # 死区/限速抑制：没有下发，但仍记账（含指令侧滞后）
                    self._apply_gimbal_result(ctx, {}, 0.0)
                    return
                # 下发 + 本拍收结果（快速后端在同一拍落地；
                # 慢后端返回 False，状态栏提示等待中，下一拍再收）
                self._gimbal_dispatch_async(gc, cmd, ctx)
            except Exception:
                pass

    def _update_doppler_chip(self, comp) -> None:
        """把链路补偿状态写进顶栏芯片（标称/实际/Δ + 开关 + 失效原因）。"""
        st = comp.state
        if st.target_name and st.valid and st.doppler_hz is not None:
            nominal = st.downlink_hz / 1e6
            actual = (st.corrected_freq_hz or 0.0) / 1e6
            self.doppler_chip.setText(
                f"Δ {st.doppler_hz:+,.0f} Hz · {nominal:.4f}→{actual:.4f}")
            if self.doppler_chip.isChecked() != st.enabled:
                self.doppler_chip.blockSignals(True)
                self.doppler_chip.setChecked(st.enabled)
                self.doppler_chip.blockSignals(False)
            self.doppler_chip.setToolTip(
                f"多普勒补偿（{st.target_name}）：调谐中心 = 标称 + Δ；"
                "点击关闭补偿（调谐回标称）")
        else:
            if self.doppler_chip.isChecked():
                self.doppler_chip.blockSignals(True)
                self.doppler_chip.setChecked(False)
                self.doppler_chip.blockSignals(False)
            self.doppler_chip.setText("多普勒 --")
            if st.reason:
                self.doppler_chip.setToolTip(f"补偿已关闭：{st.reason}")

    def _on_doppler_chip_toggled(self, checked: bool):
        """芯片开关：关补偿 → 调谐回标称下行（不沿用过期偏移）。"""
        from mbdsdr_ai.doppler_link import get_compensator
        comp = get_compensator()
        ok = comp.set_enabled(bool(checked))
        if not ok:
            # 后端补偿活动 → 复位开关并提示冲突原因（防双补偿）
            self.doppler_chip.blockSignals(True)
            self.doppler_chip.setChecked(False)
            self.doppler_chip.blockSignals(False)
            self.statusBar().showMessage(comp.state.reason, MSG_NORMAL)
            self._refresh_tx_readout()
            return
        self._refresh_tx_readout()
        if not checked and self._loop_sat_tracker is not None:
            try:
                self._on_tune_sdr(
                    float(self._loop_sat_tracker.downlink_freq_hz()),
                    self._sat_track_mode)
            except Exception:
                pass

    def _stop_satellite_tracking(self):
        """停止闭环跟踪，恢复用户之前的接收频率。"""
        active = (self._sat_track_timer is not None
                  and self._sat_track_timer.isActive())
        # 没在跟踪（定时器停 + 无云台跟随）时不做回写/不刷"已停止"：断开/退出
        # 路径会无条件调用本方法。但"跟踪中"的可见状态必须无条件复位——
        # 自动停（过境结束）与手动关开关都走这里，残留勾选就是假状态
        # （test_auto_stop_resets_sky_button 钉住）。
        if not active and not self._gimbal_follow:
            self._sync_sky_track_btn(False)
            self._sync_tracking_ui(False)
            self._refresh_tx_readout()
            return
        if self._sat_track_timer is not None:
            self._sat_track_timer.stop()
        # 链路补偿随跟踪一起诚实关闭（不锁定 → 无补偿，不残留过期偏移）
        try:
            from mbdsdr_ai.doppler_link import get_compensator
            get_compensator().clear("跟踪停止")
            self._update_doppler_chip(get_compensator())
        except Exception:
            pass
        self._sync_tracking_ui(False)
        self._sync_sky_track_btn(False)  # 自动停（过境结束）也复位天空图开关
        if self._gimbal_follow:
            # 云台跟随随跟踪单点停止：复位开关 + 下发停机（固件负值=释放）。
            # 停止路径也必须有界：挂死后端不得让「停止跟踪」卡住界面。
            self._gimbal_follow = False
            self._drop_gimbal_task()
            limiter = getattr(self, "_gimbal_limiter", None)
            if limiter is not None:
                limiter.reset()
            self._sync_sky_gimbal_btn(False)
            self._stop_gimbal_backend_bounded()
        if self._sat_track_prev_freq_hz is not None:
            mode = self._sat_track_prev_mode or "FM"
            try:
                self._on_tune_sdr(self._sat_track_prev_freq_hz, mode)
            except Exception:
                pass
        self._sat_track_prev_freq_hz = None
        # 星点亮复位：停止跟踪即熄灭（不残留假亮）
        if self._sat_signal_present:
            self._sat_signal_present = False
            try:
                sv = getattr(self, "sky_view", None)
                name = getattr(self._loop_sat_tracker, "selected_name", "")
                if sv is not None and name and hasattr(sv, "set_sat_signal"):
                    sv.set_sat_signal(str(name), False)
            except Exception:
                pass
        self.statusBar().showMessage("卫星跟踪已停止", MSG_NORMAL)
        self._refresh_tx_readout()

    def _update_sky_satellites(self):
        """天空图周期任务：仅刷新新时空授时信息。

        卫星位置统一由 SatelliteTracker（mbdsdr_ai.orbit 真实 celestrak TLE）
        以自身定时器驱动；原 decoders.BUILTIN_TLE 硬编码 TLE 计算路径已移除，
        避免两套卫星数据冲突。
        """
        # 新时空：更新授时信息（每30秒NTP同步一次，其余用系统时间）
        self._update_time_info()
        # 云台后端可用性可能随 ai-sdr-mini 连接状态变化，周期刷新按钮态
        self._refresh_gimbal_availability()

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
        # 权威持久化源是 settings（desktop_settings.json）；gui_config 不再写
        self.settings.set("gnss_port", port)
        self.settings.set("gnss_baudrate", int(baud))
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
                f"GNSS 已连接: {port or '自动检测'} @ {baud}，正在搜星…", MSG_NORMAL)
        else:
            self.statusBar().showMessage(
                f"GNSS 连接失败: {port or '自动检测'} @ {baud}"
                f"（检查串口/波特率/接线，端口是否被占用）", MSG_IMPORTANT)
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
        self.statusBar().showMessage("GNSS 已断开", MSG_NORMAL)

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
        """天空对象点击处理：显示详情，可选跟踪，并调用 MCP 工具调谐频率。

        诚实口径：只有真实后端 set_frequency 成功才写频率标签/控制面板，
        才说“已调谐”；无设备或下发失败必须明说“未调谐”并给下一步。
        """
        if obj.obj_type == "satellite":
            # 切换天线指向该卫星（纯界面指向，不代表已开始跟踪或已调谐）
            antenna = AntennaPointing(
                azimuth_deg=obj.azimuth_deg,
                elevation_deg=obj.elevation_deg,
                beamwidth_deg=30,
                gain_dbi=5.0,
                is_tracking=True,
                target_name=obj.name,
            )
            self.sky_view.set_antenna(antenna)

            tuned = False
            reason = ""
            freq_hz = obj.frequency_hz
            if freq_hz is not None and freq_hz > 0:
                backend = self._active_sdr_backend
                if backend is None:
                    reason = "未连接 SDR 设备"
                else:
                    try:
                        backend.set_frequency(float(freq_hz))
                    except Exception as e:
                        reason = f"{type(e).__name__}: {e}"
                    else:
                        tuned = True
            else:
                reason = "该卫星没有配置下行频率"

            if tuned:
                freq_mhz = float(freq_hz) / 1e6
                self.freq_label.setText(f"{freq_mhz:.1f} MHz")
                self.status_freq.setText(f"频率: {freq_mhz:.1f} MHz")

                # 调用 MCP 工具设置频率（频谱-天空联动，异步旁路）
                if self._worker:
                    try:
                        self._worker.request_tool.emit(
                            "sdr_set_frequency",
                            {"frequency_hz": int(freq_hz)})
                    except Exception:
                        pass

                # 控制面板的频率显示：只有真实成功后才回填。
                # 正确入口是 set_frequency_hz（专用恢复 setter）。
                if hasattr(self, 'control_panel'):
                    try:
                        self.control_panel.set_frequency_hz(
                            float(freq_hz))
                    except Exception:
                        pass

                # VFO 收听频率跟随 → DDC 重算 offset
                self._vfo_center_hz = float(freq_hz)
                self._backend_center_hz = float(freq_hz)
                self._update_main_vfo_offset()
                self.statusBar().showMessage(
                    f"已指向 {obj.name} (AZ {obj.azimuth_deg:.1f}°, "
                    f"EL {obj.elevation_deg:.1f}°) · "
                    f"已调谐到 {freq_mhz:.3f} MHz",
                    MSG_NORMAL,
                )
            else:
                next_step = (
                    "下一步：连接 SDR 后重新点此卫星。"
                    if freq_hz is not None and freq_hz > 0
                    else "下一步：可在「AI」面板询问该卫星的下行频率。")
                self.statusBar().showMessage(
                    f"已指向 {obj.name} (AZ {obj.azimuth_deg:.1f}°, "
                    f"EL {obj.elevation_deg:.1f}°) · "
                    f"未调谐频率：{reason}。{next_step}",
                    MSG_IMPORTANT,
                )
        elif obj.obj_type == "interferer":
            self.statusBar().showMessage(
                f"干扰源 {obj.name}: AZ {obj.azimuth_deg:.1f}°, EL {obj.elevation_deg:.1f}°, "
                f"信号 {obj.signal_strength_db:.1f} dB",
                MSG_NORMAL,
            )

    def _release_runtime_resources(self) -> None:
        """关闭窗口时回收本实例的全部运行期资源（定时器 / 全局钩子 / 全局单例引用）。

        五件事，缺一件就会留下"关了还在跑/还被引用"的残留：

        1. 停掉所有窗口级 QTimer。QTimer 不随 close() 停——只有 C++ 对象销毁才停。
           关窗不销毁对象（无 WA_DeleteOnClose）时，_sky_update_timer / _utc_timer
           / _sat_track_timer 等会继续按周期回调本窗口，白烧事件循环；
           跨文件测试里每构造一个 MainWindow 就多一组常驻定时器。
        2. 摘掉应用级事件过滤器（__init__ 里 app.installEventFilter(self) 装的
           Ctrl+C 焦点感知）。QApplication 的过滤链不随窗口关闭而清理，
           已关窗口会永久留在链上，之后每个事件都要过一遍它。
        3. 断开全局触屏单例信号。touch_manager 是进程级单例，不 disconnect 的话
           模式一切换就会调用已关窗口的 _on_touch_mode_changed（该槽里还有
           app.setStyleSheet 全量重抛光，见 _apply_touch_stylesheet 的说明）。
        4. 释放全局动作总线执行器（ui_actions / sky_actions 进程级单例）。
        5. 摘除面板全局注册表挂载（PanelRegistry 进程级单例的强引用表）。

        全部 RuntimeError 兜底：C++ 侧已销毁时不再重复清理。
        """
        for attr in ("_sky_update_timer", "_utc_timer", "_sat_track_timer",
                     "_gnss_timer", "_iq_poll_timer", "_record_timer",
                     "_replay_timer", "_repaint_timer"):
            t = getattr(self, attr, None)
            if t is None:
                continue
            try:
                t.stop()
            except RuntimeError:
                pass
        # 面板自备的定时器（status_panel 的 UTC 钟、device_panel 的 2s 热插拔
        # 枚举、rf_sky_view / spectrum 的重绘钟…）同样是本窗口的子对象，关窗后
        # 一样会继续跑——热插拔那条还会周期性去枚举真实 USB 硬件。一并停掉。
        try:
            for t in self.findChildren(QTimer):
                try:
                    t.stop()
                except RuntimeError:
                    pass
        except RuntimeError:
            pass
        # QStatusBar 的 showMessage(text, timeout) 会挂一个 C++ 内部的自动清消息
        # 定时器（parent 是 QStatusBar，findChildren(QTimer) 能扫到）。关窗后它
        # 照跑，clearMessage() 即停；顺带也让状态栏不再挂着过期提示。
        try:
            sb = self.statusBar()
            if sb is not None:
                sb.clearMessage()
        except RuntimeError:
            pass
        try:
            _app = QApplication.instance()
            if _app is not None:
                _app.removeEventFilter(self)
        except RuntimeError:
            pass
        try:
            touch_manager().touch_mode_changed.disconnect(
                self._on_touch_mode_changed)
        except (RuntimeError, TypeError):
            pass
        # 4. 释放全局动作总线执行器。ui_actions / sky_actions 是进程级单例，
        #    __init__ 把本窗口绑定方法赋给 bus.executor；不释放则单例永久强引用
        #    本窗口——窗口连同三十余个子弹出件全部不可回收，长跑回归里窗口
        #    逐个堆积，setStyleSheet 每次全量重抛光所有存活控件（二次方挂死）。
        #    executor=None 是总线原生诚实空态（enqueue/drain 报 UNAVAILABLE）。
        for attr in ("_ui_bus", "_sky_bus"):
            bus = getattr(self, attr, None)
            if bus is None:
                continue
            try:
                if getattr(getattr(bus, "executor", None), "__self__",
                           None) is self:
                    bus.executor = None
            except Exception:  # noqa: BLE001
                pass
        # 5. 摘除面板全局注册表挂载。单例 _widgets 强引用表握着本窗口十块
        #    面板（attach_widget 注释说"不拥有生命周期"，强引用表即拥有）；
        #    不摘则旧窗整棵子树连同 30 余个 Qt 弹件不可回收。条件摘除保证
        #    新窗已接管的同 id 挂载不被旧窗关窗误摘。
        try:
            reg = registry()
            for pid, wgt in getattr(self, "_panel_attachments", ()):
                reg.detach_widget(pid, wgt)
        except Exception:  # noqa: BLE001
            pass

    def closeEvent(self, event):
        """关闭时断开连接并保存配置。"""
        # 发射中关窗必须先确认：窗口一关就断波，误触即中止别人的通联。
        if self._tx_session is not None \
                and not self._confirm_stop_tx("退出程序"):
            event.ignore()
            return
        # 录音中关窗必须先确认：不告知保存路径就停录、关窗，用户会以为录音丢了。
        if getattr(self, "_recording", False):
            if not self._confirm_stop_recording_for_close():
                event.ignore()
                return
            try:
                self._apply_recording(False)
            except Exception as exc:
                QMessageBox.warning(
                    self, "录音保存失败",
                    f"停止录音时出错：{exc}。"
                    "可能原因：磁盘已满、录音目录无写权限或文件被占用。"
                    "下一步：先不要关闭窗口，检查磁盘与目录后点「停止录音」重试。")
                event.ignore()
                return
        # 跟踪中关窗：先把调谐/模式恢复成跟踪前的值，再落盘配置。
        # 否则下面的 settings.flush()/_save_gui_config() 会把当前多普勒频点
        # 当成「上次使用频率」写进设置，下次启动回不到用户原调谐。
        # 跟踪停止本身不丢数据（只停定时器/云台并把调谐回写），
        # 因此这里修复保存顺序而不是加二次确认。
        try:
            self._stop_satellite_tracking()
        except Exception as exc:  # noqa: BLE001 - 关窗收尾不能因停跟踪失败而中断
            logger.warning("关窗前停止卫星跟踪失败: %s: %s",
                           type(exc).__name__, exc)
        # 先停掉远程控制 / Web 服务（daemon 线程，避免端口残留）
        try:
            sp = getattr(self, "service_settings_panel", None)
            if sp is not None and hasattr(sp, "shutdown_all"):
                sp.shutdown_all()
        except Exception:
            pass
        # 窗口几何 + 右侧坞布局落 QSettings（下次启动原样恢复）
        try:
            self._save_window_state()
        except Exception:
            pass
        # 运行参数（频率/增益/模式/带宽/采样率/呼号/AGC/主题）立即落盘，退出不丢
        try:
            self.settings.flush()
        except Exception:
            pass
        try:
            self._save_gui_config()
        except Exception:
            pass
        try:
            self._disconnect()
        except Exception:
            pass
        # 关闭即回收：断连路径抛异常也要跑到（上面已吞），否则定时器/全局钩子残留
        self._release_runtime_resources()
        # 停止真实串口 GNSS 后台读线程（定时器已在上面统一停掉）
        try:
            if self._gnss is not None:
                self._gnss.stop()
        except Exception:
            pass
        event.accept()

    def _confirm_stop_recording_for_close(self) -> bool:
        """录音中关窗确认；返回 True 表示用户选择保存并退出。"""
        path = getattr(self, "_record_file_path", "") or "（文件名尚未生成）"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("正在录音")
        box.setText(
            "正在录音。关闭窗口会停止录音并保存到：\n"
            f"{path}\n\n要停止并保存后退出吗？")
        box.setStandardButtons(QMessageBox.Save | QMessageBox.Cancel)
        box.button(QMessageBox.Save).setText("保存录音并退出")
        box.button(QMessageBox.Cancel).setText("取消（继续录音）")
        box.setDefaultButton(QMessageBox.Cancel)
        return (QMessageBox.StandardButton(box.exec())
                == QMessageBox.StandardButton.Save)

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
        """保存 GUI 配置到 ~/.mbdsdr/gui_config.json。

        诚实红线：只落真实状态。取不到的字段直接不写键，
        绝不用演示值（tokens().AI["demo_freq_mhz"] 等）冒充真实频率/音量。
        """
        import json
        config_dir = os.path.expanduser("~/.mbdsdr")
        config_file = os.path.join(config_dir, "gui_config.json")
        if getattr(self, "_gui_config_corrupt", False) and \
                not getattr(self, "_gui_config_corrupt_backup", None):
            # 原文件损坏且备份失败：宁可本次不保存，也不能覆盖掉唯一证据
            logger.warning("配置损坏且备份失败，跳过本次保存以免覆盖原文件")
            return
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
                "observer_lat": self._observer_lat,
                "observer_lon": self._observer_lon,
            }
            # theme / gnss_port / gnss_baudrate 的权威源是 settings
            # （desktop_settings.json），此处不再写——双写会让读写不对称
            # （改了重启读到旧值）。旧 gui_config 里的残留值由 _load_gui_config
            # 一次性迁移进 settings 并回写。
            # current_freq_mhz / current_mode / volume / show_waterfall 的权威源
            # 是 settings（desktop_settings.json），此处不再写——双写会让读写
            # 不对称（gui_config 100ms 后恢复会覆盖 settings 值，改了重启回退）。
            # 旧残留值由 _load_gui_config 一次性迁移进 settings 并回写。
            # NTRIP 配置：对话框已打开则取当前表单值；否则保留磁盘上的旧值。
            # 密码本地明文保存（不打印日志），未配置时写空串。
            ntrip_dlg = getattr(self, "_ntrip_dialog", None)
            if ntrip_dlg is not None:
                try:
                    config["ntrip"] = ntrip_dlg.get_config()
                except Exception as e:  # noqa: BLE001
                    logger.warning("读取 NTRIP 表单配置失败，本段不落盘: %s", e)
            else:
                # 对话框未打开：尽量保留已存在的 ntrip 段，避免 _load 后丢配置
                old = self._read_ntrip_from_disk()
                config["ntrip"] = old or {
                    "host": "", "port": 2101,
                    "mountpoint": "", "username": "", "password": "",
                }
            # 原子写：先写临时文件再 rename，进程被杀也不会留下半截 JSON
            tmp_path = config_file + ".tmp"
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass
            os.replace(tmp_path, config_file)
        except Exception as e:  # noqa: BLE001
            logger.warning("保存 GUI 配置失败: %s", e)

    def _backup_corrupt_gui_config(self, config_file: str, why: str) -> str | None:
        """把损坏的 gui_config.json 改名保留，返回备份路径（失败返回 None）。

        绝不静默用默认值覆盖用户原文件：先留证据，再允许后续写入。
        """
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = f"{config_file}.corrupt-{stamp}"
        # 先进入损坏态再尝试改名：改名失败时也要让 _save_gui_config 拒绝写入，
        # 否则下一次保存会用默认值把唯一证据覆盖掉。
        self._gui_config_corrupt = True
        try:
            os.replace(config_file, backup)
        except Exception as e:  # noqa: BLE001
            logger.warning("备份损坏配置失败: %s", e)
            self._gui_config_corrupt_backup = None
            return None
        self._gui_config_corrupt_backup = backup
        logger.warning("gui_config.json 损坏（%s），已备份到 %s", why, backup)
        try:
            self.statusBar().showMessage(
                f"配置文件损坏（{why}），已备份为 {os.path.basename(backup)}；"
                "本次按默认值启动，原文件未删除。", MSG_IMPORTANT)
        except Exception:
            pass
        return backup

    def _load_gui_config(self):
        """从 ~/.mbdsdr/gui_config.json 加载 GUI 配置。"""
        import json
        config_file = os.path.expanduser("~/.mbdsdr/gui_config.json")
        if not os.path.exists(config_file):
            return
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except Exception as e:  # noqa: BLE001
            # 解析失败不得继续走默认值再把原文件覆盖成默认值
            self._backup_corrupt_gui_config(config_file, f"JSON 解析失败: {e}")
            return
        if not isinstance(config, dict):
            self._backup_corrupt_gui_config(config_file, "顶层不是 JSON 对象")
            return
        try:
            # 恢复窗口大小和位置
            win = config.get("window", {})
            if not win.get("maximized", False):
                w = win.get("width", 1400)
                h = win.get("height", 900)
                x = win.get("x", 100)
                y = win.get("y", 100)
                self.resize(w, h)
                # B2：外接屏拔掉后旧坐标会落在屏幕外，必须先校验再 move
                x, y = self._sanitize_window_pos(x, y, w, h)
                self.move(x, y)
            # 恢复观测站坐标：缺失时默认 None（未配置），不再回退到示例城市硬编码坐标
            self._observer_lat = config.get("observer_lat")
            self._observer_lon = config.get("observer_lon")
            # 恢复 GNSS 串口配置（port="" 留空 → 连接时 auto_detect）。
            # 权威源是 settings；gui_config 里的 gnss_* 是旧双写残留，仅在
            # settings 仍为默认时一次性迁移并回写（读旧键→迁→回写，幂等）。
            try:
                s_port = str(self.settings.get("gnss_port", "") or "")
                s_baud = int(self.settings.get("gnss_baudrate", 9600))
                legacy_port = config.get("gnss_port", "")
                legacy_baud = config.get("gnss_baudrate", None)
                if not s_port and legacy_port:
                    s_port = str(legacy_port)
                    self.settings.set("gnss_port", s_port)
                if legacy_baud is not None and s_baud == 9600 \
                        and int(legacy_baud) != 9600:
                    s_baud = int(legacy_baud)
                    self.settings.set("gnss_baudrate", s_baud)
                self._gnss_port = s_port
                self._gnss_baudrate = s_baud
            except Exception:
                pass
            # 坐标就位后同步 SatelliteTracker 与天空图空状态/角标
            self._apply_observer_location()
            # 恢复主题：权威源是 settings；gui_config 旧 theme 仅在 settings
            # 仍为默认时迁移（避免双写导致改了重启回退）。
            theme = str(self.settings.get("theme", "default") or "default")
            legacy_theme = config.get("theme", "default")
            if theme == "default" and legacy_theme and legacy_theme != "default":
                theme = legacy_theme
                self.settings.set("theme", theme)
            if theme != self._current_theme:
                self._apply_theme(theme)
            # 频率/模式/音量/瀑布：权威源是 settings。gui_config 里的
            # current_freq_mhz（注意单位 MHz）/current_mode/volume/show_waterfall
            # 是旧双写残留，仅在 settings 仍为默认时一次性迁移并回写
            # （读旧键→迁→回写，幂等）。取舍：用"settings 值==默认值"作为
            # "用户未设置过"的代理；用户恰好设成默认值时旧值会覆盖一次，
            # 一次性迁移后即以 settings 为准。
            try:
                if "current_freq_mhz" in config and \
                        abs(float(self.settings.get("frequency_hz", 0.0)) - 98_000_000.0) < 1.0:
                    self.settings.set("frequency_hz",
                                      float(config["current_freq_mhz"]) * 1e6)
                if "current_mode" in config and \
                        str(self.settings.get("demod_mode", "FM")) == "FM":
                    self.settings.set("demod_mode", str(config["current_mode"]))
                if "volume" in config and \
                        int(self.settings.get("volume", 48)) == 48:
                    # gui_config 旧 volume 是 0–63 硬件量纲 → 换算成 0–100 人类量纲。
                    try:
                        v63 = int(config["volume"])
                    except (TypeError, ValueError):
                        v63 = 48
                    self.settings.set("volume", volume_hw_to_pct(v63))
                if "show_waterfall" in config and \
                        bool(self.settings.get("show_waterfall", True)) is True:
                    self.settings.set("show_waterfall",
                                      bool(config["show_waterfall"]))
            except Exception as e:  # noqa: BLE001
                logger.warning("旧配置键迁移失败（已忽略，不影响启动）: %s", e)
            # 按 settings 单源恢复（迁移可能刚刚回写新值，在此一并生效）。
            # 走专用恢复 setter（blockSignals，不触发后端下发）。
            cp = getattr(self, "control_panel", None)
            if cp is not None:
                try:
                    cp.set_frequency_hz(float(self.settings.get("frequency_hz", 98e6)))
                except Exception as e:  # noqa: BLE001
                    logger.warning("恢复频率失败（值非法，已忽略）: %s", e)
                try:
                    cp.set_mode(str(self.settings.get("demod_mode", "FM")))
                except Exception as e:  # noqa: BLE001
                    logger.warning("恢复解调模式失败（值非法，已忽略）: %s", e)
                try:
                    cp.set_volume(int(self.settings.get("volume", 48)))
                except Exception as e:  # noqa: BLE001
                    logger.warning("恢复音量失败（值非法，已忽略）: %s", e)
            spectrum = getattr(self, "spectrum", None)
            if spectrum is not None:
                st = getattr(spectrum, "_state", None)
                has_wf = bool(getattr(st, "show_waterfall", True)) if st is not None else True
                want_wf = bool(self.settings.get("show_waterfall", True))
                if want_wf != has_wf:
                    try:
                        spectrum.toggle_waterfall()
                    except Exception as e:  # noqa: BLE001
                        logger.warning("恢复瀑布可见性失败: %s", e)
            # NTRIP 配置：对话框已创建则回填；未创建则等 _open_ntrip_dialog 时再读盘。
            ntrip_dlg = getattr(self, "_ntrip_dialog", None)
            if ntrip_dlg is not None:
                try:
                    ntrip_dlg.apply_config(config.get("ntrip"))
                except Exception as e:  # noqa: BLE001
                    logger.warning("回填 NTRIP 配置失败: %s", e)
        except Exception as e:  # noqa: BLE001
            logger.warning("加载 GUI 配置失败: %s", e)
