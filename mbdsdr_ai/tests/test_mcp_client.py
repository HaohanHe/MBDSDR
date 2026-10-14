# SPDX-License-Identifier: MIT
"""通用 MCP 客户端（mbdsdr_ai/mcp_client.py）钉扎测试。

覆盖（2026-10-14，Kilo Top-4 审计）：
  1. stdio 全链路：对**我们自己的 server**（spawn mbdsdr_ai.core.mcp_server）
     走 initialize / notifications/initialized / tools/list / tools/call；
  2. 单客户端纪律：已连接 server 重复 connect 先断后连（rtl_tcp 式接管，
     旧子进程被杀、信封带 replaced_existing）；失败重试不冒充接管；
  3. 配置诚实信封：文件缺失 / 坏 JSON / 坏结构 / 坏条目跳过 / 未注册
     server / 未连接调用——全部结构化返回，不抛不崩；
  4. 状态机：disconnected -> connecting（慢握手期间可观测）-> connected，
     失败 -> failed（可重试），运行时传输故障 connected -> failed；
     JSON-RPC error 不打 failed（服务端活着）；
     四状态枚举本身钉死：needs_auth 不伪造第五状态；
  5. streamable_http：本地 JSON-RPC echo server 验 application/json 路径、
     SSE（text/event-stream，多行 data + 注释跳过）路径、Accept 头约定、
     配置 headers 透传、401 needs_auth 信封、500/garbage 错误信封；
  6. MCP 工具四件套端到端（经 MCPToolSet.call_tool + arm_manual 门）。
"""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List

import pytest

from mbdsdr_ai.core.mcp_server import MCPToolSet
from mbdsdr_ai.mcp_client import (
    ConnState,
    MCPClientManager,
)

ROOT = Path(__file__).resolve().parents[2]


def _self_stdio_cfg(name: str = "self") -> Dict[str, Any]:
    """我们自己的 stdio MCP server 配置（PYTHONPATH 钉死仓库根，确定性）。"""
    return {"name": name, "type": "stdio",
            "command": [sys.executable, "-m", "mbdsdr_ai.core.mcp_server"],
            "env": {"PYTHONPATH": str(ROOT)}}


# ----------------------------------------------------------------------
# 本地哨兵 stdio server（-c 脚本，逐行 JSON-RPC）
# ----------------------------------------------------------------------
def _py_lines(lines: List[str]) -> List[str]:
    """把源码行列表转成 [python, -c, <源码>] 子进程命令。"""
    return [sys.executable, "-c", "\n".join(lines)]


# initialize 后立即自杀的 server（制造运行时传输故障）
_SUICIDE_SERVER = _py_lines([
    "import sys, json",
    "for line in sys.stdin:",
    "    req = json.loads(line)",
    "    if req.get('method') == 'initialize':",
    "        sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': req['id'],",
    "          'result': {'protocolVersion': '2024-11-05', 'capabilities': {},",
    "          'serverInfo': {'name': 'suicide', 'version': '0'}}}) + chr(10))",
    "        sys.stdout.flush()",
    "        break",
    "sys.exit(0)",
])

# 除了 initialize，一切请求都回 JSON-RPC error 的 server
_ERR_SERVER = _py_lines([
    "import sys, json",
    "for line in sys.stdin:",
    "    req = json.loads(line)",
    "    if req.get('id') is None:",
    "        continue",
    "    if req.get('method') == 'initialize':",
    "        res = {'protocolVersion': '2024-11-05', 'capabilities': {},",
    "               'serverInfo': {'name': 'err-server', 'version': '0'}}",
    "        out = {'jsonrpc': '2.0', 'id': req['id'], 'result': res}",
    "    else:",
    "        out = {'jsonrpc': '2.0', 'id': req['id'],",
    "               'error': {'code': -32601, 'message': 'server says no'}}",
    "    sys.stdout.write(json.dumps(out) + chr(10))",
    "    sys.stdout.flush()",
])

# initialize 慢握手 server：每请求先睡 4s 再回（验 CONNECTING 可观测）
_SLOW_SERVER = _py_lines([
    "import sys, json, time",
    "for line in sys.stdin:",
    "    req = json.loads(line)",
    "    time.sleep(4)",
    "    if req.get('id') is not None:",
    "        sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': req['id'],",
    "          'result': {'protocolVersion': '2024-11-05', 'capabilities': {},",
    "          'serverInfo': {'name': 'slow', 'version': '0'}}}) + chr(10))",
    "        sys.stdout.flush()",
])

# 握手必超时的 server：睡 3s 后才写「存活标记」——客户端若不在超时后
# 收手杀掉子进程，标记就会出现（钉死握手失败不留孤儿进程）。
_HANG_SERVER = _py_lines([
    "import sys, json, time, os",
    "for line in sys.stdin:",
    "    req = json.loads(line)",
    "    time.sleep(3)",
    "    marker = os.environ.get('MBDSDR_TEST_MARKER')",
    "    if marker:",
    "        open(marker, 'w').write('survived')",
    "    if req.get('id') is not None:",
    "        sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': req['id'],",
    "          'result': {'protocolVersion': '2024-11-05', 'capabilities': {},",
    "          'serverInfo': {'name': 'hang', 'version': '0'}}}) + chr(10))",
    "        sys.stdout.flush()",
])


# initialize 回包后顺手推一条服务端->客户端通知的 server（验入站缓冲）
_PUSH_SERVER = _py_lines([
    "import sys, json",
    "for line in sys.stdin:",
    "    req = json.loads(line)",
    "    if req.get('method') == 'initialize':",
    "        sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': req['id'],",
    "          'result': {'protocolVersion': '2024-11-05', 'capabilities': {},",
    "          'serverInfo': {'name': 'push', 'version': '0'}}}) + chr(10))",
    "        sys.stdout.write(json.dumps({'jsonrpc': '2.0',",
    "          'method': 'notifications/tools/list_changed'}) + chr(10))",
    "        sys.stdout.flush()",
    "    elif req.get('id') is not None:",
    "        sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': req['id'],",
    "          'result': {'tools': []}}) + chr(10))",
    "        sys.stdout.flush()",
])


@pytest.fixture()
def self_mgr() -> MCPClientManager:
    """注册了我们自己 server 的 manager；teardown 保证杀光客户端。"""
    mgr = MCPClientManager()
    mgr.register(_self_stdio_cfg())
    yield mgr
    mgr.disconnect_all()


# ======================================================================
# 1. stdio 全链路（initialize / tools/list / tools/call 对真 server）
# ======================================================================
def test_stdio_full_chain_against_own_server(self_mgr: MCPClientManager):
    env = self_mgr.connect("self")
    assert env["ok"] is True, env
    assert env["state"] == "connected"
    # initialize 握手：服务端自报信息原样带回
    assert env["server_info"]["protocolVersion"] == "2024-11-05"
    assert env["server_info"]["serverInfo"]["name"] == "mbdsdr-core"

    # tools/list：域卡片约定被客户端归一成扁平表（带 schema 与 domain）
    listed = self_mgr.tools("self")
    assert listed["ok"] is True and listed["count"] >= 60
    names = {t["name"] for t in listed["tools"]}
    assert "get_status" in names
    status_tool = next(t for t in listed["tools"] if t["name"] == "get_status")
    assert {"name", "description", "inputSchema", "domain"} <= set(status_tool)
    assert status_tool["domain"] == "系统设备"

    # tools/call get_status：无后端 -> 诚实 not-connected 数据，不是崩溃
    called = self_mgr.call("self", "get_status", {})
    assert called["ok"] is True
    assert called["server"] == "self" and called["tool"] == "get_status"
    assert called["result"]["connected"] is False

    # status 快照 + 主动断开
    snap = self_mgr.status()
    assert snap["ok"] is True and snap["servers"][0]["state"] == "connected"
    disc = self_mgr.disconnect("self")
    assert disc["ok"] is True and disc["state"] == "disconnected"
    # 断开是幂等的（第二次同样成功，不是错误）
    again = self_mgr.disconnect("self")
    assert again["ok"] is True and again["state"] == "disconnected"


# ======================================================================
# 2. 单客户端纪律（rtl_tcp 式：第二次 connect 前先断开）
# ======================================================================
def test_single_client_discipline_replaces_connected(self_mgr: MCPClientManager):
    first_env = self_mgr.connect("self")
    assert first_env["ok"] is True
    first_client = self_mgr._clients["self"]
    old_proc = first_client._transport._proc
    assert old_proc.poll() is None  # 前提：旧客户端确实活着

    second_env = self_mgr.connect("self")
    # 接管语义钉死：先断后连 + replaced_existing 可观测 + 新客户端可用
    assert second_env["ok"] is True
    assert second_env["replaced_existing"] is True
    second_client = self_mgr._clients["self"]
    assert second_client is not first_client
    assert first_client.state is ConnState.DISCONNECTED
    assert old_proc.poll() is not None  # 旧子进程已被杀（不泄漏）
    assert len(self_mgr._clients) == 1  # 字典里始终只有一个客户端
    assert self_mgr.call("self", "get_status")["ok"] is True


def test_reconnect_after_failure_is_not_marked_replaced(tmp_path):
    """失败后的重连是普通重试：不带 replaced_existing（honest 语义）。"""
    mgr = MCPClientManager()
    mgr.register({"name": "ghost", "type": "stdio",
                  "command": ["mbdsdr-no-such-binary-xyz", "--serve"]})
    bad = mgr.connect("ghost")
    assert bad["ok"] is False and bad["state"] == "failed"
    assert bad["error"] == "spawn_failed"
    assert mgr.status()["servers"][0]["state"] == "failed"
    # 换一个好 command 重连：成功、且不冒充接管
    mgr.register(_self_stdio_cfg("ghost"))
    good = mgr.connect("ghost")
    assert good["ok"] is True and good["state"] == "connected"
    assert "replaced_existing" not in good
    mgr.disconnect_all()


def test_concurrent_double_connect_leaves_single_client():
    """并发 4 连同一 server：串行接管，最终只有一个活客户端（无孤儿）。"""
    mgr = MCPClientManager()
    mgr.register(_self_stdio_cfg("self"))
    results: List[Dict[str, Any]] = []
    threads = [threading.Thread(target=lambda: results.append(
        mgr.connect("self"))) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert len(results) == 4 and all(r["ok"] for r in results)
    # 首个是真连接，其余是先断后连的接管
    assert sum(1 for r in results if r.get("replaced_existing")) == 3
    assert len([c for c in mgr._clients.values()
                if c.state is ConnState.CONNECTED]) == 1
    live = [c._transport._proc for c in mgr._clients.values()
            if c._transport is not None and c._transport._proc is not None]
    assert len(live) == 1 and live[0].poll() is None
    mgr.disconnect_all()


# ======================================================================
# 3. 配置/未知名/未连接的诚实信封
# ======================================================================
def test_config_missing_file_is_honest_envelope(tmp_path):
    mgr = MCPClientManager()
    env = mgr.register_from_config(str(tmp_path / "nope.json"))
    assert env["ok"] is False
    assert env["error"] == "config_not_found"
    assert "nope.json" in env["path"]
    assert "suggestion" in env


def test_config_bad_json_is_honest_envelope(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{ not json at all", encoding="utf-8")
    env = MCPClientManager().register_from_config(str(p))
    assert env["ok"] is False and env["error"] == "bad_json"
    assert "technical_detail" in env


def test_config_bad_shape_is_honest_envelope(tmp_path):
    p = tmp_path / "shape.json"
    p.write_text(json.dumps({"nope": 1}), encoding="utf-8")
    env = MCPClientManager().register_from_config(str(p))
    assert env["ok"] is False and env["error"] == "bad_config"


def test_config_bad_entries_skipped_honestly(tmp_path):
    p = tmp_path / "servers.json"
    p.write_text(json.dumps({"servers": [
        {"name": "good", "type": "stdio",
         "command": [sys.executable, "-c", "pass"]},
        {"type": "stdio", "command": [sys.executable]},           # 缺 name
        {"name": "pigeon", "type": "carrier-pigeon"},             # 未知 type
        {"name": "nocmd", "type": "stdio"},                       # 缺 command
        {"name": "ftp", "type": "streamable_http",
         "url": "ftp://invalid"},                                 # 坏 url
        {"name": "noheaders", "type": "streamable_http",
         "url": "http://127.0.0.1:1/mcp", "headers": "nope"},     # 坏 headers
    ]}, ensure_ascii=False), encoding="utf-8")
    mgr = MCPClientManager()
    env = mgr.register_from_config(str(p))
    assert env["ok"] is True
    assert env["registered"] == ["good"]
    assert len(env["skipped"]) == 5
    # 坏条目绝不入表（status 只见注册成功的）
    names = [s["name"] for s in mgr.status()["servers"]]
    assert names == ["good"]


def test_unknown_server_envelopes(self_mgr: MCPClientManager):
    for env in (self_mgr.connect("nope"),
                self_mgr.disconnect("nope"),
                self_mgr.tools("nope"),
                self_mgr.call("nope", "get_status")):
        assert env["ok"] is False
        assert env["error"] == "unknown_server"
        assert env["server"] == "nope"
        assert "suggestion" in env and "self" in env["suggestion"]


def test_registered_but_not_connected_envelopes(self_mgr: MCPClientManager):
    # 已注册未连接：tools/call -> not_connected（先连接，不假装通）
    tools_env = self_mgr.tools("self")
    assert tools_env["ok"] is False and tools_env["error"] == "not_connected"
    assert tools_env["state"] == "disconnected"
    call_env = self_mgr.call("self", "get_status")
    assert call_env["ok"] is False and call_env["error"] == "not_connected"
    assert "connect" in call_env["suggestion"]
    # status 里也是 disconnected（诚实空态）
    assert self_mgr.status()["servers"][0]["state"] == "disconnected"


def test_bad_call_arguments_envelopes(self_mgr: MCPClientManager):
    assert self_mgr.call("self", "")["error"] == "bad_arguments"
    assert self_mgr.call("self", "x", "not-a-dict")["error"] == "bad_arguments"


# ======================================================================
# 4. 连接状态机
# ======================================================================
def test_state_enum_has_no_fake_needs_auth():
    """四状态枚举钉死：needs_auth 是文档化 TODO，不伪造第五状态。"""
    assert [s.value for s in ConnState] == ["disconnected", "connecting",
                                            "connected", "failed"]


def test_state_machine_disconnected_to_connected_back(self_mgr):
    assert self_mgr.status()["servers"][0]["state"] == "disconnected"
    assert self_mgr.connect("self")["state"] == "connected"
    assert self_mgr.disconnect("self")["state"] == "disconnected"


def test_connecting_state_observable_during_slow_handshake():
    """慢握手期间 status 可见 connecting（握手完成才翻 connected）。"""
    mgr = MCPClientManager()
    mgr.register({"name": "slow", "type": "stdio",
                  "command": _SLOW_SERVER, "timeout_s": 30})
    done: Dict[str, Any] = {}

    def _run() -> None:
        done["env"] = mgr.connect("slow")

    th = threading.Thread(target=_run)
    th.start()
    time.sleep(0.6)  # 握手在飞（server 睡 4s 才回 initialize）
    states = {s["name"]: s["state"] for s in mgr.status()["servers"]}
    assert states["slow"] == "connecting"
    th.join(timeout=30)
    assert done["env"]["ok"] is True and done["env"]["state"] == "connected"
    assert mgr.disconnect("slow")["ok"] is True
    mgr.disconnect_all()


def test_spawn_failure_lands_in_failed_with_last_error():
    mgr = MCPClientManager()
    mgr.register({"name": "ghost", "type": "stdio",
                  "command": ["mbdsdr-no-such-binary-xyz", "--serve"]})
    env = mgr.connect("ghost")
    assert env["ok"] is False and env["state"] == "failed"
    assert env["error"] == "spawn_failed"
    assert env["suggestion"]
    entry = mgr.status()["servers"][0]
    assert entry["state"] == "failed"
    assert entry["last_error"]["error"] == "spawn_failed"


def test_failed_handshake_kills_spawned_child(tmp_path):
    """握手超时 -> FAILED 信封，且**已拉起的子进程被杀**（不泄漏孤儿）。"""
    marker = tmp_path / "survived.txt"
    mgr = MCPClientManager()
    mgr.register({"name": "hang", "type": "stdio",
                  "command": _HANG_SERVER, "timeout_s": 1,
                  "env": {"MBDSDR_TEST_MARKER": str(marker)}})
    env = mgr.connect("hang")
    assert env["ok"] is False and env["state"] == "failed"
    assert env["error"] == "timeout"
    # 等过子进程的 3s 睡眠窗：客户端若没收手，标记文件必然出现
    time.sleep(3.2)
    assert not marker.exists()
    assert mgr.status()["servers"][0]["state"] == "failed"
    mgr.disconnect_all()


def test_connected_downgraded_to_failed_on_transport_death():
    """连接中的 server 死掉：调用 -> failed + last_error（不假装还连着）。"""
    mgr = MCPClientManager()
    mgr.register({"name": "suicide", "type": "stdio",
                  "command": _SUICIDE_SERVER})
    assert mgr.connect("suicide")["ok"] is True
    env = mgr.tools("suicide")
    assert env["ok"] is False
    assert env["error"] in ("not_connected", "transport", "transport_closed")
    entry = mgr.status()["servers"][0]
    assert entry["state"] == "failed"
    assert entry["last_error"]["error"] == env["error"]
    mgr.disconnect_all()


def test_jsonrpc_error_keeps_connected_and_reports_error():
    """服务端回 JSON-RPC error：信封如实，但连接是活的 -> 不打 failed。"""
    mgr = MCPClientManager()
    mgr.register({"name": "err", "type": "stdio",
                  "command": _ERR_SERVER})
    assert mgr.connect("err")["ok"] is True
    env = mgr.tools("err")
    assert env["ok"] is False
    assert env["error"] == "jsonrpc_error"
    assert "server says no" in env["detail"]
    assert env["technical_detail"]["code"] == -32601
    # 服务端能回 error 说明传输活着：状态保持 connected
    assert mgr.status()["servers"][0]["state"] == "connected"
    mgr.disconnect_all()


def test_server_push_notifications_go_to_bounded_inbox():
    """服务端主动推送的通知进有界缓冲、不被当成本次响应（tools/list 照常）。"""
    mgr = MCPClientManager()
    mgr.register({"name": "push", "type": "stdio",
                  "command": _PUSH_SERVER})
    assert mgr.connect("push")["ok"] is True
    listed = mgr.tools("push")
    assert listed["ok"] is True and listed["count"] == 0
    entry = mgr.status()["servers"][0]
    assert entry["inbound_notifications"] >= 1
    mgr.disconnect_all()


# ======================================================================
# 5. streamable_HTTP（本地 JSON-RPC echo server）
# ======================================================================
class _EchoHandler(BaseHTTPRequestHandler):
    """最小 streamable-HTTP JSON-RPC echo；mode 由测试切换。"""

    mode = "json"
    seen_headers: List[Dict[str, str]] = []

    def do_POST(self):  # noqa: N802 - http.server 约定
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        _EchoHandler.seen_headers.append(dict(self.headers))
        try:
            req = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            req = {}
        mode = _EchoHandler.mode

        if mode == "unauthorized":
            body = b'{"error": "unauthorized"}'
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if mode == "boom":
            self.send_response(500)
            self.send_header("Content-Length", "4")
            self.end_headers()
            self.wfile.write(b"boom")
            return
        if mode == "garbage":
            body = b"<not json>"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        rid = req.get("id")
        if rid is None:
            # 通知（notifications/initialized）：202 空体
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        method = req.get("method")
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05",
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "http-echo", "version": "9.9"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "echo",
                                 "description": "回显参数",
                                 "inputSchema": {"type": "object",
                                                 "properties": {}}}]}
        elif method == "tools/call":
            params = req.get("params") or {}
            result = {"content": [{"type": "text", "text": json.dumps(
                {"echo": params.get("arguments") or {},
                 "tool": params.get("name", "")})}],
                "isError": False}
        else:
            body = json.dumps({"jsonrpc": "2.0", "id": rid,
                               "error": {"code": -32601,
                                         "message": "unknown method"}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # 响应体：两行 JSON（SSE 多行 data 场景下以换行拼接仍合法）
        text = ('{"jsonrpc": "2.0",\n "id": %s, "result": %s}'
                % (json.dumps(rid), json.dumps(result)))
        if mode == "sse":
            body = (": keep-alive\n"
                    "event: message\n"
                    + "".join("data: %s\n" % ln for ln in text.split("\n"))
                    + "\n").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
        else:
            body = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静默：不打脏 pytest 输出
        pass


@pytest.fixture()
def http_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _EchoHandler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    _EchoHandler.seen_headers = []
    _EchoHandler.mode = "json"
    yield srv
    _EchoHandler.mode = "json"
    srv.shutdown()
    srv.server_close()
    th.join(timeout=5)


def _http_cfg(srv: ThreadingHTTPServer, name: str = "http1",
              **extra: Any) -> Dict[str, Any]:
    cfg: Dict[str, Any] = {
        "name": name, "type": "streamable_http",
        "url": "http://127.0.0.1:%d/mcp" % srv.server_address[1]}
    cfg.update(extra)
    return cfg


def test_http_json_path_full_chain(http_server):
    _EchoHandler.mode = "json"
    mgr = MCPClientManager()
    mgr.register(_http_cfg(http_server,
                           headers={"Authorization": "Bearer test-token"}))
    env = mgr.connect("http1")
    assert env["ok"] is True, env
    assert env["server_info"]["serverInfo"]["name"] == "http-echo"

    listed = mgr.tools("http1")
    assert listed["ok"] is True and listed["count"] == 1
    assert listed["tools"][0]["name"] == "echo"

    called = mgr.call("http1", "echo", {"hello": "world"})
    assert called["ok"] is True
    assert called["result"]["echo"] == {"hello": "world"}
    assert called["result"]["tool"] == "echo"

    # Accept 头必须同时声明 json + SSE（MCP streamable HTTP 约定）
    accepts = [h.get("Accept", "") for h in _EchoHandler.seen_headers]
    assert any("application/json" in a and "text/event-stream" in a
               for a in accepts)
    # 配置 headers 透传（Authorization 原样带到外部 server）
    auths = [h.get("Authorization", "") for h in _EchoHandler.seen_headers]
    assert "Bearer test-token" in auths
    # 服务端未发 Mcp-Session-Id：客户端不伪造会话头
    assert not any("Mcp-Session-Id" in h for h in _EchoHandler.seen_headers)
    assert mgr.disconnect("http1")["ok"] is True


def test_http_sse_response_path(http_server):
    """text/event-stream 响应路径：多行 data 拼接 + 注释行跳过。"""
    _EchoHandler.mode = "sse"
    mgr = MCPClientManager()
    mgr.register(_http_cfg(http_server))
    env = mgr.connect("http1")
    assert env["ok"] is True, env
    assert env["server_info"]["serverInfo"]["name"] == "http-echo"
    called = mgr.call("http1", "echo", {"sse": True})
    assert called["ok"] is True
    assert called["result"]["echo"] == {"sse": True}
    listed = mgr.tools("http1")
    assert listed["ok"] is True and listed["count"] == 1
    mgr.disconnect_all()


def test_http_401_needs_auth_is_failed_not_fake_state(http_server):
    """401 -> needs_auth 错误信封 + FAILED 状态；不伪造第五个状态。"""
    _EchoHandler.mode = "unauthorized"
    mgr = MCPClientManager()
    mgr.register(_http_cfg(http_server))
    env = mgr.connect("http1")
    assert env["ok"] is False and env["state"] == "failed"
    assert env["error"] == "needs_auth"
    assert env["technical_detail"]["status"] == 401
    assert "认证流程未实现" in env["suggestion"]
    assert mgr.status()["servers"][0]["state"] == "failed"


def test_http_500_error_envelope(http_server):
    _EchoHandler.mode = "boom"
    mgr = MCPClientManager()
    mgr.register(_http_cfg(http_server))
    env = mgr.connect("http1")
    assert env["ok"] is False and env["state"] == "failed"
    assert env["error"] == "http_error"
    assert env["technical_detail"]["status"] == 500


def test_http_garbage_response_envelope(http_server):
    _EchoHandler.mode = "garbage"
    mgr = MCPClientManager()
    mgr.register(_http_cfg(http_server))
    env = mgr.connect("http1")
    assert env["ok"] is False and env["state"] == "failed"
    assert env["error"] == "bad_response"
    assert env["technical_detail"]["body"] == "<not json>"


def test_http_unreachable_server_envelope():
    """连不上的 HTTP server：transport 错误信封，不崩不挂。"""
    mgr = MCPClientManager()
    mgr.register({"name": "dead", "type": "streamable_http",
                  "url": "http://127.0.0.1:1/mcp", "timeout_s": 3})
    env = mgr.connect("dead")
    assert env["ok"] is False and env["state"] == "failed"
    assert env["error"] in ("transport", "timeout")
    assert env["suggestion"]


# ======================================================================
# 6. MCP 工具四件套端到端（经 MCPToolSet.call_tool + arm_manual 门）
# ======================================================================
def test_mcp_tools_suite_end_to_end():
    ts = MCPToolSet()
    try:
        assert ts.mcp_clients.register(_self_stdio_cfg("self"))["ok"] is True
        # 只读 list：不 gate，先见注册项（未连接 -> disconnected）
        listed = ts.call_tool("mcp_client_list", {})
        assert listed["ok"] is True
        entry = next(s for s in listed["servers"] if s["name"] == "self")
        assert entry["state"] == "disconnected"
        # 写工具默认 ask：无 arm_manual -> gated
        gated = ts.call_tool("mcp_client_connect", {"name": "self"})
        assert gated["ok"] is False and gated["gated"] is True
        # 带 arm_manual：真连、真调、真断
        conn = ts.call_tool("mcp_client_connect",
                            {"name": "self", "arm_manual": True})
        assert conn["ok"] is True and conn["state"] == "connected"
        called = ts.call_tool("mcp_client_call",
                              {"server": "self", "tool": "get_status",
                               "arguments": {}, "arm_manual": True})
        assert called["ok"] is True
        assert called["result"]["connected"] is False
        disc = ts.call_tool("mcp_client_disconnect",
                            {"name": "self", "arm_manual": True})
        assert disc["ok"] is True and disc["state"] == "disconnected"
    finally:
        ts.mcp_clients.disconnect_all()
