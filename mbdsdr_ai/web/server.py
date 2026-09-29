# SPDX-License-Identifier: MIT
"""
mbdsdr_ai/web/server.py — 轻量 HTTP + WebSocket 服务器
======================================================

轻量 HTTP + WebSocket 服务器设计（OpenWebRX 仅作技术参考，本仓未包含其源代码）：
  - RequestHandler(BaseHTTPRequestHandler) 路由
  - WebSocket 握手 + 帧编解码（server 端不掩码）
  - 多客户端共享一个后端

端点：
  GET  /                 浏览器前端（static/index.html）
  GET  /app.js /style.css /static/...   静态资源（白名单，防穿越）
  GET  /api/status       JSON 状态；无后端 -> {"connected": false}
  GET  /api/spectrum     最近一帧 FFT JSON；无数据 -> {"connected": false}
  GET  /api/recordings   列出 ~/.mbdsdr/recordings/ 下的录制文件
  GET  /api/recordings/<file>  下载录制文件
  WS   /ws/spectrum       二进制频谱流（下行）+ 文本 JSON 命令（上行）
  GET  /ws/audio         WebSocket 音频流（二进制，0x02 头）

WebSocket 上行命令（文本帧 JSON）：
  {cmd:"set_frequency", value:hz} / {cmd:"set_mode", value:"WFM"} /
  {cmd:"set_gain", value:db}      / {cmd:"get_status"}
  无后端 -> 回复 {type:"error", error:"not_connected"}。

红线：后端为 None 或未连接时，所有端点显式报告未连接，绝不返回假数据。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import mimetypes
import os
import queue
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse, unquote

from .streamer import AudioStreamer, SpectrumStreamer

logger = logging.getLogger(__name__)

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_OPCODE_TEXT = 0x1
_OPCODE_BINARY = 0x2
_OPCODE_CLOSE = 0x8
_OPCODE_PING = 0x9
_OPCODE_PONG = 0xA


# --------------------------------------------------------------------------- #
# 极简 WebSocket（裁剪到 server->client 扇出所需）
# --------------------------------------------------------------------------- #
class _WebSocket:
    """在一个已经完成 HTTP 升级的连接上跑收发循环。

    生命周期：由 RequestHandler 在 do_GET 中构造并阻塞式 :meth:`run`；
    run 返回后连接即关闭。出站帧进 self.out，由 send 线程写入 socket。
    """

    def __init__(self, handler, streamer):
        self.handler = handler
        self.streamer = streamer
        # 出站项为 (opcode, payload) 元组：二进制频谱帧 / 文本命令响应
        self.out: "queue.Queue[tuple[int, bytes]]" = queue.Queue(maxsize=64)
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

    # -- 供 streamer / 控制层调用 ----------------------------------------- #
    def send(self, data: bytes) -> None:
        """下行二进制频谱帧（streamer 调用）。"""
        self._enqueue(_OPCODE_BINARY, data)

    def send_text(self, obj: Dict[str, Any]) -> None:
        """下行文本 JSON（命令响应 / 状态）。"""
        self._enqueue(_OPCODE_TEXT, json.dumps(obj, allow_nan=False).encode("utf-8"))

    def _enqueue(self, opcode: int, payload: bytes) -> None:
        try:
            self.out.put_nowait((opcode, payload))
        except queue.Full:
            self.close()

    def close(self) -> None:
        self.open = False

    # -- 主循环 ---------------------------------------------------------- #
    def run(self) -> None:
        self.streamer.add_subscriber(self)
        # 新连接立即下发一次状态，前端据此显"已连接/未连接"（不造假）
        try:
            webserver = self.handler.server.webserver  # type: ignore[attr-defined]
            self.send_text({"type": "status", **webserver.status()})
        except Exception:  # pragma: no cover - 防御性
            pass
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
                item = self.out.get(timeout=0.5)
            except queue.Empty:
                continue
            # 兼容两种入队项：streamer 广播的裸 bytes（二进制帧）
            # 与本类 send_text 入队的 (opcode, payload) 元组
            if isinstance(item, tuple):
                opcode, data = item
            else:
                opcode, data = _OPCODE_BINARY, item
            try:
                self.handler.wfile.write(self._frame(opcode, data))
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
            elif opcode == _OPCODE_TEXT:
                # 上行控制命令（JSON）-> 派发给 WebServer，文本回复
                try:
                    msg = json.loads(payload.decode("utf-8"))
                    webserver = self.handler.server.webserver  # type: ignore[attr-defined]
                    reply = webserver.handle_command(msg)
                except (ValueError, KeyError, TypeError) as e:
                    reply = {"type": "error", "error": "bad_command", "detail": str(e)}
                self.send_text(reply)


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

# 浏览器前端静态资源目录（mbdsdr_ai/web/static/）
_STATIC_DIR = Path(__file__).resolve().parent / "static"
# 白名单：只允许这些相对路径，杜绝路径穿越
_STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/app.js": "app.js",
    "/style.css": "style.css",
}
_RECORDING_EXTS = {".wav", ".cf32", ".raw", ".bin", ".iq", ".csv"}


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

    def _send_bytes(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- routes ---------------------------------------------------------- #
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        ws: WebServer = self.server.webserver  # type: ignore[attr-defined]

        if path in _STATIC_FILES:
            self._serve_static(_STATIC_FILES[path])
        elif path == "/api/status":
            self._send_json(200, ws.status())
        elif path == "/api/spectrum":
            self._send_json(200, ws.spectrum_json())
        elif path == "/api/recordings":
            self._send_json(200, {"recordings": ws.list_recordings()})
        elif path.startswith("/api/recordings/"):
            fname = unquote(path[len("/api/recordings/"):])
            self._serve_recording(ws, fname)
        elif path == "/ws/spectrum":
            self._serve_ws(ws.spectrum_streamer)
        elif path == "/ws/audio":
            self._serve_ws(ws.audio_streamer)
        else:
            self._send_json(404, {"error": "not found", "path": path})

    def _serve_static(self, rel: str) -> None:
        fp = (_STATIC_DIR / rel).resolve()
        # 二次保险：resolve 后必须仍在 static 目录内
        if _STATIC_DIR.resolve() not in fp.parents or not fp.is_file():
            # 前端文件缺失 -> 退回极简状态页，不 500
            self._send_html(200, _INDEX_HTML)
            return
        ctype = mimetypes.guess_type(str(fp))[0] or "application/octet-stream"
        self._send_bytes(200, fp.read_bytes(), ctype + "; charset=utf-8")

    def _serve_recording(self, ws: "WebServer", fname: str) -> None:
        info = ws.recording_path(fname)
        if info is None:
            self._send_json(404, {"error": "not found", "file": fname})
            return
        fp, _size = info
        self._send_bytes(200, fp.read_bytes(), "application/octet-stream")

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
        # 后端若暴露当前频率/模式/增益属性，一并上报（鸭型，缺失则不报）
        be = self.backend
        freq = getattr(be, "frequency", None) or getattr(be, "center_freq", None)
        gain = getattr(be, "gain", None)
        mode = getattr(be, "mode", None)
        return {
            "service": "mbdsdr_ai.web",
            "version": "0.1.0",
            "connected": connected,
            "center_freq_hz": float(freq) if isinstance(freq, (int, float)) else None,
            "mode": mode if isinstance(mode, str) else None,
            "gain_db": float(gain) if isinstance(gain, (int, float)) else None,
            "annotations": self.spectrum_streamer.annotations(),
            "clients": {
                "spectrum": self.spectrum_streamer.subscriber_count(),
                "audio": self.audio_streamer.subscriber_count(),
            },
            "fft_fps": self.spectrum_streamer.fps,
            "fft_size": self.spectrum_streamer.fft_size,
        }

    # -- WebSocket 远程控制命令 ------------------------------------------- #
    def handle_command(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """处理浏览器上行 JSON 命令。无后端安全降级为 {error:"not_connected"}。"""
        if not isinstance(msg, dict):
            return {"type": "error", "error": "bad_command"}
        cmd = msg.get("cmd")
        value = msg.get("value")

        if cmd == "get_status":
            return {"type": "status", **self.status()}

        # 红线：无后端 -> 所有 set_* 命令拒绝，不造假成功
        if not self._backend_connected():
            return {"type": "error", "cmd": cmd, "error": "not_connected"}

        be = self.backend
        if cmd == "set_frequency":
            fn = getattr(be, "set_frequency", None)
            if not callable(fn):
                return {"type": "error", "error": "unsupported"}
            fn(float(value))
            return {"type": "ack", "cmd": cmd, "value": value}
        if cmd == "set_gain":
            fn = getattr(be, "set_gain", None)
            if not callable(fn):
                return {"type": "error", "error": "unsupported"}
            fn(float(value))
            return {"type": "ack", "cmd": cmd, "value": value}
        if cmd == "set_mode":
            fn = getattr(be, "set_mode", None)
            if not callable(fn):
                return {"type": "error", "error": "unsupported"}
            fn(str(value))
            return {"type": "ack", "cmd": cmd, "value": value}
        return {"type": "error", "cmd": cmd, "error": "unknown_command"}

    # -- 录制文件浏览 ----------------------------------------------------- #
    @staticmethod
    def _recordings_dir() -> Path:
        return Path.home() / ".mbdsdr" / "recordings"

    def list_recordings(self) -> list:
        """列出 ~/.mbdsdr/recordings/ 下的录制文件；目录不存在/无文件 -> []。"""
        d = self._recordings_dir()
        if not d.is_dir():
            return []
        out = []
        for p in sorted(d.iterdir()):
            if not p.is_file():
                continue
            if p.suffix.lower() not in _RECORDING_EXTS:
                continue
            st = p.stat()
            out.append({
                "name": p.name,
                "size_bytes": st.st_size,
                "mtime": st.st_mtime,
            })
        return out

    def recording_path(self, fname: str):
        """校验并返回录制文件绝对路径；非法/不存在 -> None。"""
        d = self._recordings_dir()
        if not fname or "/" in fname or "\\" in fname or fname.startswith(".."):
            return None
        fp = (d / fname).resolve()
        if d.resolve() not in fp.parents or not fp.is_file():
            return None
        return fp, fp.stat().st_size

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
