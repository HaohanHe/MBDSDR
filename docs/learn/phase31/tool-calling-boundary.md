# Phase31 A4：工具调用能力边界（SiliconFlow × MiMo × MiMo-Code 错误恢复）

> **目的**：为 B 块「35 工具能力文档化」与「工具调用错误恢复」提供模型侧依据。
> **精读日期**：2026-10-04。**范围**：function-calling 参数格式、并发上限、错误返回语义、配额边界、开源框架错误恢复真实源码。
> **标注约定**：`SF` = 硅基流动文档快照；`MiMo-doc` = 小米 MiMo API 文档快照；`MiMo-Code` = 已 clone 仓库（`repos/phase31_mimo` → `mimo-code/`，https://github.com/XiaomiMiMo/MiMo-Code ，HEAD `6fbb173`）。只学机制，不抄代码。

## 0. 来源清单（URL + 本地落点）

| 材料 | 来源 URL | 本地证据 |
|---|---|---|
| SF Chat Completions 参数表/错误码 | https://docs.siliconflow.cn/api-reference/chat-completions/chat-completions | `repos/model-docs/siliconflow/docs_api_chat-completions-post.html`；行号底稿 `docs/learn/model-tool-calling/sources/sf-chat-completions-api.txt` |
| SF Function Calling 指南 | https://docs.siliconflow.cn/userguide/guides/function-calling | `repos/model-docs/siliconflow/docs_userguide_guides_function-calling.html` |
| SF 流式 / interleaved thinking | 同域 userguide 页 | `…stream-mode.html`、`…interleaved-thinking.html` |
| MiMo OpenAI 兼容 API | https://api.xiaomimimo.com/v1/chat/completions （文档站 mimo.mi.com） | `repos/model-docs/xiaomi-mimo/api_chat_openai-api.html`；底稿 `mimo-openai-api.txt` |
| MiMo reasoning_content 回传 | mimo.mi.com usage-guide | `…passing-back-reasoning_content.html`；底稿 `mimo-reasoning.txt` |
| MiMo-Code agent 运行时 | https://github.com/XiaomiMiMo/MiMo-Code | `repos/phase31_mimo/packages/opencode/src/` |

> 前置批次（16 篇笔记 + sources 底稿）已收敛协议细节，本稿不重复论证，只补：错误返回语义、配额边界、**真实开源错误恢复源码 file:line**。

## 1. SiliconFlow：格式与边界

### 1.1 tools 数组形状
- 形状：`tools[] = {type:"function", function:{name, description, parameters}}`；`parameters` 为 JSON Schema，顶层 `type:"object"` + `properties`/`required`（SF FC 指南快照）。
- **数量上限：最多 128 个函数**（`sf-chat-completions-api.txt:173,317`）。我方 35 工具远低于此，安全。
- `tool_choice`：参数表无独立行，全文仅示例 `"auto"`（`sf-chat-completions-api.txt:459` 附近）；不承诺强制指定工具。
- 思考预算：`enable_thinking` + `thinking_budget`，范围 `128 <= value <= 32768`（`:69,299`）。
- 并行多 tool_calls：SF 官方两个示例都只取 `tool_calls[0]`，**未举证并行**；按 OpenAI 兼容推断应支持，落地时按「一轮 N 个 tool_calls」处理。

### 1.2 错误返回格式与配额边界
- 错误体形状：`{code, message, data}`（`sf-chat-completions-api.txt:514-518`）。
- 列出的 HTTP 状态：**400 / 401 / 403 / 404 / 429 / 503 / 504**（`:351-363`）。
- 关键错误码逐字：
  - `20012` = "Invalid token"（API key 无效/缺失，`:521`）；
  - `50505` = "Model service overloaded. Please try again later."（模型过载，`:536-540`）；
  - 429 体："Request was rejected due to rate limiting … Details: **TPM limit reached**."（`:530-533`）——**配额以 TPM（token/分）口径返回，不给 RPM 明细**，提额需联系商务。
- 语义结论：**401/403 = key/权限问题（不重试、不 mock，诚实 PENDING）；429/503/504 = 瞬态（重试）；400 = 请求构造错（不重试，修请求）**。

### 1.3 thinking × 工具调用的模型分档边界
- interleaved thinking（工具结果后继续出 reasoning 且必须回传）目前仅 **DeepSeek-V3.2 与 GLM-4.7** 两款官方支持（SF interleaved-thinking 快照；前置笔记 §02）。
- **特例红线**：DeepSeek-V3.1 做 function call 必须 `enable_thinking=false`（SF 模型文档）。我方 ModelConfig 需按型号分档，不能一刀切。
- 丢弃/改写 reasoning_content 的后果（SF 口径）：多步工具链崩、跨调用不稳定、prompt cache 命中率下降（同快照）。

## 2. 小米 MiMo：格式与边界

- 协议入口 `POST /v1/chat/completions`，OpenAI 兼容；`arguments` 是 **JSON 字符串**，需二次 parse（`mimo-openai-api.txt:87`）。
- **并行调用实证**：`tool_calls` 数组「可包含一个或多个」，客户端需逐个执行并各以 `role=tool` + `tool_call_id` 回传（`:82,256`）。
- **tool_choice 边界（硬）**：传非 `auto` 值「后端默认移除该字段，行为等同 auto」，合法枚举**只有 auto**（`:57-59`）。我方禁止下发 `required`/指定函数。
- **arguments 不信任（两处明文警告）**：模型输出「并非总能保证有效 JSON，且可能虚构 schema 外参数」，调用前必须在代码中校验（`:88-89` 非流式、`:114` 流式重复）。
- reasoning_content 不完整回传 → **HTTP 400**（`mimo-reasoning.txt`，前置笔记 §08）。
- 配额：旗舰 `mimo-v2.6-pro/flash` = **RPM 100 / TPM 10M**，上下文 1M / 输出 128K（`model-list-mimo.txt:20-21`）。RPM 100 对桌面端够用，但 agent 多步循环会快速消耗——需本地限速。
- Anthropic 兼容协议另有 `tool_choice.disable_parallel_tool_use` 开关（默认 false）；OpenAI 协议下无服务端并行开关。

## 2b. 能力边界速查（并发 / 格式 / 错误语义）

| 维度 | SiliconFlow | MiMo（OpenAI 协议） | 对我方约束 |
|---|---|---|---|
| 单次请求工具数 | ≤128 函数（`:173`） | 未写上限 | 35 个全量下发即可 |
| 单轮并行 tool_calls | 文档未举证（推断支持） | **实证支持，一个或多个**（`:82,256`） | 按 N 个配对表处理 |
| arguments 形态 | JSON 字符串（推断） | **JSON 字符串，明示**（`:87`） | 流末二次 parse |
| tool_choice | 仅示例 auto | **非 auto 被静默剥成 auto**（`:57-59`） | 只发 auto 或省略 |
| 错误体 | `{code,message,data}` | APIError 结构（同 OpenAI 形） | 按 code/HTTP 状态分类 |
| 可重试 | 429/503/504 | 429/5xx（同左通用语义） | 白名单见 §4.2 |
| 不重试 | 401/403/400 | 400/401/403/422（MiMo-Code 实证 `retry.ts:369`） | 401/403 = 诚实 PENDING |
| 配额口径 | TPM limit（429 体明示） | RPM 100 / TPM 10M（`:20`） | 本地限速 + 退避 |
| reasoning 回传 | 软后果（崩链/缓存劣化） | **硬后果（缺则 400）** | append-only 逐字 |

## 3. 开源错误恢复实证：MiMo-Code（真实源码 file:line）

> 仓库即小米开源 agent 框架（opencode 衍生）。以下机制与我方 runtime 同构，直接可借鉴。

### 3.1 工具报错回注模型（自纠循环）
- **RecoverableError 标记**：工具失败分两类——「agent 可恢复」（坏参数、 malformed 调用、未知 id）vs 真系统故障。前者 TUI 灰显不告警，但**完整可操作消息仍作为 tool result 流回模型**，模型下一轮自纠（`tool/recoverable.ts:3-14` 注释；类定义 `:15-21`；结构判定 `:32-35`）。
- **failToolCall**：捕获工具错误后把 part 置 `status:"error"`、写入 `errorMessage(error)`，**不抛出、不中断本轮循环**（`session/processor.ts:375-408`）；事件路由在 `:549-561`。
- **逐工具隔离**：每个 tool call 独立 `Effect.tryPromise` + `catch` 成 `{ok:false,error}` 再发 `tool-error` 事件（`:1015-1035`）；**一个工具失败不拖垮同轮其他并行工具**。无执行器的工具也走同一通道（`:1003-1013`）。
- **自纠闭环**（机制归纳，非抄码）：工具 error part 持久化后，下一轮模型请求的消息历史里该工具结果就是错误文本 → 模型看到「我上一步参数错了，合法示例是 X」→ 自动重发修正后的 tool_call。全程不抛异常给用户、不需要人工介入；分类标记 `recoverable` 只影响 UI 呈现，不影响回注内容。

### 3.2 LLM 侧重试（预算/退避/分类）
- **可重试 HTTP 白名单**：`{408, 425, 429}`；5xx 全段可重试但排除 `501/505`（`session/retry.ts:186,366`）。
- **终态（不重试）**：`400/401/403/422`（`:369`）、`402/501/505`（`:349`）、404（`:350`）；**FreeUsageLimitError / SubscriptionUsageLimitError → 终态 + 引导充值 UI**（`:345-348`）。
- **退避算法**：`initial * 2^(attempt-1)`，夹到 [min,max]，叠加 ±jitterRatio 抖动；**优先服从 `Retry-After` 头**（`retry.ts:379-391`）。
- **预算分档**（`:18-31, 59-114`）：stream 最多 5 次 / 10 分钟；server 最多 8 次 / 15 分钟；rate_limit 最多 5 次；network 持久退避（初值 5s、上限 60s）。
- **流式读超时**独立分类为 stream 类重试（`:187,354`）；GPT `server_is_overloaded` 静默重试不打扰用户（`:400-408`）。
- **中断传播**：abort 时 `ctrl.abort()` 传播给正在跑的工具，长任务真被打断而非悬挂（`processor.ts:1087-1096`）。

### 3.3 上下文设计（顺带证据）
- 压缩时超长工具结果替换为占位符：`"[Tool result omitted during compaction: N tokens. Re-run \"<tool>\" if this result is needed.]"`（`session/compaction.ts:165-174`）——**告诉模型结果被裁了、如何重取**，而不是静默丢弃。

## 4. 对我方 35 工具系统的落地建议

### 4.1 能力文档生成（B 块①）
- 每工具 schema 输出字段固定：`name`（`[A-Za-z0-9_-]` ≤64）、`description`、`parameters`（JSON Schema：required/properties/enum）、**read/write 标记**（对齐 `isWriteTool` 写门）、错误示例。
- 35 < 128 上限，一次全量下发；按模型补 thinking 策略（SF V3.2/GLM-4.7 开 interleaved；MiMo 工具轮建议关 thinking）。
- 文档里显式写「arguments 可能非法 JSON / 幻觉参数」的校验承诺，与我方 runtime 校验器互证。

### 4.2 错误恢复分层（B 块②，对照上表）

| 错误层 | 归类 | 我方动作 |
|---|---|---|
| 参数非法/未知工具/幻觉参数 | agent-recoverable | 不抛异常；错误字符串经 `role:tool` 回注模型，附合法示例，等下轮自纠（学 `failToolCall`） |
| 写门/手动 AI gate 拒绝 | recoverable-but-blocked | 同通道回「需手动确认，已拒绝本次写」；不自动重试写动作 |
| 硬件执行失败（调谐失败等） | 按工具粒度隔离 | 单工具 error 回注，同轮其他并行只读工具继续 |
| 401/403/无 API key | 终态 | **诚实 PENDING，不 mock 输出、不重试**（对齐 FreeUsageLimit 终态语义） |
| 429 / 503 / 504 / 流式超时 | 瞬态 | 指数退避 + 抖动；预算：rate_limit 5 次、server 8 次；服从 Retry-After；RPM 100 本地限速 |
| 400 | 终态 | 请求构造 bug，记日志不重试 |
| 看门狗 | 防死循环 | max_tool_rounds 硬停（设计值 12），超限如实报错 |

### 4.3 红线
- 禁 eval 模型 arguments（SF 官方示例用 eval 是反例）；禁改写 assistant 消息（reasoning_content 逐字 append-only）。
- `tool_choice` 只发 `auto` 或省略；并行 tool_calls 按 id 配对、N 个结果 N 条回传。

### 4.4 与我方现状的差距对照（Wave2 输入）
- 现状（Phase31 SPEC 复核）：35 工具已注册 `registeredToolSpecs` + 写门 `isWriteTool` + AI gate 手动模式；**缺**：①schema→Agent 可见文档生成器；②工具调用显式 retry/降级路径（现仅 `compactContext` fallback 一行）。
- 本稿给出的落地顺序：先做文档生成（§4.1，纯静态、可单测「35 工具全在/边界字段完整」），再做错误恢复分层（§4.2 表）——两者共用同一份 schema 数据源，不重复维护。
- 不做：SSE 断点续传（两家文档均无此字段，断线=整条重发）；不赌 Anthropic 协议（SF 侧正文未抓，YAGNI）。

## 5. 自检
| 检查项 | 结果 |
|---|---|
| SF 格式/错误码/配额带来源 URL + 行号 | ✅ §1，行号见 sources 底稿 |
| MiMo 文档 URL + 边界（并行/tool_choice/配额） | ✅ §2 |
| 错误恢复真实开源源码 file:line | ✅ §3 全部指向 `repos/phase31_mimo` 真实文件 |
| 只学机制不抄代码 | ✅ 仅引用 file:line 与注释，未复制实现 |
| 对我方工具文档生成 + 错误恢复的落地映射 | ✅ §4 |
| 篇幅 | ✅ 约 150 行（目标 120–200） |

> 备注：`mimo-siliconflow.md` 与本稿合并（材料不另起分册）；协议全量细节仍以 `docs/learn/model-tool-calling-boundaries.md`（16 篇收敛总稿）为准，本稿是其 Phase31 增量。
