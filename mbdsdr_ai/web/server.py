"""
mbdsdr_ai/web/server.py — 轻量 HTTP + WebSocket 服务器
======================================================

移植自 OpenWebRX：
  - owrx/http.py:179   RequestHandler(BaseHTTPRequestHandler) 路由
  - owrx/websocket.py:47 WebSocketConnection —— 握手 + 帧编解码（server 不掩码）
  - owrx/connection.py:116 OpenWebRxReceiverClient —— 多客户端共享一个后端

端点：
  GET  /                简单状态页
  GET  /api/status      JSON 状态；无后端 -> {"connected": false}
  GET  /api/spectrum    最近一帧 FFT JSON；无数据 -> {"connected": false}
  GET  /ws/spectrum     WebSocket 瀑布流（二进制，0x01 头）
  GET  /ws/audio        WebSocket 音频流（二进制，0x02 头）

红线：后端为 None 或未连接时，所有端点显式报告未连接，绝不返回假数据。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import queue
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from .streamer import AudioStreamer, SpectrumStreamer

logger = logging.getLogger(__name__)

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_OPCODE_TEXT = 0x1
_OPCODE_BINARY = 0x2
_OPCODE_CLOSE = 0x8
_OPCODE_PING = 0x9
_OPCODE_PONG = 0xA


# --------------------------------------------------------------------------- #
# 极简 WebSocket（移植 owrx/websocket.py，裁剪到 server->client 扇出所需）
# --------------------------------------------------------------------------- #
class _WebSocket:
    """在一个已经完成 HTTP 升级的连接上跑收发循环。

    生命周期：由 RequestHandler 在 do_GET 中构造并阻塞式 :meth:`run`；
    run 返回后连接即关闭。出站帧进 self.out，由 send 线程写入 socket。
    """

    def __init__(self, handler, streamer):
        self.handler = handler
        self.streamer = streamer
        self.out: "queue.Queue[bytes]" = queue.Queue(maxsize=64)
        self.open = True
        self.socket_error = False

    # -- 握手 ------------------------------------------------------------ #
    def handshake(self) -> None:
        headers = {k.lower(): v for k, v in self.handler.headers.items()}
        if headers.get("upgrade", "").lower() != "websocket":
            raise ValueError("not a websocket upgrade")
        key = headers.get("sec-websocket-key")
        if not key:
            raise ValueError("missing sec-websocket-key")
        accept = base64.b64encode(
            hashlib.sha1((key + _WS_GUID).encode()).digest()
        ).decode()
        resp = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "\r\n"
        )
        self.handler.wfile.write(resp.encode())
        self.handler.wfile.flush()

    # -- 帧编解码 -------------------------------------------------------- #
    @staticmethod
    def _frame(opcode: int, payload: bytes) -> bytes:
        header = bytearray([0x80 | (opcode & 0x0F)])
        n = len(payload)
        if n < 126:
            header.append(n)
        elif n < (1 << 16):
            header.append(126)
            header += struct.pack(">H", n)
        else:
            header.append(127)
            header += struct.pack(">Q", n)
        return bytes(header) + payload

    # -- 供 streamer 调用 ------------------------------------------------ #
    def send(self, data: bytes) -> None:
        try:
            self.out.put_nowait(data)
        except queue.Full:
            self.close()

    def close(self) -> None:
        self.open = False

    # -- 主循环 ---------------------------------------------------------- #
    def run(self) -> None:
        self.streamer.add_subscriber(self)
        sender = threading.Thread(target=self._send_loop, name="ws-sender", daemon=True)
        sender.start()
        try:
            self._recv_loop()
        except (OSError, ValueError):
            self.socket_error = True
        finally:
            self.open = False
            self.streamer.remove_subscriber(self)
            if not self.socket_error:
                try:
                    self.handler.wfile.write(self._frame(_OPCODE_CLOSE, b""))
                    self.handler.wfile.flush()
                except OSError:
                    pass

    def _send_loop(self) -> None:
        while self.open:
            try:
                data = self.out.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.handler.wfile.write(self._frame(_OPCODE_BINARY, data))
                self.handler.wfile.flush()
            except OSError:
                self.socket_error = True
                self.open = False
                return

    def _read_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self.handler.rfile.read(n - len(buf))
            if not chunk:
                raise OSError("connection closed")
            buf.extend(chunk)
        return bytes(buf)

    def _recv_loop(self) -> None:
        self.handler.request.settimeout(1.0)
        while self.open:
            try:
                header = self._read_exact(2)
            except (OSError, struct.error, TimeoutError):
                return
            (b0, b1) = header
            opcode = b0 & 0x0F
            masked = bool(b1 & 0x80)
            length = b1 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if masked else b""
            payload = self._read_exact(length) if length else b""
            if masked:
                payload = bytes(p ^ mask[i % 4] for i, p in enumerate(payload))
            if opcode == _OPCODE_CLOSE:
                return
            if opcode == _OPCODE_PING:
                try:
                    self.handler.wfile.write(self._frame(_OPCODE_PONG, payload))
                    self.handler.wfile.flush()
                except OSError:
                    return
            # text / pong: ignore (we are server->client streaming only)


# --------------------------------------------------------------------------- #
# HTTP 请求处理
# --------------------------------------------------------------------------- #
_INDEX_HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>MBDSDR Web</title></head><body>
<h1>MBDSDR Web 流式接口</h1>
<ul>
<li>GET /api/status — 状态 JSON</li>
<li>GET /api/spectrum — 最近一帧 FFT JSON</li>
<li>WS /ws/spectrum — 瀑布流</li>
<li>WS /ws/audio — 音频流</li>
</ul><div id="s"></div>
<script>fetch('/api/status').then(r=>r.json()).then(d=>{document.getElementById('s').textContent=JSON.stringify(d,null,2)})</script>
</body></html>"""


class _RequestHandler(BaseHTTPRequestHandler):
    server_version = "MBDSDRWeb/0.1"

    def log_message(self, *args):  # 静默
        pass

    # -- helpers --------------------------------------------------------- #
    def _send_json(self, code: int, obj: Dict[str, Any]) -> None:
        body = json.dumps(obj, allow_nan=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, code: int, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- routes ---------------------------------------------------------- #
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        ws: WebServer = self.server.webserver  # type: ignore[attr-defined]

        if path == "/":
            self._send_html(200, _INDEX_HTML)
        elif path == "/api/status":
            self._send_json(200, ws.status())
        elif path == "/api/spectrum":
            self._send_json(200, ws.spectrum_json())
        elif path == "/ws/spectrum":
            self._serve_ws(ws.spectrum_streamer)
        elif path == "/ws/audio":
            self._serve_ws(ws.audio_streamer)
        else:
            self._send_json(404, {"error": "not found", "path": path})

    def _serve_ws(self, streamer) -> None:
        try:
            ws = _WebSocket(self, streamer)
            ws.handshake()
        except (ValueError, OSError) as e:
            logger.warning("websocket handshake failed: %s", e)
            try:
                self.send_response(400)
                self.end_headers()
            except OSError:
                pass
            return
        ws.run()


# --------------------------------------------------------------------------- #
# WebServer
# --------------------------------------------------------------------------- #
class WebServer:
    """轻量 Web 服务器。多客户端共享同一个 SpectrumStreamer/AudioStreamer。

    Parameters
    ----------
    host, port:
        监听地址；port=0 表示选空闲端口（测试用）。
    backend:
        可选后端对象；只要暴露 ``.connected`` (bool) 即可。None 或 connected=False
        时所有端点显式报告未连接，不造假。
    spectrum_streamer, audio_streamer:
        可注入；默认各自新建。
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8000,
        backend: Any = None,
        spectrum_streamer: Optional[SpectrumStreamer] = None,
        audio_streamer: Optional[AudioStreamer] = None,
        fft_size: int = 1024,
        target_bins: int = 256,
        fps: float = 10.0,
    ):
        self.host = host
        self.port = port
        self.backend = backend
        self.spectrum_streamer = spectrum_streamer or SpectrumStreamer(
            fft_size=fft_size, target_bins=target_bins, fps=fps
        )
        self.audio_streamer = audio_streamer or AudioStreamer()
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # -- lifecycle -------------------------------------------------------- #
    def start(self) -> None:
        if self._httpd is not None:
            return
        self._httpd = ThreadingHTTPServer((self.host, self.port), _RequestHandler)
        self._httpd.webserver = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="mbdsdr-web", daemon=True
        )
        self._thread.start()
        self.host, self.port = self._httpd.server_address

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def __enter__(self) -> "WebServer":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- status helpers --------------------------------------------------- #
    def _backend_connected(self) -> bool:
        if self.backend is None:
            return False
        return bool(getattr(self.backend, "connected", False))

    def status(self) -> Dict[str, Any]:
        connected = self._backend_connected()
        return {
            "service": "mbdsdr_ai.web",
            "version": "0.1.0",
            "connected": connected,
            "clients": {
                "spectrum": self.spectrum_streamer.subscriber_count(),
                "audio": self.audio_streamer.subscriber_count(),
            },
            "fft_fps": self.spectrum_streamer.fps,
            "fft_size": self.spectrum_streamer.fft_size,
        }

    def spectrum_json(self) -> Dict[str, Any]:
        if not self._backend_connected():
            # 红线：无后端不造假
            return {"connected": False}
        latest = self.spectrum_streamer.latest()
        if latest is None:
            return {"connected": True, "data": False, "note": "backend connected, no IQ yet"}
        return {
            "connected": True,
            "data": True,
            "center_freq_hz": latest["center_freq_hz"],
            "samp_rate_hz": latest["samp_rate_hz"],
            "db_min": latest["db_min"],
            "db_max": latest["db_max"],
            "peak_bin": latest["peak_bin"],
            "peak_freq_hz": latest["peak_freq_hz"],
            "bins": latest["bins"].tolist(),
            "annotations": self.spectrum_streamer.annotations(),
        }
