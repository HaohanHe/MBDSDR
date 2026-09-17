#!/usr/bin/env python3
"""
MBDSDR AI 内核 - MCP stdio 服务器
====================================
将 MBDSDR AI 内核的 128 个工具暴露为标准 MCP (Model Context Protocol) 服务，
让任意 MCP 客户端 (Cursor / Claude Desktop / VS Code Copilot / 通用 agent / AI IDE)
都能直接调用 MBDSDR 的全部功能。

这是论文核心创新点的可运行实现: AI 定义无线电作为工具被任意通用 agent 调用。

用法:
  1. 作为 stdio MCP server (供 Cursor / Claude Desktop 等调用):
     python3 mbdsdr_ai_mcp_server.py

  2. 命令行直接调用 (测试用):
     python3 mbdsdr_ai_mcp_server.py --cli list_tools
     python3 mbdsdr_ai_mcp_server.py --cli call_tool sdr_set_frequency '{"frequency_hz": 98500000}'
     python3 mbdsdr_ai_mcp_server.py --cli call_tool sdr_spectrum_analyze '{"fft_size": 512}'

  3. 作为 Python 库:
     from mbdsdr_ai_mcp_server import MBSDRAIMCPServer
     server = MBSDRAIMCPServer()
     tools = server.list_tools()
     result = server.call_tool("sdr_set_frequency", {"frequency_hz": 98500000})

MCP 协议 (JSON-RPC 2.0 over stdio):
  - initialize: 初始化握手
  - tools/list: 列出全部 128 个工具
  - tools/call: 调用工具
  - notifications/initialized: 初始化完成通知

配置:
  - API Key: 环境变量 MBDSDR_API_KEY 或 --api-key 参数
  - 模型: 环境变量 MBDSDR_MODEL 或 --model 参数 (默认 Qwen/Qwen3.6-35B-A3B)
  - API Base: 环境变量 MBDSDR_API_BASE (默认 https://api.siliconflow.cn/v1)

依赖: pip install numpy (MBDSDR AI 内核依赖)
(标准库 json / sys / time / argparse / threading 无需安装)

MBDSDR Project - AI定义无线电 - 全开源 GPL-3.0 - 呼号 BI4MIB
"""

import json
import os
import sys
import time
import argparse
import threading
from typing import Any, Dict, List, Optional, Callable

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mbdsdr_ai import MBDSDRAgent, AgentConfig


# ============================================================================
# MBSDRAIMCPServer: MCP stdio 服务器核心
# ============================================================================


class MBSDRAIMCPServer:
    """
    MBDSDR AI 内核 MCP stdio 服务器。

    将 MBDSDRAgent 的 128 个工具暴露为标准 MCP 服务。
    任意 MCP 客户端 (Cursor / Claude Desktop / 通用 agent) 都能调用。
    """

    def __init__(
        self,
        api_key: str = None,
        model: str = "Qwen/Qwen3.6-35B-A3B",
        api_base: str = "https://api.siliconflow.cn/v1",
        verbose: bool = False,
    ):
        """
        初始化 MCP 服务器。

        参数:
            api_key: 硅基流动 API Key (可选，不填则工具调用不依赖 LLM)
            model: 默认模型
            api_base: API 基础 URL
            verbose: 是否输出调试信息到 stderr
        """
        self.verbose = verbose
        self._log("初始化 MBDSDR AI 内核...")

        # 初始化 Agent (即使没有 API Key 也能调用工具，只是不能做 LLM 对话)
        config = AgentConfig(
            api_key=api_key or "sk-mcp-server-no-key",
            model=model,
            base_url=api_base,
        )
        self.agent = MBDSDRAgent(config)
        self.tool_registry = self.agent.tool_registry

        # 获取工具列表
        self._tools = self.tool_registry.list_tools()
        self._log(f"MBDSDR AI 内核初始化完成，共 {len(self._tools)} 个工具可用")

        # MCP 协议状态
        self._initialized = False
        self._request_id = 0

    def _log(self, msg: str):
        """输出调试信息到 stderr (不干扰 stdio JSON-RPC)。"""
        if self.verbose:
            print(f"[MBDSDR-MCP] {msg}", file=sys.stderr)

    # ── 工具发现 ──────────────────────────────────────────

    def list_tools(self) -> List[Dict[str, Any]]:
        """
        列出全部可用工具 (MCP 标准格式)。

        返回 MCP tools/list 响应的 tools 数组。
        """
        mcp_tools = []
        for tool in self._tools:
            mcp_tool = {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "inputSchema": tool.get("parameters", {
                    "type": "object",
                    "properties": {},
                }),
            }
            # 添加类别作为 annotation
            if tool.get("category"):
                mcp_tool["annotations"] = {
                    "title": f"[{tool['category']}] {tool['name']}",
                }
            mcp_tools.append(mcp_tool)
        return mcp_tools

    # ── 工具调用 ──────────────────────────────────────────

    def call_tool(self, name: str, arguments: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        调用一个工具。

        参数:
            name: 工具名称
            arguments: 工具参数

        返回:
            MCP tools/call 响应 (含 content 数组)
        """
        if arguments is None:
            arguments = {}

        self._log(f"调用工具: {name}({json.dumps(arguments, ensure_ascii=False)[:100]})")

        try:
            result = self.tool_registry.call(name, arguments)

            if result.success:
                # 成功: 返回文本内容
                content_text = result.content if isinstance(result.content, str) else json.dumps(result.content, ensure_ascii=False, indent=2)
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": content_text,
                        }
                    ],
                    "isError": False,
                }
            else:
                # 失败: 返回错误信息
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"工具调用失败: {result.content}",
                        }
                    ],
                    "isError": True,
                }

        except Exception as e:
            self._log(f"工具调用异常: {name} - {e}")
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"工具调用异常: {type(e).__name__}: {e}",
                    }
                ],
                "isError": True,
            }

    # ── MCP 协议处理 ──────────────────────────────────────

    def _handle_initialize(self, request: Dict) -> Dict:
        """MCP initialize 握手。"""
        self._initialized = True
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {
                    "listChanged": False,
                },
            },
            "serverInfo": {
                "name": "mbdsdr-ai",
                "version": "0.8.0",
            },
        }

    def _handle_tools_list(self, request: Dict) -> Dict:
        """MCP tools/list: 列出全部工具。"""
        tools = self.list_tools()
        return {"tools": tools}

    def _handle_tools_call(self, request: Dict) -> Dict:
        """MCP tools/call: 调用工具。"""
        params = request.get("params", {})
        name = params.get("name", "")
        arguments = params.get("arguments", {})
        return self.call_tool(name, arguments)

    def handle_request(self, request: Dict) -> Optional[Dict]:
        """
        处理一条 JSON-RPC 请求。

        返回响应 dict，或 None (通知消息无响应)。
        """
        method = request.get("method", "")
        request_id = request.get("id")

        # 通知消息 (无 id) 不需要响应
        if request_id is None:
            if method == "notifications/initialized":
                self._log("MCP 客户端初始化完成通知")
            return None

        # 方法路由
        if method == "initialize":
            result = self._handle_initialize(request)
        elif method == "tools/list":
            result = self._handle_tools_list(request)
        elif method == "tools/call":
            result = self._handle_tools_call(request)
        elif method == "ping":
            result = {}
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}",
                },
            }

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result,
        }

    # ── stdio 循环 ────────────────────────────────────────

    def run_stdio(self):
        """
        运行 stdio MCP 服务器主循环。

        从 stdin 读取 JSON-RPC 请求，处理后写入 stdout。
        这是供 Cursor / Claude Desktop 等 MCP 客户端调用的模式。
        """
        self._log("MBDSDR AI MCP stdio 服务器启动")
        self._log(f"可用工具: {len(self._tools)} 个")
        self._log("等待 MCP 客户端连接...")

        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue

            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                self._log(f"JSON 解析失败: {line[:100]}")
                continue

            response = self.handle_request(request)
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)

        self._log("MCP 服务器退出")


# ============================================================================
# CLI 入口
# ============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="MBDSDR AI 内核 MCP stdio 服务器 - 暴露 128 个工具给任意通用 agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 作为 stdio MCP server (供 Cursor / Claude Desktop 调用)
  python3 mbdsdr_ai_mcp_server.py

  # 命令行测试: 列出工具
  python3 mbdsdr_ai_mcp_server.py --cli list_tools

  # 命令行测试: 调用工具
  python3 mbdsdr_ai_mcp_server.py --cli call_tool sdr_set_frequency '{"frequency_hz": 98500000}'
  python3 mbdsdr_ai_mcp_server.py --cli call_tool sdr_spectrum_analyze '{"fft_size": 512}'

  # 带 API Key (启用 LLM 对话能力)
  MBDSDR_API_KEY=sk-xxx python3 mbdsdr_ai_mcp_server.py
        """,
    )
    parser.add_argument("--cli", nargs="+", help="命令行模式: list_tools | call_tool <name> [args_json]")
    parser.add_argument("--api-key", default=None, help="硅基流动 API Key (或环境变量 MBDSDR_API_KEY)")
    parser.add_argument("--model", default="Qwen/Qwen3.6-35B-A3B", help="默认模型")
    parser.add_argument("--api-base", default="https://api.siliconflow.cn/v1", help="API 基础 URL")
    parser.add_argument("--verbose", "-v", action="store_true", help="输出调试信息")

    args = parser.parse_args()

    # 从环境变量获取 API Key
    api_key = args.api_key or os.environ.get("MBDSDR_API_KEY")

    # 创建服务器
    server = MBSDRAIMCPServer(
        api_key=api_key,
        model=args.model,
        api_base=args.api_base,
        verbose=args.verbose,
    )

    # CLI 模式
    if args.cli:
        cmd = args.cli[0]

        if cmd == "list_tools":
            tools = server.list_tools()
            print(f"MBDSDR AI 内核 - 可用工具 ({len(tools)} 个):")
            print("=" * 80)
            # 按类别分组
            categories = {}
            for tool in server._tools:
                cat = tool.get("category", "unknown")
                categories.setdefault(cat, []).append(tool)
            for cat, tools_in_cat in sorted(categories.items()):
                print(f"\n[{cat}] ({len(tools_in_cat)} 个)")
                for tool in tools_in_cat:
                    print(f"  - {tool['name']}: {tool.get('description', '')[:60]}")
            print("\n" + "=" * 80)

        elif cmd == "call_tool":
            if len(args.cli) < 2:
                print("错误: call_tool 需要指定工具名称", file=sys.stderr)
                sys.exit(1)
            tool_name = args.cli[1]
            arguments = {}
            if len(args.cli) >= 3:
                try:
                    arguments = json.loads(args.cli[2])
                except json.JSONDecodeError:
                    print(f"错误: 参数 JSON 解析失败: {args.cli[2]}", file=sys.stderr)
                    sys.exit(1)

            result = server.call_tool(tool_name, arguments)
            print(f"工具: {tool_name}")
            print(f"参数: {json.dumps(arguments, ensure_ascii=False)}")
            print(f"结果:")
            for content in result.get("content", []):
                print(content.get("text", ""))
            if result.get("isError"):
                print("(调用失败)", file=sys.stderr)
                sys.exit(1)

        else:
            print(f"未知命令: {cmd}", file=sys.stderr)
            print("可用命令: list_tools, call_tool", file=sys.stderr)
            sys.exit(1)

    else:
        # stdio MCP server 模式
        server.run_stdio()


if __name__ == "__main__":
    main()
