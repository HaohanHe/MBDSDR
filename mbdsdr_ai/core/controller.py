# SPDX-License-Identifier: MIT
"""
MBDSDR Core - 统一控制面 API（Headless-first）
=================================================

所有业务逻辑集中在此。GUI（desktop/）、CLI（core/cli.py）、Web、MCP
（core/mcp_server.py）都调用同一个 :class:`SDRController`，绝不各自实现一套。

设计红线（与任务书一致）：
  * 本文件**绝不** import PySide6 / Qt。
  * 无硬件 / 无后端时，所有"取数据"方法返回 ``None``，状态方法显式
    ``connected: False``；绝不伪造频谱/报文/卫星过境。
  * 设备/音频/网络资源用显式 start/stop（或上下文管理器）管理。
  * 可选内核模块（skyfield / psutil / sounddevice / SoapySDR…）一律
    ``try/except`` 守卫，缺失时返回 ``{"error": "not_available"}`` 而非崩溃。
  * 调试信号源 :class:`~mbdsdr_ai.debug_source.DebugSource` 会被显式标记为
    "调试信号源（合成信号，非真实设备）"，绝不冒充真实硬件。
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from ..observation_record import MAX_TIMELINE_EVENTS, make_event
from .errors import (
    SDRUserError,
    DriverNotFoundError,
    DeviceNotFoundError,
    NoAudioOutputError,
    SampleRateNotSupportedError,
    FrequencyOutOfRangeError,
    DecoderNotAvailableError,
    DecoderNotStreamableError,
)
from ..sdr_link_supervisor import (
    SdrLinkSupervisor,
    DEFAULT_BASE_DELAY_S,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_DELAY_S,
)
from ..modes_defaults import check_bandwidth

logger = logging.getLogger(__name__)

# 合法解调模式（CLI/MCP 做参数校验用）
VALID_MODES = ("AM", "DSB", "FM", "WFM", "NFM", "USB", "LSB", "CW")

# 支持流式 IQ 解码的解码器（只有这两类实现了流式入口）
STREAMABLE_DECODERS = ("adsb", "rds")

# 只有文件级入口的解码器：能力真实存在，但不能通过 start_decoder 走流式
FILE_ONLY_DECODERS = ("aprs", "noaa_apt", "meteor",
                      "sstv", "ft8", "wspr", "pocsag", "acars")

# 已知解码器类型（枚举用；不等于都可流式）
SUPPORTED_DECODERS = STREAMABLE_DECODERS + FILE_ONLY_DECODERS

# 音频输出采样率（解调后送声卡的统一速率）
AUDIO_FS = 48_000.0

# 同一解码器的 decode_hit 事件最小间隔（秒）：解码器每个 IQ 块都跑，
# 逐块记录会把时间轴刷爆；间隔内的命中聚合进下一次事件（count 累加）。
DECODE_HIT_MIN_INTERVAL_S = 1.0

# Task 78：SDR 常驻自动重连的 opt-in 开关。
# 环境变量优先于配置文件键；两者都没设 = 关闭（默认），绝不自动打开设备。
SDR_AUTORECONNECT_ENV = "MBDSDR_SDR_AUTORECONNECT"
SDR_AUTORECONNECT_KEY = "sdr_autoreconnect"
_AUTORECONNECT_ON = frozenset(("1", "true", "yes", "on"))
_AUTORECONNECT_OFF = frozenset(("0", "false", "no", "off"))


def _parse_autoreconnect_value(raw: Any) -> Optional[bool]:
    """把配置值 / 环境变量解析成开关；无法判定返回 None（按关闭处理）。"""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        if float(raw) == 1.0:
            return True
        if float(raw) == 0.0:
            return False
        return None
    text = str(raw).strip().lower()
    if text in _AUTORECONNECT_ON:
        return True
    if text in _AUTORECONNECT_OFF:
        return False
    return None


def _parse_iso_utc(text: Any):
    """ISO8601 → aware datetime；解析不了返回 ``None``（不猜时区）。

    时间轴事件的 ``utc`` 由不同来源写入（``isoformat()`` 带微秒、
    ``utc_now_iso()`` 以 ``Z`` 结尾），统一在这里解析。无时区信息的串按
    UTC 处理——本项目所有时间戳都是 UTC。
    """
    if not isinstance(text, str) or not text:
        return None
    raw = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def _brief_message(message: Dict[str, Any], max_keys: int = 8,
                   max_len: int = 120) -> Dict[str, Any]:
    """压缩一条报文为时间轴摘要：前 8 个键，长值截到 120 字符。"""
    out: Dict[str, Any] = {}
    for i, (key, value) in enumerate(message.items()):
        if i >= max_keys:
            break
        if isinstance(value, (int, float, bool)) or value is None:
            out[key] = value
        else:
            out[key] = str(value)[:max_len]
    return out


def _decode_message_is_hit(message: Any) -> bool:
    """解码器返回的一条「报文信息」是否算真实命中。

    只排除解码器自己声明的未命中/错误形态；不认识的结构按命中处理
    （宁可多记一条真实报文，也不把真结果藏起来）。
    """
    if not isinstance(message, dict) or not message:
        return False
    if message.get("error"):
        return False
    if message.get("success") is False:
        return False
    if "rds_present" in message and not message.get("rds_present"):
        return False
    return True


def read_sdr_autoreconnect_setting(config_dir: Optional[str] = None):
    """读取 SDR 自动重连 opt-in 开关，返回 ``(enabled, source)``。

    **优先级（不合并；环境变量绝对优先）**：

    1. 环境变量 ``MBDSDR_SDR_AUTORECONNECT``：``1/true/yes/on`` 为开，
       ``0/false/no/off`` 为关；其它非空值视为非法、按关处理。只要该变量
       存在就以它为准，便于测试/脚本临时覆盖配置文件（含显式关闭）。
    2. 配置文件键 ``sdr_autoreconnect``：读 ``<config_dir>/config.json``，
       ``config_dir`` 缺省时读 ``~/.mbdsdr/config.json``——与
       :class:`mbdsdr_ai.config.ConfigStore` 同一份文件。只读，不创建文件。
    3. 两者都没有 → ``(False, "default")``。

    ``source`` ∈ ``"env"`` / ``"config"`` / ``"default"``，供状态快照标明
    「这个开关是谁定的」，不把配置来源藏起来。
    """
    if SDR_AUTORECONNECT_ENV in os.environ:
        raw = os.environ.get(SDR_AUTORECONNECT_ENV)
        return (_parse_autoreconnect_value(raw) is True, "env")
    try:
        from ..config import CONFIG_PATH, ConfigStore

        path = (os.path.join(os.path.expanduser(str(config_dir)), "config.json")
                if config_dir else os.path.expanduser(CONFIG_PATH))
        data = ConfigStore(path).read()
    except Exception as e:  # 配置不可读按关闭处理，绝不因此打开设备
        logger.debug("读取 SDR 自动重连配置失败，按关闭处理: %s", e)
        return False, "default"
    if SDR_AUTORECONNECT_KEY in data:
        ok = _parse_autoreconnect_value(data[SDR_AUTORECONNECT_KEY])
        return (ok is True, "config")
    return False, "default"


# ======================================================================
# 对外数据结构（dataclass，纯数据，可 to_dict）
# ======================================================================
@dataclass
class AudioDeviceInfo:
    """一个音频输出设备。"""

    index: int
    name: str
    channels: int = 0
    default_samplerate: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class VfoInfo:
    """一个 VFO（可变频率振荡器）的快照。"""

    vfo_id: str
    center_hz: float
    mode: str
    bw_hz: float
    primary: bool = False
    muted: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SatInfo:
    """一颗可接收卫星的静态描述。"""

    name: str
    norad: int
    downlink_mhz: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PassInfo:
    """一次卫星过境预报（可 JSON 化的扁平结构）。"""

    name: str
    rise_utc: str
    set_utc: str
    max_alt_time_utc: str
    max_alt_deg: float
    duration_s: float
    rise_az_deg: float
    set_az_deg: float
    # TLE 可追溯（真实数据原则）：历元 + 来源；缺省空串 = 未知，不编造
    tle_epoch: str = ""
    tle_source: str = ""
    # P10：真实性/精度风险（身份是否核实、新鲜度分级、时刻精度）
    tle_verified: bool = False
    tle_tier: str = ""
    tle_age_days: Optional[float] = None
    refresh_status: str = ""
    accuracy_risk: str = ""
    accuracy_note: str = ""
    time_precision_s: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SystemStatus:
    """get_status() 的完整系统快照。"""

    connected: bool
    device: Optional[str] = None
    is_debug_source: bool = False
    frequency_hz: float = 0.0
    sample_rate_hz: float = 0.0
    demod: str = "FM"
    gain_db: float = 0.0
    #: 最近一次显式 set_bandwidth 与当前解调模式的默认通带不一致时的说明；
    #: 一致/未设置时为空串（字段稳定，调用方无需判键存在）。
    bandwidth_warning: str = ""
    audio_running: bool = False
    recording: bool = False
    remote_control: Dict[str, Any] = field(default_factory=dict)
    web_server: Dict[str, Any] = field(default_factory=dict)
    cpu_percent: Optional[float] = None
    mem_percent: Optional[float] = None
    # Task 70-73：SDR/GNSS/时间/站址的运行时来源快照（既有字段保持不变）。
    runtime_sources: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ======================================================================
# RC 后端适配器：把 controller 桥接成 RemoteControl 需要的 duck-type
# ======================================================================
class _RCShim:
    """GQRX/hamlib remote_control 风格的后端适配器。"""

    def __init__(self, ctrl: "SDRController") -> None:
        self._c = ctrl

    def get_freq(self) -> int:
        return int(self._c.get_frequency())

    def set_freq(self, hz: int) -> bool:
        return bool(self._c.set_frequency(float(hz)))

    def get_mode(self) -> str:
        return self._c.get_demod()

    def set_mode(self, mode: str) -> bool:
        self._c.set_demod(mode)
        return True

    def get_gain(self) -> float:
        return float(self._c.get_gain())

    def set_gain(self, db: float) -> bool:
        return bool(self._c.set_gain(float(db)))


# ======================================================================
# SDRController
# ======================================================================
def _pump_backoff_seconds(empty: bool, is_debug: bool) -> float:
    """录音泵每轮退避秒数（0 表示立即下一轮）。

    真机有界读本身按采样率阻塞，不需要额外 sleep；只有空读要退避避免
    忙转，调试源读不阻塞、保留 1 ms 小睡。2026-11-22 真机实测：旧实现
    每轮固定 5 ms，2.4 MS/s 下 2.5 s 墙钟只录到 0.53 s 样本；改为真机
    0 退避后恢复到 99.7% 实时（5 s → 4.983 s）。
    """
    if empty:
        return 0.005
    if is_debug:
        return 0.001
    return 0.0


def _sample_shortfall(wall_s: Optional[float], sample_rate: float,
                      samples: int, tol: float = 0.98
                      ) -> Optional[Dict[str, Any]]:
    """墙钟时长 vs 实际样本：短录超过 2% 返回证据 dict，正常返回 None。

    录音泵曾因每轮固定 sleep 5 ms 在 2.4 MS/s 下只录到约 1/5 样本（2026-11-22
    真机实测：2.5 s 墙钟仅 0.53 s 样本）。读空会记 ``read_gap``，但「读成功
    却不够快」没有任何事件——必须在这里用墙钟对账，不能静默。
    """
    if wall_s is None:
        return None
    try:
        wall = float(wall_s)
        rate = float(sample_rate)
        n = int(samples)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(wall) or not math.isfinite(rate) or wall <= 0 or rate <= 0:
        return None
    expected = int(round(wall * rate))
    if expected <= 0 or n <= 0:
        return None
    ratio = n / float(expected)
    if ratio >= tol:
        return None
    return {
        "wall_s": round(wall, 3),
        "expected_samples": expected,
        "accounted_samples": n,
        "ratio": round(ratio, 4),
        "sample_rate_hz": rate,
        "reason": "录制样本数明显少于墙钟×采样率（吞吐不足或设备丢样）",
    }


def _resolve_default_gain(gain_db: float, tuner_name: str) -> float:
    """0 dB 视为「未指定」：调谐器离散表里没有 0 dB 时取中档。

    FC0012 的最近档是 -4 dB；2026-11-22 真机实测 144.64 MHz 的 IQ RMS
    0.274，而中档 7.1 dB 只有 0.063（噪声底低约 13 dB）。控制器的默认
    `_gain_db=0.0` 若直接下发，会把 APRS 这类弱信号淹掉。显式非零值原样保留。
    """
    try:
        value = float(gain_db)
    except (TypeError, ValueError):
        return gain_db
    if abs(value) > 1e-9:
        return value
    try:
        from ..rtlsdr_params import get_gain_table
        table = sorted(float(g) for g in get_gain_table(str(tuner_name or "")))
    except Exception:  # noqa: BLE001 - 无表/未知调谐器就保持原值
        return value
    if not table or any(abs(g) < 1e-9 for g in table):
        return value
    return table[len(table) // 2]


class SDRController:
    """统一控制面：所有能力的唯一入口。

    Parameters
    ----------
    backend :
        已就绪的、duck-type 兼容的接收后端（如
        :class:`~mbdsdr_ai.debug_source.DebugSource`）。传入后 ``connect()``
        会直接包装它。不传则在 ``connect("debug")`` 时自建调试信号源。
    config_dir :
        书签等持久化目录；默认 ``~/.mbdsdr``。
    runtime_sources :
        运行时数据源快照实例；默认取进程级单例
        :func:`mbdsdr_ai.runtime_sources.get_runtime_sources`，与桌面天空图
        入口共用同一份。需要隔离时显式注入替身。
    """

    def __init__(
        self,
        backend: Any = None,
        config_dir: Optional[str] = None,
        runtime_sources: Any = None,
    ) -> None:
        self._config_dir = config_dir

        # ---- 接收后端与连接状态 ----
        self._backend: Any = backend
        self._connected: bool = False
        self._device_name: str = ""
        self._is_debug: bool = False
        #: 最近一次 setter 抛出的异常文本（"类名: 消息"）。后端吞异常返回
        #: False 时，用户仍需要看到「为什么失败」，不能只剩一句「未生效」。
        self._last_setter_error: str = ""
        #: 最近一次读样本没拿到数据时的结构化原因（成功读后清空）。
        #: 后端有界读超时会给 ``code=read_timeout``，CLI/工具面据此给出
        #: 「超时未收到样本」而不是含糊的「无数据」。
        self.last_read_reason: Optional[Dict[str, Any]] = None
        # 最近一次 connect 的入口标识：自动重连复用它，不另造一套连接逻辑。
        self._last_device_id: str = ""
        # 显式启用的 SDR 常驻重连监督器；默认 None（不自动连任何硬件）。
        self._sdr_supervisor: Optional[SdrLinkSupervisor] = None
        # Task 78：opt-in 开关来源（env/config/default）与显式关闭抑制位。
        # 用户显式 disable 之后，同进程内不再被 connect 成功自动拉起。
        self._sdr_autoreconnect_source = "default"
        self._sdr_autoreconnect_suppressed = False
        # 监督器内部重连进行中：此时 connect() 的 disconnect() 不得停掉监督器。
        self._sdr_reconnecting = False

        # ---- 接收参数（控制器侧真值，下发给后端）----
        self._freq_hz: float = 100_000_000.0
        self._sample_rate: float = 2_400_000.0
        self._gain_db: float = 0.0
        self._demod: str = "FM"
        self._bandwidth_hz: float = 12_500.0
        # 显式带宽与模式默认通带的一致性提示（只提示，不拦截下发）。
        self._bandwidth_warning: str = ""
        self._squelch_db: float = -150.0

        # ---- 音频 ----
        self._player: Any = None
        self._audio_dev_manager: Any = None
        self._audio_running: bool = False
        self._audio_backend_owned: bool = False
        self._volume_db: float = 0.0
        self._muted: bool = False
        self._audio_stop = threading.Event()
        self._audio_thread: Optional[threading.Thread] = None

        # ---- 子系统（懒加载）----
        self._bookmarks: Any = None
        self._vfos: Any = None
        self._anr: Any = None

        # ---- 地面站（默认 0,0,0；卫星功能前须显式 set_ground_station）----
        self._gs_lat: float = 0.0
        self._gs_lon: float = 0.0
        self._gs_alt: float = 0.0
        # P11：站址来源/精度显式化；0,0 与「未设置」一律判不可信
        self._gs_source: str = "none"
        self._gs_accuracy_m: Optional[float] = None

        # ---- 运行时数据源快照（Task 70-73）----
        # 只读 SDR/GNSS/时间/站址；GNSS 接线需显式 sync_runtime_sources()。
        # 默认共用进程级单例：桌面天空图入口 record_fix 写入的状态可直接看到。
        from ..runtime_sources import get_runtime_sources

        self._gnss_monitor: Any = None
        self._sdr_last_update_utc: Optional[str] = None
        self._runtime_sources = (runtime_sources if runtime_sources is not None
                                 else get_runtime_sources())
        bind_sdr = getattr(self._runtime_sources, "set_sdr", None)
        if callable(bind_sdr):
            bind_sdr(self)
        bind_station = getattr(self._runtime_sources, "set_station", None)
        if callable(bind_station):
            bind_station(self)

        # ---- 扫描 / 解码 / 录制句柄 ----
        self._scans: Dict[str, Dict[str, Any]] = {}
        self._decoders: Dict[str, Dict[str, Any]] = {}
        self._scan_counter: int = 0
        self._dec_counter: int = 0
        self._recorder: Any = None
        self._recorder_thread: Optional[threading.Thread] = None
        self._recorder_stop = threading.Event()
        self._recording_path: Optional[str] = None
        #: 录制开始那一刻的运行时快照（时间/站址/设备）。观测档案用它，
        #: 而不是结束时再取——结束时设备往往已断开、GNSS 已掉 fix。
        self._recording_snapshot: Optional[Dict[str, Any]] = None
        #: 录制开始的第一个时间轴事件（含当时的频率/采样率/增益）。
        self._recording_start_event: Optional[Dict[str, Any]] = None
        #: 录制期间的时间轴事件（调谐/增益/采样率/解码命中/读空…）。
        #: 只在录制中累积；停止时快照后清空，避免污染下一次录制。
        self._recording_events: List[Dict[str, Any]] = []
        self._recording_events_lock = threading.Lock()
        #: 因超过 :data:`MAX_TIMELINE_EVENTS` 被丢弃的事件数。
        self._recording_events_dropped: int = 0
        #: 录制开始墙钟（monotonic 秒）与当时采样率：停止时核对
        #: 「墙钟时长 vs 实际样本」，短录要进时间轴，不能静默。
        self._recording_start_mono: Optional[float] = None
        self._recording_sample_rate: float = 0.0
        #: 按解码器聚合、尚未到发送间隔的 decode_hit 状态：
        #: ``{decoder: {"pending": int, "last_emit": float, "first": dict|None}}``。
        self._decode_hit_pending: Dict[str, Dict[str, Any]] = {}
        self._decode_hit_lock = threading.Lock()
        #: 录制期间流式解码线程从同一设备读走的样本数。它不进录音文件，
        #: 但必须在档案里写明，否则「录了 N 秒只有 M 秒样本」会被误读成丢包。
        self._decoder_samples_read: int = 0
        self._decoder_samples_lock = threading.Lock()

        # ---- 服务 ----
        self._rc: Any = None
        self._web: Any = None

    # ==================================================================
    # 设备
    # ==================================================================
    def list_devices(self) -> List[Any]:
        """枚举可见 SDR 设备。无硬件时返回 ``[]``，绝不造假。

        Returns
        -------
        List[DeviceInfo]
            来自 :mod:`mbdsdr_ai.device_manager` 的设备快照；另把内置
            ``"debug"`` 调试信号源作为一个虚拟项显式列出（标记 is_debug）。
        """
        out: List[Any] = []
        try:
            from ..device_manager import DeviceManager, DeviceInfo

            out.extend(DeviceManager().list_devices())
        except Exception as e:  # 驱动/模块缺失 -> 不崩，仅 debug 日志
            logger.debug("device_manager 枚举失败: %s", e)
        # 显式把调试信号源作为可选项列出（明确标记，不冒充硬件）
        out.append(
            DeviceInfo(
                name="DebugSignalGenerator（调试信号源，非真实设备）",
                driver="debug",
                serial="debug",
            )
        )
        return out

    def connect(self, device_id: str) -> bool:
        """连接设备。

        Parameters
        ----------
        device_id :
            ``"debug"`` / ``"debug-source"`` 使用内置合成信号源；否则按
            ``driver:serial`` 或序列号匹配真实设备。

        Raises
        ------
        DriverNotFoundError / DeviceNotFoundError
            驱动缺失或找不到设备时抛出（带可操作建议）。
        """
        self.disconnect()
        # 记住本次入口标识：显式启用自动重连时复用它，避免第二套连接逻辑。
        self._last_device_id = str(device_id) if device_id else "debug"

        if device_id in (None, "", "debug", "debug-source", "DebugSignalGenerator"):
            from ..debug_source import DebugSource

            src = DebugSource(sample_rate=self._sample_rate)
            if not src.connect():
                raise DeviceNotFoundError("调试信号源启用失败")
            self._backend = src
            self._is_debug = True
            self._device_name = "DebugSignalGenerator（调试信号源）"
            self._connected = True
            from ..runtime_sources import utc_now_iso
            self._sdr_last_update_utc = utc_now_iso()
            self._maybe_opt_in_sdr_autoreconnect()
            logger.info("已连接调试信号源（非真实硬件）")
            return True

        # ---- 真实设备路径 ----
        try:
            from ..device_manager import DeviceManager
        except Exception as e:
            raise DriverNotFoundError(
                "SDR 驱动模块不可用", technical_detail=e
            ) from e

        dm = DeviceManager()
        devs = dm.list_devices()
        if not devs:
            raise DeviceNotFoundError(
                f"未发现任何 SDR 设备（请求 {device_id!r}）",
                suggestion=(
                    "请插好 USB 天线棒，或使用 connect('debug') 以调试信号源测试；"
                    "远端棒 / 无 USB 权限环境可设 MBDSDR_RTL_TCP=host:port 环境变量"
                    "（多个源逗号分隔），rtl_tcp 网络源会出现在 list_devices"
                    "（driver=rtltcp），再用其名称 connect。"
                ),
            )
        target = None
        for d in devs:
            if d.device_key == device_id or d.serial == device_id or d.name == device_id:
                target = d
                break
        if target is None:
            raise DeviceNotFoundError(f"设备 {device_id!r} 不在枚举列表中")
        try:
            opened = dm.open(target)
        except SDRUserError:
            # 占用冲突 / 登记不可读已经有各自的用户结论与建议，别包成
            # 「打开设备失败」——那会把 pid 和修复动作一起埋进 technical_detail。
            raise
        except Exception as e:
            raise DeviceNotFoundError(
                f"打开设备 {device_id!r} 失败", technical_detail=e
            ) from e
        self._backend = opened
        self._device_name = target.name
        self._is_debug = False
        self._connected = True
        from ..runtime_sources import utc_now_iso
        self._sdr_last_update_utc = utc_now_iso()
        # 0 dB 是「未指定」：按调谐器离散表取中档（FC0012 → 7.1 dB），
        # 避免默认 -4 dB 把噪声底抬高十几 dB。
        gain_value = _resolve_default_gain(
            self._gain_db, getattr(opened, "tuner_name", ""))
        if gain_value != self._gain_db:
            logger.info("默认增益 0 dB → %.1f dB（调谐器 %s）",
                        gain_value, getattr(opened, "tuner_name", ""))
            self._gain_db = gain_value
        # 下发初始参数：任何一项没落到硬件都必须让 connect 失败。
        # 以前只在异常时记一条 warning，返回 True——调用方以为"已连接且
        # 参数已生效"，实际设备仍跑默认值，属典型的失败当成功。
        failures = []
        for what, fn_name, value in (
            ("采样率", "set_sample_rate", self._sample_rate),
            ("频率", "set_frequency", self._freq_hz),
            ("增益", "set_gain", gain_value),
        ):
            fn = getattr(opened, fn_name, None)
            if not callable(fn):
                failures.append(f"{what}下发失败（后端不支持该设置）")
                continue
            try:
                ok = fn(value)
            except Exception as e:
                failures.append(f"{what}下发失败（{e}）")
                continue
            if ok is False:
                failures.append(self.setter_failure_text(what))
        if failures:
            self._backend = None
            self._connected = False
            self._device_name = ""
            self._sdr_last_update_utc = None
            try:
                opened.disconnect()
            except Exception as e:
                # 初始化失败后的回滚也失败：设备可能仍被占用，必须留痕。
                logger.warning("回滚 disconnect 失败：%s: %s", type(e).__name__, e)
            raise SDRUserError(
                "设备已打开但初始参数未能生效：" + "；".join(failures),
                suggestion=(
                    "请确认设备驱动支持当前采样率/频率/增益，或先用 connect('debug') "
                    "自检；参数未生效时不会把设备标记为已连接。"
                ),
            )
        self._maybe_opt_in_sdr_autoreconnect()
        return True

    def disconnect(self) -> None:
        """断开当前设备，停止所有派生资源（音频/录制/扫描/解码）。

        用户主动断开时同时停掉自动重连（不再对已断开的链路发起尝试）；
        监督器自身的重连会经 ``connect()`` 走到这里，此时必须保留它，
        否则第一次重连成功就把监督器关掉。
        """
        if not self._sdr_reconnecting and self._sdr_supervisor is not None:
            self._sdr_supervisor.disable()
        self.stop_audio()
        self._stop_all_scans()
        self._stop_all_decoders()
        self.stop_recording()
        if self._backend is not None:
            try:
                disc = getattr(self._backend, "disconnect", None) or getattr(
                    self._backend, "close", None
                )
                if callable(disc):
                    disc()
            except Exception as e:  # pragma: no cover - 防御
                logger.warning("断开后端异常: %s", e)
        self._backend = None
        self._connected = False
        self._is_debug = False
        self._device_name = ""
        self._sdr_last_update_utc = None

    def is_connected(self) -> bool:
        """是否已连接后端。"""
        return self._connected

    # ==================================================================
    # SDR 常驻重连（Task 75；默认禁用，必须显式启用）
    # Task 78：opt-in 开关（环境变量 > 配置文件键）只在首次连接成功后生效
    # ==================================================================
    def _maybe_opt_in_sdr_autoreconnect(self) -> None:
        """首次连接成功后的 opt-in 挂载（Task 78）。

        只在「开关为开 + 已知要重连哪台设备 + 未被用户显式关闭」时启用；
        **不启动线程**（默认零后台线程），由 :meth:`sdr_reconnect_tick` 驱动。
        开关为关时什么都不做，绝不扫描/连接硬件。
        """
        if self._sdr_autoreconnect_suppressed:
            return
        supervisor = self._sdr_supervisor
        if supervisor is not None:
            snap = supervisor.status()
            # 已在工作（未达失败上限）就不重建，避免重连过程中自断；
            # 若监督器已因连续失败停下，则允许本次成功连接重新挂载。
            if snap.get("enabled") and not snap.get("stopped_reason"):
                return
        enabled, source = read_sdr_autoreconnect_setting(self._config_dir)
        self._sdr_autoreconnect_source = source
        if not enabled:
            return
        try:
            self.enable_sdr_autoreconnect(
                self._last_device_id, start_thread=False, source=source)
        except SDRUserError as e:
            # 没有设备标识就不猜；连接成功却拿不到标识属状态异常，留痕即可。
            logger.warning("SDR 自动重连未启用: %s", e)
        else:
            logger.info("SDR 自动重连已启用（来源 %s，设备 %s；显式 tick 驱动）",
                        source, self._last_device_id)

    def enable_sdr_autoreconnect(
        self,
        device_id: Optional[str] = None,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        base_delay_s: float = DEFAULT_BASE_DELAY_S,
        max_delay_s: float = DEFAULT_MAX_DELAY_S,
        start_thread: bool = True,
        source: str = "manual",
    ) -> dict:
        """显式启用 SDR 常驻重连，返回重连状态。

        真正连接复用 :meth:`connect`（同一套设备打开/参数下发逻辑），
        不新造第二套。``device_id`` 省略时用最近一次 ``connect`` 的入口；
        从未连接过则拒绝启用（不猜设备）。

        ``start_thread=True`` 时用监督器的线程模式自动 tick；传 False 则
        只置为启用，由调用方自行 ``tick``（Task 78 的自动 opt-in 走这条）。

        ``source`` 只用于状态快照标明开关来源（``env`` / ``config`` /
        ``manual``），不影响行为。
        """
        target = str(device_id or "").strip()
        if not target:
            target = str(self._last_device_id or "").strip()
        if not target:
            raise SDRUserError(
                "未指定要自动重连的设备",
                suggestion=("先 connect() 一次，或显式传入 device_id"
                            "（如 'debug' 或真实设备序列号）。"),
            )
        if self._sdr_supervisor is not None:
            self._sdr_supervisor.disable()

        def _attempt() -> bool:
            # 监督器内部重连会经 connect() → disconnect()：此时不能停掉
            # 正在工作的监督器，否则第一次重连成功就把它关了。
            self._sdr_reconnecting = True
            try:
                return bool(self.connect(target))
            finally:
                self._sdr_reconnecting = False

        supervisor = SdrLinkSupervisor(
            connect_fn=_attempt,
            is_connected_fn=self.is_connected,
            max_attempts=max_attempts,
            base_delay_s=base_delay_s,
            max_delay_s=max_delay_s,
        )
        self._sdr_autoreconnect_suppressed = False
        self._sdr_autoreconnect_source = str(source or "manual")
        self._sdr_supervisor = supervisor
        supervisor.enable()
        if start_thread:
            supervisor.start()
        return self.sdr_reconnect_status()

    def disable_sdr_autoreconnect(self) -> dict:
        """显式关闭 SDR 常驻重连（立即唤醒并停止线程），返回状态。

        置抑制位：同进程内后续 connect 成功也不会再自动拉起监督器，
        用户显式关闭的意图必须压过 opt-in 开关。
        """
        self._sdr_autoreconnect_suppressed = True
        supervisor = self._sdr_supervisor
        if supervisor is not None:
            supervisor.disable()
        return self.sdr_reconnect_status()

    def sdr_reconnect_status(self) -> dict:
        """当前重连状态；未启用过时返回完整空态（enabled=False）。

        ``source`` 标明 opt-in 开关来源：``env`` / ``config`` / ``default``
        （未开启且无配置）/ ``manual``（调用方显式 enable）。
        """
        source = self._sdr_autoreconnect_source
        supervisor = self._sdr_supervisor
        if supervisor is None:
            return {
                "enabled": False,
                "connected": self.is_connected(),
                "attempts": 0,
                "max_attempts": 0,
                "next_retry_in_s": 0.0,
                "last_error": "",
                "stopped_reason": "",
                "source": source,
            }
        status = supervisor.status()
        status["source"] = source
        return status

    def sdr_reconnect_tick(self, now: Optional[float] = None) -> bool:
        """驱动一次重连检查（``start_thread=False`` 时由调用方周期性调用）。"""
        supervisor = self._sdr_supervisor
        if supervisor is None:
            return self.is_connected()
        return supervisor.tick(now)

    # ==================================================================
    # 接收参数
    # ==================================================================
    def _backend_truth(self, name: str, fallback: float) -> float:
        """优先返回后端的真实回读；读不到才退回本地缓存。"""
        if self._connected and self._backend is not None:
            fn = getattr(self._backend, name, None)
            if callable(fn):
                try:
                    value = fn()
                    if value is not None:
                        return float(value)
                except Exception as e:
                    logger.warning("%s 读取失败: %s", name, e)
        return fallback

    def setter_failure_text(self, what: str) -> str:
        """最近一次设值被拒的如实原因（后端 status.error；无则说明未生效）。

        CLI / MCP 的调用方必须把这句话展示给用户：只写日志等于用户看不见，
        「设了但没生效」与「设成功」在使用者侧无法区分。
        """
        if self._last_setter_error:
            return self._last_setter_error
        status = getattr(self._backend, "status", None)
        err = getattr(status, "error", "") if status is not None else ""
        return err or f"{what}未生效（后端返回 False）"

    def set_frequency(self, hz: float) -> bool:
        """调谐到中心频率（Hz）。成功才更新本地缓存；未连接时仅记录目标值。"""
        hz = float(hz)
        old = self._freq_hz
        self._last_setter_error = ""
        if self._connected and self._backend is not None:
            try:
                ok = self._backend.set_frequency(hz)
            except Exception as e:
                self._last_setter_error = f"{type(e).__name__}: {e}"
                logger.warning("set_frequency 失败: %s", e)
                return False
            if ok is False:
                logger.warning("set_frequency 未生效: %s", self.setter_failure_text("调谐"))
                return False
        self._freq_hz = hz
        if hz != old:
            self.note_observation_event(
                "tune", {"from_hz": old, "to_hz": hz})
        return True

    def get_frequency(self) -> float:
        """当前中心频率（Hz）；已连接时以后端回读为准。"""
        return self._backend_truth("get_frequency", self._freq_hz)

    def set_demod(self, mode: str) -> None:
        """设置解调模式（AM/DSB/FM/WFM/NFM/USB/LSB/CW）。"""
        m = (mode or "").upper()
        if m not in VALID_MODES:
            raise SDRUserError(
                f"不支持的解调模式 {mode!r}",
                suggestion=f"可用模式：{', '.join(VALID_MODES)}",
            )
        self._demod = m
        # 模式变了：上一次「带宽与模式是否一致」的判断已失效，必须清掉，
        # 否则状态面会把旧模式的结论贴到新模式上。
        self._bandwidth_warning = ""

    def get_demod(self) -> str:
        """当前解调模式。"""
        return self._demod

    def set_bandwidth(self, hz: float) -> bool:
        """设置信道带宽（Hz）。已连接时下发后端，失败不更新本地缓存。

        显式设带宽是**强制语义**：即使与当前解调模式的默认通带不一致也照
        用户值下发；不一致只作为提示记录在 ``get_status().bandwidth_warning``
        （成功后写入，失败保持原缓存与原提示，避免对未生效的值发警告）。
        """
        hz = float(hz)
        check = check_bandwidth(self._demod, hz)
        if not check["ok"]:
            logger.warning("带宽与解调模式不一致: %s", check["reason"])
        if self._connected and self._backend is not None:
            fn = getattr(self._backend, "set_bandwidth", None)
            if callable(fn):
                try:
                    ok = fn(hz)
                except Exception as e:
                    logger.warning("set_bandwidth 失败: %s", e)
                    return False
                if ok is False:
                    logger.warning("set_bandwidth 未生效: %s",
                                   self.setter_failure_text("带宽"))
                    return False
        self._bandwidth_hz = hz
        self._bandwidth_warning = "" if check["ok"] else str(check["reason"])
        return True

    def set_gain(self, db: float) -> bool:
        """设置总增益（dB）。成功才更新本地缓存。"""
        db = _resolve_default_gain(
            float(db), getattr(self._backend, "tuner_name", ""))
        old = self._gain_db
        self._last_setter_error = ""
        if self._connected and self._backend is not None:
            try:
                ok = self._backend.set_gain(db)
            except Exception as e:
                self._last_setter_error = f"{type(e).__name__}: {e}"
                logger.warning("set_gain 失败: %s", e)
                return False
            if ok is False:
                logger.warning("set_gain 未生效: %s", self.setter_failure_text("增益"))
                return False
        self._gain_db = db
        if db != old:
            self.note_observation_event(
                "gain", {"from_db": old, "to_db": db})
        return True

    def get_gain(self) -> float:
        """当前总增益（dB）；已连接时以后端回读为准。"""
        return self._backend_truth("get_gain", self._gain_db)

    def set_gain_stage(self, stage: str, db: float) -> None:
        """设置某一增益级（如 'LNA'/'VGA'）。后端不支持时静默记录。"""
        if self._connected and self._backend is not None:
            fn = getattr(self._backend, "set_gain_stage", None)
            if callable(fn):
                try:
                    fn(stage, float(db))
                except Exception as e:
                    logger.warning("set_gain_stage(%s) 失败: %s", stage, e)

    def set_sample_rate(self, sr: float) -> bool:
        """设置基带采样率（Hz）。成功才更新本地缓存；失败明确抛错。"""
        sr = float(sr)
        old = self._sample_rate
        self._last_setter_error = ""
        if self._connected and self._backend is not None:
            try:
                ok = self._backend.set_sample_rate(sr)
            except Exception as e:
                self._last_setter_error = f"{type(e).__name__}: {e}"
                raise SampleRateNotSupportedError(
                    f"设备不支持采样率 {sr}", technical_detail=e
                ) from e
            if ok is False:
                raise SampleRateNotSupportedError(
                    f"设备未接受采样率 {sr}",
                    technical_detail=self.setter_failure_text("采样率"),
                )
        self._sample_rate = sr
        if sr != old:
            self.note_observation_event(
                "sample_rate", {"from_hz": old, "to_hz": sr})
        return True

    def get_sample_rate(self) -> float:
        """当前基带采样率（Hz）；已连接时以后端回读为准。"""
        return self._backend_truth("get_sample_rate", self._sample_rate)

    def set_squelch(self, db: float) -> None:
        """设置静噪门限（dB，负值）。"""
        self._squelch_db = float(db)

    def set_deemphasis(self, tau_us: str) -> None:
        """设置去加重档位（none/22us/50us/75us；sdrpp WFM/NFM 下拉口径）。

        委托当前后端的实时链路（ReceiveChain）；未连接/后端不支持时抛
        SDRUserError 诚实信封，不伪造“已设置”。
        """
        backend = self._backend
        setter = getattr(backend, "set_deemphasis", None) if backend else None
        if setter is None:
            raise SDRUserError(
                "当前后端不支持去加重档位",
                suggestion="连接 RTL-SDR / rtl_tcp 等实时后端后再试。",
            )
        try:
            setter(tau_us)
        except ValueError as e:
            raise SDRUserError(str(e)) from e

    def set_iq_invert(self, invert: bool) -> None:
        """设置 I/Q 反相（sdrpp IqInvert：true=复共轭，边带镜像）。

        用于修正接反的 Q 路 / 负采样率设备；与增益/正交误差校正可叠加。
        无后端/链路不支持时抛 SDRUserError 诚实信封。
        """
        backend = self._backend
        setter = getattr(backend, "set_iq_invert", None) if backend else None
        if setter is None:
            raise SDRUserError(
                "当前后端不支持 I/Q 反相",
                suggestion="连接 RTL-SDR / rtl_tcp 等实时后端后再试。",
            )
        setter(bool(invert))

    # ==================================================================
    # 音频
    # ==================================================================
    def _get_audio_dev_manager(self) -> Any:
        if self._audio_dev_manager is None:
            try:
                from ..audio_device_manager import AudioDeviceManager

                self._audio_dev_manager = AudioDeviceManager()
            except Exception as e:  # pragma: no cover
                logger.debug("AudioDeviceManager 不可用: %s", e)
                self._audio_dev_manager = False
        return self._audio_dev_manager

    def list_audio_outputs(self) -> List[AudioDeviceInfo]:
        """枚举音频输出设备。无 sounddevice 时返回 ``[]``。"""
        mgr = self._get_audio_dev_manager()
        if not mgr:
            return []
        out: List[AudioDeviceInfo] = []
        try:
            for d in mgr.list_output_devices():
                out.append(
                    AudioDeviceInfo(
                        index=int(d.get("index", -1)),
                        name=str(d.get("name", "")),
                        channels=int(d.get("channels", 0)),
                        default_samplerate=float(d.get("default_samplerate", 0.0)),
                    )
                )
        except Exception as e:
            logger.warning("枚举音频输出失败: %s", e)
        return out

    def set_audio_output(self, device_index: int) -> bool:
        """选择音频输出设备（PortAudio 索引）。"""
        mgr = self._get_audio_dev_manager()
        if not mgr:
            return False
        try:
            return bool(mgr.set_output_device(int(device_index)))
        except Exception as e:
            logger.warning("set_audio_output 失败: %s", e)
            return False

    def _ensure_player(self) -> Any:
        if self._player is None:
            try:
                from ..audio_out import AudioPlayer

                self._player = AudioPlayer(sample_rate=int(AUDIO_FS))
            except Exception as e:  # pragma: no cover
                logger.debug("AudioPlayer 不可用: %s", e)
                self._player = False
        return self._player

    def start_audio(self) -> bool:
        """启动音频播放（解调后送声卡）。

        真实设备：委托 OpenDevice 的 ReceiveChain 实时线程（信道滤波、立体声、
        去加重）；调试源等无完整链后端才回退到本地极简鉴频泵。无音频硬件 /
        sounddevice 不可用返回 False（不崩）。
        """
        if self._audio_running:
            return True
        backend = self._backend if self._connected else None
        bstart = getattr(backend, "start_audio", None)
        if callable(bstart):
            try:
                ok = bool(bstart(self._demod))
            except Exception:
                ok = False
            if ok:
                self._audio_running = True
                self._audio_backend_owned = True
                return True
        # 回退：本地泵 + 极简鉴频（调试源）
        self._audio_backend_owned = False
        player = self._ensure_player()
        if not player or not getattr(player, "available", False):
            return False
        ok = player.start()
        if not ok:
            return False
        self._audio_running = True
        self._audio_stop.clear()
        self._audio_thread = threading.Thread(
            target=self._audio_pump, name="audio-pump", daemon=True
        )
        self._audio_thread.start()
        return True

    def _audio_pump(self) -> None:
        """后台泵：读 IQ → 解调 → ANR → 送声卡。"""
        while not self._audio_stop.is_set():
            try:
                audio = self.read_audio(1024)
                if audio is not None and audio.size:
                    if self._anr is not None and getattr(self._anr, "enabled", False):
                        audio = self._anr.process(audio)
                    self._player.write(audio, sr=AUDIO_FS)
            except Exception as e:  # pragma: no cover - 后台线程不崩
                logger.debug("audio pump 异常: %s", e)
            self._audio_stop.wait(0.01)

    def stop_audio(self) -> None:
        """停止音频播放。可重复调用。"""
        if getattr(self, "_audio_backend_owned", False) and self._backend is not None:
            bstop = getattr(self._backend, "stop_audio", None)
            if callable(bstop):
                try:
                    bstop()
                except Exception as e:  # pragma: no cover - 清理路径不崩，但要留痕
                    logger.warning("后端 stop_audio 失败: %s", e)
        self._audio_backend_owned = False
        self._audio_stop.set()
        t = self._audio_thread
        if t is not None:
            t.join(timeout=1.0)
        self._audio_thread = None
        self._audio_running = False
        if self._player is not None and self._player is not False:
            try:
                self._player.stop()
            except Exception as e:  # pragma: no cover
                logger.warning("AudioPlayer.stop 失败: %s", e)

    def set_volume(self, db: float) -> None:
        """设置播放音量（dB，线性映射到 0..5 增益）。"""
        self._volume_db = float(db)
        linear = 10.0 ** (self._volume_db / 20.0)
        if self._player is not None and self._player is not False:
            try:
                self._player.set_gain(linear)
            except Exception as e:  # pragma: no cover
                logger.warning("AudioPlayer.set_gain 失败: %s", e)

    def set_mute(self, muted: bool) -> None:
        """静音 / 取消静音。"""
        self._muted = bool(muted)
        if self._player is not None and self._player is not False:
            try:
                self._player.set_muted(bool(muted))
            except Exception as e:  # pragma: no cover
                logger.warning("AudioPlayer.set_muted 失败: %s", e)

    # ==================================================================
    # DSP：频谱 / IQ / 音频
    # ==================================================================
    def _read_raw_iq(self, n: int) -> Optional[np.ndarray]:
        """从后端读 n 个复样本。未连接返回 None（不造假）。"""
        if not self._connected or self._backend is None:
            return None
        try:
            arr = self._backend.read_samples(int(n))
        except Exception as e:
            logger.warning("read_samples 失败: %s", e)
            self.last_read_reason = None
            return None
        if arr is None:
            self._note_read_reason()
            return None
        out = np.asarray(arr, dtype=np.complex64)
        if out.size == 0:
            # 有界读超时/在途都会返回空数组：把后端给的结构化原因带到控制面，
            # 上层（CLI/工具）才能说“超时未收到样本”，而不是含糊的“无数据”。
            self._note_read_reason()
            return out
        self.last_read_reason = None
        return out

    def _note_read_reason(self) -> None:
        """把后端 ``last_read_reason`` 抄到控制面（没有就留 None）。"""
        reason = getattr(self._backend, "last_read_reason", None)
        self.last_read_reason = reason if isinstance(reason, dict) else None

    def read_spectrum(self, nfft: int = 1024) -> Optional[np.ndarray]:
        """读一帧功率谱（dB，长度 nfft）。未连接返回 None。"""
        iq = self._read_raw_iq(int(nfft))
        if iq is None or iq.size < 64:
            return None
        iq = np.asarray(iq, dtype=np.complex128)
        window = np.hanning(iq.size)
        sp = np.fft.fft(iq * window)
        mag = np.abs(sp) / max(iq.size, 1)
        db = 20.0 * np.log10(mag + 1e-12)
        return db.astype(np.float32)

    def read_iq(self, n: int = 8192) -> Optional[np.ndarray]:
        """读 n 个原始复基带样本（complex64）。未连接返回 None。"""
        return self._read_raw_iq(int(n))

    def _demod_to_audio(self, iq: np.ndarray) -> np.ndarray:
        """极简解调：IQ → 单声道 float32 音频（确定性，纯 numpy）。"""
        if iq is None or iq.size < 32:
            return np.empty(0, dtype=np.float32)
        fs = self._sample_rate
        mode = self._demod.upper()
        try:
            if mode in ("FM", "WFM", "NFM"):
                phase = np.unwrap(np.angle(iq.astype(np.complex128)))
                dphi = np.diff(phase)
                audio = dphi * (fs / (2.0 * np.pi))
                # 归一到合理幅度
                peak = float(np.max(np.abs(audio))) + 1e-9
                audio = audio / peak * 0.5
            elif mode in ("AM", "AMS"):
                audio = np.abs(iq)
                audio = audio - float(np.mean(audio))
                peak = float(np.max(np.abs(audio))) + 1e-9
                audio = audio / peak * 0.5
            else:  # USB / LSB / CW / 其它
                audio = np.real(iq.astype(np.complex128))
                peak = float(np.max(np.abs(audio))) + 1e-9
                audio = audio / peak * 0.5
        except Exception:
            return np.empty(0, dtype=np.float32)
        # 抽稀到音频率
        ratio = max(1, int(round(fs / AUDIO_FS)))
        audio = audio[::ratio]
        return audio.astype(np.float32)

    def read_audio(self, n: int = 1024) -> Optional[np.ndarray]:
        """读 n 个解调后音频样本（float32，48kHz）。未连接返回 None。

        优先委托 OpenDevice 的 ReceiveChain（信道滤波/立体声/去加重）；
        调试源等无完整链后端才回退极简鉴频 _demod_to_audio。
        """
        if not self._connected or self._backend is None:
            return None
        bread = getattr(self._backend, "read_audio", None)
        if callable(bread):
            try:
                a = bread(int(n), self._demod)
            except Exception:
                a = None
            if a is not None and getattr(a, "size", 0):
                return np.asarray(a, dtype=np.float32)
        iq = self._read_raw_iq(int(n) * 4)
        if iq is None:
            return None
        return self._demod_to_audio(iq)

    def start_analyze(self, modulation: Optional[str] = None) -> dict:
        """调制识别：对当前一帧 IQ 做自动调制分类。

        Returns
        -------
        dict
            ``{modulation, confidence, center_freq, bandwidth, suggestions}``；
            无后端 / 模块缺失时返回 ``{"error": "not_available"}``。
        """
        iq = self._read_raw_iq(8192)
        if iq is None:
            return {"error": "not_connected",
                    "suggestion": "请先 connect() 一个设备或调试信号源。"}
        try:
            from ..analysis.modulation_classifier import ModulationClassifier

            res = ModulationClassifier().classify(iq, self._sample_rate)
            return {
                "modulation": res.modulation,
                "confidence": float(res.confidence),
                "center_freq": float(res.center_freq),
                "bandwidth": float(res.bandwidth),
                "symbol_rate_hint": res.symbol_rate_hint,
                "suggestions": list(res.suggestions),
            }
        except Exception as e:
            return {"error": "not_available", "technical_detail": str(e)}

    # ==================================================================
    # 扫描
    # ==================================================================
    def start_scan(self, start: float, stop: float, step: float) -> str:
        """启动步进扫频（后台线程），返回扫描句柄。

        Raises
        ------
        SDRUserError
            未连接后端时（扫频需要真实 IQ，不造假）。
        """
        if not self._connected:
            raise SDRUserError(
                "未连接设备，无法扫频",
                suggestion="请先 connect('debug') 或连接真实 SDR 设备。",
            )
        from ..scanner import SweepScanner, extract_active_segments

        self._scan_counter += 1
        handle = f"scan-{self._scan_counter:04d}"
        scanner = SweepScanner(
            start_hz=float(start), stop_hz=float(stop), step_hz=float(step),
            dwell_samples=4096, threshold_db=6.0,
        )

        state: Dict[str, Any] = {
            "scanner": scanner,
            "results": [],
            "done": False,
            "error": None,
            "stop": threading.Event(),
        }

        def psd_provider(center_hz: float, bw_hz: float):
            if state["stop"].is_set():
                raise RuntimeError("scan stopped")
            # 调谐被拒还继续采数，会把「旧频点的信号」标成新中心频率，
            # 用户看到的是编出来的频谱。失败就在这里断掉整轮扫描并说明原因。
            if self._backend.set_frequency(center_hz) is False:
                raise SDRUserError(
                    f"扫频调谐 {center_hz / 1e6:.6f} MHz 失败："
                    f"{self.setter_failure_text('调谐')}",
                    suggestion="确认扫频区间在设备可调范围内，或先用 get_status 查看可调上下限。",
                )
            state["last_center"] = center_hz
            iq = self._backend.read_samples(8192)
            iq = np.asarray(iq, dtype=np.complex128)
            window = np.hanning(iq.size)
            sp = np.fft.fft(iq * window)
            mag = np.abs(sp) / iq.size
            psd_db = 20.0 * np.log10(mag + 1e-12)
            freqs = np.fft.fftfreq(iq.size, d=1.0 / self._sample_rate) + center_hz
            return freqs, psd_db

        scanner.psd_provider = psd_provider

        # 扫频逐点改的是共享设备频率。结束时必须把调谐还原，否则后台
        # 扫描会把 get_status().frequency_hz 留在最后一个频点上，用户
        # 明明没调台却看到换台（见 test_headless_smoke）。
        original_freq = self.get_frequency()

        def run() -> None:
            try:
                segs = scanner.sweep()
                state["results"] = [s.to_dict() for s in segs]
            except Exception as e:  # pragma: no cover
                state["error"] = str(e)
            finally:
                try:
                    last_center = float(state.get("last_center") or 0.0)
                    # 只在频率仍是扫描留下的那个点时还原，别覆盖扫描期间
                    # 用户自己的调谐；走 set_frequency 同步本地缓存。
                    if last_center and abs(self.get_frequency() - last_center) < 1.0:
                        self.set_frequency(original_freq)
                except Exception as e:  # pragma: no cover
                    logger.warning("扫频后恢复中心频率失败: %s", e)
                state["done"] = True

        threading.Thread(target=run, name=f"scan-{handle}", daemon=True).start()
        self._scans[handle] = state
        return handle

    def stop_scan(self, handle: str) -> bool:
        """请求停止一次扫描。句柄不存在返回 False（不再静默成功）。"""
        st = self._scans.get(handle)
        if st is None:
            return False
        st["stop"].set()
        return True

    def has_scan(self, handle: str) -> bool:
        return handle in self._scans

    def get_scan_results(self, handle: str) -> List[dict]:
        """获取扫描结果（活动段列表）。未完成时返回当前已收集结果。"""
        st = self._scans.get(handle)
        if st is None:
            return []
        return list(st["results"])

    def _stop_all_scans(self) -> None:
        for st in self._scans.values():
            st["stop"].set()
        self._scans.clear()

    # ==================================================================
    # 解码
    # ==================================================================
    def _record_decode_hits(self, decoder: str, messages: List[Any]) -> None:
        """把一批解码结果里的真实命中聚合后记进观测时间轴。

        同一解码器在 :data:`DECODE_HIT_MIN_INTERVAL_S` 内只发一条
        ``decode_hit``（``count`` 为累计条数，``first`` 为首条真实报文）；
        首条立刻发，间隔内的其余命中留待下次或 :meth:`_flush_decode_hits`。
        没有真实命中就什么都不记——解码器自报的「未命中」不是观测事件。
        """
        hits = [m for m in messages if _decode_message_is_hit(m)]
        if not hits:
            return
        now = time.monotonic()
        detail: Optional[Dict[str, Any]] = None
        with self._decode_hit_lock:
            state = self._decode_hit_pending.get(decoder)
            if state is None:
                state = {"pending": 0, "last_emit": 0.0, "first": None}
                self._decode_hit_pending[decoder] = state
            state["pending"] = int(state["pending"]) + len(hits)
            if state["first"] is None:
                state["first"] = hits[0]
            if (not state["last_emit"]
                    or now - float(state["last_emit"]) >= DECODE_HIT_MIN_INTERVAL_S):
                detail = {"decoder": decoder, "count": int(state["pending"]),
                          "first": _brief_message(state["first"])}
                state["pending"] = 0
                state["first"] = None
                state["last_emit"] = now
        if detail is not None:
            self.note_observation_event("decode_hit", detail)

    def _flush_decode_hits(self) -> None:
        """把各解码器尚未到发送间隔的命中一次性发出（停止录制前调用）。

        锁内取快照、锁外发事件，避免与 :meth:`note_observation_event` 的
        事件锁交叉等待；``count`` 是该解码器累计未发的条数。
        """
        with self._decode_hit_lock:
            pending = self._decode_hit_pending
            self._decode_hit_pending = {}
            snapshots = [
                {"decoder": decoder, "count": int(state["pending"]),
                 "first": (state["first"] if isinstance(state["first"], dict)
                           else None)}
                for decoder, state in pending.items()
                if int(state.get("pending", 0)) > 0
            ]
        for detail in snapshots:
            self.note_observation_event("decode_hit", {
                "decoder": detail["decoder"], "count": detail["count"],
                "first": _brief_message(detail["first"]) if detail["first"] else {},
            })

    def start_decoder(self, decoder_type: str, params: Optional[dict] = None) -> str:
        """启动一个流式后台解码器（仅 adsb/rds），返回句柄。

        Raises
        ------
        DecoderNotAvailableError
            未知解码器类型。
        DecoderNotStreamableError
            类型存在文件级实现，但没有流式 IQ 入口。
        """
        dt = (decoder_type or "").lower()
        if dt not in SUPPORTED_DECODERS:
            raise DecoderNotAvailableError(
                f"未知解码器 {decoder_type!r}",
                suggestion=f"可用：{', '.join(SUPPORTED_DECODERS)}",
            )
        if dt not in STREAMABLE_DECODERS:
            raise DecoderNotStreamableError(
                f"{dt} 是文件级解码器，不支持流式 IQ 解码",
                suggestion=(
                    f"请用 --decode-file <文件> --mode {dt} 走文件级入口；"
                    f"流式解码仅支持：{', '.join(STREAMABLE_DECODERS)}"
                ),
            )
        self._dec_counter += 1
        handle = f"dec-{self._dec_counter:04d}"
        state: Dict[str, Any] = {
            "type": dt,
            "params": dict(params or {}),
            "messages": [],
            "stop": threading.Event(),
        }

        def run() -> None:
            # 懒加载具体解码器函数（缺失时静默空跑，不崩）
            decode_fn = self._resolve_decode_fn(dt)
            while not state["stop"].is_set():
                if not self._connected:
                    state["stop"].wait(0.2)
                    continue
                try:
                    iq = self._read_raw_iq(8192)
                    # 录制中的流式解码与录制泵共用同一条设备读流：解码线程
                    # 每读一块，录音文件就少一块。必须计数并在档案里写明，
                    # 否则「录了 16 s 只有 3.5 s 样本」会被当成设备丢包。
                    if iq is not None and self._recorder is not None:
                        try:
                            with self._decoder_samples_lock:
                                self._decoder_samples_read += int(iq.size)
                        except (TypeError, ValueError):
                            pass
                    if iq is not None and decode_fn is not None:
                        msgs = decode_fn(iq, self._sample_rate, state["params"])
                        if isinstance(msgs, dict):
                            msgs = [msgs]
                        if isinstance(msgs, list):
                            for m in msgs:
                                if isinstance(m, dict):
                                    state["messages"].append(m)
                            self._record_decode_hits(dt, msgs)
                except Exception as e:  # pragma: no cover
                    logger.debug("decoder %s 块处理异常: %s", dt, e)
                state["stop"].wait(0.05)

        threading.Thread(target=run, name=f"dec-{handle}", daemon=True).start()
        self._decoders[handle] = state
        return handle

    @staticmethod
    def _resolve_decode_fn(dt: str) -> Optional[Callable]:
        """按类型懒加载解码器函数；缺失/无流式函数返回 None（不崩，队列空）。

        只有 IQ 流式解码器在这里接；文件级/批处理解码器（ft8/wspr/aprs/
        noaa_apt/sstv/pocsag/acars…，见 decoders.py）没有流式入口，流式场景
        诚实空跑——文件级请走 CLI --decode-file（解锁 decode_digital_mode 全家）。
        """
        try:
            if dt == "adsb":
                from ..adsb_lite import decode_adsb  # type: ignore

                return decode_adsb
            if dt == "rds":
                from ..rds_lite import decode_rds  # type: ignore

                return decode_rds
        except Exception:
            return None
        return None

    def stop_decoder(self, handle: str) -> bool:
        """停止解码器并释放句柄。句柄不存在返回 False（不再静默成功）。"""
        st = self._decoders.pop(handle, None)
        if st is None:
            return False
        st["stop"].set()
        return True

    def has_decoder(self, handle: str) -> bool:
        return handle in self._decoders

    def get_decoder_messages(self, handle: str) -> List[dict]:
        """取走解码器累积的报文（队列被清空）。无报文返回 []。"""
        st = self._decoders.get(handle)
        if st is None:
            return []
        out = list(st["messages"])
        st["messages"].clear()
        return out

    def _stop_all_decoders(self) -> None:
        for st in self._decoders.values():
            st["stop"].set()
        self._decoders.clear()

    # ==================================================================
    # 卫星
    # ==================================================================
    def set_ground_station(self, lat: float, lon: float, alt: float = 0.0,
                           source: str = "user",
                           accuracy_m: Optional[float] = None) -> None:
        """设置地面站位置（经纬度度、海拔米）。

        ``source`` 必须如实标（``user`` 手填 / ``gnss`` 定位 / ``config`` 配置）；
        不传来源视为用户手填。``accuracy_m`` 可选定位精度元数据。
        """
        self._gs_lat = float(lat)
        self._gs_lon = float(lon)
        self._gs_alt = float(alt)
        self._gs_source = str(source or "user")
        self._gs_accuracy_m = None if accuracy_m is None else float(accuracy_m)

    def set_ground_station_from_gnss(self, lat: float, lon: float,
                                     alt: float = 0.0,
                                     accuracy_m: Optional[float] = None) -> None:
        """GNSS 定位写入站址（优先级最高，覆盖手填/配置）。"""
        self.set_ground_station(lat, lon, alt, source="gnss",
                                accuracy_m=accuracy_m)

    def load_ground_station_from_config(self, path: Optional[str] = None) -> bool:
        """从 ``~/.mbdsdr/config.json`` 载入站址；未配置返回 False（不猜城市）。"""
        from ..station import station_from_config
        fix = station_from_config(path)
        if fix.lat is None or fix.lon is None:
            return False
        self.set_ground_station(fix.lat, fix.lon, fix.alt_m,
                                source="config", accuracy_m=fix.accuracy_m)
        return True

    def _station_fix(self):
        """取当前站址数据（不含任何可信度判定，判定统一在 station.py）。"""
        from ..station import StationFix
        return StationFix(self._gs_lat, self._gs_lon, self._gs_alt,
                         source=self._gs_source or "none",
                         accuracy_m=self._gs_accuracy_m)

    def station_status(self) -> dict:
        """当前站址可信度（来源、海陆、不可信原因）。

        严格（写/控制）口径：显式 (0,0)/海上坐标仍拒绝。对外语义不变。
        """
        from ..station import evaluate_station
        return evaluate_station(self._station_fix())

    def station_status_for_calculation(self) -> dict:
        """只读/计算口径的站址可信度（显式 (0,0)/海上坐标降为 warning）。"""
        from ..station import trust_for_calculation
        return trust_for_calculation(self._station_fix())

    def list_satellites(self) -> List[SatInfo]:
        """列出内置可接收卫星（NORAD 编号 + 下行频率）。"""
        out: List[SatInfo] = []
        try:
            from ..decoders import SATELLITE_CATNR, SATELLITE_FREQUENCIES

            for name, norad in SATELLITE_CATNR.items():
                out.append(
                    SatInfo(
                        name=name,
                        norad=int(norad),
                        downlink_mhz=float(SATELLITE_FREQUENCIES.get(name, 0.0)),
                    )
                )
        except Exception as e:  # pragma: no cover
            logger.debug("list_satellites 失败: %s", e)
        return out

    def _norad_to_name(self, norad: int) -> Optional[str]:
        try:
            from ..decoders import SATELLITE_CATNR

            for name, n in SATELLITE_CATNR.items():
                if int(n) == int(norad):
                    return name
        except Exception as e:  # pragma: no cover - 查表失败要留痕，不能假装"不在表中"
            logger.warning("_norad_to_name 查表失败: %s", e)
        # P10：decoders 的旧映射可能含已校正的编号（44016/54234），补上真实编号
        try:
            from ..orbit import BUILTIN_SATS

            for name, n in BUILTIN_SATS.items():
                if int(n) == int(norad):
                    return name
        except Exception as e:  # pragma: no cover
            logger.warning("_norad_to_name 内置表查询失败: %s", e)
        return None

    def get_satellite_pass(self, norad: int,
                           min_elevation_deg: Optional[float] = None,
                           window_hours: Optional[float] = None) -> dict:
        """预报某卫星在指定窗口内最近一次过境。

        真实数据优先：先用缓存/在线 TLE（含编号↔名称↔来源校验）；身份不符
        直接拒绝（绝不拿错轨道算过境）。取新失败时沿用旧缓存并标
        ``refresh_status="not_updated"``；完全无数据返回 ``error="no_tle"`` 与
        「无 TLE，无法预报」——仓内不再内置合成 TLE 回退。

        ``min_elevation_deg`` / ``window_hours`` 为**显式注入**的筛选门与搜索
        窗口：桌面侧可读同源设置 ``settings.effective_pass_min_elevation_deg()``
        / ``effective_pass_window_hours()`` 后传入，MCP 工具面由调用方传参。
        控制器属 AI 侧、无 desktop settings 依赖，因此这里不读设置，也不写
        第二份默认值：缺省回落 ``sat_passes`` 的 ``PASS_MIN_ALT_DEG`` 与
        ``PASS_WINDOW_HOURS``。

        Returns
        -------
        dict
            :class:`PassInfo` 序列化，另带 ``passes_in_window``（本窗口内符合
            该峰值门的总条数）与生效的 ``min_elevation_deg`` / ``window_hours``；
            轨道库缺失/无过境时返回
            ``{"error": "not_available"|"no_pass"|"identity_mismatch"}``。
        """
        st = self.station_status_for_calculation()
        if not st["trusted"]:
            # P11 红线：站址缺失/缺省（真没配）时拒绝算过境，
            # 不许拿默认城市坐标冒充用户位置。显式 (0,0)/海上坐标已按
            # 计算口径放行，并带 station_warning 透传给调用方。
            return {
                "error": "station_untrusted",
                "station": st["fix"],
                "reasons": st["reasons"],
                "suggestion": ("未获得可信站址（GNSS 定位或用户手填坐标）。"
                               "拒绝用不可信站址计算过境；请先用 GNSS 定位或"
                               "set_ground_station 显式设置经纬度。"),
            }
        name = self._norad_to_name(norad)
        if name is None:
            return {"error": "not_available", "suggestion": "该 NORAD 编号不在内置表中。"}
        try:
            from ..decoders import BUILTIN_TLE
            from ..sat_passes import (
                GroundStation,
                PASS_MIN_ALT_DEG,
                PASS_WINDOW_HOURS,
                grid_manifest,
                predict_passes,
            )
            from ..orbit import fetch_tle_record, tle_epoch_iso
            from ..tle_integrity import accuracy, freshness, verify_identity

            line1 = line2 = ""
            fetched_at = None
            refresh_status = "builtin"
            verified = False
            tle_source = "builtin_table（内置 TLE 回退表，decoders.BUILTIN_TLE）"
            fallback_reason = ""
            try:
                rec = fetch_tle_record(int(norad))
                ident = verify_identity(name, rec.get("name") or "")
                if ident["status"] == "mismatch":
                    return {
                        "error": "identity_mismatch",
                        "satellite": name,
                        "catnr": int(norad),
                        "declared_name": name,
                        "object_name": rec.get("name"),
                        "suggestion": (
                            "目录编号与缓存对象名不符（编号指向另一颗/另一类对象），"
                            "已拒绝用错误轨道算过境；请先修正目录映射或刷新缓存。"),
                    }
                line1, line2 = rec["line1"], rec["line2"]
                fetched_at = rec.get("fetched_at")
                refresh_status = rec.get("refresh_status", "unknown")
                verified = ident["status"] == "verified"
                tle_source = ("tle_cache/celestrak（真实 TLE；内置 builtin 表"
                              "提供编号↔名称映射）")
            except Exception as e:  # noqa: BLE001 - 无真实数据 → 诚实回退并标注
                fallback_reason = str(e)
            if not line1:
                builtin = BUILTIN_TLE.get(name)
                if builtin is None:
                    # 仓内已无合成 TLE：无真实来源就诚实空态，不拿占位值算过境。
                    return {
                        "error": "no_tle",
                        "satellite": name,
                        "catnr": int(norad),
                        "suggestion": ("无 TLE，无法预报：在线/缓存均未取到该星"
                                       "轨道根数，且仓内不再内置合成 TLE。"
                                       "请联网刷新或导入用户 TLE 文件。"),
                        "tle_source": "none（无内置合成 TLE）",
                    }
                line1, line2 = builtin
                refresh_status = (f"not_updated（{fallback_reason}）"
                                  if fallback_reason else "builtin_fallback")
                tle_source = ("builtin_table（内置合成 TLE 回退，"
                              "decoders.BUILTIN_TLE；未核实，不可用于跟踪）")
            fresh = freshness(line1, fetched_at)
            acc = accuracy(fresh)
            if not verified:
                acc = dict(acc, risk="unusable", time_precision_s=60,
                           note=("TLE 身份未核实（内置合成回退），"
                                 "过境时刻仅作占位参考"))
            if refresh_status.startswith("not_updated"):
                acc = dict(acc, note=acc["note"] + "；TLE 未更新（沿用旧缓存）")
            gs = GroundStation(self._gs_lat, self._gs_lon, self._gs_alt)
            # 与天空视图同一时间源：TimeEngine（模拟时刻优先，未穿越即墙钟）
            from ..new_spacetime import get_time_engine
            start_unix = get_time_engine().now_unix()
            # 门/窗口显式入参 → 未注入时回落 sat_passes 的单一事实源常量
            gate_deg = (float(min_elevation_deg)
                        if min_elevation_deg is not None
                        else PASS_MIN_ALT_DEG)
            hours_ahead = (float(window_hours)
                           if window_hours is not None
                           else PASS_WINDOW_HOURS)
            passes = predict_passes([name, line1, line2], gs,
                                    hours=hours_ahead,
                                    min_alt=gate_deg,
                                    start_unix=start_unix)
            if not passes:
                return {"error": "no_pass",
                        "min_elevation_deg": gate_deg,
                        "window_hours": hours_ahead,
                        "suggestion":
                        f"未来 {hours_ahead:g}h 内无高于 {gate_deg:g}° 的过境。"}
            p = passes[0]

            def _fmt(dt) -> str:
                if acc["time_precision_s"] <= 1:
                    return dt.isoformat()
                return dt.strftime("%Y-%m-%dT%H:%M+00:00")

            info = PassInfo(
                name=p.sat_name or name,
                rise_utc=_fmt(p.rise_time.utc_datetime()),
                set_utc=_fmt(p.set_time.utc_datetime()),
                max_alt_time_utc=_fmt(p.max_alt_time.utc_datetime()),
                max_alt_deg=float(p.max_alt),
                duration_s=float(p.duration),
                rise_az_deg=float(p.rise_az),
                set_az_deg=float(p.set_az),
                tle_epoch=tle_epoch_iso(line1),
                tle_source=tle_source,
                tle_verified=verified,
                tle_tier=fresh["tier"],
                tle_age_days=fresh["age_days"],
                refresh_status=refresh_status,
                accuracy_risk=acc["risk"],
                accuracy_note=acc["note"],
                time_precision_s=acc["time_precision_s"],
            )
            out = info.to_dict()
            # 本窗口内符合该峰值门的总条数（对照测试与调用方可直接计数）
            out["passes_in_window"] = len(passes)
            out["min_elevation_deg"] = gate_deg
            out["window_hours"] = hours_ahead
            # 用户看到的时刻必须带来源与精度：time_precision_s 是星历年龄
            # 决定的精度，这里补「采样路径」与采样本身带来的偏差（两者
            # 不是一回事，混着说会让用户以为 60 秒是轨道位置精度）。
            out["pass_time_source"] = (
                f"sat_passes.predict_passes（{grid_manifest()['accuracy_note']}）")
            out["pass_time_sampling_precision_s"] = 1.0
            # 显式 (0,0)/海上坐标被计算口径放行时，把可疑位置原因原样带给
            # 用户，不许静默算完。
            if st.get("station_warning"):
                out["station_warning"] = st["station_warning"]
            # P11：内置 TLE 凑整值必须在数据里标注来源与可信度，不许当精确值。
            try:
                from ..builtin_tle_meta import analyze_tle_provenance
                prov = analyze_tle_provenance(name, line1, line2,
                                              source=tle_source)
                out["tle_provenance"] = prov
                if not prov["usable_for_tracking"]:
                    out["tle_verified"] = False
                    out["accuracy_risk"] = "unusable"
                    prior = str(out.get("accuracy_note") or "").strip()
                    extra = f"{prov['trust_label']}；{prov['note']}"
                    out["accuracy_note"] = (f"{prior}；{extra}" if prior else extra)
                    out["time_precision_s"] = max(60, int(out.get("time_precision_s", 60)))
            except Exception as e:  # pragma: no cover
                out["tle_provenance"] = {"trust": "unverified", "note": str(e)}
            # P11：同一 AOS 时刻三路径 az/el 对拍（过境/天空视图/多普勒），
            # 不一致必须报出，不许各算各的。
            try:
                from ..sat_consistency import check_pass_object
                # 对拍用的升起门必须与本次实际预测门一致，否则注入 5°/15°
                # 时路径 1 的 el 固定 10° 会与天空路径假性不一致
                cons = check_pass_object(p, [line1, line2], gs,
                                         tol_deg=0.5,
                                         threshold_alt_deg=gate_deg)
                out["consistency"] = cons
                if cons.get("checked") and not cons.get("consistent"):
                    out["consistency_warning"] = cons.get("warning", "")
            except Exception as e:  # pragma: no cover - 守卫失败要留痕
                out["consistency"] = {"checked": False, "reason": str(e)}
            # P11：系统时钟跳变（NTP/用户改时间）告警，随过境结果一并返回
            try:
                from ..time_basis import get_clock_guard
                cst = get_clock_guard().observe()
                if cst.get("type") != "ok":
                    st = get_clock_guard().status()
                    out["clock"] = st
                    out["clock_warning"] = st.get("warning", "")
            except Exception:  # pragma: no cover
                pass
            return out
        except Exception as e:
            return {"error": "not_available", "technical_detail": str(e)}

    def tune_satellite(self, norad: int) -> dict:
        """根据当前多普勒计算应调谐频率，并下发给设备。

        Returns
        -------
        dict
            ``{satellite, nominal_freq_hz, tuned_freq_hz, doppler_hz, elevation_deg}``；
            失败返回 ``{"error": ...}``。
        """
        st = self.station_status()
        if not st["trusted"]:
            return {
                "error": "station_untrusted",
                "station": st["fix"],
                "reasons": st["reasons"],
                "suggestion": ("站址不可信，拒绝按错误位置做多普勒调谐；"
                               "请先 GNSS 定位或显式 set_ground_station。"),
            }
        name = self._norad_to_name(norad)
        if name is None:
            return {"error": "not_available", "suggestion": "该 NORAD 编号不在内置表中。"}
        try:
            from ..decoders import (
                compute_doppler_correction,
                SATELLITE_FREQUENCIES,
            )

            nominal = float(SATELLITE_FREQUENCIES.get(name, 0.0)) * 1e6
            if nominal <= 0.0:
                # 链路目录没有这颗星的下行频点（如 FENGYUN 3D：AHRPT 在
                # 1698–1710 MHz L 波段，不在收听面内）——如实拒绝，绝不调 0。
                return {
                    "error": "no_downlink_known",
                    "satellite": name,
                    "suggestion": "链路目录 sat_links 没有该星下行频点，"
                                  "先 sat_link_get 查证再手动调谐。",
                }
            res = compute_doppler_correction(
                name, nominal, self._gs_lat, self._gs_lon, self._gs_alt
            )
            if "error" in res:
                return res
            tuned = float(res.get("corrected_freq_mhz", 0.0)) * 1e6
            self.set_frequency(tuned)
            return {
                "satellite": name,
                "norad": int(norad),
                "nominal_freq_hz": nominal,
                "tuned_freq_hz": tuned,
                "doppler_hz": float(res.get("doppler_shift_hz", 0.0)),
                "elevation_deg": float(res.get("elevation_deg", 0.0)),
            }
        except Exception as e:
            return {"error": "not_available", "technical_detail": str(e)}

    # ==================================================================
    # 书签
    # ==================================================================
    def _get_bookmarks(self) -> Any:
        if self._bookmarks is None:
            from ..bookmark_manager import BookmarkManager

            try:
                self._bookmarks = BookmarkManager(self._config_dir)
            except Exception as e:
                # 现有书签文件损坏/格式不兼容 -> 用临时目录全新开始，不崩
                logger.warning("书签目录初始化失败，降级到临时目录: %s", e)
                import tempfile

                self._bookmarks = BookmarkManager(
                    tempfile.mkdtemp(prefix="mbdsdr-bookmarks-")
                )
        return self._bookmarks

    def add_bookmark(self, freq: float, name: str, mode: str = "FM",
                     group: str = "") -> dict:
        """添加书签。"""
        from ..bookmark_manager import Bookmark

        bm = self._get_bookmarks()
        b = Bookmark(
            frequency_hz=int(freq), name=name, modulation=mode, group=group,
        )
        bm.add(b)
        return {"id": b.frequency_hz, "frequency_hz": b.frequency_hz,
                "name": b.name, "mode": b.modulation, "group": b.group}

    def list_bookmarks(self, group: Optional[str] = None) -> List[dict]:
        """列出书签；group 给定时过滤该分组。"""
        bm = self._get_bookmarks()
        items = bm.all()
        if group is not None:
            items = [b for b in items if (b.group or "") == group]
        return [
            {"id": b.frequency_hz, "frequency_hz": b.frequency_hz, "name": b.name,
             "mode": b.modulation, "bandwidth_hz": b.bandwidth_hz, "group": b.group}
            for b in items
        ]

    def delete_bookmark(self, id: int) -> bool:
        """按频率 id 删除书签。"""
        return bool(self._get_bookmarks().remove(int(id)))

    def find_nearest_bookmark(self, freq: float) -> Optional[dict]:
        """找离给定频率最近的书签。无书签返回 None。"""
        items = self._get_bookmarks().all()
        if not items:
            return None
        best = min(items, key=lambda b: abs(b.frequency_hz - float(freq)))
        return {"id": best.frequency_hz, "frequency_hz": best.frequency_hz,
                "name": best.name, "mode": best.modulation, "group": best.group}

    def import_bookmarks(self, path: str) -> int:
        """从 CSV 文件导入书签，返回导入条数。"""
        from pathlib import Path

        text = Path(path).read_text(encoding="utf-8")
        return int(self._get_bookmarks().import_csv_text(text))

    def export_bookmarks(self, path: str) -> str:
        """导出书签为 CSV，返回写出的路径。"""
        from pathlib import Path

        text = self._get_bookmarks().export_csv_text()
        Path(path).write_text(text, encoding="utf-8")
        return str(path)

    # ==================================================================
    # 录制
    # ==================================================================
    def note_observation_event(self, kind: str,
                               detail: Optional[Dict[str, Any]] = None) -> bool:
        """记一条观测时间轴事件；返回是否真的记下。

        只在录制中有效：没在录制就不记任何东西——「观测中途事件」属于某
        次真实录制，脱离录制单独记录等于编造观测过程。超过
        :data:`MAX_TIMELINE_EVENTS` 时第一次追加一条 ``timeline_truncated``
        并返回 False，后续超限事件不再进入时间轴。
        """
        if self._recorder is None:
            return False
        k = str(kind or "").strip()
        if not k:
            return False
        event = make_event(k, dict(detail or {}))
        # 事件落点用「此刻已写入的样本数」，不用墙上时钟 × 采样率：录制期间
        # 若有别的消费者（如流式解码线程）也在读设备，样本会被分流，墙上时钟
        # 换算出的偏移会比文件实际长度大。样本索引才是回放时能直接 seek 的量。
        try:
            event["samples"] = int(getattr(self._recorder, "n_samples", 0))
        except (TypeError, ValueError):
            pass
        with self._recording_events_lock:
            if len(self._recording_events) >= MAX_TIMELINE_EVENTS:
                if not self._recording_events_dropped:
                    self._recording_events.append(make_event(
                        "timeline_truncated",
                        {"max_events": int(MAX_TIMELINE_EVENTS)}))
                self._recording_events_dropped += 1
                return False
            self._recording_events.append(event)
        return True

    def start_recording(self, path: str) -> dict:
        """开始 IQ 录制（SigMF cf32_le）。未连接返回错误 dict。"""
        if not self._connected:
            return {"error": "not_connected",
                    "suggestion": "请先 connect() 再录制。"}
        try:
            from ..recorder import IQRecorder

            # driver 写进 SigMF meta，观测档案才有设备来源可查。
            # controller 没有单独的 _driver 字段，用 connect 时记下的
            # ``driver:serial`` 入口标识解析（debug 源不在其列，留空）。
            _dev_id = str(getattr(self, "_last_device_id", "") or "")
            _driver = _dev_id.split(":", 1)[0] if ":" in _dev_id else ""
            rec = IQRecorder(
                path,
                sample_rate=self._sample_rate,
                center_freq_hz=self._freq_hz,
                gain_db=self._gain_db,
                device=self._device_name,
                driver=_driver,
            )
            if not rec.open():
                # 旧实现把所有打不开的情形一律写成"无法打开 {path} 写入"：
                # 磁盘满 / 无权限 / 同名证据会相互冒充，AI 与 CLI 拿不到
                # 可操作原因。现在把录制器分类后的真实原因透传。
                return {"error": "open_failed",
                        "suggestion": rec.last_error
                        or f"无法打开 {path} 写入。",
                        "error_code": rec.error_code}
        except Exception as e:
            return {"error": "not_available", "technical_detail": str(e)}

        self._recorder = rec
        self._recording_path = rec.data_path
        self._recorder_stop.clear()
        # 新一轮录制从空时间轴开始：上一轮的事件不能混进这次观测。
        with self._recording_events_lock:
            self._recording_events = []
            self._recording_events_dropped = 0
        # 解码命中的未发聚合同样清零：录制开始前解码线程攒下的条数如果留着，
        # 会被算进本次观测的第一条 decode_hit，等于给档案多记几条没发生的命中。
        with self._decode_hit_lock:
            self._decode_hit_pending = {}
        # 分流计数按新一轮录制重新累计。
        with self._decoder_samples_lock:
            self._decoder_samples_read = 0
        # 时空快照必须在录制**开始**时定格：结束再取会得到「设备已断开、
        # GNSS 已掉 fix」的空值（2026-11-20 真机实测：GNSS 15 星定位在录，
        # 录 2 s 后取快照变成 site.source=none）。
        try:
            from ..runtime_sources import get_runtime_sources
            self._recording_snapshot = get_runtime_sources().snapshot()
        except Exception:  # noqa: BLE001 - 快照失败不阻止录制
            self._recording_snapshot = None
        # 时间轴起点：频率/采样率/增益取「开始录制那一刻」的值，之后改了也不回写。
        # 统一用 make_event 生成 utc（微秒精度 + +00:00），与后续事件同格式——
        # 时间轴按 utc 字符串排序，混合精度（Z 截秒）会把 start 排到中间。
        try:
            self._recording_start_event = make_event("record_start", {
                "center_freq_hz": float(self._freq_hz),
                "sample_rate_hz": float(self._sample_rate),
                "gain_db": float(self._gain_db),
            })
            # 起点就是文件第 0 个样本；显式写出来，annotations 才有确定的锚点。
            self._recording_start_event["samples"] = 0
        except Exception:  # noqa: BLE001 - 时间轴是附带信息
            self._recording_start_event = None
        if self._recording_start_event is not None:
            with self._recording_events_lock:
                self._recording_events.append(self._recording_start_event)

        # 读空检测状态：空读起点/次数/原因。只由 pump 线程读写，用 nonlocal
        # 维护，不新增线程。
        gap_started: Optional[float] = None
        gap_empty_reads = 0
        gap_reason: Optional[Dict[str, Any]] = None

        def pump() -> None:
            nonlocal gap_started, gap_empty_reads, gap_reason
            while not self._recorder_stop.is_set():
                iq = self._read_raw_iq(8192)
                empty = iq is None or getattr(iq, "size", 0) == 0
                if empty:
                    if gap_started is None:
                        gap_started = time.monotonic()
                        gap_empty_reads = 0
                        reason = getattr(self._backend, "last_read_reason", None)
                        gap_reason = reason if isinstance(reason, dict) else None
                    gap_empty_reads += 1
                else:
                    if gap_started is not None:
                        self.note_observation_event("read_gap", {
                            "duration_s": round(time.monotonic() - gap_started, 3),
                            "empty_reads": gap_empty_reads,
                            "reason": gap_reason,
                        })
                        gap_started = None
                        gap_empty_reads = 0
                        gap_reason = None
                    # stop 已置位就不写：有界读可能刚好在 stop 之后返回，
                    # 晚到的块不能写进已 close() 的录制器（否则写已关闭文件）。
                    if not self._recorder_stop.is_set():
                        rec.write(iq)
                # 真机有界读本身按采样率阻塞；再固定退避 5 ms 会在 2.4 MS/s
                # 下丢样本（2026-11-22 真机实测：2.5 s 墙钟只录到 0.53 s）。
                # 只有空读或调试源（读不阻塞）才退避，避免忙转。
                delay = _pump_backoff_seconds(
                    empty, bool(getattr(self, "_is_debug", False)))
                if delay > 0:
                    self._recorder_stop.wait(delay)

        self._recorder_thread = threading.Thread(
            target=pump, name="recorder-pump", daemon=True
        )
        self._recording_start_mono = time.monotonic()
        self._recording_sample_rate = float(self._sample_rate)
        self._recorder_thread.start()
        return {"recording": True, "path": rec.data_path}

    def stop_recording(self) -> dict:
        """停止录制，落 SigMF meta。"""
        self._recorder_stop.set()
        t = self._recorder_thread
        if t is not None:
            t.join(timeout=1.0)
        self._recorder_thread = None
        rec = self._recorder
        # 最后不到一个间隔的命中必须在取事件快照之前发出，否则随录制结束丢失。
        self._flush_decode_hits()
        # 事件快照必须在 close() **之前**取：close 落 annotations 之后，
        # 录制器进入收尾状态，时间轴也不该再被 pump/解码线程改动。
        with self._recording_events_lock:
            events = list(self._recording_events)
            self._recording_events = []
            self._recording_events_dropped = 0
        meta = None
        if rec is not None:
            try:
                rec.set_annotations(self._sigmf_annotations(events))
            except Exception:  # noqa: BLE001 - 注释是附带信息，不拖垮收尾
                logger.warning("写入 SigMF annotations 失败", exc_info=True)
            try:
                meta = rec.close()
            except Exception:  # pragma: no cover
                meta = None
        n = getattr(rec, "n_samples", 0) if rec is not None else 0
        # 墙钟对账：读成功但吞吐不足时没有 read_gap 事件，必须在这里发现。
        wall_s = (time.monotonic() - self._recording_start_mono
                  if self._recording_start_mono is not None else None)
        self._recording_start_mono = None
        rate = self._recording_sample_rate or self._sample_rate
        self._recording_sample_rate = 0.0
        with self._decoder_samples_lock:
            shared = int(self._decoder_samples_read)
        shortfall = _sample_shortfall(wall_s, rate, int(n) + shared)
        self._recorder = None
        self._recording_path = None
        snapshot = self._recording_snapshot
        self._recording_snapshot = None
        start_event = self._recording_start_event
        self._recording_start_event = None
        # 时间轴 = 录制期间累计的事件。只有「确实开始了」且「确实落了 meta」
        # 时才补 record_stop，避免给一次失败的录制编出一段不存在的观测过程。
        timeline: List[Dict[str, Any]] = events
        if start_event and meta:
            try:
                if shortfall:
                    loss_event = make_event("sample_loss", shortfall)
                    loss_event["samples"] = int(n)
                    timeline.append(loss_event)
                stop_event = make_event("record_stop", {"samples": int(n)})
                stop_event["samples"] = int(n)
                timeline.append(stop_event)
            except Exception:  # noqa: BLE001 - 时间轴是附带信息
                pass
        result: Dict[str, Any] = {
            "recording": False, "samples": int(n), "meta_path": meta}
        if shortfall:
            result["sample_loss"] = shortfall
        # 录制期间被流式解码线程分流的样本数：录音文件里没有它们，档案必须写明，
        # 否则「录了 16 s 只有 3.5 s 样本」会被误读成设备丢包。
        if shared > 0:
            result["decoder_shared_samples"] = shared
        # 录制是「一次观测」的主产物；停止成功后顺手落一份观测档案（P1-08）。
        # 桌面 / CLI / MCP 三个入口都走这里，所以只需接线一处，三端拿到同一份。
        # 档案失败不影响录制结论——但必须在返回值里如实报出，不许静默。
        if meta:
            obs_path, obs_err = self._write_observation_record(
                meta, snapshot=snapshot, timeline=timeline,
                extra_notes=([f"录制期间流式解码线程分流了 {shared} 个样本，"
                              "未写入录音文件"] if shared > 0 else None),
                extra_result=({"decoder_shared_samples": shared}
                              if shared > 0 else None))
            if obs_path:
                result["observation_record"] = obs_path
            if obs_err:
                result["observation_error"] = obs_err
        return result

    def _write_observation_record(self, meta_path: str, snapshot=None,
                                  timeline=None, extra_notes=None,
                                  extra_result=None):
        """把刚结束的录制落成观测档案；返回 ``(path, error)``，二选一非空。

        不完整录制（0 样本 / 写入失败）**不生成档案**——按 P1-08「无产物拒绝
        导出」的口径，档案不能把「什么都没录到」包装成一次观测。

        ``snapshot`` 应传录制**开始时**的运行时快照；省略时退回当前快照。
        观测的时空属性属于「观测发生的那一刻」，不属于「停止按钮按下的那一刻」。
        """
        try:
            import uuid

            from ..observation_record import (
                product_from_sigmf,
                record_from_snapshot,
            )
            from ..runtime_sources import get_runtime_sources

            prod = product_from_sigmf(meta_path)
            if not prod.get("complete"):
                reason = (prod.get("incomplete_reason")
                          or "录制不完整（没有可用样本），不生成观测档案")
                return None, reason

            snap = (snapshot if snapshot is not None
                    else get_runtime_sources().snapshot())
            notes = (["时间/站址取自观测开始时刻的快照"]
                     if snapshot is not None else [])
            if extra_notes:
                notes.extend(str(x) for x in extra_notes if str(x or "").strip())
            result: Dict[str, Any] = {
                "summary": f"录制 {prod.get('samples')} 个 IQ 样本"}
            if extra_result:
                result.update(extra_result)
            rec = record_from_snapshot(
                f"obs-{uuid.uuid4().hex[:12]}",
                snap,
                started_utc=prod.get("started_utc"),
                tuning={
                    "center_freq_hz": prod.get("center_freq_hz"),
                    "sample_rate_hz": prod.get("sample_rate_hz"),
                    "gain_db": prod.get("gain_db"),
                    "doppler_residual_hz": None,
                },
                products=[prod],
                result=result,
                evidence="real_hw",
                notes=notes,
                timeline=timeline,
            )
            path = os.path.splitext(meta_path)[0] + ".observation.json"
            ok, err = rec.write_json(path)
            return (path, None) if ok else (None, err)
        except Exception as exc:  # noqa: BLE001 - 档案是附带产物，不许拖垮录制
            logger.warning("观测档案写入失败: %s: %s", type(exc).__name__, exc)
            return None, f"{type(exc).__name__}: {exc}"

    def _sigmf_annotations(self, events) -> List[Dict[str, Any]]:
        """把时间轴事件映射成 SigMF annotations（core:sample_start 按录制起点换算）。

        起点用录制器 ``open()`` 时记下的 ``_start_time``，采样率用录制器
        当时的 ``sample_rate``；任缺或采样率非正就返回空列表——没有可靠的
        时间基准时宁可不写，也不把事件挂到一个猜出来的样本偏移上。
        """
        rec = self._recorder
        if rec is None:
            return []
        start = _parse_iso_utc(getattr(rec, "_start_time", None))
        if start is None:
            return []
        try:
            sample_rate = float(getattr(rec, "sample_rate", 0.0) or 0.0)
        except (TypeError, ValueError):
            return []
        if sample_rate <= 0:
            return []
        try:
            total = int(getattr(rec, "n_samples", 0) or 0)
        except (TypeError, ValueError):
            total = 0
        out: List[Dict[str, Any]] = []
        for event in events or []:
            if not isinstance(event, dict):
                continue
            kind = str(event.get("kind") or "")
            detail = event.get("detail")
            # 优先用事件落点样本数（录制期间由 note_observation_event 记下）：
            # 录制中若样本被别的消费者分流，墙上时钟换算会超出文件实际长度。
            offset = event.get("samples")
            if isinstance(offset, int) and offset >= 0:
                sample_start = min(offset, total)
            else:
                stamp = _parse_iso_utc(event.get("utc"))
                if stamp is None:
                    continue
                seconds = (stamp - start).total_seconds()
                sample_start = int(round(seconds * sample_rate))
                sample_start = max(0, min(sample_start, total))
            try:
                comment = json.dumps(detail, ensure_ascii=False)
            except (TypeError, ValueError):
                comment = json.dumps(str(detail), ensure_ascii=False)
            out.append({
                "core:sample_start": sample_start,
                "core:label": kind,
                "core:comment": comment,
                "mbdsdr:utc": event.get("utc"),
                "mbdsdr:kind": kind,
            })
        return out

    def get_recording_status(self) -> dict:
        """当前录制状态。"""
        rec = self._recorder
        if rec is None:
            return {"recording": False, "path": None}
        return {
            "recording": True,
            "path": self._recording_path,
            "samples": int(getattr(rec, "n_samples", 0)),
            "duration_s": float(getattr(rec, "duration_s", 0.0)),
        }

    # ==================================================================
    # 服务：远程控制 / Web
    # ==================================================================
    def start_remote_control(self, port: int = 7356) -> dict:
        """启动 GQRX 风格 TCP 远程控制服务（后台线程）。"""
        if self._rc is not None:
            return {"running": True, "port": self._rc.port, "host": self._rc.host}
        try:
            from ..remote_control import RemoteControl

            self._rc = RemoteControl(backend=_RCShim(self), port=int(port))
            self._rc.start()
        except Exception as e:
            return {"error": "not_available", "technical_detail": str(e)}
        return {"running": True, "host": self._rc.host, "port": self._rc.port}

    def stop_remote_control(self) -> None:
        """停止远程控制服务。"""
        if self._rc is not None:
            try:
                self._rc.stop()
            except Exception as e:  # pragma: no cover - 清理路径不崩，但要留痕
                logger.warning("远程控制服务停止失败: %s", e)
            self._rc = None

    def start_web_server(self, port: int = 8000) -> dict:
        """启动轻量 Web 服务器（后台线程）。"""
        if self._web is not None:
            return {"running": True, "port": self._web.port, "host": self._web.host}
        try:
            from ..web.server import WebServer

            self._web = WebServer(port=int(port), backend=self)
            self._web.start()
        except Exception as e:
            return {"error": "not_available", "technical_detail": str(e)}
        return {"running": True, "host": self._web.host, "port": self._web.port}

    def stop_web_server(self) -> None:
        """停止 Web 服务器。"""
        if self._web is not None:
            try:
                self._web.stop()
            except Exception as e:  # pragma: no cover - 清理路径不崩，但要留痕
                logger.warning("Web 服务停止失败: %s", e)
            self._web = None

    def get_service_status(self) -> dict:
        """远程控制 / Web 服务运行状态。"""
        return {
            "remote_control": (
                {"running": True, "host": self._rc.host, "port": self._rc.port}
                if self._rc is not None
                else {"running": False}
            ),
            "web_server": (
                {"running": True, "host": self._web.host, "port": self._web.port}
                if self._web is not None
                else {"running": False}
            ),
        }

    # ==================================================================
    # ANR / 降噪
    # ==================================================================
    def _get_anr(self) -> Any:
        if self._anr is None:
            from ..anr import SpectralSubtractionANR

            self._anr = SpectralSubtractionANR()
        return self._anr

    def set_anr_enabled(self, enabled: bool) -> None:
        """开关自适应降噪。"""
        self._get_anr().enabled = bool(enabled)

    def set_anr_strength(self, level: int) -> None:
        """设置降噪强度 0-10（映射到谱减过减因子）。"""
        lvl = int(max(0, min(10, level)))
        anr = self._get_anr()
        anr.cfg.alpha = 0.5 + lvl * 0.45  # 0.5 .. 5.0

    def learn_noise_floor(self) -> dict:
        """用当前音频学习一次噪声底。"""
        audio = self.read_audio(2048)
        if audio is None:
            return {"error": "not_connected",
                    "suggestion": "请先 connect() 一台设备并 start_audio，"
                                  "再学习噪声底。"}
        try:
            self._get_anr().learn_noise(np.asarray(audio, dtype=np.float64))
            return {"learned": True, "samples": int(audio.size)}
        except Exception as e:
            return {"error": "not_available", "technical_detail": str(e),
                    "suggestion": "检查 ANR 模块依赖是否可用（numpy），"
                                  "或先确认音频正在输出。"}

    # ==================================================================
    # 多 VFO
    # ==================================================================
    def _get_vfos(self) -> Any:
        if self._vfos is None:
            from ..vfo_manager import VfoManager

            self._vfos = VfoManager()
        return self._vfos

    def add_vfo(self, freq: float, mode: str = "FM", bw: float = 12_500.0) -> str:
        """新建一个 VFO，返回其 id。"""
        v = self._get_vfos().add(float(freq), float(bw), mode.upper())
        return v.vfo_id

    def remove_vfo(self, vfo_id: str) -> None:
        """删除 VFO。"""
        self._get_vfos().remove(vfo_id)

    def set_primary_vfo(self, vfo_id: str) -> bool:
        """把某 VFO 设为主听。"""
        return self._get_vfos().set_primary(vfo_id) is not None

    def list_vfos(self) -> List[VfoInfo]:
        """列出全部 VFO。"""
        mgr = self._get_vfos()
        primary_id = mgr.active_vfo_id
        out: List[VfoInfo] = []
        for v in mgr.list_all():
            out.append(
                VfoInfo(
                    vfo_id=v.vfo_id, center_hz=v.center_hz, mode=v.mode,
                    bw_hz=v.bw_hz, primary=(v.vfo_id == primary_id),
                    muted=v.muted,
                )
            )
        return out

    # ==================================================================
    # 运行时数据源快照（Task 70-73）
    # ==================================================================
    def set_gnss_monitor(self, monitor: Any) -> None:
        """显式接入 GNSS 监视器（仅登记引用：不 start、不打开串口）。"""
        self._gnss_monitor = monitor
        self._runtime_sources.set_gnss(monitor)

    def get_runtime_sources(self) -> dict:
        """只读快照：SDR / GNSS / 时间 / 站址来源（不启动硬件）。"""
        return self._runtime_sources.snapshot()

    def sync_runtime_sources(self) -> dict:
        """显式把当前 GNSS fix/UTC 推给时间引擎与站址，返回快照。

        不做后台轮询/自动重连；调用方（UI/CLI/MCP）显式触发。
        """
        return self._runtime_sources.sync_gnss()

    def release_runtime_sources(self, reason: str = "显式释放 GNSS 授时/站址") -> dict:
        """显式释放 GNSS 授时/站址：回退系统时钟与手动/配置站址。"""
        return self._runtime_sources.release(reason=reason)

    # ==================================================================
    # 系统
    # ==================================================================
    def get_status(self) -> SystemStatus:
        """完整系统状态快照（连接/设备/音频/CPU/内存/服务）。"""
        cpu = mem = None
        try:
            import psutil  # type: ignore

            cpu = float(psutil.cpu_percent(interval=0.05))
            mem = float(psutil.virtual_memory().percent)
        except Exception as e:  # psutil 缺失是常见情况，debug 级留痕即可
            logger.debug("psutil 不可用，CPU/内存字段留空: %s", e)
        return SystemStatus(
            connected=self._connected,
            device=self._device_name or None,
            is_debug_source=self._is_debug,
            frequency_hz=self.get_frequency(),
            sample_rate_hz=self.get_sample_rate(),
            demod=self._demod,
            gain_db=self.get_gain(),
            bandwidth_warning=self._bandwidth_warning,
            audio_running=self._audio_running,
            recording=self._recorder is not None,
            remote_control=(
                {"running": True, "port": self._rc.port} if self._rc else {"running": False}
            ),
            web_server=(
                {"running": True, "port": self._web.port} if self._web else {"running": False}
            ),
            cpu_percent=cpu,
            mem_percent=mem,
            runtime_sources=self.get_runtime_sources(),
        )

    def shutdown(self) -> None:
        """优雅关闭：停音频/录制/扫描/解码/服务并断开设备。"""
        # 先停重连，避免断开后又触发一次自动连接。
        self.disable_sdr_autoreconnect()
        self.stop_web_server()
        self.stop_remote_control()
        self.stop_audio()
        self.stop_recording()
        self._stop_all_scans()
        self._stop_all_decoders()
        self.disconnect()


__all__ = [
    "SDRController",
    "AudioDeviceInfo",
    "VfoInfo",
    "SatInfo",
    "PassInfo",
    "SystemStatus",
    "VALID_MODES",
    "SUPPORTED_DECODERS",
    "STREAMABLE_DECODERS",
    "FILE_ONLY_DECODERS",
]
