"""
MBDSDR MCP 后台 Worker
=======================
在后台线程连接 ai-sdr Mini（或模拟服务器），定时轮询状态，
通过 Qt 信号通知 UI 更新。支持调用工具（调谐、音量、录音等）。
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
    print("警告: 无法导入 mbdsdr_mcp_client，将使用模拟数据", file=sys.stderr)


# ============================================================================
# 模拟数据生成器（无硬件时使用）
# ============================================================================

class SimDataGenerator:
    """模拟 ai-sdr Mini 的数据，用于无硬件时演示 UI。"""

    def __init__(self):
        self.mode = 1  # 0=AM, 1=FM
        self.freq = 9850  # FM 98.5 MHz
        self.volume = 30
        self.recording = False
        self.rec_samples = 0
        self.uptime = 0
        self.fm_stations = {
            87.6: 45, 88.7: 52, 90.0: 38, 91.5: 60, 93.0: 42,
            95.5: 55, 97.4: 48, 98.5: 70, 100.0: 40, 101.8: 58,
            103.9: 50, 105.1: 35, 106.1: 44, 107.7: 30,
        }

    def tick(self):
        self.uptime += 1
        if self.recording:
            self.rec_samples += 48000

    def get_status(self) -> Dict:
        freq_mhz = self.freq / 100.0
        best_rssi = 10
        for sf, sr in self.fm_stations.items():
            dist = abs(freq_mhz - sf)
            if dist < 0.5:
                best_rssi = max(best_rssi, sr - int(dist * 40))
        rssi = best_rssi + __import__('random').randint(-3, 3)
        return {
            "mode": self.mode,
            "mode_name": "FM" if self.mode == 1 else "AM",
            "freq": self.freq,
            "freq_display": f"{freq_mhz:.1f} MHz" if self.mode == 1 else f"{self.freq} kHz",
            "rssi": rssi,
            "snr": max(5, min(40, rssi - 15 + __import__('random').randint(-2, 2))),
            "volume": self.volume,
            "muted": False,
            "recording": self.recording,
            "uptime": self.uptime,
        }

    def get_gps(self) -> Dict:
        import random
        # 模拟模式：返回合成坐标，但必须标注 source="sim"，
        # 面板会据此显示"模拟定位"角标，绝不伪装成真实 fix。
        return {
            "fix": True,
            "source": "sim",
            "lat": round(39.9042 + random.uniform(-0.0001, 0.0001), 6),
            "lon": round(116.4074 + random.uniform(-0.0001, 0.0001), 6),
            "alt": round(50.0 + random.uniform(-0.5, 0.5), 1),
            "sats": 12 + random.randint(-1, 1),
            "hdop": round(0.8 + random.uniform(-0.05, 0.05), 1),
            "timestamp": time.time(),
        }

    def get_imu(self) -> Dict:
        import random
        # 模拟模式：合成 9 轴数据，标注 source="sim"
        return {
            "source": "sim",
            "acc": [
                round(random.uniform(-0.05, 0.05), 2),
                round(random.uniform(-0.05, 0.05), 2),
                round(0.98 + random.uniform(-0.03, 0.03), 2),
            ],
            "gyr": [
                round(random.uniform(-0.5, 0.5), 1),
                round(random.uniform(-0.5, 0.5), 1),
                round(random.uniform(-0.5, 0.5), 1),
            ],
            "mag": [
                round(random.uniform(20, 30), 1),
                round(random.uniform(-5, 5), 1),
                round(random.uniform(40, 50), 1),
            ],
            "temp": round(25.0 + random.uniform(-0.5, 0.5), 1),
            "timestamp": time.time(),
        }


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

    def __init__(self, host: str = "192.168.4.1", port: int = 81,
                 use_simulation: bool = False, parent=None):
        super().__init__(parent)
        self.host = host
        self.port = port
        self.use_simulation = use_simulation
        self.client: Optional[MBDSDRClient] = None
        self.sim = SimDataGenerator()
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
        self.log_message.emit(f"MCP Worker 启动 (host={self.host}, port={self.port}, sim={self.use_simulation})")

        if self.use_simulation:
            self.connected = True
            self.connection_changed.emit(True, "模拟模式（无硬件）")
            self.log_message.emit("已进入模拟模式，使用模拟数据")
        else:
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
        """连接 ai-sdr Mini。"""
        if not HAS_MCP_CLIENT:
            self.use_simulation = True
            self.connected = True
            self.connection_changed.emit(True, "无 MCP 客户端库，回退模拟模式")
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
            self.error_occurred.emit(f"连接失败: {e}")
            self.log_message.emit(f"连接失败: {e}，回退模拟模式")
            self.use_simulation = True
            self.connected = True
            self.connection_changed.emit(True, "连接失败，回退模拟模式")

    def _poll(self):
        """定时轮询状态。"""
        if not self.running:
            return

        try:
            if self.use_simulation:
                self.sim.tick()
                self.status_updated.emit(self.sim.get_status())
                self.gps_updated.emit(self.sim.get_gps())
                self.imu_updated.emit(self.sim.get_imu())
            elif self.client and self.connected:
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
        """调用 MCP 工具。"""
        self.log_message.emit(f"调用工具: {tool_name} params={params}")

        try:
            if self.use_simulation:
                result = self._sim_tool(tool_name, params)
            elif self.client and self.connected:
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

    def _sim_tool(self, tool_name: str, params: Dict) -> Dict:
        """模拟工具调用。"""
        if tool_name == "tune_fm":
            freq = params.get("freq_mhz", 98.5)
            self.sim.mode = 1
            self.sim.freq = int(float(freq) * 100)
            return {"ok": True, "freq_mhz": freq, "mode": "FM"}
        elif tool_name == "tune_am":
            freq = params.get("freq_khz", 980)
            self.sim.mode = 0
            self.sim.freq = int(freq)
            return {"ok": True, "freq_khz": freq, "mode": "AM"}
        elif tool_name == "set_volume":
            vol = max(0, min(63, int(params.get("volume", 30))))
            self.sim.volume = vol
            return {"ok": True, "volume": vol}
        elif tool_name == "start_record":
            if self.sim.recording:
                return {"ok": False, "error": "已经在录音"}
            self.sim.recording = True
            self.sim.rec_samples = 0
            return {"ok": True, "recording": True}
        elif tool_name == "stop_record":
            if not self.sim.recording:
                return {"ok": False, "error": "没有在录音"}
            self.sim.recording = False
            return {"ok": True, "samples": self.sim.rec_samples,
                    "duration_sec": round(self.sim.rec_samples / 48000, 2)}
        elif tool_name == "get_version":
            return {
                "firmware": "v0.6-SIMULATION",
                "board": "ai-sdr Mini (SIMULATION)",
                "mcu": "ESP32-S3-N8R8 (SIMULATED)",
                "sdr": "SI4732-A10-GSR (SIMULATED)",
            }
        elif tool_name == "reboot":
            return {"ok": True, "note": "模拟模式不重启"}
        elif tool_name == "list_tools":
            return {"tools": [
                {"name": "tune_fm", "desc": "调谐FM广播"},
                {"name": "tune_am", "desc": "调谐AM广播"},
                {"name": "set_volume", "desc": "设置音量"},
                {"name": "get_status", "desc": "获取状态"},
                {"name": "get_gps", "desc": "获取GPS"},
                {"name": "get_imu", "desc": "获取9轴姿态"},
                {"name": "start_record", "desc": "开始录音"},
                {"name": "stop_record", "desc": "停止录音"},
                {"name": "get_version", "desc": "获取版本"},
                {"name": "reboot", "desc": "重启"},
                {"name": "tune_sdr", "desc": "调谐SDR（频率Hz+模式）"},
                {"name": "sdr_set_frequency", "desc": "设置SDR中心频率Hz"},
            ]}
        elif tool_name == "tune_sdr":
            freq_hz = params.get("freq_hz", 100_000_000)
            mode = params.get("mode", "NFM")
            self.sim.mode = 1 if mode in ("FM", "WFM", "NFM") else 0
            self.sim.freq = int(freq_hz / 1000)
            return {"ok": True, "freq_hz": freq_hz, "mode": mode}
        elif tool_name == "sdr_set_frequency":
            freq_hz = params.get("frequency_hz", 100_000_000)
            self.sim.freq = int(freq_hz / 1000)
            return {"ok": True, "frequency_hz": freq_hz}
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

    def start(self, host: str = "192.168.4.1", port: int = 81,
              use_simulation: bool = False) -> MCPWorker:
        """启动 Worker 线程。"""
        self.stop()  # 先停止旧的

        self.thread = QThread()
        self.worker = MCPWorker(host, port, use_simulation)
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
