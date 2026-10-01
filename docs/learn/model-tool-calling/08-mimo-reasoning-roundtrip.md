# MiMo「回传 reasoning_content」（Passing Back Reasoning Content）学习笔记

> 文档来源：
> - 在线官方页 `https://mimo.mi.com/docs/usage-guide/passing-back-reasoning_content`（页面标题「深度思考」，2026-10-01 web.fetch pagination 全文抓取，total_length=4581 已读完整），正文转录落盘 `sources/mimo-reasoning.txt`
> - FAQ「API Integration」页（在线抓取，另一 agent 已转文本）：`sources/mimo-faq.txt`
> - 对照源（硅基流动 Interleaved Thinking）：`sources/sf-interleaved-thinking.txt`
> - 本地壳文件 `repos/model-docs/xiaomi-mimo/usage-guide_passing-back-reasoning_content.html`（13KB）经核查 = SPA 空壳（仅 `<div id="root"></div>` + rspack 引导脚本，title="Xiaomi MiMo Home"，无正文），与同目录另两个 HTML md5 相同（c1000a79650eeee0895cb00ca78619b3），正文不在本地。
>
> 精读方式：web_fetch 在线抓取（pagination 一次读完整）+ 本地壳核查 + FAQ 旁证 + 硅基流动对照。
> 精读日期：2026-10-01　作者：MainAgent 委派的 model-tool-calling 笔记 agent
> 覆盖边界：**b（Interleaved Thinking 逐字保留回传）、e（思考模式+工具调用稳定性警告）为主**；兼及 a（工具循环骨架）、c/d（本页未直接涉及，标注「本页未覆盖」）。

---

## 0. 一句话定位

MiMo 官方把"回传 reasoning_content"写成一条**硬性 API 红线**：开启深度思考且历史里出现过工具调用时，后续每一轮带 `tool_calls` 的 assistant 消息**必须把 `reasoning_content` 字段原样带回**，否则 API 直接返回 **400**；即便不报错，缺失也会让"指令遵循下降、幻觉增多"。这正是 MBDSDR runtime "实验室绿真机红"的高发点——本地 mock 不校验 reasoning_content，真机 MiMo 直接 400。

---

## 1. 原文事实清单

### 1.1 reasoning_content 是什么、挂在哪、何时出现

| 事实 | 出处 | 要点 |
|---|---|---|
| `reasoning_content` 是与 `content`、`tool_calls` 同级、挂在 `message` 上的字段 | `sources/mimo-reasoning.txt §开启思考（响应示例）` | 开启思考时 message 形如 `{role:"assistant", content:"...", tool_calls:null, reasoning_content:"The user wants..."}` |
| 关闭思考时该字段**不出现** | `sources/mimo-reasoning.txt §关闭思考` | 关闭后 message 无 `reasoning_content`，且 `usage.completion_tokens_details.reasoning_tokens=0` |
| 思考 token 单独计量 | `sources/mimo-reasoning.txt §开启思考（响应示例）` | `completion_tokens_details.reasoning_tokens=14`；缓存命中见 `prompt_tokens_details.cached_tokens` |
| 思考开关请求参数 = `thinking.type`，**非 OpenAI 标准字段**，须走 `extra_body` | `sources/mimo-reasoning.txt §请求参数`、`§调用示例` | `extra_body={"thinking":{"type":"enabled"|"disabled"}}`；默认对 v2.6/v2.5 全系**开启** |
| 深度思考下 `temperature`/`top_p` 被强制覆写为 1.0 / 0.95 | `sources/mimo-reasoning.txt §注意事项/参数限制` | 传了也不生效 |
| `max_completion_tokens` 限制的是"思考+回答"总长度 | `sources/mimo-reasoning.txt §注意事项/其他说明` | 思考过长会挤压最终回答，建议给足 |

### 1.2 回传要求（核心红线，逐字）

| 事实 | 出处 | 原文措辞 / 要点 |
|---|---|---|
| **必须完整回传 reasoning_content，否则 API 返回 400** | `sources/mimo-reasoning.txt §注意事项/多轮对话回传要求` | 原文："在 Agent 类产品的多轮会话中开启深度思考，且历史会话中存在工具调用时，后续所有 user 交互轮次中回传的 assistant 如果包含了工具调用，**必须完整回传 `reasoning_content` 字段，否则 API 将返回 400 错误**。" |
| 触发条件三要素 | 同上 | ①开着深度思考 ②历史会话里出现过工具调用 ③后续某 user 轮回传的 assistant **带 tool_calls**——三者同时成立即触发强制回传 |
| 缺失的软性后果（即使没触发 400） | 同上 | 原文："历史 `reasoning_content` 一旦缺失，模型上下文将不完整，可能表现出**指令遵循下降，幻觉增多**等现象。" |
| 受影响 Agent 产品清单 | 同上（表格） | OpenAI 兼容协议：TRAE、Cursor、Roo Code、Codex、GitHub Copilot CLI、Zed、AutoGen、Goose；Anthropic 兼容协议另含 OpenClaw、OpenCode、Kilo Code |
| FAQ 侧同口径表述 | `sources/mimo-faq.txt:141-143` | 原文："the model returns a `reasoning_content` field alongside `tool_calls`. To continue the conversation, it is **recommended to keep all previous `reasoning_content` in the `messages` array** for each subsequent request to achieve the best performance."（FAQ 语气偏 "recommended"，主文档语气已升级为 "必须/否则 400"——以主文档为准） |

### 1.3 回传格式：它就是 role:"assistant" 消息上的一个字段

FAQ 给了一个最直白的 curl 请求体片段（`sources/mimo-faq.txt:152-161`），证明回传方式 = **把 reasoning_content 作为 assistant 消息的同级字段塞回 messages 数组**：

```jsonc
"messages": [
  {
    "role": "assistant",
    "content": "Hello! I am MiMo.",
    "reasoning_content": "Okay, the user just asked me to introduce myself. ... I should think about why they are asking this."
  },
  { "role": "user", "content": "What is the weather like in Hebei?" }
]
```

要点（`sources/mimo-faq.txt:153-157`）：
- 字段名就叫 `reasoning_content`，不是 `thinking`、不是 `reasoning`；
- 角色仍是 `role:"assistant"`，**不是**新开一个 role；
- 它和 `content`、`tool_calls` 并列，原样带回；
- 该 curl 同时带 `"tool_choice": "auto"`（`:195`）。

### 1.4 工具调用循环中 reasoning_content 与 tool 消息的顺序组合（官方示例逐字段）

官方「思考模式下的多轮工具调用」示例（`sources/mimo-reasoning.txt §思考模式下的多轮工具调用`）的消息序列如下，这是本笔记最关键的一张表：

| 步骤 | messages 末尾追加的消息 | reasoning_content 状态 | 出处 |
|---|---|---|---|
| Turn1 用户提问 | `{role:"user", content:"北京天气+现在几点"}` | — | §示例 |
| Request 1-1 模型决定并行调两工具 | `{role:"assistant", content:"", reasoning_content:"...call both in parallel...", tool_calls:[get_current_weather(Beijing), get_time(Asia/Shanghai)]}` | **该 assistant 带 reasoning_content** | §示例输出 Request 1-1 |
| 执行两工具后 | `{role:"tool", tool_call_id:"call_01e4...", content:"Sunny 25°C"}` | — | §示例 |
|  | `{role:"tool", tool_call_id:"call_f2bf...", content:"2026-09-22 00:38:45 (Asia/Shanghai)"}` | — | §示例 |
| Request 1-2 模型结合工具结果出最终回答 | `{role:"assistant", content:"Here's ... Beijing ...", reasoning_content:"Present both results clearly.", tool_calls:null}` | **该 assistant 又带一段新的 reasoning_content** | §示例输出 Request 1-2 |
| Turn2 用户追问上海 | `{role:"user", content:"上海呢？比北京热还是冷？"}` | — | §示例 |
| Request 2-1 模型再次调工具 | `{role:"assistant", content:"", reasoning_content:"Need weather ... Shanghai ... compare to Beijing's 25°C ...", tool_calls:[get_current_weather(Shanghai), get_time(...)]}` | 历史 Turn1 的全部 reasoning_content **仍在 messages 里**（经 `messages.append(assistant_message)` 累积） | §示例 + Turn2 注释原文 |
| Request 2-2 最终对比回答 | `{role:"assistant", content:"...Shanghai is colder...", reasoning_content:"Shanghai: cloudy, 22°C vs Beijing sunny 25°C...", tool_calls:null}` | — | §示例输出 Request 2-2 |

循环骨架（官方 `run_turn` 原样逻辑，§示例）：
```
while True:
    resp = create(messages, tools, extra_body={"thinking":{"type":"enabled"}})
    assistant_message = resp.choices[0].message
    messages.append(assistant_message)      # ← 整条原样 append（含 reasoning_content）
    if not assistant_message.tool_calls: break
    for tc in assistant_message.tool_calls:
        result = TOOL_MAP[tc.function.name](**json.loads(tc.function.arguments))
        messages.append({role:"tool", tool_call_id: tc.id, content: result})
```

**顺序组合结论**：
1. 每个带 tool_calls 的 assistant 消息 = `{role:"assistant", content, reasoning_content, tool_calls}` 四件套；
2. 其后紧跟 N 条 `{role:"tool", tool_call_id, content}`（与 tool_calls 一一对应）；
3. 工具结果之后模型**还会再产出新的 reasoning_content**（见 Request 1-2 / 2-2），这段也必须保留；
4. 跨 user 轮次（Turn1→Turn2）历史 reasoning_content 全程不裁剪——官方注释原文："reasoning_content from Turn 1 is already in messages via assistant_message"。

### 1.5 流式场景的 handling

| 事实 | 出处 | 要点 |
|---|---|---|
| 流式时思考与回答**分段先后**输出 | `sources/mimo-reasoning.txt §流式响应（开启思考）` | 原文："首先通过 `reasoning_content` 逐步返回思考过程，思考完成后，再通过 `content` 逐步输出最终回答。" |
| 流式字段 = `delta.reasoning_content` | 同上（chunk 示例） | chunk.choices[0].delta 形如 `{"content":null, "role":null, "tool_calls":null, "reasoning_content":"The user is asking"}` |
| 思考阶段：delta.reasoning_content 累加、delta.content=null；切换后：delta.content 累加、delta.reasoning_content=null | 同上 | 两段在时间上不重叠，靠字段名区分 |
| 首 chunk 带 `delta.role="assistant"`；尾 chunk `finish_reason="stop"`；最后一个空 choices chunk 带 usage；以 `data:[DONE]` 结束 | 同上 | 与 OpenAI SSE 惯例一致 |
| FAQ 建议长响应用流式 | `sources/mimo-faq.txt:228` | "For long responses, it's recommended to use streaming mode" |

### 1.6 思考模式 + 工具调用并存的稳定性警告（FAQ 逐字）

| 事实 | 出处 | 原文 |
|---|---|---|
| tool_calls 混进 reasoning_content = **不稳定信号** | `sources/mimo-faq.txt:200-202` | 原文："The appearance of `tool_calls` in the reasoning content indicates **instability and incomplete output** caused by the model having `thinking` enabled when calling `tool`." |
| 官方建议：**调工具时关掉 thinking** | 同上 | 原文："It is recommended to **disable `thinking` when calling `tool` calls** and to adjust the settings according to Model Hyperparameters ... to achieve a more stable and better user experience." |

> 注意：这与 1.2 的"开着思考+工具调用时必须回传 reasoning_content"看似矛盾，实则是**两层建议**——
> 主文档规定"如果你选择开着思考跑工具循环，那么 reasoning_content 必须回传，否则 400"；
> FAQ 则说"更稳的做法是调工具时干脆关掉 thinking"。runtime 实现时两条都要记住。

---

## 2. 对 MBDSDR 工具化实现的启示

> 目标组件（按 `_SPEC.md §2.2` 点名）：`llm_client` / agent 多步循环（对应 `task_orchestrator` / `llm_worker`）、消息缓冲层（对应 `src/core/tokens.h` 或其 C++/Python 等价物）。以下给字段级方案。

### 2.1 runtime 必须持有"完整 assistant 消息"，不能只存 content

当前若 runtime 循环里把模型响应拆成 `(content, tool_calls)` 两个变量、丢弃 `reasoning_content`，真机 MiMo 会直接 400。正确做法：

```python
# 每条 assistant 响应整体入历史，字段级保留
assistant_msg = {
    "role": "assistant",
    "content":      resp.choices[0].message.content or "",
    "reasoning_content": resp.choices[0].message.reasoning_content,  # ← 新增：None 就省略键，有就原样带
    "tool_calls":   [tc.model_dump() for tc in resp.choices[0].message.tool_calls]
                    if resp.choices[0].message.tool_calls else None,
}
messages.append(assistant_msg)
```
- `reasoning_content` 为 `None`（关闭思考时）就**不要写这个键**，与官方"关闭思考响应里根本没这个字段"保持一致（§关闭思考）；
- 有值就**逐字**塞回，不做任何清洗/截断/拼接（对比硅基流动"禁止修改、合并、拆分、重排"，`sf-interleaved-thinking.txt:103-108`）。

### 2.2 多步循环的消息缓冲（字段级方案）

| 循环阶段 | 缓冲追加内容 | reasoning_content 处理 |
|---|---|---|
| 收 assistant（带 tool_calls） | `{role:assistant, content, reasoning_content, tool_calls}` | **整条保留**，含本段 reasoning_content |
| 执行工具 | N 条 `{role:tool, tool_call_id, content}` | 不动历史 |
| 收 assistant（工具结果后、最终回答） | `{role:assistant, content, reasoning_content, tool_calls:null}` | **新一段 reasoning_content 也要保留**（官方 Request 1-2 证明工具结果后模型仍会思考） |
| 跨 user 轮 | 旧轮全部 assistant/tool 消息不裁剪 | 历史 reasoning_content 全程保留 |

### 2.3 流式累加器要分三条流

```python
reasoning_buf, content_buf, tool_calls_buf = "", "", []
for chunk in stream:
    d = chunk.choices[0].delta
    if d.reasoning_content: reasoning_buf += d.reasoning_content   # 思考流
    if d.content:           content_buf   += d.content             # 回答流
    if d.tool_calls:        tool_calls_buf += d.tool_calls         # 工具流
# 收尾后按 2.1 组装：reasoning_content=reasoning_buf（空串则省略键）
```
依据：§流式响应——思考流与回答流在时间上分段、靠字段名区分，混加会污染 reasoning_content。

### 2.4 配置层：thinking 开关要可按场景切换

| 场景 | thinking.type | 理由（出处） |
|---|---|---|
| 纯对话/复杂推理 | `enabled` | 官方默认开启，提升复杂任务准确性（§引言） |
| 工具循环（求稳） | `disabled` | FAQ 建议"调 tool 时关掉 thinking 更稳定"（`mimo-faq.txt:202`） |
| 工具循环（必须开思考） | `enabled` + 严格回传 | 一旦开着思考跑工具，必须按 2.1/2.2 逐字回传，否则 400（§多轮对话回传要求） |

- `thinking` 走 `extra_body={"thinking":{"type":...}}`，不是 messages 顶层参数（§调用示例）；
- 开思考时别再传 `temperature`/`top_p`（被强制覆写为 1.0/0.95，§参数限制）；
- `max_completion_tokens` 给足——它是"思考+回答"总账（§其他说明）。

### 2.5 "实验室绿真机红"的根因与防护

| 根因 | 真机表现 | 防护 |
|---|---|---|
| 本地 mock/硅基流动不校验 reasoning_content，MiMo 校验 | 首个带 tool_calls 的后续轮直接 **HTTP 400**（§多轮对话回传要求） | runtime 层对 MiMo 通道加一条不变式："历史出现过 tool_calls 且本次开思考 → 回传 assistant 必须带 reasoning_content"；缺了就在本地报错而不是等 400 |
| 截断/压缩历史时把 reasoning_content 压掉 | 不报错但**指令遵循下降、幻觉增多**（§同节） | 历史压缩策略对 `reasoning_content` 字段**整体保留不摘要**（与硅基流动"禁止 drop"同口径，`sf-interleaved-thinking.txt:108`） |
| 开思考跑工具，tool_calls 混进 reasoning_content | 输出不稳定/不完整（`mimo-faq.txt:201`） | 检测到"reasoning_content 文本里出现 tool_calls JSON"即标记异常，按 2.4 切到工具循环关思考 |

---

## 3. 与能力边界映射

| 边界 | 文档依据 | 实现要点 |
|---|---|---|
| b) Interleaved Thinking 逐字保留回传 | `mimo-reasoning.txt §多轮对话回传要求`、`§思考模式下的多轮工具调用`；`mimo-faq.txt:143,156` | reasoning_content 作为 assistant 消息同级字段原样回传；工具前/工具间/工具后/跨轮所有片段都保留；缺失→400（MiMo）或缓存劣化+多步崩（硅基流动） |
| e) 思考模式+工具调用不稳定 | `mimo-faq.txt:200-202` | tool_calls 混入 reasoning_content = 不稳定信号；官方建议调 tool 时关 thinking |
| a) function calling 循环骨架 | `mimo-reasoning.txt §思考模式下的多轮工具调用`（run_turn 完整循环） | OpenAI 标准：tools → tool_calls → role:tool 回传 → 循环到 tool_calls=null；tool_choice=auto（`mimo-faq.txt:195`） |
| c) arguments 合法性 | **本页未直接涉及**（本页示例 arguments 均合法 JSON） | 仍须 runtime 做 schema 校验，不可 eval 直执（按 `_SPEC.md §4 c` 既有红线） |
| d) tool_choice 强制 | 本页仅见 `"tool_choice":"auto"`（`mimo-faq.txt:195`），未见"后端强制 auto"原文 | 按 `_SPEC.md §4 d` 既有结论，本页不补充 |
| f) Anthropic 兼容协议 | 本页受影响产品表提到 Anthropic 兼容协议同受影响（§多轮对话回传要求），但未给 tool_use/tool_result 内容块示例 | 本页不展开，留待 Anthropic 协议专篇 |

---

## 4. 与硅基流动 Interleaved Thinking 的异同（对比）

| 维度 | 小米 MiMo（本页） | 硅基流动（`sf-interleaved-thinking.txt`） | 异同 |
|---|---|---|---|
| 保留要求 | 必须完整回传，**缺失直接 400**（`mimo-reasoning.txt §多轮对话回传要求`） | "You must preserve … exactly as received"（`:83-84`），未提 400 | **对比**：MiMo 是硬报错（400），硅基流动是软警告（缓存劣化/多步崩）；MiMo 触发条件更窄（仅"开思考+历史有 tool_calls+本次带 tool_calls"三轮同时成立），硅基流动是"凡带 reasoning_content 就一律保留" |
| 缺失后果 | ①400 硬错误 ②指令遵循下降、幻觉增多（§同节） | "Broken multi-step behavior / Instability / **Reduced cache efficiency** and degraded output quality"（`:109-112`） | **对比**：MiMo 提"幻觉/指令遵循"，硅基流动提"缓存效率下降"——两者都认同会劣化输出，但 MiMo 多一层 HTTP 层硬拦截 |
| 禁止动作 | 主文档未逐字列"禁止修改/合并/重排"，但"必须完整回传"隐含逐字 | 明确列 ❌：修改文本/清洗后处理/合并拆分/重排/只留 content（`:103-108`） | **对比**：硅基流动写得更显式；MiMo 用"400"兜底。runtime 按最严的硅基流动口径实现即可同时满足两边 |
| 工具结果后的新 reasoning_content | 官方示例证明工具结果后仍产出（Request 1-2/2-2，§示例） | 明确要求保留"after tool results"产生的 reasoning（`:87-98`） | **同**：两边都要求保留工具结果之后新冒出来的思考段 |
| 流式字段 | `delta.reasoning_content`，先思考流后回答流（§流式） | `delta.reasoning_content`，同上（`:118-119,241`） | **同**：字段名一致，都是 OpenAI delta 扩展 |
| 流式/非流式同规则 | 本页示例为非流式，流式另节说明 | "applies to both streaming and non-streaming"（`:125-126`） | **同** |
| 触发保留的模型范围 | mimo-v2.6-flash/pro/pro-ultraspeed/v2.5-pro/v2.5（§支持的模型） | DeepSeek V3.2、GLM-4.7（`:46-48`） | **对比**：不同模型族，但字段与保留规则一致——OpenAI 事实标准趋同 |
| 思考开关参数 | `thinking.type=enabled/disabled`（extra_body） | `enable_thinking:true` + `thinking_budget:N`（`:238-240`） | **对比**：参数名不同（MiMo 用 `thinking.type`，硅基流动用 `enable_thinking`），runtime 须按通道映射；硅基流动多一个 `thinking_budget` 预算硬截断 |
| "开思考跑工具不稳"建议 | FAQ：建议调 tool 时关 thinking（`mimo-faq.txt:202`） | 本页未提此建议（只强调保留规则） | **对比**：MiMo 独有此条稳定性警告，硅基流动文档把它当成"保留规则"问题而非"开关"问题 |

**结论（对比）**：两家在"reasoning_content 逐字保留回传、流式 delta 字段名、工具结果后新思考段也要留"上**完全一致**，可共用一套 runtime 缓冲逻辑；差异只在①硬/软报错强度、②思考开关参数名、③MiMo 额外建议"调工具时关思考"。

---

## 5. 红线与自检记录

- [x] 本地壳文件已核查：`usage-guide_passing-back-reasoning_content.html` = SPA 空壳（无正文），已在元信息如实记录。
- [x] 在线页已 pagination 读完整（total_length=4581 = end_offset），未只读摘要。
- [x] 正文已落盘 `sources/mimo-reasoning.txt`，可复核。
- [x] 每条断言带出处（§章节 / sources 文件:行号）；无出处断言已避免。
- [x] 「本页未覆盖」处（边界 c/d/f）已显式标注，未编造原文不存在的内容。
- [x] 与硅基流动对照均标「对比」，事实取自 `sf-interleaved-thinking.txt` 具体行号。
- [x] 未调任何需要 API key 的接口（只读文档）；未改代码、未做 git 操作。
- [x] 篇幅：本笔记约 200 行，落在 150–500 行区间；全中文；多用表格。

**拿不准 / 推断处**：
- 「以主文档 400 为准、FAQ recommended 为旧口径」——推断（FAQ 与主文档语气差异，主文档更新于 2026-09-22，FAQ 更新于 2026-09-20，故判主文档更新）。
- 边界 c/d/f 的"本页未覆盖"——基于本页全文检索无对应原文，非编造。
