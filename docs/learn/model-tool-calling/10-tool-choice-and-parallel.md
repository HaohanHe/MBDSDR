# tool_choice 行为 + 并行工具调用 学习笔记

> 文档来源（本地纯文本，均为 2026-10-01 精读成果）：
> - 硅基流动 Chat Completions API 参考：`/home/user/Doubao/chats/38438160041798146/docs/learn/model-tool-calling/sources/sf-chat-completions-api.txt`
> - 硅基流动 Function Calling 指南：`/home/user/Doubao/chats/38438160041798146/docs/learn/model-tool-calling/sources/sf-function-calling.txt`
> - 小米 MiMo OpenAI 兼容 API：`/home/user/Doubao/chats/38438160041798146/docs/learn/model-tool-calling/sources/mimo-openai-api.txt`
> - 小米 MiMo 深度思考（回传 reasoning_content）：`/home/user/Doubao/chats/38438160041798146/docs/learn/model-tool-calling/sources/mimo-reasoning.txt`
> - 小米 MiMo FAQ API Integration：`/home/user/Doubao/chats/38438160041798146/docs/learn/model-tool-calling/sources/mimo-faq.txt`
> - 对照（思考稳定性）：`sources/sf-interleaved-thinking.txt`
>
> 精读方式：直接通读上述已落盘的纯文本精读成果（HTML 转文本 / web.fetch pagination 在线抓取，前序 agent 已完成落盘），本笔记仅做交叉整理，未新抓页面、未调任何 API。
> 精读日期：2026-10-01　作者：MainAgent 委派的 model-tool-calling 笔记 agent
> 覆盖边界：**d（tool_choice 后端强制 auto）全展开 + a 的并行侧（单次响应多个 tool_calls 的执行与配对回传）**为主；兼及 b/e（并行与思考模式并存）；f（Anthropic 协议侧 `disable_parallel_tool_use`）仅给指向不展开。

---

## 0. 一句话定位

本笔记把两家文档交叉成一条 runtime 规格：**别指望靠 `tool_choice` 强制模型调某个工具**——硅基流动的参数表里干脆没有这个字段（只在示例里出现 `auto`），MiMo 明说"传非 auto 值后端直接删掉、行为等同 auto"；同时 runtime 必须能处理**一次响应里挂多个 `tool_calls`**（MiMo 示例一次同时要天气+时间），逐个 `role:"tool"` 按 `tool_call_id` 严格配对回传，少一条都不行。

---

## 1. 原文事实清单

### 1.1 tool_choice：两家文档里到底写了什么

| 事实 | 出处 | 要点（逐字 / 定位） |
|---|---|---|
| 硅基流动 Chat Completions **请求参数表中没有 `tool_choice` 这一行** | `sources/sf-chat-completions-api.txt:35-319`（Request Body 全表） | 参数表逐项为 `model/messages/stream/max_tokens/enable_thinking/thinking_budget/reasoning_effort/min_p/stop/temperature/top_p/top_k/frequency_penalty/n/response_format/tools`（`:37-173` 主块、`:183-317` 视觉块重复一遍），**全程无 tool_choice 条目** |
| 硅基流动全文**唯一**出现 `tool_choice` 的地方是示例代码，且值就是 `"auto"` | `sources/sf-chat-completions-api.txt:459` | `client.chat.completions.create(..., tools=tools, tool_choice="auto", stream=True)`——即官方示例也只演示 auto，从未演示 `required` / 指定具体函数 |
| 硅基流动 Function Calling 指南**通篇未提 tool_choice** | `sources/sf-function-calling.txt:81-432`（§2 使用方式、§4 全部示例） | 两个完整示例（数值计算 `:284-314`、天气 `:387-423`）请求体都只带 `tools=`，没有 `tool_choice=` |
| MiMo `tool_choice` 参数定义逐字原文 | `sources/mimo-openai-api.txt:57-59` | 原文："tool_choice string — 控制模型如何选择工具。注意：**当 tool_choice 传入非 auto 值时，后端会默认移除该字段，模型响应行为仍等同于 auto 模式（该逻辑保留调整的可能性）。可选值: auto。**" |
| MiMo FAQ 的函数调用 curl 示例里也是 `"tool_choice": "auto"` | `sources/mimo-openai-api.txt:203`、`sources/mimo-faq.txt:195` | 两个来源一致：官方示例只出现 auto，从不演示强制指定 |
| MiMo 请求体里 `tools` 字段的细节 | `sources/mimo-openai-api.txt:61-69` | 仅支持 `type:"function"`；`name` 仅允许 `a-z/A-Z/0-9/_/-`、最长 64（`:66`）；`parameters` 为 JSON Schema、省略则参数列表为空（`:68`）；`strict` 布尔默认 false（`:69`） |

**交叉结论**：两家在"tool_choice 只能 auto、别指望强制指定"上口径一致，但强度不同——
- 硅基流动是"**文档不写这个参数**"（参数表无此行，只有示例 auto），属于事实标准 OpenAI 字段的隐式兼容；
- MiMo 是"**明文写死**"：传 `required`/指定函数名会被后端静默删掉，行为回落 auto，且括号注明"该逻辑保留调整的可能性"（即今天强制 auto，明天可能变）。

### 1.2 runtime 结论：tool_choice 一律只发 auto 或不发

| 结论 | 出处支撑 | 说明 |
|---|---|---|
| 不要在 runtime 里写死 `tool_choice: {type:"function", function:{name:"xxx"}}` 来"逼"模型调指定工具 | `mimo-openai-api.txt:58-59` | 后端会删掉这个字段，模型照样自由选择——**写了也不生效，等于自欺** |
| 想确保模型"必须用工具回答"，`tool_choice` 这条路在这两家都走不通 | 同上 + `sf-chat-completions-api.txt` 参数表无此行 | 强制指定工具（OpenAI 的 `required`）在 MiMo 被剥成 auto，在硅基流动文档层面根本未承诺；只能靠 prompt 引导 + tools 列表设计来影响，不能赌参数 |
| 安全做法：`tool_choice` 要么完全不发，要么只发 `"auto"` | `sf-chat-completions-api.txt:459`、`mimo-openai-api.txt:59`、`mimo-faq.txt:195` | 三家示例/定义都收敛到 auto；不发 = 平台默认（推断：OpenAI 兼容协议默认即 auto，两家未明说默认值，保守起见显式发 `"auto"`） |
| 该行为未来可能变 | `mimo-openai-api.txt:59` 括号原文"（该逻辑保留调整的可能性）" | runtime 不要把"强制指定一定无效"写成永久假设；但**当前**按"无效"来设计最稳 |

### 1.3 并行工具调用：单次响应多个 tool_calls 的原文证据

| 事实 | 出处 | 要点 |
|---|---|---|
| MiMo `tool_calls` 数组**可包含一个或多个**对象 | `sources/mimo-openai-api.txt:82` | 原文："choices.message.tool_calls array — 函数调用启动后返回待调用工具及参数。**可包含一个或多个。**" |
| 并行实证：一次响应同时挂 `get_current_weather` + `get_time` | `sources/mimo-reasoning.txt:138-140`（同例另见 `mimo-openai-api.txt:251-253`） | `tool_calls:[ {id:'call_01e402113df94ebfb85e3056', function:{name:'get_current_weather', arguments:'{"location":"Beijing"}'}}, {id:'call_f2bf1ab9f73842baa64e93d3', function:{name:'get_time', arguments:'{"timezone":"Asia/Shanghai"}'}} ]`——两个不同 id、两个不同函数，**同一条 assistant 消息里** |
| 模型自述"两个可以并行调" | `sources/mimo-reasoning.txt:134-136`、`mimo-openai-api.txt:254` | reasoning 原文："I can call both in parallel. … Call both together."——模型显式判断两调用无数据依赖 |
| 第二轮 user 追问后，模型再次并行调一对 | `sources/mimo-reasoning.txt:149-152` | Request 2-1：`tool_calls:[get_current_weather(Shanghai), get_time(Asia/Shanghai)]`——**并行不是一次性偶发，是稳定行为模式** |
| 硅基流动文档示例只演示单个 `tool_calls[0]` | `sources/sf-function-calling.txt:295-305,401-411` | SF 两个示例都取 `tool_calls[0]` 就执行；未给并行多调用示例（推断：SF 走 OpenAI 兼容协议，多 tool_calls 能力应一致，但 SF 文档未正面举证， runtime 实现时以 MiMo 示例为准） |
| `finish_reason`：工具调用轮次的终止原因 | `sources/mimo-openai-api.txt:76` | 可选值 `stop / length / tool_calls / content_filter / repetition_truncation`——模型决定调工具时 `finish_reason="tool_calls"`（推断：与 OpenAI 一致，该轮尚未出最终答案） |

### 1.4 执行顺序与 role=tool 配对回传

官方 `run_turn` 循环（`sources/mimo-reasoning.txt:107-124`，同逻辑 `mimo-openai-api.txt:239-247`）：

```
while True:
    resp = create(messages, tools, extra_body={"thinking":{"type":"enabled"}})
    assistant_message = resp.choices[0].message
    messages.append(assistant_message)          # 整条原样入历史（含 reasoning_content/content/tool_calls）
    if not assistant_message.tool_calls: break  # 无 tool_calls = 最终答案
    for tool_call in assistant_message.tool_calls:   # ← 逐个遍历
        name = tool_call.function.name
        args = json.loads(tool_call.function.arguments)
        result = TOOL_MAP[name](**args)
        messages.append({                       # ← 每个 tool_call 各回传一条 role:tool
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": result
        })
```

| 事实 | 出处 | 要点 |
|---|---|---|
| 官方示例的执行顺序 = **for 循环顺序遍历 tool_calls 数组**（顺序执行） | `sources/mimo-reasoning.txt:116-124`、`mimo-openai-api.txt:243-247` | 代码里 `for tool_call in assistant_message.tool_calls:` 是串行；但模型 reasoning 明说这两个调用"can call both in parallel"（`:134`）——**官方示例只是写法简单用串行，并未要求必须串行**（推断：无依赖关系的并行调用 runtime 可并发，见 §2.3） |
| 每个 `tool_call` 必须**单独回传一条** `role:"tool"` 消息，N 个 tool_calls → N 条 tool 消息 | `sources/mimo-openai-api.txt:247,260-264`、`mimo-reasoning.txt:120-124` | 原文示例：`messages.append({"role":"tool","tool_call_id":tool_call.id,"content":result})` 在 for 循环内逐条 append；并行两调用即回传两条 tool 消息（`mimo-reasoning.txt:141-142` 的 Tool result 1 / Tool result 2） |
| `tool_call_id` 必须与 assistant 消息里的 `tool_calls[i].id` **严格匹配** | `sources/mimo-openai-api.txt:262` | 原文注释："必须与 assistant_message.tool_calls[i].id 严格匹配" |
| 回传顺序是否必须保持 tool_calls 数组原序？ | `sources/mimo-openai-api.txt:243-247`（for 循环按数组序 append） | **原文只演示了"按数组序 append"，并未明文规定顺序是硬性要求**；配对的权威键是 `tool_call_id`（`:262`）。推断：只要每条 tool 消息的 `tool_call_id` 都能对上 assistant 里的某个 id，乱序回传大概率也能被接受；但 runtime 应**按 tool_calls 数组原序 append**，与官方示例保持一致最稳 |
| assistant 消息本身也要原样入历史（含 tool_calls 定义） | `sources/mimo-reasoning.txt:113`、`mimo-openai-api.txt:241-242` | `messages.append(assistant_message)`——不能只 append tool 结果；否则模型不知道当初自己请求了哪些调用 |

### 1.5 并行 × 思考模式并存时的注意

| 事实 | 出处 | 要点 |
|---|---|---|
| 思考模式下，模型会**在返回 tool_calls 的同时返回 reasoning_content** | `sources/mimo-openai-api.txt:50-51,62-63`、`mimo-faq.txt:143` | 原文："在思考模式下的多轮工具调用过程中，模型会在返回 tool_calls 字段的同时返回 reasoning_content 字段"——并行那轮也带 reasoning（`mimo-reasoning.txt:134-136`） |
| **强制红线**：开思考 + 历史有工具调用 + 本轮 assistant 带 tool_calls → 必须完整回传 reasoning_content，否则 **HTTP 400** | `sources/mimo-reasoning.txt:40-45` | 逐字："后续所有 user 交互轮次中回传的 assistant 如果包含了工具调用，必须完整回传 reasoning_content 字段，否则 API 将返回 400 错误。" |
| **不稳定信号**：tool_calls 混进 reasoning_content 文本里 = 输出不稳定/不完整 | `sources/mimo-faq.txt:200-202` | 原文："The appearance of `tool_calls` in the reasoning content indicates **instability and incomplete output** caused by the model having `thinking` enabled when calling `tool`." |
| 官方建议：**调 tool 时干脆关掉 thinking** | `sources/mimo-faq.txt:202`（中文同口径 `mimo-openai-api.txt:213-215`） | 原文："It is recommended to **disable `thinking` when calling `tool` calls** … to achieve a more stable and better user experience." |
| 硅基流动侧的同口径纪律：reasoning_content 逐字保留、工具结果后新冒的思考段也要留、禁止重排 | `sources/sf-interleaved-thinking.txt:87-100,103-108` | 原文 ❌："Modify / Clean up / Merge or split / **Reorder** segments / Drop"——即并行多 tool 场景下，assistant 消息里 reasoning_content 与 tool_calls 的相对顺序也不能动 |

> 两层建议别混：①主文档说"你要开着思考跑工具，那 reasoning_content 必须回传，否则 400"；②FAQ 说"更稳的做法是调工具时关思考"。runtime 两条都要实现（详见 08 号笔记 §1.6 与 §2.4）。

### 1.6 Anthropic 协议侧：`disable_parallel_tool_use`（仅指向，不展开）

| 事实 | 出处 | 处理 |
|---|---|---|
| MiMo 另提供 Anthropic 兼容协议入口 `/anthropic/v1/messages`（Claude 格式、独立 system 参数） | `sources/mimo-faq.txt:133-139`、`mimo-openai-api.txt:217-220` | 该协议才有 `disable_parallel_tool_use` 这类控制并行的开关（`_SPEC.md §4 f`） |
| **本篇不展开** | — | `disable_parallel_tool_use` 的语义、默认值、与 OpenAI 侧并行行为的差异，归 `11-mimo-anthropic-protocol.md` 专篇；本笔记只覆盖 OpenAI 兼容协议侧 |

---

## 2. 对 MBDSDR 工具化实现的启示

> 目标组件（按 `_SPEC.md §2` 点名）：`llm_client`（请求构造）、agent 多步循环 / `llm_worker` / `task_orchestrator`（执行器）、消息缓冲层。以下给字段级建议。

### 2.1 请求构造器：tool_choice 收敛成一个常量

```python
# llm_client 请求体构造 —— 永远不要出现强制指定工具
payload = {
    "model": model,
    "messages": messages,
    "tools": tool_schema_list,
    "tool_choice": "auto",     # ← 唯一合法值（或整行省略）
}
# 禁止出现：
#   "tool_choice": "required"
#   "tool_choice": {"type":"function","function":{"name":"xxx"}}
# 依据：mimo-openai-api.txt:58-59（非 auto 被后端剥离）；sf-chat-completions-api.txt:459（SF 示例只有 auto）
```

- 想让模型"优先用某个工具"，**改 prompt / 改 tools 描述**，不要赌 `tool_choice`（`mimo-openai-api.txt:59` 证明赌了也白赌）；
- 该行为"保留调整的可能性"（`mimo-openai-api.txt:59`），所以代码里把它做成**配置项** `tool_choice: "auto" | null`，未来 MiMo 放开强制指定时只改配置不改逻辑。

### 2.2 tool_call_id 配对表（runtime 必须建一张）

一次 assistant 响应可能带 N 个 tool_calls，runtime 不能"边遍历边丢 id"。收到 assistant 消息后先落一张配对表：

```python
# llm_worker 收到 assistant 消息后
assistant_msg = resp.choices[0].message
messages.append(assistant_msg)              # 整条原样入历史（含 reasoning_content）

pending = {}                                # tool_call_id -> (func_name, arguments_raw)
for tc in (assistant_msg.tool_calls or []):
    pending[tc.id] = {
        "name": tc.function.name,
        "arguments": tc.function.arguments,  # string，未必是合法 JSON（mimo-openai-api.txt:88-89）
    }

# 执行完后，按 pending 的 key 逐条回传，一条都不能少
for call_id, plan in pending.items():
    result = execute_tool(plan)             # schema 校验 + 白名单（边界 c，见 06 号笔记）
    messages.append({
        "role": "tool",
        "tool_call_id": call_id,             # ← 严格配对，mimo-openai-api.txt:262
        "content": result,
    })
# 防御：回传条数 == len(pending) 才算这轮闭环；少一条 tool_call_id 未配对，下一轮模型会报错/悬空
```

要点：
- **配对权威是 `tool_call_id`，不是函数名、不是数组下标**（`mimo-openai-api.txt:262`）；同一函数可能被并行调两次（两个不同 id），按 name 配对会串。
- assistant 消息**必须整条 append**（含 tool_calls 定义本身），不能只 append tool 结果（`mimo-reasoning.txt:113`）。

### 2.3 多 tool_call 执行器：顺序还是并发？

| 场景 | 建议 | 依据 / 推断 |
|---|---|---|
| 官方示例写法 | for 循环**顺序**执行 | `mimo-reasoning.txt:116-124` 原样就是串行 for |
| 无数据依赖的只读工具（如 `get_time` + 读状态） | **可并发**执行，收集齐结果后统一回传 | 模型 reasoning 明说 "I can call both in parallel"（`mimo-reasoning.txt:134`），两调用无前后依赖；并发只是 runtime 优化，不影响协议 |
| 涉及同一 SDR 硬件的写操作（set_freq / 切模式 / 录停） | **必须串行**（硬件单锁） | **推断**：MBDSDR 单 SDR 单 VFO（见 satnogs_client 笔记 `OBSERVER_LOCK` 思路），两个并行 tool_call 若都调谐会互相踩；协议允许并行 ≠ 硬件允许并行。执行器应按工具的硬件资源分组加锁 |
| 无论顺序还是并发 | **必须等全部 N 条 tool 结果都回传完，才发下一轮 LLM 请求** | `mimo-openai-api.txt:243-247`：for 循环把所有 tool 结果 append 完，循环末尾才进下一次 create——少回传任何一条，assistant 里那个 tool_call 就没有对应结果，模型上下文不闭环 |

伪代码（并发 + 硬件锁）：

```python
results = concurrent_execute(pending.values(),      # 只读工具并发
                             per_resource_lock=sdr_lock)  # 占用 SDR 的工具串行化
for call_id, res in results.by_id():               # 按 id 顺序回传（与官方示例同序）
    messages.append({"role":"tool","tool_call_id":call_id,"content":res})
```

### 2.4 流式下并行 tool_calls 的 index 拼接（指向 14 号笔记）

| 事实 | 出处 | 要点 |
|---|---|---|
| 流式 `delta.tool_calls` 每个分片带 `index`（从 0 开始）标识它属于第几个 tool_call | `sources/mimo-openai-api.txt:109` | 原文："choices.delta.tool_calls.index integer — 在 tool_calls 列表中的索引，从 0 开始"；同分片还有 `:110` id、`:111` type、`:113` name、`:114` arguments |
| arguments 是**分片累加**的 | 推断（OpenAI 流式惯例 + SF 示例 `if delta.tool_calls:` `sf-chat-completions-api.txt:467`） | 同一 index 的多次 delta，其 `function.arguments` 是字符串片段，必须按 index 拼接，不能直接覆盖 |
| 流式并行拼接 = 按 index 归桶 | `mimo-openai-api.txt:108-114` | 累加器形如 `tool_calls_buf = {0:{id,name,args:""}, 1:{id,name,args:""}}`；新 index 出现即新桶，首片带 id/name，后续片只拼 arguments |

> 流式 SSE 的完整拼接规则（含 reasoning_content / content / tool_calls 三流分离、index 乱序到达、finish_reason 在最后 chunk）属于 `14 流式全览` 笔记范围，本节只给出"并行场景必须按 index 归桶拼接"这条关键推论。

### 2.5 并行 × 思考模式的 runtime 检查项

| 检查项 | 依据 |
|---|---|
| 并行那轮 assistant 的 reasoning_content 也要整条保留（不是只留 tool_calls） | `mimo-reasoning.txt:133-140`（并行轮同样带 reasoning_content）+ `:42` 缺失即 400 |
| 检测到"reasoning_content 文本里混着 tool_calls JSON"→ 标记模型输出不稳定，记录并按配置切"工具循环关思考" | `mimo-faq.txt:200-202` |
| 关思考跑工具时，响应 message 根本不带 reasoning_content，runtime 就**不要写这个键**（省略而非空串） | `mimo-reasoning.txt §关闭思考`（关闭后无该字段） |
| 并行 N 个 tool 结果回传后，模型出最终答案那轮还会**再带一段新的 reasoning_content**，同样要保留 | `mimo-reasoning.txt:144-147`（Request 1-2） |

---

## 3. 与能力边界映射

| 边界 | 文档依据 | 实现要点 |
|---|---|---|
| d) tool_choice 后端强制 auto | `mimo-openai-api.txt:57-59`（逐字）；`sf-chat-completions-api.txt:459`（示例唯一值 auto）；SF 参数表无此行（`:35-319`）；SF FC 指南通篇无 tool_choice（`sf-function-calling.txt` 全文） | 请求构造器只发 `"auto"` 或不发；禁止 `required`/指定函数；靠 prompt+工具描述影响选择；做成可配置项 |
| a) 并行 tool_calls 循环 | `mimo-openai-api.txt:82,249-256`；`mimo-reasoning.txt:133-142`；`mimo-openai-api.txt:258-264`（role=tool 配对） | 一次响应多个 tool_calls → 建 id 配对表 → 逐个执行 → N 条 role:tool 按 id 严格配对回传 → 齐了才进下一轮 |
| a) 执行顺序 | 官方示例串行 for（`mimo-reasoning.txt:116-124`）；模型自述可并行（`:134`） | 只读工具可并发、占硬件工具串行；回传按数组序 |
| b) reasoning_content 并行场景保留 | `mimo-reasoning.txt:40-45`；`sf-interleaved-thinking.txt:87-108` | 并行轮 assistant 的 reasoning_content 整条保留；工具结果后新思考段也保留；禁止重排 |
| e) 思考+工具不稳定 | `mimo-faq.txt:200-202` | tool_calls 混入 reasoning_content = 不稳定信号；官方建议调 tool 时关 thinking |
| c) arguments 合法性 | `mimo-openai-api.txt:88-89,114` | 并行场景每条 arguments 都要 schema 校验（本笔记不展开，见 06 号笔记） |
| f) Anthropic 协议并行控制 | `mimo-faq.txt:133-139`；`_SPEC.md §4 f` | `disable_parallel_tool_use` 归 `11-mimo-anthropic-protocol.md`，本篇仅指向 |

---

## 4. 红线与自检记录

- [x] 目标文件可写入：`/home/user/Doubao/chats/38438160041798146/docs/learn/model-tool-calling/10-tool-choice-and-parallel.md`。
- [x] 引用的每个 `sources/*.txt:<行>` 均已在原文核对：
  - tool_choice 逐字 = `mimo-openai-api.txt:57-59` ✓；
  - SF 参数表无 tool_choice、仅示例 `:459` ✓（通读 `sf-chat-completions-api.txt:35-319` 全表确认）；
  - 并行示例 get_current_weather+get_time = `mimo-reasoning.txt:138-140` / `mimo-openai-api.txt:251-253` ✓；
  - role=tool 严格配对 = `mimo-openai-api.txt:262` ✓；
  - 不稳定警告 = `mimo-faq.txt:200-202` ✓；
  - 流式 index = `mimo-openai-api.txt:109` ✓。
- [x] 覆盖任务指定 6 个重点（tool_choice 语义 / runtime 结论 / 并行调用证据+顺序+配对+回传顺序 / 并行×思考 / Anthropic 指向 / runtime 启示含 src/ai 组件建议）✓。
- [x] 无编造：SF 未给并行多调用示例已如实标注（§1.3 末行「推断」）；「回传顺序是否必须」原文无明文已标「推断」（§1.4）；「流式 arguments 分片累加」标推断。
- [x] 「推断」处清单：①SF 多 tool_calls 能力应与 OpenAI 一致但 SF 文档未正面举证；②回传顺序非硬性、按 id 配对即可（建议仍按数组序）；③流式 arguments 分片累加；④占硬件工具须串行（硬件约束，非 API 协议要求）。
- [x] 未改任何代码、未做 git 操作、未调任何需要 API key 的接口、未抓无关页面。
- [x] 篇幅约 230 行，落在 150–500 行区间；全中文；多用表格。
