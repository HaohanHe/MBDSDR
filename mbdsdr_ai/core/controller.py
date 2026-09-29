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

import logging
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from .errors import (
    SDRUserError,
    DriverNotFoundError,
    DeviceNotFoundError,
    NoAudioOutputError,
    SampleRateNotSupportedError,
    FrequencyOutOfRangeError,
    DecoderNotAvailableError,
)

logger = logging.getLogger(__name__)

# 合法解调模式（CLI/MCP 做参数校验用）
VALID_MODES = ("AM", "FM", "WFM", "NFM", "USB", "LSB", "CW")

# 支持的解码器类型
SUPPORTED_DECODERS = ("adsb", "aprs", "noaa_apt", "meteor")

# 音频输出采样率（解调后送声卡的统一速率）
AUDIO_FS = 48_000.0


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
    audio_running: bool = False
    recording: bool = False
    remote_control: Dict[str, Any] = field(default_factory=dict)
    web_server: Dict[str, Any] = field(default_factory=dict)
    cpu_percent: Optional[float] = None
    mem_percent: Optional[float] = None

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
        self._c.set_frequency(float(hz))
        return True

    def get_mode(self) -> str:
        return self._c.get_demod()

    def set_mode(self, mode: str) -> bool:
        self._c.set_demod(mode)
        return True

    def get_gain(self) -> float:
        return self._c._gain_db

    def set_gain(self, db: float) -> bool:
        self._c.set_gain(float(db))
        return True


# ======================================================================
# SDRController
# ======================================================================
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
    """

    def __init__(
        self,
        backend: Any = None,
        config_dir: Optional[str] = None,
    ) -> None:
        self._config_dir = config_dir

        # ---- 接收后端与连接状态 ----
        self._backend: Any = backend
        self._connected: bool = False
        self._device_name: str = ""
        self._is_debug: bool = False

        # ---- 接收参数（控制器侧真值，下发给后端）----
        self._freq_hz: float = 100_000_000.0
        self._sample_rate: float = 2_400_000.0
        self._gain_db: float = 0.0
        self._demod: str = "FM"
        self._bandwidth_hz: float = 12_500.0
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

        # ---- 扫描 / 解码 / 录制句柄 ----
        self._scans: Dict[str, Dict[str, Any]] = {}
        self._decoders: Dict[str, Dict[str, Any]] = {}
        self._scan_counter: int = 0
        self._dec_counter: int = 0
        self._recorder: Any = None
        self._recorder_thread: Optional[threading.Thread] = None
        self._recorder_stop = threading.Event()
        self._recording_path: Optional[str] = None

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

        if device_id in (None, "", "debug", "debug-source", "DebugSignalGenerator"):
            from ..debug_source import DebugSource

            src = DebugSource(sample_rate=self._sample_rate)
            if not src.connect():
                raise DeviceNotFoundError("调试信号源启用失败")
            self._backend = src
            self._is_debug = True
            self._device_name = "DebugSignalGenerator（调试信号源）"
            self._connected = True
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
                suggestion="请插好 USB 天线棒，或使用 connect('debug') 以调试信号源测试。",
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
        except Exception as e:
            raise DeviceNotFoundError(
                f"打开设备 {device_id!r} 失败", technical_detail=e
            ) from e
        self._backend = opened
        self._device_name = target.name
        self._is_debug = False
        self._connected = True
        # 下发当前参数
        try:
            opened.set_sample_rate(self._sample_rate)
            opened.set_frequency(self._freq_hz)
            opened.set_gain(self._gain_db)
        except Exception as e:
            logger.warning("下发初始参数失败: %s", e)
        return True

    def disconnect(self) -> None:
        """断开当前设备，停止所有派生资源（音频/录制/扫描/解码）。"""
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

    def is_connected(self) -> bool:
        """是否已连接后端。"""
        return self._connected

    # ==================================================================
    # 接收参数
    # ==================================================================
    def set_frequency(self, hz: float) -> None:
        """调谐到中心频率（Hz）。未连接时仅记录目标值，不造假。"""
        hz = float(hz)
        self._freq_hz = hz
        if self._connected and self._backend is not None:
            try:
                self._backend.set_frequency(hz)
            except Exception as e:
                logger.warning("set_frequency 失败: %s", e)

    def get_frequency(self) -> float:
        """当前中心频率（Hz）。"""
        return self._freq_hz

    def set_demod(self, mode: str) -> None:
        """设置解调模式（AM/FM/WFM/NFM/USB/LSB/CW）。"""
        m = (mode or "").upper()
        if m not in VALID_MODES:
            raise SDRUserError(
                f"不支持的解调模式 {mode!r}",
                suggestion=f"可用模式：{', '.join(VALID_MODES)}",
            )
        self._demod = m

    def get_demod(self) -> str:
        """当前解调模式。"""
        return self._demod

    def set_bandwidth(self, hz: float) -> None:
        """设置信道带宽（Hz）。"""
        self._bandwidth_hz = float(hz)

    def set_gain(self, db: float) -> None:
        """设置总增益（dB）。"""
        self._gain_db = float(db)
        if self._connected and self._backend is not None:
            try:
                self._backend.set_gain(self._gain_db)
            except Exception as e:
                logger.warning("set_gain 失败: %s", e)

    def set_gain_stage(self, stage: str, db: float) -> None:
        """设置某一增益级（如 'LNA'/'VGA'）。后端不支持时静默记录。"""
        if self._connected and self._backend is not None:
            fn = getattr(self._backend, "set_gain_stage", None)
            if callable(fn):
                try:
                    fn(stage, float(db))
                except Exception as e:
                    logger.warning("set_gain_stage(%s) 失败: %s", stage, e)

    def set_sample_rate(self, sr: float) -> None:
        """设置基带采样率（Hz）。"""
        sr = float(sr)
        self._sample_rate = sr
        if self._connected and self._backend is not None:
            try:
                self._backend.set_sample_rate(sr)
            except Exception as e:
                raise SampleRateNotSupportedError(
                    f"设备不支持采样率 {sr}", technical_detail=e
                ) from e

    def set_squelch(self, db: float) -> None:
        """设置静噪门限（dB，负值）。"""
        self._squelch_db = float(db)

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
                except Exception:
                    pass
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
            except Exception:  # pragma: no cover
                pass

    def set_volume(self, db: float) -> None:
        """设置播放音量（dB，线性映射到 0..5 增益）。"""
        self._volume_db = float(db)
        linear = 10.0 ** (self._volume_db / 20.0)
        if self._player is not None and self._player is not False:
            try:
                self._player.set_gain(linear)
            except Exception:  # pragma: no cover
                pass

    def set_mute(self, muted: bool) -> None:
        """静音 / 取消静音。"""
        self._muted = bool(muted)
        if self._player is not None and self._player is not False:
            try:
                self._player.set_muted(bool(muted))
            except Exception:  # pragma: no cover
                pass

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
            return None
        if arr is None:
            return None
        return np.asarray(arr, dtype=np.complex64)

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
            self._backend.set_frequency(center_hz)
            iq = self._backend.read_samples(8192)
            iq = np.asarray(iq, dtype=np.complex128)
            window = np.hanning(iq.size)
            sp = np.fft.fft(iq * window)
            mag = np.abs(sp) / iq.size
            psd_db = 20.0 * np.log10(mag + 1e-12)
            freqs = np.fft.fftfreq(iq.size, d=1.0 / self._sample_rate) + center_hz
            return freqs, psd_db

        scanner.psd_provider = psd_provider

        def run() -> None:
            try:
                segs = scanner.sweep()
                state["results"] = [s.to_dict() for s in segs]
            except Exception as e:  # pragma: no cover
                state["error"] = str(e)
            finally:
                state["done"] = True

        threading.Thread(target=run, name=f"scan-{handle}", daemon=True).start()
        self._scans[handle] = state
        return handle

    def stop_scan(self, handle: str) -> None:
        """请求停止一次扫描。"""
        st = self._scans.get(handle)
        if st is not None:
            st["stop"].set()

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
    def start_decoder(self, decoder_type: str, params: Optional[dict] = None) -> str:
        """启动一个后台解码器（adsb/aprs/noaa_apt/meteor），返回句柄。

        Raises
        ------
        DecoderNotAvailableError
            未知解码器类型。
        """
        dt = (decoder_type or "").lower()
        if dt not in SUPPORTED_DECODERS:
            raise DecoderNotAvailableError(
                f"未知解码器 {decoder_type!r}",
                suggestion=f"可用：{', '.join(SUPPORTED_DECODERS)}",
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
                    if iq is not None and decode_fn is not None:
                        msgs = decode_fn(iq, self._sample_rate, state["params"])
                        if isinstance(msgs, dict):
                            msgs = [msgs]
                        if isinstance(msgs, list):
                            for m in msgs:
                                if isinstance(m, dict):
                                    state["messages"].append(m)
                except Exception as e:  # pragma: no cover
                    logger.debug("decoder %s 块处理异常: %s", dt, e)
                state["stop"].wait(0.05)

        threading.Thread(target=run, name=f"dec-{handle}", daemon=True).start()
        self._decoders[handle] = state
        return handle

    @staticmethod
    def _resolve_decode_fn(dt: str) -> Optional[Callable]:
        """按类型懒加载解码器函数；缺失返回 None（不崩）。"""
        try:
            if dt == "adsb":
                from ..adsb_lite import decode_adsb  # type: ignore

                return decode_adsb
        except Exception:
            return None
        return None  # 其余类型按 best-effort：无流式函数时队列保持空

    def stop_decoder(self, handle: str) -> None:
        """停止解码器并释放句柄。"""
        st = self._decoders.pop(handle, None)
        if st is not None:
            st["stop"].set()

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
    def set_ground_station(self, lat: float, lon: float, alt: float = 0.0) -> None:
        """设置地面站位置（经纬度度、海拔米）。"""
        self._gs_lat = float(lat)
        self._gs_lon = float(lon)
        self._gs_alt = float(alt)

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
        except Exception:
            pass
        return None

    def get_satellite_pass(self, norad: int) -> dict:
        """预报某卫星未来 24h 最近一次过境。

        Returns
        -------
        dict
            :class:`PassInfo` 序列化；轨道库缺失/无过境时返回
            ``{"error": "not_available"|"no_pass"}``。
        """
        name = self._norad_to_name(norad)
        if name is None:
            return {"error": "not_available", "suggestion": "该 NORAD 编号不在内置表中。"}
        try:
            from ..decoders import BUILTIN_TLE
            from ..sat_passes import GroundStation, predict_passes

            line1, line2 = BUILTIN_TLE[name]
            gs = GroundStation(self._gs_lat, self._gs_lon, self._gs_alt)
            passes = predict_passes([name, line1, line2], gs, hours=24.0, min_alt=10.0)
            if not passes:
                return {"error": "no_pass", "suggestion": "未来 24h 内无高于 10° 的过境。"}
            p = passes[0]
            info = PassInfo(
                name=p.sat_name or name,
                rise_utc=p.rise_time.utc_datetime().isoformat(),
                set_utc=p.set_time.utc_datetime().isoformat(),
                max_alt_time_utc=p.max_alt_time.utc_datetime().isoformat(),
                max_alt_deg=float(p.max_alt),
                duration_s=float(p.duration),
                rise_az_deg=float(p.rise_az),
                set_az_deg=float(p.set_az),
            )
            return info.to_dict()
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
        name = self._norad_to_name(norad)
        if name is None:
            return {"error": "not_available", "suggestion": "该 NORAD 编号不在内置表中。"}
        try:
            from ..decoders import (
                compute_doppler_correction,
                SATELLITE_FREQUENCIES,
            )

            nominal = float(SATELLITE_FREQUENCIES.get(name, 0.0)) * 1e6
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
    def start_recording(self, path: str) -> dict:
        """开始 IQ 录制（SigMF cf32_le）。未连接返回错误 dict。"""
        if not self._connected:
            return {"error": "not_connected",
                    "suggestion": "请先 connect() 再录制。"}
        try:
            from ..recorder import IQRecorder

            rec = IQRecorder(
                path,
                sample_rate=self._sample_rate,
                center_freq_hz=self._freq_hz,
                gain_db=self._gain_db,
                device=self._device_name,
            )
            if not rec.open():
                return {"error": "open_failed", "suggestion": f"无法打开 {path} 写入。"}
        except Exception as e:
            return {"error": "not_available", "technical_detail": str(e)}

        self._recorder = rec
        self._recording_path = rec.data_path
        self._recorder_stop.clear()

        def pump() -> None:
            while not self._recorder_stop.is_set():
                iq = self._read_raw_iq(8192)
                if iq is not None:
                    rec.write(iq)
                self._recorder_stop.wait(0.005)

        self._recorder_thread = threading.Thread(
            target=pump, name="recorder-pump", daemon=True
        )
        self._recorder_thread.start()
        return {"recording": True, "path": rec.data_path}

    def stop_recording(self) -> dict:
        """停止录制，落 SigMF meta。"""
        self._recorder_stop.set()
        t = self._recorder_thread
        if t is not None:
            t.join(timeout=1.0)
        self._recorder_thread = None
        meta = None
        if self._recorder is not None:
            try:
                meta = self._recorder.close()
            except Exception:  # pragma: no cover
                meta = None
        n = getattr(self._recorder, "n_samples", 0) if self._recorder else 0
        self._recorder = None
        self._recording_path = None
        return {"recording": False, "samples": int(n), "meta_path": meta}

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
            except Exception:  # pragma: no cover
                pass
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
            except Exception:  # pragma: no cover
                pass
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
            return {"error": "not_connected"}
        try:
            self._get_anr().learn_noise(np.asarray(audio, dtype=np.float64))
            return {"learned": True, "samples": int(audio.size)}
        except Exception as e:
            return {"error": "not_available", "technical_detail": str(e)}

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
    # 系统
    # ==================================================================
    def get_status(self) -> SystemStatus:
        """完整系统状态快照（连接/设备/音频/CPU/内存/服务）。"""
        cpu = mem = None
        try:
            import psutil  # type: ignore

            cpu = float(psutil.cpu_percent(interval=0.05))
            mem = float(psutil.virtual_memory().percent)
        except Exception:
            pass
        return SystemStatus(
            connected=self._connected,
            device=self._device_name or None,
            is_debug_source=self._is_debug,
            frequency_hz=self._freq_hz,
            sample_rate_hz=self._sample_rate,
            demod=self._demod,
            gain_db=self._gain_db,
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
        )

    def shutdown(self) -> None:
        """优雅关闭：停音频/录制/扫描/解码/服务并断开设备。"""
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
]
