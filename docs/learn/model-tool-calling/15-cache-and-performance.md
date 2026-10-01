# 上下文缓存与性能实践 学习笔记

> **文档来源**（本地绝对路径，均已全文精读）：
> - `/home/user/Doubao/chats/38438160041798146/docs/learn/model-tool-calling/sources/sf-chat-completions-api.txt`（硅基流动 OpenAI 兼容 Chat Completions API 参考，545 行）
> - `/home/user/Doubao/chats/38438160041798146/docs/learn/model-tool-calling/sources/sf-stream-mode.txt`（硅基流动《流式输出》指南，172 行）
> - `/home/user/Doubao/chats/38438160041798146/docs/learn/model-tool-calling/sources/sf-interleaved-thinking.txt`（硅基流动《Interleaved Thinking》指南英文站全文转录，241 行）
> - `/home/user/Doubao/chats/38438160041798146/docs/learn/model-tool-calling/sources/sf-cache-extras.txt`（硅基流动官方博客《Prompt Caching on SiliconFlow》在线抓取全文摘录，本次补抓落盘）
> - `/home/user/Doubao/chats/38438160041798146/docs/learn/model-tool-calling/sources/mimo-faq.txt`（小米 MiMo FAQ「API Integration」页，303 行）
> - `/home/user/Doubao/chats/38438160041798146/docs/learn/model-tool-calling/sources/mimo-quickstart.txt`（小米 MiMo《首次调用 API》，201 行）
>
> **精读方式**：本地纯文本逐节精读（_SPEC §6 全文读完）；SF 缓存博客为 `web.fetch` 在线抓取（offset=0 与 offset=7996 两段续读，end_offset=10359=total_length，已读完整）后落盘 `sources/sf-cache-extras.txt`。
> **精读日期**：2026-10-01　**作者**：MainAgent（批次学习子任务）
> **覆盖边界**：重点 **b（Interleaved Thinking 与缓存）+ 性能侧**（缓存命中/计费、限流 429/50505、超时重试、流式、max_tokens 预算）；顺带交叉 a（工具循环结构）作为成本估算的上下文。
> **标注约定**：「设计」= 对 MBDSDR runtime 的工程建议，非原文；「推断」= 原文无直接证据、基于已引原文的合理外推；数值仅在原文给出时照录，否则如实标注「原文未给」。

---

## 1. 原文事实清单

### 1.1 缓存机制：硅基流动 usage 字段（逐字）

硅基流动 `usage` 对象的完整示例（`sources/sf-chat-completions-api.txt:491-503`）：

```json
"usage": {
  "prompt_tokens": 15,
  "completion_tokens": 1540,
  "total_tokens": 1555,
  "completion_tokens_details": { "reasoning_tokens": 1190 },
  "prompt_tokens_details": { "cached_tokens": 0 },
  "prompt_cache_hit_tokens": 0,
  "prompt_cache_miss_tokens": 15
}
```

| 事实 | 出处 | 要点 |
|---|---|---|
| `prompt_cache_hit_tokens` 字段存在，示例值 `0` | `sf-chat-completions-api.txt:501` | 硅基特有：本次请求中命中上下文缓存的输入 token 数 |
| `prompt_cache_miss_tokens` 字段存在，示例值 `15` | `sf-chat-completions-api.txt:502` | 本次请求未命中缓存、按标准输入价计费的 token 数 |
| OpenAI 兼容字段 `prompt_tokens_details.cached_tokens` 同时存在，示例值 `0` | `sf-chat-completions-api.txt:498-500` | 两套字段并存；`prompt_cache_hit/miss_tokens` 是硅基扩展 |
| `completion_tokens_details.reasoning_tokens`（示例 1190）单独记账 | `sf-chat-completions-api.txt:495-497` | 思维链 token 计入 completion，不计入 prompt；成本分析时要分开看 |

> 注：上述三个缓存字段的**字段名即语义**，原文未逐字定义「hit = 按缓存读价计费、miss = 按标准输入价计费」；该计费对应关系由硅基缓存博客印证（见 1.2）。

### 1.2 缓存是否自动生效 / TTL / 计费单价

| 事实 | 出处 | 要点 |
|---|---|---|
| 缓存对「符合条件的重复输入」自动生效，但**不保证命中** | `sf-cache-extras.txt §Standard Input`（"Repeated text does not automatically receive cached-input pricing…qualifies for and uses the available cache"）；`Q2`（"Repeated text does not guarantee a cache hit. Results depend on the model, prompt structure, prefix stability, and cache eligibility."） | 无客户端开关字段；命中由模型、prompt 结构、前缀稳定性、资格条件自动决定 |
| 命中的是「输入前缀处理过程」，不是「上一次回答」 | `sf-cache-extras.txt §What Is Prompt Caching?` | 模型仍为每次请求生成全新响应；只复用输入侧的 KV 处理 |
| 输出 token **永远不**享受缓存价 | `sf-cache-extras.txt Q3` | 长响应工作流的账单降幅会明显小于输入命中率 |
| GLM-5.2 Serverless 示例价（2026-07-15，每 1M token）：标准输入 $1.302 / 缓存输入 $0.26 / 输出 $4.092 | `sf-cache-extras.txt §Standard Input…GLM-5.2` | 缓存输入约为标准输入的 20%（约 8 折折扣）；价格随时间变，预算前需查模型页 |
| 缓存 TTL（存活时间） | **原文未给** | `sf-cache-extras.txt §全文未提及` 明确标注：本页无 TTL 数字；SF API 参考页（sf-chat-completions-api.txt 全文）亦无 TTL 字段。**不得编造 TTL** |
| 真实命中率参考：OpenRouter 快照 2026-08-25 GLM-5.2 硅基 91.8%；2026-07-30 Kimi K2.7 Code 93.2% | `sf-cache-extras.txt §Why Cache Hit Rate Matters` / `Q2` | 第三方快照、动态变化，仅作量级参照 |
| MiMo 是否提供上下文缓存字段/TTL/单价 | **两家原文均未给** | `mimo-faq.txt` 与 `mimo-quickstart.txt` 全文无 `cache` 计费字段、无缓存 TTL、无缓存价表；MiMo 原文唯一提到的「cache」是**联网搜索开关的 5 分钟缓存**（`mimo-faq.txt:239`："There is a 5-minute cache period after enabling / disabling online search"），与上下文前缀缓存无关，不可混用 |

### 1.3 reasoning_content 与缓存的关系（边界 b 的性能后果）

| 事实 | 出处 | 要点 |
|---|---|---|
| 必须**逐字原样**保留并回传 `reasoning_content`（含工具调用前、多次工具调用之间、工具结果之后所有片段） | `sf-interleaved-thinking.txt:83-84`（✅ must preserve…exactly as received）、`:94-100`、`:121-126` | 流式 delta 与非流式正文遵守同一规则（`:125-126`） |
| 丢弃/修改/合并/重排 reasoning_content 的后果之一：**"Reduced cache efficiency and degraded output quality"（缓存效率下降、输出质量劣化）** | `sf-interleaved-thinking.txt:109-112`（❌ Do NOT 列表后的 If you do, you may see 三条后果：Broken multi-step behavior / Instability across tool calls / **Reduced cache efficiency**） | 这是「丢弃 reasoning_content → 缓存劣化」的原文直接证据 |
| MiMo 同样要求后续请求保留全部历史 `reasoning_content` 以获得最佳表现 | `mimo-faq.txt:143`（"keep all previous reasoning_content in the messages array…to achieve the best performance"）；`mimo-quickstart.txt:139` 同义 | 两家在「逐字保留」上完全一致 |

**机理解释（推断，基于上述原文）**：前缀缓存按「请求前缀逐字节匹配」命中（`sf-cache-extras.txt §How to Structure Prompts`："Frequent changes to the beginning of a request can reduce the amount of reusable context"；`Q2`：命中取决于 prefix stability）。多步工具循环中，第 N 轮请求 = system + tools + 第 1..N-1 轮完整对话（含 assistant 的 reasoning_content 片段）+ 本轮新输入。若 runtime 把某轮 assistant 消息里的 `reasoning_content` 丢掉，该 assistant 消息体与上一轮实际发出的前缀**不再逐字相等**，从该位置起全部前缀失配 → 后续轮次整段算 miss（`prompt_cache_miss_tokens` 上涨），只有更早的 system+tools 前缀仍命中。这就是「丢弃 reasoning_content → 缓存效率下降」的因果链。

### 1.4 限流与错误处理

| 事实 | 出处 | 要点 |
|---|---|---|
| SF 限流错误：HTTP 429，body `{"message": "Request was rejected due to rate limiting. If you want more, please contact contact@siliconflow.cn. Details:TPM limit reached.", "data": "string"}` | `sf-chat-completions-api.txt:530-533` | **TPM（tokens per minute）限流**；原文未给具体 TPM 数值（按账户/模型档位变化） |
| SF 过载错误：HTTP 503，body `{"code": 50505, "message": "Model service overloaded. Please try again later.", "data": "string"}` | `sf-chat-completions-api.txt:536-540` | code 50505 = 模型服务过载，建议稍后重试（即退避重试） |
| SF 其他状态码骨架：400 / 401("Invalid token") / 403("Forbidden") / 404("404 page not found") / 504 | `sf-chat-completions-api.txt:351-363, 521-527, 543` | 原文只给了骨架与一句话说明，**无完整错误码表**；400 的示例 body 是 `{"code": 20012, "message": "string"}`（`:514-518`） |
| MiMo 客户端超时三招（原文三条原文照录） | `mimo-faq.txt:217-228`（§How to handle timeouts?） | ① "Set reasonable connection and read timeout times"（合理的连接与读超时）；② "Use exponential backoff for retries"（重试用指数退避）；③ "For long responses, it's recommended to use streaming mode"（长响应用流式） |
| MiMo 响应速度取决于三因素 | `mimo-faq.txt:204-215`（§What's the response speed?） | 请求长度与复杂度 / 服务器负载与地理位置 / 是否用流式 |
| MiMo 是否提供错误码表、限流数值 | **原文未给** | `mimo-faq.txt`、`mimo-quickstart.txt` 全文无 429/503 错误码、无 TPM/RPM 数值；限流页链接 `/docs/en-US/api/guidance/rate-limit` 仅出现在导航（`mimo-faq.txt:24`），本次未抓取，**不编造数值** |

### 1.5 流式与 max_tokens 的性能侧事实

| 事实 | 出处 | 要点 |
|---|---|---|
| `stream=true` 时 token 以 SSE 返回，以 `data: [DONE]` 结束 | `sf-chat-completions-api.txt:49-53` | OpenAI 兼容标准 |
| 用 `requests` 手写流式时，**payload 的 `stream:true` 与 request 的 `stream=True` 两处都要设**，否则不会按流返回 | `sf-stream-mode.txt:102-104` | 常见踩坑点 |
| `curl` 看流式要加 `-N/--no-buffer`，否则 curl 缓冲导致看不到逐块输出 | `sf-stream-mode.txt:153` | 终端观测用 |
| 流式累加 `delta.reasoning_content` / `delta.content` / `delta.tool_calls`，与非流式同样要逐字保留 | `sf-stream-mode.txt:84-98`；`sf-interleaved-thinking.txt:114-126` | 流式只改变读取方式，不改变保留规则 |
| `max_tokens` = 生成上限，**不含思维链部分**；原文建议「不要把 max_tokens 设到窗口上限，为输入和系统开销预留约 10k token 缓冲区」 | `sf-chat-completions-api.txt:55-57`、`:195-197` | 长上下文 + thinking 模型尤其要留 buffer，否则生成被截断 |
| `thinking_budget` 范围 128–32768，控制思维链最大 token | `sf-chat-completions-api.txt:65-69` | thinking token 也是成本（1.1 节 reasoning_tokens 1190 的来源），预算要含它 |

---

## 2. 对 MBDSDR 工具化实现的启示

> 背景：MBDSDR runtime 每轮 = system prompt（无线电能力说明）+ 工具 Schema（SDR 函数清单）+ 历史消息（含每轮 assistant 的 reasoning_content）+ 本轮 tool 结果。这是典型的「大稳定前缀 + 每轮追加」结构，正是硅基缓存博客点名的收益最大场景（`sf-cache-extras.txt §Where It Saves Most`：chatbot 与 coding agent 两类）。

### 2.1 缓存友好的请求组装（设计）

按硅基原文推荐的前缀顺序（`sf-cache-extras.txt §How to Structure Prompts`），MBDSDR 每轮 messages 组装顺序固定为：

| 位置 | 内容 | 缓存属性 |
|---|---|---|
| 1 | system 指令（角色/无线电任务规则/输出格式） | 全程不变 → 应命中 |
| 2 | tools Schema 数组（function 清单） | 工具集不变则全程不变 → 应命中；**运行期禁止动态改 Schema 字段/空格/顺序**（原文：avoid changing tool definitions / message order / whitespace and generated metadata） |
| 3 | 历史 assistant 消息（content + **逐字 reasoning_content** + tool_calls） | 逐字保留即前缀稳定（边界 b 红线，`sf-interleaved-thinking.txt:109-112`） |
| 4 | 历史 tool 结果（role=tool） | 追加式，前缀位置固定 |
| 5 | 本轮新 user 输入 / 实时数据（当前频率、采样率、硬件状态快照） | 易变内容放最后，不污染前面的稳定前缀 |

**具体组件建议**：
- `llm_client`/消息组装器把 system + tools 做成**启动期一次构建、运行期只读的固定前缀对象**，每轮请求直接 `messages = [prefix_system, prefix_tools...] + history + new_turn`，禁止每轮重新序列化（重排 key、变空格都会破前缀——`sf-cache-extras.txt` 原文点名 whitespace/metadata 是缓存杀手）。
- `reasoning_content` 在消息持久化层（conversation store）按「原始字符串字段」存储与回放，不做 trim、不做编码转义改写（`sf-interleaved-thinking.txt:103-108` 禁止列表）。
- 动态信息（当前 UTC 时间、设备实时状态）**不要**塞进 system；放最后一条 user/tool 消息。注意 `mimo-quickstart.txt:37-45` 的示例 system prompt 里硬编码了日期占位符——MBDSDR 若照抄会让 system 每天变一次、破前缀（推断：应把日期动态化放到最后一条消息）。

### 2.2 每轮 hit/miss 成本估算（设计，公式来自 `sf-cache-extras.txt §How to Estimate Savings`）

设一轮工具循环共 N 轮请求；每轮固定前缀（system+tools+全部历史）= R token，本轮新增（新 tool 结果/新 user）= U token，每轮输出（含 reasoning_tokens）= O token；Pi/Pc/Po 为模型三层单价（SF：GLM-5.2 示例 $1.302/$0.26/$4.092 每 1M）。

- 第 1 轮全价；第 2..N 轮若前缀逐字稳定，R 部分按 Pc 命中、U 按 Pi 计费、O 按 Po 计费（输出永不缓存）。
- runtime 应在响应后读取 `usage.prompt_cache_hit_tokens / prompt_cache_miss_tokens`（SF）并打点：
  - `hit_ratio = hit / (hit + miss)`；连续多轮 hit_ratio 掉下去 → 说明某轮消息被改写/前缀顺序被破坏，告警排查。
  - 单轮成本 ≈ `miss_tokens × Pi + hit_tokens × Pc + completion_tokens × Po`；把 reasoning_tokens 单独列成本表（`sf-chat-completions-api.txt:495-497`）。
- 对照基线：若 runtime 曾经丢 reasoning_content（旧实现），改造前后对比 hit_tokens，即可量化「缓存效率下降」的实际账单损失（原文只给了后果方向，数值要自己测——推断）。

### 2.3 超时 / 重试 / 流式配置（设计，数值为建议起点，需按实测调）

| 配置项 | 建议值/策略 | 原文依据 |
|---|---|---|
| 连接超时 | 10 s（合理连接超时，具体数值原文未给，此处为设计起点） | `mimo-faq.txt:222` "reasonable connection timeout" |
| 读超时 | 非流式 120 s；**走流式后读超时改为「chunk 间隔超时」60 s**（相邻两 chunk 超过 60 s 才算卡死），避免整请求 120 s 硬等 | `mimo-faq.txt:222` read timeout；`:228` 长响应用流式 |
| 重试触发 | HTTP 429（TPM 限流）与 503 code=50505（过载）→ 重试；400/401/403/404 **不重试**（请求本身错） | `sf-chat-completions-api.txt:530-540` |
| 退避策略 | 指数退避：基数 1 s → 2 s → 4 s → 8 s，封顶 30 s，最多重试 3 次；429 可额外乘以当前分钟窗口剩余比例（推断，原文只说 exponential backoff） | `mimo-faq.txt:225` |
| 最大工具轮数 | 建议 12 轮封顶（设计值）：每多一轮都是一次完整 LLM 调用成本；原文未给轮数上限 | 成本公式推论（`sf-cache-extras.txt §Why Cache Read Pricing Matters`：额外 Agent 步骤/工具调用响应都计费） |
| max_tokens 预算 | 输出上限 = 模型窗口 − 已占用输入 − **10k 缓冲区**；thinking 模型再扣 thinking_budget（128–32768） | `sf-chat-completions-api.txt:55-57, 65-69` |

### 2.4 tokens.h / 配置文件应抽出的性能常量（设计）

在 `src/core/tokens.h`（或等价配置中心）集中定义，禁止散落魔法数：

```cpp
// 性能常量（设计；数值为建议起点，首次上线后按 usage 打点回调校准）
struct LlmPerfConfig {
    // 超时
    int connect_timeout_sec        = 10;
    int read_timeout_sec           = 120;   // 非流式
    int stream_idle_chunk_sec       = 60;     // 流式相邻 chunk 间隔
    // 重试
    int retry_max_attempts         = 3;
    double retry_backoff_base_sec   = 1.0;   // 指数基数
    double retry_backoff_cap_sec   = 30.0;
    // 重试白名单：HTTP 429、503 + body.code==50505（SF）
    // 运行预算
    int    max_tool_rounds         = 12;
    int    headroom_tokens         = 10000; // SF 原文建议预留
    // 缓存观测
    bool   enable_cache_hit_logging = true; // 读 usage.prompt_cache_hit/miss_tokens 打点
};
```

- `llm_worker`：统一出口，所有重试/超时在此收口；tool 执行本身不重试（工具幂等性由工具层保证，推断）。
- `task_orchestrator`：负责 max_tool_rounds 与「round 1 全价、后续轮看命中率」的成本预算硬停。
- Flutter 端 `api_client`：只展示流式增量，不参与重试决策（保持薄客户端，设计）。

---

## 3. 与能力边界映射

| 边界 | 文档依据 | 实现要点（性能视角） |
|---|---|---|
| b) Interleaved Thinking：逐字保留 reasoning_content | `sf-interleaved-thinking.txt:83-112`；`mimo-faq.txt:143`；`mimo-quickstart.txt:139` | 不只是「正确性红线」，也是**缓存红线**：丢弃 = 前缀失配 = miss 上涨 + 输出质量劣化（`sf-interleaved-thinking.txt:112`）。conversation store 必须按原字段保真 |
| a) 工具循环结构（成本侧） | `sf-chat-completions-api.txt:448-461`（messages 里 system/user/assistant/tool 回传示例）；`sf-cache-extras.txt §Where It Saves Most` | 循环每轮都重发完整前缀 → 前缀稳定性就是成本；max_tool_rounds 直接乘 N 次请求成本 |
| 缓存命中观测 | `sf-chat-completions-api.txt:501-502`（hit/miss 字段）；`sf-cache-extras.txt Q2`（命中不保证） | runtime 必须消费 usage 字段做命中率打点，命中率低时回头查「哪一轮消息变了」 |
| 限流/过载恢复 | `sf-chat-completions-api.txt:530-540`；`mimo-faq.txt:217-228` | 429 TPM 与 503/50505 走指数退避；错误请求不重试；长响应走流式降感知延迟 |
| 输出预算 | `sf-chat-completions-api.txt:55-69`（max_tokens 留 10k、thinking_budget 128–32768） | 窗口预算 = 输入 + 10k buffer + thinking_budget + max_tokens，四维一起算 |
| MiMo 侧缓存 | **原文未提供**（`mimo-faq.txt`/`mimo-quickstart.txt` 无缓存计费字段；唯一 cache 是联网搜索 5 分钟开关缓存，`:239`） | MBDSDR 接 MiMo 时不做命中率假设；若未来 MiMo 上线缓存字段，按 SF 同一套打点逻辑扩展（推断） |

---

## 4. 红线与自检记录

逐条对照 _SPEC §6 与本笔记边界：

- ✅ 六个本地 sources 文件全部逐节读完（行数已核：545/172/241/303/201 + 本次补抓 sf-cache-extras.txt 全文两段续读 end_offset=10359=total_length）。
- ✅ 引用的每条事实都能在原文定位：行号级引用 `sources/*.txt:<行>`；缓存博客引用其 § 小节（已落盘 sources/sf-cache-extras.txt，复核者可直接对照）。
- ✅ 覆盖任务指定 6 个重点：①缓存字段逐字（§1.1）②TTL/自动生效/单价（§1.2，缺项如实标注）③reasoning_content→缓存机理（§1.3）④限流 429/50505 + MiMo 三招（§1.4）⑤最佳实践表（§1.5 + §2.3）⑥runtime 常量与组件建议（§2.4）。
- ✅ 无编造：SF 缓存 TTL、MiMo 错误码表/限流数值、MiMo 缓存单价均标注「原文未给」；GLM-5.2 价格注明日期与「价格随时间变」；第三方命中率注明是 OpenRouter 快照。
- ✅ 「推断」已标注：前缀失配机理（§1.3）、日期动态化建议（§2.1）、重试基数/轮数/超时具体数值（§2.3–2.4 为设计起点，非原文）、工具执行不重试（§2.4）。
- ✅ 「设计」已标注：§2 全节工程建议。
- ✅ 未做任何需要 API key 的动作；未改代码、未做 git；除 SF 缓存博客页外未抓无关页面。
- ⚠️ 已知缺口（如实声明）：SF 限流的具体 TPM 数值、缓存 TTL、MiMo 限流页（`/docs/en-US/api/guidance/rate-limit`）本次未抓，均不在笔记中给数。
