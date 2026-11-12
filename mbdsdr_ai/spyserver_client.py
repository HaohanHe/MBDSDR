# SPDX-License-Identifier: MIT
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
import time
from typing import Any, Dict, Optional

import numpy as np

from .error_catalog import user_message
from .sdr_backend import SDRBackend, SDRDevice, _ThreadedRingReader
from .link_state import (
    DataHealthTracker,
    STATE_CONNECTED,
    STATE_CONNECTING,
    STATE_DISCONNECTED,
    STATE_FAILED,
    STATE_STALE,
    CATEGORY_EOF,
    CATEGORY_IDLE,
    CATEGORY_RESET,
    classify_handshake_reason,
    classify_network_error,
    error_code_for_category,
    suggestion_for,
)

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
    #: 合法 SpyServer 配置串里至少会出现其中一个键。握手串必须命中至少一个，
    #: 否则视为「不是 SpyServer」——二进制/新协议头里碰巧出现的 '=' 不算数。
    _HANDSHAKE_KNOWN_KEYS = (
        "Name", "DeviceType", "Serial", "Version",
        "SampleRate", "MinSampleRate", "MaxSampleRate",
        "MinFrequency", "MaxFrequency", "MaxGain", "Gain",
    )
    # 单次 read_samples 最大字节数（防止无限阻塞）
    _DEFAULT_CHUNK = 8192
    #: 握手总时限 = max(1.0, timeout × 本系数)。逐字节 recv 各自有超时，
    #: 但对端只要每字节都赶在超时前到达就能把 1024 字节的握手拖到 1024×timeout
    #: （默认 5s → 约 85 分钟），表现就是永远卡在「连接中」。总时限封顶。
    _HANDSHAKE_DEADLINE_FACTOR = 2.0

    #: 连续多少次 idle 超时才把"半开连接"硬判为断开（与 rtl_tcp 同口径）：
    #: 1 次=stale（网络抖动仍可能恢复）；2 次=累计 2×idle_timeout 无数据。
    _STALE_ESCALATE_AFTER = 2

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
        # 空闲超时：超过则判"心跳缺失"(stale)，不再无限阻塞 recv
        self.idle_timeout = max(0.5, float(timeout))
        # 连续 idle 超时次数（收到真实数据即清零）；达到阈值升级成硬断开
        self._stale_streak = 0
        self._health = DataHealthTracker(
            sample_rate_hz=None, stale_after=self.idle_timeout)
        self.state: str = STATE_DISCONNECTED
        self.error: str = ""
        self.error_category: str = ""
        self.error_code: str = ""
        self.suggestion: str = ""
        self.sample_rate_hz: Optional[float] = None
        #: 最近一次 _recvexactly 实际收到的字节数（半包/半帧时用于如实报数）。
        self._last_recv_bytes = 0

    # -- 连接 / 握手 --------------------------------------------------------
    def start(self, host: Optional[str] = None, port: Optional[int] = None) -> None:
        """建 TCP 连接并完成握手。失败抛 SpyServerError，不造假。"""
        if host is not None:
            self.host = str(host)
        if port is not None:
            self.port = int(port)

        # 重连前先释放旧 socket，绝不残留半开句柄
        self.stop()
        self.state = STATE_CONNECTING
        self.error = ""
        self.error_category = ""
        self.error_code = ""
        self.suggestion = ""

        # 1) 建 socket
        if self._sock_factory is not None:
            try:
                self._sock = self._sock_factory(self.host, self.port)
            except OSError as e:
                self._fail_from_network(e, "connect")
                raise SpyServerError(self.error) from e
        else:
            try:
                self._sock = socket.create_connection(
                    (self.host, self.port), timeout=self._timeout
                )
            except OSError as e:
                self._fail_from_network(e, "connect")
                raise SpyServerError(self.error) from e

        # 2) 读 1024 字节握手配置串（带总时限，慢滴对端不能把 connect 拖成分钟级）
        try:
            self._sock.settimeout(self._timeout)
            deadline = time.monotonic() + max(
                1.0, self._timeout * self._HANDSHAKE_DEADLINE_FACTOR)
            raw = self._recvexactly(
                self._HANDSHAKE_BYTES, phase="handshake", deadline=deadline)
        except OSError as e:
            self._fail_from_network(e, "handshake")
            self._close_sock()
            raise SpyServerError(self.error) from e

        if raw is None or len(raw) < self._HANDSHAKE_BYTES:
            cat, msg, sug = classify_handshake_reason(
                f"握手串不完整（{self._last_recv_bytes}/{self._HANDSHAKE_BYTES} "
                "字节，对端关闭或半开），不是可用的 SpyServer")
            self._fail(cat, msg, sug)
            self._close_sock()
            raise SpyServerError(self.error)

        # 3) 解析 ASCII key=value\\0 串，并校验它**确实像** SpyServer 配置串。
        #    只解析出 key=value 不够：二进制协议头里碰巧出现的 '=' 也会被切成
        #    键值对（键里带控制字符），照单全收就会把非 IQ 流当成 IQ 播出去。
        self.server_info = self._parse_handshake(raw)
        if not self._looks_like_config(self.server_info):
            cat, msg, sug = classify_handshake_reason(
                f"握手串不是 SpyServer 配置（解析到 {len(self.server_info)} 个 "
                f"key=value，未命中 Name/SampleRate 等已知键），"
                f"{self.host}:{self.port} 不是 SpyServer")
            self.server_info = {}
            self._fail(cat, msg, sug)
            self._close_sock()
            raise SpyServerError(self.error)

        # 4) 空闲超时读（不用永久阻塞）：长时间无数据必须能被检测成 stale
        try:
            self._sock.settimeout(self.idle_timeout)
        except OSError:
            pass
        self.state = STATE_CONNECTED
        self.error = ""
        self.error_category = ""
        self.suggestion = ""
        self._health.on_connect()
        self._running = True
        logger.info(
            "SpyServer 已连接 %s:%d  设备=%s  采样率=%s  增益=%s",
            self.host, self.port,
            self.server_info.get("Name", "?"),
            self.server_info.get("SampleRate", "?"),
            self.server_info.get("Gain", "?"),
        )

    # -- 链路状态 -----------------------------------------------------------
    @property
    def connected(self) -> bool:
        return (self._sock is not None
                and self.state in (STATE_CONNECTED, STATE_STALE))

    @property
    def link_state(self) -> str:
        return self.state

    def _fail(self, category: str, message: str, suggestion: str = "") -> None:
        self.state = STATE_FAILED
        self.error_category = category
        self.error_code = error_code_for_category(category)
        self.error = message
        self.suggestion = suggestion or suggestion_for(category)
        self._running = False
        self._stale_streak = 0
        self._health.on_disconnect()

    def _fail_from_network(self, exc: BaseException, phase: str) -> None:
        cat, msg, sug = classify_network_error(
            exc, phase=phase, host=self.host, port=self.port)
        self._fail(cat, msg, sug)

    def _mark_stale(self, message: str) -> None:
        if self.state == STATE_CONNECTED:
            self.state = STATE_STALE
        self.error_category = CATEGORY_IDLE
        self.error_code = error_code_for_category(CATEGORY_IDLE)
        self.error = message
        self.suggestion = suggestion_for(CATEGORY_IDLE)

    def _mark_disconnected(self, category: str, message: str) -> None:
        self.state = STATE_DISCONNECTED
        self.error_category = category
        self.error_code = error_code_for_category(category)
        self.error = message
        self.suggestion = suggestion_for(category)
        self._running = False
        self._stale_streak = 0
        self._health.on_disconnect()

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

    @classmethod
    def _looks_like_config(cls, info: Dict[str, str]) -> bool:
        """握手串是否真的是 SpyServer 配置串（而不是碰巧含 '=' 的二进制）。

        三条都要满足：
          1. 至少解析出一个键；
          2. 键/值里没有替换字符（非 ASCII 解码失败的痕迹）或控制字符；
          3. 至少命中一个已知配置键（Name/SampleRate/...）。
        """
        if not info:
            return False
        for k, v in info.items():
            if not k:
                return False
            for text in (k, v):
                if "\ufffd" in text:
                    return False
                if any(ord(ch) < 0x20 and ch != "\t" for ch in text):
                    return False
        return any(k in info for k in cls._HANDSHAKE_KNOWN_KEYS)

    # -- 命令下发 -----------------------------------------------------------
    def _send_cmd(self, cmd: int, param: int) -> None:
        """发 5 字节命令包 = struct.pack(">BI", cmd, param)。

        对照 rtl_tcp_client.cpp:70-73：Command{uint8 cmd; uint32 param}__packed，
        param 大端（htonl）。
        """
        if self._sock is None or not self._running:
            self._mark_disconnected(CATEGORY_RESET, "未连接，无法下发命令")
            raise SpyServerError(self.error)
        try:
            self._sock.sendall(
                struct.pack(">BI", cmd & 0xFF, int(param) & 0xFFFFFFFF)
            )
        except OSError as e:
            self._fail_from_network(e, "stream")
            raise SpyServerError(f"命令下发失败: {self.error}") from e

    def set_frequency(self, freq_hz: int) -> None:
        self._send_cmd(self.CMD_SET_FREQUENCY, int(freq_hz))

    def set_sample_rate(self, rate_hz: int) -> None:
        self._send_cmd(self.CMD_SET_SAMPLERATE, int(rate_hz))
        self.sample_rate_hz = float(rate_hz)
        self._health.sample_rate_hz = float(rate_hz)

    def set_gain(self, gain_db: float) -> None:
        """设置增益 dB。先切手动模式，再下发增益（与 rtl_tcp 一致）。"""
        self._send_cmd(self.CMD_SET_GAIN_MODE, 1)
        # SpyServer 增益用 0.1 dB 单位
        self._send_cmd(self.CMD_SET_GAIN, int(round(gain_db * 10)))

    def set_agc(self, enabled: bool) -> None:
        self._send_cmd(self.CMD_SET_GAIN_MODE, 0 if enabled else 1)

    # -- IQ 流读取 ----------------------------------------------------------
    def _recvexactly(self, n: int, phase: str = "stream",
                     deadline: Optional[float] = None) -> Optional[bytes]:
        """读满 n 字节；三种结局必须可区分（超时/对端关闭/IO 错误）。

        ``deadline``（monotonic 时间戳）只用于握手：每次 recv 前把本步超时
        收紧到「总时限剩余」，避免逐字节慢滴把握手拖到 1024×timeout。
        """
        if self._sock is None:
            return None
        self._last_recv_bytes = 0
        buf = bytearray()
        while len(buf) < n:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise socket.timeout("握手超过总时限")
                try:
                    self._sock.settimeout(min(self._timeout, remaining))
                except OSError:
                    pass
            try:
                chunk = self._sock.recv(n - len(buf))
            except (TimeoutError, socket.timeout) as e:
                if phase == "handshake":
                    raise e
                self._stale_streak += 1
                self._mark_stale(
                    f"超过 {self.idle_timeout:g}s 未收到 IQ 数据（心跳缺失）")
                if self._stale_streak >= self._STALE_ESCALATE_AFTER:
                    # 半开连接兜底：连续 2×idle_timeout 无任何数据 → 关闭
                    # socket 并如实断开，不能永远显示"已连接"。
                    hard = self.idle_timeout * self._STALE_ESCALATE_AFTER
                    self._mark_disconnected(
                        "idle",
                        f"连续 {hard:g}s 未收到任何数据（判定为半开连接，"
                        f"已关闭 socket）",
                    )
                    self._close_sock()
                raise SpyServerError(self.error) from e
            except OSError as e:
                if phase == "handshake":
                    raise e
                self._fail_from_network(e, "stream")
                raise SpyServerError(self.error) from e
            if not chunk:
                if phase == "handshake":
                    return None
                self._mark_disconnected(
                    CATEGORY_EOF, "读取 IQ 失败: 连接已被对端关闭（服务停止或掉线）")
                return None
            buf.extend(chunk)
            self._last_recv_bytes = len(buf)
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
            self._mark_disconnected(
                CATEGORY_EOF,
                f"读样本不足（{self._last_recv_bytes}/{n*2} 字节），连接断开",
            )
            raise SpyServerError(self.error)
        iq = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 2)
        i = iq[:, 0].astype(np.float32)
        q = iq[:, 1].astype(np.float32)
        real = (i - 128.0) / 128.0
        imag = (q - 128.0) / 128.0
        # 真实数据到达：从 stale 恢复 connected，并更新健康计数
        if self.state in (STATE_STALE, STATE_CONNECTED):
            self.state = STATE_CONNECTED
            self.error = ""
            self.error_category = ""
            self.error_code = ""
            self.suggestion = ""
            self._stale_streak = 0
        self._health.on_data(n, n_bytes=len(raw))
        return (real + 1j * imag).astype(np.complex64)

    def data_health(self) -> dict:
        """可序列化的链路健康度（含服务端采样率与请求值的匹配情况）。"""
        server_rate = None
        raw_rate = str(self.server_info.get("SampleRate", "")).strip()
        if raw_rate:
            try:
                server_rate = int(float(raw_rate))
            except ValueError:
                server_rate = None
        mismatch = None
        if server_rate and self.sample_rate_hz:
            # 客户端请求值与服务端配置串声明的采样率不一致 → 数据实际速率
            # 会与 UI/接收链假设不符，必须显式暴露（不许静默）。
            mismatch = int(abs(int(self.sample_rate_hz) - server_rate)) > 1
        return self._health.snapshot(
            state=self.state,
            extra={
                "host": self.host,
                "port": self.port,
                "category": self.error_category,
                "code": self.error_code,
                "message": self.error,
                "suggestion": self.suggestion,
                "connected": self.connected,
                "server_name": self.server_info.get("Name", ""),
                "server_sample_rate_hz": server_rate,
                "requested_sample_rate_hz": self.sample_rate_hz,
                "sample_rate_mismatch": mismatch,
            },
        )

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
        """停止并关闭 socket（幂等）。

        仅当仍处于运行态时才翻回 disconnected；握手/连接失败写入的 failed
        状态与 error/category 不会被 stop 抹掉。
        """
        was_active = self.state in (STATE_CONNECTED, STATE_STALE, STATE_CONNECTING)
        self._running = False
        self._close_sock()
        self._health.on_disconnect()
        if was_active:
            self.state = STATE_DISCONNECTED

    def __del__(self):
        try:
            self.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# SpyServer 后端封装（与 RtlTcpBackend 同接口，供 SDRBackend 上层统一使用）
# ---------------------------------------------------------------------------
class SpyServerBackend(SDRBackend):
    """把 SpyServerClient 包成 SDRBackend：环形缓冲 + 链路状态 + 可观测指标。

    与 RtlTcpBackend 的差异只在协议握手/命令细节；上层 UI / VFO / 解调链路
    不需要感知底层是 rtl_tcp 还是 SpyServer。
    """

    DEFAULT_SAMPLE_RATE = 2_048_000.0

    #: 服务端量程在握手串里的候选键名（不同固件/服务端版本命名不一）。
    #: 只有**服务端真的声明了**才采信；没声明就保留声明默认值并标 unknown，
    #: 绝不再把 RTL-SDR 的假设当成服务端能力（P1-32）。
    _CAP_FREQ_MIN = ("MinFrequency", "MinFreq", "MinFreqHz")
    _CAP_FREQ_MAX = ("MaxFrequency", "MaxFreq", "MaxFreqHz")
    _CAP_RATE_MIN = ("MinSampleRate", "MinRate")
    _CAP_RATE_MAX = ("MaxSampleRate", "MaxRate")
    _CAP_GAIN_MAX = ("MaxGain",)

    @staticmethod
    def _cap_num(info: Dict[str, str], keys: tuple) -> Optional[float]:
        """从握手串取数值；缺键/脏值返回 None（不猜、不兜底成 0）。"""
        for key in keys:
            raw = str(info.get(key, "")).strip()
            if not raw:
                continue
            try:
                val = float(raw)
            except ValueError:
                continue
            if val > 0:
                return val
        return None

    def _apply_server_capabilities(self, info: Dict[str, str]) -> Dict[str, Any]:
        """把服务端声明的量程写进 device，并记录来源。

        修复 P1-32：此前 frequency_range/sample_rate_range/max_gain 全硬编码成
        RTL-SDR 假设（500 k–1.766 G / 225 k–3.2 M / 49.6 dB），服务端挂 Airspy
        或 HF 设备时 UI 会给出越界档位（选不中/滑杆越界）。现在：
          * 服务端声明了就采用，并同步默认采样率/起始频点；
          * 没声明就保持默认，``capabilities_source`` 标 "declared"，
            由上层按"未核实"呈现，而不是冒充服务端能力。
        """
        applied: Dict[str, Any] = {}
        fmin = self._cap_num(info, self._CAP_FREQ_MIN)
        fmax = self._cap_num(info, self._CAP_FREQ_MAX)
        if fmin and fmax and fmin < fmax:
            self.device.frequency_range = (int(fmin), int(fmax))
            self.device.capability_status["frequency_range"] = "probed"
            self.device.capability_note["frequency_range"] = "服务端握手声明"
            applied["frequency_range"] = self.device.frequency_range
            # 默认起始频点落进服务端量程（HF 专用服务端不该默认开到 98 MHz）
            target = self.status.frequency_hz or 98_000_000.0
            self.status.frequency_hz = float(min(max(target, fmin), fmax))
        rmin = self._cap_num(info, self._CAP_RATE_MIN)
        rmax = self._cap_num(info, self._CAP_RATE_MAX)
        if rmin and rmax and rmin < rmax:
            self.device.sample_rate_range = (int(rmin), int(rmax))
            self.device.capability_status["sample_rate_range"] = "probed"
            self.device.capability_note["sample_rate_range"] = "服务端握手声明"
            applied["sample_rate_range"] = self.device.sample_rate_range
        gmax = self._cap_num(info, self._CAP_GAIN_MAX)
        if gmax:
            self.device.max_gain = float(gmax)
            self.device.capability_status["max_gain"] = "probed"
            self.device.capability_note["max_gain"] = "服务端握手声明"
            applied["max_gain"] = self.device.max_gain
        # 服务端配置串里的 SampleRate 是它实际出数速率：跟它对齐可避免
        # data_health 里那条 sample_rate_mismatch（请求值与服务端不一致）。
        exact_rate = self._cap_num(info, ("SampleRate",))
        if exact_rate:
            self.status.sample_rate_hz = float(exact_rate)
            applied["sample_rate_hz"] = self.status.sample_rate_hz
        self.capabilities_source = (
            "server" if any(k in applied for k in (
                "frequency_range", "sample_rate_range", "max_gain"))
            else "declared")
        self.capabilities_applied = applied
        if applied:
            logger.info("SpyServer 服务端量程已采信: %s", applied)
        else:
            # 没声明就保持 RTL-SDR 假设，但必须留下"未核实"的口子：
            # capability_status 保持缺省（= unknown），界面据此显示未核实。
            logger.warning(
                "SpyServer 握手未声明量程，界面按 RTL-SDR 声明值呈现（未核实）")
        return applied

    def __init__(self, host: str, port: int = 7355, ppm: int = 0,
                 sock_factory: Optional[Any] = None,
                 idle_timeout: float = 5.0):
        device = SDRDevice(
            device_type="spyserver",
            device_id=f"spyserver_{host}_{port}",
            name=f"SpyServer {host}:{port}",
            frequency_range=(500_000, 1_766_000_000),
            sample_rate_range=(225_001, 3_200_000),
            max_gain=49.6,
            supports_iq=True,
            supports_tx=False,
        )
        super().__init__(device)
        self.status.sample_rate_hz = self.DEFAULT_SAMPLE_RATE
        self._host = host
        self._port = int(port)
        self._ppm = int(ppm)
        self._sock_factory = sock_factory
        self._idle_timeout = max(0.5, float(idle_timeout))
        self._client: Optional[SpyServerClient] = None
        self._ring_reader: Optional[_ThreadedRingReader] = None
        self._reconnect_count = 0
        #: 量程来源："server"（握手声明）/ "declared"（未声明，沿用 RTL 假设）
        self.capabilities_source = "declared"
        self.capabilities_applied: Dict[str, Any] = {}

    def connect(self) -> bool:
        # 重连前彻底释放旧 socket/生产者线程
        if self._ring_reader is not None or self._client is not None:
            try:
                self.disconnect()
            except Exception:
                pass
        self.status.link_state = STATE_CONNECTING
        client = SpyServerClient(
            self._host, self._port, timeout=self._idle_timeout,
            sock_factory=self._sock_factory)
        try:
            client.start()
        except SpyServerError as e:
            self._client = None
            self.status.connected = False
            self.status.link_state = STATE_FAILED
            self.status.error_category = client.error_category
            self.status.error_code = client.error_code
            msg = (str(e) or client.error
                   or user_message("missing_error_detail", context="SpyServer"))
            self.status.error = (
                msg if "连接失败" in msg else f"连接失败: {msg}"
            )
            logger.warning("SpyServerBackend 连接失败: %s", self.status.error)
            return False

        self._client = client
        self.status.connected = True
        self.status.link_state = STATE_CONNECTED
        self.status.error = ""
        self.status.error_category = ""
        self.status.error_code = ""
        self._start_time = time.time()

        # 服务端量程优先：拿到握手就按它校准 device 与默认采样率/频点（P1-32），
        # 再下发初始化命令，避免用 RTL-SDR 假设去要求一台 Airspy/HF 服务端。
        self._apply_server_capabilities(client.server_info)

        # 初始化下发（失败不掩盖：set_* 会把原因写进 status.error）
        self.set_sample_rate(self.status.sample_rate_hz)
        self.set_frequency(self.status.frequency_hz or 98_000_000.0)
        self.set_gain(20.0)

        self._ring_reader = _ThreadedRingReader(
            read_fn=self._ring_read,
            block_size=8192,
            ring_size=1_000_000,
        )
        try:
            self._ring_reader.start()
        except Exception as e:
            logger.warning("SpyServerBackend 启动环形缓冲生产者失败: %s", e)
            self._ring_reader = None
        return True

    def _ring_read(self, num_samples: int) -> Optional[np.ndarray]:
        if self._client is None:
            return None
        try:
            out = self._client.read_samples(num_samples)
        except SpyServerError:
            state = self._client.state
            self.status.error_category = self._client.error_category
            self.status.error_code = self._client.error_code
            self.status.error = self._client.error
            if state == STATE_STALE:
                self.status.link_state = STATE_STALE
                self.status.last_data_age_s = self._client.data_health().get(
                    "last_data_age_s")
            else:
                self.status.connected = False
                self.status.link_state = STATE_DISCONNECTED
            return None
        if out is None or len(out) == 0:
            self.status.connected = False
            self.status.link_state = STATE_DISCONNECTED
            self.status.error_code = self._client.error_code
            self.status.error = "读取 IQ 失败：无数据（连接已断开）"
            return None
        if self.status.link_state in (STATE_STALE, STATE_DISCONNECTED, STATE_FAILED):
            self.status.link_state = STATE_CONNECTED
            self.status.error = ""
            self.status.error_category = ""
            self.status.error_code = ""
        self._last_ring_data_time = time.time()
        self.status.last_data_age_s = 0.0
        return out

    def _apply_frequency(self, freq_hz: float) -> bool:
        if self._client is None:
            return False
        try:
            self._client.set_frequency(int(freq_hz))
        except SpyServerError:
            return False
        if self._ring_reader is not None:
            self._ring_reader.clear()
        return True

    def _apply_sample_rate(self, rate_hz: float) -> bool:
        if self._client is None:
            return False
        try:
            self._client.set_sample_rate(int(rate_hz))
        except SpyServerError:
            return False
        if self._ring_reader is not None:
            self._ring_reader.clear()
        return True

    def _apply_gain(self, gain_db: float) -> bool:
        if self._client is None:
            return False
        try:
            self._client.set_gain(float(gain_db))
        except SpyServerError:
            return False
        return True

    def data_health(self) -> dict:
        st = self.get_status()
        base = self._client.data_health() if self._client is not None else {}
        out = {
            "backend": "SpyServerBackend",
            "state": st.link_state or "disconnected",
            "connected": bool(st.connected),
            "category": st.error_category,
            "code": st.error_code,
            "message": st.error,
            "suggestion": base.get("suggestion", ""),
            "samples_total": int(st.samples_read),
            "dropped_samples": int(st.dropped_samples),
            "drop_ratio": (
                st.dropped_samples / max(1, st.samples_read + st.dropped_samples)
            ),
            "buffer_fill": int(st.buffer_fill),
            "last_data_age_s": st.last_data_age_s,
            "expected_rate_hz": st.sample_rate_hz,
            "reconnect_count": int(self._reconnect_count),
            "server_name": base.get("server_name", ""),
            "server_sample_rate_hz": base.get("server_sample_rate_hz"),
            "sample_rate_mismatch": base.get("sample_rate_mismatch"),
        }
        for k in ("bytes_total", "gap_events", "observed_rate_hz", "rate_ratio"):
            if k in base:
                out[k] = base[k]
        return out

    def read_samples(self, num_samples: int) -> Optional[np.ndarray]:
        if not self.status.connected:
            return None
        if self._ring_reader is not None:
            samples = self._ring_reader.read(num_samples, timeout=1.0)
            if samples is None:
                return None
            self._samples_read += len(samples)
            return samples
        return None

    def disconnect(self):
        if self._ring_reader is not None:
            try:
                self._ring_reader.stop()
            except Exception:
                pass
            self._ring_reader = None
        if self._client is not None:
            try:
                self._client.stop()
            except Exception:
                pass
            self._client = None
        self._last_ring_data_time = None
        super().disconnect()

    def close(self):
        self.disconnect()


__all__ = [
    "SpyServerError",
    "SpyServerClient",
    "SpyServerBackend",
]
