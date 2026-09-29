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
        最大重试次数；None = 无限重试（默认）。
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
        max_attempts: Optional[int] = None,
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
        self._max_attempts = max_attempts

        self._backend: Any = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

        # 断开前抓拍的设置，重连后恢复
        self._settings_snapshot: Dict[str, Any] = {}
        # 状态：上一轮是否认为已连接；是否已 give_up
        self._was_connected = False
        self._gave_up = False

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
    def _retry_loop(self, backend: Any) -> bool:
        """指数退避重试重连。返回 True=成功重连，False=give_up/被 stop。"""
        attempt = 0
        while not self._stop.is_set():
            attempt += 1
            if self._max_attempts is not None and attempt > self._max_attempts:
                self._gave_up = True
                _safe_callback(self.on_give_up)
                return False

            _safe_callback(self.on_reconnecting, attempt)
            ok = False
            try:
                ok = bool(backend.connect())
            except Exception as exc:
                logger.warning("第 %d 次重连异常: %s", attempt, exc)
                ok = False

            if ok:
                self._restore_settings(backend, self._settings_snapshot)
                _safe_callback(self._start_audio)
                self._gave_up = False
                _safe_callback(self.on_reconnected)
                return True

            # 退避等待（可被 stop 打断）
            delay = compute_backoff(attempt, self._base_delay, self._max_delay)
            if self._stop.wait(delay):  # True = 被 stop 唤醒
                return False
        return False

    # ------------------------------------------------------------------
    # 监控主循环
    # ------------------------------------------------------------------
    def _run(self) -> None:
        backend = self._backend
        self._was_connected = self._is_connected(backend)
        while not self._stop.is_set():
            connected = self._is_connected(backend)

            if connected:
                self._was_connected = True
                self._gave_up = False
                if self._stop.wait(self._poll_interval):
                    break
                continue

            # 当前未连接：若刚刚才从连接态掉下来，先记录断连事件
            if self._was_connected:
                self._settings_snapshot = self._capture_settings(backend)
                _safe_callback(self._stop_audio)
                _safe_callback(self.on_disconnected)
                self._was_connected = False

            # 进入重连退避循环（阻塞到重连成功 / give_up / stop）
            self._retry_loop(backend)

            # give_up 后继续周期性探测（用户可能随时插回设备），
            # 但不再重复 on_give_up / on_disconnected。
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

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def gave_up(self) -> bool:
        return self._gave_up


__all__ = [
    "ReconnectionManager",
    "compute_backoff",
]
