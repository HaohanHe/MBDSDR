# SPDX-License-Identifier: MIT
"""
desktop/mcp_worker.py 行为测试（后台 Worker 线程接线 / 信号槽 / 错误信封 / 诚实空态）
==================================================================================

被测对象是 Worker **自己的编排逻辑**（端点解析、状态机、信号契约、转发准入、
并发序列化、失败上报），不是 WebSocket 协议本身——后者由
``tests/test_mcp_client_bridge.py`` 覆盖，本文件不碰真网络：

  * 网络边界一律用 ``_StubClient`` 替身（只替代 WebSocket，返回的是"设备信封"，
    绝不替被测对象编造成功）；
  * ``MBDSDR_OFFLINE=1``（tests/conftest.py 默认）下全文件不发起任何连接。

诚实红线对应到本文件的判据：
  * 未连接/缺依赖/超时 → 必须显式空态或错误，**不得**产生任何合成数据；
  * 错误信封即失败：``{"error": ...}`` 绝不能冒充状态/版本数据下发；
  * ``except ImportError`` 降级路径必须可达且如实报错（不是死代码）。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_mcp_worker_behavior.py -v
"""
import importlib.util
import os
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DESKTOP = os.path.join(_ROOT, "desktop")
sys.path.insert(0, _ROOT)
sys.path.insert(0, _DESKTOP)

from PySide6.QtCore import QThread, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)  # noqa: F401

import mcp_protocol  # noqa: E402
import mcp_worker  # noqa: E402
from tokens import tokens  # noqa: E402


# ----------------------------------------------------------------------
# 替身与夹具
# ----------------------------------------------------------------------

class _StubClient:
    """WebSocket 边界替身：不建连接，按脚本返回 JSON-RPC 信封或抛错。

    只替代"远端设备"，被测的 Worker 编排逻辑一行不替。
    """

    def __init__(self, host, port, timeout=5, verbose=False):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.connect_result = True
        self.closed = False
        self.calls = []          # [(method, params)]
        self.responses = {}      # method -> 响应 dict
        self.raises = {}         # method -> 异常实例
        self.gps = {}
        self.imu = {}
        self.tools = []

    def connect(self):
        if "connect" in self.raises:
            raise self.raises["connect"]
        return self.connect_result

    def close(self):
        self.closed = True

    def call(self, method, params=None):
        self.calls.append((method, dict(params or {})))
        if "call" in self.raises:
            raise self.raises["call"]
        if method in self.raises:
            raise self.raises[method]
        return self.responses.get(method, {"result": {}})

    def get_gps(self):
        self.calls.append(("get_gps", {}))
        if "get_gps" in self.raises:
            raise self.raises["get_gps"]
        return dict(self.gps)

    def get_imu(self):
        self.calls.append(("get_imu", {}))
        if "get_imu" in self.raises:
            raise self.raises["get_imu"]
        return dict(self.imu)

    def list_tools(self):
        self.calls.append(("list_tools", {}))
        if "list_tools" in self.raises:
            raise self.raises["list_tools"]
        return self.tools


class _Recorder:
    """信号记录器。

    单参信号（dict/str）直接存值，多参信号存实参元组，
    断言因此能写成最自然的形式。
    """

    def __init__(self):
        self.items = []

    def __call__(self, *args):
        self.items.append(args[0] if len(args) == 1 else tuple(args))

    def __len__(self):
        return len(self.items)


def _recorders(worker, *names):
    """把一批信号接到独立记录器上，返回 {信号名: 记录器}。"""
    out = {}
    for name in names:
        rec = _Recorder()
        getattr(worker, name).connect(rec)
        out[name] = rec
    return out


@pytest.fixture
def env_clean(monkeypatch):
    """端点相关环境变量清零，避免宿主环境污染端点解析断言。"""
    monkeypatch.delenv(mcp_protocol.ENV_MCP_HOST, raising=False)
    monkeypatch.delenv(mcp_protocol.ENV_MCP_PORT, raising=False)


@pytest.fixture
def stub():
    return _StubClient("192.0.2.10", 81)


def _patch_client(monkeypatch, stub, worker=None):
    """把 Worker 里的 MBDSDRClient 换成替身工厂（不连网）。"""
    seen = {}

    def factory(host, port, timeout=5, **_kw):
        seen["args"] = (host, port, timeout)
        stub.host, stub.port, stub.timeout = host, port, timeout
        return stub

    monkeypatch.setattr(mcp_worker, "MBDSDRClient", factory)
    return seen


def _connected(monkeypatch, stub, responses=None):
    """造一个"已连上硬件"的 Worker（替身客户端，不碰网络）。"""
    _patch_client(monkeypatch, stub)
    stub.responses.update(responses or {"get_version": {"result": {"firmware": "1.4.2"}}})
    w = mcp_worker.MCPWorker("192.0.2.10", 81)
    w._connect()
    return w


def _load_worker_without_client_lib():
    """在"mbdsdr_mcp_client 不可用"的环境下加载一份全新的 mcp_worker。

    用 sys.modules 置 None 触发 ``from ... import`` 的 ImportError，
    走通 mcp_worker 的降级分支（不污染已导入的模块对象）。
    """
    spec = importlib.util.spec_from_file_location(
        "mcp_worker_no_client", os.path.join(_DESKTOP, "mcp_worker.py"))
    mod = importlib.util.module_from_spec(spec)
    saved = sys.modules.get("mbdsdr_mcp_client", None)
    sys.modules["mbdsdr_mcp_client"] = None
    try:
        spec.loader.exec_module(mod)
    finally:
        if saved is None:
            sys.modules.pop("mbdsdr_mcp_client", None)
        else:
            sys.modules["mbdsdr_mcp_client"] = saved
    return mod


# ----------------------------------------------------------------------
# 端点解析与信号面
# ----------------------------------------------------------------------

class TestEndpointAndSignalSurface:
    def test_default_endpoint_comes_from_tokens(self, env_clean):
        """端点默认值只有一份事实源：tokens.MCP。"""
        w = mcp_worker.MCPWorker()
        assert w.host == tokens().MCP["default_host"]
        assert w.port == tokens().MCP["default_port"]

    def test_explicit_endpoint_wins_over_env(self, monkeypatch):
        monkeypatch.setenv(mcp_protocol.ENV_MCP_HOST, "10.0.0.5")
        monkeypatch.setenv(mcp_protocol.ENV_MCP_PORT, "82")
        w = mcp_worker.MCPWorker("172.16.0.2", 9000)
        assert (w.host, w.port) == ("172.16.0.2", 9000)

    def test_poll_interval_default(self, env_clean):
        w = mcp_worker.MCPWorker()
        assert w.poll_interval_ms == 1000

    def test_initial_state_is_empty_not_connected(self, env_clean):
        w = mcp_worker.MCPWorker()
        assert w.client is None
        assert w.connected is False
        assert w.running is False
        assert w._mcp_tool_names == set()

    def test_documented_signals_exist(self):
        """信号面是 Worker 的公开契约；finished 必须是真正的 Signal。

        （历史口径：不能拿 ``worker.finished = thread.quit`` 这种 bound method 冒充。）
        """
        for name in ("status_updated", "gps_updated", "imu_updated",
                     "connection_changed", "tool_result", "request_tool",
                     "error_occurred", "log_message", "finished"):
            sig = getattr(mcp_worker.MCPWorker, name, None)
            assert isinstance(sig, Signal), f"{name} 不是 Signal"

    def test_start_and_stop_are_slots(self):
        """start/stop 必须带 @Slot 装饰（跨线程排队执行的前提）。"""
        for name in ("start", "stop"):
            assert hasattr(getattr(mcp_worker.MCPWorker, name), "_slots"), name

    def test_request_tool_is_wired_to_call_tool(self, env_clean):
        """UI 线程 emit request_tool → 在 call_tool 槽里执行 → 回 tool_result。"""
        w = mcp_worker.MCPWorker()
        rec = _recorders(w, "tool_result", "log_message")
        w.request_tool.emit("tune_fm", {"freq_mhz": 98.5})
        assert rec["tool_result"].items == [("tune_fm", {"error": "未连接"})]
        assert any("调用工具: tune_fm" in m for m in rec["log_message"].items)


# ----------------------------------------------------------------------
# 诚实空态：未连接 / 无 fix / 错误信封
# ----------------------------------------------------------------------

class TestHonestEmptyState:
    def test_poll_without_connection_emits_nothing(self, env_clean):
        """未连接时轮询不得产生任何数据（不合成状态/GPS/IMU）。"""
        w = mcp_worker.MCPWorker()
        w.running = True
        rec = _recorders(w, "status_updated", "gps_updated", "imu_updated",
                         "connection_changed", "error_occurred", "log_message")
        w._poll()
        assert all(len(r) == 0 for r in rec.values()), \
            {k: r.items for k, r in rec.items() if len(r)}

    def test_call_tool_without_connection_reports_error(self, env_clean):
        w = mcp_worker.MCPWorker()
        rec = _recorders(w, "tool_result", "error_occurred")
        w.call_tool("tune_fm", {"freq_mhz": 98.5})
        assert rec["tool_result"].items == [("tune_fm", {"error": "未连接"})]
        assert rec["error_occurred"].items == []

    def test_call_tool_sync_without_connection_reports_error(self, env_clean):
        w = mcp_worker.MCPWorker()
        assert w.call_tool_sync("tune_fm", {}) == \
            {"error": "未连接", "success": False}

    def test_fetch_mcp_tools_without_connection_is_empty(self, env_clean):
        """未连接时不臆造工具 schema：空列表即空态。"""
        w = mcp_worker.MCPWorker()
        assert w.fetch_mcp_tools() == []
        assert w._mcp_tool_names == set()

    def test_gps_without_fix_is_none_not_stale(self, monkeypatch, stub):
        w = _connected(monkeypatch, stub)
        stub.gps = {"fix": False, "lat": 0.0, "lon": 0.0}  # 中性示例坐标
        rec = _recorders(w, "gps_updated")
        w.running = True
        w._poll()
        got = rec["gps_updated"].items[0]
        assert got["source"] == "none"
        assert got["fix"] is False
        assert got["lat"] is None and got["lon"] is None and got["alt"] is None
        assert got["sats"] == 0 and got["hdop"] is None
        assert got["timestamp"] > 0

    def test_gps_with_fix_is_marked_real(self, monkeypatch, stub):
        w = _connected(monkeypatch, stub)
        stub.gps = {"fix": True, "lat": 0.0, "lon": 0.0, "sats": 9}  # 中性示例坐标
        rec = _recorders(w, "gps_updated")
        w.running = True
        w._poll()
        got = rec["gps_updated"].items[0]
        assert got["source"] == "real"
        assert got["lat"] == 0.0 and got["sats"] == 9

    def test_imu_without_acc_is_none(self, monkeypatch, stub):
        w = _connected(monkeypatch, stub)
        stub.imu = {"acc": None, "gyr": [0, 0, 0]}
        rec = _recorders(w, "imu_updated")
        w.running = True
        w._poll()
        got = rec["imu_updated"].items[0]
        assert got["source"] == "none"
        assert got["acc"] is None and got["gyr"] is None and got["mag"] is None

    def test_imu_with_acc_is_marked_real(self, monkeypatch, stub):
        w = _connected(monkeypatch, stub)
        stub.imu = {"acc": [0.1, 0.2, 9.8], "temp": 31.5}
        rec = _recorders(w, "imu_updated")
        w.running = True
        w._poll()
        got = rec["imu_updated"].items[0]
        assert got["source"] == "real"
        assert got["acc"] == [0.1, 0.2, 9.8] and got["temp"] == 31.5

    def test_status_error_envelope_is_not_emitted_as_status(self, monkeypatch, stub):
        """「错误:即失败」：错误信封不得冒充状态字典下发。"""
        w = _connected(monkeypatch, stub)
        stub.responses["get_status"] = {"error": {"code": -32603, "message": "硬件忙"}}
        rec = _recorders(w, "status_updated", "log_message")
        w.running = True
        w._poll()
        assert rec["status_updated"].items == []
        assert any("get_status 失败" in m for m in rec["log_message"].items)

    def test_poll_timeout_is_logged_not_faked(self, monkeypatch, stub):
        """超时是失败：记录日志并留空，绝不补一份假状态。"""
        w = _connected(monkeypatch, stub)
        stub.raises["get_status"] = TimeoutError("调用 get_status 超时 (5s)")
        rec = _recorders(w, "status_updated", "log_message", "error_occurred")
        w.running = True
        w._poll()
        assert rec["status_updated"].items == []
        assert rec["error_occurred"].items == []
        assert any("超时" in m for m in rec["log_message"].items)

    def test_poll_gps_failure_stays_empty(self, monkeypatch, stub):
        w = _connected(monkeypatch, stub)
        stub.raises["get_gps"] = ConnectionError("链路断开")
        rec = _recorders(w, "gps_updated", "log_message")
        w.running = True
        w._poll()
        got = rec["gps_updated"].items[0]
        assert got["source"] == "none" and got["fix"] is False
        assert any("get_gps 失败" in m for m in rec["log_message"].items)


# ----------------------------------------------------------------------
# 连接结果的上报口径
# ----------------------------------------------------------------------

class TestConnectReporting:
    def test_socket_failure_reports_not_connected(self, monkeypatch, stub):
        stub.connect_result = False
        _patch_client(monkeypatch, stub)
        w = mcp_worker.MCPWorker("192.0.2.10", 81)
        rec = _recorders(w, "connection_changed", "error_occurred", "log_message")
        w._connect()
        assert w.connected is False
        assert w.client is None
        assert rec["connection_changed"].items == [
            (False, "连接失败：WebSocket 连接失败")]
        assert rec["error_occurred"].items == [
            "连接失败: WebSocket 连接失败"]
        assert any("不回退模拟数据" in m for m in rec["log_message"].items)

    def test_version_error_envelope_is_rejected(self, monkeypatch, stub):
        """get_version 回错误信封 → 绝不能报"已连接 (固件 unknown)"。"""
        _patch_client(monkeypatch, stub)
        stub.responses["get_version"] = {"error": {"code": -32601, "message": "无此方法"}}
        w = mcp_worker.MCPWorker("192.0.2.10", 81)
        rec = _recorders(w, "connection_changed", "error_occurred")
        w._connect()
        assert w.connected is False
        assert w.client is None
        conn = rec["connection_changed"].items
        assert conn and conn[0][0] is False
        assert "get_version 返回错误" in conn[0][1]
        assert "get_version 返回错误" in rec["error_occurred"].items[0]

    def test_connect_success_reports_real_firmware(self, monkeypatch, stub):
        seen = _patch_client(monkeypatch, stub)
        stub.responses["get_version"] = {"result": {"firmware": "1.4.2"}}
        w = mcp_worker.MCPWorker("192.0.2.10", 81)
        rec = _recorders(w, "connection_changed", "log_message")
        w._connect()
        assert seen["args"] == ("192.0.2.10", 81, 5)  # 超时显式 5 秒，不无限等
        assert w.connected is True
        assert rec["connection_changed"].items == [
            (True, "已连接 192.0.2.10:81 (固件 1.4.2)")]
        assert any("固件版本: 1.4.2" in m for m in rec["log_message"].items)

    def test_connect_timeout_is_reported(self, monkeypatch, stub):
        stub.raises["get_version"] = TimeoutError("调用 get_version 超时 (5s)")
        _patch_client(monkeypatch, stub)
        w = mcp_worker.MCPWorker("192.0.2.10", 81)
        rec = _recorders(w, "connection_changed")
        w._connect()
        assert w.connected is False and w.client is None
        assert "超时" in rec["connection_changed"].items[0][1]

    def test_connect_exception_is_reported(self, monkeypatch, stub):
        stub.raises["connect"] = OSError("网络不可达")
        _patch_client(monkeypatch, stub)
        w = mcp_worker.MCPWorker("192.0.2.10", 81)
        rec = _recorders(w, "connection_changed")
        w._connect()
        assert w.connected is False
        assert "网络不可达" in rec["connection_changed"].items[0][1]


# ----------------------------------------------------------------------
# 工具转发准入与错误信封透传
# ----------------------------------------------------------------------

class TestToolForwarding:
    def test_legacy_alias_forwards_without_gate_token(self, monkeypatch, stub):
        w = _connected(monkeypatch, stub)
        stub.responses["tune_fm"] = {"result": {"ok": True, "freq_mhz": 98.5}}
        out = w.call_tool_sync("tune_fm", {"freq_mhz": 98.5, "arm_manual": True})
        assert out == {"ok": True, "freq_mhz": 98.5}
        assert stub.calls[-1] == ("tune_fm", {"freq_mhz": 98.5})  # 门控 token 不下发

    def test_device_registered_name_forwards(self, monkeypatch, stub):
        w = _connected(monkeypatch, stub)
        w._mcp_tool_names = {"sdr_set_gain"}
        stub.responses["sdr_set_gain"] = {"result": {"gain_db": 20}}
        assert w.call_tool_sync("sdr_set_gain", {"gain_db": 20}) == {"gain_db": 20}
        assert ("sdr_set_gain", {"gain_db": 20}) in stub.calls

    def test_software_tool_is_skipped_not_forwarded(self, monkeypatch, stub):
        """软件工具不二次转发：如实 skipped，且不制造「未知工具」噪声。"""
        w = _connected(monkeypatch, stub)
        out = w.call_tool_sync("workflow_list", {})
        assert out["skipped"] is True and out["success"] is True
        assert "workflow_list" in out["reason"]
        assert [c for c in stub.calls if c[0] == "workflow_list"] == []

    def test_error_envelope_marks_failure(self, monkeypatch, stub):
        """设备回错误信封 → 失败，绝不吞成空结果冒充成功。"""
        w = _connected(monkeypatch, stub)
        stub.responses["tune_fm"] = {"error": {"code": -32000, "message": "硬件忙"}}
        out = w.call_tool_sync("tune_fm", {"freq_mhz": 98.5})
        assert out == {"error": {"code": -32000, "message": "硬件忙"},
                       "success": False}

    def test_tool_exception_becomes_error_result(self, monkeypatch, stub):
        w = _connected(monkeypatch, stub)
        stub.raises["tune_fm"] = TimeoutError("调用 tune_fm 超时 (5s)")
        out = w.call_tool_sync("tune_fm", {"freq_mhz": 98.5})
        assert out == {"error": "调用 tune_fm 超时 (5s)", "success": False}

    def test_async_path_reports_error_and_emits_result(self, monkeypatch, stub):
        """异步路径（UI → worker 线程）：异常同时进 error_occurred 与 tool_result。"""
        w = _connected(monkeypatch, stub)
        stub.raises["tune_fm"] = RuntimeError("炸了")
        rec = _recorders(w, "tool_result", "error_occurred")
        w.call_tool("tune_fm", {})
        assert rec["error_occurred"].items == ["工具调用失败: 炸了"]
        assert rec["tool_result"].items == [("tune_fm", {"error": "炸了"})]

    def test_is_hardware_tool_covers_registry_alias_and_software(self, monkeypatch, stub):
        w = _connected(monkeypatch, stub)
        w._mcp_tool_names = {"sdr_set_gain"}
        assert w.is_hardware_tool("sdr_set_gain") is True      # 设备注册名
        assert w.is_hardware_tool("tune_fm") is True           # 可配置别名
        assert w.is_hardware_tool("workflow_list") is False    # 软件工具
        assert w.is_hardware_tool("") is False
        assert w.is_hardware_tool("   ") is False
        assert w.is_hardware_tool(None) is False

    def test_fetch_mcp_tools_registers_names_and_gate(self, monkeypatch, stub):
        stub.tools = [
            {"name": "tune_fm", "desc": "调谐 FM", "params": {"freq_mhz": "频率 MHz"}},
            {"name": "get_status", "desc": "读状态", "params": {}},
        ]
        w = _connected(monkeypatch, stub)
        rec = _recorders(w, "log_message")
        tools = w.fetch_mcp_tools()
        assert [t["name"] for t in tools] == ["tune_fm", "get_status"]
        assert w._mcp_tool_names == {"tune_fm", "get_status"}
        # 写工具带 arm_manual 人工门，只读工具不带
        write_t = next(t for t in tools if t["name"] == "tune_fm")
        assert "arm_manual" in write_t["inputSchema"]["required"]
        read_t = next(t for t in tools if t["name"] == "get_status")
        assert "arm_manual" not in read_t["inputSchema"].get("required", [])
        assert any("2 个硬件工具" in m for m in rec["log_message"].items)

    def test_fetch_mcp_tools_failure_registers_nothing(self, monkeypatch, stub):
        """list_tools 失败 → 不注册任何工具（不把"取不到"伪装成"没有"）。"""
        stub.raises["list_tools"] = RuntimeError("list_tools 失败: 设备错误")
        w = _connected(monkeypatch, stub)
        rec = _recorders(w, "log_message")
        assert w.fetch_mcp_tools() == []
        assert w._mcp_tool_names == set()
        assert any("不注册任何工具" in m for m in rec["log_message"].items)


# ----------------------------------------------------------------------
# 并发序列化（同一 WebSocket 的收发互斥）
# ----------------------------------------------------------------------

class TestCallSerialization:
    def test_async_and_sync_calls_share_one_lock(self, monkeypatch, stub):
        """异步 UI 调用与 AI 线程同步转发必须互斥进入客户端。

        同一个 WebSocket 上响应按 id 匹配：两段调用交错会让响应错位，
        所以 worker 侧任何一次 client 收发都要过 _call_lock。
        """
        entered = threading.Event()
        release = threading.Event()
        guard = threading.Lock()
        state = {"in_flight": 0, "max_in_flight": 0}

        def call(method, params=None):
            with guard:
                state["in_flight"] += 1
                state["max_in_flight"] = max(state["max_in_flight"],
                                             state["in_flight"])
            if method == "tune_fm":
                entered.set()
                release.wait(5)
            with guard:
                state["in_flight"] -= 1
            return {"result": {"ok": True}}

        stub.call = call
        w = _connected(monkeypatch, stub)
        w._mcp_tool_names = {"get_status"}   # 让同步路径真正打到客户端

        box = {}
        done = threading.Event()

        def async_path():
            w.call_tool("tune_fm", {"freq_mhz": 98.5})

        def sync_path():
            box["result"] = w.call_tool_sync("get_status", {})
            done.set()

        t1 = threading.Thread(target=async_path, daemon=True)
        t1.start()
        assert entered.wait(5), "异步调用未进入客户端（测试自身失效）"
        t2 = threading.Thread(target=sync_path, daemon=True)
        t2.start()
        # 给"无锁抢跑"留足观察窗口
        deadline = time.time() + 0.5
        while time.time() < deadline and state["max_in_flight"] < 2:
            time.sleep(0.01)
        release.set()
        assert done.wait(5), "同步调用未返回"
        t1.join(5)
        t2.join(5)
        assert box["result"] == {"ok": True}
        assert state["max_in_flight"] == 1, (
            "异步与同步调用并发进了同一个客户端，响应 id 会错位")


# ----------------------------------------------------------------------
# 生命周期：start / stop / Manager 线程接线
# ----------------------------------------------------------------------

class TestLifecycle:
    def test_start_arms_poll_timer_and_logs(self, monkeypatch, stub):
        stub.connect_result = False
        _patch_client(monkeypatch, stub)
        w = mcp_worker.MCPWorker("192.0.2.10", 81)
        rec = _recorders(w, "log_message")
        try:
            w.start()
            assert w.running is True
            assert w._timer is not None and w._timer.isActive()
            assert w._timer.interval() == w.poll_interval_ms
            assert any("MCP Worker 启动" in m for m in rec["log_message"].items)
            assert any("轮询定时器启动" in m for m in rec["log_message"].items)
        finally:
            w.stop()

    def test_stop_resets_state_and_emits_disconnect(self, monkeypatch, stub):
        w = _connected(monkeypatch, stub)
        w._mcp_tool_names = {"tune_fm"}
        rec = _recorders(w, "connection_changed", "finished", "log_message")
        w.stop()
        assert w.running is False
        assert w.connected is False
        assert w.client is None
        assert w._timer is None
        assert w._mcp_tool_names == set()
        assert stub.closed is True               # 客户端被显式关闭（不泄漏连接）
        assert (False, "已断开") in rec["connection_changed"].items
        assert rec["finished"].items == [()]     # 生命周期结束信号
        assert any("已停止" in m for m in rec["log_message"].items)

    def test_stop_without_start_is_safe(self, env_clean):
        w = mcp_worker.MCPWorker()
        w.stop()   # 不得抛异常
        assert w.running is False and w.connected is False

    def test_manager_moves_worker_to_its_thread(self, monkeypatch, stub):
        stub.connect_result = False   # 不连网：连接失败路径
        _patch_client(monkeypatch, stub)
        mgr = mcp_worker.MCPWorkerManager()
        worker = mgr.start("192.0.2.10", 81)
        try:
            assert isinstance(mgr.thread, QThread)
            assert worker.thread() is mgr.thread, "Worker 必须搬到自己的线程"
            deadline = time.time() + 5
            while time.time() < deadline and not worker.running:
                QApplication.processEvents()
                time.sleep(0.01)
            assert worker.running is True, "线程启动后 start() 未执行"
            assert worker.connected is False     # 无硬件 → 显式未连接
            assert worker.client is None         # 失败即清，不残留半连接对象
        finally:
            mgr.stop()
        assert mgr.worker is None and mgr.thread is None

    def test_manager_restart_replaces_worker(self, monkeypatch, stub):
        stub.connect_result = False
        _patch_client(monkeypatch, stub)
        mgr = mcp_worker.MCPWorkerManager()
        try:
            first = mgr.start("192.0.2.10", 81)
            second = mgr.start("192.0.2.10", 81)
            assert second is not first, "重启必须换掉旧 Worker"
            assert mgr.thread is not None
        finally:
            mgr.stop()


# ----------------------------------------------------------------------
# 降级路径：mbdsdr_mcp_client 不可用（except ImportError 分支不是死代码）
# ----------------------------------------------------------------------

class TestDegradedWithoutClientLib:
    def test_degraded_module_flags_missing_client(self):
        mod = _load_worker_without_client_lib()
        assert mod.HAS_MCP_CLIENT is False

    def test_degraded_connect_reports_missing_lib(self):
        """缺依赖 → 显式"未连接"，不伪造连接成功。"""
        mod = _load_worker_without_client_lib()
        w = mod.MCPWorker("192.0.2.10", 81)
        rec = _recorders(w, "connection_changed", "error_occurred")
        w._connect()
        assert w.connected is False
        assert w.client is None
        assert rec["error_occurred"].items == ["未连接: mbdsdr_mcp_client 库不可用"]
        assert rec["connection_changed"].items == [
            (False, "未连接：缺少 mbdsdr_mcp_client 客户端库")]

    def test_degraded_worker_still_honest_on_calls(self):
        mod = _load_worker_without_client_lib()
        w = mod.MCPWorker()
        rec = _recorders(w, "tool_result")
        w.request_tool.emit("set_volume", {"volume": 30})
        assert rec["tool_result"].items == [("set_volume", {"error": "未连接"})]
        assert w.call_tool_sync("set_volume", {}) == \
            {"error": "未连接", "success": False}
        assert w.fetch_mcp_tools() == []

    def test_degraded_poll_emits_nothing(self):
        mod = _load_worker_without_client_lib()
        w = mod.MCPWorker()
        w.running = True
        rec = _recorders(w, "status_updated", "gps_updated", "imu_updated")
        w.start()
        try:
            w._poll()
        finally:
            w.stop()
        assert all(len(r) == 0 for r in rec.values())
        assert w.connected is False

    def test_degraded_alias_table_still_local(self):
        """别名表是本地可配置数据：缺客户端库也不影响准入判定。"""
        mod = _load_worker_without_client_lib()
        w = mod.MCPWorker()
        assert w.is_hardware_tool("tune_fm") is True
        assert w.is_hardware_tool("workflow_list") is False
