# 工具调用循环状态机与 runtime 实现蓝图（批次综合稿）

> **文档来源**：本批次 15 篇学习笔记（`01`–`15`，同目录）+ 其引用的 `sources/*.txt` 纯文本。本稿不新抓任何文档，全部断言收敛自既有笔记。
> **精读方式**：逐篇通读 15 篇笔记全文；需要核对细节时回读 `sources/*.txt:<行>`。
> **精读日期**：2026-10-01　**作者**：MainAgent（批次收尾综合 agent）
> **覆盖边界**：a/b/c/d/e/f 全链条收敛（规范 §4），并落到 `src/ai/` 组件映射。
> **标注约定**：「原文」= 可在 `NN-*.md` 或 `sources/*.txt:<行>` 复核；「设计」= 本稿基于原文约束给出的实现蓝图，非文档原文；「推断」= 原文无直接证据、按 OpenAI/Anthropic 通用协议外推。
>
> **一句话定位**：MBDSDR 的无线电能力 = 给 LLM 看的 function calling 工具集；模型 = 大脑；runtime = 中介，状态机就是「校验 arguments → 真实执行 → `role:tool` 回传 → 保留 reasoning_content → 多步循环直到 stop」。本稿把 15 篇学习成果收敛成一张可直接指导 `src/ai/` 改造的状态机蓝图。

---

## 0. 总览：为什么是状态机而不是 while-true 脚本

最简骨架是一个 while 循环（`06-mimo-openai-api.md:233-246`、`08-mimo-reasoning-roundtrip.md:80-90` 官方 `run_turn`）：

```
while True:
    resp = llm.chat(messages, tools, tool_choice="auto", thinking=...)
    messages.append(resp.choices[0].message)          # 整条 assistant 原样入历史
    if not message.tool_calls: break                  # finish_reason=stop → 终答
    for tc in message.tool_calls:                      # 并行调用逐个执行
        args = validate(tc.name, tc.function.arguments)   # schema 校验，禁 eval
        result = execute(args)
        messages.append({role:"tool", tool_call_id:tc.id, content:result})
```

但真机不能只写这个循环：它要处理①流式逐块拼接、②arguments 非法 JSON / 幻觉参数、③reasoning_content 缺失直接 400、④429/503 退避、⑤最大轮数看门狗、⑥双协议形状差异。把这些拆成显式状态，每状态有明确输入/输出/转移条件，才是可测、可恢复的 runtime。

---

## 1. 完整循环状态机

### 1.1 状态与转移总图

```
                         ┌──────────┐
              start ───► │  IDLE    │  持有 system 前缀 + tools Schema + 累积历史
                         └────┬─────┘
                              │ 组装请求 (system+tools 固定前缀 + 历史 + 本轮新输入)
                              ▼
                         ┌──────────┐
              ┌─────────►│ REQUEST  │  POST /chat/completions（SSE 或非流）
              │          └────┬─────┘
              │               │ 收到首字节 / 整个响应
              │               ▼
              │          ┌──────────────┐  流式：逐 chunk 拼接
              │          │  PARSE/STREAM │ ◄── SSE: data:{json}，[DONE]
              │          └─────┬────────┘     reasoning_content/content/tool_calls 三通道累加
              │                │ 流结束，得到完整 assistant_message
              │                ▼
              │          ┌──────────────┐
              │          │   DECIDE      │  读 finish_reason
              │          └──┬────┬────┬─┘
              │    stop ◄──┘    │    └──► tool_calls / length / 其他
              │                 │
              │                 ▼ 有 tool_calls（finish_reason="tool_calls"）
              │          ┌──────────────┐
              │          │ PARSE_TOOL_CALLS│ 按 index 分槽拼好 → {id,name,arguments_str}
              │          └─────┬────────┘
              │                ▼
              │          ┌──────────────┐
              │          │  VALIDATE    │  json.parse → schema 校验 → 白名单
              │          └──┬───────┬───┘
              │    校验失败 │       │ 通过
              │     (错误tool结果) │
              │          ▼         ▼
              │   ┌───────────────────────┐
              │   │ BUILD_TOOL_MESSAGES     │  N 个 tool_call → N 条 role:"tool"，按 tool_call_id 配对
              │   └───────────┬─────────────┘
              │               │ 全部 tool 结果入历史，append 下一轮请求
              └───────────────┘ （回到 REQUEST，轮次+1）
                              │
                              ▼
                         ┌──────────┐
                         │   STOP    │  输出终答 content；usage 入账
                         └──────────┘
```

### 1.2 每状态输入/输出/转移条件

| 状态 | 输入 | 处理 | 输出 / 转移 | 出处 |
|---|---|---|---|---|
| **IDLE** | 任务目标、可用工具表 | 无网络动作；加载固定前缀（system + tools Schema）与模型配置 | → REQUEST | 前缀固定性见 `15-cache-and-performance.md:95-110` |
| **REQUEST** | Canonical 请求体（messages + tools + sampling + thinking 配置） | 带 `Authorization: Bearer`/`api-key`、`X-Trace-Id` 发 POST；流式则 `CURLOPT_WRITEFUNCTION`/`requests.post(stream=True)` 逐回调 | 成功→PARSE/STREAM；HTTP 429/503→退避重试（§7）；401/403→中止；400→解析后判因 | `04-sf-chat-completions-api.md:18-28`；`03-sf-streaming-mode.md:30-35` |
| **PARSE/STREAM** | SSE 字节流（或非流完整 JSON） | 按行剥 `data: `；三通道累加 `content`/`reasoning_content`/`tool_calls(index 槽)`；`[DONE]` 关流 | 完整 `assistant_message` → DECIDE | `03:37-58`；`14-streaming-best-practices.md:104-155` |
| **DECIDE** | `assistant_message` + `finish_reason` | 判终止原因 | `stop`（或无 tool_calls）→ STOP；`tool_calls` → PARSE_TOOL_CALLS；`length`→截断处理；`content_filter`/`repetition_truncation`→按错误处理 | `14:71-80`；`06:71`（枚举 stop/length/tool_calls/content_filter/repetition_truncation） |
| **PARSE_TOOL_CALLS** | 流式拼好的 index 槽表 / 非流 `message.tool_calls[]` | 整段 append assistant（三件套）；落 `pending = {tool_call_id -> (name, arguments_str)}` | pending 表 → VALIDATE | `10-tool-choice-and-parallel.md:133-162` |
| **VALIDATE** | `(name, arguments_str)`、工具注册 Schema | ① name 白名单查注册表；② `json.parse(arguments)`；③ schema 校验（必填/类型/enum）；④幻觉键白名单裁剪 | 通过→EXECUTE；失败→错误 tool 结果（§3.3） | `09-tool-schema-and-validation.md:94-134` |
| **EXECUTE** | 校验后的合法参数 dict | 调真实 SDR 能力；只读工具可并发、占硬件工具按资源锁串行 | 结果字符串 → BUILD_TOOL_MESSAGES | `10:164-180` |
| **BUILD_TOOL_MESSAGES** | N 个 `(tool_call_id, result_or_error)` | 逐条 `{role:"tool", tool_call_id, content}`；N 个 tool_call 必须 N 条，按 id 严格配对 | 历史闭合 → REQUEST（轮次+1） | `10:81-87`；`09:146-162` |
| **STOP** | 终答 `content`、usage | 返回用户；usage（含 reasoning_tokens/cache hit/miss）入账；reasoning buffer 持久化 | 结束 | `15:112-120`；`04:164-177` |

### 1.3 终止条件全集（显式枚举，不只认 stop）

| 终止信号 | 来源状态 | runtime 动作 | 出处 |
|---|---|---|---|
| `finish_reason=stop` 且无 tool_calls | DECIDE | 正常出终答，退出循环 | `14:75`；`06:71` |
| 当次响应**根本没有 tool_calls**（即便流式） | DECIDE | 与 stop 等价退出（官方示例隐含逻辑） | `01-sf-function-calling-protocol.md:128-132`；`08:86` |
| `finish_reason=length`（max_tokens 截断） | DECIDE | 视为不完整；记录并按截断错误处理（注意 SF `max_tokens` 不含思维链） | `14:76,171`；`04:55-57` |
| **达到最大工具轮数**（看门狗） | 每轮 BUILD→REQUEST 之间 | 强制 break，回传"已达最大步数"终答，防模型工具死循环 | `06:246`（设计：最大轮数上限防死循环）；`15:130`（建议 12 轮封顶） |
| VALIDATE 反复失败超阈值 | VALIDATE | 模型持续自纠失败→降级终止，不无限回喂错误 | 设计，依据 `09:131` 失败回传会"多一轮往返" |
| 不可恢复错误（401/403、400 且非 reasoning 缺失） | REQUEST | 中止并上报，不进下一轮 | `04:181-193`；`15:128` |

> **两层终止**：传输层 `data: [DONE]` 只关流，不判业务完成；业务终止语义以 `finish_reason` 为准（`14:82`）。顺序为：末片带 `finish_reason` → 可能再来空 choices 带 usage chunk → `data: [DONE]` → 连接关闭。

---

## 2. 消息历史组装规则

### 2.1 固定前缀 + 追加历史（缓存友好）

按缓存友好顺序固定组装（`15:95-110`）：

| 位置 | 内容 | 是否可变 | 缓存属性 |
|---|---|---|---|
| 1 | **system 指令**（角色/无线电任务规则/输出格式） | 启动期一次构建、运行期只读 | 全程不变→应命中 |
| 2 | **tools Schema 数组**（SDR 函数清单，≤128 个） | 运行期禁止改字段/空格/顺序 | 不变→应命中；改动会破前缀 |
| 3 | 历史 assistant 消息（content + reasoning_content + tool_calls） | append-only | 逐字保留即前缀稳定 |
| 4 | 历史 tool 结果（role=tool） | append-only | 追加式，位置固定 |
| 5 | 本轮新 user 输入 / 实时数据（当前频率、硬件状态快照） | 易变，放最后 | 不污染前面稳定前缀 |

- system 与 tools 是**固定前缀**，每轮直接拼在历史前，不要每轮重新序列化（重排 key/变空格会破前缀——`15:108`）。
- 动态信息（当前 UTC 时间、设备实时状态）**不要塞进 system**，放最后一条 user/tool 消息（设计；`15:110` 指出官方示例 system 里硬编码日期会每天破前缀）。

### 2.2 assistant 消息三件套（不可拆分）

每轮收到的 assistant 响应必须**整条原样 append**，含三个字段（`02-sf-interleaved-thinking.md:125-132`；`08:92-96`）：

```jsonc
{
  "role": "assistant",
  "content": "<对外正文，可展示给用户>",
  "reasoning_content": "<思考链，逐字、原序、不裁剪>",
  "tool_calls": [ { "id":"call_xxx", "type":"function",
                    "function": {"name":"set_frequency",
                                 "arguments":"{\"freq_hz\":145000000}"} } ]
}
```

| 字段 | 规则 | 出处 |
|---|---|---|
| `content` | 正常累加，工具步可能为空/null | `04:168`；`08:27` |
| `reasoning_content` | **逐字保留、跨轮顺序保留、所有轮次不裁剪**；关闭思考时该键**整个省略**（不要写空串） | `02:36-48`；`08:28,140`（关闭思考后无该字段） |
| `tool_calls` | 原样回传，与当初请求的 id/name 一致 | `02:129`；`06:115` |

**红线**：缺 `reasoning_content` 在 MiMo 直接 **HTTP 400**（`08:38-40` 逐字："必须完整回传 reasoning_content 字段，否则 API 将返回 400 错误"；触发三要素=开思考+历史有 tool_calls+本轮 assistant 带 tool_calls）；在 SF 则软后果——多步工具链崩、跨调用不稳定、缓存劣化（`02:59-63`）。

### 2.3 role:"tool" 回传（按 tool_call_id 配对）

```jsonc
{ "role": "tool", "tool_call_id": "<= assistant.tool_calls[i].id 严格一致>",
  "content": "<工具执行结果字符串>" }
```

| 规则 | 说明 | 出处 |
|---|---|---|
| 先 append assistant 带 tool_calls 的消息，再 append tool 结果 | 否则模型不知道自己当初请求了哪些调用 | `01:100-105`；`08:85` |
| N 个 tool_call → **N 条** tool 消息，一条不能少 | 少一条=悬空 tool_call，模型上下文不闭环 | `10:84`；`06:245` |
| 配对权威是 **`tool_call_id`**，不是函数名、不是数组下标 | 同一函数可并行调两次（两个不同 id），按 name 配对会串 | `10:161`；`06:113` |
| 回传顺序按 tool_calls 数组原序 append（建议） | 原文只演示按序，未明文强制；id 配对才是权威 | `10:86` |

### 2.4 reasoning_content 的「逐段保留」覆盖位置

| 出现位置 | 是否保留回传 | 出处 |
|---|---|---|
| 工具调用之前 | 是 | `02:34` |
| 多次工具调用之间 | 是 | `02:35` |
| **收到工具结果之后**（role=tool 之后模型又冒新思考） | 是，最易漏 | `02:36,92-93`；`08:75-78` |
| 跨多个 user 轮次（Turn1→Turn2） | 是，全程不裁剪 | `02:37,99-100` |

SF 官方明确禁止：改字 / 清洗后处理 / 合并或拆分片段 / 重排 / 只留正文丢思考（`02:54-58`）。runtime 对历史消息应 **append-only，禁止事后改写任何字段**（`02:196`）。

---

## 3. arguments 校验时机与失败处理（边界 c）

### 3.1 为什么必须校验（原文警告逐字）

MiMo 官方在非流式 `message.tool_calls.function.arguments` 与流式 `delta.tool_calls.function.arguments` **两处都**警告（`09:52-62`，转写 `sources/mimo-openai-api.txt:88-89,114`）：

> "模型生成的内容并非总能保证是有效的 JSON，且可能会虚构出函数模式中未定义的参数。在调用函数之前，请在代码中对这些参数进行验证。"

两条独立风险：① `json.parse(arguments)` 可能直接失败；② parse 成功也可能多出 schema 外的幻觉键。

### 3.2 校验流水线（parse → schema → 白名单）

放在 **「parse 之后、真实执行之前」**（`09:94-115`）：

```
tool_call: name, arguments(string)
  │
  ├─[1] name 白名单查注册表 ──未知函数名──► 拒绝，回"无此工具"错误
  │
  ├─[2] json.parse(arguments) ──失败──────► 拒绝，回"arguments 不是合法 JSON"错误（§3.3）
  │
  ├─[3] schema 校验：
  │      a. required[] 键齐全？
  │      b. 每键 value 类型匹配 properties[k].type？
  │      c. 带 enum 的键取值在枚举内？
  │      d. 出现 properties 未声明的键？= 幻觉参数
  │
  └─[4] 通过 → 真实 SDR 执行
```

| 校验项 | 失败策略（分层） | 出处 |
|---|---|---|
| 缺必填 / 类型错 / enum 越界（不可安全推断） | **A. 拒绝整次调用 + 回传错误**，让模型自纠 | `09:131` |
| 仅多出未声明幻觉键（必填/类型都对） | **B. 裁剪幻觉键后继续执行**，并在回传 content 里注明丢弃了哪些未知键 | `09:132-134` |
| 数值范围（如 freq_hz 须落在 SDR 可调频段） | runtime 是最后一道闸，二次范围校验 | `04:285` |

### 3.3 校验失败的反馈路径（衔接循环）

失败不崩溃，**复用 `role:"tool"` 通道把错误喂回模型自纠**（`09:146-162`）：

```jsonc
{ "role": "tool", "tool_call_id": "<与 assistant.tool_calls[i].id 严格一致>",
  "content": "参数校验失败：工具 set_frequency 缺少必填参数 freq_hz；
              未知参数 band 已被忽略；合法示例：{\"freq_hz\":145000000}" }
```

- content 说清**错在哪、合法长什么样**，模型下一轮即可自纠。
- **红线：禁 eval、禁直接把未校验 dict unpack 进硬件调用**。SF 官方教学示例自己用了 `eval(f'{name}(**{args})')`（`01:86`、`09:68-74`）——那是反面教材，生产环境严禁照搬（SDR 调谐频率是硬件参数，幻觉参数会驱动错误硬件动作）。

---

## 4. 流式路径

### 4.1 SSE 行解析器

| 规则 | 说明 | 出处 |
|---|---|---|
| 开启流式要**两处都开**：请求体 `stream:true` 且 HTTP 客户端 `stream=True`/`CURLOPT_WRITEFUNCTION` 逐回调吐 | 否则不按流返回 | `03:30-35`；`14:23` |
| 传输形态 | 一行 `data: {json}\n\n`；事件间空行；最后一行字面量 `data: [DONE]` | `03:43-56`；`14:31` |
| 按行缓冲、不按字节交付 | TCP 回调遇半行要粘包，`\n` 才是事件边界 | `03:123` |
| 前缀精确剥 `data: `（含一个空格）；空行跳过 | 未知行前缀（event:/id:/retry:）只忽略不报错 | `03:45-57,125` |
| `[DONE]` 后不再 json.parse | 它是传输层哨兵，不是业务终止 | `03:46,126`；`14:82` |
| `choices==[]` 的空 chunk 直接 continue（通常是末尾 usage chunk） | 静默容忍 | `03:75,190`；`14:69` |

### 4.2 三通道累加器（物理隔离）

```
reasoning_buf = ""   # delta.reasoning_content 逐片 +=
content_buf    = ""  # delta.content 逐片 +=
slots = {}           # index -> {id, type, name, arguments_buf}
```

| delta 字段 | 拼接动作 | 出处 |
|---|---|---|
| `delta.content` | `content_buf += 文本` | `03:49,51`；`14:106` |
| `delta.reasoning_content` | `reasoning_buf += 文本`，与 content **分两条独立通道**，绝不合并成一个 text 流 | `03:50,52`；`14:107`；`08:102-104`（先思考流后回答流，时间上不重叠） |
| `delta.tool_calls[i]` | 按 `index` 分槽累加（§4.3） | `14:108` |
| `finish_reason` | 末片携带，记录后停止累加 | `14:109` |
| 顶层 `usage` | 仅最后一个 `choices:[]` chunk 携带，在此笔入账 | `14:50,172` |

> reasoning 累加用 `+=`，绝不 trim / 去尾补 / 正则清洗（命中 SF「clean up」禁令，`02:177`、`03:136`）。非思考模型 chunk 不带 reasoning_content 键，属正常而非错误（`03:138`）。

### 4.3 tool_calls 按 index 分槽拼接（关键）

MiMo 是流式 tool_calls 字段的权威定义（`14:52-56`，转写 `sources/mimo-openai-api.txt:108-114`）：

```
slots = {}                                  # index 从 0 开始
for chunk in sse:
    if not chunk.choices: continue
    d = chunk.choices[0].delta
    for tc in (d.tool_calls or []):
        i = tc.index                        # 多工具并行靠它区分
        s = slots.get(i) or {id:"", type:"function", name:"", args:""}
        if tc.id:                 s.id = tc.id          # 首片带 id
        if tc.type:               s.type = tc.type
        if tc.function.name:      s.name = tc.function.name      # name 一般首片即全量
        if tc.function.arguments: s.args += tc.function.arguments # ★字符串逐片 +=
        slots[i] = s
# 流末才对每个槽：json.parse(args) → schema 校验
```

红线（`14:150-154`）：
1. **不要边收边 `JSON.parse(arguments)`**——分片可能切在任意字符中间，半片一定不是合法 JSON；
2. 多工具并行 = 多个 index 槽，逐槽拼接、逐个执行；
3. 回传 `tool_call_id` 必须严格匹配该槽的 `id`；
4. 不要照搬 SF 教学 demo 的 `tool_calls.extend(delta.tool_calls)`（`14:54,154`）——那是非分片简化写法，真并行多分片会错位。

### 4.4 两层终止（finish_reason + [DONE]）

| 层 | 信号 | 作用 |
|---|---|---|
| 业务层 | `finish_reason`（末个带 choices 的 chunk 里） | `tool_calls`→执行工具回传继续循环；`stop`→出终答退出；`length`→截断 |
| 传输层 | `data: [DONE]` | 关读循环、收尾 HTTP |

两家文档均**无 SSE 断点续传字段**（`14:91,173`）：中途断线不做透明续传，把已拼内容标记 incomplete 上报；429/503 是**整条请求重发**（复用同一 messages 历史），不是续传。

---

## 5. 双协议抽象：Canonical 中间形状 + 适配器

MBDSDR 面对 OpenAI 兼容（SF + MiMo 主路径）与 Anthropic 兼容（MiMo `/anthropic/v1/messages`）两套形状。runtime 内部维护一套**协议无关的 Canonical 形状**，协议只在边界互转（`11-mimo-anthropic-protocol.md:171-186`）。

### 5.1 Canonical 中间形状（设计）

```
SystemPrompt            string
Message { role: user|assistant|tool,
          text, thinking,                       // 跨协议统一收纳思考
          tool_calls:[{id, name, args(JSON object)}],
          tool_results:[{tool_call_id, content}] }
ToolDef { name, description, input_schema(JSON Schema) }
SamplingParams { max_tokens, temperature, top_p, stop_seq[], thinking_enabled }
→ send(canonical) → CanonicalResponse { text, thinking, tool_calls, stop_reason, usage }
```

### 5.2 两张核心映射表（适配器双向转换）

**请求侧 tools 声明**（`11:65-74`）：

| Anthropic 形状 | OpenAI 形状 | 转换 |
|---|---|---|
| `tools[].type="custom"` | `tools[].type="function"` | 字面量替换 |
| `tools[].name` | `tools[].function.name` | 提一层 |
| `tools[].description` | `tools[].function.description` | 提一层 |
| `tools[].input_schema` | `tools[].function.parameters` | 整体平移，内部 properties/required 不变 |
| （无） | `tools[].function.strict`（默认 false） | Anthropic 侧丢弃（推断） |

**响应/回传侧**（`11:101-111`）：

| 概念 | Anthropic | OpenAI | 转换要点 |
|---|---|---|---|
| system 提示词 | **顶层 `system=` 参数** | messages 内 `{role:"system"}` 一条 | 独立存 system，发时按协议归位 |
| 工具调用块 | content[] 里 `tool_use{id,name,input}` | `message.tool_calls[].{id,function.name,function.arguments}` | **关键差异**：`input` 是 **JSON object** ↔ `arguments` 是 **JSON 字符串**（发 OpenAI 时 `json.dumps`，收 OpenAI 后 `json.loads`） |
| 终止信号 | `stop_reason="tool_use"` | `finish_reason="tool_calls"` | 映射 |
| 工具结果回传 | `{role:"user", content:[{type:"tool_result", tool_use_id, content}]}`（N 个合并进一个 user 块） | `{role:"tool", tool_call_id, content}`（每条独立消息） | 回传封装位置完全不同 |
| 生成长度 | `max_tokens` | `max_completion_tokens` | 字段改名 |
| 停止序列 | `stop_sequences` | `stop`（最多 4 个） | 字段改名 |
| 思考内容 | content[] 里 `thinking` 块 | `message.reasoning_content` 字符串 | Anthropic 把 thinking 块原样夹在 text/tool_use 之间回传 |
| usage | `input_tokens/output_tokens/cache_read_input_tokens` | `prompt_tokens/completion_tokens/cached_tokens` | 字段名映射 |

### 5.3 disable_parallel_tool_use（仅 Anthropic 侧）

| 事实 | 说明 | 出处 |
|---|---|---|
| 位置 | **`tool_choice.disable_parallel_tool_use`**（bool，默认 false），嵌套在 tool_choice 对象内，非顶层 | `11:120` |
| 语义 | true 且 `tool_choice.type="auto"` 时，模型至多输出一个 tool_use | `11:121` |
| OpenAI 侧无对应参数 | MiMo OpenAI 协议 `tool_choice` 传非 auto 会被后端剥成 auto（§6），**无服务端串行化开关**；要串行只能在客户端自然顺序执行 | `11:199`；`06:57-59` |
| 配对规则（两协议同构） | N 个 tool_use/tool_calls → N 个 tool_result/role:tool，按 `tool_use_id==tool_use.id` 严格配对 | `11:123` |

> **Anthropic 流式差异**（`11:132`）：`delta.type=thinking_delta`（`delta.thinking` 思考增量）+ `input_json_delta`/`partial_json`（工具入参 JSON 片段，按到达顺序拼成完整 JSON 后再 parse）——对应 OpenAI 的 `delta.reasoning_content` 与 `delta.tool_calls[].function.arguments` 分片。流式聚合器按协议分两套。

---

## 6. 按模型配置表

`tool_choice` 一律 **`auto`**（两家唯一收敛值：SF 参数表无此行、仅示例 `auto` `04:116-118`；MiMo 明文"传非 auto 后端默认移除该字段、行为等同 auto" `06:81-85`/`10:32`）。要"必须调某工具"只能靠 prompt + tools 描述，别赌 `tool_choice`（`06:212-218`）。

### 6.1 模型 × 能力配置表（`ModelConfig` 权威来源）

| 模型 | 服务商 | 协议 | thinking 开关 | IT* | Ctx | 备注 / 出处 |
|---|---|---|---|---|---|---|
| `deepseek-ai/DeepSeek-V3.2` | SF | OpenAI(+Anth 端点) | `enable_thinking`+`thinking_budget` | **官方** | 未标(推断128k级) | interleaved 官方两模型之一，必须逐字回传 reasoning `12:157`；`02:24` |
| `zai-org/GLM-4.7` | SF | OpenAI | 同上（不在 13 清单） | **官方** | 未标 | interleaved 另一模型，规则同 V3.2 `12:162`；`02:140` |
| `deepseek-ai/DeepSeek-V3.1`/-Terminus | SF | OpenAI | `enable_thinking` | 未证实 | 未标 | **做 FC 必须 `enable_thinking=false`**（原文红线）`12:58-61` |
| `deepseek-ai/DeepSeek-V4-Flash`/`-Pro` | SF | OpenAI(+Anth) | `enable_thinking`+`reasoning_effort(high/max)` | 未证实 | blog旁证1M | reasoning_effort 三模型之一，agent 请求自动 max `12:160-161`；`04:71-75` |
| `Pro/zai-org/GLM-5.2` | SF | OpenAI | `reasoning_effort` | 未证实 | 未标 | reasoning_effort 三模型之一 `12:164` |
| `Qwen/Qwen3-*` 系 | SF | OpenAI | `enable_thinking`+`thinking_budget` | 未证实 | 131072 | 唯一支持 `min_p`；到预算 Qwen3 会强制停 CoT `12:165` |
| `mimo-v2.6-pro` | MiMo | **OpenAI + Anthropic** | `thinking.type=enabled/disabled`(默认开) | 工具后可再出 reasoning | **1M** / out 128K | 旗舰；开思考调 tool 不稳，建议工具轮关 thinking；缺 reasoning 直接 400 `12:119,177` |
| `mimo-v2.6-flash` | MiMo | OpenAI + Anthropic | 同上（默认开） | 同上 | 1M / 128K | 高频办公 `12:120` |
| `mimo-v2.5`/`-v2.5-pro` | MiMo | OpenAI + Anthropic | 同上 | 同上 | 1M | **2026-10-21 弃用**，勿新接 `12:122-123,180-181` |

\* IT = interleaved thinking（工具结果后继续出 reasoning_content 且必须回传）。

### 6.2 thinking 参数族按模型下发（不能一刀切）

| 场景 | SF DeepSeek-V3.2/GLM-4.7 | SF V4-Flash | SF V3.1 | MiMo v2.6-pro |
|---|---|---|---|---|
| 开思考 | `enable_thinking:true`+`thinking_budget:4096` | `enable_thinking:true`+`reasoning_effort:"max"`(agent) | `enable_thinking:true` | `extra_body:{thinking:{type:"enabled"}}` |
| 做 FC | 同左（开 interleaved） | 同左 | **必须 `enable_thinking:false`** | 建议工具轮 `thinking.type:"disabled"` |
| tool_choice | `"auto"` | `"auto"` | `"auto"` | 只能 `"auto"`，传别的被剥 |
| 回传 assistant | 整条 append（含 reasoning） | 同左 | 同左 | 整条 append，**缺 reasoning → 400** |

出处：`12:239-247`；`13:132-141`。

- `thinking_budget` 范围 128–32768（`04:65-69`）；`reasoning_effort` 仅 V4/V4-Flash/GLM-5.2 三模型允许 high/max，**不能下发给 V3.2/GLM-4.7**（两档名单不重叠，`13:54`）。
- MiMo 开思考时 `temperature`/`top_p` 被强制覆写为 1.0/0.95（传了不生效，`08:31`、`12:136`）；`max_completion_tokens` 是"思考+回答"总长度（`08:32`）。
- 上下文预算：`max_tokens`（SF）**不含思维链**，要另扣 thinking_budget，并为输入预留 ~10k token 缓冲（`04:55-57`、`13:60-61`、`15:86`）。

---

## 7. 错误恢复与重试

### 7.1 HTTP 错误分类与处理

| HTTP | body（SF 原文） | 性质 | runtime 动作 | 出处 |
|---|---|---|---|---|
| 400 | `{"code":20012,"message":...}` | 请求体不合法 | 把 message 回灌日志；**若因缺 reasoning_content**（MiMo 硬规则）→ 本地补全后重试，否则不盲目重试 | `04:185,305`；`08:38-40` |
| 401 | `"Invalid token"` | key 无效 | **中止**，需用户改 key | `04:186` |
| 403 | `"Forbidden"` | 无权限 | 中止 | `04:187` |
| 404 | `"404 page not found"` | 路径错 | 中止排查 base_url | `04:188` |
| **429** | `Details: TPM limit reached.` | 限流（token/min） | **指数退避重试**（不硬编码额度阈值） | `04:189`；`15:71` |
| **503** | `code 50505 "Model service overloaded"` | 过载 | **指数退避重试** | `04:190`；`15:72` |
| 504 | string | 网关超时 | 退避重试 | `04:191` |

### 7.2 退避 / 超时 / 看门狗（设计起点，需实测校准）

| 配置项 | 建议值 | 依据 |
|---|---|---|
| 连接超时 | 10 s | `15:126`（原文只说"reasonable"，数值为设计起点） |
| 读超时 | 非流式 120 s；**流式改"chunk 间隔超时"60 s**（相邻两 chunk 超 60 s 才算卡死） | `15:127`；`05:169` |
| 退避 | 指数 1→2→4→8 s，封顶 30 s，最多 3 次；429 可叠乘分钟窗口剩余比例 | `15:129`；`05:170`（原文"exponential backoff"） |
| 重试白名单 | 仅 429 / 503(+code 50505)；400/401/403/404 **不重试** | `15:128` |
| 重试方式 | 整条请求重发，**复用同一 messages 历史**（含已逐字保留的 reasoning_content） | `14:176` |
| **最大工具轮数（看门狗）** | 建议 12 轮封顶，强制 break | `15:130`；`06:246` |
| VALIDATE 反复失败 | 超阈值降级终止，不无限回喂错误 | 设计，依据 `09:131` |
| 长响应 | 默认走流式（降感知延迟，尤其思考模型 CoT 长） | `05:170`；`15:86` |

> **注意**：429/503/504 是网络/服务侧；**流式中途断线无断点续传**——标记 incomplete 上报，不假装续完（`14:173-176`）。内容审核可能"HTTP 200 但 content 为空/被替换"，不能只靠 HTTP 状态码判失败（`05:183`）。

---

## 8. 对 src/ai/ 的落地映射

> 组件名为任务背景给定，**未在本批源码内逐行核对**（推断现状职责划分）。下表是「现有组件承担状态机哪些职责 + 需新增哪些组件」的映射蓝图。

### 8.1 现有组件 → 状态机职责

| 现有组件 | 承担状态机职责 | 需改造/新增 | 出处参照 |
|---|---|---|---|
| `agent` | 主循环编排（IDLE→REQUEST→…→STOP）；DECIDE 终止判定；看门狗 max_tool_rounds | 把 while-true 升级为显式状态机；维护 pending 配对表 | `01:245-263`；`10:133-162` |
| `llm_client` | REQUEST 构造 + HTTP 收发；协议适配（Canonical↔OpenAI/Anthropic）；thinking 参数按 ModelConfig 下发 | 拆出 `SseParser`；加 `protocol` 字段；system/tools 固定前缀只读对象 | `04:209-235`；`14:182`；`11:192-202` |
| `llm_worker` | HTTP 层统一出口：超时、429/503 指数退避重试、流式 chunk 间隔超时 | 重试/超时在此收口；工具执行本身不重试 | `15:157`；`05:169` |
| `agent_tools`（工具注册表） | 工具注册即带 `{name, description, parameters(JSON Schema)}` 三件套；白名单查表 | 新增 **Schema 生成器**（注册即产出 tools[]）；name 合规 `[a-zA-Z0-9_-]`≤64 | `09:136-144`；`06:66` |
| `task_orchestrator` | 多步链成本预算硬停；轮次计数；命中率打点告警 | max_tool_rounds 硬停；消费 usage hit/miss | `15:158` |
| `task_runner` | EXECUTE 状态：调真实 SDR 能力；只读工具并发、占硬件工具按资源锁串行 | 硬件单锁（两个并行调谐不能踩）；结果转字符串 | `10:164-180` |
| `plan_parser` | （如曾做 JSON 计划解析）可复用其 JSON 容错思路 | — | 推断 |
| `ai_context` | 消息历史累积器（append-only）；assistant 三件套落库 | 物理隔离 reasoning buffer；历史不裁剪、不改写 | `15:109`；`02:196` |
| `ai_session_store` | 持久化 assistant 的 reasoning_content（原字符串字段保真）；缓存前缀对象 | reasoning 按原字段存取，禁 trim/转义改写 | `15:109` |

### 8.2 需新增的组件

| 新组件 | 职责 | 对应状态 | 出处 |
|---|---|---|---|
| **Schema 生成器** | 从工具注册表产出 `tools[].function.{name,description,parameters}`（OpenAI）/`{type:custom,name,description,input_schema}`（Anthropic） | IDLE | `09:136-144`；`11:65-74` |
| **arguments 校验器** | parse→schema（必填/类型/enum）→幻觉键白名单；失败回传错误 tool 结果；禁 eval | VALIDATE | `09:94-134` |
| **SSE 拼接器（SseParser + StreamAggregator）** | 按行剥 `data:`/判 `[DONE]`；三通道累加；tool_calls 按 index 分槽 | PARSE/STREAM | `14:182-185` |
| **协议适配器** | OpenAIAdapter / AnthropicAdapter 双向转换（§5 两表） | REQUEST / PARSE | `11:188-202` |
| **reasoning 缓冲** | 独立 `reasoning_buf`，与 content 物理隔离，逐字 +=，流末填回 assistant | PARSE/STREAM | `14:167`；`08:152-163` |
| **配对表（pending map）** | `tool_call_id -> (name, args_raw)`，回传条数==pending 数才算闭环 | BUILD_TOOL_MESSAGES | `10:142-158` |

### 8.3 Flutter 端同一能力复用映射

| C++ 侧 | Flutter 侧（api_client） | 说明 | 出处 |
|---|---|---|---|
| `SseParser` | `llm_stream.dart`：`Stream<List<int>>`→`utf8.decoder`→`LineSplitter`，与 Python `iter_lines` 等价 | 同一套字段映射表 | `03:187`；`14:185` |
| StreamAggregator 三通道 | 双 buffer + index 槽表，回调实时推 UI（reasoning 灰色可折叠 / content 正常 / toolcall_delta） | **双端必须同一份拼接规则** | `14:188`；`03:137,188` |
| 重试/看门狗 | Flutter 只展示流式增量，不参与重试决策（薄客户端） | 重试收口在 C++ llm_worker | `15:159` |

> **双端一致性红线**（`14:188`）：C++ 与 Flutter 必须用同一 index 槽算法、同一 reasoning 逐字 buffer、同一"流末二次 parse"时机，否则同一轮 LLM 输出两端拼出不同 tool_calls。建议固化一份 `stream_protocol` 字段映射表作双端唯一事实来源。

### 8.4 `src/core/tokens.h` 应抽出的常量清单

| 常量 | 值 | 依据 |
|---|---|---|
| `kSfChatEndpoint` | `/v1/chat/completions` | `04:293` |
| `kSfBaseUrl` | `https://api.siliconflow.cn/v1` | `04:294` |
| `kMimoOpenAIBase` | `https://api.xiaomimimo.com/v1` | `06:22`；`07:33` |
| `kMimoAnthropicBase` | `https://api.xiaomimimo.com/anthropic` | `07:33`；`11:28` |
| `kMaxToolsPerRequest` | 128 | `04:295`；`09:82` |
| `kThinkingBudgetMin/Max` | 128 / 32768 | `04:296`；`13:45` |
| `kMaxStopSequences` | 4 | `04:297` |
| `kTemperatureMax` | 2.0（SF）；MiMo 思考模式强制 1.0 | `04:298`；`11:42` |
| `kFreqPenaltyMin/Max` | -2.0 / 2.0 | `04:299` |
| `kHeadroomTokens`（预留缓冲） | ~10000 | `04:301`；`15:86` |
| `kDefaultToolChoice` | `"auto"` | `04:302`；`06:81-85` |
| `kToolNameMaxLen` | 64（字符集 `[a-zA-Z0-9_-]`） | `06:66`；`09:141` |
| 追踪头 | 请求 `X-Trace-Id` / 响应 `x-siliconcloud-trace-id` | `04:303` |
| `kConnectTimeoutSec` | 10（设计起点） | `15:141` |
| `kReadTimeoutSec` | 120（非流）；流式 chunk 间隔 60 | `15:142-143` |
| `kRetryMaxAttempts` | 3；退避基数 1s、封顶 30s | `15:145-147` |
| `kMaxToolRounds`（看门狗） | 12 | `15:150` |
| `kDefaultMimoModel` | `"mimo-v2.6-pro"` | `12:119`；`07:41` |
| 重试白名单状态码 | 429 / 503(code 50505) | `15:148`；`04:530-540` |

---

## 9. 与能力边界映射（a–f 全链条收敛）

| 边界 | 收敛结论 | 关键出处 |
|---|---|---|
| **a) OpenAI 兼容循环** | tools 请求 → tool_calls[].function.{name,arguments} → 执行 → role:tool 按 tool_call_id 回传 → 直到无 tool_calls/finish_reason=stop；并行 N 个则 N 条回传 | `01:88-126`；`06:233-246`；`10:49-87` |
| **b) reasoning_content 逐字保留回传** | assistant 三件套；工具前/间/后/跨轮所有片段不裁剪；SF 丢弃→多步崩+缓存劣化，MiMo 缺失→400 | `02:36-63`；`08:34-43`；`15:57-65` |
| **c) arguments 不合法/幻觉参数** | runtime 必 parse→schema 校验→白名单，禁 eval；失败回传错误 tool 结果让模型自纠 | `09:52-134`；`06:74-77` |
| **d) tool_choice 强制 auto** | 一律发 auto 或不发；MiMo 传非 auto 被后端剥；靠 prompt 约束而非赌参数 | `10:25-47`；`06:81-85` |
| **e) thinking+工具稳定性** | SF interleaved 两模型(V3.2/GLM-4.7)开着并保留；MiMo 建议工具轮关 thinking；tool_calls 混入 reasoning_content=降级重试硬信号 | `13:30-108`；`05:119-123` |
| **f) Anthropic 兼容协议** | tool_use/tool_result 内容块、input(object) vs arguments(string)、独立 system、disable_parallel_tool_use | `11:51-133` |

---

## 10. 红线与自检记录

对照规范 §6 与 §2.6：

| 检查项 | 结果 | 说明 |
|---|---|---|
| 15 篇笔记全部通读、未跳读 | ✅ | 01–15 逐篇 Read；需要细节处回指 sources 行号 |
| 每条断言带出处 | ✅ | 全文用 `NN-*.md:行` 或 `sources/*.txt:行` 标注；核心警告（MiMo arguments、tool_choice auto、reasoning 400）均挂到 sources 行 |
| 设计部分标「设计」 | ✅ | §5 Canonical 形状、§7.2 数值、§8 组件映射、§8.4 常量值均标「设计/推断」 |
| 拿不准处标「推断」 | ✅ | SF V3.2/V4 精确上下文、Anthropic tool_use 子字段形状（原文 SPA 折叠）、重连策略、`src/ai` 现状职责划分均标「推断」 |
| 无编造原文不存在内容 | ✅ | SF 缓存 TTL、MiMo 错误码/限流数值均按笔记如实写"原文未给"，未补数；Anthropic 工具块逐字段形状标「原文折叠未展开/推断」 |
| 边界 a/b/c/d/e/f 全覆盖 | ✅ | §9 收敛表逐条对应 |
| 只读文档、只写本目录文件 | ✅ | 仅写 `docs/learn/model-tool-calling/16-tool-loop-state-machine.md`；未改代码、未 git、未调需 key 接口 |
| 篇幅 400–700 行、全中文、多表格+状态图 | ✅ | 含 ASCII 状态机图（§1.1）+ 12 张表 |
