# SPDX-License-Identifier: MIT
"""
MBDSDR AI - 接入层链路状态与数据健康度
======================================

rtl_tcp / SpyServer 这类网络源在"不理想的现实"里会遇到一整族不同的故障：
连接被拒、DNS 失败、握手超时、对端半开、传输中途掉线、长时间没有数据
（心跳缺失）、被服务端踢出、数据流突发丢包。把它们统统压缩成一个
"connected=True" 是本项目明确禁止的欺骗行为。

本模块只做两件事，全部纯 Python、无 Qt、无硬件依赖，可注入假时钟单测：

1. :func:`classify_network_error` —— 把 socket 层异常翻译成
   ``(category, message, suggestion)``，保证每种中断都有**可区分**的类别
   和**可操作**的下一步。
2. :class:`DataHealthTracker` —— 线程安全的真实数据流计数器：累计样本/字节、
   空洞事件、因流不达标而缺失的样点数、重连次数，并按注入时钟给出
   数据年龄 / 实际吞吐 / 丢包率。所有数值都来自真实读到的数据，
   没有数据时如实返回 None/0，绝不编造。

状态语义（供 UI / agent 工具直接展示）：

- ``connected``：socket 已连接且有数据在按预期速率到达；
- ``stale``：socket 仍然连着，但超过 ``stale_after`` 秒没有收到任何 IQ
  ——这是"假在线"，必须显式暴露，不允许继续显示"已连接"；
- ``disconnected``：运行期连接真的断了（对端 FIN/RST/IO 错误）；
- ``failed``：连接/握手阶段失败（被拒、超时、DNS、非目标服务）；
- ``connecting``：初始化中。
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

# ── 链路状态 ────────────────────────────────────────────────────────────
STATE_CONNECTING = "connecting"
STATE_CONNECTED = "connected"
STATE_STALE = "stale"
STATE_DISCONNECTED = "disconnected"
STATE_FAILED = "failed"

# ── 错误类别（UI/日志/dashboard 共用一套词表）────────────────────────────
CATEGORY_REFUSED = "refused"          # 连接被拒
CATEGORY_TIMEOUT = "timeout"          # 连接/握手超时
CATEGORY_DNS = "dns"                  # 主机名解析失败
CATEGORY_RESET = "reset"              # 对端重置 / 被踢出
CATEGORY_EOF = "eof"                  # 对端正常关闭（半开/掉线）
CATEGORY_HANDSHAKE = "handshake"      # 不是目标服务 / 握手不完整
CATEGORY_AUTH = "auth"                # 鉴权/访问控制拒绝
CATEGORY_IDLE = "idle"                # 连接在但长时间无数据（心跳缺失）
CATEGORY_BUSY = "busy"                # 设备/端口被占用
CATEGORY_OVERFLOW = "overflow"        # 环形缓冲积压覆盖旧样本（丢样）
CATEGORY_UNKNOWN = "unknown"

#: 链路错误类别 → error_catalog 错误码。用户要求在状态栏/帮助里看到稳定的
#: code，而不是只看到一句「断开了」；没有对应码的类别如实留空（不硬套）。
ERROR_CODE_BY_CATEGORY = {
    CATEGORY_TIMEOUT: "link_timeout",
    CATEGORY_REFUSED: "link_refused",
    CATEGORY_RESET: "link_reset",
    CATEGORY_EOF: "link_half_open",
    CATEGORY_IDLE: "link_half_open",
    CATEGORY_OVERFLOW: "buffer_overflow",
}

_REFUSED_CODES = {61, 111, 10061}      # macOS / Linux / Windows
_RESET_CODES = {54, 104, 10054, 32, 10053}

_SUGGESTIONS = {
    CATEGORY_REFUSED: "确认对端服务已启动并监听该端口（rtl_tcp: `rtl_tcp -a 0.0.0.0 -p <port>`；SpyServer 确认端口号），再重连",
    CATEGORY_TIMEOUT: "检查网络连通性与防火墙；对端可能不可达或握手无响应，可稍后重试",
    CATEGORY_DNS: "主机名解析失败：检查拼写/DNS 配置，或直接改用 IP 地址",
    CATEGORY_RESET: "对端主动断开（可能被服务端踢出或重启）：稍后重连；反复出现请查看服务端日志",
    CATEGORY_EOF: "对端关闭了连接（服务停止或半开）：确认服务仍在运行后重连",
    CATEGORY_HANDSHAKE: "该端口没有返回预期的 rtl_tcp/SpyServer 握手：确认端口号与服务类型",
    CATEGORY_AUTH: "服务端拒绝了本连接（访问控制/鉴权）：请在服务端放行本机 IP；该协议本身无内置账号密码",
    CATEGORY_IDLE: "连接仍在但长时间没有 IQ 数据（心跳缺失）：检查对端是否仍在采数、网络是否丢包；持续无数据请重连",
    CATEGORY_BUSY: "目标被占用：关闭占用它的程序或换一个端口/设备后重试",
    CATEGORY_UNKNOWN: "请重试；若反复出现，记录日志中的技术细节后排查网络与服务端",
}


def suggestion_for(category: str) -> str:
    """按类别给出可操作建议（未知类别回退到通用建议）。"""
    return _SUGGESTIONS.get(category, _SUGGESTIONS[CATEGORY_UNKNOWN])


def error_code_for_category(category: str) -> str:
    """链路类别 → error_catalog 码；未登记的类别返回空串（不硬套码）。"""
    return ERROR_CODE_BY_CATEGORY.get(str(category or ""), "")


def error_code_for_buffer_level(level: str) -> str:
    """缓冲水位 high（积压将覆盖旧样本）→ ``buffer_overflow``；其余为空。"""
    return "buffer_overflow" if str(level or "") == BUFFER_LEVEL_HIGH else ""


def classify_network_error(
    exc: BaseException,
    phase: str = "connect",
    host: str = "",
    port: Optional[int] = None,
) -> Tuple[str, str, str]:
    """把网络异常翻译成 ``(category, message, suggestion)``。

    ``phase`` 取 ``connect`` / ``handshake`` / ``stream``。同一异常在不同阶段
    含义不同（例如握手期 EOF 说明"不是目标服务"，流式期 EOF 才是掉线）。
    """
    if isinstance(exc, socket.gaierror):
        return (
            CATEGORY_DNS,
            f"连接失败: 主机名无法解析 {host}{':' + str(port) if port else ''} ({exc})",
            suggestion_for(CATEGORY_DNS),
        )
    if isinstance(exc, (TimeoutError, socket.timeout)):
        if phase == "handshake":
            return (
                CATEGORY_TIMEOUT,
                "连接失败: 握手超时（对端未在限定时间内发来设备信息）",
                suggestion_for(CATEGORY_TIMEOUT),
            )
        if phase == "stream":
            return (
                CATEGORY_IDLE,
                "读取 IQ 超时：超过空闲阈值仍未收到数据（心跳缺失）",
                suggestion_for(CATEGORY_IDLE),
            )
        return (
            CATEGORY_TIMEOUT,
            f"连接失败: 连接 {host}:{port} 超时 ({exc})",
            suggestion_for(CATEGORY_TIMEOUT),
        )
    if isinstance(exc, ConnectionResetError):
        return (
            CATEGORY_RESET,
            f"连接已被对端重置（可能被踢出或服务重启）: {exc}",
            suggestion_for(CATEGORY_RESET),
        )
    if isinstance(exc, (BrokenPipeError, ConnectionAbortedError)):
        return (
            CATEGORY_RESET,
            f"连接已中断: {exc}",
            suggestion_for(CATEGORY_RESET),
        )
    if isinstance(exc, ConnectionRefusedError):
        return (
            CATEGORY_REFUSED,
            f"连接失败: {host}:{port} 拒绝连接 ({exc})",
            suggestion_for(CATEGORY_REFUSED),
        )

    errno = getattr(exc, "errno", None)
    if errno in _REFUSED_CODES:
        return (
            CATEGORY_REFUSED,
            f"连接失败: {host}:{port} 拒绝连接 ({exc})",
            suggestion_for(CATEGORY_REFUSED),
        )
    if errno in _RESET_CODES:
        return (
            CATEGORY_RESET,
            f"连接已被对端断开: {exc}",
            suggestion_for(CATEGORY_RESET),
        )

    # 兜底：保留原始异常，明确标注未知类别
    return (
        CATEGORY_UNKNOWN,
        f"网络读写出错: {type(exc).__name__}: {exc}",
        suggestion_for(CATEGORY_UNKNOWN),
    )


# ── 缓冲水位（供 UI / agent 工具判断"还能撑多久"）────────────────────────
BUFFER_LEVEL_EMPTY = "empty"   # 缓冲空：读侧随时可能 underrun
BUFFER_LEVEL_LOW = "low"       # 水位偏低：读侧稍慢就会断流
BUFFER_LEVEL_OK = "ok"         # 正常
BUFFER_LEVEL_HIGH = "high"     # 积压接近覆盖阈值：读侧落后会丢旧样本


def classify_buffer_level(
    fill_samples: int,
    capacity_samples: int,
    low_ratio: float = 0.10,
    high_ratio: float = 0.75,
) -> Dict[str, Any]:
    """按真实积压/容量给出缓冲水位等级与处置提示（纯函数，可单测）。

    口径如实：只根据传入的两个真实计数计算，不做预测；容量非法时按 1 处理。
    """
    capacity = max(1, int(capacity_samples))
    fill = max(0, min(capacity, int(fill_samples)))
    ratio = fill / capacity
    if fill <= 0:
        level = BUFFER_LEVEL_EMPTY
        hint = "缓冲为空：读侧已取空，继续读会进入等待（underrun），检查订阅者是否跟得上或数据源是否断流"
    elif ratio < max(0.0, float(low_ratio)):
        level = BUFFER_LEVEL_LOW
        hint = "水位偏低：读侧稍有抖动就会断流，减少同时订阅者或降低读块大小"
    elif ratio >= min(1.0, float(high_ratio)):
        level = BUFFER_LEVEL_HIGH
        hint = "积压接近环形缓冲上限：读侧继续落后将覆盖旧样本（丢样），加快读取或降低采样率"
    else:
        level = BUFFER_LEVEL_OK
        hint = ""
    return {
        "level": level,
        "fill_samples": fill,
        "capacity_samples": capacity,
        "fill_ratio": round(ratio, 4),
        "hint": hint,
    }


def classify_handshake_reason(reason: str) -> Tuple[str, str, str]:
    """把业务层握手失败原因归类（magic 不对 / 配置串为空等）。"""
    text = str(reason or "")
    low = text.lower()
    if "auth" in low or "鉴权" in text or "denied" in low:
        category = CATEGORY_AUTH
    else:
        category = CATEGORY_HANDSHAKE
    return category, f"连接失败: {text}", suggestion_for(category)


class DataHealthTracker:
    """线程安全的真实数据流健康计数器。

    Parameters
    ----------
    sample_rate_hz : float, optional
        期望采样率；提供后才计算 ``rate_ratio`` 与"应到未到"丢样估计。
    stale_after : float
        多久没有数据就判为 ``stale``（心跳缺失）。
    gap_tolerance_s : float
        两次读数之间允许的抖动窗口；小于它的欠量不计入丢样，避免把
        TCP 突发/线程调度抖动误报成丢包。
    clock : callable
        单调时钟，默认 ``time.monotonic``；测试可注入。

    说明：``dropped_samples`` 是"按期望速率应到而未到"的样点数估计，
    不是协议层丢包序号（rtl_tcp/SpyServer 都没有序号），口径如实写在这里。
    """

    def __init__(
        self,
        sample_rate_hz: Optional[float] = None,
        stale_after: float = 5.0,
        gap_tolerance_s: float = 0.05,
        clock: Optional[Callable[[], float]] = None,
    ):
        self.sample_rate_hz = float(sample_rate_hz) if sample_rate_hz else None
        self.stale_after = max(0.1, float(stale_after))
        self.gap_tolerance_s = max(0.0, float(gap_tolerance_s))
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()

        self.samples_total = 0
        self.bytes_total = 0
        self.dropped_samples = 0
        self.gap_events = 0
        self.reconnect_count = 0
        self.session_samples = 0
        self.retune_events = 0

        self._connected_since: Optional[float] = None
        self._session_start: Optional[float] = None
        self._last_data_time: Optional[float] = None

    # ── 生命周期 ────────────────────────────────────────────────────────
    def on_connect(self, now: Optional[float] = None) -> None:
        """连接建立：开始新会话（累计计数保留，会话计数清零）。"""
        t = self._clock() if now is None else float(now)
        with self._lock:
            self._connected_since = t
            self._session_start = t
            self._session_samples = 0
            self._last_data_time = None

    def on_reconnect(self, now: Optional[float] = None) -> None:
        """统计一次重连（连接成功后调用）。"""
        with self._lock:
            self.reconnect_count += 1

    def on_disconnect(self, now: Optional[float] = None) -> None:
        """运行期断开：保留累计数据，清会话起点（避免速率口径跨越断线期）。"""
        with self._lock:
            self._connected_since = None
            self._session_start = None
            self._session_samples = 0

    def on_retune(self, now: Optional[float] = None) -> None:
        """采样率/带宽切换：重置会话速率口径，避免新旧两档数据混算。

        累计计数（samples_total / bytes_total / dropped_samples / gap_events）保留，
        只把"本次会话"的起点与样点归零——切换后的 ``observed_rate_hz`` 才是新档位
        的真实吞吐；否则 UI 会把切换瞬态误读成持续欠速。切换本身单独计数。
        """
        t = self._clock() if now is None else float(now)
        with self._lock:
            self.retune_events += 1
            self._session_start = t
            self._session_samples = 0
            self._last_data_time = None
            if self._connected_since is None:
                self._connected_since = t

    # ── 数据面 ──────────────────────────────────────────────────────────
    def on_data(
        self,
        n_samples: int,
        n_bytes: int = 0,
        now: Optional[float] = None,
    ) -> None:
        """记录一次真实读到的数据块。"""
        n = max(0, int(n_samples))
        b = max(0, int(n_bytes))
        t = self._clock() if now is None else float(now)
        with self._lock:
            last = self._last_data_time
            if last is not None and t > last:
                dt = t - last
                if dt >= self.stale_after:
                    # 跨越一次"心跳缺失"：算一次空洞事件
                    self.gap_events += 1
                if self.sample_rate_hz:
                    expected = self.sample_rate_hz * dt
                    tolerance = self.sample_rate_hz * self.gap_tolerance_s
                    deficit = expected - n
                    if deficit > tolerance:
                        self.dropped_samples += int(round(deficit - tolerance))
            elif last is None and self._session_start is not None:
                # 连接后第一块数据若已迟到超过阈值，同样是一次心跳缺失空洞
                if t - self._session_start >= self.stale_after:
                    self.gap_events += 1
            self._last_data_time = t
            if self._session_start is None:
                self._session_start = t
            self._session_samples += n
            self.samples_total += n
            self.bytes_total += b
            if self._connected_since is None:
                self._connected_since = t

    def add_bytes(self, n_bytes: int) -> None:
        """只记字节（握手/命令等非 IQ 流量）。"""
        if n_bytes <= 0:
            return
        with self._lock:
            self.bytes_total += int(n_bytes)

    # ── 观测 ────────────────────────────────────────────────────────────
    def age_s(self, now: Optional[float] = None) -> Optional[float]:
        """距离最后一次真实数据的时间；从未收到数据时给出连接后时长。"""
        t = self._clock() if now is None else float(now)
        with self._lock:
            if self._last_data_time is not None:
                return max(0.0, t - self._last_data_time)
            if self._connected_since is not None:
                return max(0.0, t - self._connected_since)
            return None

    def is_stale(self, now: Optional[float] = None) -> bool:
        age = self.age_s(now)
        return age is not None and age >= self.stale_after

    def snapshot(
        self,
        state: str = STATE_DISCONNECTED,
        now: Optional[float] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """返回可直接给 UI/agent 工具序列化的健康度字典。"""
        t = self._clock() if now is None else float(now)
        with self._lock:
            last_age = None
            if self._last_data_time is not None:
                last_age = max(0.0, t - self._last_data_time)
            elif self._connected_since is not None:
                last_age = max(0.0, t - self._connected_since)

            session_elapsed = 0.0
            if self._session_start is not None:
                session_elapsed = max(0.0, t - self._session_start)
            observed_rate = None
            if session_elapsed >= 0.15 and self._session_samples > 0:
                observed_rate = self._session_samples / session_elapsed

            measured = self.samples_total + self.dropped_samples
            drop_ratio = (self.dropped_samples / measured) if measured else 0.0

            rate_ratio = None
            if self.sample_rate_hz and observed_rate is not None:
                rate_ratio = observed_rate / self.sample_rate_hz

            out: Dict[str, Any] = {
                "state": state,
                "stale": (last_age is not None and last_age >= self.stale_after),
                "last_data_age_s": last_age,
                "samples_total": self.samples_total,
                "bytes_total": self.bytes_total,
                "dropped_samples": self.dropped_samples,
                "drop_ratio": round(drop_ratio, 6),
                "gap_events": self.gap_events,
                "reconnect_count": self.reconnect_count,
                "retune_events": self.retune_events,
                "observed_rate_hz": observed_rate,
                "expected_rate_hz": self.sample_rate_hz,
                "rate_ratio": rate_ratio,
            }
            if extra:
                out.update(extra)
            return out


__all__ = [
    "STATE_CONNECTING",
    "STATE_CONNECTED",
    "STATE_STALE",
    "STATE_DISCONNECTED",
    "STATE_FAILED",
    "CATEGORY_REFUSED",
    "CATEGORY_TIMEOUT",
    "CATEGORY_DNS",
    "CATEGORY_RESET",
    "CATEGORY_EOF",
    "CATEGORY_HANDSHAKE",
    "CATEGORY_AUTH",
    "CATEGORY_IDLE",
    "CATEGORY_BUSY",
    "CATEGORY_OVERFLOW",
    "CATEGORY_UNKNOWN",
    "ERROR_CODE_BY_CATEGORY",
    "error_code_for_category",
    "error_code_for_buffer_level",
    "BUFFER_LEVEL_EMPTY",
    "BUFFER_LEVEL_LOW",
    "BUFFER_LEVEL_OK",
    "BUFFER_LEVEL_HIGH",
    "classify_buffer_level",
    "classify_network_error",
    "classify_handshake_reason",
    "suggestion_for",
    "DataHealthTracker",
]
