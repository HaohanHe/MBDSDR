"""
mbdsdr_ai/multi_user.py — 多用户共享 SDR 调度
==============================================

对照 OpenWebRX ``owrx/source/__init__.py:438-467``：
  - ``addClient`` / ``removeClient``：客户端注册/注销；
  - ``hasClients(USER)``：有用户在就启动后端，最后一个用户离开后
    延迟 ``timeout_s`` 秒关闭设备（避免刷新页面就反复重启 SDR）；
  - 区分 USER（Web 客户端）与 BACKGROUND（后台解码）两类，后台任务
    即使无 Web 用户也保持设备。

本模块不绑定具体硬件：后端通过 ``backend`` 注入，``None`` 时安全空转。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

__all__ = ["MultiUserManager", "ClientSession"]


@dataclass
class ClientSession:
    client_id: str
    kind: str = "user"          # "user" | "background"
    last_seen: float = 0.0


class MultiUserManager:
    """共享后端：引用计数 + 超时关闭。

    backend 需提供 ``start()`` / ``stop()`` 方法；为 None 时不报错。
    """

    def __init__(self, backend: Optional[object] = None,
                 timeout_s: float = 15.0,
                 clock: Callable[[], float] = time.time):
        self._backend = backend
        self._timeout_s = timeout_s
        self._clock = clock
        self._lock = threading.RLock()
        self._clients: Dict[str, ClientSession] = {}
        self._running = False
        self._stop_at: Optional[float] = None

    @property
    def backend(self) -> Optional[object]:
        return self._backend

    @backend.setter
    def backend(self, b: Optional[object]) -> None:
        with self._lock:
            self._backend = b
            if self._running and b is not None:
                self._ensure_started()

    def add_client(self, client_id: str, kind: str = "user") -> None:
        with self._lock:
            self._clients[client_id] = ClientSession(
                client_id=client_id, kind=kind, last_seen=self._clock())
            self._cancel_shutdown()
            self._ensure_started()

    def remove_client(self, client_id: str) -> None:
        with self._lock:
            self._clients.pop(client_id, None)
            self._evaluate_shutdown()

    def touch(self, client_id: str) -> None:
        """用户活动，刷新 last_seen（用于超时清理僵死连接）。"""
        with self._lock:
            c = self._clients.get(client_id)
            if c:
                c.last_seen = self._clock()
                self._cancel_shutdown()

    def prune_idle(self, idle_s: float = 300.0) -> List[str]:
        """清理超过 idle_s 未活动的客户端。"""
        now = self._clock()
        removed = []
        with self._lock:
            for cid in list(self._clients):
                if now - self._clients[cid].last_seen > idle_s:
                    self._clients.pop(cid, None)
                    removed.append(cid)
            if removed:
                self._evaluate_shutdown()
        return removed

    def _ensure_started(self) -> None:
        if self._running:
            return
        if self._backend is not None and hasattr(self._backend, "start"):
            try:
                self._backend.start()
            except Exception:
                pass
        self._running = True

    def _cancel_shutdown(self) -> None:
        self._stop_at = None

    def _evaluate_shutdown(self) -> None:
        has_users = any(c.kind == "user" for c in self._clients.values())
        has_bg = any(c.kind == "background" for c in self._clients.values())
        if not has_users and not has_bg:
            # 安排超时关闭
            self._stop_at = self._clock() + self._timeout_s

    def poll(self) -> bool:
        """触发超时检查。返回是否关闭了设备。"""
        with self._lock:
            if self._stop_at is not None and self._clock() >= self._stop_at:
                self._shutdown_now()
                return True
            return False

    def _shutdown_now(self) -> None:
        if self._backend is not None and hasattr(self._backend, "stop"):
            try:
                self._backend.stop()
            except Exception:
                pass
        self._running = False
        self._stop_at = None

    def force_stop(self) -> None:
        with self._lock:
            self._shutdown_now()

    # --- 状态查询 --- #
    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def clients(self) -> List[ClientSession]:
        with self._lock:
            return list(self._clients.values())
