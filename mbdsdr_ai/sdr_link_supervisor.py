# SPDX-License-Identifier: MIT
"""SDR 常驻重连监督器（Task 75）。

只负责"什么时候、是否还允许再试一次连接"，不自己实现设备连接：
真正的连接入口由调用方以 ``connect_fn`` 注入（控制器直接复用 ``connect()``）。
默认**禁用**；只有显式 ``enable()``（控制器侧为
``enable_sdr_autoreconnect()``）后才会发起重连，绝不在用户没要求时
自动打开串口 / SDR / 网络源。

时间与等待都可注入：

* ``clock`` —— 默认 ``time.monotonic``，``tick(now=...)`` 可进一步覆盖；
* ``sleeper`` —— 默认 ``time.sleep``，仅用于阻塞式 ``run()``；
* 线程模式 ``start()`` 一律用 :class:`threading.Event` 的 ``wait(timeout)``，
  因此 ``stop()`` 能立即唤醒等待并退出，不做忙等。

退避为有界指数：1s, 2s, 4s, 8s, 16s … 封顶 ``max_delay_s``（默认 30s）。
成功后计数清零；连续失败达到 ``max_attempts`` 即停止，并在
``status()["stopped_reason"]`` 给出原因。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BASE_DELAY_S = 1.0
DEFAULT_MAX_DELAY_S = 30.0
#: 已连接时线程模式的重查周期（秒）。只做状态检查，不发起 connect。
DEFAULT_POLL_INTERVAL_S = 0.5


def compute_backoff(
    attempt: int,
    base_delay_s: float = DEFAULT_BASE_DELAY_S,
    max_delay_s: float = DEFAULT_MAX_DELAY_S,
) -> float:
    """第 ``attempt`` 次失败后的等待秒数（1,2,4,8,16… 封顶）。"""
    attempt = max(1, int(attempt))
    delay = float(base_delay_s) * (2 ** (attempt - 1))
    return min(delay, float(max_delay_s))


class SdrLinkSupervisor:
    """显式启停的 SDR 常驻重连状态机。

    Parameters
    ----------
    connect_fn :
        发起一次连接，返回真值表示成功；抛异常或返回假值均记为失败。
    is_connected_fn :
        只读连接态；已连接时 ``tick`` 不会重复调用 ``connect_fn``。
    clock :
        单调时钟（默认 ``time.monotonic``），测试可注入假时钟。
    sleeper :
        无条件睡眠函数（默认 ``time.sleep``），仅供阻塞式 :meth:`run`；
        线程模式不使用它，以免无法被 ``stop()`` 打断。
    max_attempts :
        连续失败上限（默认 5）；达到后停止并写 ``stopped_reason``。
    base_delay_s / max_delay_s :
        指数退避基数与上限（默认 1.0s / 30.0s）。
    poll_interval_s :
        已连接时线程模式的重查周期（默认 0.5s），不发起连接。
    """

    def __init__(
        self,
        connect_fn: Callable[[], Any],
        is_connected_fn: Callable[[], Any],
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Any] = time.sleep,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        base_delay_s: float = DEFAULT_BASE_DELAY_S,
        max_delay_s: float = DEFAULT_MAX_DELAY_S,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    ) -> None:
        if not callable(connect_fn):
            raise TypeError("connect_fn 必须可调用")
        if not callable(is_connected_fn):
            raise TypeError("is_connected_fn 必须可调用")
        self._connect_fn = connect_fn
        self._is_connected_fn = is_connected_fn
        self._clock = clock
        self._sleeper = sleeper
        self._max_attempts = max(1, int(max_attempts))
        self._base_delay_s = max(0.0, float(base_delay_s))
        self._max_delay_s = max(0.0, float(max_delay_s))
        self._poll_interval_s = max(0.01, float(poll_interval_s))

        self._lock = threading.RLock()
        self._enabled = False
        self._attempts = 0
        self._last_error = ""
        self._stopped_reason = ""
        self._next_attempt_at: Optional[float] = None
        # 一次 connect 进行中时，并发的 tick 不得再发起第二次。
        self._in_flight = False

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ------------------------------------------------------------------
    # 状态读取
    # ------------------------------------------------------------------
    def _read_connected(self) -> bool:
        try:
            return bool(self._is_connected_fn())
        except Exception as e:  # 读连接态失败不能当作已连接
            logger.debug("读取 SDR 连接态失败: %s", e)
            return False

    @staticmethod
    def _error_text(exc: BaseException) -> str:
        return f"{type(exc).__name__}: {exc}"

    def _attempt_locked(self, now: float) -> bool:
        """入口锁内的一次尝试决策；真正调用 connect_fn 在锁外。"""
        if not self._enabled or self._stopped_reason:
            return False
        if self._read_connected():
            self._attempts = 0
            self._last_error = ""
            self._next_attempt_at = None
            return False
        if self._in_flight:
            return False
        due = self._next_attempt_at
        if due is not None and now < due:
            return False
        self._attempts += 1
        self._in_flight = True
        return True

    # ------------------------------------------------------------------
    # 生命周期 / 主接口
    # ------------------------------------------------------------------
    def enable(self) -> None:
        """显式启用；首次尝试立即到期。默认状态是禁用。"""
        with self._lock:
            self._enabled = True
            self._attempts = 0
            self._last_error = ""
            self._stopped_reason = ""
            self._next_attempt_at = float(self._clock())

    def disable(self) -> None:
        """禁用并立即唤醒线程模式（若在跑）。之后 tick 不再发起连接。"""
        with self._lock:
            self._enabled = False
            self._next_attempt_at = None
        self.stop()

    def tick(self, now: Optional[float] = None) -> bool:
        """到点才尝试一次连接；返回本次 tick 结束时是否已连接。

        未启用 / 已停止 / 未到退避时间 / 已连接时都不会调用 ``connect_fn``。
        """
        when = float(self._clock() if now is None else now)
        with self._lock:
            attempt = self._attempt_locked(when)
            if not attempt:
                return self._read_connected()
            attempt_no = self._attempts

        ok = False
        error = ""
        try:
            ok = bool(self._connect_fn())
        except Exception as e:  # 连接入口抛错 = 失败，保留真实原因
            error = self._error_text(e)
        finally:
            with self._lock:
                self._in_flight = False
        if not ok and not error:
            error = "连接失败（connect 未返回成功）"

        with self._lock:
            if ok:
                self._attempts = 0
                self._last_error = ""
                self._stopped_reason = ""
                self._next_attempt_at = None
            else:
                self._last_error = error
                if self._attempts >= self._max_attempts:
                    # 达到上限：停止，不再自动尝试；保留最后一次真实原因。
                    self._next_attempt_at = None
                    self._stopped_reason = (
                        f"连续重连失败 {self._attempts} 次，已达上限 "
                        f"{self._max_attempts} 次"
                    )
                else:
                    delay = compute_backoff(
                        attempt_no, self._base_delay_s, self._max_delay_s)
                    self._next_attempt_at = when + delay
        if ok:
            logger.info("SDR 重连成功")
        elif self._stopped_reason:
            logger.warning("%s：%s", self._stopped_reason,
                           self._last_error or "无更多原因")
        else:
            logger.info("SDR 重连第 %d 次失败：%s", attempt_no,
                        self._last_error)
        return ok

    def status(self) -> Dict[str, Any]:
        """可直接序列化的状态（connected 只表示链路，不掺重连语义）。"""
        now = float(self._clock())
        with self._lock:
            enabled = self._enabled
            attempts = self._attempts
            max_attempts = self._max_attempts
            last_error = self._last_error
            stopped_reason = self._stopped_reason
            next_at = self._next_attempt_at
        running = self._thread is not None and self._thread.is_alive()
        return {
            "enabled": bool(enabled),
            "connected": self._read_connected(),
            "attempts": int(attempts),
            "max_attempts": int(max_attempts),
            "next_retry_in_s": (
                round(max(0.0, next_at - now), 3)
                if enabled and not stopped_reason and next_at is not None
                else 0.0
            ),
            "last_error": last_error,
            "stopped_reason": stopped_reason,
            "running": bool(running),
        }

    # ------------------------------------------------------------------
    # 可选线程模式：Event.wait(timeout)，stop() 立即唤醒
    # ------------------------------------------------------------------
    def _compute_wait(self) -> float:
        with self._lock:
            if not self._enabled or self._stopped_reason:
                return 0.0
            if self._read_connected():
                return self._poll_interval_s
            if self._next_attempt_at is None:
                return 0.0
            return max(0.0, self._next_attempt_at - float(self._clock()))

    def _run_thread(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                active = self._enabled and not self._stopped_reason
            if not active:
                break
            wait = self._compute_wait()
            if wait > 0.0:
                if self._stop.wait(wait):
                    break
            if self._stop.is_set():
                break
            self.tick()

    def start(self) -> None:
        """启动线程模式。重复调用是安全的（已在跑则不动）。"""
        with self._lock:
            if not self._enabled or self._stopped_reason:
                self._enabled = True
                self._attempts = 0
                self._last_error = ""
                self._stopped_reason = ""
                self._next_attempt_at = float(self._clock())
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop = threading.Event()
            self._thread = threading.Thread(
                target=self._run_thread, name="sdr-link-supervisor", daemon=True)
            thread = self._thread
        thread.start()

    def stop(self, timeout: float = 1.0) -> None:
        """立即结束线程模式：置位 Event（唤醒 wait）并等待退出。"""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        self._thread = None

    def run(self) -> None:
        """阻塞式常驻循环（无线程），使用注入的 ``sleeper``。

        仅给不便起线程的调用方使用；需要可立即停止时请用 ``start()`` /
        ``stop()``（线程模式用 ``Event.wait``）。
        """
        while True:
            with self._lock:
                active = self._enabled and not self._stopped_reason
            if not active:
                return
            wait = self._compute_wait()
            if wait > 0.0:
                self._sleeper(wait)
            self.tick()


__all__ = [
    "SdrLinkSupervisor",
    "compute_backoff",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_BASE_DELAY_S",
    "DEFAULT_MAX_DELAY_S",
    "DEFAULT_POLL_INTERVAL_S",
]
