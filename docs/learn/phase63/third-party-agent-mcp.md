# 第三方 Agent MCP 接入（headless stdio JSON-RPC）

**模块**: `cpp/src/agentapi/mcp_server.{h,cpp}` + `cpp/src/agentapi/mbdsdr_mcp_main.cpp`
**二进制**: `mbdsdr_mcp`（无头，无 GUI）
**参考实现**: `mbdsdr_ai/core/mcp_server.py`（Python 版 stdio MCP）
**协议**: JSON-RPC 2.0 over stdin/stdout，**逐行 JSON**（非 Content-Length 分帧）

---

## 1. 为什么要这个服务

桌面端 AI 工具面原本只在 GUI 进程内被本地 LLM worker 调用。本模块把同一套
**声明式工具注册表**（`ai::toolDefs()`）原样暴露成 MCP，让任意外部 agent
（Claude Desktop / Cursor / 通用 MCP 客户端）通过 stdio 直接驱动接收机，
无需内嵌任何模型。

**零模型依赖**：`mbdsdr_mcp` 进程**不实例化 LLM 客户端、不读 API key、不发 chat
请求**。模型是外部 agent 自己的事——本进程只做工具发现与真实分发。链接
`mbdsdr_core` 是复用 `executeTool` 的必然（符号存在即可），运行路径永不触碰
`llm_client`/`llm_worker`。

---

## 2. 线协议

* 每行一条请求；每响应一行 JSON 对象 + `'\n'`，写后立即 `flush`。
* 解析失败的行：写 **stderr**（`[mcp] 无法解析 JSON: ...`），**stdout 不回任何东西**，
  与 Python 参考一致——错误流永不污染协议流。
* 通知（无 `id`，当前为 `notifications/initialized`）不回写 stdout。

### 2.1 方法表

| 方法 | 入参 | 结果 |
|------|------|------|
| `initialize` | — | `{"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"mbdsdr-core","version":"0.1.0"}}` |
| `notifications/initialized` | — | 无回复（通知） |
| `tools/list` | — | `{"tools":[{"name","description","inputSchema"},...]}` |
| `tools/call` | `{"name":..,"arguments":{..}}` | `{"content":[{"type":"text","text":<executeTool 返回的 JSON 串>}]}` |

* **工具表唯一事实源**：`ai::toolDefs()`（底层 `toolDefsFromSpecs(registeredToolSpecs())`）。
  `tools/list` 里没有第二份手写清单；`inputSchema` 直接取 `ToolDef.parameters`
  （已是完整 JSON Schema object），缺 `required` 数组时补空数组 `[]`。
* 未知方法 → JSON-RPC 错误 `{"code":-32601,"message":"未知方法: <m>"}`。

### 2.2 写工具手动 gate（MCP 侧强制）

`tools/call` 命中写类工具（`ai::isWriteTool(name)`，读自声明式 spec 表）时：

* `arguments.arm_manual == true` → 先从 arguments **移除** `arm_manual` 键，再真实
  `executeTool`（该键不泄漏给底层执行器）。
* 否则 → **工具不执行**，text 载荷为 `ai::gatedToolResult(name)`：
  `{"ok":false,"gated":true,"error":"手动模式：未执行 <name>"}`。

这把“写动作必须显式确认”的安全语义外推到外部 agent：agent 想调谐/录制/改模式，
必须自己解释为什么要带 `arm_manual:true`。

### 2.3 错误形态（诚实空态）

* 无后端/未连接：工具返回 `{"error":"not_connected",...}` / 各读工具返回空数组与
  `provenance:"未连接"`，**绝不编造硬件值、绝不崩溃**（对齐 ControlHub null-engine
  错误包络）。
* 未知工具名：text 载荷含 `{"error":"unknown_tool","tool":<name>,...}`。
* 引擎为 null（`handleRequestLine` 的 `engine` 形参）：走 `executeTool` 的 null-engine
  诚实错误路径。

---

## 3. headless 运行

```bash
# 二进制自己会在 main() 里 qputenv 这两项；容器/无显示环境也可显式给：
QT_QPA_PLATFORM=offscreen MBDSDR_NULL_AUDIO=1 ./mbdsdr_mcp
```

* 用 `QCoreApplication`（`SpectrumEngine` 是 `QThread`、`AudioOutput` 是普通 QObject，
  不需要 widget 事件循环）。构造引擎前强制 offscreen + null audio，使无硬件时落在
  诚实 `NullSource` + `NullAudioSink`，不打开真实音频/渲染设备。
* `--help` 打印用法（stdio 三方法、`arm_manual` 语义）。
* stdin 收到 EOF 即优雅退出。

---

## 4. 客户端配置示例

### 4.1 Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "mbdsdr": {
      "command": "/abs/path/to/mbdsdr_mcp",
      "args": [],
      "env": {
        "QT_QPA_PLATFORM": "offscreen",
        "MBDSDR_NULL_AUDIO": "1"
      }
    }
  }
}
```

### 4.2 Cursor (`.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "mbdsdr": {
      "command": "/abs/path/to/mbdsdr_mcp",
      "args": [],
      "env": {
        "QT_QPA_PLATFORM": "offscreen",
        "MBDSDR_NULL_AUDIO": "1"
      }
    }
  }
}
```

把 `/abs/path/to/mbdsdr_mcp` 换成本机构建产物绝对路径。本进程不需要任何 API key /
模型配置；模型由 Claude Desktop / Cursor 自己提供。

---

## 5. 一次手动往返（验证用）

```bash
printf '%s\n' \
'{"jsonrpc":"2.0","id":1,"method":"initialize"}' \
'{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
'{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_capabilities","arguments":{}}}' \
| ./mbdsdr_mcp
```

* 响应 1：`serverInfo.name=="mbdsdr-core"`。
* 响应 2：`tools` 数组长度 == `ai::toolDefs().size()`（当前 68，随工具增长）。
* 响应 3：text 解析后 `ok:true`、`provenance:"未连接"`（无硬件时诚实标注）。
