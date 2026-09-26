"""
MBDSDR AI 内核 - rtl_tcp 网络接收源
====================================
通过 osmocom rtl_tcp 协议把远端 RTL-SDR 棒（或任意兼容 rtl_tcp 的服务器）
当作一个网络 SDR 前端接入。适合：
  - 本机装不上 librtlsdr / 无 USB 权限（Windows 沙箱、容器）；
  - 远端共享一根棒，手机端 / 多客户端同时取 IQ；
  - 与 RTLSDRBackend（本地 USB）共用同一套上层 UI / VFO / 解调链路。

协议来源（osmocom rtl-sdr，本地镜像 repos/librtlsdr/src/rtl_tcp.c）：
  - 连接建立后，server 先发 12 字节 dongle_info_t（rtl_tcp.c:79-83）：
        char magic[4]              = "RTL0"
        uint32_t tuner_type        (big-endian, rtl_tcp.c:623 htonl)
        uint32_t tuner_gain_count  (big-endian, rtl_tcp.c:627 htonl)
    发送位置见 rtl_tcp.c:619-627（memcpy "RTL0" + htonl(tuner_type) + htonl(gains)）。
  - 客户端命令包是 packed 5 字节（rtl_tcp.c:281-284）：
        struct command { uint8_t cmd; uint32_t param; } __attribute__((packed));
    param 一律大端（network order），server 侧用 ntohl 解析（rtl_tcp.c:316-335）。
    本客户端命令 opcode 表（命令包格式定义见 _send_cmd 上方注释块）：
        0x01  set center frequency   (Hz)
        0x02  set sample rate         (Hz)
        0x03  set tuner gain         (0.1 dB 单位，200 = 20.0 dB)
        0x04  set freq correction     (ppm)
  - 握手完成后 server 持续流式发送 8-bit unsigned IQ（interleaved I,Q,I,Q...）。
    换算 complex64：real=(I-128)/128.0, imag=(Q-128)/128.0
    （对照 rtl_433/tests/rtl_tcp_serve.py:56-62 的 CU8 生成：i=128+..., q=128+...）。

硬红线：
  - 连不上远程源必须 connect() 返回 False 且 error 含"连接失败"，绝不伪造 IQ。
  - 8-bit IQ 严格按 (I-128)/128.0 换算，不用别的公式。
  - 本文件不依赖 Qt / PySide6，纯 socket + numpy，可在无 UI 环境单测。
"""

import socket
import struct
import time
import logging
from typing import Optional

import numpy as np

from .sdr_backend import (
    SDRBackend,
    SDRDevice,
    _ThreadedRingReader,
    RTLSDRBackend,
)

logger = logging.getLogger(__name__)


class RtlTcpClient:
    """低层 rtl_tcp 协议客户端：只负责 socket 收发，不含后端状态机。

    用法：
        c = RtlTcpClient("192.168.1.10", 1234)
        if not c.connect():
            print(c.error)
        c.set_sample_rate(2_048_000)
        c.set_frequency(98_000_000)
        iq = c.read_samples(8192)   # complex64
        c.close()
    """

    # 命令 opcode（命令包格式：1 字节 cmd + 4 字节大端 uint32 param，
    # 对照 rtl_tcp.c:281-284 packed command 结构 + rtl_tcp.c:315-375 switch 派发）。
    CMD_SET_FREQUENCY = 0x01        # rtl_tcp.c:316  set center freq (Hz)
    CMD_SET_SAMPLE_RATE = 0x02      # rtl_tcp.c:320  set sample rate (Hz)
    CMD_SET_GAIN = 0x03             # set tuner gain (0.1 dB 单位)
    CMD_SET_FREQ_CORRECTION = 0x04  # set freq correction (ppm)

    # dongle_info_t 固定 12 字节（rtl_tcp.c:79-83）。
    _DONGLE_INFO_LEN = 12
    _MAGIC = b"RTL0"

    def __init__(self, host: str, port: int = 1234):
        self.host = host
        self.port = int(port)
        self._sock: Optional[socket.socket] = None
        # 设备信息（connect 成功后填充）
        self.tuner_type: int = 0
        self.tuner_gain_count: int = 0
        # 最近一次错误的人话描述（含"连接失败"前缀），空串=正常
        self.error: str = ""

    # ── 连接 / 握手 ──────────────────────────────────────────────────────
    def connect(self) -> bool:
        """建 TCP 连接并读满 12 字节设备信息。失败返回 False，self.error 含'连接失败'。"""
        # socket.create_connection((host, port), timeout=5)：
        # 连接阶段 5 秒超时，避免 server 半开连接把客户端永久挂起。
        try:
            self._sock = socket.create_connection(
                (self.host, self.port), timeout=5)
        except OSError as e:
            self.error = f"连接失败: 无法连接 {self.host}:{self.port} ({e})"
            self._sock = None
            logger.warning("rtl_tcp %s:%d %s", self.host, self.port, self.error)
            return False

        # 读满 12 字节 dongle_info_t（rtl_tcp.c:79-83）：
        #   magic[4] "RTL0" | tuner_type u32 BE | tuner_gain_count u32 BE
        try:
            hdr = self._recvexactly(self._DONGLE_INFO_LEN)
        except OSError as e:
            self.error = f"连接失败: 读取设备信息时 socket 异常 ({e})"
            self.close()
            return False

        if hdr is None or len(hdr) < self._DONGLE_INFO_LEN:
            self.error = "连接失败: 设备信息读取不完整（连接被对端关闭）"
            self.close()
            return False

        magic = hdr[0:4]
        if magic != self._MAGIC:
            self.error = (
                f"连接失败: 非法设备信息 magic={magic!r}（期望 b'RTL0'），"
                f"{self.host}:{self.port} 不是 rtl_tcp 服务器"
            )
            self.close()
            return False

        # 大端解析（rtl_tcp.c:623/627 server 侧 htonl → 客户端侧大端解）
        self.tuner_type, self.tuner_gain_count = struct.unpack(">II", hdr[4:12])

        # 握手完成后切回阻塞读：IQ 流式数据靠阻塞 recv 自然背压，
        # 断线时对端发 FIN 会让 recv 返回 b''，由 read_iq_bytes 判 None。
        try:
            self._sock.settimeout(None)
        except OSError:
            pass

        self.error = ""
        logger.info(
            "rtl_tcp 已连接 %s:%d  tuner_type=%d  gain_count=%d",
            self.host, self.port, self.tuner_type, self.tuner_gain_count)
        return True

    # ── 命令下发 ─────────────────────────────────────────────────────────
    def _send_cmd(self, cmd: int, param: int) -> bool:
        """发 5 字节命令包 = struct.pack(">BI", cmd, param)。

        格式对照 rtl_tcp.c:281-284 packed struct command {uint8 cmd; uint32 param}
        + rtl_tcp.c:316-335 server 侧 ntohl(cmd.param)：
            byte0      = cmd（1 字节无符号）
            byte1..4   = param（4 字节大端 uint32，>BI 的 I 即大端）
        """
        if self._sock is None:
            self.error = "未连接，无法下发命令"
            return False
        try:
            # >BI = big-endian, unsigned char(1) + unsigned int(4) = 5 字节
            self._sock.sendall(struct.pack(">BI", cmd & 0xFF, int(param) & 0xFFFFFFFF))
            return True
        except OSError as e:
            self.error = f"命令下发失败: {e}"
            logger.warning("rtl_tcp 发送命令 cmd=0x%02x 失败: %s", cmd, e)
            return False

    def set_frequency(self, freq_hz: int) -> bool:
        """设置中心频率（Hz）。opcode 0x01（rtl_tcp.c:316 set center freq）。"""
        return self._send_cmd(self.CMD_SET_FREQUENCY, int(freq_hz))

    def set_sample_rate(self, rate_hz: int) -> bool:
        """设置采样率（Hz）。opcode 0x02（rtl_tcp.c:320 set sample rate）。"""
        return self._send_cmd(self.CMD_SET_SAMPLE_RATE, int(rate_hz))

    def set_gain(self, gain_tenths_db: int) -> bool:
        """设置调谐器增益。参数是 0.1 dB 单位的整数（200 = 20.0 dB）。opcode 0x03。"""
        return self._send_cmd(self.CMD_SET_GAIN, int(gain_tenths_db))

    def set_freq_correction(self, ppm: int) -> bool:
        """设置晶振频偏校正（ppm）。opcode 0x04。"""
        return self._send_cmd(self.CMD_SET_FREQ_CORRECTION, int(ppm))

    # ── IQ 流读取 ───────────────────────────────────────────────────────
    def _recvexactly(self, n: int) -> Optional[bytes]:
        """从 socket 读满 n 字节（可能多次 recv）；连接断开返回 None。"""
        if self._sock is None:
            return None
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self._sock.recv(n - len(buf))
            except OSError as e:
                self.error = f"读取 IQ 失败: {e}"
                return None
            if not chunk:
                # 对端 FIN/RST：连接已断
                self.error = "读取 IQ 失败: 连接已断开"
                return None
            buf.extend(chunk)
        return bytes(buf)

    def read_iq_bytes(self, num_bytes: int) -> Optional[bytes]:
        """读满 num_bytes 原始 8-bit IQ 字节；连接断开返回 None。"""
        if self._sock is None:
            return None
        try:
            return self._recvexactly(int(num_bytes))
        except OSError:
            return None

    def read_samples(self, num_samples: int) -> Optional[np.ndarray]:
        """读 num_samples 个复样本（complex64）。

        8-bit unsigned IQ → complex64 换算（对照 rtl_433 rtl_tcp_serve.py:56-62）：
            real = (I - 128) / 128.0
            imag = (Q - 128) / 128.0
        读不够（连接断开）返回 None，绝不返回零数组冒充有数据。
        """
        n = int(num_samples)
        if n <= 0:
            return np.empty(0, dtype=np.complex64)
        raw = self.read_iq_bytes(n * 2)
        if raw is None or len(raw) < n * 2:
            return None
        # interleaved I,Q → reshape(-1, 2)
        iq = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 2)
        i = iq[:, 0].astype(np.float32)
        q = iq[:, 1].astype(np.float32)
        real = (i - 128.0) / 128.0
        imag = (q - 128.0) / 128.0
        return (real + 1j * imag).astype(np.complex64)

    # ── 关闭 ─────────────────────────────────────────────────────────────
    def close(self):
        """关闭底层 socket（幂等）。"""
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


class RtlTcpBackend(SDRBackend):
    """rtl_tcp 网络源统一后端封装：继承 SDRBackend，与本地 USB 后端同接口。

    复用基类的 set_frequency/set_sample_rate/set_gain（带连接检查、范围钳位、
    失败回滚 status）和 _ThreadedRingReader（生产者线程 + 环形缓冲），
    上层 main_window / VFO / 解调链路无需感知底层是 USB 还是网络。
    """

    def __init__(self, host: str, port: int = 1234, ppm: int = 0):
        device = SDRDevice(
            device_type="rtl_tcp",
            device_id=f"rtl_tcp_{host}_{port}",
            name=f"rtl_tcp {host}:{port}",
            # 与 RTLSDRBackend 同一频率/采样率量程（rtl_tcp 后端共用 RTL2832 前端）
            frequency_range=(500_000, 1_766_000_000),
            sample_rate_range=(225_001, 3_200_000),
            max_gain=49.6,
            supports_iq=True,
            supports_tx=False,
        )
        super().__init__(device)
        # 默认采样率 2.048M（对齐 RTLSDRBackend.DEFAULT_SAMPLE_RATE，1.024M 整数倍）
        self.status.sample_rate_hz = 2_048_000.0
        self._host = host
        self._port = int(port)
        self._ppm = int(ppm)
        self._client: Optional[RtlTcpClient] = None
        self._ring_reader: Optional[_ThreadedRingReader] = None

    def connect(self) -> bool:
        """建连并下发初始设置，然后启动环形缓冲生产者线程。

        连不上（host 不可达 / 超时 / magic 不对）必须返回 False 且
        status.error 含"连接失败"，绝不伪造 IQ。
        """
        client = RtlTcpClient(self._host, self._port)
        if not client.connect():
            self._client = None
            self.status.connected = False
            # 确保 error 一定含"连接失败"硬红线关键字
            msg = client.error or "未知错误"
            self.status.error = msg if "连接失败" in msg else f"连接失败: {msg}"
            logger.warning("RtlTcpBackend 连接失败: %s", self.status.error)
            return False

        self._client = client
        self.status.connected = True
        self.status.error = ""
        self._start_time = time.time()

        # 对照 RTLSDRBackend.connect：打开后逐项显式下发设置，不停在出厂默认。
        # 1) 采样率（先吸附到 SDR++ 离散档，见 _apply_sample_rate）
        self.set_sample_rate(self.status.sample_rate_hz)
        # 2) 中心频率：SDRStatus 默认 98 MHz（FM 广播段），用户可预设
        target_freq = self.status.frequency_hz or 98_000_000.0
        self.set_frequency(target_freq)
        # 3) 频偏校正 ppm（含 0，避免上次残留）
        self.set_ppm(self._ppm)
        # 4) 手动增益 20.0 dB（=200 个 0.1dB 单位），首启不聋也不过载
        self.set_gain(20.0)

        # 启动生产者线程持续读 IQ 写环形缓冲（对照 RTLSDRBackend.connect
        # 里 _ThreadedRingReader(read_fn=..., block_size=8192, ring_size=1_000_000)）。
        self._ring_reader = _ThreadedRingReader(
            read_fn=self._client.read_samples,
            block_size=8192,
            ring_size=1_000_000,
        )
        try:
            self._ring_reader.start()
        except Exception as e:
            logger.warning("RtlTcpBackend 启动环形缓冲生产者失败: %s", e)
            self._ring_reader = None
        return True

    def _apply_frequency(self, freq_hz: float) -> bool:
        """真正写远端 set freq；成功后清环形缓冲（丢旧频残留 IQ）。"""
        if self._client is None:
            return False
        ok = self._client.set_frequency(int(freq_hz))
        if ok and self._ring_reader is not None:
            self._ring_reader.clear()
        return ok

    def _apply_sample_rate(self, rate_hz: float) -> bool:
        """吸附到 RTLSDRBackend 的 SDR++ 离散采样率档，再下发；成功后清 ring。"""
        if self._client is None:
            return False
        snapped = RTLSDRBackend.nearest_sample_rate(rate_hz)
        if int(snapped) != int(rate_hz):
            logger.info(
                "rtl_tcp 采样率 %.0f Hz 吸附到离散档 %.0f Hz", rate_hz, snapped)
        ok = self._client.set_sample_rate(int(snapped))
        if ok and self._ring_reader is not None:
            self._ring_reader.clear()
        return ok

    def _apply_gain(self, gain_db: float) -> bool:
        """dB → 0.1dB 单位整数，下发 opcode 0x03。"""
        if self._client is None:
            return False
        gain_tenths = int(round(gain_db * 10))
        return self._client.set_gain(gain_tenths)

    def set_ppm(self, ppm: int) -> bool:
        """保存并下发晶振频偏校正（ppm）。未连接时仅保存，connect 时再发。"""
        self._ppm = int(ppm)
        if self._client is None:
            return True
        return self._client.set_freq_correction(self._ppm)

    def read_samples(self, num_samples: int) -> Optional[np.ndarray]:
        """优先从环形缓冲读；未启动 ring 时兜底直接同步读 socket。"""
        if not self.status.connected:
            return None
        if self._ring_reader is not None:
            try:
                samples = self._ring_reader.read(num_samples, timeout=1.0)
            except Exception:
                return None
            if samples is None:
                return None
            self._samples_read += len(samples)
            return samples
        # 兜底：ring 未启动时直接同步读
        if self._client is not None:
            samples = self._client.read_samples(num_samples)
            if samples is not None:
                self._samples_read += len(samples)
            return samples
        return None

    def disconnect(self):
        """停生产者线程 → 关 socket → 标记断开（对照 RTLSDRBackend.disconnect）。"""
        if self._ring_reader is not None:
            try:
                self._ring_reader.stop()
            except Exception:
                pass
            self._ring_reader = None
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        super().disconnect()

    def close(self):
        """disconnect 的别名（与 SDRDevice 生命周期习惯对齐）。"""
        self.disconnect()
