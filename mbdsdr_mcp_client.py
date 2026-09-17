#!/usr/bin/env python3
"""
MBDSDR MCP Client & stdio MCP Server Bridge
==============================================
将 ai-sdr Mini (ESP32-S3) 的 WebSocket MCP 服务包装为标准 stdio MCP server，
让任意 MCP 客户端 (Cursor / Claude Desktop / VS Code Copilot / 通用 agent)
都能直接调用 ai-sdr Mini 的 14 个硬件工具。

用法:
  1. 命令行直接调用:
     python3 mbdsdr_mcp_client.py --host 192.168.4.1 list_tools
     python3 mbdsdr_mcp_client.py --host 192.168.4.1 tune_fm --freq 98.5
     python3 mbdsdr_mcp_client.py --host 192.168.4.1 get_status

  2. 作为 stdio MCP server (供 Cursor / Claude Desktop 等调用):
     python3 mbdsdr_mcp_client.py --mcp --host 192.168.4.1

  3. 作为 Python 库:
     from mbdsdr_mcp_client import MBDSDRClient
     client = MBDSDRClient("192.168.4.1")
     client.connect()
     tools = client.list_tools()
     result = client.tune_fm(98.5)
     client.close()

依赖: pip install websocket-client
(标准库 json / sys / time / argparse / threading 无需安装)

MBDSDR Project - AI定义无线电 - 全开源 GPL-3.0 - 呼号 BI4MIB
"""

import json
import sys
import time
import argparse
import threading
from typing import Any, Dict, List, Optional, Callable

try:
    import websocket
except ImportError:
    print("错误: 缺少 websocket-client 库，请运行: pip install websocket-client",
          file=sys.stderr)
    sys.exit(1)


# ============================================================================
# MBDSDRClient: WebSocket JSON-RPC 2.0 客户端
# ============================================================================

class MBDSDRClient:
    """ai-sdr Mini WebSocket MCP 客户端。

    协议: JSON-RPC 2.0 over WebSocket (ws://host:81)
    工具: 14 个真实硬件工具 (见 list_tools 返回)
    """

    DEFAULT_PORT = 81
    DEFAULT_TIMEOUT = 10  # 秒

    def __init__(self, host: str = "192.168.4.1", port: int = DEFAULT_PORT,
                 timeout: int = DEFAULT_TIMEOUT, verbose: bool = False):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.verbose = verbose
        self.ws: Optional[websocket.WebSocket] = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._connected = False

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}"

    def connect(self) -> bool:
        """连接 ai-sdr Mini WebSocket 服务。"""
        if self._connected:
            return True
        try:
            if self.verbose:
                print(f"[MBDSDR] 连接 {self.url} ...", file=sys.stderr)
            self.ws = websocket.create_connection(
                self.url, timeout=self.timeout)
            self._connected = True
            if self.verbose:
                print(f"[MBDSDR] 已连接 {self.url}", file=sys.stderr)
            return True
        except Exception as e:
            print(f"[MBDSDR] 连接失败: {e}", file=sys.stderr)
            print(f"[MBDSDR] 请确认: 1) 已连接 WiFi 'MBDSDR-Mini' (密码 mbdsdr123); "
                  f"2) ai-sdr Mini 已上电; 3) 主机 IP 可 ping 通 {self.host}",
                  file=sys.stderr)
            return False

    def close(self):
        """关闭连接。"""
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
        self.ws = None
        self._connected = False

    def _next_id(self) -> int:
        with self._lock:
            self._request_id += 1
            return self._request_id

    def call(self, method: str, params: Optional[Dict] = None) -> Dict:
        """调用一个 MCP 工具 (JSON-RPC 2.0)。

        Args:
            method: 工具名 (如 "tune_fm", "get_status")
            params: 参数 dict (如 {"freq_mhz": 98.5})

        Returns:
            JSON-RPC 响应 dict (含 "result" 或 "error")

        Raises:
            ConnectionError: 未连接或连接断开
            TimeoutError: 响应超时
        """
        if not self._connected or not self.ws:
            raise ConnectionError("未连接 ai-sdr Mini，请先调用 connect()")

        req_id = self._next_id()
        request = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {}
        }
        request_json = json.dumps(request, ensure_ascii=False)

        if self.verbose:
            print(f"[MBDSDR] -> {request_json}", file=sys.stderr)

        try:
            self.ws.send(request_json)
        except Exception as e:
            self._connected = False
            raise ConnectionError(f"发送失败: {e}")

        # 接收响应 (匹配 id)
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            try:
                self.ws.settimeout(max(0.1, deadline - time.time()))
                raw = self.ws.recv()
                if not raw:
                    continue
                response = json.loads(raw)
                if self.verbose:
                    print(f"[MBDSDR] <- {json.dumps(response, ensure_ascii=False)}",
                          file=sys.stderr)
                # 匹配响应 id (固件可能推送其他消息，跳过)
                if response.get("id") == req_id:
                    return response
            except websocket.WebSocketTimeoutException:
                continue
            except Exception as e:
                self._connected = False
                raise ConnectionError(f"接收失败: {e}")

        raise TimeoutError(f"调用 {method} 超时 ({self.timeout}s)")

    # ------------------------------------------------------------------
    # 14 个工具的 Python 封装 (每个都有真实硬件调用，非空壳)
    # ------------------------------------------------------------------

    def list_tools(self) -> List[Dict]:
        """MCP 工具发现: 获取全部可用工具列表。"""
        resp = self.call("list_tools")
        return resp.get("result", [])

    def tune_fm(self, freq_mhz: float) -> Dict:
        """调谐 FM 广播。

        Args:
            freq_mhz: 频率 MHz (64-108)
        """
        resp = self.call("tune_fm", {"freq_mhz": freq_mhz})
        return resp.get("result", resp.get("error", {}))

    def tune_am(self, freq_khz: int) -> Dict:
        """调谐 AM/MW 广播。

        Args:
            freq_khz: 频率 kHz (531-1710)
        """
        resp = self.call("tune_am", {"freq_khz": freq_khz})
        return resp.get("result", resp.get("error", {}))

    def set_volume(self, volume: int) -> Dict:
        """设置音量 0-63。"""
        resp = self.call("set_volume", {"volume": volume})
        return resp.get("result", resp.get("error", {}))

    def get_status(self) -> Dict:
        """获取当前 SDR 状态 (mode/freq/rssi/snr)。"""
        resp = self.call("get_status")
        return resp.get("result", resp.get("error", {}))

    def get_gps(self) -> Dict:
        """获取 GPS/北斗定位 (fix/lat/lon/alt/sats/hdop)。"""
        resp = self.call("get_gps")
        return resp.get("result", resp.get("error", {}))

    def get_imu(self) -> Dict:
        """获取 9 轴姿态 (acc/gyr/mag/temp)。"""
        resp = self.call("get_imu")
        return resp.get("result", resp.get("error", {}))

    def start_record(self) -> Dict:
        """开始 I2S 基带录音 (WAV 头通过 WebSocket 推送)。"""
        resp = self.call("start_record")
        return resp.get("result", resp.get("error", {}))

    def stop_record(self) -> Dict:
        """停止录音，返回采样数。"""
        resp = self.call("stop_record")
        return resp.get("result", resp.get("error", {}))

    def get_version(self) -> Dict:
        """获取固件/硬件/MCU/SDR 芯片信息。"""
        resp = self.call("get_version")
        return resp.get("result", resp.get("error", {}))

    def check_update(self) -> Dict:
        """检查固件更新 (返回当前版本/GitHub release/OTA 地址)。"""
        resp = self.call("check_update")
        return resp.get("result", resp.get("error", {}))

    def trigger_ota(self) -> Dict:
        """触发 OTA 升级 (返回 Web OTA 地址和 espota 命令)。"""
        resp = self.call("trigger_ota")
        return resp.get("result", resp.get("error", {}))

    def web_ota_url(self) -> Dict:
        """获取 Web OTA 升级页面 URL。"""
        resp = self.call("web_ota_url")
        return resp.get("result", resp.get("error", {}))

    def reboot(self) -> Dict:
        """重启 ESP32 (调用后连接会断开)。"""
        try:
            resp = self.call("reboot")
            return resp.get("result", resp.get("error", {}))
        finally:
            self.close()

    # ------------------------------------------------------------------
    # 便利方法
    # ------------------------------------------------------------------

    def scan_fm(self, start: float = 87.5, end: float = 108.0,
                step: float = 0.1, dwell_ms: int = 50) -> List[Dict]:
        """FM 扫频: 在指定范围内步进调谐，记录每个频点的 RSSI。

        这是 AI "找电台" 技能的基础实现。

        Args:
            start: 起始频率 MHz
            end: 结束频率 MHz
            step: 步进 MHz
            dwell_ms: 每个频点停留 ms (等待 RSSI 稳定)

        Returns:
            列表 [{freq, rssi, snr}, ...]
        """
        results = []
        freq = start
        while freq <= end:
            self.tune_fm(round(freq, 1))
            time.sleep(dwell_ms / 1000.0)
            status = self.get_status()
            results.append({
                "freq_mhz": round(freq, 1),
                "rssi": status.get("rssi", -1),
                "snr": status.get("snr", -1),
            })
            freq += step
        return results

    def find_strongest_fm(self, start: float = 87.5, end: float = 108.0,
                           step: float = 0.1) -> Dict:
        """找信号最强的 FM 电台 (AI "找一个信号强的电台" 技能)。

        Returns:
            {freq_mhz, rssi, snr} 信号最强的频点
        """
        results = self.scan_fm(start, end, step)
        if not results:
            return {}
        strongest = max(results, key=lambda x: x.get("rssi", -1))
        # 调谐到最强频点
        self.tune_fm(strongest["freq_mhz"])
        return strongest


# ============================================================================
# stdio MCP Server: 将 WebSocket MCP 桥接到标准 MCP 协议
# ============================================================================
# 这是论文最大创新点的可运行实现: 任意 MCP 客户端 (Cursor / Claude
# Desktop / VS Code Copilot / 通用 agent) 通过 stdio 连接本桥接器,
# 本桥接器通过 WebSocket 调用 ai-sdr Mini 硬件, 实现 "AI IDE 直接
# 操控 SDR 硬件"。

class StdioMCPServer:
    """标准 stdio MCP server，桥接 ai-sdr Mini WebSocket。

    MCP 协议 (JSON-RPC 2.0 over stdio):
      - initialize: 初始化握手
      - tools/list: 列出工具 (转发 list_tools)
      - tools/call: 调用工具 (转发到 WebSocket)
      - notifications/initialized: 初始化完成通知
    """

    def __init__(self, host: str = "192.168.4.1", verbose: bool = False):
        self.client = MBDSDRClient(host=host, verbose=verbose)
        self.verbose = verbose
        self._tools_cache: Optional[List[Dict]] = None

    def _log(self, msg: str):
        if self.verbose:
            print(f"[MCP-stdio] {msg}", file=sys.stderr)

    def _read_message(self) -> Optional[Dict]:
        """从 stdin 读取一条 JSON-RPC 消息 (按行读取)。"""
        try:
            line = sys.stdin.readline()
            if not line:
                return None
            line = line.strip()
            if not line:
                return None
            return json.loads(line)
        except json.JSONDecodeError as e:
            self._log(f"JSON 解析错误: {e}")
            return None
        except Exception as e:
            self._log(f"读取错误: {e}")
            return None

    def _write_message(self, msg: Dict):
        """向 stdout 写入一条 JSON-RPC 消息。"""
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def _handle_initialize(self, request: Dict) -> Dict:
        """MCP initialize 握手。"""
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False}
                },
                "serverInfo": {
                    "name": "mbdsdr-mini",
                    "version": "0.6.0"
                }
            }
        }

    def _handle_tools_list(self, request: Dict) -> Dict:
        """MCP tools/list: 列出可用工具。

        从 ai-sdr Mini 获取工具列表，转换为 MCP 标准工具 schema。
        """
        if not self.client.connect():
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {
                    "code": -32603,
                    "message": "无法连接 ai-sdr Mini 硬件"
                }
            }

        try:
            if self._tools_cache is None:
                self._tools_cache = self.client.list_tools()
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {"code": -32603, "message": f"获取工具列表失败: {e}"}
            }

        # 转换为 MCP 标准工具 schema
        mcp_tools = []
        for tool in self._tools_cache:
            name = tool.get("name", "")
            desc = tool.get("desc", "")
            params = tool.get("params", {})

            # 构建 JSON Schema properties
            properties = {}
            required = []
            for pname, pdesc in params.items():
                # 简单类型推断
                ptype = "string"
                if any(kw in pdesc.lower() for kw in ["int", "float", "khz", "mhz", "volume"]):
                    ptype = "number"
                properties[pname] = {
                    "type": ptype,
                    "description": pdesc
                }
                required.append(pname)

            mcp_tools.append({
                "name": name,
                "description": desc,
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    "required": required
                }
            })

        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {"tools": mcp_tools}
        }

    def _handle_tools_call(self, request: Dict) -> Dict:
        """MCP tools/call: 调用工具。

        转发到 ai-sdr Mini WebSocket，返回 MCP 标准响应。
        """
        params = request.get("params", {})
        name = params.get("name", "")
        arguments = params.get("arguments", {})

        if not self.client.connect():
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {
                    "content": [{"type": "text", "text": "错误: 无法连接 ai-sdr Mini 硬件"}],
                    "isError": True
                }
            }

        try:
            resp = self.client.call(name, arguments)
            if "error" in resp:
                error_msg = resp["error"].get("message", str(resp["error"]))
                return {
                    "jsonrpc": "2.0",
                    "id": request.get("id"),
                    "result": {
                        "content": [{"type": "text", "text": f"工具调用错误: {error_msg}"}],
                        "isError": True
                    }
                }
            result_text = json.dumps(resp.get("result", {}), ensure_ascii=False, indent=2)
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {
                    "content": [{"type": "text", "text": result_text}],
                    "isError": False
                }
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {
                    "content": [{"type": "text", "text": f"调用异常: {e}"}],
                    "isError": True
                }
            }

    def run(self):
        """运行 stdio MCP server 主循环。"""
        self._log("stdio MCP server 启动 (桥接 ai-sdr Mini WebSocket)")
        self._log(f"目标硬件: ws://{self.client.host}:{self.client.port}")
        self._log("等待 MCP 客户端连接 (stdin/stdout)...")

        while True:
            request = self._read_message()
            if request is None:
                break

            method = request.get("method", "")
            self._log(f"收到请求: {method} (id={request.get('id')})")

            if method == "initialize":
                response = self._handle_initialize(request)
            elif method == "tools/list":
                response = self._handle_tools_list(request)
            elif method == "tools/call":
                response = self._handle_tools_call(request)
            elif method == "notifications/initialized":
                # MCP 初始化完成通知，无需响应
                self._log("MCP 客户端初始化完成")
                continue
            elif method == "ping":
                response = {"jsonrpc": "2.0", "id": request.get("id"), "result": {}}
            else:
                response = {
                    "jsonrpc": "2.0",
                    "id": request.get("id"),
                    "error": {
                        "code": -32601,
                        "message": f"未知方法: {method}"
                    }
                }

            self._write_message(response)

        self._log("stdio MCP server 退出")
        self.client.close()


# ============================================================================
# 命令行接口
# ============================================================================

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MBDSDR ai-sdr Mini MCP 客户端 & stdio MCP 桥接器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 列出工具
  python3 mbdsdr_mcp_client.py --host 192.168.4.1 list_tools

  # 调谐 FM 98.5 MHz
  python3 mbdsdr_mcp_client.py --host 192.168.4.1 tune_fm --freq 98.5

  # 获取状态
  python3 mbdsdr_mcp_client.py --host 192.168.4.1 get_status

  # FM 扫频找最强电台
  python3 mbdsdr_mcp_client.py --host 192.168.4.1 scan_fm --start 87.5 --end 108 --step 0.1

  # 作为 stdio MCP server (供 Cursor / Claude Desktop 调用)
  python3 mbdsdr_mcp_client.py --mcp --host 192.168.4.1
        """
    )
    parser.add_argument("--host", default="192.168.4.1",
                        help="ai-sdr Mini IP 地址 (默认 192.168.4.1)")
    parser.add_argument("--port", type=int, default=81,
                        help="WebSocket 端口 (默认 81)")
    parser.add_argument("--timeout", type=int, default=10,
                        help="调用超时秒数 (默认 10)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="显示详细调试信息")
    parser.add_argument("--mcp", action="store_true",
                        help="作为 stdio MCP server 运行 (供 Cursor/Claude/VS Code 调用)")

    # 子命令: 工具名
    subparsers = parser.add_subparsers(dest="command", help="可用工具命令")

    # 无参数工具
    for cmd in ["list_tools", "get_status", "get_gps", "get_imu",
                "start_record", "stop_record", "get_version", "check_update",
                "trigger_ota", "web_ota_url", "reboot"]:
        subparsers.add_parser(cmd, help=f"调用 {cmd} 工具")

    # 有参数工具
    p_tune_fm = subparsers.add_parser("tune_fm", help="调谐 FM 广播")
    p_tune_fm.add_argument("--freq", type=float, required=True,
                            help="频率 MHz (64-108)")

    p_tune_am = subparsers.add_parser("tune_am", help="调谐 AM/MW 广播")
    p_tune_am.add_argument("--freq", type=int, required=True,
                            help="频率 kHz (531-1710)")

    p_vol = subparsers.add_parser("set_volume", help="设置音量 0-63")
    p_vol.add_argument("--volume", type=int, required=True, help="音量 0-63")

    p_scan = subparsers.add_parser("scan_fm", help="FM 扫频 (AI 找电台基础)")
    p_scan.add_argument("--start", type=float, default=87.5, help="起始 MHz")
    p_scan.add_argument("--end", type=float, default=108.0, help="结束 MHz")
    p_scan.add_argument("--step", type=float, default=0.1, help="步进 MHz")
    p_scan.add_argument("--dwell", type=int, default=50, help="每频点停留 ms")

    p_find = subparsers.add_parser("find_strongest_fm", help="找信号最强的 FM 电台")
    p_find.add_argument("--start", type=float, default=87.5)
    p_find.add_argument("--end", type=float, default=108.0)
    p_find.add_argument("--step", type=float, default=0.1)

    return parser


def main():
    parser = build_arg_parser()
    args = parser.parse_args()

    # stdio MCP server 模式
    if args.mcp:
        server = StdioMCPServer(host=args.host, verbose=args.verbose)
        server.run()
        return

    # 命令行工具调用模式
    if not args.command:
        parser.print_help()
        return

    client = MBDSDRClient(host=args.host, port=args.port,
                           timeout=args.timeout, verbose=args.verbose)

    if not client.connect():
        sys.exit(1)

    try:
        cmd = args.command

        if cmd == "list_tools":
            tools = client.list_tools()
            print(json.dumps(tools, ensure_ascii=False, indent=2))
            print(f"\n共 {len(tools)} 个工具")

        elif cmd == "tune_fm":
            result = client.tune_fm(args.freq)
            print(json.dumps(result, ensure_ascii=False, indent=2))

        elif cmd == "tune_am":
            result = client.tune_am(args.freq)
            print(json.dumps(result, ensure_ascii=False, indent=2))

        elif cmd == "set_volume":
            result = client.set_volume(args.volume)
            print(json.dumps(result, ensure_ascii=False, indent=2))

        elif cmd == "get_status":
            result = client.get_status()
            print(json.dumps(result, ensure_ascii=False, indent=2))

        elif cmd == "get_gps":
            result = client.get_gps()
            print(json.dumps(result, ensure_ascii=False, indent=2))

        elif cmd == "get_imu":
            result = client.get_imu()
            print(json.dumps(result, ensure_ascii=False, indent=2))

        elif cmd == "start_record":
            result = client.start_record()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            print("录音已启动，WAV 数据通过 WebSocket 推送 (电脑端存盘)")

        elif cmd == "stop_record":
            result = client.stop_record()
            print(json.dumps(result, ensure_ascii=False, indent=2))

        elif cmd == "get_version":
            result = client.get_version()
            print(json.dumps(result, ensure_ascii=False, indent=2))

        elif cmd == "check_update":
            result = client.check_update()
            print(json.dumps(result, ensure_ascii=False, indent=2))

        elif cmd == "trigger_ota":
            result = client.trigger_ota()
            print(json.dumps(result, ensure_ascii=False, indent=2))

        elif cmd == "web_ota_url":
            result = client.web_ota_url()
            print(json.dumps(result, ensure_ascii=False, indent=2))

        elif cmd == "reboot":
            result = client.reboot()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            print("ESP32 正在重启，连接已断开")

        elif cmd == "scan_fm":
            print(f"FM 扫频: {args.start}-{args.end} MHz, 步进 {args.step} MHz...")
            results = client.scan_fm(args.start, args.end, args.step, args.dwell)
            print(json.dumps(results, ensure_ascii=False, indent=2))
            # 找最强的 5 个
            sorted_results = sorted(results, key=lambda x: x.get("rssi", -1),
                                    reverse=True)
            print("\n信号最强的 5 个频点:")
            for r in sorted_results[:5]:
                print(f"  {r['freq_mhz']:6.1f} MHz  RSSI={r['rssi']:4d}  SNR={r['snr']}")

        elif cmd == "find_strongest_fm":
            print(f"在 {args.start}-{args.end} MHz 找信号最强的电台...")
            strongest = client.find_strongest_fm(args.start, args.end, args.step)
            print(json.dumps(strongest, ensure_ascii=False, indent=2))
            if strongest:
                print(f"\n已调谐到最强电台: {strongest['freq_mhz']} MHz "
                      f"(RSSI={strongest['rssi']})")

        else:
            print(f"未知命令: {cmd}", file=sys.stderr)
            sys.exit(1)

    except (ConnectionError, TimeoutError) as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n用户中断", file=sys.stderr)
    finally:
        client.close()


if __name__ == "__main__":
    main()
