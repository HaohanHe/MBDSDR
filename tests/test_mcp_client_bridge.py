# SPDX-License-Identifier: MIT
"""mbdsdr_mcp_client.py 行为测试（离线协议层，无硬件、无真实网络）。

覆盖三种用法背后的纯逻辑：
1. 依赖缺失诚实性：作为库导入必须抛 ImportError（供 desktop/mcp_worker
   的 except ImportError 降级路径捕获），绝不能 sys.exit 杀掉宿主进程；
   CLI 直跑（__main__）才允许 exit(1)。
2. JSON-RPC 帧构造（id 递增、params 空缺省）与响应匹配（异帧/通知跳过）。
3. 断连/超时/坏帧边界：发送失败、接收中断、超时各自诚实报错；坏帧
   （固件 WAV 二进制推送、畸形 JSON、非对象 JSON）跳过且不断连——
   对应固件 audio_stream_task 在同一 WebSocket 上广播二进制帧的推送语义。
4. list_tools「错误:即失败」：错误信封抛 RuntimeError，绝不吞成空列表。
5. stdio MCP server：initialize 握手形状、未知方法 -32601、通知不回复、
   空行/畸形 JSON 不杀会话（-32700 / -32600 后继续服务）、EOF 收尾。

传输层用 stub 注入缝（websocket.create_connection → 脚本化 FakeWS；
StdioMCPServer.client → StubClient）。stub 只回放合成 fixture 帧用于钉
协议编排语义，断言不伪造"硬件成功"——没有任何用例声称硬件有输出。
"""
import importlib
import importlib.util
import io
import json
import os
import runpy
import sys
import types

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

mbdsdr_mcp_client = pytest.importorskip(
    "mbdsdr_mcp_client", reason="mbdsdr_mcp_client 模块不可用（缺依赖时改测导入诚实性）")
MCPClient = mbdsdr_mcp_client.MBDSDRClient
StdioMCPServer = mbdsdr_mcp_client.StdioMCPServer


# ============================================================================
# 传输层 stub（注入缝：websocket.create_connection / server.client）
# ============================================================================

class FakeWS:
    """脚本化 WebSocket：记录 send 帧、按队列回放 recv 帧。

    帧内容全部是合成 fixture，只用于钉 JSON-RPC 编排语义（构造/解析/
    匹配/跳过），不代表任何硬件输出。
    """

    def __init__(self, frames=(), recv_error=None, send_error=None):
        self.sent = []
        self.timeouts = []
        self.closed = False
        self._frames = list(frames)
        self._recv_error = recv_error
        self._send_error = send_error

    def send(self, data):
        if self._send_error is not None:
            raise self._send_error
        self.sent.append(data)

    def recv(self):
        if self._recv_error is not None:
            raise self._recv_error
        if not self._frames:
            raise mbdsdr_mcp_client.websocket.WebSocketTimeoutException(
                "fake: 队列已空")
        return self._frames.pop(0)

    def settimeout(self, t):
        self.timeouts.append(t)

    def close(self):
        self.closed = True


def _resp(req_id, result=None, error=None):
    frame = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        frame["error"] = error
    else:
        frame["result"] = result
    return json.dumps(frame)


class StubClient:
    """stdio server 用的客户端 stub：只回放脚本化信封。"""

    def __init__(self, connect_ok=True, responses=None, list_result=None):
        self.connect_ok = connect_ok
        self.responses = responses or {}
        self.list_result = list_result or []
        self.calls = []
        self.closed = False
        self.host = "stub-host"
        self.port = 81

    def connect(self):
        return self.connect_ok

    def close(self):
        self.closed = True

    def call(self, method, params=None):
        self.calls.append((method, params))
        return self.responses.get(
            method, _resp(1, error={"code": -32601,
                                    "message": f"stub 未编排: {method}"}))

    def list_tools(self):
        return self.list_result


def _client_with(fake, timeout=1):
    """构造已连接客户端，传输层替换为 fake（stub 注入缝）。"""
    c = MCPClient(timeout=timeout)
    c.ws = fake
    c._connected = True
    return c


# ============================================================================
# 1. 依赖缺失诚实性
# ============================================================================

class TestMissingDependencyHonesty:
    def test_library_import_raises_import_error_not_sysexit(self, monkeypatch):
        """库导入缺 websocket-client → ImportError（宿主可 except），
        绝不是 SystemExit 杀进程。"""
        monkeypatch.setitem(sys.modules, "websocket", None)
        path = os.path.join(_ROOT, "mbdsdr_mcp_client.py")
        spec = importlib.util.spec_from_file_location(
            "mbdsdr_mcp_client_dep_probe", path)
        mod = importlib.util.module_from_spec(spec)
        with pytest.raises(ImportError) as excinfo:
            spec.loader.exec_module(mod)
        assert not isinstance(excinfo.value, SystemExit)
        assert "websocket-client" in str(excinfo.value)

    def test_cli_main_missing_dependency_exits_1(self, monkeypatch):
        """CLI 直跑缺依赖 → SystemExit(1)（命令行语义保留）。"""
        monkeypatch.setitem(sys.modules, "websocket", None)
        path = os.path.join(_ROOT, "mbdsdr_mcp_client.py")
        with pytest.raises(SystemExit) as excinfo:
            runpy.run_path(path, run_name="__main__")
        assert excinfo.value.code == 1


# ============================================================================
# 2. JSON-RPC 帧构造 / 响应匹配
# ============================================================================

class TestJsonRpcFraming:
    def test_call_builds_jsonrpc_frame_and_increments_id(self):
        fake = FakeWS(frames=[
            _resp(1, result={"ok": 1}),
            _resp(2, result={"ok": 2}),
        ])
        c = _client_with(fake)
        c.call("tune_fm", {"freq_mhz": 98.5})
        c.call("get_status")

        f1 = json.loads(fake.sent[0])
        assert f1 == {"jsonrpc": "2.0", "id": 1, "method": "tune_fm",
                      "params": {"freq_mhz": 98.5}}
        f2 = json.loads(fake.sent[1])
        assert f2["id"] == 2 and f2["method"] == "get_status"
        assert f2["params"] == {}  # 无参调用 params 缺省为空对象

    def test_response_id_matching_skips_foreign_frames(self):
        # 异 id 响应、无 id 通知都跳过，只返回匹配 id 的响应
        fake = FakeWS(frames=[
            _resp(999, result={"x": 1}),
            json.dumps({"jsonrpc": "2.0", "method": "evt", "params": {}}),
            _resp(1, result={"status": "up"}),
        ])
        c = _client_with(fake)
        resp = c.call("get_status")
        assert resp["result"] == {"status": "up"}
        assert resp["id"] == 1

    def test_wrapper_error_envelope_passes_through(self):
        # tune_fm 类封装：result 缺失时回传 error 信封（不伪装成功）
        err = {"code": -32000, "message": "设备忙"}
        fake = FakeWS(frames=[_resp(1, error=err)])
        c = _client_with(fake)
        assert c.tune_fm(98.5) == err


# ============================================================================
# 3. 断连 / 超时 / 坏帧边界
# ============================================================================

class TestTransportBoundaries:
    def test_call_before_connect_raises_connection_error(self):
        c = MCPClient()
        with pytest.raises(ConnectionError, match="未连接"):
            c.call("get_status")

    def test_connect_failure_returns_false_not_fake_success(self, monkeypatch,
                                                            capsys):
        def boom(url, timeout=None):
            raise ConnectionRefusedError("stub: 拒绝连接")
        monkeypatch.setattr(mbdsdr_mcp_client.websocket,
                            "create_connection", boom)
        c = MCPClient(host="203.0.113.7")  # TEST-NET-3 文档地址，不可路由
        assert c.connect() is False
        assert c._connected is False
        err = capsys.readouterr().err
        assert "连接失败" in err
        # 失败后调用必须报错，绝不给出结果
        with pytest.raises(ConnectionError):
            c.call("get_status")

    def test_send_failure_marks_disconnected(self):
        fake = FakeWS(send_error=BrokenPipeError("stub: 管道断裂"))
        c = _client_with(fake)
        with pytest.raises(ConnectionError, match="发送失败"):
            c.call("get_status")
        assert c._connected is False

    def test_recv_failure_marks_disconnected(self):
        fake = FakeWS(recv_error=ConnectionResetError("stub: 连接被重置"))
        c = _client_with(fake)
        with pytest.raises(ConnectionError, match="接收失败"):
            c.call("get_status")
        assert c._connected is False

    def test_no_matching_response_raises_timeout(self):
        # 只有异帧 / 空队列 → 诚实地超时，不返回假结果
        fake = FakeWS(frames=[_resp(42, result={"x": 1})])
        c = _client_with(fake, timeout=0.3)
        with pytest.raises(TimeoutError, match="get_status"):
            c.call("get_status")
        assert c._connected is True  # 超时本身不是断连

    def test_bad_frames_skipped_without_dropping_connection(self):
        # 固件在同一 WebSocket 上广播 WAV 二进制帧 / 推送非对象 JSON：
        # 这些帧必须跳过（不进 json.loads 的致命路径），连接不许被误杀。
        fake = FakeWS(frames=[
            b"RIFF$\x00\x00\x00WAVEfmt ",   # WAV 头二进制推送
            "not-json{{",                    # 畸形 JSON 文本帧
            "[1, 2, 3]",                     # 非对象 JSON
            json.dumps({"jsonrpc": "2.0", "method": "evt"}),  # 通知帧
            _resp(1, result={"samples": 0}),
        ])
        c = _client_with(fake)
        resp = c.call("stop_record")
        assert resp["result"] == {"samples": 0}
        assert c._connected is True


# ============================================================================
# 4. list_tools「错误:即失败」
# ============================================================================

class TestListToolsHonesty:
    def test_error_envelope_raises_not_silent_empty(self):
        err = {"code": -32000, "message": "设备忙"}
        fake = FakeWS(frames=[_resp(1, error=err)])
        c = _client_with(fake)
        with pytest.raises(RuntimeError, match="list_tools 失败"):
            c.list_tools()

    def test_success_returns_tool_list(self):
        tools = [{"name": "tune_fm", "desc": "调谐FM", "params": {}}]
        fake = FakeWS(frames=[_resp(1, result=tools)])
        c = _client_with(fake)
        assert c.list_tools() == tools

    def test_non_list_result_coerced_to_empty(self):
        fake = FakeWS(frames=[_resp(1, result={"not": "a list"})])
        c = _client_with(fake)
        assert c.list_tools() == []


# ============================================================================
# 5. stdio MCP server
# ============================================================================

class TestStdioServer:
    def test_initialize_handshake_shape(self):
        srv = StdioMCPServer(host="203.0.113.7")
        resp = srv._handle_initialize({"id": 7, "method": "initialize"})
        assert resp["id"] == 7
        assert resp["result"]["protocolVersion"] == "2024-11-05"
        assert resp["result"]["serverInfo"] == {"name": "mbdsdr-mini",
                                                "version": "0.6.0"}
        assert resp["result"]["capabilities"] == {"tools": {"listChanged": False}}

    def test_run_loop_survives_blank_malformed_and_notifications(
            self, monkeypatch):
        # 空行、-32700 畸形帧、-32600 非对象帧、通知一律不许杀会话；
        # 通知（无 id）不得回复。
        lines = [
            '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}',
            "",                                   # 空行：跳过
            "{bad json",                          # 畸形 JSON → -32700
            "[1, 2, 3]",                          # 非对象 JSON → -32600
            '{"jsonrpc":"2.0","method":"notifications/initialized"}',
            '{"jsonrpc":"2.0","method":"notifications/cancelled","params":{}}',
            '{"jsonrpc":"2.0","id":2,"method":"ping"}',
            '{"jsonrpc":"2.0","id":3,"method":"no_such_method"}',
        ]
        srv = StdioMCPServer(host="203.0.113.7")
        stub = StubClient()
        srv.client = stub
        monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(lines) + "\n"))
        out = io.StringIO()
        monkeypatch.setattr(sys, "stdout", out)

        srv.run()  # EOF 后正常返回

        replies = [json.loads(l) for l in out.getvalue().splitlines() if l]
        # 通知两条不回复；其余 5 条各有响应，顺序保持
        assert [r.get("id") for r in replies] == [1, None, None, 2, 3]
        assert replies[0]["result"]["serverInfo"]["name"] == "mbdsdr-mini"
        assert replies[1]["error"]["code"] == -32700
        assert replies[2]["error"]["code"] == -32600
        assert replies[3] == {"jsonrpc": "2.0", "id": 2, "result": {}}
        assert replies[4]["error"]["code"] == -32601
        assert stub.closed is True  # EOF 收尾必须关客户端

    def test_tools_list_schema_mapping_and_error_path(self):
        srv = StdioMCPServer(host="203.0.113.7")
        srv.client = StubClient(connect_ok=True, list_result=[
            {"name": "tune_fm", "desc": "调谐FM",
             "params": {"freq_mhz": "float 64-108"}},
            {"name": "set_mode", "desc": "模式", "params": {"mode": "解调模式"}},
        ])
        resp = srv._handle_tools_list({"id": 5, "method": "tools/list"})
        tools = resp["result"]["tools"]
        assert [t["name"] for t in tools] == ["tune_fm", "set_mode"]
        fm_schema = tools[0]["inputSchema"]
        assert fm_schema["properties"]["freq_mhz"]["type"] == "number"  # 数值语义词
        assert fm_schema["required"] == ["freq_mhz"]
        assert tools[1]["inputSchema"]["properties"]["mode"]["type"] == "string"

        # 连接失败 → -32603 错误信封，绝不空列表装成功
        srv2 = StdioMCPServer(host="203.0.113.7")
        srv2.client = StubClient(connect_ok=False)
        resp2 = srv2._handle_tools_list({"id": 6, "method": "tools/list"})
        assert resp2["error"]["code"] == -32603
        assert "无法连接" in resp2["error"]["message"]

    def test_tools_call_envelope_mapping(self):
        srv = StdioMCPServer(host="203.0.113.7")
        srv.client = StubClient(connect_ok=True, responses={
            "get_status": _resp(1, result={"rssi": -60}),
            "tune_fm": _resp(1, error={"code": -32000, "message": "锁相环失锁"}),
        })
        req = {"id": 8, "method": "tools/call",
               "params": {"name": "get_status", "arguments": {}}}
        resp = srv._handle_tools_call(req)
        assert resp["result"]["isError"] is False
        assert json.loads(resp["result"]["content"][0]["text"]) == {"rssi": -60}
        assert ("get_status", {}) in srv.client.calls

        req2 = {"id": 9, "method": "tools/call",
                "params": {"name": "tune_fm", "arguments": {"freq_mhz": 98.5}}}
        resp2 = srv._handle_tools_call(req2)
        assert resp2["result"]["isError"] is True
        assert "工具调用错误" in resp2["result"]["content"][0]["text"]
        assert "锁相环失锁" in resp2["result"]["content"][0]["text"]

        # 未连接 → isError 内容信封，不是假装成功
        srv3 = StdioMCPServer(host="203.0.113.7")
        srv3.client = StubClient(connect_ok=False)
        resp3 = srv3._handle_tools_call(
            {"id": 10, "method": "tools/call",
             "params": {"name": "get_status", "arguments": {}}})
        assert resp3["result"]["isError"] is True
        assert "无法连接" in resp3["result"]["content"][0]["text"]


# ============================================================================
# 6. CLI 工具名映射与错误路径
# ============================================================================

class TestCliMapping:
    def test_arg_parser_maps_tool_subcommands(self):
        parser = mbdsdr_mcp_client.build_arg_parser()
        args = parser.parse_args(["--host", "192.0.2.1", "tune_fm", "--freq", "98.5"])
        assert args.command == "tune_fm" and args.freq == 98.5 and args.host == "192.0.2.1"
        for cmd in ["list_tools", "get_status", "get_gps", "get_imu",
                    "start_record", "stop_record", "get_version",
                    "check_update", "trigger_ota", "web_ota_url", "reboot"]:
            assert parser.parse_args([cmd]).command == cmd
        assert parser.parse_args(["--mcp"]).mcp is True

    def test_cli_connect_failure_exits_1_with_message(self, monkeypatch,
                                                      capsys):
        def boom(url, timeout=None):
            raise OSError("stub: 不可达")
        monkeypatch.setattr(mbdsdr_mcp_client.websocket,
                            "create_connection", boom)
        monkeypatch.setattr(sys, "argv",
                            ["mbdsdr_mcp_client.py", "--host", "203.0.113.7",
                             "get_status"])
        with pytest.raises(SystemExit) as excinfo:
            mbdsdr_mcp_client.main()
        assert excinfo.value.code == 1
        assert "连接失败" in capsys.readouterr().err

    def test_cli_error_envelope_printed_not_fabricated(self, monkeypatch,
                                                       capsys):
        # wrapper 契约是 result-or-error 透传：CLI 打印的就是错误信封本身，
        # 绝不粉饰成成功结果字段。
        fake = FakeWS(frames=[_resp(1, error={"code": -32000, "message": "忙"})])
        monkeypatch.setattr(mbdsdr_mcp_client.websocket,
                            "create_connection",
                            lambda url, timeout=None: fake)
        monkeypatch.setattr(sys, "argv",
                            ["mbdsdr_mcp_client.py", "--host", "203.0.113.7",
                             "get_status"])
        mbdsdr_mcp_client.main()  # 正常返回：信封透传打印
        printed = json.loads(capsys.readouterr().out)
        assert printed == {"code": -32000, "message": "忙"}
        assert "rssi" not in printed and "result" not in printed

    def test_cli_list_tools_error_envelope_exits_1(self, monkeypatch, capsys):
        # list_tools 的「错误:即失败」在 CLI 侧必须落到非零退出码
        fake = FakeWS(frames=[_resp(1, error={"code": -32000, "message": "忙"})])
        monkeypatch.setattr(mbdsdr_mcp_client.websocket,
                            "create_connection",
                            lambda url, timeout=None: fake)
        monkeypatch.setattr(sys, "argv",
                            ["mbdsdr_mcp_client.py", "--host", "203.0.113.7",
                             "list_tools"])
        with pytest.raises(SystemExit) as excinfo:
            mbdsdr_mcp_client.main()
        assert excinfo.value.code == 1
        assert "错误" in capsys.readouterr().err

    def test_scan_boundaries_empty(self):
        # 空扫描区间：不发一帧、find_strongest_fm 回空对象（显式空态）
        fake = FakeWS(frames=[])
        c = _client_with(fake)
        assert c.scan_fm(108.0, 87.5, 0.1) == []
        assert fake.sent == []
        assert c.find_strongest_fm(108.0, 87.5, 0.1) == {}
        assert fake.sent == []
