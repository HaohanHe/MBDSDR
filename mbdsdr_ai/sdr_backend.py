"""
MBDSDR AI 内核 - SDR 后端抽象层
================================
SDR Backend：统一的 SDR 硬件抽象层。

支持的后端：
- RTL-SDR（RTL2832U + E4000/FC0012/FC0013/R820T/R820T2）
- HackRF One
- USRP（UHD）
- PlutoSDR（ADALM-PLUTO / AD9361，经 libiio 或 SoapyPlutoSDR）
- 自研 ai-sdr Mini（SI4732 + ESP32，通过 MCP/WebSocket）

架构原则：每个数据域运行时只有一个真实后端；无硬件时 active_backend 为 None
（显式未连接），绝不实例化模拟后端并报 success=True。FileIQBackend 读取用户
明确选择的真实录制文件（.wav/.cf32/.iq），属合法离线回放源，保留。

PlutoSDR 接入说明：
- 首选 pylibiio（``pip install pylibiio``），USB/网络均走 ``iio.Context(uri)``；
  系统包等价物为 ``apt install libiio-dev python3-libiio``。
- 备选 SoapySDR + SoapyPlutoSDR 驱动（``SoapySDR.Device(dict(driver="plutosdr"))``）。
- 两者都不可用时，PlutoSDRBackend 类仍可导入，但 enumerate() 返回 []、connect()
  返回 False 并在 status.error 标注 "[未连接-无libiio/SoapySDR驱动]"，绝不假装收数据。
- 出厂频率 325 MHz–3.8 GHz；解锁 AD9361 校准后可到 70 MHz–6 GHz。
  本后端默认按出厂 325–3800 MHz 声明，覆盖 2.2 GHz LRO / 1.69 GHz GK-2A 接收。

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
    device_type: str  # rtl_sdr / hackrf / usrp / plutosdr / bladerf / limesdr / ai_sdr_mini / file
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
    # 来源: gqrx/src/applications/gqrx/receiver.cpp:66 —— GQRX 默认守 144.8 MHz 业余段；
    # 我们面向普通用户首启体验，落在 98 MHz FM 广播段，一上来就能听到东西。
    frequency_hz: float = 98_000_000.0
    # None 表示"未选定，由后端子类决定"。各后端在 __init__ 里设自己的甜点档
    # （RTL 2.048M / HackRF 8M / USRP 1M / Pluto 2M，来源 gqrx ioconfig.cpp:266,279-302）；
    # connect() 时若仍为 None，用 2.048M 兜底。
    sample_rate_hz: Optional[float] = None
    gain_db: float = 0.0
    agc_enabled: bool = True
    bandwidth_hz: float = 0.0  # 0=自动
    demod_mode: str = "FM"  # FM/AM/SSB/LSB/USB/CW/NFM/WFM
    # 来源: gqrx/src/qtgui/dockrxopt.cpp:697 —— reset 到 -150 dB = 完全开门，
    # 与 analog_demod.py 静噪默认对齐。
    squelch_db: float = -150.0
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

    # 若某个后端在 __init__ 里没有显式设采样率（status.sample_rate_hz 仍为 None），
    # connect() 时用 2.048M 兜底（1.024M 整数倍，便于 ADS-B/数字链路抽取）。
    _FALLBACK_SAMPLE_RATE_HZ: float = 2_048_000.0

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
        """子类覆写：把频率真正写入硬件或回放文件。默认无操作直接成功。"""
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
        # 切模式时自动按 SDR++ 默认设置解调带宽，不写死。
        # 来源: repos/sdrpp/decoder_modules/radio/src/demodulators/*/getDefaultBandwidth()
        #   WFM=150000, NFM=12500, AM=10000, USB/LSB=2800, CW=200, DSB=4600
        # 我们的 modes_defaults.py 已对齐这些值（WFM ±75k=150k 等），
        # 从表中取 high-low 作为总带宽，避免每个调用方各写一份常量。
        try:
            from .modes_defaults import get_mode_bandwidth
            bw_info = get_mode_bandwidth(mode.upper())
            self.status.bandwidth_hz = float(
                bw_info["high_hz"] - bw_info["low_hz"])
        except Exception:
            pass  # 表缺失时保留原有 bandwidth，不阻断模式切换
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


class _ThreadedRingReader:
    """生产者线程 + 环形缓冲读取器。

    对照 SDR++:
    - core/src/dsp/buffer/ring_buffer.h:4  RING_BUF_SZ = 1000000（容量）
    - ring_buffer.h:22-34 init() —— readc/writec/readable/writable 计数 + 分配 buffer
    - ring_buffer.h:36-64 read() —— waitUntilReadable → memcpy(处理回绕) → 更新计数 → notify canWrite
    - ring_buffer.h:131-160 write() —— waitUntilWritable → memcpy → 更新计数 → notify canRead
    - ring_buffer.h:186-196 stopReader/stopWriter —— 置停止标志 + notify 所有等待者
    - source_modules/rtl_sdr_source/src/main.cpp:526-539 worker()/asyncHandler() ——
      独立线程持续 read_async，把 uint8 转 float 后写 stream.swap。

    解决 QTimer 50ms 只读 4096 样点导致的严重欠读（2.048MS/s 下每帧应有 ~102k 样点）。
    缓冲满时丢弃最旧数据（不阻塞生产者），避免设备侧 USB 缓冲区溢出。
    """

    def __init__(self, read_fn, block_size: int = 8192, ring_size: int = 1_000_000):
        """
        参数:
            read_fn: callable(n) -> np.ndarray[complex64]，从设备读 n 个样点。
            block_size: 生产者每次读的样点数（对照 SDR++ main.cpp:324 asyncCount）。
            ring_size: 环形缓冲容量（复数样点数，对照 ring_buffer.h:4 RING_BUF_SZ）。
        """
        self._read_fn = read_fn
        self._block_size = int(block_size)
        self._ring_size = int(ring_size)
        # 对照 ring_buffer.h:31 _buffer = buffer::alloc<T>(size)
        self._buf = np.zeros(self._ring_size, dtype=np.complex64)
        # 对照 ring_buffer.h:27-29 writec/readc/readable
        self._read_idx = 0
        self._write_idx = 0
        self._count = 0  # 当前可读样点数（= ring_buffer.h 的 readable）
        # 对照 ring_buffer.h:234-237 _readable_mtx/_writable_mtx/canReadVar/canWriteVar。
        # 这里用单把锁 + 一个 Condition（SDR++ 是两把锁+两个 cond_var，单生产者单消费者
        # 下合并为一把锁足够，逻辑等价）。
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._stop = False  # 对照 ring_buffer.h:232-233 _stopReader/_stopWriter
        self._thread: Optional[threading.Thread] = None
        self._dropped = 0  # 缓冲满时丢弃的最旧样点数（监控用）

    def start(self):
        """启动生产者线程（对照 SDR++ main.cpp:326 workerThread = std::thread(...)）。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop = False
        self._thread = threading.Thread(
            target=self._produce_loop,
            name="mbdsdr-ring-producer",
            daemon=True,
        )
        self._thread.start()

    def _produce_loop(self):
        """对照 SDR++ main.cpp:526-529 worker() + asyncHandler()。"""
        while not self._stop:
            try:
                chunk = self._read_fn(self._block_size)
            except Exception as e:
                # 设备拔出/关闭会抛异常，退出循环
                if self._stop:
                    break
                logger.warning(f"环形缓冲生产者读设备失败: {e}")
                time.sleep(0.01)
                continue
            if chunk is None or len(chunk) == 0:
                if self._stop:
                    break
                time.sleep(0.005)
                continue
            self._write(chunk)

    def _write(self, chunk: np.ndarray):
        """对照 ring_buffer.h:131-160 write()：写满则覆盖最旧（不阻塞生产者）。"""
        n = len(chunk)
        with self._lock:
            free = self._ring_size - self._count
            if n > free:
                # 缓冲满：丢弃最旧数据（SDR++ ring_buffer 默认会阻塞写等待消费者，
                # 但设备侧 read_async 是持续供给的，消费者慢时阻塞生产者只会让 USB URB
                # 积压溢出，故选择覆盖最旧——等价于 ring_buffer.h:218 setMaxLatency
                # 限制最大延迟，这里直接用覆盖实现"丢旧保新"）。
                drop = n - free
                self._read_idx = (self._read_idx + drop) % self._ring_size
                self._count -= drop
                self._dropped += drop
            # 处理回绕，对照 ring_buffer.h:139-145
            first = min(n, self._ring_size - self._write_idx)
            self._buf[self._write_idx:self._write_idx + first] = chunk[:first]
            if n > first:
                self._buf[0:n - first] = chunk[first:]
            self._write_idx = (self._write_idx + n) % self._ring_size
            self._count += n
            # 对照 ring_buffer.h:157 canReadVar.notify_one()
            self._cond.notify_all()

    def read(self, n: int, timeout: float = 1.0) -> Optional[np.ndarray]:
        """消费者读 n 个样点；超时或停止返回 None。对照 ring_buffer.h:36-64 read()。"""
        n = int(n)
        if n <= 0:
            return np.empty(0, dtype=np.complex64)
        if n > self._ring_size:
            # 请求超过缓冲容量，无意义
            return None
        with self._lock:
            # 对照 ring_buffer.h:112-121 waitUntilReadable()
            deadline = time.time() + timeout
            while self._count < n:
                if self._stop:
                    return None
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self._cond.wait(timeout=remaining)
            if self._count < n:
                return None
            # 处理回绕拷贝，对照 ring_buffer.h:44-50
            out = np.empty(n, dtype=np.complex64)
            first = min(n, self._ring_size - self._read_idx)
            out[:first] = self._buf[self._read_idx:self._read_idx + first]
            if n > first:
                out[first:] = self._buf[0:n - first]
            self._read_idx = (self._read_idx + n) % self._ring_size
            self._count -= n
            # 对照 ring_buffer.h:61 canWriteVar.notify_one()
            self._cond.notify_all()
            return out

    def available(self) -> int:
        """当前可读样点数（对照 ring_buffer.h:123-129 getReadable()）。"""
        with self._lock:
            return self._count

    def clear(self):
        """清空缓冲（换频/换采样率后调用，丢弃旧频数据）。"""
        with self._lock:
            self._count = 0
            self._read_idx = 0
            self._write_idx = 0
            self._cond.notify_all()

    def stop(self):
        """干净停止生产者线程。对照 ring_buffer.h:186-196 stopReader/stopWriter
        + SDR++ main.cpp:332-342 stop()：置标志 → notify → join。"""
        self._stop = True
        with self._cond:
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


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

    # 来源: SDR++ source_modules/rtl_sdr_source/src/main.cpp:27-39 ——
    # rtl_sdr_source 模块硬编码的 11 个离散采样率档（用户只能在这 11 档里选）。
    # 对照 main.cpp:377-385 的 srId 下拉框：选哪档就 set 哪档，不做连续区间映射。
    SAMPLE_RATES = [
        250_000,      # main.cpp:28
        1_024_000,    # main.cpp:29
        1_536_000,    # main.cpp:30
        1_792_000,    # main.cpp:31
        1_920_000,    # main.cpp:32
        2_048_000,    # main.cpp:33
        2_160_000,    # main.cpp:34
        2_400_000,    # main.cpp:35（SDR++ 新设备默认 main.cpp:202）
        2_560_000,    # main.cpp:36
        2_880_000,    # main.cpp:37
        3_200_000,    # main.cpp:38
    ]

    # 默认采样率：对齐 hint 的 2.048 MS/s（SDR++ main.cpp:202 默认 2.4M，
    # 但我们按项目 hint 选 2.048M——1.024M 的整数倍，便于 ADS-B/数字链路抽取）。
    DEFAULT_SAMPLE_RATE = 2_048_000

    @classmethod
    def nearest_sample_rate(cls, rate_hz: float) -> int:
        """把任意请求采样率吸附到 SDR++ main.cpp:27-39 定义的最近离散档。

        对照 SDR++ main.cpp:377-385：用户只能从 11 档里选，不存在"连续采样率"。
        用最近邻（欧氏距离，平局取低档）吸附，再交给底层 _clamp_sample_rate
        做死区/越界安全检查。
        """
        return min(cls.SAMPLE_RATES, key=lambda s: (abs(s - float(rate_hz)), s))

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
        # 默认采样率：吸附到 SDR++ main.cpp:27-39 离散档里的 2.048M（见类常量
        # DEFAULT_SAMPLE_RATE 注释）。connect() 后 _apply_sample_rate 会真正下发硬件。
        self.status.sample_rate_hz = float(self.DEFAULT_SAMPLE_RATE)
        self._device_index = device_index
        self._host = host
        self._port = port
        self._ppm = ppm
        self._sdr = None
        self._direct = 0  # 0=off, 1=I, 2=Q
        self.tuner_name = "Unknown"
        # 来源: SDR++ main.cpp:485-494 offsetTuning checkbox —— 默认 False，
        # 开启后 RTL2832 把本振偏移到采样率中央，便于直流抵消。
        self._offset_tuning = False
        # 来源: SDR++ ring_buffer.h:4 RING_BUF_SZ=1000000 +
        # main.cpp:526-539 worker()/asyncHandler() —— 生产者线程持续读设备写环形缓冲，
        # 消费者按帧取数。connect() 成功后创建并 start。
        self._ring_reader: Optional["_ThreadedRingReader"] = None
        # 来源: librtlsdr src/librtlsdr.c:959-969 —— 真实离散增益表在
        # mbdsdr_ai/rtlsdr_params.py，连上探测到调谐器型号后填充。
        self._gain_table_db: list = []

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
        except ImportError as e:
            # 缺 pyrtlsdr 库：必须 log 出来，不能和"无设备"静默混为一谈
            logger.warning("pyrtlsdr 未安装，无法枚举 RTL-SDR 设备: %s", e)
            return []
        except Exception as e:
            logger.warning("枚举 RTL-SDR 设备失败: %s", e)
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
            # 来源: librtlsdr src/librtlsdr.c:956-1008 rtlsdr_get_tuner_gains ——
            # 按探测到的调谐器型号载入真实离散增益表（dB）。
            try:
                from . import rtlsdr_params as _rp
                self._gain_table_db = _rp.get_gain_table(self.tuner_name)
            except Exception:
                self._gain_table_db = []
            self.status.connected = True
            self._start_time = time.time()
            # 兜底：若子类未显式设采样率，用 2.048M 兜底（见 SDRBackend._FALLBACK_SAMPLE_RATE_HZ）
            if self.status.sample_rate_hz is None:
                self.status.sample_rate_hz = self._FALLBACK_SAMPLE_RATE_HZ

            # ═══════════════════════════════════════════════════════════════
            # SDR++ 对齐：打开设备后、启动采数前，必须逐项显式下发硬件设置。
            # 对照 repos/sdrpp/source_modules/rtl_sdr_source/src/main.cpp:307-322
            # start() 函数——上一版 connect() 只 readback 不下发，导致棒停在
            # 出厂默认频率/采样率（"插上收不到台"的根因）。
            # ═══════════════════════════════════════════════════════════════

            # 1) main.cpp:307 rtlsdr_set_sample_rate —— 先设采样率（SDR++ 顺序第一）
            self.set_sample_rate(self.status.sample_rate_hz)

            # 2) main.cpp:308 rtlsdr_set_center_freq —— 再设中心频率。
            #    SDRStatus 默认 98 MHz（FM 广播段），用户可在 connect 前通过
            #    status.frequency_hz 预设；绝不能留 0 或出厂残留。
            target_freq = self.status.frequency_hz or 98_000_000.0
            self.set_frequency(target_freq)

            # 3) main.cpp:309 rtlsdr_set_freq_correction —— 始终下发 ppm（含 0），
            #    避免上一次运行的频偏校正残留在棒上。
            self.set_ppm(self._ppm)

            # 4) main.cpp:310 rtlsdr_set_tuner_bandwidth(openDev, 0) —— 0=自动，
            #    由 librtlsdr 按采样率选最接近的模拟前端滤波器带宽。
            self.set_bandwidth(0)

            # 5) main.cpp:311 rtlsdr_set_direct_sampling —— 默认关闭（非 HF 模式）
            self.set_direct_sampling("off")

            # 6) main.cpp:312 rtlsdr_set_bias_tee —— 默认关闭（不给有源天线供电）
            self.set_bias_tee(False)

            # 7) main.cpp:313 rtlsdr_set_agc_mode + main.cpp:319
            #    rtlsdr_set_tuner_gain_mode(1) —— RTL2832 数字 AGC 关闭，
            #    调谐器切手动增益模式（SDR++ 默认 rtlAgc=false, tunerAgc=false）。
            self.set_agc(False)

            # 8) main.cpp:314/320 rtlsdr_set_tuner_gain —— 手动增益模式下必须显式
            #    下发增益值。首启时拉到增益表中点（来源 gqrx mainwindow.cpp:571-579，
            #    比 SDR++ 默认最低增益更适合"插上就有台"的用户体验）。
            self._maybe_apply_first_gain_midpoint()

            # 9) main.cpp:322 rtlsdr_set_offset_tuning —— 默认关闭；用户预开启时下发
            if self._offset_tuning:
                try:
                    self._sdr.set_offset_tuning(True)
                except Exception as e:
                    logger.warning(f"RTL-SDR 设置 offset_tuning 失败: {e}")

            # 回读硬件实际参数，验证上面的下发真的生效（而非静默失败）
            try:
                self.readback_hw_state()
            except Exception as e:
                logger.warning(f"RTL-SDR 回读失败: {e}")
            # 来源: SDR++ main.cpp:326 workerThread + 526-539 worker()/asyncHandler()
            # + ring_buffer.h:4 RING_BUF_SZ=1000000 —— 启动生产者线程持续读设备写环形缓冲，
            # 替代 QTimer 50ms 只读 4096 样点造成的严重欠读。
            try:
                self._ring_reader = _ThreadedRingReader(
                    read_fn=self._sdr.read_samples,
                    block_size=8192,
                    ring_size=1_000_000,
                )
                self._ring_reader.start()
            except Exception as e:
                logger.warning(f"RTL-SDR 启动环形缓冲生产者失败: {e}")
                self._ring_reader = None
            return True
        except Exception as e:
            # 绝不静默吞异常：把真实原因写进 status.error，UI（main_window.py）会读到并展示。
            # 无 pyrtlsdr / USB 权限 / 设备被占用等都要冒到用户，绝不伪造 IQ 假装已连接。
            self.status.connected = False
            msg = str(e)
            low = msg.lower()
            if isinstance(e, ImportError) and ("rtlsdr" in low or "no module named" in low):
                self.status.error = (
                    "未安装 pyrtlsdr/librtlsdr：pip install pyrtlsdr"
                    "（Windows 还需安装 librtlsdr DLL）"
                )
            elif any(k in low for k in (
                "permission", "busy", "resource", "access denied", "0bda", "2838", "device",
            )):
                # USB 权限/设备被占用类：保留原始异常消息，让用户看到 permission denied 等关键字
                self.status.error = f"RTL-SDR 连接失败: {type(e).__name__}: {msg}"
            else:
                self.status.error = f"RTL-SDR 连接失败: {type(e).__name__}: {msg}"
            logger.warning(
                "RTL-SDR 连接失败: %s: %s", type(e).__name__, e, exc_info=True
            )
            return False

    def disconnect(self):
        # 对照 SDR++ main.cpp:332-342 stop()：先停生产者线程（置标志+notify+join），
        # 再关设备（close 会解除生产者阻塞在 read_samples 上的等待）。
        if self._ring_reader is not None:
            try:
                self._ring_reader.stop()
            except Exception:
                pass
            self._ring_reader = None
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
        # 来源: SDR++ main.cpp:344-359 tune() —— set 后回读验证，最多重试 3 次
        # （SDR++ 原版 main.cpp:349-352 重试 10 次直到回读匹配；我们 3 次足够）。
        target = int(freq_hz)
        for attempt in range(1, 4):
            try:
                self._sdr.center_freq = target
            except Exception as e:
                logger.warning(f"RTL-SDR set_center_freq 第{attempt}次失败: {e}")
                continue
            try:
                actual = int(self._sdr.center_freq)
            except Exception:
                # 回读不可用，直接认为成功（pyrtlsdr 某些后端不支持回读）
                break
            if actual == target:
                break
            logger.warning(
                f"RTL-SDR 调谐第{attempt}次: 请求 {target}Hz, 回读 {actual}Hz, 重试")
        else:
            logger.warning(f"RTL-SDR 调谐 {target}Hz 重试3次仍未回读匹配")
        # 来源: librtlsdr src/librtlsdr.c:1702 — rtlsdr_reset_buffer；
        # rtl_433 src/sdr.c:1706 — 换频后必须 reset_buffer，否则 USB 队列里
        # 残留的旧频率 URB 会被当成新频数据读出。
        try:
            self._sdr.reset_buffer()
        except Exception as e:
            logger.warning(f"RTL-SDR reset_buffer 失败（忽略）: {e}")
        # 清空环形缓冲里换频前的旧频数据，避免新旧频 IQ 混叠
        if self._ring_reader is not None:
            self._ring_reader.clear()
        return True

    def _apply_sample_rate(self, rate_hz: float) -> bool:
        if self._sdr is None:
            return True
        # 来源: SDR++ main.cpp:27-39 + 377-385 —— 先吸附到 11 个离散档之一，
        # SDR++ 用户只能在这 11 档里选，不接受连续采样率。
        snapped = self.nearest_sample_rate(rate_hz)
        if snapped != int(rate_hz):
            logger.info(
                f"RTL-SDR 采样率 {rate_hz:.0f} Hz 吸附到 SDR++ 离散档 "
                f"{snapped} Hz (main.cpp:27-39)")
        # 来源: librtlsdr src/librtlsdr.c:1100-1104 —— 死区 (300k,900k] 非法，
        # 再做一次底层安全钳位（吸附后的档位都在合法区间内，这里基本不会触发）。
        clamped, was = self._clamp_sample_rate(float(snapped))
        if was:
            logger.warning(
                f"RTL-SDR 采样率 {snapped} Hz 落在 librtlsdr 死区/越界，"
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
            # 来源: librtlsdr src/convenience/convenience.c:116-141 nearest_gain —
            # 驱动只接受离散增益档，先把请求值吸附到最近一档，避免 pyrtlsdr
            # 内部再做隐式四舍五入造成与回读值不一致。
            target = gain_db
            if self._gain_table_db:
                snapped = min(self._gain_table_db, key=lambda g: abs(g - gain_db))
                if abs(snapped - gain_db) > 1e-6:
                    logger.info(
                        f"RTL-SDR 增益 {gain_db:.1f}dB 吸附到最近离散档 {snapped:.1f}dB "
                        f"(调谐器 {self.tuner_name})")
                target = snapped
            try:
                self._sdr.set_manual_gain_mode(1)  # 1 = manual tuner gain
            except Exception:
                try:
                    self._sdr.gain_mode = 1
                except Exception:
                    pass
            self._sdr.gain = float(target)
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

    def _maybe_apply_first_gain_midpoint(self) -> None:
        """首启增益自动拉到增益表中点（来源 gqrx mainwindow.cpp:571-579）。

        仅在回读后 gain_db 仍为 0.0（用户从未手动设过增益）且离散增益表已加载时触发；
        用户已手动设过非零增益时不覆盖。
        """
        if self.status.gain_db == 0.0 and self._gain_table_db:
            mid_gain = float(self._gain_table_db[len(self._gain_table_db) // 2])
            if self._apply_gain(mid_gain):
                self.status.gain_db = mid_gain
                logger.info("RTL-SDR 首启自动增益: 0 dB -> %.1f dB（增益表中点，"
                            "来源 gqrx mainwindow.cpp:571-579）", mid_gain)

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

    def set_offset_tuning(self, enabled: bool) -> bool:
        """Offset Tuning（DC 偏移抵消）。

        来源: SDR++ main.cpp:485-494 offsetTuning checkbox + main.cpp:322
        rtlsdr_set_offset_tuning。开启后 RTL2832 把本振偏移到采样率中央，
        让直流尖峰落到基带边缘，便于数字 DC 抵消。pyrtlsdr 暴露 set_offset_tuning。
        """
        self._offset_tuning = bool(enabled)
        if self._sdr:
            try:
                self._sdr.set_offset_tuning(bool(enabled))
                return True
            except Exception as e:
                logger.warning(f"RTL-SDR set_offset_tuning({enabled}) 失败: {e}")
                return False
        return True

    def read_samples(self, num_samples: int) -> Optional[np.ndarray]:
        if not self.status.connected or not self._sdr:
            return None
        # 优先走生产者线程填充的环形缓冲（对照 SDR++ ring_buffer.h:36-64 read()），
        # 接口签名不变，main_window 无感知。
        if self._ring_reader is not None:
            try:
                samples = self._ring_reader.read(num_samples, timeout=1.0)
            except Exception:
                return None
            if samples is None:
                return None
            self._samples_read += len(samples)
            return samples
        # 兜底：环形缓冲未启动时直接同步读（保持原行为）
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

    硬件量程/增益档全部来自 mbdsdr_ai/hackrf_params.HackRFParams（移植自
    repos/hackrf 的 libhackrf 与 max2837 固件驱动），关键来源：
      - 频率 1-6000 MHz        host/libhackrf/src/hackrf.h:235,662,670
      - 采样率 2-20 MHz       host/libhackrf/src/hackrf.h:247,1794
      - LNA(RX IF) 0-40/8dB   host/libhackrf/src/hackrf.c:2027,2031; firmware/common/max2837.c:344-371
      - VGA(RX BB) 0-62/2dB   host/libhackrf/src/hackrf.c:2054,2058; firmware/common/max2837.c:373-381
      - TXVGA 0-47/1dB        host/libhackrf/src/hackrf.c:2081; firmware/common/max2837.c:383-395
    更完整的 ctypes 直连后端见 mbdsdr_ai/hackrf_params.py:HackRFBackend。
    """

    def __init__(self, device_index: int = 0):
        # 量程/增益档一律用真实参数表，不再在本处硬编码魔数。
        from .hackrf_params import HackRFParams
        self._params = HackRFParams()
        device = SDRDevice(
            device_type="hackrf",
            device_id=f"hackrf_{device_index}",
            name=f"HackRF One #{device_index}",
            frequency_range=(self._params.min_freq_hz, self._params.max_freq_hz),
            sample_rate_range=(self._params.min_sr_hz, self._params.max_sr_hz),
            max_gain=float(self._params.lna_max_db),  # RX 路径最大前端增益档
            supports_iq=True,
            supports_tx=True,
        )
        super().__init__(device)
        self._device_index = device_index
        self._hackrf = None
        self._lna_db = 8
        self._vga_db = 16
        # 来源: gqrx/src/qtgui/ioconfig.cpp:279-302 —— HackRF 默认 8 MS/s 甜点档
        # （GQRX ioconfig 里 HackRF start_sample_rate=8e6）。
        self.status.sample_rate_hz = 8_000_000.0

    def connect(self) -> bool:
        try:
            import hackrf
            self._hackrf = hackrf.HackRF()
            self.status.connected = True
            self._start_time = time.time()
            if self.status.sample_rate_hz is None:
                self.status.sample_rate_hz = self._FALLBACK_SAMPLE_RATE_HZ
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
        """默认总增益映射到 VGA(基带) 档，自动吸附到偶数 dB（hackrf.c:2058）。"""
        if self._hackrf is None:
            return True
        try:
            v = self._params.clamp_vga_gain(gain_db)
            self._hackrf.vga_gain = v
            self._vga_db = v
        except Exception:
            return False
        return True

    def set_lna_gain(self, db: int) -> bool:
        """真实设置 RX IF(LNA) 增益，吸附到 8dB 档（hackrf.c:2031）。

        来源: host/libhackrf/src/hackrf.c:2022-2047；firmware/common/max2837.c:344-371。
        """
        if not self.status.connected or self._hackrf is None:
            return False
        v = self._params.clamp_lna_gain(db)
        try:
            self._hackrf.lna_gain = v
            self._lna_db = v
            return True
        except Exception:
            return False

    def set_vga_gain(self, db: int) -> bool:
        """真实设置 RX 基带(VGA) 增益，吸附到偶数 dB 档（hackrf.c:2058）。

        来源: host/libhackrf/src/hackrf.c:2049-2074；firmware/common/max2837.c:373-381。
        """
        if not self.status.connected or self._hackrf is None:
            return False
        v = self._params.clamp_vga_gain(db)
        try:
            self._hackrf.vga_gain = v
            self._vga_db = v
            return True
        except Exception:
            return False

    def set_txvga_gain(self, db: int) -> bool:
        """真实设置 TX IF 增益，钳到 0-47 dB（hackrf.c:2081）。"""
        if not self.status.connected or self._hackrf is None:
            return False
        v = self._params.clamp_txvga_gain(db)
        try:
            self._hackrf.txvga_gain = v
            return True
        except Exception:
            return False

    def set_bias_tee(self, on: bool) -> bool:
        """开关天线偏置 3.3V/50mA（hackrf.c:2102）。"""
        if not self.status.connected or self._hackrf is None:
            return False
        try:
            self._hackrf.antenna_power = bool(on)
            return True
        except Exception:
            return False

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
        # 保留 enumerate 原样返回的 dict（含 driver/serial/label/manufacturer/product），
        # connect() 直接把它传回 SoapySDR.Device()——这是官方标准用法
        # （MeasureDelay.py:81 附近 Device(enumerate()[i])）。自拼字符串会被 label 里的
        # 逗号/空格误导，所以优先 dict。_args_str 仅用于日志展示。
        self._device_args = device_args
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
            import SoapySDR
        except ImportError as e:
            SoapySDR = None
            # 红线提示：明确告诉用户怎么装，绝不静默把"没装库"和"没插设备"混为一谈
            logger.warning(
                "未安装 SoapySDR Python 绑定：pip install SoapySDR"
                "（并按需安装 SoapyRTLSDR/SoapyHackRF/SoapyPlutoSDR 等驱动模块）。"
                "原始导入错误: %s", e)
        except Exception as e:
            SoapySDR = None
            logger.warning(f"SoapySDR 导入异常，降级 CLI 枚举: {e}")

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

        # 路径 3：都没有 → 优雅报设备未找到（返回 []，绝不抛异常到上层、绝不假设备）
        logger.warning(
            "未发现 SoapySDR：既无 Python 绑定(import SoapySDR)，"
            "也无 SoapySDRUtil CLI。请执行 pip install SoapySDR，"
            "并按需安装对应 Soapy* 驱动模块后再识别硬件。"
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
            # 红线：无 Python 绑定时绝不切 mock 报成功；错误原因冒到 UI。
            self.status.error = (
                f"未安装 SoapySDR：pip install SoapySDR（并装对应 Soapy* 驱动）。"
                f"原始导入错误: {e}")
            logger.error(self.status.error)
            self.status.connected = False
            return False

        try:
            # 来源: lib/Factory.cpp:133 Device::make(args) —— Python 端即构造 Device(args)。
            # enumerate() 返回的 dict 可直接传回（官方标准用法）；空串/空 dict 表示自动选第一台。
            self._sdr = SoapySDR.Device(self._device_args)
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
        返回 status 对象，有效样本数取 status.ret。

        红线：
          - 未连接/无流 → 返回 None（错误态）；
          - 已连接但 readStream 超时(-1)/溢出(-2) → 返回 **空 complex64 数组**（len=0），
            绝不把上面零填充的 buff 当真实 IQ 吐出去（那是造假数据）。
            上层 _poll_sdr_iq 见 iq.size<64 即跳过本帧，天然兼容。
          - 抛异常（设备拔出/流损坏）→ 返回 None 并把原因写进 status.error。
        """
        if not self.status.connected or self._sdr is None or self._stream is None:
            return None
        try:
            buff = np.zeros(int(num_samples), np.complex64)
            # 500ms 超时；来源: MeasureDelay.py:107 timeout_us = 5e5。
            # flags=0, timeNs=0（单次读，不靠硬件时间戳同步）。
            status = self._sdr.readStream(
                self._stream, [buff], int(num_samples), 0, 0, timeoutUs=500_000)
            n_read = int(getattr(status, "ret", 0))
            if n_read <= 0:
                # 来源: Device.hpp readStream 返回负数为状态码（TIMEOUT=-1/OVERFLOW=-2）。
                # 这些是"本帧没新样本"的瞬时状态，不是硬错误：返回空数组让上层跳过。
                if n_read < 0:
                    logger.debug(
                        f"readStream 瞬时状态码 {n_read}（本帧无新样本，跳过）")
                return np.empty(0, np.complex64)
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
        # 来源: gqrx/src/qtgui/ioconfig.cpp:279-302 —— USRP 默认 1 MS/s 起步档
        # （GQRX ioconfig 里 USRP start_sample_rate=1e6）。
        self.status.sample_rate_hz = 1_000_000.0

    def connect(self) -> bool:
        try:
            import uhd
            self._usrp = uhd.usrp.MultiUSRP(self._device_args)
            self.status.connected = True
            self._start_time = time.time()
            if self.status.sample_rate_hz is None:
                self.status.sample_rate_hz = self._FALLBACK_SAMPLE_RATE_HZ
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


class PlutoSDRBackend(SDRBackend):
    """
    ADALM-PLUTO (PlutoSDR) 后端 —— 基于 AD9361 射频收发器。

    硬件能力（出厂固件）：
      - 频率：325 MHz – 3.8 GHz（解锁 AD9361 校准表后可到 70 MHz – 6 GHz）
      - 采样率：最高 61.44 MSPS，常用 2.5 – 10 MSPS
      - 接收：1x1 MIMO（RX only 默认；TX 另开通道）
      - 增益：RX 0 – 73 dB（manual / slow_attack / fast_attack AGC）

    接入方式（按优先级）：
      1) pylibiio（``import iio``）—— 原生 IIO 接口，USB / 网络直连；
         URI 形如 ``ip:192.168.2.1``（USB gadget 网卡）、``ip:pluto.local``、
         ``usb:1.2.3``（裸 USB 总线地址）。
      2) SoapySDR + SoapyPlutoSDR —— ``SoapySDR.Device(dict(driver="plutosdr"))``。
      3) 两者都没有 —— 类仍可导入，enumerate() 返回 []，connect() 返回 False，
         status.error 标注 "[未连接-无libiio/SoapySDR驱动]"，**绝不**假装收数据。

    IIO 属性映射（来源: AD9361 Linux 驱动Documentation/iio/ 与
      Analog Devices wiki "AD9361 IIO Device Tree Bindings"）：
      - 本地振荡器中心频率  -> ``ad9361-phy`` 通道 ``altvoltage0``(RX_LO)/``altvoltage1``(TX_LO)
        的 ``frequency`` 属性；本后端 RX 默认写 ``altvoltage0``。
      - RX 采样率            -> ``ad9361-phy`` 通道 ``voltage0``(RX) 的 ``sampling_frequency``
      - RX 模拟带宽          -> ``ad9361-phy`` 通道 ``voltage0`` 的 ``rf_bandwidth``
      - RX 增益模式          -> ``ad9361-phy`` 通道 ``voltage0`` 的 ``gain_control_mode``
        （manual / slow_attack / fast_attack）
      - RX 硬件增益          -> ``ad9361-phy`` 通道 ``voltage0`` 的 ``hardwaregain``
      - 数据流 buffer        -> ``cf-ad9361-lpc`` 设备，通道 ``voltage0``（16-bit I/Q 交错）

    安装：``pip install pylibiio`` 或 ``apt install libiio-dev python3-libiio``。
    """

    # 出厂频率范围（AD9361 校准表默认）。解锁后可到 70e6 – 6000e6。
    FREQ_MIN_HZ = 325_000_000.0
    FREQ_MAX_HZ = 3_800_000_000.0
    # 解锁后的扩展范围（仅在显式 unlock=True 时使用）
    FREQ_MIN_UNLOCKED_HZ = 70_000_000.0
    FREQ_MAX_UNLOCKED_HZ = 6_000_000_000.0
    # AD9361 采样率量程
    SR_MIN_HZ = 521_000.0
    SR_MAX_HZ = 61_440_000.0
    # RX 增益量程（dB）
    GAIN_MIN_DB = 0.0
    GAIN_MAX_DB = 73.0
    # 网络模式下 PlutoSDR 固定 USB gadget IP
    DEFAULT_NET_URI = "ip:192.168.2.1"

    def __init__(self, uri: Optional[str] = None, unlocked: bool = False,
                 device_info: Optional[Dict[str, Any]] = None):
        """
        :param uri: IIO context URI；None 时由 enumerate() 决定（先 USB 后网络）。
        :param unlocked: True 时按 70 MHz – 6 GHz 声明频率范围（需固件已解锁）。
        :param device_info: enumerate 阶段拿到的身份信息（label/serial/...）。
        """
        info = device_info or {}
        if unlocked:
            freq_range = (self.FREQ_MIN_UNLOCKED_HZ, self.FREQ_MAX_UNLOCKED_HZ)
        else:
            freq_range = (self.FREQ_MIN_HZ, self.FREQ_MAX_HZ)
        device = SDRDevice(
            device_type="plutosdr",
            device_id=f"plutosdr_{uri or 'auto'}",
            name=info.get("label") or "PlutoSDR (ADALM-PLUTO)",
            frequency_range=freq_range,
            sample_rate_range=(self.SR_MIN_HZ, self.SR_MAX_HZ),
            max_gain=self.GAIN_MAX_DB,
            supports_iq=True,
            supports_tx=True,  # AD9361 半双工收发
        )
        super().__init__(device)
        self._uri = uri
        self._unlocked = unlocked
        self._serial = info.get("serial", "")
        # 运行时句柄
        self._ctx = None           # iio.Context
        self._phy = None           # iio.Device (ad9361-phy)
        self._rx_chan = None       # iio.Channel (voltage0)
        self._rx_lo_chan = None    # iio.Channel (altvoltage0 = RX_LO)
        self._stream_dev = None    # iio.Device (cf-ad9361-lpc / cf-ad9361-dds-core-lpc)
        self._buffer = None        # iio.Buffer
        self._soapy = None         # SoapySDR.Device（备用路径）
        self._soapy_stream = None
        self._backend_lib = None   # "iio" / "soapy" / None
        # 默认参数（未连接时也记录，便于上层查询）
        self.status.sample_rate_hz = 2_000_000.0
        self.status.frequency_hz = 1_690_000_000.0  # GK-2A LR-M 默认

    # ------------------------------------------------------------------
    # 驱动可用性探测
    # ------------------------------------------------------------------
    @classmethod
    def _try_import_iio(cls):
        """尝试 import iio；失败返回 None。"""
        try:
            import iio  # pylibiio
            return iio
        except Exception:
            return None

    @classmethod
    def _try_import_soapy(cls):
        """尝试 import SoapySDR；失败返回 None。"""
        try:
            import SoapySDR
            return SoapySDR
        except Exception:
            return None

    @classmethod
    def driver_status(cls) -> Dict[str, Any]:
        """返回当前环境驱动可用性摘要（供上层 UI / 日志展示）。"""
        return {
            "pylibiio": cls._try_import_iio() is not None,
            "soapysdr": cls._try_import_soapy() is not None,
            "note": "需要 pip install pylibiio 或 apt install libiio-dev python3-libiio"
                    "（或 SoapySDR + SoapyPlutoSDR）。",
        }

    # ------------------------------------------------------------------
    # enumerate()：扫描 USB / 网络 PlutoSDR
    # ------------------------------------------------------------------
    @classmethod
    def enumerate(cls) -> List[Dict[str, Any]]:
        """扫描可用 PlutoSDR 设备。

        优先用 pylibiio 的 ``iio.scan_contexts()``（USB 枚举）；
        失败/不可用时回退探测默认网络 URI ``ip:192.168.2.1``（短超时）。
        无任何驱动时返回 []，不抛异常。

        返回形如：
            [{"driver": "plutosdr", "uri": "ip:192.168.2.1",
              "name": "PlutoSDR", "serial": "...",
              "freq_range": (325e6, 3.8e9), "sample_rate_range": (...),
              "gain_range": (0.0, 73.0)}]
        """
        out: List[Dict[str, Any]] = []
        iio_mod = cls._try_import_iio()

        if iio_mod is not None:
            # 路径 1：libiio scan_contexts() —— 枚举 USB / 网络 / 串行上下文
            try:
                contexts = iio_mod.scan_contexts() or {}
                # scan_contexts() 返回 dict: {uri: description}
                for uri, desc in contexts.items():
                    uri_s = str(uri)
                    desc_s = str(desc) if desc else ""
                    # 只保留看起来像 PlutoSDR 的设备
                    if cls._uri_is_pluto(uri_s, desc_s):
                        out.append(cls._make_entry(uri_s, desc_s))
            except Exception as e:
                logger.debug(f"iio.scan_contexts() 失败: {e}")

            # 路径 1b：USB 枚举兜底（scan_contexts 在某些后端下返回空）
            if not out:
                for usb_uri in ("usb:1.2.5", "usb:1.2.4", "usb:1.2.3"):
                    try:
                        probe = iio_mod.Context(usb_uri)
                        # 能打开就认为是 PlutoSDR
                        out.append(cls._make_entry(usb_uri, "PlutoSDR (USB)"))
                        del probe
                        break
                    except Exception:
                        continue

        # 路径 2：网络兜底 —— 尝试默认 192.168.2.1
        if not out:
            net_entry = cls._probe_network_default(iio_mod)
            if net_entry is not None:
                out.append(net_entry)

        if not out:
            # 无设备 / 无驱动：静默返回空列表（调用方不应报错）
            logger.debug("PlutoSDRBackend.enumerate(): 未发现 PlutoSDR 设备"
                         "（无 libiio/SoapySDR 或无硬件）。")
        return out

    @classmethod
    def _uri_is_pluto(cls, uri: str, desc: str) -> bool:
        """启发式判断 IIO 上下文是否为 PlutoSDR。"""
        text = (uri + " " + desc).lower()
        # PlutoSDR 常见标识
        pluto_markers = ("pluto", "adalm", "ad9364", "ad9363a", "192.168.2.1",
                         "1.2.3", "1.2.4", "1.2.5", "1.2.6")
        return any(m in text for m in pluto_markers)

    @classmethod
    def _make_entry(cls, uri: str, desc: str) -> Dict[str, Any]:
        return {
            "driver": "plutosdr",
            "uri": uri,
            "label": desc or "PlutoSDR (ADALM-PLUTO)",
            "name": "PlutoSDR",
            "serial": "",
            "manufacturer": "Analog Devices",
            "product": "ADALM-PLUTO",
            "freq_range": (cls.FREQ_MIN_HZ, cls.FREQ_MAX_HZ),
            "sample_rate_range": (cls.SR_MIN_HZ, cls.SR_MAX_HZ),
            "gain_range": (cls.GAIN_MIN_DB, cls.GAIN_MAX_DB),
            "device_args": {"uri": uri},
        }

    @classmethod
    def _probe_network_default(cls, iio_mod) -> Optional[Dict[str, Any]]:
        """尝试打开默认网络 URI；成功返回 entry，失败/超时返回 None。

        用 socket 先做一次 TCP 连通性探测（短超时），避免 iio.Context 长时间阻塞。
        """
        import socket
        host = "192.168.2.1"
        # 先 ping TCP 30431 (libiio 网络默认端口)
        try:
            with socket.create_connection((host, 30431), timeout=0.5):
                pass
        except Exception:
            return None
        # 端口通，再用 iio.Context 验证身份
        if iio_mod is None:
            return None
        try:
            ctx = iio_mod.Context(cls.DEFAULT_NET_URI)
            # 校验是否真的是 ad9361
            has_phy = any("ad9361" in (dev.name or "").lower()
                          for dev in ctx.devices)
            del ctx
            if has_phy:
                return cls._make_entry(cls.DEFAULT_NET_URI,
                                       f"PlutoSDR (network {host})")
        except Exception as e:
            logger.debug(f"网络 PlutoSDR 探测失败: {e}")
        return None

    # ------------------------------------------------------------------
    # open / close（connect/disconnect 别名）
    # ------------------------------------------------------------------
    def connect(self) -> bool:
        """打开 PlutoSDR。优先 pylibiio，回退 SoapySDR，都没有则返回 False。"""
        # 先探测驱动
        iio_mod = self._try_import_iio()
        soapy_mod = self._try_import_soapy()

        if iio_mod is None and soapy_mod is None:
            self.status.error = ("[未连接-无libiio/SoapySDR驱动] "
                                 "请 pip install pylibiio 或安装 SoapyPlutoSDR。")
            logger.warning(self.status.error)
            self.status.connected = False
            return False

        # 决定 URI：显式 > 枚举到的第一个 > 默认网络
        uri = self._uri
        if not uri:
            try:
                found = self.enumerate()
                if found:
                    uri = found[0]["uri"]
            except Exception:
                uri = None
        if not uri:
            uri = self.DEFAULT_NET_URI

        # 路径 1：pylibiio 原生
        if iio_mod is not None:
            try:
                return self._open_via_iio(iio_mod, uri)
            except Exception as e:
                self.status.error = f"[libiio] 打开 {uri} 失败: {e}"
                logger.warning(self.status.error)
                # 继续尝试 SoapySDR

        # 路径 2：SoapySDR
        if soapy_mod is not None:
            try:
                return self._open_via_soapy(soapy_mod, uri)
            except Exception as e:
                self.status.error = f"[SoapySDR] 打开 PlutoSDR 失败: {e}"
                logger.error(self.status.error)
                self.status.connected = False
                return False

        self.status.connected = False
        return False

    def _open_via_iio(self, iio_mod, uri: str) -> bool:
        """通过 pylibiio 打开设备并配置 RX 通道。"""
        self._ctx = iio_mod.Context(uri)
        # 找 ad9361-phy
        phy = None
        stream = None
        for dev in self._ctx.devices:
            name = (dev.name or "").lower()
            if "ad9361-phy" in name or "ad9364" in name:
                phy = dev
            elif "cf-ad9361" in name and ("lpc" in name or "dds" in name):
                stream = dev
        if phy is None:
            raise RuntimeError(f"{uri} 上未找到 ad9361-phy 设备")
        self._phy = phy

        # RX LO: altvoltage0（ad9361-phy 输出通道，控制 RX 本振）
        self._rx_lo_chan = self._find_channel(phy, "altvoltage0", is_output=True)
        # RX 基带通道: voltage0（输入）
        self._rx_chan = self._find_channel(phy, "voltage0", is_output=False)

        # 数据流设备
        if stream is None:
            # 兜底：找任意带 buffer 能力的设备
            for dev in self._ctx.devices:
                if dev.buffer_attrs or dev.channels:
                    stream = dev
                    break
        self._stream_dev = stream

        # 应用默认参数
        self._apply_sample_rate(self.status.sample_rate_hz)
        self._apply_bandwidth(self.status.sample_rate_hz * 0.8)
        self._apply_frequency(self.status.frequency_hz)
        self._apply_gain(self.status.gain_db)

        # 创建 RX buffer（4 MSamples，16-bit I/Q 交错 = 16 MB）
        self._create_iio_buffer(4_000_000)

        self._backend_lib = "iio"
        self.status.connected = True
        self.status.error = ""
        self._start_time = time.time()
        logger.info(f"PlutoSDR 已通过 libiio 打开: {uri}")
        try:
            self.readback_hw_state()
        except Exception as e:
            logger.debug(f"PlutoSDR 回读状态失败(忽略): {e}")
        return True

    def _find_channel(self, dev, channel_id: str, is_output: Optional[bool] = None):
        """在 iio.Device 上按 channel_id 找 Channel。"""
        for ch in dev.channels:
            if ch.id == channel_id:
                if is_output is None or ch.output == is_output:
                    return ch
        return None

    def _create_iio_buffer(self, num_samples: int):
        """在 stream 设备上创建 RX 采样 buffer。"""
        if self._stream_dev is None:
            return
        # 使能 RX 通道
        rx_ch = None
        for ch in self._stream_dev.channels:
            if not ch.output:
                rx_ch = ch
                break
        if rx_ch is not None:
            rx_ch.enabled = True
        # samples_count 是"每个通道的样本数"；16-bit I/Q 交错
        self._buffer = self._stream_dev.create_buffer(num_samples, circular=False)

    def _open_via_soapy(self, SoapySDR, uri: str) -> bool:
        """通过 SoapySDR + SoapyPlutoSDR 打开。"""
        args = {"driver": "plutosdr"}
        # 把 uri 转成 SoapySDR 能识别的参数
        if uri.startswith("ip:"):
            args["remote"] = uri[3:]
        elif uri.startswith("usb:"):
            args["soapy"] = uri  # SoapyPlutoSDR 接受 usb: URI
        self._soapy = SoapySDR.Device(args)
        self._rx = getattr(SoapySDR, "SOAPY_SDR_RX", 1)
        self._stream = self._soapy.setupStream(self._rx, "CF32", [0])
        self._soapy.activateStream(self._stream)
        self._backend_lib = "soapy"
        self.status.connected = True
        self.status.error = ""
        self._start_time = time.time()
        logger.info(f"PlutoSDR 已通过 SoapySDR 打开: {args}")
        try:
            self.readback_hw_state()
        except Exception as e:
            logger.debug(f"PlutoSDR(Soapy) 回读失败(忽略): {e}")
        return True

    def disconnect(self):
        """关闭 buffer / context / stream。"""
        try:
            if self._buffer is not None:
                self._buffer = None  # iio Buffer GC 释放
        except Exception:
            pass
        try:
            if self._soapy_stream is not None and self._soapy is not None:
                self._soapy.deactivateStream(self._soapy_stream)
                self._soapy.closeStream(self._soapy_stream)
        except Exception:
            pass
        self._soapy_stream = None
        self._soapy = None
        self._ctx = None
        self._phy = None
        self._rx_chan = None
        self._rx_lo_chan = None
        self._stream_dev = None
        self._backend_lib = None
        super().disconnect()

    # open/close 别名（与任务要求的接口名对齐）
    def open(self, uri: Optional[str] = None) -> bool:
        if uri:
            self._uri = uri
            self.device.device_id = f"plutosdr_{uri}"
        return self.connect()

    def close(self):
        self.disconnect()

    # ------------------------------------------------------------------
    # 调谐 / 采样率 / 增益
    # ------------------------------------------------------------------
    def _apply_frequency(self, freq_hz: float) -> bool:
        """写 RX LO 频率（ad9361-phy/altvoltage0:frequency）。"""
        if self._backend_lib == "iio":
            try:
                if self._rx_lo_chan is not None:
                    self._rx_lo_chan.attrs["frequency"].value = str(int(freq_hz))
                else:
                    # 兜底：写 phy 直接属性
                    self._phy.attrs["out_altvoltage0_RX_LO_frequency"].value = str(int(freq_hz))
                return True
            except Exception as e:
                self.status.error = f"[iio] set frequency 失败: {e}"
                return False
        if self._backend_lib == "soapy" and self._soapy is not None:
            try:
                self._soapy.setFrequency(self._rx, 0, float(freq_hz))
                return True
            except Exception as e:
                self.status.error = f"[soapy] setFrequency 失败: {e}"
                return False
        # 未连接：参数已由基类写入 status.frequency_hz，不报错（降级模式）
        return True

    def _apply_sample_rate(self, rate_hz: float) -> bool:
        """写 RX 采样率（ad9361-phy/voltage0:sampling_frequency）。"""
        if self._backend_lib == "iio":
            try:
                if self._rx_chan is not None:
                    self._rx_chan.attrs["sampling_frequency"].value = str(int(rate_hz))
                return True
            except Exception as e:
                self.status.error = f"[iio] set sampling_frequency 失败: {e}"
                return False
        if self._backend_lib == "soapy" and self._soapy is not None:
            try:
                self._soapy.setSampleRate(self._rx, 0, float(rate_hz))
                return True
            except Exception as e:
                self.status.error = f"[soapy] setSampleRate 失败: {e}"
                return False
        return True

    def _apply_bandwidth(self, bw_hz: float) -> bool:
        """写 RX 模拟带宽（rf_bandwidth）。"""
        if self._backend_lib == "iio" and self._rx_chan is not None:
            try:
                self._rx_chan.attrs["rf_bandwidth"].value = str(int(bw_hz))
                return True
            except Exception:
                return False
        return True

    def _apply_gain(self, gain_db: float) -> bool:
        """切 manual 增益模式并写 hardwaregain。"""
        if self._backend_lib == "iio":
            try:
                if self._rx_chan is not None:
                    self._rx_chan.attrs["gain_control_mode"].value = "manual"
                    self._rx_chan.attrs["hardwaregain"].value = str(float(gain_db))
                return True
            except Exception as e:
                self.status.error = f"[iio] set gain 失败: {e}"
                return False
        if self._backend_lib == "soapy" and self._soapy is not None:
            try:
                self._soapy.setGain(self._rx, 0, float(gain_db))
                return True
            except Exception as e:
                self.status.error = f"[soapy] setGain 失败: {e}"
                return False
        return True

    def set_agc(self, enabled: bool) -> bool:
        if not super().set_agc(enabled):
            return False
        if self._backend_lib == "iio" and self._rx_chan is not None:
            try:
                mode = "slow_attack" if enabled else "manual"
                self._rx_chan.attrs["gain_control_mode"].value = mode
                return True
            except Exception as e:
                self.status.error = f"[iio] set gain_control_mode 失败: {e}"
                return False
        if self._backend_lib == "soapy" and self._soapy is not None:
            try:
                self._soapy.setGainMode(self._rx, 0, bool(enabled))
                return True
            except Exception as e:
                self.status.error = f"[soapy] setGainMode 失败: {e}"
                return False
        return True

    # ------------------------------------------------------------------
    # readback
    # ------------------------------------------------------------------
    def readback_hw_state(self) -> bool:
        if self._backend_lib == "iio" and self._rx_chan is not None:
            got = 0
            try:
                self.status.sample_rate_hz = float(
                    self._rx_chan.attrs["sampling_frequency"].value)
                got += 1
            except Exception:
                pass
            try:
                if self._rx_lo_chan is not None:
                    self.status.frequency_hz = float(
                        self._rx_lo_chan.attrs["frequency"].value)
                    got += 1
            except Exception:
                pass
            try:
                self.status.gain_db = float(
                    self._rx_chan.attrs["hardwaregain"].value)
                got += 1
            except Exception:
                pass
            return got > 0
        if self._backend_lib == "soapy" and self._soapy is not None:
            try:
                self.status.sample_rate_hz = float(self._soapy.getSampleRate(self._rx, 0))
                self.status.frequency_hz = float(self._soapy.getFrequency(self._rx, 0))
                self.status.gain_db = float(self._soapy.getGain(self._rx, 0))
                return True
            except Exception:
                return False
        return False

    # ------------------------------------------------------------------
    # read_samples / recv_samples
    # ------------------------------------------------------------------
    def read_samples(self, num_samples: int) -> Optional[np.ndarray]:
        """读取 num_samples 个 IQ 样本，返回 complex64 numpy 数组。"""
        if not self.status.connected:
            return None
        try:
            if self._backend_lib == "iio":
                return self._read_samples_iio(num_samples)
            if self._backend_lib == "soapy":
                return self._read_samples_soapy(num_samples)
        except Exception as e:
            self.status.error = f"read_samples 异常: {e}"
            return None
        return None

    # 任务要求的别名
    def recv_samples(self, num_samples: int) -> Optional[np.ndarray]:
        return self.read_samples(num_samples)

    def _read_samples_iio(self, num_samples: int) -> Optional[np.ndarray]:
        """从 iio.Buffer 读 16-bit I/Q 交错样本，归一化为 complex64。"""
        if self._buffer is None:
            return None
        # buffer.refill() 阻塞直到填充满
        self._buffer.refill()
        # read() 返回 bytes；16-bit 小端 I/Q 交错
        raw = self._buffer.read()
        arr = np.frombuffer(raw, dtype=np.int16)
        # 截断到偶数长度
        arr = arr[:len(arr) // 2 * 2]
        # 拆 I/Q 并归一化到 [-1, 1]
        i = arr[0::2].astype(np.float32) / 32768.0
        q = arr[1::2].astype(np.float32) / 32768.0
        samples = (i + 1j * q).astype(np.complex64)
        # 按请求数截断（buffer 一次读满，可能多于请求）
        if len(samples) > num_samples:
            samples = samples[:num_samples]
        self._samples_read += len(samples)
        return samples

    def _read_samples_soapy(self, num_samples: int) -> Optional[np.ndarray]:
        buff = np.zeros(int(num_samples), np.complex64)
        status = self._soapy.readStream(
            self._stream, [buff], int(num_samples), timeoutUs=500_000)
        n_read = int(getattr(status, "ret", 0))
        if n_read <= 0:
            if n_read < 0:
                self.status.error = f"readStream 错误码 {n_read}"
            return None
        self._samples_read += n_read
        return buff[:n_read].copy()


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
        """发现可用的 SDR 设备（仅注册真实硬件后端）。

        无硬件时 backends 为空、active_backend 为 None（显式未连接），
        绝不注册模拟后端作为默认源。
        """
        # 尝试发现 RTL-SDR
        try:
            from rtlsdr import RtlSdr
            sdr = RtlSdr(0)
            sdr.close()
            rtl = RTLSDRBackend(0)
            self.backends[rtl.device.device_id] = rtl
        except Exception as e:
            # 无硬件/缺 pyrtlsdr 环境下启动是正常的；用 debug 记录原因，不静默也不弹错
            logger.debug(f"RTL-SDR 发现失败(忽略，无硬件环境正常): {e}")

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

        # PlutoSDR（注册但不自动连接；无 libiio/SoapySDR 时 connect 返回 False，
        # enumerate() 返回 []，不伪造硬件）
        try:
            pluto = PlutoSDRBackend()
            self.backends[pluto.device.device_id] = pluto
        except Exception as e:
            logger.debug(f"PlutoSDRBackend 注册失败(忽略): {e}")

        # 无默认后端：active_backend 保持 None，等用户显式 connect 真实设备。

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
        """连接设备。无设备可选时返回 False（显式未连接）。"""
        if device_id and device_id in self.backends:
            backend = self.backends[device_id]
        else:
            backend = self.active_backend

        if backend is None:
            logger.warning("connect: 无可用后端（未发现真实硬件），保持未连接")
            return False

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

    # 1) SoapySDR 总线枚举（信息最全，先放）。
    #    打 source="soapy" 标记，供 build_backend_for_device 与 UI 下拉框区分来源。
    try:
        for dev in SoapySDRBackend.list_devices():
            dev = dict(dev)
            dev.setdefault("source", "soapy")
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
                "source": "rtl_native",  # 原生 pyrtlsdr 条目：build_backend 走 RTLSDRBackend
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

    # 3) HackRF：始终列出一个条目（已知平台），量程用真实参数表。
    #    真实打开由 connect() 决定成败——无 libhackrf/无设备时 connect 返回 False，
    #    这里绝不伪造"已连接"。
    #    量程来源: mbdsdr_ai/hackrf_params.py（移植自 repos/hackrf）。
    try:
        from .hackrf_params import HackRFParams
        _hp = HackRFParams()
    except Exception:
        _hp = None
    key = "hackrf_0"
    if key not in merged and _hp is not None:
        merged[key] = {
            "driver": "hackrf",
            "label": "HackRF One (libhackrf)",
            "serial": "",
            "manufacturer": "Great Scott Gadgets",
            "product": "HackRF One",
            "gain_range": (float(_hp.lna_min_db), float(_hp.lna_max_db)),
            "vga_gain_range": (float(_hp.vga_min_db), float(_hp.vga_max_db)),
            "txvga_gain_range": (float(_hp.txvga_min_db), float(_hp.txvga_max_db)),
            "sample_rate_range": (float(_hp.min_sr_hz), float(_hp.max_sr_hz)),
            "freq_range": (float(_hp.min_freq_hz), float(_hp.max_freq_hz)),
            "device_args": {"index": 0},
        }

    # 3.5) bladeRF (Nuand)：始终列出一个条目，量程用真实参数表。
    #      真实打开由 mbdsdr_ai.bladerf_params.BladeRFBackend.connect() 决定——
    #      无 libbladeRF/无设备时 connect 返回 False，这里绝不伪造"已连接"。
    #      量程来源: mbdsdr_ai/bladerf_params.py（移植自 repos/bladeRF）
    #        - bladeRF 2.0 Micro (AD9361) RX 70MHz-6GHz, SR 520834-61.44MHz, BW 200k-56MHz
    #          fpga_common/include/bladerf2_common.h:518-562
    #        - legacy VGA 分级 (LMS6002D): RXVGA1 5-30dB(26档), RXVGA2 0-30dB(31档)
    #          host/libraries/libbladeRF/include/bladeRF1.h:154,160,166,172
    try:
        from .bladerf_params import BladeRFParams as _BladeRFParams
        _bp = _BladeRFParams()
    except Exception:
        _bp = None
    key = "bladerf_0"
    if key not in merged and _bp is not None:
        merged[key] = {
            "driver": "bladerf",
            "label": "Nuand bladeRF (libbladeRF)",
            "serial": "",
            "manufacturer": "Nuand LLC",
            "product": "bladeRF 2.0 Micro",
            "gain_range": (float(_bp.rx_total_gain_min_db),
                           float(_bp.rx_total_gain_max_db)),
            "rxvga1_gain_range": (float(_bp.rxvga1_min_db),
                                  float(_bp.rxvga1_max_db)),
            "rxvga2_gain_range": (float(_bp.rxvga2_min_db),
                                  float(_bp.rxvga2_max_db)),
            "txvga1_gain_range": (float(_bp.txvga1_min_db),
                                  float(_bp.txvga1_max_db)),
            "txvga2_gain_range": (float(_bp.txvga2_min_db),
                                  float(_bp.txvga2_max_db)),
            "sample_rate_range": (float(_bp.min_sr_hz), float(_bp.max_sr_hz)),
            "bandwidth_range": (float(_bp.min_bw_hz), float(_bp.max_bw_hz)),
            "freq_range": (float(_bp.min_freq_hz), float(_bp.max_freq_hz)),
            "device_args": {"device_identifier": ""},
        }

    # 3.6) LimeSDR (MyriadRF)：始终列出一个条目，量程用真实参数表。
    #      真实打开由 mbdsdr_ai.limesuite_params.LimeSDRBackend.connect() 决定——
    #      无 libLimeSuite/无设备时 connect 返回 False，这里绝不伪造"已连接"。
    #      量程来源: mbdsdr_ai/limesuite_params.py（移植自 repos/LimeSuite）
    #        - 频率 100k-3.8GHz (USB)  src/API/lms7_device.cpp:1384
    #        - 采样率 100k-61.44MHz (USB) src/API/lms7_device.cpp:690
    #        - 组合增益 0-73dB        src/lime/LimeSuite.h:382
    #        - LNA 0-30dB(15档)       src/lms7002m/LMS7002M.cpp:789-837
    #        - TIA 0-12dB(3档)        src/lms7002m/LMS7002M.cpp:890-914
    try:
        from .limesuite_params import LimeSDRParams as _LimeSDRParams
        _lp = _LimeSDRParams()
    except Exception:
        _lp = None
    key = "limesdr_0"
    if key not in merged and _lp is not None:
        merged[key] = {
            "driver": "limesdr",
            "label": "LimeSDR (libLimeSuite)",
            "serial": "",
            "manufacturer": "MyriadRF",
            "product": "LimeSDR (LMS7002M)",
            "gain_range": (float(_lp.gain_min_db), float(_lp.gain_max_db)),
            "lna_gain_range": (float(min(_lp.lna_gain_levels_db())),
                               float(max(_lp.lna_gain_levels_db()))),
            "tia_gain_range": (float(min(_lp.tia_gain_levels_db())),
                               float(max(_lp.tia_gain_levels_db()))),
            "sample_rate_range": (float(_lp.min_sr_hz), float(_lp.max_sr_hz)),
            "freq_range": (float(_lp.min_freq_hz), float(_lp.max_freq_hz)),
            "device_args": {"index": 0},
        }

    # 3.7) PlutoSDR (Analog Devices ADALM-PLUTO / AD9361)：
    #      真实枚举由 PlutoSDRBackend.enumerate() 负责（libiio scan_contexts + 网络探测）。
    #      无 libiio/SoapySDR 或无硬件时 enumerate() 返回 []，绝不伪造设备。
    #      频率出厂 325 MHz–3.8 GHz（可解锁 70 MHz–6 GHz），覆盖 2.2 GHz LRO / 1.69 GHz GK-2A。
    try:
        for dev in PlutoSDRBackend.enumerate():
            key = dev.get("uri") or f"plutosdr_{dev.get('serial', '')}"
            if key in merged:
                continue
            merged[key] = {
                "driver": "plutosdr",
                "label": dev.get("label", "PlutoSDR (ADALM-PLUTO)"),
                "serial": dev.get("serial", ""),
                "manufacturer": dev.get("manufacturer", "Analog Devices"),
                "product": dev.get("product", "ADALM-PLUTO"),
                "gain_range": dev.get("gain_range",
                                      (PlutoSDRBackend.GAIN_MIN_DB,
                                       PlutoSDRBackend.GAIN_MAX_DB)),
                "sample_rate_range": dev.get("sample_rate_range",
                                             (PlutoSDRBackend.SR_MIN_HZ,
                                              PlutoSDRBackend.SR_MAX_HZ)),
                "freq_range": dev.get("freq_range",
                                      (PlutoSDRBackend.FREQ_MIN_HZ,
                                       PlutoSDRBackend.FREQ_MAX_HZ)),
                "device_args": {"uri": dev.get("uri", "")},
            }
    except Exception as e:
        logger.warning(f"PlutoSDR 枚举异常: {e}")

    # 4) gr-osmosdr 通用后端枚举（rtl/hackrf/bladerf/uhd/soapy 统一设备字符串）
    #    来源: mbdsdr_ai/osmosdr_source.py（移植自 repos/gr-osmosdr/lib/source_impl.cc:202-269）
    #    仅补充尚未被 SoapySDR/pyrtlsdr 识别到的后端（bladerf/uhd/airspy 等）。
    #    无库/无设备时 DeviceEnumerator.enumerate() 返回 []，绝不造假。
    try:
        from .osmosdr_source import DeviceEnumerator as _OsmoEnum
        for dev in _OsmoEnum.enumerate():
            key = dev.get("serial") or f"osmo_{dev.get('driver')}_{dev.get('index', 0)}"
            if key in merged:
                continue
            merged[key] = {
                "driver": dev.get("driver"),
                "label": dev.get("label", dev.get("driver", "")),
                "serial": dev.get("serial", ""),
                "manufacturer": "",
                "product": dev.get("subdriver", ""),
                "gain_range": (0.0, 0.0),
                "sample_rate_range": (0.0, 0.0),
                "freq_range": (0.0, 0.0),
                "device_args": {"osmosdr_string": dev.get("device_string", "")},
            }
    except Exception as e:
        logger.warning(f"gr-osmosdr 枚举异常: {e}")

    return list(merged.values())


def build_backend_for_device(dev: Dict[str, Any]) -> Optional[SDRBackend]:
    """根据 enumerate_all_sdr_devices() 产出的设备 dict 构造对应后端实例。

    真正 connect 的成败由后端自己负责，本函数不假装成功。
    """
    driver = (dev.get("driver") or "").lower()
    args = dev.get("device_args") or {}
    try:
        # 纯 pyrtlsdr 原生条目（enumerate_all_sdr_devices 步骤2 打的 source="rtl_native"）
        # 走原生 RTLSDRBackend（环形缓冲）。注意不能再用 "device_args 里有没有 index"
        # 判断：原生条目和 SoapySDR 的 rtlsdr 条目 device_args 都可能带 driver 键，
        # 必须用显式 source 标记区分，否则原生棒会被错派给 SoapySDRBackend（无 Soapy
        # 绑定时 connect 必失败）。
        if driver == "rtlsdr" and dev.get("source") == "rtl_native":
            return RTLSDRBackend(device_index=int(args.get("index", 0)))
        if driver == "hackrf":
            return HackRFBackend(device_index=int(args.get("index", 0)))
        if driver == "bladerf":
            # 真实打开由 BladeRFBackend.connect() 决定成败；
            # 无 libbladeRF/无设备时 connect 返回 False，绝不假成功。
            # 来源: mbdsdr_ai/bladerf_params.py（移植自 repos/bladeRF）。
            from .bladerf_params import BladeRFBackend as _BladeRFBackend
            return _BladeRFBackend(
                device_identifier=str(args.get("device_identifier", "") or ""))
        if driver == "limesdr":
            # 真实打开由 LimeSDRBackend.connect() 决定成败；
            # 无 libLimeSuite/无设备时 connect 返回 False，绝不假成功。
            # 来源: mbdsdr_ai/limesuite_params.py（移植自 repos/LimeSuite）。
            from .limesuite_params import LimeSDRBackend as _LimeSDRBackend
            return _LimeSDRBackend(device_index=int(args.get("index", 0)))
        if driver == "plutosdr":
            # 真实打开由 PlutoSDRBackend.connect() 决定成败；
            # 无 libiio/SoapyPlutoSDR/无硬件时 connect 返回 False 并在 status.error
            # 标注 "[未连接-无libiio/SoapySDR驱动]"，绝不假成功。
            return PlutoSDRBackend(
                uri=str(args.get("uri", "") or None),
                device_info=dev,
            )
        # 其余一律走 SoapySDR 通用后端（rtlsdr 经 SoapySDR、usrp、bladerf...）
        return SoapySDRBackend(device_args=args, device_info=dev)
    except Exception as e:
        logger.error(f"构造 {driver} 后端失败: {e}")
        return None
