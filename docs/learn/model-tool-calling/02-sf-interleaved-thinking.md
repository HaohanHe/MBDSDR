# SiliconFlow Interleaved Thinking 学习笔记

> **文档来源**：
> - 任务指定本地 HTML：`/home/user/Doubao/chats/38438160041798146/repos/model-docs/siliconflow/docs_userguide_guides_interleaved-thinking.html`
>   —— 经逐字节核查为 **404 壳页**（尾部 flight payload 含 `"This page could not be found."` / `"页面未找到"` / `NEXT_HTTP_ERROR_FALLBACK;404`；侧边栏目录树无 `interleaved-thinking.mdx` 条目；可见 DOM 正文为空）。
> - 现行官方全文（web.fetch 抓取，total_length=2828 已读完）：`https://docs.siliconflow.com/en/userguide/guides/interleaved-thinking`
> - 旁证页：`https://docs.siliconflow.cn/docs/userguide/capabilities/reasoning`（思考开关参数）、`https://api-docs.siliconflow.cn/docs/userguide/guides/function-calling`（tools 循环）
> **精读方式**：本地 HTML 先转文本（404 壳）→ 判定本地不完整 → 按任务 hint 启用在线对照源 → web.fetch 分页读完全文（end_offset=total_length）。
> **纯文本落盘**：`sources/sf-interleaved-thinking.txt`（引用行号均指此文件）。
> **精读日期**：2026-10-01　**作者**：MainAgent（model-tool-calling 批次 02）
> **覆盖边界**：重点 b、e；对照 a、c、d、f（本页不涉及 f 的 Anthropic 协议）。
> **标注约定**：行号 = `sources/sf-interleaved-thinking.txt:<行>`；本页未明说的标「推断」。

---

## 1. 原文事实清单

### 1.1 是什么 / 谁支持（机制与适用模型）

| 事实 | 出处 | 要点（原文措辞） |
|---|---|---|
| 机制 = 思考与工具调用交错 | txt:55-63（§1） | 模型可：①判断是否要调工具 ②调用工具 ③接收工具结果 ④**从中间输出继续** ⑤决定下一步（再调工具或给最终答案）。"enables robust multi-step execution where tool outputs can influence subsequent steps" |
| 单 Turn 可含多 Step，工具后还能继续思考 | txt:65-77（§Diagram） | "a single Turn can contain multiple Steps, and how the model may continue producing `reasoning_content` **after tool results**（即你发完 `role="tool"` 之后）" |
| 支持模型（截至本文） | txt:45-48（§Overview） | **仅 DeepSeek V3.2 与 GLM-4.7** 两款；页面顶部 ⚠️ 明示 "Some models introduce new Interleaved Thinking behaviors"（txt:37-42） |
| 触发条件 | txt:39-42 | 两款模型在 **serverless model API 的 tool-calling 流程中**"may emit Interleaved Thinking-related structured output"——即**不是独立开关，而是工具调用流程里顺带吐出的结构化字段** |
| 适用场景 | txt:49-54 | Agent 编排 / tool-calling / 编码调试 / 需要中间工具输出的多步任务 |

> 字段级要点：本页**没有**一个叫 "interleaved_thinking" 的请求开关。"是否出现"由模型在工具调用流程中自行决定（"may emit"），客户端只负责**接住并原样回放**。这与 `enable_thinking`（见 §1.5）是两个层面的事。

### 1.2 reasoning_content 出现的所有位置 + "逐字保留原样回传"原文

| 出现位置 | 出处 | 原文措辞（逐字） |
|---|---|---|
| 工具调用**之前** | txt:90,96 | "before any tool call" / "Content emitted before any tool calls" |
| 多次工具调用**之间** | txt:91,97 | "between multiple tool calls" / "Content emitted between tool calls (multi-step tool chaining)" |
| 收到工具结果**之后** | txt:92-93,98 | "and after receiving tool results (i.e., after you send role=\"tool\" messages and the model continues)" / "Content emitted after tool results" |
| 跨多个 Turn | txt:99-100 | "Any reasoning_content segments produced across turns (**keep the original order**)" |
| 总规则 | txt:83-84,94 | "You must preserve `reasoning_content` **exactly as received**, and send it back **unchanged** in subsequent requests." / "preserve and replay **all** such reasoning_content **exactly as generated**" |
| 工具后也必须保留（强调） | txt:87-89 | "preservation is required **not only** for reasoning_content produced after user messages, **but also** for reasoning_content produced **after tool results**" |

### 1.3 流式场景：delta.reasoning_content

| 事实 | 出处 | 原文措辞 |
|---|---|---|
| 流式思考字段名 | txt:118-119 | "Accumulate Interleaved Thinking text from `reasoning_content` (or **`delta.reasoning_content`** if streaming)" |
| 流式三个字段都要累加 | txt:116-120 | `content`←`delta.content`；`reasoning_content`←`delta.reasoning_content`；`tool_calls`←`delta.tool_calls` |
| 流/非流保留规则一致 | txt:125-127 | "This rule applies to **both streaming and non-streaming**. Streaming only affects how you *read* fields (`delta.*`), **not what you must preserve**." |
| 代码佐证（示例） | txt:170-172,204-206 | `if getattr(delta, "reasoning_content", None): reasoning_content += delta.reasoning_content` |

### 1.4 丢弃/改动 reasoning_content 的后果（原文措辞原样）

| 禁止动作 | 后果（原文） | 出处 |
|---|---|---|
| Modify the text（改字） | — | txt:104 |
| "Clean up" / post-process（清洗后处理） | — | txt:105 |
| Merge or split segments（合并/拆分片段） | — | txt:106 |
| Reorder segments（重排） | — | txt:107 |
| Drop it while keeping only normal assistant text（**只留正文、丢思考**） | — | txt:108 |
| （做了以上任一）后果 1 | **Broken multi-step behavior around tools**（工具周边多步行为崩坏） | txt:110 |
| （做了以上任一）后果 2 | **Instability across tool calls**（跨工具调用不稳定） | txt:111 |
| （做了以上任一）后果 3 | **Reduced cache efficiency and degraded output quality**（缓存命中率下降 + 输出质量劣化） | txt:112 |

> 这三条后果正好对应用户背景里说的"实验室绿、真机红"：实验室单步不带思考就过，真机多步工具链一丢 reasoning_content 就崩。**"缓存劣化"** 是原文首次给出的机理——丢历史思考 = 破坏 prompt 前缀复用 = 前缀缓存 miss。

### 1.5 思考模式开关参数 / 默认值 / 按模型差异

| 项 | 出处 | 说明 |
|---|---|---|
| 本页**未提供** thinking 开关 | txt 全篇（§Important Notice~Summary） | 本页只规定"出现后怎么保留"，**不教怎么开/关思考**；V3.2/GLM-4.7 在工具流程中是 "may emit"（自动），客户端无字段去抑制 |
| 思考总开关（旁证：Reasoning 页） | txt:236-240（§附）；Reasoning 页 §3.1.1 | `extra_body={"enable_thinking": true, "thinking_budget": 1024}` |
| thinking_budget 语义 | Reasoning 页 §3.1.1 | = CoT 内部推理 token 数；达到预算时 **Qwen3 系列**会强制停 CoT，**其他推理模型可能继续输出思考**（按模型差异） |
| 默认值 | （本页无） | **本页未给默认值；推断**：不传 enable_thinking 时各模型默认行为不同，需按模型实测（见 §3 边界 e） |
| 返回字段定位（旁证） | Reasoning 页 §3.1.2 | `reasoning_content` 与 `content` **同级**（chat.choices[0].message 下） |

### 1.6 官方示例逐字段记录（DeepSeek V3.2，完整两轮）

出处：txt:128-217（§4）。这是本页唯一完整对话示例。

**Round 1（先思考后调工具）**

```python
# 请求字段
model="deepseek-ai/DeepSeek-V3.2"
messages=[{system},{user("What's the weather like in America?")}]
tools=[{type:function, function:{name:get_weather, parameters:{city:string, required:[city]}}}]
stream=True   # 注释原文：optional; the same preservation rule applies to non-streaming

# 累加三个流字段
reasoning_content += delta.reasoning_content
content            += delta.content
tool_calls         += delta.tool_calls

# 🔑 回传 assistant——三个字段一个都不能少
messages.append({
  "role":"assistant",
  "content": content,
  "reasoning_content": reasoning_content,   # 逐字
  "tool_calls": tool_calls
})

# 工具结果回传
messages.append({
  "role":"tool",
  "tool_call_id": tool_calls[0]["id"],
  "content": json.dumps({"weather":"Sunny","temp":"25°C"})
})
```

**Round 2（工具结果之后模型又产生新的思考）**

```python
# 注释原文：Round 2: model may produce NEW reasoning_content AFTER tool results
# 同样三字段累加
reasoning_content_2 += delta.reasoning_content
...
# 🔑 再次整体回传（含本轮新思考）
messages.append({
  "role":"assistant",
  "content": content_2,
  "reasoning_content": reasoning_content_2,   # 第二轮新产生的，也逐字带上
  "tool_calls": tool_calls_2
})
```

| 字段 | 角色 | 本页规则 |
|---|---|---|
| `reasoning_content` | assistant 消息下与 content 同级 | **每一轮 assistant 都要带**，跨轮顺序保留，逐字不改（txt:122-124, 177-183, 211-217） |
| `content` | 最终对外回答 | 正常累加，可展示给用户（txt:116-117） |
| `tool_calls` | 工具调用请求 | "as received" 原样回传（txt:124） |
| `role:"tool"` 回传体 | 工具执行结果 | 必须带 `tool_call_id` 对齐上一次 tool_calls（txt:185-190） |

> **注意**：本示例是"两轮"（Round1 思考→调一次工具→Round2 再思考），**不是**单轮内多个并行 tool_calls 之间夹 reasoning 的示例；txt:97 明确说"between tool calls (multi-step tool chaining)"也要保留，即单轮若出多个 tool_calls，中间的 reasoning 段同样逐字保留。

### 1.7 稳定性相关原文警告

| 警告 | 出处 | 原文 |
|---|---|---|
| 行为较新、可能变 | txt:37-38,42 | "⚠️ Some models introduce new Interleaved Thinking behaviors... please follow the guidelines below" |
| 客户端不当处理 = 不稳定 | txt:110-112 | 见 §1.4 三条后果（Broken multi-step / Instability across tool calls / cache 劣化） |
| GLM-4.7 与 V3.2 同规则 | txt:220-226 | 换 model id 为 `zai-org/GLM-4.7` 即可，"All Interleaved Thinking preservation rules remain the same" |

> **本页没有**出现"tool_calls 与 reasoning_content 混用/串行错位是不稳定信号"这样的字面警告（规范 §4.e 提到的那类信号）。本页能坐实的稳定性格言只有：**不改、不丢、不合并拆分、不重排 reasoning_content**（txt:103-108, 233）。若要更强的"混用信号"判据，需读 MiMo/其他厂商页——**推断**，本页不提供。

---

## 2. 对 MBDSDR 工具化实现的启示

> 背景对照：MBDSDR runtime = 模型(大脑)↔无线电工具集的中介，循环 = 校验 arguments→真实执行→`role=tool` 回传→多步直到 stop。本页正好补齐"这个循环里 assistant 消息该长什么样"。

### 2.1 assistant 消息必须是"三件套"，不是只有 content+tool_calls

`src/ai/agent.cpp` 构造回传历史时，assistant 消息对象必须同时落三个字段（对照 txt:122-124）：

```cpp
// 建议结构（推断：按 OpenAI 兼容 message schema）
struct AssistantTurn {
  std::string role = "assistant";
  std::string content;              // 对外回答，可展示
  std::string reasoning_content;     // ← 本页核心：与 content 同级
  nlohmann::json tool_calls;        // 原样回传
};
```

- **红线**：缺 `reasoning_content` 字段 = 命中 txt:108 "Drop it while keeping only normal assistant text" → 真机多步崩（txt:110-112）。
- 这是"实验室绿真机红"高发点的直接解药：实验室若用不带思考的小模型/单步，历史里没这个字段也能跑；一旦上 V3.2/GLM-4.7 多步工具链，丢了就崩。

### 2.2 llm_client：流式三通道累加，非流字段直取

`llm_client`（无论 C++ 还是封装层）收包逻辑按 txt:116-120 / 170-172：

| 模式 | content | reasoning_content | tool_calls |
|---|---|---|---|
| 流式 `stream=true` | `delta.content` 拼接 | `delta.reasoning_content` 拼接 | `delta.tool_calls` 按 index 合并 |
| 非流 `stream=false` | `message.content` | `message.reasoning_content` | `message.tool_calls` |

- 推断：流式 tool_calls 是分片（function.arguments 增量），合并要按 `tool_calls[i].index` 累加 arguments 字符串——这与本页无关但属 OpenAI 兼容常识，**本页未讲，需另读 function-calling 页**。
- 关键：**reasoning 累加用 `+=`，绝不 trim / 去尾补 / 正则清洗**（命中 txt:105 "clean up" 禁令）。

### 2.3 多步循环里 reasoning_content 的"逐段保留"落点

runtime 主循环（`agent.cpp` 的 while not stop）每一轮：

1. 请求 LLM（带完整历史）。
2. 收包得到 `(content_k, reasoning_content_k, tool_calls_k)`。
3. **无条件**把本轮 assistant 消息（含 `reasoning_content_k`）append 进 messages。
4. 执行每个 tool_call → append `{role:"tool", tool_call_id, content:结果}`。
5. 回到 1。

要点：
- **第 3 步不能省**——哪怕本轮 `content` 为空、纯思考+调工具，也要把 `reasoning_content_k` 存进历史。
- **顺序固定**：第 k 轮 reasoning 必须紧跟在它自己那轮 assistant 消息里，插在它触发的 `role=tool` 之前（txt:99-100 "keep the original order"，txt:107 禁重排）。
- **工具结果后会再来一段新的 reasoning**（txt:192 "NEW reasoning_content AFTER tool results"）——runtime 不能假设"思考只在每轮开头出现一次"，必须每轮都重新收集并 append。

### 2.4 缓存视角：为什么不能丢

txt:112 明示丢 reasoning → "Reduced cache efficiency"。硅基流动这类 OpenAI 兼容服务按 **prompt 前缀**做 KV cache。历史里 assistant 的 reasoning 段是前缀的一部分；一旦丢掉或改字，前缀哈希变化 → cache miss → 既慢又贵，且模型看到的上下文与当初生成时不一致 → 输出质量劣化。**runtime 应把"历史消息不可变"当一等约束**：append-only，禁止事后改写历史里的任何字段。

### 2.5 思考开关怎么配

- 本页（Interleaved Thinking）**不管开关**，只管保留。
- 是否开思考用 `extra_body: enable_thinking / thinking_budget`（Reasoning 页）。对 V3.2/GLM-4.7 这类工具模型，建议**默认开思考 + 给足 thinking_budget**，否则交错思考根本不产生，本页规则无从谈起；但 thinking_budget 要按任务调，过大拖慢、过小被截断（Reasoning 页 §3.1.1：截断时 `finish_reason=length`）。
- **推断**：不同模型对"工具流程中是否自动 emit reasoning"行为不同，需在 `src/core/tokens.h` 或模型能力表里按 model id 配置（对齐规范 §4.e"按模型配置 thinking"）。

---

## 3. 与能力边界映射

| 边界 | 本文档依据 | 实现要点 |
|---|---|---|
| a) OpenAI 兼容循环（tools→tool_calls→role:tool→stop） | §4 示例 txt:133-190；旁证 function-calling 页 | runtime 循环骨架照此；tool_call_id 对齐 |
| **b) reasoning_content 必须逐字保留回传**（重点） | txt:83-84,94,96-100,122-124 | assistant 消息三字段；流/非流都要；工具前/间/后三段全覆盖；append-only |
| c) arguments 校验/白名单 | **本页未讲**（旁证 function-calling 页用 `eval` 是反面教材） | runtime 仍须 schema 校验，禁 eval 直执（见 c 篇笔记） |
| d) tool_choice | **本页未讲** | 另查 function-calling / api 页 |
| **e) 思考+工具稳定性（重点）** | txt:37-42,103-112,233 | 不改/不丢/不合拆/不重排；按模型配 thinking；"may emit"意味着要容忍 reasoning_content 时有时无 |
| f) Anthropic 兼容协议 | **本页未讲** | 另查 api/messages-post 页 |

---

## 4. 红线与自检记录

- 文件可打开 ✓：`02-sf-interleaved-thinking.md` 与 `sources/sf-interleaved-thinking.txt` 均写入成功。
- 引用的每条都能在原文找到 ✓：所有事实均带 `sources/sf-interleaved-thinking.txt:<行>` 或 §章节；旁证页（reasoning / function-calling）已明确标注为旁证、不计入本页断言。
- 覆盖任务指定范围 ✓：§1.1~1.7 对应任务 7 个重点（是什么/出现位置/流式/丢弃后果/开关参数/官方示例/稳定性警告）。
- 无编造 ✓：
  - 本地 HTML 实为 404 壳、在线 .cn 源 dead，已在元信息如实披露，正文改用现行 .com 官方站（任务 hint 明确允许"本地文本疑似不完整时使用在线对照源"）。
  - 本页**未提供** thinking 开关默认值、未提供"tool_calls 与 reasoning 混用信号"警告——均如实标注「推断」或"本页未讲"，未杜撰。
- 「推断」处 = 原文无直接证据 ✓：§2.1 的 C++ 结构体、§2.2 流式 tool_calls 分片合并、§2.5 按模型配置表，均为基于 OpenAI 兼容常识的落地建议，非本文档原文。
- 未做任何越界动作 ✓：只读文档、只写本目录文件；未改代码、未 git、未调需 API key 接口（示例代码仅转录自官方页，未实跑）。
- 篇幅：229 行 markdown（含表格），落在 150–500 区间。
