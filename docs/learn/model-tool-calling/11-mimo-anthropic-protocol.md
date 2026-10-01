# MiMo Anthropic 兼容协议 学习笔记

> **文档来源**：
> - 在线抓取（已落盘）：`/home/user/Doubao/chats/38438160041798146/docs/learn/model-tool-calling/sources/mimo-anthropic.txt`
>   （源 URL：https://mimo.mi.com/docs/zh-CN/api/chat/anthropic-api ，字段表 2863 token 全文读完；
>   镜像 https://mimo.mi.com/docs/api/chat/anthropic-api 、https://platform.xiaomimimo.com/docs/en-US/api/chat/anthropic-api ）
> - 本地精读成果：`sources/mimo-openai-api.txt`（OpenAI 兼容 API 全文）、`sources/mimo-quickstart.txt`（首次调用）、
>   `sources/mimo-faq.txt`（API Integration FAQ）
>
> **精读方式**：web_fetch pagination 在线抓取 + 三份已有本地文本交叉印证
> **精读日期**：2026-10-01　**作者**：MainAgent
> **覆盖边界**：f（MiMo Anthropic 兼容协议：tool_use/tool_result 内容块、disable_parallel_tool_use 控制并行），
> 并兼及 a–e 与 f 的对照（双协议互转）
>
> **标注约定**：「原文」= MiMo 文档逐字；「推断」= 原文无直接证据、按 Anthropic/Claude 标准形状补的合理推断。
> **完整度声明**：MiMo Anthropic 页面是 SPA，「函数调用」tab 的完整请求/响应示例 JSON（含 tool_use/tool_result
> 块逐字段展开）由前端 JS 切换渲染，web_fetch 抓不到；本笔记中凡涉及该 tab 的具体块形状，均标注「原文折叠未展开 / 推断」。

---

## 1. 原文事实清单

### 1.1 Endpoint 与认证（对照任务点 1）

| 事实 | 出处 | 要点 |
|---|---|---|
| Anthropic 兼容 endpoint = `https://api.xiaomimimo.com/anthropic/v1/messages`（POST） | sources/mimo-anthropic.txt:20 | 实时推理主域；`/anthropic` 前缀 + `/v1/messages` 路径两段拼出 |
| 按量付费 BASE_URL = `https://api.xiaomimimo.com/anthropic`；Token Plan 专属域为 `https://token-plan-cn.xiaomimimo.com/anthropic` | sources/mimo-quickstart.txt:23,25 | base_url 是「域名 + /anthropic」，SDK 内部再补 `/v1/messages` |
| 认证两种二选一：`api-key: $MIMO_API_KEY` 或 `Authorization: Bearer $MIMO_API_KEY`，均须配 `Content-Type: application/json` | sources/mimo-anthropic.txt:22-28；sources/mimo-faq.txt:109 | 与 OpenAI 侧请求头完全一致（sources/mimo-openai-api.txt:21-24），换协议不用换 key |
| Key 格式：按量 `sk-xxxxx`；Token Plan 个人版 `tp-xxxxx` / 团队版 `ttp-xxxxx`，两类独立不可混用 | sources/mimo-faq.txt:115；sources/mimo-quickstart.txt:23-25 | key 形态决定计费通道，与协议形态正交 |
| 平台同时兼容 OpenAI / Anthropic 两种格式，可用现有 SDK 直连 | sources/mimo-quickstart.txt:9 | Anthropic 侧建议用官方 `anthropic` Python SDK（`pip install -U anthropic`，:97-101） |

### 1.2 请求结构差异（对照任务点 2）

| 概念 | Anthropic 兼容（MiMo 原文） | OpenAI 兼容（MiMo 原文） | 出处 |
|---|---|---|---|
| system 提示词 | **顶层参数** `system: string \| array`，不塞进 messages | messages 数组里 `{"role":"system","content":...}` | Anthropic: sources/mimo-anthropic.txt:54-55；OpenAI: sources/mimo-openai-api.txt:128；FAQ 佐证: sources/mimo-faq.txt:139「有独立 system 参数」 |
| messages.role 可选值 | `user` / `assistant` / `system`（页面枚举如此列示） | developer / system / user / assistant / tool | sources/mimo-anthropic.txt:39 vs sources/mimo-openai-api.txt:28-31 |
| messages.content | `string \| array`（内容块数组，每块 `{"type":"text","text":...}`） | `string \| array`（OpenAI 侧以 string 为主） | sources/mimo-anthropic.txt:40；SDK 示例 sources/mimo-quickstart.txt:118-127 |
| 生成长度上限参数 | **`max_tokens`**，范围 [1,131072]；v2.6 全系默认 131072、v2.5 默认 32768 | **`max_completion_tokens`**，默认值与范围相同 | sources/mimo-anthropic.txt:44-46 vs sources/mimo-openai-api.txt:38-39 |
| 停止序列参数 | **`stop_sequences`**（array） | **`stop`**（string \| array，最多 4 个） | sources/mimo-anthropic.txt:48-50 vs sources/mimo-openai-api.txt:45 |
| temperature / top_p | temperature 默认 1.0、范围 [0,1.5]；top_p 默认 0.95、[0.01,1.0]；思考模式下两者被强制 1.0 / 0.95 | 完全同值 | sources/mimo-anthropic.txt:59-61,100-103 vs sources/mimo-openai-api.txt:55,71 |
| stream | boolean 默认 false（SSE） | boolean 默认 false（SSE） | sources/mimo-anthropic.txt:52 vs sources/mimo-openai-api.txt:47 |
| 模型枚举 | `mimo-v2.6-flash / -pro / -pro-ultraspeed / -v2.5-pro / -v2.5` | 同左 | sources/mimo-anthropic.txt:41 vs sources/mimo-openai-api.txt:34 |

> SDK 侧实证（Anthropic Python SDK 示例逐字）：`client = Anthropic(api_key=..., base_url="https://api.xiaomimimo.com/anthropic")`，
> 调 `client.messages.create(model="mimo-v2.6-pro", max_tokens=1024, system="You are MiMo…",
> messages=[{"role":"user","content":[{"type":"text","text":"please introduce yourself"}]}], top_p=0.95, stream=False,
> temperature=1.0, stop_sequences=None)`（sources/mimo-quickstart.txt:108-132）。

### 1.3 工具声明格式：tools 内容块（对照任务点 3）

**MiMo Anthropic 侧 tools 字段（原文逐字段，sources/mimo-anthropic.txt:74-89）**：

| 字段 | 类型 | 必选 | 原文要点 |
|---|---|---|---|
| `tools[].name` | string | 必选 | 工具名称；模型通过它调用该工具，也是 `tool_use` 块中使用的名称（:83-84） |
| `tools[].description` | string | 可选 | 「可选，但强烈推荐」；描述应尽可能详细，可用自然语言强化 input JSON schema 中的信息（:82,85） |
| `tools[].type` | string | 可选 | 可选值仅 `custom`（:85） |
| `tools[].input_schema` | object | 必选 | 工具输入形状的 JSON Schema，模型在 `tool_use` 输出块中生成（:81,86） |
| `input_schema.type` | string | 必选 | 仅为 `object`（:87） |
| `input_schema.properties` | object \| null | — | 工具输入的属性（:88） |
| `input_schema.required` | array \| null | — | 必须包含的属性列表（:89） |

**与 OpenAI 侧 tools 字段对照表（协议适配器核心映射表）**：

| Anthropic 形状 | OpenAI 形状（MiMo 原文） | 转换说明 | 出处 |
|---|---|---|---|
| `tools[]` | `tools[]` | 数组整体平移 | — |
| `tools[].type = "custom"` | `tools[].type = "function"`（MiMo：目前仅支持 function） | 字面量替换 | sources/mimo-anthropic.txt:85 vs sources/mimo-openai-api.txt:64 |
| `tools[].name` | `tools[].function.name` | 提一层 | sources/mimo-openai-api.txt:66（命名规则 a-z/A-Z/0-9/_/-，≤64） |
| `tools[].description` | `tools[].function.description` | 提一层 | sources/mimo-openai-api.txt:67 |
| `tools[].input_schema` | `tools[].function.parameters` | 整体平移，内部 properties/required 不变 | sources/mimo-openai-api.txt:68 |
| （无对应字段） | `tools[].function.strict`（默认 false） | Anthropic 侧页面未列 strict 字段 → 推断：发送时丢弃，不映射 | sources/mimo-openai-api.txt:69 |

### 1.4 响应与回传：tool_use / tool_result 内容块（对照任务点 4）

**MiMo Anthropic 侧响应结构（原文，sources/mimo-anthropic.txt:96-115）**：

| 字段 | 原文要点 |
|---|---|
| `id` | 对话唯一标识符（:97） |
| `type` | 对 Messages 始终为 `message`（:98） |
| `role` | 始终为 `assistant`（:99） |
| `content` | 内容块数组，每块有 `type`；折叠类型为 **Text / Thinking / Tool use** 三种（:100-103） |
| `stop_reason` | `end_turn`（自然停止）/ `max_tokens` / **`tool_use`（模型调用了一个或多个工具）** / `content_filter` / `repetition_truncation`（:105-111） |
| `usage` | `input_tokens` / `output_tokens` / `cache_read_input_tokens`（:112-115） |

**tool_use 块字段（原文只给折叠名，子字段未展开）**：

| 块字段 | 原文证据 | 形状 |
|---|---|---|
| `{"type":"tool_use", "id":..., "name":..., "input":{...}}` | 原文：tools.name「模型将通过它调用该工具，并是在 tool_use 块中使用的名称」（:84）；input_schema「模型将在 tool_use 输出内容块中生成」（:86）；响应 content 折叠含 "Tool use · object"（:101） | **推断**：id/name/input 三子字段。`input` 是 **JSON object**（对比 OpenAI 侧 `function.arguments` 是 **JSON string**，见 sources/mimo-openai-api.txt:87） |

**tool_result 块（回传方向）**：

| 块字段 | 原文证据 | 形状 |
|---|---|---|
| `{"type":"tool_result", "tool_use_id":..., "content":...}` | 原文：「您可以使用模型生成的工具输入运行这些工具，然后选择性地返回结果给模型，使用 tool_result 内容块」（:76） | **推断**：tool_use_id 配对 assistant 的 tool_use.id；content 为结果文本 |

**与 OpenAI 侧回传对照表（第二张核心映射表）**：

| Anthropic 形状 | OpenAI 形状（MiMo 原文） | 转换说明 | 出处 |
|---|---|---|---|
| 响应顶层 `content[]` 里的 `tool_use` 块 | `choices[0].message.tool_calls[]` | 数组平移；stop 判断：Anthropic `stop_reason=="tool_use"` ↔ OpenAI `finish_reason=="tool_calls"` | sources/mimo-anthropic.txt:109 vs sources/mimo-openai-api.txt:76,82 |
| tool_use.`id` | tool_calls.`id`（形如 `call_01e4…`） | 一一配对，原样透传 | sources/mimo-openai-api.txt:83,252-253 |
| tool_use.`name` | tool_calls.function.`name` | 提一层 | sources/mimo-openai-api.txt:86 |
| tool_use.`input`（object） | tool_calls.function.`arguments`（**string**） | **关键差异**：object ↔ JSON string，发送前 `json.dumps`、接收后 `json.loads` | sources/mimo-openai-api.txt:87,245 |
| `stop_reason: "tool_use"` | `finish_reason: "tool_calls"` | 终止信号映射 | sources/mimo-anthropic.txt:109 vs sources/mimo-openai-api.txt:76 |
| 回传：`{role:"user", content:[{type:"tool_result", tool_use_id, content}]}` | 回传：`{role:"tool", tool_call_id, content}` | **关键差异**：Anthropic 把工具结果塞进 **user 消息的 content 块**；OpenAI 用独立 role=tool 消息 | OpenAI 逐字格式 sources/mimo-openai-api.txt:260-264 |
| `usage.input_tokens / output_tokens` | `usage.prompt_tokens / completion_tokens` | 字段名映射；`cache_read_input_tokens` ↔ `prompt_tokens_details.cached_tokens` | sources/mimo-anthropic.txt:113-115 vs sources/mimo-openai-api.txt:96-99 |

> **原文警告（双侧共用）**：模型生成的参数不保证合法 JSON，且可能虚构 schema 外参数，调用前必须校验
> （OpenAI 侧逐字警告 sources/mimo-openai-api.txt:88-89；Anthropic 侧 input_schema 是同一套 JSON Schema，推断该警告同样适用——runtime 校验逻辑两协议共用）。

### 1.5 disable_parallel_tool_use（对照任务点 5）

| 事实 | 出处 | 原文 |
|---|---|---|
| 参数位置：**`tool_choice.disable_parallel_tool_use`**（boolean，默认 false），**不是顶层参数** | sources/mimo-anthropic.txt:72 | 嵌套在 tool_choice 对象内 |
| 语义：设为 true 时，当 `tool_choice.type` 为 `auto`，**模型将输出至多一个工具使用** | sources/mimo-anthropic.txt:72-73 | 原文逐字 |
| `tool_choice.type` 可选值仅 `auto`；传非 auto 值后端会移除该字段、行为等同 auto | sources/mimo-anthropic.txt:69-71 | 与 OpenAI 侧 `tool_choice` 强制 auto 完全同构（sources/mimo-openai-api.txt:57-59） |
| 不设该参数时：模型默认可在一个响应里输出**多个** tool_use 块 | 推断（由「true 时限到至多一个」反推默认并行；OpenAI 侧实证并行：一轮返回两个 tool_calls，sources/mimo-openai-api.txt:251-256） | 配对回传规则：N 个 tool_use 块 → N 个 tool_result 块，按 `tool_use_id == tool_use.id` 严格配对（推断，配对机制与 OpenAI 侧 `tool_call_id` 一致，sources/mimo-openai-api.txt:260-263） |

### 1.6 思考模式在 Anthropic 协议下的表现（对照任务点 6）

| 事实 | 出处 | 要点 |
|---|---|---|
| 请求侧：`thinking: {"type": "enabled" \| "disabled"}`，必选，全部模型默认 `enabled` | sources/mimo-anthropic.txt:63-66 | 与 OpenAI 侧 `thinking` 对象同名（sources/mimo-openai-api.txt:49-53） |
| 响应侧：思考内容作为 **`thinking` 内容块**出现在 `content[]`（折叠类型 Text / Thinking / Tool use，:101） | sources/mimo-anthropic.txt:101,135 | 对应 OpenAI 侧 `message.reasoning_content` 字符串（sources/mimo-openai-api.txt:80） |
| 多轮工具调用时，模型会在返回 `tool_use` 块的同时返回 `thinking` 块；**后续请求 messages 中必须保留全部历史 thinking 内容块**，否则表现劣化 | sources/mimo-anthropic.txt:62,78 | 对应 OpenAI 侧硬规则：assistant 含工具调用时缺失 reasoning_content 会直接 400（sources/mimo-openai-api.txt:229-231） |
| 流式：`delta.type = thinking_delta`，`delta.thinking` 为思考增量；`input_json_delta` + `delta.partial_json` 为工具入参 JSON 片段，**按到达顺序拼接成完整 JSON 后解析** | sources/mimo-anthropic.txt:134-137 | 对应 OpenAI 侧 `delta.reasoning_content`（sources/mimo-openai-api.txt:106）与 `delta.tool_calls[].function.arguments` 分片（:114） |
| thinking 块子字段（`thinking` 文本 / `signature` 签名） | **原文未展开**（页面折叠） | 推断：按 Anthropic 标准为 `{type:"thinking", thinking:"…", signature:"…"}`；MiMo 是否要求原样回传 signature 未见原文 |

### 1.7 官方示例（对照任务点 7）

**基础调用请求（逐字，sources/mimo-anthropic.txt:143-158）**：

```jsonc
{
 "model": "mimo-v2.6-pro",
 "max_tokens": 1024,
 "system": "You are MiMo, an AI assistant developed by Xiaomi. Today is date: Tuesday, December 16, 2025. Your knowledge cutoff date is December 2024.",
 "messages": [{"role": "user", "content": [{"type": "text", "text": "please introduce yourself"}]}],
 "top_p": 0.95, "stream": false, "temperature": 1.0, "stop_sequences": null,
 "thinking": {"type": "disabled"}
}
```

**基础调用响应（逐字，sources/mimo-anthropic.txt:162-180）**：

```jsonc
{
    "id": "23894237-e793-4156-965e-56cea4295290_a43d65ad5fb048e7ba57e27e689b2bf8",
    "type": "message", "role": "assistant", "model": "mimo-v2.6-pro",
    "stop_reason": "end_turn",
    "content": [{"type": "text", "text": "Hey there! I'm MiMo, Xiaomi's AI assistant…"}],
    "usage": {"input_tokens": 57, "output_tokens": 41}
}
```

> **原文缺失**：函数调用（tools + tool_use/tool_result）与深度思考两个 tab 的完整示例 JSON 在 SPA 中未被抓到，
> 上文 1.4/1.6 的块形状为「原文折叠名 + Anthropic 标准形状」的推断组合，落地前建议用一次真实调用（需 API key，本纪律禁止）验证。

---

## 2. 对 MBDSDR 工具化实现的启示

### 2.1 双协议抽象层设计（对照任务点 8）

runtime 内部应维护一套**中间形状（Canonical Shape）**，协议只在边界互转：

```
Canonical:
  SystemPrompt        string
  Message             { role: "user"|"assistant"|"tool",
                        text: string,
                        thinking: string,                // 跨协议统一收纳
                        tool_calls: [{id, name, args(JSON object)}],
                        tool_results: [{tool_call_id, content}] }
  ToolDef             { name, description, input_schema(JSON Schema) }
  SamplingParams      { max_tokens, temperature, top_p, stop_seq[], thinking_enabled }
  → LLMClient.send(canonical) -> CanonicalResponse { text, thinking, tool_calls, stop_reason, usage }
```

两个适配器 `OpenAIAdapter` / `AnthropicAdapter` 各做双向转换，转换规则就是 §1.3 / §1.4 的两张对照表。

### 2.2 具体到 src/ai/llm_client 的建议

| 组件 | 建议 | 依据 |
|---|---|---|
| `llm_client` 配置项 | 增加 `protocol: "openai" \| "anthropic"`；base_url 拼接规则随之变：OpenAI 接 `/v1/chat/completions`，Anthropic 接 `/anthropic/v1/messages` | sources/mimo-anthropic.txt:20；sources/mimo-openai-api.txt:15 |
| system 提示词 | Canonical 层独立存 system；发 Anthropic 时提到顶层 `system=`，发 OpenAI 时包成 `{role:"system"}` 塞进 messages | sources/mimo-anthropic.txt:54 vs sources/mimo-openai-api.txt:128 |
| 长度/停止参数映射 | `max_tokens` ↔ `max_completion_tokens`；`stop_sequences` ↔ `stop` | sources/mimo-anthropic.txt:44,48 vs sources/mimo-openai-api.txt:38,45 |
| 工具声明序列化 | OpenAI 输出 `{type:function, function:{name, description, parameters}}`；Anthropic 输出 `{type:custom, name, description, input_schema}`；`strict` 字段 Anthropic 侧丢弃 | §1.3 对照表 |
| 工具入参 | Canonical 统一存 **JSON object**；发 OpenAI 时 `json.dumps(arguments)`；收 Anthropic 时 `input` 已是 object 直用；**收 OpenAI 时必须 `json.loads(arguments)` 并做 schema 校验 + 参数白名单** | sources/mimo-openai-api.txt:87-89 |
| 工具结果回传 | OpenAI：`{role:"tool", tool_call_id, content}` 每条一条消息；Anthropic：追加一条 `{role:"user", content:[{type:"tool_result", tool_use_id, content}]}`，N 个结果合并进一个 user 消息的 content 数组 | §1.4 对照表；OpenAI 逐字 sources/mimo-openai-api.txt:260-264 |
| 终止判定 | OpenAI `finish_reason=="tool_calls"` ↔ Anthropic `stop_reason=="tool_use"`；两者都可能一轮多块 | sources/mimo-anthropic.txt:109 vs sources/mimo-openai-api.txt:76 |
| 并行控制 | 需要串行执行工具链时，Anthropic 侧发 `tool_choice:{type:"auto", disable_parallel_tool_use:true}`；OpenAI 侧 MiMo 无对应参数（tool_choice 传非 auto 会被后端剥掉）→ **串行化只能在 Anthropic 协议下做服务端约束；OpenAI 协议下靠客户端自然串行执行** | sources/mimo-anthropic.txt:69-73 vs sources/mimo-openai-api.txt:57-59 |
| thinking 保留 | Anthropic 协议下把响应 content 里的 `thinking` 块**原样保留**进下一轮 assistant 消息的 content 数组（夹在 text/tool_use 块之间）；OpenAI 协议下保留 `reasoning_content` 字段。两者缺一都劣化/400 | sources/mimo-anthropic.txt:62,78 vs sources/mimo-openai-api.txt:229-231 |
| usage 统计 | 统一映射到 prompt/completion tokens + cached tokens | §1.4 对照表 |
| 流式 | OpenAI：`delta.reasoning_content` / `delta.tool_calls[i].arguments` 分片；Anthropic：`thinking_delta` / `input_json_delta`+`partial_json` 拼接。流式聚合器按协议分两套 | sources/mimo-anthropic.txt:134-137 vs sources/mimo-openai-api.txt:106-114 |

### 2.3 双协议下的 Agent 循环伪代码

```python
# Canonical 循环（协议无关）
resp = llm_client.send(system, messages, tools, sampling)
save_thinking_blocks(resp)                     # 两协议都必须原样回传
if resp.stop_reason not in ("tool_calls", "tool_use"):
    return resp.text
for tc in resp.tool_calls:                    # 一轮可能多个（并行）
    args = schema_validate(tc.args, tooldef.input_schema)   # 拒绝 schema 外参数
    messages.append(tool_result(tc.id, run_tool(tc.name, args)))
# 循环直到 end_turn / stop
```

---

## 3. 与能力边界映射

| 边界 | 文档依据 | 实现要点 |
|---|---|---|
| f：Anthropic 兼容协议 | sources/mimo-anthropic.txt:20（endpoint）、:76（tool_use/tool_result 流程）、:72（disable_parallel_tool_use）、:109（stop_reason=tool_use） | 双适配器互转；system 顶层化；max_tokens/stop_sequences 改名；input(object) vs arguments(string) |
| a：function calling 循环（OpenAI 侧） | sources/mimo-openai-api.txt:82-89,260-264 | 循环骨架同上；OpenAI 用 finish_reason=tool_calls + role=tool 回传 |
| b：Interleaved Thinking 保留 | sources/mimo-anthropic.txt:62,78（thinking 块保留）↔ sources/mimo-openai-api.txt:229-231（reasoning_content 缺失=400） | 历史 thinking/reasoning_content 逐字保留，协议两侧形状不同但语义相同 |
| c：arguments 不合法/虚构参数 | sources/mimo-openai-api.txt:88-89（逐字警告）；Anthropic input_schema 同一套 JSON Schema（推断同样适用） | schema 校验 + 白名单，两协议共用校验器 |
| d：tool_choice 强制 auto | sources/mimo-anthropic.txt:69-71 ↔ sources/mimo-openai-api.txt:57-59（双侧同规则） | 不要做「强制指定工具」的产品假设 |
| e：thinking+tool 不稳定 | sources/mimo-openai-api.txt:208-215（tool_calls 混入 reasoning_content=不稳定信号）；Anthropic 侧对应现象为 thinking 块里混入 tool_use（推断） | 按模型配置决定工具轮是否关闭 thinking；检测到异常块结构要降级重试 |

---

## 4. 红线与自检记录

- [x] 文件可打开：`sources/mimo-anthropic.txt` 新建成功；引用的本地三文件均已通读
- [x] 每条引用可复核：`sources/mimo-anthropic.txt:<行>` 均对应本文件逐行；其余出处引用既有落盘文件
- [x] 覆盖任务指定范围：endpoint/认证（1.1）、请求结构差异（1.2）、tools 对照（1.3）、tool_use/tool_result 对照（1.4）、
  disable_parallel_tool_use（1.5）、思考模式（1.6）、官方示例（1.7）、runtime 启示（§2）全覆盖
- [x] 无编造：tool_use/tool_result/thinking 块的子字段形状均已标注「推断」（原文 SPA 折叠未展开）；strict 字段丢弃为推断
- [x] 在线补抓已如实记录：URL=https://mimo.mi.com/docs/zh-CN/api/chat/anthropic-api ，字段表 2863 token 全读；
  完整度缺陷（函数调用 tab 示例 JSON 未抓到）在头部与 §1.7 两次声明
- [x] 未调任何需 API key 的接口；只写 docs/learn/model-tool-calling/ 下文件；未动代码、未做 git 操作
- [x] 篇幅：约 230 行，符合 150–500 行
