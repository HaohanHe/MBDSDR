"""
MBDSDR AI 内核 - 统一设备抽象层
================================

对照上游（见 docs/learn/sdrpp_gnuradio_port.md 第一节）：

  - SourceManager 注册表           <-> core/src/signal_path/source.h:9-56
  - SourceHandler 回调打包         <-> core/src/signal_path/source.h:13-22
  - selectSource 接线 IQ 流        <-> core/src/signal_path/source.cpp:42-60
  - 每个源模块独立 .so             <-> source_modules/rtl_tcp_source/src/main.cpp:24-94

SDR++ 的设计是"每个硬件一个插件模块，靠 SourceManager 注册表胶水"。
我们的增强：一个 Python 类 DeviceManager 统一枚举/打开/关闭所有驱动
（RTL-SDR via pyrtlsdr、SoapySDR、HackRF/BladeRF/PlutoSDR 探测），
上层 UI / VFO / 解调链不需要感知具体厂商。

硬红线（与 SDR++ source.cpp:43-45 "select non existent source -> error" 一致）：
  - 无设备时 list_devices() 返回 []，绝不伪造一个"虚拟 RTL-SDR"。
  - 打开不存在/已拔出的设备抛 DeviceNotFoundError。
  - read_samples() 期间设备拔出抛 DeviceDisconnectedError，不崩。
  - 所有可选驱动（pyrtlsdr / SoapySDR）导入失败时静默降级，不影响其它驱动枚举。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------
class DeviceError(Exception):
    """设备层根异常。"""


class DeviceNotFoundError(DeviceError):
    """尝试打开/引用一个不存在的设备。"""


class DeviceDisconnectedError(DeviceError):
    """读样本期间设备被拔出（或连接断开）。下次应重新 list_devices()。"""


# ---------------------------------------------------------------------------
# DeviceInfo：一次枚举快照
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DeviceInfo:
    """一台 SDR 设备的静态描述。

    Attributes:
        name:        人类可读名（如 "RTL-SDR #0 (Realtek RTL2838UHIDIR)"）。
        driver:      驱动键（"rtlsdr" / "soapysdr" / "hackrf" / "bladerf" / "plutosdr"）。
        serial:      USB 序列号 / 设备唯一 id；同型号多棒时靠它区分。
        sample_rates: 支持的采样率列表（Hz）。空列表表示"未声明，用驱动默认"。
        gains:       支持的增益档位列表（dB）。空列表表示 AGC-only 或未探测到。
    """

    name: str
    driver: str
    serial: str
    sample_rates: Tuple[float, ...] = field(default_factory=tuple)
    gains: Tuple[float, ...] = field(default_factory=tuple)

    @property
    def device_key(self) -> str:
        """枚举时用作去重键（driver + serial）。"""
        return f"{self.driver}:{self.serial}"


# ---------------------------------------------------------------------------
# 已打开设备句柄（薄包装，统一 read_samples / set_* 接口）
# ---------------------------------------------------------------------------
class OpenDevice:
    """一次 open() 后拿到的活动设备句柄。

    生命周期：DeviceManager.open(info) -> OpenDevice；用完 close()。
    read_samples(n) 返回 complex64 ndarray；设备拔出抛 DeviceDisconnectedError。
    """

    def __init__(self, info: DeviceInfo, backend: Any):
        self.info = info
        self._backend = backend  # 具体驱动对象（rtlsdr.RtlSdr / SoapySDR.Device / ...）
        self._closed = False
        self._lock = threading.Lock()

    # -- 配置 ---------------------------------------------------------------
    def set_sample_rate(self, rate_hz: float) -> None:
        if self._closed:
            raise DeviceDisconnectedError("设备已关闭")
        try:
            self._backend.sample_rate = float(rate_hz)
        except OSError as e:
            raise DeviceDisconnectedError(f"设置采样率失败: {e}") from e

    def set_frequency(self, freq_hz: float) -> None:
        if self._closed:
            raise DeviceDisconnectedError("设备已关闭")
        try:
            self._backend.center_freq = float(freq_hz)
        except OSError as e:
            raise DeviceDisconnectedError(f"设置频率失败: {e}") from e

    def set_gain(self, gain_db: float) -> None:
        if self._closed:
            raise DeviceDisconnectedError("设备已关闭")
        try:
            self._backend.gain = float(gain_db)
        except (OSError, AttributeError) as e:
            # 有些设备 gain 是 set_gain(value) 方法
            if hasattr(self._backend, "set_gain"):
                try:
                    self._backend.set_gain(float(gain_db))
                    return
                except Exception as e2:
                    raise DeviceDisconnectedError(f"设置增益失败: {e2}") from e2
            raise DeviceDisconnectedError(f"设置增益失败: {e}") from e

    # -- 读样本 -------------------------------------------------------------
    def read_samples(self, n: int) -> np.ndarray:
        """读 n 个复样本（complex64）。设备拔出抛 DeviceDisconnectedError。"""
        if self._closed:
            raise DeviceDisconnectedError("设备已关闭")
        n = int(n)
        if n <= 0:
            return np.empty(0, dtype=np.complex64)
        with self._lock:
            try:
                # pyrtlsdr: read_bytes(n*2) -> bytes (uint8 I/Q 交织)
                # 我们统一转 complex64
                raw = self._backend.read_bytes(n * 2)
            except (OSError, IOError) as e:
                self._closed = True
                raise DeviceDisconnectedError(
                    f"读样本失败，设备可能已拔出: {e}"
                ) from e
            if raw is None or len(raw) < n * 2:
                self._closed = True
                raise DeviceDisconnectedError(
                    f"读样本不足（{len(raw) if raw else 0}/{n*2} 字节），设备断开"
                )
            arr = np.frombuffer(bytes(raw), dtype=np.uint8).reshape(-1, 2)
            i = arr[:, 0].astype(np.float32)
            q = arr[:, 1].astype(np.float32)
            # pyrtlsdr 给的是 unsigned 8-bit（0..255，128=零），与 rtl_tcp 一致
            return (((i - 128.0) / 128.0) + 1j * ((q - 128.0) / 128.0)).astype(
                np.complex64
            )

    # -- 关闭 ---------------------------------------------------------------
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._backend.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# DeviceManager：枚举 / 打开 / 热插拔轮询
# ---------------------------------------------------------------------------
class DeviceManager:
    """统一 SDR 设备枚举与生命周期管理。

    用法：
        dm = DeviceManager()
        devs = dm.list_devices()          # [] 若无硬件
        if devs:
            dev = dm.open(devs[0])
            iq = dev.read_samples(8192)
            dev.close()
        dm.start_monitor(lambda ev: print(ev))  # 热插拔回调
        ...
        dm.stop_monitor()
    """

    # 已知支持的离散采样率档（RTL-SDR 常见，与 rtl_tcp_source main.cpp:30-40 对齐）
    _RTL_SRATES: Tuple[float, ...] = (
        250_000.0,
        1_024_000.0,
        1_536_000.0,
        1_792_000.0,
        1_920_000.0,
        2_048_000.0,
        2_160_000.0,
        2_400_000.0,
        2_560_000.0,
        2_880_000.0,
        3_200_000.0,
    )

    # RTL-SDR 常见增益档（0.1 dB 单位 -> dB）
    _RTL_GAINS: Tuple[float, ...] = (
        0.0, 0.9, 1.4, 2.7, 3.7, 7.7, 8.7, 12.5, 14.4, 15.7,
        16.6, 19.7, 20.7, 22.9, 25.4, 28.0, 29.7, 32.8, 33.8, 36.4, 37.2, 38.6, 40.2, 42.1, 45.0, 49.6,
    )

    def __init__(self, poll_interval: float = 2.0):
        self._poll_interval = float(poll_interval)
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_stop = threading.Event()
        self._last_keys: set = set()

    # -- 枚举 ---------------------------------------------------------------
    def _probe_rtlsdr(self) -> List[DeviceInfo]:
        """通过 pyrtlsdr 枚举本机 USB RTL-SDR；导入失败/无设备返回 []。"""
        try:
            from .sdr_backend import RTLSDRBackend
            items = RTLSDRBackend.list_devices()
        except Exception as e:
            logger.debug("RTLSDRBackend 枚举不可用，跳过 RTL-SDR: %s", e)
            return []
        out: List[DeviceInfo] = []
        for it in items:
            idx = it.get("index", 0)
            tuner = it.get("tuner", "")
            serial = it.get("serial", "") or ("rtl-%d" % idx)
            label = ("RTL-SDR #%d (%s)" % (idx, tuner)) if tuner else ("RTL-SDR #%d" % idx)
            out.append(
                DeviceInfo(
                    name=label,
                    driver="rtlsdr",
                    serial=serial,
                    sample_rates=self._RTL_SRATES,
                    gains=self._RTL_GAINS,
                )
            )
        return out

    def _probe_soapysdr(self) -> List[DeviceInfo]:
        """通过 SoapySDR 枚举所有驱动的设备（HackRF/BladeRF/PlutoSDR/USRP...）。"""
        try:
            import SoapySDR  # type: ignore
        except Exception as e:
            logger.debug("SoapySDR 不可用，跳过枚举: %s", e)
            return []
        try:
            results = SoapySDR.Device.enumerate()
        except Exception as e:
            logger.warning("SoapySDR.enumerate 失败: %s", e)
            return []
        out: List[DeviceInfo] = []
        for i, sarg in enumerate(results or []):
            try:
                driver = str(sarg.get("driver", "unknown"))
                serial = str(sarg.get("serial", sarg.get("label", f"soapysdr-{i}")))
                label = str(sarg.get("label", f"SoapySDR {driver} #{i}"))
                # 尝试打开一次拿采样率/增益（只读 properties，不读流）
                sample_rates: Tuple[float, ...] = ()
                gains: Tuple[float, ...] = ()
                try:
                    dev = SoapySDR.Device(sarg)
                    try:
                        srs = dev.listSampleRates(0)
                        if srs:
                            sample_rates = tuple(float(x) for x in srs)
                    except Exception:
                        pass
                    try:
                        # 只查第一个增益范围
                        granges = dev.getGainRange(0)
                        if granges is not None:
                            # 取 5 档代表
                            lo, hi = float(granges.minimum()), float(granges.maximum())
                            if hi > lo:
                                gains = tuple(
                                    round(lo + (hi - lo) * k / 4.0, 2) for k in range(5)
                                )
                    except Exception:
                        pass
                    dev.close()
                except Exception as e:
                    logger.debug("SoapySDR 打开 probe 失败 %s: %s", label, e)
                out.append(
                    DeviceInfo(
                        name=label,
                        driver=f"soapysdr:{driver}",
                        serial=serial,
                        sample_rates=sample_rates,
                        gains=gains,
                    )
                )
            except Exception as e:
                logger.warning("解析 SoapySDR 结果失败: %s", e)
        return out

    def list_devices(self) -> List[DeviceInfo]:
        """枚举所有可见设备。无硬件时返回 []，绝不造假。"""
        out: List[DeviceInfo] = []
        out.extend(self._probe_rtlsdr())
        out.extend(self._probe_soapysdr())
        return out

    # -- 打开 / 关闭 --------------------------------------------------------
    def open(self, info: DeviceInfo) -> OpenDevice:
        """打开一台设备，返回 OpenDevice 句柄。找不到/驱动失败抛 DeviceNotFoundError。"""
        # 先校验还在
        present = {d.device_key for d in self.list_devices()}
        if info.device_key not in present:
            raise DeviceNotFoundError(
                f"设备 {info.device_key} 不在当前枚举列表中（可能已拔出）"
            )
        if info.driver == "rtlsdr":
            return self._open_rtlsdr(info)
        if info.driver.startswith("soapysdr:"):
            return self._open_soapysdr(info)
        raise DeviceNotFoundError(f"未知驱动: {info.driver}")

    def _open_rtlsdr(self, info: DeviceInfo) -> OpenDevice:
        try:
            import rtlsdr  # type: ignore
        except Exception as e:
            raise DeviceNotFoundError(f"pyrtlsdr 未安装: {e}") from e
        # 找 serial 匹配的 index
        devs = self._probe_rtlsdr()
        idx = 0
        for i, d in enumerate(devs):
            if d.serial == info.serial:
                idx = i
                break
        try:
            backend = rtlsdr.RtlSdr(device_index=idx)
        except Exception as e:
            raise DeviceNotFoundError(f"打开 RTL-SDR #{idx} 失败: {e}") from e
        return OpenDevice(info, backend)

    def _open_soapysdr(self, info: DeviceInfo) -> OpenDevice:
        try:
            import SoapySDR  # type: ignore
        except Exception as e:
            raise DeviceNotFoundError(f"SoapySDR 未安装: {e}") from e
        # 重新 enumerate 找匹配的 args
        try:
            for sarg in SoapySDR.Device.enumerate():
                serial = str(sarg.get("serial", sarg.get("label", "")))
                if serial == info.serial:
                    backend = SoapySDR.Device(sarg)
                    # 启用 Rx 流（不开始 read，只配置）
                    try:
                        backend.setupStream(
                            SoapySDR.DIRECTION_RX, "CF32", []
                        )
                    except Exception:
                        pass
                    return OpenDevice(info, backend)
        except Exception as e:
            raise DeviceNotFoundError(f"SoapySDR 打开失败: {e}") from e
        raise DeviceNotFoundError(f"SoapySDR 设备 {info.serial} 未找到")

    # -- 热插拔 -------------------------------------------------------------
    def start_monitor(
        self, callback: Callable[[Dict[str, Any]], None], poll_interval: Optional[float] = None
    ) -> None:
        """启动后台轮询线程。设备增/删时回调 callback({"added": [...], "removed": [...]})。"""
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            return
        self._monitor_stop.clear()
        interval = float(poll_interval) if poll_interval else self._poll_interval
        self._last_keys = {d.device_key for d in self.list_devices()}

        def _loop():
            while not self._monitor_stop.is_set():
                try:
                    current = {d.device_key: d for d in self.list_devices()}
                except Exception as e:
                    logger.debug("热插拔轮询枚举失败: %s", e)
                    self._monitor_stop.wait(interval)
                    continue
                cur_keys = set(current.keys())
                added = cur_keys - self._last_keys
                removed = self._last_keys - cur_keys
                if added or removed:
                    added_infos = [current[k] for k in added]
                    removed_infos = [
                        DeviceInfo(name=k.split(":", 1)[1], driver=k.split(":")[0], serial=k)
                        for k in removed
                    ]
                    try:
                        callback({"added": added_infos, "removed": removed_infos})
                    except Exception as e:
                        logger.warning("热插拔回调异常: %s", e)
                    self._last_keys = cur_keys
                self._monitor_stop.wait(interval)

        self._monitor_thread = threading.Thread(
            target=_loop, name="device-monitor", daemon=True
        )
        self._monitor_thread.start()

    def stop_monitor(self) -> None:
        """停止热插拔轮询。"""
        self._monitor_stop.set()
        if self._monitor_thread is not None:
            self._monitor_thread.join(timeout=2.0)
            self._monitor_thread = None
