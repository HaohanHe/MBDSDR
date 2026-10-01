# 思考模式 × 工具调用稳定性 学习笔记（能力边界 e 专篇）

> 文档来源（本地绝对路径）：
> - `/home/user/Doubao/chats/38438160041798146/docs/learn/model-tool-calling/sources/sf-interleaved-thinking.txt`（241 行，硅基流动官方英文站 Interleaved Thinking 全文转录）
> - `/home/user/Doubao/chats/38438160041798146/docs/learn/model-tool-calling/sources/sf-chat-completions-api.txt`（545 行，硅基流动 `/chat/completions` API 手册）
> - `/home/user/Doubao/chats/38438160041798146/docs/learn/model-tool-calling/sources/mimo-faq.txt`（303 行，小米 MiMo 官方 FAQ「API Integration」页）
> - 旁证：`sources/sf-function-calling.txt`、`sources/sf-stream-mode.txt`（仅交叉引用，无新增 thinking 规则）
>
> 精读方式：本地纯文本逐行通读（HTML 已转文本落盘）；硅基 interleaved-thinking 本地 HTML 经核查为 404 壳页，真实正文为 2026-10-01 web.fetch 现行英文站 `docs.siliconflow.com/en/userguide/guides/interleaved-thinking` 全文转录（见 `sf-interleaved-thinking.txt:3-19` 元信息）。
> 精读日期：2026-10-01。作者：s_000c4Gl0GwB。
> 覆盖边界：本批 a–f 六条中**专攻 e（思考模式 + 工具调用可能不稳定，需按模型配置 thinking）**，并顺带核验 b（reasoning_content 逐字回传）在两家的一致性。a/c/d/f 仅在对比表中提及，不展开。
>
> 标注约定：「原文」= 源文件逐字/逐节可复核；「推断」= 原文无直接证据，由本 agent 据上下文合理推断；「未覆盖」= 两份源文件均无此细节，需另读 model-hyperparameters 等页确认。

---

## 0. 一句话定位

两家服务商在「工具循环里怎么对待 thinking」上**结论相反但底层共识一致**：

- **共识**：只要 thinking 开着、且在跑工具循环，`reasoning_content` 就必须原样逐字保留并回传——硅基流动（`sf-interleaved-thinking.txt:83-100`）与 MiMo（`mimo-faq.txt:141-143`）在这一点上措辞几乎一致。
- **分歧**：硅基流动把「思考中穿插工具调用」当成**专为 Agent/工具链设计的特性**，要求**开着**并正确保留（仅 DeepSeek V3.2 / GLM-4.7 两款）；MiMo 把「thinking 开着调工具」当成**不稳定状态本身**，官方建议**调工具时关掉 thinking**（`mimo-faq.txt:200-202`）。

这正是 MBDSDR runtime「按模型配置 thinking（能力边界 e）」要解决的核心问题。

---

## 1. 原文事实清单

### 1.1 硅基流动侧：interleaved thinking 支持范围

| 事实 | 出处 | 要点 |
|---|---|---|
| interleaved thinking 目前**仅支持 DeepSeek V3.2 与 GLM-4.7** | `sf-interleaved-thinking.txt:46-48`（Overview 明列两款） | 不是全模型能力；换模型即失效 |
| 这两款在 serverless API 的工具调用流里会发出 interleaved thinking 结构化输出 | `sf-interleaved-thinking.txt:38-43`（Important Notice） | "most commonly in tool-calling flows"——工具流是触发主场景 |
| 该特性尤其适用于：Agent 编排、工具调用、编码调试、需要中间工具输出的多步任务 | `sf-interleaved-thinking.txt:49-53` | 与 MBDSDR「无线电工具集给 LLM」完全同构 |
| 单 Turn 可含多个 Step，工具结果（`role="tool"`）之后模型会**再次**产出 `reasoning_content` | `sf-interleaved-thinking.txt:65-77`（Turn/Step 图） | 推理不是一次性的，而是「工具调用前 / 调用之间 / 工具结果后」分段出现 |
| GLM-4.7 的模型名为 `zai-org/GLM-4.7`，保留规则与 DeepSeek V3.2 完全相同 | `sf-interleaved-thinking.txt:220-226` | 换 model 字段即可，其余逻辑不动 |

### 1.2 硅基流动侧：thinking 开关参数（`/chat/completions`）

| 参数 | 类型/取值 | 适用模型 | 出处 |
|---|---|---|---|
| `enable_thinking` | boolean，`false\|true`，在推理/非推理模式间切换 | **大多数推理模型**（不区分具体型号） | `sf-chat-completions-api.txt:59-63`（另见 `:289-293`） |
| `thinking_budget` | integer，思维链最大 token 数，范围 **`128 <= value <= 32768`** | **大多数推理模型** | `sf-chat-completions-api.txt:65-69`（另见 `:295-299`） |
| `reasoning_effort` | string，**仅 `"high" \| "max"`** | **仅** `Pro/deepseek-ai/DeepSeek-V4`、`deepseek-ai/DeepSeek-V4-Flash`、`Pro/zai-org/GLM-5.2` | `sf-chat-completions-api.txt:71-75`（另见 `:301-305`） |
| 常规请求默认 effort = `high`；复杂 Agent 类请求（Claude Code、OpenCode）自动置 `max`；`low`/`medium` 映射到 `high`，`xhigh` 映射到 `max` | — | 同上三款 V4/GLM-5.2 | `sf-chat-completions-api.txt:73` |
| 返回 message 含 `reasoning_content` 字段；`completion_tokens_details.reasoning_tokens` 单列推理 token 计数 | 示例响应 `"reasoning_content": "..."` / `reasoning_tokens: 1190` | 推理模型 | `sf-chat-completions-api.txt:486,495-497` |
| `extra_body={"enable_thinking": true, "thinking_budget": 1024}` 用法；到预算时 **Qwen3 系列会强制停止 CoT，其他推理模型可能继续输出思考** | — | 旁证页（Reasoning 概览） | `sf-interleaved-thinking.txt:238-240` |

> ⚠️ 关键型号分档（原文比对后得出）：
> - **interleaved thinking 档**：DeepSeek V3.2、GLM-4.7（`sf-interleaved-thinking.txt:46-48`）。
> - **reasoning_effort 档**：DeepSeek V4 / V4-Flash / GLM-5.2（`sf-chat-completions-api.txt:73`）。
> - **这两档名单并不重叠**（原文未把 V3.2 / GLM-4.7 列入 reasoning_effort 适用名单）。推断：`reasoning_effort` 不能下发给 DeepSeek V3.2 / GLM-4.7；对它们只能用 `enable_thinking` + `thinking_budget`。

### 1.3 硅基流动侧：max_tokens 与思维链的关系

| 事实 | 出处 | 要点 |
|---|---|---|
| `max_tokens` 是「要生成的最大 token 数量」，**不包含思维链部分** | `sf-chat-completions-api.txt:55-57`（另见 `:195-197`） | 思维链 token 走单独预算（`thinking_budget`），不挤占 `max_tokens` |
| 建议不要把 `max_tokens` 设到窗口上限，要为输入与系统开销**预留约 10k token 缓冲** | `sf-chat-completions-api.txt:57` | runtime 设上限时必须留余量 |
| 流式与非流式的「保留规则」一致：流式只是读法变 `delta.*`，该保留的内容不变 | `sf-interleaved-thinking.txt:125-126` | 流式 `delta.reasoning_content` / `delta.content` / `delta.tool_calls` 分别累加 |

### 1.4 硅基流动侧：reasoning_content 必须逐字回传（b 边界核验）

| 事实 | 出处 | 要点 |
|---|---|---|
| 工具调用时 API 可能在专属字段返回 `reasoning_content`；**必须原样保留并不改动地回传** | `sf-interleaved-thinking.txt:80-84` | 非可选，是 non-negotiable rule |
| 需保留的片段包括：任何工具调用前、多次工具调用之间、**收到工具结果后**、跨所有 Turn 的片段（保持原始顺序） | `sf-interleaved-thinking.txt:87-100` | 工具结果后的新推理尤其易漏 |
| 禁止：改文本 / 「清洗」后处理 / 合并或拆分片段 / 重排 / 只留正文丢推理 | `sf-interleaved-thinking.txt:103-108` | runtime 不得对 reasoning_content 做任何加工 |
| 丢弃/加工的后果：工具周围多步行为崩、跨工具调用不稳定、缓存效率下降与输出质量劣化 | `sf-interleaved-thinking.txt:109-112` | 三大后果原文点名 |
| 回传 assistant 消息时须同时带 `content` + `reasoning_content`（逐字、完整、原序）+ `tool_calls` | `sf-interleaved-thinking.txt:121-124`；代码示例 `:177-183, 211-217` | 三轮示例：Round1 后回传，工具结果后 Round2 再保留新产生的 reasoning |

### 1.5 MiMo 侧：thinking 开着调工具的逐字原文记录

> 以下为 `mimo-faq.txt` 逐字要点，按任务要求原文展开。

**Q1：thinking 模式下如何做多轮工具调用？**（`mimo-faq.txt:141-143`）
> 原文：「During the multi-turn tool calls process in thinking mode, the model returns a `reasoning_content` field alongside `tool_calls`. To continue the conversation, **it is recommended to keep all previous `reasoning_content` in the `messages` array for each subsequent request** to achieve the best performance.」
- 即：MiMo 同样承认 thinking 模式可多轮工具调用，且**建议把历史 `reasoning_content` 全部留在 messages 里**——与硅基流动 b 边界一致（`mimo-faq.txt:143` ↔ `sf-interleaved-thinking.txt:94-100`）。
- 请求示例中 assistant 消息确带 `reasoning_content` 字段（`mimo-faq.txt:153-157`），`tool_choice: "auto"`（`mimo-faq.txt:195`）。

**Q2：为什么 tool_calls 有时混在 reasoning_content 里、有时在独立 tool_calls 字段？**（`mimo-faq.txt:200-202`）
> 原文：「The appearance of `tool_calls` in the reasoning content indicates **instability and incomplete output caused by the model having `thinking` enabled when calling `tool`. It is recommended to disable `thinking` when calling `tool` calls** and to adjust the settings according to Model Hyperparameters … to achieve a more stable and better user experience.」

逐句拆解：

| 原文断言 | 出处 | 含义 |
|---|---|---|
| tool_calls 出现在 reasoning_content 里 = **不稳定（instability）+ 输出不完整（incomplete output）** | `mimo-faq.txt:202` | 这是官方钦定的不稳定信号，不是偶发噪声 |
| 成因 = 模型**开着 thinking 的同时去调 tool** | `mimo-faq.txt:202` | thinking 与工具调用并存本身被视为风险源 |
| **建议调 tool 时关闭 thinking** | `mimo-faq.txt:202` | 与硅基流动「开着并保留」方向相反 |
| 具体开关项按 Model Hyperparameters 页调整 | `mimo-faq.txt:202`（外链 model-hyperparameters） | ⚠️ **该页不在本批 sources 内，MiMo 关闭 thinking 的具体请求字段名「未覆盖」** |

### 1.6 双源对比表（逐条带出处）

| 对比维度 | 硅基流动（SF） | 小米 MiMo |
|---|---|---|
| 对「thinking + 工具调用」的定性 | **正向特性**：interleaved thinking 专为工具流/Agent/多步设计（`sf-interleaved-thinking.txt:49-53`） | **风险状态**：开着 thinking 调 tool 会导致不稳定+输出不完整（`mimo-faq.txt:202`） |
| 支持的模型 | 仅 DeepSeek V3.2、GLM-4.7（`sf-interleaved-thinking.txt:46-48`） | 本页未列具体型号；示例用 `mimo-v2.6-pro`（`mimo-faq.txt:163`） |
| thinking 开关方式 | 请求体 `enable_thinking`(bool) + `thinking_budget`(128–32768)；`reasoning_effort` 仅 V4/V4-Flash/GLM-5.2（`sf-chat-completions-api.txt:59-75`） | **本 FAQ 未给出关闭 thinking 的字段名**，仅指向 Model Hyperparameters 页（`mimo-faq.txt:202`）——「未覆盖」 |
| 工具循环中 reasoning 处理要求 | `reasoning_content` **必须逐字保留回传**，含工具结果后新产生的片段，禁止改/合/拆/丢（`sf-interleaved-thinking.txt:83-112`） | 多轮工具调用时**建议保留全部历史 `reasoning_content`**（`mimo-faq.txt:143`） |
| 流式下 reasoning 字段 | `delta.reasoning_content` 累加，保留规则与非流式一致（`sf-interleaved-thinking.txt:118-126`；旁证 `sf-stream-mode.txt:87-148`） | FAQ 为 `stream: false` 示例（`mimo-faq.txt:166`），流式 reasoning 规则本页「未覆盖」 |
| 官方建议 | **开着** interleaved thinking + 严格保留 reasoning_content = 稳定工具使用（`sf-interleaved-thinking.txt:229-234`） | 调 tool 时**关掉 thinking** 更稳定（`mimo-faq.txt:202`） |
| 不稳定信号 | 丢弃 reasoning_content → 多步行为崩 / 跨工具调用不稳定 / 缓存劣化（`sf-interleaved-thinking.txt:109-112`） | **tool_calls 混进 reasoning_content 字段**（`mimo-faq.txt:200-202`） |
| `max_tokens` 是否含思维链 | **不含**思维链，另需预留 ~10k 缓冲（`sf-chat-completions-api.txt:55-57`） | FAQ 示例用 `max_completion_tokens: 1024`（`mimo-faq.txt:164`），是否含 CoT 本页未说明——「未覆盖」 |
| `tool_choice` | 示例 `tool_choice="auto"`（`sf-chat-completions-api.txt:459`） | 示例 `tool_choice: "auto"`（`mimo-faq.txt:195`） |

---

## 2. 稳定性信号清单（响应特征 → 是否预示不稳定）

| # | 观测到的响应特征 | 是否不稳定信号 | 依据性质 | 出处/理由 |
|---|---|---|---|---|
| S1 | `tool_calls` 内容出现在 `reasoning_content` 字段里，而非独立 `tool_calls` 数组 | **是，官方钦定**：instability + incomplete output | 原文 | `mimo-faq.txt:200-202` |
| S2 | 工具调用前/间/后的 `reasoning_content` 被 runtime 丢弃或改写后继续循环 | **是**：会触发多步行为崩、跨工具调用不稳定 | 原文（后果描述） | `sf-interleaved-thinking.txt:109-112` |
| S3 | 缓存命中率明显下降 / 输出质量随轮次劣化 | 是（间接）：被列为丢弃 reasoning_content 的后果之一 | 原文 | `sf-interleaved-thinking.txt:112` |
| S4 | 同一 `messages` 里历史 assistant 未带 `reasoning_content`，多步工具链行为反常 | 是（推论自两家「必须保留」要求） | 推断 | SF `:94-100` + MiMo `:143` 均要求保留；缺失即偏离官方用法 |
| S5 | `reasoning_content` 被截断后紧接着产出畸形/重复的 `tool_calls` | **是（高置信推断）**：与 S1「incomplete output」同族 | 推断 | 原文只说「输出不完整」，未给截断后形态；结合 S1 推断 |
| S6 | 非 interleaved 模型（即 V3.2/GLM-4.7 之外）强行开启 thinking 并跑多步工具链 | 是（推断）：SF 仅对两款背书 interleaved thinking | 推断 | `sf-interleaved-thinking.txt:46-48` 限定两款；名单外模型无官方 stability 背书 |

> 说明：S1–S3 有原文直接依据；S4–S6 为在原文要求上的合理外推，runtime 落地时应打日志观测、勿当作已验证事实。

---

## 3. 对 MBDSDR 工具化实现的启示

### 3.1 按模型 × 任务类型配置 thinking 的决策表

> 原则：**模型能力分档 × 任务是否真需要推理**共同决定 thinking 开关。SF 两款是「开着且保留」，MiMo 是「调工具倾向关」。

| 任务类型 | 硅基 DeepSeek V3.2 / GLM-4.7（interleaved 档） | 硅基 V4 / V4-Flash / GLM-5.2（effort 档） | MiMo（v2.6 系列） |
|---|---|---|---|
| **纯问答（无 tools）** | thinking 按需开；硬推理开 `enable_thinking=true`+`thinking_budget`，闲聊可关 | 可开 `reasoning_effort=high/max`（`sf-chat-completions-api.txt:73`） | 按任务复杂度开（本页未给开关字段，「未覆盖」） |
| **单步工具**（一次调谐/读数即答） | 开 interleaved thinking，逐字保留 reasoning_content | 原文未背书 interleaved；**推断**可开 thinking 但需保留 reasoning_content 并观测 | **官方建议调 tool 时关 thinking**（`mimo-faq.txt:202`）；若关，无 reasoning_content 需回传 |
| **多步工具链**（连续调多个无线电工具直到 stop） | **强烈推荐开**——此特性就是为多步设计的（`sf-interleaved-thinking.txt:49-53`）；必须保留每段 reasoning | 同单步，推断谨慎开、观测 S1 信号 | 两难：`mimo-faq.txt:143` 说 thinking 多轮工具要保留 reasoning 才最佳，但 `:202` 说开着调工具不稳；**推断**多步链先尝试关 thinking 求稳，不稳再开并完整保留 reasoning |

**thinking 开关的切换点（推断，供 runtime 默认值参考）：**
- 工具调用密集 / 对延迟敏感的无线电实时读数 → 倾向**关 thinking**（MiMo 默认取向，`mimo-faq.txt:202`；SF 侧 interleaved 仍可开但会增加推理 token 成本）。
- 多步链需要「先观察频谱 → 再决定调谐 → 再解调」这种中间推理 → SF V3.2/GLM-4.7 **开 thinking** 并完整保留；MiMo 侧作为实验项。
- 切换不是每轮乱跳：一旦在某个工具循环里决定开 thinking，就必须**在整个该循环内持续逐字回传 reasoning_content**（`sf-interleaved-thinking.txt:121-124`）；中途丢弃 = S2 不稳定。

### 3.2 具体代码位建议（src/ai/agent.cpp 与 llm_client）

| 组件 | 现状应补的动作 | 依据 |
|---|---|---|
| `llm_client` 请求构造 | 新增 thinking 配置字段：`enable_thinking`(bool)、`thinking_budget`(int, 限 128–32768)、`reasoning_effort`(仅 V4/V4-Flash/GLM-5.2 允许 high/max)；**按 model 名白名单决定是否下发** `reasoning_effort` | `sf-chat-completions-api.txt:59-75` |
| `llm_client` token 预算 | `max_tokens` **不含思维链**；分配预算时 = `max_tokens`(正文) + `thinking_budget`(CoT)，并额外给输入留 ~10k 缓冲，勿顶到上下文窗口上限 | `sf-chat-completions-api.txt:55-57,67-69` |
| `llm_client` 响应解析 | assistant 消息落库时**同时存** `content`、`reasoning_content`、`tool_calls` 三件套；流式分别累加 `delta.content` / `delta.reasoning_content` / `delta.tool_calls` | `sf-interleaved-thinking.txt:115-126`；旁证 `sf-stream-mode.txt:87-148` |
| `agent.cpp` 工具循环 | 回传 `role="tool"` 后，把上一轮 assistant 的 `reasoning_content` **原样**塞回 messages（禁止 trim/清洗/重排/丢弃），再发下一次请求 | `sf-interleaved-thinking.txt:83-112`；MiMo `:143` |
| `agent.cpp` 稳定性护栏 | 响应后做 S1 检测：若 `tool_calls` 内容混进 `reasoning_content` 文本 → 记 WARN 日志 + 标记本轮「不稳定」；对 MiMo 模型据此触发「下次关 thinking」重试策略 | `mimo-faq.txt:200-202`（S1） |
| `agent.cpp` 策略层 | 维护一张「模型 → thinking 默认策略」表（见 §3.1）；纯问答 / 单步工具 / 多步链三档，按模型分流 | 综合 `sf-interleaved-thinking.txt:46-53` + `mimo-faq.txt:202` |
| `llm_client` 超时 | 客户端侧设连接/读超时 + 指数退避；长响应用流式（thinking 模型 CoT 常较长） | `mimo-faq.txt:219-228` |

> 伪代码（回传 assistant 消息，对齐 SF 示例 `sf-interleaved-thinking.txt:177-183`）：
> ```cpp
> messages.push_back({.role="assistant",
>                     .content=acc_content,
>                     .reasoning_content=acc_reasoning,  // 逐字、原序、禁止加工
>                     .tool_calls=acc_tool_calls});
> messages.push_back({.role="tool",
>                     .tool_call_id=call_id,
>                     .content=tool_result_json});
> // 下一轮请求带 enable_thinking / thinking_budget，且对 SF interleaved 模型保持开启
> ```

---

## 4. 与能力边界映射

| 边界 | 文档依据 | 实现要点 |
|---|---|---|
| **e) thinking + 工具调用可能不稳定，需按模型配置 thinking**（本篇核心） | SF：`sf-interleaved-thinking.txt:38-53,109-112`；MiMo：`mimo-faq.txt:200-202` | 按模型分档（SF interleaved 两款 vs MiMo），决定默认开/关；S1 信号触发降级重试 |
| b) reasoning_content 必须逐字保留回传 | SF `sf-interleaved-thinking.txt:83-112`；MiMo `mimo-faq.txt:141-143` | 两家一致；agent.cpp 工具循环全程保留 |
| a) OpenAI 兼容工具循环 | SF 示例 `sf-interleaved-thinking.txt:140-190`；MiMo 示例 `mimo-faq.txt:147-196` | tools=[{type:function,function:{...}}] → tool_calls → role=tool 回传 → 直到 stop |
| c) arguments 不保证合法 JSON | 本批 sources 未展开（见他篇） | runtime 仍需 schema 校验（不在本篇原文范围，标注） |
| d) tool_choice 后端 auto | 两家示例均 `tool_choice="auto"`（`sf-chat-completions-api.txt:459`；`mimo-faq.txt:195`） | 不指望强制指定工具 |

---

## 5. 红线与自检记录

- 文件可打开、目录已建 ✓（写于 `…/model-tool-calling/13-thinking-config-stability.md`）。
- 引用的每个 `sources/*.txt:<行>` 均在对应源文件逐行复核过 ✓：
  - SF interleaved 模型名单/保留规则/三大后果 → `sf-interleaved-thinking.txt:46-48,83-112`；
  - `enable_thinking`/`thinking_budget`(128–32768)/`reasoning_effort`(仅 V4 系) → `sf-chat-completions-api.txt:59-75`；
  - `max_tokens` 不含思维链 → `sf-chat-completions-api.txt:55-57`；
  - MiMo「tool_calls 混 reasoning_content = 不稳定 + 建议调工具关 thinking」→ `mimo-faq.txt:200-202`；多轮保留 reasoning_content → `mimo-faq.txt:141-143`。
- 覆盖任务指定范围（§1.1–1.5 全部要点 + 双源对比表 + 稳定性信号清单 + runtime 决策表与代码位）✓。
- 无编造 ✓；所有外推均标「推断」：S4–S6、MiMo 多步链「先关后试」、`reasoning_effort` 不可下发给 V3.2/GLM-4.7。
- 如实标注「未覆盖」：MiMo 关闭 thinking 的具体请求字段名（仅给外链 Model Hyperparameters 页，不在本批 sources）；MiMo 流式 reasoning 规则；MiMo `max_completion_tokens` 是否含 CoT；SF 本地 interleaved HTML 为 404 壳页（真实正文为现行英文站转录，见 `sf-interleaved-thinking.txt:3-19`）。
- 未改任何代码、未做 git、未调任何需 API key 接口 ✓。

---

## 6. 一句话总结

**两家对「保留 reasoning_content」完全一致，但对「要不要在工具循环里开 thinking」持相反默认：硅基流动说对 DeepSeek V3.2/GLM-4.7 开 interleaved thinking 是稳定特性、必须逐字回传；MiMo 说开着 thinking 调 tool 本身就是不稳定、官方建议调工具时关掉。MBDSDR runtime 应当按模型分档——SF interleaved 两款开着并全程保留，MiMo 默认调工具关 thinking，并把「tool_calls 混进 reasoning_content」作为降级重试的硬信号。**
