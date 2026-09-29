# SPDX-License-Identifier: MIT
"""
MBDSDR AI 内核 - 远程控制服务器
================================

提供与 GQRX remote_control 兼容的行命令远程控制协议（GQRX 仅作技术参考，
本仓未包含其源代码）：

  - 默认端口 7356 / 仅 127.0.0.1
  - 按行读、空格拆分命令
  - 应答 RPRT 0 / RPRT 1
  - q/Q 关闭连接

支持命令（按本任务简化）：
  * ``f``          读频率（Hz）
  * ``f <hz>``     设频率
  * ``m``          读模式
  * ``m <mode>``   设模式（FM/AM/NFM/WFM/USB/LSB/CW...）
  * ``g``          读增益（dB）
  * ``g <db>``     设增益
  * ``q``          关闭连接

无后端（backend=None）时返回 ``RPRT 1`` 但绝不抛异常崩溃。

我们的增强：
  * 同一套 :meth:`process_line` 命令分发器同时服务 TCP 与 WebSocket
    （:meth:`ws_handle`），便于 web 前端接入。
"""

from __future__ import annotations

import logging
import socket
import socketserver
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_RC_PORT = 7356
DEFAULT_RC_HOST = "127.0.0.1"

# 命令分发器返回这个 sentinel 表示要求关闭连接（对应 q）
CLOSE_CONNECTION = "__CLOSE__"

RPRT_OK = "RPRT 0\n"
RPRT_ERR = "RPRT 1\n"


class RemoteControl:
    """GQRX/hamlib rigctld 风格的远程控制服务器。

    Args:
        backend: 可选后端对象，需实现 ``get_freq/set_freq/get_mode/set_mode/
                 get_gain/set_gain``。为 None 时所有写命令安全降级为 RPRT 1。
        host:    监听地址，默认 127.0.0.1。
        port:    监听端口，默认 7356。
    """

    def __init__(self, backend: Any = None,
                 host: str = DEFAULT_RC_HOST, port: int = DEFAULT_RC_PORT):
        self._backend = backend
        self.host = host
        self.port = port
        self._lock = threading.Lock()          # 命令处理串行化（线程安全）
        self._server: Optional[socketserver.ThreadingTCPServer] = None
        self._server_thread: Optional[threading.Thread] = None
        # web 前端可注册的事件回调（我们的增强）
        self.on_command: Optional[Callable[[str, str], None]] = None

    # ------------------------------------------------------------ backend glue
    def _call_backend(self, method: str, *args) -> Any:
        """安全调用后端方法；无后端或抛异常都返回 None（不崩）。"""
        if self._backend is None:
            return None
        fn = getattr(self._backend, method, None)
        if not callable(fn):
            return None
        try:
            return fn(*args)
        except Exception as e:  # 后端任何异常都不外泄
            logger.warning("remote_control backend %s(%s) failed: %s",
                           method, args, e)
            return None

    # ------------------------------------------------------------ core dispatch
    def process_line(self, line: str) -> str:
        """解析单行命令并返回应答字符串（纯函数，无 socket，便于单测）。

        对应 remote_control.cpp:201-280。
        """
        parts = line.strip().split()
        if not parts:
            return RPRT_ERR
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else None

        with self._lock:
            if cmd == "f":
                return self._handle_freq(arg)
            if cmd == "m":
                return self._handle_mode(arg)
            if cmd == "g":
                return self._handle_gain(arg)
            if cmd == "q":
                return CLOSE_CONNECTION
            # 未知命令 -> RPRT 1（remote_control.cpp:271-276）
            return RPRT_ERR

    def _handle_freq(self, arg: Optional[str]) -> str:
        if arg is None:  # get
            v = self._call_backend("get_freq")
            return f"{int(v)}\n" if v is not None else RPRT_ERR
        try:
            hz = int(float(arg))
        except ValueError:
            return RPRT_ERR
        ok = self._call_backend("set_freq", hz)
        return RPRT_OK if ok else RPRT_ERR

    def _handle_mode(self, arg: Optional[str]) -> str:
        if arg is None:  # get
            v = self._call_backend("get_mode")
            return f"{v}\n" if v is not None else RPRT_ERR
        ok = self._call_backend("set_mode", arg.upper())
        return RPRT_OK if ok else RPRT_ERR

    def _handle_gain(self, arg: Optional[str]) -> str:
        if arg is None:  # get
            v = self._call_backend("get_gain")
            return f"{v}\n" if v is not None else RPRT_ERR
        try:
            db = float(arg)
        except ValueError:
            return RPRT_ERR
        ok = self._call_backend("set_gain", db)
        return RPRT_OK if ok else RPRT_ERR

    # ------------------------------------------------------------ WebSocket 钩子
    def ws_handle(self, text: str) -> str:
        """WebSocket 单帧处理（我们的增强）：逐行处理，拼回应答。

        web 前端一帧可能含多条命令；这里按行切分，返回所有应答拼接。
        """
        out = []
        for line in text.splitlines():
            if not line.strip():
                continue
            resp = self.process_line(line)
            if resp == CLOSE_CONNECTION:
                continue
            out.append(resp)
        return "".join(out)

    # ------------------------------------------------------------ TCP server
    def start(self) -> None:
        """在后台线程启动 TCP 服务器（不阻塞主流程）。"""
        if self._server is not None:
            return

        rc = self  # closure

        class _Handler(socketserver.StreamRequestHandler):
            def handle(self):  # 对应 remote_control.cpp:201 startRead
                # 简单的 host 白名单（默认仅 127.0.0.1 连接进来即可）
                while True:
                    try:
                        raw = self.rfile.readline()
                    except OSError:
                        break
                    if not raw:
                        break
                    try:
                        line = raw.decode("utf-8", "replace")
                    except Exception:
                        line = ""
                    resp = rc.process_line(line)
                    if resp == CLOSE_CONNECTION:
                        break
                    try:
                        self.wfile.write(resp.encode("utf-8"))
                    except OSError:
                        break

        class _Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._server = _Server((self.host, self.port), _Handler)
        self._server_thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._server_thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
            self._server_thread = None
