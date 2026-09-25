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
import traceback
from typing import Optional, Dict, Any

from PySide6.QtCore import QObject, QThread, Signal, QTimer, Slot

# 确保能导入上层目录的 mbdsdr_mcp_client
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from mbdsdr_mcp_client import MBDSDRClient
    HAS_MCP_CLIENT = True
except ImportError:
    HAS_MCP_CLIENT = False
    print("警告: 无法导入 mbdsdr_mcp_client，硬件将处于未连接状态", file=sys.stderr)


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

    def __init__(self, host: str = "192.168.4.1", port: int = 81, parent=None):
        super().__init__(parent)
        self.host = host
        self.port = port
        self.client: Optional[MBDSDRClient] = None
        self.connected = False
        self.running = False
        self.poll_interval_ms = 1000  # 轮询间隔
        self._timer: Optional[QTimer] = None
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
                self.client.close()
            except Exception:
                pass
            self.client = None
        self.connected = False
        self.connection_changed.emit(False, "已断开")
        self.log_message.emit("MCP Worker 已停止")

    def _connect(self):
        """连接 ai-sdr Mini。失败时显式未连接，绝不回退模拟数据。"""
        if not HAS_MCP_CLIENT:
            self.connected = False
            self.error_occurred.emit("未连接: mbdsdr_mcp_client 库不可用")
            self.connection_changed.emit(False, "未连接：缺少 mbdsdr_mcp_client 客户端库")
            return

        try:
            self.client = MBDSDRClient(self.host, self.port, timeout=5)
            self.client.connect()
            # 测试连接
            version = self.client.get_version()
            self.connected = True
            fw = version.get("firmware", "unknown")
            self.connection_changed.emit(True, f"已连接 {self.host}:{self.port} (固件 {fw})")
            self.log_message.emit(f"连接成功，固件版本: {fw}")
        except Exception as e:
            self.connected = False
            self.client = None
            self.error_occurred.emit(f"连接失败: {e}")
            self.log_message.emit(f"连接失败: {e}（不回退模拟数据）")
            self.connection_changed.emit(False, f"连接失败：{e}")

    def _poll(self):
        """定时轮询状态。仅在真实硬件已连接时轮询；未连接时不产生任何数据。"""
        if not self.running:
            return

        try:
            if not (self.client and self.connected):
                # 未连接：不 emit 任何合成数据，UI 维持"未连接"态
                return

            try:
                status = self.client.get_status()
                self.status_updated.emit(status)
            except Exception as e:
                self.log_message.emit(f"get_status 失败: {e}")

            try:
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
                self.log_message.emit(f"get_gps 失败: {e}")
                self.gps_updated.emit({
                    "fix": False, "source": "none",
                    "lat": None, "lon": None, "alt": None,
                    "sats": 0, "hdop": None,
                    "timestamp": time.time(),
                })

            try:
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
                self.log_message.emit(f"get_imu 失败: {e}")
                self.imu_updated.emit({
                    "source": "none",
                    "acc": None, "gyr": None,
                    "mag": None, "temp": None,
                    "timestamp": time.time(),
                })
        except Exception as e:
            self.error_occurred.emit(f"轮询异常: {e}")
            self.log_message.emit(f"轮询异常: {traceback.format_exc()}")

    # ========================================================================
    # 工具调用（从 UI 线程调用，通过信号返回结果）
    # ========================================================================

    @Slot(str, dict)
    def call_tool(self, tool_name: str, params: Dict):
        """调用 MCP 工具。只走真实硬件路径；未连接时返回错误。"""
        self.log_message.emit(f"调用工具: {tool_name} params={params}")

        try:
            if self.client and self.connected:
                result = self._real_tool(tool_name, params)
            else:
                result = {"error": "未连接"}

            self.tool_result.emit(tool_name, result)
        except Exception as e:
            self.error_occurred.emit(f"工具调用失败: {e}")
            self.tool_result.emit(tool_name, {"error": str(e)})

    def _real_tool(self, tool_name: str, params: Dict) -> Dict:
        """调用真实硬件工具。"""
        if not self.client:
            return {"error": "客户端未初始化"}

        method_map = {
            "tune_fm": lambda p: self.client.tune_fm(p.get("freq_mhz", 98.5)),
            "tune_am": lambda p: self.client.tune_am(p.get("freq_khz", 980)),
            "set_volume": lambda p: self.client.set_volume(p.get("volume", 30)),
            "start_record": lambda p: self.client.start_record(),
            "stop_record": lambda p: self.client.stop_record(),
            "get_version": lambda p: self.client.get_version(),
            "reboot": lambda p: self.client.reboot(),
            "list_tools": lambda p: self.client.list_tools(),
            # 通用 JSON-RPC 转发：AI 层注册的 SDR 工具（不直接操作硬件，由远端 MCP 服务路由）
            "tune_sdr": lambda p: self.client.call("tune_sdr", p).get("result", {}),
            "sdr_set_frequency": lambda p: self.client.call("sdr_set_frequency", p).get("result", {}),
        }

        if tool_name in method_map:
            return method_map[tool_name](params)
        else:
            return {"error": f"未知工具: {tool_name}"}


# ============================================================================
# Worker 管理器（在 UI 线程中使用）
# ============================================================================

class MCPWorkerManager:
    """管理 MCP Worker 的生命周期（UI 线程使用）。"""

    def __init__(self):
        self.thread: Optional[QThread] = None
        self.worker: Optional[MCPWorker] = None

    def start(self, host: str = "192.168.4.1", port: int = 81) -> MCPWorker:
        """启动 Worker 线程。"""
        self.stop()  # 先停止旧的

        self.thread = QThread()
        self.worker = MCPWorker(host, port)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.start)
        self.worker.finished = self.thread.quit  # 自定义信号
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
