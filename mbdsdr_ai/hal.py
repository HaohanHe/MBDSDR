"""
MBDSDR AI - 硬件抽象层（HAL）
=============================

统一 SDR 硬件接入，通过 SoapySDR 适配多种设备：
- RTL-SDR（RTL2832U/R820T/FC0012）- 仅RX
- HackRF One（1MHz-6GHz, TX/RX）
- PlutoSDR（325MHz-3.8GHz, TX/RX）
- BladeRF（300MHz-3.8GHz, TX/RX）
- LimeSDR（100kHz-3.8GHz, TX/RX）
- USRP/Ettus（UHD, TX/RX）
- Airspy / SDRplay（RX）

同时支持仪器接入：
- 示波器（SCPI/VISA，作为信号源或额外采集）
- 信号发生器（SCPI/VISA，作为激励源）
- 频谱仪（嵌入式电脑+SDR模块）

设计原则：
- 所有硬件都是可选后端，通过 SoapySDR device string 统一访问
- RX/TX 能力由设备能力标志决定
- 无硬件时显式未连接，绝不降级模拟
- 嵌入式部署（树莓派/Jetson/ARM）自动检测并优化
"""

import numpy as np
import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


def _import_soapy():
    """惰性导入 SoapySDR，未安装时返回 None（不抛异常）。

    模块顶部不得 import SoapySDR，否则无硬件环境 `import mbdsdr_ai.hal` 即崩。
    """
    try:
        import SoapySDR
        return SoapySDR
    except ImportError:
        return None


class DeviceCapability(Enum):
    """设备能力。"""
    RX = "rx"
    TX = "tx"
    FULL_DUPLEX = "full_duplex"
    HF = "hf"           # 100kHz以下
    VHF = "vhf"         # 30-300MHz
    UHF = "uhf"         # 300MHz-3GHz
    SHF = "shf"         # 3-30GHz
    GUI = "gui"         # 有图形界面
    EMBEDDED = "embedded"  # 嵌入式平台


@dataclass
class DeviceInfo:
    """设备信息。"""
    name: str
    driver: str               # soapyhackrf/soapyrtlsdr/soapyplutosdr等
    serial: str = ""
    manufacturer: str = ""
    product: str = ""
    rx_range: Tuple[float, float] = (0.0, 0.0)  # Hz
    tx_range: Tuple[float, float] = (0.0, 0.0)  # Hz
    sample_rates: List[float] = field(default_factory=list)  # Hz
    gain_stages: Dict[str, Tuple[float, float]] = field(default_factory=dict)  # name -> (min, max)
    capabilities: List[str] = field(default_factory=list)
    is_available: bool = False
    description: str = ""


class SDRBackendBase(ABC):
    """SDR 后端基类。"""

    def __init__(self, device_args: str = ""):
        self.device_args = device_args
        self._device = None
        self._rx_stream = None
        self._tx_stream = None
        self._center_freq = 100e6
        self._sample_rate = 2.048e6
        self._gain = 30.0
        self._running = False

    @abstractmethod
    def list_devices(self) -> List[DeviceInfo]:
        """列出所有可用设备。"""
        pass

    @abstractmethod
    def connect(self, device_str: str = "") -> bool:
        """连接设备。"""
        pass

    @abstractmethod
    def disconnect(self):
        """断开设备。"""
        pass

    @abstractmethod
    def set_frequency(self, freq_hz: float):
        """设置中心频率。"""
        pass

    @abstractmethod
    def set_sample_rate(self, rate_hz: float):
        """设置采样率。"""
        pass

    @abstractmethod
    def set_gain(self, gain_db: float, stage: str = ""):
        """设置增益。"""
        pass

    @abstractmethod
    def read_rx(self, num_samples: int) -> np.ndarray:
        """读取 RX IQ 采样。"""
        pass

    def write_tx(self, iq_samples: np.ndarray) -> bool:
        """写 TX IQ 采样（默认不支持TX）。"""
        logger.warning(f"{self.__class__.__name__} 不支持TX发射")
        return False

    def supports_tx(self) -> bool:
        """是否支持发射。"""
        return False

    def get_info(self) -> DeviceInfo:
        """获取设备信息。"""
        return DeviceInfo(name=self.__class__.__name__, driver="unknown")


class SoapySDRBackend(SDRBackendBase):
    """
    SoapySDR 统一后端。
    通过 SoapySDR 库访问所有支持的 SDR 设备。
    """

    # 已知设备驱动映射
    DRIVER_MAP = {
        "hackrf": "HackRF One (1MHz-6GHz, TX/RX)",
        "rtlsdr": "RTL-SDR (24-1766MHz, RX only)",
        "plutosdr": "PlutoSDR (325MHz-3.8GHz, TX/RX)",
        "bladerf": "BladeRF (300MHz-3.8GHz, TX/RX)",
        "lime": "LimeSDR (100kHz-3.8GHz, TX/RX)",
        "uhd": "USRP/Ettus (UHD, TX/RX)",
        "airspy": "Airspy (24-1700MHz, RX)",
        "sdrplay": "SDRplay (1kHz-2GHz, RX)",
        "audio": "声卡 (SoapyAudio)",
        "redpitaya": "Red Pitaya (STEMlab, TX/RX)",
        "netsdr": "RFSpace/NetSDR (RX)",
    }

    # 频率范围映射
    RANGE_MAP = {
        "hackrf": (1e6, 6e9),
        "rtlsdr": (24e3, 1766e6),
        "plutosdr": (325e6, 3.8e9),
        "bladerf": (300e6, 3.8e9),
        "lime": (100e3, 3.8e9),
        "uhd": (1e3, 6e9),
        "airspy": (24e3, 1700e6),
        "sdrplay": (1e3, 2e9),
    }

    def list_devices(self) -> List[DeviceInfo]:
        """通过 SoapySDR 列出所有可用设备。"""
        devices = []
        try:
            import SoapySDR
            results = SoapySDR.Device.enumerate()
            for i, result in enumerate(results):
                driver = result.get("driver", "unknown")
                serial = result.get("serial", "")
                name = result.get("label", f"{driver}-{i}")

                rx_range = self.RANGE_MAP.get(driver, (0.0, 0.0))
                tx_capable = driver in ("hackrf", "plutosdr", "bladerf", "lime", "uhd", "redpitaya")
                tx_range = rx_range if tx_capable else (0.0, 0.0)

                caps = ["rx"]
                if tx_capable:
                    caps.append("tx")
                if rx_range[0] < 30e6:
                    caps.append("hf")
                if rx_range[0] < 300e6:
                    caps.append("vhf")
                if rx_range[1] > 300e6:
                    caps.append("uhf")

                devices.append(DeviceInfo(
                    name=name,
                    driver=f"soapy_{driver}",
                    serial=serial,
                    manufacturer=self.DRIVER_MAP.get(driver, driver),
                    rx_range=rx_range,
                    tx_range=tx_range,
                    sample_rates=[2e6, 4e6, 8e6, 10e6, 20e6],
                    capabilities=caps,
                    is_available=True,
                    description=self.DRIVER_MAP.get(driver, driver),
                ))
        except ImportError:
            logger.info("SoapySDR 未安装，列出已知设备类型")
            # 列出已知设备类型（即使未安装SoapySDR）
            for driver, desc in self.DRIVER_MAP.items():
                rx_range = self.RANGE_MAP.get(driver, (0.0, 0.0))
                tx_capable = driver in ("hackrf", "plutosdr", "bladerf", "lime", "uhd", "redpitaya")
                caps = ["rx"]
                if tx_capable:
                    caps.append("tx")
                devices.append(DeviceInfo(
                    name=desc.split("(")[0].strip(),
                    driver=f"soapy_{driver}",
                    rx_range=rx_range,
                    tx_range=rx_range if tx_capable else (0.0, 0.0),
                    capabilities=caps,
                    is_available=False,
                    description=f"{desc} (SoapySDR未安装或设备未连接)",
                ))
        except Exception as e:
            logger.warning(f"SoapySDR 枚举失败: {e}")

        return devices

    def connect(self, device_str: str = "") -> bool:
        """通过 SoapySDR 连接设备。"""
        try:
            import SoapySDR
            args_str = device_str or self.device_args
            if not args_str:
                # 自动选择第一个可用设备
                results = SoapySDR.Device.enumerate()
                if not results:
                    logger.error("未找到任何 SDR 设备")
                    return False
                args_str = str(results[0])

            self._device = SoapySDR.Device(args_str)
            logger.info(f"已连接: {args_str}")
            return True
        except ImportError:
            logger.warning("SoapySDR 未安装，无法连接真实设备")
            return False
        except Exception as e:
            logger.error(f"连接失败: {e}")
            return False

    def disconnect(self):
        """断开设备。"""
        if self._rx_stream:
            self._rx_stream = None
        if self._device:
            self._device = None
        self._running = False
        logger.info("设备已断开")

    def set_frequency(self, freq_hz: float):
        self._center_freq = freq_hz
        if self._device:
            SoapySDR = _import_soapy()
            if SoapySDR is None:
                logger.warning("SoapySDR 未安装，无法下发频率")
                return
            try:
                self._device.setFrequency(SoapySDR.SOAPY_SDR_RX, 0, freq_hz)
            except Exception as e:
                logger.warning(f"设置频率失败: {e}")

    def set_sample_rate(self, rate_hz: float):
        self._sample_rate = rate_hz
        if self._device:
            SoapySDR = _import_soapy()
            if SoapySDR is None:
                logger.warning("SoapySDR 未安装，无法下发采样率")
                return
            try:
                self._device.setSampleRate(SoapySDR.SOAPY_SDR_RX, 0, rate_hz)
            except Exception as e:
                logger.warning(f"设置采样率失败: {e}")

    def set_gain(self, gain_db: float, stage: str = ""):
        self._gain = gain_db
        if self._device:
            SoapySDR = _import_soapy()
            if SoapySDR is None:
                logger.warning("SoapySDR 未安装，无法下发增益")
                return
            try:
                if stage:
                    self._device.setGain(SoapySDR.SOAPY_SDR_RX, 0, gain_db, stage)
                else:
                    self._device.setGain(SoapySDR.SOAPY_SDR_RX, 0, gain_db)
            except Exception as e:
                logger.warning(f"设置增益失败: {e}")

    def setupStream(self, direction: str = "RX", format: str = "CF32",
                    channels: Optional[List[int]] = None) -> bool:
        """
        设置并激活 SoapySDR 流。

        参数:
            direction: "RX" 或 "TX"
            format: 流格式（CF32 / CS16 / CU8）
            channels: 通道列表，默认 [0]
        返回:
            True 成功
        异常:
            RuntimeError: SoapySDR 未安装或设备未连接时抛出明确异常
        """
        SoapySDR = _import_soapy()
        if SoapySDR is None:
            raise RuntimeError("SoapySDR 未安装，无法 setupStream")
        if not self._device:
            raise RuntimeError("设备未连接，无法 setupStream")
        if channels is None:
            channels = [0]
        try:
            dir_const = (SoapySDR.SOAPY_SDR_RX if direction.upper() == "RX"
                         else SoapySDR.SOAPY_SDR_TX)
            stream = self._device.setupStream(dir_const, format, channels)
            if stream is None:
                raise RuntimeError(f"setupStream 返回空流 (dir={direction}, fmt={format})")
            self._device.activateStream(stream)
            if direction.upper() == "RX":
                self._rx_stream = stream
            else:
                self._tx_stream = stream
            logger.info(f"流已设置并激活: dir={direction}, fmt={format}, ch={channels}")
            return True
        except Exception as e:
            logger.error(f"setupStream 失败: {e}")
            raise

    def read_rx(self, num_samples: int) -> np.ndarray:
        """读取 RX IQ 采样。"""
        if self._device and self._rx_stream:
            SoapySDR = _import_soapy()
            if SoapySDR is None:
                logger.warning("SoapySDR 未安装，无法读取真实 IQ")
            else:
                try:
                    buff = np.zeros(num_samples, dtype=np.complex64)
                    flags = 0
                    time_ns = 0
                    n = self._rx_stream.read(buff, num_samples, flags, time_ns)
                    if n and n > 0:
                        return buff[:n]
                except Exception as e:
                    logger.warning(f"读取RX失败: {e}")

        # 设备/流未就绪：不返回白噪声冒充真 IQ，抛错让上层知道
        raise RuntimeError(
            "RX 流未就绪（device 或 rx_stream 为 None），未收真实 IQ；"
            "请先 connect() 并 setupStream()")

    def write_tx(self, iq_samples: np.ndarray) -> bool:
        """写 TX IQ 采样。"""
        if not self._device or not self._tx_stream:
            logger.warning("TX 流未初始化")
            return False
        try:
            buff = np.array(iq_samples, dtype=np.complex64)
            flags = 0
            time_ns = 0
            n = self._tx_stream.write(buff, len(buff), flags, time_ns)
            return n > 0
        except Exception as e:
            logger.error(f"TX 写入失败: {e}")
            return False

    def supports_tx(self) -> bool:
        """检查当前设备是否支持TX。"""
        if not self._device:
            return False
        SoapySDR = _import_soapy()
        if SoapySDR is None:
            return False
        try:
            return self._device.hasTxChannel(SoapySDR.SOAPY_SDR_TX, 0)
        except Exception:
            return False

    def get_info(self) -> DeviceInfo:
        if not self._device:
            return DeviceInfo(name="未连接", driver="none")
        try:
            hw_key = self._device.getHardwareKey()
            return DeviceInfo(
                name=hw_key,
                driver="soapy",
                is_available=True,
            )
        except Exception:
            return DeviceInfo(name="未知", driver="soapy")


class InstrumentBackend:
    """
    仪器接入后端（示波器/信号发生器/频谱仪）。
    通过 SCPI/VISA 协议控制仪器。
    """

    # 常见仪器厂商
    VENDORS = {
        "keysight": "Keysight/Agilent",
        "tektronix": "Tektronix",
        "rigol": "RIGOL",
        "siglent": "Siglent",
        "yokogawa": "Yokogawa",
        "stanford": "Stanford Research",
    }

    def __init__(self, visa_address: str = ""):
        self.visa_address = visa_address
        self._resource = None

    def list_instruments(self) -> List[Dict[str, Any]]:
        """列出所有可用仪器（通过 VISA 或网络扫描）。"""
        instruments = []
        try:
            import pyvisa
            rm = pyvisa.ResourceManager()
            for addr in rm.list_resources():
                try:
                    inst = rm.open_resource(addr)
                    idn = inst.query("*IDN?").strip()
                    inst.close()
                    parts = idn.split(",")
                    vendor = parts[0] if len(parts) > 0 else "Unknown"
                    model = parts[1] if len(parts) > 1 else ""
                    instruments.append({
                        "address": addr,
                        "vendor": vendor,
                        "model": model,
                        "idn": idn,
                        "type": self._guess_instrument_type(vendor, model),
                    })
                except Exception:
                    continue
        except ImportError:
            logger.info("pyvisa 未安装，列出已知仪器类型")
            for vendor, name in self.VENDORS.items():
                instruments.append({
                    "address": f"TCPIP::{vendor}.local::INSTR",
                    "vendor": name,
                    "model": "(未知)",
                    "type": "未知",
                    "available": False,
                })
        except Exception as e:
            logger.warning(f"VISA 枚举失败: {e}")

        return instruments

    def _guess_instrument_type(self, vendor: str, model: str) -> str:
        """根据厂商和型号猜测仪器类型。"""
        model_lower = model.lower()
        if any(x in model_lower for x in ["dso", "mso", "osc", "scope", "ds1", "ds2", "mdo"]):
            return "oscilloscope"
        elif any(x in model_lower for x in ["sig", "33", "afg", "dg", "sdg", "gen"]):
            return "signal_generator"
        elif any(x in model_lower for x in ["spectrum", "esa", "n9", "fsv", "fsw", "rsa"]):
            return "spectrum_analyzer"
        elif any(x in model_lower for x in ["psu", "e36", "xte", "dp", "gpd"]):
            return "power_supply"
        else:
            return "unknown"

    def connect(self, visa_address: str) -> bool:
        """连接仪器。"""
        try:
            import pyvisa
            rm = pyvisa.ResourceManager()
            self._resource = rm.open_resource(visa_address)
            self.visa_address = visa_address
            logger.info(f"已连接仪器: {visa_address}")
            return True
        except ImportError:
            logger.warning("pyvisa 未安装")
            return False
        except Exception as e:
            logger.error(f"仪器连接失败: {e}")
            return False

    def query(self, scpi_cmd: str) -> str:
        """发送 SCPI 查询命令。"""
        if not self._resource:
            return "仪器未连接"
        try:
            return self._resource.query(scpi_cmd).strip()
        except Exception as e:
            return f"查询失败: {e}"

    def write(self, scpi_cmd: str) -> bool:
        """发送 SCPI 写入命令。"""
        if not self._resource:
            return False
        try:
            self._resource.write(scpi_cmd)
            return True
        except Exception as e:
            logger.error(f"SCPI写入失败: {e}")
            return False

    def read_waveform(self, channel: int = 1) -> Optional[np.ndarray]:
        """从示波器读取波形数据。"""
        if not self._resource:
            return None
        try:
            # 停止采集
            self._resource.write(f":WAV:SOUR CHAN{channel}")
            self._resource.write(":WAV:FORM ASC")
            # 读取数据
            raw = self._resource.query_binary_values(":WAV:DATA?", datatype='B')
            return np.array(raw, dtype=np.float64)
        except Exception as e:
            logger.error(f"读取波形失败: {e}")
            return None


# ========================================================================
# 嵌入式平台检测
# ========================================================================

def detect_embedded_platform() -> Dict[str, Any]:
    """检测当前运行平台是否为嵌入式设备。"""
    import platform
    import os

    info = {
        "platform": platform.system(),
        "machine": platform.machine(),
        "is_arm": platform.machine().startswith("arm"),
        "is_embedded": False,
        "platform_name": "desktop",
        "gpu_available": False,
    }

    # 检测树莓派
    try:
        with open("/proc/cpuinfo", "r") as f:
            cpuinfo = f.read()
        if "BCM" in cpuinfo or "Raspberry Pi" in cpuinfo:
            info["is_embedded"] = True
            info["platform_name"] = "raspberry_pi"
    except Exception:
        pass

    # 检测 Jetson
    try:
        with open("/proc/device-tree/model", "r") as f:
            model = f.read().strip()
        if "Jetson" in model:
            info["is_embedded"] = True
            info["platform_name"] = "jetson"
            info["gpu_available"] = True
    except Exception:
        pass

    # 检测 Windows ARM
    if platform.system() == "Windows" and platform.machine() == "ARM64":
        info["is_embedded"] = True
        info["platform_name"] = "windows_on_arm"

    # 检测 macOS ARM
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        info["platform_name"] = "macos_apple_silicon"

    return info


# ========================================================================
# 硬件管理器（单例）
# ========================================================================

class HardwareManager:
    """
    硬件管理器：统一管理所有 SDR 后端和仪器。
    自动检测可用设备，提供统一接口。
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._soapy_backend = None
        self._instrument = None
        self._active_backend = None
        self._platform = detect_embedded_platform()
        self._initialized = True

    @classmethod
    def get_instance(cls):
        return cls()

    def get_platform_info(self) -> Dict[str, Any]:
        return self._platform

    def list_all_devices(self) -> List[Dict[str, Any]]:
        """列出所有可用硬件（SDR + 仪器）。"""
        all_devices = []

        # SoapySDR 设备
        try:
            soapy = SoapySDRBackend()
            soapy_devices = soapy.list_devices()
            for d in soapy_devices:
                all_devices.append({
                    "type": "sdr",
                    "name": d.name,
                    "driver": d.driver,
                    "rx_range": f"{d.rx_range[0]/1e6:.1f}-{d.rx_range[1]/1e6:.1f} MHz",
                    "tx": "TX" if "tx" in d.capabilities else "RX only",
                    "available": d.is_available,
                    "description": d.description,
                })
        except Exception as e:
            all_devices.append({"type": "sdr", "name": f"SoapySDR错误: {e}", "available": False})

        # 仪器
        try:
            inst = InstrumentBackend()
            instruments = inst.list_instruments()
            for inst_info in instruments:
                all_devices.append({
                    "type": "instrument",
                    "name": f"{inst_info['vendor']} {inst_info['model']}",
                    "driver": "scpi_visa",
                    "address": inst_info["address"],
                    "instrument_type": inst_info.get("type", "unknown"),
                    "available": inst_info.get("available", True),
                })
        except Exception as e:
            all_devices.append({"type": "instrument", "name": f"VISA错误: {e}", "available": False})

        return all_devices

    def connect_sdr(self, device_str: str = "") -> Dict[str, Any]:
        """连接 SDR 设备。

        只对接真实 SoapySDR 硬件。device_str 为空时自动枚举第一个真实设备；
        无设备 / 连接失败一律显式返回 success=False，绝不降级模拟后端。
        """
        # 尝试 SoapySDR（device_str 为空时 connect() 内部自动选第一个真实设备）
        self._soapy_backend = SoapySDRBackend()
        if self._soapy_backend.connect(device_str):
            self._active_backend = self._soapy_backend
            return {
                "success": True,
                "device": device_str,
                "tx": self._soapy_backend.supports_tx(),
            }

        # 真硬件连不上：明确报错，不偷偷切任何模拟后端
        if not device_str:
            return {
                "success": False,
                "error": "未找到 SDR 设备",
            }
        return {
            "success": False,
            "error": f"SoapySDR 连接 {device_str!r} 失败",
        }

    def get_active_backend(self) -> Optional[SDRBackendBase]:
        return self._active_backend

    def transmit_cw(self, freq_hz: float, amplitude: float = 0.1, duration_sec: float = 1.0) -> bool:
        """发射连续波（CW）。"""
        if not self._active_backend or not self._active_backend.supports_tx():
            return False
        self._active_backend.set_frequency(freq_hz)
        n = int(self._active_backend._sample_rate * duration_sec)
        t = np.arange(n) / self._active_backend._sample_rate
        iq = amplitude * np.exp(2j * np.pi * 0 * t)  # 零中频CW
        return self._active_backend.write_tx(iq.astype(np.complex64))

    def transmit_modulated(
        self,
        freq_hz: float,
        iq_samples: np.ndarray,
        amplitude: float = 0.1,
    ) -> bool:
        """发射调制信号。"""
        if not self._active_backend or not self._active_backend.supports_tx():
            return False
        self._active_backend.set_frequency(freq_hz)
        scaled = amplitude * iq_samples / (np.max(np.abs(iq_samples)) + 1e-12)
        return self._active_backend.write_tx(scaled.astype(np.complex64))
