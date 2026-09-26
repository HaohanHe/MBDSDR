"""
MBDSDR AI 内核 - SpyServer 网络源客户端
======================================

对照上游（见 docs/learn/sdrpp_gnuradio_port.md 第 1.5 节）：
  - SDR++ rtl_tcp_source 模块  <-> source_modules/rtl_tcp_source/src/main.cpp:24-94
  - rtl_tcp worker 线程读 socket + uint8 IQ 换算
                              <-> source_modules/rtl_tcp_source/src/rtl_tcp_client.cpp:75-95
  - 5 字节命令包 (cmd + BE u32)
                              <-> rtl_tcp_client.cpp:70-73 sendCommand

SpyServer（SDR# 网络共享协议）与 rtl_tcp 同源：
  - 连接建立后 server 先发一段 ASCII 配置串（key=value\\0 对，1024 字节缓冲），
    告诉客户端设备类型、采样率范围、增益范围等；
  - 客户端随后发 5 字节命令包（1 字节 opcode + 4 字节大端 uint32 参数）调频率/采样率/增益；
  - 之后 server 持续推送 IQ 流（uint8 I/Q 交织，每样本 2 字节）。

与 rtl_tcp 的区别：
  - rtl_tcp server 主动发 12 字节 "RTL0" magic；
  - SpyServer server 主动发 ASCII 配置串（不是二进制 magic），客户端读满 1024 字节
    再解析。
  - 命令 opcode 编号不同（SpyServer 用自己的表），但包格式一致。

硬红线：
  - 连不上 / 握手失败必须抛 SpyServerError，绝不伪造 IQ。
  - 8-bit IQ 严格按 (I-128)/128.0 换算（与 rtl_tcp_client.py:229-231 一致）。
  - 本文件纯 socket + numpy，可在无 UI 环境单测（接受注入 fake socket）。
"""

from __future__ import annotations

import logging
import socket
import struct
import threading
from typing import Any, Dict, Optional

import numpy as np

logger = logging.getLogger(__name__)


class SpyServerError(Exception):
    """SpyServer 连接/握手/协议错误。"""


# ---------------------------------------------------------------------------
# SpyServer 客户端
# ---------------------------------------------------------------------------
class SpyServerClient:
    """低层 SpyServer 协议客户端。

    用法：
        c = SpyServerClient("192.168.1.10", 7355)
        c.start()                              # 连接 + 握手
        c.set_frequency(98_000_000)
        c.set_sample_rate(2_048_000)
        c.set_gain(20.0)
        iq = c.read_samples(8192)              # complex64
        c.stop()

    测试时可传 ``sock_factory`` 参数注入 fake socket，不走真实网络。
    """

    # SpyServer 命令 opcode（与 rtl_tcp 同包格式：1B cmd + 4B BE u32）
    # 参考 SDR# SpyServer 协议（常见编号）：
    CMD_SET_FREQUENCY = 0x01
    CMD_SET_SAMPLERATE = 0x02
    CMD_SET_GAIN_MODE = 0x03     # 1=manual, 0=AGC
    CMD_SET_GAIN = 0x04
    CMD_SET_FREQ_CORRECTION = 0x05
    CMD_SET_DIRECT_SAMPLING = 0x09
    CMD_SET_BIAS_TEE = 0x14

    # server 首包：1024 字节 ASCII 配置串
    _HANDSHAKE_BYTES = 1024
    # 单次 read_samples 最大字节数（防止无限阻塞）
    _DEFAULT_CHUNK = 8192

    def __init__(
        self,
        host: str,
        port: int = 7355,
        sock_factory: Optional[Any] = None,
        timeout: float = 5.0,
    ):
        self.host = str(host)
        self.port = int(port)
        self._sock: Optional[socket.socket] = None
        self._sock_factory = sock_factory  # 测试注入
        self._timeout = float(timeout)
        # 握手后由 server 配置串填充
        self.server_info: Dict[str, str] = {}
        self._running = False
        self._lock = threading.Lock()

    # -- 连接 / 握手 --------------------------------------------------------
    def start(self, host: Optional[str] = None, port: Optional[int] = None) -> None:
        """建 TCP 连接并完成握手。失败抛 SpyServerError，不造假。"""
        if host is not None:
            self.host = str(host)
        if port is not None:
            self.port = int(port)

        # 1) 建 socket
        if self._sock_factory is not None:
            try:
                self._sock = self._sock_factory(self.host, self.port)
            except OSError as e:
                raise SpyServerError(
                    f"连接失败: socket factory 拒绝 {self.host}:{self.port} ({e})"
                ) from e
        else:
            try:
                self._sock = socket.create_connection(
                    (self.host, self.port), timeout=self._timeout
                )
            except OSError as e:
                raise SpyServerError(
                    f"连接失败: 无法连接 {self.host}:{self.port} ({e})"
                ) from e

        # 2) 读 1024 字节握手配置串
        try:
            self._sock.settimeout(self._timeout)
            raw = self._recvexactly(self._HANDSHAKE_BYTES)
        except OSError as e:
            self._close_sock()
            raise SpyServerError(f"连接失败: 读取握手串失败 ({e})") from e

        if raw is None or len(raw) < self._HANDSHAKE_BYTES:
            self._close_sock()
            raise SpyServerError(
                f"连接失败: 握手串不完整（{len(raw) if raw else 0}/"
                f"{self._HANDSHAKE_BYTES} 字节）"
            )

        # 3) 解析 ASCII key=value\\0 串
        self.server_info = self._parse_handshake(raw)
        if not self.server_info:
            self._close_sock()
            raise SpyServerError(
                f"连接失败: 握手串未解析到任何 key=value（{self.host}:{self.port} "
                "不是 SpyServer）"
            )

        # 4) 切回阻塞读（IQ 流靠阻塞 recv 背压）
        try:
            self._sock.settimeout(None)
        except OSError:
            pass
        self._running = True
        logger.info(
            "SpyServer 已连接 %s:%d  设备=%s  采样率=%s  增益=%s",
            self.host, self.port,
            self.server_info.get("Name", "?"),
            self.server_info.get("SampleRate", "?"),
            self.server_info.get("Gain", "?"),
        )

    @staticmethod
    def _parse_handshake(raw: bytes) -> Dict[str, str]:
        """把 1024 字节 ASCII \\0 分隔的 key=value 串解析成 dict。"""
        out: Dict[str, str] = {}
        # 截到全零 padding 之前（最后一个非零字节之后）
        # 按 \x00 切分所有段
        for part in raw.split(b"\x00"):
            part = part.strip()
            if not part or b"=" not in part:
                continue
            k, _, v = part.partition(b"=")
            try:
                out[k.decode("ascii", errors="replace").strip()] = v.decode(
                    "ascii", errors="replace"
                ).strip()
            except Exception:
                continue
        return out

    # -- 命令下发 -----------------------------------------------------------
    def _send_cmd(self, cmd: int, param: int) -> None:
        """发 5 字节命令包 = struct.pack(">BI", cmd, param)。

        对照 rtl_tcp_client.cpp:70-73：Command{uint8 cmd; uint32 param}__packed，
        param 大端（htonl）。
        """
        if self._sock is None or not self._running:
            raise SpyServerError("未连接，无法下发命令")
        try:
            self._sock.sendall(
                struct.pack(">BI", cmd & 0xFF, int(param) & 0xFFFFFFFF)
            )
        except OSError as e:
            self._running = False
            raise SpyServerError(f"命令下发失败: {e}") from e

    def set_frequency(self, freq_hz: int) -> None:
        self._send_cmd(self.CMD_SET_FREQUENCY, int(freq_hz))

    def set_sample_rate(self, rate_hz: int) -> None:
        self._send_cmd(self.CMD_SET_SAMPLERATE, int(rate_hz))

    def set_gain(self, gain_db: float) -> None:
        """设置增益 dB。先切手动模式，再下发增益（与 rtl_tcp 一致）。"""
        self._send_cmd(self.CMD_SET_GAIN_MODE, 1)
        # SpyServer 增益用 0.1 dB 单位
        self._send_cmd(self.CMD_SET_GAIN, int(round(gain_db * 10)))

    def set_agc(self, enabled: bool) -> None:
        self._send_cmd(self.CMD_SET_GAIN_MODE, 0 if enabled else 1)

    # -- IQ 流读取 ----------------------------------------------------------
    def _recvexactly(self, n: int) -> Optional[bytes]:
        """读满 n 字节；连接断开返回 None。"""
        if self._sock is None:
            return None
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self._sock.recv(n - len(buf))
            except OSError as e:
                raise SpyServerError(f"读取 IQ 失败: {e}") from e
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def read_samples(self, n: int) -> np.ndarray:
        """读 n 个复样本（complex64）。

        uint8 I/Q 交织 -> complex64 换算（对照 rtl_tcp_client.cpp:86-87）：
            real = (I - 128) / 128.0
            imag = (Q - 128) / 128.0
        连接断开抛 SpyServerError，不返回零数组。
        """
        if not self._running:
            raise SpyServerError("未连接，无法读样本")
        n = int(n)
        if n <= 0:
            return np.empty(0, dtype=np.complex64)
        with self._lock:
            raw = self._recvexactly(n * 2)
        if raw is None or len(raw) < n * 2:
            self._running = False
            raise SpyServerError(
                f"读样本不足（{len(raw) if raw else 0}/{n*2} 字节），连接断开"
            )
        iq = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 2)
        i = iq[:, 0].astype(np.float32)
        q = iq[:, 1].astype(np.float32)
        real = (i - 128.0) / 128.0
        imag = (q - 128.0) / 128.0
        return (real + 1j * imag).astype(np.complex64)

    # -- 关闭 ---------------------------------------------------------------
    def _close_sock(self) -> None:
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

    def stop(self) -> None:
        """停止并关闭 socket（幂等）。"""
        self._running = False
        self._close_sock()

    def __del__(self):
        try:
            self.stop()
        except Exception:
            pass
