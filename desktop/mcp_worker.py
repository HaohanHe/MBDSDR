# SPDX-License-Identifier: MIT
"""
MBDSDR MCP 后台 Worker
=======================
在后台线程连接 ai-sdr Mini，定时轮询状态，
通过 Qt 信号通知 UI 更新。支持调用工具（调谐、音量、录音等）。

架构原则：只对接真实硬件后端；连接失败时显式"未连接"，
绝不回退到模拟数据并报成功。
"""

import sys
import os
import time
import json
import threading
from typing import Optional, Dict, Any, List

from PySide6.QtCore import QObject, QThread, Signal, QTimer, Slot

# 确保能导入上层目录的 mbdsdr_mcp_client
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Qt 无关的编排纯逻辑：端点解析 / schema 归一 / 可配置别名表 / 转发策略
from mcp_protocol import (
    MCP_TOOL_ALIASES,
    annotate_write_gate,
    firmware_tools_to_mcp_schema,
    normalize_tool_call,
    resolve_mcp_endpoint,
)

try:
    from mbdsdr_mcp_client import MBDSDRClient
    HAS_MCP_CLIENT = True
except ImportError:
    HAS_MCP_CLIENT = False
    print("警告: 无法导入 mbdsdr_mcp_client，硬件将处于未连接状态", file=sys.stderr)

try:  # pragma: no cover - 失败消息整形（clean_exc 战役先例），缺失时退化原文
    from mbdsdr_ai.tool_registry import clean_exc as _clean_exc
except Exception:  # noqa: BLE001
    def _clean_exc(e):  # type: ignore[misc]
        return str(e)


# ============================================================================
# MCP Worker (后台线程)
# ============================================================================

class MCPWorker(QObject):
    """MCP 后台 Worker，在独立线程中运行。"""

    # 信号
    status_updated = Signal(dict)       # get_status 结果
    gps_updated = Signal(dict)          # get_gps 结果
    imu_updated = Signal(dict)          # get_imu 结果
    connection_changed = Signal(bool, str)  # 连接状态 (connected, message)
    tool_result = Signal(str, dict)     # 工具调用结果 (tool_name, result)
    request_tool = Signal(str, dict)    # UI 请求调用工具 (tool_name, params)，worker 线程执行
    error_occurred = Signal(str)        # 错误信息
    log_message = Signal(str)           # 日志
    finished = Signal()                 # Worker 生命周期结束（stop 完成）→ 线程退出

    def __init__(self, host: str = "", port: int = 0, parent=None):
        super().__init__(parent)
        # 端点：显式参数 > 环境变量(MBDSDR_MCP_HOST/PORT) > tokens.MCP 默认值
        self.host, self.port = resolve_mcp_endpoint(
            host or None, port if port else None)
        self.client: Optional[MBDSDRClient] = None
        self.connected = False
        self.running = False
        self.poll_interval_ms = 1000  # 轮询间隔
        self._timer: Optional[QTimer] = None
        # 客户端调用互斥锁：轮询（worker 线程）与 AI 线程的同步转发共用一个
        # WebSocket，序列化收发避免响应 id 错位。
        self._call_lock = threading.Lock()
        # 设备 list_tools 报告的硬件工具注册名（硬件面事实源；未连接时为空）
        self._mcp_tool_names: set = set()
        # UI 线程 emit request_tool → worker 线程在 call_tool 槽中执行（跨线程自动排队）
        self.request_tool.connect(self.call_tool)

    @Slot()
    def start(self):
        """启动 Worker（在新线程中调用）。"""
        self.running = True
        self.log_message.emit(f"MCP Worker 启动 (host={self.host}, port={self.port})")
        self._connect()

        # 启动轮询定时器
        self._timer = QTimer()
        self._timer.timeout.connect(self._poll)
        self._timer.start(self.poll_interval_ms)
        self.log_message.emit(f"轮询定时器启动，间隔 {self.poll_interval_ms}ms")

    @Slot()
    def stop(self):
        """停止 Worker。"""
        self.running = False
        if self._timer:
            self._timer.stop()
            self._timer = None
        if self.client:
            try:
                with self._call_lock:
                    self.client.close()
            except Exception:
                pass
            self.client = None
        self.connected = False
        self._mcp_tool_names = set()
        self.connection_changed.emit(False, "已断开")
        self.log_message.emit("MCP Worker 已停止")
        self.finished.emit()

    def _connect(self):
        """连接 ai-sdr Mini。失败时显式未连接，绝不回退模拟数据。"""
        if not HAS_MCP_CLIENT:
            self.connected = False
            self.error_occurred.emit("未连接: mbdsdr_mcp_client 库不可用")
            self.connection_changed.emit(False, "未连接：缺少 mbdsdr_mcp_client 客户端库")
            return

        try:
            self.client = MBDSDRClient(self.host, self.port, timeout=5)
            with self._call_lock:
                if not self.client.connect():
                    raise ConnectionError("WebSocket 连接失败")
                # 测试连接：必须拿到成功结果。「错误:即失败」——get_version 回
                # 错误信封时绝不能报"已连接 (固件 unknown)"伪装成功。
                resp = self.client.call("get_version")
            if "error" in resp:
                raise ConnectionError(f"get_version 返回错误: {resp['error']}")
            version = resp.get("result") or {}
            self.connected = True
            fw = version.get("firmware", "unknown")
            self.connection_changed.emit(True, f"已连接 {self.host}:{self.port} (固件 {fw})")
            self.log_message.emit(f"连接成功，固件版本: {fw}")
        except Exception as e:
            self.connected = False
            self.client = None
            self.error_occurred.emit(f"连接失败: {_clean_exc(e)}")
            self.log_message.emit(f"连接失败: {_clean_exc(e)}（不回退模拟数据）")
            self.connection_changed.emit(False, f"连接失败：{_clean_exc(e)}")

    def _poll(self):
        """定时轮询状态。仅在真实硬件已连接时轮询；未连接时不产生任何数据。"""
        if not self.running:
            return

        try:
            if not (self.client and self.connected):
                # 未连接：不 emit 任何合成数据，UI 维持"未连接"态
                return

            try:
                with self._call_lock:
                    resp = self.client.call("get_status")
                if "error" in resp:
                    # 「错误:即失败」：错误信封不得冒充状态数据下发，
                    # 否则下游会把 {"code","message"} 当状态字典消费。
                    self.log_message.emit(f"get_status 失败: {resp['error']}")
                else:
                    self.status_updated.emit(resp.get("result") or {})
            except Exception as e:
                self.log_message.emit(f"get_status 失败: {_clean_exc(e)}")

            try:
                with self._call_lock:
                    gps = self.client.get_gps()
                # 真实硬件：仅在有 fix 时标注 source="real"；
                # 否则按"未连接"处理，emit 全 None 字典，避免 UI 用旧值/0 伪装。
                if isinstance(gps, dict) and gps.get("fix"):
                    gps["source"] = "real"
                    gps["timestamp"] = time.time()
                    self.gps_updated.emit(gps)
                else:
                    self.gps_updated.emit({
                        "fix": False, "source": "none",
                        "lat": None, "lon": None, "alt": None,
                        "sats": 0, "hdop": None,
                        "timestamp": time.time(),
                    })
            except Exception as e:
                self.log_message.emit(f"get_gps 失败: {_clean_exc(e)}")
                self.gps_updated.emit({
                    "fix": False, "source": "none",
                    "lat": None, "lon": None, "alt": None,
                    "sats": 0, "hdop": None,
                    "timestamp": time.time(),
                })

            try:
                with self._call_lock:
                    imu = self.client.get_imu()
                # 真实硬件：有有效 acc 数据才标注 source="real"；
                # 否则 emit 全 None 字典。
                if isinstance(imu, dict) and imu.get("acc") is not None:
                    imu["source"] = "real"
                    imu["timestamp"] = time.time()
                    self.imu_updated.emit(imu)
                else:
                    self.imu_updated.emit({
                        "source": "none",
                        "acc": None, "gyr": None,
                        "mag": None, "temp": None,
                        "timestamp": time.time(),
                    })
            except Exception as e:
                self.log_message.emit(f"get_imu 失败: {_clean_exc(e)}")
                self.imu_updated.emit({
                    "source": "none",
                    "acc": None, "gyr": None,
                    "mag": None, "temp": None,
                    "timestamp": time.time(),
                })
        except Exception as e:
            self.error_occurred.emit(f"轮询异常: {_clean_exc(e)}")
            self.log_message.emit(f"轮询异常: {_clean_exc(e)}")

    # ========================================================================
    # 工具调用（从 UI 线程调用，通过信号返回结果）
    # ========================================================================

    @Slot(str, dict)
    def call_tool(self, tool_name: str, params: Dict):
        """调用 MCP 工具（异步路径：UI 请求 → worker 线程执行 → tool_result 信号）。

        只走真实硬件路径；未连接时返回错误。软件工具名不二次转发（skipped）。
        """
        self.log_message.emit(f"调用工具: {tool_name} params={params}")

        try:
            if self.client and self.connected:
                # 与轮询、AI 线程同步转发共用同一把锁：三条路径都走同一条
                # WebSocket，不串行会把响应 id 配错到别的调用上。
                with self._call_lock:
                    result = self._real_tool(tool_name, params)
            else:
                result = {"error": "未连接"}

            self.tool_result.emit(tool_name, result)
        except Exception as e:
            self.error_occurred.emit(f"工具调用失败: {_clean_exc(e)}")
            self.tool_result.emit(tool_name, {"error": _clean_exc(e)})

    def call_tool_sync(self, tool_name: str, params: Dict) -> Dict:
        """同步调用硬件工具（供 AI 工具环内的 MCP handler 用，任意线程可调）。

        与轮询共用 _call_lock，序列化对同一 WebSocket 的收发；不 emit
        tool_result（结果由调用方直接拿到，避免与步骤卡重复记账）。
        """
        if not (self.client and self.connected):
            return {"error": "未连接", "success": False}
        try:
            with self._call_lock:
                return self._real_tool(tool_name, params)
        except Exception as e:
            return {"error": _clean_exc(e), "success": False}

    def fetch_mcp_tools(self) -> List[Dict]:
        """从设备 list_tools 取硬件工具 schema（MCP 标准形状）。

        顺带记录注册名（硬件面事实源）。未连接/失败时返回空列表——
        不臆造工具 schema；调用方按空态如实提示。
        """
        if not (self.client and self.connected):
            return []
        try:
            with self._call_lock:
                raw = self.client.list_tools()
        except Exception as e:
            self.log_message.emit(f"list_tools 失败: {_clean_exc(e)}（不注册任何工具）")
            return []
        tools = annotate_write_gate(
            firmware_tools_to_mcp_schema(raw if isinstance(raw, list) else []))
        self._mcp_tool_names = {t["name"] for t in tools}
        self.log_message.emit(f"设备报告 {len(tools)} 个硬件工具")
        return tools

    def is_hardware_tool(self, tool_name: str) -> bool:
        """是否属于硬件面（设备注册名 ∪ 可配置别名表）——转发链的准入判据。"""
        name = (tool_name or "").strip()
        return bool(name) and (name in self._mcp_tool_names
                               or name in MCP_TOOL_ALIASES)

    def _real_tool(self, tool_name: str, params: Dict) -> Dict:
        """通用 JSON-RPC 转发：只转发硬件面，软件工具不二次转发。

        - 转发目标由 normalize_tool_call 决定（设备注册名或可配置别名表，
          不再内联写死方法名映射）；
        - 非硬件面的调用如实返回 skipped（不是失败，不制造「未知工具」噪声）。
        """
        if not self.client:
            return {"error": "客户端未初始化"}

        plan = normalize_tool_call(tool_name, params, self._mcp_tool_names)
        if plan is None:
            return {"skipped": True, "success": True,
                    "reason": f"软件工具 {tool_name} 不二次转发"}

        resp = self.client.call(plan["method"], plan["params"])
        if "error" in resp:
            return {"error": resp["error"], "success": False}
        return resp.get("result", {})


# ============================================================================
# Worker 管理器（在 UI 线程中使用）
# ============================================================================

class MCPWorkerManager:
    """管理 MCP Worker 的生命周期（UI 线程使用）。"""

    def __init__(self):
        self.thread: Optional[QThread] = None
        self.worker: Optional[MCPWorker] = None

    def start(self, host: str = "", port: int = 0) -> MCPWorker:
        """启动 Worker 线程。端点缺省时按 mcp_protocol.resolve_mcp_endpoint
        解析（环境变量可覆盖 tokens.MCP 默认值）。"""
        self.stop()  # 先停止旧的

        self.thread = QThread()
        self.worker = MCPWorker(host, port)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.start)
        # 自定义信号 finished（stop 完成时发出）→ 线程退出；必须 connect，
        # 不能 `worker.finished = thread.quit` 赋值个 bound method 冒充信号。
        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)

        self.thread.start()
        return self.worker

    def stop(self):
        """停止 Worker 线程。"""
        if self.worker:
            self.worker.stop()
        if self.thread:
            self.thread.quit()
            self.thread.wait(3000)
        self.worker = None
        self.thread = None
