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


import os
import json
import threading
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple


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
        """设置频率。"""
        if not self.status.connected:
            return False
        if freq_hz < self.device.frequency_range[0] or freq_hz > self.device.frequency_range[1]:
            return False
        self.status.frequency_hz = freq_hz
        return True

    def get_frequency(self) -> float:
        return self.status.frequency_hz

    def set_sample_rate(self, rate_hz: float) -> bool:
        if not self.status.connected:
            return False
        self.status.sample_rate_hz = rate_hz
        return True

    def get_sample_rate(self) -> float:
        return self.status.sample_rate_hz

    def set_gain(self, gain_db: float) -> bool:
        if not self.status.connected:
            return False
        self.status.gain_db = max(0, min(gain_db, self.device.max_gain))
        self.status.agc_enabled = False
        return True

    def get_gain(self) -> float:
        return self.status.gain_db

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

    def __init__(self, device_index: int = 0, host: Optional[str] = None,
                 port: int = 1234, ppm: int = 0):
        device = SDRDevice(
            device_type="rtl_sdr",
            device_id=f"rtl_{device_index}" if host is None else f"rtl_tcp_{host}_{port}",
            name=f"RTL-SDR #{device_index}" if host is None else f"rtl_tcp {host}:{port}",
            frequency_range=(500000, 1766000000),  # direct sampling 可下探至 ~0.5MHz
            sample_rate_range=(250000, 3200000),
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

    def set_frequency(self, freq_hz: float) -> bool:
        if not super().set_frequency(freq_hz):
            return False
        if self._sdr:
            self._sdr.center_freq = freq_hz
        return True

    def set_sample_rate(self, rate_hz: float) -> bool:
        if not super().set_sample_rate(rate_hz):
            return False
        if self._sdr:
            self._sdr.sample_rate = rate_hz
        return True

    def set_gain(self, gain_db: float) -> bool:
        if not super().set_gain(gain_db):
            return False
        if self._sdr:
            try:
                self._sdr.gain = float(gain_db)
            except Exception:
                return False
        return True

    def set_agc(self, enabled: bool) -> bool:
        if not super().set_agc(enabled):
            return False
        if self._sdr:
            try:
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
        samples = self._sdr.read_samples(num_samples)
        self._samples_read += num_samples
        return samples


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
                self.status.rssi = float(result["rssi"])
            if "snr" in result:
                self.status.snr = float(result["snr"])
            if "volume" in result:
                self.status.volume = int(result["volume"])
            if "mode_name" in result:
                self._current_mode = result["mode_name"].lower()

    def set_frequency(self, freq_hz: float) -> bool:
        if not super().set_frequency(freq_hz):
            return False
        # 根据频率范围选择 FM 或 AM
        if 64000000 <= freq_hz <= 108000000:
            result = self._send_mcp("tune_fm", {"freq_mhz": freq_hz / 1000000})
            self._current_mode = "fm"
        elif 531000 <= freq_hz <= 1710000:
            result = self._send_mcp("tune_am", {"freq_khz": int(freq_hz / 1000)})
            self._current_mode = "am"
        else:
            # SW 短波范围（需要上变频）
            result = self._send_mcp("tune_am", {"freq_khz": int(freq_hz / 1000)})
            self._current_mode = "sw"
        return result is not None

    def set_gain(self, gain_db: float) -> bool:
        # SI4732 没有直接的增益控制，用 AGC
        if not super().set_gain(gain_db):
            return False
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

    def set_frequency(self, freq_hz: float) -> bool:
        if not super().set_frequency(freq_hz):
            return False
        if self._hackrf:
            try:
                self._hackrf.frequency = int(freq_hz)
            except Exception:
                return False
        return True

    def set_sample_rate(self, rate_hz: float) -> bool:
        if not super().set_sample_rate(rate_hz):
            return False
        if self._hackrf:
            try:
                self._hackrf.sample_rate = int(rate_hz)
            except Exception:
                return False
        return True

    def set_gain(self, gain_db: float) -> bool:
        if not super().set_gain(gain_db):
            return False
        if self._hackrf:
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

    def set_frequency(self, freq_hz: float) -> bool:
        if not super().set_frequency(freq_hz):
            return False
        if self._usrp:
            try:
                self._usrp.set_rx_freq(uhd.libpyuhd.types.tune_request(freq_hz))
            except Exception:
                return False
        return True

    def set_sample_rate(self, rate_hz: float) -> bool:
        if not super().set_sample_rate(rate_hz):
            return False
        if self._usrp:
            try:
                self._usrp.set_rx_rate(rate_hz)
            except Exception:
                return False
        return True

    def set_gain(self, gain_db: float) -> bool:
        if not super().set_gain(gain_db):
            return False
        if self._usrp:
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

    def set_frequency(self, freq_hz: float) -> bool:
        ok = super().set_frequency(freq_hz)
        self._freq = self.status.frequency_hz
        return ok

    def set_sample_rate(self, rate_hz: float) -> bool:
        ok = super().set_sample_rate(rate_hz)
        self._rate = self.status.sample_rate_hz
        return ok


class SDRBackendManager:
    """
    SDR 后端管理器。

    管理多个 SDR 设备的连接、切换、状态查询。
    支持异构双前端（RTL-SDR + ai-sdr Mini 同时使用）。
    """

    def __init__(self):
        self.backends: Dict[str, SDRBackend] = {}
        self.active_backend: Optional[SDRBackend] = None
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
