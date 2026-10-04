# Phase31 Wave2 落地映射（Agent 层改造，C++）

> 根：`cpp/src/ai/`。把 Wave1 四篇冻结结论（`context-compaction.md` / `multi-session.md` /
> `streaming.md` / `tool-calling-boundary.md`）落到代码。无结构改动、不新增工具、35 工具
> 清单与写门测试为冻结契约（`test_tool_registry.cpp` 未改）。

## 学习结论 → 落地映射表

| Wave1 结论（笔记 / 行号） | 落地位置 | 测试 |
|---|---|---|
| **A1 前置修剪**：单条大 tool 输出先轻修剪，不触发全量摘要（context-compaction §6.1，OpenAI ToolOutputTrimmer） | `ai/ai_context.cpp:36` `truncateLargeToolOutputs()`；阈值具名 `core/tokens.h:897` `kAiToolOutputMaxChars=1200` / `kAiToolOutputPreviewChars=400`；在 `compactContext` 预算检查前应用 `ai_context.cpp:93` | `test_ai_session.cpp toolOutputPreTrimmedBeforeBudgetCheck` |
| **A1 budget=窗口 75%**：换大窗口模型不再过早压缩（§6.2，MetaGPT threshold=0.8） | `core/tokens.h:890` `kAiDefaultContextWindowTokens=32768` / `kAiContextBudgetRatio=0.75`；`ai_context.cpp:31 defaultContextBudgetTokens()`、`:88` 比例换算；`CompactOptions::budgetTokens<=0` 时自动 | `test_ai_session.cpp budgetIsWindowRatioNotHardcoded` |
| **A1 user 保留**：summary 保留全部 user ask，不只前 3（§6.3，OpenAI 排除 user） | `ai_context.cpp:65` 去掉 `<3` 上限，全部 ask 一行式 | `test_ai_session.cpp allUserAsksPreservedInRuleSummary` |
| **A2 kind/ts 元数据**：区分普通对话与工具/错误事件（multi-session §4.1.1） | `ai/ai_session_store.h` `SessionMessage{kind,ts}`；加载回退 `ai_session_store.cpp:122-124`（无 kind=旧行，kind==role）；落盘 `:211-212` | `test_ai_session.cpp legacySessionLoadsWithKindFallback` / `kindAndTsRoundTrip` |
| **A2 index version 迁移钩子**（§4.1.2） | `ai_session_store.h kIndexVersion=1`；写 `:180`、读迁移 `:229`（缺 version 视同 v1，不报错不丢数据） | `test_ai_session.cpp indexWritesVersion` |
| **A2 incomplete 标记**：流式写一半崩溃诚实显示（§4.1.3） | `setIncomplete()/isIncomplete()` `ai_session_store.cpp:147`；index+session 双写；新 user 消息自动清除（appendMessage） | `test_ai_session.cpp incompleteMarkerSurvivesAndClears` |
| **A3 出错保留已流式内容**（streaming §4，G1） | `llm_client.cpp:112 errorResponseWithPartial()` 纯接缝；QNAM 错误分支 `:188` `acc.toResponse()` 保留片段再附错误，不再丢半句话 | `test_ai_tool_loop.cpp errorResponseKeepsPartialContent` |
| **A3 错误不落盘污染会话**（§5.1，G2） | 新增独立信号 `LLMWorker::chatError`（替代 `chatFinished`）；`llm_worker.cpp:172`；`agent.cpp:119 onChatError` 只转发不 `history_.append` | `test_ai_tool_loop.cpp chatErrorSignalNotFinished` |
| **A4 工具文档全量**：35 工具 Agent 可见文档，固定字段（tool-calling §4.1） | `tool_schema.cpp:730 generateToolDocumentation()`（name/description/schema/read-write/错误示例）；快照 `docs/learn/phase31/agent-tool-documentation.md` | `test_tool_schema.cpp generateToolDocumentationCoversAllTools` |
| **A4 错误恢复：写门拒绝回注**（§4.2 层 2） | 已存在的 gated 结果以 `role=tool` 回注（worker 循环），本次补测试钉死 | `test_ai_tool_loop.cpp manualGatedWriteFedBackAsToolMessage` |
| **A4 坏参回注自纠**（层 1） | 已存在（validateArguments→errorJson→role=tool），冻结契约 | `test_ai_tool_loop.cpp outOfRangeArgs_notExecuted`（未改） |
| **A4 429/503 退避预算**（层 5） | `llm_worker.cpp:17 classifyLlmError()` / `:40 backoffDelayMs()`；`tokens.h:904 kAiMaxTransientRetries=3`；worker 有界重试 `:157-168`；可注入 sleep 接缝 `setBackoffSleepForTests` | `errorClassTerminalVsRetryable` / `backoffDelayExponentialAndClamped` |
| **A4 400/401/无 key 终态如实**（层 4） | `classifyLlmError` 判为 Terminal 不重试；无 key 诚实 PENDING，不 mock（`chatError` 发原文） | `chatErrorSignalNotFinished` |
| **A4 看门狗 max rounds**（层 7） | 已存在 `kAiMaxToolRounds=8`，冻结契约 | `test_ai_tool_loop.cpp roundCap_forceStops`（未改） |

## 移动端（A5）—— 诚实 YAGNI

**本轮不做任何移动端 AI 代码。** 回补条件（明确）：**当移动端 AI 聊天页开始独立重构**（把
`control_ui` 的 QML 聊天面板换成原生移动端会话组件、或接入独立的 AI 会话存储）时，再回补
A2 的 `kind/ts/incomplete` 在移动端模型层的映射。理由：当前移动端仅复用桌面 `control_ui`，
无独立 AI 会话域；现在写移动端适配是无消费方的猜测式抽象。本映射即回补锚点。

## 未解决项（诚实，禁虚报）

1. **incomplete 的生产接线**：store 层已提供 `setIncomplete/isIncomplete` 原语并测好，但
   流式写入的启停在 UI 层（`ui/main_window`，红线禁碰）。**回补点**：`main_window` 在
   `partialReady` 起始处 `setIncomplete(id,true)`、`responseReady/chatError` 处 `(…,false)`。
2. **429/503 的 HTTP 状态分类**：`classifyLlmError` 基于错误字符串启发式（QNAM 不直接暴露
   HTTP status code）。真·按状态码分类需 transport 层透传 status（结构性改动，本轮未做）。
3. **真实网络重试端到端**：有界重试循环已在 worker 内接好，但离线测试经注入 transport 触发
   不到网络错误；重试路径由纯函数（分类/退避）+ 无 key 终态测试钉住，真实 socket 重试留待 e2e。

## 红线自查
- 改动域：`cpp/src/ai/{ai_context,ai_session_store,llm_client,llm_worker,agent,tool_schema}.{h,cpp}`
  + `cpp/src/core/tokens.h` + `cpp/tests/{test_ai_session,test_tool_schema,test_ai_tool_loop}.cpp`
  + `cpp/CMakeLists.txt`（仅给 test_ai_session 加 Qt6::Gui）+ 本目录文档。未碰 ui/control/dsp/mobile。
- 未新增工具；35 清单/写门冻结测试未改。MIT SPDX 保留。未 commit/push。
