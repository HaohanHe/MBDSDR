# 硅基流动 Chat Completions API（OpenAI 兼容）学习笔记

> 文档来源（本地原始 HTML）：`/home/user/Doubao/chats/38438160041798146/repos/model-docs/siliconflow/docs_api_chat-completions-post.html`（628,559 字节）
> 转换纯文本：`sources/sf-chat-completions-api.txt`（545 行，本目录 `sources/` 下，他人可引用）
> 精读方式：HTML→文本（Python `html2text`，提取 `<article>` 正文），逐节读完转换文本全文
> 精读日期：2026-10-01　作者：s_000c4GAuGAS（MainAgent）
> 覆盖边界：**a / c / d 的参数侧**（function calling 循环参数、arguments 合法性、tool_choice 取值）；旁及 b（reasoning_content 保留）、e（thinking+工具的模型差异）
> 标注约定：「原文」= 转换文本可直接定位；「推断」= 原文无直接证据、按 OpenAI 兼容惯例/代码示例合理外推；行号均指 `sources/sf-chat-completions-api.txt:<行>`。
>
> **重要前置说明（原文形态）**：本页是 Fumadocs 静态渲染的 OpenAPI 参考页。嵌套对象 schema（messages 数组项、tools 数组项、choices 数组项、usage 内部）在静态 HTML 中是**折叠面板**（"Array Item" / "Show Attributes"），未展开为字段表；字段级细节只能从**代码示例**与 **200 响应 Default JSON 示例**反推。下文凡折叠面板未给出的字段，均以示例为据并标注。

---

## 1. 原文事实清单

### 1.1 Endpoint、认证、请求方法

| 事实 | 出处 | 要点 |
|---|---|---|
| 接口标题 | `:3` | 「创建对话请求（OpenAI）」——明确 OpenAI 兼容 |
| 方法 + 路径 | `:9-11` | `POST` `/chat/completions` |
| 完整 URL（示例） | `:369`、`:386` | `https://api.siliconflow.cn/v1/chat/completions`（base_url=`https://api.siliconflow.cn/v1`） |
| 认证头 | `:15-21` | `Authorization: Bearer <token>`，**required**；原文：`添加 Header 'Authorization: Bearer {账户 API Key}' 进行鉴权`，位置 `In: header` |
| 请求头 X-Trace-Id | `:25-27` | string，请求追踪 ID，可自定义；不传则平台自动生成 |
| 请求头 traceparent | `:29-31` | W3C Trace Context 标准追踪头；传入时取其中 trace-id 作为本次请求追踪标识 |
| 响应追踪头 | `:325` | 响应头含 `x-siliconcloud-trace-id`；用户传入的 `X-Trace-Id` 会以该字段原样返回 |

> 对 MBDSDR 的硬约束：runtime 必须固定 `POST {base}/chat/completions`，且每个请求带 `Authorization: Bearer $SILICONFLOW_API_KEY`；建议同时发 `X-Trace-Id`（= 会话/任务 UUID），便于把模型侧报错与本地日志对齐。

### 1.2 请求 Body 顶层参数（LLM 标签页）

原文把请求体做成 `LLM / VLM` 两个标签页（`:35`），SSR 同时渲染了两份，下文先列 **LLM 标签**（`:37-175`），VLM 差异见 1.3。

| 参数 | 类型 | 必填 | 取值/范围 | 原文要点 | 出处 |
|---|---|---|---|---|---|
| `model` | string | ✅ | 如 `"deepseek-ai/DeepSeek-V4-Flash"` | 模型名；平台会定期上下线/调整模型，完整列表见「对话模型」页 | `:37-41` |
| `messages` | array&lt;object&gt; | ✅ | 数组项字段折叠未展开 | 对话消息列表（Array Item 折叠，见 1.4） | `:43-47` |
| `stream` | boolean | – | `false \| true` | 设 true 则 token 以 SSE 流式输出；流式通常以 `data: [DONE]` 结束 | `:49-53` |
| `max_tokens` | integer | – | – | 最大生成 token 数，**不包含思维链部分**；建议不要设到窗口上限，为输入+系统开销预留约 10k token 缓冲 | `:55-57` |
| `enable_thinking` | boolean | – | `false \| true` | 在推理/非推理模式间切换；适用于大多数推理模型 | `:59-63` |
| `thinking_budget` | integer | – | **128 <= v <= 32768** | 思维链输出的最大 token 数；适用于大多数推理模型 | `:65-69` |
| `reasoning_effort` | string | – | `"high" \| "max"` | 仅适用于 `Pro/deepseek-ai/DeepSeek-V4`、`deepseek-ai/DeepSeek-V4-Flash`、`Pro/zai-org/GLM-5.2`；常规请求默认 high；Claude Code/OpenCode 类智能体请求自动设 max；low/medium 映射为 high，xhigh 映射为 max | `:71-75` |
| `min_p` | number(float) | – | `v <= 1` | 按 token 概率动态过滤阈值；**仅 Qwen3 适用** | `:77-83` |
| `stop` | array&lt;string&gt; \| string \| null | – | 最多 4 个序列 | 遇到即停生成；返回文本不含停止序列 | `:85-89` |
| `temperature` | number(float) | – | **0 ~ 2**（range 标 v<=2） | 采样温度；高(0.8)更随机，低(0.2)更确定；例 0.7 | `:91-99` |
| `top_p` | number(float) | – | `v <= 1` | 核采样；与 temperature 二选一调，不要同时调；例 0.7 | `:101-109` |
| `top_k` | number(float) | – | `v <= 100` | （原文未给文字说明，仅类型/range；推断即 top-k 采样） | `:111-115` |
| `frequency_penalty` | number(float) | – | **-2 ~ 2** | 按已出现频率惩罚，降低逐字重复 | `:117-123` |
| `n` | integer | – | 例 1 | 返回生成结果数量 | `:125-129` |
| `response_format` | object(三选一) | – | text / json_schema / json_object | 见 1.5 | `:131-139` |
| `tools` | array&lt;object&gt; | – | 最多 128 个函数 | 模型可能调用的工具列表；**目前仅支持函数作为工具**；为模型提供会为其生成 JSON 输入的函数列表（Array Item 折叠，见 1.6） | `:171-175` |

> 注意：**本参考页的请求体参数表里没有 `tool_choice` 这一行**（全文 `tool_choice` 仅 3 处，均在代码示例内，见 1.6）。这是与 OpenAI 官方文档的一个事实差异，runtime 不能假设服务端把 `tool_choice` 当独立受控参数校验。

### 1.3 VLM（视觉）标签页参数差异

VLM 标签（`:177-319`）复用同一套结构，差异如下（原文直接给出 Default）：

| 参数 | LLM 标签 | VLM 标签 | 出处 |
|---|---|---|---|
| `model` 示例 | `deepseek-ai/DeepSeek-V4-Flash` | `moonshotai/Kimi-K2.7-Code`（标注「视觉输入模型」） | `:41` / `:181,179` |
| `temperature` | 无 Default（例 0.7） | **Default 0.7** | `:209` |
| `top_k` | 无 Default（range<=100） | **Default 50** | `:225` |
| `frequency_penalty` | 无 Default（range -2..2） | **Default 0.5** | `:235` |
| `n` | 无 Default（例 1） | **Default 1** | `:245` |
| `enable_thinking`/`thinking_budget`/`reasoning_effort`/`min_p` | 有 | 同样有（`:289-313`） | – |
| `tools` | 有 | 有（`:315-319`），同样「最多 128 个函数」 | – |

视觉输入的消息形态由示例给出：`content` 可为数组，元素 `{"type":"text","text":...}` 与 `{"type":"image_url","image_url":{"url":"https://..."}}`（`:407-412`）。MBDSDR 若未来给模型看 waterfall 图，content 走此多模态数组形态。

### 1.4 `messages` 数组项字段（折叠面板 + 示例反推）

折叠面板在静态 HTML 中未展开 role/content 字段表。可确认的消息形态全部来自示例：

| role | content 形态 | 出处 |
|---|---|---|
| `system` | 字符串（`"你是一个有用的助手"`） | `:375,449` |
| `user` | 字符串（`:376`）；或多模态数组 `[{type:text},{type:image_url,image_url:{url}}]`（`:408-412`） | `:376` / `:407-412` |
| `assistant` | 字符串内容（`"波士顿今天晴，气温 20°C。"`） | `:451` |
| `assistant`（响应里） | `{role:"assistant", content, reasoning_content}`（见 1.7） | `:483-487` |

> 推断（原文无直接证据）：本页**没有出现** `role:"tool"` 消息，也没有出现回传工具结果所需的 `tool_call_id` 字段（全文 `tool_call_id` 命中 0 次）。按 OpenAI 兼容惯例，多步工具循环需要 `{role:"tool", content:..., tool_call_id:...}` 把工具结果回传——但**硅基流动本页未文档化此消息形状**。runtime 实现时按 OpenAI 约定发送属合理推断，需在真实联调（有 key 时）验证服务端是否接受。

### 1.5 `response_format`（结构化输出，`:131-169`）

| 模式 | type | 说明 | 出处 |
|---|---|---|---|
| Text | `"text"` | 默认响应格式，生成文本 | `:141-147` |
| JSON schema | `"json_schema"` | 结构化输出，确保模型匹配所给 JSON Schema；推荐优先用 | `:149-159`（`json_schema` 对象 required，Recursive） |
| JSON object | `"json_object"` | 较早的 JSON 模式；**若无 system/user 消息指示模型生成 JSON，模型不会生成 JSON**；有 json_schema 时优先后者 | `:163-169` |

### 1.6 `tools` / `tool_choice`（function calling 参数侧）

**tools 顶层**：`array<object>`，「模型可能调用的工具列表，目前仅支持函数作为工具……最多支持 128 个函数」（`:173,317`）。

**tools 数组项的字段结构**——折叠面板未展开，以下结构**逐字来自 python-Advanced 示例**（`:427-446`）：

```jsonc
{
  "type": "function",
  "function": {
    "name": "get_current_weather",
    "description": "Get the current weather in a given location",
    "parameters": {
      "type": "object",
      "properties": {
        "location": {"type": "string", "description": "The city and state, e.g. San Francisco, CA"},
        "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}
      },
      "required": ["location"]
    }
  }
}
```

**tool_choice**：原文参数表无此行；仅在 Advanced 示例调用中出现一次：`tool_choice="auto"`（`:459`，Python SDK 写法）。**原文未给出 auto / required / none / 指定函数名 的取值枚举与语义表。**

> 事实结论：硅基流动本页对 `tool_choice` 的全部文档化信息就是示例里的 `"auto"`。**不能据此断言支持 `required`、`none` 或 `{"type":"function","function":{"name":...}}` 强制指定**——这与能力边界 d（tool_choice 行为）相关，但硅基流动侧**未文档化**这些取值，runtime 应默认 `auto`，把强制指定工具当作「未验证能力」，联调时再测。

**流式下的工具调用读取**（示例，`:463-468`）：
```python
for chunk in stream:
    delta = chunk.choices[0].delta
    if delta.content: print(delta.content, end="", flush=True)
    if delta.tool_calls:        # 原文此处截断，仅示意 delta.tool_calls 分支
```

### 1.7 响应结构（200）

响应面板顶层字段（`:333-349`）：`id`(string)、`choices`(array&lt;object&gt;，Array Item 折叠)、`usage`(object，Show Attributes 折叠)、`created`(integer)、`model`(string)、`object`(string，恒 `"chat.completion"`)。

**choices / usage 内部字段**由 **Default 200 示例 JSON**（`:475-505`）完整给出——这是本页字段级响应结构的权威来源：

```jsonc
{
  "id": "019bdaa55225ef854b320e9b838f77ce",
  "object": "chat.completion",
  "created": 1768899826,
  "model": "deepseek-ai/DeepSeek-V4-Flash",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "你好！...",
        "reasoning_content": "..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 15,
    "completion_tokens": 1540,
    "total_tokens": 1555,
    "completion_tokens_details": { "reasoning_tokens": 1190 },
    "prompt_tokens_details": { "cached_tokens": 0 },
    "prompt_cache_hit_tokens": 0,
    "prompt_cache_miss_tokens": 15
  },
  "system_fingerprint": ""
}
```

| 字段 | 出处 | 要点 |
|---|---|---|
| `choices[].index` | `:481-482` | 0 |
| `choices[].message.role` | `:484` | `"assistant"` |
| `choices[].message.content` | `:485` | 正文（可能为 null 推断，工具调用时） |
| `choices[].message.reasoning_content` | `:486` | **思维链原文**，必须逐字保留（边界 b） |
| `choices[].finish_reason` | `:488` | 示例仅见 `"stop"`；**原文无枚举表**（推断另有 `length` / `tool_calls` 等，未文档化） |
| `usage.prompt_tokens` | `:492` | 15 |
| `usage.completion_tokens` | `:493` | 1540（含/不含思维链：见 max_tokens 不含思维链 `:57`） |
| `usage.total_tokens` | `:494` | 1555 |
| `usage.completion_tokens_details.reasoning_tokens` | `:496` | 1190——思维链单独计费计数 |
| `usage.prompt_tokens_details.cached_tokens` | `:499` | 0 |
| `usage.prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` | `:501-502` | 硅基流动特有缓存命中/未命中文 |
| `system_fingerprint` | `:504` | 示例为空串 |

> 流式分支面板 `### Streaming / ### Image input / ### Function`（`:507-511`）在静态 HTML 中为空（tab 内容未 SSR），流式响应逐块结构未展开；可据 `:463-468` 推断流式为 `chunk.choices[0].delta.{content, tool_calls}`，结束于 `data: [DONE]`（`:51`）。

### 1.8 错误码表（`:351-363, 514-543`）

| HTTP | 响应体（原文） | 出处 |
|---|---|---|
| 400 | `{"code":20012,"message":"string","data":"string"}` | `:514-518` |
| 401 | `"Invalid token"` | `:521` |
| 403 | `"Forbidden"` | `:524` |
| 404 | `"404 page not found"` | `:527` |
| 429 | `{"message":"Request was rejected due to rate limiting. If you want more, please contact contact@siliconflow.cn. Details:TPM limit reached.","data":"string"}` | `:530-533` |
| 503 | `{"code":50505,"message":"Model service overloaded. Please try again later.","data":"string"}` | `:536-540` |
| 504 | `"string"` | `:543` |

**限流**：429 明确为 **TPM（token per minute）limit reached**（`:531`），联系 contact@siliconflow.cn 提额。503 为模型服务过载，建议稍后重试。**本页未给 QPM/并发数、未给具体 TPM 数值**——runtime 应对 429/503 做指数退避重试，但不能硬编码额度阈值。

### 1.9 官方请求/响应示例（原样索引）

- 最简 curl（system+user 纯文本）：`:368-378`
- 最简 python（OpenAI SDK，base_url 指向 siliconflow）：`:382-396`
- 视觉 curl（多模态 content 数组）：`:400-416`
- 高级 python（tools + tool_choice="auto" + stream）：`:420-469`
- 200 成功响应 JSON：`:475-505`

---

## 2. 对 MBDSDR 工具化实现的启示

定位：MBDSDR runtime = 给 LLM 看的 function calling 工具集（无线电能力）+ 中介（构造请求 → 解析响应 → 校验 arguments → 真实执行 → role=tool 回传 → 保 reasoning_content → 多步循环到 stop）。以下具体到 `src/ai/llm_client`。

### 2.1 请求构造器（request builder）

在 `src/ai/llm_client` 维护一个 `SiliconFlowChatRequest`，字段一一对应 1.2：

```cpp
// 建议伪代码（src/ai/llm_client.h）
struct ChatRequest {
    std::string model = "deepseek-ai/DeepSeek-V4-Flash"; // 默认模型可配置
    std::vector<ChatMessage> messages;
    bool   stream = true;                 // Agent 循环一律 SSE，便于实时打日志
    int    max_tokens = 2048;             // 预留 10k 缓冲，勿贴窗口上限（:57）
    bool   enable_thinking = true;       // 思考模型开启
    int    thinking_budget = 4096;       // 受 128..32768 约束（:69）
    // reasoning_effort：仅 V4/GLM-5.2 系传 "high"/"max"（:73）；其他模型 omit
    std::optional<std::string> reasoning_effort;
    double temperature = 0.2;             // 工具调用宜低（文档 0~2，:93）
    double top_p = 0.7;                   // 与 temperature 二选一调（:103）
    int    n = 1;
    std::vector<ToolDef> tools;           // ≤128 个（:173）
    std::string tool_choice = "auto";     // 仅验证过 auto（:459）
};
```

- **Header 固定**：`Authorization: Bearer <key>`、`Content-Type: application/json`、外加 `X-Trace-Id`（= 本次 agent 会话 UUID），便于拿 `x-siliconcloud-trace-id` 对齐服务端日志（`:27,325`）。
- **tool_choice 只发 `"auto"`**：不要发 `required`/`none`/指定函数名——本页未文档化（1.6）。需要强制走某工具时，在 system prompt 里约束，而不是赌 `tool_choice` 后端支持。
- **thinking 参数按模型白名单下发**：`enable_thinking`/`thinking_budget` 仅推理模型支持（`:61,67`），`reasoning_effort` 仅 V4/GLM-5.2 系（`:73`），`min_p` 仅 Qwen3（`:79`）。构造器要按当前 model 名决定是否带这些键，否则可能 400。
- **max_tokens 语义**：不含思维链（`:57`）。给无线电工具循环留足余量，避免 reasoning 把额度吃光导致工具调用被截断。

### 2.2 工具定义（tools）构造

把 SDR 能力暴露成 `{type:"function", function:{name, description, parameters(JSON Schema)}}`（逐字来自 `:427-446`）。建议在 `src/ai/llm_client` 提供：

```cpp
ToolDef{ .name="set_frequency", .description="调谐到指定频率(Hz)",
  .parameters={ .type="object",
    .properties={ {"freq_hz", {.type="integer", .description="目标中心频率，Hz"}},
                  {"vfo",     {.type="string", .enum={"a","b"}}}},
    .required={"freq_hz"} } };
```

- `parameters` 用标准 JSON Schema（`type/object/properties/required/enum`），与示例完全同构。
- 工具数量硬上限 **128**（`:173`）；MBDSDR 无线电工具远小于此，无需担心，但构造器要防御性拒绝 >128。

### 2.3 响应解析器（response parser）

非流式按 `:475-505` 结构解析；流式按 `:463-468` 逐 chunk 聚合 `delta`：

```cpp
struct ChatChoice {
    int index;
    struct Message {
        std::string role;              // "assistant"
        std::string content;           // 正文（工具步可能为空/null）
        std::string reasoning_content; // ★思维链，原样保留（:486）
        std::vector<ToolCall> tool_calls; // {id, function{name, arguments(JSON字符串)}}
    } message;
    std::string finish_reason;         // 示例 "stop"（:488）
};
struct Usage {
    long prompt_tokens, completion_tokens, total_tokens;
    long reasoning_tokens;             // completion_tokens_details.reasoning_tokens(:496)
    long cached_tokens;                // prompt_tokens_details.cached_tokens(:499)
    long cache_hit, cache_miss;        // :501-502
};
```

- **reasoning_content 必须原样累积并在多步间保留**（边界 b）：把每轮 assistant 消息的 `reasoning_content` 原样塞回下一轮 messages，不要截断/重写/丢弃。
- **arguments 是 JSON 字符串，不是对象**：模型返回 `tool_calls[].function.arguments` 为字符串，runtime 必须二次 `json_parse`。
- **finish_reason 判定循环终止**：示例只见 `"stop"`（`:488`）。runtime 主循环以 `finish_reason == "stop"` 作自然结束；出现 `tool_calls` 则执行工具、把结果以 `role:"tool"` 回传继续循环；`finish_reason` 其他取值（推断 `length`=max_tokens 截断、`tool_calls`=有待执行调用）要兜底处理，不能只认 `"stop"`。

### 2.4 arguments 合法性校验（边界 c，重点）

文档示例里 `parameters` 是严格 JSON Schema，但**原文未承诺模型生成的 arguments 一定是合法 JSON、也未承诺只给 schema 内参数**（硅基本页无此明示；该断言来自 MiMo 对照文档）。runtime 必须：

1. `json_parse(arguments)` 失败 → 不调用工具，把「参数不是合法 JSON」作为 `role:"tool"` 错误内容回传，让模型自我修正；**禁止 eval 直执、禁止把未校验字符串拼进 SDR 命令**。
2. 解析成功后按工具的 JSON Schema 做**白名单校验**（required 字段齐全、类型正确、enum 命中），schema 外字段一律丢弃而非透传。
3. 数值范围二次校验：如 `freq_hz` 必须落在 SDR 可调频段内——模型不知道硬件硬限制，runtime 是最后一道闸。

### 2.5 tokens.h 常量抽取建议

`src/core/tokens.h`（或 `src/ai/llm_client` 内常量）建议集中放：

| 常量 | 值 | 依据 |
|---|---|---|
| `kSfChatEndpoint` | `/v1/chat/completions` | `:9-11,369` |
| `kSfBaseUrl` | `https://api.siliconflow.cn/v1` | `:386` |
| `kMaxToolsPerRequest` | 128 | `:173` |
| `kThinkingBudgetMin/Max` | 128 / 32768 | `:69` |
| `kMaxStopSequences` | 4 | `:87` |
| `kTemperatureMax` | 2.0 | `:93,97` |
| `kFreqPenaltyMin/Max` | -2.0 / 2.0 | `:123` |
| `kTopKMax` | 100 | `:115` |
| `kMaxTokensReserveBuffer` | ~10000 | `:57` |
| `kDefaultToolChoice` | `"auto"` | `:459` |
| 追踪头名 | `X-Trace-Id` / 响应 `x-siliconcloud-trace-id` | `:25,325` |

**错误处理常量**：401→key 无效需中止（`:521`）；403→无权限（`:524`）；429/503→指数退避重试（TPM 限流/过载，`:531,538`）；400(code 20012)→请求体不合法，把 `message` 回灌日志但不盲目重试。

---

## 3. 与能力边界映射

| 边界 | 文档依据 | 实现要点 |
|---|---|---|
| **a) OpenAI 兼容 function calling 循环** | tools 结构 `:427-446`；`tool_choice="auto"` `:459`；响应 message `:483-487`；`data:[DONE]` `:51` | 请求发 `tools=[{type:function,function:{name,description,parameters}}]`；解析 `choices[].message.tool_calls[].function.{name,arguments}`；执行后回传 `role:"tool"` 消息（此消息形态本页未文档化，推断，需联调验证）；循环到 `finish_reason="stop"` |
| **b) Interleaved Thinking** | `message.reasoning_content` `:486`；`usage.completion_tokens_details.reasoning_tokens` `:496`；`enable_thinking`/`thinking_budget` `:59-69` | reasoning_content 逐字保留、跨多步原样回传；流式累积 `delta.reasoning_content`（推断自 delta 模式）；丢弃会破坏多步工具链 |
| **c) arguments 合法性** | tools.parameters 为 JSON Schema `:433-444`；但本页未承诺模型输出合法 JSON | runtime 必须 `json_parse` + Schema 白名单校验 + 频段范围校验，禁 eval；失败以 tool 错误消息回传 |
| **d) tool_choice** | 全文仅示例 `tool_choice="auto"` `:459`，参数表无此行、无 auto/required/none 枚举 | **默认 auto，勿赌强制指定工具**；与 MiMo「后端强制 auto」一致，硅基本页同样未暴露更强控制 |
| **e) thinking + 工具稳定性** | `enable_thinking` 适用于大多数推理模型 `:61`；`reasoning_effort` 仅特定模型 `:73` | 按模型配置 thinking；工具调用步注意 reasoning_content 与 tool_calls 可能交错，解析器要分开聚合 |
| **f) Anthropic 兼容** | 本页为 OpenAI 兼容；页脚有「创建对话请求（Anthropic）POST /docs/api/messages-post」链接 `:545` | Anthropic 协议是另一个端点，本笔记不覆盖；MBDSDR 先对齐 OpenAI 兼容路径 |

---

## 4. 红线与自检记录

- ✅ 文件可打开：原始 HTML 628,559 字节可读；转换文本 545 行，首尾完整（头 `:1-3` 标题，尾 `:543-545` 错误示例+页脚），无截断。
- ✅ 引用可复核：本笔记每条事实均带 `sources/sf-chat-completions-api.txt:<行>`；折叠面板未展开处（messages/tools/choices/usage 内部）已明确说明并改引示例行号，未编造字段表。
- ✅ 覆盖任务指定范围：endpoint/认证/方法（1.1）、全部请求参数逐字段（1.2/1.3）、messages 各 role（1.4）、tools/tool_choice（1.6）、stream（`:49-53`）、thinking 三参数（`:59-75`）、采样参数（temperature/top_p/top_k/min_p/freq_penalty/n）、max_tokens（`:55`）、响应结构与 usage（1.7）、finish_reason、错误码/限流（1.8）、官方示例（1.9）。
- ✅ 无编造：`tool_choice` 取值、`role:"tool"` 消息、`finish_reason` 枚举、流式 delta 结构在原文缺失处均已标「推断」或「原文未文档化」，未虚构。
- ✅ 只读该文档、只写本目录下文件；未改任何代码、未做 git、未调任何需 key 的接口、未抓无关页面。
- ⚠️ 遗留（推断/待联调）：① `role:"tool"` 回传消息形状本页未文档化；② `tool_choice` 是否支持 required/none/指定函数名未验证；③ finish_reason 完整枚举（length/tool_calls…）未文档化；④ Streaming/Image/Function 三个响应 tab 在静态 HTML 为空。以上需有 key 后真实联调补全，本笔记不臆测。
