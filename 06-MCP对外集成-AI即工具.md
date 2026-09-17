# MBDSDR 的 MCP 对外集成：让 SDR 成为 AI 的标准工具

> 模块：`src/desktop/ai/mcp_stdio_server.py`（Python，桥接运行中的桌面端）
> 配置示例：`src/desktop/ai/mcp_server.example.json`
> 对称实现：C++ 核心 `src/mcp`（DSP 原语，66 工具，`mbdsdr mcp`）
> License: GPL-3.0-or-later

## 1. 定位：AI 定义无线电的"最后一公里"

"AI 定义无线电"不仅要求软件**内部**有自主智能体，还要求 SDR 能力能被**任意外部
AI**（Claude Desktop、Cursor、豆包、自研 Agent / AI IDE）像调用普通工具一样调用，
用于自动测量、辅助 coding、脚本化实验与远程协作。MCP（Model Context Protocol）
提供了厂商中立的标准工具协议，MBDSDR 据此把自身暴露为一个 MCP Server。

关键设计：**内部智能体与外部 AI 使用同一套工具语义**。桌面内 `AgentLoop` 通过
`ToolCallManager` 调用的 15 个高层工具，外部 AI Host 通过 MCP 调到的是完全相同的
实现（都走本地 HTTP → 同一后端），因此"自己用着不膈应"的工具，外部 AI 也同样好用
（推己及人 / 回旋镖原则）。

## 2. 两个互补的 MCP Server

| | C++ 核心 MCP | Python 桌面 MCP（本文） |
|---|---|---|
| 启动 | `mbdsdr mcp`（stdio） | `python3 -m ai.mcp_stdio_server`（stdio） |
| 面向 | DSP/编解码原语（CRC、Viterbi、调制、FFT 等 66 个） | 运行中桌面端的高层观测/控制/录制/视觉 |
| 依赖 | 无（独立二进制） | 需桌面软件在跑（本地 HTTP 7878） |
| 典型用户 | 写 DSP 算法的 AI IDE、离线计算 | 操控真实/模拟接收会话的 AI 助手 |

二者协议一致：JSON-RPC 2.0 over stdio，每行一个 JSON（NDJSON），
protocolVersion `2024-11-05`，方法 `initialize / notifications/initialized /
tools/list / tools/call / ping`。

## 3. 暴露的桌面工具（15）

- 频谱感知：`spectrum_get_view`、`spectrum_find_signals`、
  `spectrum_analyze_range`、`spectrum_suggest_next`（主动感知）
- 接收机控制：`get_status`、`ui_set_frequency`、`ui_set_gain`、
  `spectrum_set_view`（中心+跨度声明式缩放）、`spectrum_set_resolution`
- 多模态：`ui_screenshot`、`vision_inspect_spectrum`（视觉模型看频谱）
- 基带 IO：`ui_start_recording`（含定时/抽取/格式）、`ui_stop_recording`、
  `baseband_analyze_file`（离线文件分析）
- 元信息：`ui_capabilities`

每个工具的 `ToolSpec` 自动转换为标准 JSON Schema `inputSchema`
（type/default/enum/minimum/maximum/required），外部 AI 无需额外文档即可知道
合法参数名与取值范围——这对"弱模型不瞎编参数"同样关键。

## 4. 协议行为要点

- `initialize` 返回 protocolVersion、`capabilities.tools`、serverInfo；
- `tools/list` 返回全部工具及 inputSchema；
- `tools/call` 返回 MCP 标准 `{content:[{type:"text",text:<JSON结果>}], isError}`；
  后端工具 `ok:false` 时 `isError=true`，外部 AI 可据此自纠换路（与内部 Reflexion 同源）；
- 通知类消息（`notifications/*`、无 id）不回包；
- **stdout 只走协议，日志全部走 stderr**，不污染 JSON-RPC 流；
- 桥接地址由 `MBDSDR_DESKTOP_URL` 覆盖，视觉模型 key 走 `SILICONFLOW_API_KEY`。

## 5. 接入示例（AI Host mcpServers）

```json
{
  "mcpServers": {
    "mbdsdr": {
      "command": "python3",
      "args": ["-m", "ai.mcp_stdio_server"],
      "cwd": "/path/to/MBDSDR/src/desktop",
      "env": { "MBDSDR_DESKTOP_URL": "http://127.0.0.1:7878" }
    }
  }
}
```

接入后可对外部 AI 直接说："看看 433MHz 附近有什么信号，录 5 秒 cf32 再分析文件"，
外部 AI 即编排 find_signals → start_recording(duration_s=5) → stop →
baseband_analyze_file 完成任务。

## 6. 验证

- 协议自测 `python3 -m ai.mcp_stdio_server --selftest`：initialize、tools/list
  （工具数与 inputSchema 合法）、tools/call（content 文本、ok 判定）、未知方法
  -32601 错误码全部断言通过；
- 真实 stdio 管道测试：以 NDJSON 向 stdin 喂 initialize/tools/list，stdout 正确
  回包，15 工具、schema 含 default/minimum/maximum。

## 7. 论文创新点对应

1. **SDR-as-Tool 的标准化封装**：把接收机的"感知-决策-控制-录制"闭环以厂商中立协议
   暴露，使任意 LLM Agent 无需专有 SDK 即可驱动 SDR；
2. **内外同构**：内置自主智能体与外部 AI IDE 共享同一工具语义层与同一可靠性机制
   （两级确认检测、检查点回滚、失败自纠、数值优先视觉补充），可做"内置 7B vs
   外部强模型"的对照实验；
3. **可复现实验管线**：外部 AI 编排录制→落盘→离线分析，全过程产物（基带文件 +
   sidecar + 工具 JSON 结果）可留存复现。

## 8. 后续

- 增加 SSE/HTTP 传输（除 stdio 外支持远程 AI、网页端调用）；
- 工具粒度增加 `resources`（频谱快照、录制文件列表）与 `prompts`（常用任务模板）；
- 与 C++ 核心 MCP 做能力聚合（一个入口按工具名路由到 C++ 原语或桌面高层）。
