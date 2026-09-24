"""
MBDSDR AI 内核 - SDR 后端抽象层
================================
SDR Backend：统一的 SDR 硬件抽象层。

支持的后端：
- RTL-SDR（RTL2832U + E4000/FC0012/FC0013/R820T/R820T2）
- HackRF One
- USRP（UHD）
- 自研 ai-sdr Mini（SI4732 + ESP32，通过 MCP/WebSocket）
- 模拟后端（用于开发测试，生成模拟信号）

统一接口：
- connect() / disconnect()
- set_frequency() / get_frequency()
- set_sample_rate() / get_sample_rate()
- set_gain() / get_gain() / set_agc()
- set_bandwidth()
- read_samples()  # 读取 IQ 样本
- get_status()

所有后端实现相同的接口，上层工具不需要关心具体硬件。
"""

import time
import logging

import os
import json
import threading
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class SDRDevice:
    """SDR 设备信息。"""
    device_type: str  # rtl_sdr / hackrf / usrp / ai_sdr_mini / mock
    device_id: str
    name: str
    frequency_range: Tuple[float, float]  # Hz
    sample_rate_range: Tuple[float, float]  # Hz
    max_gain: float  # dB
    supports_iq: bool = True
    supports_tx: bool = False
    connected: bool = False


@dataclass
class SDRStatus:
    """SDR 当前状态。"""
    connected: bool = False
    frequency_hz: float = 100000000.0
    sample_rate_hz: float = 2400000.0
    gain_db: float = 0.0
    agc_enabled: bool = True
    bandwidth_hz: float = 0.0  # 0=自动
    demod_mode: str = "FM"  # FM/AM/SSB/LSB/USB/CW/NFM/WFM
    squelch_db: float = -100.0
    volume: float = 0.5
    rssi_db: float = -100.0
    snr_db: float = 0.0
    recording: bool = False
    recording_path: str = ""
    recording_duration: float = 0.0
    device_temp: float = 0.0
    samples_read: int = 0
    uptime_seconds: float = 0.0
    error: str = ""  # 最近一次 setter 失败/硬件回读失败的错误描述，空串=正常


class SDRBackend:
    """
    SDR 后端基类。

    所有具体后端都继承此类并实现接口。
    """

    def __init__(self, device: SDRDevice):
        self.device = device
        self.status = SDRStatus()
        self._start_time = 0.0
        self._samples_read = 0
        # 录制线程相关
        self._recording_thread = None
        self._recording_stop_event = threading.Event()
        self._recording_samples_total = 0
        self._recording_start_time = 0.0
        self._recording_format = "cf32"
        self._recording_gain = 1.0
        self._recording_decimation = 1

    def connect(self) -> bool:
        """连接设备。"""
        raise NotImplementedError

    def disconnect(self):
        """断开设备。"""
        self.status.connected = False

    def set_frequency(self, freq_hz: float) -> bool:
        """设置中心频率。失败时回滚 status，不留"已设置"假状态。"""
        if not self.status.connected:
            return False
        if freq_hz < self.device.frequency_range[0] or freq_hz > self.device.frequency_range[1]:
            return False
        old = self.status.frequency_hz
        self.status.error = ""
        try:
            ok = self._apply_frequency(freq_hz)
        except Exception as e:
            ok = False
            self.status.error = f"set_frequency异常: {e}"
        if not ok:
            self.status.frequency_hz = old  # 回滚
            if not self.status.error:
                self.status.error = "set_frequency失败"
            logger.warning(f"set_frequency({freq_hz}) 失败，已回滚到 {old}")
            return False
        self.status.frequency_hz = freq_hz
        return True

    def _apply_frequency(self, freq_hz: float) -> bool:
        """子类覆写：把频率真正写入硬件。默认无硬件（mock/文件），直接成功。"""
        return True

    def get_frequency(self) -> float:
        return self.status.frequency_hz

    def set_sample_rate(self, rate_hz: float) -> bool:
        """设置采样率。失败时回滚 status。"""
        if not self.status.connected:
            return False
        old = self.status.sample_rate_hz
        self.status.error = ""
        try:
            ok = self._apply_sample_rate(rate_hz)
        except Exception as e:
            ok = False
            self.status.error = f"set_sample_rate异常: {e}"
        if not ok:
            self.status.sample_rate_hz = old
            if not self.status.error:
                self.status.error = "set_sample_rate失败"
            logger.warning(f"set_sample_rate({rate_hz}) 失败，已回滚到 {old}")
            return False
        self.status.sample_rate_hz = rate_hz
        return True

    def _apply_sample_rate(self, rate_hz: float) -> bool:
        """子类覆写：把采样率真正写入硬件。"""
        return True

    def get_sample_rate(self) -> float:
        return self.status.sample_rate_hz

    def set_gain(self, gain_db: float) -> bool:
        """设置增益。失败时回滚 status。"""
        if not self.status.connected:
            return False
        clamped = max(0, min(gain_db, self.device.max_gain))
        old = self.status.gain_db
        self.status.error = ""
        try:
            ok = self._apply_gain(clamped)
        except Exception as e:
            ok = False
            self.status.error = f"set_gain异常: {e}"
        if not ok:
            self.status.gain_db = old
            if not self.status.error:
                self.status.error = "set_gain失败"
            logger.warning(f"set_gain({clamped}) 失败，已回滚到 {old}")
            return False
        self.status.gain_db = clamped
        self.status.agc_enabled = False
        return True

    def _apply_gain(self, gain_db: float) -> bool:
        """子类覆写：把增益真正写入硬件。"""
        return True

    def get_gain(self) -> float:
        return self.status.gain_db

    def readback_hw_state(self) -> bool:
        """连接成功后从硬件回读实际 center_freq/sample_rate/gain，对齐内部状态。

        硬件不支持回读时记录 warning 并返回 False，不抛异常。
        子类应覆写此方法实现真实回读。
        """
        logger.warning(f"{self.__class__.__name__} 不支持硬件回读，软件状态可能与硬件实际不一致")
        return False

    def set_agc(self, enabled: bool) -> bool:
        if not self.status.connected:
            return False
        self.status.agc_enabled = enabled
        return True

    def set_bandwidth(self, bw_hz: float) -> bool:
        if not self.status.connected:
            return False
        self.status.bandwidth_hz = bw_hz
        return True

    def set_demod(self, mode: str) -> bool:
        # RAW/DIG 表示不解调模拟音频、直接取原始 IQ（ADS-B/APRS/FT8/导航等数字链路）
        valid = ["FM", "AM", "SSB", "LSB", "USB", "CW", "NFM", "WFM",
                 "RAW", "DIG"]
        if mode.upper() not in valid:
            return False
        self.status.demod_mode = mode.upper()
        return True

    def set_squelch(self, db: float) -> bool:
        self.status.squelch_db = db
        return True

    def set_volume(self, vol: float) -> bool:
        self.status.volume = max(0.0, min(1.0, vol))
        return True

    def read_samples(self, num_samples: int) -> Optional[np.ndarray]:
        """读取 IQ 样本，返回复数数组。"""
        raise NotImplementedError

    def get_status(self) -> SDRStatus:
        if self.status.connected:
            self.status.uptime_seconds = time.time() - self._start_time
            self.status.samples_read = self._samples_read
        return self.status

    def start_recording(self, path: str, duration: float = 0,
                        fmt: str = "cf32", gain: float = 1.0,
                        decimation: int = 1) -> bool:
        """
        开始录制基带（真正写文件）。

        启动后台线程，边采边流式写盘，支持长时间录制不占内存。
        支持 cu8（rtl_sdr 原生 unsigned8，生态最通用）/ cf32 / cs16 / wav / csv。
        duration > 0 时到点自动停止。
        录制同时写同名 .json sidecar（含 format/sample_rate/center_freq），可直接回放。
        """
        if not self.status.connected:
            return False
        if self.status.recording:
            return False  # 已经在录制

        # 确保目录存在
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

        self.status.recording = True
        self.status.recording_path = path
        self.status.recording_duration = duration
        self._recording_format = fmt
        self._recording_gain = gain
        self._recording_decimation = decimation
        self._recording_samples_total = 0
        self._recording_start_time = time.time()
        self._recording_stop_event.clear()

        # 启动录制线程
        self._recording_thread = threading.Thread(
            target=self._recording_loop,
            args=(path, fmt, duration, gain, decimation),
            daemon=True,
        )
        self._recording_thread.start()
        return True

    def _recording_loop(self, path: str, fmt: str, duration: float,
                        gain: float, decimation: int):
        """录制线程主循环：边采边流式写盘，支持长时间录制不占内存。"""
        chunk_size = 16384  # 每次读取的样本数
        samples_total = 0
        fh = None
        wf = None
        fmt_l = fmt.lower()
        effective_rate = int(self.status.sample_rate_hz / max(decimation, 1))

        try:
            if fmt_l == "wav":
                import wave
                wf = wave.open(path, "wb")
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(effective_rate)
            elif fmt_l == "csv":
                fh = open(path, "w", encoding="utf-8")
                fh.write("I,Q\n")
            else:
                fh = open(path, "wb")  # cf32 / cs16 / cu8

            while not self._recording_stop_event.is_set():
                if duration > 0 and (time.time() - self._recording_start_time) >= duration:
                    break

                samples = self.read_samples(chunk_size)
                if samples is None or len(samples) == 0:
                    time.sleep(0.01)
                    continue

                if decimation > 1:
                    samples = samples[::decimation]

                # pyrtlsdr 等后端返回的 IQ 已归一化到约 [-1,1]，直接按满量程量化
                z = np.asarray(samples, dtype=np.complex128) * gain
                if fmt_l == "cf32":
                    np.column_stack([z.real, z.imag]).astype(np.float32).tofile(fh)
                elif fmt_l == "cs16":
                    ci = np.clip(z.real, -1.0, 1.0) * 32767
                    cq = np.clip(z.imag, -1.0, 1.0) * 32767
                    np.column_stack([ci, cq]).astype(np.int16).tofile(fh)
                elif fmt_l == "cu8":
                    # rtl_sdr / SDR# / GQRX 通用 unsigned 8-bit 交错，中心 127.5
                    ui = np.clip(z.real, -1.0, 1.0) * 127.5 + 127.5
                    uq = np.clip(z.imag, -1.0, 1.0) * 127.5 + 127.5
                    np.column_stack([ui, uq]).astype(np.uint8).tofile(fh)
                elif fmt_l == "wav":
                    ci = np.clip(z.real, -1.0, 1.0) * 32767
                    cq = np.clip(z.imag, -1.0, 1.0) * 32767
                    wf.writeframes(np.column_stack([ci, cq]).astype(np.int16).tobytes())
                elif fmt_l == "csv":
                    np.savetxt(fh, np.column_stack([z.real, z.imag]), fmt="%.8f", delimiter=",")

                samples_total += len(z)
                self._recording_samples_total = samples_total

        except Exception as e:
            print(f"录制线程错误: {e}")
        finally:
            try:
                if wf is not None:
                    wf.close()
                if fh is not None:
                    fh.close()
            except Exception:
                pass
            self.status.recording = False
            self._write_recording_sidecar(path, fmt_l, effective_rate,
                                          samples_total, decimation)

    def _write_recording_sidecar(self, path: str, fmt: str, effective_rate: int,
                                 samples_total: int, decimation: int):
        """写 sidecar 元数据；键名与 FileIQBackend 对齐，录完即可回放。"""
        try:
            metadata = {
                # FileIQBackend 直接识别的三个键
                "format": fmt,
                "sample_rate": float(effective_rate),
                "center_freq": float(self.status.frequency_hz),
                # 兼容旧字段
                "path": path,
                "center_hz": float(self.status.frequency_hz),
                "sample_rate_hz": float(effective_rate),
                "decimation": decimation,
                "samples": int(samples_total),
                "bytes": int(os.path.getsize(path)) if os.path.exists(path) else 0,
                "duration_s": float(samples_total / effective_rate) if effective_rate else 0,
                "start_unix": float(self._recording_start_time),
                "end_unix": float(time.time()),
                "gain_db": float(self.status.gain_db),
                "agc_enabled": bool(self.status.agc_enabled),
                "note": "cu8=rtl_sdr unsigned8 interleaved; cf32=float32 interleaved; "
                        "cs16=int16 interleaved; wav I=ch1 Q=ch2",
            }
            with open(path + ".json", "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"写 sidecar JSON 错误: {e}")

    def stop_recording(self) -> str:
        """停止录制，返回文件路径。定时自动停止后再调用也应幂等返回该路径。"""
        path = self.status.recording_path
        if self.status.recording:
            self._recording_stop_event.set()
            # 等待录制线程结束（最多 5 秒）
            if self._recording_thread and self._recording_thread.is_alive():
                self._recording_thread.join(timeout=5.0)

        self.status.recording = False
        self.status.recording_path = ""
        return path or ""


class MockSDRBackend(SDRBackend):
    """
    模拟 SDR 后端（用于开发测试）。

    生成模拟的 IQ 信号，包含：
    - 中心频率处的载波
    - 随机噪声
    - 可选的 FM 调制信号
    """

    def __init__(self):
        device = SDRDevice(
            device_type="mock",
            device_id="mock_0",
            name="模拟 SDR（开发测试用）",
            frequency_range=(500000, 6000000000),
            sample_rate_range=(250000, 3200000),
            max_gain=49.6,
            supports_iq=True,
            supports_tx=False,
        )
        super().__init__(device)
        self._noise_level = 0.1
        self._signal_level = 0.5
        self._fm_deviation = 75000.0

    def connect(self) -> bool:
        self.status.connected = True
        self._start_time = time.time()
        self.status.rssi_db = -60.0
        self.status.snr_db = 20.0
        return True

    def read_samples(self, num_samples: int) -> np.ndarray:
        if not self.status.connected:
            return None

        # 生成模拟 IQ 信号
        t = np.arange(num_samples) / self.status.sample_rate_hz

        # 中心载波（有微小频偏）
        freq_offset = 1000.0  # 1kHz 频偏
        carrier = self._signal_level * np.exp(1j * 2 * np.pi * freq_offset * t)

        # FM 调制（模拟广播信号）
        if self.status.demod_mode in ("FM", "NFM", "WFM"):
            modulating = 0.5 * np.sin(2 * np.pi * 1000 * t)  # 1kHz 音频
            phase = 2 * np.pi * self._fm_deviation * np.cumsum(modulating) / self.status.sample_rate_hz
            carrier = self._signal_level * np.exp(1j * phase)

        # 噪声
        noise = self._noise_level * (np.random.randn(num_samples) + 1j * np.random.randn(num_samples))

        # AGC 增益
        gain = 10 ** (self.status.gain_db / 20.0) if not self.status.agc_enabled else 1.0

        samples = (carrier + noise) * gain
        self._samples_read += num_samples

        # 更新 RSSI/SNR
        signal_power = np.mean(np.abs(carrier) ** 2)
        noise_power = np.mean(np.abs(noise) ** 2)
        self.status.rssi_db = 10 * np.log10(signal_power + noise_power) - 100
        self.status.snr_db = 10 * np.log10(signal_power / max(noise_power, 1e-10))

        return samples


class RTLSDRBackend(SDRBackend):
    """
    RTL-SDR 后端（RTL2832U + E4000/FC0012/FC0013/R820T/R820T2）。

    两种接入方式：
    - 本地 USB：RTLSDRBackend(device_index=0, ppm=...)，依赖 pyrtlsdr + librtlsdr。
    - rtl_tcp 网络：RTLSDRBackend(host="127.0.0.1", port=1234)，走 rtl_tcp 协议，
      适合本机装不上 librtlsdr / WOA / 远端共享棒 / 手机端共用一个前端。

    廉价棒晶振有 20~50ppm 频偏，务必用 ppm 校正（可由 AI 对照已知信标估计）。
    HF 短波（<24MHz）需要 direct sampling（RTL2832 直采）或外接上变频器。
    """

    # pyrtlsdr 调谐器枚举索引 → 名称
    _TUNER_NAMES = {0: "Unknown", 1: "E4000", 2: "FC0013", 3: "FC0025",
                    4: "FC2580", 5: "R820T", 6: "R828D", 7: "R860", 8: "R2000"}

    # 来源: librtlsdr src/librtlsdr.c:1100-1104 — 合法采样率区间为
    # (225000, 300000] ∪ (900000, 3200000]，300k~900k 是死区（库直接返回 -EINVAL）。
    # 常用档：低段 rtl_433 默认 250000（rtl_433/include/rtl_433.h:13），
    # 高段常用 1000000/1024000/2400000。
    _RTL_LOW_MAX = 300_000      # 低段上沿（合法）
    _RTL_HIGH_MIN = 900_000     # 死区上沿（900000 本身非法，>900000 才合法）
    _RTL_ABS_MAX = 3_200_000
    _RTL_PREFERRED_LOW = 250_000   # rtl_433 默认
    _RTL_PREFERRED_HIGH = 1_000_000  # 高段常用起点

    @classmethod
    def _clamp_sample_rate(cls, rate_hz: float) -> Tuple[float, bool]:
        """把请求采样率钳位到 librtlsdr 合法区间。

        合法: (225000, 300000] ∪ (900000, 3200000]。
        死区 (300000, 900000] 内的值按中点 600k 二分：偏下钳到 250k，偏上钳到 1M。
        低于 225k 钳到 250k；高于 3.2M 钳到 3.2M。

        返回 (clamped_rate, was_clamped)。
        """
        r = float(rate_hz)
        # 来源: librtlsdr src/librtlsdr.c:1100-1104 — 区间判定
        low_ok = (225_000 < r <= cls._RTL_LOW_MAX)
        high_ok = (cls._RTL_HIGH_MIN < r <= cls._RTL_ABS_MAX)
        if low_ok or high_ok:
            return r, False
        # 死区或越界：钳位到最近常用合法档
        if r <= 225_000:
            return float(cls._RTL_PREFERRED_LOW), True
        if r > cls._RTL_ABS_MAX:
            return float(cls._RTL_ABS_MAX), True
        # 死区 (300000, 900000]：以 600k 为界偏下偏上
        if r < 600_000:
            return float(cls._RTL_PREFERRED_LOW), True
        return float(cls._RTL_PREFERRED_HIGH), True

    def __init__(self, device_index: int = 0, host: Optional[str] = None,
                 port: int = 1234, ppm: int = 0):
        device = SDRDevice(
            device_type="rtl_sdr",
            device_id=f"rtl_{device_index}" if host is None else f"rtl_tcp_{host}_{port}",
            name=f"RTL-SDR #{device_index}" if host is None else f"rtl_tcp {host}:{port}",
            frequency_range=(500000, 1766000000),  # direct sampling 可下探至 ~0.5MHz
            # 来源: librtlsdr src/librtlsdr.c:1100-1104 — 声明为两段合法区间而非连续范围
            sample_rate_range=(225_001, 3_200_000),
            max_gain=49.6,
            supports_iq=True,
            supports_tx=False,
        )
        super().__init__(device)
        self._device_index = device_index
        self._host = host
        self._port = port
        self._ppm = ppm
        self._sdr = None
        self._direct = 0  # 0=off, 1=I, 2=Q
        self.tuner_name = "Unknown"

    @staticmethod
    def list_devices() -> list:
        """枚举本机 USB RTL-SDR，返回 [{index, serial, tuner}]，无库/无设备返回 []。"""
        try:
            from rtlsdr import RtlSdr
            out = []
            n = RtlSdr.get_device_count()
            try:
                serials = RtlSdr.get_device_serial_addresses()
            except Exception:
                serials = {}
            for i in range(n):
                tuner = "Unknown"
                try:
                    probe = RtlSdr(i)
                    tuner = RTLSDRBackend._TUNER_NAMES.get(int(probe.tuner_type), "Unknown")
                    probe.close()
                except Exception:
                    pass
                out.append({"index": i, "serial": serials.get(i, ""), "tuner": tuner})
            return out
        except Exception:
            return []

    def connect(self) -> bool:
        try:
            if self._host is not None:
                from rtlsdr import RtlSdrTcpClient
                self._sdr = RtlSdrTcpClient(hostname=self._host, port=self._port)
            else:
                from rtlsdr import RtlSdr
                self._sdr = RtlSdr(self._device_index)
            # 调谐器型号
            try:
                self.tuner_name = self._TUNER_NAMES.get(int(self._sdr.tuner_type), "Unknown")
            except Exception:
                self.tuner_name = "Unknown"
            # 上电先应用 ppm
            if self._ppm:
                self.set_ppm(self._ppm)
            self.status.connected = True
            self._start_time = time.time()
            # 回读硬件实际参数，对齐软件状态
            try:
                self.readback_hw_state()
            except Exception as e:
                logger.warning(f"RTL-SDR 回读失败: {e}")
            return True
        except Exception:
            self.status.connected = False
            return False

    def disconnect(self):
        if self._sdr:
            try:
                self._sdr.close()
            except Exception:
                pass
            self._sdr = None
        super().disconnect()

    def _apply_frequency(self, freq_hz: float) -> bool:
        if self._sdr is None:
            return True
        self._sdr.center_freq = freq_hz
        # 来源: librtlsdr src/librtlsdr.c:1702 — rtlsdr_reset_buffer；
        # rtl_433 src/sdr.c:1706 — 换频后必须 reset_buffer，否则 USB 队列里
        # 残留的旧频率 URB 会被当成新频数据读出。
        try:
            self._sdr.reset_buffer()
        except Exception as e:
            logger.warning(f"RTL-SDR reset_buffer 失败（忽略）: {e}")
        return True

    def _apply_sample_rate(self, rate_hz: float) -> bool:
        if self._sdr is None:
            return True
        # 来源: librtlsdr src/librtlsdr.c:1100-1104 — 死区 (300k,900k] 非法，
        # 先在软件层钳位到合法档，避免 pyrtlsdr 抛 EINVAL。
        clamped, was = self._clamp_sample_rate(rate_hz)
        if was:
            logger.warning(
                f"RTL-SDR 采样率 {rate_hz:.0f} Hz 落在 librtlsdr 死区/越界，"
                f"已钳位到 {clamped:.0f} Hz")
        try:
            self._sdr.sample_rate = clamped
        except Exception as e:
            logger.warning(f"RTL-SDR 设置采样率 {clamped:.0f} 失败: {e}")
            return False
        # 来源: librtlsdr src/librtlsdr.c:1702 — 换采样率后也要 reset_buffer
        try:
            self._sdr.reset_buffer()
        except Exception as e:
            logger.warning(f"RTL-SDR reset_buffer 失败（忽略）: {e}")
        return True

    def _apply_gain(self, gain_db: float) -> bool:
        if self._sdr is None:
            return True
        try:
            # 来源: librtlsdr include/rtl-sdr.h:253 + src/librtlsdr.c:1073 —
            # 手动增益必须先 set_tuner_gain_mode(dev, 1)，再 set_tuner_gain()；
            # rtl_433 src/sdr.c:1409 同样先切 manual 再设增益。
            # pyrtlsdr 的 gain 属性接收 dB 浮点，内部自动查表并 ×10 转 0.1dB
            # （等价 rtl_433 src/sdr.c:1396 atof*10），无需我们手动乘 10。
            try:
                self._sdr.set_manual_gain_mode(1)  # 1 = manual tuner gain
            except Exception:
                try:
                    self._sdr.gain_mode = 1
                except Exception:
                    pass
            self._sdr.gain = float(gain_db)
        except Exception as e:
            logger.warning(f"RTL-SDR 设置增益 {gain_db} dB 失败: {e}")
            return False
        return True

    def readback_hw_state(self) -> bool:
        if not self._sdr:
            return False
        got = 0
        try:
            self.status.frequency_hz = float(self._sdr.center_freq)
            got += 1
        except Exception:
            pass
        try:
            self.status.sample_rate_hz = float(self._sdr.sample_rate)
            got += 1
        except Exception:
            pass
        try:
            self.status.gain_db = float(self._sdr.gain)
            self.status.agc_enabled = False
            got += 1
        except Exception:
            pass
        if got:
            logger.info(f"RTL-SDR 回读硬件状态: freq={self.status.frequency_hz/1e6:.3f}MHz, "
                        f"rate={self.status.sample_rate_hz/1e3:.1f}kHz, gain={self.status.gain_db:.1f}dB")
        else:
            logger.warning("RTL-SDR 不支持参数回读")
        return got > 0

    def set_agc(self, enabled: bool) -> bool:
        if not super().set_agc(enabled):
            return False
        if self._sdr:
            try:
                # 来源: librtlsdr include/rtl-sdr.h:253 + src/librtlsdr.c:1073 —
                # tuner gain mode: 0=auto(AGC), 1=manual。
                # 开 AGC 时显式切 tuner 到 auto；关 AGC 时由后续 _apply_gain 切回 manual。
                # 注意这是 tuner 前端 AGC，与下面的 RTL2832 数字 AGC 相互独立
                # （librtlsdr src/librtlsdr.c:1157 set_agc_mode）。
                try:
                    self._sdr.set_manual_gain_mode(0 if enabled else 1)
                except Exception:
                    try:
                        self._sdr.gain_mode = 0 if enabled else 1
                    except Exception:
                        pass
                self._sdr.set_agc_mode(bool(enabled))
            except Exception:
                try:
                    self._sdr.agc_mode = bool(enabled)
                except Exception:
                    return False
        return True

    def set_bandwidth(self, bw_hz: float) -> bool:
        if not super().set_bandwidth(bw_hz):
            return False
        if self._sdr:
            try:
                self._sdr.bandwidth = int(bw_hz)
            except Exception:
                return False
        return True

    def set_ppm(self, ppm: int) -> bool:
        """设置晶振频偏校正（ppm）。廉价棒典型 20~50ppm。"""
        self._ppm = int(ppm)
        if self._sdr:
            try:
                self._sdr.freq_correction = int(ppm)
                return True
            except Exception:
                return False
        return True

    def set_direct_sampling(self, branch: str = "q") -> bool:
        """HF 短波直采：branch='q'/'i' 开启，'off' 关闭。需调谐器支持（RTL2832 原生）。"""
        mapping = {"off": 0, "i": 1, "q": 2}
        val = mapping.get(str(branch).lower(), 2)
        self._direct = val
        if self._sdr:
            try:
                self._sdr.set_direct_sampling(val)
                return True
            except Exception:
                try:
                    self._sdr.direct_sampling = ("off" if val == 0 else branch.lower())
                    return True
                except Exception:
                    return False
        return True

    def set_bias_tee(self, enabled: bool) -> bool:
        """偏置供电（RTL-SDR Blog V3/V4 等支持，给有源天线/LNA 供电）。"""
        if self._sdr:
            try:
                self._sdr.set_bias_tee(bool(enabled))
                return True
            except Exception:
                return False  # 老棒/老库不支持
        return False

    def read_samples(self, num_samples: int) -> Optional[np.ndarray]:
        if not self.status.connected or not self._sdr:
            return None
        try:
            samples = self._sdr.read_samples(num_samples)
            self._samples_read += num_samples
            return samples
        except Exception:
            # USB 拔出或设备错误，降级返回 None
            return None


class AISDRMiniBackend(SDRBackend):
    """
    自研 ai-sdr Mini 后端（SI4732 + ESP32）。

    通过 WebSocket/MCP 协议与 ESP32 通信。
    SI4732 输出解调后音频流，不输出原始 IQ。
    """

    def __init__(self, host: str = "192.168.4.1", port: int = 81):
        device = SDRDevice(
            device_type="ai_sdr_mini",
            device_id="ai_sdr_mini_0",
            name="ai-sdr Mini（自研 SI4732）",
            frequency_range=(144000, 108000000),
            sample_rate_range=(48000, 48000),
            max_gain=12.0,
            supports_iq=False,  # SI4732 不出原始 IQ
            supports_tx=False,
        )
        super().__init__(device)
        self._host = host
        self._port = port
        self._ws = None
        self._ws_lock = threading.Lock()
        self._current_mode = "fm"  # fm / am / sw

    def _send_mcp(self, method: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """发送 MCP JSON-RPC 请求并等待响应。"""
        if not self._ws:
            return None
        try:
            with self._ws_lock:
                request = {
                    "jsonrpc": "2.0",
                    "id": int(time.time() * 1000) % 100000,
                    "method": method,
                    "params": params or {},
                }
                self._ws.send(json.dumps(request))
                # 等待响应（最多 2 秒）
                self._ws.settimeout(2.0)
                result = json.loads(self._ws.recv())
                return result.get("result") or result.get("params", {}).get("result")
        except Exception:
            return None

    def connect(self) -> bool:
        """通过 WebSocket 连接 ESP32。"""
        try:
            import websocket
            url = f"ws://{self._host}:{self._port}"
            self._ws = websocket.create_connection(url, timeout=3)
            # 验证连接：调用 list_tools
            result = self._send_mcp("list_tools")
            if result is not None:
                self.status.connected = True
                self._start_time = time.time()
                # 拉取当前状态
                self._refresh_status()
                # 注入云台板载通道：gimbal board_pwm 模式经此 WebSocket 下发
                self._inject_gimbal_channel(available=True)
                return True
            else:
                self.disconnect()
                return False
        except Exception:
            self.status.connected = False
            self._ws = None
            return False

    def _inject_gimbal_channel(self, available: bool):
        """连接成功后把板子 MCP 通道注入全局云台控制器（断开则清除）。"""
        try:
            from mbdsdr_ai.sdr_tools import _get_gimbal_controller
            gc = _get_gimbal_controller()
            if available:
                gc.board.set_send_callable(
                    lambda method, params: self._send_mcp(method, params) or {}
                )
                # 若当前就是板载模式，刷新连接状态
                if gc.status.mode.value == "board_pwm":
                    gc.status.connected = True
            else:
                gc.board.set_send_callable(None)
                if gc.status.mode.value == "board_pwm":
                    gc.status.connected = False
        except Exception:
            pass  # 云台模块缺失不影响 SDR 主功能

    def gimbal_set(self, az: float, el: float) -> Optional[Dict]:
        """驱动板载舵机云台（az 0-180°, el 0-90°, 负值释放）。"""
        return self._send_mcp("gimbal_set", {"az": az, "el": el})

    def gimbal_get(self) -> Optional[Dict]:
        """读取板载云台当前角度。"""
        return self._send_mcp("gimbal_get", {})

    def disconnect(self):
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._inject_gimbal_channel(available=False)
        super().disconnect()

    def _refresh_status(self):
        """从设备拉取当前状态。"""
        result = self._send_mcp("get_status")
        if result:
            if "freq" in result:
                self.status.frequency_hz = float(result["freq"]) * 1000000 if result.get("mode_name") == "FM" else float(result["freq"]) * 1000
            if "rssi" in result:
                self.status.rssi_db = float(result["rssi"])
            if "snr" in result:
                self.status.snr_db = float(result["snr"])
            if "volume" in result:
                self.status.volume = int(result["volume"])
            if "mode_name" in result:
                self._current_mode = result["mode_name"].lower()

    def _apply_frequency(self, freq_hz: float) -> bool:
        # 根据频率范围选择 FM 或 AM 模式下发 MCP
        if 64000000 <= freq_hz <= 108000000:
            result = self._send_mcp("tune_fm", {"freq_mhz": freq_hz / 1000000})
            self._current_mode = "fm"
        elif 531000 <= freq_hz <= 1710000:
            result = self._send_mcp("tune_am", {"freq_khz": int(freq_hz / 1000)})
            self._current_mode = "am"
        else:
            # SW 短波范围（需要上变频器）
            result = self._send_mcp("tune_am", {"freq_khz": int(freq_hz / 1000)})
            self._current_mode = "sw"
        return result is not None

    def _apply_gain(self, gain_db: float) -> bool:
        # SI4732 没有直接增益控制，走 AGC；无硬件写入，视为成功
        return True

    def set_volume(self, volume: int) -> bool:
        """设置音量（0-63）。"""
        volume = max(0, min(63, volume))
        result = self._send_mcp("set_volume", {"volume": volume})
        if result is not None:
            self.status.volume = volume
            return True
        return False

    def get_gps(self) -> Optional[Dict]:
        """获取 GPS/北斗定位。"""
        return self._send_mcp("get_gps")

    def get_imu(self) -> Optional[Dict]:
        """获取 9 轴姿态。"""
        return self._send_mcp("get_imu")

    def start_record(self) -> bool:
        """开始 I2S 录音。"""
        result = self._send_mcp("start_record")
        if result is not None:
            self.status.recording = True
            return True
        return False

    def stop_record(self) -> Optional[Dict]:
        """停止录音，返回采样数。"""
        result = self._send_mcp("stop_record")
        if result is not None:
            self.status.recording = False
        return result

    def read_samples(self, num_samples: int) -> Optional[np.ndarray]:
        # SI4732 不出 IQ，返回 None
        return None


class HackRFBackend(SDRBackend):
    """
    HackRF One 后端。

    使用 hackrf 库（libhackrf + Python 绑定）。
    支持 1 MHz - 6 GHz，最大 20 MS/s，半双工收发。
    需要安装：pip install hackrf 以及 libhackrf 系统库。
    """

    def __init__(self, device_index: int = 0):
        device = SDRDevice(
            device_type="hackrf",
            device_id=f"hackrf_{device_index}",
            name=f"HackRF One #{device_index}",
            frequency_range=(1000000, 6000000000),
            sample_rate_range=(2000000, 20000000),
            max_gain=40.0,
            supports_iq=True,
            supports_tx=True,
        )
        super().__init__(device)
        self._device_index = device_index
        self._hackrf = None

    def connect(self) -> bool:
        try:
            import hackrf
            self._hackrf = hackrf.HackRF()
            self.status.connected = True
            self._start_time = time.time()
            return True
        except Exception:
            self.status.connected = False
            return False

    def disconnect(self):
        if self._hackrf:
            try:
                self._hackrf.close()
            except Exception:
                pass
            self._hackrf = None
        super().disconnect()

    def _apply_frequency(self, freq_hz: float) -> bool:
        if self._hackrf is None:
            return True
        try:
            self._hackrf.frequency = int(freq_hz)
        except Exception:
            return False
        return True

    def _apply_sample_rate(self, rate_hz: float) -> bool:
        if self._hackrf is None:
            return True
        try:
            self._hackrf.sample_rate = int(rate_hz)
        except Exception:
            return False
        return True

    def _apply_gain(self, gain_db: float) -> bool:
        if self._hackrf is None:
            return True
        try:
            self._hackrf.gain = int(gain_db)
        except Exception:
            return False
        return True

    def read_samples(self, num_samples: int) -> Optional[np.ndarray]:
        if not self.status.connected or not self._hackrf:
            return None
        try:
            samples = self._hackrf.read_samples(num_samples)
            self._samples_read += num_samples
            return samples
        except Exception:
            return None


class SoapySDRBackend(SDRBackend):
    """
    通用 SoapySDR 后端（pothosware/SoapySDR 统一抽象层）。

    SoapySDR 是 SDR 生态的"总线"：同一个 API 驱动 RTL-SDR、HackRF、USRP、BladeRF、
    Airspy、LimeSDR、Red Pitaya 等数十种前端，只要装好对应 Soapy* 支持模块即可。
    本后端按三层降级策略接入，**绝不假成功**：
      1) 有 SoapySDR Python 绑定（``import SoapySDR``）→ 原生 API 全功能；
      2) 无 Python 绑定但有 ``SoapySDRUtil`` CLI → 解析其文本输出做枚举；
      3) 两者都没有 → list_devices() 返回 [] 并 log warning，connect() 直接失败。

    关键 API 与行号（来源: repos/SoapySDR）：
      - Device::enumerate(args) 枚举入口        —— lib/Factory.cpp:41
      - 枚举结果自动注入 driver 键             —— lib/Factory.cpp:101
      - Device::make(args) 打开设备             —— lib/Factory.cpp:133
      - SOAPY_SDR_RX = 1（接收方向）            —— include/SoapySDR/Constants.h:22
      - setFrequency(RX, chan, freq)            —— include/SoapySDR/Device.hpp:801
      - getFrequencyRange(RX, chan) → RangeList —— include/SoapySDR/Device.hpp:855
      - setSampleRate(RX, chan, rate)           —— include/SoapySDR/Device.hpp:884
      - getSampleRateRange(RX, chan) → RangeList—— include/SoapySDR/Device.hpp:909
      - setGain(RX, chan, db)                   —— include/SoapySDR/Device.hpp:725
      - getGainRange(RX, chan) → Range          —— include/SoapySDR/Device.hpp:759
      - setupStream(RX, format, channels)       —— include/SoapySDR/Device.hpp:267
      - readStream(stream, buffs, n, ...)       —— include/SoapySDR/Device.hpp:352
      - Range.minimum()/maximum()               —— include/SoapySDR/Types.hpp:64
    Python 侧真实用法参考: repos/SoapySDR/swig/python/apps/MeasureDelay.py:81(setupStream),
      :108(readStream, 返回对象 .ret/.flags/.timeNs)。
    """

    # 我们用复数 float32 流（与 pyrtlsdr 返回一致，上层解调链无需改）。
    # 来源: include/SoapySDR/Device.hpp:230 附近 stream format 字符串；
    # MeasureDelay.py:81 用 SOAPY_SDR_CF32。
    _STREAM_FORMAT = "CF32"

    def __init__(self, device_args: Any = "", device_info: Optional[Dict[str, Any]] = None):
        """
        :param device_args: 打开设备用的参数字符串或 dict，例如
                            "driver=rtlsdr,serial=00000001"（来自 enumerate 结果）。
        :param device_info: enumerate 阶段拿到的身份信息 dict（label/serial/...），
                            用于构造 SDRDevice；缺省时填占位。
        """
        info = device_info or {}
        # 枚举身份字段；拿不到时给安全默认，绝不伪造真实范围。
        driver = info.get("driver", "soapy")
        label = info.get("label", str(device_args) or "SoapySDR device")
        serial = info.get("serial", "")
        # 频率/采样率/增益范围：优先用枚举阶段探测到的真实范围；
        # 探测不到时给"宽但保守"的占位范围，connect 后会用 readback_hw_state() 校正。
        freq_range = self._range_tuple(info.get("freq_range"), (1e3, 7.2e9))
        rate_range = self._range_tuple(info.get("sample_rate_range"), (1e3, 61.44e6))
        max_gain = float((info.get("gain_range") or (0.0, 60.0))[1]) \
            if isinstance(info.get("gain_range"), (tuple, list)) else 60.0

        device = SDRDevice(
            device_type=f"soapy_{driver}",
            device_id=f"soapy_{driver}_{serial or label}",
            name=label,
            frequency_range=freq_range,
            sample_rate_range=rate_range,
            max_gain=max_gain,
            supports_iq=True,
            supports_tx=False,  # 接收优先；TX 由具体 driver 决定，本后端默认只收
        )
        super().__init__(device)
        # 统一存成字符串，SoapySDR.Device() 两种都吃
        if isinstance(device_args, dict):
            self._args_str = ",".join(f"{k}={v}" for k, v in device_args.items())
        else:
            self._args_str = str(device_args)
        self._serial = serial
        self._driver = driver
        self._sdr = None       # SoapySDR.Device 实例
        self._stream = None    # setupStream 返回的流句柄
        self._chan = 0
        self._rx = 1           # SOAPY_SDR_RX，来源: Constants.h:22

    # ------------------------------------------------------------------
    # 工具：RangeList → (min, max)
    # ------------------------------------------------------------------
    @staticmethod
    def _range_tuple(rl, default: Tuple[float, float]) -> Tuple[float, float]:
        """把 SoapySDR RangeList / 单个 Range / 已有 tuple 归一成 (min, max)。

        来源: include/SoapySDR/Types.hpp:91 —— 整个 RangeList 的最小=front().minimum()，
        最大=back().maximum()。Python SWIG 对象用 .minimum()/.maximum() 方法访问。
        注意 getGainRange()（Device.hpp:759）返回单个 Range 而非 RangeList，
        而 getFrequencyRange()/getSampleRateRange() 返回 RangeList，二者都要兼容。
        """
        try:
            if not rl:
                return default
            # 单个 Range 对象（带 minimum()/maximum() 方法）
            if hasattr(rl, "minimum") and hasattr(rl, "maximum"):
                return (float(rl.minimum()), float(rl.maximum()))
            if isinstance(rl, (tuple, list)):
                if len(rl) == 2 and all(isinstance(x, (int, float)) for x in rl):
                    return (float(rl[0]), float(rl[1]))
                # RangeList 形态：元素是 Range 对象
                lo = min(float(r.minimum()) for r in rl)
                hi = max(float(r.maximum()) for r in rl)
                return (lo, hi)
        except Exception:
            pass
        return default

    # ------------------------------------------------------------------
    # 枚举：优先 Python 绑定，降级 SoapySDRUtil CLI，都没有返回 []
    # ------------------------------------------------------------------
    @classmethod
    def list_devices(cls) -> List[Dict[str, Any]]:
        """枚举真实 SoapySDR 设备。

        返回每台设备一个 dict，键：driver/label/serial/manufacturer/product/
        gain_range/freq_range/sample_rate_range/device_args。
        无 Python 绑定、无 CLI 时返回 [] 并 log warning（**绝不**返回假设备）。
        """
        # 路径 1：原生 Python 绑定
        try:
            import SoapySDR  # noqa: F401
        except Exception:
            SoapySDR = None

        if SoapySDR is not None:
            try:
                return cls._enumerate_via_python(SoapySDR)
            except Exception as e:
                logger.warning(f"SoapySDR Python 枚举失败，尝试 CLI 降级: {e}")

        # 路径 2：SoapySDRUtil --find 命令行
        try:
            return cls._enumerate_via_cli()
        except Exception as e:
            logger.warning(f"SoapySDRUtil CLI 枚举失败: {e}")

        # 路径 3：都没有 → 优雅报设备未找到
        logger.warning(
            "未发现 SoapySDR：既无 Python 绑定(import SoapySDR)，"
            "也无 SoapySDRUtil CLI。安装 SoapySDR + 对应 Soapy* 模块后可识别硬件。"
        )
        return []

    @classmethod
    def _enumerate_via_python(cls, SoapySDR) -> List[Dict[str, Any]]:
        """用 SoapySDR.Device.enumerate() 枚举；并 best-effort 开probe读真实范围。

        来源: lib/Factory.cpp:41 enumerate(args)；:101 注入 driver 键。
        enumerate 本身只给身份信息（label/serial/manufacturer/product），
        范围要 open 后查 getFrequencyRange 等（Device.hpp:855/909/759）。
        """
        # 来源: lib/Factory.cpp:41 — enumerate() 无参即枚举全部
        found = SoapySDR.Device.enumerate() or []
        out: List[Dict[str, Any]] = []
        for kwargs in found:
            # SWIG Kwargs 支持 .to_dict() 或直接迭代；做一次防御性转换
            try:
                info = dict(kwargs)
            except Exception:
                info = {str(k): str(getattr(kwargs, k, "")) for k in getattr(kwargs, "keys", lambda: [])()}
            entry = {
                "driver": info.get("driver", "?"),
                "label": info.get("label", info.get("driver", "SoapySDR device")),
                "serial": info.get("serial", ""),
                "manufacturer": info.get("manufacturer", ""),
                "product": info.get("product", ""),
                "gain_range": None,
                "freq_range": None,
                "sample_rate_range": None,
                "device_args": dict(info),
            }
            # best-effort 开一下设备读范围；失败不影响身份列出
            try:
                probe = SoapySDR.Device(info)
                rx = getattr(SoapySDR, "SOAPY_SDR_RX", 1)
                entry["freq_range"] = cls._range_tuple(
                    probe.getFrequencyRange(rx, 0), (1e3, 7.2e9))
                entry["sample_rate_range"] = cls._range_tuple(
                    probe.getSampleRateRange(rx, 0), (1e3, 61.44e6))
                entry["gain_range"] = cls._range_tuple(
                    probe.getGainRange(rx, 0), (0.0, 60.0))
                # 来源: lib/Factory.cpp:133 make() 返回的指针由 Python 端 del/close 释放
                del probe
            except Exception as e:
                logger.debug(f"probe {entry['label']} 读范围失败(忽略): {e}")
            out.append(entry)
        return out

    @classmethod
    def _enumerate_via_cli(cls) -> List[Dict[str, Any]]:
        """解析 ``SoapySDRUtil --find=""`` 的文本输出。

        SoapySDRUtil --find 输出形如：
            Found device 0:
            :driver=rtlsdr
            :label=Generic RTL2832U :: 00000001
            :serial=00000001
            :manufacturer=...
            :product=...
        CLI 路径拿不到频率/增益范围（那要 open 后查询），相关字段留 None。
        """
        import subprocess
        try:
            proc = subprocess.run(
                ["SoapySDRUtil", "--find", ""],
                capture_output=True, text=True, timeout=10,
            )
        except FileNotFoundError:
            raise RuntimeError("SoapySDRUtil 未安装")
        if proc.returncode != 0:
            raise RuntimeError(f"SoapySDRUtil --find 退出码 {proc.returncode}: {proc.stderr[:200]}")

        out: List[Dict[str, Any]] = []
        cur: Dict[str, str] = {}
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.startswith(":") and "=" in line:
                k, v = line[1:].split("=", 1)
                cur[k.strip()] = v.strip()
            elif line.startswith("Found device") and cur:
                out.append(cur)
                cur = {}
        if cur:
            out.append(cur)

        result = []
        for info in out:
            result.append({
                "driver": info.get("driver", "?"),
                "label": info.get("label", info.get("driver", "SoapySDR device")),
                "serial": info.get("serial", ""),
                "manufacturer": info.get("manufacturer", ""),
                "product": info.get("product", ""),
                "gain_range": None,
                "freq_range": None,
                "sample_rate_range": None,
                "device_args": dict(info),
            })
        return result

    # ------------------------------------------------------------------
    # 连接 / 断开：真实打开，失败返回 False（绝不切 mock 报成功）
    # ------------------------------------------------------------------
    def connect(self) -> bool:
        try:
            import SoapySDR
        except Exception as e:
            self.status.error = f"SoapySDR Python 绑定未安装，无法打开真实设备: {e}"
            logger.error(self.status.error)
            self.status.connected = False
            return False

        try:
            # 来源: lib/Factory.cpp:133 Device::make(args) —— Python 端即构造 Device(args)
            self._sdr = SoapySDR.Device(self._args_str)
            self._rx = getattr(SoapySDR, "SOAPY_SDR_RX", 1)
        except Exception as e:
            self.status.error = f"SoapySDR 打开设备失败({self._args_str}): {e}"
            logger.error(self.status.error)
            self._sdr = None
            self.status.connected = False
            return False

        try:
            # 来源: Device.hpp:267 setupStream(direction, format, channels)
            self._stream = self._sdr.setupStream(self._rx, self._STREAM_FORMAT, [self._chan])
            # 来源: Device.hpp:339 附近 activateStream —— 必须先 activate 才能 readStream
            self._sdr.activateStream(self._stream)
        except Exception as e:
            self.status.error = f"SoapySDR 建立接收流失败: {e}"
            logger.error(self.status.error)
            self.disconnect()
            return False

        self.status.connected = True
        self.status.error = ""
        self._start_time = time.time()
        # 回读硬件真实范围/参数，对齐软件状态
        try:
            self.readback_hw_state()
        except Exception as e:
            logger.warning(f"SoapySDR 回读状态失败(忽略): {e}")
        logger.info(f"SoapySDR 设备已连接: {self.device.name} (args={self._args_str})")
        return True

    def disconnect(self):
        if self._sdr is not None:
            try:
                if self._stream is not None:
                    # 来源: Device.hpp:267 附近 deactivateStream/closeStream
                    self._sdr.deactivateStream(self._stream)
                    self._sdr.closeStream(self._stream)
            except Exception as e:
                logger.debug(f"关闭流时忽略错误: {e}")
            self._stream = None
            try:
                del self._sdr  # Python 端释放 Device（对应 C++ delete）
            except Exception:
                pass
            self._sdr = None
        super().disconnect()

    # ------------------------------------------------------------------
    # 调谐 / 增益 / 采样率：真实写硬件，异常记入 status.error
    # ------------------------------------------------------------------
    def _apply_frequency(self, freq_hz: float) -> bool:
        if self._sdr is None:
            return True
        try:
            # 来源: Device.hpp:801 setFrequency(RX, chan, freq)
            self._sdr.setFrequency(self._rx, self._chan, float(freq_hz))
            return True
        except Exception as e:
            self.status.error = f"setFrequency({freq_hz}) 失败: {e}"
            return False

    def _apply_sample_rate(self, rate_hz: float) -> bool:
        if self._sdr is None:
            return True
        try:
            # 来源: Device.hpp:884 setSampleRate(RX, chan, rate)
            self._sdr.setSampleRate(self._rx, self._chan, float(rate_hz))
            return True
        except Exception as e:
            self.status.error = f"setSampleRate({rate_hz}) 失败: {e}"
            return False

    def _apply_gain(self, gain_db: float) -> bool:
        if self._sdr is None:
            return True
        try:
            # 来源: Device.hpp:725 setGain(RX, chan, db) —— 总增益
            self._sdr.setGain(self._rx, self._chan, float(gain_db))
            return True
        except Exception as e:
            self.status.error = f"setGain({gain_db}) 失败: {e}"
            return False

    def set_agc(self, enabled: bool) -> bool:
        if not super().set_agc(enabled):
            return False
        if self._sdr is not None:
            try:
                # 来源: Device.hpp:708 setGainMode(RX, chan, automatic)
                self._sdr.setGainMode(self._rx, self._chan, bool(enabled))
            except Exception as e:
                self.status.error = f"setGainMode 失败: {e}"
                return False
        return True

    def set_bandwidth(self, bw_hz: float) -> bool:
        if not super().set_bandwidth(bw_hz):
            return False
        if self._sdr is not None and bw_hz > 0:
            try:
                # 来源: Device.hpp:921 setBandwidth(RX, chan, bw)
                self._sdr.setBandwidth(self._rx, self._chan, float(bw_hz))
                return True
            except Exception as e:
                self.status.error = f"setBandwidth({bw_hz}) 失败: {e}"
                return False
        return True

    # ------------------------------------------------------------------
    #  对齐 SDR++ soapy_source 的设备控制面
    #  （来源: source_modules/soapy_source/src/main.cpp）
    # ------------------------------------------------------------------
    def set_antenna(self, antenna: str) -> bool:
        """选择接收天线。

        来源: soapy_source/main.cpp:334 setAntenna(RX,chan,antennaList[uiAntennaId])
              + :372 listAntennas()。天线名必须来自 list_antennas()，不写死。
        """
        if self._sdr is None:
            return False
        try:
            self._sdr.setAntenna(self._rx, self._chan, str(antenna))
            self._antenna = str(antenna)
            return True
        except Exception as e:
            self.status.error = f"setAntenna({antenna}) 失败: {e}"
            return False

    def list_antennas(self) -> List[str]:
        """列出本设备可用接收天线。来源: soapy_source/main.cpp:172。"""
        if self._sdr is None:
            return []
        try:
            return list(self._sdr.listAntennas(self._rx, self._chan))
        except Exception:
            return []

    def select_bandwidth_by_samplerate(self, sample_rate: float) -> float:
        """自动选一个 ≥ 采样率的最小模拟带宽。

        来源: soapy_source/main.cpp:101-115 selectBwBySr()：
          从大到小遍历带宽列表，挑第一个 >= samplerate 的；
          SDR++ 里 "Auto" 档(-1)就走这个逻辑。
        """
        if self._sdr is None:
            return 0.0
        try:
            rng = self._sdr.getBandwidthRange(self._rx, self._chan)
            cands = [float(r.minimum()) for r in rng]
        except Exception:
            return 0.0
        chosen = 0.0
        for bw in sorted(cands, reverse=True):   # 从大到小
            if bw >= sample_rate:
                chosen = bw
            else:
                break
        if chosen > 0:
            self.set_bandwidth(chosen)
        return chosen

    def recommended_block_size(self) -> int:
        """推荐单次 readStream 块大小。

        来源: soapy_source/main.cpp:501 blockSize = sampleRate/200.0f
        即每秒约 200 块。
        """
        sr = self.status.sample_rate_hz or self.device.sample_rate_range[1]
        return max(512, int(sr / 200.0))


    def readback_hw_state(self) -> bool:
        """open 后回读真实频率/采样率/增益/范围，对齐 SDRDevice 与 SDRStatus。"""
        if self._sdr is None:
            return False
        got = 0
        try:
            # 来源: Device.hpp:892 getSampleRate(RX, chan)
            self.status.sample_rate_hz = float(
                self._sdr.getSampleRate(self._rx, self._chan))
            got += 1
        except Exception:
            pass
        try:
            # 来源: Device.hpp:789 附近 getFrequency(RX, chan)
            self.status.frequency_hz = float(
                self._sdr.getFrequency(self._rx, self._chan))
            got += 1
        except Exception:
            pass
        try:
            self.status.gain_db = float(
                self._sdr.getGain(self._rx, self._chan))
            got += 1
        except Exception:
            pass
        # 用硬件真实范围覆盖构造时的占位范围
        try:
            fr = self._sdr.getFrequencyRange(self._rx, self._chan)
            self.device.frequency_range = self._range_tuple(
                fr, self.device.frequency_range)
            sr = self._sdr.getSampleRateRange(self._rx, self._chan)
            self.device.sample_rate_range = self._range_tuple(
                sr, self.device.sample_rate_range)
            gr = self._sdr.getGainRange(self._rx, self._chan)
            self.device.max_gain = float(gr.maximum())
        except Exception:
            pass
        return got > 0

    def read_samples(self, num_samples: int) -> Optional[np.ndarray]:
        """读取 IQ 样本。

        来源: Device.hpp:352 readStream(stream, buffs, n, flags, timeNs, timeoutUs)。
        Python 用法: MeasureDelay.py:108 —— 传 [numpy_complex64 数组]，
        返回 status 对象，有效样本数取 status.ret。失败(负数)记 error 并返回 None。
        """
        if not self.status.connected or self._sdr is None or self._stream is None:
            return None
        try:
            buff = np.zeros(int(num_samples), np.complex64)
            # 500ms 超时；来源: MeasureDelay.py:107 timeout_us = 5e5
            status = self._sdr.readStream(
                self._stream, [buff], int(num_samples), timeoutUs=500_000)
            n_read = int(getattr(status, "ret", 0))
            if n_read <= 0:
                # 来源: Device.hpp readStream 返回负数为错误码（如 OVERFLOW/TIMEOUT）
                if n_read < 0:
                    self.status.error = f"readStream 错误码 {n_read}"
                return None
            self._samples_read += n_read
            return buff[:n_read].copy()
        except Exception as e:
            self.status.error = f"read_samples 异常: {e}"
            return None


class USRPBackend(SDRBackend):
    """
    USRP 后端（Ettus Research / NI）。

    使用 UHD 库（uhd Python 绑定）。
    支持多种 USRP 型号（B200/B210/X300/X310/N200/N210 等）。
    需要安装：pip install uhd 以及 libuhd 系统库。
    """

    def __init__(self, device_args: str = ""):
        device = SDRDevice(
            device_type="usrp",
            device_id=f"usrp_{device_args or 'default'}",
            name=f"USRP ({device_args or 'auto'})",
            frequency_range=(10000, 6000000000),
            sample_rate_range=(100000, 56000000),
            max_gain=76.0,
            supports_iq=True,
            supports_tx=True,
        )
        super().__init__(device)
        self._device_args = device_args
        self._usrp = None
        self._rx_stream = None

    def connect(self) -> bool:
        try:
            import uhd
            self._usrp = uhd.usrp.MultiUSRP(self._device_args)
            self.status.connected = True
            self._start_time = time.time()
            return True
        except Exception:
            self.status.connected = False
            return False

    def disconnect(self):
        if self._rx_stream:
            try:
                self._rx_stream = None
            except Exception:
                pass
        if self._usrp:
            self._usrp = None
        super().disconnect()

    def _apply_frequency(self, freq_hz: float) -> bool:
        if self._usrp is None:
            return True
        try:
            import uhd
            self._usrp.set_rx_freq(uhd.libpyuhd.types.tune_request(freq_hz))
        except Exception:
            return False
        return True

    def _apply_sample_rate(self, rate_hz: float) -> bool:
        if self._usrp is None:
            return True
        try:
            self._usrp.set_rx_rate(rate_hz)
        except Exception:
            return False
        return True

    def _apply_gain(self, gain_db: float) -> bool:
        if self._usrp is None:
            return True
        try:
            self._usrp.set_rx_gain(gain_db)
        except Exception:
            return False
        return True

    def read_samples(self, num_samples: int) -> Optional[np.ndarray]:
        if not self.status.connected or not self._usrp:
            return None
        try:
            samples = self._usrp.recv_num_samps(num_samples, self.status.frequency_hz,
                                                   self.status.sample_rate_hz, [0], 0.1)
            if samples is not None and len(samples) > 0:
                self._samples_read += len(samples[0])
                return samples[0]
            return None
        except Exception:
            return None


class FileIQBackend(SDRBackend):
    """
    IQ 文件回放源（离线复现）。

    把录制的 IQ 文件当作一台 SDR 设备顺序/循环回放，便于无棒环境下反复跑解码器、
    做可复现实验（对标 SDR++ IQ file playback / GNU Radio file source）。

    支持格式（按扩展名，或 fmt= 显式指定）：
      .npy            complex 一维数组（本项目自检/录音默认）
      .cu8/.u8/.bin   unsigned 8-bit 交错 IQ（`rtl_sdr` 命令原生录制格式）
      .cfile/.cf32     complex float32 交错（GNU Radio .cfile）
      .cs16/.s16      signed 16-bit 交错
    同名 .json sidecar 可提供 {"sample_rate":..., "center_freq":..., "format":...}。
    """

    def __init__(self, path: str, sample_rate: float = 2_400_000,
                 center_freq: float = 100_000_000, loop: bool = True, fmt: str = None):
        device = SDRDevice(
            device_type="iq_file",
            device_id=f"iqfile_{os.path.basename(path)}",
            name=f"IQ 文件回放: {os.path.basename(path)}",
            frequency_range=(1, 8_000_000_000),
            sample_rate_range=(1, 61_440_000),
            max_gain=0.0,
            supports_iq=True,
            supports_tx=False,
        )
        super().__init__(device)
        self._path = path
        self._loop = loop
        self._fmt = fmt
        self._samples = None
        self._cursor = 0
        self._rate = sample_rate
        self._freq = center_freq
        self.status.sample_rate_hz = sample_rate
        self.status.frequency_hz = center_freq

    def _load(self) -> int:
        import json
        # 兼容两种 sidecar 命名：数据文件全名+.json（本项目录制/值守写出，
        # 如 x.cu8.json）与 去扩展名+.json（GNU Radio 惯例，如 x.json）。
        meta_candidates = [
            self._path + ".json",
            os.path.splitext(self._path)[0] + ".json",
        ]
        meta_path = next((p for p in meta_candidates if os.path.exists(p)), None)
        if meta_path:
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                self._rate = meta.get("sample_rate",
                             meta.get("sample_rate_hz", self._rate))
                self._freq = meta.get("center_freq",
                             meta.get("center_hz", self._freq))
                self._fmt = meta.get("format", self._fmt)
            except Exception:
                pass
        ext = (self._fmt or os.path.splitext(self._path)[1].lower().lstrip(".")).lower()
        if ext == "npy":
            self._samples = np.asarray(np.load(self._path), dtype=np.complex64)
        elif ext in ("cu8", "u8", "bin"):
            raw = np.fromfile(self._path, dtype=np.uint8)
            raw = raw[:len(raw) // 2 * 2].reshape(-1, 2).astype(np.float32)
            self._samples = np.asarray(((raw - 127.5) / 127.5).view(np.complex64).reshape(-1),
                                       dtype=np.complex64)
        elif ext in ("cfile", "cf32", "iq", "fc32"):
            raw = np.fromfile(self._path, dtype=np.float32)
            raw = raw[:len(raw) // 2 * 2].reshape(-1, 2)
            self._samples = np.asarray(raw.view(np.complex64).reshape(-1), dtype=np.complex64)
        elif ext in ("cs16", "s16", "sc16"):
            raw = np.fromfile(self._path, dtype=np.int16)
            raw = raw[:len(raw) // 2 * 2].reshape(-1, 2).astype(np.float32) / 32768.0
            self._samples = np.asarray(raw.view(np.complex64).reshape(-1), dtype=np.complex64)
        else:
            raise ValueError(f"不支持的 IQ 文件格式: {ext}（支持 npy/cu8/cfile/cs16）")
        self.status.sample_rate_hz = self._rate
        self.status.frequency_hz = self._freq
        return len(self._samples)

    def connect(self) -> bool:
        try:
            self._load()
            self.status.connected = True
            self._start_time = time.time()
            return True
        except Exception:
            self.status.connected = False
            return False

    def disconnect(self):
        self._samples = None
        self._cursor = 0
        super().disconnect()

    def read_samples(self, num_samples: int) -> Optional[np.ndarray]:
        if not self.status.connected or self._samples is None:
            return None
        n = len(self._samples)
        if self._cursor >= n:
            if not self._loop:
                return None
            self._cursor = 0
        end = self._cursor + num_samples
        if end <= n:
            out = self._samples[self._cursor:end].copy()
            self._cursor = 0 if (end == n and self._loop) else end
        else:
            tail = self._samples[self._cursor:n]
            if self._loop:
                head = self._samples[:num_samples - len(tail)]
                out = np.concatenate([tail, head])
                self._cursor = len(head)
            else:
                out = tail
                self._cursor = n
        self._samples_read += len(out)
        return out

    def seek(self, position: int):
        if self._samples is not None:
            self._cursor = max(0, min(int(position), len(self._samples) - 1))

    def playback_info(self) -> Dict[str, Any]:
        n = 0 if self._samples is None else len(self._samples)
        return {
            "path": self._path, "total_samples": n,
            "duration_s": n / self._rate if self._rate else 0,
            "cursor": self._cursor, "sample_rate": self._rate,
            "center_freq": self._freq, "loop": self._loop,
        }

    def _apply_frequency(self, freq_hz: float) -> bool:
        self._freq = freq_hz
        return True

    def _apply_sample_rate(self, rate_hz: float) -> bool:
        self._rate = rate_hz
        return True


class SDRBackendManager:
    """
    SDR 后端管理器。

    管理多个 SDR 设备的连接、切换、状态查询。
    支持异构双前端（RTL-SDR + ai-sdr Mini 同时使用）。
    """

    def __init__(self):
        self.backends: Dict[str, SDRBackend] = {}
        self.active_backend: Optional[SDRBackend] = None
        # VFO（可变频率振荡器）管理：当前中心频点 + 多个监听频率预设
        self.vfos: Dict[str, Dict] = {}  # vfo_id -> {name, frequency_hz, demod_mode, bandwidth_hz, active}
        self._vfo_counter = 0
        self.active_vfo_id: Optional[str] = None
        self._discover()

    def _discover(self):
        """发现可用的 SDR 设备。"""
        # 模拟后端总是可用
        mock = MockSDRBackend()
        self.backends[mock.device.device_id] = mock

        # 尝试发现 RTL-SDR
        try:
            from rtlsdr import RtlSdr
            sdr = RtlSdr(0)
            sdr.close()
            rtl = RTLSDRBackend(0)
            self.backends[rtl.device.device_id] = rtl
        except Exception:
            pass  # 没有 RTL-SDR

        # 尝试发现 HackRF
        try:
            import hackrf
            hf = HackRFBackend(0)
            # 不实际连接，只注册（用户手动连接）
            self.backends[hf.device.device_id] = hf
        except Exception:
            pass  # 没有 HackRF 库

        # 尝试发现 USRP
        try:
            import uhd
            usrp = USRPBackend()
            self.backends[usrp.device.device_id] = usrp
        except Exception:
            pass  # 没有 UHD 库

        # 自研 ai-sdr Mini（注册但不自动连接，用户手动连接）
        ai_mini = AISDRMiniBackend()
        self.backends[ai_mini.device.device_id] = ai_mini

        # 默认使用模拟后端
        self.active_backend = mock

    def list_devices(self) -> List[Dict[str, Any]]:
        """列出所有可用设备。"""
        return [
            {
                "device_id": b.device.device_id,
                "type": b.device.device_type,
                "name": b.device.name,
                "frequency_range": list(b.device.frequency_range),
                "sample_rate_range": list(b.device.sample_rate_range),
                "max_gain": b.device.max_gain,
                "supports_iq": b.device.supports_iq,
                "connected": b.status.connected,
                "active": b == self.active_backend,
            }
            for b in self.backends.values()
        ]

    def connect(self, device_id: str = None) -> bool:
        """连接设备。"""
        if device_id and device_id in self.backends:
            backend = self.backends[device_id]
        else:
            backend = self.active_backend

        if backend.connect():
            self.active_backend = backend
            # 连接成功后从硬件回读实际参数，对齐软件状态与硬件实际
            try:
                backend.readback_hw_state()
            except Exception as e:
                logger.warning(f"硬件回读失败（不影响连接）: {e}")
            return True
        return False

    def disconnect(self):
        """断开当前设备。"""
        if self.active_backend:
            self.active_backend.disconnect()

    def switch_device(self, device_id: str) -> bool:
        """切换到另一个设备。"""
        if device_id not in self.backends:
            return False
        if self.active_backend:
            self.active_backend.disconnect()
        self.active_backend = self.backends[device_id]
        return self.active_backend.connect()

    def get_active(self) -> Optional[SDRBackend]:
        return self.active_backend

    def open_iq_file(self, path: str, sample_rate: float = 2_400_000,
                     center_freq: float = 100_000_000, loop: bool = True,
                     fmt: str = None) -> Optional["FileIQBackend"]:
        """打开 IQ 录制文件作为回放源并切换为当前设备（离线复现实验）。"""
        be = FileIQBackend(path, sample_rate, center_freq, loop, fmt)
        if not be.connect():
            return None
        if self.active_backend and self.active_backend is not be:
            try:
                self.active_backend.disconnect()
            except Exception:
                pass
        self.backends[be.device.device_id] = be
        self.active_backend = be
        return be

    def get_status(self) -> Optional[SDRStatus]:
        if self.active_backend:
            return self.active_backend.get_status()
        return None

    # ──────────────────────────────────────────────────────────
    # VFO（可变频率振荡器）管理：监听频率预设列表
    # ──────────────────────────────────────────────────────────

    def vfo_add(self, name: str, frequency_hz: float, demod_mode: str = "FM",
                bandwidth_hz: float = 12500) -> Optional[str]:
        """添加一个 VFO。返回 vfo_id；参数非法时返回 None。"""
        try:
            if frequency_hz is None or float(frequency_hz) <= 0:
                return None
            self._vfo_counter += 1
            vfo_id = f"vfo_{self._vfo_counter}"
            self.vfos[vfo_id] = {
                "vfo_id": vfo_id,
                "name": str(name),
                "frequency_hz": float(frequency_hz),
                "demod_mode": str(demod_mode),
                "bandwidth_hz": float(bandwidth_hz),
                "active": False,
            }
            return vfo_id
        except Exception:
            return None

    def vfo_remove(self, vfo_id: str) -> bool:
        """删除一个 VFO。如果是当前激活的，先取消激活。不存在则返回 False。"""
        try:
            if vfo_id not in self.vfos:
                return False
            if self.active_vfo_id == vfo_id:
                self.active_vfo_id = None
            self.vfos.pop(vfo_id, None)
            return True
        except Exception:
            return False

    def vfo_list(self) -> List[Dict]:
        """列出所有 VFO，每个包含 vfo_id, name, frequency_hz, demod_mode, bandwidth_hz, active。"""
        try:
            return [dict(v) for v in self.vfos.values()]
        except Exception:
            return []

    def vfo_select(self, vfo_id: str) -> bool:
        """切换到指定 VFO：设置 active_backend 的频率和解调模式，标记该 VFO 为 active。"""
        try:
            if vfo_id not in self.vfos:
                return False
            vfo = self.vfos[vfo_id]
            # 切换时把之前 active 的 VFO 标记为非 active
            for vid, v in self.vfos.items():
                v["active"] = (vid == vfo_id)
            self.active_vfo_id = vfo_id
            # 应用到当前激活后端（若存在且已连接）
            be = self.active_backend
            if be is not None:
                connected = getattr(getattr(be, "status", None), "connected", False)
                if connected and hasattr(be, "set_frequency"):
                    try:
                        be.set_frequency(vfo["frequency_hz"])
                    except Exception:
                        pass
                # set_demod 不存在时只设频率
                if connected and hasattr(be, "set_demod"):
                    try:
                        be.set_demod(vfo["demod_mode"])
                    except Exception:
                        pass
            return True
        except Exception:
            return False

    def vfo_get_active(self) -> Optional[Dict]:
        """获取当前激活的 VFO 信息。无激活 VFO 时返回 None。"""
        try:
            if self.active_vfo_id and self.active_vfo_id in self.vfos:
                return dict(self.vfos[self.active_vfo_id])
            return None
        except Exception:
            return None


# ======================================================================
# 模块级：合并枚举所有 SDR 设备（SoapySDR + 原生 RTL-SDR / HackRF）
# ======================================================================

def enumerate_all_sdr_devices() -> List[Dict[str, Any]]:
    """枚举本机所有可用 SDR 设备并去重合并。

    合并来源：
      - SoapySDRBackend.list_devices() —— SoapySDR 总线识别的所有前端
        （RTL-SDR/HackRF/USRP/BladeRF/Airspy...），来源: lib/Factory.cpp:41。
      - RTLSDRBackend.list_devices() —— pyrtlsdr 原生 USB 枚举（见本文件 RTLSDRBackend）。
      - HackRFBackend —— 无专用枚举接口，靠 import 探测（本函数只做 best-effort 标识）。

    去重键：优先 serial，其次 label。SoapySDR 条目信息更全（含真实范围），
    已覆盖到同 serial 的原生条目时优先保留 SoapySDR 条目。

    返回 [] 表示"真的没发现设备"（绝不返回假设备/mock）。
    """
    merged: Dict[str, Dict[str, Any]] = {}

    def _key_of(d: Dict[str, Any]) -> str:
        return d.get("serial") or d.get("label") or d.get("driver", "?")

    # 1) SoapySDR 总线枚举（信息最全，先放）
    try:
        for dev in SoapySDRBackend.list_devices():
            merged[_key_of(dev)] = dev
    except Exception as e:
        logger.warning(f"SoapySDR 枚举异常: {e}")

    # 2) 原生 RTL-SDR（pyrtlsdr）——与 SoapySDR 的 rtlsdr 条目去重
    try:
        for dev in RTLSDRBackend.list_devices():
            serial = dev.get("serial", "")
            key = serial or f"rtl_index_{dev.get('index', 0)}"
            if key in merged:
                continue  # SoapySDR 已识别同 serial 设备，保留信息更全的那条
            merged[key] = {
                "driver": "rtlsdr",
                "label": f"RTL-SDR #{dev.get('index', 0)} ({dev.get('tuner', '?')})",
                "serial": serial,
                "manufacturer": "",
                "product": dev.get("tuner", ""),
                "gain_range": (0.0, 49.6),
                # 来源: SoapyRTLSDR/Settings.cpp:489-497 —— RTL-SDR 两段合法采样率
                "sample_rate_range": (225001, 3_200_000),
                # 来源: SoapyRTLSDR/Settings.cpp:410-427 —— R820T 类默认 24MHz~1.764GHz
                "freq_range": (24e6, 1764e6),
                "device_args": {"driver": "rtlsdr", "index": dev.get("index", 0)},
            }
    except Exception as e:
        logger.warning(f"原生 RTL-SDR 枚举异常: {e}")

    # 3) HackRF：仅当能 import hackrf 时给一个占位条目（真实打开由 connect 决定成败）
    try:
        import hackrf  # noqa: F401
        key = "hackrf_0"
        if key not in merged:
            merged[key] = {
                "driver": "hackrf",
                "label": "HackRF One (libhackrf)",
                "serial": "",
                "manufacturer": "Great Scott Gadgets",
                "product": "HackRF One",
                "gain_range": (0.0, 40.0),
                "sample_rate_range": (2e6, 20e6),
                "freq_range": (1e6, 6e9),
                "device_args": {"index": 0},
            }
    except Exception:
        pass  # 没有 hackrf 库就不列

    return list(merged.values())


def build_backend_for_device(dev: Dict[str, Any]) -> Optional[SDRBackend]:
    """根据 enumerate_all_sdr_devices() 产出的设备 dict 构造对应后端实例。

    真正 connect 的成败由后端自己负责，本函数不假装成功。
    """
    driver = (dev.get("driver") or "").lower()
    args = dev.get("device_args") or {}
    try:
        if driver == "rtlsdr" and not isinstance(args, dict) or (
                isinstance(args, dict) and args.get("index") is not None and "driver" not in args):
            # 纯 pyrtlsdr 原生条目（无 SoapySDR）
            return RTLSDRBackend(device_index=int(args.get("index", 0)))
        if driver == "hackrf":
            return HackRFBackend(device_index=int(args.get("index", 0)))
        # 其余一律走 SoapySDR 通用后端（rtlsdr 经 SoapySDR、usrp、bladerf...）
        return SoapySDRBackend(device_args=args, device_info=dev)
    except Exception as e:
        logger.error(f"构造 {driver} 后端失败: {e}")
        return None
