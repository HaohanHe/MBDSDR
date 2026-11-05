# SPDX-License-Identifier: MIT
"""
MBDSDR AI - 断连重连管理器
============================

GQRX 拔出 USB 后只能手动重连、且要重新调频率/模式/增益。本模块在纯 Python
线程里实现：

- 周期性探测后端连接状态；
- 一旦断开：自动停止音频流、触发 ``on_disconnected``（UI 可显"设备已断开，
  正在重连..."），并按**指数退避**重试重连；
- 重连成功：恢复断开前的频率/采样率/增益/解调模式设置，重启音频流，
  触发 ``on_reconnected``；
- 超过最大重试次数：触发 ``on_give_up``（默认无限重试，等待用户插回设备）。

退避策略（任务要求）：1s, 2s, 4s, 8s, 16s, 封顶 30s。

与 device_watchdog.py 的区别：
- device_watchdog.py 是 PySide6 QThread/Signal，绑桌面 GUI；
- 本模块是纯 threading + 普通回调，无头可用、可确定性单测，
  既可被桌面 UI 包一层，也可在命令行/服务进程里直接用。

后端鸭子类型协议（任意满足其一即可）：
- ``get_status()`` 返回带 ``connected`` 属性的对象，或
- ``is_connected() -> bool``。
重连用 ``connect() -> bool``；恢复设置用 ``set_frequency / set_sample_rate /
set_gain``。全部调用包 try/except，后端抛错只记日志不崩。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

#: 指数退避基数（秒）与上限（秒），以及默认探测周期
DEFAULT_BASE_DELAY = 1.0
DEFAULT_MAX_DELAY = 30.0
DEFAULT_POLL_INTERVAL = 0.5
#: 默认最多连续重试次数。禁止默认无限重试：用户必须在有限时间内拿到
#: "已放弃"的结论和下一步动作，而不是永远转圈。
#: 默认总退避等待 = 1+2+4+8+16+30 = 61s（另加每次 connect 自身耗时）。
DEFAULT_MAX_ATTEMPTS = 6

#: 重连状态机（供 UI / agent 工具展示；绝不出现"假装已连接"的中间态）
STATE_STOPPED = "stopped"
STATE_CONNECTED = "connected"
STATE_DISCONNECTED = "disconnected"
STATE_RETRYING = "retrying"
STATE_GAVE_UP = "gave_up"

_GIVE_UP_TIPS = {
    "usb": (
        "设备未恢复：请检查 USB 线/接口后重新插入；若仍失败，关闭可能独占设备的"
        "其它程序（SDR# / GQRX / SDR++ / SDRangel），必要时重启应用后手动重连"
    ),
    "network": (
        "远端未恢复：确认 rtl_tcp/SpyServer 服务仍在运行、地址与端口正确、网络/防火墙"
        "可达；修正后手动重连，或等待服务恢复"
    ),
    "unknown": "重连次数已耗尽：请检查设备/网络后手动重连（应用不会无限重试刷屏）",
}


def compute_backoff(
    attempt: int,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
) -> float:
    """第 ``attempt`` 次重试前应等待的秒数（指数退避，封顶）。

    attempt 从 1 开始：1->base, 2->2*base, 3->4*base, ... 封顶 max_delay。
    """
    if attempt < 1:
        attempt = 1
    delay = float(base_delay) * (2 ** (attempt - 1))
    return min(delay, float(max_delay))


def _safe_callback(fn: Optional[Callable[..., None]], *args: Any) -> None:
    """调用回调，吞掉一切异常，绝不让回调崩掉监控线程。"""
    if fn is None:
        return
    try:
        fn(*args)
    except Exception as exc:
        logger.warning("重连回调 %s 异常: %s", getattr(fn, "__name__", fn), exc)


class ReconnectionManager:
    """设备断连检测 + 自动重连 + 设置恢复。

    Parameters
    ----------
    on_disconnected : callable, optional
        检测到设备断开时调用（无参）。
    on_reconnecting : callable, optional
        每次重连尝试时调用，参数为 ``attempt: int``（从 1 开始）。
    on_reconnected : callable, optional
        重连成功并恢复设置后调用（无参）。
    on_give_up : callable, optional
        重连次数耗尽时调用（无参）。仅在 ``max_attempts`` 非 None 时触发。
    stop_audio : callable, optional
        断开时停止音频流（无参）。
    start_audio : callable, optional
        重连成功后重启音频流（无参）。
    poll_interval : float
        连接状态探测周期（秒）。
    base_delay, max_delay : float
        指数退避基数/上限（秒）。
    max_attempts : int, optional
        最大连续重试次数；默认 6（有上限）。显式传 ``None`` 也会被归一到默认
        上限——不再提供"无限重试"路径，避免用户永远看不到结论。
    """

    def __init__(
        self,
        on_disconnected: Optional[Callable[[], None]] = None,
        on_reconnecting: Optional[Callable[[int], None]] = None,
        on_reconnected: Optional[Callable[[], None]] = None,
        on_give_up: Optional[Callable[[], None]] = None,
        stop_audio: Optional[Callable[[], None]] = None,
        start_audio: Optional[Callable[[], None]] = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        base_delay: float = DEFAULT_BASE_DELAY,
        max_delay: float = DEFAULT_MAX_DELAY,
        max_attempts: Optional[int] = DEFAULT_MAX_ATTEMPTS,
        source_kind: str = "unknown",
    ) -> None:
        self.on_disconnected = on_disconnected
        self.on_reconnecting = on_reconnecting
        self.on_reconnected = on_reconnected
        self.on_give_up = on_give_up
        self._stop_audio = stop_audio
        self._start_audio = start_audio

        self._poll_interval = max(0.01, float(poll_interval))
        self._base_delay = float(base_delay)
        self._max_delay = float(max_delay)
        # 归一：None 也落到默认上限。无限重试是历史缺陷（用户永远等不到
        # 放弃结论），这里不再保留逃生口。
        self._max_attempts = (
            DEFAULT_MAX_ATTEMPTS if max_attempts is None else max(1, int(max_attempts))
        )
        self._source_kind = source_kind if source_kind in _GIVE_UP_TIPS else "unknown"

        self._backend: Any = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

        # 断开前抓拍的设置，重连后恢复
        self._settings_snapshot: Dict[str, Any] = {}
        # 状态：上一轮是否认为已连接；是否已 give_up
        self._was_connected = False
        self._gave_up = False
        # 可观测状态（供 UI / agent 工具查询）
        self._state = STATE_STOPPED
        self._attempt = 0              # 当前这轮连续重试到第几次
        self._attempts_total = 0       # 累计重试次数（跨多轮断线）
        self._reconnects_total = 0     # 累计成功重连次数
        self._next_retry_in = 0.0      # 距下一次重试的秒数（0=立即/不在等待）
        self._last_error = ""

    # ------------------------------------------------------------------
    # 后端状态探测（鸭子类型，全部容错）
    # ------------------------------------------------------------------
    def _is_connected(self, backend: Any) -> bool:
        if backend is None:
            return False
        try:
            status = backend.get_status()
            if status is not None and getattr(status, "connected", False):
                return True
        except Exception:
            pass
        try:
            return bool(backend.is_connected())
        except Exception:
            return False

    def _capture_settings(self, backend: Any) -> Dict[str, Any]:
        """抓拍断开前的频率/采样率/增益/模式等设置。"""
        snap: Dict[str, Any] = {}
        if backend is None:
            return snap
        try:
            status = backend.get_status()
            for key in ("frequency_hz", "sample_rate_hz", "gain_db",
                        "demod_mode", "squelch_db", "volume"):
                if hasattr(status, key):
                    snap[key] = getattr(status, key)
        except Exception:
            pass
        # hal.py SDRBackendBase 风格的私有属性兜底
        for attr in ("_center_freq", "_sample_rate", "_gain"):
            if hasattr(backend, attr):
                snap[attr] = getattr(backend, attr)
        return snap

    def _restore_settings(self, backend: Any, snap: Dict[str, Any]) -> None:
        """把抓拍的设置下发回新连接的后端。"""
        if backend is None or not snap:
            return
        try:
            if "frequency_hz" in snap and snap["frequency_hz"] is not None:
                if hasattr(backend, "set_frequency"):
                    backend.set_frequency(float(snap["frequency_hz"]))
            if snap.get("sample_rate_hz") and hasattr(backend, "set_sample_rate"):
                backend.set_sample_rate(float(snap["sample_rate_hz"]))
            if "gain_db" in snap and hasattr(backend, "set_gain"):
                backend.set_gain(float(snap["gain_db"]))
        except Exception as exc:
            logger.warning("恢复设置失败: %s", exc)

    # ------------------------------------------------------------------
    # 重连重试循环
    # ------------------------------------------------------------------
    def _read_backend_error(self) -> str:
        """从后端 status.error 读取最近一次失败原因（读不到就如实留空）。"""
        try:
            status = self._backend.get_status()
            return str(getattr(status, "error", "") or "")
        except Exception:
            return ""

    def _retry_loop(self, backend: Any) -> bool:
        """指数退避重试重连。返回 True=成功重连，False=give_up/被 stop。

        状态机：``retrying``（正在重试，含 attempt 与退避倒计时）→
        ``connected``（成功）或 ``gave_up``（超过上限）。日志按尝试次数抽样，
        避免无限重试把日志刷爆；回调仍逐次触发供 UI 更新。
        """
        self._attempt = 0
        self._next_retry_in = 0.0
        while not self._stop.is_set():
            self._attempt += 1
            if self._attempt > self._max_attempts:
                self._gave_up = True
                self._state = STATE_GAVE_UP
                self._next_retry_in = 0.0
                current = self._read_backend_error()
                if current:
                    self._last_error = current
                logger.warning(
                    "重连放弃：已连续尝试 %d 次（上限 %d）。%s",
                    self._max_attempts, self._max_attempts,
                    self.give_up_suggestion(),
                )
                _safe_callback(self.on_give_up)
                return False

            # 只有真正发起一次 connect 才计入累计尝试次数（放弃那次不算）
            self._attempts_total += 1
            self._state = STATE_RETRYING
            _safe_callback(self.on_reconnecting, self._attempt)
            ok = False
            try:
                ok = bool(backend.connect())
            except Exception as exc:
                ok = False
                self._last_error = f"{type(exc).__name__}: {exc}"
            if not ok:
                current = self._read_backend_error()
                if current:
                    self._last_error = current
                elif not self._last_error:
                    self._last_error = "后端未返回失败原因"

            if ok:
                self._restore_settings(backend, self._settings_snapshot)
                _safe_callback(self._start_audio)
                self._gave_up = False
                self._state = STATE_CONNECTED
                self._attempt = 0
                self._next_retry_in = 0.0
                self._last_error = ""
                self._reconnects_total += 1
                # 后端 status 上也暴露累计重连次数（UI/agent 工具可直接读）
                try:
                    status = backend.get_status()
                    if hasattr(status, "reconnect_count"):
                        status.reconnect_count = self._reconnects_total
                    if hasattr(status, "link_state"):
                        status.link_state = "connected"
                except Exception:
                    pass
                _safe_callback(self.on_reconnected)
                return True

            # 退避等待（可被 stop 打断）。日志只在第 1/2/3 次与每 10 次打印，
            # 避免"重试 N 次"把终端/日志刷满。
            delay = compute_backoff(self._attempt, self._base_delay, self._max_delay)
            if self._attempt <= 3 or self._attempt % 10 == 0:
                logger.info(
                    "第 %d 次重连失败（%s）；%.1fs 后重试",
                    self._attempt,
                    self._last_error or "后端未返回原因",
                    delay,
                )
            self._next_retry_in = float(delay)
            if self._stop.wait(delay):  # True = 被 stop 唤醒
                self._next_retry_in = 0.0
                return False
            self._next_retry_in = 0.0
        return False

    # ------------------------------------------------------------------
    # 监控主循环
    # ------------------------------------------------------------------
    def _run(self) -> None:
        backend = self._backend
        self._was_connected = self._is_connected(backend)
        self._state = STATE_CONNECTED if self._was_connected else STATE_DISCONNECTED
        while not self._stop.is_set():
            connected = self._is_connected(backend)

            if connected:
                self._was_connected = True
                self._gave_up = False
                self._state = STATE_CONNECTED
                self._attempt = 0
                if self._stop.wait(self._poll_interval):
                    break
                continue

            # 当前未连接：若刚刚才从连接态掉下来，先记录断连事件
            if self._was_connected:
                self._settings_snapshot = self._capture_settings(backend)
                self._last_error = self._read_backend_error()
                _safe_callback(self._stop_audio)
                _safe_callback(self.on_disconnected)
                self._was_connected = False
                self._state = STATE_DISCONNECTED

            # 已放弃：不再自动发起任何 connect。历史上这里会"再来一轮"，
            # 效果是每隔一段时间又重试 6 次并重复 on_give_up——本质仍是无限
            # 重试。现在只有用户显式 retry_now() / start() 才重新开始。
            if self._gave_up:
                if self._stop.wait(self._poll_interval):
                    break
                continue

            # 进入重连退避循环（阻塞到重连成功 / give_up / stop）
            self._retry_loop(backend)

            if self._stop.wait(self._poll_interval):
                break

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self, backend: Any) -> None:
        """启动重连监控。``backend`` 为待监控的 SDR 后端。

        可重复调用：会先停掉旧线程再用新 backend 重启。
        """
        self.stop()
        self._backend = backend
        self._stop = threading.Event()
        self._settings_snapshot = {}
        self._was_connected = self._is_connected(backend)
        self._gave_up = False
        self._attempt = 0
        self._attempts_total = 0
        self._reconnects_total = 0
        self._next_retry_in = 0.0
        self._last_error = ""
        self._state = STATE_CONNECTED if self._was_connected else STATE_DISCONNECTED
        self._thread = threading.Thread(
            target=self._run, name="reconnection-manager", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """请求停止并等待线程退出。可重复调用，安全。"""
        if self._stop is not None:
            self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=timeout)
        self._thread = None
        if self._state != STATE_GAVE_UP:
            self._state = STATE_STOPPED
        self._next_retry_in = 0.0

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def gave_up(self) -> bool:
        return self._gave_up

    @property
    def state(self) -> str:
        return self._state

    def retry_now(self, backend: Any = None) -> None:
        """用户在"已放弃"后主动重连：重置放弃状态并开启新的一轮有上限重试。

        这是放弃提示里"手动重连"的具体入口；不调用它就不会有任何自动重试，
        避免后台永远转圈。
        """
        if backend is not None:
            self._backend = backend
        self._gave_up = False
        self._attempt = 0
        self._next_retry_in = 0.0
        self._last_error = ""
        if self._thread is not None and self._thread.is_alive():
            # 后台线程只用 _gave_up 标志判断是否继续，置 False 即进入新一轮
            self._state = STATE_DISCONNECTED
            return
        if self._backend is not None:
            self.start(self._backend)

    def give_up_suggestion(self) -> str:
        """放弃重连时给用户的可操作下一步。"""
        return _GIVE_UP_TIPS[self._source_kind]

    def snapshot(self) -> Dict[str, Any]:
        """返回可直接展示/序列化的重连状态（供 UI 与 agent 工具使用）。

        字段与真实行为一致：``state`` 是 stopped/connected/disconnected/retrying/
        gave_up；``attempt``、``attempts_total``、``reconnects_total``、
        ``next_retry_in``、``last_error`` 均来自真实重试循环，不做推测。
        """
        return {
            "state": self._state,
            "attempt": self._attempt,
            "attempts_total": self._attempts_total,
            "reconnects_total": self._reconnects_total,
            "max_attempts": self._max_attempts,
            "next_retry_in": round(float(self._next_retry_in), 3),
            "last_error": self._last_error,
            "gave_up": self._gave_up,
            "suggestion": self.give_up_suggestion() if self._gave_up else "",
            "source_kind": self._source_kind,
        }

    def get_state(self) -> Dict[str, Any]:
        """``snapshot()`` 的别名（语义更直白的调用入口）。"""
        return self.snapshot()


__all__ = [
    "ReconnectionManager",
    "compute_backoff",
    "DEFAULT_MAX_ATTEMPTS",
    "STATE_STOPPED",
    "STATE_CONNECTED",
    "STATE_DISCONNECTED",
    "STATE_RETRYING",
    "STATE_GAVE_UP",
]
