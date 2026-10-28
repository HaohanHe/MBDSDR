# SPDX-License-Identifier: MIT
"""
MBDSDR AI - 通用 MCP 客户端（transport 抽象 + 连接状态机）
==========================================================

让 MBDSDR 的 agent 侧**消费外部 MCP server**（第三方工具链、satdump 的
MCP 化、其他 agent 暴露的工具面……）。与 ``mbdsdr_mcp_client.py``
（ESP32 特例 WebSocket 桥）不同，本模块是**协议通用客户端**：任何讲
JSON-RPC 2.0 over stdio / streamable-HTTP 的 MCP server 都能接入，
零第三方依赖（纯标准库）。

配置驱动
--------
``~/.mbdsdr/mcp_servers.json``（路径可经 ``MBDSDR_MCP_SERVERS`` 环境
变量覆盖；显式传 ``path`` 优先，对齐 ``tool_policy`` / ``hook_commands``
的环境变量解析约定）：

.. code-block:: json

    {"servers": [
        {"name": "demo", "type": "stdio",
         "command": ["python", "-m", "mbdsdr_ai.core.mcp_server"],
         "env": {"PYTHONPATH": "/path/to/repo"}},
        {"name": "http1", "type": "streamable_http",
         "url": "http://127.0.0.1:50732/mcp",
         "headers": {"Authorization": "Bearer ..."}}
    ]}

``type`` 合法值：``stdio``（子进程、逐行 JSON-RPC）、
``streamable_http``（urllib POST JSON-RPC）。坏条目诚实跳过并给理由，
绝不让一份坏配置把整个 manager 拖垮。

连接状态机
----------
``DISCONNECTED``（disconnected）-> ``CONNECTING``（connecting） ->
``CONNECTED``（connected）；任一环节失败 -> ``FAILED``（failed），
失败后允许重连（failed -> connecting -> connected）。

* **needs_auth 是文档化 TODO，未实现**：HTTP 401 不引入第五个伪造状态，
  而是诚实落入 FAILED + 错误信封 ``error="needs_auth"``（OAuth/token
  刷新整个认证面不在本批范围）。
* 运行时传输故障（超时/子进程死亡/IO 失败）把 CONNECTED 打成 FAILED 并
  记录 ``last_error``——状态永远反映事实，不假装还连着。

单客户端纪律（rtl_tcp 式）
--------------------------
**每个已注册 server 同时最多一个活客户端**。对已 CONNECTED 的 server
再次 ``connect`` 时，先断开旧连接（杀子进程/关流）再建新连接，返回
信封带 ``replaced_existing: true``（可观测的接管，不是静默替换）。
对 FAILED/DISCONNECTED 的旧客户端则是普通重试，不带该标记。

错误信封（诚实文化）
-------------------
manager 全部 API 返回 dict，不向调用方抛异常（编程错误除外）：
未知名（``unknown_server``）、未连接（``not_connected``）、超时
（``timeout``）、JSON-RPC error（``jsonrpc_error``，detail 带
code/message）、HTTP 错误（``http_error``/``needs_auth``，detail 带
status/body 片段）、坏响应（``bad_response``）……每个信封都带
``suggestion`` 或 ``technical_detail``，绝不伪造成功。

安全边界
--------
* **外部 server 的工具结果一律按不可信数据对待**：本客户端只做协议
  转发与信封标注，不解释、不执行外部内容；上抛给 LLM 前由调用方
  （MCP 工具面 / agent）自行消毒。
* 连接动作会**起子进程 / 发网络请求**——在 MCP 工具面（见
  ``core/mcp_server.py`` 的「外部工具」域）中 connect/disconnect/call
  均为写工具，走既有 ``arm_manual`` 门。

线程安全（最小化）
----------------
一把 manager 级 ``RLock`` 只锁 server 注册表 / 客户端字典（短临界，
**绝不在持锁期间做 IO**）；另给每个 server 一把小串行锁，保证同一
server 同时只有一个 connect/disconnect 在飞（单客户端纪律，防孤儿
子进程），且不阻塞其他 server 的 status/tools/call。每个 transport
自带一把小锁分配请求 id。不过度设计：没有连接池、没有后台重连线程、
没有通知订阅表。

SSE（streamable HTTP 的 text/event-stream）实现边界
--------------------------------------------------
* **已做**：POST JSON-RPC；``application/json`` 单响应路径；
  ``text/event-stream`` 响应路径的 SSE 解析（``data:`` 多行拼接、
  ``event:`` 记录、空行派发、``:`` 注释/keep-alive 跳过）；
  ``Mcp-Session-Id`` 响应头捕获与后续请求回带；202 空体（通知）。
* **未做（文档化 TODO，不伪造）**：SSE 断线重连续传（Last-Event-ID /
  retry）、服务端->客户端的 GET 通知流、批量（batch）请求、OAuth 等
  认证流程（401 只是 FAILED + needs_auth 信封）。
"""

from __future__ import annotations

import json
import os
import queue
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from enum import Enum
from typing import Any, Deque, Dict, List, Optional, Tuple

#: 覆盖配置路径的环境变量（对齐 tool_policy.POLICY_ENV 约定）。
MCP_SERVERS_ENV = "MBDSDR_MCP_SERVERS"
#: 配置文件默认位置（``~`` 会 expanduser）。
DEFAULT_MCP_SERVERS_PATH = "~/.mbdsdr/mcp_servers.json"

#: MCP 协议版本（与 mbdsdr 两侧 server 的 initialize 返回值一致）。
PROTOCOL_VERSION = "2024-11-05"
#: 客户端自报信息（initialize 的 clientInfo）。
CLIENT_INFO: Dict[str, str] = {"name": "mbdsdr-mcp-client", "version": "0.1.0"}

#: 默认单次请求超时（秒）；可被 server 条目的 ``timeout_s`` 覆盖。
DEFAULT_TIMEOUT_S = 15.0
#: 支持的传输类型。
SUPPORTED_TYPES: Tuple[str, ...] = ("stdio", "streamable_http")
#: stdio/stderr 环形缓冲保留行数（诊断用，不进协议流）。
STDERR_TAIL_LINES = 50
#: 服务端->客户端入站通知（非匹配 id 的消息）保留条数上限。
INBOUND_QUEUE_MAX = 32

HTTP_ACCEPT = "application/json, text/event-stream"


# ======================================================================
# 连接状态机
# ======================================================================
class ConnState(str, Enum):
    """客户端连接状态。needs_auth 刻意**不在**这里——见模块 docstring。"""

    DISCONNECTED = "disconnected"   # 未连接 / 已主动断开
    CONNECTING = "connecting"       # initialize 握手进行中
    CONNECTED = "connected"         # 握手完成，可 tools/list + tools/call
    FAILED = "failed"               # 连接失败或运行时传输故障（可重试）


# ======================================================================
# 传输层错误（kind 词汇表即错误信封的 ``error`` 值）
# ======================================================================
class MCPTransportError(Exception):
    """传输/协议层失败。manager 负责把它翻译成结构化信封，不向上抛。"""

    def __init__(self, kind: str, message: str, detail: Any = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.detail = detail

    def to_dict(self) -> Dict[str, Any]:
        """错误信封：``{"error": kind, "detail": 人话, "technical_detail": ...}``。"""
        out: Dict[str, Any] = {"error": self.kind, "detail": self.message}
        if self.detail is not None:
            out["technical_detail"] = self.detail
        return out


# ======================================================================
# 配置校验（坏条目 -> (None, 理由)，由调用方诚实跳过）
# ======================================================================
def _validate_server_entry(entry: Any) -> Tuple[Optional[Dict[str, Any]],
                                                Optional[str]]:
    """校验并规范化一条 server 配置；返回 (规范化 dict, None) 或 (None, 理由)。"""
    if not isinstance(entry, dict):
        return None, "条目必须是 JSON 对象"
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        return None, "缺 name（非空字符串）"
    name = name.strip()
    stype = entry.get("type")
    if stype not in SUPPORTED_TYPES:
        return None, ("未知 type %r（合法值: %s）"
                      % (stype, "/".join(SUPPORTED_TYPES)))
    out: Dict[str, Any] = {"name": name, "type": stype}

    if stype == "stdio":
        command = entry.get("command")
        if (not isinstance(command, list) or not command
                or not all(isinstance(c, str) and c for c in command)):
            return None, "stdio server 需要 command：非空字符串数组"
        out["command"] = list(command)
        env = entry.get("env")
        if env is not None:
            if not isinstance(env, dict):
                return None, "env 必须是对象（字符串键值）"
            out["env"] = {str(k): str(v) for k, v in env.items()}
    else:  # streamable_http
        url = entry.get("url")
        if not isinstance(url, str) or not url.startswith(("http://",
                                                          "https://")):
            return None, "streamable_http server 需要 http(s) url"
        out["url"] = url
        headers = entry.get("headers")
        if headers is not None:
            if not isinstance(headers, dict):
                return None, "headers 必须是对象（字符串键值）"
            out["headers"] = {str(k): str(v) for k, v in headers.items()}

    timeout = entry.get("timeout_s", DEFAULT_TIMEOUT_S)
    try:
        timeout_f = float(timeout)
        if timeout_f <= 0:
            raise ValueError("timeout_s 必须为正")
    except (TypeError, ValueError):
        timeout_f = DEFAULT_TIMEOUT_S
    out["timeout_s"] = timeout_f
    return out, None


# ======================================================================
# transport 抽象：stdio / streamable_http 共用请求-响应接口
# ======================================================================
class MCPTransport:
    """传输层基类：连接握手 + JSON-RPC 请求/响应 + 通知 + 关闭。"""

    type_name = "?"

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.timeout = float(config.get("timeout_s", DEFAULT_TIMEOUT_S))
        self._id_lock = threading.Lock()
        self._next_id = 0
        # 服务端->客户端入站但 id 不匹配的消息（通知等），有界缓冲。
        self._inbound: Deque[Dict[str, Any]] = deque(maxlen=INBOUND_QUEUE_MAX)

    # -- 请求 id --------------------------------------------------------
    def _alloc_id(self) -> int:
        with self._id_lock:
            self._next_id += 1
            return self._next_id

    def inbound(self) -> List[Dict[str, Any]]:
        """取走（清空）入站通知缓冲（有界；满了丢最旧，不假装齐全）。"""
        with self._id_lock:
            items = list(self._inbound)
            self._inbound.clear()
        return items

    def inbound_count(self) -> int:
        """入站通知缓冲当前条数（status 可观测用，不清空）。"""
        with self._id_lock:
            return len(self._inbound)

    # -- 接口 -----------------------------------------------------------
    def connect(self) -> Dict[str, Any]:
        """建立传输并完成 initialize 握手；返回 initialize 的 result。"""
        raise NotImplementedError

    def request(self, method: str,
                params: Optional[Dict[str, Any]] = None) -> Any:
        """发一条带 id 的请求，返回 result；失败抛 MCPTransportError。"""
        raise NotImplementedError

    def notify(self, method: str,
               params: Optional[Dict[str, Any]] = None) -> bool:
        """发一条无 id 通知；返回是否成功送达（失败不抛、不假装送达）。"""
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


# ======================================================================
# stdio transport：子进程 + 逐行 JSON-RPC
# ======================================================================
class StdioTransport(MCPTransport):
    """JSON-RPC 2.0 over stdin/stdout（逐行 framing，与 mbdsdr server 同约定）。

    子进程 stdout 由读线程逐行解析入队，请求侧按 id 匹配 + 超时等待——
    跨平台（Windows 无名管道无 select），超时不挂死主线程。stderr 另起
    线程排入有界环形缓冲（诊断用，不回流协议流、不打印污染）。
    """

    type_name = "stdio"

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(config)
        self._proc: Optional[subprocess.Popen] = None
        self._lines: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()
        self._reader: Optional[threading.Thread] = None
        self._stderr_reader: Optional[threading.Thread] = None
        self._stderr_tail: Deque[str] = deque(maxlen=STDERR_TAIL_LINES)
        self._closed = False

    # -- 生命周期 -------------------------------------------------------
    def connect(self) -> Dict[str, Any]:
        command = self.config.get("command")
        env = dict(os.environ)
        for k, v in (self.config.get("env") or {}).items():
            env[str(k)] = str(v)
        try:
            self._proc = subprocess.Popen(
                command,  # type: ignore[arg-type]  # 配置校验已保证
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=env,
                text=True, encoding="utf-8", errors="replace", bufsize=1)
        except (OSError, ValueError) as e:
            raise MCPTransportError("spawn_failed",
                                    "启动子进程失败：%s" % e,
                                    detail={"command": command})
        self._reader = threading.Thread(target=self._read_loop,
                                        name="mcp-stdio-reader", daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(
            target=self._drain_stderr, name="mcp-stdio-stderr", daemon=True)
        self._stderr_reader.start()
        # initialize 握手（capabilities + clientInfo），随后通知 initialized
        result = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "clientInfo": CLIENT_INFO,
        })
        self.notify("notifications/initialized")
        return result if isinstance(result, dict) else {}

    def close(self) -> None:
        proc, self._proc = self._proc, None
        self._closed = True
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            proc.terminate()
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except Exception:  # pragma: no cover - 极端不退出进程
            try:
                proc.kill()
            except OSError:
                pass

    # -- 内部 -----------------------------------------------------------
    def _read_loop(self) -> None:
        """逐行读 stdout、解析入队；流结束/进程退出时投递 None 哨兵。"""
        proc = self._proc
        try:
            assert proc is not None and proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    self._stderr_tail.append("[client] 不可解析行：%s" % line)
                    continue
                if isinstance(msg, dict):
                    self._lines.put(msg)
        except (OSError, ValueError):
            pass
        finally:
            self._lines.put(None)  # 唤醒所有等待中的请求

    def _drain_stderr(self) -> None:
        proc = self._proc
        try:
            assert proc is not None and proc.stderr is not None
            for line in proc.stderr:
                self._stderr_tail.append(line.rstrip("\n"))
        except (OSError, ValueError):
            pass

    def _write(self, msg: Dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            raise MCPTransportError("not_connected",
                                    "stdio 子进程未在运行")
        try:
            proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise MCPTransportError("transport", "写入 stdin 失败：%s" % e)

    def notify(self, method: str,
               params: Optional[Dict[str, Any]] = None) -> bool:
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        try:
            self._write(msg)
            return True
        except MCPTransportError:
            return False

    def request(self, method: str,
                params: Optional[Dict[str, Any]] = None) -> Any:
        rid = self._alloc_id()
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self._write(msg)
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPTransportError(
                    "timeout", "%s 超时（>%ss）" % (method, self.timeout))
            try:
                item = self._lines.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                proc = self._proc
                if proc is not None and proc.poll() is not None:
                    raise MCPTransportError(
                        "transport_closed",
                        "stdio 子进程已退出（code=%s）" % proc.returncode)
                continue
            if item is None:
                raise MCPTransportError(
                    "transport_closed", "stdio 流已关闭（子进程退出）")
            if item.get("id") != rid:
                # 服务端->客户端通知 / 其他 id：有界留存，不当成本次响应
                self._inbound.append(item)
                continue
            return _result_or_error(item)


# ======================================================================
# streamable_http transport：urllib POST JSON-RPC
# ======================================================================
def _opener_for(url: str):
    """为一次 MCP HTTP 请求构造 opener：**回环地址一律不走代理**。

    真机实测（非推测）：本机设了 HTTPS_PROXY，urllib 默认 ProxyHandler
    会把 ``127.0.0.1`` 的本地 MCP 请求也发给代理，代理对 localhost 回
    502 —— 于是 `test_http_unreachable_server_envelope` 长期拿到
    ``http_error`` 而非真实的 ``transport``，把真 bug 藏成了"环境问题"。

    回环是本机进程，走代理既无意义又有害：它把"连不上"伪装成"服务端 502"。
    所以回环**永远**绕过代理；非回环仍照常尊重系统/环境代理设置。
    """
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    is_loopback = (host in ("localhost", "::1") or
                   host.startswith("127."))
    if is_loopback:
        # ProxyHandler({}) = 显式空代理表，覆盖环境里的代理设置
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


class StreamableHttpTransport(MCPTransport):
    """MCP streamable HTTP 客户端（2024-11-05 传输约定子集，纯标准库）。

    POST JSON-RPC，``Accept: application/json, text/event-stream``；
    响应按 Content-Type 分流：``application/json`` 走单响应路径，
    ``text/event-stream`` 走 SSE 解析路径（见模块 docstring 的实现边界）。
    initialize 响应的 ``Mcp-Session-Id`` 头被捕获并在后续请求回带。
    """

    type_name = "streamable_http"

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(config)
        self._session_id: Optional[str] = None

    # -- 生命周期 -------------------------------------------------------
    def connect(self) -> Dict[str, Any]:
        result = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "clientInfo": CLIENT_INFO,
        })
        self.notify("notifications/initialized")
        return result if isinstance(result, dict) else {}

    def close(self) -> None:
        self._session_id = None

    # -- 头 -------------------------------------------------------------
    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": HTTP_ACCEPT,
            "User-Agent": "%s/%s" % (CLIENT_INFO["name"], CLIENT_INFO["version"]),
        }
        for k, v in (self.config.get("headers") or {}).items():
            headers[str(k)] = str(v)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    # -- POST -----------------------------------------------------------
    def _post(self, msg: Dict[str, Any]) -> Any:
        """POST 一条 JSON-RPC 消息；返回 result / None（无体响应）。"""
        url = self.config["url"]
        body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=self._headers(),
                                     method="POST")
        try:
            resp = _opener_for(url).open(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read(512).decode("utf-8", "replace")
            except Exception:  # pragma: no cover - 读体失败的极端情况
                pass
            if e.code == 401:
                # needs_auth 是错误码 + FAILED 状态，不是第五个伪造状态
                raise MCPTransportError(
                    "needs_auth",
                    "HTTP 401：外部 server 要求认证（needs_auth 状态机为"
                    "文档化 TODO，本批未实现认证流程）",
                    detail={"status": 401, "body": detail})
            raise MCPTransportError(
                "http_error", "HTTP %d：%s" % (e.code, e.reason),
                detail={"status": e.code, "body": detail})
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise MCPTransportError(
                    "timeout", "POST %s 超时（>%ss）" % (url, self.timeout))
            raise MCPTransportError("transport", "连接 %s 失败：%s"
                                    % (url, reason))
        except (socket.timeout, TimeoutError):
            raise MCPTransportError(
                "timeout", "POST %s 超时（>%ss）" % (url, self.timeout))
        with resp:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self._session_id = sid
            ctype = resp.headers.get("Content-Type", "")
            if "text/event-stream" in ctype:
                return self._read_sse(resp, msg.get("id"))
            text = resp.read().decode("utf-8", "replace").strip()
            if not text:
                return None  # 202 Accepted 等无体响应（通知的常态）
            try:
                obj = json.loads(text)
            except json.JSONDecodeError as e:
                raise MCPTransportError(
                    "bad_response", "HTTP 响应不是合法 JSON：%s" % e,
                    detail={"body": text[:200]})
            if not isinstance(obj, dict):
                raise MCPTransportError("bad_response", "HTTP 响应不是 JSON 对象")
            return _result_or_error(obj)

    def notify(self, method: str,
               params: Optional[Dict[str, Any]] = None) -> bool:
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        try:
            self._post(msg)
            return True
        except MCPTransportError:
            return False  # 通知失败不假装送达（initialize 才是真握手）

    def request(self, method: str,
                params: Optional[Dict[str, Any]] = None) -> Any:
        rid = self._alloc_id()
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        return self._post(msg)

    # -- SSE 解析 -------------------------------------------------------
    def _read_sse(self, resp: Any, rid: Any) -> Any:
        """从 text/event-stream 响应里找出 id 匹配的 JSON-RPC 响应。

        处理 ``data:`` 多行拼接（SSE 语义：以 ``\\n`` 连接）、``event:``
        字段、空行派发、``:`` 开头注释/keep-alive 跳过；``id:``/``retry:``
        字段被忽略（断线重连续传未做，见模块 docstring）。
        """
        data_lines: List[str] = []
        try:
            while True:
                raw = resp.readline()
                if not raw:
                    break  # EOF：HTTP/1.0 关闭即流结束
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not line:
                    if not data_lines:
                        continue
                    obj = self._dispatch_sse(data_lines, rid)
                    if obj is not None:
                        return obj
                    data_lines = []
                    continue
                if line.startswith(":"):
                    continue  # 注释 / keep-alive
                field, _, value = line.partition(":")
                if value.startswith(" "):
                    value = value[1:]
                if field == "data":
                    data_lines.append(value)
                # event / id / retry：记录或忽略（断线重连续传未做）
        except (socket.timeout, TimeoutError):
            raise MCPTransportError("timeout", "SSE 读取超时")
        except (OSError, ValueError) as e:
            raise MCPTransportError("transport", "SSE 读取失败：%s" % e)
        raise MCPTransportError(
            "bad_response", "SSE 流中未找到 id=%r 的响应" % (rid,),
            detail={"session_id": self._session_id})

    def _dispatch_sse(self, data_lines: List[str], rid: Any) -> Any:
        """解析并派发一个 SSE 事件的 data 段；非目标 id 返回 None。"""
        payload = "\n".join(data_lines)
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError as e:
            raise MCPTransportError("bad_response",
                                     "SSE data 不是合法 JSON：%s" % e,
                                     detail={"data": payload[:200]})
        if not isinstance(obj, dict):
            raise MCPTransportError("bad_response", "SSE data 不是 JSON 对象")
        if obj.get("id") != rid:
            self._inbound.append(obj)
            return None
        return _result_or_error(obj)


def _result_or_error(msg: Dict[str, Any]) -> Any:
    """JSON-RPC 响应 -> result；带 error 对象则抛 MCPTransportError。"""
    if "error" in msg:
        err = msg.get("error") or {}
        if isinstance(err, dict):
            code = err.get("code")
            message = str(err.get("message", "未知 JSON-RPC 错误"))
            detail = {"code": code}
            data = err.get("data")
            if data is not None:
                detail["data"] = data
        else:
            message, detail = str(err), None
        raise MCPTransportError("jsonrpc_error", message, detail=detail)
    return msg.get("result")


# ======================================================================
# 单 server 客户端（持有 transport + 状态机）
# ======================================================================
class MCPClient:
    """一个外部 MCP server 的客户端句柄。

    全部操作不向调用方抛异常（manager 负责信封化）；内部传输故障把
    CONNECTED 降级为 FAILED 并记录 last_error（jsonrpc_error 除外——
    服务端能回 error 说明连接是活的）。
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        self.name = str(config.get("name", ""))
        self.type = str(config.get("type", ""))
        self.config = dict(config)
        self._state = ConnState.DISCONNECTED
        self._transport: Optional[MCPTransport] = None
        self._server_info: Optional[Dict[str, Any]] = None
        self._last_error: Optional[Dict[str, Any]] = None
        self._lock = threading.RLock()

    # -- 状态 -----------------------------------------------------------
    @property
    def state(self) -> ConnState:
        with self._lock:
            return self._state

    def status_entry(self) -> Dict[str, Any]:
        """status() 用的一条记录（state / server_info / last_error / 工具数）。"""
        with self._lock:
            entry: Dict[str, Any] = {
                "name": self.name,
                "type": self.type,
                "state": self._state.value,
                "connected": self._state is ConnState.CONNECTED,
            }
            if self._server_info is not None:
                entry["server_info"] = self._server_info
            if self._last_error is not None:
                entry["last_error"] = self._last_error
            transport = self._transport
        if transport is not None:
            # 服务端->客户端入站通知数（无订阅/回调，仅有界缓冲）
            entry["inbound_notifications"] = transport.inbound_count()
        return entry

    # -- 连接 -----------------------------------------------------------
    def connect(self) -> Dict[str, Any]:
        """initialize 握手 + notifications/initialized；失败 -> FAILED 信封。"""
        with self._lock:
            if self._state is ConnState.CONNECTED:
                # 单客户端纪律兜底：直接复用本对象重连也先断后连
                self._teardown_locked()
            self._state = ConnState.CONNECTING
            self._last_error = None
            self._server_info = None
        transport = self._make_transport()
        try:
            info = transport.connect()
        except MCPTransportError as e:
            # 握手失败也要杀掉已拉起的子进程/关流——否则失败一次泄漏一个
            # 进程（transport 尚未挂上 self._transport，必须就地 teardown）。
            try:
                transport.close()
            except Exception:  # pragma: no cover - 防御
                pass
            with self._lock:
                self._teardown_locked()
                self._state = ConnState.FAILED
                self._last_error = e.to_dict()
            env = {"ok": False, "server": self.name, "type": self.type,
                   "state": ConnState.FAILED.value}
            env.update(e.to_dict())
            env["suggestion"] = ("检查 command/url 与网络后重试 connect；"
                                 "needs_auth 见模块文档（认证流程未实现）。")
            return env
        except Exception as e:  # pragma: no cover - 编程错误之外的兜底
            try:
                transport.close()
            except Exception:
                pass
            with self._lock:
                self._teardown_locked()
                self._state = ConnState.FAILED
                self._last_error = {"error": "internal_error",
                                    "technical_detail": str(e)}
            return {"ok": False, "server": self.name,
                    "state": ConnState.FAILED.value,
                    "error": "internal_error", "detail": str(e)}
        with self._lock:
            self._transport = transport
            self._server_info = info
            self._state = ConnState.CONNECTED
        return {"ok": True, "server": self.name, "type": self.type,
                "state": ConnState.CONNECTED.value,
                "server_info": info}

    def disconnect(self) -> Dict[str, Any]:
        """主动断开（幂等）：杀子进程/关流，状态回 DISCONNECTED。"""
        with self._lock:
            self._teardown_locked()
            self._state = ConnState.DISCONNECTED
            self._server_info = None
        return {"ok": True, "server": self.name,
                "state": ConnState.DISCONNECTED.value}

    def _make_transport(self) -> MCPTransport:
        if self.type == "stdio":
            return StdioTransport(self.config)
        if self.type == "streamable_http":
            return StreamableHttpTransport(self.config)
        raise MCPTransportError("bad_config", "未知 type %r" % self.type)

    def _teardown_locked(self) -> None:
        transport, self._transport = self._transport, None
        if transport is not None:
            try:
                transport.close()
            except Exception:  # pragma: no cover - 防御：关闭不炸主流程
                pass

    # -- 工具 -----------------------------------------------------------
    def list_tools(self) -> List[Dict[str, Any]]:
        """tools/list，归一成扁平工具表（见 :func:`_normalize_tools`）。

        显式带 ``detail="schema"``：mbdsdr 自己的 server 在无 params 时
        只回域卡片 name 级（省 token 的目录约定），要拿 description/
        inputSchema 必须显式要；标准 MCP server 会忽略未知 params 键，
        同样返回扁平全量 schema。服务端若严格校验 params 而报
        jsonrpc_error，信封如实上抛，不静默降级。
        """
        result = self._request("tools/list", {"detail": "schema"})
        return _normalize_tools(result)

    def call_tool(self, tool: str,
                  arguments: Optional[Dict[str, Any]] = None) -> Any:
        """tools/call；返回 MCP 原始 result（content/isError 信封）。"""
        return self._request(
            "tools/call", {"name": tool, "arguments": arguments or {}})

    def _request(self, method: str,
                 params: Optional[Dict[str, Any]] = None) -> Any:
        """带状态机守卫的请求：传输层故障 -> FAILED + last_error。"""
        with self._lock:
            if self._state is not ConnState.CONNECTED or \
                    self._transport is None:
                raise MCPTransportError(
                    "not_connected",
                    "server %r 未连接（state=%s）"
                    % (self.name, self._state.value))
            transport = self._transport
        try:
            return transport.request(method, params)
        except MCPTransportError as e:
            if e.kind != "jsonrpc_error":
                # 服务端能回 JSON-RPC error 说明连接是活的，不打 FAILED
                with self._lock:
                    if self._state is ConnState.CONNECTED:
                        self._state = ConnState.FAILED
                        self._last_error = e.to_dict()
            raise


# ======================================================================
# 外部 tools/call 结果翻译（content 数组 -> 可编程消费的值）
# ======================================================================
def _normalize_tools(result: Any) -> List[Dict[str, Any]]:
    """把 tools/list 结果归一化成扁平工具表（兼容三种服务端形状）。

    * 标准 flat：``{"tools": [{name, description, inputSchema}, ...]}``；
    * mbdsdr 域卡片（name 级）：``{"tools": [{domain, summary,
      tools: [名字串...]}]}``；
    * mbdsdr detailed：``{"domains": [{domain, summary,
      tools: [完整 schema...]}]}``。

    字符串条目归一为 ``{"name": <str>}``；从域卡片展开时补记
    ``domain`` 字段（信息来自服务端卡片，不是客户端编造）。无法识别的
    形状原样返回空表——不把不可解析的响应当成工具清单。
    """
    if not isinstance(result, dict):
        return []
    cards = result.get("tools")
    if not isinstance(cards, list):
        cards = result.get("domains")
    if not isinstance(cards, list):
        return []
    out: List[Dict[str, Any]] = []
    for card in cards:
        if isinstance(card, dict) and isinstance(card.get("tools"), list):
            domain = card.get("domain")
            for t in card["tools"]:
                if isinstance(t, str):
                    entry: Dict[str, Any] = {"name": t}
                    if domain:
                        entry["domain"] = domain
                elif isinstance(t, dict):
                    entry = dict(t)
                    if domain and "domain" not in entry:
                        entry["domain"] = domain
                else:
                    continue
                out.append(entry)
        elif isinstance(card, dict) and isinstance(card.get("name"), str):
            out.append(dict(card))
        elif isinstance(card, str):
            out.append({"name": card})
    return out


def _extract_call_value(result: Any) -> Any:
    """MCP ``tools/call`` result -> 值：单 text 段且为 JSON 则解析，原样返回。"""
    if not isinstance(result, dict):
        return result
    content = result.get("content")
    if not isinstance(content, list):
        return result
    texts = [c.get("text") for c in content
             if isinstance(c, dict) and c.get("type") == "text"
             and isinstance(c.get("text"), str)]
    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except json.JSONDecodeError:
            return texts[0]
    return result


# ======================================================================
# manager：注册表 + 单客户端纪律 + 全部 API 信封化
# ======================================================================
class MCPClientManager:
    """外部 MCP server 的客户端管理器。

    API（全部返回 dict 信封，不抛异常）：
      * :meth:`register` / :meth:`register_from_config` —— 注册 server；
      * :meth:`connect` / :meth:`disconnect` —— 连接生命周期
        （单客户端纪律：已连接 server 再次 connect 先断后连）；
      * :meth:`status` —— 全部 server 的状态 + 工具数；
      * :meth:`tools` / :meth:`call` —— 工具发现与调用代理；
      * :meth:`disconnect_all` —— 批量断开（测试/关闭时用）。
    """

    def __init__(self, config_path: Optional[str] = None) -> None:
        self._config_path = config_path
        # 管理器字典锁：只保护注册表/客户端字典/每 server 串行锁表（短临界，
        # 绝不在持锁期间做 IO）。IO 期间（connect 握手）持的是 per-name
        # 串行锁——既保证「同一 server 同时只有一个 connect 在飞」（单客户
        # 端纪律，防孤儿子进程），又不阻塞其他 server 的 status/tools/call。
        self._lock = threading.RLock()
        self._servers: Dict[str, Dict[str, Any]] = {}
        self._clients: Dict[str, MCPClient] = {}
        self._name_locks: Dict[str, threading.Lock] = {}

    # -- 线程安全小工具 -------------------------------------------------
    def _name_lock(self, name: str) -> threading.Lock:
        """取（懒建）某 server 的 connect/disconnect 串行锁。"""
        with self._lock:
            lk = self._name_locks.get(name)
            if lk is None:
                lk = threading.Lock()
                self._name_locks[name] = lk
            return lk

    # -- 配置 -----------------------------------------------------------
    def _resolve_config_path(self, path: Optional[str]) -> str:
        p = (path or self._config_path or os.environ.get(MCP_SERVERS_ENV)
             or DEFAULT_MCP_SERVERS_PATH)
        return os.path.expanduser(p)

    def register(self, entry: Any) -> Dict[str, Any]:
        """注册一条 server 配置（坏条目 -> 诚实信封，不入表）。"""
        cfg, reason = _validate_server_entry(entry)
        if cfg is None:
            return {"ok": False, "error": "bad_config",
                    "technical_detail": reason,
                    "suggestion": "条目需 name + type(stdio|streamable_http)"
                                  " + command/url，见模块 docstring。"}
        name = cfg["name"]
        with self._lock:
            replaced = name in self._servers
            self._servers[name] = cfg
        return {"ok": True, "server": name, "type": cfg["type"],
                "replaced": replaced}

    def register_from_config(self, path: Optional[str] = None) -> Dict[str, Any]:
        """从 JSON 配置文件批量注册；缺失/坏 JSON -> 诚实信封（不崩）。"""
        p = self._resolve_config_path(path)
        if not os.path.isfile(p):
            return {"ok": False, "error": "config_not_found", "path": p,
                    "suggestion": "创建 %s（或用 MBDSDR_MCP_SERVERS 指定"
                                  " 路径）；无配置 = 无外部 server，空态。"
                                  % p}
        try:
            # utf-8-sig：透明剥离子常见 Windows 编辑器写出的 UTF-8 BOM
            with open(p, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            return {"ok": False, "error": "bad_json", "path": p,
                    "technical_detail": str(e),
                    "suggestion": "修复 JSON 后重试；坏配置不注册任何 server。"}
        if not isinstance(data, dict) or \
                not isinstance(data.get("servers"), list):
            return {"ok": False, "error": "bad_config", "path": p,
                    "suggestion": "顶层需 {\"servers\": [ ... ]}。"}
        registered: List[str] = []
        skipped: List[Dict[str, Any]] = []
        for i, entry in enumerate(data["servers"]):
            env = self.register(entry)
            if env.get("ok"):
                registered.append(env["server"])
            else:
                skipped.append({"index": i,
                                "reason": env.get("technical_detail", "")})
        return {"ok": True, "path": p, "registered": registered,
                "skipped": skipped}

    # -- 连接 -----------------------------------------------------------
    def connect(self, name: str) -> Dict[str, Any]:
        """连接一个已注册 server。单客户端纪律：先断后连（接管）。"""
        with self._name_lock(name):
            with self._lock:
                cfg = self._servers.get(name)
                if cfg is None:
                    return self._unknown(name)
                old = self._clients.get(name)
                replaced = False
                if old is not None:
                    # rtl_tcp 式纪律：第二次 connect 前先断开旧客户端，
                    # 防止重复 connect 留下孤儿子进程或双连接。
                    replaced = old.state is ConnState.CONNECTED
                    old.disconnect()
                client = MCPClient(cfg)
                self._clients[name] = client  # 先登记：status 可见 CONNECTING
            env = client.connect()  # 锁外握手：不阻塞其他 server 的操作
        if replaced and env.get("ok"):
            env["replaced_existing"] = True
        return env

    def disconnect(self, name: str) -> Dict[str, Any]:
        """断开一个 server（未注册 -> unknown_server；未连接 -> 幂等成功）。"""
        with self._name_lock(name):
            with self._lock:
                if name not in self._servers:
                    return self._unknown(name)
                client = self._clients.get(name)
            if client is None:
                return {"ok": True, "server": name,
                        "state": ConnState.DISCONNECTED.value,
                        "note": "本来就未连接（无客户端实例）。"}
            return client.disconnect()

    def disconnect_all(self) -> Dict[str, Any]:
        """断开全部客户端（关闭/测试卫生用）。"""
        with self._lock:
            names = list(self._clients)
        for n in names:
            self.disconnect(n)
        return {"ok": True, "disconnected": names}

    # -- 查询 -----------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        """全部 server 的状态快照（未连接过的注册项也列，诚实空态）。"""
        with self._lock:
            names = sorted(self._servers)
            clients = dict(self._clients)
            servers = dict(self._servers)
        out: List[Dict[str, Any]] = []
        for n in names:
            client = clients.get(n)
            if client is None:
                out.append({"name": n, "type": servers[n]["type"],
                            "state": ConnState.DISCONNECTED.value,
                            "connected": False})
            else:
                out.append(client.status_entry())
        return {"ok": True, "servers": out, "count": len(out)}

    def _client_for_call(self, name: str) -> Tuple[Optional[MCPClient],
                                                    Optional[Dict[str, Any]]]:
        """取一个已连接的客户端；失败返回 (None, 信封)。"""
        with self._lock:
            if name not in self._servers:
                return None, self._unknown(name)
            client = self._clients.get(name)
            if client is None:
                return None, {"ok": False, "error": "not_connected",
                              "server": name,
                              "state": ConnState.DISCONNECTED.value,
                              "suggestion": "先 connect(%r)。" % name}
            if client.state is not ConnState.CONNECTED:
                return None, {"ok": False, "error": "not_connected",
                              "server": name,
                              "state": client.state.value,
                              "suggestion": "先 connect(%r)（当前 %s）。"
                                            % (name, client.state.value)}
            return client, None

    def _unknown(self, name: str) -> Dict[str, Any]:
        with self._lock:
            known = sorted(self._servers)
        return {"ok": False, "error": "unknown_server", "server": name,
                "suggestion": ("未注册的 server：%r；已注册：%s。用 "
                               "register_from_config()/register() 先注册。"
                               % (name, known or "（空）"))}

    # -- 工具代理 -------------------------------------------------------
    def tools(self, name: str) -> Dict[str, Any]:
        """tools/list 代理；未知名/未连接/传输错误 -> 诚实信封。"""
        client, err = self._client_for_call(name)
        if err is not None:
            return err
        try:
            tools = client.list_tools()
        except MCPTransportError as e:
            env = {"ok": False, "server": name}
            env.update(e.to_dict())
            return env
        return {"ok": True, "server": name, "tools": tools,
                "count": len(tools)}

    def call(self, name: str, tool: str,
             arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """tools/call 代理。外部结果一律按不可信数据上抛（见模块安全边界）。"""
        if not isinstance(tool, str) or not tool.strip():
            return {"ok": False, "error": "bad_arguments",
                    "suggestion": "tool 必须是非空工具名。"}
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return {"ok": False, "error": "bad_arguments",
                    "suggestion": "arguments 必须是 JSON 对象。"}
        client, err = self._client_for_call(name)
        if err is not None:
            return err
        try:
            result = client.call_tool(tool, arguments)
        except MCPTransportError as e:
            env = {"ok": False, "server": name, "tool": tool}
            env.update(e.to_dict())
            return env
        envelope: Dict[str, Any] = {
            "ok": not bool(isinstance(result, dict) and result.get("isError")),
            "server": name, "tool": tool,
            "result": _extract_call_value(result),
        }
        if not envelope["ok"]:
            envelope["error"] = "external_tool_error"
            envelope["suggestion"] = ("外部 server 返回 isError=true；其结果"
                                      "按不可信数据处理，勿直接执行。")
        return envelope


__all__ = [
    "MCP_SERVERS_ENV",
    "DEFAULT_MCP_SERVERS_PATH",
    "PROTOCOL_VERSION",
    "CLIENT_INFO",
    "DEFAULT_TIMEOUT_S",
    "SUPPORTED_TYPES",
    "ConnState",
    "MCPTransportError",
    "MCPTransport",
    "StdioTransport",
    "StreamableHttpTransport",
    "MCPClient",
    "MCPClientManager",
]


if __name__ == "__main__":  # pragma: no cover - 手工冒烟：python -m mbdsdr_ai.mcp_client
    _mgr = MCPClientManager()
    _reg = _mgr.register_from_config()
    print(json.dumps(_reg, ensure_ascii=False, indent=2))
    if _reg.get("ok"):
        for _s in _reg.get("registered", []):
            print(json.dumps(_mgr.connect(_s), ensure_ascii=False,
                             default=str))
        print(json.dumps(_mgr.status(), ensure_ascii=False, default=str))
        _mgr.disconnect_all()
