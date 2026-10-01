# Model Tool-Calling 能力边界总稿（硅基流动 × 小米 MiMo）

> **文档来源**：本批次 16 篇细分学习笔记（同目录 `model-tool-calling/01`–`16-*.md`）+ 其引用的 `model-tool-calling/sources/*.txt` 纯文本底稿（13 个，均带行号）。本稿不新抓任何文档，全部断言收敛自既有笔记与底稿。
> **精读方式**：逐篇通读 16 篇笔记全文；关键逐字句回读 `sources/*.txt:<行>` 核对。
> **精读日期**：2026-10-01　**作者**：MainAgent（批次收尾综合 agent）
> **覆盖边界**：a–f 六条能力边界全链条收敛 + 双协议对照 + 模型差异，并落到 `src/ai/` 改造速查。
> **标注约定**：「原文」= 可在 `sources/*.txt:<行>` 或 `NN-*.md` 复核；「设计」= 本稿基于原文约束给出的实现蓝图；「推断」= 原文无直接证据、按 OpenAI/Anthropic 通用协议外推；「原文未给」= 两家原文均无此细节。
>
> **一句话定位**：MBDSDR 把无线电能力做成给 LLM 的 function calling 工具集——模型=大脑，runtime=中介（校验 arguments → 真实执行 → `role=tool` 回传 → 保留 reasoning_content → 多步循环到 stop）。本稿是 16 篇细分笔记收敛出的权威总览，供后续直接指导 `src/ai/` 改造。

---

## 0. 六条能力边界总览（先给结论）

| 边界 | 一句话结论 | 双服务商差异 | runtime 动作 |
|---|---|---|---|
| **a** | FC = OpenAI 兼容标准循环：tools 请求 → tool_calls → role:tool 回传 → 到 stop | 两家同构；MiMo 实证并行多 tool_calls，SF 文档仅单调用示例 | 建 id 配对表，N 个 tool_call → N 条回传 |
| **b** | assistant 的 `reasoning_content` 必须逐字保留并回传（工具前/间/后/跨轮所有片段） | SF=软后果（崩链+缓存劣化）；MiMo=硬后果（缺失直接 400） | append-only，禁改/合拆/重排/丢弃 |
| **c** | 模型生成的 `arguments` 不保证合法 JSON、可能虚构 schema 外参数 | 警告逐字挂在 MiMo（两处）；SF 官方示例反而用 eval（反例） | parse→schema→白名单；禁 eval |
| **d** | `tool_choice` 一律 auto，别指望强制指定工具 | MiMo 明文剥除非 auto 值；SF 参数表无此行、仅示例 auto | 只发 auto 或不发，靠 prompt 约束 |
| **e** | 思考模式 + 工具调用可能不稳定，需按模型配置 thinking | **两家相反**：SF 对 V3.2/GLM-4.7 要求「开着并保留」；MiMo 建议「调 tool 时关掉」 | 按 ModelConfig 分档；tool_calls 混入 reasoning=降级信号 |
| **f** | MiMo 另支持 Anthropic 兼容协议：tool_use/tool_result 内容块 | 仅 MiMo（/anthropic/v1/messages）；SF 另有 /messages 端点但未抓正文 | Canonical 中间形状 + 双适配器 |

---

## 1. 六条能力边界逐条展开（验收 §1）

### 1.1 边界 a —— Function Calling = OpenAI 兼容标准循环

**原文结论（逐字关键句）**

> "该功能和 OpenAI 兼容"（SF）；tools 形状 `{type:function, function:{name, description, parameters(JSON Schema)}}`（`sources/sf-function-calling.txt:86-100`、`sources/mimo-openai-api.txt:61-69`）。
> 响应 `choices[].message.tool_calls[].function.{name, arguments}`，`tool_calls` "可包含一个或多个"（`sources/mimo-openai-api.txt:82`）。
> 回传 `{role:"tool", tool_call_id, content}`，`tool_call_id` "必须与 assistant_message.tool_calls[i].id 严格匹配"（`sources/mimo-openai-api.txt:262`）。

**循环骨架（官方 run_turn，`sources/mimo-reasoning.txt:107-124`）**

```
while True:
    resp = llm.chat(messages, tools, tool_choice="auto", thinking=...)
    messages.append(resp.choices[0].message)     # 整条 assistant 原样入历史（三件套）
    if not message.tool_calls: break               # 无 tool_calls = 终答（等价 stop）
    for tc in message.tool_calls:                 # 并行调用逐个执行
        args = validate(tc.name, tc.function.arguments)   # schema 校验，禁 eval
        result = execute(args)
        messages.append({role:"tool", tool_call_id:tc.id, content:result})
```

**双服务商差异**

| 维度 | 硅基流动 SF | 小米 MiMo |
|---|---|---|
| 协议入口 | `POST /v1/chat/completions`（`sources/sf-chat-completions-api.txt:9-11,369`） | 同左（`sources/mimo-openai-api.txt:15`） |
| 单次响应多 tool_calls | 文档两示例都只取 `tool_calls[0]`（`sources/sf-function-calling.txt:295-305,401-411`），未举证并行（推断：OpenAI 兼容应一致） | **实证并行**：一轮同时 `get_current_weather`+`get_time`，模型 reasoning 自述 "I can call both in parallel"（`sources/mimo-reasoning.txt:134-140`） |
| 终止信号 | 文档未列 `finish_reason` 枚举，示例只见 `stop`（`sf-chat-completions-api.txt:488`） | 枚举 `stop/length/tool_calls/content_filter/repetition_truncation`（`mimo-openai-api.txt:76`） |
| 先 append assistant 再 append tool | 示例如此（`sf-function-calling.txt:300-305`） | 同左（`mimo-reasoning.txt:113`） |

**对 runtime 的实现要求**：① 一次响应可能带 N 个 tool_calls，先落 `pending = {tool_call_id -> (name, args_str)}` 配对表；② N 个 tool_call → **N 条** `role:"tool"`，一条不能少，少一条=悬空 tool_call；③ 配对权威是 `tool_call_id` 不是函数名/下标（同函数可并行调两次，两个 id）；④ 全部 tool 结果回传完才发下一轮；⑤ 终止不只认 `stop`，无 tool_calls 即等价终止。

---

### 1.2 边界 b —— reasoning_content 必须逐字保留并回传

**原文结论（逐字关键句）**

> SF："You must preserve `reasoning_content` **exactly as received**, and send it back **unchanged** in subsequent requests."（`sources/sf-interleaved-thinking.txt:83-84`）；需保留 "not only … after user messages, **but also** … after tool results"（`:87-89`）。
> MiMo："必须完整回传 `reasoning_content` 字段，**否则 API 将返回 400 错误**。"（`sources/mimo-reasoning.txt §多轮对话回传要求`；逐字见 `08-mimo-reasoning-roundtrip.md:38`）

**出现位置（全覆盖，`sources/sf-interleaved-thinking.txt:90-100`）**

| 位置 | 是否保留回传 | 出处 |
|---|---|---|
| 工具调用之前 | 是 | `:90,96` |
| 多次工具调用之间 | 是 | `:91,97` |
| **收到工具结果之后**（最易漏） | 是 | `:92-93,98`；`sources/mimo-reasoning.txt` Request 1-2/2-2 |
| 跨多个 user 轮次（Turn1→Turn2） | 是，全程不裁剪、保持原序 | `:99-100` |

**双服务商差异（硬/软强度）**

| 维度 | SF | MiMo |
|---|---|---|
| 缺失后果 | **软**："Broken multi-step behavior / Instability across tool calls / **Reduced cache efficiency and degraded output quality**"（`sf-interleaved-thinking.txt:109-112`） | **硬**：HTTP 400；即便不报错也 "指令遵循下降，幻觉增多"（`sources/mimo-reasoning.txt:40`） |
| 触发条件 | 凡带 reasoning_content 一律保留 | 三要素同时成立才 400：①开深度思考 ②历史出现过 tool_calls ③本轮 assistant 带 tool_calls（`08:39`） |
| 禁止动作 | 明确列 ❌：改文本/清洗后处理/合并拆分/重排/只留正文（`:103-108`） | 未逐字列，但"必须完整回传"隐含逐字 |
| 字段名 | `message.reasoning_content`（`sf-chat-completions-api.txt:486`） | 同名（`sources/mimo-faq.txt:153-157`） |
| 关闭思考时 | 该键整个省略（不写空串） | 同左，`usage.completion_tokens_details.reasoning_tokens=0`（`08:28`） |

**对 runtime 的实现要求**：① 每轮 assistant 消息**整条原样 append**（content + reasoning_content + tool_calls 三件套，`02:125-132`）；② 对历史消息 **append-only，禁止事后改写任何字段**；③ 流式 `delta.reasoning_content` 逐片 `+=`，绝不 trim/正则清洗/合并；④ 关思考时该键**省略**而非写空串；⑤ 缓存视角：丢 reasoning = 前缀失配 = cache miss 上涨（`sf-interleaved-thinking.txt:112` + `15:65` 机理）。

---

### 1.3 边界 c —— arguments 不合法 / 幻觉参数，必须校验

**原文结论（逐字警告，MiMo 两处）**

> "模型生成的内容并非总能保证是有效的 JSON，且可能会虚构出函数模式中未定义的参数。在调用函数之前，请在代码中对这些参数进行验证。"
> —— 非流式 `message.tool_calls.function.arguments`（`sources/mimo-openai-api.txt:88-89`）；流式 `delta.tool_calls.function.arguments` **重复同一警告**（`:114`）。

两条独立风险：① `json.parse(arguments)` 可能直接失败；② parse 成功也可能多出 schema 外幻觉键。

**双服务商差异**

| 维度 | SF | MiMo |
|---|---|---|
| 是否明文警告坏 JSON/幻觉参数 | 本批 sources **未出现**该警告（`05:143` 已 grep 核实 FAQ 无此句） | **逐字警告挂两处**（`:88-89,114`） |
| 官方示例执行方式 | 用 `eval(f'{name}(**{args})')`，且 eval 前无校验（`sf-function-calling.txt:297,403`）——**教学反例** | 官方 SDK 用 `json.loads(arguments)` 后再 `TOOL_MAP[name](**args)`（`sources/mimo-reasoning.txt:118-119`） |
| arguments 传输形态 | 文档未明示字符串/对象，eval 反推为 JSON 字符串（`01:85`） | 明说 `arguments` 是 **string，格式为 JSON**（`mimo-openai-api.txt:87`） |

**对 runtime 的实现要求（校验流水线，放在 parse 后、执行前）**

```
tool_call(name, arguments_str)
 ├─[1] name 白名单查注册表 ──未知函数──► 拒绝，回"无此工具"错误
 ├─[2] json.parse(arguments_str) ──失败──► 回"不是合法 JSON"错误（不崩溃）
 ├─[3] schema 校验：required 齐全？类型匹配 properties[k].type？enum 命中？
 │     出现 properties 未声明的键？= 幻觉参数
 └─[4] 通过 → 真实 SDR 执行（runtime 再做频段/量程二次范围校验）
```

分层失败策略（`09:131-134`）：缺必填/类型错/enum 越界 → **A. 拒绝整次调用 + 回传错误**；仅多出未声明幻觉键 → **B. 裁剪后执行**并在回传注明丢弃了哪些键。**红线：禁 eval、禁把未校验 dict 直 unpack 进硬件调用**（SDR 调谐频率是硬件参数，幻觉参数会驱动错误硬件动作）。

---

### 1.4 边界 d —— tool_choice 后端强制 auto

**原文结论（逐字关键句）**

> MiMo："当 `tool_choice` 传入非 `auto` 值时，后端会默认移除该字段，模型响应行为仍等同于 `auto` 模式（该逻辑保留调整的可能性）。可选值：`auto`。"（`sources/mimo-openai-api.txt:57-59`）

**双服务商差异**

| 维度 | SF | MiMo |
|---|---|---|
| 参数表是否有 tool_choice 行 | **无此行**（通读 `sf-chat-completions-api.txt:35-319` 全表确认）；全文唯一出现在示例 `tool_choice="auto"`（`:459`） | 有此行，但枚举**只有 auto 一个合法值**（`mimo-openai-api.txt:57-59`） |
| FC 指南是否提 tool_choice | 通篇未提（`sf-function-calling.txt` 全文） | FAQ 示例也是 `"auto"`（`sources/mimo-faq.txt:195`） |
| 强制指定工具（required/指定函数） | 文档层面未承诺 | 传 `{type:function,function:{name}}` 会被后端静默剥成 auto |

**对 runtime 的实现要求**：① 请求构造器**只发 `"auto"` 或整行省略**；② 禁止写死 `tool_choice:"required"`/指定函数名——写了也被吃掉，等于自欺；③ 要"必须调某工具"靠 **system prompt + tools 描述**引导，别赌参数；④ 做成配置项 `tool_choice:"auto"|null`，括号注明"该逻辑保留调整的可能性"，未来放开只改配置。

---

### 1.5 边界 e —— 思考模式 × 工具调用稳定性（两家结论相反）

**原文结论（逐字关键句）**

> SF（正向特性）：interleaved thinking "enables robust multi-step execution"，目前**仅 DeepSeek V3.2 与 GLM-4.7** 两款支持（`sources/sf-interleaved-thinking.txt:46-48`）；要求**开着**并严格保留 reasoning_content。
> MiMo（风险状态）："The appearance of `tool_calls` in the reasoning content indicates **instability and incomplete output** … It is recommended to **disable `thinking` when calling `tool` calls**."（`sources/mimo-faq.txt:200-202`）

**双服务商差异（相反结论）**

| 对比维度 | SF | MiMo |
|---|---|---|
| 对"思考中穿插工具"的定性 | **正向特性**，专为 Agent/工具链设计（`sf-interleaved-thinking.txt:49-53`） | **不稳定状态本身**，开 thinking 调 tool 会输出不完整（`mimo-faq.txt:202`） |
| 官方建议 | **开着** interleaved thinking + 逐字保留 = 稳定 | 调 tool 时**关掉** thinking 更稳 |
| 支持模型 | 仅 V3.2 / GLM-4.7 | 本页未列型号；示例用 `mimo-v2.6-pro`（`mimo-faq.txt:163`） |
| 不稳定信号 | 丢弃/改写 reasoning → 崩链/缓存劣化（`:109-112`） | **tool_calls 混进 reasoning_content 文本**（`mimo-faq.txt:202`） |
| thinking 开关参数 | `enable_thinking`(bool)+`thinking_budget`(128–32768)（`sf-chat-completions-api.txt:59-69`） | `extra_body:{thinking:{type:"enabled"|"disabled"}}`，默认开（`sources/mimo-reasoning.txt §请求参数`） |
| 特例红线 | **DeepSeek-V3.1 做 FC 必须 `enable_thinking=false`**（原文："If you want to use the function call feature for deepseek-ai/DeepSeek-V3.1, you need to set enable_thinking to false."，`model-list-sf.txt §二`） | 无此型号特例 |

> **两层建议别混**：MiMo 主文档说"你要开着思考跑工具，那 reasoning_content 必须回传否则 400"；FAQ 说"更稳是调工具时关 thinking"。runtime 两条都要实现。

**对 runtime 的实现要求**：① 维护「模型 × thinking 默认策略」表（§6.2）；② 响应后做 S1 检测：reasoning_content 文本里混着 tool_calls JSON → 记 WARN + 标记本轮不稳定，对 MiMo 据此触发"下次关 thinking"重试；③ 一旦某循环决定开 thinking，就**整轮持续逐字回传**，中途丢弃=崩；④ `reasoning_effort` 仅 V4/V4-Flash/GLM-5.2 允许，不能下发给 V3.2/GLM-4.7（两档名单不重叠，`13:54`）。

---

### 1.6 边界 f —— MiMo Anthropic 兼容协议

**原文结论（逐字关键句）**

> endpoint `POST /anthropic/v1/messages`（`sources/mimo-anthropic.txt:20`）；system 是**顶层独立参数**不塞进 messages（`:54-55`）；工具声明 `tools[].type="custom"` + `input_schema`（`:74-89`）；响应 content[] 折叠含 **Text / Thinking / Tool use** 三块（`:100-103`）；`stop_reason="tool_use"`（`:109`）；`tool_choice.disable_parallel_tool_use`（bool，默认 false）嵌在 tool_choice 内，true 时模型至多输出一个 tool_use（`:72-73`）。

**双服务商差异**：Anthropic 兼容协议**仅 MiMo 本批抓了正文**；SF 侧边栏有 `/messages-post` 端点（`sf-chat-completions-api.txt:545`）但正文未抓（原文未给细节）。

**对 runtime 的实现要求**：内部维护协议无关的 **Canonical 中间形状**，协议只在边界互转（§2）；thinking 块要原样夹在 text/tool_use 之间回传（`:62,78`）。

---

## 2. 双协议对照：OpenAI 兼容 vs Anthropic 兼容（验收 §2）

> 权威收敛自 `11-mimo-anthropic-protocol.md` §1.3/§1.4（MiMo 同时提供两协议）。SF 侧 Anthropic 端点正文未抓，以下 Anthropic 形状以 MiMo 原文为准。

### 2.1 请求 / 响应 / 回传形状差异表

| 概念 | OpenAI 兼容（两家同构） | Anthropic 兼容（MiMo） | 适配器转换要点 | 出处 |
|---|---|---|---|---|
| endpoint | `POST /v1/chat/completions` | `POST /anthropic/v1/messages` | base_url 随之变 | `mimo-openai-api.txt:15`；`mimo-anthropic.txt:20` |
| system 提示词 | messages 内 `{role:"system"}` 一条 | **顶层 `system=` 参数** | Canonical 独立存 system，发时按协议归位 | `mimo-openai-api.txt:128`；`mimo-anthropic.txt:54` |
| 认证头 | `api-key:` 或 `Authorization: Bearer`（二选一） | 同左（换协议不换 key） | 两协议一致 | `mimo-openai-api.txt:21-24`；`mimo-anthropic.txt:22-28` |
| 工具声明 | `tools[].type="function"` + `function.{name,description,parameters}` | `tools[].type="custom"` + `{name,description,input_schema}` | type 字面量替换；name/description 提一层；`input_schema`↔`parameters` 整体平移 | `mimo-anthropic.txt:85` vs `mimo-openai-api.txt:64-68` |
| 工具入参 | `function.arguments` 是 **JSON 字符串** | `tool_use.input` 是 **JSON object** | **关键差异**：发 OpenAI 时 `json.dumps`，收 OpenAI 后 `json.loads` | `mimo-openai-api.txt:87`；`mimo-anthropic.txt:93` |
| 工具调用块位置 | `message.tool_calls[]` | content[] 里 `tool_use{id,name,input}` | 数组平移 | `mimo-anthropic.txt:101` |
| 终止信号 | `finish_reason="tool_calls"` | `stop_reason="tool_use"` | 映射；自然停止=OpenAI `stop` ↔ Anthropic `end_turn` | `mimo-openai-api.txt:76`；`mimo-anthropic.txt:105-109` |
| **工具结果回传** | `{role:"tool", tool_call_id, content}`（每条独立消息） | `{role:"user", content:[{type:"tool_result", tool_use_id, content}]}`（N 个合并进一个 user 块） | **封装位置完全不同** | `mimo-openai-api.txt:260-264`；`mimo-anthropic.txt:99` |
| 生成长度 | `max_completion_tokens` | `max_tokens`（范围 [1,131072]，v2.6 默认 131072） | 字段改名 | `mimo-openai-api.txt:38-39`；`mimo-anthropic.txt:44-46` |
| 停止序列 | `stop`（最多 4 个） | `stop_sequences` | 字段改名 | `mimo-openai-api.txt:45`；`mimo-anthropic.txt:48` |
| 思考内容 | `message.reasoning_content` 字符串 | content[] 里 `thinking` 块 | Anthropic 把 thinking 块原样夹回 text/tool_use 之间 | `mimo-openai-api.txt:80`；`mimo-anthropic.txt:101,131` |
| usage | `prompt_tokens/completion_tokens/cached_tokens` | `input_tokens/output_tokens/cache_read_input_tokens` | 字段名映射 | `mimo-openai-api.txt:96-99`；`mimo-anthropic.txt:112-115` |
| 并行控制 | 无服务端开关（tool_choice 被剥成 auto） | `tool_choice.disable_parallel_tool_use`（默认 false） | 串行化只能在 Anthropic 协议下服务端约束 | `mimo-anthropic.txt:72`；`mimo-openai-api.txt:57-59` |
| 流式增量 | `delta.reasoning_content` + `delta.tool_calls[].function.arguments` | `thinking_delta`(`delta.thinking`) + `input_json_delta`(`delta.partial_json`) | 聚合器按协议分两套 | `mimo-openai-api.txt:106-114`；`mimo-anthropic.txt:134-137` |

> **完整度声明**：MiMo Anthropic 页面是 SPA，`tool_use/tool_result/thinking` 块的逐字段形状（id/name/input、signature 等）由前端 JS 折叠渲染，web_fetch 抓不到；上表块子字段形状标注「原文折叠未展开/推断」，落地前建议真实联调验证（`11:162-163`）。

### 2.2 协议适配器要点（设计）

```
Canonical（协议无关中间形状）:
  SystemPrompt string
  Message { role: user|assistant|tool, text, thinking,
            tool_calls:[{id,name,args(JSON object)}],
            tool_results:[{tool_call_id, content}] }
  ToolDef { name, description, input_schema(JSON Schema) }
  SamplingParams { max_tokens, temperature, top_p, stop_seq[], thinking_enabled }
→ send(canonical) → CanonicalResponse { text, thinking, tool_calls, stop_reason, usage }
```

两个适配器 `OpenAIAdapter` / `AnthropicAdapter` 各做双向转换，转换规则即 §2.1 表。校验器（边界 c）两协议共用——Anthropic 侧 `input` 是 object 直接校验，OpenAI 侧 `arguments` 先 `json.loads` 再校验。

---

## 3. 思考模式与 reasoning_content 全链路规则（验收 §3）

### 3.1 出现位置 + 逐字保留（一图流）

```
Turn k:
  [user] 提问
  [assistant-1] content:""  reasoning:"先思考要不要调工具…"  tool_calls:[A,B]   ← 工具前的思考
  [tool A] {role:tool, tool_call_id:A.id, content:"..."}
  [tool B] {role:tool, tool_call_id:B.id, content:"..."}
  [assistant-2] content:"最终回答"  reasoning:"结合结果再想…"  tool_calls:null    ← 工具结果后的新思考（最易漏）
Turn k+1:
  [user] 追问
  [assistant-3] …（Turn1 全部 reasoning 仍在 messages 里）                      ← 跨轮全程不裁剪
```

### 3.2 全链路规则清单

| 规则 | 说明 | 出处 |
|---|---|---|
| 字段定位 | `reasoning_content` 与 `content`、`tool_calls` 同级，挂在 `message` 上 | `sf-chat-completions-api.txt:486`；`sources/mimo-faq.txt:153-157` |
| 逐字保留 | 改字/清洗/合拆/重排/只留正文 = 明令禁止 | `sf-interleaved-thinking.txt:103-108` |
| 流式形态 | `delta.reasoning_content` 逐片 `+=`，与 `delta.content` 物理分两条通道；MiMo 先思考流后回答流、时间上不重叠 | `sf-interleaved-thinking.txt:118-119`；`08:102-104` |
| 流/非流同规则 | "This rule applies to both streaming and non-streaming"，流式只改变读法不改保留内容 | `sf-interleaved-thinking.txt:125-126` |
| 缺失后果（SF） | 多步工具链崩 + 跨调用不稳定 + 缓存效率下降 + 输出质量劣化 | `sf-interleaved-thinking.txt:109-112` |
| 缺失后果（MiMo） | **HTTP 400**（三要素触发）；否则指令遵循下降、幻觉增多 | `sources/mimo-reasoning.txt:38-40` |
| 关闭思考时 | 该键整个省略，不要写空串；`reasoning_tokens=0` | `08:28,140` |
| 思考开关参数名 | SF=`enable_thinking`(bool)+`thinking_budget`；MiMo=`thinking.type`(extra_body)，默认开 | `sf-chat-completions-api.txt:59-69`；`mimo-reasoning.txt §请求参数` |
| 开思考时采样参数 | MiMo 强制覆写 `temperature=1.0`/`top_p=0.95`（传了不生效）；`max_completion_tokens` 是"思考+回答"总长 | `sources/mimo-reasoning.txt:31-32` |
| 两家"调工具时开不开 thinking"相反结论 | SF V3.2/GLM-4.7：**开着**并保留（interleaved 为特性）；MiMo：**关掉**更稳 | `sf-interleaved-thinking.txt:49-53`；`mimo-faq.txt:202` |

---

## 4. 流式协议要点（验收 §4）

### 4.1 SSE 行解析

| 规则 | 说明 | 出处 |
|---|---|---|
| 两处都要开流式 | 请求体 `stream:true` **且** HTTP 客户端 `stream=True`/`CURLOPT_WRITEFUNCTION` 逐回调吐，否则不按流返回 | `sources/sf-stream-mode.txt:103-104,129` |
| 传输形态 | 一行 `data: {json}\n\n`；事件间空行；最后一行字面量 `data: [DONE]` | `sf-stream-mode.txt:135-139`；`sources/mimo-openai-api.txt:117` |
| 按行缓冲 | TCP 回调遇半行要粘包，`\n` 才是事件边界 | `sf-stream-mode.txt:135` |
| 剥前缀 | 精确剥 `data: `（含一个空格）；空行跳过；未知行前缀只忽略不报错 | `sf-stream-mode.txt:137` |
| `[DONE]` 后不再 json.parse | 它是传输层哨兵，不是业务终止 | `sf-stream-mode.txt:138` |
| 空 chunk 容忍 | `choices==[]` 的 chunk 直接 `continue`（通常是末尾 usage chunk） | `sf-stream-mode.txt:81-82`；`mimo-openai-api.txt:272` |

### 4.2 三通道累加器（物理隔离）

```
reasoning_buf = ""   # delta.reasoning_content 逐片 +=
content_buf    = ""  # delta.content 逐片 +=
slots = {}           # index -> {id, type, name, arguments_buf}
```

| delta 字段 | 拼接动作 | 出处 |
|---|---|---|
| `delta.content` | `content_buf += 文本` | `sf-stream-mode.txt:141` |
| `delta.reasoning_content` | `reasoning_buf += 文本`，与 content 分两条独立通道，绝不合并 | `sf-stream-mode.txt:142,146-148` |
| `delta.tool_calls[i]` | 按 `index` 分槽累加（§4.3） | `mimo-openai-api.txt:108` |
| `finish_reason` | 末片携带，记录后停止累加 | `mimo-openai-api.txt:115` |
| 顶层 `usage` | 仅最后一个 `choices:[]` chunk 携带，在此笔入账 | `mimo-openai-api.txt:118,272` |

### 4.3 tool_calls 按 index 分槽拼接（关键）

MiMo 是流式 tool_calls 字段的权威定义（`mimo-openai-api.txt:108-114`）：

```
slots = {}                       # index 从 0 开始
for chunk in sse:
    if not chunk.choices: continue
    for tc in (chunk.choices[0].delta.tool_calls or []):
        i = tc.index             # 多工具并行靠它区分
        s = slots.get(i) or {id:"", type:"function", name:"", args:""}
        if tc.id:                 s.id = tc.id          # 首片带 id
        if tc.type:               s.type = tc.type
        if tc.function.name:      s.name = tc.function.name      # name 一般首片即全量
        if tc.function.arguments: s.args += tc.function.arguments # ★字符串逐片 +=
        slots[i] = s
# 流末才对每个槽：json.parse(args) → schema 校验
```

红线（`14:150-154`）：① **不要边收边 `JSON.parse(arguments)`**——分片可能切在任意字符中间，半片一定不是合法 JSON；② 多工具并行 = 多个 index 槽，逐槽拼接逐个执行；③ 回传 `tool_call_id` 严格匹配该槽 `id`；④ 不要照搬 SF 教学 demo 的 `tool_calls.extend(delta.tool_calls)`（`sf-stream-extras.txt §4`）——那是非分片简化写法，真并行多分片会错位。

### 4.4 两层终止

| 层 | 信号 | 作用 |
|---|---|---|
| 业务层 | `finish_reason`（末个带 choices 的 chunk 里） | `tool_calls`→执行回传继续循环；`stop`→出终答；`length`→截断 |
| 传输层 | `data: [DONE]` | 关读循环、收尾 HTTP |

顺序：末片带 `finish_reason` → 可能再来空 choices 带 usage chunk → `data: [DONE]` → 连接关闭（`14:82`）。**两家文档均无 SSE 断点续传字段**（`14:91`）：中途断线不做透明续传，标记 incomplete 上报；429/503 是**整条请求重发**（复用同一 messages 历史），不是续传。

---

## 5. 工具 Schema 与校验规则（验收 §5）

### 5.1 JSON Schema 要求（两家逐字段对照）

| 字段 | 取值/约束 | 出处 |
|---|---|---|
| `tools[].type` | 固定 `"function"`（OpenAI）/`"custom"`（Anthropic） | `mimo-openai-api.txt:64`；`mimo-anthropic.txt:85` |
| `function.name` | `a-z/A-Z/0-9/_/-`，长度 1–64 | `mimo-openai-api.txt:66` |
| `function.description` | 可选；Anthropic 侧"可选但强烈推荐" | `mimo-openai-api.txt:67`；`mimo-anthropic.txt:82` |
| `function.parameters` | JSON Schema；顶层 `type:"object"`，含 `properties`/`required`；省略=空参数列表 | `mimo-openai-api.txt:68`；`sf-function-calling.txt:204-215` |
| 参数级 `type` | 示例混用 `int/str/float`（Python 风）与 `string`（JSON Schema 风）；推断严格标准应 `integer/number/string` | `sf-function-calling.txt:207,249,270` vs `:376` |
| 参数级 `enum` | string[]，限定取值范围（如 `["celsius","fahrenheit"]`） | `sf-chat-completions-api.txt:440`；`mimo-openai-api.txt:196` |
| `strict`（仅 MiMo） | bool 默认 false；true 时仅支持 JSON Schema 子集 | `mimo-openai-api.txt:69` |
| tools 数量上限 | SF **最多 128 个函数**；MiMo 未写上限 | `sf-chat-completions-api.txt:173,317` |

### 5.2 arguments 字符串二次 parse + 校验

- 模型回的 `arguments` 是一段 **JSON 字符串**（OpenAI 侧），runtime 拿到后必须 `json.loads`/`JSON.parse` 成对象才能用；流式先把多次 delta 片段拼成完整字符串再 parse（`mimo-openai-api.txt:87,108-114`）。
- 校验失败自纠路径：复用 `role:"tool"` 通道把错误喂回模型（`09:146-162`）：

```jsonc
{ "role":"tool", "tool_call_id":"<与 assistant.tool_calls[i].id 严格一致>",
  "content":"参数校验失败：工具 set_frequency 缺少必填参数 freq_hz；
             未知参数 band 已被忽略；合法示例：{\"freq_hz\":145000000}" }
```

- content 说清**错在哪、合法长什么样**，模型下一轮即自纠。
- **禁 eval**：SF 官方教学示例自己用 `eval(f'{name}(**{args})')`（`sf-function-calling.txt:297,403`）——那是反面教材，生产环境严禁照搬。

---

## 6. 模型清单与差异（验收 §6）

### 6.1 模型 × 能力矩阵

> 列含义：FC=function calling；IT=interleaved thinking（工具结果后继续出 reasoning_content 且必须回传）；Ctx=上下文窗口。出处为各笔记收敛。

| 模型 | 服务商 | FC | thinking 开关 | IT | 协议 | Ctx | 备注 / 出处 |
|---|---|---|---|---|---|---|---|
| `deepseek-ai/DeepSeek-V3.2` | SF | ✓（旁证） | `enable_thinking`+`thinking_budget` | **✓ 官方** | OpenAI（+Anthropic 端点） | 未标（推断 128k 级） | interleaved 官方两模型之一，必须逐字回传 reasoning `sf-interleaved-thinking.txt:46,83` |
| `zai-org/GLM-4.7` | SF | ✓（旁证） | 未在 enable_thinking 13 清单 | **✓ 官方** | OpenAI | 未标 | interleaved 另一模型，规则同 V3.2 `sf-interleaved-thinking.txt:220-226` |
| `deepseek-ai/DeepSeek-V3.1` / `-Terminus` | SF | ✓ | `enable_thinking` | 未证实 | OpenAI | 未标 | **做 FC 必须 `enable_thinking=false`**（原文红线）`model-list-sf.txt §二` |
| `deepseek-ai/DeepSeek-V4-Flash` | SF | ✓（API 示例） | `enable_thinking`+`reasoning_effort`(high/max) | 未证实 | OpenAI | blog 旁证 1M（推断） | reasoning_effort 三模型之一；agent 请求自动 max `sf-chat-completions-api.txt:73` |
| `deepseek-ai/DeepSeek-V4-Pro` | SF | ✓（blog） | 同上 | 未证实 | OpenAI（+Anthropic） | blog 旁证 1M（推断） | reasoning_effort 三模型之一 |
| `Pro/zai-org/GLM-5.2` | SF | ✓（旁证） | `reasoning_effort` | 未证实 | OpenAI | 未标 | reasoning_effort 三模型之一 |
| `Qwen/Qwen3-*` 系 | SF | ✓（旁证） | `enable_thinking`+`thinking_budget` | 未证实 | OpenAI | 131072 | 唯一支持 `min_p`；到预算 Qwen3 强制停 CoT `12:165` |
| `mimo-v2.6-pro` | MiMo | ✓ | `thinking.type=enabled/disabled`（默认开） | 工具后可再出 reasoning | **OpenAI + Anthropic** | 1M / out 128K | 旗舰；开思考调 tool 不稳建议工具轮关；缺 reasoning 直接 400 `model-list-mimo.txt §一` |
| `mimo-v2.6-flash` | MiMo | ✓ | 同上（默认开） | 同上 | OpenAI + Anthropic | 1M / out 128K | 高频办公 |
| `mimo-v2.6-pro-ultraspeed` | MiMo | ✓ | 同上 | 同上 | OpenAI + Anthropic | 1M / out 128K | 强实时；限流定制 |
| `mimo-v2.5` / `-v2.5-pro` | MiMo | ✓ | 同上 | 同上 | OpenAI + Anthropic | 1M | **2026-10-21 弃用**，勿新接 |

> SF 其余 FC 候选（Kimi-K2 系、gpt-oss、MiniMax-M2、Hunyuan-A13B、gemma-4 等）见 `model-list-sf.txt §一`（约 70 LLM，FC 精确开关以模型广场 tags=Tools 为准，需登录，推断）。MiMo 限流：v2.6 系 RPM 100 / TPM 10M（`model-list-mimo.txt §一`）；SF 限流具体 TPM 数值**原文未给**（按账户/模型档位变）。

### 6.2 选型建议（衔接 12 篇）

| 候选 | 评级 | 理由（出处） |
|---|---|---|
| **SF DeepSeek-V3.2 / GLM-4.7** | ★★★★★ | 官方明确 interleaved thinking，就是为 tool-calling flows 设计；`sf-interleaved-thinking.txt:46-53` |
| **MiMo v2.6-pro** | ★★★★☆ | FC+思考+1M 上下文+双协议齐全；但需按边界 e 在工具轮关 thinking，且 reasoning 不回传直接 400 |
| SF V4-Flash/Pro | ★★★★☆ | reasoning_effort 可调 max，1M 上下文（blog）；interleaved 未官方背书 |
| Qwen3 系 | ★★★☆☆ | enable_thinking 官方支持、131072 上下文；interleaved 未证实，思考到 budget 强制停 CoT |
| DeepSeek-V3.1 | ★★☆☆☆ | **FC 必须关 thinking**，等于放弃思考做多步，与 agent 大脑目标相悖 |
| mimo-v2.5 / v2.5-pro | ★☆☆☆☆ | 2026-10-21 弃用，勿新接 |

**结论**：MBDSDR agent 大脑首选 **SF 的 DeepSeek-V3.2 / GLM-4.7**（interleaved 官方背书），备选 **MiMo v2.6-pro**（双协议、1M 上下文，但工具轮关 thinking）。

---

## 7. 对 src/ai/ 改造的落地速查（验收 §7，衔接 16 篇）

> 组件名为任务背景给定，**未在本批源码内逐行核对**（推断现状职责）。下表是「现有组件承担状态机哪些职责 + 需新增哪些」的映射蓝图。

| 现有组件 | 承担状态机职责 | 需改造/新增 | 出处参照 |
|---|---|---|---|
| `agent` | 主循环编排（IDLE→REQUEST→…→STOP）；DECIDE 终止判定；看门狗 max_tool_rounds | while-true 升级为显式状态机；维护 pending 配对表 | `01:245-263`；`10:133-162` |
| `llm_client` | REQUEST 构造 + HTTP 收发；协议适配；thinking 参数按 ModelConfig 下发 | 拆出 `SseParser`；加 `protocol` 字段；system/tools 固定前缀只读对象 | `04:209-235`；`11:192-202` |
| `llm_worker` | HTTP 层统一出口：超时、429/503 指数退避重试、流式 chunk 间隔超时 | 重试/超时在此收口；工具执行本身不重试 | `15:157`；`05:169` |
| `agent_tools`（注册表） | 注册即带 `{name, description, parameters(JSON Schema)}`；白名单查表 | 新增 Schema 生成器；name 合规 `[a-zA-Z0-9_-]`≤64 | `09:136-144`；`06:66` |
| `task_orchestrator` | 多步链成本预算硬停；轮次计数；命中率打点告警 | max_tool_rounds 硬停；消费 usage hit/miss | `15:158` |
| `task_runner` | EXECUTE：调真实 SDR 能力；只读工具并发、占硬件工具按资源锁串行 | 硬件单锁（两个并行调谐不能踩）；结果转字符串 | `10:164-180` |
| `ai_context` / `ai_session_store` | 消息历史累积器（append-only）；assistant 三件套落库 | 物理隔离 reasoning buffer；历史不裁剪、不改写、不 trim | `15:109`；`02:196` |

**需新增组件**：Schema 生成器（IDLE）、arguments 校验器（VALIDATE）、SSE 拼接器 SseParser+StreamAggregator（PARSE/STREAM）、协议适配器 OpenAI/Anthropic（REQUEST/PARSE）、reasoning 缓冲（PARSE/STREAM）、配对表 pending map（BUILD_TOOL_MESSAGES）。

**`src/core/tokens.h` 应抽常量速查**

| 常量 | 值 | 依据 |
|---|---|---|
| SF base / endpoint | `https://api.siliconflow.cn/v1` / `/v1/chat/completions` | `04:293-294` |
| MiMo OpenAI / Anthropic base | `https://api.xiaomimimo.com/v1` / `.../anthropic` | `06:22`；`11:28` |
| `kMaxToolsPerRequest` | 128 | `04:295`；`09:82` |
| `kThinkingBudgetMin/Max` | 128 / 32768 | `04:296`；`13:45` |
| `kDefaultToolChoice` | `"auto"` | `04:302`；`06:81-85` |
| `kToolNameMaxLen` | 64（`[a-zA-Z0-9_-]`） | `06:66`；`09:141` |
| `kHeadroomTokens` | ~10000（SF max_tokens 不含思维链） | `04:301`；`15:86` |
| `kMaxToolRounds`（看门狗） | 12（设计值） | `15:150` |
| 重试白名单 | 429 / 503(code 50505)；400/401/403/404 不重试 | `15:148`；`04:530-540` |
| 默认 MiMo 模型 | `mimo-v2.6-pro` | `12:119`；`07:41` |

> **双端一致性红线**：C++ 与 Flutter 必须用同一 index 槽算法、同一 reasoning 逐字 buffer、同一"流末二次 parse"时机，否则同一轮 LLM 输出两端拼出不同 tool_calls（`14:188`）。

---

## 8. 16 篇学习笔记索引表（验收 §8）

| # | 篇名 | 覆盖边界 | 关键内容 | sources 底稿 |
|---|---|---|---|---|
| 01 | sf-function-calling-protocol | a、c | SF tools/tool_calls/role:tool 循环；官方 eval 反例 | `sf-function-calling.txt` |
| 02 | sf-interleaved-thinking | b、e | reasoning_content 逐字保留；仅 V3.2/GLM-4.7；丢弃三后果 | `sf-interleaved-thinking.txt` |
| 03 | sf-streaming-mode | b 流式侧 | SSE 行解析；delta.content/reasoning_content 双通道累加 | `sf-stream-mode.txt` |
| 04 | sf-chat-completions-api | a/c/d 参数侧 | SF 请求全参数表；thinking 三参数；错误码；usage 缓存字段 | `sf-chat-completions-api.txt` |
| 05 | mimo-faq-integration | a/b/e/f 部分 | 双 key 双 base_url；保留 reasoning；调 tool 关 thinking；FAQ 缺项如实列 | `mimo-faq.txt` |
| 06 | mimo-openai-api | a/c/d + b/e | MiMo tools/tool_calls 结构；arguments 警告；tool_choice 强制 auto | `mimo-openai-api.txt` |
| 07 | mimo-quickstart-api | a/b | 三计费形态 base_url；双协议 SDK 首调；model=mimo-v2.6-pro | `mimo-quickstart.txt` |
| 08 | mimo-reasoning-roundtrip | b、e | 缺 reasoning 直接 400（三要素）；run_turn 完整循环；流式先思考后回答 | `mimo-reasoning.txt` |
| 09 | tool-schema-and-validation | c（+a） | JSON Schema 字段；arguments 字符串二次 parse；校验器规格；禁 eval | `sf-function-calling.txt` 等 |
| 10 | tool-choice-and-parallel | d、a 并行侧 | tool_choice 只 auto；一轮多 tool_calls；id 配对表；N 条回传 | `mimo-openai-api.txt` 等 |
| 11 | mimo-anthropic-protocol | f（+a–e 对照） | tool_use/tool_result 内容块；input(object) vs arguments(string)；disable_parallel_tool_use | `mimo-anthropic.txt` |
| 12 | model-capability-matrix | a–f 模型侧 | SF 13 模型 enable_thinking；MiMo 5 模型能力表；选型评级 | `model-list-sf.txt`/`model-list-mimo.txt` |
| 13 | thinking-config-stability | e（+b） | 两家相反结论；稳定性信号清单 S1–S6；按模型 ×任务配置 thinking | `sf-chat-completions-api.txt` 等 |
| 14 | streaming-best-practices | b、a 流式侧 | SSE 全景；三通道累加；index 分槽拼接；两层终止 | `sf-stream-extras.txt` 等 |
| 15 | cache-and-performance | b（性能侧）+限流 | prompt_cache_hit/miss；前缀固定性；429/503 退避；max_tokens 留 10k | `sf-cache-extras.txt` 等 |
| 16 | tool-loop-state-machine | a–f 全链条 | 状态机总图；消息组装规则；错误恢复；src/ai 组件映射蓝图 | 前 15 篇收敛 |

> 底稿目录：`docs/learn/model-tool-calling/sources/`（13 个 txt，均带行号可引用）。

---

## 9. 自检记录（验收 §9，规范 §2.6）

| 检查项 | 结果 | 说明 |
|---|---|---|
| 16 篇笔记全部通读、未跳读 | ✅ | 01–16 逐篇 Read；关键逐字句回指 `sources/*.txt:行` |
| 六条边界 a–f 逐条展开（原文结论+出处+双服务商差异+runtime 要求） | ✅ | §1.1–1.6 每节四要素齐全 |
| 双协议对照表 + 适配器要点 | ✅ | §2.1 差异表 + §2.2 Canonical 设计 |
| reasoning_content 全链路（位置/逐字/流式/缺失后果/两家相反结论） | ✅ | §3.1–3.2 |
| 流式要点（SSE/三通道/index 分槽/两层终止） | ✅ | §4.1–4.4 |
| Schema 与校验（JSON Schema/二次 parse/幻觉参数/禁 eval/自纠） | ✅ | §5.1–5.2 |
| 模型矩阵 + 选型建议 | ✅ | §6.1–6.2 |
| src/ai 改造速查 | ✅ | §7 |
| 16 篇索引表 + 指向 sources | ✅ | §8 |
| 每条断言带出处，禁止无出处断言 | ✅ | 全文用 `sources/*.txt:行` 或 `NN-*.md:行` 标注；核心警告（arguments、tool_choice auto、reasoning 400）均挂 sources 行 |
| 拿不准标「推断」 | ✅ | SF V3.2/V4 精确上下文、SF Anthropic 端点正文、Anthropic 工具块子字段形状、重连策略、`src/ai` 现状职责均标「推断」 |
| 两家原文都没有的标「原文未给」 | ✅ | SF 缓存 TTL、MiMo 错误码表/限流数值、SF 多 tool_calls 并行举证均如实写"原文未给/未举证"，未补数 |
| 设计部分标「设计」 | ✅ | §2.2 Canonical 形状、§7 组件映射、tokens.h 常量值均标「设计/推断」 |
| 只读文档、只写本总稿一个文件 | ✅ | 仅写 `docs/learn/model-tool-calling-boundaries.md`；未改代码、未 git、未调需 key 接口 |
| 篇幅 300–600 行、全中文、多表格 | ✅ | 约 450 行；含 20+ 张表 |

---

## 10. 一句话总结

MBDSDR 的工具循环 = 「**OpenAI 兼容骨架（a）** × **reasoning_content 逐字透传（b）** × **arguments 不信任必须校验（c）** × **tool_choice 只发 auto（d）** × **按模型分档配置 thinking（e）** × **Canonical 形状 + 双协议适配器（f）**」。最容易在真机翻车、实验室却测不出来的三个点是：**① assistant 三件套（content+reasoning_content+tool_calls）丢了 reasoning_content——SF 软崩、MiMo 直接 400；② 流式 tool_calls 不按 index 分槽、边收边 parse arguments——并行多工具必错位；③ tool_choice 赌强制指定工具——MiMo 后端静默剥成 auto，写了也白写。**
