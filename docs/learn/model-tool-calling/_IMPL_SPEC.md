# MBDSDR 第二阶段实现规格：真 Function Calling 循环（v1，2026-10-01）

> 依据：docs/learn/model-tool-calling-boundaries.md + 16 篇学习笔记（同目录）。
> 目标：C++ src/ai/ 与 Flutter mobile/lib 升级为真 function calling 循环；
> 无 key 时用确定性 mock（transport 注入）验证，禁真调 API、禁造假。
> 验收：cpp ctest 保持 79/79 全绿 + 新增测试；flutter analyze 0 issue + 209 全绿 + 新增；git 增量 push（禁 add -A）。

## 0. 现状要点（勘察结论，子 agent 不必重查）

- C++：`src/ai/llm_client.{h,cpp}`（OpenAI 硬编码、SSE 已有 content+tool_calls 拼接、30s 超时硬编码、无 reasoning/校验/finish_reason/thinking/协议层）；
  `llm_worker.cpp`（已有 3 轮工具循环，硬编码 3；执行前无校验；无 reasoning 回传）；
  `agent_tools.cpp`（7 个硬编码 ToolDef：tune_frequency/set_mode/start_recording/stop_recording/scan_band/set_bandwidth/get_status；executeTool 直接读 args，无校验）；
  `ai_context.cpp`（上下文预算压缩，仅循环前调用）。
- 常量中心：`src/core/tokens.h`（硬件范围 kFreqMinHz=24e6/kFreqMaxHz=1700e6/kFreqStepHz、kGainMinDb=0/kGainMaxDb=49.6、kSampleRatesHz、kTaskMaxSteps=8）；带宽档位在 `src/core/bandwidth_preset.h`（kBwNfmHz=12500/kBwWfmHz=200000/kBwAmHz=9000/kBwSsbHz=2400/kBwCwHz=500/kBwDigitalHz=12000/kBwAdsbHz=2000000/kBwFallbackHz=12500）。
- Flutter：`mobile/lib/services/ai_client.dart`（AiClient 完整流式循环、6 轮上限硬编码、ToolCallAccumulator 按 index、无 reasoning/校验/thinking/协议层）；`mobile/lib/app/ai_tools.dart`（5 个 AiTool，范围只写在 description，未用 AppTokens 的 min/max/enum）；`mobile/lib/models/chat_message.dart`（ChatMessage.toApiJson）；`mobile/lib/app/tokens.dart`（AppTokens：freqMinHz/freqMaxHz/gainMinDb/gainMaxDb/sampleRatesHz）。
- 现有 AI 测试（test_ai_function_calling / test_ai_integration）无 key 时 QSKIP，不会 FAIL——新测试不得依赖真实网络。

## 1. 模块划分与文件所有权（并行互斥，禁越界）

| 模块 | 新建文件（唯一所有权） | 允许触碰的现有文件 | 不许碰 |
|---|---|---|---|
| M1 Schema 生成器 | `cpp/src/ai/tool_schema.{h,cpp}`、`cpp/tests/test_tool_schema.cpp` | `cpp/src/core/tokens.h`（仅追加：工具 Schema 专用常量，锚点在"Hardware ranges"节之后） | agent_tools.cpp、CMakeLists 之外任何文件 |
| M2 arguments 校验器 | `cpp/src/ai/arguments_validator.{h,cpp}`、`cpp/tests/test_arguments_validator.cpp` | 无（纯新模块） | 其他一切 |
| M3 协议层（OpenAI/Anthropic 双形状） | `cpp/src/ai/llm_protocol.{h,cpp}`、`cpp/tests/test_llm_protocol.cpp` | 无（纯新模块，禁 QNetwork） | 其他一切 |
| M4 循环集成（依赖 M1-3 完成后派发） | `cpp/tests/test_ai_tool_loop.cpp` | llm_client.{h,cpp}、llm_worker.cpp、agent_tools.cpp、ai_context.cpp、CMakeLists.txt（SOURCES 加 M1-3 新文件 + 注册 M1-3/M4 测试）、tokens.h（追加循环常量，锚点=文件末尾） | 其他 |
| M6 Flutter 端同步 | `mobile/lib/services/tool_arguments_validator.dart`、`mobile/test/tool_arguments_validator_test.dart` | ai_client.dart、ai_tools.dart、chat_message.dart、tokens.dart、ai_client_test.dart | mobile 其他文件 |

C++ 各模块可读全部学习笔记与 sources（绝对路径见批次规范），但**只写自己所有权内的文件**。

## 2. 公共类型契约（llm_client.h，M4 落地；M1/M2/M3 用同形状）

```cpp
struct ChatMessage { QString role; QString content;
    QString reasoningContent;        // NEW：assistant 消息逐字保留（可空）
    QString toolCallId; QList<ToolCall> toolCalls; };
struct ToolCall { QString id; QString name; QJsonObject arguments; };
struct ToolDef { QString name; QString description; QJsonObject parameters; }; // parameters = JSON Schema
struct LLMResponse { QString content; QString reasoningContent;
    QList<ToolCall> toolCalls; QString finishReason; QString error; };          // finishReason NEW
```

## 3. M1 Schema 生成器（能力边界 c 的"自动生成"侧）

- 声明式注册表 `QList<ToolSchemaSpec> registeredToolSpecs()`（新文件内），字段：name/description/params（name/type/description/min/max/enumValues/required）。
- `QJsonObject buildToolSchema(const ToolSchemaSpec&)` → `{"type":"object","properties":{...},"required":[...]}`；number 型带 minimum/maximum，string 型带 enum。
- `QList<ToolDef> toolDefsFromSpecs(...)` 组装 ToolDef（name/description 与现状**逐字一致**，避免破坏 UI 文案与既有测试）。
- 7 个工具的参数规格（数值边界**必须来自 tokens.h/bandwidth_preset.h 常量**，禁止魔法数）：
  - tune_frequency：freq_hz number required，min kFreqMinHz max kFreqMaxHz
  - set_mode：mode string required，enum {AM,NFM,WFM,USB,LSB,CW}
  - start_recording / stop_recording / get_status：无参数（properties={}）
  - scan_band：low_hz number required（min kFreqMinHz max kFreqMaxHz）、high_hz number required（同界）、step_hz number optional（min 1，默认 200000）
  - set_bandwidth：bandwidth_hz number required，enum 取 bandwidth_preset.h 全部 kBw* 档位（{9000,12500,2400,500,12000,200000,2000000}）
- 测试：schema 形状、边界注入（min/max 值等于 tokens 常量）、enum、required、description 非空；与现有 7 工具名完全一致。

## 4. M2 arguments 校验器（能力边界 c 的"拒绝"侧）

- `ValidationResult validateArguments(const QString& toolName, const QJsonObject& args, const QJsonObject& schema)`。
- 检查顺序：① 必填存在；② 值类型匹配（number/integer/string/boolean）；③ enum 成员；④ min/max；⑤ **schema.properties 之外的键一律拒绝**（幻觉/越界参数，MiMo 原文明示风险）；⑥ 非法 JSON 由调用方在 parse 层拒绝（llm_client 层，M4）。
- `errorJson`：`{"ok":false,"tool":"<name>","error":"参数校验失败：<首个错误>","reasons":[...]}`（Compact JSON），直接作为 role=tool 的 content 回灌让模型自纠。
- 测试：必填缺失、类型错、enum 外、越界、幻觉键、合法通过、空对象工具。

## 5. M3 协议层（能力边界 a/b/f 的协议抽象，纯函数禁网络）

```cpp
enum class Protocol { OpenAI, Anthropic };
struct RequestOptions { Protocol protocol=OpenAI; QString model; bool stream=false;
    QString toolChoice="auto";          // 永远 auto（边界 d）
    bool thinkingEnabled=false; int thinkingBudget=0; };
QJsonObject buildChatRequest(const QList<ChatMessage>&, const QList<ToolDef>&, const RequestOptions&);
QJsonObject parseChatResponse(const QByteArray& body, Protocol, LLMResponse* out, QString* err); // 非流式
struct ToolCallDelta { int index; QString id; QString name; QString argumentsFragment; };
struct StreamChunk { QString contentDelta; QString reasoningDelta;
    QList<ToolCallDelta> toolDeltas; QString finishReason; };
StreamChunk parseStreamChunk(const QByteArray& payload, Protocol); // 一条 SSE data 行
```
- OpenAI 形状：现状同款 + `message.reasoning_content` / `delta.reasoning_content` + finish_reason + thinking 参数（enable_thinking/thinking_budget，仅 thinkingEnabled 时下发）。
- Anthropic 形状（MiMo /anthropic/v1，能力边界 f）：system 为顶层参数；messages 用内容块；tools 用 `{type:"custom",name,description,input_schema}`；assistant 回复 tool_use 块 `{id,name,input(object)}`；工具结果以 `{role:"user",content:[{type:"tool_result",tool_use_id,content}]}`；`stop_reason=="tool_use"`/`"end_turn"`；thinking 对应 reasoning。
- 测试：OpenAI 非流式含 reasoning/tool_calls 解析；OpenAI SSE 行（含 delta.reasoning_content 与 arguments 分片）；Anthropic 请求形状断言（system 顶层、input_schema）；Anthropic tool_use/tool_result 解析；thinking 参数只在 enabled 时出现。

## 6. M4 循环集成（M1-3 完成后派发，能力边界 a/b/c/d/e 全链路）

- llm_client：新增 transport 注入缝 `using TransportFn = std::function<QByteArray(const QNetworkRequest&, const QByteArray& body)>`，默认实现走现有 QNAM post；测试注入 mock（返回 canned SSE/JSON）。请求经 M3 构造；响应经 M3 解析；ChatMessage 增 reasoningContent 并**逐字原样往返**（assistant 消息带 reasoning_content 上送）；finish_reason 透出；thinking 按模型配置（模型→thinking 开关映射：DeepSeek-V3.2/GLM-4.7 开、Qwen 默认关、MiMo v2.6-pro 对话轮开/工具轮关）；tool_choice 只发 auto（现状已满足）。
- llm_worker：循环上限硬编码 3 → `tokens.h` 新增 `kAiMaxToolRounds`（默认 8，与 kTaskMaxSteps 一致即可，避免与现有任务编排混淆可命名 kAiMaxToolRounds）；每轮 tool_calls 执行前经 M2 校验：校验失败→把 errorJson 作为 role=tool 回灌（不执行工具），继续下一轮让模型自纠；校验通过→dispatchToolCall。assistant 消息（content+reasoningContent+toolCalls）与 role=tool（tool_call_id 严格配对，N 个回 N 条）原样入历史；reasoningContent 跨轮逐字保留。循环内不触发上下文压缩（现状已满足：压缩在循环前）。
- agent_tools：toolDefs() 改为消费 M1 registeredToolSpecs()（名称/描述不变）；executeTool 语义不变（校验已前置）。
- tokens.h 追加（锚点=文件末尾）：kAiMaxToolRounds、kAiRequestTimeoutMs（替代 30000 硬编码，默认 30000）、kAiThinkingBudgetTokens（默认 4096，界 128..32768）。
- 新测试 tests/test_ai_tool_loop.cpp（mock transport，全离线确定性）：
  ① 多轮循环：第 1 轮返回 tool_calls（带 reasoning_content）→ 执行 → 第 2 轮返回 stop 最终回答；断言：请求体 messages 中 assistant 消息带逐字 reasoning_content、role=tool 按 tool_call_id 配对、顺序正确；
  ② 非法 arguments（如 tune_frequency freq_hz=3e99 越界）→ 不执行工具、errorJson 以 role=tool 回灌、下一轮继续；
  ③ 幻觉参数（schema 外键）→ 拒绝；
  ④ finish_reason=stop 终止；
  ⑤ 轮数上限封顶；
  ⑥ 流式 SSE 序列：tool_calls index 分槽拼接 + delta.reasoning_content 逐字累积；
  ⑦ MiMo 工具轮 thinking 关（请求体无 enable_thinking）；
  ⑧ Anthropic 协议 mock（工具轮 tool_use→tool_result 全流程）。
- CMakeLists：SOURCES 追加 tool_schema.cpp、arguments_validator.cpp、llm_protocol.cpp；注册 test_tool_schema / test_arguments_validator / test_llm_protocol / test_ai_tool_loop 四个 add_executable+add_test（按现有 470-480 行模式）。
- 全量验证：`cmake -S . -B build && cmake --build build --target mbdsdr -j4`；`LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib:/home/user/.local/lib QT_QPA_PLATFORM=offscreen ctest --test-dir build --output-on-failure` → 79/79 + 新增全绿。

## 7. M6 Flutter 端（与 Wave1 并行，独立代码库零冲突）

- chat_message.dart：ChatMessage 增 `reasoningContent`（String，默认 ''）；toApiJson 在非空时输出 `reasoning_content` 字段。
- ai_client.dart：
  - ChatCompletionChunk 增 `reasoningDelta`（delta.reasoning_content）；complete() 内 assistant 消息携带逐字累积的 reasoningContent；finishReason=="stop" 视为当轮正常收尾；6 轮上限移到 AppTokens（tokens.dart 增 `kMaxToolRounds`）；
  - 工具执行前经新校验器（tool_arguments_validator.dart）校验 tool.parameters（required/type/enum/min/max/未知键白名单），失败返回 `{"ok":false,"error":...}` 文本并以 role=tool 回灌（不执行 execute）；
  - buildRequestJson 增 thinking 支持：thinkingEnabled/thinkingBudget 与协议参数（默认 OpenAI；接口留 protocol 参数，Anthropic 形状给出映射函数 `buildAnthropicRequestJson`，双协议不混写，不接入网络路径）；
- ai_tools.dart：Schema 补数值边界——set_frequency frequency_hz minimum/maximum 用 AppTokens.freqMinHz/freqMaxHz；set_gain gain_db minimum/maximum 用 AppTokens.gainMinDb/gainMaxDb；set_sample_rate sample_rate_hz enum 用 AppTokens.sampleRatesHz；描述文案不改。
- 新测试：tool_arguments_validator_test.dart（必填/类型/enum/越界/幻觉键/合法）；ai_client_test.dart 扩展（mock transport）：多轮循环+reasoning 回传断言、非法参数不执行工具、finish_reason stop、thinking 参数下发、MiMo 工具轮 thinking 关、Anthropic 请求形状。
- 验证：`/home/user/tools/flutter/bin/flutter analyze`（0 issue）+ `flutter test`（209 全绿 + 新增）。

## 8. 红线（全部模块）

- 禁 eval / 禁把 args 当代码执行；禁真调 API（无 key 一切 mock）；禁造假数据、禁假峰值/假执行。
- 禁逐字复制 GPL 源码（干净室 MIT）；文档与代码不得出现"比赛/competition"字样。
- 禁硬编码魔法数：数值一律走 tokens.h / AppTokens / bandwidth_preset.h。
- 每模块交付：实现 + 确定性测试（可运行、全绿）+ 自检记录；不动 git（推送由主控统一做）。
- 设计气质与 UI 无关，但新增对话文案保持克制中文、不夸大。
