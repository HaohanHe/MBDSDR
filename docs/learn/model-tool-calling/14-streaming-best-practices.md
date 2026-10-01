# 流式协议全览与最佳实践（SiliconFlow × 小米 MiMo）学习笔记

> 文档来源：
> - 本地精读：`sources/sf-stream-mode.txt`（SF 流式入门中文页）、`sources/sf-chat-completions-api.txt`（SF 创建对话请求 API 参考中文站）、`sources/mimo-openai-api.txt`（MiMo OpenAI 兼容 API + 两份补充来源）、`sources/mimo-quickstart.txt`（MiMo 首次调用）
> - 在线补抓（2026-10-01，web_fetch 分页全文读完）：`sources/sf-stream-extras.txt`，源自
>   https://docs.siliconflow.com/en/userguide/guides/interleaved-thinking （全文 2828 字符一次读完）与
>   https://docs.siliconflow.com/en/api-reference/chat-completions/chat-completions （全文 1942 字符一次读完）
> 精读方式：本地纯文本逐行精读 + 在线 web_fetch 在线抓取落盘
> 精读日期：2026-10-01；作者：MainAgent
> 覆盖边界：**b（Interleaved Thinking 流式侧）+ a（Function Calling 流式侧全展开）**；顺带覆盖 finish_reason/usage/错误码收尾。
> 标注约定：「原文」= 直接来自 sources/*.txt；「设计」= 本笔记基于两家协议推导出的实现方案，非原文；「推断」= 原文无直接证据。

---

## 1. 原文事实清单

### 1.1 开启流式的方式与 SSE 传输形态

| 事实 | 出处 | 要点 |
|---|---|---|
| SF：请求体 `stream: true` 即开流式；token 以 SSE 返回，通常以 `data: [DONE]` 结束 | `sf-chat-completions-api.txt:49-51`；`sf-stream-extras.txt §stream` | 两家文档用词一致：SSE + `data: [DONE]` |
| SF：用 OpenAI Python SDK 时 `client.chat.completions.create(..., stream=True)` | `sf-stream-mode.txt:71-77` | 推荐路径 |
| SF：裸 requests 必须**两处**都开 stream——payload 里 `"stream": True` **且** `requests.post(..., stream=True)`，否则不按流返回 | `sf-stream-mode.txt:104,120,129` | 双端 runtime 若手写 HTTP，两个开关都要设 |
| SF：requests 侧解析按行读，剥掉前缀 `data: `，判断 `!= "[DONE]"` 后 `json.loads` | `sf-stream-mode.txt:135-139` | 即行帧协议：一行一个 `data: {...}` |
| SF：curl 需 `-N/--no-buffer` 否则 curl 自己缓冲，看不到逐块效果 | `sf-stream-mode.txt:153,155` | 调试期注意 |
| SF：响应头含 `x-siliconcloud-trace-id`；请求可传 `X-Trace-Id` / `traceparent` 做追踪 | `sf-chat-completions-api.txt:25-31,325` | 排障用 |
| MiMo：请求体 `stream` 布尔，默认 `false`；`true` 时通过 SSE 流式传输 | `mimo-openai-api.txt:47` | 与 SF 同义 |
| MiMo：endpoint `https://api.xiaomimimo.com/v1/chat/completions`；鉴权头 `api-key: $MIMO_API_KEY` + `Content-Type: application/json`（也支持 `Authorization: Bearer`） | `mimo-openai-api.txt:15,21-24` | 注意 MiMo 主鉴权头是 `api-key` 而非 `Authorization` |
| MiMo：quickstart 示例显式 `"stream": false` | `mimo-quickstart.txt:84,134,162` | 默认非流式，流式需显式打开 |

**SSE 帧形态（两家交叉）**：每条消息一行 `data: {json}\n\n`；客户端逐行读，`data: ` 后是 JSON，最后一行字面量 `data: [DONE]` 表示流结束。MiMo 另给了 chunk 的 `object` 字段固定为 `chat.completion.chunk`（`mimo-openai-api.txt:117`）。

### 1.2 delta 字段全景（逐家原文证据）

MiMo 把流式 chunk 结构列得最完整（`mimo-openai-api.txt:101-118`）：

| delta 字段 | 类型/含义 | MiMo 原文出处 | SF 侧对应证据 |
|---|---|---|---|
| `choices[].delta.content` | 正文增量字符串 | `mimo-openai-api.txt:105` | `sf-stream-mode.txt:141`（`delta.get('content','')`，非空才 `+=`） |
| `choices[].delta.reasoning_content` | 推理（思维链）增量 | `mimo-openai-api.txt:106`（"推理内容增量"） | `sf-stream-mode.txt:142`（`delta.get('reasoning_content','')`，独立累加）；`sf-stream-extras.txt §3` |
| `choices[].delta.role` | 首包通常为 `"assistant"` | `mimo-openai-api.txt:107` | （SF 本地页未列，推断同 OpenAI 首包带 role） |
| `choices[].delta.tool_calls[]` | 工具调用增量数组 | `mimo-openai-api.txt:108` | `sf-chat-completions-api.txt:467`（`if delta.tool_calls:`，示例在此截断）；`sf-stream-extras.txt §3`（"Collect tool requests from `tool_calls` (or `delta.tool_calls` if streaming)"） |
| └ `tool_calls[].index` | 在 tool_calls 列表中的索引，**从 0 开始** | `mimo-openai-api.txt:109` | SF 本地/英文参考页未展开该字段（缺口，见 §1.2.1） |
| └ `tool_calls[].id` | 调用 ID，通常首片带 | `mimo-openai-api.txt:110` | 非流式消息体有 `tool_calls[].id`（`sf-stream-extras.txt §200`；`sf-chat-completions-api.txt:483-487` 同类） |
| └ `tool_calls[].type` | 仅 `"function"` | `mimo-openai-api.txt:111` | 同上 `"type":"function"`（`sf-stream-extras.txt §200`） |
| └ `tool_calls[].function.name` | 函数名增量（通常首片即全量） | `mimo-openai-api.txt:113` | 非流式 `function.name`（`sf-stream-extras.txt §200`） |
| └ `tool_calls[].function.arguments` | **JSON 字符串逐片增量**，需客户端拼接 | `mimo-openai-api.txt:114` | 非流式 `function.arguments` 为 JSON 字符串（`sf-stream-extras.txt §200`） |
| `choices[].finish_reason` | 字符串或 null；末片携带 | `mimo-openai-api.txt:115` | 非流式示例 `"finish_reason":"stop"`（`sf-chat-completions-api.txt:488`；`sf-stream-extras.txt §200`） |
| `choices[].index` | 候选下标 | `mimo-openai-api.txt:116` | 非流式 `"index":0`（`sf-chat-completions-api.txt:482`） |
| 顶层 `usage` | 仅**最后一个 chunk** 携带 | `mimo-openai-api.txt:118`；`mimo-openai-api.txt:272`（"usage 在最后一个 chunk (choices:[]) 携带"） | SF 非流式 usage 字段全量（`sf-chat-completions-api.txt:491-503`） |

#### 1.2.1 SF 流式 tool_calls chunk 细节的缺口（如实记录）

SF 本地中文 `sf-chat-completions-api.txt:463-468` 的 `for chunk in stream:` 循环在 `if delta.tool_calls:` 后**空行截断**，没有给出 index/id/function.name/arguments 的逐片结构；英文 API 参考页 `sf-stream-extras.txt §来源2` 的 200 示例也只画了**非流式** message。SF 英文 interleaved-thinking 页给的累加 demo 是 `tool_calls.extend(delta.tool_calls)`（`sf-stream-extras.txt §4`），**未按 index 分槽**——该 demo 是教学简化版。

**结论（设计）**：流式 tool_calls 的「按 index 分槽、arguments 逐片拼接」结构，在 MBDSDR 里以 **MiMo 文档（`mimo-openai-api.txt:108-114`）为权威字段定义**实现；SF 是 OpenAI 兼容协议（`sf-chat-completions-api.txt:3` "创建对话请求（OpenAI）"），按同一解析器消费即可。

### 1.3 流式 chunk 的逐包时序（MiMo 原文示例）

`mimo-openai-api.txt:269-272` 给出了纯思考流的 SSE chunk 序列：

| 阶段 | chunk delta 内容 | 出处 |
|---|---|---|
| 首包 | `{"content":"","role":"assistant","tool_calls":null,"reasoning_content":null}` | `mimo-openai-api.txt:269` |
| 思考中 | `{"content":null,"role":null,"tool_calls":null,"reasoning_content":"..."}`（reasoning_content 逐片来） | `mimo-openai-api.txt:270` |
| 收尾 | `{"content":null,"reasoning_content":null,"finish_reason":"stop"}` | `mimo-openai-api.txt:271` |
| usage | 最后一个 chunk，`choices:[]` 时带 `usage` | `mimo-openai-api.txt:272` |

要点：SF 侧 demo 也印证「reasoning_content 与 content 是两个独立键、各自累加、不同时出现」（`sf-stream-mode.txt:140-148`）；空 chunk（`choices` 为空）要 `continue` 跳过（`sf-stream-mode.txt:81-82`）。

### 1.4 finish_reason 取值与 [DONE] 的关系

| finish_reason 取值 | 含义/出处 |
|---|---|
| `stop` | 正常结束；两家非流式示例都用它（`sf-chat-completions-api.txt:488`；`mimo-openai-api.txt:146`） |
| `length` | 达到 max_tokens 上限截断（MiMo 枚举，`mimo-openai-api.txt:76`） |
| `tool_calls` | 模型发起了工具调用，本轮结束（MiMo 枚举，`mimo-openai-api.txt:76`） |
| `content_filter` | 内容过滤截断（MiMo，`mimo-openai-api.txt:76`） |
| `repetition_truncation` | 重复截断（MiMo 特有枚举，`mimo-openai-api.txt:76`） |
| `null` | 中间 chunk 恒为 null（`mimo-openai-api.txt:115` "string \| null"；`mimo-openai-api.txt:270-271`） |

**与 `[DONE]` 的关系（设计 + 原文）**：`data: [DONE]` 是传输层终止信号（`sf-chat-completions-api.txt:51`；`sf-stream-mode.txt:138`）；`finish_reason` 是业务层终止原因，出现在 `[DONE]` 之前的最后一个带 choices 的 chunk 里（`mimo-openai-api.txt:271`）。即顺序：末片带 `finish_reason` → 可能再来一个空 choices 带 usage 的 chunk → `data: [DONE]` → 连接关闭。

### 1.5 中断/限流/重连相关原文

| 事实 | 出处 |
|---|---|
| SF 429：限流，`Details:TPM limit reached.` | `sf-chat-completions-api.txt:530-533` |
| SF 503：`code 50505 "Model service overloaded. Please try again later."` | `sf-chat-completions-api.txt:536-540` |
| SF 504：网关超时（响应体 string） | `sf-chat-completions-api.txt:363,543` |
| （推断）流式中途断连的 resume：两家文档均**未**提供断点续传/重连续传的 SSE 字段，`[DONE]` 也不是幂等游标 | 两家 sources 全文检索无 resume/last-event-id |
| SF 建议流式用于缓解瞬时 503/504（博客语，非本批 sources） | 推断：`sf-chat-completions-api.txt:361-363` 仅列出错误码存在 |

> 说明：本批 sources 没有任何「断线重连后续传已生成 token」的原文，重连策略属设计层（见 §2.4）。

---

## 2. 对 MBDSDR 工具化实现的启示（设计）

### 2.1 双端共用的协议映射表（一张表喂 C++ 与 Flutter）

两家都是 OpenAI Chat Completions 形态，`delta.*` 字段名完全一致。建议在 runtime 里固化一份「流式 chunk → 内部事件」映射表，双端共用同一份字段名：

| SSE `data:` 解析后的 JSON 路径 | 内部事件 | 拼接动作 |
|---|---|---|
| `choices[0].delta.content` 非空 | `OnContentDelta(text)` | `fullContent += text` |
| `choices[0].delta.reasoning_content` 非空 | `OnReasoningDelta(text)` | `fullReasoning += text`（逐字，禁止改写） |
| `choices[0].delta.tool_calls[i]` | `OnToolCallDelta(slot, delta)` | 按 `index` 分槽累加（见 §2.2） |
| `choices[0].finish_reason != null` | `OnFinish(reason)` | 记录终止原因，停止累加 |
| 顶层 `usage` 非空 | `OnUsage(u)` | 记账 prompt/completion/reasoning tokens |
| 行文本 == `[DONE]` | `OnStreamEnd()` | 关闭读循环 |
| HTTP 429 / 503 / 504 | `OnError` | 退避重试（见 §2.4） |

### 2.2 流式 tool_calls 增量拼接算法（设计，C++ 与 Flutter 同构）

**核心：按 `index` 分槽；同一槽内 `name` 通常首片即全量、`arguments` 是字符串逐片 `+=`；流末对每个槽做一次完整 `JSON.parse(arguments)` + schema 校验。**

```
# 伪代码（双端语义一致）
slots = {}                      # index -> {id, type, name, arguments_buf}
for line in sse_lines:
    frame = parse_data_line(line)     # 剥 "data: "，"[DONE]" 则 break
    if not frame.choices: continue    # SF 空 chunk 跳过（sf-stream-mode.txt:81）
    d = frame.choices[0].delta
    if d.tool_calls:
        for tc in d.tool_calls:
            i = tc.index              # MiMo: index 从 0 起（mimo-openai-api.txt:109）
            s = slots.get(i) or {id:"", type:"function", name:"", args:""}
            if tc.id:   s.id = tc.id          # 首片带 id，后续片可能不带
            if tc.type: s.type = tc.type
            if tc.function.name: s.name += tc.function.name      # 通常首片完整
            if tc.function.arguments: s.args += tc.function.arguments  # 关键：字符串累加
            slots[i] = s
    if frame.choices[0].finish_reason:
        finish = frame.choices[0].finish_reason

# 流末：把 slots 按 index 排序产出完整 tool_calls
out = []
for i in sorted(slots):
    s = slots[i]
    try:
        args_obj = json_parse(s.args)        # 二次 parse，非流中 parse
    except JsonError:
        raise InvalidToolArguments(s.name, s.args)   # 衔接 09：schema 校验
    validate_against_schema(s.name, args_obj)         # 衔接 09：参数白名单
    out.append({id: s.id, type: s.type,
                function: {name: s.name, arguments: args_obj}})
```

要点与红线：
1. **不要边收边 `JSON.parse(arguments)`**——arguments 是分片到达的，半片一定不是合法 JSON（MiMo 警告「模型生成的内容并非总能保证是有效的 JSON，且可能虚构 schema 外参数」，`mimo-openai-api.txt:88-89,114`）。
2. **多工具并行 = 多个 index 槽**。MiMo 原文证实模型可一轮并行返回两个 tool_calls（`mimo-openai-api.txt:251-256`，北京天气 + 时区）；客户端必须逐槽拼接、逐个执行、各自 `role=tool + tool_call_id` 回传（`mimo-openai-api.txt:260-264`）。
3. **`tool_call_id` 回传必须严格匹配** assistant 消息里该槽的 `id`（`mimo-openai-api.txt:262` 注释 "必须与 assistant_message.tool_calls[i].id 严格匹配"）。
4. **不要照搬 SF 教学 demo 的 `tool_calls.extend(delta.tool_calls)`**（`sf-stream-extras.txt §4`）——那是非分片教学写法；真并行多分片会错位，必须用 index 槽。

### 2.3 流式 reasoning_content 的逐字保留语义（设计，衔接 02/08）

两家红线完全一致：

| 规则 | 原文 |
|---|---|
| 流式 `delta.reasoning_content` 逐片累加，与回传要求的 reasoning_content 是同一串 | SF：`sf-stream-extras.txt §3`（"Accumulate Interleaved Thinking text from `reasoning_content` (or `delta.reasoning_content` if streaming)"）；MiMo：`mimo-openai-api.txt:268`（先 reasoning_content 后 content） |
| 回传时必须逐字原样，不改写/不清理/不合并拆分/不重排 | SF：`sf-stream-extras.txt §2 What you must NOT do` |
| 保留范围 = 工具调用前 + 多次调用之间 + **工具结果之后**所有片段，保持原始顺序 | SF：`sf-stream-extras.txt §2`；MiMo：`mimo-openai-api.txt:50-51,62-63` |
| 缺失后果：多步工具链崩、缓存劣化、指令遵循下降/幻觉增多；MiMo 更硬——**后续请求缺 reasoning_content 直接 400** | MiMo：`mimo-openai-api.txt:229-231`；SF：`sf-stream-extras.txt §2`（Reduced cache efficiency） |

**对 runtime 的具体要求（设计）**：流式侧 `OnReasoningDelta` 进一个独立 buffer，与 `OnContentDelta` 的正文 buffer **物理隔离**；拼回合 assistant 消息时，`reasoning_content` 字段直接填这个 buffer 的完整字符串，不做任何 trim/正则清洗。C++ 端用 `std::string` append，Flutter 端用 `StringBuffer` append——和正文 append 同构，但分两条通道。

### 2.4 结束判定、中断与重连（设计）

- **正常结束**：读循环以「见到 `finish_reason`」为业务收尾信号，以「读到 `[DONE]`」为传输收尾信号；两者都要等。`finish_reason == "tool_calls"` → 进入工具执行循环；`== "stop"` → 终答完成；`== "length"` → 截断，要决定是否续跑（注意 SF `max_tokens` 不含思维链，`sf-chat-completions-api.txt:57`）。
- **usage 记账**：最后一个空 choices chunk 带 usage（`mimo-openai-api.txt:272`）；SF usage 含 `completion_tokens_details.reasoning_tokens` 与 cache hit/miss（`sf-chat-completions-api.txt:491-503`）——MBDSDR 的 token 账本应在这一笔入账，不要逐 chunk 估。
- **中断/重连（设计，原文无断点续传）**：两家文档都没有 SSE resume 字段。断线处理策略：
  1. 网络层断开且未到 `[DONE]`：**不做透明续传**，把已拼接的 `content/reasoning_content/tool_calls` 标记为 incomplete，向上层报「流中断」；
  2. 429（TPM）/ 503（overloaded）：指数退避后**整条请求重发**（不是续传）；
  3. 重发要复用同一 messages 历史（含已逐字保留的 reasoning_content），不要丢上下文。

### 2.5 对 runtime 的组件建议（设计）

| 端 | 组件 | 职责 |
|---|---|---|
| C++ | `src/ai/llm_client` 内拆 `SseParser` | 按行读 socket → 剥 `data: ` → 识别 `[DONE]` → JSON.parse 每帧 → 抛事件；独立于 HTTP 客户端，便于单测喂 mock SSE 文本 |
| C++ | `src/ai/stream_aggregator` | 持有 content / reasoning_content 两条 string + tool_calls index 槽表；消费 SseParser 事件；流末产出完整 `assistant_message`（含 tool_calls 槽） |
| C++ | `src/ai/tool_call_builder` | 对槽 arguments 做 `JSON.parse` + schema 校验（衔接 09），产出可执行工具调用 |
| Flutter | `mobile/.../llm_stream.dart`（对应 api_client） | 与 C++ 同构：SSE 解析 + 双 buffer + index 槽表；通过回调把 reasoning/tool_call delta 实时推给 UI，流末回传完整 message |
| 双端共享 | 一份 `stream_protocol.md` / `.ts` / `.h` 字段映射表 | 即本笔记 §2.1 表，作为双端唯一事实来源；新增字段先改表再改代码 |

**双端一致性红线（设计）**：C++ 与 Flutter 必须用**同一套拼接规则**——同一份 index 槽算法、同一条 reasoning 逐字 buffer、同一个「流末二次 parse」时机。否则同一轮 LLM 输出在桌面端和移动端会拼出不同 tool_calls。

---

## 3. 与能力边界映射

| 边界 | 文档依据 | 实现要点 |
|---|---|---|
| a) Function calling 流式侧 | `mimo-openai-api.txt:108-114`（delta.tool_calls 结构）；`sf-stream-extras.txt §3`（delta.tool_calls 累加）；`mimo-openai-api.txt:251-256`（并行多 tool_calls） | SseParser + index 槽拼接器；流末 JSON.parse + schema 校验；并行多槽逐个 role=tool 回传 |
| b) Interleaved Thinking 流式侧 | `sf-stream-mode.txt:142,146-148`；`sf-stream-extras.txt §2-3`；`mimo-openai-api.txt:106,268-271` | delta.reasoning_content 独立 buffer 逐字累加；回传原样不改造；工具结果后的新 reasoning 也要保留 |
| c) arguments 合法性 | `mimo-openai-api.txt:88-89,114`（不保证 JSON、可能虚构参数） | 流末二次 parse + schema 白名单校验；禁 eval 直执 |
| d) tool_choice | `mimo-openai-api.txt:58-59`（非 auto 被后端剥成 auto）；SF 示例 `tool_choice:"auto"`（`sf-chat-completions-api.txt:459`） | runtime 固定发 auto，别指望强制指定 |
| e) thinking + tool 稳定性 | `mimo-openai-api.txt:208-215`（tool_calls 混入 reasoning_content = 不稳定）；`sf-stream-extras.txt §Important Notice`（DeepSeek V3.2/GLM-4.7 在 tool 流会发 interleaved thinking）；SF enable_thinking=false 才能给 V3.1 function call（`sf-stream-extras.txt body`） | 按模型配置 thinking；出现 reasoning_content 里夹 tool_calls 文本要记告警 |
| f) Anthropic 协议 | `mimo-openai-api.txt:219-220`（MiMo 另有 /anthropic/v1/messages） | 本笔记只覆盖 OpenAI 兼容流式侧；Anthropic SSE（message_start/content_block_delta）另行整理 |

---

## 4. 红线与自检记录

- [x] 文件可打开：本笔记引用的 5 个 sources/*.txt 全部 Read 成功；在线两页 web_fetch 分页读到 `end_offset >= total_length`（2828/2828、1942/1942）。
- [x] 引用的每个 §/行号都在原文找到：行号引自 Read 输出；在线页章节标题引自落盘 `sf-stream-extras.txt`。
- [x] 覆盖任务指定范围：① 两家开流式方式 + SSE/data:/[DONE]/请求响应头（§1.1）；② delta 全景含 content/reasoning_content/tool_calls(index/id/type/name/arguments)/finish_reason/usage（§1.2）；③ 流式 tool_calls 增量拼接算法（§2.2）；④ reasoning_content 逐字保留（§2.3）；⑤ finish_reason 取值 + [DONE] 关系 + 中断重连（§1.4、§2.4）；⑥ runtime 双端组件建议（§2.5）。
- [x] 无编造：SF 流式 chunk 的 index 细节缺口已如实声明（§1.2.1），未把 OpenAI 通用结构伪装成 SF 原文；SF 教学 demo 的 `extend()` 与正确 index 槽算法的差异已标注。
- [x] 「推断」标注：§1.2.1 首包带 role、§1.5 重连 resume 缺失、§2.4 重发策略均标「推断/设计」。
- [x] 未改任何代码、未做 git、未调需 key 的接口；只写了 `docs/learn/model-tool-calling/` 下的 `14-streaming-best-practices.md` 与 `sources/sf-stream-extras.txt`。
