# SPDX-License-Identifier: MIT
"""MBDSDR Web 桥接宿主（阶段 C 的**非 Qt** 一半）：把 `WebBridge` 注入浏览器。

============================================================================
它解决什么问题
============================================================================
阶段 B 的 `mbdsdr_ai/web_bridge.py` 只定义了「可注入的形状」，没有注入者。
Qt/WebEngine 那条路要碰 `desktop/main_window.py`（操作逻辑线在途，禁止顺手改）。
本模块给出一条**完全独立、可运行、可验证**的注入路径：

    GET  /                 -> 服务 web/product/dist/index.html，并在 </head> 前
                              注入 <script src="/bridge.js"></script>
    GET  /bridge.js        -> 生成 window.mbdsdrBridge 的 shim（同源、带 token）
    POST /bridge/<method>  -> 调 WebBridge 对应方法，返回 JSON
    GET  /health           -> 存活探针
    GET  /manifest         -> 与 WebBridge.manifest() 同一份契约

于是「一套系统两形态」在 Web 侧成立：
* 直接打开 `dist/index.html`（file:// 或静态托管）→ 没有 `mbdsdrBridge`，
  走离线诚实空态；
* 打开 `python -m mbdsdr_ai.web_bridge_host` 起的 `http://127.0.0.1:<port>/`
  → 桥接存在，`MB.data` 切到 `BridgeSource`，工具回执来自真实内核注册表。

============================================================================
安全边界（本地回环服务是有攻击面的，别当它无害）
============================================================================
1. **只绑 127.0.0.1**：`ThreadingHTTPServer` 的地址写死回环，绝不 `0.0.0.0`。
2. **同源校验**：`Host` / `Origin` / `Referer` 必须是 `127.0.0.1:<port>` 或
   `localhost:<port>`；否则 403。挡 DNS rebinding 与跨源页面调用。
3. **`Sec-Fetch-Site` 校验**：跨源 `<script src>` 拉 `/bridge.js` 会带
   `cross-site`，直接 403 —— 否则任意网页都能把桥挂到自己页面上。
4. **一次性随机 token**：`/bridge.js` 内嵌 `X-MBDSDR-Bridge` 令牌，写请求必须
   带对；令牌每次启动重新生成，不落盘、不进日志。
5. **写工具要真人确认，token 永不出宿主**：`POST /bridge/confirm` 只登记
   「用户在同源页面上确认了这个动作」（绑定动作摘要、一次性、TTL 60s）；
   真正的授权仍是 `tx_guard.issue_confirmation()` 在**宿主进程内**签发的一次性
   token，前端拿不到、也伪造不了。没有这次登记，`/bridge/invokeTool` 调写工具
   只会回 `manual_gate_required`——本地 host 不是「打开写开关」的后门。
   `MBDSDR_BRIDGE_CONFIRM=deny` 可整体关掉确认端点（只读模式）。

============================================================================
诚实口径 / not_verified
============================================================================
* 这是**开发/演示用**宿主，不是生产形态。没有 TLS、没有多用户、没有端口
  协商；端口被占用会直接报错退出（不静默换端口）。
* 未做完整的 CSRF token 轮换与请求频控；同源 + Sec-Fetch-Site + 一次性 token
  是当前的全部防护。
* `getTle` 在 shim 里是**同步**的（前端 `50-dialog.js` 把它当真值判断），
  所以 TLE 在生成 `/bridge.js` 时预取并内嵌；`readSpectrum` / `invoke` 是
  Promise（前端已显式处理 `typeof r.then === "function"`）。
* `isLive()` 只表示桥挂上了，**不代表设备在线**；设备状态看工具回执。
* 站址 `site_trusted=False` 时过境恒空；不合成 IQ、不编造 TLE。
* Qt WebChannel 路线仍未做（需操作逻辑线放行 `main_window.py`）。

用法：
    python -m mbdsdr_ai.web_bridge_host --port 8770 --open
    python -m mbdsdr_ai.web_bridge_host --port 0        # 随机端口，打印 URL
    python -m mbdsdr_ai.web_bridge_host --no-mcp        # 强制离线（诚实空态演示）
    MBDSDR_BRIDGE_CONFIRM=deny python -m mbdsdr_ai.web_bridge_host   # 只读模式
"""
from __future__ import annotations

import argparse
import json
import logging
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple
from urllib.parse import urlparse

from mbdsdr_ai.web_bridge import WebBridge
from mbdsdr_ai.web_bridge_mcp import (CONFIRM_ENV, DEFAULT_CONFIRM_TTL_S,
                                      PendingConfirms, build_default_backend,
                                      confirm_enabled)

logger = logging.getLogger("mbdsdr.web_bridge_host")

#: 允许的方法名（白名单；不做反射调用）
ALLOWED_METHODS: Sequence[str] = (
    "invokeTool", "getTle", "getPasses", "readSpectrum",
    "getCapabilities", "sourceName", "isLive", "confirm",
)

#: 需要在 `/bridge.js` 里预取、同步返回的方法（前端按同步值消费）
_SYNC_METHODS = ("getTle",)


def _default_dist() -> Path:
    """仓库内默认产物路径；不存在时 `GET /` 返回 503 并给构建提示。"""
    return Path(__file__).resolve().parents[1] / "web" / "product" / "dist" / "index.html"


def _json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _js_literal(obj: Any) -> str:
    """JSON -> 可安全嵌进 <script> 的字面量（转义 `</` 防提前闭合）。"""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


class BridgeHost:
    """本地回环 HTTP 宿主：`WebBridge` ↔ 浏览器 `window.mbdsdrBridge`。"""

    def __init__(
        self,
        bridge: Optional[WebBridge] = None,
        *,
        dist: Optional[Path] = None,
        host: str = "127.0.0.1",
        port: int = 0,
        mcp: bool = True,
        pending: Optional[PendingConfirms] = None,
    ) -> None:
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError(f"只能绑回环地址，拒绝 {host!r}")
        if bridge is None:
            # 默认接**真实内核工具面**（MCPToolSet：82 工具 / 43 写工具）；
            # 构造失败才退回离线桥，并在 /health 与 /bridge.js 里如实反映。
            bridge, registry, pending_default = build_default_backend(mcp=mcp)
            self.registry = registry
            self.pending = pending if pending is not None else pending_default
        else:
            self.registry = None
            self.pending = pending if pending is not None else PendingConfirms()
        self.bridge = bridge
        self.dist = Path(dist) if dist is not None else _default_dist()
        self.host = host
        self.port = int(port)
        self.token = secrets.token_urlsafe(24)
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._boot: Optional[Dict[str, Any]] = None
        self._boot_lock = threading.Lock()

    # ── URL / 同源 ─────────────────────────────────────────────────────
    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def _allowed_hosts(self) -> set[str]:
        p = self.port
        return {f"127.0.0.1:{p}", f"localhost:{p}", f"[::1]:{p}"}

    def _allowed_origins(self) -> set[str]:
        p = self.port
        return {f"http://127.0.0.1:{p}", f"http://localhost:{p}", f"http://[::1]:{p}"}

    def _same_origin_ok(self, headers: Dict[str, str]) -> Tuple[bool, str]:
        """Host / Origin / Referer / Sec-Fetch-Site 四道校验。"""
        hs = {k.lower(): v for k, v in (headers or {}).items()}
        host = hs.get("host", "")
        # port=0 表示还没绑定（单测直接调 dispatch），此时放宽精确端口，只认主机名
        if self.port:
            if host and host not in self._allowed_hosts():
                return False, f"host not allowed: {host}"
        elif host and urlparse(f"//{host}").hostname not in ("127.0.0.1", "localhost", "::1"):
            return False, f"host not allowed: {host}"
        origin = hs.get("origin", "")
        if origin and self.port and origin not in self._allowed_origins():
            return False, f"origin not allowed: {origin}"
        referer = hs.get("referer", "")
        if referer and self.port:
            ref_origin = "{0.scheme}://{0.netloc}".format(urlparse(referer))
            if ref_origin not in self._allowed_origins():
                return False, f"referer not allowed: {referer}"
        site = hs.get("sec-fetch-site", "")
        if site and site not in ("same-origin", "none"):
            return False, f"cross-site request blocked: {site}"
        return True, ""

    def _token_ok(self, headers: Dict[str, str]) -> bool:
        hs = {k.lower(): v for k, v in (headers or {}).items()}
        return hs.get("x-mbdsdr-bridge", "") == self.token

    # ── 预取（同步方法必须内嵌） ───────────────────────────────────────
    def bootstrap(self) -> Dict[str, Any]:
        """satellites + 每颗星的 TLE，供 `/bridge.js` 内嵌（getTle 要同步）。"""
        with self._boot_lock:
            if self._boot is not None:
                return self._boot
            try:
                sats = self.bridge.list_satellites() or []
            except Exception as exc:  # provider 异常不能把整个宿主拖死
                logger.warning("list_satellites failed: %s: %s", type(exc).__name__, exc)
                sats = []
            tle: Dict[str, Any] = {}
            for s in sats:
                if not isinstance(s, dict):
                    continue
                key = str(s.get("norad") or "")
                if not key:
                    continue
                try:
                    tle[key] = self.bridge.get_tle(key)
                except Exception as exc:  # 单颗失败 → 该项 null，不编造
                    logger.warning("get_tle(%s) failed: %s: %s", key, type(exc).__name__, exc)
                    tle[key] = None
            self._boot = {
                "satellites": sats,
                "tle": tle,
                "sourceName": self.bridge.source_name(),
                # 桥挂上 ≠ 内核可用：前端用这个真值决定是否切到 BridgeSource
                "live": bool(self.bridge.is_live()),
                # 前端据此决定写工具回执里给不给「确认执行」按钮
                "canConfirm": bool(self.pending is not None and confirm_enabled()
                                   and self.bridge.confirm_wired),
                "toolCount": len(self.registry.get_tool_names()) if self.registry else 0,
                "methods": list(_SYNC_METHODS),
            }
            return self._boot

    def bootstrap_js(self) -> str:
        b = self.bootstrap()
        return (
            "/* 由 mbdsdr_ai/web_bridge_host.py 生成，勿手改。同源 + 一次性 token。 */\n"
            "(function () {\n"
            '  "use strict";\n'
            f"  var TOKEN = {_js_literal(self.token)};\n"
            '  var BASE = "/bridge";\n'
            "  function post(name, payload) {\n"
            "    return fetch(BASE + \"/\" + name, {\n"
            '      method: "POST",\n'
            '      headers: { "Content-Type": "application/json", "X-MBDSDR-Bridge": TOKEN },\n'
            "      body: JSON.stringify(payload || {})\n"
            "    }).then(function (r) {\n"
            '      if (!r.ok) throw new Error("mbdsdr bridge " + name + " HTTP " + r.status);\n'
            "      return r.json();\n"
            "    });\n"
            "  }\n"
            "  var bridge = {\n"
            f"    satellites: {_js_literal(b['satellites'])},\n"
            f"    tle: {_js_literal(b['tle'])},\n"
            f"    sourceName: function () {{ return {_js_literal(b['sourceName'])}; }},\n"
            f"    isLive: function () {{ return {_js_literal(b['live'])}; }},\n"
            f"    canConfirm: {_js_literal(b['canConfirm'])},\n"
            f"    toolCount: {_js_literal(b['toolCount'])},\n"
            "    getTle: function (norad) {\n"
            "      var k = String(norad == null ? \"\" : norad);\n"
            "      return Object.prototype.hasOwnProperty.call(bridge.tle, k) ? bridge.tle[k] : null;\n"
            "    },\n"
            "    getPasses: function (norad) { return post(\"getPasses\", { norad: norad }); },\n"
            "    readSpectrum: function () { return post(\"readSpectrum\", {}); },\n"
            "    getCapabilities: function () { return post(\"getCapabilities\", {}); },\n"
            "    invoke: function (name, args) { return post(\"invokeTool\", { name: name, args: args || {} }); },\n"
            "    /* 只登记「用户点了确认」，不签发 token —— 签发在宿主进程内完成。 */\n"
            "    confirm: function (name, args) { return post(\"confirm\", { tool: name, args: args || {} }); }\n"
            "  };\n"
            "  window.mbdsdrBridge = bridge;\n"
            "  // 60-app.js 可能已经调用过 detectSource；这里主动重选一次源，\n"
            "  // 让 MB.data 从 OfflineSource 切到 BridgeSource。\n"
            "  try {\n"
            "    if (window.MBDSDR && typeof window.MBDSDR.detectSource === \"function\")\n"
            "      window.MBDSDR.detectSource();\n"
            "  } catch (e) { /* 页面未就绪时忽略，主循环会再试 */ }\n"
            "})();\n"
        )

    # ── HTML 注入 ──────────────────────────────────────────────────────
    def injected_html(self) -> Optional[bytes]:
        if not self.dist.is_file():
            return None
        html = self.dist.read_text(encoding="utf-8")
        if "/bridge.js" in html:
            return html.encode("utf-8")
        tag = '<script src="/bridge.js"></script>\n'
        # 放在 </head> 前：同步脚本，保证 60-app.js 执行时 mbdsdrBridge 已存在
        for anchor in ("</head>", "</HEAD>"):
            if anchor in html:
                return html.replace(anchor, tag + anchor, 1).encode("utf-8")
        return (tag + html).encode("utf-8")

    # ── 路由（与 socket 解耦，便于单测） ───────────────────────────────
    def dispatch(
        self,
        method: str,
        path: str,
        body: bytes = b"",
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, bytes, str]:
        hs = {k.lower(): v for k, v in (headers or {}).items()}
        ok, why = self._same_origin_ok(hs)
        if not ok:
            return 403, _json_bytes({"ok": False, "error": "forbidden", "detail": why}), "application/json"

        route = urlparse(path).path
        if route == "/health":
            return 200, _json_bytes({
                "ok": True,
                "host": "loopback",
                "source": self.bridge.source_name(),
                "live": self.bridge.is_live(),
                "tools": len(self.registry.get_tool_names()) if self.registry else 0,
                "confirm": bool(self.pending is not None and confirm_enabled()
                                and self.bridge.confirm_wired),
            }), "application/json"

        if route == "/bridge.js":
            # 浏览器 <script src> 不能自定义头，所以这条免 token；
            # 跨源加载已由 Sec-Fetch-Site / Referer 挡住。
            return 200, self.bootstrap_js().encode("utf-8"), "application/javascript; charset=utf-8"

        if route == "/manifest":
            if not self._token_ok(hs):
                return 403, _json_bytes({"ok": False, "error": "bad_token"}), "application/json"
            return 200, self.bridge.manifest_json().encode("utf-8"), "application/json"

        if route in ("/", "/index.html"):
            html = self.injected_html()
            if html is None:
                return 503, _json_bytes({
                    "ok": False,
                    "error": "dist_missing",
                    "detail": "run: cd web/product && python run_all.py",
                    "path": str(self.dist),
                }), "application/json"
            return 200, html, "text/html; charset=utf-8"

        if route.startswith("/bridge/"):
            if not self._token_ok(hs):
                return 403, _json_bytes({"ok": False, "error": "bad_token"}), "application/json"
            name = route[len("/bridge/"):]
            if name not in ALLOWED_METHODS:
                return 404, _json_bytes({"ok": False, "error": "unknown_method", "method": name}), "application/json"
            try:
                payload = json.loads(body.decode("utf-8") or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                return 400, _json_bytes({"ok": False, "error": "bad_json", "detail": str(exc)}), "application/json"
            if not isinstance(payload, dict):
                return 400, _json_bytes({"ok": False, "error": "bad_payload"}), "application/json"
            return 200, _json_bytes(self.call(name, payload)), "application/json"

        return 404, _json_bytes({"ok": False, "error": "not_found", "path": route}), "application/json"

    def call(self, name: str, payload: Dict[str, Any]) -> Any:
        """白名单方法 -> WebBridge。写工具的门在 WebBridge 内部，本层不碰。"""
        if name == "invokeTool":
            tool = str(payload.get("name") or "")
            args = payload.get("args")
            return self.bridge.invoke_tool(tool, args if isinstance(args, dict) else {})
        if name == "getTle":
            return self.bridge.get_tle(str(payload.get("norad") or ""))
        if name == "getPasses":
            return self.bridge.get_passes(str(payload.get("norad") or ""))
        if name == "readSpectrum":
            return self.bridge.read_spectrum()
        if name == "getCapabilities":
            return self.bridge.get_capabilities()
        if name == "sourceName":
            return self.bridge.source_name()
        if name == "isLive":
            return self.bridge.is_live()
        if name == "confirm":
            return self.request_confirm(str(payload.get("tool") or ""),
                                        payload.get("args"))
        return {"ok": False, "error": "unknown_method", "method": name}

    def request_confirm(self, tool: str, args: Any) -> Dict[str, Any]:
        """登记一次用户确认（不签发 token）：一次性、绑定参数、有 TTL。"""
        if self.pending is None or not self.bridge.confirm_wired:
            return {"ok": False, "error": "manual_gate_unavailable",
                    "gated": True, "notFailure": True,
                    "detail": "this host has no confirmation channel"}
        if not confirm_enabled():
            return {"ok": False, "error": "confirm_disabled",
                    "gated": True, "notFailure": True,
                    "detail": f"{CONFIRM_ENV}=deny: read-only mode"}
        if self.registry is not None and tool not in self.registry.get_tool_names():
            return {"ok": False, "error": "unknown_tool", "tool": tool}
        clean = args if isinstance(args, dict) else {}
        ok, digest = self.pending.request(tool, clean)
        if not ok:
            return {"ok": False, "error": "confirm_rejected", "tool": tool}
        logger.info("confirm registered: %s (digest %s…)", tool, digest[:12])
        return {"ok": True, "pending": True, "tool": tool,
                "digest": digest[:16],
                "expiresInS": DEFAULT_CONFIRM_TTL_S,
                "note": "确认只对当前参数有效、只可使用一次"}

    # ── 服务 ───────────────────────────────────────────────────────────
    def serve_forever(self, open_browser: bool = False) -> None:
        host = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "MBDSDRBridgeHost/1.0"
            sys_version = ""

            def log_message(self, fmt: str, *args: Any) -> None:  # 不把 token 写进日志
                logger.debug("%s - %s", self.address_string(), fmt % args)

            def _send(self, code: int, payload: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(payload)

            def _body(self) -> bytes:
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = 0
                return self.rfile.read(n) if n > 0 else b""

            def do_GET(self) -> None:  # noqa: N802
                code, payload, ctype = host.dispatch("GET", self.path, b"", dict(self.headers))
                self._send(code, payload, ctype)

            def do_POST(self) -> None:  # noqa: N802
                code, payload, ctype = host.dispatch("POST", self.path, self._body(), dict(self.headers))
                self._send(code, payload, ctype)

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = int(self._server.server_address[1])
        logger.info("bridge host on %s (loopback only)", self.url)
        print(f"[web-bridge-host] {self.url}  (loopback only; Ctrl-C to stop)")
        if open_browser:
            webbrowser.open(self.url)
        try:
            self._server.serve_forever()
        finally:
            self._server.server_close()

    def start_background(self) -> "BridgeHost":
        """给测试/嵌入用：后台线程起服务，返回 self（端口已确定）。"""
        host = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:
                logger.debug("%s - %s", self.address_string(), fmt % args)

            def _send(self, code: int, payload: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:  # noqa: N802
                code, payload, ctype = host.dispatch("GET", self.path, b"", dict(self.headers))
                self._send(code, payload, ctype)

            def do_POST(self) -> None:  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n > 0 else b""
                code, payload, ctype = host.dispatch("POST", self.path, body, dict(self.headers))
                self._send(code, payload, ctype)

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def shutdown(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="MBDSDR Web bridge host (loopback only)")
    ap.add_argument("--port", type=int, default=8770, help="0 = 随机端口")
    ap.add_argument("--dist", type=Path, default=None, help="dist/index.html 路径")
    ap.add_argument("--open", action="store_true", help="启动后打开浏览器")
    ap.add_argument("--no-mcp", action="store_true",
                    help="不接真实 MCP 工具面，退回离线诚实空态")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    host = BridgeHost(dist=args.dist, port=args.port, mcp=not args.no_mcp)
    try:
        host.serve_forever(open_browser=args.open)
    except KeyboardInterrupt:
        print("\n[web-bridge-host] stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
