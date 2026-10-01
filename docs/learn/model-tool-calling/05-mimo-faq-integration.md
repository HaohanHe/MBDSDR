# 小米 MiMo API 集成 FAQ 学习笔记

> **文档来源**：本地克隆 `/home/user/Doubao/chats/38438160041798146/repos/model-docs/xiaomi-mimo/en-US_quick-start_faq_api-integration.html`（约 160KB）。
> **精读方式**：python3 stdlib `html.parser` 转纯文本，全文 303 行逐节读完（脚本在 agent workspace，未入仓库）。
> **转换文本**：`docs/learn/model-tool-calling/sources/mimo-faq.txt`（本笔记所有 `mimo-faq.txt:<行号>` 引用均指向该文件）。
> **精读日期**：2026-10-01；**作者**：s_000c4GAuTz6。
> **覆盖边界**：a、b、c、d、e、f 中与本 FAQ 相关的部分。
> **标注约定**：「原文」= `mimo-faq.txt` 可直接定位；「推断」= 原文无直接证据、仅据常识/其他服务商惯例推测。
>
> **⚠ 先读这一行**：本 FAQ 实际只有 13 个问答，内容远薄于 `_SPEC §4` 预设。规范里 c（arguments 非法 JSON 警告）、d（后端强制 tool_choice=auto）、f（Anthropic tool_use/tool_result 块、disable_parallel_tool_use）、以及错误码/限流数值，**在本 FAQ 正文中均未出现**——本笔记如实记录"有"，并在 §1.8 与 §3 逐条列出"没有"，绝不补写。

---

## 1. 原文事实清单

### 1.1 文档定位与导航（原文，非正文）

| 事实 | 出处 | 要点 |
|---|---|---|
| 本页是 Quick Start → FAQ → **API Integration** 子页 | `mimo-faq.txt:28` | 同目录还有 Account/Payment/Token Plan/Promotions/Others 等 FAQ 子页，本文件只含 API Integration 一节 |
| 导航指向「Text Generation → Tool Calling」子页，但**正文未展开其内容** | `mimo-faq.txt:44-46` | Tool Calling 详情在另一个未克隆页面（web-search/Deep Thinking/Structured Output 均为外链） |
| 导航指向 API Reference 的 `guidance/rate-limit` | `mimo-faq.txt:24` | 限流正文不在本 FAQ，属另一页（本任务只读此 FAQ，不抓取） |
| 页面更新时间 | `mimo-faq.txt:255` | September 20, 2026 |

### 1.2 接入前置：认证（原文）

| 事实 | 出处 | 要点 |
|---|---|---|
| 按量付费 API Key：登录开放平台后在 **Console → API Keys** 申请 | `mimo-faq.txt:109` | 控制台地址 `platform.xiaomimimo.com/#/console/api-keys` |
| 请求头二选一：`api-key: $MIMO_API_KEY` **或** `Authorization: Bearer $MIMO_API_KEY` | `mimo-faq.txt:109` | 两种头都接受，runtime 选其一即可 |
| Token Plan（套餐）Key：购买后在 Token Plan 页查看，**创建时只显示一次** | `mimo-faq.txt:112` | 遗失/泄漏在 Token Plan 页重置（`mimo-faq.txt:119`） |
| **Key 格式分两类、互不通用**：按量 = `sk-xxxxx`；套餐 = `tp-xxxxx` | `mimo-faq.txt:115` | `tp-` 只在套餐有效期内可用；两类不能混用 |

> 对 runtime 的直接含义：MBDSDR 配置模型服务商时，必须允许用户填一个 key + 一个 base_url，并能区分 `sk-` 与 `tp-`（前缀可用于校验/日志脱敏，不要把两类 key 当同一池混用）。

### 1.3 接入前置：base_url、双协议端点、模型标识（原文）

| 事实 | 出处 | 要点 |
|---|---|---|
| 平台**同时提供两种 Base URL**：兼容 OpenAI 协议 与 兼容 Anthropic 协议 | `mimo-faq.txt:123` | 「以 Token Plan 页面提供为准，可按需复制」——FAQ 正文**未把完整 base_url 字符串印出来** |
| 示例 curl 中实际出现的 OpenAI 端点 | `mimo-faq.txt:148` | `https://api.xiaomimimo.com/v1/chat/completions` |
| OpenAI 接口路径 | `mimo-faq.txt:136` | `/v1/chat/completions`，遵循 OpenAI 格式，角色含 **developer / system / user / assistant** |
| Anthropic 接口路径 | `mimo-faq.txt:139` | `/anthropic/v1/messages`，遵循 Claude 格式，**system 是独立参数**（不塞进 messages 数组） |
| 示例中的模型标识 | `mimo-faq.txt:163` | `"model": "mimo-v2.6-pro"` |
| 示例生成参数 | `mimo-faq.txt:164-166` | `max_completion_tokens: 1024`、`temperature: 1.0`、`stream: false` |
| 支持联网搜索的模型名单 | `mimo-faq.txt:245` | 仅 `mimo-v2.6-flash` / `mimo-v2.6-pro` / `mimo-v2.6-pro-ultraspeed` / `mimo-v2.5-pro` / `mimo-v2.5` |

### 1.4 FAQ 逐条编号（原文，保留问答结构）

| # | 问题（原文标题） | 出处 | 答案要点（原文浓缩，未加戏） |
|---|---|---|---|
| Q1 | How to obtain API Key? | `:106-115` | 按量在 Console→API Keys 申请 `sk-`；套餐在 Token Plan 页得 `tp-`，仅创建时可见；两类互不通用 |
| Q2 | What if the API Key is lost or leaked? | `:117-119` | 在 Token Plan 页重置 |
| Q3 | How can I obtain the Base URL of the Token Plan? | `:121-123` | 以 Token Plan 页为准；提供 OpenAI 兼容与 Anthropic 兼容两种 base_url，按需复制 |
| Q4 | Which programming tools does Token Plan support? | `:125-127` | Claude Code、OpenClaw、OpenCode、Kilo Code、Cline、Hermes Agent、CodeBuddy Code 等；接入方式见 tools-overview |
| Q5 | Can Token Plan be used in multiple tools at the same time? | `:129-131` | 同一套餐可在所有支持的工具间共用，但**配额共享**，一处使用即消耗同一额度 |
| Q6 | What's the difference between OpenAI and Anthropic interfaces? | `:133-139` | OpenAI `/v1/chat/completions`（developer/system/user/assistant 角色）；Anthropic `/anthropic/v1/messages`（Claude 格式，system 独立参数） |
| Q7 | How to make multi-turn tool calls in thinking mode? | `:141-198` | 见 §1.5（带完整 curl 示例） |
| Q8 | Why are tool_calls sometimes inside reasoning_content, sometimes in separate tool_calls? | `:200-202` | 见 §1.6 |
| Q9 | What's the response speed? | `:204-215` | 取决于：请求长度与复杂度、服务器负载与地理位置、是否用流式 |
| Q10 | How to handle timeouts? | `:217-228` | 客户端自己做：合理连接/读超时、重试用指数退避、长响应用流式 |
| Q11 | What if the API returns inappropriate content? | `:230-232` | 平台对用户输入与模型输出都做内容审核，违规自动拦截 |
| Q12 | Why no web search after enabling online search? | `:234-245` | 三因：①开关有 5 分钟缓存；②模型判断无需搜索（强制用 `forced_search: true`）；③仅部分模型支持（见 §1.3 名单） |
| Q13 | Does it support local file upload? | `:247-253` | 实时推理：不支持本地上传；Batch 推理：支持 JSONL 文件上传 |

### 1.5 Q7 原文示例（请求 JSON 原样记录，`mimo-faq.txt:147-198`）

```bash
curl --location --request POST 'https://api.xiaomimimo.com/v1/chat/completions' \
--header "api-key: $MIMO_API_KEY" \
--header "Content-Type: application/json" \
--data-raw '{
    "messages": [
        {
            "role": "assistant",
            "content": "Hello! I am MiMo.",
            "reasoning_content": "Okay, the user just asked me to introduce myself. That is a pretty straightforward request, but I should think about why they are asking this."
        },
        {
            "role": "user",
            "content": "What is the weather like in Hebei?"
        }
    ],
    "model": "mimo-v2.6-pro",
    "max_completion_tokens": 1024,
    "temperature": 1.0,
    "stream": false,
    "tools": [
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
    ],
    "tool_choice": "auto"
}'
```

> 这是本 FAQ 里**唯一**一段 function-calling 请求示例。注意几点（均原文可见）：
> 1. `tools` 结构就是 OpenAI 标准 `{type:function, function:{name,description,parameters(JSON Schema)}}`（`:167-194`）——对应边界 a 的请求侧。
> 2. `tool_choice` 字段在示例里取值 `"auto"`（`:195`）。**但 FAQ 没有任何一句话说"后端强制 auto / 不允许指定某个工具"**——那只是示例里填了 auto。见 §1.8。
> 3. messages 数组里塞了一条 `role:"assistant"` 且**带 `reasoning_content` 字段**（`:152-157`）——这就是 Q7 说的"把历史 reasoning_content 带回去"的具体形态。
> 4. 示例**没有**展示响应体、没有展示 `role:"tool"` 回传、没有展示 `finish_reason=tool_calls`——响应侧契约不在本 FAQ。

### 1.6 reasoning_content / 思考模式相关问答（原文）

| 事实 | 出处 | 原文（关键句） |
|---|---|---|
| 思考模式多轮工具调用时，模型**同时**返回 `reasoning_content` 与 `tool_calls` | `:143` | "the model returns a `reasoning_content` field alongside `tool_calls`" |
| 续聊建议把**每一次**历史 `reasoning_content` 都保留在 messages 里 | `:143` | "keep all previous `reasoning_content` in the `messages` array for each subsequent request to achieve the best performance" |
| `tool_calls` 出现在 reasoning_content 里 = **不稳定/输出不完整**信号 | `:202` | "indicates instability and incomplete output caused by the model having `thinking` enabled when calling `tool`" |
| 官方建议：调工具时**关掉 thinking** | `:202` | "recommended to disable `thinking` when calling `tool` calls" |
| 进一步调参指向另一个文档 Model Hyperparameters | `:202` | `mimo.mi.com/#/docs/quick-start/model-hyperparameters`（未克隆，本任务不读） |

> 流式侧的 `delta.reasoning_content` 形态**本 FAQ 未给出**（只在 Q9/Q10 把流式当作"提速/长响应"手段提及，`:215, :228`）。边界 b 里"流式逐块保留 reasoning"的那一半，本 FAQ 无直接证据。

### 1.7 流式 / 超时 / 错误 / 限流 / 调试相关（原文）

| 主题 | 出处 | 原文要点 |
|---|---|---|
| 流式的作用 | `:215, :228` | 影响响应速度；长响应推荐用流式。**未给 chunk 格式 / SSE 字段名** |
| 客户端超时 | `:222` | "Set reasonable connection and read timeout times" |
| 重试策略 | `:225` | "Use exponential backoff for retries"（指数退避；**未给具体退避基数/最大次数**） |
| 响应速度三因素 | `:206-215` | 请求长度复杂度 / 服务器负载与地理位置 / 是否流式 |
| 内容审核拦截 | `:232` | 输入与输出双向审核，违规自动拦截（**未给被拦截时的错误码/返回体形态**） |
| 限流 | — | **本 FAQ 无任何数值**；仅导航栏有 `guidance/rate-limit` 链接（`:24`），正文未展开 |
| 错误码表 | — | **本 FAQ 完全没有** 4xx/5xx/429 列表或错误体 schema |

### 1.8 任务清单要求、但本 FAQ 正文**没有**的内容（如实标注，禁止补写）

| 任务/规范要求项 | 本 FAQ 实情 | 结论 |
|---|---|---|
| 「arguments 不保证合法 JSON / 可能虚构 schema 外参数」逐字警告 | `grep arguments\|schema\|invalid\|fabricat\|hallucinat` 全空（`:全文`） | **本 FAQ 未出现此警告**。该说法在 `_SPEC §4.c` 被断言为"MiMo 原文明示"，但在所给这唯一一份实内容 FAQ 里**找不到**。可能在 Tool Calling 子页（`:44` 外链，未克隆）。runtime 仍应按"不信任模型产出"实现，但**出处不能挂到本 FAQ** |
| 「tool_choice 后端强制 auto」 | 仅示例值 `"tool_choice":"auto"`（`:195`）；无"forced/ignore/不支持指定工具"字样 | **本 FAQ 未明示强制**。边界 d 在本 FAQ 范围内证据不足 |
| `disable_parallel_tool_use` / 并行调用开关 | `grep parallel\|disable` 仅命中无关词 | **本 FAQ 未提并行工具调用控制** |
| Anthropic `tool_use` / `tool_result` 内容块结构 | 只给端点 `/anthropic/v1/messages` 与"system 独立参数"（`:139`）；无 content block 示例 | **本 FAQ 未给 Anthropic 工具块 schema**。边界 f 的"tool_use/tool_result 内容块、disable_parallel_tool_use"在本 FAQ 无证据 |
| 限流数值 / 错误码 / 调试端点 | 无 | 见 §1.7 |
| 真实 base_url 字符串（Anthropic 侧） | OpenAI 侧示例见 `:148`；Anthropic 侧只说"去 Token Plan 页复制" | Anthropic base_url 本 FAQ 未印出 |

---

## 2. 对 MBDSDR 工具化实现的启示

> 背景：MBDSDR runtime 要对齐模型服务商真实契约。MiMo 作为**第二服务商**接入时，与第一家（硅基流动）的差异点如下，全部落到组件位。

### 2.1 必须照搬的点（原文有证据）

1. **reasoning_content 要当"assistant 消息字段"逐字回传，而不是只在 UI 展示。**
   原文示例把历史思考直接放在 `messages[i]` 的 `role:"assistant"` 对象里，和 `content` 平级（`:152-157`）。
   → 落到 `llm_client.cpp` / `task_orchestrator`：assistant 历史消息结构应为
   ```
   {role:"assistant", content:<文本>, reasoning_content:<上次的思考>, tool_calls:[...]}
   ```
   多步循环里，每一轮把上一条 assistant 的 `reasoning_content` 原样粘回去，**不要丢、不要改写、不要只存最后一条**。这与边界 b 一致，且是 MiMo 官方明文要求（`:143`）。

2. **认证头两种都认，但 key 分 `sk-` / `tp-` 两类、不能串。**
   → Flutter 端 `api_client` 配置项：`api_key`（自由文本）+ `base_url`；日志脱敏时按 `^(sk|tp)-` 前缀识别。runtime 选 `api-key:` 头即可（示例用的就是它，`:149`），比 `Authorization: Bearer` 更不易和其他服务商冲突。

3. **客户端超时 + 指数退避 + 长响应走流式**，这是官方唯一给的可靠性指南（`:222-225, :228`）。
   → `llm_worker` 的 HTTP 层：设连接/读超时（原文未给数值，**推断**：读超时对流式首字节和对非流式总时长应分开）；重试只做指数退避，不要硬编码次数；工具链里 SDR 扫描这类长任务，runtime 应默认 `stream:true`。

4. **Anthropic 协议的 system 是独立参数，不是 messages[0]。**
   若未来 MBDSDR 走 MiMo 的 Anthropic 兼容端点（`/anthropic/v1/messages`，`:139`），消息组装层要分叉：OpenAI 协议把 system 塞进 messages 数组，Anthropic 协议要提到顶层 `system` 字段。不要用同一套 messages 构造函数硬套两边。

### 2.2 与第一服务商（硅基流动）的关键差异——MiMo 特有的"反直觉"点

| 差异 | 原文依据 | 对 runtime 的含义 |
|---|---|---|
| **MiMo 官方建议"调工具时关掉 thinking"**，否则会出现 `tool_calls` 混进 `reasoning_content` 的不稳定输出（`:202`） | `:200-202` | 这与硅基流动"必须保留 reasoning_content"是**两个方向**。MBDSDR 的 runtime 应做成**按服务商/按模型可配**：MiMo 工具调用会话默认关 thinking（或按 Model Hyperparameters 页调），硅基流动默认开并逐字保留。**不能写死一种策略** |
| **本 FAQ 没有任何"arguments 非法 JSON / 幻觉参数"警告** | §1.8 | 但边界 c 的 runtime 防护（schema 校验 + 参数白名单 + 禁 eval）**仍必须做**——这是通用安全红线，只是不能把"MiMo 官方这么警告"当论据。实现照做，引用别挂错地方 |
| **本 FAQ 没有 tool_choice 强制 auto 的明文** | `:195` 仅示例值 | runtime 发请求时可以照 OpenAI 标准填 `tool_choice:"auto"`，但**别假设"指定工具"一定被支持**——发了也可能被后端忽略。要做"指定工具失败后自动回退 auto"的容错 |
| **本 FAQ 没有 Anthropic tool_use/tool_result 块示例** | `:139` 仅端点 | MiMo 的 Anthropic 兼容层工具调用长什么样，本 FAQ 答不了。**第二阶段若真走 Anthropic 协议接 MiMo，必须再去读其 Tool Calling 子页**（`:44` 外链），不能凭 OpenAI 格式猜 |
| **内容审核会静默拦截**，未给错误码 | `:232` | runtime 要预期"HTTP 200 但 content 为空 / 被替换"的情况，不能只靠 HTTP 状态码判失败；记录并上报被拦截事件，但不假定具体返回体 |

### 2.3 不需要从本 FAQ 借鉴的

- 多工具共享配额（`:131`）、支持哪些编程客户端（`:127`）、在线搜索 `forced_search`（`:242`）、Batch JSONL 上传（`:253`）——与 MBDSDR 的 SDR 工具循环无关，记录但不实现。

---

## 3. 与能力边界映射

| 边界 | 文档依据（本 FAQ） | 实现要点 |
|---|---|---|
| **a) function calling = OpenAI 标准循环** | 请求侧有证据：`tools=[{type:function,function:{name,description,parameters}}]` + `tool_choice:"auto"`（`:167-195`）；**响应侧（tool_calls 结构 / role=tool 回传 / finish_reason）本 FAQ 未展示** | runtime 按 OpenAI 兼容协议实现循环即可；响应解析仍要按 OpenAI 标准 `message.tool_calls[].function.{name,arguments}`，但本 FAQ 只是"请求侧样例"，响应契约需以 OpenAI 范式 + 实测为准（推断） |
| **b) interleaved thinking：reasoning_content 原样保留回传** | **强证据**：模型返回 `reasoning_content` alongside `tool_calls`，且官方建议把历史 reasoning_content 全部留在 messages（`:143`）；示例把它放 assistant 消息里（`:156`） | runtime 必须把每段 reasoning_content 原样挂回 assistant 历史；流式侧 chunk 字段名本 FAQ 未给，需按 OpenAI 兼容 `delta.reasoning_content` 处理（推断） |
| **c) arguments 不保证合法 JSON / 虚构 schema 外参数** | **本 FAQ 无证据**（§1.8） | runtime 仍做 schema 校验 + 参数白名单 + 禁 eval（通用红线），但**不要在文档引用里写"MiMo FAQ 警告过"**——那句警告不在这份文档 |
| **d) tool_choice 后端强制 auto** | **证据不足**：仅示例值 `"auto"`（`:195`），无"强制"字样 | 请求可填 auto；不要依赖"指定工具"必生效，做 auto 回退 |
| **e) thinking + 工具调用不稳定** | **强证据**：`tool_calls` 混入 reasoning_content = 不稳定信号，官方建议调工具时关 thinking（`:202`） | runtime 按服务商配置 thinking 开关；MiMo 工具会话倾向关 thinking，与 b 的"保留 reasoning"策略并存而非冲突（不同模型不同默认值） |
| **f) Anthropic 兼容协议** | **部分证据**：端点 `/anthropic/v1/messages`、system 独立参数（`:139`）、双 base_url（`:123`）；**无 tool_use/tool_result 块、无 disable_parallel_tool_use** | 双协议抽象层要预留 system 独立参数；Anthropic 工具块 schema 待补读 Tool Calling 子页后再定 |

---

## 4. 红线与自检记录

| 自检项（规范 §6 / §2.6） | 结果 | 说明 |
|---|---|---|
| 转换文本可打开、行数核对 | ✅ | `sources/mimo-faq.txt` 共 303 行，`wc -l` 复核一致 |
| 引用的每个行号都在原文找到 | ✅ | 全部 `:NNN` 引用来自 `Read` 全文 + grep 复核 |
| 覆盖任务指定范围（接入前置 / function calling / reasoning_content / 流式超时错误限流 / 官方示例 / 警告 / 逐条编号） | ✅ | 分别落在 §1.2-1.3 / §1.4-1.5 / §1.6 / §1.7 / §1.5 / §1.8 / §1.4 |
| 无编造 | ✅ | 本 FAQ **没有**的内容（arguments 警告、tool_choice 强制、disable_parallel_tool_use、错误码、限流数值、Anthropic 工具块）一律在 §1.8 明写"没有"，未补写 |
| 「推断」处已标注 | ✅ | 见 §2.1（超时数值）、§2.2（流式 chunk 字段、Anthropic 工具块待补）、§3.a（响应契约按 OpenAI 范式） |
| 未改代码 / 未做 git / 未调 API / 未抓无关页面 | ✅ | 只写 `docs/learn/model-tool-calling/` 下的 txt 与 md；转换脚本在 agent workspace，未入仓库目录 |
| 篇幅 | ✅ | 本文件约 190 行，落在 150–500 区间 |

**一句话总结**：这份 MiMo FAQ 的"真材实料"集中在三点——(1) 双 key（sk-/tp-）+ 双 base_url（OpenAI `/v1/chat/completions` / Anthropic `/anthropic/v1/messages`，system 独立）；(2) 思考模式多轮工具调用必须把历史 `reasoning_content` 原样带回 messages；(3) thinking 开着调工具会让 `tool_calls` 混进 reasoning_content，官方建议工具会话关 thinking。而规范预设的"arguments 非法 JSON 警告 / tool_choice 强制 auto / Anthropic 工具块与并行开关 / 错误码限流"，**这份 FAQ 里都没有**——MBDSDR runtime 该防的仍要防，但引用别挂错文档。
