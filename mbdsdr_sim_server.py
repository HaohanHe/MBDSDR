#!/usr/bin/env python3
"""
MBDSDR ai-sdr Mini 硬件模拟服务器
====================================
在没有真实硬件时，模拟 ai-sdr Mini 的 WebSocket MCP 服务，
用于测试 MCP 客户端 (mbdsdr_mcp_client.py)、MCP 桥接器、
Cursor/Claude Desktop 等 MCP 客户端的全链路集成。

用法:
  python3 mbdsdr_sim_server.py --port 81
  python3 mbdsdr_sim_server.py --port 81 --verbose

然后在另一个终端:
  python3 mbdsdr_mcp_client.py --host localhost --port 81 list_tools
  python3 mbdsdr_mcp_client.py --host localhost --port 81 tune_fm --freq 98.5
  python3 mbdsdr_mcp_client.py --host localhost --port 81 get_status

模拟数据说明:
  - RSSI 随频率变化 (FM 广播段有几个模拟强台)
  - GPS 固定在北京附近 (可通过 --lat/--lon 修改)
  - IMU 数据随机但合理 (加速度 ~1g, 陀螺仪 ~0, 磁力计 ~地磁场)
  - 录音返回递增的采样数
  - 版本信息标记为 SIMULATION

MBDSDR Project - AI定义无线电 - 全开源 GPL-3.0 - 呼号 BI4MIB
"""

import asyncio
import json
import math
import random
import argparse
import sys
from typing import Any, Dict, Optional

try:
    import websockets
except ImportError:
    print("错误: 缺少 websockets 库，请运行: pip install websockets", file=sys.stderr)
    sys.exit(1)


# ============================================================================
# 模拟状态
# ============================================================================

class SimState:
    """模拟 ai-sdr Mini 的硬件状态。"""

    def __init__(self, lat: float = 39.9042, lon: float = 116.4074):
        self.mode = 1  # 0=AM, 1=FM
        self.freq = 9850  # FM 98.5 MHz (单位 0.01MHz)
        self.volume = 30
        self.muted = False
        self.recording = False
        self.rec_samples = 0
        self.uptime = 0
        self.gps_fix = True
        self.gps_lat = lat
        self.gps_lon = lon
        self.gps_alt = 50.0
        self.gps_sats = 12
        self.gps_hdop = 0.8
        # 模拟 FM 强台位置 (MHz -> RSSI)
        self.fm_stations = {
            87.6: 45, 88.7: 52, 90.0: 38, 91.5: 60, 93.0: 42,
            95.5: 55, 97.4: 48, 98.5: 70, 100.0: 40, 101.8: 58,
            103.9: 50, 105.1: 35, 106.1: 44, 107.7: 30,
        }
        # 模拟 AM 强台位置 (kHz -> RSSI)
        self.am_stations = {
            540: 40, 639: 55, 720: 35, 828: 60, 900: 42,
            1008: 50, 1134: 38, 1251: 45, 1359: 30, 1521: 48,
        }

    def tick(self):
        """每秒调用一次，更新状态。"""
        self.uptime += 1
        if self.recording:
            self.rec_samples += 48000  # 48kHz 采样率

    def calc_rssi(self) -> int:
        """根据当前频率计算模拟 RSSI。"""
        if self.mode == 1:  # FM
            freq_mhz = self.freq / 100.0
            # 找最近的强台
            best_rssi = 10
            for station_freq, station_rssi in self.fm_stations.items():
                dist = abs(freq_mhz - station_freq)
                if dist < 0.5:
                    # 距离越近信号越强
                    rssi = station_rssi - int(dist * 40)
                    best_rssi = max(best_rssi, rssi)
            return best_rssi + random.randint(-3, 3)
        else:  # AM
            freq_khz = self.freq
            best_rssi = 10
            for station_freq, station_rssi in self.am_stations.items():
                dist = abs(freq_khz - station_freq)
                if dist < 20:
                    rssi = station_rssi - int(dist * 0.5)
                    best_rssi = max(best_rssi, rssi)
            return best_rssi + random.randint(-2, 2)

    def calc_snr(self) -> int:
        """计算模拟 SNR (与 RSSI 正相关)。"""
        rssi = self.calc_rssi()
        return max(5, min(40, rssi - 15 + random.randint(-2, 2)))


# ============================================================================
# 工具实现 (14 个 MCP 工具的模拟版本)
# ============================================================================

TOOLS_LIST = [
    {"name": "tune_fm", "desc": "调谐FM广播, freq_mhz单位MHz", "params": {"freq_mhz": "float 64-108"}},
    {"name": "tune_am", "desc": "调谐AM/MW广播, freq_khz单位kHz", "params": {"freq_khz": "int 531-1710"}},
    {"name": "set_volume", "desc": "设置音量 0-63", "params": {"volume": "int 0-63"}},
    {"name": "get_status", "desc": "获取当前SDR状态(模式/频率/RSSI/SNR)", "params": {}},
    {"name": "get_gps", "desc": "获取GPS/北斗定位(经纬度/高度/卫星数/HDOP)", "params": {}},
    {"name": "get_imu", "desc": "获取9轴姿态(加速度/陀螺仪/磁力计/温度)", "params": {}},
    {"name": "start_record", "desc": "开始I2S基带录音(WAV头通过WebSocket推送,电脑端存盘)", "params": {}},
    {"name": "stop_record", "desc": "停止录音,返回采样数", "params": {}},
    {"name": "get_version", "desc": "获取固件版本/硬件/MCU/SDR芯片信息", "params": {}},
    {"name": "check_update", "desc": "检查固件更新(返回当前版本/GitHub release地址/OTA地址)", "params": {}},
    {"name": "trigger_ota", "desc": "触发OTA升级(返回Web OTA地址和espota命令)", "params": {}},
    {"name": "web_ota_url", "desc": "获取Web OTA升级页面URL(浏览器上传固件.bin)", "params": {}},
    {"name": "reboot", "desc": "重启ESP32", "params": {}},
    {"name": "list_tools", "desc": "列出所有可用MCP工具(本工具)", "params": {}},
]


def handle_tool_call(method: str, params: Dict, state: SimState) -> Dict:
    """处理一个工具调用，返回 result dict。"""

    if method == "list_tools":
        return {"tools": TOOLS_LIST}

    elif method == "tune_fm":
        freq = params.get("freq_mhz", params.get("freq", 98.5))
        try:
            freq = float(freq)
        except (ValueError, TypeError):
            return {"error": "freq_mhz 必须是数字"}
        if not (64 <= freq <= 108):
            return {"error": f"freq_mhz 必须在 64-108 之间，当前 {freq}"}
        state.mode = 1
        state.freq = int(freq * 100)
        return {"ok": True, "freq_mhz": round(freq, 1), "mode": "FM"}

    elif method == "tune_am":
        freq = params.get("freq_khz", params.get("freq", 980))
        try:
            freq = int(freq)
        except (ValueError, TypeError):
            return {"error": "freq_khz 必须是整数"}
        if not (531 <= freq <= 1710):
            return {"error": f"freq_khz 必须在 531-1710 之间，当前 {freq}"}
        state.mode = 0
        state.freq = freq
        return {"ok": True, "freq_khz": freq, "mode": "AM"}

    elif method == "set_volume":
        vol = params.get("volume", 30)
        try:
            vol = int(vol)
        except (ValueError, TypeError):
            return {"error": "volume 必须是整数"}
        vol = max(0, min(63, vol))
        state.volume = vol
        return {"ok": True, "volume": vol}

    elif method == "get_status":
        return {
            "mode": state.mode,
            "mode_name": "FM" if state.mode == 1 else "AM",
            "freq": state.freq,
            "freq_display": f"{state.freq / 100:.1f} MHz" if state.mode == 1 else f"{state.freq} kHz",
            "rssi": state.calc_rssi(),
            "snr": state.calc_snr(),
            "volume": state.volume,
            "muted": state.muted,
            "recording": state.recording,
            "uptime": state.uptime,
        }

    elif method == "get_gps":
        return {
            "fix": state.gps_fix,
            "lat": round(state.gps_lat + random.uniform(-0.0001, 0.0001), 6),
            "lon": round(state.gps_lon + random.uniform(-0.0001, 0.0001), 6),
            "alt": round(state.gps_alt + random.uniform(-0.5, 0.5), 1),
            "sats": state.gps_sats + random.randint(-1, 1),
            "hdop": round(state.gps_hdop + random.uniform(-0.05, 0.05), 1),
        }

    elif method == "get_imu":
        # 加速度 ~1g (z 轴), 陀螺仪 ~0, 磁力计 ~地磁场
        return {
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
        }

    elif method == "start_record":
        if state.recording:
            return {"ok": False, "error": "已经在录音中"}
        state.recording = True
        state.rec_samples = 0
        return {"ok": True, "recording": True, "sample_rate": 48000, "format": "WAV/I2S"}

    elif method == "stop_record":
        if not state.recording:
            return {"ok": False, "error": "没有在录音"}
        state.recording = False
        duration_sec = state.rec_samples / 48000
        return {
            "ok": True,
            "samples": state.rec_samples,
            "duration_sec": round(duration_sec, 2),
            "sample_rate": 48000,
        }

    elif method == "get_version":
        return {
            "firmware": "v0.6-SIMULATION",
            "build": "20260916-sim",
            "board": "ai-sdr Mini (SIMULATION)",
            "mcu": "ESP32-S3-N8R8 (SIMULATED)",
            "sdr": "SI4732-A10-GSR (SIMULATED)",
            "imu": "BMI260 + TMAG5273 (SIMULATED)",
            "gps": "ATGM336H-5NR32-G (SIMULATED)",
            "usb_hub": "USB2514B (SIMULATED)",
            "ota_web": "http://192.168.4.1/update",
            "note": "这是模拟服务器返回的数据，非真实硬件",
        }

    elif method == "check_update":
        return {
            "current": "v0.6-SIMULATION",
            "build": "20260916-sim",
            "latest": "v0.6",
            "latest_url": "https://github.com/BI4MIB/MBDSDR/releases",
            "ota_web": "http://192.168.4.1/update",
            "update_available": False,
            "note": "模拟服务器，无真实更新检查",
        }

    elif method == "trigger_ota":
        return {
            "ok": True,
            "method": "web_ota",
            "url": "http://192.168.4.1/update",
            "note": "模拟服务器：请在浏览器打开上述地址，选择固件.bin上传",
        }

    elif method == "web_ota_url":
        return {
            "url": "http://192.168.4.1/update",
            "method": "browser_upload",
            "description": "浏览器打开此地址，选择固件.bin文件上传即可升级",
        }

    elif method == "reboot":
        return {"ok": True, "note": "模拟服务器：真实硬件会重启，模拟服务器不重启"}

    else:
        return {"error": f"未知工具: {method}"}


# ============================================================================
# WebSocket 服务器
# ============================================================================

async def handle_client(websocket, state: SimState, verbose: bool):
    """处理一个 WebSocket 客户端连接。"""
    client_addr = websocket.remote_address
    if verbose:
        print(f"[SIM] 客户端连接: {client_addr}", file=sys.stderr)

    try:
        async for message in websocket:
            try:
                request = json.loads(message)
            except json.JSONDecodeError:
                if verbose:
                    print(f"[SIM] JSON 解析错误: {message[:100]}", file=sys.stderr)
                continue

            method = request.get("method", "")
            req_id = request.get("id")
            params = request.get("params", {})

            if verbose:
                print(f"[SIM] <- {method} (id={req_id}, params={params})",
                      file=sys.stderr)

            # 处理工具调用
            result = handle_tool_call(method, params, state)

            # 构建 JSON-RPC 响应
            if "error" in result and method != "get_status":
                response = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32603, "message": result["error"]}
                }
            else:
                response = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": result
                }

            response_json = json.dumps(response, ensure_ascii=False)
            await websocket.send(response_json)

            if verbose:
                print(f"[SIM] -> {response_json[:120]}...", file=sys.stderr)

    except websockets.exceptions.ConnectionClosed:
        if verbose:
            print(f"[SIM] 客户端断开: {client_addr}", file=sys.stderr)
    except Exception as e:
        print(f"[SIM] 错误: {e}", file=sys.stderr)


async def tick_loop(state: SimState):
    """每秒更新模拟状态。"""
    while True:
        await asyncio.sleep(1)
        state.tick()


async def main():
    parser = argparse.ArgumentParser(
        description="MBDSDR ai-sdr Mini 硬件模拟服务器 (用于无硬件时测试 MCP 全链路)")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址 (默认 0.0.0.0)")
    parser.add_argument("--port", type=int, default=81, help="监听端口 (默认 81)")
    parser.add_argument("--lat", type=float, default=39.9042, help="模拟 GPS 纬度 (默认北京)")
    parser.add_argument("--lon", type=float, default=116.4074, help="模拟 GPS 经度 (默认北京)")
    parser.add_argument("-v", "--verbose", action="store_true", help="显示详细调试信息")
    args = parser.parse_args()

    state = SimState(lat=args.lat, lon=args.lon)

    print(f"========================================")
    print(f"  MBDSDR ai-sdr Mini 模拟服务器")
    print(f"  监听: ws://{args.host}:{args.port}")
    print(f"  模拟 GPS: {args.lat}, {args.lon}")
    print(f"  工具数: {len(TOOLS_LIST)}")
    print(f"  按 Ctrl+C 停止")
    print(f"========================================")
    print(f"")
    print(f"测试命令 (另一个终端):")
    print(f"  python3 mbdsdr_mcp_client.py --host localhost --port {args.port} list_tools")
    print(f"  python3 mbdsdr_mcp_client.py --host localhost --port {args.port} tune_fm --freq 98.5")
    print(f"  python3 mbdsdr_mcp_client.py --host localhost --port {args.port} get_status")
    print(f"")

    # 启动 tick 循环
    tick_task = asyncio.create_task(tick_loop(state))

    # 启动 WebSocket 服务器
    async with websockets.serve(
        lambda ws: handle_client(ws, state, args.verbose),
        args.host, args.port
    ):
        try:
            await asyncio.Future()  # 永久运行
        except KeyboardInterrupt:
            print("\n[SIM] 收到中断信号，停止服务器")
        finally:
            tick_task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[SIM] 服务器已停止")
