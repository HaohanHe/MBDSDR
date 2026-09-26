"""
设备看门狗（DeviceWatchdog）
=============================

在独立 QThread 中周期性地 ping 后端连接状态：
- 连接正常 -> 对外上报 connected；
- 连接断开 -> 对外上报 disconnected，并按间隔自动尝试重连（reconnecting）；
- 任何后端异常都被捕获、转为 ``error`` 信号，看门狗线程绝不因此崩溃。

设计约束：
- 只持有 backend 引用、只读地调用其公开接口（get_status / connect），
  绝不修改 backend 内部状态。
- backend 可为 None：此时一直上报 disconnected，不做任何重连动作。
- 允许注入自定义 ping 函数便于测试，但生产路径默认走标准 get_status 探测。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)

# 状态机取值
STATE_DISCONNECTED = "disconnected"
STATE_RECONNECTING = "reconnecting"
STATE_CONNECTED = "connected"
# 启动瞬间的未知态：第一次 ping 后无论结果都会对外上报真实初始状态，
# 使 UI 能拿到一份完整的初始状态报告。
STATE_UNKNOWN = ""


class DeviceWatchdog(QThread):
    """周期性探测后端连接状态并自动重连的看门狗线程。

    Signals:
        status_changed(str): 状态迁移时发出，取值
            "connected" / "disconnected" / "reconnecting"。
        connected(): 进入已连接状态。
        disconnected(): 进入已断开状态。
        reconnecting(): 开始一次重连尝试。
        error(str): 后端调用异常时附带错误描述。
    """

    status_changed = Signal(str)
    connected = Signal()
    disconnected = Signal()
    reconnecting = Signal()
    error = Signal(str)

    def __init__(
        self,
        backend: Any,
        ping_interval_ms: int = 2000,
        reconnect_interval_ms: int = 5000,
        ping_fn: Optional[Callable[[Any], bool]] = None,
        parent: Optional[Any] = None,
    ) -> None:
        """
        参数：
            backend: SDR 后端实例；为 None 时恒为 disconnected。
            ping_interval_ms: 每次状态探测的间隔（毫秒）。
            reconnect_interval_ms: 两次重连尝试之间的最小间隔（毫秒）。
            ping_fn: 可选的自定义探测函数 ``ping_fn(backend) -> bool``；
                不传则使用默认的 ``backend.get_status().connected`` 探测。
        """
        super().__init__(parent)
        self._backend = backend
        self._ping_ms = max(1, int(ping_interval_ms))
        self._reconnect_ms = max(1, int(reconnect_interval_ms))
        self._ping_fn = ping_fn

        self._stop_event = threading_event()
        self._state = STATE_UNKNOWN
        self._last_reconnect_attempt = 0.0  # 单调时钟秒

    # ------------------------------------------------------------------
    # 内部探测
    # ------------------------------------------------------------------
    def _ping_ok(self) -> bool:
        """探测后端当前是否已连接。任何异常都视为未连接（不向外抛）。"""
        backend = self._backend
        if backend is None:
            return False
        try:
            if self._ping_fn is not None:
                return bool(self._ping_fn(backend))
            status = backend.get_status()
            return bool(getattr(status, "connected", False))
        except Exception as exc:  # 探测异常按未连接处理
            logger.warning("看门狗 ping 异常（按未连接处理）: %s", exc)
            return False

    # ------------------------------------------------------------------
    # 状态迁移
    # ------------------------------------------------------------------
    def _set_state(self, new_state: str) -> None:
        if new_state == self._state:
            return
        self._state = new_state
        self.status_changed.emit(new_state)
        if new_state == STATE_CONNECTED:
            self.connected.emit()
        elif new_state == STATE_DISCONNECTED:
            self.disconnected.emit()
        elif new_state == STATE_RECONNECTING:
            self.reconnecting.emit()

    def _try_reconnect(self) -> None:
        """发起一次重连尝试。backend 为 None 时直接跳过。"""
        backend = self._backend
        if backend is None:
            return
        self._set_state(STATE_RECONNECTING)
        try:
            backend.connect()
        except Exception as exc:  # 重连失败只报 error，继续循环
            self.error.emit(str(exc))
            logger.warning("看门狗自动重连失败: %s", exc)

    # ------------------------------------------------------------------
    # 线程主循环
    # ------------------------------------------------------------------
    def run(self) -> None:  # noqa: D401  (QThread 入口)
        while not self._stop_event.is_set():
            ok = self._ping_ok()

            if ok and self._state != STATE_CONNECTED:
                # 恢复连接：从 disconnected/reconnecting 回到 connected
                self._set_state(STATE_CONNECTED)
                self._last_reconnect_attempt = 0.0
            elif not ok and self._state != STATE_DISCONNECTED:
                # 连接丢失：先落回 disconnected
                self._set_state(STATE_DISCONNECTED)

            # 处于断开态时，按间隔尝试自动重连
            if not ok and self._backend is not None:
                now = time.monotonic()
                if (now - self._last_reconnect_attempt) * 1000.0 >= self._reconnect_ms:
                    self._last_reconnect_attempt = now
                    self._try_reconnect()

            # 用 wait 替代固定 sleep，便于 stop() 及时唤醒退出
            self._stop_event.wait(self._ping_ms / 1000.0)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def stop(self) -> None:
        """请求线程优雅退出，并等待结束（最多 3 秒）。"""
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        # QThread.wait 等待线程结束
        if not self.wait(3000):
            # 优雅退出超时才兜底 terminate（优先优雅路径）
            logger.warning("看门狗线程优雅退出超时，兜底 terminate")
            self.terminate()
            self.wait(1000)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def is_connected(self) -> bool:
        return self._state == STATE_CONNECTED

    def current_state(self) -> str:
        return self._state


def threading_event():
    """延迟导入并返回 threading.Event，便于模块顶层保持轻量。"""
    import threading
    return threading.Event()
